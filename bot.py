import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, Poll, PollAnswer
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
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")

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


# Helper: Fetch English edition metadata for author/genre enrichment
async def fetch_english_enrichment(session: aiohttp.ClientSession, title: str) -> Dict[str, Any]:
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q={aiohttp.helpers.quote(title)}&langRestrict=en&maxResults=1"
        if GOOGLE_BOOKS_API_KEY:
            url += f"&key={GOOGLE_BOOKS_API_KEY}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])
                if items:
                    info = items[0].get("volumeInfo", {})
                    authors = info.get("authors", [])
                    categories = info.get("categories", [])
                    return {
                        "author": ", ".join(authors) if authors else None,
                        "genre": categories[0] if categories else None
                    }
    except Exception as e:
        logger.debug(f"Enrichment fetch failed for '{title}': {e}")
    return {"author": None, "genre": None}


# Google Books API fetcher with Top-5 results & Enrichment
async def fetch_google_books(query: str, lang: str = "en") -> List[Dict[str, str]]:
    url = f"https://www.googleapis.com/books/v1/volumes?q={aiohttp.helpers.quote(query)}&maxResults=5"
    if GOOGLE_BOOKS_API_KEY:
        url += f"&key={GOOGLE_BOOKS_API_KEY}"

    unknown_author_str = t("unknown_author", lang)
    general_genre_str = t("general_genre", lang)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Google Books API returned status {resp.status}")
                    return [{
                        "title": query.title(),
                        "author": unknown_author_str,
                        "genre": general_genre_str
                    }]
                data = await resp.json()
                items = data.get("items", [])
                results = []
                for item in items[:5]:
                    info = item.get("volumeInfo", {})
                    title = info.get("title", "Unknown Title")
                    authors = info.get("authors", [])
                    categories = info.get("categories", [])

                    author_val = ", ".join(authors) if authors else None
                    genre_val = categories[0] if categories else None

                    # If author or genre is missing/General, attempt English enrichment
                    if not author_val or not genre_val or genre_val.lower() == "general":
                        enriched = await fetch_english_enrichment(session, title)
                        if not author_val and enriched.get("author"):
                            author_val = enriched["author"]
                        if (not genre_val or genre_val.lower() == "general") and enriched.get("genre"):
                            genre_val = enriched["genre"]

                    author_str = author_val if author_val else unknown_author_str
                    genre_str = genre_val if (genre_val and genre_val.lower() != "general") else general_genre_str

                    results.append({
                        "title": title,
                        "author": author_str,
                        "genre": genre_str
                    })

                if not results:
                    results.append({
                        "title": query.title(),
                        "author": unknown_author_str,
                        "genre": general_genre_str
                    })
                return results
        except Exception as e:
            logger.error(f"Error fetching Google Books: {e}")
            return [{
                "title": query.title(),
                "author": unknown_author_str,
                "genre": general_genre_str
            }]


