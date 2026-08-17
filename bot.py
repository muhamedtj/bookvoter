import os
import sys
import json
import logging
import asyncio
import traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, Poll, PollAnswer, ErrorEvent
)
from aiogram.enums import ChatType
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from i18n import t

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bookvoter")

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bookvoter.db")
MIN_VOTES_FOR_RATING = 3

SUPER_ADMIN_IDS: List[int] = []
super_admin_env = os.getenv("SUPER_ADMIN_IDS", "")
if super_admin_env:
    for s_id in super_admin_env.split(","):
        s_id_str = s_id.strip()
        if s_id_str.isdigit():
            SUPER_ADMIN_IDS.append(int(s_id_str))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is not set in environment variables.")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
router = Router()
dp.include_router(router)


# --- Error Notification Helper ---

async def notify_superadmin_error(error_title: str, error_traceback: str, user_id: Optional[int] = None, chat_id: Optional[int] = None, context_info: str = ""):
    """Send formatted error diagnostic report to superadmin in PM with fallback logging."""
    report = (
        f"🚨 **Critical Error Report**\n\n"
        f"📌 **Title:** {error_title}\n"
        f"👤 **User ID:** {user_id or 'N/A'}\n"
        f"💬 **Chat ID:** {chat_id or 'N/A'}\n"
        f"⏰ **Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📝 **Context:** {context_info or 'N/A'}\n\n"
        f"📋 **Traceback:**\n```\n{error_traceback[-1500:]}\n```"
    )

    if not SUPER_ADMIN_IDS:
        logger.error(f"[Superadmin Notification Skipped] {report}")
        return

    for admin_id in SUPER_ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send error report to superadmin {admin_id}: {e}\nFull Report:\n{report}")


# Global Aiogram Error Middleware / Handler
@dp.errors()
async def global_error_handler(event: ErrorEvent):
    logger.error(f"Unhandled exception in handler: {event.exception}", exc_info=event.exception)
    tb_str = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))

    user_id = None
    chat_id = None
    context_str = "Unhandled Exception"

    if event.update.message:
        user_id = event.update.message.from_user.id if event.update.message.from_user else None
        chat_id = event.update.message.chat.id
        context_str = f"Command/Text: {event.update.message.text}"
    elif event.update.callback_query:
        user_id = event.update.callback_query.from_user.id
        chat_id = event.update.callback_query.message.chat.id if event.update.callback_query.message else None
        context_str = f"Callback Data: {event.update.callback_query.data}"

    await notify_superadmin_error(
        error_title=str(type(event.exception).__name__),
        error_traceback=tb_str,
        user_id=user_id,
        chat_id=chat_id,
        context_info=context_str
    )


# Helper: Check if user is Admin in Chat or Superadmin
async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in SUPER_ADMIN_IDS:
        return True
    if chat_id > 0:  # Private chat is not a group control context
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception as e:
        logger.warning(f"Failed to check admin status for user {user_id} in chat {chat_id}: {e}")
        return False


# Onboarding middleware / helper
async def register_user_and_chat(message: Message):
    if message.from_user:
        await database.get_or_create_user(
            DATABASE_PATH,
            tg_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
    if message.chat:
        chat_title = message.chat.title if message.chat.title else message.chat.full_name
        await database.register_or_update_chat(
            DATABASE_PATH,
            chat_id=message.chat.id,
            title=chat_title,
            status="active"
        )


# Helper: Get effective language
async def get_lang(chat_id: int, user_id: Optional[int] = None) -> str:
    return await database.get_effective_language(DATABASE_PATH, chat_id, user_id)


# UI Helper: Common Welcome Keyboard
def get_welcome_keyboard(lang: str, bot_username: str, is_private: bool = False) -> InlineKeyboardMarkup:
    rate_url = f"https://t.me/{bot_username}?start=rate_new"
    keyboard = [
        [InlineKeyboardButton(text=t("btn_how_it_works", lang), callback_data="show_help")],
        [InlineKeyboardButton(text=t("btn_rate_books", lang), url=rate_url)]
    ]
    if not is_private:
        keyboard.append([InlineKeyboardButton(text=t("btn_open_control_panel", lang), callback_data="open_control_panel")])
    keyboard.append([InlineKeyboardButton(text=t("btn_report_error", lang), callback_data="report_error")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Helper: Help Screen Keyboard
def get_help_keyboard(lang: str, bot_username: str, include_back: bool = True) -> InlineKeyboardMarkup:
    rate_url = f"https://t.me/{bot_username}?start=rate_new"
    keyboard = [
        [InlineKeyboardButton(text=t("btn_rate_books", lang), url=rate_url)],
        [InlineKeyboardButton(text=t("btn_report_error", lang), callback_data="report_error")]
    ]
    if include_back:
        keyboard.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_welcome")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Language Selection Keyboard
def get_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru")
        ]
    ])


# Userbot search invoker
async def fetch_books_via_userbot(query: str, lang: str = "en") -> Optional[List[Dict[str, str]]]:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "downloader.py",
            "search",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            output_str = stdout.decode("utf-8").strip()
            for line in reversed(output_str.splitlines()):
                line_str = line.strip()
                if line_str.startswith("["):
                    try:
                        return json.loads(line_str)
                    except json.JSONDecodeError:
                        pass

        logger.warning(f"Userbot search returned non-zero code or failed: {stderr.decode()}")
        return [{
            "title": query.title(),
            "author": "Library Bot",
            "genre": None,
            "raw_label": query.title()
        }]
    except Exception as e:
        logger.error(f"Subprocess search execution error: {e}")
        return None


