import os
import re
import time
import secrets
import asyncio
from typing import Optional, Dict, Any, List
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
from info import COMMAND_PREFIXES, LOG_CHANNEL
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
    get_detailed_mediainfo,
    telegram_media_metadata,
    compress_media,
    compress_audio,
    compress_to_target_size,
    remux_container,
    validate_media_output,
    check_media_streams,
    resolve_thumbnail,
    extract_and_format_caption,
    schedule_deletion,
    download_media_with_auth_recovery,
    get_ffmpeg_status,
    analyze_code_file,
    optimize_code_file,
    compress_file_to_zip,
)
from plugins.media_types import is_code_file, detect_code_language, is_audio_mime, is_video_mime, normalize_mime, extract_message_media

MAX_BOT_FILE_SIZE = int(getattr(Config, "LARGE_FILE_THRESHOLD_BYTES", 2 * 1024 * 1024 * 1024))
_RECOVERY_LOCK = None
_RECOVERY_LOCK_LOOP = None


def _get_recovery_lock() -> asyncio.Lock:
    global _RECOVERY_LOCK, _RECOVERY_LOCK_LOOP
    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        curr_loop = None
    if _RECOVERY_LOCK is None or _RECOVERY_LOCK_LOOP is not curr_loop:
        _RECOVERY_LOCK = asyncio.Lock()
        _RECOVERY_LOCK_LOOP = curr_loop
    return _RECOVERY_LOCK
_IN_FLIGHT_IDEMPOTENCY: Dict[str, str] = {}


@Client.on_message(filters.command(["ffmpeg", "ffmpeginfo"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def ffmpeg_status_cmd(client: Client, message: Message):
    """Display active FFmpeg & FFprobe binary status, version, codecs, and task recovery engine state."""
    st = await get_ffmpeg_status()
    badge = "🟢 ʀᴇᴀᴅʏ" if st.get("ok") else "🔴 ɴᴏᴛ ғᴏᴜɴᴅ"
    rep = task_manager.get_recovery_report()
    await safe_reply_text(
        message,
        f"✦ **{to_smallcaps('ffmpeg & media encoder status')}** ✦ 🗜️\n\n"
        f"• 🔋 **{to_smallcaps('status')}** : `{badge}`\n"
        f"• 🛠️ **{to_smallcaps('ffmpeg binary')}** : `{st.get('ffmpeg_bin', 'ffmpeg')}`\n"
        f"• 🔍 **{to_smallcaps('ffprobe binary')}** : `{st.get('ffprobe_bin', 'ffprobe')}`\n"
        f"• 📦 **{to_smallcaps('version')}** : `{st.get('version', 'Unknown')}`\n"
        f"• 🎞️ **{to_smallcaps('video codecs')}** : `libx264 (H.264) • {'libx265 (H.265/HEVC) • ' if st.get('has_h265') else ''}yuv420p • +faststart`\n"
        f"• 🎵 **{to_smallcaps('audio codecs')}** : `AAC • libmp3lame (MP3)`\n"
        f"• ♻️ **{to_smallcaps('task recovery')}** : `Active (Completed: {rep['completed_tasks']} • Recovered: {rep['recovered_tasks']})`\n\n"
        f"💡 {to_smallcaps('use /compress on any video/audio or /autocompress to toggle automatic compression!')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"♻️ {to_smallcaps('recovery report')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
        ]])
    )


@Client.on_message(filters.command(["recover", "taskreport", "recoveryreport"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def task_recovery_report_cmd(client: Client, message: Message):
    """Trigger immediate check/recovery of incomplete tasks and display the execution report."""
    cmd = message.command[0].lower()
    if cmd == "recover":
        status = await safe_reply_text(message, f"♻️ **{to_smallcaps('scanning & recovering incomplete media tasks...')}**")
        report = await recover_and_complete_pending_tasks(client, trigger_source="manual_command")
        return await safe_edit_text(
            status,
            format_task_execution_report_text(report),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🔄 {to_smallcaps('refresh report')}", callback_data="task_rec#report"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ]])
        )

    report = await generate_task_execution_report()
    await safe_reply_text(
        message,
        format_task_execution_report_text(report),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"♻️ {to_smallcaps('run recovery now')}", callback_data="task_rec#run"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ]])
    )


@Client.on_callback_query(filters.regex(r"^task_rec#(run|report)$"))
async def task_recovery_callback(client: Client, query: CallbackQuery):
    act = query.matches[0].group(1)
    if act == "run":
        await query.answer("♻️ sᴄᴀɴɴɪɴɢ & ʀᴇᴄᴏᴠᴇʀɪɴɢ ᴘᴇɴᴅɪɴɢ ᴛᴀsᴋs...")
        report = await recover_and_complete_pending_tasks(client, trigger_source="callback")
    else:
        await query.answer("📊 ᴛᴀsᴋ ᴇxᴇᴄᴜᴛɪᴏɴ ʀᴇᴘᴏʀᴛ")
        report = await generate_task_execution_report()

    await safe_edit_text(
        query.message,
        format_task_execution_report_text(report),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"♻️ {to_smallcaps('run recovery')}", callback_data="task_rec#run"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ]])
    )


@Client.on_message(filters.private & filters.command(["autocompress", "auto_compress"], prefixes=COMMAND_PREFIXES))
async def toggle_autocompress_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    u = await user_repo.get_or_create(user_id, message.from_user.first_name, message.from_user.username)
    new_state = not bool(u.get("auto_compress", False))
    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        new_state = arg in ("on", "true", "1", "yes", "enable")
    await user_repo.update_user(user_id, {"auto_compress": new_state})
    state_txt = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_state else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    await safe_reply_text(
        message,
        f"✦ **{to_smallcaps('auto-compressor mode (ffmpeg)')}** ✦\n\n"
        f"• 🗜️ **{to_smallcaps('status')}** : `{state_txt}`\n"
        f"• ⚙️ **{to_smallcaps('default preset')}** : `720p HD (Superfast CRF 28)`\n"
        f"• 📦 **{to_smallcaps('> 2gb files')}** : `{to_smallcaps('auto-prompts for compress or file stream')}`\n\n"
        f"💡 {to_smallcaps('when enabled, incoming videos <= 2gb are automatically compressed with H.264 + custom thumbnail, caption & 4-player stream links!')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⚙️ {to_smallcaps('settings')}", callback_data="nav_settings"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home")
        ]])
    )


async def run_auto_compress_workflow(client: Client, media_msg: Message, user_id: int):
    u = await user_repo.get_user(user_id)
    res_raw = str((u.get("resolution") if u else "720p") or "720p").lower().replace("p", "").strip()
    scale_h = int(res_raw) if res_raw.isdigit() else 720
    preset_raw = str((u.get("compression_preset") if u else "turbo") or "turbo").lower()
    if preset_raw not in ("hyper", "turbo", "ultrafast", "superfast", "veryfast", "fast", "hevc"):
        preset_raw = "turbo"
    status = await safe_reply_text(
        media_msg,
        f"🗜️ **{to_smallcaps(f'auto-compressing media with ffmpeg ({scale_h}p {preset_raw})...')}**",
        quote=True
    )
    return await perform_compression_job(
        client=client,
        original_msg=media_msg,
        status=status,
        user_id=user_id,
        scale_h=scale_h,
        crf=28,
        preset=preset_raw,
        is_target_mode=False,
        target_mb=None
    )


def parse_custom_compress_input(text: str, orig_size_bytes: int = 0) -> Dict[str, Any]:
    """
    Parse flexible custom compression input such as:
      - '450MB', '450', '1.2GB', '50%'
      - '500MB 480p', '700MB 720p hyper', '300MB 360p', '1.5GB 1080p fast'
      - '480p crf26', '360p'
    Returns dict with keys: target_mb, scale_h, speed, audio_br, crf, has_custom_res.
    """
    raw = (text or "").strip().lower()
    result: Dict[str, Any] = {
        "target_mb": None,
        "scale_h": None,
        "speed": None,
        "audio_br": None,
        "crf": None,
        "has_custom_res": False,
    }
    if not raw:
        return result

    res_match = re.search(r"\b(240|360|480|540|720|1080)p\b", raw)
    if res_match:
        result["scale_h"] = int(res_match.group(1))
        result["has_custom_res"] = True
        raw = raw[:res_match.start()] + " " + raw[res_match.end():]
    elif re.search(r"\bauto\b", raw):
        result["scale_h"] = 0
        result["has_custom_res"] = False

    crf_match = re.search(r"\bcrf\s*(\d{2})\b", raw)
    if crf_match:
        result["crf"] = max(16, min(40, int(crf_match.group(1))))
        raw = raw[:crf_match.start()] + " " + raw[crf_match.end():]

    aud_match = re.search(r"\b(48k|64k|96k|128k|160k|192k|copy)\b", raw)
    if aud_match:
        result["audio_br"] = aud_match.group(1)
        raw = raw[:aud_match.start()] + " " + raw[aud_match.end():]

    spd_match = re.search(r"\b(hyper|turbo|ultrafast|superfast|veryfast|fast|quality|hevc)\b", raw)
    if spd_match:
        s_tok = spd_match.group(1)
        result["speed"] = "superfast" if s_tok == "quality" else ("hyper" if s_tok == "ultrafast" else s_tok)
        raw = raw[:spd_match.start()] + " " + raw[spd_match.end():]

    pct_match = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*%", raw)
    if pct_match and orig_size_bytes > 0:
        pct_val = max(5.0, min(95.0, float(pct_match.group(1))))
        orig_mb = orig_size_bytes / (1024 * 1024)
        result["target_mb"] = round(max(1.0, min(1900.0, orig_mb * (pct_val / 100.0))), 1)
        raw = raw[:pct_match.start()] + " " + raw[pct_match.end():]

    if result["target_mb"] is None:
        gb_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:gb|g)\b", raw)
        mb_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:mb|m)?\b", raw)
        if gb_match:
            val_mb = float(gb_match.group(1)) * 1024.0
            result["target_mb"] = round(max(1.0, min(1900.0, val_mb)), 1)
        elif mb_match:
            val = float(mb_match.group(1))
            if val <= 4.0 and "." in mb_match.group(1):
                val = val * 1024.0
            if val >= 1.0:
                result["target_mb"] = round(max(1.0, min(1900.0, val)), 1)

    return result


