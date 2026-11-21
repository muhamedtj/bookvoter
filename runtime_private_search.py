"""Private BookVoter library search.

This is only a thin private-chat UI over the library search/downloader that
already exists in BookVoter. It does not add books to any club or alter club
backlogs.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import ForceReply

import bot_core as core


_installed = False
PROMPT_TTL_SECONDS = 300
SEARCH_TTL_SECONDS = 600
DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("SUBPROCESS_DOWNLOAD_TIMEOUT_SECONDS", "120"))

_pending_prompts: dict[int, dict[str, Any]] = {}
_search_sessions: dict[str, dict[str, Any]] = {}


def _cleanup_expired() -> None:
    now = time.time()
    for user_id, item in list(_pending_prompts.items()):
        if now - float(item.get("created_at") or 0) > PROMPT_TTL_SECONDS:
            _pending_prompts.pop(user_id, None)
    for token, item in list(_search_sessions.items()):
        if now - float(item.get("created_at") or 0) > SEARCH_TTL_SECONDS:
            _search_sessions.pop(token, None)


async def _start_private_search(callback: core.CallbackQuery) -> None:
    if callback.message.chat.type != core.ChatType.PRIVATE:
        await callback.answer()
        return

    _cleanup_expired()
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    user_id = callback.from_user.id

    old = _pending_prompts.pop(user_id, None)
    if old:
        try:
            await core.bot.delete_message(user_id, int(old["message_id"]))
        except Exception:
            pass

    text = (
        "🔎 <b>Найти книгу</b>\n\nОтветьте на это сообщение названием книги."
        if lang == "ru"
        else "🔎 <b>Find a book</b>\n\nReply to this message with the book title."
    )
    prompt = await core.bot.send_message(
        user_id,
        text,
        parse_mode="HTML",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder=("Название книги" if lang == "ru" else "Book title"),
        ),
    )
    _pending_prompts[user_id] = {
        "message_id": prompt.message_id,
        "created_at": time.time(),
    }
    await callback.answer()


async def _handle_private_title(message: core.Message) -> bool:
    if not message.from_user or message.chat.type != core.ChatType.PRIVATE:
        return False

    _cleanup_expired()
    pending = _pending_prompts.get(message.from_user.id)
    if not pending:
        return False

    reply = message.reply_to_message
    if not reply or reply.message_id != int(pending.get("message_id") or 0):
        return False

    _pending_prompts.pop(message.from_user.id, None)
    lang = await core.get_lang(message.chat.id, message.from_user.id)
    query = (message.text or message.caption or "").strip()[:200]

    if not query:
        await message.answer("⚠️ Укажите название книги." if lang == "ru" else "⚠️ Enter a book title.")
        return True

    status = await message.answer("🔎 Ищем книгу..." if lang == "ru" else "🔎 Searching for the book...")
    books = await core.fetch_books_via_userbot(query, lang)
    if not books:
        await status.edit_text(
            "Книгу не удалось найти. Попробуйте уточнить название."
            if lang == "ru"
            else "Could not find a matching book. Try a more specific title."
        )
        return True

    token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    _search_sessions[token] = {
        "user_id": message.from_user.id,
        "books": books,
        "created_at": time.time(),
    }

    rows = []
    for idx, book in enumerate(books[:8]):
        title = (book.get("title") or "")[:30]
        author = (book.get("author") or "").strip()
        if author.lower() in {"unknown author", "n/a", "none"}:
            author = ""
        label = f"📖 {title}" + (f" — {author[:16]}" if author else "")
        rows.append([
            core.InlineKeyboardButton(
                text=label,
                callback_data=f"private_search_select:{token}:{idx}",
            )
        ])

    await status.edit_text(
        "Выберите нужную книгу:" if lang == "ru" else "Choose the book:",
        reply_markup=core.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    return True


async def _download_private_book(callback: core.CallbackQuery) -> None:
    if callback.message.chat.type != core.ChatType.PRIVATE:
        await callback.answer()
        return

    _cleanup_expired()
    lang = await core.get_lang(callback.message.chat.id, callback.from_user.id)
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Поиск устарел." if lang == "ru" else "Search expired.", show_alert=True)
        return

    token = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await callback.answer()
        return

    session = _search_sessions.get(token)
    if not session or int(session.get("user_id") or 0) != callback.from_user.id:
        await callback.answer("Поиск устарел." if lang == "ru" else "Search expired.", show_alert=True)
        return

    books = session.get("books") or []
    if index < 0 or index >= len(books):
        await callback.answer("Поиск устарел." if lang == "ru" else "Search expired.", show_alert=True)
        return

    selected = books[index]
    _search_sessions.pop(token, None)
    title = (selected.get("title") or "").strip()
    author = (selected.get("author") or "").strip()
    saved_cmd = (selected.get("download_cmd") or "").strip()

    if saved_cmd.startswith("/"):
        queries = [saved_cmd]
    elif author and author.lower() not in {"unknown author", "n/a", "none"}:
        queries = [f"{title} {author}".strip(), title]
    else:
        queries = [title]

    await callback.answer()
    await callback.message.edit_text(
        f"📥 Ищу файл: <b>{core.escape_html(title)}</b>..."
        if lang == "ru"
        else f"📥 Looking for file: <b>{core.escape_html(title)}</b>...",
        parse_mode="HTML",
    )

    last_error = ""
    async with core.library_access_lock:
        for query in queries:
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "downloader.py",
                    "download",
                    query,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    last_error = "download timeout"
                    continue

                if proc.returncode != 0:
                    last_error = stderr.decode("utf-8", errors="replace").strip()
                    continue

                lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
                file_path = lines[-1] if lines else ""
                if not file_path or not os.path.exists(file_path):
                    last_error = "downloaded file not found"
                    continue

                try:
                    await core.bot.send_document(
                        chat_id=callback.from_user.id,
                        document=core.FSInputFile(file_path),
                        caption=(
                            f"📚 <b>{core.escape_html(title)}</b>"
                            + (f" — {core.escape_html(author)}" if author else "")
                        ),
                        parse_mode="HTML",
                    )
                finally:
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

                await callback.message.edit_text(
                    "✅ Книга найдена и отправлена." if lang == "ru" else "✅ Book found and sent."
                )
                return
            except Exception as exc:
                last_error = str(exc)

    core.logger.warning(f"Private library search failed for {callback.from_user.id}: {last_error}")
    await callback.message.edit_text(
        "⚠️ Файл этой книги сейчас найти не удалось."
        if lang == "ru"
        else "⚠️ Could not find a downloadable file for this book."
    )


class _PrivateSearchMessageMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if await _handle_private_title(event):
            return None
        return await handler(event, data)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    core.router.callback_query(core.F.data == "private_book_search")(_start_private_search)
    core.router.callback_query(core.F.data.startswith("private_search_select:"))(_download_private_book)
    core.router.message.outer_middleware(_PrivateSearchMessageMiddleware())
