"""Managed suggestion attribution for BookVoter.

Optional per-club mode: after a manager selects a book search result, BookVoter
asks who actually suggested it. The chosen club member is stored as
``suggested_by`` instead of the Telegram user who entered the book.
"""

from __future__ import annotations

from typing import Any

import bot_core as core
import runtime_suggest
from runtime_ux import label, schedule_message_delete


_installed = False
_original_admin_panel = None
_original_select_book = None
_original_callback_call = None
PAGE_SIZE = 16


async def _ensure_table() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS club_feature_settings (
                chat_id INTEGER PRIMARY KEY,
                manual_suggestor INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def is_manual_suggestor_enabled(chat_id: int) -> bool:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT manual_suggestor FROM club_feature_settings WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return bool(row and int(row[0]))


async def _set_manual_suggestor(chat_id: int, enabled: bool) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO club_feature_settings (chat_id, manual_suggestor, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                manual_suggestor = excluded.manual_suggestor,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, 1 if enabled else 0),
        )
        await db.commit()


def _setting_button(lang: str, enabled: bool):
    state = label(lang, en="ON", ru="ВКЛ") if enabled else label(lang, en="OFF", ru="ВЫКЛ")
    text = label(
        lang,
        en=f"👤 Choose suggestor manually: {state}",
        ru=f"👤 Ручной выбор автора предложения: {state}",
    )
    return core.InlineKeyboardButton(text=text, callback_data="toggle_manual_suggestor")


async def _admin_panel_with_manual_suggestor(chat_id: int, lang: str, db_path: str = core.DATABASE_PATH):
    text, markup = await _original_admin_panel(chat_id, lang, db_path=db_path)
    enabled = await is_manual_suggestor_enabled(chat_id)
    rows = [list(row) for row in (getattr(markup, "inline_keyboard", None) or [])]
    rows.append([_setting_button(lang, enabled)])
    suffix = label(
        lang,
        en=(
            "👤 <b>Suggestion attribution:</b> choose a club member after selecting a book"
            if enabled
            else "👤 <b>Suggestion attribution:</b> automatic (the person who enters the book)"
        ),
        ru=(
            "👤 <b>Автор предложения:</b> выбирается вручную после выбора книги"
            if enabled
            else "👤 <b>Автор предложения:</b> определяется автоматически"
        ),
    )
    return f"{text}\n\n{suffix}", core.InlineKeyboardMarkup(inline_keyboard=rows)


async def _club_members(chat_id: int) -> list[dict[str, Any]]:
    """Return all club members known to BookVoter, plus live Telegram admins.

    Telegram Bot API does not expose a general get-all-members method. The
    canonical member list is therefore BookVoter's chat_members table; current
    Telegram admins are merged in so a manager is available even before normal
    interaction with the bot.
    """
    members: dict[int, dict[str, Any]] = {}
    try:
        async with core.database.open_db(core.DATABASE_PATH) as db:
            async with db.execute(
                """
                SELECT u.tg_id, u.full_name, u.username
                FROM chat_members cm
                JOIN users u ON u.internal_id = cm.user_id
                WHERE cm.chat_id = ?
                ORDER BY COALESCE(NULLIF(u.full_name, ''), NULLIF(u.username, ''), CAST(u.tg_id AS TEXT)) COLLATE NOCASE
                """,
                (chat_id,),
            ) as cursor:
                for tg_id, full_name, username in await cursor.fetchall():
                    members[int(tg_id)] = {
                        "tg_id": int(tg_id),
                        "full_name": (full_name or "").strip(),
                        "username": (username or "").strip(),
                    }
    except Exception as exc:
        core.logger.warning(f"Could not read BookVoter members for {chat_id}: {exc}")

    try:
        for admin in await core.bot.get_chat_administrators(chat_id):
            user = getattr(admin, "user", None)
            if not user or getattr(user, "is_bot", False):
                continue
            members[user.id] = {
                "tg_id": int(user.id),
                "full_name": (getattr(user, "full_name", None) or "").strip(),
                "username": (getattr(user, "username", None) or "").strip(),
            }
    except Exception as exc:
        core.logger.debug(f"Could not merge Telegram admins for suggestor picker in {chat_id}: {exc}")

    return sorted(
        members.values(),
        key=lambda m: (m.get("full_name") or m.get("username") or str(m["tg_id"])).casefold(),
    )


