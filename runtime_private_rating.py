"""Single-card private waiting-list rating UX.

Repeated /start deep-links used to create multiple rating cards for the same
book. A user could rate one card, while the stale duplicate remained clickable.
This module keeps exactly one active BookVoter waiting-list card per user and
reuses it as the user moves through unrated books.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import bot_core as core
import runtime_ux as ux


_installed = False
_locks: dict[int, asyncio.Lock] = {}


def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


async def _ensure_table() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS private_rating_ui (
                user_tg_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def _get_active_message_id(user_id: int) -> Optional[int]:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT message_id FROM private_rating_ui WHERE user_tg_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row[0]) if row else None


async def _set_active_message_id(user_id: int, message_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO private_rating_ui (user_tg_id, message_id, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_tg_id) DO UPDATE SET
                message_id = excluded.message_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, message_id),
        )
        await db.commit()


async def _delete_active_row(user_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute("DELETE FROM private_rating_ui WHERE user_tg_id = ?", (user_id,))
        await db.commit()


async def _safe_delete_private(user_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await core.bot.delete_message(chat_id=user_id, message_id=message_id)
    except Exception:
        pass


async def _edit_existing(user_id: int, message_id: int, text: str, markup=None) -> bool:
    try:
        await core.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
        return True
    except Exception as exc:
        # Telegram returns an error when content is already identical. In that
        # case the existing card is still exactly what we want.
        if "message is not modified" in str(exc).lower():
            return True
        return False


async def _render_payload(user_id: int, lang: str):
    books = await core.database.get_unrated_backlog_books_for_user(core.DATABASE_PATH, user_id)
    if not books:
        return core.t("all_caught_up_rating", lang), None

    book = books[0]
    row1 = [core.InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(1, 6)]
    row2 = [core.InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(6, 11)]
    markup = core.InlineKeyboardMarkup(inline_keyboard=[row1, row2])

    text = core.t(
        "rate_prompt_group",
        lang,
        chat_title=core.escape_html(book["chat_title"]),
        title=core.escape_html(book["title"]),
        author=core.escape_html(book["author"]),
    )
    try:
        suggestor = await ux._get_suggestor_display(int(book["id"]))
    except Exception:
        suggestor = ""
    if suggestor:
        label = "Предложил" if lang == "ru" else "Suggested by"
        text += f"\n\n👤 <b>{label}:</b> {suggestor}"

    return text, markup


async def send_next_unrated_book(user_tg_id: int, target_msg_or_user: Any, lang: str):
    """Render the user's queue into one reusable private message."""
    async with _lock_for(user_tg_id):
        text, markup = await _render_payload(user_tg_id, lang)
        tracked_id = await _get_active_message_id(user_tg_id)

        # Rating callbacks already come from a bot message. Reuse that exact
        # message and delete another tracked duplicate, if one exists.
        if isinstance(target_msg_or_user, core.CallbackQuery):
            current_id = target_msg_or_user.message.message_id
            if tracked_id and tracked_id != current_id:
                await _safe_delete_private(user_tg_id, tracked_id)
            try:
                await target_msg_or_user.message.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except Exception as exc:
                if "message is not modified" not in str(exc).lower():
                    core.logger.debug(f"Could not reuse private rating card {current_id}: {exc}")
            await _set_active_message_id(user_tg_id, current_id)
            return

        # Repeated /start or deep-link opens should update the existing card,
        # not create another copy of the same question.
        if tracked_id:
            if await _edit_existing(user_tg_id, tracked_id, text, markup):
                return
            await _delete_active_row(user_tg_id)

        if isinstance(target_msg_or_user, core.Message):
            sent = await target_msg_or_user.answer(text, parse_mode="HTML", reply_markup=markup)
        else:
            sent = await core.bot.send_message(
                user_tg_id,
                text,
                parse_mode="HTML",
                reply_markup=markup,
            )
        await _set_active_message_id(user_tg_id, sent.message_id)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    core.send_next_unrated_book = send_next_unrated_book
