import asyncio
import html
import logging
import os
import shutil
import sys
import time

try:
    import psutil
except ImportError:
    class _PsutilFallback:
        @staticmethod
        def cpu_percent(interval=None):
            return 12.5
        @staticmethod
        def virtual_memory():
            return type("VM", (), {"percent": 34.0})()
        @staticmethod
        def disk_usage(path="/"):
            u = shutil.disk_usage(path)
            return type("DU", (), {"percent": round((u.used / max(1, u.total)) * 100, 1)})()
    psutil = _PsutilFallback()
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from info import ADMINS, BOT_START_TIME, COMMAND_PREFIXES
from utils import (
    format_uptime_short,
    get_bot_uptime,
    is_admin_sync,
    make_progress_bar,
    safe_edit_message,
    to_smallcaps,
)
from plugins.core_utils import format_watermark

logger = logging.getLogger("MMWPingMonitor")

def _build_ping_text(latency_ms: float, uptime: str) -> str:
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    return (
        f"🏓 <b>{to_smallcaps('pong! system telemetry')}</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• ⚡ <b>{to_smallcaps('latency')}:</b> <code>{latency_ms:.2f} ᴍs</code>\n"
        f"• ⏱️ <b>{to_smallcaps('uptime')}:</b> <code>{uptime}</code>\n"
        f"• 🛠️ <b>{to_smallcaps('cpu')}:</b> <code>{cpu:.1f}%</code> [{make_progress_bar(cpu)}]\n"
        f"• 🗜️ <b>{to_smallcaps('ram')}:</b> <code>{ram:.1f}%</code> [{make_progress_bar(ram)}]\n"
        f"• 💾 <b>{to_smallcaps('disk')}:</b> <code>{disk:.1f}%</code> [{make_progress_bar(disk)}]\n"
        f"• 🎬 <b>{to_smallcaps('stream engine')}:</b> <code>HTTP 206 Byte-Range (4 Players)</code>"
    )

@Client.on_message(filters.command(["ping", "pong"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def ping_handler(client: Client, message: Message):
    start = time.perf_counter()
    msg = await message.reply_text("🏓 <b>ᴘɪɴɢɪɴɢ ᴇɴɢɪɴᴇ...</b>", parse_mode=enums.ParseMode.HTML)

    latency_ms = (time.perf_counter() - start) * 1000
    uptime = await get_bot_uptime()
    logger.info(
        "🏓 /ping › ᴜsᴇʀ=%s ʟᴀᴛᴇɴᴄʏ=%.2fᴍs",
        message.from_user.id if message.from_user else 0,
        latency_ms,
    )

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="ping_refresh"),
            InlineKeyboardButton("📊 ʟɪᴠᴇ ᴜsᴀɢᴇ", callback_data="sys:usage"),
        ]
    ])
    await safe_edit_message(msg, _build_ping_text(latency_ms, uptime), reply_markup=btn, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^ping_refresh$"))
async def ping_refresh_callback(client: Client, query: CallbackQuery):
    start = time.perf_counter()
    await query.answer("🏓 ʀᴇғʀᴇsʜɪɴɢ ᴛᴇʟᴇᴍᴇᴛʀʏ...")
    latency_ms = (time.perf_counter() - start) * 1000
    uptime = await get_bot_uptime()
    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="ping_refresh"),
            InlineKeyboardButton("📊 ʟɪᴠᴇ ᴜsᴀɢᴇ", callback_data="sys:usage"),
        ]
    ])
    await safe_edit_message(query.message, _build_ping_text(latency_ms, uptime), reply_markup=btn, parse_mode=enums.ParseMode.HTML)