async def _get_or_init_custom_comp_cfg(
    user_id: int,
    msg_id: int,
    orig_size_bytes: int,
    duration_sec: int = 0,
) -> Dict[str, Any]:
    """Retrieve or initialize the user's Custom Ultra-Fast Compressor configuration for a media message."""
    key = f"custcomp_cfg_{user_id}_{msg_id}"
    cfg = await cache.get(key)
    if isinstance(cfg, dict) and cfg.get("target_mb"):
        return cfg

    orig_mb = orig_size_bytes / (1024 * 1024) if orig_size_bytes > 0 else 1000.0
    if orig_mb > 2000:
        default_mb = 700
    elif orig_mb > 1000:
        default_mb = 450
    elif orig_mb > 400:
        default_mb = 250
    else:
        default_mb = max(1.0, round(orig_mb * 0.45, 1))

    cfg = {
        "target_mb": float(default_mb),
        "scale_h": 480 if (duration_sec >= 2400 or orig_mb >= 1200) else 0,
        "speed": "hyper",
        "audio_br": "auto",
        "crf": 28,
        "has_custom_res": bool(duration_sec >= 2400 or orig_mb >= 1200),
    }
    await cache.set(key, cfg, ttl=1800)
    return cfg


@Client.on_message(filters.private & filters.command(["compress", "comp", "customcompress", "ccomp", "fastcompress"], prefixes=COMMAND_PREFIXES))
async def compress_command_handler(client: Client, message: Message):
    from plugins.rename import resolve_target_media_message
    user_id = message.from_user.id if message.from_user else message.chat.id
    target = await resolve_target_media_message(client, message, user_id=user_id)
    if not target or extract_message_media(target)[0] is None:
        return await safe_reply_text(
            message,
            f"⚠️ **{to_smallcaps('please reply to a video or audio file to compress with ffmpeg.')}**\n\n"
            f"• **{to_smallcaps('usage 1')}** : {to_smallcaps('reply with')} `/compress` {to_smallcaps('or')} `/customcompress`\n"
            f"• **{to_smallcaps('usage 2')}** : {to_smallcaps('reply with')} `/compress 450MB 480p` {to_smallcaps('or')} `/compress 700MB`"
            f"{format_watermark()}"
        )

    cmd_name = (message.command[0] if message.command else "compress").lower()
    raw_args = message.text.split(None, 1)[1].strip() if (message.text and len(message.text.split(None, 1)) > 1) else ""
    media = extract_message_media(target)[0]
    orig_size_bytes = int(getattr(media, "file_size", 0) or 0)
    dur_val = int(getattr(media, "duration", 0) or 0)

    if cmd_name in ("customcompress", "ccomp", "fastcompress") or raw_args.lower() == "custom":
        if raw_args and raw_args.lower() != "custom":
            parsed = parse_custom_compress_input(raw_args, orig_size_bytes)
            cfg = await _get_or_init_custom_comp_cfg(user_id, target.id, orig_size_bytes, dur_val)
            if parsed.get("target_mb"):
                cfg["target_mb"] = parsed["target_mb"]
            if parsed.get("scale_h") is not None:
                cfg["scale_h"] = parsed["scale_h"]
                cfg["has_custom_res"] = bool(parsed["scale_h"] > 0)
            if parsed.get("speed"):
                cfg["speed"] = parsed["speed"]
            if parsed.get("audio_br"):
                cfg["audio_br"] = parsed["audio_br"]
            await cache.set(f"custcomp_cfg_{user_id}_{target.id}", cfg, ttl=1800)
        return await show_custom_compress_menu(target, message, user_id=user_id, is_callback=False)

    target_mb = None
    if raw_args:
        parsed = parse_custom_compress_input(raw_args, orig_size_bytes)
        target_mb = parsed.get("target_mb")
        if target_mb or parsed.get("scale_h") is not None:
            cfg = await _get_or_init_custom_comp_cfg(user_id, target.id, orig_size_bytes, dur_val)
            if target_mb:
                cfg["target_mb"] = target_mb
                await cache.set(f"comp_target_{user_id}_{target.id}", target_mb, ttl=600)
            if parsed.get("scale_h") is not None:
                cfg["scale_h"] = parsed["scale_h"]
                cfg["has_custom_res"] = bool(parsed["scale_h"] > 0)
            if parsed.get("speed"):
                cfg["speed"] = parsed["speed"]
            await cache.set(f"custcomp_cfg_{user_id}_{target.id}", cfg, ttl=1800)

    await show_compression_menu(target, message, custom_target_mb=target_mb)


@Client.on_callback_query(filters.regex(r"^compress_menu_(\d+)"))
async def compress_menu_callback(client: Client, query: CallbackQuery):
    msg_id = int(query.matches[0].group(1))
    original_msg = await client.get_messages(query.message.chat.id, msg_id)
    if not original_msg or not original_msg.media:
        return await query.answer(to_smallcaps("media not found!"), show_alert=True)
    await query.answer("🗜️ ғғᴍᴘᴇɢ ᴄᴏᴍᴘʀssᴏʀ")
    await show_compression_menu(original_msg, query.message, is_callback=True)


async def show_compression_menu(media_msg: Message, target_msg: Message, is_callback: bool = False, custom_target_mb: float = None):
    msg_id = media_msg.id
    media = extract_message_media(media_msg)[0]
    raw_file_name = getattr(media, "file_name", None) or "video.mp4"
    file_name = raw_file_name.replace("`", "'")
    file_size_bytes = getattr(media, "file_size", 0)
    file_size = humanbytes(file_size_bytes)
    dur_val = int(getattr(media, "duration", 0) or 0)
    dur_str = time_formatter(seconds=dur_val) if dur_val > 0 else "Auto-Detect"
    is_oversized = file_size_bytes > MAX_BOT_FILE_SIZE
    mime_t = normalize_mime(getattr(media, "mime_type", ""))
    is_code = is_code_file(raw_file_name, mime_t)
    is_vid_or_aud = bool(media_msg.video or media_msg.audio or is_video_mime(mime_t) or is_audio_mime(mime_t)) and not is_code

    btn = []
    if is_code:
        code_lang = detect_code_language(raw_file_name, mime_t).upper()
        btn.append([
            InlineKeyboardButton(f"⚡ ᴍɪɴɪғʏ & ᴏᴘᴛɪᴍɪᴢᴇ {code_lang} ᴄᴏᴅᴇ", callback_data=f"do_comp_{msg_id}_0_0_codeopt"),
        ])
        btn.append([
            InlineKeyboardButton("🗜️ ᴄᴏᴍᴘʀᴇss ᴛᴏ .ᴢɪᴘ ᴀʀᴄʜɪᴠᴇ (ʟ9)", callback_data=f"do_comp_{msg_id}_0_0_zip"),
            InlineKeyboardButton("💻 ᴄᴏᴅᴇ ᴘʀᴏ sᴛᴜᴅɪᴏ", callback_data=f"codepro_menu_{msg_id}"),
        ])
    elif not is_vid_or_aud:
        btn.append([
            InlineKeyboardButton("🗜️ ᴄᴏᴍᴘʀᴇss ғɪʟᴇ ᴛᴏ .ᴢɪᴘ (ғᴀsᴛ ʟ9)", callback_data=f"do_comp_{msg_id}_0_0_zip"),
        ])

    btn.append([
        InlineKeyboardButton("🎛️ ᴄᴜsᴛᴏᴍ ᴄᴏᴍᴘʀᴇss (sɪᴢᴇ & ǫᴜᴀʟɪᴛʏ ⚡ 5-10ᴍ)", callback_data=f"custcomp_open_{msg_id}"),
    ])
    if is_oversized:
        btn.append([
            InlineKeyboardButton("🎯 ᴀᴜᴛᴏ-ғɪᴛ < 2ɢʙ (1.85 ɢʙ ғғᴍᴘᴇɢ)", callback_data=f"do_comptarget_{msg_id}_1850"),
        ])

    btn.extend([
        [
            InlineKeyboardButton("⚡ 5-ᴍɪɴ ᴛᴜʀʙᴏ ᴄᴏᴍᴘʀᴇss (ғᴀsᴛᴇsᴛ)", callback_data=f"do_comp_{msg_id}_480_30_hyper"),
        ],
        [
            InlineKeyboardButton("⚡ ғᴀsᴛ (720ᴘ ʜᴅ)", callback_data=f"do_comp_{msg_id}_720_28_superfast"),
            InlineKeyboardButton("🚀 480ᴘ sᴅ (ᴜʟᴛʀᴀ ғᴀsᴛ)", callback_data=f"do_comp_{msg_id}_480_30_ultrafast")
        ],
        [
            InlineKeyboardButton("📦 sᴍᴀʟʟ sɪᴢᴇ (360ᴘ)", callback_data=f"do_comp_{msg_id}_360_32_hyper"),
            InlineKeyboardButton("🎚️ 1080ᴘ (ғᴜʟʟ ʜᴅ)", callback_data=f"do_comp_{msg_id}_1080_24_veryfast")
        ],
        [
            InlineKeyboardButton("⚖️ ʙᴀʟᴀɴᴄᴇᴅ (ᴄʀғ 24)", callback_data=f"do_comp_{msg_id}_0_24_fast"),
            InlineKeyboardButton("💎 ʜɪɢʜ ǫᴜᴀʟɪᴛʏ (ᴄʀғ 20)", callback_data=f"do_comp_{msg_id}_0_20_fast")
        ],
        [
            InlineKeyboardButton("🧬 ʜ.265 ʜᴇᴠᴄ (720ᴘ)", callback_data=f"do_comp_{msg_id}_720_28_hevc"),
            InlineKeyboardButton("🔄 ғᴀsᴛ ʀᴇᴍᴜx (ᴍᴘ4)", callback_data=f"do_comp_{msg_id}_0_0_remux")
        ],
        [
            InlineKeyboardButton("🎯 ᴛᴀʀɢᴇᴛ: 700 ᴍʙ", callback_data=f"do_comptarget_{msg_id}_700"),
            InlineKeyboardButton("🎯 ᴛᴀʀɢᴇᴛ: 350 ᴍʙ", callback_data=f"do_comptarget_{msg_id}_350")
        ]
    ])

    t_mb = custom_target_mb or await cache.get(f"comp_target_{target_msg.chat.id}_{msg_id}")
    if t_mb and int(t_mb) not in (1850, 700, 350):
        btn.append([
            InlineKeyboardButton(f"🎯 ᴄᴜsᴛᴏᴍ ᴛᴀʀɢᴇᴛ: {int(t_mb)} ᴍʙ", callback_data=f"do_comptarget_{msg_id}_{int(t_mb)}")
        ])

    btn.append([
        InlineKeyboardButton(f"⚡ {to_smallcaps('file stream link')}", callback_data=f"stream_{msg_id}"),
        InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"back_to_media_{msg_id}"),
    ])
    btn.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")
    ])

    oversize_note = (
        f"\n⚠️ **{to_smallcaps('oversized file (> 2gb) detected')}** : "
        f"{to_smallcaps('use custom compress or auto-fit below to compress any 3gb+ movie in 5–10 mins!')}\n"
        if is_oversized else ""
    )

    text = (
        f"✦ **{to_smallcaps('ffmpeg pro video & audio compressor')}** ✦ 🗜️\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_name}`\n"
        f"• 📦 **{to_smallcaps('current size')}** : `{file_size}`\n"
        f"• ⏱️ **{to_smallcaps('duration')}** : `{dur_str}`\n"
        f"{oversize_note}\n"
        f"💡 **{to_smallcaps('tap custom compress (5-10m) to choose your exact size & quality, or pick a preset:')}**"
        f"{format_watermark()}"
    )
    if is_callback:
        await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(btn))
    else:
        await safe_reply_text(target_msg, text, reply_markup=InlineKeyboardMarkup(btn))


