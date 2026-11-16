import html
import logging
from typing import Tuple

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from database.users_chats_db import db
from info import COMMAND_PREFIXES
from plugins.core_db import user_repo, chat_repo
from plugins.core_utils import to_smallcaps
from utils import format_watermark_html, is_chat_or_global_admin, safe_edit_message

logger = logging.getLogger("MMWAutoSet")

AS_SEP = "|"


async def build_autoset_view(
    client: Client,
    user_id: int,
    chat_id: int,
    chat_type: enums.ChatType,
    chat_title: str = "",
) -> Tuple[str, InlineKeyboardMarkup]:
    if chat_type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        cfg = await db.get_helpdesk_config(chat_id)
        af = "🟢 ᴏɴ" if cfg.get("antiflood_enabled") else "⚪ ᴏғғ"
        asp = "🟢 ᴏɴ" if cfg.get("antispam_enabled") else "⚪ ᴏғғ"
        wel = "🟢 ᴏɴ" if cfg.get("welcome_enabled") else "⚪ ᴏғғ"
        cls = "🟢 ᴏɴ" if cfg.get("clean_service") else "⚪ ᴏғғ"
        nm = "🟢 ᴏɴ" if cfg.get("nightmode_enabled") else "⚪ ᴏғғ"
        text = (
            f"✦ <b>{to_smallcaps('one-click group auto-set wizard')}</b> ✦ ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 💬 <b>{to_smallcaps('group')}:</b> {html.escape(chat_title or str(chat_id))} (<code>{chat_id}</code>)\n"
            f"• 🌊 <b>{to_smallcaps('anti-flood')}:</b> <code>{af}</code>\n"
            f"• 🛡️ <b>{to_smallcaps('anti-spam')}:</b> <code>{asp}</code>\n"
            f"• 👋 <b>{to_smallcaps('welcome msg')}:</b> <code>{wel}</code>\n"
            f"• 🧹 <b>{to_smallcaps('clean service')}:</b> <code>{cls}</code>\n"
            f"• 🌙 <b>{to_smallcaps('night mode')}:</b> <code>{nm}</code>\n\n"
            f"👇 <b>{to_smallcaps('apply a 1-click group preset below')}:</b>"
            f"{format_watermark_html()}"
        )
        buttons = [
            [
                InlineKeyboardButton(f"🛡️ {to_smallcaps('apply pro security shield')}", callback_data=f"autoset{AS_SEP}grp_shield{AS_SEP}{chat_id}"),
            ],
            [
                InlineKeyboardButton(f"🧹 {to_smallcaps('apply clean & welcome preset')}", callback_data=f"autoset{AS_SEP}grp_clean{AS_SEP}{chat_id}"),
                InlineKeyboardButton(f"🌙 {to_smallcaps('toggle night mode')}", callback_data=f"autoset{AS_SEP}grp_night{AS_SEP}{chat_id}"),
            ],
            [
                InlineKeyboardButton(f"⚙️ {to_smallcaps('open group helpdesk')}", callback_data="hdesk|settings"),
                InlineKeyboardButton(f"🔄 {to_smallcaps('reset defaults')}", callback_data=f"autoset{AS_SEP}grp_reset{AS_SEP}{chat_id}"),
            ],
            [
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ],
        ]
        return text, InlineKeyboardMarkup(buttons)

    conn_cid = await db.get_user_connected_channel(user_id)
    u = await user_repo.get_or_create(user_id, "User")
    auto_cap = "🟢 ᴏɴ" if u.get("auto_caption", True) else "⚪ ᴏғғ"
    auto_ren = "🟢 ᴏɴ" if u.get("auto_rename", False) else "⚪ ᴏғғ"
    auto_str = "🟢 ᴏɴ" if u.get("auto_stream", True) else "⚪ ᴏғғ"
    auto_cmp = "🟢 ᴏɴ" if u.get("auto_compress", False) else "⚪ ᴏғғ"

    chnl_line = f"<code>{conn_cid}</code>" if conn_cid else f"<i>{to_smallcaps('none')} (<code>/connect &lt;id&gt;</code>)</i>"
    text = (
        f"✦ <b>{to_smallcaps('all-in-one auto-set master wizard')}</b> ✦ ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{to_smallcaps('user auto profile')}:</b>\n"
        f"• 📝 <b>{to_smallcaps('auto caption')}:</b> <code>{auto_cap}</code>\n"
        f"• ✏️ <b>{to_smallcaps('auto rename')}:</b> <code>{auto_ren}</code>\n"
        f"• ⚡ <b>{to_smallcaps('auto stream & 4 players')}:</b> <code>{auto_str}</code>\n"
        f"• 🗜️ <b>{to_smallcaps('auto compress')}:</b> <code>{auto_cmp}</code>\n"
        f"• 📢 <b>{to_smallcaps('connected channel')}:</b> {chnl_line}\n\n"
        f"👇 <b>{to_smallcaps('tap any 1-click preset or toggle below')}:</b>"
        f"{format_watermark_html()}"
    )

    buttons = [
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('1-click enable all auto features')}", callback_data=f"autoset{AS_SEP}user_all_on"),
        ],
        [
            InlineKeyboardButton(f"📝 {to_smallcaps('auto caption')}: {auto_cap}", callback_data=f"autoset{AS_SEP}t_cap"),
            InlineKeyboardButton(f"✏️ {to_smallcaps('auto rename')}: {auto_ren}", callback_data=f"autoset{AS_SEP}t_ren"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto stream')}: {auto_str}", callback_data=f"autoset{AS_SEP}t_str"),
            InlineKeyboardButton(f"🗜️ {to_smallcaps('auto compress')}: {auto_cmp}", callback_data=f"autoset{AS_SEP}t_cmp"),
        ],
    ]
    if conn_cid:
        buttons.append([
            InlineKeyboardButton(f"📢 {to_smallcaps('auto-set connected channel')}", callback_data=f"autoset{AS_SEP}chnl_pro{AS_SEP}{conn_cid}"),
        ])
    buttons.extend([
        [
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward center')}", callback_data="autofwd|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton(f"⚙️ {to_smallcaps('user settings')}", callback_data="nav_settings"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('reset defaults')}", callback_data=f"autoset{AS_SEP}user_reset"),
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    return text, InlineKeyboardMarkup(buttons)


@Client.on_message(filters.command(["autoset", "autosetup", "autopro"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def autoset_command_handler(client: Client, message: Message):
    """Launch the One-Click Auto-Set Wizard for User, Group, or Channel."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
        authorized = await is_chat_or_global_admin(client, message.chat.id, user_id=uid, sender_chat_id=sender_cid)
        if not authorized:
            return await message.reply_text(
                f"⛔ <b>{to_smallcaps('only chat admins/owner or bot admins can run /autoset here!')}</b>",
                parse_mode=enums.ParseMode.HTML,
            )

    logger.info("⚡ /autoset ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s ᴛʏᴘᴇ=%s", uid, message.chat.id, message.chat.type)
    text, markup = await build_autoset_view(client, uid, message.chat.id, message.chat.type, message.chat.title or "")
    await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^autoset\|"))
async def autoset_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split(AS_SEP)
    action = parts[1] if len(parts) > 1 else "home"

    if action == "home":
        await query.answer("⚡ ᴀᴜᴛᴏ-sᴇᴛ ᴡɪᴢᴀʀᴅ")
        text, markup = await build_autoset_view(client, uid, query.message.chat.id, query.message.chat.type, query.message.chat.title or "")
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    if action.startswith("grp_") and len(parts) > 2:
        cid = int(parts[2])
        if not await is_chat_or_global_admin(client, cid, user_id=uid):
            return await query.answer("⛔ ᴏɴʟʏ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs!", show_alert=True)

        cfg = await db.get_helpdesk_config(cid)
        if action == "grp_shield":
            await db.update_helpdesk_config(
                cid,
                {
                    "antiflood_enabled": True,
                    "flood_limit": 5,
                    "flood_window": 5,
                    "flood_action": "mute",
                    "antispam_enabled": True,
                    "antidup_enabled": True,
                    "warn_limit": 3,
                    "warn_action": "ban",
                },
            )
            await query.answer("🛡️ ᴘʀᴏ sᴇᴄᴜʀɪᴛʏ sʜɪᴇʟᴅ ᴀᴘᴘʟɪᴇᴅ!", show_alert=True)
        elif action == "grp_clean":
            await db.update_helpdesk_config(
                cid,
                {
                    "welcome_enabled": True,
                    "clean_service": True,
                    "clean_commands": True,
                },
            )
            await query.answer("🧹 ᴄʟᴇᴀɴ & ᴡᴇʟᴄᴏᴍᴇ ᴘʀᴇsᴇᴛ ᴀᴘᴘʟɪᴇᴅ!", show_alert=True)
        elif action == "grp_night":
            nm = not bool(cfg.get("nightmode_enabled", False))
            await db.update_helpdesk_config(
                cid,
                {
                    "nightmode_enabled": nm,
                    "locks": {"media": nm, "stickers": nm, "gifs": nm, "links": nm},
                },
            )
            await query.answer(f"🌙 ɴɪɢʜᴛ ᴍᴏᴅᴇ: {'ᴏɴ' if nm else 'ᴏғғ'}", show_alert=True)
        elif action == "grp_reset":
            await db.update_helpdesk_config(cid, db.default_helpdesk_group_config(cid))
            await query.answer("🔄 ɢʀᴏᴜᴘ ᴅᴇғᴀᴜʟᴛs ʀᴇsᴛᴏʀᴇᴅ!", show_alert=True)

        text, markup = await build_autoset_view(client, uid, cid, enums.ChatType.SUPERGROUP, query.message.chat.title or "")
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    if action == "chnl_pro" and len(parts) > 2:
        cid = int(parts[2])
        if not await is_chat_or_global_admin(client, cid, user_id=uid):
            return await query.answer("⛔ ᴏɴʟʏ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs!", show_alert=True)
        await db.update_channel_config(
            cid,
            {
                "auto_tag_enabled": True,
                "auto_caption_enabled": True,
                "auto_stream_enabled": True,
                "footer_text": f"⚡ <b>{Config.WATERMARK}</b>",
                "button_text": f"📢 {Config.WATERMARK}",
                "button_url": Config.WATERMARK_URL,
            },
        )
        await chat_repo.update_chat(cid, {"auto_caption": True, "auto_approve_joins": True})
        await query.answer("📢 ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ + ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ + ʙʀᴀɴᴅɪɴɢ ᴀᴄᴛɪᴠᴀᴛᴇᴅ!", show_alert=True)
        text, markup = await build_autoset_view(client, uid, query.message.chat.id, query.message.chat.type)
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    u = await user_repo.get_or_create(uid, query.from_user.first_name if query.from_user else "User")
    if action == "user_all_on":
        prefix = u.get("prefix") or f"[{Config.WATERMARK}]"
        caption = u.get("custom_caption") or "📁 <b>{filename}</b>\n📦 <b>sɪᴢᴇ:</b> <code>{filesize}</code>\n⏱️ <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{duration}</code>"
        await user_repo.update_user(
            uid,
            {
                "auto_caption": True,
                "auto_rename": True,
                "auto_stream": True,
                "prefix": prefix,
                "custom_caption": caption,
            },
        )
        await query.answer("⚡ ᴀʟʟ ᴀᴜᴛᴏ ғᴇᴀᴛᴜʀᴇs (ᴄᴀᴘᴛɪᴏɴ + ʀᴇɴᴀᴍᴇ + sᴛʀᴇᴀᴍ) ᴇɴᴀʙʟᴇᴅ!", show_alert=True)
    elif action == "t_cap":
        await user_repo.update_user(uid, {"auto_caption": not bool(u.get("auto_caption", True))})
        await query.answer("📝 ᴀᴜᴛᴏ ᴄᴀᴘᴛɪᴏɴ ᴛᴏɢɢʟᴇᴅ!")
    elif action == "t_ren":
        new_ren = not bool(u.get("auto_rename", False))
        updates = {"auto_rename": new_ren}
        if new_ren and not (u.get("prefix") or u.get("suffix")):
            updates["prefix"] = f"[{Config.WATERMARK}]"
        await user_repo.update_user(uid, updates)
        await query.answer("✏️ ᴀᴜᴛᴏ ʀᴇɴᴀᴍᴇ ᴛᴏɢɢʟᴇᴅ!")
    elif action == "t_str":
        await user_repo.update_user(uid, {"auto_stream": not bool(u.get("auto_stream", True))})
        await query.answer("⚡ ᴀᴜᴛᴏ sᴛʀᴇᴀᴍ ᴛᴏɢɢʟᴇᴅ!")
    elif action == "t_cmp":
        await user_repo.update_user(uid, {"auto_compress": not bool(u.get("auto_compress", False))})
        await query.answer("🗜️ ᴀᴜᴛᴏ ᴄᴏᴍᴘʀᴇss ᴛᴏɢɢʟᴇᴅ!")
    elif action == "user_reset":
        await user_repo.update_user(
            uid,
            {
                "auto_caption": True,
                "auto_rename": False,
                "auto_stream": True,
                "auto_compress": False,
                "prefix": "",
                "suffix": "",
                "custom_caption": None,
            },
        )
        await query.answer("🔄 ᴀᴜᴛᴏ sᴇᴛᴛɪɴɢs ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ!", show_alert=True)

    text, markup = await build_autoset_view(client, uid, query.message.chat.id, query.message.chat.type)
    await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
