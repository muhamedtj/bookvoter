"""Persistent, explicit vote-result message for BookVoter.

The existing core winner announcement is enriched with the actual Telegram vote
result before the downloader starts. The message stays as club history while the
book file becomes the next active pinned stage.
"""

from __future__ import annotations

import json
from typing import Any

import bot_core as core

_installed = False
_original_finish_vote = None
_original_bot_send_message = None
_vote_snapshots: dict[int, dict[str, Any]] = {}


async def _snapshot_vote(chat_id: int) -> None:
    try:
        active = await core.database.get_active_poll(core.DATABASE_PATH, chat_id)
        if not active:
            _vote_snapshots.pop(chat_id, None)
            return

        poll_id = str(active["poll_id"])
        raw_mapping = active.get("options_json") or "{}"
        mapping = json.loads(raw_mapping) if isinstance(raw_mapping, str) else dict(raw_mapping)
        mapping = {int(k): int(v) for k, v in mapping.items()}

        tracker = core.poll_votes_tracker.get(poll_id, {}) or {}
        # tracker is user_id -> option_idx in the current core implementation.
        counts: dict[int, int] = {idx: 0 for idx in mapping}
        for option_idx in tracker.values():
            try:
                idx = int(option_idx)
            except (TypeError, ValueError):
                continue
            if idx in counts:
                counts[idx] += 1

        _vote_snapshots[chat_id] = {
            "poll_id": poll_id,
            "mapping": mapping,
            "counts": counts,
            "total": sum(counts.values()),
        }
    except Exception as exc:
        core.logger.debug(f"Could not snapshot BookVoter vote result for {chat_id}: {exc}")
        _vote_snapshots.pop(chat_id, None)


async def _finish_vote_with_snapshot(chat_id: int):
    await _snapshot_vote(chat_id)
    try:
        return await _original_finish_vote(chat_id)
    finally:
        _vote_snapshots.pop(chat_id, None)


def _is_winner_announcement(text: str) -> bool:
    plain = str(text or "")
    return plain.startswith((
        "🏆 <b>Выбрана следующая книга</b>",
        "🏆 <b>Next book selected</b>",
    ))


async def _send_message_with_explicit_vote_result(self, *args, **kwargs):
    text = kwargs.get("text")
    if text is None and len(args) >= 2:
        text = args[1]

    if _is_winner_announcement(str(text or "")):
        try:
            chat_id = int(kwargs.get("chat_id") or (args[0] if args else 0) or 0)
            snapshot = _vote_snapshots.get(chat_id)
            reading = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)

            if snapshot and reading:
                winner_id = int(reading["id"])
                winner_idx = next(
                    (idx for idx, book_id in snapshot["mapping"].items() if int(book_id) == winner_id),
                    None,
                )
                winner_votes = int(snapshot["counts"].get(winner_idx, 0)) if winner_idx is not None else 0
                total = int(snapshot.get("total", 0))
                percent = round((winner_votes / total) * 100) if total else 0
                lang = await core.database.get_effective_language(core.DATABASE_PATH, chat_id)
                title = core.escape_html(reading["title"])
                author = core.escape_html(reading["author"])

                if lang == "ru":
                    if total and winner_votes * 2 > total:
                        result_line = f"🏆 Большинство голосов получила: <b>«{title}»</b> — {author}."
                    else:
                        result_line = f"🏆 Больше всего голосов получила: <b>«{title}»</b> — {author}."
                    votes_line = (
                        f"👥 Результат: <b>{winner_votes} из {total}</b> голосов ({percent}%)."
                        if total
                        else "👥 Голосование завершено без доступной детализации голосов."
                    )
                    enhanced = (
                        "🏆 <b>Выбрана следующая книга</b>\n\n"
                        "✅ <b>Голосование завершено.</b>\n"
                        f"{result_line}\n"
                        f"{votes_line}\n\n"
                        "📥 BookVoter начинает загрузку выбранной книги."
                    )
                else:
                    if total and winner_votes * 2 > total:
                        result_line = f"🏆 The majority voted for: <b>«{title}»</b> — {author}."
                    else:
                        result_line = f"🏆 The most votes went to: <b>«{title}»</b> — {author}."
                    votes_line = (
                        f"👥 Result: <b>{winner_votes} of {total}</b> votes ({percent}%)."
                        if total
                        else "👥 Voting ended without detailed vote counts available."
                    )
                    enhanced = (
                        "🏆 <b>Next book selected</b>\n\n"
                        "✅ <b>Voting has ended.</b>\n"
                        f"{result_line}\n"
                        f"{votes_line}\n\n"
                        "📥 BookVoter is now downloading the selected book."
                    )

                if "text" in kwargs:
                    kwargs["text"] = enhanced
                else:
                    args = list(args)
                    if len(args) >= 2:
                        args[1] = enhanced
                    args = tuple(args)
        except Exception as exc:
            core.logger.debug(f"Could not enrich BookVoter vote-result message: {exc}")

    return await _original_bot_send_message(self, *args, **kwargs)


def install() -> None:
    global _installed, _original_finish_vote, _original_bot_send_message
    if _installed:
        return
    _installed = True

    # Install last: snapshot wraps the full current finish lifecycle, while the
    # send-message wrapper sits outside stage-pin/cleanup wrappers and feeds the
    # enriched text into them. Keeping the same winner prefix preserves pinning.
    _original_finish_vote = core.finish_vote_process
    core.finish_vote_process = _finish_vote_with_snapshot

    _original_bot_send_message = core.Bot.send_message
    core.Bot.send_message = _send_message_with_explicit_vote_result
