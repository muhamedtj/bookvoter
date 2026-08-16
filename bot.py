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
    FSInputFile, Poll
)
from aiogram.enums import ChatType
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database

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


# Google Books API fetcher
async def fetch_google_books(query: str) -> List[Dict[str, str]]:
    url = f"https://www.googleapis.com/books/v1/volumes?q={aiohttp.helpers.quote(query)}&maxResults=3"
    if GOOGLE_BOOKS_API_KEY:
        url += f"&key={GOOGLE_BOOKS_API_KEY}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Google Books API returned status {resp.status}")
                    # Fallback construct if API limit reached or error
                    return [{
                        "title": query.title(),
                        "author": "Unknown Author",
                        "genre": "General"
                    }]
                data = await resp.json()
                items = data.get("items", [])
                results = []
                for item in items[:3]:
                    info = item.get("volumeInfo", {})
                    title = info.get("title", "Unknown Title")
                    authors = info.get("authors", ["Unknown Author"])
                    author_str = ", ".join(authors)
                    categories = info.get("categories", [])
                    genre_str = categories[0] if categories else "General"
                    results.append({
                        "title": title,
                        "author": author_str,
                        "genre": genre_str
                    })
                if not results:
                    results.append({
                        "title": query.title(),
                        "author": "Unknown Author",
                        "genre": "General"
                    })
                return results
        except Exception as e:
            logger.error(f"Error fetching Google Books: {e}")
            return [{
                "title": query.title(),
                "author": "Unknown Author",
                "genre": "General"
            }]


# --- Handlers ---

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    args = command.args

    if args == "rate_new" and message.chat.type == ChatType.PRIVATE:
        await send_next_unrated_book(message.from_user.id, message)
        return

    welcome_text = (
        "📚 **Welcome to BookVoter Bot!**\n\n"
        "Commands:\n"
        "• `/suggest [book title]` - Suggest a book for your group backlog.\n"
        "• `/admin` - Group admin panel (Start vote, Finish reading, Stats).\n"
        "• Click on book rating links sent in groups to rate backlog books privately!\n"
    )
    await message.answer(welcome_text, parse_mode="Markdown")


@router.message(Command("suggest"))
async def handle_suggest(message: Message, command: CommandObject):
    await register_user_and_chat(message)
    query = command.args
    if not query:
        await message.answer("Please specify a book title, e.g.: `/suggest Dune`", parse_mode="Markdown")
        return

    status_msg = await message.answer("Searching Google Books...")
    books = await fetch_google_books(query)

    if not books:
        await status_msg.edit_text("No books found for your query. Please try a different title.")
        return

    temp_key = f"sug_{message.chat.id}_{message.from_user.id}_{int(datetime.now().timestamp())}"
    pending_suggestions[temp_key] = books

    inline_keyboard = []
    for idx, b in enumerate(books):
        btn_text = f"📖 {b['title'][:25]} by {b['author'][:15]}"
        inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"sel_sug:{temp_key}:{idx}")])

    markup = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await status_msg.edit_text("Select the matching book from Google Books results:", reply_markup=markup)


# Pending suggestions dictionary
pending_suggestions: Dict[str, List[Dict[str, str]]] = {}


@router.callback_query(F.data.startswith("sel_sug:"))
async def handle_suggestion_select(callback: CallbackQuery):
    await register_user_and_chat(callback.message)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid selection.")
        return

    temp_key, idx_str = parts[1], parts[2]
    idx = int(idx_str)

    books_list = pending_suggestions.get(temp_key)
    if not books_list or idx >= len(books_list):
        await callback.answer("Selection expired or invalid. Please run /suggest again.")
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
        [InlineKeyboardButton(text="⭐ Rate Backlog Books", url=rate_url)]
    ])

    await callback.answer("Book saved to backlog!")
    await callback.message.edit_text(
        f"✅ **Book Added to Backlog!**\n\n"
        f"📖 **Title:** {selected_book['title']}\n"
        f"✍️ **Author:** {selected_book['author']}\n"
        f"🏷️ **Genre:** {selected_book['genre']}\n\n"
        f"Click below to rate unrated books in private messages!",
        parse_mode="Markdown",
        reply_markup=rate_markup
    )


