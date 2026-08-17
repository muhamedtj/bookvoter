"""Timeout-safe overrides around the external Telegram library subprocess."""

import asyncio
import os
import sys
from typing import Optional

import bot_core as core

SEARCH_TIMEOUT_SECONDS = int(os.getenv("SUBPROCESS_SEARCH_TIMEOUT_SECONDS", "45"))
DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("SUBPROCESS_DOWNLOAD_TIMEOUT_SECONDS", "120"))
_installed = False

async def fetch_books_via_userbot(query: str, lang: str = "en"):
    async with core.library_access_lock:
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, "downloader.py", "search", query, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SEARCH_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill(); await proc.communicate()
                core.logger.warning(f"Userbot search timed out after {SEARCH_TIMEOUT_SECONDS}s for query: {query}")
                return []
            if proc.returncode == 0:
                output_str = stdout.decode("utf-8").strip()
                for line in reversed(output_str.splitlines()):
                    line_str = line.strip()
                    if line_str.startswith("["):
                        try:
                            res = core.json.loads(line_str)
                            return res if isinstance(res, list) else []
                        except core.json.JSONDecodeError:
                            pass
            core.logger.warning(f"Userbot search returned non-zero code or failed: {stderr.decode()}")
            return []
        except Exception as exc:
            core.logger.error(f"Subprocess search execution error: {exc}")
            return []

async def execute_downloader_and_send(chat_id: int, book_id: int, book_title: str, book_author: str, saved_file_id: Optional[str], lang: str):
    lock = core.get_download_lock(chat_id, book_id)
    if lock.locked():
        core.logger.info(f"Download already in progress for chat {chat_id}, book {book_id}. Skipping duplicate request.")
        return
    async with lock:
        invalid_authors = {"unknown author", "library bot", "n/a", "none", ""}
        clean_author = book_author.strip() if book_author else ""
        if clean_author.lower() in invalid_authors: clean_author = ""
        if saved_file_id and saved_file_id.strip().startswith("/"):
            queries = [saved_file_id.strip()]
        elif clean_author:
            queries = [f"{book_title.strip()} {clean_author}".strip(), book_title.strip()]
        else:
            queries = [book_title.strip()]
        success = False; last_error = ""
        async with core.library_access_lock:
            for query_str in queries:
                try:
                    proc = await asyncio.create_subprocess_exec(sys.executable, "downloader.py", "download", query_str, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        proc.kill(); await proc.communicate()
                        last_error = f"Downloader timed out after {DOWNLOAD_TIMEOUT_SECONDS}s for query '{query_str}'"
                        core.logger.warning(last_error); continue
                    if proc.returncode == 0:
                        output_lines = stdout.decode().strip().splitlines(); file_path = output_lines[-1] if output_lines else ""
                        if file_path and os.path.exists(file_path):
                            document = core.FSInputFile(file_path)
                            msg = await core.bot.send_document(chat_id=chat_id, document=document, caption=core.t("book_file_caption", lang, title=core.escape_html(book_title)), parse_mode="HTML")
                            file_id = msg.document.file_id if msg.document else ""
                            await core.database.update_book_file_id(core.DATABASE_PATH, book_id, file_id)
                            try: os.remove(file_path)
                            except Exception as exc: core.logger.warning(f"Could not remove local file {file_path}: {exc}")
                            success = True; break
                        last_error = "Downloaded file path not found locally"
                    else:
                        last_error = stderr.decode().strip() or "Downloader process returned non-zero code"
                except Exception as exc:
                    last_error = str(exc); core.logger.error(f"Subprocess execution error for query '{query_str}': {exc}")
        if not success:
            core.logger.error(f"Downloader failed for '{book_title}': {last_error}")
            await core.bot.send_message(chat_id, core.t("file_not_found_in_lib", lang, title=core.escape_html(book_title)), parse_mode="HTML")
            await core.notify_superadmin_error(error_title="Book File Download Failed", error_traceback=f"Last Error: {last_error}", user_id=None, chat_id=chat_id, context_info=f"Book ID: {book_id} | Title: '{book_title}' | Author: '{book_author}' | Queries Tried: {queries}")

def install():
    global _installed
    if _installed: return
    _installed = True
    core.fetch_books_via_userbot = fetch_books_via_userbot
    core.execute_downloader_and_send = execute_downloader_and_send
