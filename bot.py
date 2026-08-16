import os
import sys
import json
import logging
import asyncio
import traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import quote

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
    if chat_id > 0:  # Private chat
        return True
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
            # Extract JSON array output securely
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
            "genre": t("general_genre", lang),
            "raw_label": query.title()
        }]
    except Exception as e:
        logger.error(f"Subprocess search execution error: {e}")
        return None


# In-memory poll vote counter for tracking non-anonymous PollAnswer votes per option
poll_votes_tracker: Dict[str, Dict[int, int]] = {}


# --- Handlers ---

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else chat_id

    current_lang = await database.get_user_language(DATABASE_PATH, user_id) if message.chat.type == ChatType.PRIVATE else await database.get_chat_language(DATABASE_PATH, chat_id)

    if not current_lang:
        await message.answer(
            t("select_language_prompt", "en"),
            parse_mode="Markdown",
            reply_markup=get_language_keyboard()
        )
        return

    lang = current_lang
    args = command.args

    if args == "rate_new" and message.chat.type == ChatType.PRIVATE:
        await send_next_unrated_book(message.from_user.id, message, lang)
        return

    welcome_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_open_control_panel", lang), callback_data="open_control_panel")],
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang), url=f"https://t.me/{(await bot.get_me()).username}?start=rate_new")],
        [InlineKeyboardButton(text=t("btn_report_error", lang), callback_data="report_error")]
    ])

    await message.answer(t("welcome_msg", lang), parse_mode="Markdown", reply_markup=welcome_markup)


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
        text += (
            f"{idx}. **{b['title']}** — {b['author']}\n"
            f"   🏷️ {b['genre'] or 'N/A'} | ⭐ {round(b['wish_score'], 1)}/10 | 👤 Suggested by: {s_name}\n\n"
        )

    await message.answer(text, parse_mode="Markdown")


@router.callback_query(F.data == "open_control_panel")
async def handle_open_control_panel_cb(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return
    await callback.answer()
    markup = get_admin_keyboard(lang)
    await callback.message.answer(t("admin_panel_title", lang), parse_mode="Markdown", reply_markup=markup)


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

    if callback.message.chat.type == ChatType.PRIVATE:
        await database.set_user_language(DATABASE_PATH, callback.from_user.id, lang_code)
    else:
        if not await is_admin(chat_id, callback.from_user.id):
            await callback.answer(t("only_admins_allowed", lang_code), show_alert=True)
            return
        await database.set_chat_language(DATABASE_PATH, chat_id, lang_code)

    await callback.answer(t("language_selected", lang_code))
    welcome_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_open_control_panel", lang_code), callback_data="open_control_panel")],
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang_code), url=f"https://t.me/{(await bot.get_me()).username}?start=rate_new")],
        [InlineKeyboardButton(text=t("btn_report_error", lang_code), callback_data="report_error")]
    ])
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
        err_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_report_error", lang), callback_data="report_error")]
        ])
        await status_msg.edit_text(t("google_books_api_error", lang), reply_markup=err_markup)
        return

    if not books:
        await status_msg.edit_text(t("no_valid_books_found", lang))
        return

    temp_key = f"sug_{message.chat.id}_{message.from_user.id}_{int(datetime.now().timestamp())}"
    pending_suggestions[temp_key] = books

    inline_keyboard = []
    for idx, b in enumerate(books):
        label_author = f" ({b['author'][:20]})" if b.get('author') and b['author'].lower() != 'unknown author' else ""
        btn_text = f"📖 {b['title'][:30]}{label_author}"
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

    # If genre is missing or empty, ask user to select a genre via inline buttons
    if not selected_book.get("genre") or selected_book["genre"].strip().lower() in ["general", "без жанра", ""]:
        # Save selection details in pending_genre_selections
        genre_key = f"gen_{chat_id}_{user_id}_{int(datetime.now().timestamp())}"
        pending_genre_selections[genre_key] = selected_book
        pending_suggestions.pop(temp_key, None)

        genre_markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t("genre_scifi", lang), callback_data=f"set_gen:{genre_key}:Sci-Fi"),
                InlineKeyboardButton(text=t("genre_classics", lang), callback_data=f"set_gen:{genre_key}:Classics")
            ],
            [
                InlineKeyboardButton(text=t("genre_business", lang), callback_data=f"set_gen:{genre_key}:Business"),
                InlineKeyboardButton(text=t("genre_detective", lang), callback_data=f"set_gen:{genre_key}:Detective")
            ],
            [
                InlineKeyboardButton(text=t("genre_nonfiction", lang), callback_data=f"set_gen:{genre_key}:Non-Fiction"),
                InlineKeyboardButton(text=t("genre_other", lang), callback_data=f"set_gen:{genre_key}:General")
            ]
        ])

        await callback.answer()
        await callback.message.edit_text(
            t("select_genre_prompt", lang, title=selected_book["title"], author=selected_book["author"]),
            parse_mode="Markdown",
            reply_markup=genre_markup
        )
        return

    book_id = await database.add_book(
        DATABASE_PATH,
        chat_id=chat_id,
        title=selected_book["title"],
        author=selected_book["author"],
        genre=selected_book["genre"],
        suggested_by_tg_id=user_id
    )

    pending_suggestions.pop(temp_key, None)

    bot_info = await bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_new"
    rate_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang), url=rate_url)]
    ])

    await callback.answer(t("book_saved_cb", lang))
    text = (
        t("book_added_confirmation_exact", lang, title=selected_book["title"], author=selected_book["author"]) + "\n\n" +
        t("click_below_to_rate", lang)
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=rate_markup)


