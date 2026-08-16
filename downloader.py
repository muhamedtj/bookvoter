import sys
import os
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
        # 1. Send /start to library bot if needed
        try:
            await app.send_message(target_channel, "/start")
            await asyncio.sleep(2)
        except Exception as ex:
            logging.warning(f"Could not send /start: {ex}")

        # 2. Send query with book title
        query_msg = await app.send_message(target_channel, query_title)
        await asyncio.sleep(3)

        results = []

        # 3. Intercept reply from library bot
        async for message in app.get_chat_history(target_channel, limit=10):
            if message.id <= query_msg.id:
                continue

            # Case A: Reply contains inline buttons (multiple book choices)
            if message.reply_markup and message.reply_markup.inline_keyboard:
                for row in message.reply_markup.inline_keyboard[:5]:
                    for btn in row:
                        btn_text = btn.text.strip()
                        # Extract title/author or format clean label
                        results.append({
                            "title": btn_text,
                            "author": "Library Bot",
                            "genre": "General",
                            "raw_label": btn_text
                        })
                break

            # Case B: Reply is text message listing search results
            if message.text and not message.from_user.is_self:
                lines = [line.strip() for line in message.text.splitlines() if line.strip()]
                for line in lines[:5]:
                    results.append({
                        "title": line[:50],
                        "author": "Library Bot",
                        "genre": "General",
                        "raw_label": line[:50]
                    })
                if results:
                    break

            # Case C: Direct document returned
            if message.document:
                file_name = message.document.file_name or query_title
                results.append({
                    "title": os.path.splitext(file_name)[0],
                    "author": "Library Bot",
                    "genre": "General",
                    "raw_label": file_name
                })
                break

        if not results:
            # Default single entry fallback using query_title
            results.append({
                "title": query_title.title(),
                "author": "Library Bot",
                "genre": "General",
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

        try:
            await app.send_message(target_channel, "/start")
            await asyncio.sleep(2)
        except Exception as start_err:
            logging.warning(f"Could not send /start to target {target_channel}: {start_err}")

        try:
            await app.send_message(target_channel, title)
            await asyncio.sleep(4)
        except Exception as send_title_err:
            logging.warning(f"Could not send title query to target {target_channel}: {send_title_err}")

        async for message in app.get_chat_history(target_channel, limit=10):
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
