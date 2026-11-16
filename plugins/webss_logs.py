from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
import requests
try:
    import aiohttp
except ImportError:
    aiohttp = None
import os
import io
import html
import logging
from io import BytesIO
from typing import Optional, Tuple
from urllib.parse import urlparse
from info import *
from utils import (
    LOG_FILE_PATH,
    redact_secrets,
    is_admin_sync,
    setup_logging,
    get_recent_logs_preview,
    get_expanded_logs,
    get_log_analytics,
    safe_edit_message,
)
from plugins.core_utils import safe_reply_text, execute_with_floodwait, MEMORY_LOGS

setup_logging()
logger = logging.getLogger("GokuWebLogs")

if not os.path.exists(LOG_FILE_PATH):
    open(LOG_FILE_PATH, "a", encoding="utf-8").close()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}


def _is_safe_public_url(raw_url: str) -> bool:
    """Validate URL scheme and block localhost/private-network SSRF targets."""
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.startswith(("10.", "192.168.", "169.254.")):
            return False
        return True
    except Exception:
        return False


async def paste_to_pasty(content: str, extension: str = "txt") -> Tuple[bool, Optional[str], Optional[str]]:
    """Upload text to pasty.lus.pm. Returns (success, url, raw_url)."""
    url = "https://pasty.lus.pm/api/v1/pastes"
    payload = {"content": content}
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    return False, None, None
                data = await resp.json()
                paste_id = data.get("id")
                if not paste_id:
                    return False, None, None
                paste_url = f"https://pasty.lus.pm/{paste_id}.{extension}"
                raw_url = f"https://pasty.lus.pm/{paste_id}/raw"
                return True, paste_url, raw_url
    except Exception:
        return False, None, None


