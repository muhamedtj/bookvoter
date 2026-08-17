"""Button-based book suggestion flow for BookVoter.

The public UX no longer requires the generic /suggest command. Users tap
"Suggest a book", reply to a short ForceReply prompt, choose a search result,
and BookVoter removes the temporary chat messages afterwards.
"""

import secrets
import time
from typing import Dict, Any

from aiogram import BaseMiddleware
from aiogram.types import ForceReply

import bot_core as core
from runtime_ux import label, schedule_message_delete, safe_delete_message


PROMPT_TTL_SECONDS = 300
SEARCH_TTL_SECONDS = 600
_pending_prompts: Dict[tuple[int, int], Dict[str, Any]] = {}
_search_sessions: Dict[str, Dict[str, Any]] = {}
_installed = False


def _cleanup_expired() -> None:
    now = time.time()
    for key, value in list(_pending_prompts.items()):
        if now - value.get("created_at", 0) > PROMPT_TTL_SECONDS:
            _pending_prompts.pop(key, None)
    for token, value in list(_search_sessions.items()):
        if now - value.get("created_at", 0) > SEARCH_TTL_SECONDS:
            _search_sessions.pop(token, None)


def _rate_markup(chat_id: int, lang: str, bot_username: str):
    rate_url = f"https://t.me/{bot_username}?start=rate_c{abs(chat_id)}"
    return core.InlineKeyboardMarkup(
        inline_keyboard=[
            [core.InlineKeyboardButton(text=core.t("btn_rate_books", lang), url=rate_url)]
        ]
    )


async def _start_suggestion(callback: core.CallbackQuery) -> None:
    chat = callback.message.chat
    lang = await core.get_lang(chat.id, callback.from_user.id)

    if chat.type == core.ChatType.PRIVATE:
        await callback.answer(
            label(
                lang,
                en="Suggest books from your book-club group.",
                ru="Предлагайте книги из группы книжного клуба.",
            ),
            show_alert=True,
        )
        return

    _cleanup_expired()
    old = _pending_prompts.pop((chat.id, callback.from_user.id), None)
    if old:
        await safe_delete_message(chat.id, old.get("prompt_message_id"))

    prompt_text = label(
        lang,
        en=(
            "📖 <b>Suggest a book</b>\n\n"
            "Reply to this message with the book title. "
            "The prompt and your reply will be removed after processing."
        ),
        ru=(
            "📖 <b>Предложить книгу</b>\n\n"
            "Ответьте на это сообщение названием книги. "
            "После обработки подсказка и ваш ответ будут удалены."
        ),
    )
    placeholder = label(lang, en="Book title", ru="Название книги")

    prompt = await core.bot.send_message(
        chat.id,
        prompt_text,
        parse_mode="HTML",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder=placeholder[:64],
        ),
    )
    _pending_prompts[(chat.id, callback.from_user.id)] = {
        "prompt_message_id": prompt.message_id,
        "created_at": time.time(),
    }
    schedule_message_delete(chat.id, prompt.message_id, PROMPT_TTL_SECONDS)
    await callback.answer()


