import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from database.users_chats_db import db
from info import SUPPORT_CHAT
from utils import temp, is_admin_sync

logger = logging.getLogger("GokuBanned")


async def banned_users(_, client, message: Message):
    return bool(
        message.from_user is not None
        and message.from_user.id in temp.BANNED_USERS
        and not is_admin_sync(message.from_user.id)
    )


banned_user = filters.create(banned_users)


async def disabled_chat(_, client, message: Message):
    return bool(
        message.chat is not None
        and message.chat.id in temp.BANNED_CHATS
    )


disabled_group = filters.create(disabled_chat)


async def maintenance_mode_filter(_, client, message: Message):
    if not temp.MAINTENANCE_MODE:
        return False
    uid = message.from_user.id if message.from_user else 0
    return not is_admin_sync(uid)


maintenance_guard = filters.create(maintenance_mode_filter)


@Client.on_message(filters.private & banned_user & filters.incoming, group=-2)
async def ban_reply(bot: Client, message: Message):
    if not message.from_user:
        return
    ban = await db.get_ban_status(message.from_user.id)
    reason = ban.get("ban_reason", "ɴᴏ ʀᴇᴀsᴏɴ")
    logger.warning("🚫 ʙʟᴏᴄᴋᴇᴅ ʙᴀɴɴᴇᴅ ᴜsᴇʀ › ᴜsᴇʀ=%s ʀᴇᴀsᴏɴ=%s", message.from_user.id, reason)
    await message.reply(
        f"🚫 <b>sᴏʀʀʏ, ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴍᴇ!</b>\n"
        f"📌 <b>ʀᴇᴀsᴏɴ:</b> <code>{reason}</code>\n"
        f"🆘 <b>sᴜᴘᴘᴏʀᴛ:</b> @{SUPPORT_CHAT}",
        parse_mode=enums.ParseMode.HTML,
    )
    message.stop_propagation()


@Client.on_message(filters.private & maintenance_guard & filters.incoming, group=-2)
async def maintenance_reply(bot: Client, message: Message):
    if not message.from_user:
        return
    logger.info("🛠️ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ ʙʟᴏᴄᴋᴇᴅ ᴘᴍ › ᴜsᴇʀ=%s", message.from_user.id)
    await message.reply(
        "🛠️ <b>ʙᴏᴛ ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴜɴᴅᴇʀ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ!</b> ⚡\n\n"
        "<i>ᴏᴜʀ ᴀᴅᴍɪɴs ᴀʀᴇ ᴘᴇʀғᴏʀᴍɪɴɢ sʏsᴛᴇᴍ ᴜᴘɢʀᴀᴅᴇs. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ sʜᴏʀᴛʟʏ.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🆘 sᴜᴘᴘᴏʀᴛ ᴄʜᴀᴛ", url=f"https://t.me/{SUPPORT_CHAT}")]
        ]),
        parse_mode=enums.ParseMode.HTML,
    )
    message.stop_propagation()


@Client.on_message(filters.group & disabled_group & filters.incoming, group=-2)
async def grp_bd(bot: Client, message: Message):
    buttons = [[InlineKeyboardButton("🆘 sᴜᴘᴘᴏʀᴛ", url=f"https://t.me/{SUPPORT_CHAT}")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    vazha = await db.get_chat(message.chat.id)
    reason = vazha.get("reason", "ɴᴏ ʀᴇᴀsᴏɴ") if isinstance(vazha, dict) else "ɴᴏ ʀᴇᴀsᴏɴ"
    logger.warning("🚫 ᴅɪsᴀʙʟᴇᴅ ɢʀᴏᴜᴘ ᴛʀɪɢɢᴇʀᴇᴅ › ᴄʜᴀᴛ=%s ʀᴇᴀsᴏɴ=%s", message.chat.id, reason)
    k = await message.reply(
        text=(
            f"🚫 <b>ᴄʜᴀᴛ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ</b> 🐞\n\n"
            f"ᴍʏ ᴀᴅᴍɪɴs ʜᴀᴠᴇ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴍᴇ ғʀᴏᴍ ᴡᴏʀᴋɪɴɢ ʜᴇʀᴇ! "
            f"ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ ғᴏʀ ᴍᴏʀᴇ ᴅᴇᴛᴀɪʟs.\n"
            f"📌 <b>ʀᴇᴀsᴏɴ:</b> <code>{reason}</code>"
        ),
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        await k.pin()
    except Exception:
        pass
    try:
        await bot.leave_chat(message.chat.id)
    except Exception:
        pass
    message.stop_propagation()
