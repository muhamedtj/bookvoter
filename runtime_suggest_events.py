"""Permanent club event for successful book suggestions.

The title typed by the member and the ForceReply/search UI are transient. After a
book is selected, the search card is converted into a permanent club-history
message showing which book entered the waiting list and who suggested it.
"""

import bot_core as core
import runtime_suggest
from runtime_ux import label, schedule_message_delete


_installed = False


def _suggestor_name(user) -> str:
    name = (getattr(user, "full_name", None) or "").strip()
    if name:
        return core.escape_html(name)
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return core.escape_html(f"@{username}")
    return str(getattr(user, "id", "—"))


async def _select_book_as_event(callback: core.CallbackQuery) -> None:
    runtime_suggest._cleanup_expired()
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

    session = runtime_suggest._search_sessions.get(token)
    if not session:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    if session["chat_id"] != callback.message.chat.id or session["user_id"] != callback.from_user.id:
        await callback.answer(
            label(
                lang,
                en="This selection belongs to another member.",
                ru="Этот выбор принадлежит другому участнику.",
            ),
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
        runtime_suggest._search_sessions.pop(token, None)
        await callback.answer(core.t("book_already_exists", lang), show_alert=True)
        await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
        schedule_message_delete(chat_id, callback.message.message_id, 30)
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
        if (
            "unique" in str(exc).lower()
            or "duplicate" in str(exc).lower()
            or "integrity" in type(exc).__name__.lower()
        ):
            runtime_suggest._search_sessions.pop(token, None)
            await callback.answer(core.t("book_already_exists", lang), show_alert=True)
            await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
            schedule_message_delete(chat_id, callback.message.message_id, 30)
            return
        raise

    runtime_suggest._search_sessions.pop(token, None)

    title = core.escape_html(selected["title"])
    author = core.escape_html(selected["author"])
    suggestor = _suggestor_name(callback.from_user)

    if lang == "ru":
        text = (
            f"✅ <b>«{title}»</b> — {author} добавлена в <b>Лист ожидания</b>.\n"
            f"👤 Предложил: <b>{suggestor}</b>\n\n"
            "Нажмите ниже, чтобы оценить книги из Листа ожидания в личных сообщениях."
        )
    else:
        text = (
            f"✅ <b>«{title}»</b> — {author} was added to the <b>Waiting list</b>.\n"
            f"👤 Suggested by: <b>{suggestor}</b>\n\n"
            "Tap below to rate Waiting-list books in private messages."
        )

    bot_info = await core.bot.get_me()
    await callback.answer(f"✅ {selected['title']}"[:180], show_alert=True)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=runtime_suggest._rate_markup(chat_id, lang, bot_info.username),
    )
    # Deliberately no deletion timer: a successful suggestion is a club event,
    # not transient UI. The member's typed title and prompt are deleted earlier.


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # The callback middleware resolves this module-level function at runtime.
    runtime_suggest._select_book = _select_book_as_event