async def _handle_title_reply(message: core.Message) -> bool:
    if not message.from_user:
        return False
    if message.chat.type not in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        return False

    _cleanup_expired()
    key = (message.chat.id, message.from_user.id)
    pending = _pending_prompts.get(key)
    if not pending:
        return False

    reply = message.reply_to_message
    if not reply or reply.message_id != pending.get("prompt_message_id"):
        return False

    _pending_prompts.pop(key, None)
    await core.register_user_and_chat(message)

    query = (message.text or message.caption or "").strip()
    await safe_delete_message(message.chat.id, message.message_id)
    await safe_delete_message(message.chat.id, pending.get("prompt_message_id"))

    lang = await core.get_lang(message.chat.id, message.from_user.id)
    if not query:
        notice = await core.bot.send_message(
            message.chat.id,
            label(lang, en="⚠️ Send a book title.", ru="⚠️ Укажите название книги."),
        )
        schedule_message_delete(message.chat.id, notice.message_id, 45)
        return True

    query = query[:200]
    status = await core.bot.send_message(message.chat.id, core.t("searching_google_books", lang))
    schedule_message_delete(message.chat.id, status.message_id, SEARCH_TTL_SECONDS)

    books = await core.fetch_books_via_userbot(query, lang)
    if books is None:
        await status.edit_text(core.t("google_books_api_error", lang))
        schedule_message_delete(message.chat.id, status.message_id, 90)
        return True
    if not books:
        await status.edit_text(core.t("no_valid_books_found", lang))
        schedule_message_delete(message.chat.id, status.message_id, 90)
        return True

    token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    _search_sessions[token] = {
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
        "books": books,
        "created_at": time.time(),
    }

    keyboard = []
    for idx, book in enumerate(books[:8]):
        title = (book.get("title") or "")[:28]
        author = (book.get("author") or "").strip()
        if author.lower() in {"unknown author", "n/a", "none"}:
            author = ""
        author = author[:16]
        text = f"📖 {title}" + (f" — {author}" if author else "")
        keyboard.append([
            core.InlineKeyboardButton(
                text=text,
                callback_data=f"book_suggest_select:{token}:{idx}",
            )
        ])

    await status.edit_text(
        core.t("select_matching_book", lang),
        reply_markup=core.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    return True


async def _select_book(callback: core.CallbackQuery) -> None:
    _cleanup_expired()
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    token = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    session = _search_sessions.get(token)
    if not session:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return
    if session["chat_id"] != callback.message.chat.id or session["user_id"] != callback.from_user.id:
        await callback.answer(
            label(lang, en="This selection belongs to another member.", ru="Этот выбор принадлежит другому участнику."),
            show_alert=True,
        )
        return

    books = session.get("books") or []
    if index < 0 or index >= len(books):
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    selected = books[index]
    chat_id = callback.message.chat.id
    exists = await core.database.is_book_exists(
        core.DATABASE_PATH,
        chat_id=chat_id,
        title=selected["title"],
        author=selected["author"],
    )
    if exists:
        _search_sessions.pop(token, None)
        await callback.answer(core.t("book_already_exists", lang), show_alert=True)
        await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
        schedule_message_delete(chat_id, callback.message.message_id, 60)
        return

    try:
        await core.database.add_book(
            core.DATABASE_PATH,
            chat_id=chat_id,
            title=selected["title"],
            author=selected["author"],
            genre=None,
            suggested_by_tg_id=callback.from_user.id,
            file_id=selected.get("download_cmd") or None,
        )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower() or "integrity" in type(exc).__name__.lower():
            _search_sessions.pop(token, None)
            await callback.answer(core.t("book_already_exists", lang), show_alert=True)
            await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
            schedule_message_delete(chat_id, callback.message.message_id, 60)
            return
        raise

    _search_sessions.pop(token, None)
    bot_info = await core.bot.get_me()
    text = (
        core.t(
            "book_added_confirmation_exact",
            lang,
            title=core.escape_html(selected["title"]),
            author=core.escape_html(selected["author"]),
        )
        + "\n\n"
        + core.t("click_below_to_rate", lang)
    )
    await callback.answer(
        f"✅ {selected['title']}"[:180],
        show_alert=True,
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_rate_markup(chat_id, lang, bot_info.username),
    )
    schedule_message_delete(chat_id, callback.message.message_id, 120)


class _SuggestionCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        callback_data = getattr(event, "data", None) or ""
        if callback_data == "show_suggest_help":
            await _start_suggestion(event)
            return None
        if callback_data.startswith("book_suggest_select:"):
            await _select_book(event)
            return None
        return await handler(event, data)


class _SuggestionMessageMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if await _handle_title_reply(event):
            return None
        return await handler(event, data)


def _install_copy() -> None:
    # Update visible product copy without rewriting the stable i18n module.
    try:
        import i18n

        i18n.STRINGS["welcome_msg"]["en"] = (
            "📚 <b>BookVoter</b>\n\n"
            "Choose your next book with your club.\n\n"
            "Suggest books, rate interest, vote, and give final ratings after reading.\n\n"
            "📖 To suggest a book, tap <b>➕ Suggest a book</b> below."
        )
        i18n.STRINGS["welcome_msg"]["ru"] = (
            "📚 <b>BookVoter</b>\n\n"
            "Выбирайте следующую книгу вместе с клубом.\n\n"
            "Предлагайте книги, оценивайте интерес, голосуйте и выставляйте итоговые оценки после чтения.\n\n"
            "📖 Чтобы предложить книгу, нажмите <b>➕ Предложить книгу</b> ниже."
        )

        i18n.STRINGS["help_msg"]["en"] = (
            "ℹ️ <b>How BookVoter Works</b>\n\n"
            "1. <b>Suggest a book</b>\n"
            "   Tap <b>➕ Suggest a book</b> in the group, reply with the title and choose the matching edition.\n\n"
            "2. <b>Rate your interest</b>\n"
            "   Rate waiting-list books privately from 1 to 10. The highest-rated books rise to the top.\n\n"
            "3. <b>Start the club vote</b>\n"
            "   Any member may request a vote. An administrator can approve or cancel it; if no admin reacts within 24 hours, the vote starts automatically.\n\n"
            "4. <b>Vote for the next book</b>\n"
            "   The administrator controls vote duration with <code>/booktimer 2h</code> (or minutes such as <code>/booktimer 30m</code>). The vote may close earlier when at least 3 members voted and one option has more than 50%.\n\n"
            "5. <b>Read and rate</b>\n"
            "   After the result, BookVoter searches for the winning book and sends the file. When reading is finished, each member gives one final score or chooses “Didn't read”.\n\n"
            "🏆 Completed books enter the <b>Hall of Fame</b>.\n"
            "🐛 Problems can be reported from the menu."
        )
        i18n.STRINGS["help_msg"]["ru"] = (
            "ℹ️ <b>Как работает BookVoter</b>\n\n"
            "1. <b>Предложите книгу</b>\n"
            "   Нажмите <b>➕ Предложить книгу</b> в группе, ответьте названием и выберите подходящее издание.\n\n"
            "2. <b>Оцените интерес</b>\n"
            "   В личных сообщениях оцените книги Листа ожидания от 1 до 10. Книги с более высоким интересом поднимаются выше.\n\n"
            "3. <b>Инициируйте голосование</b>\n"
            "   Любой участник может запросить голосование. Администратор может подтвердить или отменить его; если за 24 часа реакции нет, голосование стартует автоматически.\n\n"
            "4. <b>Выберите следующую книгу</b>\n"
            "   Администратор задаёт длительность через <code>/booktimer 2h</code> или, например, <code>/booktimer 30m</code>. Голосование может закрыться раньше, если проголосовало минимум 3 человека и одна книга набрала больше 50%.\n\n"
            "5. <b>Читайте и оценивайте</b>\n"
            "   После результата BookVoter ищет победившую книгу и отправляет файл. После завершения чтения каждый участник один раз ставит итоговую оценку или выбирает «Не читал».\n\n"
            "🏆 Прочитанные книги попадают в <b>Зал славы</b>.\n"
            "🐛 О проблемах можно сообщить из меню."
        )
    except Exception as exc:
        core.logger.warning(f"Could not install updated suggestion copy: {exc}")


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    _install_copy()
    core.router.callback_query.outer_middleware(_SuggestionCallbackMiddleware())
    core.router.message.outer_middleware(_SuggestionMessageMiddleware())
