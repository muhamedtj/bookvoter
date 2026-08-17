import sqlite3
import aiosqlite
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, AsyncGenerator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_db(db_path: str) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Open SQLite connection with foreign keys and busy timeout enabled."""
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        yield db
    finally:
        await db.close()


async def init_db(db_path: str = "bookvoter.db") -> None:
    """Initialize database tables and run migrations."""
    async with open_db(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL;")

        # Table: users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                internal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                language_code TEXT
            );
        """)

        # Table: chats (club chats only, chat_id < 0)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'active',
                title TEXT,
                language_code TEXT
            );
        """)

        # Table: chat_members (multi-tenant membership tracking)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id),
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (internal_id) ON DELETE CASCADE
            );
        """)

        # Table: books
        await db.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                genre TEXT,
                status TEXT NOT NULL DEFAULT 'backlog',
                file_id TEXT,
                suggested_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE,
                FOREIGN KEY (suggested_by) REFERENCES users (internal_id) ON DELETE SET NULL
            );
        """)

        # Drop old unique index if it exists to allow preserving historical legacy duplicates
        await db.execute("DROP INDEX IF EXISTS idx_books_chat_title_author;")

        # BEFORE INSERT trigger for duplicate protection on new inserts without mutating legacy duplicates
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_duplicate_books
            BEFORE INSERT ON books
            BEGIN
                SELECT RAISE(ABORT, 'UNIQUE constraint failed: book already exists in this chat')
                WHERE EXISTS (
                    SELECT 1 FROM books
                    WHERE chat_id = NEW.chat_id
                      AND LOWER(TRIM(title)) = LOWER(TRIM(NEW.title))
                      AND LOWER(TRIM(author)) = LOWER(TRIM(NEW.author))
                );
            END;
        """)

        # Table: backlog_ratings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS backlog_ratings (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                score INTEGER NOT NULL CHECK (score >= 1 AND score <= 10),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, book_id),
                FOREIGN KEY (user_id) REFERENCES users (internal_id) ON DELETE CASCADE,
                FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
            );
        """)

        # Table: read_ratings (Post-reading scores 1-10 or NULL for "didn't read")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS read_ratings (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                score INTEGER CHECK (score IS NULL OR (score >= 1 AND score <= 10)),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, book_id),
                FOREIGN KEY (user_id) REFERENCES users (internal_id) ON DELETE CASCADE,
                FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
            );
        """)

        # Table: hall_of_fame
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hall_of_fame (
                book_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                final_club_rating REAL,
                votes_count INTEGER DEFAULT 0,
                is_hidden INTEGER DEFAULT 0,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE
            );
        """)

        # Table: active_polls
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_polls (
                chat_id INTEGER PRIMARY KEY,
                poll_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                options_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE
            );
        """)

        # Migrations for existing DBs
        for table in ["users", "chats"]:
            async with db.execute(f"PRAGMA table_info({table})") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                if "language_code" not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN language_code TEXT;")

        async with db.execute("PRAGMA table_info(backlog_ratings)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "created_at" not in columns:
                try:
                    await db.execute("ALTER TABLE backlog_ratings ADD COLUMN created_at TIMESTAMP;")
                except (sqlite3.OperationalError, aiosqlite.OperationalError, Exception) as e:
                    logger.warning(f"Failed or skipped adding created_at column to backlog_ratings: {e}")

        async with db.execute("PRAGMA table_info(hall_of_fame)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "votes_count" not in columns:
                try:
                    await db.execute("ALTER TABLE hall_of_fame ADD COLUMN votes_count INTEGER DEFAULT 0;")
                except (sqlite3.OperationalError, aiosqlite.OperationalError, Exception) as e:
                    logger.warning(f"Failed or skipped adding votes_count column to hall_of_fame: {e}")
            if "is_hidden" not in columns:
                try:
                    await db.execute("ALTER TABLE hall_of_fame ADD COLUMN is_hidden INTEGER DEFAULT 0;")
                except (sqlite3.OperationalError, aiosqlite.OperationalError, Exception) as e:
                    logger.warning(f"Failed or skipped adding is_hidden column to hall_of_fame: {e}")

        # Migration: legacy status migration to proper lifecycle 'reading'
        # legacy won not in Hall of Fame -> reading
        # legacy done not in Hall of Fame -> reading
        await db.execute("""
            UPDATE books
            SET status = 'reading'
            WHERE status IN ('won', 'done')
              AND id NOT IN (SELECT book_id FROM hall_of_fame);
        """)

        await db.commit()


async def get_or_create_user(db_path: str, tg_id: int, username: Optional[str] = None, full_name: Optional[str] = None) -> int:
    """Fetch internal_id for tg_id, creating the record if it doesn't exist."""
    async with open_db(db_path) as db:
        async with db.execute("SELECT internal_id, username, full_name FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                internal_id, curr_username, curr_fullname = row
                new_username = username if username is not None else curr_username
                new_fullname = full_name if full_name is not None else curr_fullname
                if curr_username != new_username or curr_fullname != new_fullname:
                    await db.execute(
                        "UPDATE users SET username = ?, full_name = ? WHERE internal_id = ?",
                        (new_username, new_fullname, internal_id)
                    )
                    await db.commit()
                return internal_id

        cursor = await db.execute(
            "INSERT INTO users (tg_id, username, full_name) VALUES (?, ?, ?)",
            (tg_id, username, full_name)
        )
        await db.commit()
        return cursor.lastrowid


async def register_or_update_chat(db_path: str, chat_id: int, title: Optional[str] = None, status: str = "active") -> None:
    """Register or update a Telegram club chat in DB (groups/supergroups only, chat_id < 0)."""
    if chat_id > 0:
        return
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO chats (chat_id, status, title)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                status = excluded.status,
                title = COALESCE(excluded.title, chats.title);
        """, (chat_id, status, title))
        await db.commit()


async def record_chat_member(db_path: str, chat_id: int, tg_id: int) -> None:
    """Record or update user membership in a group/supergroup chat."""
    if chat_id > 0:
        return
    await register_or_update_chat(db_path, chat_id)
    user_internal_id = await get_or_create_user(db_path, tg_id)
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO chat_members (chat_id, user_id, last_seen_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
        """, (chat_id, user_internal_id))
        await db.commit()


async def is_user_chat_member(db_path: str, chat_id: int, tg_id: int) -> bool:
    """Check if user is recorded as a member of the given group chat."""
    if chat_id > 0:
        return False
    async with open_db(db_path) as db:
        async with db.execute("""
            SELECT 1 FROM chat_members cm
            JOIN users u ON cm.user_id = u.internal_id
            WHERE cm.chat_id = ? AND u.tg_id = ?
        """, (chat_id, tg_id)) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def set_chat_language(db_path: str, chat_id: int, language_code: str) -> None:
    """Set language preference for a chat."""
    if chat_id > 0:
        return
    await register_or_update_chat(db_path, chat_id)
    async with open_db(db_path) as db:
        await db.execute("UPDATE chats SET language_code = ? WHERE chat_id = ?", (language_code, chat_id))
        await db.commit()


async def get_chat_language(db_path: str, chat_id: int) -> Optional[str]:
    """Get language preference for a chat (returns None if not set)."""
    if chat_id > 0:
        return None
    async with open_db(db_path) as db:
        async with db.execute("SELECT language_code FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None


async def set_user_language(db_path: str, tg_id: int, language_code: str) -> None:
    """Set language preference for a user."""
    async with open_db(db_path) as db:
        await db.execute("UPDATE users SET language_code = ? WHERE tg_id = ?", (language_code, tg_id))
        await db.commit()


async def get_user_language(db_path: str, tg_id: int) -> Optional[str]:
    """Get language preference for a user (returns None if not set)."""
    async with open_db(db_path) as db:
        async with db.execute("SELECT language_code FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None


async def get_effective_language(db_path: str, chat_id: int, user_tg_id: Optional[int] = None) -> str:
    """
    Get effective language preference:
    If chat is private (chat_id > 0), user language is used.
    If chat is group (chat_id < 0), chat language is used.
    Defaults to 'en' if not explicitly set.
    """
    if chat_id > 0:
        lang = await get_user_language(db_path, chat_id)
        if not lang and user_tg_id:
            lang = await get_user_language(db_path, user_tg_id)
        return lang if lang else "en"
    else:
        lang = await get_chat_language(db_path, chat_id)
        return lang if lang else "en"


async def is_book_exists(db_path: str, chat_id: int, title: str, author: str) -> bool:
    """Check if a book with matching title and author already exists in chat's records."""
    async with open_db(db_path) as db:
        async with db.execute("""
            SELECT id FROM books
            WHERE chat_id = ? AND LOWER(TRIM(title)) = LOWER(TRIM(?)) AND LOWER(TRIM(author)) = LOWER(TRIM(?))
        """, (chat_id, title, author)) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def add_book(
    db_path: str,
    chat_id: int,
    title: str,
    author: str,
    genre: Optional[str] = None,
    suggested_by_tg_id: Optional[int] = None,
    file_id: Optional[str] = None
) -> int:
    """Add a new book suggestion with status='backlog'."""
    await register_or_update_chat(db_path, chat_id)
    async with open_db(db_path) as db:
        internal_id = None
        if suggested_by_tg_id:
            internal_id = await get_or_create_user(db_path, suggested_by_tg_id)

        cursor = await db.execute("""
            INSERT INTO books (chat_id, title, author, genre, status, suggested_by, file_id)
            VALUES (?, ?, ?, ?, 'backlog', ?, ?)
        """, (chat_id, title, author, genre, internal_id, file_id))
        await db.commit()
        return cursor.lastrowid


async def delete_book(db_path: str, book_id: int, chat_id: int) -> bool:
    """Delete a book from database for a specific chat."""
    async with open_db(db_path) as db:
        cursor = await db.execute("DELETE FROM books WHERE id = ? AND chat_id = ?", (book_id, chat_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_backlog_books_for_chat(db_path: str, chat_id: int) -> List[Dict[str, Any]]:
    """Fetch all backlog books for a group chat."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, chat_id, title, author, genre, status
            FROM books
            WHERE chat_id = ? AND status = 'backlog'
            ORDER BY id DESC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_backlog_books_full_info(db_path: str, chat_id: int) -> List[Dict[str, Any]]:
    """Fetch full backlog info for a chat including suggestor user info and average wish score."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT b.id, b.title, b.author, b.genre, b.status, b.created_at,
                   u.tg_id as suggestor_tg_id, u.full_name as suggestor_name, u.username as suggestor_username,
                   COALESCE(AVG(r.score), 0) as wish_score,
                   COUNT(r.score) as wish_votes_count
            FROM books b
            LEFT JOIN users u ON b.suggested_by = u.internal_id
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
            GROUP BY b.id
            ORDER BY wish_score DESC, b.id ASC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_unrated_backlog_books_for_user(db_path: str, tg_id: int) -> List[Dict[str, Any]]:
    """Fetch all backlog books across active chats where user is a registered member that user hasn't rated yet."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT b.id, b.chat_id, b.title, b.author, b.genre, c.title as chat_title
            FROM books b
            JOIN chats c ON b.chat_id = c.chat_id
            JOIN chat_members cm ON b.chat_id = cm.chat_id
            JOIN users u ON cm.user_id = u.internal_id
            WHERE u.tg_id = ?
              AND b.status = 'backlog'
              AND c.status = 'active'
              AND c.chat_id < 0
              AND b.id NOT IN (
                  SELECT book_id FROM backlog_ratings WHERE user_id = u.internal_id
              )
            ORDER BY b.id ASC
        """, (tg_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_backlog_rating(db_path: str, tg_id: int, book_id: int, score: int) -> None:
    """Save user score (1-10) for a backlog book."""
    internal_id = await get_or_create_user(db_path, tg_id)
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO backlog_ratings (user_id, book_id, score, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, book_id) DO UPDATE SET score = excluded.score, created_at = CURRENT_TIMESTAMP
        """, (internal_id, book_id, score))
        await db.commit()


async def save_read_rating(db_path: str, tg_id: int, book_id: int, score: Optional[int]) -> None:
    """Save post-reading score (1-10 or None for didn't read) for a finished book."""
    internal_id = await get_or_create_user(db_path, tg_id)
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO read_ratings (user_id, book_id, score, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, book_id) DO UPDATE SET score = excluded.score, created_at = CURRENT_TIMESTAMP
        """, (internal_id, book_id, score))
        await db.commit()


async def update_hall_of_fame_rating(db_path: str, book_id: int, chat_id: int) -> Dict[str, Any]:
    """Calculate average post-reading rating and votes count, update Hall of Fame and book status to 'done'."""
    await register_or_update_chat(db_path, chat_id)
    async with open_db(db_path) as db:
        async with db.execute("""
            SELECT COALESCE(AVG(score), 0), COUNT(score)
            FROM read_ratings
            WHERE book_id = ? AND score IS NOT NULL AND score >= 1
        """, (book_id,)) as cursor:
            row = await cursor.fetchone()
            avg_rating = round(row[0], 2) if row else 0.0
            votes_count = row[1] if row else 0

        await db.execute("""
            INSERT INTO hall_of_fame (book_id, chat_id, final_club_rating, votes_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET
                final_club_rating = excluded.final_club_rating,
                votes_count = excluded.votes_count
        """, (book_id, chat_id, avg_rating, votes_count))

        # Ensure status is 'done'
        await db.execute("UPDATE books SET status = 'done' WHERE id = ?", (book_id,))
        await db.commit()

        return {"avg_rating": avg_rating, "votes_count": votes_count}


async def get_top_backlog_books_for_vote(db_path: str, chat_id: int, limit: int = 3) -> List[Dict[str, Any]]:
    """
    Select top N backlog books based on average interest score from backlog_ratings.
    Ordered by avg_score DESC, b.id ASC.
    """
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT b.id, b.chat_id, b.title, b.author, b.genre,
                   COALESCE(AVG(r.score), 0) as avg_score,
                   COUNT(r.score) as rating_count
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
            GROUP BY b.id
            ORDER BY avg_score DESC, b.id ASC
            LIMIT ?
        """
        async with db.execute(query, (chat_id, limit)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_interest_scores_for_books(db_path: str, book_ids: List[int]) -> Dict[int, float]:
    """Retrieve pre-vote average interest scores for explicit book IDs regardless of current status."""
    if not book_ids:
        return {}
    async with open_db(db_path) as db:
        placeholders = ",".join("?" * len(book_ids))
        query = f"""
            SELECT b.id, COALESCE(AVG(r.score), 0) as avg_score
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.id IN ({placeholders})
            GROUP BY b.id
        """
        async with db.execute(query, book_ids) as cursor:
            rows = await cursor.fetchall()
            return {r[0]: float(r[1]) for r in rows}


async def update_books_status(db_path: str, book_ids: List[int], status: str) -> None:
    """Bulk update status for books."""
    if not book_ids:
        return
    async with open_db(db_path) as db:
        placeholders = ",".join("?" * len(book_ids))
        await db.execute(f"UPDATE books SET status = ? WHERE id IN ({placeholders})", [status] + list(book_ids))
        await db.commit()


async def save_active_poll(db_path: str, chat_id: int, poll_id: str, message_id: int, options_json: str) -> None:
    """Save details of currently running Telegram Poll for a chat."""
    await register_or_update_chat(db_path, chat_id)
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO active_polls (chat_id, poll_id, message_id, options_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                poll_id = excluded.poll_id,
                message_id = excluded.message_id,
                options_json = excluded.options_json,
                created_at = CURRENT_TIMESTAMP
        """, (chat_id, poll_id, message_id, options_json))
        await db.commit()


async def get_active_poll(db_path: str, chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve active poll for a chat by chat_id."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_polls WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_active_poll_by_poll_id(db_path: str, poll_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve active poll by poll_id."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_polls WHERE poll_id = ?", (poll_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_active_polls(db_path: str) -> List[Dict[str, Any]]:
    """Retrieve all active polls across chats for startup recovery."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_polls") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def clear_active_poll(db_path: str, chat_id: int) -> None:
    """Remove active poll record for a chat."""
    async with open_db(db_path) as db:
        await db.execute("DELETE FROM active_polls WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def resolve_vote_winner(db_path: str, chat_id: int, winning_book_id: int, voting_book_ids: List[int]) -> None:
    """
    Mark winning_book_id as 'reading', and revert other voting_book_ids to 'backlog'.
    """
    async with open_db(db_path) as db:
        # Revert non-winners to backlog
        other_ids = [bid for bid in voting_book_ids if bid != winning_book_id]
        if other_ids:
            placeholders = ",".join("?" * len(other_ids))
            await db.execute(f"UPDATE books SET status = 'backlog' WHERE id IN ({placeholders})", other_ids)

        # Set winner status to 'reading'
        await db.execute("UPDATE books SET status = 'reading' WHERE id = ?", (winning_book_id,))
        await db.commit()


async def mark_book_done(db_path: str, book_id: int, file_id: str) -> None:
    """Update book file_id without changing lifecycle status (remains 'reading')."""
    async with open_db(db_path) as db:
        await db.execute("UPDATE books SET file_id = ? WHERE id = ?", (file_id, book_id))
        await db.commit()


async def update_book_file_id(db_path: str, book_id: int, file_id: str) -> None:
    """Store downloaded file_id on book record."""
    await mark_book_done(db_path, book_id, file_id)


async def add_to_hall_of_fame(db_path: str, book_id: int, chat_id: int, final_rating: Optional[float] = None) -> None:
    """Add completed book to Hall of Fame."""
    await register_or_update_chat(db_path, chat_id)
    async with open_db(db_path) as db:
        await db.execute("""
            INSERT INTO hall_of_fame (book_id, chat_id, final_club_rating)
            VALUES (?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET final_club_rating = excluded.final_club_rating
        """, (book_id, chat_id, final_rating))
        await db.commit()


async def hide_book_from_hall_of_fame(db_path: str, book_id: int, chat_id: int) -> bool:
    """Soft delete / hide a book from Hall of Fame for a chat."""
    async with open_db(db_path) as db:
        cursor = await db.execute("""
            UPDATE hall_of_fame SET is_hidden = 1 WHERE book_id = ? AND chat_id = ?
        """, (book_id, chat_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_hall_of_fame_detailed(db_path: str, chat_id: int, min_votes: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get Hall of Fame books for a chat split into qualified (>= min_votes) and low_votes (< min_votes).
    Calculates live average read_score (R), vote count (v), global club average (C), and Bayesian Weighted Rating (WR):
    WR = (v / (v + m)) * R + (m / (v + m)) * C
    Books with 0 actual read ratings (v = 0) yield weighted_rating = 0.0 and belong in low_votes.
    """
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Calculate C: Mean average score across all read_ratings in this chat
        async with db.execute("""
            SELECT COALESCE(AVG(r.score), 7.0)
            FROM read_ratings r
            JOIN books b ON r.book_id = b.id
            JOIN hall_of_fame h ON b.id = h.book_id
            WHERE h.chat_id = ? AND h.is_hidden = 0 AND r.score IS NOT NULL AND r.score >= 1
        """, (chat_id,)) as cursor:
            global_avg_c = (await cursor.fetchone())[0]
            if not global_avg_c:
                global_avg_c = 7.0

        async with db.execute("""
            SELECT b.id, b.title, b.author, b.genre, h.completed_at,
                   COALESCE(AVG(r.score), h.final_club_rating, 0) as avg_score,
                   COUNT(r.score) as live_votes_count
            FROM hall_of_fame h
            JOIN books b ON h.book_id = b.id
            LEFT JOIN read_ratings r ON b.id = r.book_id AND r.score IS NOT NULL AND r.score >= 1
            WHERE h.chat_id = ? AND h.is_hidden = 0
            GROUP BY b.id
        """, (chat_id,)) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

        m = float(min_votes)
        c = float(global_avg_c)

        qualified = []
        low_votes = []
        for r in rows:
            v = float(r["live_votes_count"])
            avg_r = float(r["avg_score"])

            # Bayesian Weighted Rating
            if v > 0:
                weighted_rating = (v / (v + m)) * avg_r + (m / (v + m)) * c
            else:
                weighted_rating = 0.0

            r["avg_score"] = round(avg_r, 2)
            r["weighted_rating"] = round(weighted_rating, 2)

            if v >= min_votes and v > 0:
                qualified.append(r)
            else:
                low_votes.append(r)

        # Sort qualified books by weighted rating (WR) descending
        qualified.sort(key=lambda x: (x["weighted_rating"], x["live_votes_count"], x["avg_score"]), reverse=True)
        # Sort low votes books by average score descending
        low_votes.sort(key=lambda x: (x["avg_score"], x["live_votes_count"]), reverse=True)

        return {"qualified": qualified, "low_votes": low_votes}


async def get_hall_of_fame(db_path: str, chat_id: int) -> List[Dict[str, Any]]:
    """Get all Hall of Fame books for a chat."""
    res = await get_hall_of_fame_detailed(db_path, chat_id, min_votes=0)
    return res["qualified"] + res["low_votes"]


async def audit_backlog_activity(db_path: str, chat_id: int, active_tg_ids: List[int]) -> List[Dict[str, Any]]:
    """
    Audit books in backlog for a chat:
    1. Suggested by a user who is not in active_tg_ids (departed member).
    2. Suggested by a user who has not rated any books in the backlog for this chat (inactive member).
    """
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT b.id, b.title, b.author, b.genre, b.created_at,
                   u.tg_id as suggestor_tg_id, u.full_name as suggestor_name, u.username as suggestor_username,
                   u.internal_id as suggestor_internal_id
            FROM books b
            LEFT JOIN users u ON b.suggested_by = u.internal_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
            ORDER BY b.id DESC
        """, (chat_id,)) as cursor:
            books = [dict(r) for r in await cursor.fetchall()]

        audit_results = []
        for b in books:
            s_tg = b["suggestor_tg_id"]
            reasons = []

            if s_tg and s_tg not in active_tg_ids:
                reasons.append("departed")

            # Check voter activity in THIS specific chat
            s_internal = b["suggestor_internal_id"]
            if s_internal:
                async with open_db(db_path) as db2:
                    async with db2.execute("""
                        SELECT COUNT(*)
                        FROM backlog_ratings r
                        JOIN books bk ON r.book_id = bk.id
                        WHERE r.user_id = ? AND bk.chat_id = ?
                    """, (s_internal, chat_id)) as r_cursor:
                        vote_count = (await r_cursor.fetchone())[0]
                        if vote_count == 0:
                            reasons.append("inactive")

            if reasons:
                b["reasons"] = reasons
                audit_results.append(b)

        return audit_results


async def get_chat_stats_detailed(db_path: str, chat_id: int, days: Optional[int] = None) -> Dict[str, Any]:
    """Get detailed chat metrics with optional time filter (e.g. days=30)."""
    async with open_db(db_path) as db:
        time_filter_books = ""
        time_filter_hof = ""
        time_filter_ratings = ""
        params_books = [chat_id]
        params_hof = [chat_id]
        params_ratings = [chat_id]

        if days:
            time_filter_books = " AND b.created_at >= datetime('now', ?)"
            time_filter_hof = " AND h.completed_at >= datetime('now', ?)"
            time_filter_ratings = " AND r.created_at >= datetime('now', ?)"
            params_books.append(f"-{days} days")
            params_hof.append(f"-{days} days")
            params_ratings.append(f"-{days} days")

        # Backlog count
        async with db.execute(f"SELECT COUNT(*) FROM books b WHERE b.chat_id = ? AND b.status = 'backlog'{time_filter_books}", params_books) as cursor:
            backlog_count = (await cursor.fetchone())[0]

        # Completed books count (status 'done' and in hall_of_fame filtered by completed_at)
        async with db.execute(f"""
            SELECT COUNT(*)
            FROM hall_of_fame h
            JOIN books b ON h.book_id = b.id
            WHERE h.chat_id = ?{time_filter_hof}
        """, params_hof) as cursor:
            done_count = (await cursor.fetchone())[0]

        # Average wish rating
        async with db.execute(f"""
            SELECT COALESCE(AVG(r.score), 0) FROM backlog_ratings r
            JOIN books b ON r.book_id = b.id
            WHERE b.chat_id = ?{time_filter_ratings}
        """, params_ratings) as cursor:
            avg_club_rating = (await cursor.fetchone())[0]

        # Top Contributors (most suggested books)
        async with db.execute(f"""
            SELECT u.full_name, u.username, COUNT(b.id) as cnt
            FROM books b
            JOIN users u ON b.suggested_by = u.internal_id
            WHERE b.chat_id = ?{time_filter_books}
            GROUP BY u.internal_id
            ORDER BY cnt DESC LIMIT 3
        """, params_books) as cursor:
            top_contributors = [{"name": r[0] or r[1] or "User", "count": r[2]} for r in await cursor.fetchall()]

        # Top Voters (most active raters)
        async with db.execute(f"""
            SELECT u.full_name, u.username, COUNT(r.book_id) as cnt
            FROM backlog_ratings r
            JOIN users u ON r.user_id = u.internal_id
            JOIN books b ON r.book_id = b.id
            WHERE b.chat_id = ?{time_filter_ratings}
            GROUP BY u.internal_id
            ORDER BY cnt DESC LIMIT 3
        """, params_ratings) as cursor:
            top_voters = [{"name": r[0] or r[1] or "User", "count": r[2]} for r in await cursor.fetchall()]

        return {
            "backlog_count": backlog_count,
            "done_count": done_count,
            "avg_club_rating": round(avg_club_rating, 2),
            "top_contributors": top_contributors,
            "top_voters": top_voters
        }


async def get_superadmin_stats_detailed(db_path: str, days: Optional[int] = None) -> Dict[str, Any]:
    """Get global aggregated superadmin metrics with optional time filter."""
    async with open_db(db_path) as db:
        time_filter_books = ""
        time_filter_ratings = ""
        params_books = []
        params_ratings = []

        if days:
            time_filter_books = " WHERE b.created_at >= datetime('now', ?)"
            time_filter_ratings = " WHERE r.created_at >= datetime('now', ?)"
            params_books.append(f"-{days} days")
            params_ratings.append(f"-{days} days")

        async with db.execute("SELECT COUNT(*) FROM chats WHERE status = 'active' AND chat_id < 0", ()) as cursor:
            total_active_chats = (await cursor.fetchone())[0]

        async with db.execute(f"SELECT COUNT(DISTINCT r.user_id) FROM backlog_ratings r{time_filter_ratings}", params_ratings) as cursor:
            total_voters = (await cursor.fetchone())[0]

        async with db.execute(f"SELECT COUNT(*) FROM books b JOIN chats c ON b.chat_id = c.chat_id WHERE c.chat_id < 0{time_filter_books.replace('WHERE', 'AND') if time_filter_books else ''}", params_books) as cursor:
            total_books_suggested = (await cursor.fetchone())[0]

        # Top books by wish rating
        async with db.execute(f"""
            SELECT b.title, b.author, COALESCE(AVG(r.score), 0) as avg_s
            FROM books b
            JOIN backlog_ratings r ON b.id = r.book_id
            JOIN chats c ON b.chat_id = c.chat_id
            WHERE c.chat_id < 0 {time_filter_ratings.replace('WHERE', 'AND') if time_filter_ratings else ''}
            GROUP BY b.id
            ORDER BY avg_s DESC LIMIT 3
        """, params_ratings) as cursor:
            top_books = [{"title": r[0], "author": r[1], "score": round(r[2], 2)} for r in await cursor.fetchall()]

        return {
            "total_active_chats": total_active_chats,
            "total_voters": total_voters,
            "total_books_suggested": total_books_suggested,
            "top_books": top_books
        }


async def get_sys_stats(db_path: str) -> Dict[str, Any]:
    """Get global system statistics for superadmin."""
    async with open_db(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM chats WHERE status = 'active' AND chat_id < 0", ()) as cursor:
            total_active_chats = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM users", ()) as cursor:
            total_unique_users = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM books b JOIN chats c ON b.chat_id = c.chat_id WHERE b.status = 'done' AND c.chat_id < 0", ()) as cursor:
            total_downloaded_books = (await cursor.fetchone())[0]

        return {
            "total_active_chats": total_active_chats,
            "total_unique_users": total_unique_users,
            "total_downloaded_books": total_downloaded_books
        }


async def get_book_by_id(db_path: str, book_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve book details by ID."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM books WHERE id = ?", (book_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_current_reading_book(db_path: str, chat_id: int) -> Optional[Dict[str, Any]]:
    """Get currently active reading book for a club chat (status = 'reading')."""
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM books
            WHERE chat_id = ? AND status = 'reading'
            ORDER BY id DESC LIMIT 1
        """, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_current_winning_or_reading_book(db_path: str, chat_id: int) -> Optional[Dict[str, Any]]:
    """Backward compatibility alias for get_current_reading_book."""
    return await get_current_reading_book(db_path, chat_id)
