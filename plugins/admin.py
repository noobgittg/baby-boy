import asyncio
import gc
import html
import io
import logging
import math
import os
import platform
import subprocess
import sys
import time

try:
    import psutil
except ImportError:
    from plugins.PING import psutil
from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from database.users_chats_db import db
from info import ADMINS, BOT_START_TIME, COMMAND_PREFIXES
from Script import script
from utils import (
    USER_CACHE,
    format_watermark_html,
    format_uptime_short,
    get_recent_logs_preview,
    is_admin_sync,
    make_progress_bar,
    safe_edit_message,
    temp,
)
from plugins.core_db import user_repo, chat_repo, file_repo, mongo
from plugins.core_services import RESTART_MARKER_FILE
from plugins.core_utils import (
    MEMORY_LOGS,
    cache,
    check_admin,
    format_watermark,
    humanbytes,
    is_admin,
    is_devil_mode_active,
    set_devil_mode,
    time_formatter,
    to_smallcaps,
)

logger = logging.getLogger("MMWAdminPanel")
START_TIME = BOT_START_TIME


async def get_bot_uptime() -> str:
    return format_uptime_short(int(time.time() - BOT_START_TIME))


def build_admin_dashboard_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 ʟɪᴠᴇ sᴛᴀᴛs", callback_data="adm_panel#stats"),
            InlineKeyboardButton("🟢 sᴇʀᴠᴇʀ sᴛᴀᴛᴜs", callback_data="adm_panel#status"),
        ],
        [
            InlineKeyboardButton("⚙️ ɢʟᴏʙᴀʟ ᴛᴏɢɢʟᴇs", callback_data="adm_panel#global_cfg"),
            InlineKeyboardButton("🚀 ᴇɴɢɪɴᴇ & ᴠᴀʀs ᴛᴜɴᴇʀ", callback_data="adm_panel#engine_hub"),
        ],
        [
            InlineKeyboardButton("👥 ᴜsᴇʀs ʙʀᴏᴡsᴇʀ", callback_data="admin_users_page_1"),
            InlineKeyboardButton("💬 ɢʀᴏᴜᴘs ɪɴsᴘᴇᴄᴛᴏʀ", callback_data="adm_panel#groups_page#1"),
        ],
        [
            InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟs ɪɴsᴘᴇᴄᴛᴏʀ", callback_data="adm_panel#channels_page#1"),
            InlineKeyboardButton("🛡️ ɢʟᴏʙᴀʟ sᴇᴄᴜʀɪᴛʏ ʜᴜʙ", callback_data="adm_panel#security_hub"),
        ],
        [
            InlineKeyboardButton("📡 ʙʀᴏᴀᴅᴄᴀsᴛ ʜᴜʙ", callback_data="adm_panel#broadcast_hub"),
            InlineKeyboardButton("🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ", callback_data="autofwd|home"),
        ],
        [
            InlineKeyboardButton("🛡️ ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
            InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton("🎨 ᴘᴏsᴛ & ғᴇᴀᴛᴜʀᴇ sᴛᴜᴅɪᴏ", callback_data="postedit|home"),
            InlineKeyboardButton("⚡ ᴀᴜᴛᴏ-sᴇᴛ ᴡɪᴢᴀʀᴅ", callback_data="autoset|home"),
        ],
        [
            InlineKeyboardButton("🔄 ᴀᴄᴛɪᴠᴇ ᴛᴀsᴋs ʜᴜʙ", callback_data="adm_panel#tasks_hub"),
            InlineKeyboardButton("🌐 ᴜᴘᴛɪᴍᴇ ᴀᴅᴍɪɴ", callback_data="upad_home"),
        ],
        [
            InlineKeyboardButton("🗄️ ᴅᴀᴛᴀʙᴀsᴇ ʜᴜʙ", callback_data="adm_panel#dbstatus"),
            InlineKeyboardButton("⚡ ᴄᴀᴄʜᴇ ᴄᴇɴᴛᴇʀ", callback_data="adm_panel#cache"),
        ],
        [
            InlineKeyboardButton("📋 ᴠɪᴇᴡ ʟᴏɢs", callback_data="adm_panel#logs"),
            InlineKeyboardButton("📑 ᴇxᴛʀᴀᴄᴛ ᴄᴍᴅs", callback_data="adm_panel#extract_cmds"),
        ],
        [
            InlineKeyboardButton("🔌 ᴘᴏᴡᴇʀ & ɢɪᴛ", callback_data="adm_panel#power"),
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#home"),
        ],
        [
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ],
    ])


def build_admin_help_text() -> str:
    devil_state = "🟢 ᴏɴ" if (temp.DEVIL_MODE or is_devil_mode_active()) else "⚪ ᴏғғ"
    maint_state = "🚨 ᴏɴ" if temp.MAINTENANCE_MODE else "🟢 ᴏғғ"
    return (
        "👑 <b>ᴍᴍᴡ ᴀʟʟ-ɪɴ-ᴏɴᴇ ᴘʀᴏ ᴍᴀsᴛᴇʀ ᴀᴅᴍɪɴ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>ᴀᴜᴛᴏ, sᴛʀᴇᴀᴍ &amp; ᴍᴇᴅɪᴀ ᴇɴɢɪɴᴇs:</b>\n"
        "• <code>/autoset</code> — 1-ᴄʟɪᴄᴋ ᴀᴜᴛᴏ-sᴇᴛ ᴡɪᴢᴀʀᴅ (ᴜsᴇʀ, ɢʀᴏᴜᴘ &amp; ᴄʜᴀɴɴᴇʟ)\n"
        "• <code>/autoforward</code> │ <code>/addforward</code> │ <code>/fbatch</code> — ʟɪᴠᴇ ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ &amp; ʙᴀᴛᴄʜ ᴄʟᴏɴᴇ\n"
        "• <code>/autostream</code> │ <code>/autocaption</code> │ <code>/autorename</code> │ <code>/autocompress</code>\n\n"
        "🛡️ <b>ɢʀᴏᴜᴘ &amp; ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ:</b>\n"
        "• <code>/helpdesk</code> — 70+ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ, 16 ʟᴏᴄᴋs, ᴡᴀʀɴs &amp; sᴇᴄᴜʀɪᴛʏ\n"
        "• <code>/channelhelp</code> │ <code>/cpanel</code> — 35+ ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇʀ, sᴄʜᴇᴅᴜʟᴇ &amp; ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ\n"
        "• <code>/connect &lt;channel_id&gt;</code> — ᴄᴏɴɴᴇᴄᴛ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴘᴍ/ɢʀᴏᴜᴘ\n\n"
        "📣 <b>ᴜsᴇʀs, ɢʀᴏᴜᴘs, ᴄʜᴀɴɴᴇʟs &amp; ʙʀᴏᴀᴅᴄᴀsᴛ:</b>\n"
        "• <code>/broadcast</code> │ <code>/pin_broadcast</code> — ᴜsᴇʀ ʙʀᴏᴀᴅᴄᴀsᴛ\n"
        "• <code>/grp_broadcast</code> │ <code>/chnl_broadcast</code> — ɢʀᴏᴜᴘ/ᴄʜᴀɴɴᴇʟ ʙʀᴏᴀᴅᴄᴀsᴛ\n"
        "• <code>/users</code> │ <code>/userinfo &lt;id&gt;</code> │ <code>/chats</code> │ <code>/channels</code> │ <code>/send &lt;id&gt;</code>\n"
        "• <code>/botban &lt;id&gt;</code> │ <code>/botunban &lt;id&gt;</code> │ <code>/disable</code> │ <code>/enable</code> │ <code>/leave</code>\n\n"
        "📊 <b>sʏsᴛᴇᴍ, ᴜᴘᴛɪᴍᴇ, ᴅʙ &amp; ᴅɪᴀɢɴᴏsᴛɪᴄs:</b>\n"
        "• <code>/stats</code> │ <code>/dbstatus</code> │ <code>/cache</code> │ <code>/dev</code> — ᴛᴇʟᴇᴍᴇᴛʀʏ &amp; ᴅʙ ʜᴇᴀʟᴛʜ\n"
        "• <code>/upad</code> — 24/7 ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ ᴀᴅᴍɪɴ ᴄᴏɴsᴏʟᴇ\n"
        "• <code>/logs</code> — ᴠɪᴇᴡ / ᴅᴏᴡɴʟᴏᴀᴅ ʀᴇᴅᴀᴄᴛᴇᴅ ʙᴏᴛ ʟᴏɢs\n"
        "• <code>/excommands</code> — ʀᴇᴀʟ-ᴛɪᴍᴇ ᴄᴏᴍᴍᴀɴᴅ ᴀᴜᴅɪᴛ &amp; ᴇxᴛʀᴀᴄᴛᴏʀ\n"
        f"• <b>ᴅᴇᴠɪʟᴍᴏᴅ:</b> <code>{devil_state}</code> │ <b>ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ:</b> <code>{maint_state}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>sᴇʟᴇᴄᴛ ᴀ ᴍᴏᴅᴜʟᴇ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀɴᴀɢᴇ ᴛʜᴇ ʙᴏᴛ:</b>"
    )


async def build_dbstatus_text() -> str:
    used_mb, free_mb, ping_ms = await db.get_db_storage_stats()
    users_cnt, chats_cnt, chnls_cnt, notes_cnt, filters_cnt, warns_cnt, modlogs_cnt, files_cnt = await asyncio.gather(
        db.total_users_count(),
        db.total_chat_count(),
        db.total_channels_count(),
        db.total_notes_count(),
        db.total_filters_count(),
        db.total_warnings_count(),
        db.total_mod_logs_count(),
        file_repo.get_total_files(),
    )
    fwd_cnt = len(await db.get_all_autoforward_rules())
    return (
        "🗄️ <b>ᴍᴏɴɢᴏᴅʙ &amp; ᴄᴏʟʟᴇᴄᴛɪᴏɴs ʜᴇᴀʟᴛʜ sᴛᴀᴛᴜs</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 <b>ᴘʀɪᴍᴀʀʏ ᴅᴀᴛᴀʙᴀsᴇ ʟᴀᴛᴇɴᴄʏ:</b> <code>{ping_ms:.1f} ᴍs</code>\n"
        f"   ├ 👥 <b>ᴜsᴇʀs:</b> <code>{users_cnt:,}</code> │ 💬 <b>ɢʀᴏᴜᴘs:</b> <code>{chats_cnt:,}</code>\n"
        f"   ├ 📢 <b>ᴄʜᴀɴɴᴇʟs:</b> <code>{chnls_cnt:,}</code> │ 📁 <b>sᴛʀᴇᴀᴍ ғɪʟᴇs:</b> <code>{files_cnt:,}</code>\n"
        f"   ├ 🔁 <b>ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ʀᴏᴜᴛᴇs:</b> <code>{fwd_cnt:,}</code>\n"
        f"   ├ 📝 <b>sᴀᴠᴇᴅ ɴᴏᴛᴇs:</b> <code>{notes_cnt:,}</code> │ ⚡ <b>ғɪʟᴛᴇʀs:</b> <code>{filters_cnt:,}</code>\n"
        f"   ├ ⚠️ <b>ᴀᴄᴛɪᴠᴇ ᴡᴀʀɴs:</b> <code>{warns_cnt:,}</code> │ 🛡️ <b>ᴍᴏᴅ ʟᴏɢs:</b> <code>{modlogs_cnt:,}</code>\n"
        f"   └ 💾 <b>ᴜsᴇᴅ:</b> <code>{used_mb:.2f} ᴍʙ</code> │ <b>ғʀᴇᴇ:</b> <code>{free_mb:.2f} ᴍʙ</code>"
    )


