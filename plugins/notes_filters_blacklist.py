import asyncio
from datetime import datetime, timedelta
import html
import logging
import re
from typing import Any, Dict, Optional

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.users_chats_db import db
from info import COMMAND_PREFIXES
from utils import (
    get_file_id,
    is_chat_or_global_admin,
    parse_Inline_buttons_from_text,
    safe_edit_message,
)
from plugins.help_desk_features import MUTE_PERMS, verify_group_and_permissions

logger = logging.getLogger("GokuNotesFilters")


async def _send_saved_payload(
    client: Client,
    message: Message,
    item: Dict[str, Any],
) -> None:
    raw_content = item.get("content", "")
    cleaned_text, markup = parse_Inline_buttons_from_text(raw_content)
    file_id = item.get("file_id")
    media_type = item.get("media_type")

    try:
        if file_id and media_type:
            send_map = {
                "photo": message.reply_photo,
                "video": message.reply_video,
                "document": message.reply_document,
                "audio": message.reply_audio,
                "voice": message.reply_voice,
                "animation": message.reply_animation,
                "sticker": message.reply_sticker,
            }
            fn = send_map.get(media_type)
            if fn:
                if media_type == "sticker":
                    await fn(file_id, reply_markup=markup, quote=True)
                else:
                    await fn(
                        file_id,
                        caption=cleaned_text[:1024] if cleaned_text else None,
                        reply_markup=markup,
                        parse_mode=enums.ParseMode.HTML,
                        quote=True,
                    )
                return
        await message.reply_text(
            cleaned_text or "📌",
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
    except Exception as e:
        logger.debug("⚠️ _sᴇɴᴅ_sᴀᴠᴇᴅ_ᴘᴀʏʟᴏᴀᴅ ᴇʀʀᴏʀ › %s", e)


@Client.on_message(filters.command("save", prefixes=COMMAND_PREFIXES))
async def save_note_cmd(client: Client, message: Message):
    """Save a text or media note in a group or channel."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return

    parts = message.text.split(None, 2) if message.text else []
    if len(parts) < 2:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/save &lt;notename&gt; &lt;content&gt;</code> (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    note_name = parts[1].strip().lstrip("#").lower()
    content = parts[2].strip() if len(parts) > 2 else ""
    file_id = None
    media_type = None

    if message.reply_to_message:
        r = message.reply_to_message
        if not content:
            content = r.text or r.caption or ""
        finfo = get_file_id(r)
        if finfo:
            file_id = finfo.file_id
            media_type = finfo.message_type

    if not content and not file_id:
        return await message.reply_text(
            "⚠️ <b>ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ɴᴏᴛᴇ ᴛᴇxᴛ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇᴅɪᴀ/ᴛᴇxᴛ ᴍᴇssᴀɢᴇ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    await db.save_note(
        message.chat.id,
        note_name,
        {"content": content, "file_id": file_id, "media_type": media_type},
    )
    await message.reply_text(
        f"📝 <b>ɴᴏᴛᴇ <code>#{html.escape(note_name)}</code> sᴀᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b> ✅\n"
        f"<i>ʀᴇᴛʀɪᴇᴠᴇ ɪᴛ ᴡɪᴛʜ <code>#{html.escape(note_name)}</code> ᴏʀ <code>/get {html.escape(note_name)}</code></i>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("get", prefixes=COMMAND_PREFIXES))
async def get_note_cmd(client: Client, message: Message):
    """Retrieve a saved note by name."""
    if len(message.command) < 2:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/get &lt;notename&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    name = message.command[1].strip().lstrip("#").lower()
    doc = await db.get_note(message.chat.id, name)
    if not doc:
        return await message.reply_text(f"❌ <b>ɴᴏᴛᴇ <code>#{html.escape(name)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    logger.info("📝 /get ɴᴏᴛᴇ › ᴄʜᴀᴛ=%s ɴᴀᴍᴇ=%s", message.chat.id, name)
    await _send_saved_payload(client, message, doc)


@Client.on_message(filters.command(["notes", "saved"], prefixes=COMMAND_PREFIXES))
async def list_notes_cmd(client: Client, message: Message):
    """List all saved notes in the current group or channel."""
    names = await db.get_all_notes(message.chat.id)
    if not names:
        return await message.reply_text("ℹ️ <b>ɴᴏ sᴀᴠᴇᴅ ɴᴏᴛᴇs ɪɴ ᴛʜɪs ᴄʜᴀᴛ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    bullets = "\n".join(f"• <code>#{html.escape(n)}</code>" for n in names)
    await message.reply_text(
        f"📝 <b>sᴀᴠᴇᴅ ɴᴏᴛᴇs ɪɴ ᴛʜɪs ᴄʜᴀᴛ ({len(names)}):</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bullets}\n\n"
        f"💡 <i>ᴛʏᴘᴇ <code>#notename</code> ᴏʀ <code>/get notename</code> ᴛᴏ ᴠɪᴇᴡ!</i>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["clear", "delnote"], prefixes=COMMAND_PREFIXES))
async def clear_note_cmd(client: Client, message: Message):
    """Delete a specific saved note."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return
    if len(message.command) < 2:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/clear &lt;notename&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    name = message.command[1].strip().lstrip("#").lower()
    removed = await db.delete_note(message.chat.id, name)
    if removed:
        await message.reply_text(f"🗑️ <b>ɴᴏᴛᴇ <code>#{html.escape(name)}</code> ᴅᴇʟᴇᴛᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ <b>ɴᴏᴛᴇ <code>#{html.escape(name)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("clearall", prefixes=COMMAND_PREFIXES))
async def clear_all_notes_cmd(client: Client, message: Message):
    """Delete all saved notes in the chat with confirmation."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return
    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ɴᴏᴛᴇs", callback_data="notes|clearall_ok"),
            InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="close_data"),
        ]
    ])
    await message.reply_text(
        "⚠️ <b>ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ɴᴏᴛᴇs ɪɴ ᴛʜɪs ᴄʜᴀᴛ?</b>",
        reply_markup=btn,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("filter", prefixes=COMMAND_PREFIXES))
async def add_filter_cmd(client: Client, message: Message):
    """Create a custom keyword auto-reply filter in the chat."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return

    parts = message.text.split(None, 2) if message.text else []
    if len(parts) < 2:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/filter &lt;keyword&gt; &lt;reply&gt;</code> (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    trigger = parts[1].strip().lower()
    content = parts[2].strip() if len(parts) > 2 else ""
    file_id = None
    media_type = None

    if message.reply_to_message:
        r = message.reply_to_message
        if not content:
            content = r.text or r.caption or ""
        finfo = get_file_id(r)
        if finfo:
            file_id = finfo.file_id
            media_type = finfo.message_type

    if not content and not file_id:
        return await message.reply_text(
            "⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ғɪʟᴛᴇʀ ʀᴇᴘʟʏ ᴛᴇxᴛ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    await db.save_filter(
        message.chat.id,
        trigger,
        {"content": content, "file_id": file_id, "media_type": media_type},
    )
    await message.reply_text(
        f"⚡ <b>ᴀᴜᴛᴏ-ғɪʟᴛᴇʀ sᴀᴠᴇᴅ ғᴏʀ:</b> <code>{html.escape(trigger)}</code> ✅",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


def _render_filters_page(chat_id: int, keys: list, page: int = 0) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    per_page = 10
    total = len(keys)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    start = page * per_page
    page_keys = keys[start : start + per_page]
    bullets = "\n".join(f"`{idx}.` • <code>{html.escape(k)}</code>" for idx, k in enumerate(page_keys, start=start + 1))
    text = (
        f"⚡ <b>ᴀᴄᴛɪᴠᴇ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs ({total}) — ᴘᴀɢᴇ {page + 1}/{total_pages}:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bullets}"
    )
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"fltr|page|{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="fltr|noop"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"fltr|page|{page + 1}"))
    kb = InlineKeyboardMarkup([nav_row, [InlineKeyboardButton("❌ Close", callback_data="close_data")]])
    return text, kb


@Client.on_message(filters.command(["filters", "filterlist"], prefixes=COMMAND_PREFIXES))
async def list_filters_cmd(client: Client, message: Message):
    """List all active custom auto-filters in the chat with pagination buttons."""
    fltrs = await db.get_all_filters(message.chat.id)
    if not fltrs:
        return await message.reply_text("ℹ️ <b>ɴᴏ ᴀᴄᴛɪᴠᴇ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs ɪɴ ᴛʜɪs ᴄʜᴀᴛ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    keys = sorted(fltrs.keys())
    text, kb = _render_filters_page(message.chat.id, keys, page=0)
    await message.reply_text(
        text,
        reply_markup=kb,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["stop", "delfilter"], prefixes=COMMAND_PREFIXES))
async def stop_filter_cmd(client: Client, message: Message):
    """Remove a specific custom auto-filter."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return
    if len(message.command) < 2:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/stop &lt;keyword&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    trig = message.command[1].strip().lower()
    removed = await db.delete_filter(message.chat.id, trig)
    if removed:
        await message.reply_text(f"🗑️ <b>sᴛᴏᴘᴘᴇᴅ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀ:</b> <code>{html.escape(trig)}</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ <b>ғɪʟᴛᴇʀ <code>{html.escape(trig)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("stopall", prefixes=COMMAND_PREFIXES))
async def stop_all_filters_cmd(client: Client, message: Message):
    """Remove all custom auto-filters in the chat with confirmation."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return
    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ sᴛᴏᴘ ᴀʟʟ ғɪʟᴛᴇʀs", callback_data="fltr|stopall_ok"),
            InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="close_data"),
        ]
    ])
    await message.reply_text(
        "⚠️ <b>ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀʟʟ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs ɪɴ ᴛʜɪs ᴄʜᴀᴛ?</b>",
        reply_markup=btn,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["addblacklist", "rmblacklist", "unblacklist", "blacklist", "unblacklistall", "blacklistmode"], prefixes=COMMAND_PREFIXES))