def _member_label(member: dict[str, Any]) -> str:
    name = member.get("full_name") or (f"@{member['username']}" if member.get("username") else str(member["tg_id"]))
    return name[:48]


async def _show_member_picker(callback: core.CallbackQuery, token: str, page: int = 0) -> None:
    session = runtime_suggest._search_sessions.get(token)
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    if not session or session.get("chat_id") != callback.message.chat.id or session.get("user_id") != callback.from_user.id:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    members = await _club_members(callback.message.chat.id)
    if not members:
        await callback.answer(
            label(
                lang,
                en="No club members are known to BookVoter yet.",
                ru="BookVoter пока не знает участников этого клуба.",
            ),
            show_alert=True,
        )
        return

    pages = max(1, (len(members) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    visible = members[start:start + PAGE_SIZE]

    keyboard = [
        [core.InlineKeyboardButton(
            text=f"👤 {_member_label(member)}",
            callback_data=f"manual_suggestor_pick:{token}:{member['tg_id']}",
        )]
        for member in visible
    ]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(core.InlineKeyboardButton(text="⬅️", callback_data=f"manual_suggestor_page:{token}:{page - 1}"))
        nav.append(core.InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="manual_suggestor_noop"))
        if page + 1 < pages:
            nav.append(core.InlineKeyboardButton(text="➡️", callback_data=f"manual_suggestor_page:{token}:{page + 1}"))
        keyboard.append(nav)

    selected = session.get("manual_selected_book") or {}
    title = core.escape_html(selected.get("title") or "")
    text = label(
        lang,
        en=f"👤 <b>Who suggested this book?</b>\n\n📖 {title}\n\nChoose a club member:",
        ru=f"👤 <b>Кто предложил эту книгу?</b>\n\n📖 {title}\n\nВыберите участника клуба:",
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=core.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def _select_book_with_manual_mode(callback: core.CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    if not await is_manual_suggestor_enabled(chat_id):
        await _original_select_book(callback)
        return

    runtime_suggest._cleanup_expired()
    lang = await core.get_lang(chat_id, callback.from_user.id)
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
    if not session or session.get("chat_id") != chat_id or session.get("user_id") != callback.from_user.id:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return
    books = session.get("books") or []
    if index < 0 or index >= len(books):
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    selected = books[index]
    if await core.database.is_book_exists(
        core.DATABASE_PATH,
        chat_id=chat_id,
        title=selected["title"],
        author=selected["author"],
    ):
        runtime_suggest._search_sessions.pop(token, None)
        await callback.answer(core.t("book_already_exists", lang), show_alert=True)
        await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
        schedule_message_delete(chat_id, callback.message.message_id, 30)
        return

    session["manual_selected_book"] = selected
    await _show_member_picker(callback, token, 0)


async def _remember_member(member: dict[str, Any]) -> None:
    try:
        internal_id = await core.database.get_or_create_user(core.DATABASE_PATH, int(member["tg_id"]))
        async with core.database.open_db(core.DATABASE_PATH) as db:
            await db.execute(
                """
                UPDATE users
                SET full_name = CASE WHEN ? <> '' THEN ? ELSE full_name END,
                    username = CASE WHEN ? <> '' THEN ? ELSE username END
                WHERE internal_id = ?
                """,
                (
                    member.get("full_name") or "",
                    member.get("full_name") or "",
                    member.get("username") or "",
                    member.get("username") or "",
                    internal_id,
                ),
            )
            await db.commit()
    except Exception as exc:
        core.logger.debug(f"Could not refresh selected suggestor profile: {exc}")


async def _finalize_manual_suggestor(callback: core.CallbackQuery, token: str, tg_id: int) -> None:
    session = runtime_suggest._search_sessions.get(token)
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    if not session or session.get("chat_id") != callback.message.chat.id or session.get("user_id") != callback.from_user.id:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    selected = session.get("manual_selected_book")
    if not selected:
        await callback.answer(core.t("selection_expired", lang), show_alert=True)
        return

    members = await _club_members(callback.message.chat.id)
    member = next((m for m in members if int(m["tg_id"]) == int(tg_id)), None)
    if not member:
        await callback.answer(
            label(lang, en="This club member is no longer available.", ru="Этот участник клуба больше недоступен."),
            show_alert=True,
        )
        return

    chat_id = callback.message.chat.id
    if await core.database.is_book_exists(
        core.DATABASE_PATH,
        chat_id=chat_id,
        title=selected["title"],
        author=selected["author"],
    ):
        runtime_suggest._search_sessions.pop(token, None)
        await callback.answer(core.t("book_already_exists", lang), show_alert=True)
        await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
        schedule_message_delete(chat_id, callback.message.message_id, 30)
        return

    await _remember_member(member)
    try:
        await core.database.add_book(
            core.DATABASE_PATH,
            chat_id=chat_id,
            title=selected["title"],
            author=selected["author"],
            genre=None,
            suggested_by_tg_id=int(member["tg_id"]),
            file_id=selected.get("download_cmd") or None,
        )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower() or "integrity" in type(exc).__name__.lower():
            runtime_suggest._search_sessions.pop(token, None)
            await callback.answer(core.t("book_already_exists", lang), show_alert=True)
            await callback.message.edit_text(core.t("book_already_exists", lang), parse_mode="HTML")
            schedule_message_delete(chat_id, callback.message.message_id, 30)
            return
        raise

    runtime_suggest._search_sessions.pop(token, None)
    title = core.escape_html(selected["title"])
    author = core.escape_html(selected["author"])
    suggestor = core.escape_html(_member_label(member))
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


async def _toggle_setting(callback: core.CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    lang = await core.get_lang(chat_id, callback.from_user.id)
    if chat_id >= 0 or not await core.is_admin(chat_id, callback.from_user.id):
        await callback.answer(
            label(lang, en="Administrators only.", ru="Только для администраторов."),
            show_alert=True,
        )
        return
    enabled = not await is_manual_suggestor_enabled(chat_id)
    await _set_manual_suggestor(chat_id, enabled)
    text, markup = await core.get_admin_panel_view(chat_id, lang)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer(label(lang, en="Setting updated.", ru="Настройка обновлена."))


async def _patched_callback_call(self, handler, event, data):
    callback_data = getattr(event, "data", None) or ""
    if callback_data == "toggle_manual_suggestor":
        await _toggle_setting(event)
        return None
    if callback_data == "manual_suggestor_noop":
        await event.answer()
        return None
    if callback_data.startswith("manual_suggestor_page:"):
        parts = callback_data.split(":")
        if len(parts) == 3:
            try:
                await _show_member_picker(event, parts[1], int(parts[2]))
            except ValueError:
                await event.answer()
        return None
    if callback_data.startswith("manual_suggestor_pick:"):
        parts = callback_data.split(":")
        if len(parts) == 3:
            try:
                await _finalize_manual_suggestor(event, parts[1], int(parts[2]))
            except ValueError:
                await event.answer()
        return None
    return await _original_callback_call(self, handler, event, data)


def install() -> None:
    global _installed, _original_admin_panel, _original_select_book, _original_callback_call
    if _installed:
        return
    _installed = True

    _original_admin_panel = core.get_admin_panel_view
    core.get_admin_panel_view = _admin_panel_with_manual_suggestor

    _original_select_book = runtime_suggest._select_book
    runtime_suggest._select_book = _select_book_with_manual_mode

    _original_callback_call = runtime_suggest._SuggestionCallbackMiddleware.__call__
    runtime_suggest._SuggestionCallbackMiddleware.__call__ = _patched_callback_call

    core.is_manual_suggestor_enabled = is_manual_suggestor_enabled
