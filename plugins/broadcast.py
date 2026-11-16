import time
import datetime
import asyncio
import logging
from typing import Dict
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from info import ADMINS, COMMAND_PREFIXES
from database.users_chats_db import db
from utils import broadcast_messages, is_admin_sync, make_progress_bar, safe_edit_message

logger = logging.getLogger("MMWBroadcast")

_ACTIVE_BROADCASTS: Dict[int, bool] = {}


def _cancel_kb(bcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 ᴄᴀɴᴄᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ", callback_data=f"bcast_cancel#{bcast_id}")
    ]])


@Client.on_callback_query(filters.regex(r"^bcast_cancel#(\d+)$"))
async def cancel_broadcast_cb(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)
    bid = int(query.matches[0].group(1))
    if bid in _ACTIVE_BROADCASTS:
        _ACTIVE_BROADCASTS[bid] = False
        await query.answer("🛑 ᴄᴀɴᴄᴇʟʟɪɴɢ ʙʀᴏᴀᴅᴄᴀsᴛ...", show_alert=True)
    else:
        await query.answer("ℹ️ ʙʀᴏᴀᴅᴄᴀsᴛ ᴀʟʀᴇᴀᴅʏ ғɪɴɪsʜᴇᴅ.", show_alert=True)


@Client.on_message(filters.command("cancel_broadcast", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def cancel_broadcast_cmd(bot: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    active = 0
    for bid in list(_ACTIVE_BROADCASTS.keys()):
        if _ACTIVE_BROADCASTS.get(bid):
            _ACTIVE_BROADCASTS[bid] = False
            active += 1
    await message.reply_text(
        f"🛑 <b>sᴛᴏᴘᴘᴇᴅ <code>{active}</code> ᴀᴄᴛɪᴠᴇ ʙʀᴏᴀᴅᴄᴀsᴛ(s)!</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["broadcast", "pin_broadcast"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def verupikkals(bot: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)

    if not message.reply_to_message:
        return await message.reply_text(
            "⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ ᴛᴏ ᴀʟʟ ᴜsᴇʀs!</b>\n"
            "• <code>/broadcast</code> — sᴛᴀɴᴅᴀʀᴅ ʙʀᴏᴀᴅᴄᴀsᴛ\n"
            "• <code>/pin_broadcast</code> — ʙʀᴏᴀᴅᴄᴀsᴛ &amp; ᴘɪɴ ᴍᴇssᴀɢᴇ\n"
            "• <code>/cancel_broadcast</code> — sᴛᴏᴘ ᴀᴄᴛɪᴠᴇ ʙʀᴏᴀᴅᴄᴀsᴛ",
            parse_mode=enums.ParseMode.HTML,
        )

    pin_mode = message.command[0].lower() == "pin_broadcast"
    users = await db.get_all_users()
    b_msg = message.reply_to_message
    total_users = len(users)
    if total_users == 0:
        return await message.reply_text("ℹ️ <b>ɴᴏ ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀs ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.</b>", parse_mode=enums.ParseMode.HTML)

    logger.info(
        "📡 ᴜsᴇʀ ʙʀᴏᴀᴅᴄᴀsᴛ sᴛᴀʀᴛᴇᴅ › ᴀᴅᴍɪɴ=%s ᴛᴏᴛᴀʟ_ᴜsᴇʀs=%s ᴘɪɴ=%s",
        message.from_user.id,
        total_users,
        pin_mode,
    )

    sts = await message.reply_text(
        f"📡 <b>{'📌 ᴘɪɴ ' if pin_mode else ''}ʙʀᴏᴀᴅᴄᴀsᴛ ɪɴɪᴛɪᴀᴛᴇᴅ...</b>\n"
        f"👥 <b>ᴛᴀʀɢᴇᴛ ᴜsᴇʀs:</b> <code>{total_users}</code>",
        reply_markup=_cancel_kb(message.id),
        parse_mode=enums.ParseMode.HTML,
    )
    _ACTIVE_BROADCASTS[message.id] = True
    start_time = time.time()
    done = blocked = deleted = failed = success = 0
    cancelled = False

    try:
        for user in users:
            if not _ACTIVE_BROADCASTS.get(message.id, False):
                cancelled = True
                break
            uid = int(user.get("id") or user.get("user_id") or 0)
            if not uid:
                done += 1
                failed += 1
                continue

            pti, sh = await broadcast_messages(uid, b_msg, pin=pin_mode, is_chat=False)
            if pti:
                success += 1
            else:
                if sh == "Blocked":
                    blocked += 1
                elif sh == "Deleted":
                    deleted += 1
                else:
                    failed += 1
            done += 1
            await asyncio.sleep(0.2)
            if done % 15 == 0:
                pct = (done / total_users) * 100.0
                await safe_edit_message(
                    sts,
                    f"📣 <b>ʙʀᴏᴀᴅᴄᴀsᴛ ᴘʀᴏɢʀᴇss</b> ⚡\n"
                    f"<code>[{make_progress_bar(pct)}] {pct:.1f}%</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> <code>{total_users}</code>\n"
                    f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_users}</code>\n"
                    f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code> │ 🚫 <b>ʙʟᴏᴄᴋᴇᴅ:</b> <code>{blocked}</code>\n"
                    f"🗑️ <b>ᴅᴇʟᴇᴛᴇᴅ:</b> <code>{deleted}</code> │ ❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
                    reply_markup=_cancel_kb(message.id),
                    parse_mode=enums.ParseMode.HTML,
                )
    finally:
        _ACTIVE_BROADCASTS.pop(message.id, None)

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await db.update_bot_config({"total_broadcasts": int((await db.get_bot_config()).get("total_broadcasts", 0)) + 1})
    header = "🛑 <b>ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ!</b>" if cancelled else "✅ <b>ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b> 🎉"
    logger.info(
        "%s › ᴅᴏɴᴇ=%s sᴜᴄᴄᴇss=%s ʙʟᴏᴄᴋᴇᴅ=%s ᴅᴇʟᴇᴛᴇᴅ=%s ғᴀɪʟᴇᴅ=%s ᴛɪᴍᴇ=%s",
        "🛑 ᴄᴀɴᴄᴇʟʟᴇᴅ" if cancelled else "✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
        done, success, blocked, deleted, failed, time_taken,
    )
    await safe_edit_message(
        sts,
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <b>ᴛɪᴍᴇ ᴛᴀᴋᴇɴ:</b> <code>{time_taken}</code>\n"
        f"👥 <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> <code>{total_users}</code>\n"
        f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_users}</code>\n"
        f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code>\n"
        f"🚫 <b>ʙʟᴏᴄᴋᴇᴅ:</b> <code>{blocked}</code>\n"
        f"🗑️ <b>ᴅᴇʟᴇᴛᴇᴅ:</b> <code>{deleted}</code>\n"
        f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["grp_broadcast", "group_broadcast"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def grp_brodcst(bot: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)

    if not message.reply_to_message:
        return await message.reply_text(
            "⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ ᴛᴏ ᴀʟʟ ɢʀᴏᴜᴘs!</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    chats = await db.get_all_chats()
    b_msg = message.reply_to_message
    total_chats = len(chats)
    if total_chats == 0:
        return await message.reply_text("ℹ️ <b>ɴᴏ ʀᴇɢɪsᴛᴇʀᴇᴅ ɢʀᴏᴜᴘs ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.</b>", parse_mode=enums.ParseMode.HTML)

    logger.info("📡 ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ sᴛᴀʀᴛᴇᴅ › ᴛᴏᴛᴀʟ_ᴄʜᴀᴛs=%s", total_chats)
    sts = await message.reply_text(
        f"📡 <b>ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ ɪɴɪᴛɪᴀᴛᴇᴅ...</b>\n"
        f"💬 <b>ᴛᴀʀɢᴇᴛ ɢʀᴏᴜᴘs:</b> <code>{total_chats}</code>",
        reply_markup=_cancel_kb(message.id),
        parse_mode=enums.ParseMode.HTML,
    )
    _ACTIVE_BROADCASTS[message.id] = True
    start_time = time.time()
    done = failed = success = 0
    cancelled = False

    try:
        for chat in chats:
            if not _ACTIVE_BROADCASTS.get(message.id, False):
                cancelled = True
                break
            cid = int(chat.get("id") or chat.get("chat_id") or 0)
            if not cid:
                done += 1
                failed += 1
                continue
            pti, _ = await broadcast_messages(cid, b_msg, is_chat=True)
            if pti:
                success += 1
            else:
                failed += 1
            done += 1
            await asyncio.sleep(0.5)
            if done % 10 == 0:
                pct = (done / total_chats) * 100.0
                await safe_edit_message(
                    sts,
                    f"📣 <b>ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ ᴘʀᴏɢʀᴇss</b> ⚡\n"
                    f"<code>[{make_progress_bar(pct)}] {pct:.1f}%</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💬 <b>ᴛᴏᴛᴀʟ ᴄʜᴀᴛs:</b> <code>{total_chats}</code>\n"
                    f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_chats}</code>\n"
                    f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code> │ ❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
                    reply_markup=_cancel_kb(message.id),
                    parse_mode=enums.ParseMode.HTML,
                )
    finally:
        _ACTIVE_BROADCASTS.pop(message.id, None)

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await db.update_bot_config({"total_broadcasts": int((await db.get_bot_config()).get("total_broadcasts", 0)) + 1})
    header = "🛑 <b>ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ!</b>" if cancelled else "✅ <b>ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b> 🎉"
    logger.info("✅ ɢʀᴏᴜᴘ ʙʀᴏᴀᴅᴄᴀsᴛ ғɪɴɪsʜᴇᴅ › ᴅᴏɴᴇ=%s sᴜᴄᴄᴇss=%s ғᴀɪʟᴇᴅ=%s", done, success, failed)
    await safe_edit_message(
        sts,
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <b>ᴛɪᴍᴇ ᴛᴀᴋᴇɴ:</b> <code>{time_taken}</code>\n"
        f"💬 <b>ᴛᴏᴛᴀʟ ᴄʜᴀᴛs:</b> <code>{total_chats}</code>\n"
        f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_chats}</code>\n"
        f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code>\n"
        f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["chnl_broadcast", "channel_broadcast"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def chnl_brodcst(bot: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)

    if not message.reply_to_message:
        return await message.reply_text(
            "⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ ᴛᴏ ᴀʟʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs!</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    channels = await db.get_all_channels()
    b_msg = message.reply_to_message
    total_chnls = len(channels)
    logger.info("📡 ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ sᴛᴀʀᴛᴇᴅ › ᴛᴏᴛᴀʟ_ᴄʜᴀɴɴᴇʟs=%s", total_chnls)

    if total_chnls == 0:
        return await message.reply_text("ℹ️ <b>ɴᴏ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.</b>", parse_mode=enums.ParseMode.HTML)

    sts = await message.reply_text(
        f"📡 <b>ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ ɪɴɪᴛɪᴀᴛᴇᴅ...</b>\n"
        f"📢 <b>ᴛᴀʀɢᴇᴛ ᴄʜᴀɴɴᴇʟs:</b> <code>{total_chnls}</code>",
        reply_markup=_cancel_kb(message.id),
        parse_mode=enums.ParseMode.HTML,
    )
    _ACTIVE_BROADCASTS[message.id] = True
    start_time = time.time()
    done = failed = success = 0
    cancelled = False

    try:
        for ch in channels:
            if not _ACTIVE_BROADCASTS.get(message.id, False):
                cancelled = True
                break
            cid = int(ch.get("chat_id") or ch.get("id") or 0)
            if not cid:
                done += 1
                failed += 1
                continue
            pti, _ = await broadcast_messages(cid, b_msg, is_chat=True)
            if pti:
                success += 1
            else:
                failed += 1
            done += 1
            await asyncio.sleep(0.5)
            if done % 10 == 0:
                pct = (done / total_chnls) * 100.0
                await safe_edit_message(
                    sts,
                    f"📣 <b>ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ ᴘʀᴏɢʀᴇss</b> ⚡\n"
                    f"<code>[{make_progress_bar(pct)}] {pct:.1f}%</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📢 <b>ᴛᴏᴛᴀʟ ᴄʜᴀɴɴᴇʟs:</b> <code>{total_chnls}</code>\n"
                    f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_chnls}</code>\n"
                    f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code> │ ❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
                    reply_markup=_cancel_kb(message.id),
                    parse_mode=enums.ParseMode.HTML,
                )
    finally:
        _ACTIVE_BROADCASTS.pop(message.id, None)

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await db.update_bot_config({"total_broadcasts": int((await db.get_bot_config()).get("total_broadcasts", 0)) + 1})
    header = "🛑 <b>ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ!</b>" if cancelled else "✅ <b>ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b> 🎉"
    logger.info("✅ ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ ғɪɴɪsʜᴇᴅ › ᴅᴏɴᴇ=%s sᴜᴄᴄᴇss=%s ғᴀɪʟᴇᴅ=%s", done, success, failed)
    await safe_edit_message(
        sts,
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <b>ᴛɪᴍᴇ ᴛᴀᴋᴇɴ:</b> <code>{time_taken}</code>\n"
        f"📢 <b>ᴛᴏᴛᴀʟ ᴄʜᴀɴɴᴇʟs:</b> <code>{total_chnls}</code>\n"
        f"✅ <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{done} / {total_chnls}</code>\n"
        f"✔️ <b>sᴜᴄᴄᴇss:</b> <code>{success}</code>\n"
        f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{failed}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
