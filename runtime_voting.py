"""Voting lifecycle, public stats and final-rating safeguards for BookVoter."""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Optional

from aiogram import BaseMiddleware

import bot_core as core
import i18n
from runtime_ux import label, schedule_message_delete, send_ephemeral_reply

_installed = False
_base_welcome_keyboard = None
_original_launch_poll = None
_original_finish_vote = None
_original_recover_polls = None

DEFAULT_VOTE_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 24 * 60 * 60
MIN_VOTE_DURATION_SECONDS = 60
MAX_VOTE_DURATION_SECONDS = 30 * 24 * 60 * 60


def _format_duration(seconds: int, lang: str = "ru") -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}д" if lang == "ru" else f"{days}d")
    if hours:
        parts.append(f"{hours}ч" if lang == "ru" else f"{hours}h")
    if minutes or (not parts and seconds >= 60):
        parts.append(f"{minutes}м" if lang == "ru" else f"{minutes}m")
    if not parts:
        parts.append(f"{secs}с" if lang == "ru" else f"{secs}s")
    return " ".join(parts[:2])


def _parse_duration(raw: str) -> Optional[int]:
    if not raw:
        return None
    value = raw.strip().lower().replace(" ", "")
    aliases = [
        ("minutes", 60), ("minute", 60), ("mins", 60), ("min", 60), ("m", 60),
        ("hours", 3600), ("hour", 3600), ("hrs", 3600), ("hr", 3600), ("h", 3600),
        ("days", 86400), ("day", 86400), ("d", 86400),
        ("минут", 60), ("мин", 60), ("м", 60),
        ("часов", 3600), ("час", 3600), ("ч", 3600),
        ("дней", 86400), ("день", 86400), ("д", 86400),
    ]
    for suffix, multiplier in aliases:
        if value.endswith(suffix):
            number = value[:-len(suffix)]
            try:
                return int(float(number) * multiplier)
            except (TypeError, ValueError):
                return None
    try:
        return int(float(value) * 60)
    except (TypeError, ValueError):
        return None


