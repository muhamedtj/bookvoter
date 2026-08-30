"""Keep successful book-suggestion events as permanent club history.

Suggestion prompts, typed titles, search results and technical notices are
transient. Once a book has actually been added to the Waiting list, the final
confirmation is a meaningful club event and must not inherit any cleanup timer
from the temporary search card it replaced.
"""

import bot_core as core
import runtime_ux as ux


_installed = False
_original_message_edit_text = None


def _is_successful_suggestion_event(text) -> bool:
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value.startswith("✅"):
        return False
    return (
        "добавлена в <b>Лист ожидания</b>" in value
        or "добавлен в <b>Лист ожидания</b>" in value
        or "was added to the <b>Waiting list</b>" in value
    )


async def _edit_text_preserving_suggestion_event(self, text, *args, **kwargs):
    result = await _original_message_edit_text(self, text, *args, **kwargs)
    if (
        self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP)
        and _is_successful_suggestion_event(text)
    ):
        # The message began life as temporary search UI and may already have a
        # 5–10 minute deletion task. runtime_ux can also classify generic ✅
        # messages as ephemeral. Cancel whichever timer is currently attached
        # after the final edit so this club-history event remains indefinitely.
        ux.cancel_message_delete(self.chat.id, self.message_id)
        if ux.active_ui_messages.get(self.chat.id) == self.message_id:
            ux.active_ui_messages.pop(self.chat.id, None)
    return result


def install() -> None:
    global _installed, _original_message_edit_text
    if _installed:
        return
    _installed = True
    _original_message_edit_text = core.Message.edit_text
    core.Message.edit_text = _edit_text_preserving_suggestion_event
