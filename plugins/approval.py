import os
from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config
from info import COMMAND_PREFIXES
from utils import is_chat_or_global_admin
from plugins.core_utils import (
    execute_with_floodwait,
    format_watermark,
    logger,
    safe_edit_text,
    safe_reply_text,
    to_smallcaps,
)


async def _approve_all_with_userbot(chat_id: int) -> bool:
    session_str = (
        os.environ.get("USER_SESSION", "").strip()
        or os.environ.get("STRING_SESSION", "").strip()
    )
    if not session_str:
        return False
    ub = Client(
        name="MMW_APPROVAL_UB",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=session_str,
        in_memory=True,
        no_updates=True,
    )
    async with ub:
        await execute_with_floodwait(ub.approve_all_chat_join_requests, chat_id=chat_id)
    return True


@Client.on_message(
    filters.command(["approval", "approveall"], prefixes=COMMAND_PREFIXES)
    & (filters.group | filters.channel)
)
async def approve_all_pending_joins(client: Client, message: Message):
    chat_id = message.chat.id
    uid = message.from_user.id if message.from_user else 0
    scid = message.sender_chat.id if message.sender_chat else 0

    if not await is_chat_or_global_admin(client, chat_id, user_id=uid, sender_chat_id=scid):
        return await safe_reply_text(
            message,
            f"🚫 **{to_smallcaps('only chat/channel admins or bot admins can run /approval!')}**"
            f"{format_watermark()}",
        )

    status = await safe_reply_text(
        message,
        f"⏳ **{to_smallcaps('approving all pending join requests...')}**",
    )

    try:
        used_ub = await _approve_all_with_userbot(chat_id)
        if not used_ub:
            await execute_with_floodwait(client.approve_all_chat_join_requests, chat_id=chat_id)

        mode_lbl = "ᴜsᴇʀʙᴏᴛ" if used_ub else "ʙᴏᴛ"
        logger.info(
            "🤝 /approval ᴄᴏᴍᴘʟᴇᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴍᴏᴅᴇ=%s ʙʏ=%s",
            chat_id,
            mode_lbl,
            uid or scid,
        )
        await safe_edit_text(
            status,
            f"✦ **{to_smallcaps('all pending join requests approved')}** ✦ ✅\n\n"
            f"• 💬 **{to_smallcaps('chat')}** : `{message.chat.title or chat_id}`\n"
            f"• ⚡ **{to_smallcaps('engine')}** : `{mode_lbl}`"
            f"{format_watermark()}",
        )
    except Exception as exc:
        logger.warning("⚠️ /approval ғᴀɪʟᴇᴅ › ᴄʜᴀᴛ=%s ᴇʀʀ=%s", chat_id, exc)
        await safe_edit_text(
            status,
            f"❌ **{to_smallcaps('failed to approve pending requests')}** : `{exc}`\n\n"
            f"💡 {to_smallcaps('make sure the userbot/bot is admin with invite users permission.')}"
            f"{format_watermark()}",
        )
