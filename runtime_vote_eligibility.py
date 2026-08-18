"""Reliable pre-vote eligibility for BookVoter.

A raw average from one person should not outrank a book rated by a meaningful
part of the club. Books become eligible for the Telegram vote only after they
reach a dynamic interest-rating quorum. The quorum scales with club size but is
capped so large groups do not get stuck forever.
"""

from __future__ import annotations

import math

import bot_core as core
import runtime_voting
from runtime_ux import label


_installed = False
_original_get_top = None


async def _estimated_human_members(chat_id: int) -> int:
    """Best-effort number of humans in the Telegram group."""
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
        except Exception:
            pass

        return max(1, total - len(bot_ids))
    except Exception as exc:
        core.logger.debug(f"Could not get Telegram member count for vote eligibility in {chat_id}: {exc}")

    # Fallback to members BookVoter has observed.
    try:
        async with core.database.open_db(core.DATABASE_PATH) as db:
            async with db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM chat_members WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return max(1, int((row or [1])[0] or 1))
    except Exception:
        return 1


async def required_interest_ratings(chat_id: int) -> int:
    """Dynamic quorum: 25% of humans, min 3 and max 10.

    Very small clubs are allowed to use all available humans instead of being
    permanently blocked by the minimum of three.
    """
    humans = await _estimated_human_members(chat_id)
    if humans <= 2:
        return max(1, humans)
    return min(10, max(3, math.ceil(humans * 0.25)))


async def _get_top_backlog_books_for_vote(db_path: str, chat_id: int, limit: int = 3):
    required = await required_interest_ratings(chat_id)
    async with core.database.open_db(db_path) as db:
        db.row_factory = core.aiosqlite.Row
        query = """
            SELECT b.id, b.chat_id, b.title, b.author, b.genre,
                   COALESCE(AVG(r.score), 0) AS avg_score,
                   COUNT(r.score) AS rating_count
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
            GROUP BY b.id
            HAVING COUNT(r.score) >= ?
            ORDER BY avg_score DESC, rating_count DESC, b.id ASC
            LIMIT ?
        """
        async with db.execute(query, (chat_id, required, limit)) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    for item in rows:
        raw_title = item["title"]
        item["raw_title"] = raw_title
        item["min_ratings_required"] = required
        # Keep the raw average visible, but also expose the sample size so users
        # can immediately see how trustworthy the pre-vote score is.
        item["title"] = (
            f"⭐ {float(item['avg_score']):.1f}/10 · "
            f"👥 {int(item['rating_count'])} · {raw_title}"
        )
    return rows


async def _validate_vote_ready(chat_id: int, lang: str):
    if await core.database.get_active_poll(core.DATABASE_PATH, chat_id):
        return False, core.t("vote_in_progress_err", lang), None

    current = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
    if current:
        return (
            False,
            core.t("active_reading_exists_err", lang, title=core.escape_html(current["title"])),
            None,
        )

    top_books = await core.database.get_top_backlog_books_for_vote(
        core.DATABASE_PATH,
        chat_id,
        limit=3,
    )
    if len(top_books) >= 2:
        return True, "", top_books

    required = await required_interest_ratings(chat_id)
    all_backlog = await core.database.get_backlog_books_full_info(core.DATABASE_PATH, chat_id)
    if not all_backlog:
        return False, core.t("no_backlog_books_err", lang), None

    ready = sum(1 for book in all_backlog if int(book.get("wish_votes_count") or 0) >= required)
    reason = label(
        lang,
        en=(
            f"📊 Not enough reliable interest ratings yet. Each book needs at least "
            f"{required} ratings before it can enter the vote. Ready now: {ready}. "
            f"Ask members to tap ‘Rate books’."
        ),
        ru=(
            f"📊 Пока недостаточно оценок интереса. Чтобы книга попала в голосование, "
            f"ей нужно минимум {required} оценок. Сейчас готовы: {ready}. "
            f"Попросите участников нажать «Оценить книги»."
        ),
    )
    return False, reason, None


def _patch_copy() -> None:
    try:
        import i18n

        i18n.STRINGS["no_backlog_books_err"]["ru"] = (
            "📊 Пока нет книг, готовых к голосованию. Добавьте книги и оцените интерес к ним."
        )
        i18n.STRINGS["no_backlog_books_err"]["en"] = (
            "📊 No books are ready for voting yet. Add books and rate your interest first."
        )
        i18n.STRINGS["min_two_books_err"]["ru"] = (
            "📊 Для голосования нужны минимум две книги с достаточным количеством оценок интереса."
        )
        i18n.STRINGS["min_two_books_err"]["en"] = (
            "📊 Voting needs at least two books with enough interest ratings."
        )

        ru = i18n.STRINGS.get("help_msg", {}).get("ru", "")
        en = i18n.STRINGS.get("help_msg", {}).get("en", "")
        if ru and "порог" not in ru.lower():
            i18n.STRINGS["help_msg"]["ru"] = ru + (
                "\n\n📊 В голосование попадают только книги, набравшие достаточный порог оценок интереса: "
                "25% участников клуба, минимум 3 и максимум 10 оценок. Это защищает рейтинг от перекоса одной оценкой."
            )
        if en and "25%" not in en:
            i18n.STRINGS["help_msg"]["en"] = en + (
                "\n\n📊 A book enters the vote only after reaching the interest-rating quorum: "
                "25% of club members, minimum 3 and maximum 10 ratings. This prevents one rating from skewing selection."
            )
    except Exception as exc:
        core.logger.warning(f"Could not patch vote-eligibility copy: {exc}")


def install() -> None:
    global _installed, _original_get_top
    if _installed:
        return
    _installed = True

    _original_get_top = core.database.get_top_backlog_books_for_vote
    core.database.get_top_backlog_books_for_vote = _get_top_backlog_books_for_vote

    # runtime_voting resolves this module-global function at callback execution,
    # so replacing it also improves public/admin vote-request error messages.
    runtime_voting._validate_vote_ready = _validate_vote_ready

    core.required_interest_ratings = required_interest_ratings
    _patch_copy()
