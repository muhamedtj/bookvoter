"""Admin recovery controls for BookVoter lifecycle.

Provides safe rollback actions when a vote/read cycle needs to be restarted.
"""

from __future__ import annotations

import json

import bot_core as core
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

_installed = False
_original_get_admin_panel_view = None


async def _get_admin_panel_view(chat_id: int, lang: str, db_path: str = None):
    db_path = db_path or core.DATABASE_PATH
    text, markup = await _original_get_admin_panel_view(chat_id, lang, db_path=db_path)
    rows = [list(row) for row in markup.inline_keyboard]

    active_poll = await core.database.get_active_poll(db_path, chat_id)
    current_book = await core.database.get_current_reading_book(db_path, chat_id)

    if active_poll:
        rows.insert(1, [InlineKeyboardButton(
            text="↩️ Отменить голосование" if lang == "ru" else "↩️ Cancel voting",
            callback_data="admin_cancel_vote_reset",
        )])
    elif current_book:
        rows.insert(1, [InlineKeyboardButton(
            text="↩️ Отменить выбор книги" if lang == "ru" else "↩️ Cancel selected book",
            callback_data="admin_cancel_reading_reset",
        )])

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _cancel_vote(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if not await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer(core.t("only_admins_allowed", lang), show_alert=True)
        return

    active_poll = await core.database.get_active_poll(core.DATABASE_PATH, chat_id)
    if not active_poll:
        await callback.answer("Активного голосования нет." if lang == "ru" else "No active vote.", show_alert=True)
        return

    options = json.loads(active_poll.get("options_json") or "{}")
    book_ids = [int(v) for v in options.values()]

    try:
        await core.bot.stop_poll(chat_id, active_poll["message_id"])
    except Exception:
        pass
    try:
        await core.bot.delete_message(chat_id, active_poll["message_id"])
    except Exception:
        pass

    await core.database.update_books_status(core.DATABASE_PATH, book_ids, "backlog")
    await core.database.clear_active_poll(core.DATABASE_PATH, chat_id)

    try:
        poll_id = active_poll.get("poll_id")
        if poll_id and hasattr(core, "poll_votes_tracker"):
            core.poll_votes_tracker.pop(poll_id, None)
    except Exception:
        pass

    try:
        job_id = f"poll_end_{chat_id}_{active_poll['message_id']}"
        if core.scheduler.get_job(job_id):
            core.scheduler.remove_job(job_id)
    except Exception:
        pass

    if hasattr(core, "clear_stage_message"):
        try:
            await core.clear_stage_message(chat_id, delete_message=False)
        except Exception:
            pass

    await callback.answer("Голосование отменено. Книги возвращены в Лист ожидания." if lang == "ru" else "Voting cancelled. Books returned to backlog.", show_alert=True)
    text, markup = await core.get_admin_panel_view(chat_id, lang)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _cancel_reading(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if not await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer(core.t("only_admins_allowed", lang), show_alert=True)
        return

    current_book = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
    if not current_book:
        await callback.answer("Нет выбранной книги для отмены." if lang == "ru" else "No selected book to cancel.", show_alert=True)
        return

    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE books SET status = 'backlog' WHERE id = ? AND chat_id = ? AND status = 'reading'",
            (current_book["id"], chat_id),
        )
        await db.commit()

    # Remove the current BookVoter lifecycle pin/message (typically the downloaded file)
    # so the club can restart voting cleanly. Historical unrelated pins are untouched.
    if hasattr(core, "clear_stage_message"):
        try:
            await core.clear_stage_message(chat_id, delete_message=True)
        except Exception:
            pass

    await callback.answer("Выбор книги отменён. Книга возвращена в Лист ожидания — можно запускать голосование заново." if lang == "ru" else "Selected book cancelled and returned to backlog. You can start voting again.", show_alert=True)
    text, markup = await core.get_admin_panel_view(chat_id, lang)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


def install() -> None:
    global _installed, _original_get_admin_panel_view
    if _installed:
        return
    _installed = True

    _original_get_admin_panel_view = core.get_admin_panel_view
    core.get_admin_panel_view = _get_admin_panel_view

    core.router.callback_query.register(_cancel_vote, F.data == "admin_cancel_vote_reset")
    core.router.callback_query.register(_cancel_reading, F.data == "admin_cancel_reading_reset")
