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
    elif target_channel.startswith("-") and target_channel[1:].isdigit():
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

        # 1. Send /start command to target library bot/chat to activate dialogue if needed
        try:
            await app.send_message(target_channel, "/start")
            # 2. Pause 5-10 seconds after sending /start
            await asyncio.sleep(5)
        except Exception as start_err:
            logging.warning(f"Could not send /start to target {target_channel}: {start_err}")

        # 3. Send query with book title to the library bot
        try:
            await app.send_message(target_channel, title)
            await asyncio.sleep(5)
        except Exception as send_title_err:
            logging.warning(f"Could not send title query to target {target_channel}: {send_title_err}")

        # 4. Search recent messages in target channel/chat for documents or inline buttons
        async for message in app.get_chat_history(target_channel, limit=10):
            # Check if message contains inline keyboard buttons (e.g. selection list from library bot)
            if message.reply_markup and message.reply_markup.inline_keyboard:
                try:
                    # Click the first inline download button
                    first_button = message.reply_markup.inline_keyboard[0][0]
                    if first_button.callback_data:
                        await app.request_callback_answer(
                            chat_id=message.chat.id,
                            message_id=message.id,
                            callback_data=first_button.callback_data
                        )
                        # Pause brief moment for bot to deliver the document
                        await asyncio.sleep(5)
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

        # Fallback check if document arrived in latest messages after button click
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