async def blacklist_cmds(client: Client, message: Message):
    """Manage blacklisted words and auto-censor penalty mode in the group."""
    cmd = message.command[0].lower()
    if cmd == "blacklist":
        cfg = await db.get_blacklist_config(message.chat.id)
        words = cfg.get("words", [])
        mode = cfg.get("mode", "warn").upper()
        if not words:
            return await message.reply_text("ℹ️ <b>ɴᴏ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅs ɪɴ ᴛʜɪs ᴄʜᴀᴛ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        w_str = ", ".join(f"<code>{html.escape(w)}</code>" for w in words)
        return await message.reply_text(
            f"🚫 <b>ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅs ({len(words)})</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴘᴇɴᴀʟᴛʏ ᴍᴏᴅᴇ:</b> <code>{mode}</code>\n"
            f"• <b>ᴡᴏʀᴅs:</b> {w_str}",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_delete_messages", "can_delete_messages")
    if not ok:
        return

    cfg = await db.get_blacklist_config(message.chat.id)
    words = list(cfg.get("words", []))

    if cmd == "unblacklistall":
        await db.update_blacklist_config(message.chat.id, [])
        return await message.reply_text("🧹 <b>ᴄʟᴇᴀʀᴇᴅ ᴀʟʟ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅs!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if len(message.command) < 2:
        return await message.reply_text(f"⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} &lt;word(s)&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "blacklistmode":
        mode = message.command[1].strip().lower()
        if mode not in ("delete", "warn", "mute", "ban", "kick"):
            return await message.reply_text("⚠️ <b>ᴍᴏᴅᴇs:</b> <code>delete, warn, mute, ban, kick</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        await db.update_blacklist_config(message.chat.id, words, mode=mode)
        return await message.reply_text(f"⚙️ <b>ʙʟᴀᴄᴋʟɪsᴛ ᴘᴇɴᴀʟᴛʏ ᴍᴏᴅᴇ sᴇᴛ ᴛᴏ:</b> <code>{mode.upper()}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    args = [w.strip().lower() for w in message.text.split()[1:] if w.strip()]
    if cmd == "addblacklist":
        words.extend(args)
        updated = await db.update_blacklist_config(message.chat.id, words)
        return await message.reply_text(
            f"🚫 <b>ᴀᴅᴅᴇᴅ <code>{len(args)}</code> ᴡᴏʀᴅ(s) ᴛᴏ ʙʟᴀᴄᴋʟɪsᴛ! (ᴛᴏᴛᴀʟ: <code>{len(updated['words'])}</code>)</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        words = [w for w in words if w not in args]
        await db.update_blacklist_config(message.chat.id, words)
        return await message.reply_text(
            f"✅ <b>ʀᴇᴍᴏᴠᴇᴅ <code>{len(args)}</code> ᴡᴏʀᴅ(s) ғʀᴏᴍ ʙʟᴀᴄᴋʟɪsᴛ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_callback_query(filters.regex(r"^(notes|fltr)\|"))
async def notes_filters_cb(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id if query.message else 0
    uid = query.from_user.id if query.from_user else 0
    prefix, act = query.data.split("|", 1)
    if prefix == "fltr" and act == "noop":
        return await query.answer()
    if prefix == "fltr" and act.startswith("page|"):
        pg = int(act.split("|", 1)[1]) if act.split("|", 1)[1].isdigit() else 0
        fltrs = await db.get_all_filters(chat_id)
        if not fltrs:
            return await query.answer("ℹ️ ɴᴏ ғɪʟᴛᴇʀs!", show_alert=True)
        text, kb = _render_filters_page(chat_id, sorted(fltrs.keys()), page=pg)
        await query.answer(f"📄 Page {pg + 1}")
        return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    if not await is_chat_or_global_admin(client, chat_id, user_id=uid):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    if prefix == "notes" and act == "clearall_ok":
        cnt = await db.delete_all_notes(chat_id)
        await query.answer(f"🗑️ ᴅᴇʟᴇᴛᴇᴅ {cnt} ɴᴏᴛᴇs!", show_alert=True)
        await safe_edit_message(query.message, f"🗑️ <b>ᴅᴇʟᴇᴛᴇᴅ ᴀʟʟ <code>{cnt}</code> ɴᴏᴛᴇs ɪɴ ᴛʜɪs ᴄʜᴀᴛ!</b>", parse_mode=enums.ParseMode.HTML)
    elif prefix == "fltr" and act == "stopall_ok":
        cnt = await db.delete_all_filters(chat_id)
        await query.answer(f"🗑️ sᴛᴏᴘᴘᴇᴅ {cnt} ғɪʟᴛᴇʀs!", show_alert=True)
        await safe_edit_message(query.message, f"🗑️ <b>sᴛᴏᴘᴘᴇᴅ ᴀʟʟ <code>{cnt}</code> ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs ɪɴ ᴛʜɪs ᴄʜᴀᴛ!</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message((filters.group | filters.channel) & filters.incoming & ~filters.service, group=5)
async def notes_filters_blacklist_watchdog(client: Client, message: Message):
    """Watchdog for #notename triggers, word blacklist censoring, and custom auto-filter replies."""
    raw_txt = (message.text or message.caption or "").strip()
    if not raw_txt or raw_txt.startswith(tuple(COMMAND_PREFIXES)):
        return

    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if raw_txt.startswith("#") and len(raw_txt) > 1 and " " not in raw_txt:
        note_name = raw_txt[1:].strip().lower()
        doc = await db.get_note(chat_id, note_name)
        if doc:
            logger.info("📝 #ɴᴏᴛᴇ ᴛʀɪɢɢᴇʀᴇᴅ › ᴄʜᴀᴛ=%s ɴᴀᴍᴇ=%s", chat_id, note_name)
            await _send_saved_payload(client, message, doc)
            return

    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) and user_id:
        bl_cfg = await db.get_blacklist_config(chat_id)
        bl_words = bl_cfg.get("words", [])
        if bl_words and not await is_chat_or_global_admin(client, chat_id, user_id=user_id):
            low_txt = raw_txt.lower()
            matched_word = next((w for w in bl_words if re.search(rf"\b{re.escape(w)}\b", low_txt)), None)
            if matched_word:
                try:
                    await message.delete()
                except Exception:
                    pass
                mode = bl_cfg.get("mode", "warn").lower()
                logger.info("🚫 ʙʟᴀᴄᴋʟɪsᴛ ᴛʀɪɢɢᴇʀᴇᴅ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s ᴡᴏʀᴅ=%s ᴍᴏᴅᴇ=%s", chat_id, user_id, matched_word, mode)
                if mode == "warn":
                    cnt, _ = await db.add_warning(chat_id, user_id, f"Blacklisted word: {matched_word}", 0)
                    await client.send_message(
                        chat_id,
                        f"⚠️ {message.from_user.mention} <b>ᴡᴀʀɴᴇᴅ (<code>{cnt}</code>) ғᴏʀ ᴜsɪɴɢ ᴀ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅ!</b>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                elif mode == "mute":
                    await client.restrict_chat_member(
                        chat_id, user_id, permissions=MUTE_PERMS, until_date=datetime.now() + timedelta(minutes=30)
                    )
                    await client.send_message(
                        chat_id,
                        f"🔇 {message.from_user.mention} <b>ᴍᴜᴛᴇᴅ (30ᴍ) ғᴏʀ ᴜsɪɴɢ ᴀ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅ!</b>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                elif mode == "ban":
                    await client.ban_chat_member(chat_id, user_id)
                    await client.send_message(
                        chat_id,
                        f"🚫 {message.from_user.mention} <b>ʙᴀɴɴᴇᴅ ғᴏʀ ᴜsɪɴɢ ᴀ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅ!</b>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                elif mode == "kick":
                    await client.ban_chat_member(chat_id, user_id)
                    await asyncio.sleep(0.5)
                    await client.unban_chat_member(chat_id, user_id)
                return

    fltrs = await db.get_all_filters(chat_id)
    if fltrs:
        low_txt = raw_txt.lower()
        for trig, fdoc in fltrs.items():
            if low_txt == trig or re.search(rf"(?:^|\W){re.escape(trig)}(?:$|\W)", low_txt):
                logger.info("⚡ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀ ᴍᴀᴛᴄʜᴇᴅ › ᴄʜᴀᴛ=%s ᴛʀɪɢɢᴇʀ=%s", chat_id, trig)
                await _send_saved_payload(client, message, fdoc)
                break