async def _ensure_tables() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vote_settings (
                chat_id INTEGER PRIMARY KEY,
                vote_duration_seconds INTEGER NOT NULL DEFAULT 86400,
                approval_timeout_seconds INTEGER NOT NULL DEFAULT 86400,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vote_requests (
                chat_id INTEGER PRIMARY KEY,
                requested_by_tg_id INTEGER NOT NULL,
                request_message_id INTEGER,
                deadline_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vote_sessions (
                chat_id INTEGER PRIMARY KEY,
                poll_id TEXT,
                deadline_ts INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def _get_vote_settings(chat_id: int):
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT vote_duration_seconds, approval_timeout_seconds FROM vote_settings WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return DEFAULT_VOTE_DURATION_SECONDS, DEFAULT_APPROVAL_TIMEOUT_SECONDS
    return int(row[0] or DEFAULT_VOTE_DURATION_SECONDS), int(row[1] or DEFAULT_APPROVAL_TIMEOUT_SECONDS)


async def _set_vote_duration(chat_id: int, seconds: int) -> None:
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO vote_settings (chat_id, vote_duration_seconds, approval_timeout_seconds, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                vote_duration_seconds = excluded.vote_duration_seconds,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, seconds, DEFAULT_APPROVAL_TIMEOUT_SECONDS),
        )
        await db.commit()


async def _get_vote_request(chat_id: int):
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT chat_id, requested_by_tg_id, request_message_id, deadline_ts, status FROM vote_requests WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    return {
        "chat_id": row[0],
        "requested_by_tg_id": row[1],
        "request_message_id": row[2],
        "deadline_ts": int(row[3]),
        "status": row[4],
    }


async def _save_vote_request(chat_id: int, user_id: int, message_id: int, deadline_ts: int) -> None:
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO vote_requests (chat_id, requested_by_tg_id, request_message_id, deadline_ts, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(chat_id) DO UPDATE SET
                requested_by_tg_id = excluded.requested_by_tg_id,
                request_message_id = excluded.request_message_id,
                deadline_ts = excluded.deadline_ts,
                status = 'pending',
                created_at = CURRENT_TIMESTAMP
            """,
            (chat_id, user_id, message_id, deadline_ts),
        )
        await db.commit()


async def _clear_vote_request(chat_id: int) -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute("DELETE FROM vote_requests WHERE chat_id = ?", (chat_id,))
        await db.commit()
    job_id = f"vote_request_{chat_id}"
    try:
        if core.scheduler.get_job(job_id):
            core.scheduler.remove_job(job_id)
    except Exception:
        pass


async def _save_vote_session(chat_id: int, poll_id: str, deadline_ts: int) -> None:
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO vote_sessions (chat_id, poll_id, deadline_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                poll_id = excluded.poll_id,
                deadline_ts = excluded.deadline_ts,
                created_at = CURRENT_TIMESTAMP
            """,
            (chat_id, poll_id, deadline_ts),
        )
        await db.commit()


async def _get_vote_session(chat_id: int):
    await _ensure_tables()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT poll_id, deadline_ts FROM vote_sessions WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return {"poll_id": row[0], "deadline_ts": int(row[1])} if row else None


async def _clear_vote_session(chat_id: int) -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute("DELETE FROM vote_sessions WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def _launch_poll_for_books(chat_id: int, top_books, lang: str) -> bool:
    if len(top_books) < 2:
        await core.bot.send_message(chat_id, core.t("min_two_books_err", lang))
        return False

    current_reading = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
    if current_reading:
        await core.bot.send_message(
            chat_id,
            core.t("active_reading_exists_err", lang, title=core.escape_html(current_reading["title"])),
            parse_mode="HTML",
        )
        return False

    active = await core.database.get_active_poll(core.DATABASE_PATH, chat_id)
    if active:
        return False

    duration, _ = await _get_vote_settings(chat_id)
    book_ids = [b["id"] for b in top_books]
    await core.database.update_books_status(core.DATABASE_PATH, book_ids, "voting")

    options = [f"{b['title']} — {b['author']}"[:100] for b in top_books]
    options_mapping = {idx: b["id"] for idx, b in enumerate(top_books)}
    question = f"{core.t('poll_question', lang)} (⏳ {_format_duration(duration, lang)})"

    poll_msg = None
    try:
        send_kwargs = dict(
            chat_id=chat_id,
            question=question[:300],
            options=options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
        try:
            poll_msg = await core.bot.send_poll(**send_kwargs, allows_revoting=False)
        except TypeError:
            poll_msg = await core.bot.send_poll(**send_kwargs)

        await core.database.save_active_poll(
            core.DATABASE_PATH,
            chat_id=chat_id,
            poll_id=poll_msg.poll.id,
            message_id=poll_msg.message_id,
            options_json=json.dumps(options_mapping),
        )

        deadline_ts = int(time.time()) + duration
        await _save_vote_session(chat_id, poll_msg.poll.id, deadline_ts)
        await _clear_vote_request(chat_id)

        job_id = f"poll_end_{chat_id}_{poll_msg.message_id}"
        core.scheduler.add_job(
            core.finish_vote_process,
            "date",
            run_date=datetime.fromtimestamp(deadline_ts),
            args=[chat_id],
            id=job_id,
            replace_existing=True,
        )
        return True
    except Exception as exc:
        core.logger.error(f"Error launching poll for chat {chat_id}: {exc}")
        await core.database.update_books_status(core.DATABASE_PATH, book_ids, "backlog")
        await core.database.clear_active_poll(core.DATABASE_PATH, chat_id)
        await _clear_vote_session(chat_id)
        if poll_msg:
            try:
                await core.bot.stop_poll(chat_id, poll_msg.message_id)
            except Exception:
                pass
        return False


async def _finish_vote_process(chat_id: int):
    await _original_finish_vote(chat_id)
    if not await core.database.get_active_poll(core.DATABASE_PATH, chat_id):
        await _clear_vote_session(chat_id)


async def _recover_active_polls():
    await _original_recover_polls()
    await _ensure_tables()
    now_ts = int(time.time())

    for poll in await core.database.get_all_active_polls(core.DATABASE_PATH):
        chat_id = poll["chat_id"]
        session = await _get_vote_session(chat_id)
        if session:
            deadline_ts = session["deadline_ts"]
        else:
            duration, _ = await _get_vote_settings(chat_id)
            try:
                created = datetime.strptime(poll["created_at"], "%Y-%m-%d %H:%M:%S")
                deadline_ts = int(created.timestamp()) + duration
            except Exception:
                deadline_ts = now_ts + duration
            await _save_vote_session(chat_id, poll["poll_id"], deadline_ts)

        run_ts = max(now_ts + 5, deadline_ts)
        job_id = f"poll_end_{chat_id}_{poll['message_id']}"
        core.scheduler.add_job(
            core.finish_vote_process,
            "date",
            run_date=datetime.fromtimestamp(run_ts),
            args=[chat_id],
            id=job_id,
            replace_existing=True,
        )

    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT chat_id, deadline_ts FROM vote_requests WHERE status = 'pending'"
        ) as cursor:
            requests = await cursor.fetchall()
    for chat_id, deadline_ts in requests:
        run_ts = max(now_ts + 5, int(deadline_ts))
        core.scheduler.add_job(
            _auto_launch_vote_request,
            "date",
            run_date=datetime.fromtimestamp(run_ts),
            args=[chat_id],
            id=f"vote_request_{chat_id}",
            replace_existing=True,
        )


async def _validate_vote_ready(chat_id: int, lang: str):
    if await core.database.get_active_poll(core.DATABASE_PATH, chat_id):
        return False, core.t("vote_in_progress_err", lang), None
    current = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
    if current:
        return False, core.t("active_reading_exists_err", lang, title=core.escape_html(current["title"])), None
    top_books = await core.database.get_top_backlog_books_for_vote(core.DATABASE_PATH, chat_id, limit=3)
    if not top_books:
        return False, core.t("no_backlog_books_err", lang), None
    if len(top_books) < 2:
        return False, core.t("min_two_books_err", lang), None
    return True, "", top_books


async def _auto_launch_vote_request(chat_id: int):
    request = await _get_vote_request(chat_id)
    if not request or request.get("status") != "pending":
        return
    lang = await core.database.get_effective_language(core.DATABASE_PATH, chat_id)
    ok, reason, top_books = await _validate_vote_ready(chat_id, lang)
    msg_id = request.get("request_message_id")
    if not ok:
        await _clear_vote_request(chat_id)
        if msg_id:
            try:
                await core.bot.edit_message_text(
                    label(lang, en=f"⚠️ Automatic vote start was cancelled. {reason}", ru=f"⚠️ Автозапуск голосования отменён. {reason}"),
                    chat_id=chat_id,
                    message_id=msg_id,
                    parse_mode="HTML",
                )
                schedule_message_delete(chat_id, msg_id, 90)
            except Exception:
                pass
        return

    success = await _launch_poll_for_books(chat_id, top_books, lang)
    if msg_id:
        try:
            text = label(
                lang,
                en="⏱ Administrator approval window expired. Voting started automatically.",
                ru="⏱ Время на подтверждение администратора истекло. Голосование запущено автоматически.",
            ) if success else label(
                lang,
                en="⚠️ Automatic vote start failed. An administrator can retry from /bookvoter.",
                ru="⚠️ Автозапуск голосования не удался. Администратор может повторить через /bookvoter.",
            )
            await core.bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
            schedule_message_delete(chat_id, msg_id, 90)
        except Exception:
            pass


async def _initiate_vote(callback: core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if callback.message.chat.type == core.ChatType.PRIVATE:
        await callback.answer(label(lang, en="Voting is started from the club group.", ru="Голосование запускается из группы клуба."), show_alert=True)
        return

    ok, reason, top_books = await _validate_vote_ready(chat_id, lang)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    if await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer()
        success = await _launch_poll_for_books(chat_id, top_books, lang)
        if success:
            try:
                await callback.message.edit_text(core.t("vote_started_msg", lang))
                schedule_message_delete(chat_id, callback.message.message_id, 60)
            except Exception:
                pass
        else:
            await callback.answer(core.t("poll_launch_failed_err", lang), show_alert=True)
        return

    existing = await _get_vote_request(chat_id)
    if existing and existing.get("status") == "pending" and existing.get("deadline_ts", 0) > int(time.time()):
        remaining = existing["deadline_ts"] - int(time.time())
        await callback.answer(
            label(lang, en=f"A vote request already exists. Auto-start in {_format_duration(remaining, lang)}.", ru=f"Запрос на голосование уже есть. Автозапуск через {_format_duration(remaining, lang)}."),
            show_alert=True,
        )
        return

    _, approval_timeout = await _get_vote_settings(chat_id)
    deadline_ts = int(time.time()) + approval_timeout
    requester = core.escape_html(callback.from_user.full_name or callback.from_user.username or str(callback.from_user.id))
    text = label(
        lang,
        en=(f"🗳 <b>{requester}</b> requested a vote for the next book.\n\nAn administrator can start it now or cancel it. If nobody acts, voting starts automatically in <b>{_format_duration(approval_timeout, lang)}</b>."),
        ru=(f"🗳 <b>{requester}</b> предложил начать голосование за следующую книгу.\n\nАдминистратор может запустить его сейчас или отменить. Если реакции не будет, голосование стартует автоматически через <b>{_format_duration(approval_timeout, lang)}</b>."),
    )
    markup = core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text=label(lang, en="✅ Start now", ru="✅ Запустить сейчас"), callback_data="vote_request_approve"), core.InlineKeyboardButton(text=label(lang, en="❌ Cancel", ru="❌ Отменить"), callback_data="vote_request_reject")]])
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await _save_vote_request(chat_id, callback.from_user.id, callback.message.message_id, deadline_ts)
    core.scheduler.add_job(_auto_launch_vote_request, "date", run_date=datetime.fromtimestamp(deadline_ts), args=[chat_id], id=f"vote_request_{chat_id}", replace_existing=True)


