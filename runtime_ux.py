"""BookVoter product UI and chat-cleanup overrides."""

import asyncio
from typing import Optional
from aiogram import BaseMiddleware
import bot_core as core

active_ui_messages = {}
_cleanup_tasks = set()
_cleanup_tasks_by_message = {}
_installed = False

def label(lang: str, *, en: str, ru: str) -> str:
    return ru if lang == "ru" else en

async def safe_delete_message(chat_id: int, message_id: int) -> bool:
    try:
        await core.bot.delete_message(chat_id=chat_id, message_id=message_id); return True
    except Exception as exc:
        core.logger.debug(f"Could not delete message {message_id} in chat {chat_id}: {exc}"); return False

def cancel_message_delete(chat_id: int, message_id: int) -> None:
    task = _cleanup_tasks_by_message.pop((chat_id, message_id), None)
    if task and not task.done(): task.cancel()

def schedule_message_delete(chat_id: int, message_id: int, delay_seconds: int = 90) -> None:
    cancel_message_delete(chat_id, message_id)
    async def _delete_later():
        try:
            await asyncio.sleep(max(0, delay_seconds)); await safe_delete_message(chat_id, message_id)
            if active_ui_messages.get(chat_id) == message_id: active_ui_messages.pop(chat_id, None)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            core.logger.debug(f"Cleanup task failed for {chat_id}/{message_id}: {exc}")
        finally:
            if _cleanup_tasks_by_message.get((chat_id, message_id)) is asyncio.current_task():
                _cleanup_tasks_by_message.pop((chat_id, message_id), None)
    task = asyncio.create_task(_delete_later()); _cleanup_tasks.add(task); _cleanup_tasks_by_message[(chat_id, message_id)] = task
    task.add_done_callback(_cleanup_tasks.discard)

async def cleanup_command_message(message) -> None:
    if message.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        await safe_delete_message(message.chat.id, message.message_id)

_ORIGINAL_MESSAGE_ANSWER = core.Message.answer
_ORIGINAL_MESSAGE_EDIT_TEXT = core.Message.edit_text

async def send_or_replace_group_ui(message, text: str, parse_mode="HTML", reply_markup=None):
    if message.chat.type == core.ChatType.PRIVATE:
        return await _ORIGINAL_MESSAGE_ANSWER(message, text, parse_mode=parse_mode, reply_markup=reply_markup)
    previous_id = active_ui_messages.get(message.chat.id)
    if previous_id:
        cancel_message_delete(message.chat.id, previous_id); await safe_delete_message(message.chat.id, previous_id)
    sent = await _ORIGINAL_MESSAGE_ANSWER(message, text, parse_mode=parse_mode, reply_markup=reply_markup)
    active_ui_messages[message.chat.id] = sent.message_id; return sent

async def send_ephemeral_reply(message, text: str, delay_seconds: int = 45, parse_mode=None, reply_markup=None):
    sent = await _ORIGINAL_MESSAGE_ANSWER(message, text, parse_mode=parse_mode, reply_markup=reply_markup)
    if message.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP): schedule_message_delete(sent.chat.id, sent.message_id, delay_seconds)
    return sent

def _bot_message_policy(text):
    if not isinstance(text, str) or not text.strip(): return None, None
    plain = text.strip()
    ui_prefixes = ("📚 <b>BookVoter", "📚 **BookVoter", "⚙️", "📚 <b>Club Backlog", "📚 <b>Бэклог клуба", "🏆 <b>Hall of Fame", "🏆 <b>Зал славы", "📊 <b>Group Statistics", "📊 <b>Статистика группы", "🌐 <b>", "ℹ️ <b>How BookVoter Works", "ℹ️ <b>Как работает BookVoter")
    if plain.startswith(ui_prefixes): return "ui", None
    if plain.startswith("🔎"): return "ephemeral", 600
    if plain.startswith(("✅", "⚠️", "❌")): return "ephemeral", 90
    if plain.startswith(("Пожалуйста, укажите название книги", "Please specify a book title")): return "ephemeral", 60
    if plain.startswith(("Выберите подходящую книгу", "Select the matching book")): return "ephemeral", 600
    if plain.startswith(("Чтение завершено", "Reading finished")): return "ephemeral", 90
    return None, None