# In-memory poll vote counter for tracking non-anonymous PollAnswer votes per option
poll_votes_tracker: Dict[str, Dict[int, int]] = {}

# Process-local locks to prevent parallel downloading of the same book in the same chat
download_locks: Dict[Tuple[int, int], asyncio.Lock] = {}

# Process-local locks to prevent concurrent duplicate execution of finish_vote_process per chat
finish_vote_locks: Dict[int, asyncio.Lock] = {}

def get_download_lock(chat_id: int, book_id: int) -> asyncio.Lock:
    key = (chat_id, book_id)
    if key not in download_locks:
        download_locks[key] = asyncio.Lock()
    return download_locks[key]

# Chat-level locks for finishing vote to avoid race conditions
finish_vote_locks: Dict[int, asyncio.Lock] = {}

def get_finish_vote_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in finish_vote_locks:
        finish_vote_locks[chat_id] = asyncio.Lock()
    return finish_vote_locks[chat_id]


# --- Admin Panel View Helper ---

async def get_admin_panel_view(chat_id: int, lang: str, db_path: str = DATABASE_PATH) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Determine current club state and return formatted state message + inline keyboard.
    State 1: Ready for vote (no active poll & no book being read) -> Start voting
    State 2: Voting active (active poll exists) -> Finish voting early
    State 3: Book selected & reading (book status 'won') -> Finish reading
    """
    active_poll = await database.get_active_poll(db_path, chat_id)
    current_book = await database.get_current_winning_or_reading_book(db_path, chat_id)

    keyboard = []

    if active_poll:
        # State 2: Vote in progress
        text = t("admin_state_voting", lang)
        keyboard.append([InlineKeyboardButton(text=t("btn_finish_vote_early", lang), callback_data="admin_finish_vote_early")])
    elif current_book:
        # State 3: Reading in progress
        text = t("admin_state_reading", lang, title=current_book["title"], author=current_book["author"])
        keyboard.append([InlineKeyboardButton(text=t("btn_finish_reading", lang), callback_data="admin_finish_reading")])
    else:
        # State 1: Ready for new vote
        text = t("admin_state_ready", lang)
        keyboard.append([InlineKeyboardButton(text=t("btn_start_vote", lang), callback_data="admin_start_vote")])

    # Common secondary management buttons
    keyboard.extend([
        [InlineKeyboardButton(text=t("btn_group_stats", lang), callback_data="admin_group_stats")],
        [InlineKeyboardButton(text=t("btn_audit_backlog", lang), callback_data="admin_audit_backlog")],
        [InlineKeyboardButton(text=t("btn_delete_book", lang), callback_data="admin_delete_book")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_welcome")]
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


# --- Handlers ---

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else chat_id
    is_private = message.chat.type == ChatType.PRIVATE

    current_lang = await database.get_user_language(DATABASE_PATH, user_id) if is_private else await database.get_chat_language(DATABASE_PATH, chat_id)

    if not current_lang:
        await message.answer(
            t("select_language_prompt", "en"),
            parse_mode="Markdown",
            reply_markup=get_language_keyboard()
        )
        return

    lang = current_lang
    args = command.args

    if args == "rate_new" and is_private:
        await send_next_unrated_book(message.from_user.id, message, lang)
        return

    bot_info = await bot.get_me()
    welcome_markup = get_welcome_keyboard(lang, bot_info.username, is_private=is_private)
    await message.answer(t("welcome_msg", lang), parse_mode="Markdown", reply_markup=welcome_markup)


@router.message(Command("help"))
async def handle_help_command(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    bot_info = await bot.get_me()
    help_markup = get_help_keyboard(lang, bot_info.username, include_back=False)
    await message.answer(t("help_msg", lang), parse_mode="Markdown", reply_markup=help_markup)


@router.callback_query(F.data == "show_help")
async def handle_show_help_cb(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    bot_info = await bot.get_me()
    help_markup = get_help_keyboard(lang, bot_info.username, include_back=True)
    await callback.answer()
    await callback.message.edit_text(t("help_msg", lang), parse_mode="Markdown", reply_markup=help_markup)


@router.callback_query(F.data == "back_to_welcome")
async def handle_back_to_welcome_cb(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    is_private = callback.message.chat.type == ChatType.PRIVATE
    bot_info = await bot.get_me()
    welcome_markup = get_welcome_keyboard(lang, bot_info.username, is_private=is_private)
    await callback.answer()
    await callback.message.edit_text(t("welcome_msg", lang), parse_mode="Markdown", reply_markup=welcome_markup)


@router.callback_query(F.data == "report_error")
async def handle_report_error_cb(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    u_name = callback.from_user.full_name or callback.from_user.username or "User"

    context_str = f"User {u_name} ({user_id}) manually submitted an error report from chat {chat_id}."

    await notify_superadmin_error(
        error_title="User Error Report 🐛",
        error_traceback="No traceback (User reported issue via button)",
        user_id=user_id,
        chat_id=chat_id,
        context_info=context_str
    )

    await callback.answer(t("error_reported_thanks", lang), show_alert=True)


@router.message(Command("backlog"))
async def handle_backlog_command(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    books = await database.get_backlog_books_full_info(DATABASE_PATH, message.chat.id)

    if not books:
        await message.answer(t("backlog_empty", lang), parse_mode="Markdown")
        return

    text = t("backlog_list_header", lang)
    for idx, b in enumerate(books, 1):
        s_name = b["suggestor_name"] or (f"@{b['suggestor_username']}" if b["suggestor_username"] else "N/A")
        score_formatted = round(b['wish_score'], 1)
        text += t("backlog_item_format", lang, idx=idx, title=b["title"], author=b["author"], score=score_formatted, suggestor=s_name)

    bot_info = await bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_new"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rate_books", lang), url=rate_url)]
    ])

    await message.answer(text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "open_control_panel")
async def handle_open_control_panel_cb(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)

    if callback.message.chat.type == ChatType.PRIVATE:
        await callback.answer(t("control_panel_dm_notice", lang), show_alert=True)
        return

    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    await callback.answer()
    panel_text, markup = await get_admin_panel_view(chat_id, lang)
    await callback.message.edit_text(panel_text, parse_mode="Markdown", reply_markup=markup)


@router.message(Command("language"))
async def handle_language_command(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    await message.answer(
        t("select_language_prompt", lang),
        parse_mode="Markdown",
        reply_markup=get_language_keyboard()
    )


@router.callback_query(F.data.startswith("set_lang:"))
async def handle_set_language_callback(callback: CallbackQuery):
    await register_user_and_chat(callback.message)
    lang_code = callback.data.split(":")[1]
    chat_id = callback.message.chat.id
    is_private = callback.message.chat.type == ChatType.PRIVATE

    if is_private:
        await database.set_user_language(DATABASE_PATH, callback.from_user.id, lang_code)
    else:
        if not await is_admin(chat_id, callback.from_user.id):
            await callback.answer(t("only_admins_allowed", lang_code), show_alert=True)
            return
        await database.set_chat_language(DATABASE_PATH, chat_id, lang_code)

    await callback.answer(t("language_selected", lang_code))

    bot_info = await bot.get_me()
    welcome_markup = get_welcome_keyboard(lang_code, bot_info.username, is_private=is_private)
    await callback.message.edit_text(
        t("language_selected", lang_code) + "\n\n" + t("welcome_msg", lang_code),
        parse_mode="Markdown",
        reply_markup=welcome_markup
    )


@router.message(Command("suggest"))
async def handle_suggest(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    query = command.args

    if not query:
        await message.answer(t("suggest_usage", lang), parse_mode="Markdown")
        return

    status_msg = await message.answer(t("searching_google_books", lang))
    books = await fetch_books_via_userbot(query, lang)

    if books is None:
        bot_info = await bot.get_me()
        err_markup = get_welcome_keyboard(lang, bot_info.username, is_private=(message.chat.type == ChatType.PRIVATE))
        await status_msg.edit_text(t("google_books_api_error", lang), reply_markup=err_markup)
        return

    if not books:
        await status_msg.edit_text(t("no_valid_books_found", lang))
        return

    temp_key = f"sug_{message.chat.id}_{message.from_user.id}_{int(datetime.now().timestamp())}"
    pending_suggestions[temp_key] = books

    inline_keyboard = []
    for idx, b in enumerate(books):
        title_part = b['title'][:25] + "…" if len(b['title']) > 25 else b['title']
        author_val = b.get('author', '')
        if author_val and author_val.lower() not in ['unknown author', 'n/a', '']:
            author_part = author_val[:12] + "…" if len(author_val) > 12 else author_val
            btn_text = f"📖 {title_part} — {author_part}"
        else:
            btn_text = f"📖 {title_part}"
        inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"sel_sug:{temp_key}:{idx}")])

    markup = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await status_msg.edit_text(t("select_matching_book", lang), reply_markup=markup)


# Pending suggestions dictionary
pending_suggestions: Dict[str, List[Dict[str, str]]] = {}


@router.callback_query(F.data.startswith("sel_sug:"))
async def handle_suggestion_select(callback: CallbackQuery):
    await register_user_and_chat(callback.message)
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(t("selection_expired", lang))
        return

    temp_key, idx_str = parts[1], parts[2]
    idx = int(idx_str)

    books_list = pending_suggestions.get(temp_key)
    if not books_list or idx >= len(books_list):
        await callback.answer(t("selection_expired", lang))
        return

    selected_book = books_list[idx]
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    already_exists = await database.is_book_exists(
        DATABASE_PATH,
        chat_id=chat_id,
        title=selected_book["title"],
        author=selected_book["author"]
    )
    if already_exists:
        await callback.answer(t("book_already_exists", lang), show_alert=True)
        await callback.message.edit_text(t("book_already_exists", lang), parse_mode="Markdown")
        return

    book_id = await database.add_book(
        DATABASE_PATH,
        chat_id=chat_id,
        title=selected_book["title"],
        author=selected_book["author"],
        genre=None,
        suggested_by_tg_id=user_id,
        file_id=selected_book.get("download_cmd") or None
    )

    pending_suggestions.pop(temp_key, None)

    bot_info = await bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_new"
    rate_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rate_books", lang), url=rate_url)]
    ])

    alert_full_info = f"✅ «{selected_book['title']}» ({selected_book['author']})"
    await callback.answer(alert_full_info[:200], show_alert=True)
    text = (
        t("book_added_confirmation_exact", lang, title=selected_book["title"], author=selected_book["author"]) + "\n\n" +
        t("click_below_to_rate", lang)
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=rate_markup)

 feat/bookvoter-telegram-bot-1878892981235505973
# Anti-Spam Rating in Private Messages
async def send_next_unrated_book(user_tg_id: int, target_msg_or_user: Any, lang: str):
    unrated_books = await database.get_unrated_backlog_books_for_user(DATABASE_PATH, user_tg_id)
    if not unrated_books:
        text = t("all_caught_up_rating", lang)
        if isinstance(target_msg_or_user, Message):
            await target_msg_or_user.answer(text, parse_mode="Markdown")
        elif isinstance(target_msg_or_user, CallbackQuery):
            await target_msg_or_user.message.edit_text(text, parse_mode="Markdown")
        return

    book = unrated_books[0]
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(6, 11)]
    markup = InlineKeyboardMarkup(inline_keyboard=[row1, row2])

    msg_text = t(
        "rate_prompt_group",
        lang,
        chat_title=book['chat_title'],
        title=book['title'],
        author=book['author']
    )

    if isinstance(target_msg_or_user, Message):
        await target_msg_or_user.answer(msg_text, parse_mode="Markdown", reply_markup=markup)
    elif isinstance(target_msg_or_user, CallbackQuery):
        await target_msg_or_user.message.edit_text(msg_text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data.startswith("rate:"))
async def handle_rate_callback(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    book_id, score = int(parts[1]), int(parts[2])
    await database.save_backlog_rating(DATABASE_PATH, callback.from_user.id, book_id, score)
    await callback.answer(t("saved_score_cb", lang, score=score))

    await send_next_unrated_book(callback.from_user.id, callback, lang)


# --- Real-time Poll & PollAnswer Monitoring (> 50% majority auto-closure) ---

@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer):
    active_poll = await database.get_active_poll_by_poll_id(DATABASE_PATH, poll_answer.poll_id)
    if not active_poll:
        return

    poll_id = poll_answer.poll_id
    chat_id = active_poll["chat_id"]

    if poll_id not in poll_votes_tracker:
        poll_votes_tracker[poll_id] = {}

    user_votes = poll_votes_tracker[poll_id]
    if poll_answer.option_ids:
        user_votes[poll_answer.user.id] = poll_answer.option_ids[0]
    else:
        user_votes.pop(poll_answer.user.id, None)

    total_votes = len(user_votes)
    if total_votes > 0:
        counts: Dict[int, int] = {}
        for opt_id in user_votes.values():
            counts[opt_id] = counts.get(opt_id, 0) + 1

        for opt_id, count in counts.items():
            if count / total_votes > 0.5:
                logger.info(f"Poll option achieved majority (>50%) in chat {chat_id}. Finishing vote automatically...")
                poll_votes_tracker.pop(poll_id, None)
                job_id = f"poll_end_{chat_id}_{active_poll['message_id']}"
                try:
                    if scheduler.get_job(job_id):
                        scheduler.remove_job(job_id)
                except Exception as e:
                    logger.warning(f"Failed to remove scheduled job {job_id}: {e}")

                await finish_vote_process(chat_id)
                break


@router.poll()
async def handle_poll_update(poll: Poll):
    if poll.is_closed:
        return

    active_poll = await database.get_active_poll_by_poll_id(DATABASE_PATH, poll.id)
    if not active_poll:
        return

    chat_id = active_poll["chat_id"]
    total_votes = poll.total_voter_count

    if total_votes == 0:
        return

    for option in poll.options:
        if option.voter_count / total_votes > 0.5:
            logger.info(f"Poll option achieved majority (>50%) in chat {chat_id}. Finishing vote automatically...")
            poll_votes_tracker.pop(poll.id, None)
            job_id = f"poll_end_{chat_id}_{active_poll['message_id']}"
            try:
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)
            except Exception as e:
                logger.warning(f"Failed to remove scheduled job {job_id}: {e}")

            await finish_vote_process(chat_id)
            break


# --- Admin Panel & Control Methods ---

@router.message(Command("bookvoter"))
@router.message(Command("admin"))
async def handle_admin(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)

    if message.chat.type == ChatType.PRIVATE:
        await message.answer(t("control_panel_dm_notice", lang), parse_mode="Markdown")
        return

    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return

    panel_text, markup = await get_admin_panel_view(message.chat.id, lang)
    await message.answer(panel_text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "admin_start_vote")
async def handle_admin_start_vote_menu(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    active_reading = await database.get_current_winning_or_reading_book(DATABASE_PATH, chat_id)
    if active_reading:
        await callback.answer(t("active_reading_in_progress_err", lang, title=active_reading["title"]), show_alert=True)
        return

    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if active_poll:
        await callback.answer(t("vote_in_progress_err", lang), show_alert=True)
        return

    current_book = await database.get_current_winning_or_reading_book(DATABASE_PATH, chat_id)
    if current_book:
        await callback.answer(t("vote_in_progress_err", lang), show_alert=True)
        return

    top_books = await database.get_top_backlog_books_for_vote(DATABASE_PATH, chat_id, limit=3)
    if not top_books:
        await callback.answer(t("no_backlog_books_err", lang), show_alert=True)
        return

    await callback.answer()
    await launch_poll_for_books(chat_id, top_books, lang)
    await callback.message.edit_text(
        t("vote_started_msg", lang),
        parse_mode="Markdown"
    )


async def launch_poll_for_books(chat_id: int, top_books: List[Dict[str, Any]], lang: str):
    book_ids = [b["id"] for b in top_books]
    await database.update_books_status(DATABASE_PATH, book_ids, "voting")

    options = [f"{b['title']} — {b['author']}"[:100] for b in top_books]
    options_mapping = {idx: b["id"] for idx, b in enumerate(top_books)}

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question=t("poll_question", lang),
        options=options,
        is_anonymous=False,
        allows_multiple_answers=False
    )

    await database.save_active_poll(
        DATABASE_PATH,
        chat_id=chat_id,
        poll_id=poll_msg.poll.id,
        message_id=poll_msg.message_id,
        options_json=json.dumps(options_mapping)
    )

    job_id = f"poll_end_{chat_id}_{poll_msg.message_id}"
    run_time = datetime.now() + timedelta(hours=24)
    scheduler.add_job(
        finish_vote_process,
        "date",
        run_date=run_time,
        args=[chat_id],
        id=job_id,
        replace_existing=True
    )


@router.callback_query(F.data == "admin_audit_backlog")
async def handle_admin_audit_backlog(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)

    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    active_tg_ids = []
    books = await database.get_backlog_books_full_info(DATABASE_PATH, chat_id)
    for b in books:
        if b.get("suggestor_tg_id"):
            try:
                member = await bot.get_chat_member(chat_id, b["suggestor_tg_id"])
                if member.status not in ("left", "kicked"):
                    active_tg_ids.append(b["suggestor_tg_id"])
            except Exception:
                pass

    audit_items = await database.audit_backlog_activity(DATABASE_PATH, chat_id, active_tg_ids)

    if not audit_items:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin_main_menu")]
        ])
        await callback.answer()
        await callback.message.edit_text(t("audit_clean", lang), parse_mode="Markdown", reply_markup=markup)
        return

    text = t("audit_title", lang)
    for item in audit_items:
        s_name = item["suggestor_name"] or (f"@{item['suggestor_username']}" if item["suggestor_username"] else "User")
        reasons_str = ", ".join([t(f"reason_{r}", lang) for r in item["reasons"]])
        text += f"• **{item['title']}** — {item['author']}\n  👤 Suggested by: {s_name}\n  ⚠️ {reasons_str}\n\n"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_delete_book", lang), callback_data="admin_delete_book")],
        [InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin_main_menu")]
    ])

    await callback.answer()
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


# --- Local Chat Statistics with Filters ---

@router.message(Command("chat_stats"))
async def handle_chat_stats_cmd(message: Message):
    await register_user_and_chat(message)
    chat_id = message.chat.id
    lang = await get_lang(chat_id, message.from_user.id if message.from_user else None)
    await send_chat_stats_response(chat_id, lang, days=None, target_msg_or_cb=message)


@router.callback_query(F.data == "admin_group_stats")
@router.callback_query(F.data.startswith("stats_chat:"))
async def handle_chat_stats_cb(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)

    days = None
    if callback.data.startswith("stats_chat:"):
        period = callback.data.split(":")[1]
        if period == "30":
            days = 30

    await callback.answer()
    await send_chat_stats_response(chat_id, lang, days=days, target_msg_or_cb=callback)


async def send_chat_stats_response(chat_id: int, lang: str, days: Optional[int], target_msg_or_cb: Any):
    stats = await database.get_chat_stats_detailed(DATABASE_PATH, chat_id, days=days)
    filter_label = t("filter_30_days", lang) if days == 30 else t("filter_all_time", lang)

    contributors_str = "\n".join([f"• {c['name']}: {c['count']} books" for c in stats["top_contributors"]]) or "N/A"
    voters_str = "\n".join([f"• {v['name']}: {v['count']} votes" for v in stats["top_voters"]]) or "N/A"

    text = t(
        "group_stats_text",
        lang,
        filter_label=filter_label,
        backlog_count=stats["backlog_count"],
        done_count=stats["done_count"],
        avg_club_rating=stats["avg_club_rating"],
        contributors_str=contributors_str,
        voters_str=voters_str
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{'✅ ' if not days else ''}{t('filter_all_time', lang)}", callback_data="stats_chat:all"),
            InlineKeyboardButton(text=f"{'✅ ' if days == 30 else ''}{t('filter_30_days', lang)}", callback_data="stats_chat:30")
        ],
        [InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin_main_menu")]
    ])

    if isinstance(target_msg_or_cb, Message):
        await target_msg_or_cb.answer(text, parse_mode="Markdown", reply_markup=markup)
    elif isinstance(target_msg_or_cb, CallbackQuery):
        await target_msg_or_cb.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


# --- Global Superadmin Dashboard (/superadmin) ---

@router.message(Command("superadmin"))
@router.message(Command("sys_stats"))
async def handle_superadmin_cmd(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return

    await send_superadmin_response(lang, days=None, target_msg_or_cb=message)


@router.callback_query(F.data.startswith("stats_super:"))
async def handle_superadmin_cb(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if callback.from_user.id not in SUPER_ADMIN_IDS:
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    period = callback.data.split(":")[1]
    days = 30 if period == "30" else None

    await callback.answer()
    await send_superadmin_response(lang, days=days, target_msg_or_cb=callback)


async def send_superadmin_response(lang: str, days: Optional[int], target_msg_or_cb: Any):
    stats = await database.get_superadmin_stats_detailed(DATABASE_PATH, days=days)
    filter_label = t("filter_30_days", lang) if days == 30 else t("filter_all_time", lang)

    top_books_str = "\n".join([f"• {b['title']} ({b['author']}) — ⭐ {b['score']}/10" for b in stats["top_books"]]) or "N/A"

    text = t(
        "sys_stats_text",
        lang,
        filter_label=filter_label,
        total_active_chats=stats["total_active_chats"],
        total_voters=stats["total_voters"],
        total_books_suggested=stats["total_books_suggested"],
        top_books_str=top_books_str
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{'✅ ' if not days else ''}{t('filter_all_time', lang)}", callback_data="stats_super:all"),
            InlineKeyboardButton(text=f"{'✅ ' if days == 30 else ''}{t('filter_30_days', lang)}", callback_data="stats_super:30")
        ]
    ])

    if isinstance(target_msg_or_cb, Message):
        await target_msg_or_cb.answer(text, parse_mode="Markdown", reply_markup=markup)
    elif isinstance(target_msg_or_cb, CallbackQuery):
        await target_msg_or_cb.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


# Administrative Early Finish / Delete Callbacks

@router.callback_query(F.data == "admin_finish_vote_early")
async def handle_admin_finish_vote_early(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    chat_id = callback.message.chat.id
    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if not active_poll:
        await callback.answer(t("no_active_vote_err", lang), show_alert=True)
        return

    job_id = f"poll_end_{chat_id}_{active_poll['message_id']}"
    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    except Exception as e:
        logger.warning(f"Failed to remove scheduled job {job_id}: {e}")

    await callback.answer()
    await finish_vote_process(chat_id)


@router.callback_query(F.data == "admin_delete_book")
async def handle_admin_delete_book_menu(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)

    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    books = await database.get_backlog_books_for_chat(DATABASE_PATH, chat_id)
    if not books:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin_main_menu")]
        ])
        await callback.answer()
        await callback.message.edit_text(t("no_books_to_delete", lang), parse_mode="Markdown", reply_markup=markup)
        return

    keyboard = []
    for b in books[:10]:
        btn_text = f"❌ {b['title'][:25]} — {b['author'][:15]}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_book:{b['id']}")])

    keyboard.append([InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin_main_menu")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.answer()
    await callback.message.edit_text(t("select_book_to_delete", lang), parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data.startswith("del_book:"))
async def handle_del_book_action(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)

    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    book_id = int(callback.data.split(":")[1])
    book = await database.get_book_by_id(DATABASE_PATH, book_id)
    book_title = book["title"] if book else f"#{book_id}"

    deleted = await database.delete_book(DATABASE_PATH, book_id, chat_id)
    if deleted:
        await callback.answer(t("book_deleted_success", lang, title=book_title), show_alert=True)
    else:
        await callback.answer("Error deleting book.", show_alert=True)

    panel_text, markup = await get_admin_panel_view(chat_id, lang)
    await callback.message.edit_text(panel_text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "admin_main_menu")
async def handle_admin_main_menu(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    await callback.answer()
    panel_text, markup = await get_admin_panel_view(callback.message.chat.id, lang)
    await callback.message.edit_text(panel_text, parse_mode="Markdown", reply_markup=markup)


@router.message(Command("vote"))
async def handle_vote_cmd(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)

    if message.chat.type == ChatType.PRIVATE:
        await message.answer(t("control_panel_dm_notice", lang))
        return

    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return

active_poll = await database.get_active_poll(
    DATABASE_PATH,
    message.chat.id
)
if active_poll:
    await message.answer(
        t("vote_in_progress_err", lang),
        parse_mode="Markdown"
    )
    return

current_book = await database.get_current_winning_or_reading_book(
    DATABASE_PATH,
    message.chat.id
)
if current_book:
    await message.answer(
        t(
            "active_reading_in_progress_err",
            lang,
            title=current_book["title"]
        ),
        parse_mode="Markdown"
    )
    return
        return

    top_books = await database.get_top_backlog_books_for_vote(DATABASE_PATH, message.chat.id, limit=3)
    if not top_books:
        await message.answer(t("no_backlog_books_err", lang))
        return

    await launch_poll_for_books(message.chat.id, top_books, lang)
    await message.answer(t("vote_started_msg", lang), parse_mode="Markdown")


@router.message(Command("finish_vote"))
async def handle_finish_vote_cmd(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)

    if message.chat.type == ChatType.PRIVATE:
        await message.answer(t("control_panel_dm_notice", lang))
        return

    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return

    await finish_vote_process(message.chat.id)


async def finish_vote_process(chat_id: int):
    lock = get_finish_vote_lock(chat_id)
    if lock.locked():
        logger.info(f"finish_vote_process already running for chat {chat_id}. Skipping duplicate execution.")
        return

    async with lock:
        lang = await database.get_effective_language(DATABASE_PATH, chat_id)
        active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
        if not active_poll:
            await bot.send_message(chat_id, t("no_active_vote_err", lang))
            return
poll_message_id = active_poll["message_id"]
options_mapping: Dict[int, int] = {
    int(k): v
    for k, v in json.loads(active_poll["options_json"]).items()
}
voting_book_ids = list(options_mapping.values())

total_voters = 0
winning_book_id = None
canceled_due_to_zero_votes = False

try:
    try:
        stopped_poll: Poll = await bot.stop_poll(
            chat_id,
            poll_message_id
        )
        total_voters = stopped_poll.total_voter_count

        if total_voters == 0:
            canceled_due_to_zero_votes = True
        else:
            max_votes = -1
            candidate_indices = []

            for idx, option in enumerate(stopped_poll.options):
                if option.voter_count > max_votes:
                    max_votes = option.voter_count
                    candidate_indices = [idx]
                elif option.voter_count == max_votes:
                    candidate_indices.append(idx)

            if len(candidate_indices) == 1:
                winning_book_id = options_mapping.get(
                    candidate_indices[0]
                )
            else:
                # Tie-breaker:
                # choose tied book with highest backlog interest score
                tied_book_ids = [
                    options_mapping[idx]
                    for idx in candidate_indices
                    if idx in options_mapping
                ]

                full_info = await database.get_backlog_books_full_info(
                    DATABASE_PATH,
                    chat_id
                )

                scores_by_id = {
                    book["id"]: book.get("wish_score", 0.0)
                    for book in full_info
                }

                winning_book_id = max(
                    tied_book_ids,
                    key=lambda book_id: (
                        scores_by_id.get(book_id, 0.0),
                        -book_id
                    )
                )

    except Exception as e:
        logger.error(
            f"Error stopping poll in chat {chat_id}: {e}"
        )

        # Preserve existing fallback behavior for now
        winning_book_id = (
            voting_book_ids[0]
            if voting_book_ids
            else None
        )

    # No one voted: cancel vote and return books to backlog
    if canceled_due_to_zero_votes or not winning_book_id:
        await database.update_books_status(
            DATABASE_PATH,
            voting_book_ids,
            "backlog"
        )

        await bot.send_message(
            chat_id,
            t("vote_canceled_no_votes", lang),
            parse_mode="Markdown"
        )
        return

    # Resolve winner before starting downloader
    await database.resolve_vote_winner(
        DATABASE_PATH,
        chat_id,
        winning_book_id,
        voting_book_ids
    )

    winning_book = await database.get_book_by_id(
        DATABASE_PATH,
        winning_book_id
    )

    if not winning_book:
        await bot.send_message(
            chat_id,
            "Error retrieving winning book details."
        )
        return

    win_title = winning_book["title"]
    win_author = winning_book["author"]
    win_file_id = winning_book.get("file_id")

    await bot.send_message(
        chat_id,
        t(
            "voting_ended_title",
            lang,
            title=win_title,
            author=win_author
        ),
        parse_mode="Markdown"
    )

    # Download/send in background so Telegram update handler
    # is not blocked for 30+ seconds
    asyncio.create_task(
        execute_downloader_and_send(
            chat_id,
            winning_book_id,
            win_title,
            win_author,
            win_file_id,
            lang
        )
    )

finally:
    await database.clear_active_poll(
        DATABASE_PATH,
        chat_id
    )


async def execute_downloader_and_send(
    chat_id: int,
    book_id: int,
    book_title: str,
    book_author: str,
    saved_file_id: Optional[str],
    lang: str
):
    lock = get_download_lock(chat_id, book_id)
    if lock.locked():
        logger.info(f"Download already in progress for chat {chat_id}, book {book_id}. Skipping duplicate request.")
        return

    async with lock:
        invalid_authors = {"unknown author", "library bot", "n/a", "none", ""}
        clean_author = book_author.strip() if book_author else ""
        if clean_author.lower() in invalid_authors:
            clean_author = ""

        # Priority 1: Saved direct download command starting with /
        if saved_file_id and saved_file_id.strip().startswith("/"):
            queries = [saved_file_id.strip()]
        else:
            # Priority 2: Title + Author (if valid)
            if clean_author:
                queries = [f"{book_title.strip()} {clean_author}".strip(), book_title.strip()]
            else:
                # Priority 3: Title only
                queries = [book_title.strip()]

        success = False
        last_error = ""

        for query_str in queries:
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "downloader.py",
                    "download",
                    query_str,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0:
                    output_lines = stdout.decode().strip().splitlines()
                    file_path = output_lines[-1] if output_lines else ""
                    if os.path.exists(file_path):
                        document = FSInputFile(file_path)
                        msg = await bot.send_document(
                            chat_id=chat_id,
                            document=document,
                            caption=t("book_file_caption", lang, title=book_title),
                            parse_mode="Markdown"
                        )
                        file_id = msg.document.file_id if msg.document else ""
                        await database.mark_book_done(DATABASE_PATH, book_id, file_id)

                        try:
                            os.remove(file_path)
                        except Exception as ex:
                            logger.warning(f"Could not remove local file {file_path}: {ex}")

                        success = True
                        break
                    else:
                        last_error = "Downloaded file path not found locally"
                else:
                    last_error = stderr.decode().strip() or "Downloader process returned non-zero code"

            except Exception as e:
                last_error = str(e)
                logger.error(f"Subprocess execution error for query '{query_str}': {e}")

        if not success:
            logger.error(f"Downloader failed for '{book_title}': {last_error}")
            await bot.send_message(chat_id, t("file_not_found_in_lib", lang, title=book_title), parse_mode="Markdown")

            # Report diagnostic error to superadmin without cancelling winner status
            await notify_superadmin_error(
                error_title="Book File Download Failed",
                error_traceback=f"Last Error: {last_error}",
                user_id=None,
                chat_id=chat_id,
                context_info=f"Book ID: {book_id} | Title: '{book_title}' | Author: '{book_author}' | Queries Tried: {queries}"
            )


# --- Hall of Fame & Post-Reading Rating Handlers ---

@router.message(Command("halloffame"))
@router.message(Command("hof"))
async def handle_halloffame_command(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    await send_halloffame_response(message.chat.id, lang, target_msg_or_cb=message)


async def send_halloffame_response(chat_id: int, lang: str, target_msg_or_cb: Any, user_id: Optional[int] = None):
    data = await database.get_hall_of_fame_detailed(DATABASE_PATH, chat_id, min_votes=MIN_VOTES_FOR_RATING)
    qualified = data["qualified"]
    low_votes = data["low_votes"]

    user_is_admin = False
    if user_id:
        user_is_admin = await is_admin(chat_id, user_id)

    markup = None
    if user_is_admin and (qualified or low_votes):
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_hof_delete_book", lang), callback_data="hof_del_menu")]
        ])

    if not qualified and not low_votes:
        text = t("hof_empty", lang)
        if isinstance(target_msg_or_cb, Message):
            await target_msg_or_cb.answer(text, parse_mode="Markdown")
        elif isinstance(target_msg_or_cb, CallbackQuery):
            await target_msg_or_cb.message.edit_text(text, parse_mode="Markdown")
        return

    text = t("hof_header", lang)
    if qualified:
        for idx, item in enumerate(qualified, 1):
            text += t("hof_item_format", lang, idx=idx, title=item["title"], author=item["author"], wr=item["weighted_rating"], count=item["live_votes_count"]) + "\n"
    else:
        text += "—\n"

    if low_votes:
        text += t("hof_low_votes_header", lang, min_votes=MIN_VOTES_FOR_RATING)
        for idx, item in enumerate(low_votes, 1):
            text += t("hof_item_format", lang, idx=idx, title=item["title"], author=item["author"], wr=item["weighted_rating"], count=item["live_votes_count"]) + "\n"

    if isinstance(target_msg_or_cb, Message):
        await target_msg_or_cb.answer(text, parse_mode="Markdown", reply_markup=markup)
    elif isinstance(target_msg_or_cb, CallbackQuery):
        await target_msg_or_cb.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "hof_del_menu")
async def handle_hof_del_menu(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    data = await database.get_hall_of_fame_detailed(DATABASE_PATH, chat_id, min_votes=MIN_VOTES_FOR_RATING)
    all_hof_books = data["qualified"] + data["low_votes"]

    if not all_hof_books:
        await callback.answer(t("hof_empty", lang), show_alert=True)
        return

    keyboard = []
    for b in all_hof_books:
        btn_text = f"❌ {b['title'][:25]} — {b['author'][:15]}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"hof_del_confirm:{b['id']}")])

    keyboard.append([InlineKeyboardButton(text=t("btn_back_to_menu", lang), callback_data="hof_refresh")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.answer()
    await callback.message.edit_text(t("select_hof_book_to_delete", lang), parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "hof_refresh")
async def handle_hof_refresh(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    await callback.answer()
    await send_halloffame_response(chat_id, lang, target_msg_or_cb=callback, user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("hof_del_confirm:"))
async def handle_hof_del_confirm(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    book_id = int(callback.data.split(":")[1])
    book = await database.get_book_by_id(DATABASE_PATH, book_id)
    book_title = book["title"] if book else f"#{book_id}"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_confirm_yes", lang), callback_data=f"hof_del_do:{book_id}"),
            InlineKeyboardButton(text=t("btn_confirm_cancel", lang), callback_data="hof_del_menu")
        ]
    ])

    await callback.answer()
    await callback.message.edit_text(
        t("hof_confirm_delete_prompt", lang, title=book_title),
        parse_mode="Markdown",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("hof_del_do:"))
async def handle_hof_del_do(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    book_id = int(callback.data.split(":")[1])
    book = await database.get_book_by_id(DATABASE_PATH, book_id)
    book_title = book["title"] if book else f"#{book_id}"

    await database.hide_book_from_hall_of_fame(DATABASE_PATH, book_id, chat_id)
    await callback.answer(t("hof_deleted_success", lang, title=book_title), show_alert=True)

    await send_halloffame_response(chat_id, lang, target_msg_or_cb=callback, user_id=callback.from_user.id)


@router.callback_query(F.data == "admin_finish_reading")
async def handle_admin_finish_reading(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    chat_id = callback.message.chat.id
    current_book = await database.get_current_winning_or_reading_book(DATABASE_PATH, chat_id)
    if not current_book:
        await callback.answer(t("no_active_reading_err", lang), show_alert=True)
        return

    book_id = current_book["id"]
    await database.update_hall_of_fame_rating(DATABASE_PATH, book_id, chat_id)
    await callback.answer(t("added_to_hof_cb", lang))

    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"vote_book:{book_id}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"vote_book:{book_id}:{i}") for i in range(6, 11)]
    row3 = [InlineKeyboardButton(text=t("btn_did_not_read", lang), callback_data=f"vote_book:{book_id}:not_read")]
    markup = InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])

    card_text = t(
        "finish_reading_card_title",
        lang,
        title=current_book["title"],
        author=current_book["author"]
    )

    await bot.send_message(chat_id, card_text, parse_mode="Markdown", reply_markup=markup)
    await callback.message.edit_text(t("added_to_hof_cb", lang), parse_mode="Markdown")


@router.callback_query(F.data.startswith("vote_book:"))
async def handle_vote_book_callback(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    book_id = int(parts[1])
    score_param = parts[2].lower()
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    if score_param in ["not_read", "none"]:
        score = None
        await database.save_read_rating(DATABASE_PATH, user_id, book_id, score=None)
        await callback.answer(t("saved_did_not_read_cb", lang), show_alert=True)
    else:
        score = int(score_param)
        await database.save_read_rating(DATABASE_PATH, user_id, book_id, score=score)
        await callback.answer(t("saved_read_score_cb", lang, score=score), show_alert=True)

    stats = await database.update_hall_of_fame_rating(DATABASE_PATH, book_id, chat_id)
    book = await database.get_book_by_id(DATABASE_PATH, book_id)
    book_title = book["title"] if book else f"#{book_id}"

    feedback_text = t(
        "vote_feedback_msg",
        lang,
        title=book_title,
        avg_rating=stats["avg_rating"],
        count=stats["votes_count"]
    )

    try:
        await callback.message.edit_text(feedback_text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not edit message for vote_book feedback: {e}")


async def main():
    logger.info("Initializing database...")
    await database.init_db(DATABASE_PATH)

    logger.info("Starting scheduler...")
    scheduler.start()

    logger.info("Starting Bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