# Pending genre selections dictionary
pending_genre_selections: Dict[str, Dict[str, str]] = {}


@router.callback_query(F.data.startswith("set_gen:"))
async def handle_set_genre_callback(callback: CallbackQuery):
    await register_user_and_chat(callback.message)
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return

    genre_key, chosen_genre = parts[1], parts[2]
    selected_book = pending_genre_selections.get(genre_key)

    if not selected_book:
        await callback.answer(t("selection_expired", lang))
        return

    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    book_id = await database.add_book(
        DATABASE_PATH,
        chat_id=chat_id,
        title=selected_book["title"],
        author=selected_book["author"],
        genre=chosen_genre,
        suggested_by_tg_id=user_id
    )

    pending_genre_selections.pop(genre_key, None)

    bot_info = await bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_new"
    rate_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang), url=rate_url)]
    ])

    await callback.answer(t("book_saved_cb", lang))
    text = (
        t("book_added_confirmation_exact", lang, title=selected_book["title"], author=selected_book["author"]) + "\n\n" +
        t("click_below_to_rate", lang)
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=rate_markup)


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
        author=book['author'],
        genre=book['genre'] or 'N/A'
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
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return

    markup = get_admin_keyboard(lang)
    await message.answer(t("admin_panel_title", lang), parse_mode="Markdown", reply_markup=markup)


def get_admin_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_start_vote", lang), callback_data="admin_start_vote")],
        [InlineKeyboardButton(text=t("btn_finish_vote_early", lang), callback_data="admin_finish_vote_early")],
        [InlineKeyboardButton(text=t("btn_finish_reading", lang), callback_data="admin_finish_reading")],
        [InlineKeyboardButton(text=t("btn_delete_book", lang), callback_data="admin_delete_book")],
        [InlineKeyboardButton(text=t("btn_audit_backlog", lang), callback_data="admin_audit_backlog")],
        [InlineKeyboardButton(text=t("btn_group_stats", lang), callback_data="admin_group_stats")],
        [InlineKeyboardButton(text=t("btn_report_error", lang), callback_data="report_error")]
    ])


