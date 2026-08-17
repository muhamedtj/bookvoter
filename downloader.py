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


def parse_library_response_blocks(raw_text: str, query_title: str = "") -> list[dict]:
    blocks = []
    if not raw_text:
        return blocks

    # Split text by blank lines
    raw_blocks = re.split(r"\n\s*\n", raw_text)

    # Further split any block if it contains multiple download commands
    final_raw_blocks = []
    for rb in raw_blocks:
        dl_matches = list(re.finditer(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", rb))
        if len(dl_matches) > 1:
            sub_lines = rb.splitlines()
            current_sub = []
            for line in sub_lines:
                current_sub.append(line)
                if re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", line):
                    final_raw_blocks.append("\n".join(current_sub))
                    current_sub = []
            if current_sub:
                final_raw_blocks.append("\n".join(current_sub))
        else:
            final_raw_blocks.append(rb)

    clean_query = query_title.strip().lower()

    for block in final_raw_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        # Skip header/footer lines or system prompts
        filtered_lines = [
            l for l in lines
            if not any(w in l.lower() for w in [
                "/start", "добро пожаловать", "приветствую", "найдено:",
                "мы нашли именно то", "книга по вашему запросу", "поиск...", "идет поиск",
                "обрабатываем", "подождите", "результаты поиска", "выберите"
            ])
        ]
        if not filtered_lines:
            continue

        # Find download command in block
        dl_cmd = ""
        non_cmd_lines = []
        for l in filtered_lines:
            cmd_m = re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", l)
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

            # Skip if title itself is just a search status line
            clean_title_lower = clean_title.lower()
            if clean_title_lower.startswith("поиск") or clean_title_lower.startswith("обрат") or clean_title_lower == "результаты поиска":
                if len(non_cmd_lines) > 1:
                    non_cmd_lines = non_cmd_lines[1:]
                    clean_title = re.sub(r"\s*[-–—]?\s*(?:ru|en|litres|pdf|epub)\b.*$", "", non_cmd_lines[0], flags=re.IGNORECASE).strip()
                else:
                    continue

            author = ""
            if len(non_cmd_lines) >= 3:
                author = non_cmd_lines[-1]
            elif len(non_cmd_lines) == 2:
                author = non_cmd_lines[1]
            elif " — " in clean_title or " - " in clean_title:
                parts = re.split(r"\s+[—\-]\s+", clean_title, 1)
                if len(parts) == 2:
                    clean_title, author = parts[0].strip(), parts[1].strip()

            # Clean up invalid author strings
            if author and (
                author.startswith("(")
                or any(w in author.lower() for w in ["скачать", "найдено", "размер", "язык", "страниц", "книга", "автор"])
            ):
                author = ""

            # Check if block is just repeating the query without a download command or real author
            if clean_query and clean_title.lower() == clean_query and not author and not dl_cmd:
                continue

            final_author = author if author else "Unknown Author"

            blocks.append({
                "title": clean_title,
                "author": final_author,
                "genre": "",
                "download_cmd": dl_cmd,
                "raw_label": f"{clean_title} — {final_author}" if final_author != "Unknown Author" else clean_title
            })

    return blocks


def parse_library_response(
    msg_text: str = "",
    reply_markup: Any = None,
    query_title: str = "",
    document: Any = None
) -> list[dict]:
    # 1. Direct document
    if document:
        file_name = getattr(document, "file_name", None) or "document"
        base_name = os.path.splitext(file_name)[0]
        return [{
            "title": base_name,
            "author": "Unknown Author",
            "genre": "",
            "download_cmd": "",
            "raw_label": file_name
        }]

    text_lower = msg_text.lower().strip() if msg_text else ""

    # Skip system welcome messages
    if any(w in text_lower for w in ["/start", "добро пожаловать", "приветствую"]):
        return []

    # 2. Try parsing text blocks
    blocks = parse_library_response_blocks(msg_text, query_title)

    # Extract keyboard rows if present
    keyboard_rows = []
    if reply_markup:
        if hasattr(reply_markup, "inline_keyboard"):
            keyboard_rows = reply_markup.inline_keyboard
        elif isinstance(reply_markup, list):
            keyboard_rows = reply_markup

    # If text blocks exist, attach download commands from buttons if missing
    if blocks:
        if len(blocks) == 1 and not blocks[0]["download_cmd"] and keyboard_rows:
            for row in keyboard_rows:
                for btn in row:
                    cb = getattr(btn, "callback_data", "") or getattr(btn, "url", "") or ""
                    txt = getattr(btn, "text", "") or ""
                    cmd_m = re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", cb) or re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", txt)
                    if cmd_m:
                        blocks[0]["download_cmd"] = cmd_m.group(0)
                        break
                    elif cb and (cb.startswith("/") or "download" in cb.lower() or "dl" in cb.lower()):
                        blocks[0]["download_cmd"] = cb
                        break
        valid_blocks = []
        clean_q = query_title.strip().lower()
        for b in blocks:
            is_generic_title = clean_q and b["title"].lower() == clean_q
            if is_generic_title and b["author"] == "Unknown Author" and not b["download_cmd"]:
                continue
            valid_blocks.append(b)
        return valid_blocks

    # 3. If no text blocks, parse inline buttons if present
    results = []
    clean_query = query_title.strip().lower()

    if keyboard_rows:
        for row in keyboard_rows:
            for btn in row:
                btn_text = (getattr(btn, "text", "") or "").strip()
                cb_data = getattr(btn, "callback_data", "") or getattr(btn, "url", "") or ""

                btn_lower = btn_text.lower()

                # Ignore intermediate buttons that repeat query_title or are generic prompts
                if clean_query and btn_lower == clean_query:
                    continue
                if any(p in btn_lower for p in ["поиск...", "искать", "назад", "далее", "cancel", "отмена", "search"]):
                    continue

                # Ignore sub-search or navigation callback_data
                if (cb_data.startswith("search") or cb_data.startswith("page") or cb_data.startswith("find")) and not ("download" in cb_data.lower() or cb_data.startswith("/")):
                    continue

                # Parse book title & author from button text
                parts = re.split(r"\s+[—\-]\s+", btn_text, 1) if (" — " in btn_text or " - " in btn_text) else [btn_text, ""]
                title_val = parts[0].strip()
                author_val = parts[1].strip() if len(parts) > 1 and parts[1] else "Unknown Author"

                cmd_match = re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", cb_data) or re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", btn_text)
                dl_cmd = cmd_match.group(0) if cmd_match else (cb_data if (cb_data.startswith("/") or "download" in cb_data.lower()) else "")

                if clean_query and title_val.lower() == clean_query and author_val == "Unknown Author" and not dl_cmd:
                    continue

                results.append({
                    "title": title_val,
                    "author": author_val,
                    "genre": "",
                    "download_cmd": dl_cmd,
                    "raw_label": btn_text
                })

    return results