async def _patched_message_answer(self, text, *args, **kwargs):
    policy, delay = _bot_message_policy(text)
    if self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP) and policy == "ui":
        previous_id = active_ui_messages.get(self.chat.id)
        if previous_id:
            cancel_message_delete(self.chat.id, previous_id); await safe_delete_message(self.chat.id, previous_id)
    sent = await _ORIGINAL_MESSAGE_ANSWER(self, text, *args, **kwargs)
    if self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        if policy == "ui": active_ui_messages[self.chat.id] = sent.message_id
        elif policy == "ephemeral": schedule_message_delete(sent.chat.id, sent.message_id, delay or 90)
    return sent

async def _patched_message_edit_text(self, text, *args, **kwargs):
    result = await _ORIGINAL_MESSAGE_EDIT_TEXT(self, text, *args, **kwargs); policy, delay = _bot_message_policy(text)
    if self.chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP):
        if policy == "ui":
            cancel_message_delete(self.chat.id, self.message_id)
            previous_id = active_ui_messages.get(self.chat.id)
            if previous_id and previous_id != self.message_id:
                cancel_message_delete(self.chat.id, previous_id); await safe_delete_message(self.chat.id, previous_id)
            active_ui_messages[self.chat.id] = self.message_id
        elif policy == "ephemeral":
            schedule_message_delete(self.chat.id, self.message_id, delay or 90)
            if active_ui_messages.get(self.chat.id) == self.message_id: active_ui_messages.pop(self.chat.id, None)
    return result

class _CommandCleanupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            text = getattr(event, "text", None) or ""; chat = getattr(event, "chat", None)
            if chat and chat.type in (core.ChatType.GROUP, core.ChatType.SUPERGROUP) and text.lstrip().startswith("/"):
                await safe_delete_message(chat.id, event.message_id)

def _report_button(lang: str):
    return core.InlineKeyboardButton(text=core.t("btn_report_error", lang), callback_data="report_error_v2")

def get_welcome_keyboard(lang: str, bot_username: str, is_private: bool = False, chat_id: Optional[int] = None):
    if not is_private and chat_id and chat_id < 0:
        rate_url = f"https://t.me/{bot_username}?start=rate_c{abs(chat_id)}"
        keyboard = [[core.InlineKeyboardButton(text=label(lang,en="📚 Waiting list",ru="📚 Лист ожидания"),callback_data="show_backlog"), core.InlineKeyboardButton(text=label(lang,en="🏆 Hall of Fame",ru="🏆 Зал славы"),callback_data="show_hof")], [core.InlineKeyboardButton(text=core.t("btn_rate_books",lang),url=rate_url)], [core.InlineKeyboardButton(text=label(lang,en="➕ Suggest a book",ru="➕ Предложить книгу"),callback_data="show_suggest_help")], [core.InlineKeyboardButton(text=core.t("btn_how_it_works",lang),callback_data="show_help")], [_report_button(lang)]]
    else:
        rate_url = f"https://t.me/{bot_username}?start=rate_new"; keyboard = [[core.InlineKeyboardButton(text=core.t("btn_rate_books",lang),url=rate_url)], [core.InlineKeyboardButton(text=core.t("btn_how_it_works",lang),callback_data="show_help")], [_report_button(lang)]]
    return core.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_help_keyboard(lang: str, bot_username: str, include_back: bool = True, chat_id: Optional[int] = None):
    rate_url = f"https://t.me/{bot_username}?start=rate_c{abs(chat_id)}" if chat_id and chat_id < 0 else f"https://t.me/{bot_username}?start=rate_new"
    keyboard = [[core.InlineKeyboardButton(text=core.t("btn_rate_books",lang),url=rate_url)], [_report_button(lang)]]
    if include_back: keyboard.append([core.InlineKeyboardButton(text=core.t("btn_back",lang),callback_data="back_to_welcome")])
    return core.InlineKeyboardMarkup(inline_keyboard=keyboard)

def _admin_status(status) -> bool:
    value = getattr(status, "value", status); return str(value).lower() in {"creator", "owner", "administrator"}

