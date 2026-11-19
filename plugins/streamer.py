
import secrets
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from config import Config
from info import COMMAND_PREFIXES
from plugins.core_db import file_repo, user_repo
from plugins.core_utils import cache, to_smallcaps, format_watermark, humanbytes, time_formatter, logger, safe_forward_message, safe_edit_text, safe_reply_text, execute_with_floodwait
from plugins.media_types import is_code_file, detect_code_language, extract_message_media
from plugins.core_web import build_4_external_player_links
from plugins.core_services import telegram_media_metadata


_EXT_MIME_MAP = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def _resolve_tg_mime_fallback(file_name: str, mime_type: str) -> str:
    if mime_type and mime_type != "application/octet-stream":
        return mime_type
    lower = str(file_name or "").strip().lower()
    for ext, mapped in _EXT_MIME_MAP.items():
        if lower.endswith(ext):
            return mapped
    return mime_type or "application/octet-stream"


async def get_cached_user_autostream(user_id: int):
    return await cache.get(f"autostream_state_{user_id}")


async def set_cached_user_autostream(user_id: int, state: bool):
    await cache.set(f"autostream_state_{user_id}", bool(state), ttl=120)


async def get_cached_stream_file_id(chat_id: int, message_id: int):
    return await cache.get(f"media_fid_{chat_id}_{message_id}")


async def set_cached_stream_file_id(chat_id: int, message_id: int, file_id: str):
    await cache.set(f"media_fid_{chat_id}_{message_id}", str(file_id), ttl=600)


async def invalidate_stream_bot_cache(file_id: str):
    for key in (f"file_doc_{file_id}", f"meta_view_{file_id}", f"api_info_{file_id}", f"tg_msg_{file_id}"):
        await cache.delete(key)
    cache.auto_clear_after_response_sync(preserve_workflows=True)