async def show_custom_compress_menu(
    media_msg: Message,
    target_msg: Message,
    user_id: int,
    is_callback: bool = True,
):
    """
    Render the interactive Custom Ultra-Fast Compression Studio where users can select
    or type their exact desired Target Size (MB/GB/%), Quality/Resolution (Auto/360p/480p/540p/720p/1080p),
    Speed Engine (5-10m Hyper Turbo / 10-15m Fast / 15-20m HD), and Audio Bitrate.
    """
    msg_id = media_msg.id
    media = extract_message_media(media_msg)[0]
    raw_file_name = getattr(media, "file_name", None) or "video.mp4"
    file_name = raw_file_name.replace("`", "'")
    orig_size_bytes = int(getattr(media, "file_size", 0) or 0)
    file_size_str = humanbytes(orig_size_bytes)
    dur_val = int(getattr(media, "duration", 0) or 0)
    if dur_val <= 0 and orig_size_bytes > 5 * 1024 * 1024:
        est_dur = max(60, int(orig_size_bytes / (280 * 1024)))
        dur_str = f"~{time_formatter(seconds=est_dur)} (Auto)"
    else:
        est_dur = max(60, dur_val) if dur_val > 0 else 1800
        dur_str = time_formatter(seconds=dur_val) if dur_val > 0 else "Auto-Detect"

    cfg = await _get_or_init_custom_comp_cfg(user_id, msg_id, orig_size_bytes, dur_val)
    target_mb = float(cfg.get("target_mb") or 500.0)
    scale_h = int(cfg.get("scale_h") or 0)
    speed = str(cfg.get("speed") or "hyper")
    audio_br = str(cfg.get("audio_br") or "auto")

    orig_mb = orig_size_bytes / (1024 * 1024) if orig_size_bytes > 0 else 1000.0
    saved_pct = round(max(0.0, (1.0 - (target_mb / max(0.1, orig_mb))) * 100.0), 1) if orig_mb > target_mb else 0.0
    total_kbps = int((target_mb * 8192.0 * 0.94) / max(1, est_dur))
    est_v_kbps = max(65, total_kbps - 64)

    res_label = f"{scale_h}p" if scale_h > 0 else "Auto Smart"
    speed_labels = {
        "hyper": "⚡ 5–10 Min Hyper Turbo (15x–25x)",
        "turbo": "🚀 10–15 Min Fast Turbo (10x–15x)",
        "superfast": "💎 15–20 Min HD Quality (8x–12x)",
    }
    speed_short = {
        "hyper": "⚡ 5-10m",
        "turbo": "🚀 10-15m",
        "superfast": "💎 15-20m",
    }.get(speed, "⚡ 5-10m")

    def _chk(cond: bool) -> str:
        return "✅ " if cond else ""

    btn = [
        [
            InlineKeyboardButton(
                f"🚀 sᴛᴀʀᴛ ᴄᴜsᴛᴏᴍ ᴄᴏᴍᴘʀᴇss ({int(target_mb) if target_mb >= 5 else target_mb}ᴍʙ • {res_label} • {speed_short})",
                callback_data=f"custcomp_run_{msg_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ ᴛʏᴘᴇ ᴄᴜsᴛᴏᴍ sɪᴢᴇ / ǫᴜᴀʟɪᴛʏ (ᴇ.ɢ. 450MB 480p)",
                callback_data=f"custcomp_ask_{msg_id}",
            )
        ],
        [
            InlineKeyboardButton(f"{_chk(int(target_mb)==150)}150ᴍʙ", callback_data=f"custcomp_sz_{msg_id}_150"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==250)}250ᴍʙ", callback_data=f"custcomp_sz_{msg_id}_250"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==400)}400ᴍʙ", callback_data=f"custcomp_sz_{msg_id}_400"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==500)}500ᴍʙ", callback_data=f"custcomp_sz_{msg_id}_500"),
        ],
        [
            InlineKeyboardButton(f"{_chk(int(target_mb)==700)}700ᴍʙ", callback_data=f"custcomp_sz_{msg_id}_700"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==1000)}1.0ɢʙ", callback_data=f"custcomp_sz_{msg_id}_1000"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==1400)}1.4ɢʙ", callback_data=f"custcomp_sz_{msg_id}_1400"),
            InlineKeyboardButton(f"{_chk(int(target_mb)==1850)}1.85ɢʙ", callback_data=f"custcomp_sz_{msg_id}_1850"),
        ],
        [
            InlineKeyboardButton("📉 25% sɪᴢᴇ", callback_data=f"custcomp_pct_{msg_id}_25"),
            InlineKeyboardButton("📉 40% sɪᴢᴇ", callback_data=f"custcomp_pct_{msg_id}_40"),
            InlineKeyboardButton("📉 50% sɪᴢᴇ", callback_data=f"custcomp_pct_{msg_id}_50"),
            InlineKeyboardButton("📉 70% sɪᴢᴇ", callback_data=f"custcomp_pct_{msg_id}_70"),
        ],
        [
            InlineKeyboardButton(f"{_chk(scale_h==0)}ᴀᴜᴛᴏ", callback_data=f"custcomp_res_{msg_id}_0"),
            InlineKeyboardButton(f"{_chk(scale_h==360)}360ᴘ", callback_data=f"custcomp_res_{msg_id}_360"),
            InlineKeyboardButton(f"{_chk(scale_h==480)}480ᴘ", callback_data=f"custcomp_res_{msg_id}_480"),
            InlineKeyboardButton(f"{_chk(scale_h==540)}540ᴘ", callback_data=f"custcomp_res_{msg_id}_540"),
            InlineKeyboardButton(f"{_chk(scale_h==720)}720ᴘ", callback_data=f"custcomp_res_{msg_id}_720"),
            InlineKeyboardButton(f"{_chk(scale_h==1080)}1080ᴘ", callback_data=f"custcomp_res_{msg_id}_1080"),
        ],
        [
            InlineKeyboardButton(f"{_chk(speed=='hyper')}⚡ 5-10ᴍ ʜʏᴘᴇʀ", callback_data=f"custcomp_spd_{msg_id}_hyper"),
            InlineKeyboardButton(f"{_chk(speed=='turbo')}🚀 10-15ᴍ ғᴀsᴛ", callback_data=f"custcomp_spd_{msg_id}_turbo"),
            InlineKeyboardButton(f"{_chk(speed=='superfast')}💎 15-20ᴍ ʜᴅ", callback_data=f"custcomp_spd_{msg_id}_superfast"),
        ],
        [
            InlineKeyboardButton(f"{_chk(audio_br=='auto')}🎵 ᴀᴜᴛᴏ", callback_data=f"custcomp_aud_{msg_id}_auto"),
            InlineKeyboardButton(f"{_chk(audio_br=='64k')}64ᴋ", callback_data=f"custcomp_aud_{msg_id}_64k"),
            InlineKeyboardButton(f"{_chk(audio_br=='96k')}96ᴋ", callback_data=f"custcomp_aud_{msg_id}_96k"),
            InlineKeyboardButton(f"{_chk(audio_br=='128k')}128ᴋ", callback_data=f"custcomp_aud_{msg_id}_128k"),
            InlineKeyboardButton(f"{_chk(audio_br=='copy')}ᴄᴏᴘʏ", callback_data=f"custcomp_aud_{msg_id}_copy"),
        ],
        [
            InlineKeyboardButton("⚡ 1-ᴛᴀᴘ: 350ᴍʙ • 360ᴘ (5ᴍ)", callback_data=f"custcomp_quick_{msg_id}_350_360_hyper"),
            InlineKeyboardButton("⚡ 1-ᴛᴀᴘ: 500ᴍʙ • 480ᴘ (7ᴍ)", callback_data=f"custcomp_quick_{msg_id}_500_480_hyper"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to presets')}", callback_data=f"compress_menu_{msg_id}"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]

    text = (
        f"✦ **{to_smallcaps('custom ultra-fast compressor studio (5–10 min)')}** ✦ 🎛️⚡\n\n"
        f"• 📁 **{to_smallcaps('file name')}** : `{file_name}`\n"
        f"• 📦 **{to_smallcaps('original size')}** : `{file_size_str}` | ⏱️ **{to_smallcaps('duration')}** : `{dur_str}`\n"
        f"• 🎯 **{to_smallcaps('target size')}** : `{int(target_mb) if target_mb >= 5 else target_mb} MB` (`-{saved_pct}%` {to_smallcaps('reduction')})\n"
        f"• 🎚️ **{to_smallcaps('quality / res')}** : `{res_label}` (`~{est_v_kbps} kbps` video)\n"
        f"• 🎵 **{to_smallcaps('audio mode')}** : `{audio_br.upper()}`\n"
        f"• 🚀 **{to_smallcaps('speed engine')}** : `{speed_labels.get(speed, speed_labels['hyper'])}`\n\n"
        f"💡 **{to_smallcaps('select size, resolution & speed below, then tap start — or tap type custom size/quality:')}**"
        f"{format_watermark()}"
    )

    if is_callback:
        await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(btn))
    else:
        await safe_reply_text(target_msg, text, reply_markup=InlineKeyboardMarkup(btn))


@Client.on_callback_query(
    filters.regex(r"^custcomp_(open|sz|pct|res|spd|aud|ask|run|quick)_(\d+)(?:_([a-zA-Z0-9.]+))?(?:_(\d+)_([a-z]+))?$")
)
async def custom_compress_callback_handler(client: Client, query: CallbackQuery):
    action = query.matches[0].group(1)
    msg_id = int(query.matches[0].group(2))
    arg1 = query.matches[0].group(3)
    arg2 = query.matches[0].group(4)
    arg3 = query.matches[0].group(5)
    user_id = query.from_user.id if query.from_user else query.message.chat.id

    original_msg = await client.get_messages(query.message.chat.id, msg_id)
    if not original_msg or not original_msg.media:
        return await query.answer(to_smallcaps("original media not found!"), show_alert=True)

    media = extract_message_media(original_msg)[0]
    orig_size_bytes = int(getattr(media, "file_size", 0) or 0)
    dur_val = int(getattr(media, "duration", 0) or 0)
    cfg = await _get_or_init_custom_comp_cfg(user_id, msg_id, orig_size_bytes, dur_val)

    if action == "open":
        await query.answer("🎛️ ᴄᴜsᴛᴏᴍ ᴜʟᴛʀᴀ-ғᴀsᴛ ᴄᴏᴍᴘʀᴇssᴏʀ")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "sz" and arg1:
        cfg["target_mb"] = max(1.0, min(1900.0, float(arg1)))
        await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)
        await query.answer(f"🎯 ᴛᴀʀɢᴇᴛ sɪᴢᴇ: {int(cfg['target_mb'])} ᴍʙ")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "pct" and arg1:
        pct = max(10.0, min(90.0, float(arg1)))
        orig_mb = (orig_size_bytes / (1024 * 1024)) if orig_size_bytes > 0 else 1000.0
        cfg["target_mb"] = round(max(1.0, min(1900.0, orig_mb * (pct / 100.0))), 1)
        await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)
        await query.answer(f"📉 {int(pct)}% sɪᴢᴇ ➔ {cfg['target_mb']} ᴍʙ")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "res" and arg1 is not None:
        res_v = int(arg1)
        cfg["scale_h"] = res_v
        cfg["has_custom_res"] = bool(res_v > 0)
        await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)
        await query.answer(f"🎚️ ǫᴜᴀʟɪᴛʏ: {res_v}p" if res_v > 0 else "🎚️ ǫᴜᴀʟɪᴛʏ: ᴀᴜᴛᴏ sᴍᴀʀᴛ")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "spd" and arg1:
        cfg["speed"] = str(arg1).lower()
        await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)
        await query.answer(f"🚀 sᴘᴇᴇᴅ ᴍᴏᴅᴇ: {arg1.upper()}")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "aud" and arg1:
        cfg["audio_br"] = str(arg1).lower()
        await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)
        await query.answer(f"🎵 ᴀᴜᴅɪᴏ: {arg1.upper()}")
        return await show_custom_compress_menu(original_msg, query.message, user_id, is_callback=True)

    if action == "ask":
        await query.answer("✏️ ᴛʏᴘᴇ ᴄᴜsᴛᴏᴍ sɪᴢᴇ & ǫᴜᴀʟɪᴛʏ")
        raw_file_name = getattr(media, "file_name", None) or "video.mp4"
        await cache.set(
            f"waiting_custom_comp_{user_id}",
            {"msg_id": msg_id, "orig_size": orig_size_bytes, "menu_msg_id": query.message.id},
            ttl=900,
        )
        await safe_send_message(
            client,
            chat_id=query.message.chat.id,
            text=(
                f"🎛️ **{to_smallcaps('enter custom size & quality')}** ✦ ⚡\n\n"
                f"• 📁 **{to_smallcaps('file')}** : `{raw_file_name}` (`{humanbytes(orig_size_bytes)}`)\n\n"
                f"💬 **{to_smallcaps('send your desired target size and quality/resolution:')}**\n"
                f"• `{to_smallcaps('examples')}` :\n"
                f"  • `450MB 480p` _(450 MB at 480p in 5–10 mins)_\n"
                f"  • `700MB 720p` _(700 MB at 720p HD)_\n"
                f"  • `300MB 360p` _(300 MB ultra-small fast)_\n"
                f"  • `1.2GB` {to_smallcaps('or')} `500MB` {to_smallcaps('or')} `40%`\n\n"
                f"💡 {to_smallcaps('send')} `/cancel` {to_smallcaps('to abort.')}"
                f"{format_watermark()}"
            ),
            reply_markup=ForceReply(placeholder="e.g. 450MB 480p or 700MB 720p"),
        )
        return

    if action == "quick" and arg1 and arg2 and arg3:
        target_mb = max(1.0, min(1900.0, float(arg1)))
        scale_h = int(arg2)
        speed = str(arg3).lower()
        audio_br = str(cfg.get("audio_br") or "auto")
        respect_res = bool(scale_h > 0)
    else:
        target_mb = max(1.0, min(1900.0, float(cfg.get("target_mb") or 500.0)))
        scale_h = int(cfg.get("scale_h") or 0)
        speed = str(cfg.get("speed") or "hyper")
        audio_br = str(cfg.get("audio_br") or "auto")
        respect_res = bool(cfg.get("has_custom_res") and scale_h > 0)

    await query.answer(f"🚀 sᴛᴀʀᴛɪɴɢ {int(target_mb)}ᴍʙ ᴜʟᴛʀᴀ-ғᴀsᴛ ᴄᴏᴍᴘʀᴇssɪᴏɴ...")
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"❌ {to_smallcaps('cancel compression')}", callback_data="cancel_op")]])
    status = await safe_edit_text(
        query.message,
        f"⚡ **{to_smallcaps('initializing custom ultra-fast ffmpeg engine...')}**\n\n"
        f"• 🎯 **{to_smallcaps('target size')}** : `{target_mb} MB`\n"
        f"• 🎚️ **{to_smallcaps('quality / res')}** : `{f'{scale_h}p' if scale_h > 0 else 'Auto Smart'}`\n"
        f"• 🚀 **{to_smallcaps('speed mode')}** : `{speed.upper()}`",
        reply_markup=cancel_kb,
    )
    await perform_compression_job(
        client=client,
        original_msg=original_msg,
        status=status,
        user_id=user_id,
        scale_h=scale_h,
        crf=int(cfg.get("crf") or 28),
        preset=speed,
        is_target_mode=True,
        target_mb=target_mb,
        audio_bitrate=audio_br,
        respect_custom_height=respect_res,
    )


