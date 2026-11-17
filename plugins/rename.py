import re
import os
import time
import secrets
import asyncio
from typing import Optional, Dict, Any
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ForceReply
)
from config import Config
from info import COMMAND_PREFIXES
from plugins.core_db import user_repo, file_repo, task_repo
from plugins.core_utils import (
    cache,
    to_smallcaps,
    format_watermark,
    humanbytes,
    time_formatter,
    clean_temp_files,
    sanitize_filename,
    progress_for_pyrogram,
    logger,
    safe_edit_text,
    safe_reply_text,
    safe_send_message,
    safe_forward_message,
    bounded_media_job,
    execute_with_floodwait,
    pro_floodwait_sleep,
    _flood_wait_seconds,
    task_manager,
)
from plugins.core_services import (
    get_media_attributes,
    telegram_media_metadata,
    resolve_thumbnail,
    extract_and_format_caption,
    schedule_deletion,
    download_media_with_auth_recovery
)
from plugins.media_types import is_audio_mime, is_video_mime, normalize_mime

MAX_BOT_FILE_SIZE = int(getattr(Config, "LARGE_FILE_THRESHOLD_BYTES", 2 * 1024 * 1024 * 1024))
_USER_MEDIA_LOCKS: dict[int, asyncio.Lock] = {}
_USER_LAST_MEDIA_TS: dict[int, float] = {}


async def resolve_target_media_message(client: Client, message: Message, user_id: int = 0) -> Optional[Message]:
    """
    Intelligently resolve the target media message whether the user:
    1. Replied directly to the video/document/audio message,
    2. Replied to the bot's Smart Media Studio card or ForceReply prompt, or
    3. Sent `/rename` or `/compress` right after sending a media file without swipe-replying.
    """
    uid = user_id or (message.from_user.id if getattr(message, "from_user", None) else 0)
    chat_id = message.chat.id if getattr(message, "chat", None) else uid

    rep = getattr(message, "reply_to_message", None)
    if rep:
        if getattr(rep, "video", None) or getattr(rep, "document", None) or getattr(rep, "audio", None):
            return rep
        rep_parent = getattr(rep, "reply_to_message", None)
        if rep_parent and (getattr(rep_parent, "video", None) or getattr(rep_parent, "document", None) or getattr(rep_parent, "audio", None)):
            return rep_parent
        markup = getattr(rep, "reply_markup", None)
        rows = getattr(markup, "inline_keyboard", None) if markup else None
        if rows:
            for r in rows:
                for b in r:
                    cb = getattr(b, "callback_data", "") or ""
                    m = re.search(r"(?:rename|stream|compress_menu|pro_menu|custcomp_open|back_to_media)_(\d+)", cb)
                    if m and client and chat_id:
                        try:
                            fetched = await client.get_messages(chat_id, int(m.group(1)))
                            if fetched and (fetched.video or fetched.document or fetched.audio):
                                return fetched
                        except Exception:
                            pass

    for key in (f"waiting_rename_{uid}", f"last_media_{uid}"):
        cached_id = await cache.get(key)
        if cached_id and client and chat_id:
            try:
                mid = int(cached_id.get("msg_id") if isinstance(cached_id, dict) else cached_id)
                if mid > 0:
                    fetched = await client.get_messages(chat_id, mid)
                    if fetched and (fetched.video or fetched.document or fetched.audio):
                        return fetched
            except Exception:
                pass
    return None