async def build_bot_status_text() -> str:
    uptime = await get_bot_uptime()
    cpu = psutil.cpu_percent(interval=None)
    ram_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage("/")
    u_stats = USER_CACHE.stats()
    c_stats = await cache.get_stats()
    active_tasks_cnt = len(temp.ACTIVE_TASKS)
    users_cnt, chats_cnt, chnls_cnt, files_cnt = await asyncio.gather(
        db.total_users_count(),
        db.total_chat_count(),
        db.total_channels_count(),
        file_repo.get_total_files(),
    )
    return (
        "🟢 <b>ᴍᴍᴡ ᴀʟʟ-ɪɴ-ᴏɴᴇ ᴘʀᴏ ʀᴜɴᴛɪᴍᴇ &amp; sᴇʀᴠᴇʀ sᴛᴀᴛᴜs</b> 🚀\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <b>ᴜᴘᴛɪᴍᴇ:</b> <code>{uptime}</code>\n"
        f"🐍 <b>ᴘʏᴛʜᴏɴ:</b> <code>{platform.python_version()}</code> │ <b>ᴘʏʀᴏғᴏʀᴋ:</b> <code>2.3.45</code>\n"
        f"🛠️ <b>ᴄᴘᴜ:</b> <code>{cpu:.1f}%</code> [{make_progress_bar(cpu)}]\n"
        f"🗜️ <b>ʀᴀᴍ:</b> <code>{ram_info.percent:.1f}%</code> [{make_progress_bar(ram_info.percent)}] (<code>{humanbytes(ram_info.used)}/{humanbytes(ram_info.total)}</code>)\n"
        f"💾 <b>ᴅɪsᴋ:</b> <code>{disk_info.percent:.1f}%</code> [{make_progress_bar(disk_info.percent)}] (<code>{humanbytes(disk_info.used)}/{humanbytes(disk_info.total)}</code>)\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>ᴜsᴇʀs:</b> <code>{users_cnt:,}</code> │ 💬 <b>ɢʀᴏᴜᴘs:</b> <code>{chats_cnt:,}</code> │ 📢 <b>ᴄʜᴀɴɴᴇʟs:</b> <code>{chnls_cnt:,}</code>\n"
        f"📁 <b>sᴛʀᴇᴀᴍ ғɪʟᴇs:</b> <code>{files_cnt:,}</code> │ 🔄 <b>ᴛᴀsᴋs:</b> <code>{active_tasks_cnt}</code>\n"
        f"⚡ <b>ʟ1/ʟ2 ᴄᴀᴄʜᴇ:</b> <code>{u_stats['entries'] + c_stats['cached_keys']}</code> ᴋᴇʏs (ʜɪᴛ: <code>{c_stats['hit_ratio_percent']}%</code>)\n"
        f"😈 <b>ᴅᴇᴠɪʟ ᴍᴏᴅᴇ:</b> <code>{'ᴇɴᴀʙʟᴇᴅ 🔥' if (temp.DEVIL_MODE or is_devil_mode_active()) else 'sᴛᴀɴᴅᴀʀᴅ ⚡'}</code>\n"
        f"🛠️ <b>ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ:</b> <code>{'ᴀᴄᴛɪᴠᴇ 🚨' if temp.MAINTENANCE_MODE else 'ᴏғғ 🟢'}</code>"
    )


async def build_stats_formatted_text() -> str:
    (
        total_users,
        totl_chats,
        totl_chnls,
        notes_cnt,
        filters_cnt,
        warns_cnt,
        modlogs_cnt,
        (used_mb, free_mb, _),
    ) = await asyncio.gather(
        db.total_users_count(),
        db.total_chat_count(),
        db.total_channels_count(),
        db.total_notes_count(),
        db.total_filters_count(),
        db.total_warnings_count(),
        db.total_mod_logs_count(),
        db.get_db_storage_stats(),
    )
    return script.STATUS_TXT.format(
        total_users,
        totl_chats,
        totl_chnls,
        notes_cnt,
        filters_cnt,
        warns_cnt,
        modlogs_cnt,
        len(temp.ACTIVE_TASKS),
        used_mb,
        free_mb,
    )


async def build_global_config_view() -> tuple[str, InlineKeyboardMarkup]:
    cfg = await db.get_bot_config()
    temp.BOT_CONFIG = cfg
    temp.MAINTENANCE_MODE = bool(cfg.get("maintenance_mode", False))

    def _b(val: bool) -> str:
        return "🟢 ᴏɴ" if val else "🔴 ᴏғғ"

    devil_on = bool(temp.DEVIL_MODE or is_devil_mode_active())
    text = (
        "⚙️ <b>ɢʟᴏʙᴀʟ ʙᴏᴛ ᴄᴏɴғɪɢᴜʀᴀᴛɪᴏɴ &amp; ʟɪᴠᴇ ғᴇᴀᴛᴜʀᴇ ᴛᴏɢɢʟᴇs</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🛠️ <b>ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ:</b> <code>{_b(cfg.get('maintenance_mode', False))}</code>\n"
        f"• 🎬 <b>ɢʟᴏʙᴀʟ ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ:</b> <code>{_b(cfg.get('auto_stream_global', True))}</code>\n"
        f"• 📝 <b>ɢʟᴏʙᴀʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ:</b> <code>{_b(cfg.get('auto_caption_global', True))}</code>\n"
        f"• 🔁 <b>ɢʟᴏʙᴀʟ ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ:</b> <code>{_b(cfg.get('auto_forward_global', True))}</code>\n"
        f"• 🧹 <b>ᴀᴜᴛᴏ-ᴄʟᴇᴀɴ sᴇʀᴠɪᴄᴇ ᴍsɢs:</b> <code>{_b(cfg.get('auto_clean_service', False))}</code>\n"
        f"• 🔒 <b>ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ:</b> <code>{_b(cfg.get('protect_content', False))}</code>\n"
        f"• 👋 <b>ᴡᴇʟᴄᴏᴍᴇ ɴᴇᴡ ᴍᴇᴍʙᴇʀs:</b> <code>{_b(cfg.get('welcome_new_users', True))}</code>\n"
        f"• 👤 <b>ʟᴏɢ ɴᴇᴡ ᴜsᴇʀs:</b> <code>{_b(cfg.get('log_new_users', True))}</code>\n"
        f"• 💬 <b>ʟᴏɢ ɴᴇᴡ ɢʀᴏᴜᴘs:</b> <code>{_b(cfg.get('log_new_groups', True))}</code>\n"
        f"• ⚡ <b>ғᴀsᴛ ʟ1+ʟ2 ᴄᴀᴄʜᴇ:</b> <code>{_b(cfg.get('fast_cache', True))}</code>\n"
        f"• 🔥 <b>ᴅᴇᴠɪʟᴍᴏᴅ ᴍᴀx ᴍᴏᴅᴇ:</b> <code>{_b(devil_on)}</code>\n\n"
        "👇 <i>ᴛᴀᴘ ᴀɴʏ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴛᴏɢɢʟᴇ ʟɪᴠᴇ ɪɴ ᴍᴏɴɢᴏᴅʙ:</i>"
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🛠️ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ: {_b(cfg.get('maintenance_mode', False))}",
                callback_data="adm_panel#toggle_cfg#maintenance_mode",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🎬 ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ: {_b(cfg.get('auto_stream_global', True))}",
                callback_data="adm_panel#toggle_cfg#auto_stream_global",
            ),
            InlineKeyboardButton(
                f"📝 ᴀᴜᴛᴏ-ᴄᴀᴘ: {_b(cfg.get('auto_caption_global', True))}",
                callback_data="adm_panel#toggle_cfg#auto_caption_global",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ: {_b(cfg.get('auto_forward_global', True))}",
                callback_data="adm_panel#toggle_cfg#auto_forward_global",
            ),
            InlineKeyboardButton(
                f"🧹 ᴄʟᴇᴀɴ sᴠᴄ: {_b(cfg.get('auto_clean_service', False))}",
                callback_data="adm_panel#toggle_cfg#auto_clean_service",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🔒 ᴘʀᴏᴛᴇᴄᴛ: {_b(cfg.get('protect_content', False))}",
                callback_data="adm_panel#toggle_cfg#protect_content",
            ),
            InlineKeyboardButton(
                f"👋 ᴡᴇʟᴄᴏᴍᴇ: {_b(cfg.get('welcome_new_users', True))}",
                callback_data="adm_panel#toggle_cfg#welcome_new_users",
            ),
        ],
        [
            InlineKeyboardButton(
                f"👤 ʟᴏɢ ᴜsᴇʀs: {_b(cfg.get('log_new_users', True))}",
                callback_data="adm_panel#toggle_cfg#log_new_users",
            ),
            InlineKeyboardButton(
                f"💬 ʟᴏɢ ɢʀᴏᴜᴘs: {_b(cfg.get('log_new_groups', True))}",
                callback_data="adm_panel#toggle_cfg#log_new_groups",
            ),
        ],
        [
            InlineKeyboardButton(
                f"⚡ ғᴀsᴛ ᴄᴀᴄʜᴇ: {_b(cfg.get('fast_cache', True))}",
                callback_data="adm_panel#toggle_cfg#fast_cache",
            ),
            InlineKeyboardButton(
                f"🔥 ᴅᴇᴠɪʟᴍᴏᴅ: {_b(devil_on)}",
                callback_data="adm_panel#toggle_devil",
            ),
        ],
        [
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
        ],
    ])
    return text, buttons


@Client.on_message(
    filters.command(["adminhelp", "admin", "adminpanel", "panel", "stats"], prefixes=COMMAND_PREFIXES)
    & filters.incoming
)
async def admin_help_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not is_admin_sync(uid):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)

    cmd = message.command[0].lower()
    logger.info("👑 /%s ɪɴᴠᴏᴋᴇᴅ › ᴀᴅᴍɪɴ=%s ᴄʜᴀᴛ=%s", cmd, uid, message.chat.id)

    if cmd == "stats":
        text = await build_stats_formatted_text()
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#stats"),
                InlineKeyboardButton("🗄️ ᴅʙ sᴛᴀᴛᴜs", callback_data="adm_panel#dbstatus"),
            ],
            [
                InlineKeyboardButton("🛡️ ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
                InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
            ],
            [
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            ],
        ])
        return await message.reply_text(
            text,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )

    await message.reply_text(
        build_admin_help_text(),
        reply_markup=build_admin_dashboard_buttons(),
        parse_mode=enums.ParseMode.HTML,
        quote=True,
    )