async def handle_custom_compress_text_input(client: Client, message: Message, wait_data: Any):
    """Handle user text reply for Custom Compress Size & Quality and start ultra-fast compression."""
    user_id = message.from_user.id if message.from_user else message.chat.id
    msg_id = int(wait_data.get("msg_id", 0)) if isinstance(wait_data, dict) else int(wait_data or 0)
    orig_size_bytes = int(wait_data.get("orig_size", 0)) if isinstance(wait_data, dict) else 0

    original_msg = await client.get_messages(message.chat.id, msg_id)
    if not original_msg or not original_msg.media:
        return await safe_reply_text(message, f"❌ **{to_smallcaps('original media message no longer exists!')}**")

    media = extract_message_media(original_msg)[0]
    if orig_size_bytes <= 0:
        orig_size_bytes = int(getattr(media, "file_size", 0) or 0)
    dur_val = int(getattr(media, "duration", 0) or 0)

    cfg = await _get_or_init_custom_comp_cfg(user_id, msg_id, orig_size_bytes, dur_val)
    parsed = parse_custom_compress_input(message.text or "", orig_size_bytes)

    if parsed.get("target_mb") is not None:
        cfg["target_mb"] = parsed["target_mb"]
    if parsed.get("scale_h") is not None:
        cfg["scale_h"] = parsed["scale_h"]
        cfg["has_custom_res"] = bool(parsed["scale_h"] > 0)
    if parsed.get("speed"):
        cfg["speed"] = parsed["speed"]
    if parsed.get("audio_br"):
        cfg["audio_br"] = parsed["audio_br"]
    if parsed.get("crf"):
        cfg["crf"] = parsed["crf"]

    await cache.set(f"custcomp_cfg_{user_id}_{msg_id}", cfg, ttl=1800)

    target_mb = max(1.0, min(1900.0, float(cfg.get("target_mb") or 500.0)))
    scale_h = int(cfg.get("scale_h") or 0)
    speed = str(cfg.get("speed") or "hyper")
    audio_br = str(cfg.get("audio_br") or "auto")
    respect_res = bool(cfg.get("has_custom_res") and scale_h > 0)
    is_target_mode = True
    if parsed.get("target_mb") is None and parsed.get("crf") is not None:
        is_target_mode = False

    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"❌ {to_smallcaps('cancel compression')}", callback_data="cancel_op")]])
    status = await safe_reply_text(
        message,
        f"⚡ **{to_smallcaps('starting custom ultra-fast compression...')}**\n\n"
        f"• 🎯 **{to_smallcaps('target size')}** : `{target_mb} MB`\n"
        f"• 🎚️ **{to_smallcaps('quality / res')}** : `{f'{scale_h}p' if scale_h > 0 else 'Auto Smart'}`\n"
        f"• 🚀 **{to_smallcaps('speed engine')}** : `{speed.upper()}`",
        reply_markup=cancel_kb,
    )
    return await perform_compression_job(
        client=client,
        original_msg=original_msg,
        status=status,
        user_id=user_id,
        scale_h=scale_h,
        crf=int(cfg.get("crf") or 28),
        preset=speed,
        is_target_mode=is_target_mode,
        target_mb=target_mb if is_target_mode else None,
        audio_bitrate=audio_br,
        respect_custom_height=respect_res,
    )


@Client.on_callback_query(filters.regex(r"^do_comp(target)?_(\d+)_(\d+)(?:_(\d+)_([a-z]+))?"))
async def execute_compression(client: Client, query: CallbackQuery):
    is_target_mode = bool(query.matches[0].group(1))
    msg_id = int(query.matches[0].group(2))
    user_id = query.from_user.id

    if is_target_mode:
        target_mb = min(1900.0, float(query.matches[0].group(3)))
        scale_h = 0
        crf = 28
        preset = "hyper"
    else:
        scale_h = int(query.matches[0].group(3))
        crf = int(query.matches[0].group(4))
        preset = query.matches[0].group(5)
        target_mb = None

    original_msg = await client.get_messages(query.message.chat.id, msg_id)
    if not original_msg or not original_msg.media:
        return await query.answer(to_smallcaps("original media not found!"), show_alert=True)

    await query.answer("🗜️ sᴛᴀʀᴛɪɴɢ ғғᴍᴘᴇɢ ᴄᴏᴍᴘʀᴇssɪᴏɴ...")
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"❌ {to_smallcaps('cancel compression')}", callback_data="cancel_op")]])
    status = await safe_edit_text(query.message, f"⏳ **{to_smallcaps('initializing ffmpeg compression workflow...')}**", reply_markup=cancel_kb)
    await perform_compression_job(
        client=client,
        original_msg=original_msg,
        status=status,
        user_id=user_id,
        scale_h=scale_h,
        crf=crf,
        preset=preset,
        is_target_mode=is_target_mode,
        target_mb=target_mb,
        respect_custom_height=bool(scale_h > 0),
    )