async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in core.SUPER_ADMIN_IDS: return True
    if chat_id > 0: return False
    member_error = None
    try:
        member = await core.bot.get_chat_member(chat_id, user_id)
        if _admin_status(getattr(member, "status", None)): return True
    except Exception as exc: member_error = exc
    try:
        for admin_member in await core.bot.get_chat_administrators(chat_id):
            if getattr(getattr(admin_member, "user", None), "id", None) == user_id: return True
    except Exception as exc:
        core.logger.warning(f"Admin check failed for user {user_id} in chat {chat_id}: get_chat_member={member_error}; get_chat_administrators={exc}")
    return False

def _back_markup(lang: str):
    return core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text=core.t("btn_back",lang),callback_data="back_to_welcome")]])

async def _get_suggestor_display(book_id: int) -> str:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        async with db.execute("SELECT u.full_name, u.username FROM books b LEFT JOIN users u ON b.suggested_by = u.internal_id WHERE b.id = ?", (book_id,)) as cursor: row = await cursor.fetchone()
    if not row: return ""
    full_name, username = row
    if full_name: return core.escape_html(full_name)
    if username: return core.escape_html(f"@{username}")
    return ""

async def send_next_unrated_book(user_tg_id: int, target_msg_or_user, lang: str):
    books = await core.database.get_unrated_backlog_books_for_user(core.DATABASE_PATH, user_tg_id)
    if not books:
        text = core.t("all_caught_up_rating", lang)
        if isinstance(target_msg_or_user, core.Message): await target_msg_or_user.answer(text, parse_mode="HTML")
        else: await target_msg_or_user.message.edit_text(text, parse_mode="HTML")
        return
    book = books[0]; row1=[core.InlineKeyboardButton(text=str(i),callback_data=f"rate:{book['id']}:{i}") for i in range(1,6)]; row2=[core.InlineKeyboardButton(text=str(i),callback_data=f"rate:{book['id']}:{i}") for i in range(6,11)]; markup=core.InlineKeyboardMarkup(inline_keyboard=[row1,row2])
    msg_text=core.t("rate_prompt_group",lang,chat_title=core.escape_html(book["chat_title"]),title=core.escape_html(book["title"]),author=core.escape_html(book["author"])); suggestor=await _get_suggestor_display(book["id"])
    if suggestor: msg_text += f"\n\n👤 <b>{label(lang,en='Suggested by',ru='Предложил')}:</b> {suggestor}"
    if isinstance(target_msg_or_user, core.Message): await target_msg_or_user.answer(msg_text,parse_mode="HTML",reply_markup=markup)
    else: await target_msg_or_user.message.edit_text(msg_text,parse_mode="HTML",reply_markup=markup)

async def send_halloffame_response(chat_id: int, lang: str, target_msg_or_cb, user_id: Optional[int] = None):
    data=await core.database.get_hall_of_fame_detailed(core.DATABASE_PATH,chat_id,min_votes=core.MIN_VOTES_FOR_RATING); qualified=data["qualified"]; low_votes=data["low_votes"]
    if user_id is None: user_id=getattr(getattr(target_msg_or_cb,"from_user",None),"id",None)
    markup=None
    if user_id and await is_admin(chat_id,user_id) and (qualified or low_votes): markup=core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text=core.t("btn_hof_delete_book",lang),callback_data="hof_del_menu")]])
    if not qualified and not low_votes:
        text=core.t("hof_empty",lang)
        if isinstance(target_msg_or_cb,core.Message):
            if target_msg_or_cb.chat.type==core.ChatType.PRIVATE: await target_msg_or_cb.answer(text,parse_mode="HTML")
            else: await send_or_replace_group_ui(target_msg_or_cb,text,parse_mode="HTML")
        else: await target_msg_or_cb.message.edit_text(text,parse_mode="HTML",reply_markup=_back_markup(lang))
        return
    async def add(text,idx,item):
        suggestor=await _get_suggestor_display(item["id"]); title=core.escape_html(item["title"]); title=f"{title} ({suggestor})" if suggestor else title
        return text+core.t("hof_item_format",lang,idx=idx,title=title,author=core.escape_html(item["author"]),wr=item["weighted_rating"],count=item["live_votes_count"])+"\n"
    text=core.t("hof_header",lang)
    if qualified:
        for idx,item in enumerate(qualified,1): text=await add(text,idx,item)
    else: text+="—\n"
    if low_votes:
        text+=core.t("hof_low_votes_header",lang,min_votes=core.MIN_VOTES_FOR_RATING)
        for idx,item in enumerate(low_votes,1): text=await add(text,idx,item)
    if isinstance(target_msg_or_cb,core.Message) and target_msg_or_cb.chat.type!=core.ChatType.PRIVATE: await send_or_replace_group_ui(target_msg_or_cb,text,parse_mode="HTML",reply_markup=markup)
    else: await core.send_split_messages(target_msg_or_cb,text,reply_markup=markup)

