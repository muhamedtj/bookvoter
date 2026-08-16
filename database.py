import aiosqlite
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

async def init_db(db_path: str = "bookvoter.db") -> None:
    """Initialize database tables and run migrations."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")

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

        # Table: chats
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'active',
                title TEXT,
                language_code TEXT
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

        # Table: backlog_ratings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS backlog_ratings (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                score INTEGER NOT NULL CHECK (score >= 1 AND score <= 10),
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

        # Migrations for existing DBs if language_code column doesn't exist
        for table in ["users", "chats"]:
            async with db.execute(f"PRAGMA table_info({table})") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                if "language_code" not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN language_code TEXT;")

        await db.commit()


async def get_or_create_user(db_path: str, tg_id: int, username: Optional[str] = None, full_name: Optional[str] = None) -> int:
    """Fetch internal_id for tg_id, creating the record if it doesn't exist."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT internal_id, username, full_name FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                internal_id, curr_username, curr_fullname = row
                if curr_username != username or curr_fullname != full_name:
                    await db.execute(
                        "UPDATE users SET username = ?, full_name = ? WHERE internal_id = ?",
                        (username, full_name, internal_id)
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
    """Register or update a Telegram chat in DB."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO chats (chat_id, status, title)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                status = excluded.status,
                title = COALESCE(excluded.title, chats.title);
        """, (chat_id, status, title))
        await db.commit()


async def set_chat_language(db_path: str, chat_id: int, language_code: str) -> None:
    """Set language preference for a chat."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE chats SET language_code = ? WHERE chat_id = ?", (language_code, chat_id))
        await db.commit()


async def get_chat_language(db_path: str, chat_id: int) -> Optional[str]:
    """Get language preference for a chat (returns None if not set)."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT language_code FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None


