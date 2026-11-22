import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config
from database.users_chats_db import db
from info import COMMAND_PREFIXES
from utils import is_chat_or_global_admin, temp
from plugins.core_utils import to_smallcaps, format_watermark, logger


@Client.on_message(filters.group & filters.service, group=8)
async def service_message_cleaner(client: Client, message: Message):
    """Clean group service messages when enabled in group Helpdesk config or global BOT_CONFIG."""
    cfg = await db.get_helpdesk_config(message.chat.id)
    if not (cfg.get("clean_service") or temp.BOT_CONFIG.get("auto_clean_service")):
        return
    if message.new_chat_members and cfg.get("welcome_enabled"):
        return
    if message.left_chat_member and cfg.get("goodbye_enabled"):
        return
    try:
        await message.delete()
    except Exception:
        pass


@Client.on_message(filters.group & filters.command(["clean", "cleanall"], prefixes=COMMAND_PREFIXES))
async def manual_group_clean(client: Client, message: Message):
    caller_id = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0
    if not await is_chat_or_global_admin(client, message.chat.id, user_id=caller_id, sender_chat_id=sender_cid):
        return await message.reply_text(
            f"⚠️ **{to_smallcaps('permission denied')}**\n\n"
            f"• {to_smallcaps('you must be a group administrator to use /clean.')}"
            f"{format_watermark()}"
        )

    count = 15
    if len(message.command) > 1 and message.command[1].isdigit():
        count = min(100, int(message.command[1]))

    status = await message.reply_text(f"🧹 **{to_smallcaps(f'cleaning {count} recent messages...')}**")
    msg_ids = [rid for rid in range(max(1, message.id - count), message.id + 1) if rid != status.id]

    if msg_ids:
        try:
            await client.delete_messages(chat_id=message.chat.id, message_ids=msg_ids)
        except Exception:
            pass

    logger.info("🧹 /clean ᴇxᴇᴄᴜᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴄᴏᴜɴᴛ=%s", message.chat.id, len(msg_ids))
    await status.edit_text(
        f"✦ **{to_smallcaps('group cleaner')}** ✦\n\n"
        f"✅ **{to_smallcaps(f'cleaned {len(msg_ids)} messages successfully!')}**"
        f"{format_watermark()}"
    )
    await asyncio.sleep(4)
    try:
        await status.delete()
    except Exception:
        pass