def parse_message_for_books(message: Any, query_title: str = "") -> list[dict]:
    msg_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    reply_markup = getattr(message, "reply_markup", None)
    document = getattr(message, "document", None)

    return parse_library_response(
        msg_text=msg_text,
        reply_markup=reply_markup,
        query_title=query_title,
        document=document
    )


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

        # 2. Send query directly with book title and save query_msg.id
        query_msg = await app.send_message(target_channel, query_title)

        results = []
        start_time = asyncio.get_running_loop().time()
        timeout_seconds = 30
        poll_interval = 0.5

        # 3. Poll new/updated library messages for up to 30 seconds
        while asyncio.get_running_loop().time() - start_time < timeout_seconds:
            candidate_results = []
            try:
                async for message in app.get_chat_history(target_channel, limit=15):
                    if message.id <= query_msg.id:
                        continue

                    msg_books = parse_message_for_books(message, query_title)
                    if msg_books:
                        for b in msg_books:
                            if not any(
                                (r.get("download_cmd") and r["download_cmd"] == b.get("download_cmd"))
                                or (r["title"].lower() == b["title"].lower() and r["author"].lower() == b["author"].lower())
                                for r in candidate_results
                            ):
                                candidate_results.append(b)
            except Exception as poll_err:
                logging.warning(f"Error while polling chat history: {poll_err}")

            if candidate_results:
                results = candidate_results[:5]
                break

            await asyncio.sleep(poll_interval)

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
            cmd_match = re.search(r"/(?:download|get|dl|d)_?[a-zA-Z0-9_]+", msg_text)
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