@Client.on_message(filters.command(["autostream", "auto_stream"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def toggle_autostream_cmd(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    u = await user_repo.get_or_create(uid, message.from_user.first_name, message.from_user.username)
    current = await get_cached_user_autostream(uid)
    if current is None:
        current = bool(u.get("auto_stream", False))
    new_state = not bool(current)
    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        new_state = arg in ("on", "true", "1", "yes", "enable")
    await user_repo.update_user(uid, {"auto_stream": new_state})
    await set_cached_user_autostream(uid, new_state)
    badge = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_state else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    logger.info("⚡ /autostream ᴛᴏɢɢʟᴇᴅ › ᴜsᴇʀ=%s sᴛᴀᴛᴇ=%s", uid, new_state)
    await message.reply_text(
        f"✦ **{to_smallcaps('auto-stream & 4 players mode')}** ✦ ⚡\n\n"
        f"• 🎬 **{to_smallcaps('status')}** : `{badge}`\n"
        f"• 📱 **{to_smallcaps('players')}** : `PLAYit • MX Player • VLC • S Player`\n\n"
        f"💡 {to_smallcaps('when enabled, every file you send or rename automatically gets instant HTTP 206 byte-range stream & 4 external player links!')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⚪ {to_smallcaps('disable auto-stream')}" if new_state else f"🟢 {to_smallcaps('enable auto-stream')}", callback_data="autostream_toggle_cb")],
            [
                InlineKeyboardButton(f"⚙️ {to_smallcaps('settings')}", callback_data="nav_settings"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home")
            ]
        ])
    )
    cache.auto_clear_after_response_sync(preserve_workflows=True)


@Client.on_callback_query(filters.regex(r"^autostream_toggle_cb$"))
async def autostream_toggle_callback(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    u = await user_repo.get_or_create(uid, query.from_user.first_name, query.from_user.username)
    new_state = not bool(u.get("auto_stream", False))
    await user_repo.update_user(uid, {"auto_stream": new_state})
    await set_cached_user_autostream(uid, new_state)
    badge = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_state else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    tbtn = f"⚪ {to_smallcaps('disable auto-stream')}" if new_state else f"🟢 {to_smallcaps('enable auto-stream')}"
    await query.answer(to_smallcaps(f"auto-stream {'enabled' if new_state else 'disabled'}!"))
    await safe_edit_text(
        query.message,
        f"✦ **{to_smallcaps('auto-stream & 4 players mode')}** ✦ ⚡\n\n"
        f"• 🎬 **{to_smallcaps('status')}** : `{badge}`\n"
        f"• 📱 **{to_smallcaps('players')}** : `PLAYit • MX Player • VLC • S Player`\n\n"
        f"💡 {to_smallcaps('when enabled, every file you send or rename automatically gets instant HTTP 206 byte-range stream & 4 external player links!')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(tbtn, callback_data="autostream_toggle_cb")],
            [
                InlineKeyboardButton(f"⚙️ {to_smallcaps('settings')}", callback_data="nav_settings"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home")
            ]
        ])
    )
    cache.auto_clear_after_response_sync(preserve_workflows=True)


@Client.on_message(filters.command(["stream", "filestream", "link"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def stream_command_entry(client: Client, message: Message):
    from plugins.rename import resolve_target_media_message
    user_id = message.from_user.id if message.from_user else message.chat.id
    media_msg = await resolve_target_media_message(client, message, user_id=user_id)
    if not media_msg or not media_msg.media:
        return await message.reply_text(
            f"⚠️ **{to_smallcaps('please reply to a video, audio, or document to generate an adaptive range stream link.')}**\n\n"
            f"• 📝 **{to_smallcaps('usage')}** : {to_smallcaps('reply to any media with')} `/stream` {to_smallcaps('or')} `/filestream`"
            f"{format_watermark()}"
        )
    logger.info("🎬 /stream ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s ᴍsɢ=%s", user_id, message.chat.id, media_msg.id)
    await generate_stream_response(client, media_msg, message, is_callback=False)


@Client.on_callback_query(filters.regex(r"^stream_(\d+)"))
async def stream_callback_entry(client: Client, query: CallbackQuery):
    msg_id = int(query.matches[0].group(1))
    original = await client.get_messages(query.message.chat.id, msg_id)
    if not original or not original.media:
        return await query.answer(to_smallcaps("media not found!"), show_alert=True)
    await query.answer(to_smallcaps("generating stream link..."))
    await generate_stream_response(client, original, query.message, is_callback=True)


async def generate_stream_response(client: Client, media_msg: Message, target_reply: Message, is_callback: bool = False):
    status = await (safe_edit_text if is_callback else safe_reply_text)(
        target_reply,
        f"⚡ **{to_smallcaps('generating zero-buffer stream link...')}**"
    )

    bin_chan = Config.BIN_CHANNEL if Config.BIN_CHANNEL != 0 else media_msg.chat.id
    log_msg = await safe_forward_message(media_msg, bin_chan) if bin_chan != media_msg.chat.id else media_msg
    if log_msg is None:
        logger.warning("⚠️ sᴛʀᴇᴀᴍ ᴀʀᴄʜɪᴠᴇ ғᴏʀᴡᴀʀᴅ ғᴀɪʟᴇᴅ; ᴜsɪɴɢ ᴏʀɪɢɪɴᴀʟ ᴍᴇᴅɪᴀ ᴍᴇssᴀɢᴇ")
        log_msg = media_msg

    media, raw_name, file_size, raw_mime, duration_val = extract_message_media(media_msg)
    file_name = raw_name.replace("`", "'")
    mime_type = _resolve_tg_mime_fallback(file_name, raw_mime)
    duration = time_formatter(seconds=duration_val) if duration_val > 0 else "Undetermined"
    tg_file_id = getattr(media, "file_id", None)

    thumb_id = getattr(media.thumbs[0], "file_id", None) if getattr(media, "thumbs", None) else None
    metadata = telegram_media_metadata(media, media_msg)
    metadata["mime_type"] = mime_type

    user_id = media_msg.from_user.id if media_msg.from_user else (target_reply.from_user.id if target_reply.from_user else 0)

    cached_fid = await get_cached_stream_file_id(media_msg.chat.id, media_msg.id)
    existing_doc = None
    if cached_fid:
        existing_doc = await file_repo.get_file(str(cached_fid))
    if not existing_doc:
        existing_doc = await file_repo.find_existing_file(log_msg.chat.id, log_msg.id, tg_file_id)

    if existing_doc and existing_doc.get("file_id"):
        file_id = str(existing_doc["file_id"])
    else:
        file_id = secrets.token_urlsafe(12)
        await file_repo.save_file(
            file_id=file_id,
            chat_id=log_msg.chat.id,
            message_id=log_msg.id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            user_id=user_id,
            metadata=metadata,
            thumb_id=thumb_id,
            tg_file_id=tg_file_id
        )
    await set_cached_stream_file_id(media_msg.chat.id, media_msg.id, file_id)

    watch_url = f"{Config.BASE_URL}/watch/{file_id}"
    stream_url = f"{Config.BASE_URL}/stream/{file_id}"
    download_url = f"{Config.BASE_URL}/download/{file_id}"
    dl_hub_url = f"{Config.BASE_URL}/dl/{file_id}"
    code_url = f"{Config.BASE_URL}/code/{file_id}" if is_code_file(file_name, mime_type) else ""
    logger.info("✅ sᴛʀᴇᴀᴍ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ (ᴀɴʏ sɪᴢᴇ) › ғɪʟᴇ_ɪᴅ=%s ɴᴀᴍᴇ=%s sɪᴢᴇ=%s", file_id, file_name, file_size)

    text = (
        f"✦ **{to_smallcaps('zero-buffer file stream link')}** ✦\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_name}`\n"
        f"• 📦 **{to_smallcaps('file size')}** : `{humanbytes(file_size)}` (`Any Size Supported` ✅)\n"
        f"• ⚡ **{to_smallcaps('stream mode')}** : `zero-buffer HTTP 206 byte-range`\n"
        f"• 🏷️ **{to_smallcaps('mime type')}** : `{mime_type}`\n"
        f"• ⏱️ **{to_smallcaps('duration')}** : `{duration}`\n"
        f"• 🆔 **{to_smallcaps('file id')}** : `{file_id}`\n\n"
        f"📱 **{to_smallcaps('4 external players supported')}** :\n"
        f"  `1.` ▶️ **{to_smallcaps('playit')}**\n"
        f"  `2.` 🎬 **{to_smallcaps('mx player')}**\n"
        f"  `3.` 🔊 **{to_smallcaps('vlc player')}**\n"
        f"  `4.` 🚀 **{to_smallcaps('s player')}**\n\n"
        f"🔗 **{to_smallcaps('watch & external players page')}** :\n{watch_url}\n\n"
        f"⚡ **{to_smallcaps('direct range stream url')}** :\n`{stream_url}`\n\n"
        f"⬇️ **{to_smallcaps('fast download link')}** :\n{download_url}"
        f"{format_watermark()}"
    )

    buttons = [
        [
            InlineKeyboardButton(f"🎬 {to_smallcaps('watch & 4 players')}", url=watch_url),
            InlineKeyboardButton(f"⬇️ {to_smallcaps('fast download')}", url=download_url)
        ],
        [
            InlineKeyboardButton(f"📦 {to_smallcaps('download hub')}", url=dl_hub_url),
            InlineKeyboardButton(f"📥 {to_smallcaps('get file')}", callback_data=f"getfile_{file_id}")
        ],
        [
            InlineKeyboardButton(f"🗑️ {to_smallcaps('revoke link')}", callback_data=f"revoke_{file_id}"),
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to media')}", callback_data=f"back_to_media_{media_msg.id}")
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
        ]
    ]

    if code_url:
        buttons.insert(1, [InlineKeyboardButton(f"💻 {to_smallcaps('live code viewer')}", url=code_url)])
    await safe_edit_text(status, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    cache.auto_clear_after_response_sync(preserve_workflows=True)


@Client.on_message(filters.private & filters.command(["getfile", "file"], prefixes=COMMAND_PREFIXES))
async def get_file_command_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"📝 **{to_smallcaps('usage')}** : `/getfile [file_id]`\n\n"
            f"• 💡 {to_smallcaps('retrieves and sends the original media file directly to you.')}"
            f"{format_watermark()}"
        )
    file_id = message.command[1].strip()
    await send_streamed_file_to_user(client, message.chat.id, file_id, message)


@Client.on_callback_query(filters.regex(r"^getfile_([a-zA-Z0-9_-]+)"))
async def get_file_callback_entry(client: Client, query: CallbackQuery):
    file_id = query.matches[0].group(1)
    await query.answer(to_smallcaps("sending file to you..."))
    await send_streamed_file_to_user(client, query.message.chat.id, file_id, query.message)


async def send_streamed_file_to_user(client: Client, chat_id: int, file_id: str, reply_target: Message):
    file_doc = await cache.get(f"file_doc_{file_id}")
    if not file_doc:
        file_doc = await file_repo.get_file(file_id)
    if not file_doc:
        return await reply_target.reply_text(
            f"❌ **{to_smallcaps('file not found or stream link has been revoked!')}**\n\n"
            f"💡 {to_smallcaps('this stream link may have expired or been deleted.')}"
            f"{format_watermark()}"
        )

    try:
        msg = await client.get_messages(file_doc["chat_id"], file_doc["message_id"])
        if msg and msg.media:
            await execute_with_floodwait(msg.copy, chat_id=chat_id)
        else:
            await reply_target.reply_text(f"❌ **{to_smallcaps('original media no longer exists in storage channel!')}**")
    except Exception as e:
        logger.error(f"Error copying media file for file_id {file_id}: {e}", exc_info=True)
        await reply_target.reply_text(f"❌ **{to_smallcaps('failed to send file')}** : `{e}`")
    finally:
        cache.auto_clear_after_response_sync(preserve_workflows=True)


async def revoke_stream_by_id(message: Message, file_id: str) -> bool:
    file_doc = await file_repo.get_file(file_id, include_revoked=True)
    if not file_doc:
        return False

    user_id = message.from_user.id if message.from_user else 0
    if file_doc.get("user_id") and file_doc["user_id"] != user_id and user_id not in Config.ADMINS:
        await message.reply_text(f"🚫 **{to_smallcaps('you are not authorized to revoke this stream link!')}**")
        return True

    await file_repo.revoke_file(file_id)
    await invalidate_stream_bot_cache(file_id)
    logger.info("🗑️ sᴛʀᴇᴀᴍ ʟɪɴᴋ ʀᴇᴠᴏᴋᴇᴅ › ғɪʟᴇ_ɪᴅ=%s ʙʏ=%s", file_id, user_id)
    await message.reply_text(
        f"✦ **{to_smallcaps('stream link revoked')}** ✦\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_doc.get('file_name', 'Media')}`\n"
        f"• 🆔 **{to_smallcaps('file id')}** : `{file_id}`\n\n"
        f"✅ **{to_smallcaps('this stream link has been permanently revoked & disabled.')}**"
        f"{format_watermark()}"
    )
    return True


@Client.on_message(filters.private & filters.command(["delfile", "revokestream"], prefixes=COMMAND_PREFIXES))
async def revoke_stream_command_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"📝 **{to_smallcaps('usage')}** : `/revokestream [file_id]`\n\n"
            f"• 💡 {to_smallcaps('permanently disables the streaming link for this file.')}"
            f"{format_watermark()}"
        )
    file_id = message.command[1].strip()
    found = await revoke_stream_by_id(message, file_id)
    if not found:
        await message.reply_text(f"❌ **{to_smallcaps('file id not found!')}**")


@Client.on_callback_query(filters.regex(r"^revoke_([a-zA-Z0-9_-]+)"))
async def revoke_callback_entry(client: Client, query: CallbackQuery):
    file_id = query.matches[0].group(1)
    file_doc = await file_repo.get_file(file_id, include_revoked=True)
    if not file_doc:
        return await query.answer(to_smallcaps("file not found!"), show_alert=True)

    user_id = query.from_user.id
    if file_doc.get("user_id") and file_doc["user_id"] != user_id and user_id not in Config.ADMINS:
        return await query.answer(to_smallcaps("you are not authorized to revoke this link!"), show_alert=True)

    await file_repo.revoke_file(file_id)
    await invalidate_stream_bot_cache(file_id)
    await query.answer(to_smallcaps("stream link revoked!"), show_alert=True)

    await safe_edit_text(
        query.message,
        f"✦ **{to_smallcaps('stream link revoked')}** ✦\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_doc.get('file_name', 'Media')}`\n"
        f"• 🆔 **{to_smallcaps('file id')}** : `{file_id}`\n\n"
        f"🚫 **{to_smallcaps('this stream link has been permanently revoked & disabled.')}**"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
        ]])
    )
