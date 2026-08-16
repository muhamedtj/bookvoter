import sys
import os
import re
import json
import asyncio
import logging
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

        results = []

        # Helper: Extract structured book blocks from library response text
        def parse_library_response_blocks(raw_text: str) -> list[dict]:
            blocks = []
            # Split text by blank lines
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

                # Guaranteed structure from the library:
                #   line[0]      = "Title - lang"   (always)
                #   line[1..N-2] = description       (optional, may be absent)
                #   line[N-1]    = "Author Name"     (always if N >= 2)
                #   last line containing /downloadXXX = download command
                #
                # Pull the download line out first, then work with the rest.
                dl_cmd = ""
                content_lines = []
                for l in filtered_lines:
                    cmd_m = re.search(r"/(?:download|get|dl|d)[a-zA-Z0-9_]+", l)
                    if cmd_m and not dl_cmd:
                        dl_cmd = cmd_m.group(0)
                        # Keep any text on the line before the command
                        remainder = l[:cmd_m.start()].strip()
                        if remainder:
                            content_lines.append(remainder)
                    else:
                        content_lines.append(l)

                if not content_lines:
                    continue

                # --- Title: first content line, strip language tag (e.g. "- ru") ---
                raw_title = content_lines[0]
                clean_title = re.sub(
                    r"\s*[-–—]\s*(?:ru|en|de|fr|es|it|uk|pl|litres|pdf|epub)\b.*$",
                    "", raw_title, flags=re.IGNORECASE
                ).strip()
                if not clean_title:
                    clean_title = raw_title

                # --- Author: last content line (second-to-last in original block) ---
                author = content_lines[-1].strip() if len(content_lines) >= 2 else ""

                # Sanity-check: reject if it looks like a system/service string
                if author and any(w in author.lower() for w in ["скачать", "найдено", "запрос"]):
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

        def try_parse_response(message) -> list[dict]:
            """Try to extract book results from a single message."""
            found = []
            msg_text = message.text or message.caption or ""

            if any(w in msg_text.lower() for w in ["/start", "добро пожаловать", "приветствую"]):
                return found

            # Case A: Structured text with download commands
            if "скачать книгу:" in msg_text.lower() or "найдено:" in msg_text.lower() or "/download" in msg_text.lower():
                parsed = parse_library_response_blocks(msg_text)
                if parsed:
                    return parsed[:5]

            # Case B: Inline keyboard buttons
            if message.reply_markup and message.reply_markup.inline_keyboard:
                for row in message.reply_markup.inline_keyboard[:5]:
                    for btn in row:
                        btn_text = btn.text.strip()
                        parts = btn_text.split(" - ", 1) if " - " in btn_text else (btn_text.split(" — ", 1) if " — " in btn_text else [btn_text, ""])
                        author_val = parts[1].strip() if len(parts) > 1 and parts[1] else "Unknown Author"
                        found.append({
                            "title": parts[0].strip(),
                            "author": author_val,
                            "genre": "",
                            "download_cmd": btn.callback_data or "",
                            "raw_label": btn_text
                        })
                return found

            # Case C: Direct document
            if message.document:
                file_name = message.document.file_name or query_title
                base_name = os.path.splitext(file_name)[0]
                found.append({
                    "title": base_name,
                    "author": "Unknown Author",
                    "genre": "",
                    "download_cmd": "",
                    "raw_label": file_name
                })

            return found

        # 3. Poll for library response — check every 2 seconds, up to 20 seconds total.
        #    The library can be slow, especially for large result sets (200+ books).
        MAX_WAIT_SEC = 20
        POLL_INTERVAL = 2
        elapsed = 0

        while elapsed < MAX_WAIT_SEC and not results:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            async for message in app.get_chat_history(target_channel, limit=10):
                if message.id <= query_msg.id:
                    break  # Reached messages older than our query — stop scanning
                parsed = try_parse_response(message)
                if parsed:
                    results = parsed
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

        # If title is already a direct download command (e.g. /download338051 or /get_123)
        if title.startswith("/"):
            try:
                query_msg = await app.send_message(target_channel, title)
                await asyncio.sleep(4)
            except Exception as send_cmd_err:
                logging.warning(f"Could not send direct download command to target {target_channel}: {send_cmd_err}")

            q_id = query_msg.id if query_msg else 0
            async for message in app.get_chat_history(target_channel, limit=10):
                if message.id <= q_id:
                    continue
                if message.document:
                    file_name = message.document.file_name or ""
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext in [".epub", ".pdf"]:
                        downloaded_path = await app.download_media(
                            message,
                            file_name=os.path.join(DOWNLOAD_DIR, file_name)
                        )
                        break

            if downloaded_path and os.path.exists(downloaded_path):
                abs_path = os.path.abspath(downloaded_path)
                print(abs_path)
                sys.exit(0)

        query_msg = None
        try:
            query_msg = await app.send_message(target_channel, title)
            await asyncio.sleep(4)
        except Exception as send_title_err:
            logging.warning(f"Could not send title query to target {target_channel}: {send_title_err}")

        q_id = query_msg.id if query_msg else 0

        async for message in app.get_chat_history(target_channel, limit=15):
            if message.id <= q_id:
                continue

            msg_text = message.text or message.caption or ""
            if any(w in msg_text.lower() for w in ["/start", "добро пожаловать", "приветствую"]):
                continue

            # Case A: Reply has inline button for download/selection
            if message.reply_markup and message.reply_markup.inline_keyboard:
                try:
                    first_button = message.reply_markup.inline_keyboard[0][0]
                    if first_button.callback_data:
                        await app.request_callback_answer(
                            chat_id=message.chat.id,
                            message_id=message.id,
                            callback_data=first_button.callback_data
                        )
                        await asyncio.sleep(4)
                except Exception as cb_err:
                    logging.warning(f"Failed to trigger inline button: {cb_err}")

            # Case B: Message contains text list with download command (e.g., /download682541 or /download_123 or /get_456 or /d_789)
            if msg_text and not message.document:
                # Find download command pattern like /download682541
                cmd_match = re.search(r"/(?:download|get|dl|d)[a-zA-Z0-9_]+", msg_text)
                if cmd_match:
                    dl_cmd = cmd_match.group(0)
                    try:
                        cmd_msg = await app.send_message(target_channel, dl_cmd)
                        await asyncio.sleep(4)
                    except Exception as cmd_err:
                        logging.warning(f"Failed to send download command {dl_cmd}: {cmd_err}")

            # Check direct document attached to message
            if message.document:
                file_name = message.document.file_name or ""
                ext = os.path.splitext(file_name)[1].lower()
                if ext in [".epub", ".pdf"]:
                    downloaded_path = await app.download_media(
                        message,
                        file_name=os.path.join(DOWNLOAD_DIR, file_name)
                    )
                    break

        if not downloaded_path:
            async for message in app.get_chat_history(target_channel, limit=5):
                if message.document:
                    file_name = message.document.file_name or ""
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext in [".epub", ".pdf"]:
                        downloaded_path = await app.download_media(
                            message,
                            file_name=os.path.join(DOWNLOAD_DIR, file_name)
                        )
                        break

        if downloaded_path and os.path.exists(downloaded_path):
            abs_path = os.path.abspath(downloaded_path)
            print(abs_path)
            sys.exit(0)
        else:
            sys.stderr.write(f"File not found in library for title: {title}\n")
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