async def render_media_studio_card(
    client: Client,
    media_msg: Message,
    user_id: int,
    target_msg: Message = None,
    is_edit: bool = False,
):
    """Render or edit in-place the Smart Media Studio card (or >2GB Auto Compress & File Stream prompt)."""
    if user_id and getattr(media_msg, "id", None):
        await cache.set(f"last_media_{user_id}", int(media_msg.id), ttl=3600)

    u = await user_repo.get_or_create(user_id, "User") if user_id else {}
    media = media_msg.document or media_msg.video or media_msg.audio
    raw_file_name = getattr(media, "file_name", None) or "Unknown_File.mp4"
    file_name = raw_file_name.replace("`", "'")
    file_size_bytes = getattr(media, "file_size", 0)
    file_size = humanbytes(file_size_bytes)
    mime_type = getattr(media, "mime_type", "video/mp4")
    dur_val = getattr(media, "duration", 0)
    duration = time_formatter(seconds=dur_val) if dur_val > 0 else "N/A"

    normalized_mime = normalize_mime(mime_type)
    is_video = bool(media_msg.video or is_video_mime(normalized_mime))
    is_compressible = is_video or is_audio_mime(normalized_mime)
    media_metadata = telegram_media_metadata(media, media_msg)

    cached_fid = await cache.get(f"media_fid_{media_msg.chat.id}_{media_msg.id}")
    if not cached_fid:
        cached_fid = secrets.token_urlsafe(10)
        await file_repo.save_file(
            file_id=cached_fid,
            chat_id=media_msg.chat.id,
            message_id=media_msg.id,
            file_name=file_name,
            file_size=file_size_bytes,
            mime_type=mime_type,
            user_id=user_id,
            metadata=media_metadata,
            tg_file_id=getattr(media, "file_id", None)
        )
        await cache.set(f"media_fid_{media_msg.chat.id}_{media_msg.id}", cached_fid, ttl=3600)
        bin_chan = Config.BIN_CHANNEL if Config.BIN_CHANNEL != 0 else media_msg.chat.id
        if bin_chan != media_msg.chat.id:
            async def _bg_archive_to_bin(fid: str, m_obj: Message, b_ch: int):
                try:
                    await asyncio.sleep(1.2)
                    fwd = await safe_forward_message(m_obj, b_ch)
                    if fwd and getattr(fwd, "chat", None) and getattr(fwd, "id", None):
                        await file_repo.update_file(fid, {"chat_id": fwd.chat.id, "message_id": fwd.id})
                except Exception as exc:
                    logger.debug("⚠️ ʙɢ ʙɪɴ_ᴄʜᴀɴɴᴇʟ ғᴏʀᴡᴀʀᴅ sᴋɪᴘᴘᴇᴅ › %s", exc)
            asyncio.create_task(_bg_archive_to_bin(cached_fid, media_msg, bin_chan))

    watch_url = f"{Config.BASE_URL}/watch/{cached_fid}"
    download_url = f"{Config.BASE_URL}/download/{cached_fid}"

    if file_size_bytes > MAX_BOT_FILE_SIZE:
        text = (
            f"✦ **{to_smallcaps('large media (> 2gb) — compress or file stream')}** ✦ ⚡\n\n"
            f"• 📁 **{to_smallcaps('file name')}** : `{file_name}`\n"
            f"• 📦 **{to_smallcaps('file size')}** : `{file_size}` (`> 2 GB`)\n"
            f"• ⏱️ **{to_smallcaps('duration')}** : `{duration}`\n"
            f"• 🏷️ **{to_smallcaps('mime type')}** : `{mime_type}`\n"
            f"• 🎬 **{to_smallcaps('players')}** : `PLAYit • MX Player • VLC • S Player`\n\n"
            f"🔗 **{to_smallcaps('instant 4-player stream link')}** :\n{watch_url}\n\n"
            f"💡 **{to_smallcaps('this file is above 2 gb. choose an option below to compress it with ffmpeg (< 2gb) or stream/download directly:')}**"
            f"{format_watermark()}"
        )
        buttons = [
            [
                InlineKeyboardButton(f"🎛️ {to_smallcaps('custom compress (5-10m)')}", callback_data=f"custcomp_open_{media_msg.id}"),
            ],
            [
                InlineKeyboardButton(f"🗜️ {to_smallcaps('compress < 2gb (ffmpeg)')}", callback_data=f"do_comptarget_{media_msg.id}_1850"),
                InlineKeyboardButton(f"🎚️ {to_smallcaps('ffmpeg presets')}", callback_data=f"compress_menu_{media_msg.id}")
            ],
            [
                InlineKeyboardButton(f"⚡ {to_smallcaps('file stream card')}", callback_data=f"stream_{media_msg.id}"),
                InlineKeyboardButton(f"📊 {to_smallcaps('mediainfo')}", callback_data=f"pro_mediainfo_{media_msg.id}")
            ],
            [
                InlineKeyboardButton(f"🎬 {to_smallcaps('watch (4 players)')}", url=watch_url),
                InlineKeyboardButton(f"⬇️ {to_smallcaps('fast range download')}", url=download_url)
            ],
            [
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
            ]
        ]
        if is_edit and target_msg:
            return await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
        return await safe_reply_text(media_msg, text, reply_markup=InlineKeyboardMarkup(buttons), quote=True, disable_web_page_preview=True)

    has_thumb = "✅" if u and u.get("thumb_id") else "🌐 ᴛʜᴀᴍ_ᴜʀʟ"
    has_caption = "✅" if u and (u.get("custom_caption") or u.get("captions_list")) else "📄 ᴀᴜᴛᴏ"
    auto_ren_status = "🟢 ᴏɴ" if u.get("auto_rename") else "⚪ ᴏғғ"
    auto_comp_status = "🟢 ᴏɴ" if u.get("auto_compress") else "⚪ ᴏғғ"

    text = (
        f"✦ **{to_smallcaps('smart media studio & adaptive range stream')}** ✦\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_name}`\n"
        f"• 📦 **{to_smallcaps('file size')}** : `{file_size}`\n"
        f"• ⚡ **{to_smallcaps('stream mode')}** : `adaptive HTTP byte-range (PLAYit • MX • VLC • S Player)`\n"
        f"• 🏷️ **{to_smallcaps('mime type')}** : `{mime_type}`\n"
        f"• ⏱️ **{to_smallcaps('duration')}** : `{duration}`\n"
        f"• 🖼️ **{to_smallcaps('thumbnail')}** : `{has_thumb}` | 📝 **{to_smallcaps('caption')}** : `{has_caption}`\n"
        f"• 🤖 **{to_smallcaps('auto rename')}** : `{auto_ren_status}` | 🗜️ **{to_smallcaps('auto compress')}** : `{auto_comp_status}`\n\n"
        f"🔗 **{to_smallcaps('instant 4-player watch link')}** :\n{watch_url}\n\n"
        f"💡 **{to_smallcaps('select an action below to proceed:')}**"
        f"{format_watermark()}"
    )

    buttons = [
        [
            InlineKeyboardButton(f"✏️ {to_smallcaps('rename file')}", callback_data=f"rename_{media_msg.id}"),
            InlineKeyboardButton(f"⚡ {to_smallcaps('stream details')}", callback_data=f"stream_{media_msg.id}")
        ],
        [
            InlineKeyboardButton(f"🎬 {to_smallcaps('watch (4 players)')}", url=watch_url),
            InlineKeyboardButton(f"⬇️ {to_smallcaps('adaptive range download')}", url=download_url)
        ]
    ]
    row3 = []
    if is_compressible:
        row3.append(InlineKeyboardButton(f"🗜️ {to_smallcaps('compress (ffmpeg)')}", callback_data=f"compress_menu_{media_msg.id}"))
    row3.append(InlineKeyboardButton(f"📊 {to_smallcaps('mediainfo')}", callback_data=f"pro_mediainfo_{media_msg.id}"))
    buttons.append(row3)
    buttons.append([
        InlineKeyboardButton(f"🎛️ {to_smallcaps('file pro advance features')}", callback_data=f"pro_menu_{media_msg.id}")
    ])
    buttons.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
    ])

    if is_edit and target_msg:
        return await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    return await safe_reply_text(media_msg, text, reply_markup=InlineKeyboardMarkup(buttons), quote=True, disable_web_page_preview=True)


