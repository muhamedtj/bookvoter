"""Participation-first ranking for BookVoter interest ratings.

The waiting list and the shortlist for the main Telegram poll must not let a
small sample outrank broad club interest. Rank first by number of people who
rated a book, then by the average interest score. The waiting-list UI also
shows the number of ratings explicitly.
"""

from __future__ import annotations

import aiosqlite

import bot_core as core


_installed = False
_original_get_backlog_full = None
_original_send_split_messages = None


async def _get_backlog_full_ranked(db_path: str, chat_id: int):
    rows = await _original_get_backlog_full(db_path, chat_id)
    return sorted(
        rows,
        key=lambda b: (
            -int(b.get("wish_votes_count") or 0),
            -float(b.get("wish_score") or 0),
            int(b.get("id") or 0),
        ),
    )


async def _get_top_participation_first(db_path: str, chat_id: int, limit: int = 5):
    required = await core.required_interest_ratings(chat_id)
    async with core.database.open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT b.id, b.chat_id, b.title, b.author, b.genre,
                   COALESCE(AVG(r.score), 0) AS avg_score,
                   COUNT(r.score) AS rating_count
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
            GROUP BY b.id
            HAVING COUNT(r.score) >= ?
            ORDER BY rating_count DESC, avg_score DESC, b.id ASC
            LIMIT ?
            """,
            (chat_id, required, limit),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

    valid = getattr(core, "is_valid_book_record", None)
    if callable(valid):
        rows = [row for row in rows if valid(row)]

    for item in rows:
        raw_title = item["title"]
        item["raw_title"] = raw_title
        item["min_ratings_required"] = required
        item["title"] = (
            f"👥 {int(item['rating_count'])} · "
            f"⭐ {float(item['avg_score']):.1f}/10 · {raw_title}"
        )
    return rows


def _target_chat_id(target) -> int | None:
    message = getattr(target, "message", None)
    chat = getattr(message, "chat", None) if message else getattr(target, "chat", None)
    return getattr(chat, "id", None)


async def _send_split_with_interest_counts(target, text: str, *args, **kwargs):
    """Rebuild only the waiting-list view so rating sample size is visible."""
    plain = str(text or "")
    if plain.startswith(("📚 <b>Бэклог клуба</b>", "📚 <b>Club Backlog</b>")):
        chat_id = _target_chat_id(target)
        if chat_id and chat_id < 0:
            try:
                books = await core.database.get_backlog_books_full_info(core.DATABASE_PATH, chat_id)
                lang = "ru" if plain.startswith("📚 <b>Бэклог клуба</b>") else "en"
                rebuilt = core.t("backlog_list_header", lang)
                for idx, book in enumerate(books, 1):
                    suggestor = book.get("suggestor_name") or (
                        f"@{book['suggestor_username']}" if book.get("suggestor_username") else "N/A"
                    )
                    rebuilt += (
                        f"{idx}. <b>{core.escape_html(book['title'])}</b> — {core.escape_html(book['author'])}\n"
                        f"   👥 {int(book.get('wish_votes_count') or 0)} · "
                        f"⭐ {float(book.get('wish_score') or 0):.1f}/10 · "
                        f"👤 {core.escape_html(suggestor)}\n\n"
                    )
                text = rebuilt
            except Exception as exc:
                core.logger.warning(f"Could not render backlog interest counts in {chat_id}: {exc}")

    return await _original_send_split_messages(target, text, *args, **kwargs)


def install() -> None:
    global _installed, _original_get_backlog_full, _original_send_split_messages
    if _installed:
        return
    _installed = True

    # Wrap the already-installed catalog-safety selector so malformed historical
    # rows remain quarantined before ranking/rendering.
    _original_get_backlog_full = core.database.get_backlog_books_full_info
    core.database.get_backlog_books_full_info = _get_backlog_full_ranked

    # Replace the shortlist selector after vote-eligibility/catalog-safety are
    # installed. Quorum is preserved, but participation is the primary rank.
    core.database.get_top_backlog_books_for_vote = _get_top_participation_first

    _original_send_split_messages = core.send_split_messages
    core.send_split_messages = _send_split_with_interest_counts
