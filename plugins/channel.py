import os
import secrets
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from config import Config
from database.users_chats_db import db
from plugins.core_db import chat_repo, file_repo
from plugins.core_utils import to_smallcaps, format_watermark, humanbytes, time_formatter, logger, execute_with_floodwait
from plugins.core_services import format_caption, schedule_deletion, telegram_media_metadata


@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def channel_media_listener(client: Client, message: Message):
    """
    Channel Auto-Caption, Auto-Stream (4 External Players), Auto-React, and Auto-Delete listener.
    Works seamlessly alongside channel_manager.py templates.
    """
    media = message.video or message.document or message.audio
    if not media:
        return

    chat_doc = await chat_repo.get_or_create_chat(message.chat.id, message.chat.title or "Channel", "channel")
    ch_cfg = await db.get_channel_config(message.chat.id, title=message.chat.title or "Channel")

    # 1. Auto-React on new channel media post if configured
    react_emoji = (ch_cfg.get("auto_react_emoji") or "").strip()
    if react_emoji:
        try:
            await client.send_reaction(message.chat.id, message.id, react_emoji)
        except Exception:
            pass

    filename = getattr(media, "file_name", None) or "file.mp4"
    file_size_bytes = getattr(media, "file_size", 0)
    file_size_str = humanbytes(file_size_bytes)
    duration = getattr(media, "duration", 0)
    duration_str = time_formatter(seconds=duration) if duration else "0s"
    _, ext = os.path.splitext(filename)
    ext = ext.replace(".", "")

    # 2. Auto-Stream: Index channel media & attach Watch (4 Players) + Fast Download buttons if enabled
    stream_markup = None
    from utils import temp as _t
    if _t.BOT_CONFIG.get("auto_stream_global", True) and ch_cfg.get("auto_stream_enabled", False):
        try:
            file_id = secrets.token_urlsafe(10)
            await file_repo.save_file(
                file_id=file_id,
                chat_id=message.chat.id,
                message_id=message.id,
                file_name=filename,
                file_size=file_size_bytes,
                mime_type=getattr(media, "mime_type", "video/mp4"),
                user_id=0,
                metadata=telegram_media_metadata(media, message),
                tg_file_id=getattr(media, "file_id", None),
            )
            watch_url = f"{Config.BASE_URL}/watch/{file_id}"
            dl_url = f"{Config.BASE_URL}/download/{file_id}"
            rows = [
                [
                    InlineKeyboardButton(f"🎬 {to_smallcaps('stream (4 players)')}", url=watch_url),
                    InlineKeyboardButton(f"⬇️ {to_smallcaps('fast download')}", url=dl_url),
                ]
            ]
            if message.reply_markup and getattr(message.reply_markup, "inline_keyboard", None):
                rows.extend(message.reply_markup.inline_keyboard)
            stream_markup = InlineKeyboardMarkup(rows)
            logger.info("⚡ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ ᴀᴛᴛᴀᴄʜᴇᴅ › ᴄʜᴀᴛ=%s ᴍsɢ=%s ғɪʟᴇ_ɪᴅ=%s", message.chat.id, message.id, file_id)
        except Exception as e:
            logger.debug("⚠️ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ ᴡᴀʀɴ › %s", e)

    # 3. Auto-Caption: Format caption using channel custom_caption or global format_caption
    from utils import temp as _t
    auto_cap_on = _t.BOT_CONFIG.get("auto_caption_global", True) and bool(chat_doc.get("auto_caption", True)) and bool(ch_cfg.get("auto_caption_enabled", True))
    curr_caption = message.caption or ""
    new_caption = curr_caption

    if auto_cap_on and Config.WATERMARK not in curr_caption:
        ch_custom_tpl = (ch_cfg.get("custom_caption") or "").strip()
        if ch_custom_tpl:
            new_caption = (
                ch_custom_tpl.replace("{filename}", filename)
                .replace("{file_name}", filename)
                .replace("{filesize}", file_size_str)
                .replace("{file_size}", file_size_str)
                .replace("{duration}", duration_str)
                .replace("{ext}", ext)
                .replace("{original_caption}", curr_caption)
            )
        else:
            new_caption = await format_caption(
                user_or_id=message.chat.id,
                file_name=filename,
                file_size_str=file_size_str,
                duration_str=duration_str,
                ext=ext,
                file_caption=curr_caption,
                file_id=str(message.id),
            )

    if new_caption != curr_caption or stream_markup is not None:
        try:
            await execute_with_floodwait(
                message.edit_caption,
                caption=(new_caption or curr_caption)[:1024],
                reply_markup=stream_markup or message.reply_markup,
            )
        except Exception:
            pass

    # 4. Auto-Delete timer if configured on channel
    auto_del = int(ch_cfg.get("auto_delete_seconds", 0) or chat_doc.get("auto_delete_time", 0) or 0)
    if auto_del > 0:
        await schedule_deletion(message.chat.id, message.id, auto_del)
