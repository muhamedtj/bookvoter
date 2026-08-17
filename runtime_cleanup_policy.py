"""Product cleanup timings for a low-noise BookVoter group chat.

This module keeps the generic cleanup machinery intact and only changes the
product policy: menus are short-lived, suggestion confirmations disappear
quickly, while search/prompt timeouts remain long enough for users to act.
"""

import runtime_cleanup
import runtime_suggest


_installed = False
_original_suggest_schedule = None


def _suggest_schedule(chat_id: int, message_id: int, delay_seconds: int = 90) -> None:
    # Successful/duplicate suggestion cards used to stay for 1–2 minutes. They
    # are transactional UI, not club history, so keep them only briefly.
    if delay_seconds >= 120:
        delay_seconds = 20
    elif delay_seconds == 60:
        delay_seconds = 20
    _original_suggest_schedule(chat_id, message_id, delay_seconds)


def install() -> None:
    global _installed, _original_suggest_schedule
    if _installed:
        return
    _installed = True

    # Main menu / admin panel are invokable on demand. Three minutes is enough
    # to navigate without leaving stale UI in the conversation.
    runtime_cleanup.UI_TTL_SECONDS = 3 * 60

    _original_suggest_schedule = runtime_suggest.schedule_message_delete
    runtime_suggest.schedule_message_delete = _suggest_schedule
