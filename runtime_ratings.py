"""Final-reading rating rounds for BookVoter.

A shared rating card stays open while club members respond. The expected number
of responses is snapshotted when reading is finished. "Didn't read" counts as a
response, but not as a numeric rating. When 100% of the snapshotted participants
have responded, the shared card is closed and replaced by a compact result with
the book's current Hall of Fame position.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram import BaseMiddleware

import bot_core as core
from runtime_ux import label


_installed = False
_rating_locks: dict[int, asyncio.Lock] = {}


def _lock_for(book_id: int) -> asyncio.Lock:
    lock = _rating_locks.get(book_id)
    if lock is None:
        lock = asyncio.Lock()
        _rating_locks[book_id] = lock
    return lock


async def _ensure_table() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_sessions (
                book_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                expected_responses INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE
            )
            """
        )
        await db.commit()


async def _expected_human_members(chat_id: int) -> int:
    """Snapshot the best Bot-API estimate of human members in the club.

    Telegram does not expose a complete member list to bots. We therefore use
    the group member count and subtract BookVoter plus any bot administrators we
    can identify. If that is unavailable, fall back to BookVoter's recorded
    membership table.
    """
    try:
        total = int(await core.bot.get_chat_member_count(chat_id))
        bot_ids = set()

        try:
            me = await core.bot.get_me()
            bot_ids.add(me.id)
        except Exception:
            pass

        try:
            for member in await core.bot.get_chat_administrators(chat_id):
                user = getattr(member, "user", None)
                if user and getattr(user, "is_bot", False):
                    bot_ids.add(user.id)
        except Exception as exc:
            core.logger.debug(f"Could not enumerate bot administrators for rating target in {chat_id}: {exc}")

        expected = total - len(bot_ids)
        if expected > 0:
            return expected
    except Exception as exc:
        core.logger.warning(f"Could not snapshot Telegram member count for rating round in {chat_id}: {exc}")

    # Fallback: users BookVoter has actually observed in this club.
    try:
        async with core.database.open_db(core.DATABASE_PATH) as db:
            async with db.execute(
                """
                SELECT COUNT(*)
                FROM chat_members cm
                JOIN users u ON u.internal_id = cm.user_id
                WHERE cm.chat_id = ?
                """,
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row and int(row[0]) > 0:
            return int(row[0])
    except Exception as exc:
        core.logger.warning(f"Could not use recorded membership fallback for rating round in {chat_id}: {exc}")

    return 1


async def _create_session(book_id: int, chat_id: int) -> dict:
    await _ensure_table()
    expected = await _expected_human_members(chat_id)
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO rating_sessions (book_id, chat_id, expected_responses, status)
            VALUES (?, ?, ?, 'open')
            ON CONFLICT(book_id) DO NOTHING
            """,
            (book_id, chat_id, max(1, expected)),
        )
        await db.commit()
    return await _get_session(book_id)


async def _get_session(book_id: int) -> Optional[dict]:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT book_id, chat_id, expected_responses, status FROM rating_sessions WHERE book_id = ?",
            (book_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    return {
        "book_id": int(row[0]),
        "chat_id": int(row[1]),
        "expected_responses": int(row[2]),
        "status": str(row[3]),
    }


async def _response_stats(book_id: int) -> dict:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS responses,
                   COUNT(score) AS scored,
                   COALESCE(AVG(score), 0) AS avg_score,
                   SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS not_read
            FROM read_ratings
            WHERE book_id = ?
            """,
            (book_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return {
        "responses": int(row[0] or 0),
        "scored": int(row[1] or 0),
        "avg_score": round(float(row[2] or 0), 2),
        "not_read": int(row[3] or 0),
    }


async def _close_session(book_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            UPDATE rating_sessions
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND status = 'open'
            """,
            (book_id,),
        )
        await db.commit()


async def _save_read_rating_once(db_path: str, tg_id: int, book_id: int, score: Optional[int]) -> bool:
    """Persist the first final response only; later attempts never overwrite it."""
    internal_id = await core.database.get_or_create_user(db_path, tg_id)
    async with core.database.open_db(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO read_ratings (user_id, book_id, score, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, book_id) DO NOTHING
            """,
            (internal_id, book_id, score),
        )
        await db.commit()
        return cursor.rowcount > 0


async def _hall_position(chat_id: int, book_id: int) -> tuple[Optional[int], bool]:
    data = await core.database.get_hall_of_fame_detailed(
        core.DATABASE_PATH,
        chat_id,
        min_votes=core.MIN_VOTES_FOR_RATING,
    )
    for index, item in enumerate(data.get("qualified", []), 1):
        if int(item["id"]) == book_id:
            return index, True
    for item in data.get("low_votes", []):
        if int(item["id"]) == book_id:
            return None, False
    return None, False


def _live_text(lang: str, book: dict, stats: dict, expected: int) -> str:
    avg = f"{stats['avg_score']:.1f}/10" if stats["scored"] else "—"
    if lang == "ru":
        return (
            f"📖 <b>{core.escape_html(book['title'])}</b> — {core.escape_html(book['author'])}\n\n"
            f"Как вы оцениваете книгу?\n\n"
            f"📊 Средний балл: <b>{avg}</b> ({stats['scored']} оценок)\n"
            f"👥 Ответили: <b>{stats['responses']} из {expected}</b>\n"
            f"🙈 Не читали: <b>{stats['not_read']}</b>"
        )
    return (
        f"📖 <b>{core.escape_html(book['title'])}</b> — {core.escape_html(book['author'])}\n\n"
        f"How would you rate this book?\n\n"
        f"📊 Average score: <b>{avg}</b> ({stats['scored']} ratings)\n"
        f"👥 Responded: <b>{stats['responses']} of {expected}</b>\n"
        f"🙈 Didn't read: <b>{stats['not_read']}</b>"
    )


def _final_text(
    lang: str,
    book: dict,
    stats: dict,
    expected: int,
    position: Optional[int],
    qualified: bool,
) -> str:
    avg = f"{stats['avg_score']:.1f}/10" if stats["scored"] else "—"
    if lang == "ru":
        if qualified and position:
            hall_line = f"🏆 Текущее место в Зале славы: <b>#{position}</b>"
        else:
            hall_line = (
                f"🏆 Книга в Зале славы, но рейтинговое место появится после "
                f"<b>{core.MIN_VOTES_FOR_RATING}</b> числовых оценок."
            )
        return (
            f"🏁 <b>Оценка завершена</b>\n\n"
            f"📖 <b>{core.escape_html(book['title'])}</b> — {core.escape_html(book['author'])}\n"
            f"⭐ Итоговая оценка: <b>{avg}</b>\n"
            f"👥 Ответили: <b>{stats['responses']} из {expected}</b>\n"
            f"🙈 Не читали: <b>{stats['not_read']}</b>\n\n"
            f"{hall_line}"
        )

    if qualified and position:
        hall_line = f"🏆 Current Hall of Fame position: <b>#{position}</b>"
    else:
        hall_line = (
            f"🏆 The book is in the Hall of Fame, but a ranked position appears after "
            f"<b>{core.MIN_VOTES_FOR_RATING}</b> numeric ratings."
        )
    return (
        f"🏁 <b>Rating complete</b>\n\n"
        f"📖 <b>{core.escape_html(book['title'])}</b> — {core.escape_html(book['author'])}\n"
        f"⭐ Final score: <b>{avg}</b>\n"
        f"👥 Responded: <b>{stats['responses']} of {expected}</b>\n"
        f"🙈 Didn't read: <b>{stats['not_read']}</b>\n\n"
        f"{hall_line}"
    )


async def _render_round(callback, book_id: int) -> None:
    async with _lock_for(book_id):
        session = await _get_session(book_id)
        book = await core.database.get_book_by_id(core.DATABASE_PATH, book_id)
        if not book:
            return
        if not session:
            session = await _create_session(book_id, int(book["chat_id"]))

        stats = await _response_stats(book_id)
        expected = max(1, int(session["expected_responses"]))
        should_close = session["status"] == "closed" or stats["responses"] >= expected

        if should_close:
            if session["status"] != "closed":
                await _close_session(book_id)
            # Refresh Hall of Fame aggregates before calculating the current rank.
            await core.database.update_hall_of_fame_rating(
                core.DATABASE_PATH,
                book_id,
                int(book["chat_id"]),
            )
            position, qualified = await _hall_position(int(book["chat_id"]), book_id)
            text = _final_text(
                await core.get_lang(int(book["chat_id"]), callback.from_user.id),
                book,
                stats,
                expected,
                position,
                qualified,
            )
            try:
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
            except Exception as exc:
                core.logger.debug(f"Could not render completed rating round for book {book_id}: {exc}")
            return

        text = _live_text(
            await core.get_lang(int(book["chat_id"]), callback.from_user.id),
            book,
            stats,
            expected,
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=core.get_final_rating_card_keyboard(book_id, await core.get_lang(int(book["chat_id"]), callback.from_user.id)),
            )
        except Exception as exc:
            core.logger.debug(f"Could not refresh rating progress for book {book_id}: {exc}")


class _RatingRoundMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        callback_data = getattr(event, "data", None) or ""

        # Freeze the expected participant count when reading is explicitly ended.
        if callback_data == "admin_finish_reading":
            chat_id = event.message.chat.id
            current = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
            result = await handler(event, data)
            if current:
                book = await core.database.get_book_by_id(core.DATABASE_PATH, int(current["id"]))
                if book and book.get("status") == "done":
                    await _create_session(int(current["id"]), chat_id)
            return result

        if not (callback_data.startswith("vote_book:") or callback_data.startswith("rate_read:")):
            return await handler(event, data)

        parts = callback_data.split(":")
        if len(parts) != 3:
            return await handler(event, data)
        try:
            book_id = int(parts[1])
        except ValueError:
            return await handler(event, data)

        session = await _get_session(book_id)
        if session and session.get("status") == "closed":
            lang = await core.get_lang(event.message.chat.id, event.from_user.id)
            await event.answer(
                label(lang, en="🏁 Rating is already complete.", ru="🏁 Итоговая оценка уже завершена."),
                show_alert=True,
            )
            await _render_round(event, book_id)
            return None

        result = await handler(event, data)
        await _render_round(event, book_id)
        return result


def _patch_help_copy() -> None:
    try:
        import i18n

        ru = i18n.STRINGS.get("help_msg", {}).get("ru", "")
        en = i18n.STRINGS.get("help_msg", {}).get("en", "")
        if ru and "100%" not in ru:
            i18n.STRINGS["help_msg"]["ru"] = ru + (
                "\n\nКогда ответили <b>100% участников</b> раунда, карточка оценки закрывается автоматически "
                "и показывает итоговый балл и текущее место книги в Зале славы. «Не читал» считается ответом."
            )
        if en and "100%" not in en:
            i18n.STRINGS["help_msg"]["en"] = en + (
                "\n\nWhen <b>100% of the rating round</b> have responded, the card closes automatically "
                "and shows the final score and current Hall of Fame position. “Didn't read” counts as a response."
            )
    except Exception as exc:
        core.logger.warning(f"Could not patch final-rating help copy: {exc}")


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # Enforce the product rule at the persistence layer: final responses are immutable.
    core.database.save_read_rating = _save_read_rating_once

    _patch_help_copy()
    core.router.callback_query.outer_middleware(_RatingRoundMiddleware())
