"""Collision-safe Telegram command scopes for BookVoter.

Public group interaction is button-first. Only BookVoter-specific commands are
advertised in groups, leaving generic commands such as /start, /help, /admin and
/suggest free for other bots that may coexist in the same chat.
"""

from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
)

import bot_core as core
import runtime_voting


_installed = False
_original_main = None


async def _booktimer_command(message: core.Message, command: core.CommandObject):
    """Namespaced alias for the existing per-club voting timer setting."""
    await runtime_voting._votetimer_command(message, command)


async def _configure_commands() -> None:
    """Publish a minimal, collision-safe command menu by Telegram scope."""
    try:
        # Clear legacy default-scope commands first. Scope-specific menus below
        # are the product contract going forward.
        try:
            await core.bot.delete_my_commands()
        except Exception:
            pass

        private_scope = BotCommandScopeAllPrivateChats()
        group_scope = BotCommandScopeAllGroupChats()
        admin_scope = BotCommandScopeAllChatAdministrators()

        private_en = [
            BotCommand(command="start", description="Open BookVoter"),
            BotCommand(command="language", description="Change language"),
        ]
        private_ru = [
            BotCommand(command="start", description="Открыть BookVoter"),
            BotCommand(command="language", description="Сменить язык"),
        ]

        group_en = [
            BotCommand(command="books", description="Open BookVoter"),
        ]
        group_ru = [
            BotCommand(command="books", description="Открыть BookVoter"),
        ]

        admin_en = [
            BotCommand(command="books", description="Open BookVoter"),
            BotCommand(command="bookadmin", description="BookVoter control panel"),
            BotCommand(command="booktimer", description="Set voting duration, e.g. 2h"),
        ]
        admin_ru = [
            BotCommand(command="books", description="Открыть BookVoter"),
            BotCommand(command="bookadmin", description="Панель управления BookVoter"),
            BotCommand(command="booktimer", description="Таймер голосования, например 2h"),
        ]

        await core.bot.set_my_commands(private_en, scope=private_scope)
        await core.bot.set_my_commands(private_ru, scope=private_scope, language_code="ru")
        await core.bot.set_my_commands(group_en, scope=group_scope)
        await core.bot.set_my_commands(group_ru, scope=group_scope, language_code="ru")
        await core.bot.set_my_commands(admin_en, scope=admin_scope)
        await core.bot.set_my_commands(admin_ru, scope=admin_scope, language_code="ru")
        core.logger.info("Installed scoped BookVoter Telegram commands")
    except Exception as exc:
        # Command-menu configuration must never prevent the bot from starting.
        core.logger.warning(f"Could not configure Telegram command scopes: {exc}")


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

    _original_main = core.main
    core.main = _main_with_commands
