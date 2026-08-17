"""BookVoter application entrypoint.

The existing implementation lives in :mod:`bot_core`.  This thin layer keeps the
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
    """Public navigation.

    The control panel is deliberately not shown in the common group keyboard:
    inline keyboards are visible to every member of a Telegram group, so an
    admin-only button cannot truly be rendered per viewer.  Administrators keep
    access through /admin and /bookvoter.
    """
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


async def notify_superadmin_error(
    error_title: str,
    error_traceback: str,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    context_info: str = "",
):
    """Deliver diagnostics to configured superadmins, with a safe group-admin fallback.

    Telegram bots cannot DM arbitrary users who never opened the bot.  If the
    configured SUPER_ADMIN_IDS are missing or delivery fails, a report triggered
    from a club group is therefore also attempted via that group's administrators.
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

    # Fallback for manual reports/errors originating in a club group.  This makes
    # the feature useful even before SUPER_ADMIN_IDS is configured correctly.
    if not delivered and chat_id is not None and chat_id < 0:
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


@_core.router.callback_query(_core.F.data == "show_backlog")
async def _show_backlog(callback: _core.CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await _core.get_lang(chat_id, callback.from_user.id)

    if callback.message.chat.type == _core.ChatType.PRIVATE:
        await callback.answer(
            _label(lang, en="Open this section in your book-club group.", ru="Откройте этот раздел в группе книжного клуба."),
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
            _label(lang, en="Open this section in your book-club group.", ru="Откройте этот раздел в группе книжного клуба."),
            show_alert=True,
        )
        return

    data = await _core.database.get_hall_of_fame_detailed(
        _core.DATABASE_PATH,
        chat_id,
        min_votes=_core.MIN_VOTES_FOR_RATING,
    )
    qualified = data["qualified"]
    low_votes = data["low_votes"]
    await callback.answer()

    if not qualified and not low_votes:
        await callback.message.edit_text(
            _core.t("hof_empty", lang),
            parse_mode="HTML",
            reply_markup=_back_markup(lang),
        )
        return

    text = _core.t("hof_header", lang)
    if qualified:
        for idx, item in enumerate(qualified, 1):
            text += _core.t(
                "hof_item_format",
                lang,
                idx=idx,
                title=_core.escape_html(item["title"]),
                author=_core.escape_html(item["author"]),
                wr=item["weighted_rating"],
                count=item["live_votes_count"],
            ) + "\n"
    else:
        text += "—\n"

    if low_votes:
        text += _core.t("hof_low_votes_header", lang, min_votes=_core.MIN_VOTES_FOR_RATING)
        for idx, item in enumerate(low_votes, 1):
            text += _core.t(
                "hof_item_format",
                lang,
                idx=idx,
                title=_core.escape_html(item["title"]),
                author=_core.escape_html(item["author"]),
                wr=item["weighted_rating"],
                count=item["live_votes_count"],
            ) + "\n"

    await _core.send_split_messages(callback, text, reply_markup=_back_markup(lang))


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