@Client.on_message(filters.command(["dbstatus", "database", "dbstats"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def dbstatus_cmd(client: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    wait_msg = await message.reply_text("🔎 <b>ᴄʜᴇᴄᴋɪɴɢ ᴍᴏɴɢᴏᴅʙ &amp; ᴄᴏʟʟᴇᴄᴛɪᴏɴs...</b> ⏳", parse_mode=enums.ParseMode.HTML)
    text = await build_dbstatus_text()
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚙️ ᴇɴsᴜʀᴇ ɪɴᴅᴇxᴇs", callback_data="adm_panel#reindex"),
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#dbstatus"),
        ],
        [
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
        ],
    ])
    await safe_edit_message(wait_msg, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("cache", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def admin_cache_command(client: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    await render_cache_card(message, is_edit=False)


async def render_cache_card(target_msg: Message, is_edit: bool = False):
    stats = await cache.get_stats()
    u_stats = USER_CACHE.stats()
    text = (
        f"✦ <b>{to_smallcaps('in-memory o(1) l1+l2 cache monitor')}</b> ✦\n\n"
        f"• ⚡ <b>{to_smallcaps('cache engine')}:</b> <code>FastMemoryCache + UserDynamicCache</code>\n"
        f"• 📦 <b>{to_smallcaps('active keys')}:</b> <code>{stats['cached_keys'] + u_stats['entries']}</code> / <code>{stats['max_entries']}</code>\n"
        f"• 🎯 <b>{to_smallcaps('cache hits')}:</b> <code>{stats['hits'] + u_stats['hits']}</code>\n"
        f"• ❌ <b>{to_smallcaps('cache misses')}:</b> <code>{stats['misses'] + u_stats['misses']}</code>\n"
        f"• 🚀 <b>{to_smallcaps('hit ratio')}:</b> <code>{stats['hit_ratio_percent']}%</code>\n"
        f"• 🧹 <b>{to_smallcaps('auto-cleared responses')}:</b> <code>{stats.get('auto_clears', 0)} (scopes: {stats.get('response_scopes_cleared', 0)})</code>\n"
        f"• ⏱️ <b>{to_smallcaps('default ttl')}:</b> <code>{Config.CACHE_TTL}s</code>"
        f"{format_watermark_html()}"
    )
    buttons = [
        [
            InlineKeyboardButton(f"🧹 {to_smallcaps('clear all cache')}", callback_data="adm_panel#clearcache"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data="admin_cache_info"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ],
    ]
    if is_edit:
        await safe_edit_message(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command(["user", "inspectuser", "udb"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def admin_userinfo_command(client: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2 or not message.command[1].lstrip("-").isdigit():
        return await message.reply_text(
            f"📝 <b>{to_smallcaps('usage')}:</b> <code>/userinfo &lt;user_id&gt;</code>\n"
            f"• {to_smallcaps('example')}: <code>/userinfo {Config.OWNER_ID}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    target_id = int(message.command[1])
    await render_userinfo_card(message, target_id, is_edit=False)


async def render_users_page(target_msg: Message, page: int = 1, is_edit: bool = False):
    total_users = await user_repo.get_total_users()
    limit = 10
    total_pages = max(1, math.ceil(total_users / limit))
    page = max(1, min(page, total_pages))
    skip = (page - 1) * limit

    users = await user_repo.get_users_paginated(skip=skip, limit=limit)

    text = f"✦ <b>{to_smallcaps('registered user directory')}</b> ✦\n\n"
    text += f"• <b>{to_smallcaps('page')}:</b> <code>{page}/{total_pages}</code> │ <b>{to_smallcaps('total')}:</b> <code>{total_users}</code>\n\n"

    if not users:
        text += f"<i>{to_smallcaps('no users found.')}</i>\n"
    else:
        for idx, u in enumerate(users, start=skip + 1):
            uid = u.get("user_id", 0)
            name = html.escape(str(u.get("first_name", "User")))
            uname = f"@{html.escape(str(u['username']))}" if u.get("username") else "ɴᴏ ᴜsᴇʀɴᴀᴍᴇ"
            status = "🚫 ʙᴀɴɴᴇᴅ" if u.get("is_banned") else "✅ ᴀᴄᴛɪᴠᴇ"
            text += f"<code>{idx}.</code> <b>{name}</b> (<code>{uid}</code>) — {uname} [{status}]\n"

    text += f"\n💡 {to_smallcaps('to inspect a user, send')} <code>/userinfo &lt;user_id&gt;</code>"
    text += format_watermark_html()

    nav_btns = []
    if page > 1:
        nav_btns.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"admin_users_page_{page - 1}"))
    nav_btns.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data=f"admin_users_page_{page}"))
    if page < total_pages:
        nav_btns.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"admin_users_page_{page + 1}"))

    buttons = [
        nav_btns,
        [
            InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ᴜsᴇʀs", callback_data="adm_panel#export_users"),
            InlineKeyboardButton("💬 ᴜsᴇʀs ʜᴜʙ", callback_data="adm_panel#users_hub"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ],
    ]

    if is_edit:
        await safe_edit_message(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


async def render_userinfo_card(target_msg: Message, target_id: int, is_edit: bool = False):
    u = await user_repo.get_user(target_id)
    if not u:
        msg = f"❌ <b>{to_smallcaps('user not found in database!')}</b> (<code>{target_id}</code>)"
        if is_edit:
            return await safe_edit_message(target_msg, msg, parse_mode=enums.ParseMode.HTML)
        return await target_msg.reply_text(msg, parse_mode=enums.ParseMode.HTML)

    is_banned = u.get("is_banned", False)
    ban_label = "🚫 ʙᴀɴɴᴇᴅ" if is_banned else "✅ ᴀᴄᴛɪᴠᴇ"
    has_thumb = "✅ ᴄᴜsᴛᴏᴍ" if u.get("thumb_id") else ("🌐 ᴛʜᴀᴍ_ᴜʀʟ" if u.get("tham_url") else "❌ ɴᴏɴᴇ")
    custom_cap = "✅ sᴇᴛ" if u.get("custom_caption") else "❌ ᴅᴇғᴀᴜʟᴛ"
    caps_count = len(u.get("captions_list", []))
    auto_del = time_formatter(seconds=u.get("auto_delete_time", 0)) if u.get("auto_delete_time", 0) > 0 else "Disabled"
    created = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(u.get("created_at", time.time())))
    uname_disp = f"@{html.escape(str(u['username']))}" if u.get("username") else to_smallcaps("none")

    text = (
        f"✦ <b>{to_smallcaps('user inspection card')}</b> ✦\n\n"
        f"• 🆔 <b>{to_smallcaps('user id')}:</b> <code>{target_id}</code>\n"
        f"• 👤 <b>{to_smallcaps('first name')}:</b> {html.escape(str(u.get('first_name', 'N/A')))}\n"
        f"• 🏷️ <b>{to_smallcaps('username')}:</b> {uname_disp}\n"
        f"• 🛡️ <b>{to_smallcaps('account status')}:</b> <code>{ban_label}</code>\n"
        f"• 🖼️ <b>{to_smallcaps('thumbnail')}:</b> <code>{has_thumb}</code>\n"
        f"• 📝 <b>{to_smallcaps('custom caption')}:</b> <code>{custom_cap}</code> (<code>{caps_count}</code> in list)\n"
        f"• 🔤 <b>{to_smallcaps('prefix / suffix')}:</b> <code>{html.escape(str(u.get('prefix', '') or 'None'))}</code> / <code>{html.escape(str(u.get('suffix', '') or 'None'))}</code>\n"
        f"• ⏱️ <b>{to_smallcaps('auto-delete')}:</b> <code>{auto_del}</code>\n"
        f"• 📅 <b>{to_smallcaps('joined')}:</b> <code>{created} UTC</code>"
        f"{format_watermark_html()}"
    )

    ban_btn_text = f"✅ {to_smallcaps('unban user')}" if is_banned else f"🚫 {to_smallcaps('ban user')}"
    buttons = [
        [
            InlineKeyboardButton(ban_btn_text, callback_data=f"admin_toggle_ban_{target_id}"),
            InlineKeyboardButton(f"👥 {to_smallcaps('user list')}", callback_data="admin_users_page_1"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ],
    ]

    if is_edit:
        await safe_edit_message(target_msg, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command(["dev", "developer", "devilmod", "devmode"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def dev_mode_cmd(client: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ:</b> ᴅᴇᴠᴇʟᴏᴘᴇʀ / ᴀᴅᴍɪɴ ᴏɴʟʏ 🔒", parse_mode=enums.ParseMode.HTML)

    cmd_arg = message.command[1].lower().strip() if len(message.command) > 1 else ""
    if cmd_arg in ("on", "enable", "1", "true"):
        temp.DEVIL_MODE = True
        set_devil_mode(True)
    elif cmd_arg in ("off", "disable", "0", "false"):
        temp.DEVIL_MODE = False
        set_devil_mode(False)

    await render_dev_console(message, is_edit=False)


async def render_dev_console(target_msg: Message, is_edit: bool = False):
    users_count = await db.total_users_count()
    chats_count = await db.total_chat_count()
    files_count = await file_repo.get_total_files()
    cache_stats = await cache.get_stats()
    uptime_sec = round(float(time.time()) - START_TIME)
    active_tasks = len(asyncio.all_tasks())
    gc_objects = len(gc.get_objects())
    devil_on = bool(temp.DEVIL_MODE or is_devil_mode_active())
    devil_status = "🔥 ᴀᴄᴛɪᴠᴇ (ᴏɴ)" if devil_on else "⚪ ɪɴᴀᴄᴛɪᴠᴇ (ᴏғғ)"

    dev_text = (
        f"🛠️ <b>{to_smallcaps('mmw pro engine dev console & diagnostics')}</b> ⚡\n\n"
        f"• 😈 <b>{to_smallcaps('devil mode')}:</b> <code>{devil_status}</code>\n"
        f"• 🐍 <b>{to_smallcaps('python version')}:</b> <code>{sys.version.split()[0]}</code>\n"
        f"• ⚡ <b>{to_smallcaps('stream engine')}:</b> <code>adaptive ByteStreamer + 4 Players</code>\n"
        f"• 🍃 <b>{to_smallcaps('database')}:</b> <code>Single Unified DB + O(1) Cache</code>\n"
        f"  - {to_smallcaps('active cache keys')} : <code>{cache_stats['cached_keys']}</code> / <code>{cache_stats['max_entries']}</code>\n"
        f"  - {to_smallcaps('cache hit ratio')} : <code>{cache_stats['hit_ratio_percent']}%</code>\n"
        f"• 🔄 <b>{to_smallcaps('active asyncio tasks')}:</b> <code>{active_tasks}</code>\n"
        f"• 🧹 <b>{to_smallcaps('tracked gc objects')}:</b> <code>{gc_objects}</code>\n"
        f"• ⏱️ <b>{to_smallcaps('system uptime')}:</b> <code>{time_formatter(seconds=uptime_sec)}</code>\n"
        f"• 👥 <b>{to_smallcaps('users / chats / files')}:</b> <code>{users_count}</code> / <code>{chats_count}</code> / <code>{files_count}</code>"
        f"{format_watermark_html()}"
    )

    toggle_btn_text = f"⚪ {to_smallcaps('disable devilmod')}" if devil_on else f"🔥 {to_smallcaps('enable devilmod')}"
    buttons = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data="dev_toggle"),
            InlineKeyboardButton(f"🧹 {to_smallcaps('force gc')}", callback_data="dev_gc"),
        ],
        [
            InlineKeyboardButton(f"📊 {to_smallcaps('admin stats')}", callback_data="adm_panel#stats"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('global toggles')}", callback_data="adm_panel#global_cfg"),
        ],
        [
            InlineKeyboardButton(f"🧹 {to_smallcaps('clear cache')}", callback_data="adm_panel#clearcache"),
            InlineKeyboardButton(f"👑 {to_smallcaps('admin panel')}", callback_data="adm_panel#home"),
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]

    if is_edit:
        await safe_edit_message(target_msg, dev_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(dev_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(
    filters.command(["recheck", "resystem", "restart"], prefixes=COMMAND_PREFIXES) & filters.user(ADMINS)
)
async def soft_restart(client: Client, message: Message):
    logger.warning("♻️ sᴏғᴛ ʀᴇsᴛᴀʀᴛ ᴛʀɪɢɢᴇʀᴇᴅ ʙʏ ᴀᴅᴍɪɴ › %s", message.from_user.id if message.from_user else 0)
    msg = await message.reply_text("🔄 <b>ᴄʜᴇᴄᴋᴘᴏɪɴᴛɪɴɢ ᴛᴀsᴋs &amp; ʀᴇsᴛᴀʀᴛɪɴɢ ʙᴏᴛ...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        from plugins.compressor import checkpoint_active_tasks_before_shutdown
        await checkpoint_active_tasks_before_shutdown("admin_soft_restart")
    except Exception:
        pass
    try:
        with open(RESTART_MARKER_FILE, "w") as f:
            f.write(f"{float(time.time())}")
    except Exception:
        pass
    await asyncio.sleep(1.0)
    await safe_edit_message(msg, "✅ <b>ʀᴇsᴛᴀʀᴛ ɪɴɪᴛɪᴀᴛᴇᴅ!</b> ⚡", parse_mode=enums.ParseMode.HTML)
    os.execl(sys.executable, sys.executable, *sys.argv)


@Client.on_message(
    filters.command(["update", "reupdate"], prefixes=COMMAND_PREFIXES) & filters.user(ADMINS)
)
async def git_update_and_restart(client: Client, message: Message):
    logger.info("⬇️ /update ᴛʀɪɢɢᴇʀᴇᴅ ʙʏ ᴀᴅᴍɪɴ › %s", message.from_user.id if message.from_user else 0)
    msg = await message.reply_text("⬇️ <b>ᴘᴜʟʟɪɴɢ ʟᴀᴛᴇsᴛ ᴜᴘᴅᴀᴛᴇ...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=25)
        stdout_s = stdout_b.decode("utf-8", errors="ignore").strip()
        stderr_s = stderr_b.decode("utf-8", errors="ignore").strip()
        if proc.returncode != 0:
            await msg.edit_text(
                f"❌ <b>ɢɪᴛ ᴘᴜʟʟ ғᴀɪʟᴇᴅ</b>\n<code>{html.escape(stderr_s)}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        output = stdout_s or stderr_s
        if "Already up to date" in output:
            await msg.edit_text("✅ <b>ʙᴏᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴜᴘ ᴛᴏ ᴅᴀᴛᴇ.</b>", parse_mode=enums.ParseMode.HTML)
            return
        try:
            from plugins.compressor import checkpoint_active_tasks_before_shutdown
            await checkpoint_active_tasks_before_shutdown("admin_git_update")
        except Exception:
            pass
        await safe_edit_message(msg, "♻️ <b>ᴜᴘᴅᴀᴛᴇ ᴅᴏɴᴇ • ʀᴇsᴛᴀʀᴛɪɴɢ...</b>", parse_mode=enums.ParseMode.HTML)
        await asyncio.sleep(1.5)
        os.execl(sys.executable, sys.executable, *sys.argv)
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        await msg.edit_text("⌛ <b>ᴜᴘᴅᴀᴛᴇ ᴛɪᴍᴇᴅ ᴏᴜᴛ.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ <b>ᴜᴘᴅᴀᴛᴇ ᴇʀʀᴏʀ:</b>\n<code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("reboot", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def full_reboot_command(client: Client, message: Message):
    logger.warning("🔄 /reboot ᴛʀɪɢɢᴇʀᴇᴅ ʙʏ ᴀᴅᴍɪɴ › %s", message.from_user.id if message.from_user else 0)
    msg = await message.reply_text("<b>🔄 ʀᴇʙᴏᴏᴛɪɴɢ ʙᴏᴛ...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        from plugins.compressor import checkpoint_active_tasks_before_shutdown
        await checkpoint_active_tasks_before_shutdown("admin_reboot")
    except Exception:
        pass
    await asyncio.sleep(1.5)
    await safe_edit_message(msg, "<b>✅ ʀᴇʙᴏᴏᴛ sᴇǫᴜᴇɴᴄᴇ sᴛᴀʀᴛᴇᴅ</b>", parse_mode=enums.ParseMode.HTML)
    os.execl(sys.executable, sys.executable, *sys.argv)


@Client.on_message(filters.command(["gbanlist", "setvar", "getvar", "delvar", "tunestream", "tasks", "killtask"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def extra_advanced_admin_cmds(client: Client, message: Message):
    if not message.from_user or not is_admin_sync(message.from_user.id):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)

    cmd = message.command[0].lower()
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""

    if cmd == "gbanlist":
        b_u, b_c = await db.get_banned()
        temp.BANNED_USERS, temp.BANNED_CHATS = b_u, b_c
        u_str = ", ".join(f"<code>{x}</code>" for x in b_u[:50]) or "<code>ɴᴏɴᴇ</code>"
        c_str = ", ".join(f"<code>{x}</code>" for x in b_c[:50]) or "<code>ɴᴏɴᴇ</code>"
        return await message.reply_text(
            f"🛡️ <b>{to_smallcaps('global federation ban & disabled chat list')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🚫 <b>{to_smallcaps('banned users')} ({len(b_u)}):</b>\n{u_str}\n\n"
            f"• 💬 <b>{to_smallcaps('disabled chats')} ({len(b_c)}):</b>\n{c_str}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛡️ sᴇᴄᴜʀɪᴛʏ ʜᴜʙ", callback_data="adm_panel#security_hub"),
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            ]]),
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("setvar", "getvar", "delvar"):
        runtime_vars = temp.BOT_CONFIG.setdefault("runtime_vars", {})
        if cmd == "getvar":
            if not arg:
                items = [f"• <code>{html.escape(str(k))}</code> = <code>{html.escape(str(v))}</code>" for k, v in runtime_vars.items()]
                return await message.reply_text(
                    f"🔧 <b>{to_smallcaps('runtime variables')} ({len(runtime_vars)}):</b>\n"
                    + ("\n".join(items) if items else "<i>(ɴᴏ ᴄᴜsᴛᴏᴍ ʀᴜɴᴛɪᴍᴇ ᴏᴠᴇʀʀɪᴅᴇs)</i>")
                    + "\n\n💡 <b>ᴜsᴀɢᴇ:</b> <code>/setvar KEY VALUE</code> │ <code>/getvar KEY</code> │ <code>/delvar KEY</code>",
                    parse_mode=enums.ParseMode.HTML,
                )
            val = runtime_vars.get(arg, getattr(Config, arg, os.environ.get(arg, "ɴᴏᴛ sᴇᴛ")))
            return await message.reply_text(
                f"🔍 <b>{to_smallcaps('variable')} <code>{html.escape(arg)}</code>:</b> <code>{html.escape(str(val))}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        if cmd == "delvar":
            if not arg:
                return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/delvar KEY</code>", parse_mode=enums.ParseMode.HTML)
            runtime_vars.pop(arg, None)
            await db.update_bot_config({"runtime_vars": runtime_vars})
            return await message.reply_text(f"🗑️ <b>{to_smallcaps('deleted runtime variable')}:</b> <code>{html.escape(arg)}</code>", parse_mode=enums.ParseMode.HTML)

        parts = arg.split(None, 1)
        if len(parts) < 2:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/setvar KEY VALUE</code>", parse_mode=enums.ParseMode.HTML)
        k, v = parts[0].strip(), parts[1].strip()
        runtime_vars[k] = v
        await db.update_bot_config({"runtime_vars": runtime_vars})
        if hasattr(Config, k):
            orig = getattr(Config, k)
            if isinstance(orig, int) and v.lstrip("-").isdigit():
                setattr(Config, k, int(v))
            elif isinstance(orig, str):
                setattr(Config, k, v)
        return await message.reply_text(
            f"✅ <b>{to_smallcaps('runtime variable updated')}:</b> <code>{html.escape(k)}</code> = <code>{html.escape(v)}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "tunestream":
        from plugins.core_utils import task_manager
        return await message.reply_text(
            f"🚀 <b>{to_smallcaps('adaptive stream & media engine tuner')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• ⚡ <b>{to_smallcaps('chunk size')}:</b> <code>1 MiB (1,048,576 B)</code>\n"
            f"• 🔄 <b>{to_smallcaps('read-ahead prefetch')}:</b> <code>4 Chunks (4 MiB Queue)</code>\n"
            f"• 📡 <b>{to_smallcaps('max transmissions')}:</b> <code>{getattr(Config, 'MAX_CONCURRENT_TRANSMISSIONS', 16)}</code>\n"
            f"• ⏱️ <b>{to_smallcaps('cache ttl')}:</b> <code>{Config.CACHE_TTL}s</code>\n"
            f"• 🎬 <b>{to_smallcaps('external players')}:</b> <code>PLAYit • MX Player • VLC • S Player</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 ᴏᴘᴇɴ ᴇɴɢɪɴᴇ ᴛᴜɴᴇʀ", callback_data="adm_panel#engine_hub"),
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            ]]),
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "tasks":
        all_t = list(asyncio.all_tasks())
        return await message.reply_text(
            f"🔄 <b>{to_smallcaps('active runtime tasks & workers')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• ⚙️ <b>{to_smallcaps('asyncio tasks')}:</b> <code>{len(all_t)}</code>\n"
            f"• 🎬 <b>{to_smallcaps('tracked bot tasks')}:</b> <code>{len(temp.ACTIVE_TASKS)}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 ᴏᴘᴇɴ ᴛᴀsᴋs ʜᴜʙ", callback_data="adm_panel#tasks_hub"),
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            ]]),
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "killtask":
        from plugins.core_utils import task_manager
        if arg and arg.isdigit():
            cnt = await task_manager.cancel_user_tasks(int(arg))
            return await message.reply_text(f"🛑 <b>{to_smallcaps(f'cancelled {cnt} tasks for user')} <code>{arg}</code>!</b>", parse_mode=enums.ParseMode.HTML)
        cancelled = 0
        for tid in list(task_manager._tasks.keys()):
            if await task_manager.cancel_task(tid):
                cancelled += 1
        return await message.reply_text(f"🛑 <b>{to_smallcaps(f'stopped {cancelled} active media tasks!')}</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^adm_panel#"))
async def admin_panel_callback(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    raw_action = query.data.split("#", 1)[1]
    parts = raw_action.split("#")
    action = parts[0]
    logger.info("👑 ᴀᴅᴍ_ᴘᴀɴᴇʟ_ᴄʙ › ᴀᴅᴍɪɴ=%s ᴀᴄᴛɪᴏɴ=%s", query.from_user.id, raw_action)

    if action == "home":
        await query.answer("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ")
        await safe_edit_message(
            query.message,
            build_admin_help_text(),
            reply_markup=build_admin_dashboard_buttons(),
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "status":
        await query.answer("🟢 ʟᴏᴀᴅɪɴɢ sᴛᴀᴛᴜs...")
        text = await build_bot_status_text()
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#status"),
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
            ]
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "dbstatus":
        await query.answer("🗄️ ᴄʜᴇᴄᴋɪɴɢ ᴅʙ...")
        text = await build_dbstatus_text()
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚙️ ᴇɴsᴜʀᴇ ɪɴᴅᴇxᴇs", callback_data="adm_panel#reindex"),
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#dbstatus"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "stats":
        await query.answer("📊 ʟᴏᴀᴅɪɴɢ sᴛᴀᴛs...")
        formatted = await build_stats_formatted_text()
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#stats"),
                InlineKeyboardButton("🗄️ ᴅʙ sᴛᴀᴛᴜs", callback_data="adm_panel#dbstatus"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
            ],
        ])
        await safe_edit_message(query.message, formatted, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "global_cfg":
        await query.answer("⚙️ ɢʟᴏʙᴀʟ ᴄᴏɴғɪɢ")
        text, buttons = await build_global_config_view()
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "toggle_cfg" and len(parts) > 1:
        key = parts[1]
        cfg = await db.get_bot_config()
        new_val = not bool(cfg.get(key, False))
        await db.update_bot_config({key: new_val})
        temp.BOT_CONFIG[key] = new_val
        if key == "maintenance_mode":
            temp.MAINTENANCE_MODE = new_val
        await query.answer(f"✅ {key}: {'ᴏɴ' if new_val else 'ᴏғғ'}")
        text, buttons = await build_global_config_view()
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "users_hub":
        await query.answer("👥 ᴜsᴇʀs & ᴄʜᴀᴛs ʜᴜʙ")
        users_cnt, chats_cnt, chnls_cnt = await asyncio.gather(
            db.total_users_count(),
            db.total_chat_count(),
            db.total_channels_count(),
        )
        b_u, b_c = await db.get_banned()
        temp.BANNED_USERS, temp.BANNED_CHATS = b_u, b_c
        text = (
            "👥 <b>ᴜsᴇʀs, ɢʀᴏᴜᴘs &amp; ᴄʜᴀɴɴᴇʟs ᴍᴀɴᴀɢᴇᴍᴇɴᴛ ʜᴜʙ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 👤 <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> <code>{users_cnt:,}</code> (🚫 ʙᴀɴɴᴇᴅ: <code>{len(b_u)}</code>)\n"
            f"• 💬 <b>ᴛᴏᴛᴀʟ ɢʀᴏᴜᴘs:</b> <code>{chats_cnt:,}</code> (🚫 ᴅɪsᴀʙʟᴇᴅ: <code>{len(b_c)}</code>)\n"
            f"• 📢 <b>ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs:</b> <code>{chnls_cnt:,}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 <b>ǫᴜɪᴄᴋ ᴄᴏᴍᴍᴀɴᴅs:</b>\n"
            "• <code>/botban &lt;id&gt; [reason]</code> │ <code>/botunban &lt;id&gt;</code>\n"
            "• <code>/userinfo &lt;id&gt;</code> — ɪɴsᴘᴇᴄᴛ &amp; ᴛᴏɢɢʟᴇ ᴜsᴇʀ ʙᴀɴ\n"
            "• <code>/disable &lt;chat_id&gt;</code> │ <code>/enable &lt;chat_id&gt;</code>\n"
            "• <code>/leave &lt;chat_id&gt;</code> │ <code>/invite &lt;chat_id&gt;</code>"
        )
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👥 ʙʀᴏᴡsᴇ ᴜsᴇʀs", callback_data="admin_users_page_1"),
                InlineKeyboardButton("🚫 ᴠɪᴇᴡ ʙᴀɴ ʟɪsᴛ", callback_data="adm_panel#banned_list"),
            ],
            [
                InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ᴜsᴇʀs", callback_data="adm_panel#export_users"),
                InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ᴄʜᴀᴛs", callback_data="adm_panel#export_chats"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "export_users":
        await query.answer("📥 ᴇxᴘᴏʀᴛɪɴɢ ᴜsᴇʀs...")
        users = await db.get_all_users()
        lines = [f"Total Users: {len(users)}\n" + "=" * 50]
        for u in users:
            st = " [BANNED]" if u.get("ban_status", {}).get("is_banned") else ""
            lines.append(f"{u.get('id')} | {u.get('name', 'User')}{st}")
        bio = io.BytesIO("\n".join(lines).encode("utf-8"))
        bio.name = "registered_users.txt"
        await query.message.reply_document(
            bio,
            caption=f"👥 <b>ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀs ᴇxᴘᴏʀᴛ (<code>{len(users)}</code>)</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "export_chats":
        await query.answer("📥 ᴇxᴘᴏʀᴛɪɴɢ ᴄʜᴀᴛs & ᴄʜᴀɴɴᴇʟs...")
        chats = await db.get_all_chats()
        chnls = await db.get_all_channels()
        lines = [f"Groups ({len(chats)}) & Channels ({len(chnls)})\n" + "=" * 50, "\n[GROUPS]"]
        for c in chats:
            st = " [DISABLED]" if c.get("chat_status", {}).get("is_disabled") else ""
            lines.append(f"{c.get('id')} | {c.get('title', 'Group')}{st}")
        lines.append("\n[CHANNELS]")
        for ch in chnls:
            lines.append(f"{ch.get('chat_id')} | {ch.get('title', 'Channel')}")
        bio = io.BytesIO("\n".join(lines).encode("utf-8"))
        bio.name = "groups_and_channels.txt"
        await query.message.reply_document(
            bio,
            caption=f"💬 <b>ɢʀᴏᴜᴘs (<code>{len(chats)}</code>) &amp; 📢 ᴄʜᴀɴɴᴇʟs (<code>{len(chnls)}</code>)</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "banned_list":
        await query.answer("🚫 ʙᴀɴɴᴇᴅ ʟɪsᴛ")
        b_u, b_c = await db.get_banned()
        temp.BANNED_USERS, temp.BANNED_CHATS = b_u, b_c
        u_str = ", ".join(f"<code>{x}</code>" for x in b_u[:40]) or "<code>ɴᴏɴᴇ</code>"
        c_str = ", ".join(f"<code>{x}</code>" for x in b_c[:40]) or "<code>ɴᴏɴᴇ</code>"
        text = (
            "🚫 <b>ʙᴀɴɴᴇᴅ ᴜsᴇʀs &amp; ᴅɪsᴀʙʟᴇᴅ ᴄʜᴀᴛs</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>ʙᴀɴɴᴇᴅ ᴜsᴇʀs ({len(b_u)}):</b>\n{u_str}\n\n"
            f"💬 <b>ᴅɪsᴀʙʟᴇᴅ ᴄʜᴀᴛs ({len(b_c)}):</b>\n{c_str}"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#users_hub")]
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "broadcast_hub":
        await query.answer("📡 ʙʀᴏᴀᴅᴄᴀsᴛ ʜᴜʙ")
        users_cnt, chats_cnt, chnls_cnt, cfg = await asyncio.gather(
            db.total_users_count(),
            db.total_chat_count(),
            db.total_channels_count(),
            db.get_bot_config(),
        )
        b_cnt = int(cfg.get("total_broadcasts", 0))
        text = (
            "📡 <b>ʙʀᴏᴀᴅᴄᴀsᴛ &amp; ᴅɪʀᴇᴄᴛ ᴍᴇssᴀɢɪɴɢ ᴄᴇɴᴛᴇʀ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 👥 <b>ᴜsᴇʀ ʀᴇᴀᴄʜ:</b> <code>{users_cnt:,}</code>\n"
            f"• 💬 <b>ɢʀᴏᴜᴘ ʀᴇᴀᴄʜ:</b> <code>{chats_cnt:,}</code>\n"
            f"• 📢 <b>ᴄʜᴀɴɴᴇʟ ʀᴇᴀᴄʜ:</b> <code>{chnls_cnt:,}</code>\n"
            f"• 📊 <b>ᴛᴏᴛᴀʟ ʙʀᴏᴀᴅᴄᴀsᴛs sᴇɴᴛ:</b> <code>{b_cnt:,}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 <b>ʜᴏᴡ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ (ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇssᴀɢᴇ):</b>\n"
            "• <code>/broadcast</code> — sᴇɴᴅ ᴛᴏ ᴀʟʟ ᴜsᴇʀs\n"
            "• <code>/pin_broadcast</code> — sᴇɴᴅ &amp; ᴘɪɴ ɪɴ ᴜsᴇʀ ᴘᴍs\n"
            "• <code>/grp_broadcast</code> — sᴇɴᴅ ᴛᴏ ᴀʟʟ ɢʀᴏᴜᴘs\n"
            "• <code>/chnl_broadcast</code> — sᴇɴᴅ ᴛᴏ ᴀʟʟ ᴄʜᴀɴɴᴇʟs\n"
            "• <code>/send &lt;user_or_chat_id&gt;</code> — sᴇɴᴅ ᴛᴏ ᴀ sɪɴɢʟᴇ ᴛᴀʀɢᴇᴛ"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"), InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home")]
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "cache":
        await query.answer("⚡ ᴄᴀᴄʜᴇ sᴛᴀᴛs")
        u = USER_CACHE.stats()
        c_stats = await cache.get_stats()
        text = (
            "⚡ <b>ᴅʏɴᴀᴍɪᴄ ʟ1 + ʟ2 ᴄᴀᴄʜᴇ sᴛᴀᴛᴜs</b> 📊\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ғᴀsᴛ ᴄᴀᴄʜᴇ ᴋᴇʏs:</b> <code>{c_stats['cached_keys']} / {c_stats['max_entries']}</code>\n"
            f"• <b>ᴜsᴇʀ ᴄᴀᴄʜᴇ ᴇɴᴛʀɪᴇs:</b> <code>{u['entries']} / {u['max_entries']}</code>\n"
            f"• <b>ᴄᴀᴄʜᴇ ʜɪᴛ ʀᴀᴛᴇ:</b> <code>{c_stats['hit_ratio_percent']}%</code> (ʜɪᴛs: <code>{c_stats['hits'] + u['hits']}</code> │ ᴍɪssᴇs: <code>{c_stats['misses'] + u['misses']}</code>)\n"
            f"• <b>ʜᴇʟᴘᴅᴇsᴋ ɢʀᴏᴜᴘ ᴄᴀᴄʜᴇ:</b> <code>{len(db._mem_hd_grp)}</code>\n"
            f"• <b>ᴄʜᴀɴɴᴇʟ ᴄᴏɴғɪɢ ᴄᴀᴄʜᴇ:</b> <code>{len(db._mem_chnl_cfg)}</code>"
        )
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ ᴄʟᴇᴀʀ ᴀʟʟ ᴄᴀᴄʜᴇ", callback_data="adm_panel#clearcache"),
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#cache"),
            ],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home")],
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "clearcache":
        await cache.clear()
        u_rem = USER_CACHE.clear()
        g_rem = len(db._mem_hd_grp)
        c_rem = len(db._mem_chnl_cfg)
        db._mem_hd_grp.clear()
        db._mem_chnl_cfg.clear()
        await query.answer(f"🧹 ᴄʟᴇᴀʀᴇᴅ {u_rem + g_rem + c_rem} ᴄᴀᴄʜᴇ ᴇɴᴛʀɪᴇs!", show_alert=True)
        await safe_edit_message(
            query.message,
            f"✅ <b>ɪɴ-ᴍᴇᴍᴏʀʏ ᴄᴀᴄʜᴇ ᴄʟᴇᴀʀᴇᴅ!</b> 🧹\n\n"
            f"• <b>ᴜsᴇʀ ᴄᴀᴄʜᴇ ʀᴇᴍᴏᴠᴇᴅ:</b> <code>{u_rem}</code>\n"
            f"• <b>ɢʀᴏᴜᴘ ᴄᴏɴғɪɢ ᴄᴀᴄʜᴇ ʀᴇᴍᴏᴠᴇᴅ:</b> <code>{g_rem}</code>\n"
            f"• <b>ᴄʜᴀɴɴᴇʟ ᴄᴏɴғɪɢ ᴄᴀᴄʜᴇ ʀᴇᴍᴏᴠᴇᴅ:</b> <code>{c_rem}</code>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 ᴄᴀᴄʜᴇ sᴛᴀᴛs", callback_data="adm_panel#cache"),
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "logs":
        await query.answer("📋 ʟᴏᴀᴅɪɴɢ ʟᴏɢs...")
        preview = html.escape(get_recent_logs_preview(lines_count=28, max_chars=3200))
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📥 ᴅᴏᴡɴʟᴏᴀᴅ ʟᴏɢ ғɪʟᴇ", callback_data="logs_act#download"),
                InlineKeyboardButton("🗑️ ᴄʟᴇᴀʀ ʟᴏɢs", callback_data="logs_act#confirm_clear"),
            ],
            [
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#logs"),
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
            ],
        ])
        await safe_edit_message(
            query.message,
            f"📋 <b>ʀᴇᴄᴇɴᴛ ᴀᴘᴘʟɪᴄᴀᴛɪᴏɴ ʟᴏɢs (ʀᴇᴅᴀᴄᴛᴇᴅ)</b> 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{preview}</pre>",
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "extract_cmds":
        await query.answer("⚡ sᴄᴀɴɴɪɴɢ ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅs...")
        from plugins.allcommand import CommandExtractorEngine, generate_record_file
        admin_cmds, user_cmds = CommandExtractorEngine.get_all_commands(root_dir=".")
        me = await client.get_me()
        rec_file = generate_record_file(
            admin_cmds=admin_cmds,
            user_cmds=user_cmds,
            bot_name=me.first_name,
            admin_user=f"{query.from_user.first_name} ({query.from_user.id})",
        )
        await query.message.reply_document(
            document=rec_file,
            caption=(
                f"📑 <b>ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅs ᴀᴜᴅɪᴛ ʀᴇᴘᴏʀᴛ</b> ⚡\n"
                f"• <b>ᴛᴏᴛᴀʟ ᴄᴏᴍᴍᴀɴᴅs:</b> <code>{len(admin_cmds) + len(user_cmds)}</code>\n"
                f"• <b>ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅs:</b> <code>{len(admin_cmds)}</code> │ <b>ᴜsᴇʀ ᴄᴏᴍᴍᴀɴᴅs:</b> <code>{len(user_cmds)}</code>"
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "power":
        await query.answer("🔌 ᴘᴏᴡᴇʀ ᴄᴏɴᴛʀᴏʟs")
        text = (
            "🔌 <b>sᴇʀᴠᴇʀ ᴘᴏᴡᴇʀ, ɢɪᴛ &amp; ʟɪғᴇᴄʏᴄʟᴇ ᴄᴏɴᴛʀᴏʟs</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• 🔄 <b>sᴏғᴛ ʀᴇsᴛᴀʀᴛ:</b> ʀᴇsᴛᴀʀᴛs ᴛʜᴇ ᴘʏᴛʜᴏɴ ᴘʀᴏᴄᴇss ɪɴsᴛᴀɴᴛʟʏ\n"
            "• ⬇️ <b>ɢɪᴛ ᴘᴜʟʟ ᴄʜᴇᴄᴋ:</b> ғᴇᴛᴄʜᴇs ʟᴀᴛᴇsᴛ ᴄᴏᴍᴍɪᴛs ғʀᴏᴍ ʀᴇᴘᴏsɪᴛᴏʀʏ\n"
            "• ⚙️ <b>ʀᴇ-ɪɴᴅᴇx ᴍᴏɴɢᴏᴅʙ:</b> ᴠᴇʀɪғɪᴇs ᴀʟʟ 11 ᴄᴏʟʟᴇᴄᴛɪᴏɴ ɪɴᴅᴇxᴇs"
        )
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 sᴏғᴛ ʀᴇsᴛᴀʀᴛ", callback_data="sys:restart"),
                InlineKeyboardButton("⬇️ ɢɪᴛ ᴘᴜʟʟ", callback_data="adm_panel#git_pull"),
            ],
            [
                InlineKeyboardButton("⚙️ ᴇɴsᴜʀᴇ ᴅʙ ɪɴᴅᴇxᴇs", callback_data="adm_panel#reindex"),
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "git_pull":
        await query.answer("⬇️ ʀᴜɴɴɪɴɢ ɢɪᴛ ᴘᴜʟʟ...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "pull", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            so, se = await asyncio.wait_for(proc.communicate(), timeout=20)
            out = (so.decode("utf-8", errors="ignore") or se.decode("utf-8", errors="ignore") or "ɴᴏ ᴏᴜᴛᴘᴜᴛ").strip()
        except Exception as e:
            out = f"ᴇʀʀᴏʀ: {e}"
        await safe_edit_message(
            query.message,
            f"⬇️ <b>ɢɪᴛ ᴘᴜʟʟ ᴏᴜᴛᴘᴜᴛ:</b>\n<pre>{html.escape(out[:3000])}</pre>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="adm_panel#power")]]),
            parse_mode=enums.ParseMode.HTML,
        )

    elif action == "toggle_devil":
        new_devil = not bool(temp.DEVIL_MODE or is_devil_mode_active())
        temp.DEVIL_MODE = new_devil
        set_devil_mode(new_devil)
        await query.answer(
            f"🔥 ᴅᴇᴠɪʟᴍᴏᴅ: {'ᴇɴᴀʙʟᴇᴅ' if new_devil else 'ᴅɪsᴀʙʟᴇᴅ'}",
            show_alert=True,
        )
        text, buttons = await build_global_config_view()
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif action == "groups_page":
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        await query.answer("💬 ʟᴏᴀᴅɪɴɢ ɢʀᴏᴜᴘs...")
        chats = await db.get_all_chats()
        total_g = len(chats)
        per_page = 8
        total_pages = max(1, math.ceil(total_g / per_page))
        page = max(1, min(page, total_pages))
        slice_g = chats[(page - 1) * per_page : page * per_page]

        lines = [
            f"💬 <b>{to_smallcaps('remote groups browser & inspector')}</b> ⚡",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"• 📊 <b>{to_smallcaps('total groups')}:</b> <code>{total_g}</code> │ 📄 <b>{to_smallcaps('page')}:</b> <code>{page}/{total_pages}</code>\n",
        ]
        kb_rows = []
        if not slice_g:
            lines.append(f"<i>{to_smallcaps('no groups registered in database yet.')}</i>")
        else:
            for idx, g in enumerate(slice_g, start=(page - 1) * per_page + 1):
                cid = g.get("id")
                title = html.escape(str(g.get("title", "Group"))[:24])
                dis = g.get("chat_status", {}).get("is_disabled", False)
                badge = "🚫" if dis else "🟢"
                lines.append(f"<code>{idx}.</code> {badge} <b>{title}</b> (<code>{cid}</code>)")
                kb_rows.append([
                    InlineKeyboardButton(f"{badge} {g.get('title', 'Group')[:20]} ({cid})", callback_data=f"adm_panel#gview#{cid}")
                ])

        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"adm_panel#groups_page#{page - 1}"))
        nav.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data=f"adm_panel#groups_page#{page}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"adm_panel#groups_page#{page + 1}"))
        kb_rows.append(nav)
        kb_rows.append([
            InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ᴄʜᴀᴛs", callback_data="adm_panel#export_chats"),
            InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟs ɪɴsᴘᴇᴄᴛᴏʀ", callback_data="adm_panel#channels_page#1"),
        ])
        kb_rows.append([
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
        ])
        await safe_edit_message(query.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=enums.ParseMode.HTML)

    elif action == "gview" and len(parts) > 1:
        cid = int(parts[1])
        await query.answer(f"💬 ɪɴsᴘᴇᴄᴛɪɴɢ {cid}")
        cfg = await db.get_helpdesk_config(cid)
        st_doc = await db.get_chat(cid)
        is_dis = bool(st_doc and st_doc.get("is_disabled"))
        locks = [k for k, v in (cfg.get("locks") or {}).items() if v]
        title = str(cid)
        members_cnt = 0
        try:
            cobj = await client.get_chat(cid)
            title = cobj.title or str(cid)
            members_cnt = await client.get_chat_members_count(cid)
        except Exception:
            pass
        text = (
            f"💬 <b>{to_smallcaps('remote group inspector')}</b> 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🏷️ <b>{to_smallcaps('title')}:</b> <b>{html.escape(title)}</b>\n"
            f"• 🆔 <b>{to_smallcaps('chat id')}:</b> <code>{cid}</code> │ 👥 <b>{to_smallcaps('members')}:</b> <code>{members_cnt}</code>\n"
            f"• 🔋 <b>{to_smallcaps('bot status')}:</b> <code>{'🚫 ᴅɪsᴀʙʟᴇᴅ' if is_dis else '🟢 ᴀᴄᴛɪᴠᴇ'}</code>\n"
            f"• 🌊 <b>{to_smallcaps('anti-flood')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('antiflood_enabled') else '⚪ ᴏғғ'}</code> │ 🚨 <b>{to_smallcaps('raid')}:</b> <code>{'🚨 ᴏɴ' if cfg.get('raid_mode') else '⚪ ᴏғғ'}</code>\n"
            f"• 🔐 <b>{to_smallcaps('active locks')}:</b> <code>{', '.join(locks) if locks else 'ɴᴏɴᴇ'}</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎛️ ᴇᴅɪᴛ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇs", callback_data=f"gfeat|home|{cid}"),
                InlineKeyboardButton("🛡️ ᴀᴘᴘʟʏ ᴘʀᴏ sʜɪᴇʟᴅ", callback_data=f"autoset|grp_shield|{cid}"),
            ],
            [
                InlineKeyboardButton("🟢 ᴇɴᴀʙʟᴇ ɢʀᴏᴜᴘ" if is_dis else "🚫 ᴅɪsᴀʙʟᴇ ɢʀᴏᴜᴘ", callback_data=f"adm_panel#gtoggle#{cid}"),
                InlineKeyboardButton("🔗 ɢᴇᴛ ɪɴᴠɪᴛᴇ ʟɪɴᴋ", callback_data=f"adm_panel#ginvite#{cid}"),
            ],
            [
                InlineKeyboardButton("🚪 ʟᴇᴀᴠᴇ ɢʀᴏᴜᴘ", callback_data=f"adm_panel#gleave#{cid}"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ɢʀᴏᴜᴘs", callback_data="adm_panel#groups_page#1"),
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    elif action == "gtoggle" and len(parts) > 1:
        cid = int(parts[1])
        st_doc = await db.get_chat(cid)
        is_dis = bool(st_doc and st_doc.get("is_disabled"))
        if is_dis:
            await db.re_enable_chat(cid)
            if cid in temp.BANNED_CHATS:
                temp.BANNED_CHATS.remove(cid)
            await query.answer("🟢 ɢʀᴏᴜᴘ ᴇɴᴀʙʟᴇᴅ!", show_alert=True)
        else:
            await db.disable_chat(cid, "Disabled via Admin Panel")
            if cid not in temp.BANNED_CHATS:
                temp.BANNED_CHATS.append(cid)
            await query.answer("🚫 ɢʀᴏᴜᴘ ᴅɪsᴀʙʟᴇᴅ!", show_alert=True)
        query.data = f"adm_panel#gview#{cid}"
        return await admin_panel_callback(client, query)

    elif action == "ginvite" and len(parts) > 1:
        cid = int(parts[1])
        try:
            lnk = await client.export_chat_invite_link(cid)
            await query.answer("🔗 ɪɴᴠɪᴛᴇ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ!")
            await query.message.reply_text(f"🔗 <b>{to_smallcaps('invite link for')} <code>{cid}</code>:</b>\n<code>{lnk}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)

    elif action == "gleave" and len(parts) > 1:
        cid = int(parts[1])
        try:
            await client.leave_chat(cid)
            await query.answer("🚪 ʟᴇғᴛ ɢʀᴏᴜᴘ!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        query.data = "adm_panel#groups_page#1"
        return await admin_panel_callback(client, query)

    elif action == "channels_page":
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        await query.answer("📢 ʟᴏᴀᴅɪɴɢ ᴄʜᴀɴɴᴇʟs...")
        chnls = await db.get_all_channels()
        total_c = len(chnls)
        per_page = 8
        total_pages = max(1, math.ceil(total_c / per_page))
        page = max(1, min(page, total_pages))
        slice_c = chnls[(page - 1) * per_page : page * per_page]

        lines = [
            f"📢 <b>{to_smallcaps('remote channels browser & inspector')}</b> ⚡",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"• 📊 <b>{to_smallcaps('total channels')}:</b> <code>{total_c}</code> │ 📄 <b>{to_smallcaps('page')}:</b> <code>{page}/{total_pages}</code>\n",
        ]
        kb_rows = []
        if not slice_c:
            lines.append(f"<i>{to_smallcaps('no channels connected yet. use /connect <channel_id>.')}</i>")
        else:
            for idx, ch in enumerate(slice_c, start=(page - 1) * per_page + 1):
                cid = ch.get("chat_id")
                title = html.escape(str(ch.get("title", "Channel"))[:24])
                st = "🟢" if ch.get("auto_stream_enabled") else "📢"
                lines.append(f"<code>{idx}.</code> {st} <b>{title}</b> (<code>{cid}</code>)")
                kb_rows.append([
                    InlineKeyboardButton(f"{st} {ch.get('title', 'Channel')[:20]} ({cid})", callback_data=f"adm_panel#cview#{cid}")
                ])

        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"adm_panel#channels_page#{page - 1}"))
        nav.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data=f"adm_panel#channels_page#{page}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"adm_panel#channels_page#{page + 1}"))
        kb_rows.append(nav)
        kb_rows.append([
            InlineKeyboardButton("💬 ɢʀᴏᴜᴘs ɪɴsᴘᴇᴄᴛᴏʀ", callback_data="adm_panel#groups_page#1"),
            InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
        ])
        kb_rows.append([
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
        ])
        await safe_edit_message(query.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=enums.ParseMode.HTML)

    elif action == "cview" and len(parts) > 1:
        cid = int(parts[1])
        await query.answer(f"📢 ɪɴsᴘᴇᴄᴛɪɴɢ {cid}")
        cfg = await db.get_channel_config(cid)
        title = cfg.get("title") or str(cid)
        text = (
            f"📢 <b>{to_smallcaps('remote channel inspector')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🏷️ <b>{to_smallcaps('title')}:</b> <b>{html.escape(str(title))}</b>\n"
            f"• 🆔 <b>{to_smallcaps('channel id')}:</b> <code>{cid}</code>\n"
            f"• 🏷️ <b>{to_smallcaps('auto-tag')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_tag_enabled') else '⚪ ᴏғғ'}</code>\n"
            f"• 📝 <b>{to_smallcaps('auto-caption')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_caption_enabled', True) else '⚪ ᴏғғ'}</code>\n"
            f"• 🎬 <b>{to_smallcaps('auto-stream (4 players)')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_stream_enabled') else '⚪ ᴏғғ'}</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎛️ ᴇᴅɪᴛ ᴄʜᴀɴɴᴇʟ ғᴇᴀᴛᴜʀᴇs", callback_data=f"cfeat|home|{cid}"),
                InlineKeyboardButton("⚡ ᴀᴘᴘʟʏ ᴘʀᴏ ᴘʀᴇsᴇᴛ", callback_data=f"cfeat|apply_wm|{cid}"),
            ],
            [
                InlineKeyboardButton("🔗 sᴇᴛ ᴀs ᴍʏ ᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ", callback_data=f"adm_panel#cconnect#{cid}"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴄʜᴀɴɴᴇʟs", callback_data="adm_panel#channels_page#1"),
                InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    elif action == "cconnect" and len(parts) > 1:
        cid = int(parts[1])
        cfg = await db.get_channel_config(cid)
        await db.connect_user_channel(query.from_user.id, cid, title=cfg.get("title", "Channel"))
        await query.answer(f"🔗 ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ {cid}!", show_alert=True)

    elif action == "security_hub":
        await query.answer("🛡️ ɢʟᴏʙᴀʟ sᴇᴄᴜʀɪᴛʏ ʜᴜʙ")
        b_u, b_c = await db.get_banned()
        temp.BANNED_USERS, temp.BANNED_CHATS = b_u, b_c
        raid_groups = sum(1 for c in db._mem_hd_grp.values() if c.get("raid_mode"))
        flood_groups = sum(1 for c in db._mem_hd_grp.values() if c.get("antiflood_enabled"))
        text = (
            f"🛡️ <b>{to_smallcaps('global security & federation ban center')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🚫 <b>{to_smallcaps('globally banned users')}:</b> <code>{len(b_u)}</code>\n"
            f"• 💬 <b>{to_smallcaps('disabled chats')}:</b> <code>{len(b_c)}</code>\n"
            f"• 🌊 <b>{to_smallcaps('groups with anti-flood')}:</b> <code>{flood_groups}</code>\n"
            f"• 🚨 <b>{to_smallcaps('groups in raid mode')}:</b> <code>{raid_groups}</code>\n\n"
            f"📌 <b>{to_smallcaps('federation commands')}:</b>\n"
            f"• <code>/gban &lt;user_id&gt; [reason]</code> │ <code>/ungban &lt;user_id&gt;</code>\n"
            f"• <code>/gbanlist</code> — {to_smallcaps('view all banned users & chats')}\n"
            f"• <code>/disable &lt;chat_id&gt;</code> │ <code>/enable &lt;chat_id&gt;</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚫 ᴠɪᴇᴡ ʙᴀɴ ʟɪsᴛ", callback_data="adm_panel#banned_list"),
                InlineKeyboardButton("🔓 ᴜɴʙᴀɴ ᴀʟʟ ᴜsᴇʀs", callback_data="adm_panel#unban_all"),
            ],
            [
                InlineKeyboardButton("💬 ɢʀᴏᴜᴘs ɪɴsᴘᴇᴄᴛᴏʀ", callback_data="adm_panel#groups_page#1"),
                InlineKeyboardButton("👥 ᴜsᴇʀs ʜᴜʙ", callback_data="adm_panel#users_hub"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    elif action == "unban_all":
        b_u, _ = await db.get_banned()
        for uid_b in b_u:
            await db.remove_ban(uid_b)
        temp.BANNED_USERS = []
        await query.answer(f"🔓 ᴜɴʙᴀɴɴᴇᴅ {len(b_u)} ᴜsᴇʀs!", show_alert=True)
        query.data = "adm_panel#security_hub"
        return await admin_panel_callback(client, query)

    elif action == "engine_hub":
        await query.answer("🚀 ᴇɴɢɪɴᴇ & ᴠᴀʀs ᴛᴜɴᴇʀ")
        max_tx = getattr(Config, "MAX_CONCURRENT_TRANSMISSIONS", 16)
        cttl = getattr(Config, "CACHE_TTL", 300)
        rvars = temp.BOT_CONFIG.get("runtime_vars", {})
        text = (
            f"🚀 <b>{to_smallcaps('live runtime, stream & media engine tuner')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• ⚡ <b>{to_smallcaps('http byte-range chunk')}:</b> <code>1 MiB (4-Chunk Prefetch)</code>\n"
            f"• 📡 <b>{to_smallcaps('max concurrent transmissions')}:</b> <code>{max_tx}</code>\n"
            f"• ⏱️ <b>{to_smallcaps('l1+l2 cache ttl')}:</b> <code>{cttl}s</code>\n"
            f"• 🏷️ <b>{to_smallcaps('global watermark')}:</b> <code>{html.escape(str(Config.WATERMARK))}</code>\n"
            f"• 🔧 <b>{to_smallcaps('runtime overrides')}:</b> <code>{len(rvars)}</code>\n\n"
            f"📌 <b>{to_smallcaps('live var commands')}:</b>\n"
            f"• <code>/setvar KEY VALUE</code> │ <code>/getvar KEY</code> │ <code>/delvar KEY</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"📡 ᴍᴀx ᴛx: {max_tx} (ᴄʏᴄʟᴇ)", callback_data="adm_panel#cycle_tx"),
                InlineKeyboardButton(f"⏱️ ᴄᴀᴄʜᴇ ᴛᴛʟ: {cttl}s (ᴄʏᴄʟᴇ)", callback_data="adm_panel#cycle_ttl"),
            ],
            [
                InlineKeyboardButton("⚙️ ɢʟᴏʙᴀʟ ᴛᴏɢɢʟᴇs", callback_data="adm_panel#global_cfg"),
                InlineKeyboardButton("🛠️ ᴅᴇᴠ ᴄᴏɴsᴏʟᴇ", callback_data="adm_panel#dev_open"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    elif action == "cycle_tx":
        seq = [8, 16, 24, 32]
        curr = getattr(Config, "MAX_CONCURRENT_TRANSMISSIONS", 16)
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else 16
        Config.MAX_CONCURRENT_TRANSMISSIONS = nxt
        await query.answer(f"📡 ᴍᴀx ᴛʀᴀɴsᴍɪssɪᴏɴs: {nxt}")
        query.data = "adm_panel#engine_hub"
        return await admin_panel_callback(client, query)

    elif action == "cycle_ttl":
        seq = [60, 180, 300, 600, 1800]
        curr = getattr(Config, "CACHE_TTL", 300)
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else 300
        Config.CACHE_TTL = nxt
        await query.answer(f"⏱️ ᴄᴀᴄʜᴇ ᴛᴛʟ: {nxt}s")
        query.data = "adm_panel#engine_hub"
        return await admin_panel_callback(client, query)

    elif action == "dev_open":
        await query.answer("🛠️ ᴅᴇᴠ ᴄᴏɴsᴏʟᴇ")
        return await render_dev_console(query.message, is_edit=True)

    elif action == "tasks_hub":
        await query.answer("🔄 ᴀᴄᴛɪᴠᴇ ᴛᴀsᴋs ʜᴜʙ")
        from plugins.core_utils import task_manager
        all_t = list(asyncio.all_tasks())
        media_cnt = len(task_manager._tasks)
        media_users = len({t.user_id for t in task_manager._tasks.values()})
        text = (
            f"🔄 <b>{to_smallcaps('active tasks, workers & queue manager')}</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• ⚙️ <b>{to_smallcaps('total asyncio tasks')}:</b> <code>{len(all_t)}</code>\n"
            f"• 🎬 <b>{to_smallcaps('active media jobs')}:</b> <code>{media_cnt}</code> ({media_users} ᴜsᴇʀs)\n"
            f"• 🌐 <b>{to_smallcaps('tracked background workers')}:</b> <code>{len(temp.ACTIVE_TASKS)}</code>\n"
            f"• 🧹 <b>{to_smallcaps('gc tracked objects')}:</b> <code>{len(gc.get_objects()):,}</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🛑 sᴛᴏᴘ ᴀʟʟ ᴍᴇᴅɪᴀ ᴊᴏʙs", callback_data="adm_panel#stop_media_tasks"),
                InlineKeyboardButton("🧹 ғᴏʀᴄᴇ ɢᴄ ᴄʟᴇᴀɴᴜᴘ", callback_data="adm_panel#force_gc"),
            ],
            [
                InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="adm_panel#tasks_hub"),
                InlineKeyboardButton("🌐 ᴜᴘᴛɪᴍᴇ ᴀᴅᴍɪɴ", callback_data="upad_home"),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    elif action == "stop_media_tasks":
        from plugins.core_utils import task_manager
        stopped = 0
        for tid in list(task_manager._tasks.keys()):
            if await task_manager.cancel_task(tid):
                stopped += 1
        await query.answer(f"🛑 sᴛᴏᴘᴘᴇᴅ {stopped} ᴍᴇᴅɪᴀ ᴊᴏʙs!", show_alert=True)
        query.data = "adm_panel#tasks_hub"
        return await admin_panel_callback(client, query)

    elif action == "force_gc":
        freed = gc.collect()
        await query.answer(f"🧹 ɢᴄ ғʀᴇᴇᴅ {freed} ᴏʙᴊᴇᴄᴛs!", show_alert=True)
        query.data = "adm_panel#tasks_hub"
        return await admin_panel_callback(client, query)

    elif action == "reindex":
        await query.answer("⚙️ ᴇɴsᴜʀɪɴɢ ɪɴᴅᴇxᴇs...")
        await db.ensure_indexes()
        await safe_edit_message(
            query.message,
            "⚙️ <b>ᴍᴏɴɢᴏᴅʙ ɪɴᴅᴇx ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ</b>\n\n"
            "✅ <code>users, groups, channels, autoforwards, helpdesk_groups, helpdesk_warnings, helpdesk_modlogs, group_notes, group_filters, group_blacklist, user_afk</code> ɪɴᴅᴇxᴇs ᴇɴsᴜʀᴇᴅ!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"), InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home")]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_callback_query(
    filters.regex(r"^admin_(panel_back|stats|clear_cache|clean|restart|close|settings|dbstats|cache_info|logs|broadcast_info)$")
)
async def legacy_admin_callbacks(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer(to_smallcaps("access denied!"), show_alert=True)

    action = query.matches[0].group(1)
    if action == "panel_back":
        await query.answer("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ")
        await safe_edit_message(query.message, build_admin_help_text(), reply_markup=build_admin_dashboard_buttons(), parse_mode=enums.ParseMode.HTML)
    elif action == "stats":
        await query.answer("📊 ʀᴇғʀᴇsʜɪɴɢ sᴛᴀᴛs...")
        await safe_edit_message(query.message, await build_stats_formatted_text(), reply_markup=build_admin_dashboard_buttons(), parse_mode=enums.ParseMode.HTML)
    elif action in ("clear_cache", "clean"):
        await cache.clear()
        USER_CACHE.clear()
        await query.answer(to_smallcaps("in-memory cache cleared!"), show_alert=True)
        await safe_edit_message(query.message, build_admin_help_text(), reply_markup=build_admin_dashboard_buttons(), parse_mode=enums.ParseMode.HTML)
    elif action == "cache_info":
        await query.answer("⚡ ᴄᴀᴄʜᴇ ᴛᴇʟᴇᴍᴇᴛʀʏ")
        await render_cache_card(query.message, is_edit=True)
    elif action == "dbstats":
        await query.answer("🗄️ ᴅʙ sᴛᴀᴛᴜs")
        await safe_edit_message(query.message, await build_dbstatus_text(), reply_markup=build_admin_dashboard_buttons(), parse_mode=enums.ParseMode.HTML)
    elif action == "logs":
        await query.answer("📋 ʟᴏɢs ᴘʀᴇᴠɪᴇᴡ")
        preview = html.escape(get_recent_logs_preview(lines_count=28, max_chars=3200))
        await safe_edit_message(query.message, f"📋 <b>ʀᴇᴄᴇɴᴛ ʟᴏɢs:</b>\n<pre>{preview}</pre>", reply_markup=build_admin_dashboard_buttons(), parse_mode=enums.ParseMode.HTML)
    elif action in ("settings", "broadcast_info"):
        await query.answer("⚙️ ɢʟᴏʙᴀʟ sᴇᴛᴛɪɴɢs")
        text, buttons = await build_global_config_view()
        await safe_edit_message(query.message, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    elif action == "close":
        await query.answer("🗑️ ᴄʟᴏsᴇᴅ")
        try:
            await query.message.delete()
        except Exception:
            pass
    elif action == "restart":
        await query.answer(to_smallcaps("checkpointing tasks & initiating bot restart..."), show_alert=True)
        try:
            from plugins.compressor import checkpoint_active_tasks_before_shutdown
            await checkpoint_active_tasks_before_shutdown("admin_callback_restart")
        except Exception:
            pass
        os.execl(sys.executable, sys.executable, *sys.argv)


@Client.on_callback_query(filters.regex(r"^admin_users_page_(\d+)$"))
async def admin_users_pagination_callback(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer(to_smallcaps("access denied!"), show_alert=True)
    page = int(query.matches[0].group(1))
    await query.answer(f"👥 ᴜsᴇʀs ᴘᴀɢᴇ {page}")
    await render_users_page(query.message, page=page, is_edit=True)


@Client.on_callback_query(filters.regex(r"^admin_toggle_ban_(\d+)$"))
async def admin_toggle_ban_callback(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer(to_smallcaps("access denied!"), show_alert=True)
    target_id = int(query.matches[0].group(1))
    u = await user_repo.get_user(target_id)
    if u and u.get("is_banned"):
        await db.remove_ban(target_id)
        if target_id in temp.BANNED_USERS:
            temp.BANNED_USERS.remove(target_id)
        await query.answer(to_smallcaps("user unbanned!"), show_alert=True)
    else:
        await db.ban_user(target_id, "Banned via Admin Panel")
        if target_id not in temp.BANNED_USERS:
            temp.BANNED_USERS.append(target_id)
        await query.answer(to_smallcaps("user banned!"), show_alert=True)
    await render_userinfo_card(query.message, target_id, is_edit=True)


@Client.on_callback_query(filters.regex(r"^dev_(toggle|close|gc)$"))
async def dev_callbacks(client: Client, query: CallbackQuery):
    if not query.from_user or not is_admin_sync(query.from_user.id):
        return await query.answer(to_smallcaps("access denied!"), show_alert=True)

    action = query.matches[0].group(1)
    if action == "toggle":
        new_devil = not bool(temp.DEVIL_MODE or is_devil_mode_active())
        temp.DEVIL_MODE = new_devil
        set_devil_mode(new_devil)
        await query.answer(to_smallcaps(f"devil mode {'enabled' if new_devil else 'disabled'}!"))
        await render_dev_console(query.message, is_edit=True)
    elif action == "gc":
        collected = gc.collect()
        await query.answer(to_smallcaps(f"gc complete: {collected} objects freed!"), show_alert=True)
        await render_dev_console(query.message, is_edit=True)
    elif action == "close":
        await query.answer("🗑️ ᴄʟᴏsᴇᴅ")
        try:
            await query.message.delete()
        except Exception:
            pass
