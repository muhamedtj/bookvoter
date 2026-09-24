"""Superadmin usage analytics for BookVoter clubs.

Tracks real BookVoter interactions per Telegram club (not ordinary chat traffic)
and adds a private superadmin dashboard listing where the bot is used and how
frequently each club interacts with it.

Historical books/ratings are backfilled once as coarse product events so the
first dashboard is useful immediately. Precise interaction tracking starts when
this module is deployed.
"""

from __future__ import annotations

from typing import Optional, Any

from aiogram import BaseMiddleware

import bot_core as core


_installed = False
_original_superadmin_response = None

PAGE_SIZE = 5
TRACKED_GROUP_COMMANDS = {
    "books": "menu_opened",
    "bookvoter": "menu_opened",
    "bookadmin": "admin_opened",
    "booktimer": "timer_changed",
    "votetimer": "timer_changed",
    "finish_vote": "vote_finished_early",
}

EVENT_LABELS_RU = {
    "menu_opened": "Открытие меню",
    "admin_opened": "Открытие админки",
    "timer_changed": "Настройка таймера",
    "backlog_viewed": "Просмотр Листа ожидания",
    "hall_viewed": "Просмотр Зала славы",
    "suggestion_started": "Начало добавления книги",
    "suggestion_selected": "Выбор найденной книги",
    "suggestion_attributed": "Выбор автора предложения",
    "book_suggested": "Добавление книги",
    "interest_rated": "Оценка интереса",
    "rating_opened": "Открытие оценок",
    "vote_requested": "Запрос голосования",
    "vote_approved": "Подтверждение голосования",
    "vote_rejected": "Отклонение голосования",
    "vote_started": "Запуск голосования",
    "main_vote_cast": "Голос в основном голосовании",
    "vote_finished_early": "Досрочное завершение голосования",
    "reading_finished": "Завершение чтения",
    "reading_completed": "Завершённая книга",
    "final_rated": "Итоговая оценка",
    "stats_viewed": "Просмотр статистики",
    "help_viewed": "Просмотр справки",
    "error_report_started": "Сообщение об ошибке",
    "recovery_action": "Восстановление состояния",
    "setting_changed": "Изменение настройки",
    "admin_action": "Действие администратора",
    "bot_added": "Бот добавлен в клуб",
    "bot_removed": "Бот удалён из клуба",
}

EVENT_LABELS_EN = {
    "menu_opened": "Menu opened",
    "admin_opened": "Admin panel opened",
    "timer_changed": "Vote timer changed",
    "backlog_viewed": "Waiting list viewed",
    "hall_viewed": "Hall of Fame viewed",
    "suggestion_started": "Book suggestion started",
    "suggestion_selected": "Search result selected",
    "suggestion_attributed": "Suggestor selected",
    "book_suggested": "Book added",
    "interest_rated": "Interest rating",
    "rating_opened": "Rating queue opened",
    "vote_requested": "Vote requested",
    "vote_approved": "Vote approved",
    "vote_rejected": "Vote rejected",
    "vote_started": "Vote started",
    "main_vote_cast": "Main vote cast",
    "vote_finished_early": "Vote finished early",
    "reading_finished": "Reading finished",
    "reading_completed": "Completed book",
    "final_rated": "Final rating",
    "stats_viewed": "Stats viewed",
    "help_viewed": "Help viewed",
    "error_report_started": "Error report started",
    "recovery_action": "Recovery action",
    "setting_changed": "Setting changed",
    "admin_action": "Admin action",
    "bot_added": "Bot added to club",
    "bot_removed": "Bot removed from club",
}