@Client.on_message(filters.command("web", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def web_cmd(client: Client, message: Message):
    args = message.command
    if len(args) == 1:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b>\n"
            "• <code>/web &lt;url&gt;</code>\n"
            "• <code>/web d &lt;url&gt;</code>\n"
            "• <code>/web d2 &lt;url&gt;</code>\n"
            "• <code>/web pl &lt;url&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(args) == 2:
        subcmd = "normal"
        url = args[1]
    else:
        subcmd = args[1].lower()
        url = args[2]

    if not _is_safe_public_url(url):
        return await message.reply_text("❌ <b>ɪɴᴠᴀʟɪᴅ ᴏʀ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴜʀʟ.</b> ᴜsᴇ ᴀ ᴠᴀʟɪᴅ <code>https://</code> ᴜʀʟ.", parse_mode=enums.ParseMode.HTML)

    logger.info("🌐 /web ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴍᴏᴅᴇ=%s ᴜʀʟ=%s", message.from_user.id if message.from_user else 0, subcmd, url)
    status_msg = await message.reply_text("🌐 <b>ғᴇᴛᴄʜɪɴɢ ᴜʀʟ...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        resp = await asyncio.to_thread(requests.get, url, timeout=10)
        resp.raise_for_status()
        raw_text = resp.text

        if len(raw_text) > 5 * 1024 * 1024:
            await safe_edit_message(status_msg, "⚠️ <b>ᴛᴇxᴛ ᴛᴏᴏ ʟᴀʀɢᴇ</b> (&gt;5ᴍʙ). ᴄᴀɴ'ᴛ ғᴇᴛᴄʜ.", parse_mode=enums.ParseMode.HTML)
            return

        escaped_text = raw_text
        text_len = len(escaped_text)
        MAX_MSG_LEN = 3800

        if subcmd in ("d", "d2"):
            file_stream = io.BytesIO(escaped_text.encode("utf-8"))
            file_stream.name = "response.txt" if subcmd == "d" else "web_preview.txt"
            await status_msg.delete()
            await message.reply_document(
                document=file_stream,
                caption=f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b> <code>{html.escape(url)}</code>",
                parse_mode=enums.ParseMode.HTML,
            )

        elif subcmd == "pl":
            if text_len <= MAX_MSG_LEN:
                await safe_edit_message(status_msg, 
                    f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b>\n<code>{html.escape(url)}</code>\n\n"
                    f"📄 <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n<pre>{html.escape(escaped_text)}</pre>",
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                success, paste_url, raw_url = await paste_to_pasty(escaped_text, "html")
                if success:
                    await safe_edit_message(status_msg, 
                        f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b>\n<code>{html.escape(url)}</code>\n\n"
                        f"📄 <b>ᴄᴏɴᴛᴇɴᴛ ᴛᴏᴏ ʟᴏɴɢ</b> — ᴜᴘʟᴏᴀᴅᴇᴅ ᴛᴏ ᴘᴀsᴛʏ:\n\n"
                        f"🔗 <b>ʟɪɴᴋ:</b> <a href='{paste_url}'>ᴏᴘᴇɴ ʜᴇʀᴇ</a>\n"
                        f"📜 <b>ʀᴀᴡ:</b> <a href='{raw_url}'>ᴏᴘᴇɴ ʜᴇʀᴇ</a>",
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                else:
                    file_stream = io.BytesIO(escaped_text.encode("utf-8"))
                    file_stream.name = "web_content.txt"
                    await status_msg.delete()
                    await message.reply_document(
                        document=file_stream,
                        caption=f"🌐 <b>ᴜʀʟ:</b> <code>{html.escape(url)}</code>",
                        parse_mode=enums.ParseMode.HTML,
                    )
        else:
            if text_len <= MAX_MSG_LEN:
                await safe_edit_message(status_msg, 
                    f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b>\n<code>{html.escape(url)}</code>\n\n"
                    f"📄 <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n<pre>{html.escape(escaped_text)}</pre>",
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                file_stream = io.BytesIO(escaped_text.encode("utf-8"))
                file_stream.name = "web_content.txt"
                await status_msg.delete()
                await message.reply_document(
                    document=file_stream,
                    caption=f"🌐 <b>ᴜʀʟ:</b> <code>{html.escape(url)}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )

    except requests.exceptions.Timeout:
        await safe_edit_message(status_msg, "❌ <b>ғᴀɪʟᴇᴅ:</b> ʀᴇǫᴜᴇsᴛ ᴛɪᴍᴇᴅ ᴏᴜᴛ.", parse_mode=enums.ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        await safe_edit_message(status_msg, f"❌ <b>ғᴀɪʟᴇᴅ:</b> ɴᴇᴛᴡᴏʀᴋ ᴇʀʀᴏʀ — <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await safe_edit_message(status_msg, f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("webi", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def web_cmdi(client: Client, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/webi &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML)

    url = args[1]
    if not _is_safe_public_url(url):
        return await message.reply_text("❌ <b>ɪɴᴠᴀʟɪᴅ ᴏʀ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴜʀʟ.</b>", parse_mode=enums.ParseMode.HTML)

    logger.info("🌐 /webi ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴜʀʟ=%s", message.from_user.id if message.from_user else 0, url)
    status_msg = await message.reply_text("🌐 <b>ғᴇᴛᴄʜɪɴɢ ᴜʀʟ...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    await safe_edit_message(status_msg, 
                        f"❌ <b>ғᴀɪʟᴇᴅ:</b> ʜᴛᴛᴘ {resp.status} — {resp.reason}",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return
                raw_text = await resp.text()
                if len(raw_text) > 5 * 1024 * 1024:
                    await safe_edit_message(status_msg, "⚠️ <b>ᴛᴇxᴛ ᴛᴏᴏ ʟᴀʀɢᴇ</b> (&gt;5ᴍʙ). ᴄᴀɴ'ᴛ ғᴇᴛᴄʜ.", parse_mode=enums.ParseMode.HTML)
                    return

        escaped_text = html.escape(raw_text)
        text_len = len(escaped_text)
        MAX_MSG_LEN = 3800

        if text_len <= MAX_MSG_LEN:
            await safe_edit_message(status_msg, 
                f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b>\n<code>{html.escape(url)}</code>\n\n"
                f"📄 <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n<pre>{escaped_text}</pre>",
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            success, paste_url, raw_url = await paste_to_pasty(raw_text, "html")
            if success:
                await safe_edit_message(status_msg, 
                    f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b>\n<code>{html.escape(url)}</code>\n\n"
                    "📄 <b>ᴄᴏɴᴛᴇɴᴛ ᴛᴏᴏ ʟᴏɴɢ</b> — ᴜᴘʟᴏᴀᴅᴇᴅ ᴛᴏ ᴘᴀsᴛʏ:\n\n"
                    f"🔗 <b>ʟɪɴᴋ:</b> <a href='{paste_url}'>ᴏᴘᴇɴ ʜᴇʀᴇ</a>\n"
                    f"📜 <b>ʀᴀᴡ:</b> <a href='{raw_url}'>ᴏᴘᴇɴ ʜᴇʀᴇ</a>",
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                file_stream = io.BytesIO(raw_text.encode("utf-8"))
                file_stream.name = "web_content.txt"
                await status_msg.delete()
                await message.reply_document(
                    document=file_stream,
                    caption=f"🌐 <b>ᴜʀʟ:</b> <code>{html.escape(url)}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )

    except asyncio.TimeoutError:
        await safe_edit_message(status_msg, "❌ <b>ғᴀɪʟᴇᴅ:</b> ʀᴇǫᴜᴇsᴛ ᴛɪᴍᴇᴅ ᴏᴜᴛ.", parse_mode=enums.ParseMode.HTML)
    except aiohttp.ClientError as e:
        await safe_edit_message(status_msg, f"❌ <b>ғᴀɪʟᴇᴅ:</b> ɴᴇᴛᴡᴏʀᴋ ᴇʀʀᴏʀ — <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await safe_edit_message(status_msg, f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["webss", "fwebss"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def webss_cmd(client: Client, message: Message):
    cmd = message.command[0].lower()
    if len(message.command) < 2:
        return await message.reply_text(f"⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML)

    url = message.command[1]
    if not _is_safe_public_url(url):
        return await message.reply_text("❌ <b>ɪɴᴠᴀʟɪᴅ ᴏʀ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴜʀʟ.</b>", parse_mode=enums.ParseMode.HTML)

    logger.info("📸 /%s ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴜʀʟ=%s", cmd, message.from_user.id if message.from_user else 0, url)
    progress_msg = await message.reply_text("⏳ <b>ɢᴇɴᴇʀᴀᴛɪɴɢ ᴡᴇʙ sᴄʀᴇᴇɴsʜᴏᴛ...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        if cmd == "fwebss":
            screenshot_url = f"https://image.thum.io/get/width/1280/crop/768/noanimate/{url}"
        else:
            screenshot_url = f"https://image.thum.io/get/fullpage/{url}"
        response = await asyncio.to_thread(requests.get, screenshot_url, timeout=20)

        if response.status_code != 200:
            return await safe_edit_message(progress_msg, "❌ <b>ғᴀɪʟᴇᴅ:</b> ᴄᴏᴜʟᴅɴ'ᴛ ғᴇᴛᴄʜ sᴄʀᴇᴇɴsʜᴏᴛ.", parse_mode=enums.ParseMode.HTML)

        img = BytesIO(response.content)
        img.name = "screenshot.jpg"

        await message.reply_photo(
            photo=img,
            caption=(
                f"🌐 <b>ғᴇᴛᴄʜᴇᴅ ᴜʀʟ:</b> <code>{html.escape(url)}</code>\n"
                "📸 <b>sᴄʀᴇᴇɴsʜᴏᴛ ʀᴇᴀᴅʏ!</b> ⚡"
            ),
            parse_mode=enums.ParseMode.HTML,
        )
        await progress_msg.delete()

    except Exception as e:
        await safe_edit_message(progress_msg, f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


def build_logs_markup(active_mode: str = "all", lines_count: int = 50) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{'🟢 ' if lines_count == 35 else ''}📋 35 ʟɪɴᴇs",
                callback_data=f"logs_act#view_{active_mode}_35",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if lines_count == 65 else ''}📜 65 ʟɪɴᴇs",
                callback_data=f"logs_act#view_{active_mode}_65",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if lines_count >= 120 else ''}📚 120 ᴍᴀx",
                callback_data=f"logs_act#view_{active_mode}_120",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{'🟢 ' if active_mode == 'all' else ''}🌐 ᴀʟʟ",
                callback_data=f"logs_act#view_all_{lines_count}",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if active_mode == 'err' else ''}⚠️ ᴇʀʀᴏʀs",
                callback_data=f"logs_act#view_err_{lines_count}",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if active_mode == 'flood' else ''}🌊 ғʟᴏᴏᴅ",
                callback_data=f"logs_act#view_flood_{lines_count}",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if active_mode == 'media' else ''}🎬 ᴍᴇᴅɪᴀ",
                callback_data=f"logs_act#view_media_{lines_count}",
            ),
            InlineKeyboardButton(
                f"{'🟢 ' if active_mode == 'tasks' else ''}♻️ ᴛᴀsᴋs",
                callback_data=f"logs_act#view_tasks_{lines_count}",
            ),
        ],
        [
            InlineKeyboardButton("📊 ʟᴏɢ & ғʟᴏᴏᴅ sᴛᴀᴛs", callback_data="logs_act#stats"),
            InlineKeyboardButton("♻️ ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ", callback_data="task_rec#report"),
            InlineKeyboardButton("🔗 ᴡᴇʙ ᴘᴀsᴛᴇ", callback_data="logs_act#paste"),
        ],
        [
            InlineKeyboardButton("📥 ᴅᴏᴡɴʟᴏᴀᴅ ғᴜʟʟ ʟᴏɢs", callback_data="logs_act#download"),
            InlineKeyboardButton("🗑️ ᴄʟᴇᴀʀ ʟᴏɢs", callback_data="logs_act#confirm_clear"),
        ],
        [
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"logs_act#refresh_{active_mode}_{lines_count}"),
            InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="logs_act#close"),
        ],
    ])


def _render_logs_header(mode: str, lines_count: int) -> str:
    stats = get_log_analytics()
    fw = stats.get("floodwait_solver", {})
    tr = stats.get("task_recovery", {})
    mode_labels = {
        "all": "ᴀʟʟ ʟᴏɢs",
        "err": "ᴇʀʀᴏʀs & ᴡᴀʀɴɪɴɢs",
        "flood": "ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ",
        "media": "ᴍᴇᴅɪᴀ / ғғᴍᴘᴇɢ / sᴛʀᴇᴀᴍ",
        "tasks": "ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ & ǫᴜᴇᴜᴇ",
    }
    label = mode_labels.get(mode, mode.upper())
    return (
        f"📋 <b>ᴇxᴘᴀɴᴅᴇᴅ ᴘʀᴏ ʟᴏɢ ᴄᴏɴsᴏʟᴇ ({label} • ʟᴀsᴛ {lines_count})</b> 🛡️\n"
        f"• 📊 <b>ᴛᴏᴛᴀʟ:</b> <code>{stats['total_lines']}</code> │ "
        f"⚠️ <b>ᴡᴀʀɴ:</b> <code>{stats['warning_count']}</code> │ "
        f"❌ <b>ᴇʀʀ:</b> <code>{stats['error_count']}</code> │ "
        f"🌊 <b>ғʟᴏᴏᴅs:</b> <code>{fw.get('total_events', 0)}</code> │ "
        f"♻️ <b>ᴛᴀsᴋs:</b> <code>{tr.get('completed_tasks', 0)}/{tr.get('total_tasks_detected', tr.get('total_detected', 0))}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
    )


@Client.on_message(
    filters.command(
        ["logs", "log", "errorlogs", "floodlogs", "medialogs", "logstats"],
        prefixes=COMMAND_PREFIXES,
    )
    & filters.incoming,
    group=-1,
)
async def handle_logs_cmds(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id or not is_admin_sync(user_id):
        return

    cmd = message.command[0].lower()
    logger.info("📋 /%s ɪɴᴠᴏᴋᴇᴅ › ᴀᴅᴍɪɴ=%s ᴀʀɢs=%s", cmd, user_id, message.command[1:])

    has_file = os.path.isfile(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 0
    if not has_file and not MEMORY_LOGS:
        await safe_reply_text(
            message,
            "ℹ️ <b>ʟᴏɢ ғɪʟᴇ ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴇᴍᴘᴛʏ ᴏʀ ɴᴏᴛ ғᴏᴜɴᴅ.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        message.stop_propagation()
        return

    if cmd == "log":
        status_msg = await safe_reply_text(message, "⏳ <b>ᴘʀᴇᴘᴀʀɪɴɢ ғᴜʟʟ ᴇxᴘᴀɴᴅᴇᴅ ʟᴏɢ ғɪʟᴇ...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            if has_file:
                with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                    redacted_full = redact_secrets(f.read())
            else:
                redacted_full = redact_secrets("\n".join(MEMORY_LOGS))

            bio = io.BytesIO(redacted_full.encode("utf-8"))
            bio.name = "TelegramBot_Expanded_Logs.txt"
            stats = get_log_analytics()
            fw = stats["floodwait_solver"]

            caption = (
                "📄 <b>ᴍᴍᴡ ᴘʀᴏ ᴇxᴘᴀɴᴅᴇᴅ ᴀᴘᴘʟɪᴄᴀᴛɪᴏɴ ʟᴏɢs</b> 🛡️\n\n"
                f"• 📏 <b>ᴛᴏᴛᴀʟ ʟɪɴᴇs:</b> <code>{stats['total_lines']}</code>\n"
                f"• 📦 <b>sɪᴢᴇ:</b> <code>{len(redacted_full) / 1024:.2f} ᴋʙ</code>\n"
                f"• ⚠️ <b>ᴡᴀʀɴɪɴɢs / ᴇʀʀᴏʀs:</b> <code>{stats['warning_count']} / {stats['error_count']}</code>\n"
                f"• 🌊 <b>ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇᴅ:</b> <code>{fw.get('total_events', 0)} ({fw.get('total_slept_seconds', 0)}s slept)</code>\n"
                "• 🔒 <b>sᴇᴄᴜʀɪᴛʏ:</b> <code>sᴇᴄʀᴇᴛs ʀᴇᴅᴀᴄᴛᴇᴅ</code>"
            )

            await execute_with_floodwait(
                message.reply_document,
                document=bio,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                quote=True,
                reply_markup=build_logs_markup("all", 65),
            )
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        except Exception as e:
            if status_msg:
                await safe_edit_message(status_msg, f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)

        message.stop_propagation()
        return

    if cmd == "logstats":
        stats = get_log_analytics()
        fw = stats["floodwait_solver"]
        text = (
            "📊 <b>ᴇxᴘᴀɴᴅᴇᴅ ʟᴏɢs &amp; ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ ᴀɴᴀʟʏᴛɪᴄs</b> ⚡\n\n"
            f"• 📏 <b>ᴛᴏᴛᴀʟ ʟᴏɢ ʟɪɴᴇs:</b> <code>{stats['total_lines']}</code>\n"
            f"• ℹ️ <b>ɪɴғᴏ ᴇᴠᴇɴᴛs:</b> <code>{stats['info_count']}</code>\n"
            f"• ⚠️ <b>ᴡᴀʀɴɪɴɢ ᴇᴠᴇɴᴛs:</b> <code>{stats['warning_count']}</code>\n"
            f"• ❌ <b>ᴇʀʀᴏʀ ᴇᴠᴇɴᴛs:</b> <code>{stats['error_count']}</code>\n"
            f"• 🎬 <b>ᴍᴇᴅɪᴀ/ғғᴍᴘᴇɢ ᴇᴠᴇɴᴛs:</b> <code>{stats['media_log_lines']}</code>\n"
            f"• 💾 <b>ʟᴏɢ ғɪʟᴇ sɪᴢᴇ:</b> <code>{stats['file_size_kb']} KB</code> (ʀᴀᴍ: <code>{stats['memory_buffer_count']}/2000</code>)\n"
            f"• ⚙️ <b>ᴀᴄᴛɪᴠᴇ ᴍᴇᴅɪᴀ ᴛᴀsᴋs:</b> <code>{stats['active_media_tasks']}</code>\n\n"
            "🌊 <b>ᴘʀᴏ ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ (asyncio.sleep):</b>\n"
            f"• 🛡️ <b>ʜᴀɴᴅʟᴇᴅ ғʟᴏᴏᴅᴡᴀɪᴛs:</b> <code>{fw.get('total_events', 0)}</code>\n"
            f"• ⏱️ <b>ᴛᴏᴛᴀʟ ᴀᴜᴛᴏ-sʟᴇᴘᴛ:</b> <code>{fw.get('total_slept_seconds', 0)}s</code>\n"
            f"• 🏷️ <b>ʟᴀsᴛ ᴍᴇᴛʜᴏᴅ:</b> <code>{html.escape(str(fw.get('last_method', 'None')))}</code> (<code>{fw.get('last_wait_seconds', 0)}s</code>)\n"
            f"• ⚡ <b>ᴅᴇᴅᴜᴘʟɪᴄᴀᴛᴇᴅ / ᴄᴏᴀʟᴇsᴄᴇᴅ ᴇᴅɪᴛs:</b> <code>{fw.get('deduplicated_edits', 0)} / {fw.get('coalesced_progress_edits', 0)}</code>\n"
            f"• ♻️ <b>ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ (ᴅᴇᴛᴇᴄᴛᴇᴅ/ᴅᴏɴᴇ/ʀᴇᴄᴏᴠᴇʀᴇᴅ):</b> <code>{stats.get('task_recovery', {}).get('total_detected', 0)} / {stats.get('task_recovery', {}).get('completed_tasks', 0)} / {stats.get('task_recovery', {}).get('recovered_tasks', 0)}</code>"
        )
        await safe_reply_text(
            message,
            text=text,
            reply_markup=build_logs_markup("all", 65),
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    # Determine mode/lines/search from command & args
    mode = "all"
    lines_count = 65
    search_q = ""
    if cmd == "errorlogs":
        mode = "err"
    elif cmd == "floodlogs":
        mode = "flood"
    elif cmd == "medialogs":
        mode = "media"

    if len(message.command) > 1:
        arg1 = message.command[1].strip()
        if arg1.isdigit():
            lines_count = max(10, min(200, int(arg1)))
        elif arg1.lower() in ("err", "error", "errors", "warn"):
            mode = "err"
        elif arg1.lower() in ("flood", "floodwait"):
            mode = "flood"
        elif arg1.lower() in ("media", "ffmpeg", "stream", "rename", "compress"):
            mode = "media"
        else:
            search_q = " ".join(message.command[1:]).strip()

    try:
        header = _render_logs_header(mode, lines_count)
        max_body = max(1500, 3850 - len(header))
        log_content = html.escape(
            get_expanded_logs(lines_count=lines_count, mode=mode, search_query=search_q, max_chars=max_body)
        )
        text = f"{header}<pre>{log_content}</pre>"
        await safe_reply_text(
            message,
            text=text,
            reply_markup=build_logs_markup(mode, lines_count),
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
    except Exception as e:
        await safe_reply_text(
            message,
            f"❌ <b>ᴇʀʀᴏʀ ʀᴇᴀᴅɪɴɢ ʟᴏɢs:</b> <code>{html.escape(str(e))}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    message.stop_propagation()


@Client.on_callback_query(filters.regex(r"^logs_act#"))
async def logs_action_cb(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    act = query.data.split("#", 1)[1]
    logger.info("🔘 ʟᴏɢs_ᴀᴄᴛ › ᴀᴅᴍɪɴ=%s ᴀᴄᴛɪᴏɴ=%s", query.from_user.id, act)

    if act == "download":
        has_file = os.path.isfile(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 0
        if not has_file and not MEMORY_LOGS:
            return await query.answer("ℹ️ ʟᴏɢ ғɪʟᴇ ɪs ᴇᴍᴘᴛʏ!", show_alert=True)

        await query.answer("📥 sᴇɴᴅɪɴɢ ғᴜʟʟ ʟᴏɢ ғɪʟᴇ...")
        if has_file:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                redacted_full = redact_secrets(f.read())
        else:
            redacted_full = redact_secrets("\n".join(MEMORY_LOGS))

        bio = io.BytesIO(redacted_full.encode("utf-8"))
        bio.name = "TelegramBot_Expanded_Logs.txt"
        stats = get_log_analytics()

        await execute_with_floodwait(
            query.message.reply_document,
            document=bio,
            caption=(
                "📄 <b>ʙᴏᴛ ᴇxᴘᴀɴᴅᴇᴅ ᴀᴘᴘʟɪᴄᴀᴛɪᴏɴ ʟᴏɢs (sᴇᴄʀᴇᴛs ʀᴇᴅᴀᴄᴛᴇᴅ)</b> 🛡️\n"
                f"• 📏 <b>ʟɪɴᴇs:</b> <code>{stats['total_lines']}</code> │ 📦 <b>sɪᴢᴇ:</b> <code>{len(redacted_full)/1024:.2f} KB</code>"
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    elif act == "paste":
        await query.answer("🔗 ᴜᴘʟᴏᴀᴅɪɴɢ ғᴜʟʟ ʟᴏɢs ᴛᴏ ᴘᴀsᴛʏ...")
        has_file = os.path.isfile(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 0
        if has_file:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                redacted_full = redact_secrets(f.read())
        else:
            redacted_full = redact_secrets("\n".join(MEMORY_LOGS))
        ok, paste_url, raw_url = await paste_to_pasty(redacted_full[-250000:] or "Empty logs", "txt")
        if ok:
            await safe_edit_message(
                query.message,
                "🔗 <b>ғᴜʟʟ ᴇxᴘᴀɴᴅᴇᴅ ʟᴏɢs ᴜᴘʟᴏᴀᴅᴇᴅ!</b> 🌐\n\n"
                f"• 📄 <b>ᴡᴇʙ ᴠɪᴇᴡ:</b> <a href='{paste_url}'>{paste_url}</a>\n"
                f"• 📜 <b>ʀᴀᴡ ᴛᴇxᴛ:</b> <a href='{raw_url}'>{raw_url}</a>",
                reply_markup=build_logs_markup("all", 65),
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await query.answer("⚠️ ᴘᴀsᴛᴇ sᴇʀᴠɪᴄᴇ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ; ᴜsᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʙᴜᴛᴛᴏɴ.", show_alert=True)

    elif act == "stats":
        await query.answer("📊 ʟᴏɢ & ғʟᴏᴏᴅᴡᴀɪᴛ sᴛᴀᴛs")
        stats = get_log_analytics()
        fw = stats["floodwait_solver"]
        text = (
            "📊 <b>ᴇxᴘᴀɴᴅᴇᴅ ʟᴏɢs &amp; ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ ᴀɴᴀʟʏᴛɪᴄs</b> ⚡\n\n"
            f"• 📏 <b>ᴛᴏᴛᴀʟ ʟᴏɢ ʟɪɴᴇs:</b> <code>{stats['total_lines']}</code>\n"
            f"• ℹ️ <b>ɪɴғᴏ ᴇᴠᴇɴᴛs:</b> <code>{stats['info_count']}</code>\n"
            f"• ⚠️ <b>ᴡᴀʀɴɪɴɢ ᴇᴠᴇɴᴛs:</b> <code>{stats['warning_count']}</code>\n"
            f"• ❌ <b>ᴇʀʀᴏʀ ᴇᴠᴇɴᴛs:</b> <code>{stats['error_count']}</code>\n"
            f"• 🎬 <b>ᴍᴇᴅɪᴀ/ғғᴍᴘᴇɢ ᴇᴠᴇɴᴛs:</b> <code>{stats['media_log_lines']}</code>\n"
            f"• 💾 <b>ʟᴏɢ ғɪʟᴇ sɪᴢᴇ:</b> <code>{stats['file_size_kb']} KB</code> (ʀᴀᴍ: <code>{stats['memory_buffer_count']}/2000</code>)\n"
            f"• ⚙️ <b>ᴀᴄᴛɪᴠᴇ ᴍᴇᴅɪᴀ ᴛᴀsᴋs:</b> <code>{stats['active_media_tasks']}</code>\n\n"
            "🌊 <b>ᴘʀᴏ ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ (asyncio.sleep):</b>\n"
            f"• 🛡️ <b>ʜᴀɴᴅʟᴇᴅ ғʟᴏᴏᴅᴡᴀɪᴛs:</b> <code>{fw.get('total_events', 0)}</code>\n"
            f"• ⏱️ <b>ᴛᴏᴛᴀʟ ᴀᴜᴛᴏ-sʟᴇᴘᴛ:</b> <code>{fw.get('total_slept_seconds', 0)}s</code>\n"
            f"• 🏷️ <b>ʟᴀsᴛ ᴍᴇᴛʜᴏᴅ:</b> <code>{html.escape(str(fw.get('last_method', 'None')))}</code> (<code>{fw.get('last_wait_seconds', 0)}s</code>)\n"
            f"• ⚡ <b>ᴅᴇᴅᴜᴘʟɪᴄᴀᴛᴇᴅ / ᴄᴏᴀʟᴇsᴄᴇᴅ ᴇᴅɪᴛs:</b> <code>{fw.get('deduplicated_edits', 0)} / {fw.get('coalesced_progress_edits', 0)}</code>\n"
            f"• ♻️ <b>ᴛᴀsᴋ ʀᴇᴄᴏᴠᴇʀʏ (ᴅᴇᴛᴇᴄᴛᴇᴅ/ᴅᴏɴᴇ/ʀᴇᴄᴏᴠᴇʀᴇᴅ):</b> <code>{stats.get('task_recovery', {}).get('total_detected', 0)} / {stats.get('task_recovery', {}).get('completed_tasks', 0)} / {stats.get('task_recovery', {}).get('recovered_tasks', 0)}</code>"
        )
        await safe_edit_message(
            query.message,
            text=text,
            reply_markup=build_logs_markup("all", 65),
            parse_mode=enums.ParseMode.HTML,
        )

    elif act == "refresh" or act.startswith(("view_", "refresh_")):
        mode = "all"
        lines_count = 65
        parts = act.split("_")
        if len(parts) >= 3:
            mode = parts[1]
            lines_count = int(parts[2]) if parts[2].isdigit() else 65
        header = _render_logs_header(mode, lines_count)
        max_body = max(1500, 3850 - len(header))
        log_content = html.escape(get_expanded_logs(lines_count=lines_count, mode=mode, max_chars=max_body))
        new_text = f"{header}<pre>{log_content}</pre>"
        ok = await safe_edit_message(
            query.message,
            text=new_text,
            reply_markup=build_logs_markup(mode, lines_count),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer(f"🔄 ʟᴏɢs ({mode.upper()} • {lines_count})" if ok else "⚡ ᴀʟʀᴇᴀᴅʏ ᴜᴘ ᴛᴏ ᴅᴀᴛᴇ!")

    elif act == "confirm_clear":
        await query.answer()
        await safe_edit_message(
            query.message,
            "⚠️ <b>ᴄᴏɴғɪʀᴍ ᴄʟᴇᴀʀ ʟᴏɢs?</b> 🗑️\n\n"
            "ᴛʜɪs ᴡɪʟʟ ᴛʀᴜɴᴄᴀᴛᴇ <code>TelegramBot.txt</code> ᴀɴᴅ ʀᴇsᴇᴛ ᴛʜᴇ ɪɴ-ᴍᴇᴍᴏʀʏ ʟᴏɢ ʙᴜғғᴇʀ.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ ᴄᴏɴғɪʀᴍ", callback_data="logs_act#do_clear"),
                    InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="logs_act#refresh"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )

    elif act == "do_clear":
        try:
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
            MEMORY_LOGS.clear()
            logger.info("🧹 ʟᴏɢs ᴄʟᴇᴀʀᴇᴅ ʙʏ ᴀᴅᴍɪɴ › %s", query.from_user.id)
            await query.answer("✅ ʟᴏɢs ᴄʟᴇᴀʀᴇᴅ!", show_alert=True)
            await safe_edit_message(
                query.message,
                "✅ <b>ʟᴏɢs ᴄʟᴇᴀʀᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b> 🧹",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔄 ᴠɪᴇᴡ ʟᴏɢs", callback_data="logs_act#refresh"),
                        InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                    ]
                ]),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            await query.answer(f"❌ ᴇʀʀᴏʀ: {e}", show_alert=True)

    elif act == "close":
        await query.answer("✅ ᴄʟᴏsᴇᴅ")
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.answer("⚠️ ᴜɴᴋɴᴏᴡɴ ʟᴏɢ ᴀᴄᴛɪᴏɴ", show_alert=True)