# Anti-Spam Rating in Private Messages
async def send_next_unrated_book(user_tg_id: int, target_msg_or_user: Any):
    unrated_books = await database.get_unrated_backlog_books_for_user(DATABASE_PATH, user_tg_id)
    if not unrated_books:
        text = "🎉 **All caught up!** You have rated all available backlog books for your groups."
        if isinstance(target_msg_or_user, Message):
            await target_msg_or_user.answer(text, parse_mode="Markdown")
        elif isinstance(target_msg_or_user, CallbackQuery):
            await target_msg_or_user.message.edit_text(text, parse_mode="Markdown")
        return

    book = unrated_books[0]
    rating_buttons = []
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"rate:{book['id']}:{i}") for i in range(6, 11)]
    markup = InlineKeyboardMarkup(inline_keyboard=[row1, row2])

    msg_text = (
        f"📚 **Group:** {book['chat_title']}\n\n"
        f"📖 **Title:** {book['title']}\n"
        f"✍️ **Author:** {book['author']}\n"
        f"🏷️ **Genre:** {book['genre'] or 'N/A'}\n\n"
        f"Rate this book from **1** (lowest) to **10** (highest):"
    )

    if isinstance(target_msg_or_user, Message):
        await target_msg_or_user.answer(msg_text, parse_mode="Markdown", reply_markup=markup)
    elif isinstance(target_msg_or_user, CallbackQuery):
        await target_msg_or_user.message.edit_text(msg_text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data.startswith("rate:"))
async def handle_rate_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    book_id, score = int(parts[1]), int(parts[2])
    await database.save_backlog_rating(DATABASE_PATH, callback.from_user.id, book_id, score)
    await callback.answer(f"Saved score: {score}/10")

    # Proceed to send next unrated book
    await send_next_unrated_book(callback.from_user.id, callback)


# --- Admin Panel & Voting ---

@router.message(Command("admin"))
async def handle_admin(message: Message):
    await register_user_and_chat(message)
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer("⚠️ Only group administrators can open the admin panel.")
        return

    markup = get_admin_keyboard()
    await message.answer("⚙️ **BookVoter Admin Panel**", parse_mode="Markdown", reply_markup=markup)


def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Start Next Book Vote", callback_data="admin_start_vote")],
        [InlineKeyboardButton(text="🏆 Finish Reading (Hall of Fame)", callback_data="admin_finish_reading")],
        [InlineKeyboardButton(text="📊 Group Stats", callback_data="admin_group_stats")]
    ])


@router.callback_query(F.data == "admin_start_vote")
async def handle_admin_start_vote(callback: CallbackQuery):
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer("Admin rights required.", show_alert=True)
        return

    await callback.answer()
    await start_vote_process(callback.message.chat.id)


@router.message(Command("vote"))
async def handle_vote_cmd(message: Message):
    await register_user_and_chat(message)
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer("⚠️ Only group administrators can start a vote.")
        return
    await start_vote_process(message.chat.id)


async def start_vote_process(chat_id: int):
    # Check if poll is already running
    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if active_poll:
        await bot.send_message(chat_id, "⚠️ A vote is already in progress for this group!")
        return

    # Select top 3 books from backlog
    top_books = await database.get_top_backlog_books_for_vote(DATABASE_PATH, chat_id, limit=3)
    if not top_books:
        await bot.send_message(chat_id, "❌ No backlog books available to start a vote. Suggest some books with `/suggest` first!")
        return

    book_ids = [b["id"] for b in top_books]
    await database.update_books_status(DATABASE_PATH, book_ids, "voting")

    options = [f"{b['title']} — {b['author']}"[:100] for b in top_books]
    options_mapping = {idx: b["id"] for idx, b in enumerate(top_books)}

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question="🗳️ Vote for the next book to read!",
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
        f"⏳ **Vote started!** The poll will automatically close in 24 hours.",
        parse_mode="Markdown"
    )


@router.message(Command("finish_vote"))
async def handle_finish_vote_cmd(message: Message):
    await register_user_and_chat(message)
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.answer("⚠️ Only group administrators can manually finish a vote.")
        return
    await finish_vote_process(message.chat.id)


