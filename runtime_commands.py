"""Collision-safe, interface-language-aware Telegram command scopes.

Public group interaction is button-first. Only BookVoter-specific commands are
advertised in groups, leaving generic commands free for other bots. In addition
to Telegram-client language fallbacks, each club gets a chat-specific command
menu matching the language selected inside BookVoter itself.
"""

from aiogram import BaseMiddleware
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChat,
    BotCommandScopeChatAdministrators,
)

import bot_core as core
import runtime_voting


_installed = False
_original_main = None


def _private_commands(lang: str):
    if lang == "ru":
        return [
            BotCommand(command="start", description="Открыть BookVoter"),
            BotCommand(command="language", description="Сменить язык интерфейса"),
        ]
    return [
        BotCommand(command="start", description="Open BookVoter"),
        BotCommand(command="language", description="Change interface language"),
    ]


def _group_commands(lang: str):
    if lang == "ru":
        return [BotCommand(command="books", description="Открыть меню BookVoter")]
    return [BotCommand(command="books", description="Open BookVoter menu")]


def _admin_commands(lang: str):
    if lang == "ru":
        return [
            BotCommand(command="books", description="Открыть меню BookVoter"),
            BotCommand(command="bookadmin", description="Управление книжным клубом"),
            BotCommand(command="booktimer", description="Настроить длительность голосования"),
        ]
    return [
        BotCommand(command="books", description="Open BookVoter menu"),
        BotCommand(command="bookadmin", description="Manage the book club"),
        BotCommand(command="booktimer", description="Set voting duration"),
    ]


async def _booktimer_command(message: core.Message, command: core.CommandObject):
    """Namespaced alias for the existing per-club voting timer setting."""
    await runtime_voting._votetimer_command(message, command)


async def configure_commands_for_chat(chat_id: int, lang: str, *, is_private: bool = False) -> None:
    """Make Telegram's slash-command descriptions match BookVoter's chosen UI language."""
    lang = "ru" if lang == "ru" else "en"
    try:
        if is_private or chat_id > 0:
            await core.bot.set_my_commands(
                _private_commands(lang),
                scope=BotCommandScopeChat(chat_id=chat_id),
            )
            return

        # Chat scope is for ordinary members; chat-administrator scope has higher
        # specificity and exposes the two management commands only to admins.
        await core.bot.set_my_commands(
            _group_commands(lang),
            scope=BotCommandScopeChat(chat_id=chat_id),
        )
        await core.bot.set_my_commands(
            _admin_commands(lang),
            scope=BotCommandScopeChatAdministrators(chat_id=chat_id),
        )
    except Exception as exc:
        core.logger.warning(f"Could not configure BookVoter commands for chat {chat_id}: {exc}")


async def _configure_saved_group_commands() -> None:
    """Restore per-club command language after a bot restart/deploy."""
    try:
        async with core.database.open_db(core.DATABASE_PATH) as db:
            async with db.execute(
                "SELECT chat_id, COALESCE(language_code, 'en') FROM chats WHERE status = 'active'"
            ) as cursor:
                rows = await cursor.fetchall()
        for chat_id, lang in rows:
            await configure_commands_for_chat(int(chat_id), str(lang or "en"), is_private=False)
    except Exception as exc:
        core.logger.warning(f"Could not restore per-chat BookVoter command language: {exc}")


async def _configure_commands() -> None:
    """Publish global fallbacks, then restore each club's selected UI language."""
    try:
        try:
            await core.bot.delete_my_commands()
        except Exception:
            pass

        private_scope = BotCommandScopeAllPrivateChats()
        group_scope = BotCommandScopeAllGroupChats()
        admin_scope = BotCommandScopeAllChatAdministrators()

        # Fallbacks follow the Telegram client's language when no BookVoter
        # per-chat language choice has been stored yet.
        await core.bot.set_my_commands(_private_commands("en"), scope=private_scope)
        await core.bot.set_my_commands(_private_commands("ru"), scope=private_scope, language_code="ru")
        await core.bot.set_my_commands(_group_commands("en"), scope=group_scope)
        await core.bot.set_my_commands(_group_commands("ru"), scope=group_scope, language_code="ru")
        await core.bot.set_my_commands(_admin_commands("en"), scope=admin_scope)
        await core.bot.set_my_commands(_admin_commands("ru"), scope=admin_scope, language_code="ru")

        await _configure_saved_group_commands()
        core.logger.info("Installed localized, scoped BookVoter Telegram commands")
    except Exception as exc:
        # Command-menu configuration must never prevent the bot from starting.
        core.logger.warning(f"Could not configure Telegram command scopes: {exc}")


class _SelectedLanguageCommandMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        callback_data = getattr(event, "data", None) or ""
        if callback_data.startswith("set_lang:") and getattr(event, "message", None):
            lang = callback_data.split(":", 1)[1]
            chat = event.message.chat
            await configure_commands_for_chat(
                chat.id,
                lang,
                is_private=(chat.type == core.ChatType.PRIVATE),
            )
        return result


async def _main_with_commands():
    await _configure_commands()
    await _original_main()


def install() -> None:
    global _installed, _original_main
    if _installed:
        return
    _installed = True

    # Keep /votetimer as an unadvertised legacy handler in core, but the public
    # collision-safe command is /booktimer.
    core.router.message(core.Command("booktimer"))(_booktimer_command)
    core.router.callback_query.outer_middleware(_SelectedLanguageCommandMiddleware())

    core.configure_commands_for_chat = configure_commands_for_chat

    _original_main = core.main
    core.main = _main_with_commands
