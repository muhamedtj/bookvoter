"""Book catalog input safety for BookVoter.

The Telegram library occasionally emits promotional/status messages around real
search results. Those messages must never become books. This runtime layer
validates search results before the suggestion UI sees them and quarantines
obviously malformed legacy backlog rows so they cannot block private ratings,
backlog views, or vote selection.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import bot_core as core


_installed = False
_original_fetch_books = None
_original_get_unrated = None
_original_get_backlog_full = None
_original_get_backlog = None
_original_get_top = None

_PROMO_MARKERS = (
    "слушайте аудиокниги",
    "аудиокниги на канале",
    "подписывайтесь на канал",
    "наш канал",
    "t.me/",
)


def _visible_text(value: Any) -> str:
    """Strip invisible/control Unicode that can masquerade as a title."""
    text = str(value or "")
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("C")).strip()


def is_valid_book_record(book: dict[str, Any]) -> bool:
    title = _visible_text(book.get("title"))
    author = _visible_text(book.get("author"))

    # A title must contain at least one real letter/number. This catches the
    # observed U+200C-only library artifact without rejecting normal punctuation.
    if not title or not any(ch.isalnum() for ch in title):
        return False

    combined = f"{title}\n{author}".casefold()
    if any(marker in combined for marker in _PROMO_MARKERS):
        return False

    # Telegram handles inside the author field are almost always library ads,
    # not bibliographic authors. Keep @ in titles untouched.
    if author and re.search(r"(?:^|\s)@[a-zA-Z0-9_]{4,}", author):
        return False

    return True


async def _quarantine_invalid_backlog(chat_id: int | None = None) -> int:
    """Move only clearly malformed backlog rows out of the active lifecycle."""
    params: list[Any] = []
    where = "WHERE status = 'backlog'"
    if chat_id is not None:
        where += " AND chat_id = ?"
        params.append(chat_id)

    async with core.database.open_db(core.DATABASE_PATH) as db:
        db.row_factory = core.aiosqlite.Row
        async with db.execute(
            f"SELECT id, title, author FROM books {where}",
            params,
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

        bad_ids = [int(row["id"]) for row in rows if not is_valid_book_record(row)]
        if bad_ids:
            placeholders = ",".join("?" * len(bad_ids))
            await db.execute(
                f"UPDATE books SET status = 'invalid' WHERE id IN ({placeholders}) AND status = 'backlog'",
                bad_ids,
            )
            await db.commit()
            core.logger.warning("Quarantined malformed BookVoter backlog rows: %s", bad_ids)
        return len(bad_ids)


async def _fetch_books_safe(query: str, lang: str = "en"):
    results = await _original_fetch_books(query, lang)
    if results is None:
        return None
    clean = []
    seen = set()
    for item in results:
        if not isinstance(item, dict) or not is_valid_book_record(item):
            core.logger.warning("Dropped malformed library search result for %r: %r", query, item)
            continue
        title = _visible_text(item.get("title"))
        author = _visible_text(item.get("author"))
        key = (title.casefold(), author.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(item)
        normalized["title"] = title
        normalized["author"] = author or "Unknown Author"
        clean.append(normalized)
    return clean


async def _get_unrated_safe(db_path: str, tg_id: int):
    # One malformed historical row should never trap the private rating queue.
    await _quarantine_invalid_backlog()
    rows = await _original_get_unrated(db_path, tg_id)
    return [row for row in rows if is_valid_book_record(row)]


async def _get_backlog_full_safe(db_path: str, chat_id: int):
    await _quarantine_invalid_backlog(chat_id)
    rows = await _original_get_backlog_full(db_path, chat_id)
    return [row for row in rows if is_valid_book_record(row)]


async def _get_backlog_safe(db_path: str, chat_id: int):
    await _quarantine_invalid_backlog(chat_id)
    rows = await _original_get_backlog(db_path, chat_id)
    return [row for row in rows if is_valid_book_record(row)]


async def _get_top_safe(db_path: str, chat_id: int, limit: int = 3):
    await _quarantine_invalid_backlog(chat_id)
    rows = await _original_get_top(db_path, chat_id, limit=limit)
    return [row for row in rows if is_valid_book_record(row)][:limit]


def install() -> None:
    global _installed
    global _original_fetch_books, _original_get_unrated, _original_get_backlog_full
    global _original_get_backlog, _original_get_top

    if _installed:
        return
    _installed = True

    _original_fetch_books = core.fetch_books_via_userbot
    core.fetch_books_via_userbot = _fetch_books_safe

    _original_get_unrated = core.database.get_unrated_backlog_books_for_user
    core.database.get_unrated_backlog_books_for_user = _get_unrated_safe

    _original_get_backlog_full = core.database.get_backlog_books_full_info
    core.database.get_backlog_books_full_info = _get_backlog_full_safe

    _original_get_backlog = core.database.get_backlog_books_for_chat
    core.database.get_backlog_books_for_chat = _get_backlog_safe

    # Installed after runtime_vote_eligibility so this wraps its quorum-aware
    # selector instead of replacing it.
    _original_get_top = core.database.get_top_backlog_books_for_vote
    core.database.get_top_backlog_books_for_vote = _get_top_safe

    core.is_valid_book_record = is_valid_book_record
