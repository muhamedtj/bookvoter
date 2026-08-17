"""BookVoter application entrypoint.

The existing implementation lives in :mod:`bot_core`. This thin layer keeps the
stable core intact while we iterate on user-facing navigation and operational
fixes without another large monolithic rewrite.
"""

import asyncio
import sys
from typing import Optional

import bot_core as _core


def _label(lang: str, *, en: str, ru: str) -> str:
    return ru if lang == "ru" else en


def get_welcome_keyboard(
    lang: str,
    bot_username: str,
    is_private: bool = False,
    chat_id: Optional[int] = None,
):
    """Build public navigation for group members and private-chat users."""
    if not is_private and chat_id and chat_id < 0:
        rate_url = f"https://t.me/{bot_username}?start=rate_c{abs(chat_id)}"
        keyboard = [
            [
                _core.InlineKeyboardButton(
                    text=_label(lang, en="📚 Waiting list", ru="📚 Лист ожидания"),
                    callback_data="show_backlog",
                ),
                _core.InlineKeyboardButton(
                    text=_label(lang, en="🏆 Hall of Fame", ru="🏆 Зал славы"),
                    callback_data="show_hof",
                ),
            ],
            [_core.InlineKeyboardButton(text=_core.t("btn_rate_books", lang), url=rate_url)],
            [
                _core.InlineKeyboardButton(
                    text=_label(lang, en="➕ Suggest a book", ru="➕ Предложить книгу"),
                    callback_data="show_suggest_help",
                )
            ],
            [_core.InlineKeyboardButton(text=_core.t("btn_how_it_works", lang), callback_data="show_help")],
            [_core.InlineKeyboardButton(text=_core.t("btn_report_error", lang), callback_data="report_error")],
        ]
    else:
        rate_url = f"https://t.me/{bot_username}?start=rate_new"
        keyboard = [
            [_core.InlineKeyboardButton(text=_core.t("btn_rate_books", lang), url=rate_url)],
            [_core.InlineKeyboardButton(text=_core.t("btn_how_it_works", lang), callback_data="show_help")],
            [_core.InlineKeyboardButton(text=_core.t("btn_report_error", lang), callback_data="report_error")],
        ]

    return _core.InlineKeyboardMarkup(inline_keyboard=keyboard)


# Existing handlers resolve this global at runtime, so replacing it here updates
# /start, language switching and the Back button without duplicating handlers.
_core.get_welcome_keyboard = get_welcome_keyboard


def _admin_status(status) -> bool:
    """Normalize Telegram/aiogram admin status values."""
    value = getattr(status, "value", status)
    return str(value).lower() in {"creator", "owner", "administrator"}


async def is_admin(chat_id: int, user_id: int) -> bool:
    """Check group admin/owner status with a resilient administrators-list fallback."""
    if user_id in _core.SUPER_ADMIN_IDS:
        return True
    if chat_id > 0:
        return False

    member_error = None
    try:
        member = await _core.bot.get_chat_member(chat_id, user_id)
        if _admin_status(getattr(member, "status", None)):
            return True
    except Exception as exc:
        member_error = exc

    # get_chat_member can be unreliable for arbitrary members when the bot has
    # limited group permissions. The administrators list is a better fallback
    # for detecting the group's creator/owner and administrators.
    try:
        administrators = await _core.bot.get_chat_administrators(chat_id)
        for admin_member in administrators:
            admin_user = getattr(admin_member, "user", None)
            if getattr(admin_user, "id", None) == user_id:
                return True
    except Exception as exc:
        if member_error:
            _core.logger.warning(
                f"Admin check failed for user {user_id} in chat {chat_id}: "
                f"get_chat_member={member_error}; get_chat_administrators={exc}"
            )
        else:
            _core.logger.warning(
                f"Failed to load administrators for user {user_id} in chat {chat_id}: {exc}"
            )

    return False


_core.is_admin = is_admin


