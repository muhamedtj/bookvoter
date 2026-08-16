import sys
import os
import re
import json
import asyncio
import logging
from typing import Any
from dotenv import load_dotenv
from pyrogram import Client

# Load environment variables
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHANNEL_ID = os.getenv("CHANNEL_ID")

DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")

def get_target_channel():
    if not CHANNEL_ID:
        return None
    target = CHANNEL_ID.strip()
    if target.startswith("-100") and target[1:].isdigit():
        return int(target)
    elif target.isdigit():
        return int(target)
    elif target.startswith("-") and target[1:].isdigit():
        return int(target)
    return target

async def search_options_via_userbot(query_title: str) -> None:
    if not API_ID or not API_HASH or not SESSION_STRING or not CHANNEL_ID:
        sys.stderr.write("Error: Missing required environment variables (API_ID, API_HASH, SESSION_STRING, CHANNEL_ID).\n")
        sys.exit(1)

    target_channel = get_target_channel()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    app = Client(
        name="userbot_searcher",
        api_id=int(API_ID),
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    )

    try:
        await app.start()
    except Exception as e:
        sys.stderr.write(f"Error starting Pyrogram client: {e}\n")
        sys.exit(1)

    try:
        # 1. Check if chat history is completely empty; send /start only once on initial session setup
        history_count = 0
        async for _ in app.get_chat_history(target_channel, limit=1):
            history_count += 1

        if history_count == 0:
            try:
                await app.send_message(target_channel, "/start")
                await asyncio.sleep(2)
            except Exception as ex:
                logging.warning(f"Could not send /start: {ex}")

        # 2. Send query directly with book title
        query_msg = await app.send_message(target_channel, query_title)
        await asyncio.sleep(3)

        results = []

        # Helper: Extract structured book blocks from library response text
        def parse_library_response_blocks(raw_text: str) -> list[dict]:
            blocks = []
            # Split text by blank lines or download commands
            raw_blocks = re.split(r"\n\s*\n", raw_text)
            for block in raw_blocks:
                lines = [line.strip() for line in block.splitlines() if line.strip()]
                # Skip header/footer lines or system prompts
                filtered_lines = [
                    l for l in lines
                    if not any(w in l.lower() for w in [
                        "/start", "добро пожаловать", "приветствую", "найдено:",
                        "мы нашли именно то", "книга по вашему запросу", "поиск...", "идет поиск"
                    ])
                ]
                if not filtered_lines:
                    continue

                # Find download command in block
                dl_cmd = ""
                non_cmd_lines = []
                for l in filtered_lines:
                    cmd_m = re.search(r"/(?:download|get|dl|d)_?[a_zA_Z0_9_]+", l)
                    if cmd_m:
                        dl_cmd = cmd_m.group(0)
                    else:
                        non_cmd_lines.append(l)

                if non_cmd_lines:
                    raw_title = non_cmd_lines[0]
                    # Clean title by removing language suffixes like '- ru', '– ru', '[ru]', etc.
                    clean_title = re.sub(r"\s*[-–—]?\s*(?:ru|en|litres|pdf|epub)\b.*$", "", raw_title, flags=re.IGNORECASE).strip()
                    if not clean_title:
                        clean_title = raw_title

                    author = ""
                    if len(non_cmd_lines) >= 2:
                        author = non_cmd_lines[-1]
                    elif " — " in clean_title or " - " in clean_title:
                        parts = re.split(r"\s+[—\-]\s+", clean_title, 1)
                        if len(parts) == 2:
                            clean_title, author = parts[0].strip(), parts[1].strip()

                    # Filter invalid author strings
                    if author and (author.startswith("(") or "скачать" in author.lower() or "найдено" in author.lower()):
                        author = ""

                    final_author = author if author else "Unknown Author"

                    blocks.append({
                        "title": clean_title,
                        "author": final_author,
                        "genre": "",
                        "download_cmd": dl_cmd,
                        "raw_label": f"{clean_title} — {final_author}" if final_author != "Unknown Author" else clean_title
                    })

            return blocks

        # 3. Intercept reply from library bot, filtering for search responses
        async for message in app.get_chat_history(target_channel, limit=15):
            if message.id <= query_msg.id:
                continue

            msg_text = message.text or message.caption or ""
            if any(w in msg_text.lower() for w in ["/start", "добро пожаловать", "приветствую"]):
                continue

            # Case A: Reply contains structured text blocks with download commands (like screenshot)
            if "скачать книгу:" in msg_text.lower() or "найдено:" in msg_text.lower() or "/download" in msg_text.lower():
                parsed_blocks = parse_library_response_blocks(msg_text)
                if parsed_blocks:
                    results = parsed_blocks[:5]
                    break

            # Case B: Reply contains inline buttons
            if message.reply_markup and message.reply_markup.inline_keyboard:
                for row in message.reply_markup.inline_keyboard[:5]:
                    for btn in row:
                        btn_text = btn.text.strip()
                        parts = btn_text.split(" - ", 1) if " - " in btn_text else (btn_text.split(" — ", 1) if " — " in btn_text else [btn_text, ""])
                        author_val = parts[1].strip() if len(parts) > 1 and parts[1] else "Unknown Author"
                        results.append({
                            "title": parts[0].strip(),
                            "author": author_val,
                            "genre": "",
                            "download_cmd": btn.callback_data or "",
                            "raw_label": btn_text
                        })
                if results:
                    break

            # Case C: Direct document returned
            if message.document:
                file_name = message.document.file_name or query_title
                base_name = os.path.splitext(file_name)[0]
                results.append({
                    "title": base_name,
                    "author": "Unknown Author",
                    "genre": "",
                    "download_cmd": "",
                    "raw_label": file_name
                })
                if results:
                    break

        if not results:
            # Fallback using query_title
            results.append({
                "title": query_title.title(),
                "author": "Unknown Author",
                "genre": "",
                "raw_label": query_title.title()
            })

        print(json.dumps(results, ensure_ascii=False))
        sys.exit(0)

    except Exception as e:
        sys.stderr.write(f"Error during search: {e}\n")
        sys.exit(1)
    finally:
        await app.stop()