async def _ensure_tables() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_tg_id INTEGER,
                event_type TEXT NOT NULL,
                source_key TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_chat_time ON usage_events(chat_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_chat_user_time ON usage_events(chat_id, user_tg_id, created_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_tracking_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        async with db.execute(
            "SELECT value FROM usage_tracking_meta WHERE key = 'legacy_backfill_v1'"
        ) as cursor:
            already_backfilled = await cursor.fetchone()

        if not already_backfilled:
            # These historical rows are intentionally conservative. They provide
            # useful context before precise event tracking existed without
            # pretending that old menu opens/callbacks can be reconstructed.
            await db.execute(
                """
                INSERT OR IGNORE INTO usage_events
                    (chat_id, user_tg_id, event_type, source_key, created_at)
                SELECT b.chat_id, u.tg_id, 'book_suggested',
                       'legacy:book:' || b.id,
                       COALESCE(b.created_at, CURRENT_TIMESTAMP)
                FROM books b
                LEFT JOIN users u ON u.internal_id = b.suggested_by
                WHERE b.chat_id < 0 AND COALESCE(b.status, '') <> 'invalid'
                """
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO usage_events
                    (chat_id, user_tg_id, event_type, source_key, created_at)
                SELECT b.chat_id, u.tg_id, 'interest_rated',
                       'legacy:interest:' || r.book_id || ':' || r.user_id,
                       COALESCE(r.created_at, b.created_at, CURRENT_TIMESTAMP)
                FROM backlog_ratings r
                JOIN books b ON b.id = r.book_id
                JOIN users u ON u.internal_id = r.user_id
                WHERE b.chat_id < 0
                """
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO usage_events
                    (chat_id, user_tg_id, event_type, source_key, created_at)
                SELECT b.chat_id, u.tg_id, 'final_rated',
                       'legacy:final:' || r.book_id || ':' || r.user_id,
                       COALESCE(r.created_at, h.completed_at, b.created_at, CURRENT_TIMESTAMP)
                FROM read_ratings r
                JOIN books b ON b.id = r.book_id
                JOIN users u ON u.internal_id = r.user_id
                LEFT JOIN hall_of_fame h ON h.book_id = b.id
                WHERE b.chat_id < 0
                """
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO usage_events
                    (chat_id, user_tg_id, event_type, source_key, created_at)
                SELECT h.chat_id, NULL, 'reading_completed',
                       'legacy:completed:' || h.book_id,
                       COALESCE(h.completed_at, CURRENT_TIMESTAMP)
                FROM hall_of_fame h
                WHERE h.chat_id < 0
                """
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO usage_tracking_meta(key, value, updated_at)
                VALUES ('legacy_backfill_v1', 'done', CURRENT_TIMESTAMP)
                """
            )

        await db.commit()


async def record_usage_event(
    chat_id: int,
    user_tg_id: Optional[int],
    event_type: str,
    *,
    source_key: Optional[str] = None,
) -> None:
    if not chat_id or int(chat_id) >= 0 or not event_type:
        return
    try:
        await _ensure_tables()
        async with core.database.open_db(core.DATABASE_PATH) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO usage_events
                    (chat_id, user_tg_id, event_type, source_key, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (int(chat_id), int(user_tg_id) if user_tg_id else None, event_type, source_key),
            )
            await db.commit()
    except Exception as exc:
        # Product analytics must never break the bot's main flow.
        core.logger.debug(f"Could not record BookVoter usage event {event_type}: {exc}")


def _command_name(text: str) -> str:
    first = (text or "").strip().split(maxsplit=1)[0]
    if not first.startswith("/"):
        return ""
    name = first[1:].split("@", 1)[0].lower()
    return name


def _callback_event_type(data: str) -> Optional[str]:
    if data == "show_backlog":
        return "backlog_viewed"
    if data == "show_hof":
        return "hall_viewed"
    if data == "show_suggest_help":
        return "suggestion_started"
    if data.startswith("book_suggest_select:"):
        return "suggestion_selected"
    if data.startswith("manual_suggestor_pick:"):
        return "suggestion_attributed"
    if data == "vote_initiate":
        return "vote_requested"
    if data == "vote_request_approve":
        return "vote_approved"
    if data == "vote_request_reject":
        return "vote_rejected"
    if data == "admin_start_vote":
        return "vote_started"
    if data == "admin_finish_vote_early":
        return "vote_finished_early"
    if data == "admin_finish_reading":
        return "reading_finished"
    if data == "show_stats_public" or data.startswith("stats_public:"):
        return "stats_viewed"
    if data == "show_help":
        return "help_viewed"
    if data == "report_error_v2":
        return "error_report_started"
    if data.startswith("vote_book:") or data.startswith("rate_read:"):
        return "final_rated"
    if data.startswith("admin_cancel_"):
        return "recovery_action"
    if data == "toggle_manual_suggestor":
        return "setting_changed"
    if data.startswith("admin_"):
        return "admin_action"
    return None


async def _book_chat_id(book_id: int) -> Optional[int]:
    try:
        book = await core.database.get_book_by_id(core.DATABASE_PATH, int(book_id))
        if book and int(book.get("chat_id") or 0) < 0:
            return int(book["chat_id"])
    except Exception:
        pass
    return None


class _UsageMessageMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        try:
            chat = getattr(event, "chat", None)
            user = getattr(event, "from_user", None)
            text = getattr(event, "text", None) or ""

            if chat and chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
                name = _command_name(text)
                event_type = TRACKED_GROUP_COMMANDS.get(name)
                if event_type:
                    await record_usage_event(chat.id, getattr(user, "id", None), event_type)
                return result

            if chat and chat.type == core.ChatType.PRIVATE:
                # Attribute group->DM interest-rating deep links back to the club.
                if text.startswith("/start"):
                    parts = text.split(maxsplit=1)
                    if len(parts) == 2 and parts[1].startswith("rate_c"):
                        raw = parts[1][6:]
                        if raw.isdigit():
                            await record_usage_event(
                                -int(raw),
                                getattr(user, "id", None),
                                "rating_opened",
                            )
        except Exception as exc:
            core.logger.debug(f"Usage message middleware skipped event: {exc}")
        return result


class _UsageCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        try:
            callback_data = getattr(event, "data", None) or ""
            user_id = getattr(getattr(event, "from_user", None), "id", None)
            message = getattr(event, "message", None)
            chat = getattr(message, "chat", None)

            if chat and chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
                event_type = _callback_event_type(callback_data)
                if event_type:
                    await record_usage_event(chat.id, user_id, event_type)
                return result

            # Private interest rating callbacks contain the book id, so they can
            # still be attributed to the correct club.
            if callback_data.startswith("rate:"):
                parts = callback_data.split(":")
                if len(parts) >= 3:
                    try:
                        chat_id = await _book_chat_id(int(parts[1]))
                    except ValueError:
                        chat_id = None
                    if chat_id:
                        await record_usage_event(chat_id, user_id, "interest_rated")
        except Exception as exc:
            core.logger.debug(f"Usage callback middleware skipped event: {exc}")
        return result


class _UsagePollAnswerMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        active = None
        try:
            poll_id = getattr(event, "poll_id", None)
            if poll_id:
                # Resolve the club before the vote handler runs: a decisive vote
                # may immediately finish the poll and clear active_polls.
                active = await core.database.get_active_poll_by_poll_id(core.DATABASE_PATH, poll_id)
        except Exception as exc:
            core.logger.debug(f"Could not resolve poll club for usage tracking: {exc}")

        result = await handler(event, data)
        try:
            option_ids = list(getattr(event, "option_ids", None) or [])
            user = getattr(event, "user", None)
            if active and option_ids:
                await record_usage_event(
                    int(active["chat_id"]),
                    getattr(user, "id", None),
                    "main_vote_cast",
                )
        except Exception as exc:
            core.logger.debug(f"Usage poll middleware skipped event: {exc}")
        return result


async def _bot_membership_update(event) -> None:
    try:
        chat = getattr(event, "chat", None)
        if not chat or chat.type not in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
            return

        old_member = getattr(event, "old_chat_member", None)
        new_member = getattr(event, "new_chat_member", None)
        old_status = getattr(getattr(old_member, "status", None), "value", getattr(old_member, "status", None))
        new_status = getattr(getattr(new_member, "status", None), "value", getattr(new_member, "status", None))
        old_status = str(old_status or "").lower()
        new_status = str(new_status or "").lower()

        inactive = {"left", "kicked"}
        now_active = new_status not in inactive
        title = getattr(chat, "title", None) or getattr(chat, "full_name", None)

        await core.database.register_or_update_chat(
            core.DATABASE_PATH,
            chat.id,
            title=title,
            status="active" if now_active else "inactive",
        )

        actor = getattr(event, "from_user", None)
        if old_status in inactive and now_active:
            await record_usage_event(chat.id, getattr(actor, "id", None), "bot_added")
        elif new_status in inactive and old_status not in inactive:
            await record_usage_event(chat.id, getattr(actor, "id", None), "bot_removed")
    except Exception as exc:
        core.logger.debug(f"Could not update BookVoter club membership status: {exc}")


async def _club_usage_rows() -> list[dict[str, Any]]:
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        db.row_factory = core.aiosqlite.Row if hasattr(core, "aiosqlite") else None
        # Use CTEs so the dashboard remains fast even after the event table grows.
        query = """
            WITH events AS (
                SELECT
                    chat_id,
                    COUNT(*) AS actions_all,
                    SUM(CASE WHEN created_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END) AS actions_7,
                    SUM(CASE WHEN created_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) AS actions_30,
                    COUNT(DISTINCT CASE
                        WHEN created_at >= datetime('now', '-30 days') THEN user_tg_id
                    END) AS active_users_30,
                    MAX(created_at) AS last_activity
                FROM usage_events
                GROUP BY chat_id
            ),
            members AS (
                SELECT chat_id, COUNT(DISTINCT user_id) AS known_members
                FROM chat_members
                GROUP BY chat_id
            ),
            books_stats AS (
                SELECT
                    chat_id,
                    SUM(CASE WHEN COALESCE(status, '') <> 'invalid' THEN 1 ELSE 0 END) AS books_total
                FROM books
                GROUP BY chat_id
            ),
            completed AS (
                SELECT chat_id, COUNT(*) AS completed_books
                FROM hall_of_fame
                WHERE COALESCE(is_hidden, 0) = 0
                GROUP BY chat_id
            )
            SELECT
                c.chat_id,
                COALESCE(NULLIF(c.title, ''), CAST(c.chat_id AS TEXT)) AS title,
                COALESCE(c.status, 'active') AS status,
                COALESCE(m.known_members, 0) AS known_members,
                COALESCE(b.books_total, 0) AS books_total,
                COALESCE(h.completed_books, 0) AS completed_books,
                COALESCE(e.actions_7, 0) AS actions_7,
                COALESCE(e.actions_30, 0) AS actions_30,
                COALESCE(e.actions_all, 0) AS actions_all,
                COALESCE(e.active_users_30, 0) AS active_users_30,
                e.last_activity
            FROM chats c
            LEFT JOIN events e ON e.chat_id = c.chat_id
            LEFT JOIN members m ON m.chat_id = c.chat_id
            LEFT JOIN books_stats b ON b.chat_id = c.chat_id
            LEFT JOIN completed h ON h.chat_id = c.chat_id
            WHERE c.chat_id < 0
            ORDER BY
                CASE WHEN COALESCE(c.status, 'active') = 'active' THEN 0 ELSE 1 END,
                CASE WHEN e.last_activity IS NULL THEN 1 ELSE 0 END,
                e.last_activity DESC,
                title COLLATE NOCASE
        """
        async with db.execute(query) as cursor:
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in await cursor.fetchall()]


def _period_actions(row: dict[str, Any], period: str) -> int:
    if period == "7":
        return int(row.get("actions_7") or 0)
    if period == "all":
        return int(row.get("actions_all") or 0)
    return int(row.get("actions_30") or 0)


def _period_label(lang: str, period: str) -> str:
    if lang == "ru":
        return {"7": "7 дней", "30": "30 дней", "all": "всё время"}.get(period, "30 дней")
    return {"7": "7 days", "30": "30 days", "all": "all time"}.get(period, "30 days")


def _short_time(value: Any) -> str:
    if not value:
        return "—"
    raw = str(value)
    return raw[:16] + " UTC"


def _filter_row(period: str, prefix: str, suffix: str = ""):
    def button(code: str, text: str):
        mark = "✅ " if period == code else ""
        return core.InlineKeyboardButton(
            text=f"{mark}{text}",
            callback_data=f"{prefix}:{code}{suffix}",
        )
    return [
        button("7", "7д"),
        button("30", "30д"),
        button("all", "Всё"),
    ]


async def _clubs_view(lang: str, period: str = "30", page: int = 0):
    rows = await _club_usage_rows()
    total = len(rows)
    active_total = sum(1 for row in rows if str(row.get("status") or "active") == "active")
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    visible = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    if lang == "ru":
        text = (
            f"🏘 <b>Клубы BookVoter</b> · {_period_label(lang, period)}\n"
            f"🟢 Активных: <b>{active_total}</b> · Всего известных: <b>{total}</b>\n\n"
            "⚡ Считаются действия именно с BookVoter, а не обычные сообщения в группе.\n"
            "Исторические книги и оценки добавлены как базовые события; точный учёт кликов ведётся с момента включения мониторинга.\n\n"
        )
    else:
        text = (
            f"🏘 <b>BookVoter clubs</b> · {_period_label(lang, period)}\n"
            f"🟢 Active: <b>{active_total}</b> · Known total: <b>{total}</b>\n\n"
            "⚡ Counts BookVoter interactions, not ordinary group chat messages.\n"
            "Historical books and ratings are backfilled as baseline events; precise click tracking starts when monitoring is enabled.\n\n"
        )

    keyboard = [_filter_row(period, "usage_clubs", f":{page}")]

    if not visible:
        text += "—"
    for index, row in enumerate(visible, page * PAGE_SIZE + 1):
        title = core.escape_html(str(row["title"]))
        actions = _period_actions(row, period)
        status_icon = "🟢" if str(row.get("status") or "active") == "active" else "⚫"
        status_text = ("активен" if str(row.get("status") or "active") == "active" else "отключён") if lang == "ru" else ("active" if str(row.get("status") or "active") == "active" else "inactive")
        if lang == "ru":
            text += (
                f"<b>{index}. {title}</b> · {status_icon} {status_text}\n"
                f"🆔 <code>{row['chat_id']}</code>\n"
                f"⚡ Действий: <b>{actions}</b> · 👤 активных за 30д: <b>{row['active_users_30']}</b>\n"
                f"👥 Известно участников: <b>{row['known_members']}</b> · 📚 книг: <b>{row['books_total']}</b> · 🏆 завершено: <b>{row['completed_books']}</b>\n"
                f"🕒 Последнее использование: <b>{_short_time(row['last_activity'])}</b>\n\n"
            )
            btn_text = f"📊 {str(row['title'])[:32]}"
        else:
            text += (
                f"<b>{index}. {title}</b> · {status_icon} {status_text}\n"
                f"🆔 <code>{row['chat_id']}</code>\n"
                f"⚡ Actions: <b>{actions}</b> · 👤 active in 30d: <b>{row['active_users_30']}</b>\n"
                f"👥 Known members: <b>{row['known_members']}</b> · 📚 books: <b>{row['books_total']}</b> · 🏆 completed: <b>{row['completed_books']}</b>\n"
                f"🕒 Last use: <b>{_short_time(row['last_activity'])}</b>\n\n"
            )
            btn_text = f"📊 {str(row['title'])[:32]}"

        keyboard.append([
            core.InlineKeyboardButton(
                text=btn_text,
                callback_data=f"usage_club:{row['chat_id']}:{period}:{page}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(core.InlineKeyboardButton(text="⬅️", callback_data=f"usage_clubs:{period}:{page - 1}"))
    nav.append(core.InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="usage_noop"))
    if page + 1 < pages:
        nav.append(core.InlineKeyboardButton(text="➡️", callback_data=f"usage_clubs:{period}:{page + 1}"))
    keyboard.append(nav)
    keyboard.append([
        core.InlineKeyboardButton(
            text="⬅️ Сводка" if lang == "ru" else "⬅️ Overview",
            callback_data="usage_overview",
        )
    ])

    return text, core.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _event_breakdown(chat_id: int, period: str) -> list[tuple[str, int]]:
    await _ensure_tables()
    where = "chat_id = ?"
    params: list[Any] = [chat_id]
    if period == "7":
        where += " AND created_at >= datetime('now', '-7 days')"
    elif period == "30":
        where += " AND created_at >= datetime('now', '-30 days')"

    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            f"""
            SELECT event_type, COUNT(*) AS cnt
            FROM usage_events
            WHERE {where}
            GROUP BY event_type
            ORDER BY cnt DESC, event_type ASC
            """,
            params,
        ) as cursor:
            return [(str(row[0]), int(row[1])) for row in await cursor.fetchall()]


async def _club_detail_view(lang: str, chat_id: int, period: str, list_page: int):
    rows = await _club_usage_rows()
    row = next((item for item in rows if int(item["chat_id"]) == int(chat_id)), None)
    if not row:
        return (
            "Клуб не найден." if lang == "ru" else "Club not found.",
            core.InlineKeyboardMarkup(inline_keyboard=[[
                core.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"usage_clubs:{period}:{list_page}",
                )
            ]]),
        )

    breakdown = await _event_breakdown(chat_id, period)
    labels = EVENT_LABELS_RU if lang == "ru" else EVENT_LABELS_EN
    breakdown_text = "\n".join(
        f"• {core.escape_html(labels.get(event_type, event_type))}: <b>{count}</b>"
        for event_type, count in breakdown
    ) or "—"

    title = core.escape_html(str(row["title"]))
    if lang == "ru":
        text = (
            f"📊 <b>{title}</b>\n"
            f"🆔 <code>{chat_id}</code>\n\n"
            f"⚡ 7 дней: <b>{row['actions_7']}</b>\n"
            f"⚡ 30 дней: <b>{row['actions_30']}</b>\n"
            f"⚡ Всё время: <b>{row['actions_all']}</b>\n"
            f"👤 Активных пользователей за 30д: <b>{row['active_users_30']}</b>\n"
            f"👥 Известно участников: <b>{row['known_members']}</b>\n"
            f"📚 Предложено книг: <b>{row['books_total']}</b>\n"
            f"🏆 Завершено книг: <b>{row['completed_books']}</b>\n"
            f"🕒 Последнее использование: <b>{_short_time(row['last_activity'])}</b>\n\n"
            f"<b>События за {_period_label(lang, period)}:</b>\n{breakdown_text}"
        )
    else:
        text = (
            f"📊 <b>{title}</b>\n"
            f"🆔 <code>{chat_id}</code>\n\n"
            f"⚡ 7 days: <b>{row['actions_7']}</b>\n"
            f"⚡ 30 days: <b>{row['actions_30']}</b>\n"
            f"⚡ All time: <b>{row['actions_all']}</b>\n"
            f"👤 Active users in 30d: <b>{row['active_users_30']}</b>\n"
            f"👥 Known members: <b>{row['known_members']}</b>\n"
            f"📚 Suggested books: <b>{row['books_total']}</b>\n"
            f"🏆 Completed books: <b>{row['completed_books']}</b>\n"
            f"🕒 Last use: <b>{_short_time(row['last_activity'])}</b>\n\n"
            f"<b>Events for {_period_label(lang, period)}:</b>\n{breakdown_text}"
        )

    all_label = "Всё" if lang == "ru" else "All"
    keyboard = [
        [
            core.InlineKeyboardButton(text=("✅ " if period == "7" else "") + ("7д" if lang == "ru" else "7d"), callback_data=f"usage_club:{chat_id}:7:{list_page}"),
            core.InlineKeyboardButton(text=("✅ " if period == "30" else "") + ("30д" if lang == "ru" else "30d"), callback_data=f"usage_club:{chat_id}:30:{list_page}"),
            core.InlineKeyboardButton(text=("✅ " if period == "all" else "") + all_label, callback_data=f"usage_club:{chat_id}:all:{list_page}"),
        ],
        [
            core.InlineKeyboardButton(
                text="⬅️ Клубы" if lang == "ru" else "⬅️ Clubs",
                callback_data=f"usage_clubs:{period}:{list_page}",
            )
        ],
    ]
    return text, core.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _send_superadmin_response(lang: str, days: Optional[int], target_msg_or_cb: Any):
    stats = await core.database.get_superadmin_stats_detailed(core.DATABASE_PATH, days=days)
    filter_label = core.t("filter_30_days", lang) if days == 30 else core.t("filter_all_time", lang)
    top_books_str = "\n".join(
        f"• {core.escape_html(b['title'])} ({core.escape_html(b['author'])}) — ⭐ {b['score']}/10"
        for b in stats["top_books"]
    ) or "N/A"

    text = core.t(
        "sys_stats_text",
        lang,
        filter_label=filter_label,
        total_active_chats=stats["total_active_chats"],
        total_voters=stats["total_voters"],
        total_books_suggested=stats["total_books_suggested"],
        top_books_str=top_books_str,
    )
    markup = core.InlineKeyboardMarkup(inline_keyboard=[
        [
            core.InlineKeyboardButton(
                text=f"{'✅ ' if not days else ''}{core.t('filter_all_time', lang)}",
                callback_data="stats_super:all",
            ),
            core.InlineKeyboardButton(
                text=f"{'✅ ' if days == 30 else ''}{core.t('filter_30_days', lang)}",
                callback_data="stats_super:30",
            ),
        ],
        [
            core.InlineKeyboardButton(
                text="🏘 Клубы и использование" if lang == "ru" else "🏘 Clubs & usage",
                callback_data="usage_clubs:30:0",
            )
        ],
    ])

    if isinstance(target_msg_or_cb, core.Message):
        await target_msg_or_cb.answer(text, parse_mode="HTML", reply_markup=markup)
    elif isinstance(target_msg_or_cb, core.CallbackQuery):
        await target_msg_or_cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _usage_clubs_callback(callback: core.CallbackQuery):
    if callback.from_user.id not in core.SUPER_ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    period = parts[1] if len(parts) > 1 and parts[1] in {"7", "30", "all"} else "30"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    text, markup = await _clubs_view(lang, period, page)
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _usage_club_callback(callback: core.CallbackQuery):
    if callback.from_user.id not in core.SUPER_ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
        page = int(parts[3])
    except ValueError:
        await callback.answer()
        return
    period = parts[2] if parts[2] in {"7", "30", "all"} else "30"
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    text, markup = await _club_detail_view(lang, chat_id, period, page)
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _usage_overview_callback(callback: core.CallbackQuery):
    if callback.from_user.id not in core.SUPER_ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    await callback.answer()
    await _send_superadmin_response(lang, days=None, target_msg_or_cb=callback)


async def _usage_noop(callback: core.CallbackQuery):
    await callback.answer()


def install() -> None:
    global _installed, _original_superadmin_response
    if _installed:
        return
    _installed = True

    # Install these middleware first in bot.py so even runtime modules that
    # intercept callbacks (suggestion flow, manual suggestor picker, etc.) are
    # still observed by usage analytics.
    core.router.message.outer_middleware(_UsageMessageMiddleware())
    core.router.callback_query.outer_middleware(_UsageCallbackMiddleware())
    try:
        core.router.poll_answer.outer_middleware(_UsagePollAnswerMiddleware())
    except Exception as exc:
        core.logger.warning(f"Could not install poll-answer usage tracking: {exc}")
    try:
        core.router.my_chat_member(_bot_membership_update)
    except Exception as exc:
        core.logger.warning(f"Could not install bot-membership usage tracking: {exc}")

    _original_superadmin_response = core.send_superadmin_response
    core.send_superadmin_response = _send_superadmin_response
    core.record_usage_event = record_usage_event

    core.router.callback_query(core.F.data.startswith("usage_clubs:"))(_usage_clubs_callback)
    core.router.callback_query(core.F.data.startswith("usage_club:"))(_usage_club_callback)
    core.router.callback_query(core.F.data == "usage_overview")(_usage_overview_callback)
    core.router.callback_query(core.F.data == "usage_noop")(_usage_noop)
