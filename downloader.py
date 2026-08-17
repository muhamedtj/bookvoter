"""Public downloader entrypoint with a robust multi-step Telegram-library resolver.

The parser/search implementation stays in :mod:`downloader_core`; this layer fixes
multi-step downloads where a saved /download command returns a book card first
and only then exposes EPUB/FB2/MOBI buttons.
"""

import asyncio
import os
import re
import sys
from typing import Any, Optional

import downloader_core as _core


def _message_is_self(message: Any) -> bool:
    from_user = getattr(message, "from_user", None)
    return bool(getattr(from_user, "is_self", False))


def _message_action_fingerprint(message: Any) -> tuple:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    rows = []
    markup = getattr(message, "reply_markup", None)
    if markup and getattr(markup, "inline_keyboard", None):
        for row in markup.inline_keyboard:
            rows.append(
                tuple(
                    (
                        getattr(btn, "text", "") or "",
                        getattr(btn, "callback_data", "") or "",
                        getattr(btn, "url", "") or "",
                    )
                    for btn in row
                )
            )
    return (getattr(message, "id", None), text, tuple(rows))


async def _resolve_to_document(
    app: Any,
    target_channel: Any,
    request_msg_id: int,
    timeout_seconds: int = 50,
) -> Optional[Any]:
    """Follow library-bot cards/buttons/commands until a document is produced.

    Important detail: the library may answer `/download123` with a book card and
    format buttons instead of a file.  The previous implementation treated that
    first card as a terminal response and timed out.  Here each unique response
    state is acted on once, including edits of an existing message.
    """
    started = asyncio.get_running_loop().time()
    acted_states = set()
    after_id = request_msg_id

    while asyncio.get_running_loop().time() - started < timeout_seconds:
        messages = []
        try:
            async for message in app.get_chat_history(target_channel, limit=20):
                if message.id <= after_id:
                    continue
                if _message_is_self(message):
                    continue
                messages.append(message)
        except _core.FloodWait as fw:
            remaining = timeout_seconds - (asyncio.get_running_loop().time() - started)
            wait_for = getattr(fw, "value", 1)
            if wait_for < remaining:
                await asyncio.sleep(wait_for)
                continue
            return None
        except Exception as exc:
            err = str(exc)
            if "FloodWait" in err or "GetHistory" in err:
                match = re.search(r"(\d+)\s*second", err, re.IGNORECASE)
                await asyncio.sleep(int(match.group(1)) if match else 2)
                continue
            _core.logging.warning(f"Error polling library response chain: {exc}")
            await asyncio.sleep(1)
            continue

        # Process in chronological order so a book-selection card is handled
        # before a later format card/document from the same request chain.
        messages.sort(key=lambda msg: msg.id)

        for message in messages:
            if message.id <= after_id:
                continue

            if getattr(message, "document", None):
                return message

            state = _message_action_fingerprint(message)
            if state in acted_states:
                continue

            markup = getattr(message, "reply_markup", None)
            keyboard_rows = getattr(markup, "inline_keyboard", None) if markup else None
            if keyboard_rows:
                best_btn = _core.select_best_button(keyboard_rows)
                callback_data = getattr(best_btn, "callback_data", None) if best_btn else None
                if callback_data:
                    acted_states.add(state)
                    try:
                        await app.request_callback_answer(
                            chat_id=message.chat.id,
                            message_id=message.id,
                            callback_data=callback_data,
                        )
                        # The same message may be edited into the next step; its
                        # new fingerprint will be handled on the following poll.
                        await asyncio.sleep(0.7)
                        continue
                    except _core.FloodWait as fw:
                        await asyncio.sleep(getattr(fw, "value", 1))
                        continue
                    except Exception as exc:
                        _core.logging.warning(
                            f"Could not press library button '{getattr(best_btn, 'text', '')}': {exc}"
                        )

            msg_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
            cmd_match = re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", msg_text)
            if cmd_match:
                acted_states.add(state)
                try:
                    sent = await _core.send_message_with_floodwait(
                        app,
                        target_channel,
                        cmd_match.group(0),
                    )
                    # Ignore everything older than our newly issued command from
                    # this point forward, while still allowing edited later cards.
                    after_id = max(after_id, sent.id)
                    await asyncio.sleep(0.7)
                    continue
                except Exception as exc:
                    _core.logging.warning(f"Could not follow library download command: {exc}")

            # Mark inert text states so they are not reprocessed every second.
            acted_states.add(state)

        await asyncio.sleep(0.8)

    return None


async def search_and_download(title: str) -> None:
    """Search/download a book and follow all intermediate Telegram-library steps."""
    if not _core.API_ID or not _core.API_HASH or not _core.SESSION_STRING or not _core.CHANNEL_ID:
        sys.stderr.write(
            "Error: Missing required environment variables (API_ID, API_HASH, SESSION_STRING, CHANNEL_ID).\n"
        )
        sys.exit(1)

    target_channel = _core.get_target_channel()
    os.makedirs(_core.DOWNLOAD_DIR, exist_ok=True)

    app = _core.Client(
        name="userbot_downloader",
        api_id=int(_core.API_ID),
        api_hash=_core.API_HASH,
        session_string=_core.SESSION_STRING,
        in_memory=True,
    )

    try:
        await app.start()
    except Exception as exc:
        sys.stderr.write(f"Error starting Pyrogram client: {exc}\n")
        sys.exit(1)

    try:
        history_count = await _core.check_history_with_floodwait(app, target_channel)
        if history_count == 0:
            try:
                await _core.send_message_with_floodwait(app, target_channel, "/start")
                await asyncio.sleep(2)
            except Exception as exc:
                _core.logging.warning(f"Could not send /start to target {target_channel}: {exc}")

        try:
            request_msg = await _core.send_message_with_floodwait(app, target_channel, title)
        except Exception as exc:
            sys.stderr.write(f"Failed to send library request '{title}': {exc}\n")
            sys.exit(1)

        document_message = await _resolve_to_document(
            app,
            target_channel,
            request_msg.id,
            timeout_seconds=50,
        )

        if document_message and document_message.document:
            file_name = document_message.document.file_name or "downloaded_book.epub"
            downloaded_path = await app.download_media(
                document_message,
                file_name=os.path.join(_core.DOWNLOAD_DIR, file_name),
            )
            if downloaded_path and os.path.exists(downloaded_path):
                print(os.path.abspath(downloaded_path))
                sys.exit(0)

        sys.stderr.write(f"File not received from library for request: {title}\n")
        sys.exit(1)

    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write(f"Error during search or download: {exc}\n")
        sys.exit(1)
    finally:
        try:
            await app.stop()
        except Exception:
            pass


_core.search_and_download = search_and_download


if __name__ == "__main__":
    _core.main()
else:
    # Keep imports/tests compatible with the historical public module name.
    sys.modules[__name__] = _core
