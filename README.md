# BookVoter 📚

A multi-tenant Telegram Bot system for managing book club suggestions, ratings, voting, and automated book downloads.

## Features
- **Multi-Tenancy Support**: Independent group chats supported with isolated book backlogs, ratings, and statistics.
- **Google Books Integration**: `/suggest [book title]` searches Google Books API and provides interactive selection buttons.
- **Anti-Spam Private Rating**: Members rate books privately (1-10) using deep links (`/start rate_new`).
- **Smart Rotation & 24h Voting**: Automatically calculates average backlog ratings, filters previously read genres, picks Top 3 options, starts a native Telegram Poll, and schedules poll closure in 24 hours using `APScheduler`.
- **Automated Userbot Downloader**: Standalone `pyrogram` script (`downloader.py`) searches for `.epub` or `.pdf` files in a Telegram library channel and delivers them to group chat.
- **Hall of Fame & Group Stats**: Track finished books and view group engagement stats.
- **Superadmin Monitoring**: `/sys_stats` command for superadmins to view global system metrics.

## Tech Stack
- Python 3.11+
- `aiogram 3.x` (Main Bot)
- `pyrogram` + `tgcrypto` (Userbot Downloader)
- `aiohttp` (Google Books API)
- `aiosqlite` (SQLite Async Database)
- `apscheduler` (24-hour voting timers)

## Project Structure
- `database.py`: SQLite schema and async database functions.
- `bot.py`: Main aiogram bot logic, handlers, admin panel, and scheduler.
- `downloader.py`: Standalone pyrogram script for document search and downloading.
- `.env.example`: Template for environment variables.
- `requirements.txt`: Project dependencies.

## Setup Instructions

1. **Clone the repository and install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   ```
   - `BOT_TOKEN`: Telegram bot token from @BotFather.
   - `API_ID` & `API_HASH`: Telegram API credentials from my.telegram.org.
   - `SESSION_STRING`: Pyrogram String Session for the downloader userbot.
   - `CHANNEL_ID`: Channel ID or username (e.g. `@my_book_channel` or `-100123456789`) where book files are hosted.
   - `SUPER_ADMIN_IDS`: Comma-separated user Telegram IDs for superadmin access (`/sys_stats`).

3. **Run the Bot**:
   ```bash
   python bot.py
   ```
