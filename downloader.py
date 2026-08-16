import sys
import os
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

async def search_and_download(title: str) -> None:
    if not API_ID or not API_HASH or not SESSION_STRING or not CHANNEL_ID:
        sys.stderr.write("Error: Missing required environment variables (API_ID, API_HASH, SESSION_STRING, CHANNEL_ID).\n")
        sys.exit(1)

    try:
        api_id_int = int(API_ID)
    except ValueError:
        sys.stderr.write("Error: API_ID must be an integer.\n")
        sys.exit(1)

    # Convert channel ID to int if numeric
    target_channel = CHANNEL_ID
    if target_channel.startswith("-100") and target_channel[1:].isdigit():
        target_channel = int(target_channel)
    elif target_channel.isdigit():
        target_channel = int(target_channel)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    app = Client(
        name="userbot_downloader",
        api_id=api_id_int,
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

        # Search messages in target channel
        async for message in app.search_messages(target_channel, query=title):
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
    # Suppress verbose pyrogram logging
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python downloader.py <book_title>\n")
        sys.exit(1)

    book_title = sys.argv[1].strip()
    if not book_title:
        sys.stderr.write("Error: Book title argument cannot be empty.\n")
        sys.exit(1)

    asyncio.run(search_and_download(book_title))

if __name__ == "__main__":
    main()
