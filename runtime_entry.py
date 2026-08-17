"""Group entry routing for BookVoter.

Telegram's /start is kept for private-chat onboarding and deep links only.
Group interaction uses BookVoter-specific commands so another domain bot can
coexist in the same group without sharing generic entry/admin commands.
"""

from aiogram import BaseMiddleware

import bot_core as core


_installed = False

# Generic commands are deliberately silent in groups. They are common enough to
# collide with MovieVoter or other future bots. BookVoter's group UX is button-
# first, with only /books, /bookadmin and /booktimer as public command names.
_SILENT_GENERIC_GROUP_COMMANDS = {
    "/start",
    "/help",
    "/admin",
    "/suggest",
    "/backlog",
    "/hof",
    "/halloffame",
    "/language",
    "/votetimer",
    "/finish_vote",
    "/reports",
}


def _command_name(text: str) -> str:
    if not text:
        return ""
    first = text.strip().split(maxsplit=1)[0]
    if not first.startswith("/"):
        return ""
    # /command@BotUsername -> /command
    return first.split("@", 1)[0].lower()


async def _show_public_menu(message) -> None:
    await core.register_user_and_chat(message)
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None

    if message.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        current_lang = await core.database.get_chat_language(core.DATABASE_PATH, chat_id)
        if not current_lang:
            await core.send_or_replace_group_ui(
                message,
                core.t("select_language_prompt", "en"),
                parse_mode="HTML",
                reply_markup=core.get_language_keyboard(),
            )
            return
        lang = current_lang
        bot_info = await core.bot.get_me()
        markup = core.get_welcome_keyboard(
            lang,
            bot_info.username,
            is_private=False,
            chat_id=chat_id,
        )
        await core.send_or_replace_group_ui(
            message,
            core.t("welcome_msg", lang),
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # In private chat /bookvoter and /books simply open the normal BookVoter menu.
    lang = await core.get_lang(chat_id, user_id)
    bot_info = await core.bot.get_me()
    markup = core.get_welcome_keyboard(
        lang,
        bot_info.username,
        is_private=True,
        chat_id=chat_id,
    )
    await message.answer(core.t("welcome_msg", lang), parse_mode="HTML", reply_markup=markup)


async def _show_admin_panel(message) -> None:
    await core.register_user_and_chat(message)
    lang = await core.get_lang(
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )

    if message.chat.type == core.ChatType.PRIVATE:
        await message.answer(core.t("control_panel_dm_notice", lang), parse_mode="HTML")
        return

    if not message.from_user or not await core.is_admin(message.chat.id, message.from_user.id):
        await core.send_ephemeral_reply(
            message,
            core.t("only_admins_allowed", lang),
            delay_seconds=30,
        )
        return

    panel_text, markup = await core.get_admin_panel_view(message.chat.id, lang)
    await core.send_or_replace_group_ui(
        message,
        panel_text,
        parse_mode="HTML",
        reply_markup=markup,
    )


class _EntryRoutingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None) or ""
        chat = getattr(event, "chat", None)
        command = _command_name(text)

        if not chat or chat.type not in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
            return await handler(event, data)

        # Generic commands are intentionally ignored by BookVoter in groups.
        # If another bot owns /start, /help, /suggest, /admin, etc., BookVoter
        # stays out of the way. When deletion permission exists, the command is
        # also removed from the chat to keep the group clean.
        if command in _SILENT_GENERIC_GROUP_COMMANDS:
            await core.cleanup_command_message(event)
            return None

        # /bookvoter remains a legacy, product-specific alias for /books.
        if command == "/bookvoter":
            await core.cleanup_command_message(event)
            await _show_public_menu(event)
            return None

        return await handler(event, data)


async def _books_command(message: core.Message):
    await core.cleanup_command_message(message)
    await _show_public_menu(message)


async def _bookadmin_command(message: core.Message):
    await core.cleanup_command_message(message)
    await _show_admin_panel(message)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # Intercept legacy handlers registered in bot_core without rewriting them.
    core.router.message.outer_middleware(_EntryRoutingMiddleware())

    # Collision-safe public commands.
    core.router.message(core.Command("books"))(_books_command)
    core.router.message(core.Command("bookadmin"))(_bookadmin_command)
