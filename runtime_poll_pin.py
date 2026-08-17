"""Ensure every BookVoter native poll becomes the active stage pin."""

import bot_core as core

_installed = False
_original_send_poll = None


async def _send_poll_and_pin(self, *args, **kwargs):
    msg = await _original_send_poll(self, *args, **kwargs)
    try:
        chat_id = int(getattr(getattr(msg, "chat", None), "id", kwargs.get("chat_id", 0)) or 0)
        if chat_id < 0 and callable(getattr(core, "set_stage_message", None)):
            await core.set_stage_message(
                chat_id,
                msg.message_id,
                "voting",
                delete_previous=True,
            )
    except Exception as exc:
        core.logger.debug(f"Could not pin BookVoter poll message: {exc}")
    return msg


def install() -> None:
    global _installed, _original_send_poll
    if _installed:
        return
    _installed = True
    _original_send_poll = core.Bot.send_poll
    core.Bot.send_poll = _send_poll_and_pin