@router.callback_query(F.data == "admin_start_vote")
async def handle_admin_start_vote_menu(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if active_poll:
        await callback.answer(t("vote_in_progress_err", lang), show_alert=True)
        return

    genres = await database.get_available_genres_in_backlog(DATABASE_PATH, chat_id)

    keyboard = [
        [InlineKeyboardButton(text=t("all_genres_btn", lang), callback_data="vote_genre:all")]
    ]
    for g in genres:
        keyboard.append([InlineKeyboardButton(text=f"🏷️ {g[:30]}", callback_data=f"vote_genre:{g}")])

    keyboard.append([InlineKeyboardButton(text=t("btn_back_to_menu", lang), callback_data="admin_main_menu")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.answer()
    await callback.message.edit_text(t("select_genre_for_vote", lang), parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data.startswith("vote_genre:"))
async def handle_vote_genre_selected(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    if not await is_admin(chat_id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    genre_param = callback.data.split(":", 1)[1]
    excluded_note = ""

    if genre_param.lower() == "all":
        result_dict = await database.get_top_backlog_books_for_vote(DATABASE_PATH, chat_id, limit=3)
        books = result_dict["books"]
        if result_dict.get("excluded_genre"):
            excluded_note = t("rotation_note", lang, genre=result_dict["excluded_genre"])
    else:
        all_genre_books = await database.get_backlog_books_by_genre(DATABASE_PATH, chat_id, genre_param)
        books = all_genre_books[:3]

    if not books:
        await callback.answer(t("no_backlog_books_err", lang), show_alert=True)
        return

    await callback.answer()
    await launch_poll_for_books(chat_id, books, lang)

    await callback.message.edit_text(
        t("vote_started_msg", lang) + excluded_note,
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
        await callback.answer()
        await callback.message.edit_text(t("audit_clean", lang), parse_mode="Markdown")
        return

    text = t("audit_title", lang)
    for item in audit_items:
        s_name = item["suggestor_name"] or (f"@{item['suggestor_username']}" if item["suggestor_username"] else "User")
        reasons_str = ", ".join([t(f"reason_{r}", lang) for r in item["reasons"]])
        text += f"• **{item['title']}** ({item['author']})\n  👤 Suggested by: {s_name}\n  ⚠️ {reasons_str}\n\n"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_delete_book", lang), callback_data="admin_delete_book")],
        [InlineKeyboardButton(text=t("btn_back_to_menu", lang), callback_data="admin_main_menu")]
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
        [InlineKeyboardButton(text=t("btn_back_to_menu", lang), callback_data="admin_main_menu")]
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
    top_genres_str = "\n".join([f"• {g['genre']}: ⭐ {g['score']}/10" for g in stats["top_genres"]]) or "N/A"

    text = t(
        "sys_stats_text",
        lang,
        filter_label=filter_label,
        total_active_chats=stats["total_active_chats"],
        total_voters=stats["total_voters"],
        total_books_suggested=stats["total_books_suggested"],
        top_books_str=top_books_str,
        top_genres_str=top_genres_str
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
        await callback.answer(t("no_books_to_delete", lang), show_alert=True)
        return

    keyboard = []
    for b in books[:10]:
        btn_text = f"❌ {b['title'][:25]} ({b['author'][:15]})"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_book:{b['id']}")])

    keyboard.append([InlineKeyboardButton(text=t("btn_back_to_menu", lang), callback_data="admin_main_menu")])
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

    markup = get_admin_keyboard(lang)
    await callback.message.edit_text(t("admin_panel_title", lang), parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data == "admin_main_menu")
async def handle_admin_main_menu(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    await callback.answer()
    markup = get_admin_keyboard(lang)
    await callback.message.edit_text(t("admin_panel_title", lang), parse_mode="Markdown", reply_markup=markup)


@router.message(Command("vote"))
async def handle_vote_cmd(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return

    result_dict = await database.get_top_backlog_books_for_vote(DATABASE_PATH, message.chat.id, limit=3)
    top_books = result_dict["books"]
    if not top_books:
        await message.answer(t("no_backlog_books_err", lang))
        return

    excluded_note = ""
    if result_dict.get("excluded_genre"):
        excluded_note = t("rotation_note", lang, genre=result_dict["excluded_genre"])

    await launch_poll_for_books(message.chat.id, top_books, lang)
    await message.answer(t("vote_started_msg", lang) + excluded_note, parse_mode="Markdown")


@router.message(Command("finish_vote"))
async def handle_finish_vote_cmd(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer(t("only_admins_allowed", lang))
        return
    await finish_vote_process(message.chat.id)


async def finish_vote_process(chat_id: int):
    lang = await database.get_effective_language(DATABASE_PATH, chat_id)
    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if not active_poll:
        await bot.send_message(chat_id, t("no_active_vote_err", lang))
        return

    poll_message_id = active_poll["message_id"]
    options_mapping: Dict[int, int] = {int(k): v for k, v in json.loads(active_poll["options_json"]).items()}
    voting_book_ids = list(options_mapping.values())

    try:
        stopped_poll: Poll = await bot.stop_poll(chat_id, poll_message_id)

        max_votes = -1
        winning_option_idx = 0
        for idx, option in enumerate(stopped_poll.options):
            if option.voter_count > max_votes:
                max_votes = option.voter_count
                winning_option_idx = idx

        winning_book_id = options_mapping.get(winning_option_idx, voting_book_ids[0])
    except Exception as e:
        logger.error(f"Error stopping poll in chat {chat_id}: {e}")
        winning_book_id = voting_book_ids[0]

    await database.resolve_vote_winner(DATABASE_PATH, chat_id, winning_book_id, voting_book_ids)
    await database.clear_active_poll(DATABASE_PATH, chat_id)

    winning_book = await database.get_book_by_id(DATABASE_PATH, winning_book_id)
    if not winning_book:
        await bot.send_message(chat_id, "Error retrieving winning book details.")
        return

    win_title = winning_book["title"]
    win_author = winning_book["author"]

    await bot.send_message(
        chat_id,
        t("voting_ended_title", lang, title=win_title, author=win_author),
        parse_mode="Markdown"
    )

    query_str = f"{win_title} {win_author}".strip() if win_author and win_author.lower() != "n/a" else win_title
    await execute_downloader_and_send(chat_id, winning_book_id, win_title, query_str, lang)


async def execute_downloader_and_send(chat_id: int, book_id: int, book_title: str, query_str: str, lang: str):
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
            else:
                await bot.send_message(chat_id, t("file_not_found_in_lib", lang, title=book_title), parse_mode="Markdown")
        else:
            err_msg = stderr.decode().strip()
            logger.error(f"Downloader failed for '{book_title}': {err_msg}")
            await bot.send_message(chat_id, t("file_not_found_in_lib", lang, title=book_title), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Subprocess execution error: {e}")
        await bot.send_message(chat_id, t("file_download_err", lang, title=book_title), parse_mode="Markdown")


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
            text += t("hof_item_format", lang, idx=idx, title=item["title"], author=item["author"], wr=item["weighted_rating"], score=item["avg_score"], count=item["live_votes_count"]) + "\n"
    else:
        text += "—\n"

    if low_votes:
        text += t("hof_low_votes_header", lang, min_votes=MIN_VOTES_FOR_RATING)
        for idx, item in enumerate(low_votes, 1):
            text += t("hof_item_format", lang, idx=idx, title=item["title"], author=item["author"], wr=item["weighted_rating"], score=item["avg_score"], count=item["live_votes_count"]) + "\n"

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
        btn_text = f"❌ {b['title'][:25]} ({b['author'][:15]})"
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

    # Automatically refresh Hall of Fame with recalculated Bayesian ratings
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

    # Construct rating keyboard (1-10 and Didn't read) using vote_book:{book_id}:{score}
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"vote_book:{book_id}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"vote_book:{book_id}:{i}") for i in range(6, 11)]
    row3 = [InlineKeyboardButton(text=t("btn_did_not_read", lang), callback_data=f"vote_book:{book_id}:not_read")]
    markup = InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])

    card_text = t(
        "finish_reading_card_title",
        lang,
        title=current_book["title"],
        author=current_book["author"],
        genre=current_book["genre"] or t("general_genre", lang)
    )

    await bot.send_message(chat_id, card_text, parse_mode="Markdown", reply_markup=markup)
    await callback.message.edit_text(t("added_to_hof_cb", lang), parse_mode="Markdown")


@router.callback_query(F.data.startswith("vote_book:"))
@router.callback_query(F.data.startswith("rate_read:"))
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

    # Recalculate Hall of Fame average rating and votes count
    stats = await database.update_hall_of_fame_rating(DATABASE_PATH, book_id, chat_id)
    book = await database.get_book_by_id(DATABASE_PATH, book_id)
    book_title = book["title"] if book else f"#{book_id}"

    # Visual feedback update
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
