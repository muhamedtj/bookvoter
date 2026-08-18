"""BookVoter chat-cleanup hardening.

This module is installed last and keeps cleanup collision-safe for future bots:
BookVoter deletes only its own group commands, expires transient UI cards after
one minute of inactivity, and removes stale control-panel UI when a vote finishes.
"""

from __future__ import annotations

from typing import Optional

import bot_core as core
import runtime_ux as ux


# Product rule: navigation/control UI should not linger in the group. Every time
# an active UI card is edited, its cleanup timer is re-armed, so this behaves as
# an inactivity timeout rather than a hard one-minute lifetime while navigating.
UI_TTL_SECONDS = 60
OWN_GROUP_COMMANDS = {
    "/books",
    "/bookvoter",  # legacy alias
    "/bookadmin",
    "/booktimer",
}

_installed = False
_bot_username: Optional[str] = None
_original_send_or_replace_group_ui = None
_original_message_answer = None
_original_message_edit_text = None
_original_finish_vote_process = None
_original_get_admin_panel_view = None


def _command_parts(text: str) -> tuple[str, Optional[str]]:
    if not text:
        return "", None
    first = text.strip().split(maxsplit=1)[0]
    if not first.startswith("/"):
        return "", None
    raw = first[1:]
    if "@" in raw:
        name, target = raw.split("@", 1)
        return f"/{name.lower()}", target.lower()
    return f"/{raw.lower()}", None


async def _own_username() -> str:
    global _bot_username
    if _bot_username:
        return _bot_username
    info = await core.bot.get_me()
    _bot_username = (info.username or "").lower()
    return _bot_username


async def has_delete_permission(chat_id: int) -> bool:
    """Return whether BookVoter can delete members' messages in this group."""
    if chat_id >= 0:
        return False
    try:
        me = await core.bot.get_me()
        member = await core.bot.get_chat_member(chat_id, me.id)
        status = getattr(getattr(member, "status", None), "value", getattr(member, "status", None))
        status = str(status or "").lower()
        if status in {"creator", "owner"}:
            return True
        if status != "administrator":
            return False
        return bool(getattr(member, "can_delete_messages", False))
    except Exception as exc:
        core.logger.warning(f"Could not inspect BookVoter delete permission in chat {chat_id}: {exc}")
        return False


async def clear_group_ui(chat_id: int) -> bool:
    """Delete the currently tracked transient BookVoter UI card for a group."""
    message_id = ux.active_ui_messages.pop(chat_id, None)
    if not message_id:
        return False
    ux.cancel_message_delete(chat_id, message_id)
    return await ux.safe_delete_message(chat_id, message_id)


async def _collision_safe_cleanup_call(self, handler, event, data):
    """Replace the old 'delete every slash command' middleware behavior."""
    try:
        return await handler(event, data)
    finally:
        text = getattr(event, "text", None) or ""
        chat = getattr(event, "chat", None)
        should_check = bool(
            chat
            and chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP)
            and text.lstrip().startswith("/")
        )
        if should_check:
            command, target = _command_parts(text)
            own_target = not target or target == await _own_username()
            if command in OWN_GROUP_COMMANDS and own_target:
                deleted = await ux.safe_delete_message(chat.id, event.message_id)
                if not deleted and not await has_delete_permission(chat.id):
                    core.logger.warning(
                        "BookVoter could not clean command %s in chat %s: grant the bot "
                        "administrator permission 'Delete messages'.",
                        command,
                        chat.id,
                    )


async def _send_or_replace_group_ui_with_ttl(message, text: str, parse_mode="HTML", reply_markup=None):
    sent = await _original_send_or_replace_group_ui(
        message,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    if message.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP) and sent:
        ux.schedule_message_delete(sent.chat.id, sent.message_id, UI_TTL_SECONDS)
    return sent


async def _message_answer_with_ui_ttl(self, text, *args, **kwargs):
    sent = await _original_message_answer(self, text, *args, **kwargs)
    if self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP) and sent:
        if ux.active_ui_messages.get(self.chat.id) == sent.message_id:
            ux.schedule_message_delete(self.chat.id, sent.message_id, UI_TTL_SECONDS)
    return sent


async def _message_edit_text_with_ui_ttl(self, text, *args, **kwargs):
    result = await _original_message_edit_text(self, text, *args, **kwargs)
    if self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        if ux.active_ui_messages.get(self.chat.id) == self.message_id:
            # Any navigation/edit is activity: start a fresh one-minute window.
            ux.schedule_message_delete(self.chat.id, self.message_id, UI_TTL_SECONDS)
    return result


async def _finish_vote_and_clear_ui(chat_id: int):
    result = await _original_finish_vote_process(chat_id)
    try:
        if not await core.database.get_active_poll(core.DATABASE_PATH, chat_id):
            await clear_group_ui(chat_id)
    except Exception as exc:
        core.logger.debug(f"Could not clear stale group UI after vote in {chat_id}: {exc}")
    return result


async def _admin_panel_with_cleanup_status(chat_id: int, lang: str, db_path: str = core.DATABASE_PATH):
    text, markup = await _original_get_admin_panel_view(chat_id, lang, db_path=db_path)
    if await has_delete_permission(chat_id):
        status = (
            "🧹 <b>Очистка чата:</b> включена · UI удаляется через 1 мин бездействия"
            if lang == "ru"
            else "🧹 <b>Chat cleanup:</b> enabled · UI expires after 1 min of inactivity"
        )
    else:
        status = (
            "⚠️ <b>Очистка команд:</b> нет права «Удаление сообщений»"
            if lang == "ru"
            else "⚠️ <b>Command cleanup:</b> grant the bot ‘Delete messages’ permission"
        )
    return f"{text}\n\n{status}", markup


def install() -> None:
    global _installed
    global _original_send_or_replace_group_ui
    global _original_message_answer
    global _original_message_edit_text
    global _original_finish_vote_process
    global _original_get_admin_panel_view

    if _installed:
        return
    _installed = True

    # The middleware instance is already registered by runtime_ux. Python looks
    # up __call__ on its class at runtime, so patching the class fixes that
    # existing instance without registering a second competing middleware.
    ux._CommandCleanupMiddleware.__call__ = _collision_safe_cleanup_call

    _original_send_or_replace_group_ui = core.send_or_replace_group_ui
    core.send_or_replace_group_ui = _send_or_replace_group_ui_with_ttl

    _original_message_answer = core.Message.answer
    _original_message_edit_text = core.Message.edit_text
    core.Message.answer = _message_answer_with_ui_ttl
    core.Message.edit_text = _message_edit_text_with_ui_ttl

    _original_finish_vote_process = core.finish_vote_process
    core.finish_vote_process = _finish_vote_and_clear_ui

    _original_get_admin_panel_view = core.get_admin_panel_view
    core.get_admin_panel_view = _admin_panel_with_cleanup_status

    core.has_delete_permission = has_delete_permission
    core.clear_group_ui = clear_group_ui