@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def incoming_file_entry(client: Client, message: Message):
    user = message.from_user
    uid = user.id if user else 0
    if user and await user_repo.is_banned(uid):
        return await safe_reply_text(
            message,
            f"🚫 **{to_smallcaps('you are banned from using this bot!')}**"
            f"{format_watermark()}"
        )
    lock = _USER_MEDIA_LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        now_m = time.monotonic()
        gap = 0.25 - (now_m - _USER_LAST_MEDIA_TS.get(uid, 0.0))
        if gap > 0:
            await asyncio.sleep(gap)
        _USER_LAST_MEDIA_TS[uid] = time.monotonic()
    return await _process_incoming_file(client, message, user)


async def _process_incoming_file(client: Client, message: Message, user):
    uid = user.id if user else 0
    if uid and (await cache.get(f"waiting_addaudio_{uid}") or await cache.get(f"waiting_addsub_{uid}")):
        return
    if message.reply_to_message and message.reply_to_message.reply_markup:
        if isinstance(message.reply_to_message.reply_markup, ForceReply):
            return

    if uid and getattr(message, "id", None):
        await cache.set(f"last_media_{uid}", int(message.id), ttl=3600)

    u = await user_repo.get_or_create(user.id, user.first_name, user.username) if user else {}
    media = message.document or message.video or message.audio
    raw_file_name = getattr(media, "file_name", None) or "Unknown_File.mp4"
    file_size_bytes = getattr(media, "file_size", 0)
    mime_type = getattr(media, "mime_type", "video/mp4")
    normalized_mime = normalize_mime(mime_type)
    is_video = bool(message.video or is_video_mime(normalized_mime))
    is_compressible = is_video or is_audio_mime(normalized_mime)

    logger.info(
        "📥 ɪɴᴄᴏᴍɪɴɢ ᴍᴇᴅɪᴀ › ᴜsᴇʀ=%s ᴍsɢ=%s ɴᴀᴍᴇ='%s' sɪᴢᴇ=%s (%s) ᴍɪᴍᴇ=%s",
        uid,
        message.id,
        raw_file_name,
        humanbytes(file_size_bytes),
        file_size_bytes,
        mime_type,
    )
    if file_size_bytes > MAX_BOT_FILE_SIZE:
        logger.info("📦 >2ɢʙ ᴍᴇᴅɪᴀ ᴅᴇᴛᴇᴄᴛᴇᴅ (%s ʙʏᴛᴇs) › ᴀᴜᴛᴏ-ᴘʀᴏᴍᴘᴛɪɴɢ ᴄᴏᴍᴘʀᴇss / ғɪʟᴇ sᴛʀᴇᴀᴍ", file_size_bytes)
        return await render_media_studio_card(client, message, uid, is_edit=False)

    if u.get("auto_compress") and is_compressible:
        from plugins.compressor import run_auto_compress_workflow
        return await run_auto_compress_workflow(client, message, user.id)

    if u.get("auto_rename"):
        name_parts = os.path.splitext(raw_file_name)
        auto_target = sanitize_filename(f"{name_parts[0]}{name_parts[1] or '.mp4'}")
        await cache.set(f"rename_name_{user.id}_{message.id}", auto_target, ttl=1800)
        upload_mode = "vid" if (is_video and u.get("upload_mode", "video") == "video") else "doc"
        return await run_rename_workflow(client, message, message, user.id, message.id, auto_target, upload_mode)

    await render_media_studio_card(client, message, uid, is_edit=False)


@Client.on_message(filters.private & filters.command(["autorename", "auto_rename"], prefixes=COMMAND_PREFIXES))
async def toggle_autorename_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    u = await user_repo.get_or_create(user_id, message.from_user.first_name, message.from_user.username)
    new_state = not bool(u.get("auto_rename", False))
    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        new_state = arg in ("on", "true", "1", "yes", "enable")
    await user_repo.update_user(user_id, {"auto_rename": new_state})
    state_txt = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_state else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    await safe_reply_text(
        message,
        f"✦ **{to_smallcaps('auto-rename mode')}** ✦\n\n"
        f"• 🤖 **{to_smallcaps('status')}** : `{state_txt}`\n"
        f"• 🏷️ **{to_smallcaps('prefix')}** : `{u.get('prefix') or 'None'}`\n"
        f"• 🏷️ **{to_smallcaps('suffix')}** : `{u.get('suffix') or 'None'}`\n\n"
        f"💡 {to_smallcaps('when enabled, incoming files <= 2gb are automatically renamed with your prefix/suffix, custom thumbnail, caption & 4-player stream buttons!')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⚙️ {to_smallcaps('settings')}", callback_data="nav_settings"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home")
        ]])
    )


def apply_rename_template(
    raw_name: str,
    user_doc: dict,
    media_obj: object = None,
) -> str:
    """Apply customizable filename template, cleanup, prefix, and suffix while preserving valid extension."""
    name_parts = os.path.splitext(raw_name or "file.mp4")
    base_name = name_parts[0]
    ext = name_parts[1] or ".mp4"
    clean_base = re.sub(r"[._]+", " ", base_name).strip() or base_name
    tpl = (user_doc.get("rename_format") if user_doc else None) or ""
    if tpl:
        width = getattr(media_obj, "width", 0) or 0
        height = getattr(media_obj, "height", 0) or 0
        res = f"{height}p" if height else "HD"
        dur = time_formatter(seconds=int(getattr(media_obj, "duration", 0) or 0))
        fsize = humanbytes(int(getattr(media_obj, "file_size", 0) or 0))
        rendered = (
            tpl.replace("{filename}", clean_base)
            .replace("{file_name}", clean_base)
            .replace("{original}", base_name)
            .replace("{ext}", ext.lstrip("."))
            .replace("{resolution}", res)
            .replace("{duration}", dur)
            .replace("{filesize}", fsize)
        )
        base_name = rendered.strip() or base_name
    prefix = (user_doc.get("prefix") if user_doc else "") or ""
    suffix = (user_doc.get("suffix") if user_doc else "") or ""
    if prefix and not base_name.startswith(prefix):
        base_name = f"{prefix}{base_name}"
    if suffix and not base_name.endswith(suffix):
        base_name = f"{base_name}{suffix}"
    if not ext.startswith("."):
        ext = f".{ext}"
    return sanitize_filename(f"{base_name}{ext}")