async def set_user_language(db_path: str, tg_id: int, language_code: str) -> None:
    """Set language preference for a user."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET language_code = ? WHERE tg_id = ?", (language_code, tg_id))
        await db.commit()


async def get_user_language(db_path: str, tg_id: int) -> Optional[str]:
    """Get language preference for a user (returns None if not set)."""
    async with aiosqlite.connect(db_path) as db:
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
    async with aiosqlite.connect(db_path) as db:
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
    suggested_by_tg_id: Optional[int] = None
) -> int:
    """Add a new book suggestion with status='backlog'."""
    async with aiosqlite.connect(db_path) as db:
        # Ensure chat exists
        await register_or_update_chat(db_path, chat_id)

        internal_id = None
        if suggested_by_tg_id:
            internal_id = await get_or_create_user(db_path, suggested_by_tg_id)

        cursor = await db.execute("""
            INSERT INTO books (chat_id, title, author, genre, status, suggested_by)
            VALUES (?, ?, ?, ?, 'backlog', ?)
        """, (chat_id, title, author, genre, internal_id))
        await db.commit()
        return cursor.lastrowid


async def delete_book(db_path: str, book_id: int, chat_id: int) -> bool:
    """Delete a book from database for a specific chat."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM books WHERE id = ? AND chat_id = ?", (book_id, chat_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_backlog_books_for_chat(db_path: str, chat_id: int) -> List[Dict[str, Any]]:
    """Fetch all backlog / non-finished books for a group chat."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, chat_id, title, author, genre, status
            FROM books
            WHERE chat_id = ? AND status IN ('backlog', 'voting')
            ORDER BY id DESC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_available_genres_in_backlog(db_path: str, chat_id: int) -> List[str]:
    """Get list of distinct non-empty genres present in a chat's backlog."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT DISTINCT genre FROM books
            WHERE chat_id = ? AND status = 'backlog' AND genre IS NOT NULL AND TRIM(genre) != ''
            ORDER BY genre ASC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_backlog_books_by_genre(db_path: str, chat_id: int, genre: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch backlog books optionally filtered by genre, sorted by average rating in descending order.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        base_query = """
            SELECT b.id, b.chat_id, b.title, b.author, b.genre,
                   COALESCE(AVG(r.score), 0) as avg_score,
                   COUNT(r.score) as rating_count
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
        """
        params = [chat_id]
        if genre and genre.lower() != "all":
            base_query += " AND LOWER(b.genre) = LOWER(?)"
            params.append(genre)

        base_query += " GROUP BY b.id ORDER BY avg_score DESC, b.id ASC"

        async with db.execute(base_query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_unrated_backlog_books_for_user(db_path: str, tg_id: int) -> List[Dict[str, Any]]:
    """Fetch all backlog books across chats where user is active that user hasn't rated yet."""
    internal_id = await get_or_create_user(db_path, tg_id)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT b.id, b.chat_id, b.title, b.author, b.genre, c.title as chat_title
            FROM books b
            JOIN chats c ON b.chat_id = c.chat_id
            WHERE b.status = 'backlog'
              AND c.status = 'active'
              AND b.id NOT IN (
                  SELECT book_id FROM backlog_ratings WHERE user_id = ?
              )
            ORDER BY b.id ASC
        """, (internal_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_backlog_rating(db_path: str, tg_id: int, book_id: int, score: int) -> None:
    """Save user score (1-10) for a backlog book."""
    internal_id = await get_or_create_user(db_path, tg_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO backlog_ratings (user_id, book_id, score)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET score = excluded.score
        """, (internal_id, book_id, score))
        await db.commit()


async def get_last_read_genre(db_path: str, chat_id: int) -> Optional[str]:
    """Get genre of the most recently finished/won book in the chat."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT genre FROM books
            WHERE chat_id = ? AND status IN ('won', 'done') AND genre IS NOT NULL AND TRIM(genre) != ''
            ORDER BY id DESC LIMIT 1
        """, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_top_backlog_books_for_vote(db_path: str, chat_id: int, limit: int = 3) -> List[Dict[str, Any]]:
    """
    Select top N backlog books based on average rating.
    Excludes the genre of the previously read book if possible.
    """
    last_genre = await get_last_read_genre(db_path, chat_id)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Base query calculating average score
        base_query = """
            SELECT b.id, b.chat_id, b.title, b.author, b.genre,
                   COALESCE(AVG(r.score), 0) as avg_score,
                   COUNT(r.score) as rating_count
            FROM books b
            LEFT JOIN backlog_ratings r ON b.id = r.book_id
            WHERE b.chat_id = ? AND b.status = 'backlog'
        """

        # If we have a last_genre, try fetching excluding that genre first
        if last_genre:
            query = base_query + " AND (b.genre IS NULL OR LOWER(b.genre) != LOWER(?)) GROUP BY b.id ORDER BY avg_score DESC, b.id ASC LIMIT ?"
            async with db.execute(query, (chat_id, last_genre, limit)) as cursor:
                results = [dict(r) for r in await cursor.fetchall()]
                if len(results) >= limit:
                    return results

        # If not enough books excluding last_genre, fetch all backlog books
        query = base_query + " GROUP BY b.id ORDER BY avg_score DESC, b.id ASC LIMIT ?"
        async with db.execute(query, (chat_id, limit)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def update_books_status(db_path: str, book_ids: List[int], status: str) -> None:
    """Bulk update status for books."""
    if not book_ids:
        return
    async with aiosqlite.connect(db_path) as db:
        placeholders = ",".join("?" * len(book_ids))
        await db.execute(f"UPDATE books SET status = ? WHERE id IN ({placeholders})", [status] + list(book_ids))
        await db.commit()


async def save_active_poll(db_path: str, chat_id: int, poll_id: str, message_id: int, options_json: str) -> None:
    """Save details of currently running Telegram Poll for a chat."""
    async with aiosqlite.connect(db_path) as db:
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
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_polls WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_active_poll_by_poll_id(db_path: str, poll_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve active poll by poll_id."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_polls WHERE poll_id = ?", (poll_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def clear_active_poll(db_path: str, chat_id: int) -> None:
    """Remove active poll record for a chat."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM active_polls WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def resolve_vote_winner(db_path: str, chat_id: int, winning_book_id: int, voting_book_ids: List[int]) -> None:
    """
    Mark winning_book_id as 'won', and revert other voting_book_ids to 'backlog'.
    """
    async with aiosqlite.connect(db_path) as db:
        # Revert non-winners to backlog
        other_ids = [bid for bid in voting_book_ids if bid != winning_book_id]
        if other_ids:
            placeholders = ",".join("?" * len(other_ids))
            await db.execute(f"UPDATE books SET status = 'backlog' WHERE id IN ({placeholders})", other_ids)

        # Set winner status to 'won'
        await db.execute("UPDATE books SET status = 'won' WHERE id = ?", (winning_book_id,))
        await db.commit()


async def mark_book_done(db_path: str, book_id: int, file_id: str) -> None:
    """Update book status to 'done' and record file_id."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE books SET status = 'done', file_id = ? WHERE id = ?", (file_id, book_id))
        await db.commit()


async def add_to_hall_of_fame(db_path: str, book_id: int, chat_id: int, final_rating: Optional[float] = None) -> None:
    """Add completed book to Hall of Fame."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO hall_of_fame (book_id, chat_id, final_club_rating)
            VALUES (?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET final_club_rating = excluded.final_club_rating
        """, (book_id, chat_id, final_rating))
        await db.commit()


async def get_hall_of_fame(db_path: str, chat_id: int) -> List[Dict[str, Any]]:
    """Get all Hall of Fame books for a chat."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT b.title, b.author, b.genre, h.final_club_rating, h.completed_at
            FROM hall_of_fame h
            JOIN books b ON h.book_id = b.id
            WHERE h.chat_id = ?
            ORDER BY h.completed_at DESC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_group_stats(db_path: str, chat_id: int) -> Dict[str, Any]:
    """Get stats for a specific chat."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM books WHERE chat_id = ? AND status = 'backlog'", (chat_id,)) as cursor:
            backlog_count = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM books WHERE chat_id = ? AND status = 'done'", (chat_id,)) as cursor:
            done_count = (await cursor.fetchone())[0]

        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM backlog_ratings
            WHERE book_id IN (SELECT id FROM books WHERE chat_id = ?)
        """, (chat_id,)) as cursor:
            active_raters = (await cursor.fetchone())[0]

        return {
            "backlog_count": backlog_count,
            "done_count": done_count,
            "active_raters": active_raters
        }


async def get_sys_stats(db_path: str) -> Dict[str, Any]:
    """Get global system statistics for superadmin."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM chats WHERE status = 'active'", ()) as cursor:
            total_active_chats = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM users", ()) as cursor:
            total_unique_users = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM books WHERE status = 'done'", ()) as cursor:
            total_downloaded_books = (await cursor.fetchone())[0]

        return {
            "total_active_chats": total_active_chats,
            "total_unique_users": total_unique_users,
            "total_downloaded_books": total_downloaded_books
        }


async def get_book_by_id(db_path: str, book_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve book details by ID."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM books WHERE id = ?", (book_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_current_winning_or_reading_book(db_path: str, chat_id: int) -> Optional[Dict[str, Any]]:
    """Get book with status 'won' or 'done' that is currently being read/discussed."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM books
            WHERE chat_id = ? AND status IN ('won', 'done')
              AND id NOT IN (SELECT book_id FROM hall_of_fame)
            ORDER BY id DESC LIMIT 1
        """, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
