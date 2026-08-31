"""Housekeeping for BookVoter lifecycle pins.

Telegram creates a visible service message every time a bot pins something
("BookVoter pinned ..."). When the referenced lifecycle message is later deleted,
that service row turns into noisy "pinned Deleted message" history. This module
tracks only BookVoter-generated pin service messages and removes them, while
leaving unrelated human/group pins untouched.
"""

from __future__ import annotations

from aiogram import BaseMiddleware

import bot_core as core
import runtime_stage_pin as stagepin
import runtime_ux as ux


_installed = False
_original_set_stage_message = None
_original_clear_stage_message = None


async def _ensure_table() -> None:
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookvoter_pin_service_messages (
                chat_id INTEGER NOT NULL,
                service_message_id INTEGER NOT NULL,
                pinned_message_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, service_message_id)
            )
            """
        )
        await db.commit()


async def _remember_service_message(chat_id: int, service_message_id: int, pinned_message_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO bookvoter_pin_service_messages
                (chat_id, service_message_id, pinned_message_id, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (chat_id, service_message_id, pinned_message_id),
        )
        await db.commit()


async def _forget_service_message(chat_id: int, service_message_id: int) -> None:
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute(
            "DELETE FROM bookvoter_pin_service_messages WHERE chat_id = ? AND service_message_id = ?",
            (chat_id, service_message_id),
        )
        await db.commit()


async def cleanup_pin_service_messages(chat_id: int, pinned_message_id: int | None = None) -> int:
    """Delete recorded BookVoter pin-service rows for this chat/stage."""
    await _ensure_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        if pinned_message_id is None:
            async with db.execute(
                "SELECT service_message_id FROM bookvoter_pin_service_messages WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT service_message_id
                FROM bookvoter_pin_service_messages
                WHERE chat_id = ? AND pinned_message_id = ?
                """,
                (chat_id, pinned_message_id),
            ) as cursor:
                rows = await cursor.fetchall()

    deleted = 0
    for row in rows:
        service_id = int(row[0])
        if await ux.safe_delete_message(chat_id, service_id):
            deleted += 1
            await _forget_service_message(chat_id, service_id)
    return deleted


async def _set_stage_message_with_housekeeping(
    chat_id: int,
    message_id: int,
    stage: str,
    *,
    delete_previous: bool,
) -> bool:
    current = await stagepin._get_stage_message(chat_id)
    if current and current.get("message_id") != message_id:
        await cleanup_pin_service_messages(chat_id, int(current["message_id"]))

    result = await _original_set_stage_message(
        chat_id,
        message_id,
        stage,
        delete_previous=delete_previous,
    )

    await cleanup_pin_service_messages(chat_id)
    return result


async def _clear_stage_message_with_housekeeping(chat_id: int, *, delete_message: bool = False) -> bool:
    current = await stagepin._get_stage_message(chat_id)
    if current:
        await cleanup_pin_service_messages(chat_id, int(current["message_id"]))
    result = await _original_clear_stage_message(chat_id, delete_message=delete_message)
    await cleanup_pin_service_messages(chat_id)
    return result


class _PinServiceCleanupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            chat = getattr(event, "chat", None)
            pinned = getattr(event, "pinned_message", None)
            if not chat or chat.type not in (core.ChatType.GROUP, core.ChatType.SUPERGROUP) or not pinned:
                return

            # Telegram does not consistently expose the pin-service actor as the
            # bot that called pinChatMessage. Ownership is therefore determined
            # by the pinned target itself: only a message currently tracked as a
            # BookVoter lifecycle stage qualifies. This cannot match unrelated
            # human/admin pins because their target message id is not in
            # stage_messages for BookVoter.
            current = await stagepin._get_stage_message(chat.id)
            if not current or int(current["message_id"]) != int(pinned.message_id):
                return

            await _remember_service_message(chat.id, event.message_id, pinned.message_id)
            if await ux.safe_delete_message(chat.id, event.message_id):
                await _forget_service_message(chat.id, event.message_id)


def install() -> None:
    global _installed, _original_set_stage_message, _original_clear_stage_message
    if _installed:
        return
    _installed = True

    _original_set_stage_message = stagepin.set_stage_message
    _original_clear_stage_message = stagepin.clear_stage_message

    stagepin.set_stage_message = _set_stage_message_with_housekeeping
    stagepin.clear_stage_message = _clear_stage_message_with_housekeeping

    core.set_stage_message = _set_stage_message_with_housekeeping
    core.clear_stage_message = _clear_stage_message_with_housekeeping
    core.cleanup_pin_service_messages = cleanup_pin_service_messages

    core.router.message.outer_middleware(_PinServiceCleanupMiddleware())