@Client.on_message(filters.private & filters.command(["setformat", "renameformat", "delformat"], prefixes=COMMAND_PREFIXES))
async def set_rename_format_handler(client: Client, message: Message):
    cmd = message.command[0].lower()
    if cmd == "delformat" or (len(message.command) > 1 and message.command[1].lower() in ("off", "clear", "reset", "none")):
        await user_repo.update_user(message.from_user.id, {"rename_format": None})
        return await safe_reply_text(message, f"🗑️ **{to_smallcaps('custom rename format template cleared!')}**{format_watermark()}")
    if len(message.command) < 2:
        u = await user_repo.get_user(message.from_user.id)
        curr = (u.get("rename_format") if u else None) or "Default ({prefix}{filename}{suffix}.{ext})"
        return await safe_reply_text(
            message,
            f"✦ **{to_smallcaps('custom filename template engine')}** ✦ ✏️\n\n"
            f"• 📋 **{to_smallcaps('current template')}** : `{curr}`\n"
            f"• 📝 **{to_smallcaps('usage')}** : `/setformat [template]` (or `/delformat`)\n"
            f"• 🏷️ **{to_smallcaps('variables')}** : `{{filename}}`, `{{resolution}}`, `{{duration}}`, `{{filesize}}`, `{{ext}}`\n"
            f"• 💡 **{to_smallcaps('example')}** : `/setformat [MMW] {{filename}} [{{resolution}}]`"
            f"{format_watermark()}"
        )
    tpl = message.text.split(None, 1)[1].strip()[:180]
    await user_repo.update_user(message.from_user.id, {"rename_format": tpl})
    await safe_reply_text(
        message,
        f"✅ **{to_smallcaps('custom rename format saved!')}**\n\n"
        f"• 🏷️ **{to_smallcaps('template')}** : `{tpl}`"
        f"{format_watermark()}"
    )


@Client.on_message(filters.private & filters.command(["setprefix", "prefix"], prefixes=COMMAND_PREFIXES))
async def set_prefix_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 **{to_smallcaps('usage')}** : `/setprefix [prefix_text]`\n\n"
            f"• **{to_smallcaps('example')}** : `/setprefix [MMW] `\n\n"
            f"💡 {to_smallcaps('this prefix will be prepended to every renamed file.')}"
            f"{format_watermark()}"
        )
    prefix = message.text.split(None, 1)[1]
    await user_repo.update_user(message.from_user.id, {"prefix": prefix})
    await safe_reply_text(
        message,
        f"✅ **{to_smallcaps('file prefix saved!')}**\n\n"
        f"• 🏷️ **{to_smallcaps('new prefix')}** : `{prefix}`"
        f"{format_watermark()}"
    )


@Client.on_message(filters.private & filters.command(["setsuffix", "suffix"], prefixes=COMMAND_PREFIXES))
async def set_suffix_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 **{to_smallcaps('usage')}** : `/setsuffix [suffix_text]`\n\n"
            f"• **{to_smallcaps('example')}** : `/setsuffix @mallumovieworldmain2`\n\n"
            f"💡 {to_smallcaps('this suffix will be inserted before the file extension.')}"
            f"{format_watermark()}"
        )
    suffix = message.text.split(None, 1)[1]
    await user_repo.update_user(message.from_user.id, {"suffix": suffix})
    await safe_reply_text(
        message,
        f"✅ **{to_smallcaps('file suffix saved!')}**\n\n"
        f"• 🏷️ **{to_smallcaps('new suffix')}** : `{suffix}`"
        f"{format_watermark()}"
    )


@Client.on_message(filters.private & filters.command(["delprefix", "delsuffix"], prefixes=COMMAND_PREFIXES))
async def del_prefix_suffix(client: Client, message: Message):
    cmd = message.command[0].lower()
    if "prefix" in cmd:
        await user_repo.update_user(message.from_user.id, {"prefix": None})
        await safe_reply_text(message, f"🗑️ **{to_smallcaps('file prefix removed successfully!')}**{format_watermark()}")
    else:
        await user_repo.update_user(message.from_user.id, {"suffix": None})
        await safe_reply_text(message, f"🗑️ **{to_smallcaps('file suffix removed successfully!')}**{format_watermark()}")