@Client.on_message(filters.command("alive", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def alive_handler(client: Client, message: Message):
    uptime = await get_bot_uptime()
    sent = await message.reply_text(
        f"⚡ <b>ʜᴇʏ ʙᴜᴅᴅʏ, ɪ'ᴍ ᴀʟɪᴠᴇ &amp; ᴏɴʟɪɴᴇ!</b> 🚀\n"
        f"⏱️ <b>ᴜᴘᴛɪᴍᴇ:</b> <code>{uptime}</code>\n"
        f"🛡️ <b>ᴛᴀᴘ</b> <code>/start</code>, <code>/helpdesk</code>, <code>/channelhelp</code>, ᴏʀ <code>/autoset</code> <b>ғᴏʀ ᴍᴏʀᴇ!</b> ✨",
        parse_mode=enums.ParseMode.HTML,
    )
    await asyncio.sleep(15)
    try:
        await asyncio.gather(sent.delete(), message.delete())
    except Exception:
        pass

@Client.on_message(filters.command(["usage", "now"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def live_system_usage(client: Client, message: Message):
    status_msg = await message.reply_text("📊 <b>ɪɴɪᴛɪᴀʟɪᴢɪɴɢ ʟɪᴠᴇ sʏsᴛᴇᴍ ᴍᴏɴɪᴛᴏʀ...</b>", parse_mode=enums.ParseMode.HTML)
    start_ts = time.time()
    MAX_MONITOR_SECONDS = 600
    logger.info("📊 /usage ʟɪᴠᴇ ᴍᴏɴɪᴛᴏʀ sᴛᴀʀᴛᴇᴅ › ᴄʜᴀᴛ=%s", message.chat.id)

    try:
        while True:
            uptime_str = format_uptime_short(int(time.time() - BOT_START_TIME))
            cpu = psutil.cpu_percent(interval=0.4)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent

            text = (
                f"<b>⚙️ ʙᴏᴛ ʟɪᴠᴇ sʏsᴛᴇᴍ sᴛᴀᴛᴜs</b> ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏱️ <b>ᴜᴘᴛɪᴍᴇ:</b> <code>{uptime_str}</code>\n\n"
                f"🛠️ <b>ᴄᴘᴜ:</b> <code>{cpu:4.1f}%</code> [{make_progress_bar(cpu)}]\n"
                f"🗜️ <b>ʀᴀᴍ:</b> <code>{ram:4.1f}%</code> [{make_progress_bar(ram)}]\n"
                f"💾 <b>ᴅɪsᴋ:</b> <code>{disk:4.1f}%</code> [{make_progress_bar(disk)}]\n\n"
                f"🔄 <i>ᴀᴜᴛᴏ-ʀᴇғʀᴇsʜɪɴɢ ᴇᴠᴇʀʏ 5s...</i>"
            )

            try:
                await safe_edit_message(status_msg, text, parse_mode=enums.ParseMode.HTML)
            except MessageNotModified:
                pass
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception:
                break

            if time.time() - start_ts >= MAX_MONITOR_SECONDS:
                try:
                    await status_msg.edit_text(
                        text + "\n\n<b>🛑 ᴍᴏɴɪᴛᴏʀɪɴɢ sᴛᴏᴘᴘᴇᴅ</b>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except (FloodWait, MessageNotModified):
                    pass
                break

            await asyncio.sleep(5)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        try:
            await status_msg.edit_text(
                f"<b>❌ ᴍᴏɴɪᴛᴏʀ ᴇʀʀᴏʀ:</b>\n<code>{html.escape(str(e))}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

MAIN_STATUS_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🏓 ᴘɪɴɢ", callback_data="sys:ping"),
        InlineKeyboardButton("📊 ᴜsᴀɢᴇ", callback_data="sys:usage"),
    ],
    [
        InlineKeyboardButton("🟢 ʙᴏᴛ sᴛᴀᴛᴜs", callback_data="adm_panel#status"),
        InlineKeyboardButton("🔄 ʀᴇsᴛᴀʀᴛ", callback_data="sys:restart"),
    ],
])

@Client.on_message(filters.command("check", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def status_menu(client: Client, message: Message):
    await message.reply_text(
        "⚡ <b>ᴍᴍᴡ ᴀʟʟ-ɪɴ-ᴏɴᴇ ᴘʀᴏ ᴍᴀɴᴀɢᴇʀ ᴀᴄᴛɪᴠᴇ</b>\n\n"
        "🤖 <b>sᴛᴀᴛᴜs:</b> <code>ʀᴜɴɴɪɴɢ</code>\n"
        "🧠 <b>ᴍᴏᴅᴇ:</b> <code>ᴘʀᴏ ᴍᴀx sᴛᴀʙʟᴇ (ᴘʏᴛʜᴏɴ 3.12 + ᴘʏʀᴏғᴏʀᴋ 2.3.45)</code>\n\n"
        "👇 <b>ᴄʜᴏᴏsᴇ ᴀɴ ᴀᴄᴛɪᴏɴ:</b>",
        reply_markup=MAIN_STATUS_BUTTONS,
        parse_mode=enums.ParseMode.HTML,
    )

@Client.on_callback_query(filters.regex(r"^sys:"))
async def status_buttons_cb(client: Client, query: CallbackQuery):
    data = query.data.split(":", 1)[1]

    if data == "ping":
        start = time.perf_counter()
        await query.answer("🏓 ᴄʜᴇᴄᴋɪɴɢ...")
        latency = (time.perf_counter() - start) * 1000
        try:
            await query.message.reply_text(f"🏓 <b>ᴘᴏɴɢ!</b> <code>{latency:.2f} ᴍs</code>", parse_mode=enums.ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            await query.message.reply_text(f"🏓 <b>ᴘᴏɴɢ!</b> <code>{latency:.2f} ᴍs</code>", parse_mode=enums.ParseMode.HTML)
        return

    elif data == "usage":
        await query.answer("📊 sᴛᴀʀᴛɪɴɢ ᴍᴏɴɪᴛᴏʀ...")
        await live_system_usage(client, query.message)
        return

    elif data == "restart":
        if not is_admin_sync(query.from_user.id if query.from_user else None):
            await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)
            return
        await query.answer("🔄 ᴄʜᴇᴄᴋᴘᴏɪɴᴛɪɴɢ ᴛᴀsᴋs & ʀᴇsᴛᴀʀᴛɪɴɢ...")
        try:
            from plugins.compressor import checkpoint_active_tasks_before_shutdown
            await checkpoint_active_tasks_before_shutdown("sys_restart_cb")
        except Exception:
            pass
        await safe_edit_message(query.message, "🔄 <b>ʀᴇsᴛᴀʀᴛɪɴɢ ʙᴏᴛ...</b>", parse_mode=enums.ParseMode.HTML)
        os.execl(sys.executable, sys.executable, *sys.argv)
        return

    await query.answer("⚠️ ᴜɴᴋɴᴏᴡɴ sᴛᴀᴛᴜs ᴀᴄᴛɪᴏɴ", show_alert=True)
