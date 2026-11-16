import time
import os
import socket
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import Config
from info import COMMAND_PREFIXES
import io
from plugins.core_utils import (
    to_smallcaps,
    format_watermark,
    humanbytes,
    time_formatter,
    clean_temp_files,
    logger,
    safe_reply_text,
    safe_edit_text,
    execute_with_floodwait,
    task_manager,
)
from plugins.core_db import user_repo, file_repo
from plugins.core_services import get_detailed_mediainfo, download_media_with_auth_recovery


@Client.on_message(filters.command("myid", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def myid_command_handler(client: Client, message: Message):
    user = message.from_user
    chat = message.chat
    reply = message.reply_to_message

    text = f"✦ **{to_smallcaps('user & chat identifier')}** ✦\n\n"
    if user:
        dc_id = getattr(user, "dc_id", "N/A") or "N/A"
        text += (
            f"👤 **{to_smallcaps('your info')}** :\n"
            f"• 🆔 **{to_smallcaps('user id')}** : `{user.id}`\n"
            f"• 👤 **{to_smallcaps('first name')}** : {user.first_name}\n"
            f"• 🏷️ **{to_smallcaps('username')}** : @{user.username if user.username else to_smallcaps('none')}\n"
            f"• 🌐 **{to_smallcaps('datacenter')}** : `DC {dc_id}`\n\n"
        )
    if reply and reply.from_user:
        r_user = reply.from_user
        r_dc = getattr(r_user, "dc_id", "N/A") or "N/A"
        text += (
            f"💬 **{to_smallcaps('replied user')}** :\n"
            f"• 🆔 **{to_smallcaps('user id')}** : `{r_user.id}`\n"
            f"• 👤 **{to_smallcaps('first name')}** : {r_user.first_name}\n"
            f"• 🏷️ **{to_smallcaps('username')}** : @{r_user.username if r_user.username else to_smallcaps('none')}\n"
            f"• 🌐 **{to_smallcaps('datacenter')}** : `DC {r_dc}`\n\n"
        )
    text += (
        f"💬 **{to_smallcaps('chat details')}** :\n"
        f"• 🆔 **{to_smallcaps('chat id')}** : `{chat.id}`\n"
        f"• 🏷️ **{to_smallcaps('chat type')}** : `{chat.type.name if hasattr(chat.type, 'name') else str(chat.type)}`\n"
        f"• 📨 **{to_smallcaps('message id')}** : `{message.id}`"
        f"{format_watermark()}"
    )
    await safe_reply_text(message, text)


@Client.on_message(filters.command(["speedtest", "speed"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def speedtest_command_handler(client: Client, message: Message):
    status = await safe_reply_text(message, f"🚀 **{to_smallcaps('measuring real server network latency...')}**")
    loop = asyncio.get_running_loop()

    targets = [
        ("Cloudflare DNS", "1.1.1.1", 53),
        ("Google DNS", "8.8.8.8", 53),
        ("Telegram DC", "149.154.167.50", 443),
    ]

    results = []
    for label, host, port in targets:
        def _test_sock():
            t0 = time.time()
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                return round((time.time() - t0) * 1000, 2)
            except Exception:
                return -1
        ms = await loop.run_in_executor(None, _test_sock)
        results.append((label, ms))

    text = f"✦ **{to_smallcaps('server network latency report')}** ✦\n\n"
    for label, ms in results:
        val = f"`{ms} ms`" if ms > 0 else "`Timeout / Blocked`"
        text += f"• 🌐 **{to_smallcaps(label)}** : {val}\n"
    text += (
        f"\n• ⚡ **{to_smallcaps('stream mode')}** : `adaptive HTTP byte-range ready`\n"
        f"• ⏱️ **{to_smallcaps('server status')}** : `Operational & Ultra-Responsive`"
        f"{format_watermark()}"
    )
    await safe_edit_text(status, text)


@Client.on_message(filters.command(["mediainfo", "probe"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def mediainfo_command_handler(client: Client, message: Message):
    target = message.reply_to_message
    if not target or not target.media:
        return await safe_reply_text(message, 
            f"⚠️ **{to_smallcaps('please reply to a video, audio, or document.')}**\n\n"
            f"• **{to_smallcaps('usage')}** : {to_smallcaps('reply to media with')} `/mediainfo`"
            f"{format_watermark()}"
        )
    await generate_mediainfo_response(client, target, message)


@Client.on_callback_query(filters.regex(r"^mediainfo_(\d+)"))
async def mediainfo_callback_entry(client: Client, query: CallbackQuery):
    msg_id = int(query.matches[0].group(1))
    original = await client.get_messages(query.message.chat.id, msg_id)
    if not original or not original.media:
        return await query.answer(to_smallcaps("media not found!"), show_alert=True)
    await query.answer()
    await generate_mediainfo_response(client, original, query.message)


async def generate_mediainfo_response(client: Client, media_msg: Message, target_reply: Message):
    media = media_msg.video or media_msg.document or media_msg.audio
    raw_name = getattr(media, "file_name", None) or "sample_media"
    file_size_bytes = getattr(media, "file_size", 0)
    file_size_str = humanbytes(file_size_bytes)
    mime_type = getattr(media, "mime_type", "unknown")
    dur = getattr(media, "duration", 0)
    dur_str = time_formatter(seconds=dur) if dur > 0 else "N/A"
    width = getattr(media, "width", 0)
    height = getattr(media, "height", 0)

    if file_size_bytes > 25 * 1024 * 1024:
        res_str = f"{width}x{height}" if width and height else "N/A"
        text = (
            f"✦ **{to_smallcaps('media information')}** ✦\n\n"
            f"• 📁 **{to_smallcaps('file name')}** : `{raw_name}`\n"
            f"• 📦 **{to_smallcaps('file size')}** : `{file_size_str}`\n"
            f"• 🏷️ **{to_smallcaps('mime type')}** : `{mime_type}`\n"
            f"• ⏱️ **{to_smallcaps('duration')}** : `{dur_str}`\n"
            f"• 📐 **{to_smallcaps('resolution')}** : `{res_str}`\n\n"
            f"💡 {to_smallcaps('for external playback in playit, mx, vlc, or s player, use the stream button below:')}"
            f"{format_watermark()}"
        )
        buttons = [
            [InlineKeyboardButton(f"⚡ {to_smallcaps('adaptive stream link')}", callback_data=f"stream_{media_msg.id}")],
            [InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"back_to_media_{media_msg.id}"), InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"), InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")],
        ]
        return await safe_reply_text(target_reply, text, reply_markup=InlineKeyboardMarkup(buttons))

    status = await safe_reply_text(target_reply, f"🔍 **{to_smallcaps('extracting deep mediainfo with ffprobe...')}**")

    temp_dir = os.path.join(Config.DOWNLOAD_DIR, f"probe_{media_msg.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, raw_name)

    try:
        await download_media_with_auth_recovery(client, media_msg, file_name=temp_path)
    except Exception as e:
        clean_temp_files(temp_path, temp_dir)
        return await safe_edit_text(status, f"❌ **{to_smallcaps('extraction failed')}** : `{e}`")

    info = await get_detailed_mediainfo(temp_path)
    clean_temp_files(temp_path, temp_dir)

    if not info:
        res_str = f"{width}x{height}" if width and height else "N/A"
        return await safe_edit_text(status, 
            f"✦ **{to_smallcaps('media information')}** ✦\n\n"
            f"• 📁 **{to_smallcaps('file name')}** : `{raw_name}`\n"
            f"• 📦 **{to_smallcaps('file size')}** : `{file_size_str}`\n"
            f"• 🏷️ **{to_smallcaps('mime type')}** : `{mime_type}`\n"
            f"• ⏱️ **{to_smallcaps('duration')}** : `{dur_str}`\n"
            f"• 📐 **{to_smallcaps('resolution')}** : `{res_str}`"
            f"{format_watermark()}"
        )

    container = info.get("container", "Unknown")
    dur_str = time_formatter(seconds=info.get("duration", 0))
    bitrate_str = f"{round(info.get('bitrate', 0) / 1000)} kbps" if info.get("bitrate") else "N/A"

    text = (
        f"✦ **{to_smallcaps('ffprobe media information')}** ✦\n\n"
        f"📁 **{to_smallcaps('file')}** : `{raw_name}`\n"
        f"📦 **{to_smallcaps('size')}** : `{file_size_str}`\n"
        f"📦 **{to_smallcaps('container')}** : `{container}`\n"
        f"⏱️ **{to_smallcaps('duration')}** : `{dur_str}`\n"
        f"📊 **{to_smallcaps('overall bitrate')}** : `{bitrate_str}`\n\n"
    )

    if info.get("video_streams"):
        for i, v in enumerate(info["video_streams"], 1):
            text += (
                f"🎬 **{to_smallcaps(f'video stream #{i}')}** :\n"
                f"  • 🏷️ **{to_smallcaps('codec')}** : `{v['codec']}` ({v['profile']})\n"
                f"  • 📐 **{to_smallcaps('resolution')}** : `{v['width']}x{v['height']}` ({v['aspect_ratio']})\n"
                f"  • 🎨 **{to_smallcaps('pixel format')}** : `{v['pix_fmt']}`\n"
                f"  • 🎞️ **{to_smallcaps('frame rate')}** : `{v['r_frame_rate']} fps`\n\n"
            )

    if info.get("audio_streams"):
        for i, a in enumerate(info["audio_streams"], 1):
            lang_val = a.get("language") or a.get("lang") or "Undetermined"
            text += (
                f"🔊 **{to_smallcaps(f'audio stream #{i}')}** :\n"
                f"  • 🏷️ **{to_smallcaps('codec')}** : `{a['codec']}`\n"
                f"  • 📻 **{to_smallcaps('channels')}** : `{a['channels']} ({a['channel_layout']})`\n"
                f"  • 🎚️ **{to_smallcaps('sampling')}** : `{a['sample_rate']} Hz`\n"
                f"  • 🌐 **{to_smallcaps('language')}** : `{lang_val}`\n\n"
            )

    if info.get("subtitle_streams"):
        text += f"📝 **{to_smallcaps('subtitles')}** :\n"
        for i, s in enumerate(info["subtitle_streams"], 1):
            lang_val = s.get("language") or s.get("lang") or "Undetermined"
            text += f"  • #{i} : `{s['codec']}` [{lang_val}] - {s['title']}\n"
        text += "\n"

    text += f"⚡ **{to_smallcaps('powered by')}** : [{Config.WATERMARK}]({Config.WATERMARK_URL})"
    btn = [[InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"back_to_media_{media_msg.id}"), InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"), InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data")]]
    await safe_edit_text(status, text, reply_markup=InlineKeyboardMarkup(btn))


@Client.on_message(filters.command("search", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def search_command_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await safe_reply_text(message, 
            f"📝 **{to_smallcaps('usage')}** : `/search [keyword]`\n\n"
            f"• **{to_smallcaps('example')}** : `/search sample`\n\n"
            f"💡 {to_smallcaps('searches your indexed stream files instantly.')}"
            f"{format_watermark()}"
        )

    query_str = message.text.split(None, 1)[1].strip()
    files = await file_repo.search_files(query_str, limit=10)

    if not files:
        return await safe_reply_text(message, 
            f"🔍 **{to_smallcaps('no files found matching')}** : `{query_str}`\n\n"
            f"💡 {to_smallcaps('try another search keyword.')}"
            f"{format_watermark()}"
        )

    text = f"✦ **{to_smallcaps('search results for')}** : `{query_str}` ✦\n\n"
    buttons = []
    for i, f in enumerate(files, 1):
        fname = f.get("file_name", "Media")
        fid = f.get("file_id")
        fsize = humanbytes(f.get("file_size", 0))
        text += f"{i}. 📁 **{fname}** (`{fsize}`)\n"
        buttons.append([
            InlineKeyboardButton(f"🎬 {i}. {to_smallcaps('watch (4 players)')}", url=f"{Config.BASE_URL}/watch/{fid}"),
            InlineKeyboardButton(f"⬇️ {to_smallcaps('download')}", url=f"{Config.BASE_URL}/download/{fid}"),
        ])

    text += format_watermark()
    await safe_reply_text(message, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)


# ==================== PRIVATE USER CLOUD VAULT & PRESET/TASK DASHBOARD ====================

async def render_user_vault_view(client: Client, target_msg: Message, user_id: int, page: int = 0, is_edit: bool = True):
    per_page = 5
    page = max(0, int(page))
    skip = page * per_page
    files, total = await asyncio.gather(
        file_repo.get_user_files(user_id, skip=skip, limit=per_page),
        file_repo.get_user_files_count(user_id),
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page >= total_pages and total > 0:
        page = total_pages - 1
        skip = page * per_page
        files = await file_repo.get_user_files(user_id, skip=skip, limit=per_page)

    text = (
        f"✦ **{to_smallcaps('private cloud file vault')}** ✦ 🗂️\n\n"
        f"• 👤 **{to_smallcaps('user id')}** : `{user_id}`\n"
        f"• 📦 **{to_smallcaps('indexed files')}** : `{total}` (Page `{page + 1}/{total_pages}`)\n\n"
    )
    buttons = []
    if not files:
        text += (
            f"ℹ️ _{to_smallcaps('your private vault is currently empty.')}_\n"
            f"💡 {to_smallcaps('send or forward any video, audio, or document to index it with 4-player stream links!')}"
        )
    else:
        for idx, f in enumerate(files, start=skip + 1):
            fid = f.get("file_id", "")
            fname = (f.get("file_name") or "Media.mp4").replace("`", "'")
            fsize = humanbytes(f.get("file_size", 0))
            w_url = f"{Config.BASE_URL}/watch/{fid}"
            d_url = f"{Config.BASE_URL}/download/{fid}"
            text += f"`{idx}.` 📁 `{fname[:42]}` (`{fsize}`)\n"
            buttons.append([
                InlineKeyboardButton(f"🎬 #{idx} {to_smallcaps('watch')}", url=w_url),
                InlineKeyboardButton(f"⬇️ {to_smallcaps('dl')}", url=d_url),
                InlineKeyboardButton("📤 ᴛɢ", callback_data=f"pvt_vault#send_{fid}"),
                InlineKeyboardButton("🗑️", callback_data=f"pvt_vault#del_{fid}_{page}"),
            ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(f"⬅️ {to_smallcaps('prev')}", callback_data=f"pvt_vault#page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data=f"pvt_vault#page_{page}"))
    if skip + per_page < total:
        nav_row.append(InlineKeyboardButton(f"{to_smallcaps('next')} ➡️", callback_data=f"pvt_vault#page_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    if total > 0:
        buttons.append([
            InlineKeyboardButton(f"📜 {to_smallcaps('export links (.txt)')}", callback_data="pvt_vault#export"),
            InlineKeyboardButton(f"🧹 {to_smallcaps('clear vault')}", callback_data="pvt_vault#clear_ask"),
        ])
    buttons.append([
        InlineKeyboardButton(f"🎛️ {to_smallcaps('presets & tasks')}", callback_data="pvt_dash#home"),
        InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])
    text += format_watermark()
    if is_edit:
        await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    else:
        await safe_reply_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)


@Client.on_message(filters.command(["myfiles", "vault", "history", "clearfiles"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def user_vault_cmd_handler(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    cmd = message.command[0].lower()
    logger.info("🗂️ /%s ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s", cmd, uid)
    if cmd == "clearfiles":
        removed = await file_repo.clear_user_files(uid)
        return await safe_reply_text(
            message,
            f"🧹 **{to_smallcaps('cleared')} `{removed}` {to_smallcaps('files from your private vault!')}**{format_watermark()}",
        )
    await render_user_vault_view(client, message, uid, page=0, is_edit=False)


@Client.on_callback_query(filters.regex(r"^pvt_vault#(.+)$"))
async def user_vault_callback_handler(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    act = query.matches[0].group(1)
    logger.info("🔘 ᴘᴠᴛ_ᴠᴀᴜʟᴛ › ᴜsᴇʀ=%s ᴀᴄᴛ=%s", uid, act)

    if act.startswith("page_"):
        pg = int(act.split("_", 1)[1]) if act.split("_", 1)[1].isdigit() else 0
        await query.answer(f"🗂️ ᴘᴀɢᴇ {pg + 1}")
        return await render_user_vault_view(client, query.message, uid, page=pg, is_edit=True)

    if act.startswith("send_"):
        fid = act.split("_", 1)[1]
        fdoc = await file_repo.get_file(fid)
        if not fdoc:
            return await query.answer("❌ ғɪʟᴇ ɴᴏᴛ ғᴏᴜɴᴅ ᴏʀ ʀᴇᴠᴏᴋᴇᴅ!", show_alert=True)
        await query.answer("📤 sᴇɴᴅɪɴɢ ғɪʟᴇ ᴛᴏ ʏᴏᴜʀ ᴄʜᴀᴛ...")
        try:
            src_msg = await client.get_messages(int(fdoc["chat_id"]), int(fdoc["message_id"]))
            if src_msg and src_msg.media:
                await execute_with_floodwait(src_msg.copy, chat_id=query.message.chat.id)
                return
        except Exception as exc:
            logger.warning("Vault copy failed for %s: %s", fid, exc)
        return await query.answer("⚠️ ᴏʀɪɢɪɴᴀʟ ᴍᴇᴅɪᴀ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ɪɴ sᴛᴏʀᴀɢᴇ.", show_alert=True)

    if act.startswith("del_"):
        parts = act.split("_")
        fid = parts[1] if len(parts) > 1 else ""
        pg = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await file_repo.revoke_file(fid)
        await query.answer("🗑️ ғɪʟᴇ ʀᴇᴍᴏᴠᴇᴅ ғʀᴏᴍ ᴠᴀᴜʟᴛ!")
        return await render_user_vault_view(client, query.message, uid, page=pg, is_edit=True)

    if act == "export":
        files = await file_repo.get_user_files(uid, skip=0, limit=250)
        if not files:
            return await query.answer("ℹ️ ɴᴏ ғɪʟᴇs ᴛᴏ ᴇxᴘᴏʀᴛ!", show_alert=True)
        await query.answer("📜 ᴇxᴘᴏʀᴛɪɴɢ ʟɪɴᴋs...")
        lines = [f"# MMW BOTZ — Private Cloud Vault Export (User {uid})", ""]
        for i, f in enumerate(files, 1):
            fid = f.get("file_id")
            fname = f.get("file_name", "Media.mp4")
            fsize = humanbytes(f.get("file_size", 0))
            lines.append(
                f"{i}. {fname} ({fsize})\n"
                f"   Watch (4 Players): {Config.BASE_URL}/watch/{fid}\n"
                f"   Direct Download  : {Config.BASE_URL}/download/{fid}\n"
            )
        bio = io.BytesIO("\n".join(lines).encode("utf-8"))
        bio.name = f"MMW_Vault_Links_{uid}.txt"
        await execute_with_floodwait(
            query.message.reply_document,
            document=bio,
            caption=f"📜 **{to_smallcaps('exported')} `{len(files)}` {to_smallcaps('stream & download links!')}**{format_watermark()}",
        )
        return

    if act == "clear_ask":
        await query.answer()
        return await safe_edit_text(
            query.message,
            f"⚠️ **{to_smallcaps('confirm clear all vault files?')}** 🧹\n\n"
            f"{to_smallcaps('this will revoke all your saved stream links in the vault.')}{format_watermark()}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"✅ {to_smallcaps('yes, clear all')}", callback_data="pvt_vault#clear_do"),
                    InlineKeyboardButton(f"❌ {to_smallcaps('cancel')}", callback_data="pvt_vault#page_0"),
                ]
            ]),
        )

    if act == "clear_do":
        removed = await file_repo.clear_user_files(uid)
        await query.answer(f"🧹 ᴄʟᴇᴀʀᴇᴅ {removed} ғɪʟᴇs!", show_alert=True)
        return await render_user_vault_view(client, query.message, uid, page=0, is_edit=True)


async def render_user_dashboard_view(target_msg: Message, user_id: int, first_name: str = "User", is_edit: bool = True):
    u, total_files = await asyncio.gather(
        user_repo.get_or_create(user_id, first_name),
        file_repo.get_user_files_count(user_id),
    )
    user_tasks = [t for t in task_manager.get_user_tasks(user_id) if not t.is_cancelled]
    preset = str(u.get("compression_preset") or "turbo").lower()
    res = str(u.get("resolution") or "720p").lower()
    tpl = u.get("rename_format") or "{prefix}{filename}{suffix}.{ext}"
    prefix = u.get("prefix") or "None"
    suffix = u.get("suffix") or "None"
    auto_ren = "🟢 ON" if u.get("auto_rename") else "⚪ OFF"
    auto_comp = "🟢 ON" if u.get("auto_compress") else "⚪ OFF"
    auto_str = "🟢 ON" if u.get("auto_stream", True) else "⚪ OFF"

    text = (
        f"✦ **{to_smallcaps('personal media, preset & task studio')}** ✦ 🎛️\n\n"
        f"• 👤 **{to_smallcaps('user')}** : `{first_name}` (`{user_id}`)\n"
        f"• 🗂️ **{to_smallcaps('vault files')}** : `{total_files}` │ ⚙️ **{to_smallcaps('active tasks')}** : `{len(user_tasks)}`\n"
        f"• 🗜️ **{to_smallcaps('default ffmpeg preset')}** : `{preset.upper()}` (`{res}`)\n"
        f"• ✏️ **{to_smallcaps('rename format')}** : `{tpl}`\n"
        f"• 🏷️ **{to_smallcaps('prefix / suffix')}** : `{prefix}` / `{suffix}`\n"
        f"• 🤖 **{to_smallcaps('auto modes')}** : `Rename: {auto_ren} • Compress: {auto_comp} • Stream: {auto_str}`\n\n"
        f"💡 **{to_smallcaps('tap below to switch your default ffmpeg speed preset or resolution:')}**"
        f"{format_watermark()}"
    )

    buttons = [
        [
            InlineKeyboardButton(f"{'🟢 ' if preset == 'hyper' else ''}⚡ ʜʏᴘᴇʀ", callback_data="pvt_preset#hyper"),
            InlineKeyboardButton(f"{'🟢 ' if preset == 'turbo' else ''}🚀 ᴛᴜʀʙᴏ", callback_data="pvt_preset#turbo"),
            InlineKeyboardButton(f"{'🟢 ' if preset == 'superfast' else ''}💨 sᴜᴘᴇʀғᴀsᴛ", callback_data="pvt_preset#superfast"),
            InlineKeyboardButton(f"{'🟢 ' if preset == 'hevc' else ''}🧬 ʜ.265", callback_data="pvt_preset#hevc"),
        ],
        [
            InlineKeyboardButton(f"{'🟢 ' if res == '360p' else ''}360p", callback_data="pvt_res#360p"),
            InlineKeyboardButton(f"{'🟢 ' if res == '480p' else ''}480p", callback_data="pvt_res#480p"),
            InlineKeyboardButton(f"{'🟢 ' if res == '720p' else ''}720p", callback_data="pvt_res#720p"),
            InlineKeyboardButton(f"{'🟢 ' if res == '1080p' else ''}1080p", callback_data="pvt_res#1080p"),
        ],
        [
            InlineKeyboardButton(f"🗂️ {to_smallcaps('open file vault')}", callback_data="pvt_vault#page_0"),
            InlineKeyboardButton(f"🛑 {to_smallcaps('stop my tasks')} ({len(user_tasks)})", callback_data="pvt_dash#cancel_all"),
        ],
        [
            InlineKeyboardButton(f"⚙️ {to_smallcaps('all settings')}", callback_data="nav_settings"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]
    if is_edit:
        await safe_edit_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await safe_reply_text(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons))


@Client.on_message(filters.command(["mytasks", "myprofile", "preset", "setpreset"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def user_dashboard_cmd_handler(client: Client, message: Message):
    user = message.from_user
    uid = user.id if user else 0
    fname = user.first_name if user else "User"
    cmd = message.command[0].lower()
    if cmd in ("preset", "setpreset") and len(message.command) > 1:
        arg = message.command[1].strip().lower()
        if arg in ("hyper", "turbo", "ultrafast", "superfast", "veryfast", "fast", "hevc"):
            await user_repo.update_user(uid, {"compression_preset": arg})
        elif arg in ("360p", "480p", "720p", "1080p"):
            await user_repo.update_user(uid, {"resolution": arg})
    await render_user_dashboard_view(message, uid, fname, is_edit=False)


@Client.on_callback_query(filters.regex(r"^pvt_(dash|preset|res)#(.+)$"))
async def user_dashboard_cb_handler(client: Client, query: CallbackQuery):
    user = query.from_user
    uid = user.id if user else 0
    fname = user.first_name if user else "User"
    kind = query.matches[0].group(1)
    val = query.matches[0].group(2)

    if kind == "preset":
        await user_repo.update_user(uid, {"compression_preset": val})
        await query.answer(f"🗜️ ᴘʀᴇsᴇᴛ sᴇᴛ ᴛᴏ {val.upper()}!")
    elif kind == "res":
        await user_repo.update_user(uid, {"resolution": val})
        await query.answer(f"📐 ʀᴇsᴏʟᴜᴛɪᴏɴ sᴇᴛ ᴛᴏ {val.upper()}!")
    elif kind == "dash" and val == "cancel_all":
        cancelled = await task_manager.cancel_user_tasks(uid)
        await query.answer(f"🛑 ᴄᴀɴᴄᴇʟʟᴇᴅ {cancelled} ᴀᴄᴛɪᴠᴇ ᴛᴀsᴋ(s)!", show_alert=True)
    else:
        await query.answer("🎛️ ᴘʀᴇsᴇᴛ & ᴛᴀsᴋ sᴛᴜᴅɪᴏ")

    await render_user_dashboard_view(query.message, uid, fname, is_edit=True)