@Client.on_message(filters.private & filters.command("rename", prefixes=COMMAND_PREFIXES))
async def rename_cmd_handler(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    target = await resolve_target_media_message(client, message, user_id=user_id)
    if not target or not (target.document or target.video or target.audio):
        return await safe_reply_text(
            message,
            f"⚠️ **{to_smallcaps('please reply to a file or video to rename.')}**\n\n"
            f"• **{to_smallcaps('usage')}** : `/rename [new_name.ext]`"
            f"{format_watermark()}"
        )

    media = target.document or target.video or target.audio
    if getattr(media, "file_size", 0) > MAX_BOT_FILE_SIZE:
        return await render_media_studio_card(client, target, user_id, is_edit=False)

    if len(message.command) < 2:
        old_name = getattr(media, "file_name", None) or "file.mp4"
        await cache.set(f"waiting_rename_{user_id}", target.id, ttl=900)
        return await safe_send_message(
            client,
            chat_id=message.chat.id,
            text=(
                f"✏️ **{to_smallcaps('enter new file name')}** ✦\n\n"
                f"• 📁 **{to_smallcaps('current')}** : `{old_name}`\n\n"
                f"💬 {to_smallcaps('reply to this message or send your new filename.')}\n"
                f"💡 {to_smallcaps('send')} `/cancel` {to_smallcaps('to abort.')}"
                f"{format_watermark()}"
            ),
            reply_markup=ForceReply(placeholder=to_smallcaps("enter new file name..."))
        )

    await cache.delete(f"waiting_rename_{user_id}")
    raw_input_name = message.text.split(None, 1)[1].strip()
    new_name = sanitize_filename(raw_input_name)

    orig_name = getattr(media, "file_name", "") or ""
    orig_ext = os.path.splitext(orig_name)[1]
    if not os.path.splitext(new_name)[1] and orig_ext:
        new_name = f"{new_name}{orig_ext}"

    await cache.set(f"rename_name_{user_id}_{target.id}", new_name, ttl=1800)
    await show_rename_options(message, target.id, new_name)


@Client.on_callback_query(filters.regex(r"^rename_(\d+)"))
async def rename_callback_ask(client: Client, query: CallbackQuery):
    msg_id = int(query.matches[0].group(1))
    user_id = query.from_user.id if query.from_user else query.message.chat.id
    original = await client.get_messages(query.message.chat.id, msg_id)
    if not original or not original.media:
        return await query.answer(to_smallcaps("media not found!"), show_alert=True)

    media = original.document or original.video or original.audio
    if getattr(media, "file_size", 0) > MAX_BOT_FILE_SIZE:
        await query.answer("⚠️ >2ɢʙ: ᴄʜᴏᴏsᴇ ᴄᴏᴍᴘʀᴇss ᴏʀ ғɪʟᴇ sᴛʀᴇᴀᴍ!")
        return await render_media_studio_card(
            client,
            original,
            user_id,
            target_msg=query.message,
            is_edit=True,
        )

    await query.answer("✏️ ᴇɴᴛᴇʀ ɴᴇᴡ ғɪʟᴇ ɴᴀᴍᴇ")
    old_name = getattr(media, "file_name", None) or "file.mp4"
    await cache.set(f"waiting_rename_{user_id}", msg_id, ttl=900)
    await cache.set(f"last_media_{user_id}", msg_id, ttl=3600)

    try:
        await query.message.delete()
    except Exception:
        pass
    await safe_send_message(
        client,
        chat_id=query.message.chat.id,
        text=(
            f"✏️ **{to_smallcaps('enter new file name')}** ✦\n\n"
            f"• 📁 **{to_smallcaps('current')}** : `{old_name}`\n\n"
            f"💬 {to_smallcaps('reply to this message or send your new filename.')}\n"
            f"💡 {to_smallcaps('send')} `/cancel` {to_smallcaps('to abort.')}"
            f"{format_watermark()}"
        ),
        reply_markup=ForceReply(placeholder=to_smallcaps("enter new file name..."))
    )


@Client.on_message(filters.private & filters.text, group=1)
async def reply_force_rename_collector(client: Client, message: Message):
    if not message.from_user or not message.text:
        message.continue_propagation()
    txt = message.text.strip()
    if txt.startswith(tuple(COMMAND_PREFIXES)):
        message.continue_propagation()

    user_id = message.from_user.id

    wait_comp = await cache.get(f"waiting_custom_comp_{user_id}")
    if wait_comp:
        from plugins.compressor import handle_custom_compress_text_input
        await cache.delete(f"waiting_custom_comp_{user_id}")
        return await handle_custom_compress_text_input(client, message, wait_comp)

    wait_trim = await cache.get(f"waiting_trim_{user_id}")
    if wait_trim:
        from plugins.file_pro import execute_trim_video
        await cache.delete(f"waiting_trim_{user_id}")
        return await execute_trim_video(client, message, wait_trim, txt)

    if await cache.get(f"sess_step_{user_id}"):
        from plugins.session import handle_session_text_step
        return await handle_session_text_step(client, message)

    msg_id = await cache.get(f"waiting_rename_{user_id}")
    if not msg_id:
        message.continue_propagation()

    await cache.delete(f"waiting_rename_{user_id}")
    new_name = sanitize_filename(txt)

    try:
        orig_msg = await client.get_messages(message.chat.id, int(msg_id))
        if orig_msg and orig_msg.media:
            orig_media = orig_msg.document or orig_msg.video or orig_msg.audio
            orig_file_name = getattr(orig_media, "file_name", "") or ""
            orig_ext = os.path.splitext(orig_file_name)[1]
            if not os.path.splitext(new_name)[1] and orig_ext:
                new_name = f"{new_name}{orig_ext}"
    except Exception:
        pass

    await cache.set(f"rename_name_{user_id}_{msg_id}", new_name, ttl=1800)
    await show_rename_options(message, int(msg_id), new_name)


async def show_rename_options(message: Message, target_id: int, new_name: str):
    btn = [
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('fast rename (auto)')}", callback_data=f"renameto_auto_{target_id}"),
        ],
        [
            InlineKeyboardButton(f"📁 {to_smallcaps('upload as document')}", callback_data=f"renameto_doc_{target_id}"),
            InlineKeyboardButton(f"🎬 {to_smallcaps('upload as video')}", callback_data=f"renameto_vid_{target_id}")
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to media')}", callback_data=f"back_to_media_{target_id}"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('cancel')}", callback_data="cancel_op")
        ]
    ]
    await safe_reply_text(
        message,
        f"✦ **{to_smallcaps('select upload type')}** ✦\n\n"
        f"• ✏️ **{to_smallcaps('new file name')}** : `{new_name}`\n\n"
        f"💡 {to_smallcaps('tap fast rename (auto) or choose whether to send the file as a streamable video or a raw document.')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^renameto_(doc|vid|auto)_(\d+)"))
