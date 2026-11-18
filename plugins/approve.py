from pyrogram import Client, filters
from pyrogram.types import ChatJoinRequest, Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import Config
from info import COMMAND_PREFIXES
from database.users_chats_db import db
from utils import is_chat_or_global_admin
from plugins.core_db import chat_repo
from plugins.core_utils import to_smallcaps, format_watermark, logger

@Client.on_chat_join_request()
async def join_request_approver(client: Client, request: ChatJoinRequest):
    chat = request.chat
    user = request.from_user

    hd_cfg = await db.get_helpdesk_config(chat.id)
    if hd_cfg.get("raid_mode"):
        try:
            await client.decline_chat_join_request(chat.id, user.id)
        except Exception:
            pass
        return
    chat_doc = await chat_repo.get_or_create_chat(chat.id, chat.title or "Channel", str(chat.type))
    if not chat_doc.get("auto_approve_joins", True):
        return

    try:
        await client.approve_chat_join_request(chat_id=chat.id, user_id=user.id)
        logger.info(f"🤝 Auto-approved user {user.id} in {chat.title} ({chat.id})")

        welcome_text = (
            f"🎉 **{to_smallcaps('join request accepted!')}** ✨\n\n"
            f"👋 **{to_smallcaps('hello')} {user.mention}**, {to_smallcaps('your request to join')} **{chat.title}** "
            f"{to_smallcaps('has been automatically accepted!')} ✅"
            f"{format_watermark()}"
        )
        btn = [[InlineKeyboardButton(f"📢 {to_smallcaps('official channel')}", url=Config.WATERMARK_URL)]]
        try:
            await client.send_message(
                chat_id=user.id,
                text=welcome_text,
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True
            )
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Error approving join request for {user.id} in {chat.id}: {e}")

@Client.on_message(filters.command(["autoapprove", "accept", "autoaccept"], prefixes=COMMAND_PREFIXES) & (filters.group | filters.private | filters.channel))
async def auto_approve_command(client: Client, message: Message):
    chat_id = message.chat.id
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0
    if str(message.chat.type).lower() not in ("private", "bot", "chattype.private", "chattype.bot"):
        if not await is_chat_or_global_admin(client, chat_id, user_id=uid, sender_chat_id=sender_cid):
            return await message.reply_text(
                f"🚫 **{to_smallcaps('only chat administrators can change auto-accept settings.')}**"
                f"{format_watermark()}"
            )
    chat_doc = await chat_repo.get_or_create_chat(chat_id, message.chat.title or "Chat", str(message.chat.type))
    curr = bool(chat_doc.get("auto_approve_joins", True))

    if len(message.command) > 1:
        arg = message.command[1].lower()
        curr = False if arg in ["off", "disable", "0", "false"] else True
    else:
        curr = not curr

    await chat_repo.update_chat(chat_id, {"auto_approve_joins": curr})
    status_str = "🟢 ᴇɴᴀʙʟᴇᴅ" if curr else "⚪ ᴅɪsᴀʙʟᴇᴅ"

    toggle_lbl = f"⚪ {to_smallcaps('disable auto-accept')}" if curr else f"🟢 {to_smallcaps('enable auto-accept')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_lbl, callback_data=f"approve_toggle_{chat_id}")],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    await message.reply_text(
        f"✦ **{to_smallcaps('auto-accept join requests')}** ✦\n\n"
        f"• 🤝 **{to_smallcaps('status')}** : `{status_str}`\n"
        f"• 💬 **{to_smallcaps('chat')}** : `{message.chat.title or 'Private / Channels'}`\n\n"
        f"💡 {to_smallcaps('add the bot as admin in your channel or group with invite users permission to auto-accept all join requests instantly!')}"
        f"{format_watermark()}",
        reply_markup=kb,
    )


@Client.on_callback_query(filters.regex(r"^approve_toggle_(-?\d+)$"))
async def approve_toggle_callback(client: Client, query: CallbackQuery):
    chat_id = int(query.matches[0].group(1))
    uid = query.from_user.id if query.from_user else 0
    if chat_id < 0 and not await is_chat_or_global_admin(client, chat_id, user_id=uid):
        return await query.answer(to_smallcaps("only chat administrators can change auto-accept!"), show_alert=True)
    chat_doc = await chat_repo.get_or_create_chat(chat_id, getattr(query.message.chat, "title", None) or "Chat", str(getattr(query.message.chat, "type", "private")))
    new_state = not bool(chat_doc.get("auto_approve_joins", True))
    await chat_repo.update_chat(chat_id, {"auto_approve_joins": new_state})
    status_str = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_state else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    toggle_lbl = f"⚪ {to_smallcaps('disable auto-accept')}" if new_state else f"🟢 {to_smallcaps('enable auto-accept')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_lbl, callback_data=f"approve_toggle_{chat_id}")],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    await query.answer(to_smallcaps(f"auto-accept {'enabled' if new_state else 'disabled'}!"))
    from plugins.core_utils import safe_edit_text
    await safe_edit_text(
        query.message,
        f"✦ **{to_smallcaps('auto-accept join requests')}** ✦\n\n"
        f"• 🤝 **{to_smallcaps('status')}** : `{status_str}`\n"
        f"• 💬 **{to_smallcaps('chat')}** : `{getattr(query.message.chat, 'title', None) or 'Private / Channels'}`\n\n"
        f"💡 {to_smallcaps('add the bot as admin in your channel or group with invite users permission to auto-accept all join requests instantly!')}"
        f"{format_watermark()}",
        reply_markup=kb,
    )
