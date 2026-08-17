"""Small final navigation layout adjustments."""

import bot_core as core

_installed = False
_base_welcome = None


def get_welcome_keyboard(lang: str, bot_username: str, is_private: bool = False, chat_id=None):
    markup = _base_welcome(lang, bot_username, is_private=is_private, chat_id=chat_id)
    if not is_private and chat_id and chat_id < 0 and markup.inline_keyboard:
        rows = [list(row) for row in markup.inline_keyboard]
        if len(rows[-1]) == 2:
            # Keep "Report an error" on the left and Group stats as the second/right button.
            rows[-1] = [rows[-1][1], rows[-1][0]]
        return core.InlineKeyboardMarkup(inline_keyboard=rows)
    return markup


def install():
    global _installed, _base_welcome
    if _installed:
        return
    _installed = True
    _base_welcome = core.get_welcome_keyboard
    core.get_welcome_keyboard = get_welcome_keyboard