async def finish_vote_process(chat_id: int):
    active_poll = await database.get_active_poll(DATABASE_PATH, chat_id)
    if not active_poll:
        await bot.send_message(chat_id, "⚠️ No active vote found to finish.")
        return

    poll_message_id = active_poll["message_id"]
    options_mapping: Dict[int, int] = {int(k): v for k, v in json.loads(active_poll["options_json"]).items()}
    voting_book_ids = list(options_mapping.values())

    # Stop poll to get results
    try:
        stopped_poll: Poll = await bot.stop_poll(chat_id, poll_message_id)

        # Find winning option
        max_votes = -1
        winning_option_idx = 0
        for idx, option in enumerate(stopped_poll.options):
            if option.voter_count > max_votes:
                max_votes = option.voter_count
                winning_option_idx = idx

        winning_book_id = options_mapping.get(winning_option_idx, voting_book_ids[0])
    except Exception as e:
        logger.error(f"Error stopping poll in chat {chat_id}: {e}")
        # Default to first book if stopping poll fails
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
        f"🏆 **Voting Ended!**\n\n"
        f"The winner is: **{win_title}** by **{win_author}**!\n"
        f"📥 Fetching book file from library...",
        parse_mode="Markdown"
    )

    # Trigger downloader.py subprocess
    await execute_downloader_and_send(chat_id, winning_book_id, win_title)


async def execute_downloader_and_send(chat_id: int, book_id: int, book_title: str):
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
            file_path = stdout.decode().strip()
            if os.path.exists(file_path):
                document = FSInputFile(file_path)
                msg = await bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=f"📚 Here is your book: **{book_title}**",
                    parse_mode="Markdown"
                )
                file_id = msg.document.file_id if msg.document else ""
                await database.mark_book_done(DATABASE_PATH, book_id, file_id)

                # Delete local file after sending
                try:
                    os.remove(file_path)
                except Exception as ex:
                    logger.warning(f"Could not remove local file {file_path}: {ex}")
            else:
                await bot.send_message(chat_id, "⚠️ File downloaded, but physical file was not found on server.")
        else:
            err_msg = stderr.decode().strip()
            logger.error(f"Downloader failed for '{book_title}': {err_msg}")
            await bot.send_message(chat_id, f"❌ File not found in library for **{book_title}**.", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Subprocess execution error: {e}")
        await bot.send_message(chat_id, f"❌ An error occurred while downloading **{book_title}**.", parse_mode="Markdown")


# --- Hall of Fame & Stats Callbacks ---

@router.callback_query(F.data == "admin_finish_reading")
async def handle_admin_finish_reading(callback: CallbackQuery):
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer("Admin rights required.", show_alert=True)
        return

    chat_id = callback.message.chat.id
    current_book = await database.get_current_winning_or_reading_book(DATABASE_PATH, chat_id)
    if not current_book:
        await callback.answer("No currently active book to finish.", show_alert=True)
        return

    await database.add_to_hall_of_fame(DATABASE_PATH, current_book["id"], chat_id, final_rating=None)
    await callback.answer("Book added to Hall of Fame!")

    hof_list = await database.get_hall_of_fame(DATABASE_PATH, chat_id)
    text = "🏆 **Hall of Fame Updated!**\n\n"
    for idx, item in enumerate(hof_list, 1):
        text += f"{idx}. **{item['title']}** by {item['author']}\n"

    await callback.message.edit_text(text, parse_mode="Markdown")


@router.callback_query(F.data == "admin_group_stats")
async def handle_admin_group_stats(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    stats = await database.get_group_stats(DATABASE_PATH, chat_id)

    text = (
        f"📊 **Group Statistics**\n\n"
        f"• Backlog Books: **{stats['backlog_count']}**\n"
        f"• Completed Books: **{stats['done_count']}**\n"
        f"• Active Raters: **{stats['active_raters']}**"
    )
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())


# Superuser Monitoring
@router.message(Command("sys_stats"))
async def handle_sys_stats(message: Message):
    await register_user_and_chat(message)
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return

    stats = await database.get_sys_stats(DATABASE_PATH)
    text = (
        f"🌐 **Global System Metrics**\n\n"
        f"• Total Active Groups: **{stats['total_active_chats']}**\n"
        f"• Total Unique Internal Users: **{stats['total_unique_users']}**\n"
        f"• Total Downloaded Books: **{stats['total_downloaded_books']}**"
    )
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