async def execute_rename_operation(client: Client, query: CallbackQuery):
    upload_type = query.matches[0].group(1)
    target_id = int(query.matches[0].group(2))
    user_id = query.from_user.id if query.from_user else query.message.chat.id

    new_name = await cache.get(f"rename_name_{user_id}_{target_id}")
    if not new_name and query.message and getattr(query.message, "text", None):
        m = re.search(r":\s*`?([^`\n]+)`?", query.message.text)
        if m:
            new_name = sanitize_filename(m.group(1).strip())
            await cache.set(f"rename_name_{user_id}_{target_id}", new_name, ttl=1800)

    if not new_name:
        return await query.answer(to_smallcaps("session expired! please try again."), show_alert=True)

    original_msg = await client.get_messages(query.message.chat.id, target_id)
    if not original_msg or not original_msg.media:
        await query.answer(to_smallcaps("original media not found!"), show_alert=True)
        return await safe_edit_text(query.message, f"❌ **{to_smallcaps('original media no longer exists!')}**")

    if upload_type == "auto":
        u = await user_repo.get_user(user_id)
        media = original_msg.document or original_msg.video or original_msg.audio
        mime_t = normalize_mime(getattr(media, "mime_type", ""))
        is_vid = bool(original_msg.video or is_video_mime(mime_t))
        upload_type = "vid" if (is_vid and (not u or u.get("upload_mode", "video") == "video")) else "doc"

    await query.answer("⚡ sᴛᴀʀᴛɪɴɢ ғᴀsᴛ ʀᴇɴᴀᴍᴇ...")
    await run_rename_workflow(client, original_msg, query.message, user_id, target_id, new_name, upload_type, is_edit=True)


