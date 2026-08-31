"""Single active BookVoter stage pin for clean group UX.

BookVoter keeps at most one of its own lifecycle messages pinned at a time:
voting poll -> winner announcement -> downloaded book -> final rating/result.
Only the tracked BookVoter pin is unpinned; unrelated group pins are never touched.
Lifecycle history events such as the vote result stay in chat history.
"""

from __future__ import annotations

from typing import Optional

import bot_core as core


_installed = False
_original_launch_poll = None
_original_finish_vote = None
_original_get_admin_panel_view = None
_original_bot_send_message = None
_original_bot_send_document = None


async def _ensure_table() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stage_messages (
                chat_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE CASCADE
            )
            """
        )
        await db.commit()


async def _get_stage_message(chat_id: int) -> Optional[dict]:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT message_id, stage, pinned FROM stage_messages WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    return {"message_id": int(row[0]), "stage": str(row[1]), "pinned": bool(row[2])}


async def _save_stage_message(chat_id: int, message_id: int, stage: str, pinned: bool) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO stage_messages (chat_id, message_id, stage, pinned, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                message_id = excluded.message_id,
                stage = excluded.stage,
                pinned = excluded.pinned,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, message_id, stage, 1 if pinned else 0),
        )
        await db.commit()


async def _delete_stage_row(chat_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute("DELETE FROM stage_messages WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def has_pin_permission(chat_id: int) -> bool:
    if chat_id >= 0:
        return False
    try:
        me = await core.bot.get_me()
        member = await core.bot.get_chat_member(chat_id, me.id)
        status = getattr(getattr(member, "status", None), "value", getattr(member, "status", None))
        status = str(status or "").lower()
        if status in {"creator", "owner"}:
            return True
        if status != "administrator":
            return False
        return bool(getattr(member, "can_pin_messages", False))
    except Exception as exc:
        core.logger.debug(f"Could not inspect BookVoter pin permission in {chat_id}: {exc}")
        return False


async def _unpin_specific(chat_id: int, message_id: int) -> bool:
    try:
        await core.bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        core.logger.debug(f"Could not unpin BookVoter stage message {chat_id}/{message_id}: {exc}")
        return False


async def clear_stage_message(chat_id: int, *, delete_message: bool = False) -> bool:
    current = await _get_stage_message(chat_id)
    if not current:
        return False

    if current.get("pinned"):
        await _unpin_specific(chat_id, current["message_id"])

    if delete_message:
        try:
            await core.safe_delete_message(chat_id, current["message_id"])
        except Exception:
            pass

    await _delete_stage_row(chat_id)
    return True


async def set_stage_message(
    chat_id: int,
    message_id: int,
    stage: str,
    *,
    delete_previous: bool,
) -> bool:
    """Move BookVoter's one tracked stage marker to a new message.

    `delete_previous=False` keeps the previous lifecycle event in chat history
    while merely removing its pin. This is used for durable club-history events
    such as a completed vote result and the downloaded book.
    """
    if chat_id >= 0:
        return False

    current = await _get_stage_message(chat_id)
    if current and current["message_id"] != message_id:
        if current.get("pinned"):
            await _unpin_specific(chat_id, current["message_id"])
        if delete_previous:
            try:
                await core.safe_delete_message(chat_id, current["message_id"])
            except Exception:
                pass

    pinned = False
    if await has_pin_permission(chat_id):
        try:
            await core.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message_id,
                disable_notification=True,
            )
            pinned = True
        except Exception as exc:
            core.logger.warning(f"Could not pin BookVoter {stage} message in {chat_id}: {exc}")

    await _save_stage_message(chat_id, message_id, stage, pinned)
    return pinned


def _has_final_rating_buttons(markup) -> bool:
    rows = getattr(markup, "inline_keyboard", None) if markup else None
    if not rows:
        return False
    for row in rows:
        for button in row:
            data = getattr(button, "callback_data", None) or ""
            if data.startswith("vote_book:") or data.startswith("rate_read:"):
                return True
    return False


async def _send_message_with_stage(self, *args, **kwargs):
    msg = await _original_bot_send_message(self, *args, **kwargs)
    try:
        chat_id = int(getattr(getattr(msg, "chat", None), "id", kwargs.get("chat_id", 0)) or 0)
        text = kwargs.get("text")
        if text is None and len(args) >= 2:
            text = args[1]
        text = str(text or "")

        if chat_id < 0 and text.startswith((
            "🏆 <b>Выбрана следующая книга</b>",
            "🏆 <b>Next book selected</b>",
        )):
            # The live poll itself is obsolete once voting ends. Replace its pin
            # with the result announcement. The result announcement is a durable
            # club-history event and will remain when the book file arrives.
            await set_stage_message(chat_id, msg.message_id, "winner", delete_previous=True)

        elif chat_id < 0 and _has_final_rating_buttons(kwargs.get("reply_markup")):
            # Reading is finished: unpin the book file but keep the document in
            # chat history. The rating card becomes the active pinned stage.
            await set_stage_message(chat_id, msg.message_id, "rating", delete_previous=False)
    except Exception as exc:
        core.logger.debug(f"Could not classify sent BookVoter stage message: {exc}")
    return msg


async def _send_document_with_stage(self, *args, **kwargs):
    msg = await _original_bot_send_document(self, *args, **kwargs)
    try:
        chat_id = int(getattr(getattr(msg, "chat", None), "id", kwargs.get("chat_id", 0)) or 0)
        caption = str(kwargs.get("caption") or "")
        if chat_id < 0 and caption.startswith(("📚 Ваша книга:", "📚 Here is your book:")):
            # The file becomes the active pinned stage, but the completed vote
            # result stays permanently in chat history so members can see what
            # was chosen and why the file appeared.
            await set_stage_message(chat_id, msg.message_id, "reading", delete_previous=False)
    except Exception as exc:
        core.logger.debug(f"Could not pin downloaded BookVoter book: {exc}")
    return msg


async def _launch_poll_with_stage(chat_id: int, top_books, lang: str) -> bool:
    success = await _original_launch_poll(chat_id, top_books, lang)
    if success:
        try:
            active = await core.database.get_active_poll(core.DATABASE_PATH, chat_id)
            if active:
                await set_stage_message(
                    chat_id,
                    int(active["message_id"]),
                    "voting",
                    delete_previous=True,
                )
        except Exception as exc:
            core.logger.warning(f"Could not pin active BookVoter poll in {chat_id}: {exc}")
    return success


async def _finish_vote_with_stage(chat_id: int):
    result = await _original_finish_vote(chat_id)
    try:
        active = await core.database.get_active_poll(core.DATABASE_PATH, chat_id)
        reading = await core.database.get_current_reading_book(core.DATABASE_PATH, chat_id)
        current = await _get_stage_message(chat_id)
        # Zero-vote / cancelled vote: there is no winner message to replace the
        # poll, so remove the now-obsolete poll stage explicitly.
        if not active and not reading and current and current.get("stage") == "voting":
            await clear_stage_message(chat_id, delete_message=True)
    except Exception as exc:
        core.logger.debug(f"Could not reconcile stage pin after vote in {chat_id}: {exc}")
    return result


async def _admin_panel_with_pin_status(chat_id: int, lang: str, db_path: str = core.DATABASE_PATH):
    text, markup = await _original_get_admin_panel_view(chat_id, lang, db_path=db_path)
    if await has_pin_permission(chat_id):
        status = "📌 <b>Закрепление этапов:</b> включено" if lang == "ru" else "📌 <b>Stage pinning:</b> enabled"
    else:
        status = (
            "⚠️ <b>Закрепление этапов:</b> нет права «Закрепление сообщений»"
            if lang == "ru"
            else "⚠️ <b>Stage pinning:</b> grant the bot ‘Pin messages’ permission"
        )
    return f"{text}\n\n{status}", markup


def _patch_help_copy() -> None:
    try:
        import i18n

        ru = i18n.STRINGS.get("help_msg", {}).get("ru", "")
        en = i18n.STRINGS.get("help_msg", {}).get("en", "")
        if ru and "закреп" not in ru.lower():
            i18n.STRINGS["help_msg"]["ru"] = ru + (
                "\n\n📌 BookVoter автоматически закрепляет текущий этап: голосование → выбранную книгу → файл → итоговую оценку. "
                "При переходе к следующему этапу старое закрепление снимается, а итоги завершённого голосования остаются в истории чата."
            )
        if en and "pin" not in en.lower():
            i18n.STRINGS["help_msg"]["en"] = en + (
                "\n\n📌 BookVoter automatically pins the current stage: vote → selected book → file → final rating. "
                "The previous BookVoter pin is removed when the club advances, while completed vote results remain in chat history."
            )
    except Exception as exc:
        core.logger.warning(f"Could not patch stage-pin help copy: {exc}")


def install() -> None:
    global _installed
    global _original_launch_poll
    global _original_finish_vote
    global _original_get_admin_panel_view
    global _original_bot_send_message
    global _original_bot_send_document

    if _installed:
        return
    _installed = True

    _original_launch_poll = core.launch_poll_for_books
    core.launch_poll_for_books = _launch_poll_with_stage

    _original_finish_vote = core.finish_vote_process
    core.finish_vote_process = _finish_vote_with_stage

    _original_get_admin_panel_view = core.get_admin_panel_view
    core.get_admin_panel_view = _admin_panel_with_pin_status

    # Intercept only BookVoter's own lifecycle messages/documents; unrelated
    # messages are passed through untouched.
    _original_bot_send_message = core.Bot.send_message
    _original_bot_send_document = core.Bot.send_document
    core.Bot.send_message = _send_message_with_stage
    core.Bot.send_document = _send_document_with_stage

    core.set_stage_message = set_stage_message
    core.clear_stage_message = clear_stage_message
    core.has_pin_permission = has_pin_permission

    _patch_help_copy()