async def notify_superadmin_error(
    error_title: str,
    error_traceback: str,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    context_info: str = "",
):
    """Deliver diagnostics to configured superadmins.

    Manual reports have a group-admin DM fallback so the button remains useful
    even when SUPER_ADMIN_IDS is not configured yet. Internal tracebacks never
    use that fallback and therefore are not exposed to club administrators.
    """
    report = (
        f"🚨 <b>Critical Error Report</b>\n\n"
        f"📌 <b>Title:</b> {_core.escape_html(error_title)}\n"
        f"👤 <b>User ID:</b> {user_id or 'N/A'}\n"
        f"💬 <b>Chat ID:</b> {chat_id or 'N/A'}\n"
        f"⏰ <b>Timestamp:</b> {_core.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📝 <b>Context:</b> {_core.escape_html(context_info or 'N/A')}\n\n"
        f"📋 <b>Traceback:</b>\n<pre>{_core.escape_html(error_traceback[-1500:])}</pre>"
    )

    delivered = False
    attempted_ids = set()

    for admin_id in _core.SUPER_ADMIN_IDS:
        attempted_ids.add(admin_id)
        try:
            await _core.bot.send_message(admin_id, report, parse_mode="HTML")
            delivered = True
        except Exception as exc:
            _core.logger.error(f"Failed to send error report to superadmin {admin_id}: {exc}")

    is_manual_report = error_title.startswith("User Error Report")

    if not delivered and is_manual_report and chat_id is not None and chat_id < 0:
        try:
            administrators = await _core.bot.get_chat_administrators(chat_id)
        except Exception as exc:
            administrators = []
            _core.logger.error(f"Failed to get group administrators for error-report fallback: {exc}")

        for member in administrators:
            admin_user = getattr(member, "user", None)
            admin_id = getattr(admin_user, "id", None)
            is_bot = bool(getattr(admin_user, "is_bot", False))
            if not admin_id or is_bot or admin_id in attempted_ids:
                continue
            attempted_ids.add(admin_id)
            try:
                await _core.bot.send_message(admin_id, report, parse_mode="HTML")
                delivered = True
            except Exception as exc:
                _core.logger.warning(f"Could not DM group admin {admin_id} with error report: {exc}")

    if not delivered:
        _core.logger.error(f"[Error report not delivered by DM] {report}")

    return delivered


_core.notify_superadmin_error = notify_superadmin_error


def _back_markup(lang: str):
    return _core.InlineKeyboardMarkup(
        inline_keyboard=[
            [_core.InlineKeyboardButton(text=_core.t("btn_back", lang), callback_data="back_to_welcome")]
        ]
    )


async def _get_suggestor_display(book_id: int) -> str:
    """Resolve the member who originally suggested a book."""
    async with _core.database.open_db(_core.DATABASE_PATH) as db:
        async with db.execute(
            """
            SELECT u.full_name, u.username
            FROM books b
            LEFT JOIN users u ON b.suggested_by = u.internal_id
            WHERE b.id = ?
            """,
            (book_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return ""

    full_name, username = row
    if full_name:
        return _core.escape_html(full_name)
    if username:
        return _core.escape_html(f"@{username}")
    return ""


async def send_next_unrated_book(user_tg_id: int, target_msg_or_user, lang: str):
    """Show the next private interest-rating card, including who suggested it."""
    unrated_books = await _core.database.get_unrated_backlog_books_for_user(
        _core.DATABASE_PATH, user_tg_id
    )
    if not unrated_books:
        text = _core.t("all_caught_up_rating", lang)
        if isinstance(target_msg_or_user, _core.Message):
            await target_msg_or_user.answer(text, parse_mode="HTML")
        elif isinstance(target_msg_or_user, _core.CallbackQuery):
            await target_msg_or_user.message.edit_text(text, parse_mode="HTML")
        return

    book = unrated_books[0]
    row1 = [
        _core.InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}")
        for i in range(1, 6)
    ]
    row2 = [
        _core.InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}")
        for i in range(6, 11)
    ]
    markup = _core.InlineKeyboardMarkup(inline_keyboard=[row1, row2])

    msg_text = _core.t(
        "rate_prompt_group",
        lang,
        chat_title=_core.escape_html(book["chat_title"]),
        title=_core.escape_html(book["title"]),
        author=_core.escape_html(book["author"]),
    )

    suggestor = await _get_suggestor_display(book["id"])
    if suggestor:
        suggested_label = _label(lang, en="Suggested by", ru="Предложил")
        msg_text += f"\n\n👤 <b>{suggested_label}:</b> {suggestor}"

    if isinstance(target_msg_or_user, _core.Message):
        await target_msg_or_user.answer(msg_text, parse_mode="HTML", reply_markup=markup)
    elif isinstance(target_msg_or_user, _core.CallbackQuery):
        await target_msg_or_user.message.edit_text(msg_text, parse_mode="HTML", reply_markup=markup)


_core.send_next_unrated_book = send_next_unrated_book