async def _show_backlog(callback: core.CallbackQuery):
    chat_id=callback.message.chat.id; lang=await core.get_lang(chat_id,callback.from_user.id)
    if callback.message.chat.type==core.ChatType.PRIVATE:
        await callback.answer(label(lang,en="Open this section in your book-club group.",ru="Откройте этот раздел в группе книжного клуба."),show_alert=True); return
    books=await core.database.get_backlog_books_full_info(core.DATABASE_PATH,chat_id); await callback.answer()
    if not books:
        await callback.message.edit_text(core.t("backlog_empty",lang),parse_mode="HTML",reply_markup=_back_markup(lang)); return
    text=core.t("backlog_list_header",lang)
    for idx,b in enumerate(books,1):
        s=core.escape_html(b["suggestor_name"] or (f"@{b['suggestor_username']}" if b["suggestor_username"] else "N/A")); text+=core.t("backlog_item_format",lang,idx=idx,title=core.escape_html(b["title"]),author=core.escape_html(b["author"]),score=round(b["wish_score"],1),suggestor=s)
    info=await core.bot.get_me(); rate_url=f"https://t.me/{info.username}?start=rate_c{abs(chat_id)}"; markup=core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text=core.t("btn_rate_books",lang),url=rate_url)],[core.InlineKeyboardButton(text=core.t("btn_back",lang),callback_data="back_to_welcome")]])
    await core.send_split_messages(callback,text,reply_markup=markup)

async def _show_hof(callback: core.CallbackQuery):
    chat_id=callback.message.chat.id; lang=await core.get_lang(chat_id,callback.from_user.id)
    if callback.message.chat.type==core.ChatType.PRIVATE:
        await callback.answer(label(lang,en="Open this section in your book-club group.",ru="Откройте этот раздел в группе книжного клуба."),show_alert=True); return
    await callback.answer(); await send_halloffame_response(chat_id,lang,callback,callback.from_user.id)

async def _show_suggest_help(callback: core.CallbackQuery):
    lang=await core.get_lang(callback.message.chat.id,callback.from_user.id); await callback.answer(); await callback.message.edit_text(core.t("suggest_usage",lang),parse_mode="HTML",reply_markup=_back_markup(lang))

def install():
    global _installed
    if _installed: return
    _installed=True
    core.Message.answer=_patched_message_answer; core.Message.edit_text=_patched_message_edit_text
    core.safe_delete_message=safe_delete_message; core.schedule_message_delete=schedule_message_delete; core.cancel_message_delete=cancel_message_delete; core.cleanup_command_message=cleanup_command_message; core.send_or_replace_group_ui=send_or_replace_group_ui; core.send_ephemeral_reply=send_ephemeral_reply
    core.get_welcome_keyboard=get_welcome_keyboard; core.get_help_keyboard=get_help_keyboard; core.is_admin=is_admin; core.send_next_unrated_book=send_next_unrated_book; core.send_halloffame_response=send_halloffame_response
    core.router.message.outer_middleware(_CommandCleanupMiddleware())
    core.router.callback_query(core.F.data=="show_backlog")(_show_backlog); core.router.callback_query(core.F.data=="show_hof")(_show_hof); core.router.callback_query(core.F.data=="show_suggest_help")(_show_suggest_help)