@bounded_media_job
async def run_rename_workflow(
    client: Client,
    original_msg: Message,
    reply_msg: Message,
    user_id: int,
    target_id: int,
    new_name: str,
    upload_type: str = "vid",
    watch_url: str = "",
    download_url: str = "",
    is_edit: bool = False,
    task_id: Optional[str] = None,
    is_recovery: bool = False,
) -> Optional[Dict[str, Any]]:
    source_media = original_msg.document or original_msg.video or original_msg.audio
    if getattr(source_media, "file_size", 0) > MAX_BOT_FILE_SIZE:
        return await render_media_studio_card(client, original_msg, user_id, target_msg=reply_msg if is_edit else None, is_edit=is_edit)

    chat_id = original_msg.chat.id if getattr(original_msg, "chat", None) else user_id
    media = source_media
    u = await user_repo.get_user(user_id)
    orig_file_name = getattr(media, "file_name", None) or "file.mp4"
    expected_size = int(getattr(media, "file_size", 0) or 0)
    name_parts = os.path.splitext(new_name)
    if not name_parts[1]:
        orig_ext = os.path.splitext(orig_file_name)[1] or ".mp4"
        new_name = f"{name_parts[0]}{orig_ext}"
    final_name = apply_rename_template(new_name, u or {}, media)

    if not task_id:
        task_id = f"ren_{user_id}_{target_id}_{secrets.token_hex(3)}"
        task_manager.record_metric(detected=1)

    existing_doc = await task_repo.get_task(task_id)
    if existing_doc and existing_doc.get("work_dir"):
        download_dir = existing_doc["work_dir"]
    else:
        download_dir = os.path.abspath(os.path.join(Config.DOWNLOAD_DIR, "persistent_tasks", task_id))
    os.makedirs(download_dir, exist_ok=True)
    download_path = (
        existing_doc.get("output_path")
        if (existing_doc and existing_doc.get("output_path"))
        else os.path.join(download_dir, final_name)
    )

    init_text = (
        f"📥 **{to_smallcaps('fast downloading file')}**...\n\n"
        f"[▱▱▱▱▱▱▱▱▱▱] `0%`\n"
        f"📦 **{to_smallcaps('target')}** : `{final_name}`"
        f"{format_watermark()}"
    )
    status_msg = await safe_edit_text(reply_msg, init_text) if is_edit else await safe_reply_text(reply_msg, init_text)
    if status_msg is None and client:
        status_msg = await safe_send_message(client, chat_id=chat_id, text=init_text)

    params = {"new_name": new_name, "final_name": final_name, "upload_type": upload_type}
    if not existing_doc:
        await task_repo.create_task(
            task_id=task_id,
            task_type="rename",
            user_id=user_id,
            chat_id=chat_id,
            original_message_id=target_id,
            status_chat_id=status_msg.chat.id if (status_msg and getattr(status_msg, "chat", None)) else chat_id,
            status_message_id=status_msg.id if status_msg else 0,
            file_name=final_name,
            file_size=expected_size,
            mime_type=getattr(media, "mime_type", "video/mp4") or "video/mp4",
            tg_file_id=getattr(media, "file_id", None),
            work_dir=download_dir,
            input_path=download_path,
            output_path=download_path,
            params=params,
            idempotency_key=f"rename:{chat_id}:{target_id}:{final_name}:{upload_type}",
            max_retries=Config.TASK_MAX_RETRIES,
        )
    else:
        await task_repo.update_task(
            task_id,
            {"status": "running", "recovered": bool(is_recovery or existing_doc.get("recovered", False))},
        )

    await task_manager.register_task(
        task_id=task_id,
        user_id=user_id,
        task_type="rename",
        work_dir=download_dir,
        asyncio_task=asyncio.current_task(),
        stage="downloading",
        params=params,
    )

    start_time = time.time()
    source_mime = normalize_mime(getattr(source_media, "mime_type", ""))
    is_video_media = bool(getattr(original_msg, "video", None)) or is_video_mime(source_mime)
    tg_dur = int(getattr(source_media, "duration", 0) or 0)
    tg_w = int(getattr(source_media, "width", 0) or 1280)
    tg_h = int(getattr(source_media, "height", 0) or 720)
    tg_thumb_fid = getattr(source_media.thumbs[0], "file_id", None) if getattr(source_media, "thumbs", None) else None

    early_thumb_task = asyncio.create_task(
        resolve_thumbnail(
            client=client,
            user_id=user_id,
            temp_dir=download_dir,
            video_path=None,
            duration=tg_dur,
            fallback_tg_thumb_id=tg_thumb_fid
        )
    ) if is_video_media else None

    try:
        already_dl = os.path.exists(download_path) and os.path.getsize(download_path) > 0 and (
            expected_size <= 0 or os.path.getsize(download_path) >= expected_size
        )
        if not already_dl:
            try:
                await download_media_with_auth_recovery(
                    client=client,
                    message=original_msg,
                    file_name=download_path,
                    progress=progress_for_pyrogram,
                    progress_args=("📥 fast downloading file", status_msg, start_time)
                )
            except Exception as e:
                if early_thumb_task:
                    early_thumb_task.cancel()
                await task_repo.record_task_error(task_id, f"Download error: {e}", stage="downloading")
                await task_repo.update_task(task_id, {"status": "failed", "stage": "downloading"})
                task_manager.record_metric(failed=1, error=f"Rename download failed: {e}")
                clean_temp_files(download_path, download_dir)
                await safe_edit_text(status_msg, f"❌ **{to_smallcaps('download failed')}** : `{e}`")
                return await task_repo.get_task(task_id)

        if not os.path.exists(download_path):
            if early_thumb_task:
                early_thumb_task.cancel()
            await task_repo.update_task(task_id, {"status": "failed", "stage": "downloading"})
            task_manager.record_metric(failed=1, error="File not found after rename download")
            clean_temp_files(download_dir)
            await safe_edit_text(status_msg, f"❌ **{to_smallcaps('file not found after download!')}**")
            return await task_repo.get_task(task_id)

        await task_repo.update_task(task_id, {"status": "running", "stage": "downloaded", "progress_pct": 50.0})
        task_manager.update_stage(task_id, "downloaded", "running")

        if is_video_media:
            thumb_path = await early_thumb_task if early_thumb_task else None
            if not isinstance(thumb_path, str) or not os.path.exists(thumb_path):
                thumb_path = await resolve_thumbnail(
                    client=client,
                    user_id=user_id,
                    temp_dir=download_dir,
                    video_path=download_path,
                    duration=tg_dur
                )
            user_tpl = (u.get("custom_caption") or "") if u else ""
            needs_probe = (tg_dur <= 0) or ("{language}" in user_tpl) or ("{audio_track}" in user_tpl)
            if needs_probe:
                from plugins.core_services import get_detailed_mediainfo
                probe_info = await get_detailed_mediainfo(download_path)
            else:
                probe_info = {"duration": tg_dur, "video_streams": [{"width": tg_w, "height": tg_h}], "audio_streams": []}
            v_streams = (probe_info or {}).get("video_streams") or []
            dur = int((probe_info or {}).get("duration") or tg_dur)
            w = int(v_streams[0].get("width") if v_streams and v_streams[0].get("width") else tg_w)
            h = int(v_streams[0].get("height") if v_streams and v_streams[0].get("height") else tg_h)
            attrs = {"width": w, "height": h, "duration": dur}
            if not isinstance(thumb_path, str) or not os.path.exists(thumb_path):
                thumb_path = None
        else:
            probe_info = {"duration": tg_dur, "video_streams": [], "audio_streams": []}
            attrs = {"width": 0, "height": 0, "duration": tg_dur}
            dur = tg_dur
            thumb_path = None

        orig_caption = getattr(original_msg, "caption", None) or ""
        final_caption = await extract_and_format_caption(
            user_id=user_id,
            file_path=download_path,
            original_caption=orig_caption,
            file_id=str(target_id),
            metadata=probe_info
        )

        await task_repo.update_task(task_id, {"status": "running", "stage": "uploading", "progress_pct": 75.0})
        task_manager.update_stage(task_id, "uploading", "running")

        upload_start = time.time()
        await safe_edit_text(status_msg, f"📤 **{to_smallcaps('uploading renamed file...')}**")

        is_audio_media = bool(getattr(original_msg, "audio", None)) or is_audio_mime(source_mime)
        target_chat_id = reply_msg.chat.id if (reply_msg and getattr(reply_msg, "chat", None)) else chat_id
        sent_msg = None
        max_retries = int(Config.TASK_MAX_RETRIES)

        from utils import temp as _t
        attach_stream = bool(_t.BOT_CONFIG.get("auto_stream_global", True) and (not u or u.get("auto_stream", True)))
        new_fid = secrets.token_urlsafe(10) if attach_stream else ""
        w_url = f"{Config.BASE_URL}/watch/{new_fid}" if new_fid else ""
        d_url = f"{Config.BASE_URL}/download/{new_fid}" if new_fid else ""

        cur_thumb = thumb_path
        for up_attempt in range(max_retries):
            try:
                if upload_type == "vid" and is_video_media:
                    w = int(attrs.get("width", 1280)) or 1280
                    h = int(attrs.get("height", 720)) or 720
                    try:
                        sent_msg = await execute_with_floodwait(
                            client.send_video,
                            chat_id=target_chat_id,
                            video=download_path,
                            file_name=final_name,
                            caption=final_caption,
                            duration=dur,
                            width=w,
                            height=h,
                            thumb=cur_thumb,
                            supports_streaming=True,
                            progress=progress_for_pyrogram,
                            progress_args=("📤 uploading video", status_msg, upload_start)
                        )
                    except FloodWait as error:
                        wait_s = _flood_wait_seconds(error)
                        await pro_floodwait_sleep(wait_s, f"rename_upload#{up_attempt+1}", chat_id=target_chat_id)
                        continue
                    except Exception:
                        sent_msg = await execute_with_floodwait(
                            client.send_document,
                            chat_id=target_chat_id,
                            document=download_path,
                            file_name=final_name,
                            caption=final_caption,
                            thumb=None,
                            force_document=True,
                            progress=progress_for_pyrogram,
                            progress_args=("📤 uploading document", status_msg, upload_start)
                        )
                elif upload_type == "vid" and is_audio_media:
                    sent_msg = await execute_with_floodwait(
                        client.send_audio,
                        chat_id=target_chat_id,
                        audio=download_path,
                        file_name=final_name,
                        caption=final_caption,
                        duration=dur,
                        thumb=cur_thumb,
                        progress=progress_for_pyrogram,
                        progress_args=("📤 uploading audio", status_msg, upload_start)
                    )
                else:
                    sent_msg = await execute_with_floodwait(
                        client.send_document,
                        chat_id=target_chat_id,
                        document=download_path,
                        file_name=final_name,
                        caption=final_caption,
                        thumb=cur_thumb,
                        force_document=True,
                        progress=progress_for_pyrogram,
                        progress_args=("📤 uploading document", status_msg, upload_start)
                    )
                if sent_msg is not None:
                    break
            except FloodWait as fw:
                wait_s = _flood_wait_seconds(fw)
                await pro_floodwait_sleep(wait_s, f"rename_upload_outer#{up_attempt+1}", chat_id=target_chat_id)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                cur_thumb = None
                await task_repo.record_task_error(task_id, f"Rename upload attempt {up_attempt+1} failed: {e}", stage="uploading")
                if up_attempt >= max_retries - 1:
                    clean_temp_files(download_path, thumb_path, download_dir)
                    await task_repo.update_task(task_id, {"status": "failed", "stage": "uploading"})
                    task_manager.record_metric(failed=1, error=f"Rename upload failed: {e}")
                    await safe_edit_text(status_msg, f"❌ **{to_smallcaps('upload failed')}** : `{e}`")
                    return await task_repo.get_task(task_id)
                await asyncio.sleep(1.5 * (up_attempt + 1))

        logger.info(
            "✅ ʀᴇɴᴀᴍᴇ ᴡᴏʀᴋғʟᴏᴡ ᴄᴏᴍᴘʟᴇᴛᴇ › ᴛᴀsᴋ=%s ᴜsᴇʀ=%s ᴏʀɪɢ='%s' ɴᴇᴡ='%s' ᴇʟᴀᴘsᴇᴅ=%.1fs",
            task_id,
            user_id,
            orig_file_name,
            final_name,
            time.time() - start_time,
        )

        if sent_msg and attach_stream and new_fid:
            try:
                out_media = sent_msg.video or sent_msg.document or sent_msg.audio
                await file_repo.save_file(
                    file_id=new_fid,
                    chat_id=sent_msg.chat.id,
                    message_id=sent_msg.id,
                    file_name=final_name,
                    file_size=getattr(out_media, "file_size", expected_size),
                    mime_type=getattr(out_media, "mime_type", "video/mp4"),
                    user_id=user_id,
                    metadata=telegram_media_metadata(out_media, sent_msg),
                    tg_file_id=getattr(out_media, "file_id", None)
                )
                stream_kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(f"🎬 {to_smallcaps('watch (4 players)')}", url=w_url),
                        InlineKeyboardButton(f"⬇️ {to_smallcaps('fast download')}", url=d_url),
                    ],
                    [
                        InlineKeyboardButton(f"⚡ {to_smallcaps('stream card')}", callback_data=f"stream_{sent_msg.id}"),
                    ]
                ])
                await sent_msg.edit_reply_markup(reply_markup=stream_kb)
                bin_chan = Config.BIN_CHANNEL if Config.BIN_CHANNEL != 0 else sent_msg.chat.id
                if bin_chan != sent_msg.chat.id:
                    async def _bg_fwd_ren(fid_val: str, s_msg: Message, b_ch: int):
                        try:
                            fwd = await safe_forward_message(s_msg, b_ch)
                            if fwd and getattr(fwd, "chat", None) and getattr(fwd, "id", None):
                                await file_repo.update_file(fid_val, {"chat_id": fwd.chat.id, "message_id": fwd.id})
                        except Exception:
                            pass
                    asyncio.create_task(_bg_fwd_ren(new_fid, sent_msg, bin_chan))
            except Exception:
                pass

        completed_doc = await task_repo.update_task(
            task_id,
            {
                "status": "completed",
                "stage": "completed",
                "progress_pct": 100.0,
                "output_message_id": getattr(sent_msg, "id", 0),
                "output_file_id": new_fid,
                "completed_at": float(time.time()),
            },
        )
        task_manager.update_stage(task_id, "completed", "completed")
        task_manager.record_metric(completed=1, recovered=(1 if is_recovery else 0))

        clean_temp_files(download_path, thumb_path, download_dir)
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

        del_time = u.get("auto_delete_time", 0) if u else 0
        if del_time > 0 and sent_msg:
            await schedule_deletion(sent_msg.chat.id, sent_msg.id, del_time)

        return completed_doc

    except asyncio.CancelledError:
        t_obj = task_manager.get_task(task_id)
        if t_obj and t_obj.is_cancelled:
            await task_repo.update_task(task_id, {"status": "cancelled", "stage": "cancelled"})
            clean_temp_files(download_path, download_dir)
        else:
            await task_repo.update_task(task_id, {"status": "interrupted"})
        raise
    finally:
        t_obj = task_manager.get_task(task_id)
        preserve_ws = bool(t_obj and not t_obj.is_cancelled and t_obj.status != "completed")
        await task_manager.cleanup_task(task_id, preserve_workspace=preserve_ws)