async def wait_for_document_or_response(app: Client, target_channel: Any, request_msg_id: int, timeout_seconds: int = 30):
    """Poll for new messages after request_msg_id until a document or relevant text/markup response is received."""
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout_seconds:
        async for message in app.get_chat_history(target_channel, limit=10):
            if message.id <= request_msg_id:
                continue
            msg_text = message.text or message.caption or ""
            if any(w in msg_text.lower() for w in ["/start", "добро пожаловать", "приветствую"]):
                continue
            return message
        await asyncio.sleep(1)
    return None

def select_best_button(keyboard_rows) -> Any:
    """Select format button with priority: epub > fb2 > mobi > pdf > other."""
    candidates = []
    for row in keyboard_rows:
        for btn in row:
            txt = btn.text.lower() if btn.text else ""
            cb = btn.callback_data or ""
            candidates.append((btn, txt, cb))

    if not candidates:
        return None

    priorities = ["epub", "fb2", "mobi", "pdf"]
    for prio in priorities:
        for btn, txt, cb in candidates:
            if prio in txt or prio in cb.lower():
                return btn

    return candidates[0][0]

async def search_and_download(title: str) -> None:
    if not API_ID or not API_HASH or not SESSION_STRING or not CHANNEL_ID:
        sys.stderr.write("Error: Missing required environment variables (API_ID, API_HASH, SESSION_STRING, CHANNEL_ID).\n")
        sys.exit(1)

    target_channel = get_target_channel()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    app = Client(
        name="userbot_downloader",
        api_id=int(API_ID),
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    )

    try:
        await app.start()
    except Exception as e:
        sys.stderr.write(f"Error starting Pyrogram client: {e}\n")
        sys.exit(1)

    try:
        downloaded_path = None

        # Send /start only if history is completely empty
        history_count = 0
        async for _ in app.get_chat_history(target_channel, limit=1):
            history_count += 1

        if history_count == 0:
            try:
                await app.send_message(target_channel, "/start")
                await asyncio.sleep(2)
            except Exception as start_err:
                logging.warning(f"Could not send /start to target {target_channel}: {start_err}")

        # Case 1: Direct command starting with '/'
        if title.startswith("/"):
            try:
                cmd_msg = await app.send_message(target_channel, title)
            except Exception as send_cmd_err:
                sys.stderr.write(f"Failed to send direct download command '{title}': {send_cmd_err}\n")
                sys.exit(1)

            resp_msg = await wait_for_document_or_response(app, target_channel, cmd_msg.id, timeout_seconds=30)
            if resp_msg and resp_msg.document:
                file_name = resp_msg.document.file_name or "downloaded_book"
                downloaded_path = await app.download_media(
                    resp_msg,
                    file_name=os.path.join(DOWNLOAD_DIR, file_name)
                )

            if downloaded_path and os.path.exists(downloaded_path):
                print(os.path.abspath(downloaded_path))
                sys.exit(0)
            else:
                sys.stderr.write(f"Timeout or file not received for direct command: {title}\n")
                sys.exit(1)

        # Case 2: General text query search
        try:
            query_msg = await app.send_message(target_channel, title)
        except Exception as send_title_err:
            sys.stderr.write(f"Failed to send query '{title}': {send_title_err}\n")
            sys.exit(1)

        resp_msg = await wait_for_document_or_response(app, target_channel, query_msg.id, timeout_seconds=30)

        if not resp_msg:
            sys.stderr.write(f"Timeout waiting for response to query: {title}\n")
            sys.exit(1)

        # Check inline buttons format choice
        if resp_msg.reply_markup and resp_msg.reply_markup.inline_keyboard:
            best_btn = select_best_button(resp_msg.reply_markup.inline_keyboard)
            if best_btn and best_btn.callback_data:
                cb_msg_id = resp_msg.id
                await app.request_callback_answer(
                    chat_id=resp_msg.chat.id,
                    message_id=resp_msg.id,
                    callback_data=best_btn.callback_data
                )
                resp_msg = await wait_for_document_or_response(app, target_channel, cb_msg_id, timeout_seconds=30)

        # Check text response with download command
        msg_text = (resp_msg.text or resp_msg.caption or "") if resp_msg else ""
        if resp_msg and not resp_msg.document and msg_text:
            cmd_match = re.search(r"/(?:download|get|dl|d)_?[a_zA_Z0_9_]+", msg_text)
            if cmd_match:
                dl_cmd = cmd_match.group(0)
                sub_cmd_msg = await app.send_message(target_channel, dl_cmd)
                resp_msg = await wait_for_document_or_response(app, target_channel, sub_cmd_msg.id, timeout_seconds=30)

        # Process document
        if resp_msg and resp_msg.document:
            file_name = resp_msg.document.file_name or f"{title}.epub"
            downloaded_path = await app.download_media(
                resp_msg,
                file_name=os.path.join(DOWNLOAD_DIR, file_name)
            )

        if downloaded_path and os.path.exists(downloaded_path):
            print(os.path.abspath(downloaded_path))
            sys.exit(0)
        else:
            sys.stderr.write(f"File not found in library for query: {title}\n")
            sys.exit(1)

    except Exception as e:
        sys.stderr.write(f"Error during search or download: {e}\n")
        sys.exit(1)
    finally:
        await app.stop()


def main():
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python downloader.py <search|download> <book_title> OR python downloader.py <book_title>\n")
        sys.exit(1)

    if len(sys.argv) >= 3 and sys.argv[1].lower() in ["search", "download"]:
        mode = sys.argv[1].lower()
        title = sys.argv[2].strip()
        if mode == "search":
            asyncio.run(search_options_via_userbot(title))
        else:
            asyncio.run(search_and_download(title))
    else:
        title = sys.argv[1].strip()
        asyncio.run(search_and_download(title))

if __name__ == "__main__":
    main()