async def _approve_vote_request(callback: core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if not await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer(core.t("only_admins_allowed", lang), show_alert=True)
        return
    request = await _get_vote_request(chat_id)
    if not request:
        await callback.answer(label(lang, en="This request is no longer active.", ru="Этот запрос уже неактивен."), show_alert=True)
        return
    ok, reason, top_books = await _validate_vote_ready(chat_id, lang)
    if not ok:
        await _clear_vote_request(chat_id)
        await callback.answer(reason, show_alert=True)
        return
    await callback.answer()
    success = await _launch_poll_for_books(chat_id, top_books, lang)
    text = core.t("vote_started_msg", lang) if success else core.t("poll_launch_failed_err", lang)
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
        schedule_message_delete(chat_id, callback.message.message_id, 60)
    except Exception:
        pass


async def _reject_vote_request(callback: core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if not await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer(core.t("only_admins_allowed", lang), show_alert=True)
        return
    await _clear_vote_request(chat_id)
    await callback.answer()
    try:
        await callback.message.edit_text(label(lang, en="❌ Vote request cancelled by an administrator.", ru="❌ Запрос на голосование отменён администратором."))
        schedule_message_delete(chat_id, callback.message.message_id, 60)
    except Exception:
        pass


async def _votetimer_command(message: core.Message, command: core.CommandObject):
    lang = await core.get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    if message.chat.type == core.ChatType.PRIVATE:
        await message.answer(core.t("control_panel_dm_notice", lang), parse_mode="HTML")
        return
    if not message.from_user or not await core.is_admin(message.chat.id, message.from_user.id):
        await send_ephemeral_reply(message, core.t("only_admins_allowed", lang), delay_seconds=45)
        return

    current, _ = await _get_vote_settings(message.chat.id)
    raw = (command.args or "").strip()
    if not raw:
        await send_ephemeral_reply(message, label(lang, en=f"⏳ Current voting timer: <b>{_format_duration(current, lang)}</b>.\nExample: <code>/votetimer 30m</code> or <code>/votetimer 6h</code>.", ru=f"⏳ Текущий таймер голосования: <b>{_format_duration(current, lang)}</b>.\nПример: <code>/votetimer 30m</code> или <code>/votetimer 6h</code>."), delay_seconds=90, parse_mode="HTML")
        return

    seconds = _parse_duration(raw)
    if seconds is None or seconds < MIN_VOTE_DURATION_SECONDS or seconds > MAX_VOTE_DURATION_SECONDS:
        await send_ephemeral_reply(message, label(lang, en="⚠️ Use a duration from 1 minute to 30 days, e.g. <code>/votetimer 45m</code> or <code>/votetimer 3h</code>.", ru="⚠️ Укажите от 1 минуты до 30 дней, например <code>/votetimer 45m</code> или <code>/votetimer 3h</code>."), delay_seconds=90, parse_mode="HTML")
        return

    await _set_vote_duration(message.chat.id, seconds)
    await send_ephemeral_reply(message, label(lang, en=f"✅ Voting timer set to <b>{_format_duration(seconds, lang)}</b>. It applies to new votes.", ru=f"✅ Таймер голосования установлен: <b>{_format_duration(seconds, lang)}</b>. Он применяется к новым голосованиям."), delay_seconds=90, parse_mode="HTML")


async def _get_admin_panel_view(chat_id: int, lang: str, db_path: str = core.DATABASE_PATH):
    active_poll = await core.database.get_active_poll(db_path, chat_id)
    current_book = await core.database.get_current_reading_book(db_path, chat_id)
    request = await _get_vote_request(chat_id)
    keyboard = []

    if active_poll:
        text = core.t("admin_state_voting", lang)
        session = await _get_vote_session(chat_id)
        if session:
            remaining = max(0, session["deadline_ts"] - int(time.time()))
            text += label(lang, en=f"\n\n⏳ Time left: <b>{_format_duration(remaining, lang)}</b>", ru=f"\n\n⏳ Осталось: <b>{_format_duration(remaining, lang)}</b>")
        keyboard.append([core.InlineKeyboardButton(text=core.t("btn_finish_vote_early", lang), callback_data="admin_finish_vote_early")])
    elif current_book:
        text = core.t("admin_state_reading", lang, title=core.escape_html(current_book["title"]), author=core.escape_html(current_book["author"]))
        keyboard.append([core.InlineKeyboardButton(text=core.t("btn_finish_reading", lang), callback_data="admin_finish_reading")])
    elif request and request.get("status") == "pending":
        remaining = max(0, request["deadline_ts"] - int(time.time()))
        text = label(lang, en=f"⚙️ <b>BookVoter Control Panel</b>\n\n🗳 A member requested a vote. Automatic start in <b>{_format_duration(remaining, lang)}</b>.", ru=f"⚙️ <b>Панель управления BookVoter</b>\n\n🗳 Участник запросил голосование. Автозапуск через <b>{_format_duration(remaining, lang)}</b>.")
        keyboard.append([core.InlineKeyboardButton(text=label(lang, en="✅ Start now", ru="✅ Запустить сейчас"), callback_data="vote_request_approve"), core.InlineKeyboardButton(text=label(lang, en="❌ Cancel", ru="❌ Отменить"), callback_data="vote_request_reject")])
    else:
        text = core.t("admin_state_ready", lang)
        duration, _ = await _get_vote_settings(chat_id)
        text += label(lang, en=f"\n\n⏳ Vote timer: <b>{_format_duration(duration, lang)}</b>", ru=f"\n\n⏳ Таймер голосования: <b>{_format_duration(duration, lang)}</b>")
        keyboard.append([core.InlineKeyboardButton(text=core.t("btn_start_vote", lang), callback_data="admin_start_vote")])

    keyboard.extend([[core.InlineKeyboardButton(text=core.t("btn_delete_book", lang), callback_data="admin_delete_book")], [core.InlineKeyboardButton(text=core.t("btn_back", lang), callback_data="back_to_welcome")]])
    return text, core.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _get_welcome_keyboard(lang: str, bot_username: str, is_private: bool = False, chat_id: Optional[int] = None):
    if is_private or not chat_id or chat_id > 0:
        return _base_welcome_keyboard(lang, bot_username, is_private=is_private, chat_id=chat_id)

    rate_url = f"https://t.me/{bot_username}?start=rate_c{abs(chat_id)}"
    keyboard = [
        [core.InlineKeyboardButton(text=label(lang, en="📚 Waiting list", ru="📚 Лист ожидания"), callback_data="show_backlog"), core.InlineKeyboardButton(text=label(lang, en="🏆 Hall of Fame", ru="🏆 Зал славы"), callback_data="show_hof")],
        [core.InlineKeyboardButton(text=core.t("btn_rate_books", lang), url=rate_url)],
        [core.InlineKeyboardButton(text=label(lang, en="➕ Suggest a book", ru="➕ Предложить книгу"), callback_data="show_suggest_help")],
        [core.InlineKeyboardButton(text=label(lang, en="🗳 Start voting", ru="🗳 Начать голосование"), callback_data="vote_initiate")],
        [core.InlineKeyboardButton(text=core.t("btn_how_it_works", lang), callback_data="show_help")],
        [core.InlineKeyboardButton(text=core.t("btn_group_stats", lang), callback_data="show_stats_public"), core.InlineKeyboardButton(text=core.t("btn_report_error", lang), callback_data="report_error_v2")],
    ]
    return core.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _show_public_stats(callback: core.CallbackQuery):
    await _show_public_stats_period(callback, None)


async def _show_public_stats_period(callback: core.CallbackQuery, days: Optional[int] = None):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if callback.message.chat.type == core.ChatType.PRIVATE:
        await callback.answer(label(lang, en="Open group statistics in the club chat.", ru="Статистика клуба доступна в группе."), show_alert=True)
        return
    stats = await core.database.get_chat_stats_detailed(core.DATABASE_PATH, chat_id, days=days)
    filter_label = core.t("filter_30_days", lang) if days == 30 else core.t("filter_all_time", lang)
    contributors = "\n".join(f"• {core.escape_html(c['name'])}: {c['count']}" for c in stats["top_contributors"]) or "—"
    voters = "\n".join(f"• {core.escape_html(v['name'])}: {v['count']}" for v in stats["top_voters"]) or "—"
    text = core.t("group_stats_text", lang, filter_label=filter_label, backlog_count=stats["backlog_count"], done_count=stats["done_count"], avg_club_rating=stats["avg_club_rating"], contributors_str=contributors, voters_str=voters)
    markup = core.InlineKeyboardMarkup(inline_keyboard=[
        [core.InlineKeyboardButton(text=f"{'✅ ' if days is None else ''}{core.t('filter_all_time', lang)}", callback_data="stats_public:all"), core.InlineKeyboardButton(text=f"{'✅ ' if days == 30 else ''}{core.t('filter_30_days', lang)}", callback_data="stats_public:30")],
        [core.InlineKeyboardButton(text=core.t("btn_back", lang), callback_data="back_to_welcome")],
    ])
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _public_stats_period_callback(callback: core.CallbackQuery):
    days = 30 if callback.data.endswith(":30") else None
    await _show_public_stats_period(callback, days)


async def _has_final_rating(user_tg_id: int, book_id: int):
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            """
            SELECT rr.score
            FROM read_ratings rr
            JOIN users u ON rr.user_id = u.internal_id
            WHERE u.tg_id = ? AND rr.book_id = ?
            """,
            (user_tg_id, book_id),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return False, None
    return True, row[0]


class _FinalRatingLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        callback_data = getattr(event, "data", None) or ""
        if not (callback_data.startswith("vote_book:") or callback_data.startswith("rate_read:")):
            return await handler(event, data)
        parts = callback_data.split(":")
        if len(parts) != 3:
            return await handler(event, data)
        try:
            book_id = int(parts[1])
        except ValueError:
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        exists, score = await _has_final_rating(user.id, book_id)
        if not exists:
            return await handler(event, data)
        lang = await core.get_lang(event.message.chat.id, user.id)
        if score is None:
            text = label(lang, en="✅ Already recorded: Didn't read.", ru="✅ Уже зафиксировано: Не читал.")
        else:
            text = label(lang, en=f"✅ Your final rating is already {int(score)}/10.", ru=f"✅ Ваша итоговая оценка уже зафиксирована: {int(score)}/10.")
        await event.answer(text, show_alert=True)
        return None


def _patch_help_text() -> None:
    i18n.STRINGS["help_msg"]["ru"] = (
        "ℹ️ <b>Как работает BookVoter</b>\n\n"
        "1. <b>Предложите книгу</b> — в группе используйте <code>/suggest Название книги</code>.\n\n"
        "2. <b>Оцените интерес</b> — в ЛС поставьте книгам 1–10. Топ книг по среднему интересу попадает в голосование.\n\n"
        "3. <b>Запустите голосование</b> — администратор запускает его сразу. Обычный участник тоже может нажать «Начать голосование»: у администратора есть 24 часа на подтверждение или отмену, после чего голосование стартует автоматически.\n\n"
        "4. <b>Таймер</b> — администратор задаёт длительность новых голосований командой <code>/votetimer 30m</code> или <code>/votetimer 6h</code>. По умолчанию — 24 часа. Голосование закрывается по таймеру либо раньше, если после минимум 3 проголосовавших одна книга получает больше 50% поданных голосов. Выбор в голосовании изменить нельзя.\n\n"
        "5. <b>После голосования</b> — бот определяет победителя, ищет файл и отправляет книгу в группу.\n\n"
        "6. <b>После чтения</b> — участники ставят итоговую оценку 1–10 или «Не читал». Итоговый выбор фиксируется один раз.\n\n"
        "🏆 Прочитанные книги попадают в <b>Зал славы</b>. 📊 Статистика клуба доступна из главного меню."
    )
    i18n.STRINGS["help_msg"]["en"] = (
        "ℹ️ <b>How BookVoter Works</b>\n\n"
        "1. <b>Suggest a book</b> in the group with <code>/suggest Book title</code>.\n\n"
        "2. <b>Rate interest</b> from 1–10 in private chat. The highest average-interest books enter the vote.\n\n"
        "3. <b>Start voting</b> — an administrator starts immediately. Any member may also request a vote; administrators have 24 hours to approve or cancel it, otherwise it starts automatically.\n\n"
        "4. <b>Timer</b> — administrators set the duration of new votes with <code>/votetimer 30m</code> or <code>/votetimer 6h</code>. Default: 24 hours. A vote closes on the timer or earlier when, after at least 3 voters, one book has more than 50% of votes cast. A poll choice cannot be changed.\n\n"
        "5. <b>After voting</b> the bot selects the winner, finds the file and posts the book to the group.\n\n"
        "6. <b>After reading</b> members give one final 1–10 rating or choose “Didn't read”. The final choice is recorded once.\n\n"
        "🏆 Finished books enter the <b>Hall of Fame</b>. 📊 Club statistics are available from the main menu."
    )


def install():
    global _installed, _base_welcome_keyboard, _original_launch_poll, _original_finish_vote, _original_recover_polls
    if _installed:
        return
    _installed = True

    _base_welcome_keyboard = core.get_welcome_keyboard
    _original_launch_poll = core.launch_poll_for_books
    _original_finish_vote = core.finish_vote_process
    _original_recover_polls = core.recover_active_polls

    core.get_welcome_keyboard = _get_welcome_keyboard
    core.get_admin_panel_view = _get_admin_panel_view
    core.launch_poll_for_books = _launch_poll_for_books
    core.finish_vote_process = _finish_vote_process
    core.recover_active_polls = _recover_active_polls

    _patch_help_text()

    core.router.callback_query.outer_middleware(_FinalRatingLockMiddleware())
    core.router.callback_query(core.F.data == "vote_initiate")(_initiate_vote)
    core.router.callback_query(core.F.data == "vote_request_approve")(_approve_vote_request)
    core.router.callback_query(core.F.data == "vote_request_reject")(_reject_vote_request)
    core.router.callback_query(core.F.data == "show_stats_public")(_show_public_stats)
    core.router.callback_query(core.F.data.startswith("stats_public:"))(_public_stats_period_callback)
    core.router.message(core.Command("votetimer"))(_votetimer_command)