async def send_halloffame_response(
    chat_id: int,
    lang: str,
    target_msg_or_cb,
    user_id: Optional[int] = None,
):
    """Render Hall of Fame with the original suggestor shown in parentheses."""
    data = await _core.database.get_hall_of_fame_detailed(
        _core.DATABASE_PATH,
        chat_id,
        min_votes=_core.MIN_VOTES_FOR_RATING,
    )
    qualified = data["qualified"]
    low_votes = data["low_votes"]

    if user_id is None:
        source_user = getattr(target_msg_or_cb, "from_user", None)
        if source_user is None and isinstance(target_msg_or_cb, _core.CallbackQuery):
            source_user = target_msg_or_cb.from_user
        user_id = getattr(source_user, "id", None)

    user_is_admin = bool(user_id and await is_admin(chat_id, user_id))

    markup = None
    if user_is_admin and (qualified or low_votes):
        markup = _core.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _core.InlineKeyboardButton(
                        text=_core.t("btn_hof_delete_book", lang),
                        callback_data="hof_del_menu",
                    )
                ]
            ]
        )

    if not qualified and not low_votes:
        text = _core.t("hof_empty", lang)
        if isinstance(target_msg_or_cb, _core.Message):
            await target_msg_or_cb.answer(text, parse_mode="HTML")
        elif isinstance(target_msg_or_cb, _core.CallbackQuery):
            await target_msg_or_cb.message.edit_text(text, parse_mode="HTML")
        return

    async def append_book(text: str, idx: int, item: dict) -> str:
        suggestor = await _get_suggestor_display(item["id"])
        title = _core.escape_html(item["title"])
        if suggestor:
            title = f"{title} ({suggestor})"
        return text + _core.t(
            "hof_item_format",
            lang,
            idx=idx,
            title=title,
            author=_core.escape_html(item["author"]),
            wr=item["weighted_rating"],
            count=item["live_votes_count"],
        ) + "\n"

    text = _core.t("hof_header", lang)
    if qualified:
        for idx, item in enumerate(qualified, 1):
            text = await append_book(text, idx, item)
    else:
        text += "—\n"

    if low_votes:
        text += _core.t(
            "hof_low_votes_header",
            lang,
            min_votes=_core.MIN_VOTES_FOR_RATING,
        )
        for idx, item in enumerate(low_votes, 1):
            text = await append_book(text, idx, item)

    await _core.send_split_messages(target_msg_or_cb, text, reply_markup=markup)


_core.send_halloffame_response = send_halloffame_response


@_core.router.callback_query(_core.F.data == "show_backlog")
async def _show_backlog(callback: _core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await _core.get_lang(chat_id, callback.from_user.id)

    if callback.message.chat.type == _core.ChatType.PRIVATE:
        await callback.answer(
            _label(
                lang,
                en="Open this section in your book-club group.",
                ru="Откройте этот раздел в группе книжного клуба.",
            ),
            show_alert=True,
        )
        return

    books = await _core.database.get_backlog_books_full_info(_core.DATABASE_PATH, chat_id)
    await callback.answer()

    if not books:
        await callback.message.edit_text(
            _core.t("backlog_empty", lang),
            parse_mode="HTML",
            reply_markup=_back_markup(lang),
        )
        return

    text = _core.t("backlog_list_header", lang)
    for idx, book in enumerate(books, 1):
        suggestor = _core.escape_html(
            book["suggestor_name"]
            or (f"@{book['suggestor_username']}" if book["suggestor_username"] else "N/A")
        )
        text += _core.t(
            "backlog_item_format",
            lang,
            idx=idx,
            title=_core.escape_html(book["title"]),
            author=_core.escape_html(book["author"]),
            score=round(book["wish_score"], 1),
            suggestor=suggestor,
        )

    bot_info = await _core.bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_c{abs(chat_id)}"
    markup = _core.InlineKeyboardMarkup(
        inline_keyboard=[
            [_core.InlineKeyboardButton(text=_core.t("btn_rate_books", lang), url=rate_url)],
            [_core.InlineKeyboardButton(text=_core.t("btn_back", lang), callback_data="back_to_welcome")],
        ]
    )
    await _core.send_split_messages(callback, text, reply_markup=markup)


@_core.router.callback_query(_core.F.data == "show_hof")
async def _show_hall_of_fame(callback: _core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await _core.get_lang(chat_id, callback.from_user.id)

    if callback.message.chat.type == _core.ChatType.PRIVATE:
        await callback.answer(
            _label(
                lang,
                en="Open this section in your book-club group.",
                ru="Откройте этот раздел в группе книжного клуба.",
            ),
            show_alert=True,
        )
        return

    await callback.answer()
    await send_halloffame_response(
        chat_id,
        lang,
        target_msg_or_cb=callback,
        user_id=callback.from_user.id,
    )


@_core.router.callback_query(_core.F.data == "show_suggest_help")
async def _show_suggest_help(callback: _core.CallbackQuery):
    lang = await _core.get_lang(callback.message.chat.id, callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(
        _core.t("suggest_usage", lang),
        parse_mode="HTML",
        reply_markup=_back_markup(lang),
    )


# When imported (tests, scripts), expose the patched core module under the public
# name `bot` so monkeypatches such as `bot.bot = ...` keep working as before.
if __name__ == "__main__":
    asyncio.run(_core.main())
else:
    sys.modules[__name__] = _core