# --- Handlers ---

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else chat_id

    # Check if language is selected yet
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

    # Duplicate key sections in start welcome message as inline buttons
    welcome_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_open_control_panel", lang), callback_data="open_control_panel")],
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang), url=f"https://t.me/{(await bot.get_me()).username}?start=rate_new")]
    ])

    await message.answer(t("welcome_msg", lang), parse_mode="Markdown", reply_markup=welcome_markup)


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
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang_code), url=f"https://t.me/{(await bot.get_me()).username}?start=rate_new")]
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
    books = await fetch_google_books(query, lang)

    if not books:
        await status_msg.edit_text(t("no_books_found", lang))
        return

    temp_key = f"sug_{message.chat.id}_{message.from_user.id}_{int(datetime.now().timestamp())}"
    pending_suggestions[temp_key] = books

    inline_keyboard = []
    for idx, b in enumerate(books):
        btn_text = f"📖 {b['title'][:25]} by {b['author'][:15]}"
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

    book_id = await database.add_book(
        DATABASE_PATH,
        chat_id=chat_id,
        title=selected_book["title"],
        author=selected_book["author"],
        genre=selected_book["genre"],
        suggested_by_tg_id=user_id
    )

    # Clean up pending
    pending_suggestions.pop(temp_key, None)

    bot_info = await bot.get_me()
    rate_url = f"https://t.me/{bot_info.username}?start=rate_new"
    rate_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rate_backlog", lang), url=rate_url)]
    ])

    await callback.answer(t("book_saved_cb", lang))
    text = (
        f"{t('book_added_title', lang)}\n\n"
        f"{t('book_field_title', lang, title=selected_book['title'])}\n"
        f"{t('book_field_author', lang, author=selected_book['author'])}\n"
        f"{t('book_field_genre', lang, genre=selected_book['genre'])}\n\n"
        f"{t('click_below_to_rate', lang)}"
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

    # Proceed to send next unrated book
    await send_next_unrated_book(callback.from_user.id, callback, lang)


# --- Real-time Poll Answer / Vote Monitoring (> 50% majority auto-closure) ---

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

    # Check if any option has > 50% of the votes
    for option in poll.options:
        if option.voter_count / total_votes > 0.5:
            logger.info(f"Poll option achieved majority (>50%) in chat {chat_id}. Finishing vote automatically...")

            # Cancel scheduled 24h job
            job_id = f"poll_end_{chat_id}_{active_poll['message_id']}"
            try:
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)
            except Exception as e:
                logger.warning(f"Failed to remove scheduled job {job_id}: {e}")

            # Complete vote process early
            await finish_vote_process(chat_id)
            break


# --- Admin Panel & Voting ---

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
        [InlineKeyboardButton(text=t("btn_group_stats", lang), callback_data="admin_group_stats")]
    ])


@router.callback_query(F.data == "admin_start_vote")
async def handle_admin_start_vote(callback: CallbackQuery):
    lang = await get_lang(callback.message.chat.id, callback.from_user.id)
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer(t("only_admins_allowed", lang), show_alert=True)
        return

    await callback.answer()
    await start_vote_process(callback.message.chat.id, lang)


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

    # Cancel scheduled job
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

    # Return to admin main menu
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
    await start_vote_process(message.chat.id, lang)


async def start_vote_process(chat_id: int, lang: str):
    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if active_poll:
        await bot.send_message(chat_id, t("vote_in_progress_err", lang))
        return

    top_books = await database.get_top_backlog_books_for_vote(DATABASE_PATH, chat_id, limit=3)
    if not top_books:
        await bot.send_message(chat_id, t("no_backlog_books_err", lang))
        return

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

    # Schedule 24h job
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

    await bot.send_message(
        chat_id,
        t("vote_started_msg", lang),
        parse_mode="Markdown"
    )


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

    await execute_downloader_and_send(chat_id, winning_book_id, win_title, lang)


async def execute_downloader_and_send(chat_id: int, book_id: int, book_title: str, lang: str):
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "downloader.py",
            book_title,
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


# --- Hall of Fame & Stats Callbacks ---

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

    await database.add_to_hall_of_fame(DATABASE_PATH, current_book["id"], chat_id, final_rating=None)
    await callback.answer(t("added_to_hof_cb", lang))

    hof_list = await database.get_hall_of_fame(DATABASE_PATH, chat_id)
    text = t("hof_title", lang)
    for idx, item in enumerate(hof_list, 1):
        text += f"{idx}. **{item['title']}** ({item['author']})\n"

    await callback.message.edit_text(text, parse_mode="Markdown")


@router.callback_query(F.data == "admin_group_stats")
async def handle_admin_group_stats(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    lang = await get_lang(chat_id, callback.from_user.id)
    stats = await database.get_group_stats(DATABASE_PATH, chat_id)

    text = t("group_stats_text", lang, **stats)
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard(lang))


# Superuser Monitoring
@router.message(Command("sys_stats"))
async def handle_sys_stats(message: Message):
    await register_user_and_chat(message)
    lang = await get_lang(message.chat.id, message.from_user.id if message.from_user else None)
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return

    stats = await database.get_sys_stats(DATABASE_PATH)
    text = t("sys_stats_text", lang, **stats)
    await message.answer(text, parse_mode="Markdown")


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