@bounded_media_job
async def perform_compression_job(
    client: Client,
    original_msg: Message,
    status: Optional[Message],
    user_id: int,
    scale_h: int,
    crf: int,
    preset: str,
    is_target_mode: bool = False,
    target_mb: Optional[float] = None,
    task_id: Optional[str] = None,
    is_recovery: bool = False,
    audio_bitrate: str = "auto",
    respect_custom_height: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Fault-tolerant, stage-resumable FFmpeg compression job.
    Preserves state across bot restarts, server restarts, container rebuilds, and updates.
    Never marks a task complete until the final compressed media is validated and uploaded.
    """
    msg_id = original_msg.id
    chat_id = original_msg.chat.id if original_msg.chat else user_id
    media = extract_message_media(original_msg)[0]
    raw_name = getattr(media, "file_name", None) or f"video_{msg_id}.mp4"
    clean_name = sanitize_filename(raw_name)
    expected_input_size = int(getattr(media, "file_size", 0) or 0)
    mime_type = getattr(media, "mime_type", "video/mp4") or "video/mp4"
    tg_file_id = getattr(media, "file_id", None)

    idempotency_key = (
        f"compress:{chat_id}:{msg_id}:{scale_h}:{crf}:{preset}:{int(target_mb or 0)}"
    )

    if not is_recovery and not task_id:
        in_flight_tid = _IN_FLIGHT_IDEMPOTENCY.get(idempotency_key)
        if in_flight_tid and task_manager.get_task(in_flight_tid):
            logger.info(f"🛡️ Duplicate compression prevented › joining in-flight task {in_flight_tid}")
            task_manager.record_metric(skipped=1)
            return await task_repo.get_task(in_flight_tid)

        existing_doc = await task_repo.find_by_idempotency_key(idempotency_key)
        if existing_doc and existing_doc.get("status") == "completed" and existing_doc.get("output_message_id"):
            try:
                existing_out = await client.get_messages(chat_id, int(existing_doc["output_message_id"]))
                if existing_out and existing_out.media:
                    logger.info(f"✅ Skipping already-completed task {existing_doc['task_id']} (output msg {existing_out.id})")
                    task_manager.record_metric(skipped=1)
                    if status:
                        await safe_edit_text(
                            status,
                            f"✅ **{to_smallcaps('this compression task was already completed!')}**\n"
                            f"• 🆔 `{existing_doc['task_id']}`"
                            f"{format_watermark()}"
                        )
                    return existing_doc
            except Exception:
                pass

    if not task_id:
        task_id = f"comp_{user_id}_{msg_id}_{scale_h}_{preset}_{secrets.token_hex(3)}"
        task_manager.record_metric(detected=1)

    _IN_FLIGHT_IDEMPOTENCY[idempotency_key] = task_id

    existing_task_doc = await task_repo.get_task(task_id)
    if existing_task_doc and existing_task_doc.get("work_dir"):
        download_dir = existing_task_doc["work_dir"]
    else:
        download_dir = os.path.abspath(os.path.join(Config.DOWNLOAD_DIR, "persistent_tasks", task_id))
    os.makedirs(download_dir, exist_ok=True)
    input_path = (
        existing_task_doc.get("input_path")
        if (existing_task_doc and existing_task_doc.get("input_path"))
        else os.path.join(download_dir, f"in_{clean_name}")
    )

    u = await user_repo.get_user(user_id)
    prefix = (u.get("prefix") if u else "") or ""
    suffix = (u.get("suffix") if u else "") or ""
    base_n, _ = os.path.splitext(clean_name)
    if prefix and not base_n.startswith(prefix):
        base_n = f"{prefix}{base_n}"
    if suffix and not base_n.endswith(suffix):
        base_n = f"{base_n}{suffix}"

    is_hevc = (preset == "hevc")
    eff_preset = "ultrafast" if is_hevc else preset
    vcodec = "libx265" if is_hevc else "libx264"
    res_tag = f"{scale_h}p_" if scale_h > 0 else ""
    codec_tag = "HEVC_" if is_hevc else ""
    orig_ext = os.path.splitext(clean_name)[1] or ".mp4"
    is_code_input = is_code_file(clean_name, mime_type)
    if preset == "codeopt" or (is_code_input and preset not in ("zip", "remux") and not is_target_mode):
        default_out_name = sanitize_filename(f"optimized_{base_n}{orig_ext}")
    elif preset == "zip":
        default_out_name = sanitize_filename(f"compressed_{base_n}{orig_ext}.zip")
    elif is_target_mode and target_mb:
        safe_target_mb = min(1900.0, float(target_mb))
        default_out_name = sanitize_filename(f"compressed_{int(safe_target_mb)}MB_{res_tag}{base_n}.mp4")
    elif preset == "remux":
        default_out_name = sanitize_filename(f"remuxed_{base_n}.mp4")
    else:
        default_out_name = sanitize_filename(f"compressed_{codec_tag}{res_tag or 'CRF_'}{base_n}.mp4")

    output_path = (
        existing_task_doc.get("output_path")
        if (existing_task_doc and existing_task_doc.get("output_path"))
        else os.path.join(download_dir, default_out_name)
    )

    status_chat_id = status.chat.id if (status and getattr(status, "chat", None)) else chat_id
    status_msg_id = status.id if status else 0

    params = {
        "scale_h": scale_h,
        "crf": crf,
        "preset": preset,
        "is_target_mode": is_target_mode,
        "target_mb": target_mb,
        "audio_bitrate": audio_bitrate,
        "respect_custom_height": respect_custom_height,
        "clean_name": clean_name,
        "expected_input_size": expected_input_size,
    }

    if not existing_task_doc:
        await task_repo.create_task(
            task_id=task_id,
            task_type="compress",
            user_id=user_id,
            chat_id=chat_id,
            original_message_id=msg_id,
            status_chat_id=status_chat_id,
            status_message_id=status_msg_id,
            file_name=clean_name,
            file_size=expected_input_size,
            mime_type=mime_type,
            tg_file_id=tg_file_id,
            work_dir=download_dir,
            input_path=input_path,
            output_path=output_path,
            params=params,
            idempotency_key=idempotency_key,
            max_retries=Config.TASK_MAX_RETRIES,
        )
    else:
        await task_repo.update_task(
            task_id,
            {
                "status": "running",
                "recovered": bool(is_recovery or existing_task_doc.get("recovered", False)),
                "recovery_count": int(existing_task_doc.get("recovery_count", 0)) + (1 if is_recovery else 0),
                "work_dir": download_dir,
                "input_path": input_path,
                "output_path": output_path,
            },
        )

    await task_manager.register_task(
        task_id=task_id,
        user_id=user_id,
        task_type="compress",
        work_dir=download_dir,
        asyncio_task=asyncio.current_task(),
        stage=existing_task_doc.get("stage", "initialized") if existing_task_doc else "initialized",
        params=params,
    )

    max_retries = int((existing_task_doc or {}).get("max_retries", Config.TASK_MAX_RETRIES) or 4)
    start_dl = time.time()

    try:
        already_encoded = os.path.exists(output_path) and await validate_media_output(output_path)
        already_downloaded = (
            already_encoded
            or (
                os.path.exists(input_path)
                and os.path.getsize(input_path) > 0
                and (expected_input_size <= 0 or os.path.getsize(input_path) >= expected_input_size)
            )
        )

        if not already_downloaded:
            task_manager.update_stage(task_id, "downloading", "running")
            await task_repo.update_task(task_id, {"status": "running", "stage": "downloading"})

            if status is None:
                status = await safe_reply_text(
                    original_msg,
                    f"📥 **{to_smallcaps('downloading media for ffmpeg')}**... `0%`",
                )
                if status:
                    await task_repo.update_task(task_id, {"status_message_id": status.id})
            else:
                await safe_edit_text(
                    status,
                    f"📥 **{to_smallcaps('recovering & downloading media for ffmpeg' if is_recovery else 'downloading media for ffmpeg')}**...\n\n"
                    f"[▱▱▱▱▱▱▱▱▱▱] `0%`\n"
                    f"📁 **{to_smallcaps('file')}** : `{clean_name}`"
                    f"{format_watermark()}",
                )

            dl_ok = False
            last_dl_err = None
            for dl_attempt in range(max_retries):
                try:
                    await download_media_with_auth_recovery(
                        client=client,
                        message=original_msg,
                        file_name=input_path,
                        progress=progress_for_pyrogram,
                        progress_args=("📥 downloading media for ffmpeg", status, start_dl),
                    )
                    if os.path.exists(input_path) and os.path.getsize(input_path) > 0:
                        dl_ok = True
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_dl_err = e
                    task_manager.record_metric(retries=1, error=f"Download retry #{dl_attempt+1}: {e}")
                    await task_repo.record_task_error(task_id, f"Download attempt {dl_attempt+1} failed: {e}", stage="downloading")
                    await task_repo.update_task(task_id, {"retry_count": dl_attempt + 1, "status": "retrying"})
                    if dl_attempt < max_retries - 1:
                        await asyncio.sleep(1.5 * (dl_attempt + 1))

            if not dl_ok or not os.path.exists(input_path):
                await task_repo.update_task(task_id, {"status": "failed", "stage": "downloading"})
                task_manager.record_metric(failed=1, error=f"Download failed for {task_id}: {last_dl_err}")
                clean_temp_files(input_path, download_dir)
                await safe_edit_text(status, f"❌ **{to_smallcaps('download failed')}** : `{last_dl_err or 'file missing'}`")
                return await task_repo.get_task(task_id)

            await task_repo.update_task(
                task_id,
                {"status": "running", "stage": "downloaded", "progress_pct": 25.0, "file_size": os.path.getsize(input_path)},
            )
            task_manager.update_stage(task_id, "downloaded", "running")
        else:
            logger.info(f"♻️ Task {task_id} reusing existing local media checkpoint on disk.")

        orig_size = (
            os.path.getsize(input_path)
            if os.path.exists(input_path)
            else int(expected_input_size or os.path.getsize(output_path))
        )

        is_doc_or_code_mode = bool(preset in ("codeopt", "zip") or is_code_input)
        if not is_doc_or_code_mode:
            if os.path.exists(input_path):
                streams = await check_media_streams(input_path)
                in_attrs = await get_media_attributes(input_path)
            else:
                streams = await check_media_streams(output_path)
                in_attrs = await get_media_attributes(output_path)
            if not streams.get("has_video") and not streams.get("has_audio"):
                is_doc_or_code_mode = True
        else:
            streams = {"has_video": False, "has_audio": False, "duration": 0}
            in_attrs = {"width": 0, "height": 0, "duration": 0}

        tg_dur = int(streams.get("duration") or in_attrs.get("duration") or getattr(media, "duration", 0) or 0)
        is_audio_only = (not is_doc_or_code_mode) and (not streams["has_video"]) and streams["has_audio"]

        if is_doc_or_code_mode and not already_encoded:
            if preset == "codeopt" or (is_code_input and preset != "zip"):
                out_name = sanitize_filename(f"optimized_{base_n}{orig_ext}")
            else:
                out_name = sanitize_filename(f"compressed_{base_n}{orig_ext}.zip")
            output_path = os.path.join(download_dir, out_name)
            await task_repo.update_task(task_id, {"output_path": output_path})
        elif is_audio_only and not already_encoded:
            out_name = sanitize_filename(f"compressed_{base_n}.m4a")
            output_path = os.path.join(download_dir, out_name)
            await task_repo.update_task(task_id, {"output_path": output_path})
        else:
            out_name = os.path.basename(output_path)

        if not already_encoded:
            task_manager.update_stage(task_id, "encoding", "running")
            await task_repo.update_task(task_id, {"status": "running", "stage": "encoding", "progress_pct": 35.0})

            encode_success = False
            for enc_attempt in range(max_retries):
                try:
                    if os.path.exists(output_path):
                        clean_temp_files(output_path)

                    if is_doc_or_code_mode:
                        if preset == "codeopt" or (is_code_input and preset != "zip"):
                            lang_lbl = detect_code_language(clean_name, mime_type).upper()
                            await safe_edit_text(status, f"⚡ **{to_smallcaps(f'optimizing & minifying {lang_lbl} code file...')}**")
                            encode_success = await optimize_code_file(input_path, output_path, clean_name)
                        else:
                            await safe_edit_text(status, f"🗜️ **{to_smallcaps('compressing file to high-speed .zip archive...')}**")
                            encode_success = await compress_file_to_zip(input_path, output_path, clean_name)
                    elif is_audio_only:
                        abr_val = str(audio_bitrate if audio_bitrate not in ("auto", "copy") else "96k")
                        await safe_edit_text(status, f"🎵 **{to_smallcaps(f'compressing audio stream with ffmpeg (aac {abr_val})...')}**")
                        encode_success = await compress_audio(input_path, output_path, bitrate=abr_val)
                    elif is_target_mode and target_mb:
                        safe_target_mb = min(1900.0, float(target_mb))
                        res_disp = f"{scale_h}p" if scale_h > 0 else "Auto Smart"
                        await safe_edit_text(
                            status,
                            f"🎯 **{to_smallcaps('encoding video to target size')}** (`{safe_target_mb} MB • {res_disp}`)...\n\n"
                            f"• ⚡ **{to_smallcaps('engine')}** : `FFmpeg Ultra-Fast ({eff_preset.upper()})`"
                        )
                        encode_success = await compress_to_target_size(
                            input_path=input_path,
                            output_path=output_path,
                            target_size_mb=safe_target_mb,
                            duration_sec=tg_dur,
                            scale_height=scale_h,
                            progress_message=status,
                            task_id=task_id,
                            preset=eff_preset if enc_attempt == 0 else "hyper",
                            audio_bitrate=audio_bitrate,
                            respect_custom_height=respect_custom_height,
                        )
                    elif preset == "remux" and enc_attempt == 0:
                        await safe_edit_text(status, f"🔄 **{to_smallcaps('fast remuxing container to mp4 (stream copy)...')}**")
                        encode_success = await remux_container(input_path, output_path)
                        if not encode_success:
                            encode_success = await compress_media(
                                input_path=input_path,
                                output_path=output_path,
                                crf=26,
                                preset="hyper",
                                scale_height=scale_h,
                                progress_message=status,
                                total_duration=tg_dur,
                                task_id=task_id,
                            )
                    else:
                        max_out_mb = 1880.0 if orig_size > MAX_BOT_FILE_SIZE else None
                        attempt_vcodec = vcodec if enc_attempt == 0 else "libx264"
                        attempt_preset = eff_preset if enc_attempt == 0 else "hyper"
                        await safe_edit_text(
                            status,
                            f"🗜️ **{to_smallcaps('encoding video with ffmpeg')} ({'H.265/HEVC' if attempt_vcodec == 'libx265' else 'H.264'})...**\n\n"
                            f"• 🎚️ **{to_smallcaps('resolution')}** : `{scale_h if scale_h > 0 else 'Auto/Original'}p`\n"
                            f"• ⚙️ **{to_smallcaps('preset')}** : `{attempt_preset}` (CRF `{crf}`)\n"
                            f"• 📦 **{to_smallcaps('original size')}** : `{humanbytes(orig_size)}`\n\n"
                            f"⏳ _{to_smallcaps('live ffmpeg progress will update automatically...')}_"
                        )
                        encode_success = await compress_media(
                            input_path=input_path,
                            output_path=output_path,
                            crf=crf,
                            preset=attempt_preset,
                            scale_height=scale_h,
                            progress_message=status,
                            total_duration=tg_dur,
                            max_output_mb=max_out_mb,
                            vcodec=attempt_vcodec,
                            audio_bitrate=audio_bitrate,
                            task_id=task_id,
                            respect_custom_height=respect_custom_height,
                        )

                    if encode_success and (
                        (is_doc_or_code_mode and os.path.exists(output_path) and os.path.getsize(output_path) > 0)
                        or await validate_media_output(output_path)
                    ):
                        break
                    encode_success = False
                    task_manager.record_metric(retries=1, error=f"FFmpeg validation failed on attempt {enc_attempt+1} for {task_id}")
                    await task_repo.record_task_error(
                        task_id,
                        f"FFmpeg encode attempt {enc_attempt+1}/{max_retries} failed validation",
                        stage="encoding",
                    )
                    await task_repo.update_task(task_id, {"retry_count": enc_attempt + 1, "status": "retrying"})
                    if enc_attempt < max_retries - 1:
                        await asyncio.sleep(1.5 * (enc_attempt + 1))
                except asyncio.CancelledError:
                    raise
                except Exception as enc_err:
                    encode_success = False
                    task_manager.record_metric(retries=1, error=f"FFmpeg exception on attempt {enc_attempt+1}: {enc_err}")
                    await task_repo.record_task_error(task_id, f"Encode exception: {enc_err}", stage="encoding")
                    await task_repo.update_task(task_id, {"retry_count": enc_attempt + 1, "status": "retrying"})
                    if enc_attempt < max_retries - 1:
                        await asyncio.sleep(1.5 * (enc_attempt + 1))

            if not encode_success or not (
                (is_doc_or_code_mode and os.path.exists(output_path) and os.path.getsize(output_path) > 0)
                or await validate_media_output(output_path)
            ):
                await task_repo.update_task(task_id, {"status": "failed", "stage": "encoding"})
                task_manager.record_metric(failed=1, error=f"FFmpeg compression failed after {max_retries} attempts ({task_id})")
                clean_temp_files(input_path, output_path, download_dir)
                await safe_edit_text(
                    status,
                    f"❌ **{to_smallcaps('ffmpeg compression failed or timed out!')}**\n\n"
                    f"💡 {to_smallcaps('ensure the media is a valid video or audio file.')}"
                    f"{format_watermark()}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"🔙 {to_smallcaps('back to media')}", callback_data=f"back_to_media_{msg_id}"),
                        InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home")
                    ]])
                )
                return await task_repo.get_task(task_id)

            comp_size = os.path.getsize(output_path)
            if comp_size > MAX_BOT_FILE_SIZE and not is_audio_only and os.path.exists(input_path):
                await safe_edit_text(
                    status,
                    f"🎯 **{to_smallcaps('output exceeded 2gb; running auto-fit pass (< 1.85 gb)...')}**"
                )
                refit_path = os.path.join(download_dir, f"fit_{out_name}")
                fit_ok = await compress_to_target_size(
                    input_path=input_path,
                    output_path=refit_path,
                    target_size_mb=1850.0,
                    duration_sec=tg_dur,
                    scale_height=min(scale_h if scale_h > 0 else 480, 720),
                    progress_message=status,
                    task_id=task_id,
                    preset="hyper",
                )
                if fit_ok and os.path.exists(refit_path):
                    os.replace(refit_path, output_path)

            await task_repo.update_task(task_id, {"status": "running", "stage": "encoded", "progress_pct": 80.0})
            task_manager.update_stage(task_id, "encoded", "running")
        else:
            logger.info(f"♻️ Task {task_id} reusing validated encoded output file '{output_path}' for immediate upload.")

        comp_size = os.path.getsize(output_path)
        size_diff = orig_size - comp_size
        if size_diff >= 0:
            saved_pct = round((size_diff / orig_size) * 100, 1) if orig_size > 0 else 0.0
            size_delta_str = f"-{saved_pct}% {to_smallcaps('saved')}"
        else:
            inc_pct = round((abs(size_diff) / orig_size) * 100, 1) if orig_size > 0 else 0.0
            saved_pct = -inc_pct
            size_delta_str = f"+{inc_pct}% ({to_smallcaps('input already compressed')})"

        info_task = asyncio.create_task(get_detailed_mediainfo(output_path))
        thumb_task = asyncio.create_task(
            resolve_thumbnail(
                client=client,
                user_id=user_id,
                temp_dir=download_dir,
                video_path=output_path,
                duration=tg_dur
            )
        )
        probe_info, thumb_path = await asyncio.gather(info_task, thumb_task)
        v_streams = (probe_info or {}).get("video_streams") or []
        dur = int((probe_info or {}).get("duration") or tg_dur)
        w = int(v_streams[0].get("width") if v_streams and v_streams[0].get("width") else 1280)
        h = int(v_streams[0].get("height") if v_streams and v_streams[0].get("height") else 720)
        attrs = {"width": w, "height": h, "duration": dur}
        if not isinstance(thumb_path, str) or not os.path.exists(thumb_path):
            thumb_path = None

        base_caption = await extract_and_format_caption(
            user_id=user_id,
            file_path=output_path,
            original_caption=original_msg.caption or "",
            file_id=str(msg_id),
            metadata=probe_info
        )
        stats_footer = (
            f"\n\n📊 **{to_smallcaps('ffmpeg compression stats')}** :\n"
            f"• 📄 `{clean_name}` ➔ `{out_name}`\n"
            f"• 📉 `{humanbytes(orig_size)}` ➔ `{humanbytes(comp_size)}` (`{size_delta_str}`)"
        )
        if len(base_caption) + len(stats_footer) <= 1024:
            final_caption = base_caption + stats_footer
        else:
            final_caption = base_caption

        task_manager.update_stage(task_id, "uploading", "running")
        await task_repo.update_task(task_id, {"status": "running", "stage": "uploading", "progress_pct": 85.0})

        upload_start = time.time()
        await safe_edit_text(status, f"📤 **{to_smallcaps('uploading compressed media...')}**")

        target_chat_id = status.chat.id if (status and getattr(status, "chat", None)) else chat_id
        sent_msg = None
        last_up_err = None

        from utils import temp as _t
        attach_stream = bool(_t.BOT_CONFIG.get("auto_stream_global", True) and u and u.get("auto_stream", False))
        new_fid = secrets.token_urlsafe(10) if attach_stream else ""
        w_url = f"{Config.BASE_URL}/watch/{new_fid}" if new_fid else ""
        d_url = f"{Config.BASE_URL}/download/{new_fid}" if new_fid else ""

        cur_thumb = thumb_path
        for up_attempt in range(max_retries):
            try:
                if is_doc_or_code_mode:
                    sent_msg = await execute_with_floodwait(
                        client.send_document,
                        chat_id=target_chat_id,
                        document=output_path,
                        file_name=out_name,
                        caption=final_caption,
                        thumb=cur_thumb,
                        force_document=True,
                        progress=progress_for_pyrogram,
                        progress_args=("📤 uploading compressed file", status, upload_start)
                    )
                elif is_audio_only:
                    sent_msg = await execute_with_floodwait(
                        client.send_audio,
                        chat_id=target_chat_id,
                        audio=output_path,
                        file_name=out_name,
                        caption=final_caption,
                        duration=dur,
                        thumb=cur_thumb,
                        progress=progress_for_pyrogram,
                        progress_args=("📤 uploading compressed audio", status, upload_start)
                    )
                else:
                    w = int(attrs.get("width", 1280)) or 1280
                    h = int(attrs.get("height", 720)) or 720
                    try:
                        sent_msg = await execute_with_floodwait(
                            client.send_video,
                            chat_id=target_chat_id,
                            video=output_path,
                            file_name=out_name,
                            caption=final_caption,
                            duration=dur,
                            width=w,
                            height=h,
                            thumb=cur_thumb,
                            supports_streaming=True,
                            progress=progress_for_pyrogram,
                            progress_args=("📤 uploading compressed video", status, upload_start)
                        )
                    except FloodWait as fw:
                        wait_s = _flood_wait_seconds(fw)
                        await pro_floodwait_sleep(wait_s, f"compress_upload#{up_attempt+1}", chat_id=target_chat_id)
                        continue
                    except Exception:
                        sent_msg = await execute_with_floodwait(
                            client.send_document,
                            chat_id=target_chat_id,
                            document=output_path,
                            file_name=out_name,
                            caption=final_caption,
                            thumb=None,
                            force_document=True,
                            progress=progress_for_pyrogram,
                            progress_args=("📤 uploading compressed file", status, upload_start)
                        )
                if sent_msg is not None:
                    break
            except FloodWait as fw:
                wait_s = _flood_wait_seconds(fw)
                task_manager.record_metric(retries=1)
                await pro_floodwait_sleep(wait_s, f"compress_upload_outer#{up_attempt+1}", chat_id=target_chat_id)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                cur_thumb = None
                last_up_err = e
                task_manager.record_metric(retries=1, error=f"Upload attempt {up_attempt+1} failed: {e}")
                await task_repo.record_task_error(task_id, f"Upload attempt {up_attempt+1} failed: {e}", stage="uploading")
                await task_repo.update_task(task_id, {"retry_count": up_attempt + 1, "status": "retrying"})
                if up_attempt < max_retries - 1:
                    await asyncio.sleep(1.5 * (up_attempt + 1))

        if sent_msg is None:
            await task_repo.update_task(task_id, {"status": "interrupted", "stage": "encoded"})
            await safe_edit_text(status, f"⚠️ **{to_smallcaps('upload temporarily delayed; task queued for automatic recovery')}** : `{last_up_err}`")
            return await task_repo.get_task(task_id)

        logger.info(
            "✅ ғғᴍᴘᴇɢ ᴄᴏᴍᴘʀᴇssɪᴏɴ ᴄᴏᴍᴘʟᴇᴛᴇ › ᴛᴀsᴋ=%s ᴜsᴇʀ=%s ғɪʟᴇ='%s' ᴏʀɪɢ=%s ᴄᴏᴍᴘ=%s (-%s%%) ᴇʟᴀᴘsᴇᴅ=%.1fs",
            task_id,
            user_id,
            out_name,
            humanbytes(orig_size),
            humanbytes(comp_size),
            saved_pct,
            time.time() - start_dl,
        )

        if sent_msg:
            try:
                if attach_stream and new_fid:
                    out_media = extract_message_media(sent_msg)[0]
                    await file_repo.save_file(
                        file_id=new_fid,
                        chat_id=sent_msg.chat.id,
                        message_id=sent_msg.id,
                        file_name=out_name,
                        file_size=getattr(out_media, "file_size", comp_size),
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
                            InlineKeyboardButton(f"✏️ {to_smallcaps('rename')}", callback_data=f"rename_{sent_msg.id}"),
                        ]
                    ])
                    await sent_msg.edit_reply_markup(reply_markup=stream_kb)
                    bin_chan = Config.BIN_CHANNEL if Config.BIN_CHANNEL != 0 else sent_msg.chat.id
                    if bin_chan != sent_msg.chat.id:
                        async def _bg_fwd_comp(fid_val: str, s_msg: Message, b_ch: int):
                            try:
                                fwd = await safe_forward_message(s_msg, b_ch)
                                if fwd and getattr(fwd, "chat", None) and getattr(fwd, "id", None):
                                    await file_repo.update_file(fid_val, {"chat_id": fwd.chat.id, "message_id": fwd.id})
                            except Exception:
                                pass
                        asyncio.create_task(_bg_fwd_comp(new_fid, sent_msg, bin_chan))
                else:
                    on_demand_btns = [
                        InlineKeyboardButton(f"🎬 {to_smallcaps('generate file stream')}", callback_data=f"stream_{sent_msg.id}"),
                    ]
                    if comp_size <= MAX_BOT_FILE_SIZE:
                        on_demand_btns.append(InlineKeyboardButton(f"✏️ {to_smallcaps('rename')}", callback_data=f"rename_{sent_msg.id}"))
                    await sent_msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup([on_demand_btns]))
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

        clean_temp_files(input_path, output_path, thumb_path, download_dir)
        if status:
            try:
                await status.delete()
            except Exception:
                pass

        del_time = u.get("auto_delete_time", 0) if u else 0
        if del_time > 0 and sent_msg:
            await schedule_deletion(sent_msg.chat.id, sent_msg.id, del_time)

        return completed_doc

    except asyncio.CancelledError:
        task_Obj = task_manager.get_task(task_id)
        if task_Obj and task_Obj.is_cancelled:
            await task_repo.update_task(task_id, {"status": "cancelled", "stage": "cancelled"})
            clean_temp_files(input_path, output_path, download_dir)
        else:
            logger.warning(f"♻️ Checkpointing interrupted compression task {task_id} for automatic recovery on restart.")
            await task_repo.update_task(task_id, {"status": "interrupted"})
        raise
    finally:
        _IN_FLIGHT_IDEMPOTENCY.pop(idempotency_key, None)
        t_obj = task_manager.get_task(task_id)
        preserve_ws = bool(t_obj and not t_obj.is_cancelled and t_obj.status != "completed")
        await task_manager.cleanup_task(task_id, preserve_workspace=preserve_ws)


async def checkpoint_active_tasks_before_shutdown(reason: str = "restart") -> int:
    """
    Preserve all in-flight compression/rename/media tasks before a bot restart,
    server rebuild, container stop, or `/update` so they resume seamlessly on boot.
    """
    checkpointed = 0
    for tid, mtask in list(task_manager._tasks.items()):
        if mtask.is_cancelled or mtask.status in ("completed", "cancelled", "skipped"):
            continue
        if mtask.process and mtask.process.returncode is None:
            try:
                mtask.process.terminate()
            except Exception:
                pass
        await task_repo.update_task(
            tid,
            {
                "status": "interrupted",
                "stage": mtask.stage or "encoding",
                "last_error": f"Interrupted by {reason}; preserved for automatic recovery",
            },
        )
        checkpointed += 1
    if checkpointed > 0:
        logger.info(f"♻️ Checkpointed {checkpointed} active media task(s) before {reason}.")
    return checkpointed


async def _recover_single_task(bot: Any, task_doc: Dict[str, Any]) -> str:
    """
    Validate and resume a single incomplete/interrupted/failed task from its latest valid stage.
    Returns one of: 'completed', 'skipped', 'failed'.
    """
    task_id = str(task_doc.get("task_id", ""))
    task_type = str(task_doc.get("task_type", "compress"))
    user_id = int(task_doc.get("user_id", 0) or 0)
    chat_id = int(task_doc.get("chat_id", 0) or user_id)
    orig_msg_id = int(task_doc.get("original_message_id", 0) or 0)
    status_chat_id = int(task_doc.get("status_chat_id", 0) or chat_id)
    status_msg_id = int(task_doc.get("status_message_id", 0) or 0)
    params = dict(task_doc.get("params") or {})

    if task_manager.get_task(task_id):
        return "skipped"

    if task_doc.get("status") == "completed" and task_doc.get("output_message_id"):
        task_manager.record_metric(skipped=1)
        return "skipped"

    logger.info(
        "♻️ [ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ] Resuming task_id=%s type=%s stage=%s retries=%s/%s",
        task_id,
        task_type,
        task_doc.get("stage", "initialized"),
        task_doc.get("retry_count", 0),
        task_doc.get("max_retries", Config.TASK_MAX_RETRIES),
    )

    original_msg = None
    if bot and getattr(bot, "is_connected", False) and chat_id and orig_msg_id:
        try:
            original_msg = await bot.get_messages(chat_id, orig_msg_id)
        except Exception as e:
            logger.debug(f"Recovery get_messages({chat_id}, {orig_msg_id}) notice: {e}")

    status_msg = None
    if bot and getattr(bot, "is_connected", False) and status_chat_id and status_msg_id:
        try:
            status_msg = await bot.get_messages(status_chat_id, status_msg_id)
        except Exception:
            status_msg = None

    input_path = task_doc.get("input_path", "")
    output_path = task_doc.get("output_path", "")
    has_local_media = (input_path and os.path.exists(input_path)) or (output_path and os.path.exists(output_path))

    if (not original_msg or not getattr(original_msg, "media", None)) and not has_local_media:
        await task_repo.record_task_error(task_id, "Original message and local media no longer available", stage="recovery")
        await task_repo.update_task(
            task_id,
            {
                "status": "failed",
                "retry_count": int(task_doc.get("max_retries", Config.TASK_MAX_RETRIES)),
            },
        )
        task_manager.record_metric(failed=1, error=f"Task {task_id}: source media unavailable")
        return "failed"

    if original_msg is None and has_local_media:
        class _LocalRecoveredMessage:
            def __init__(self, doc):
                self.id = int(doc.get("original_message_id", 1) or 1)
                self.chat = type("Chat", (), {"id": int(doc.get("chat_id", 0) or user_id)})()
                self.caption = doc.get("params", {}).get("caption", "")
                self.video = type(
                    "Video",
                    (),
                    {
                        "file_name": doc.get("file_name", "video.mp4"),
                        "file_size": int(doc.get("file_size", 0) or 0),
                        "mime_type": doc.get("mime_type", "video/mp4"),
                        "duration": int(doc.get("params", {}).get("duration", 0) or 0),
                        "width": 1280,
                        "height": 720,
                        "file_id": doc.get("tg_file_id"),
                        "thumbs": None,
                    },
                )()
                self.document = None
                self.audio = None
                self.media = True
        original_msg = _LocalRecoveredMessage(task_doc)

    if task_type == "rename":
        from plugins.rename import run_rename_workflow
        res_doc = await run_rename_workflow(
            client=bot,
            original_msg=original_msg,
            reply_msg=status_msg or original_msg,
            user_id=user_id,
            target_id=orig_msg_id,
            new_name=params.get("new_name") or task_doc.get("file_name", "renamed.mp4"),
            upload_type=params.get("upload_type", "vid"),
            is_edit=bool(status_msg),
            task_id=task_id,
            is_recovery=True,
        )
    else:
        res_doc = await perform_compression_job(
            client=bot,
            original_msg=original_msg,
            status=status_msg,
            user_id=user_id,
            scale_h=int(params.get("scale_h", 720)),
            crf=int(params.get("crf", 28)),
            preset=str(params.get("preset", "turbo")),
            is_target_mode=bool(params.get("is_target_mode", False)),
            target_mb=params.get("target_mb"),
            task_id=task_id,
            is_recovery=True,
            audio_bitrate=str(params.get("audio_bitrate", "auto")),
            respect_custom_height=bool(params.get("respect_custom_height", False)),
        )

    final_doc = res_doc or await task_repo.get_task(task_id) or {}
    if final_doc.get("status") == "completed":
        return "completed"
    return "failed"


async def recover_and_complete_pending_tasks(bot: Any, trigger_source: str = "startup") -> Dict[str, Any]:
    """
    Scan persistent task storage for any incomplete, interrupted, queued, or failed (retryable)
    tasks and automatically resume and finish them concurrently.
    Returns the complete execution report.
    """
    if not getattr(Config, "TASK_RECOVERY_ENABLED", True):
        return await generate_task_execution_report()

    async with _get_recovery_lock():
        task_manager._recovery_metrics["last_recovery_run_ts"] = float(time.time())
        task_manager._recovery_metrics["status"] = f"recovering ({trigger_source})"
        recoverable = await task_repo.get_recoverable_tasks()

        if not recoverable:
            task_manager._recovery_metrics["status"] = "completed_all_healthy"
            return await generate_task_execution_report()

        logger.info(
            "♻️ [ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ ᴇɴɢɪɴᴇ] Detected %s incomplete/interrupted task(s) on %s — resuming automatically...",
            len(recoverable),
            trigger_source,
        )

        outcomes = await asyncio.gather(
            *(_recover_single_task(bot, tdoc) for tdoc in recoverable),
            return_exceptions=True,
        )
        for tdoc, out in zip(recoverable, outcomes):
            if isinstance(out, Exception):
                tid = tdoc.get("task_id", "unknown")
                logger.error(f"❌ Task recovery exception for {tid}: {out}")
                await task_repo.record_task_error(tid, f"Recovery exception: {out}", stage="recovery")
                task_manager.record_metric(failed=1, error=f"{tid}: {out}")

        report = await generate_task_execution_report()
        if LOG_CHANNEL and bot and getattr(bot, "is_connected", False) and len(recoverable) > 0:
            try:
                await safe_send_message(
                    bot,
                    chat_id=int(LOG_CHANNEL),
                    text=format_task_execution_report_text(report),
                )
            except Exception:
                pass
        return report


async def generate_task_execution_report() -> Dict[str, Any]:
    """
    Generate a comprehensive execution report containing total tasks detected,
    completed tasks, recovered tasks, failed tasks, skipped tasks, retry counts,
    processing status, and any remaining errors.
    """
    await task_repo.load_journal_into_db()
    all_tasks = await task_repo.get_all_tasks(limit=500)
    mem_rep = task_manager.get_recovery_report()

    db_total = len(all_tasks)
    db_completed = sum(1 for t in all_tasks if t.get("status") == "completed")
    db_recovered = sum(1 for t in all_tasks if t.get("status") == "completed" and t.get("recovered"))
    db_failed = sum(
        1 for t in all_tasks
        if t.get("status") == "failed" and int(t.get("retry_count", 0)) >= int(t.get("max_retries", Config.TASK_MAX_RETRIES))
    )
    db_skipped = sum(1 for t in all_tasks if t.get("status") in ("skipped", "cancelled"))
    db_pending = sum(
        1 for t in all_tasks
        if t.get("status") in ("queued", "running", "downloading", "downloaded", "encoding", "encoded", "uploading", "interrupted", "retrying")
    )
    db_retries = sum(int(t.get("retry_count", 0)) for t in all_tasks)
    remaining_errors = [
        f"{t.get('task_id')}: {t.get('last_error')}"
        for t in all_tasks
        if t.get("status") == "failed" and t.get("last_error")
    ]

    total_detected = max(db_total, int(mem_rep.get("total_detected", 0)))
    completed_tasks = max(db_completed, int(mem_rep.get("completed_tasks", 0)))
    recovered_tasks = max(db_recovered, int(mem_rep.get("recovered_tasks", 0)))
    failed_tasks = db_failed
    skipped_tasks = max(db_skipped, int(mem_rep.get("skipped_tasks", 0)))
    retry_counts = max(db_retries, int(mem_rep.get("total_retries", 0)))
    all_completed_ok = (db_pending == 0 and failed_tasks == 0)

    status_label = (
        "ALL_TASKS_COMPLETED_SUCCESSFULLY"
        if all_completed_ok
        else ("PROCESSING_ACTIVE_TASKS" if db_pending > 0 else "COMPLETED_WITH_ERRORS")
    )
    task_manager._recovery_metrics["status"] = status_label

    return {
        "total_tasks_detected": total_detected,
        "completed_tasks": completed_tasks,
        "recovered_tasks": recovered_tasks,
        "failed_tasks": failed_tasks,
        "skipped_tasks": skipped_tasks,
        "in_progress_tasks": db_pending,
        "retry_counts": retry_counts,
        "processing_status": status_label,
        "remaining_errors": remaining_errors[:15],
        "all_tasks_completed": all_completed_ok,
        "timestamp": float(time.time()),
    }


def format_task_execution_report_text(report: Dict[str, Any]) -> str:
    errs = report.get("remaining_errors") or []
    err_str = "\n".join(f"  • `{e}`" for e in errs[:5]) if errs else "  • `None (0 errors)`"
    confirm = (
        "✅ **ᴇᴠᴇʀʏ ᴛᴀsᴋ ᴡᴀs sᴜᴄᴄᴇssғᴜʟʟʏ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!**"
        if report.get("all_tasks_completed")
        else f"⏳ **ᴀᴄᴛɪᴠᴇ / ᴘᴇɴᴅɪɴɢ ᴛᴀsᴋs: `{report.get('in_progress_tasks', 0)}`**"
    )
    return (
        f"✦ **{to_smallcaps('persistent task recovery & execution report')}** ✦ ♻️\n\n"
        f"• 📊 **{to_smallcaps('total tasks detected')}** : `{report.get('total_tasks_detected', 0)}`\n"
        f"• ✅ **{to_smallcaps('completed tasks')}** : `{report.get('completed_tasks', 0)}`\n"
        f"• ♻️ **{to_smallcaps('recovered tasks')}** : `{report.get('recovered_tasks', 0)}`\n"
        f"• ❌ **{to_smallcaps('failed tasks')}** : `{report.get('failed_tasks', 0)}`\n"
        f"• ⏭️ **{to_smallcaps('skipped tasks')}** : `{report.get('skipped_tasks', 0)}`\n"
        f"• 🔄 **{to_smallcaps('retry counts')}** : `{report.get('retry_counts', 0)}`\n"
        f"• ⚙️ **{to_smallcaps('processing status')}** : `{report.get('processing_status', 'READY')}`\n\n"
        f"🛠️ **{to_smallcaps('remaining errors')}** :\n{err_str}\n\n"
        f"{confirm}"
        f"{format_watermark()}"
    )


async def run_task_recovery_watchdog(bot: Any, interval: int = None):
    """
    Background watchdog that runs startup task recovery and continuously monitors
    for interrupted or stalled tasks across bot restarts, server rebuilds, and updates.
    """
    eff_interval = max(5, int(interval or getattr(Config, "TASK_WATCHDOG_INTERVAL", 15)))
    logger.info(f"♻️ Persistent Task Recovery & Watchdog Engine started (interval: {eff_interval}s).")
    await asyncio.sleep(2.0)
    try:
        report = await recover_and_complete_pending_tasks(bot, trigger_source="startup_recovery")
        logger.info(
            "♻️ [sᴛᴀʀᴛᴜᴘ ʀᴇᴄᴏᴠᴇʀʏ ʀᴇᴘᴏʀᴛ] detected=%s completed=%s recovered=%s failed=%s skipped=%s status=%s",
            report["total_tasks_detected"],
            report["completed_tasks"],
            report["recovered_tasks"],
            report["failed_tasks"],
            report["skipped_tasks"],
            report["processing_status"],
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Startup task recovery notice: {e}")

    while True:
        try:
            await asyncio.sleep(eff_interval)
            recoverable = await task_repo.get_recoverable_tasks(sync_journal=False)
            stalled = [
                t for t in recoverable
                if not task_manager.get_task(str(t.get("task_id", "")))
            ]
            if stalled:
                await recover_and_complete_pending_tasks(bot, trigger_source="watchdog")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Task watchdog cycle notice: {e}")
