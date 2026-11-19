import asyncio
import hashlib
import html
import io
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.users_chats_db import db
from info import ADMINS, COMMAND_PREFIXES, LOG_CHANNEL
from plugins.channel_manager import resolve_target_channel_and_verify
from utils import (
    get_readable_time,
    is_admin_sync,
    safe_edit_message,
    to_smallcaps,
)

logger = logging.getLogger("MMWExtraChannelMgr")

XM_SEP = "|"
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_TME_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+", re.IGNORECASE)
_USER_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
_HASH_RE = re.compile(r"#[A-Za-z0-9_]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_RECENT_POST_HASHES: Dict[int, Dict[str, float]] = {}

EXTRA_CHANNEL_CATEGORIES: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {
    "format": (
        "🎨 Auto-Formatting & Styling",
        [
            ("bold_title", "Bold First Line Title"),
            ("italic_footer", "Italic Last Line Footer"),
            ("mono_filesize", "Monospace File Size"),
            ("smallcaps_caption", "SmallCaps Styled Caption"),
            ("strip_links", "Strip External URLs"),
            ("strip_usernames", "Strip @Usernames"),
            ("strip_hashtags", "Strip Tags"),
            ("strip_emojis", "Strip Non-ASCII Symbols"),
            ("trim_whitespace", "Trim Extra Whitespace"),
            ("uppercase_header", "Uppercase First Line"),
            ("add_separator_line", "Add Divider Line"),
            ("add_bullet_prefix", "Add Bullet Prefix"),
            ("add_timestamp", "Append UTC Time Stamp"),
            ("add_date_stamp", "Append UTC Date Stamp"),
            ("add_post_number", "Append Post Sequence No."),
            ("wrap_blockquote", "Wrap Caption in Blockquote"),
            ("wrap_spoiler_caption", "Wrap Caption in Spoiler"),
            ("clean_newlines", "Collapse Blank Lines"),
            ("replace_underscores", "Replace Underscores w/ Spaces"),
            ("keep_extension_tag", "Append File Extension Tag"),
        ],
    ),
    "media": (
        "🎬 Media & File Automation",
        [
            ("auto_file_size_tag", "Auto File Size Line"),
            ("auto_mime_badge", "Auto MIME Type Badge"),
            ("auto_resolution_tag", "Auto Video Resolution Tag"),
            ("auto_duration_tag", "Auto Media Duration Tag"),
            ("auto_audio_badge", "Auto Audio Media Badge"),
            ("auto_video_badge", "Auto Video Media Badge"),
            ("auto_doc_badge", "Auto Document Badge"),
            ("auto_photo_badge", "Auto Photo Media Badge"),
            ("auto_voice_badge", "Auto Voice Note Badge"),
            ("auto_animation_badge", "Auto GIF Animation Badge"),
            ("protect_content_copy", "Protect Forwarded Copies"),
            ("disable_web_preview", "Disable Link Previews"),
            ("silent_notification", "Silent Channel Broadcasts"),
            ("auto_forward_backup", "Auto Copy to Log Channel"),
            ("auto_mirror_channel", "Auto Mirror to Backup ID"),
            ("auto_pin_video", "Auto Pin New Videos"),
            ("auto_pin_audio", "Auto Pin New Audios"),
            ("auto_pin_document", "Auto Pin New Documents"),
            ("auto_pin_photo", "Auto Pin New Photos"),
            ("auto_pin_poll", "Auto Pin New Polls"),
        ],
    ),
    "buttons": (
        "🔘 Link, Button & CTA Engine",
        [
            ("btn_share_post", "Share Post URL Button"),
            ("btn_channel_link", "Join Channel CTA Button"),
            ("btn_request_group", "Request Group Button"),
            ("btn_bot_start", "Open Bot CTA Button"),
            ("btn_watch_stream", "Watch Online CTA Button"),
            ("btn_fast_download", "Fast Download CTA Button"),
            ("btn_download_hub", "Download Hub CTA Button"),
            ("btn_backup_channel", "Backup Channel Button"),
            ("btn_contact_admin", "Contact Admin Button"),
            ("btn_feedback_url", "Feedback Link Button"),
            ("btn_mirror_link", "Mirror Index Button"),
            ("btn_copy_id", "Channel Post Link Button"),
            ("btn_subscribe_cta", "Subscribe CTA Button"),
            ("btn_report_broken", "Report Broken Link Button"),
            ("btn_next_part", "Next Part Link Button"),
            ("btn_prev_part", "Previous Part Button"),
            ("btn_playlist_index", "Playlist Index Button"),
            ("btn_vip_channel", "VIP Channel CTA Button"),
            ("btn_rules_link", "Channel Rules Button"),
            ("btn_support_desk", "Support Desk Button"),
        ],
    ),
    "guard": (
        "🛡️ Channel Moderation & Guard",
        [
            ("block_external_urls", "Delete External URL Posts"),
            ("block_tme_invites", "Delete t.me Invite Posts"),
            ("block_forwarded_posts", "Delete Forwarded Posts"),
            ("block_bot_posts", "Delete Bot-Sent Posts"),
            ("block_stickers", "Delete Sticker Posts"),
            ("block_gifs", "Delete GIF Posts"),
            ("block_voice_notes", "Delete Voice Notes"),
            ("block_video_notes", "Delete Video Notes"),
            ("block_polls", "Delete Poll Posts"),
            ("block_locations", "Delete Location Posts"),
            ("block_contacts", "Delete Contact Cards"),
            ("block_dice", "Delete Dice Posts"),
            ("block_games", "Delete Game Posts"),
            ("block_venues", "Delete Venue Posts"),
            ("block_empty_captions", "Delete Uncaptioned Media"),
            ("block_duplicate_posts", "Delete Duplicate Posts"),
            ("block_oversized_files", "Delete Files > 2GB"),
            ("block_undersized_files", "Delete Tiny Files < 1KB"),
            ("block_bad_words", "Delete Bad-Word Posts"),
            ("block_non_media_text", "Delete Text-Only Posts"),
        ],
    ),
    "retention": (
        "⏳ Auto-Delete & Retention",
        [
            ("ttl_text_1h", "Auto-Del Text After 1h"),
            ("ttl_text_24h", "Auto-Del Text After 24h"),
            ("ttl_photo_24h", "Auto-Del Photo After 24h"),
            ("ttl_video_24h", "Auto-Del Video After 24h"),
            ("ttl_doc_24h", "Auto-Del Doc After 24h"),
            ("ttl_audio_24h", "Auto-Del Audio After 24h"),
            ("ttl_sticker_10m", "Auto-Del Sticker After 10m"),
            ("ttl_gif_30m", "Auto-Del GIF After 30m"),
            ("ttl_poll_24h", "Auto-Del Poll After 24h"),
            ("ttl_service_msgs", "Auto-Del Service Messages"),
            ("ttl_pinned_notice", "Auto-Del Pin Notices"),
            ("ttl_forwarded_1h", "Auto-Del Forwards After 1h"),
            ("ttl_links_2h", "Auto-Del Link Posts 2h"),
            ("ttl_promos_6h", "Auto-Del Promo Posts 6h"),
            ("ttl_alerts_12h", "Auto-Del Alert Posts 12h"),
            ("auto_purge_deleted_refs", "Purge Stale Hash Cache"),
            ("auto_cleanup_empty", "Auto-Del Empty Posts"),
            ("retention_7d_log", "Track 7-Day Retention"),
            ("retention_30d_log", "Track 30-Day Retention"),
            ("auto_expire_invites", "Single-Use Invite Flag"),
        ],
    ),
    "react": (
        "🔥 Reactions & Engagement",
        [
            ("react_fire", "Auto React 🔥"),
            ("react_thumbs_up", "Auto React 👍"),
            ("react_heart", "Auto React ❤️"),
            ("react_star", "Auto React ⭐"),
            ("react_clap", "Auto React 👏"),
            ("react_rocket", "Auto React 🚀"),
            ("react_party", "Auto React 🎉"),
            ("react_100", "Auto React 💯"),
            ("react_lightning", "Auto React ⚡"),
            ("react_trophy", "Auto React 🏆"),
            ("react_eyes", "Auto React 👀"),
            ("react_mindblown", "Auto React 🤯"),
            ("react_crown", "Auto React 👑"),
            ("react_gem", "Auto React 💎"),
            ("react_check", "Auto React ✅"),
            ("auto_view_counter_log", "Log Post Views Telemetry"),
            ("auto_milestone_100", "Log Every 100th Post"),
            ("auto_milestone_1k", "Log Every 1000th Post"),
            ("auto_milestone_10k", "Log Every 10000th Post"),
            ("auto_comment_prompt", "Append Comment Prompt"),
        ],
    ),
    "brand": (
        "🏷️ Metadata & Branding",
        [
            ("brand_watermark", "Append Channel Watermark"),
            ("brand_channel_handle", "Append @Channel Handle"),
            ("brand_channel_title", "Append Channel Title"),
            ("brand_copyright_line", "Append Copyright Notice"),
            ("brand_verified_tag", "Append Verified Tag"),
            ("brand_quality_tag", "Append HD Quality Label"),
            ("brand_language_tag", "Append Language Label"),
            ("brand_category_tag", "Append Category Label"),
            ("brand_series_tag", "Append Series Label"),
            ("brand_season_tag", "Append Season Label"),
            ("brand_episode_tag", "Append Episode Label"),
            ("brand_year_tag", "Append Release Year Tag"),
            ("brand_source_tag", "Append Source Credit Tag"),
            ("brand_credit_line", "Append Uploader Credit"),
            ("brand_disclaimer_line", "Append Channel Disclaimer"),
            ("brand_mirror_notice", "Append Mirror Notice"),
            ("brand_dmca_notice", "Append DMCA Notice"),
            ("brand_join_reminder", "Append Join Reminder"),
            ("brand_share_reminder", "Append Share Reminder"),
            ("brand_archive_id", "Append Archive Post ID"),
        ],
    ),
    "schedule": (
        "⏱️ Scheduling & Rate Control",
        [
            ("queue_mode_enabled", "Enable Queue Logging"),
            ("queue_interval_5m", "5-Min Post Pacing Flag"),
            ("queue_interval_15m", "15-Min Post Pacing Flag"),
            ("queue_interval_30m", "30-Min Post Pacing Flag"),
            ("queue_interval_60m", "60-Min Post Pacing Flag"),
            ("night_quiet_hours", "Silent Mode 23:00-06:00 UTC"),
            ("morning_digest_pin", "Morning Digest Pin Flag"),
            ("evening_digest_pin", "Evening Digest Pin Flag"),
            ("weekend_boost_mode", "Weekend Reaction Boost"),
            ("batch_album_mode", "Album Group Tracking"),
            ("auto_delay_5s", "5s Edit Delay"),
            ("auto_delay_30s", "30s Edit Delay"),
            ("auto_delay_60s", "60s Edit Delay"),
            ("rate_limit_10_per_hr", "Warn > 10 Posts/Hour"),
            ("rate_limit_30_per_hr", "Warn > 30 Posts/Hour"),
            ("rate_limit_60_per_hr", "Warn > 60 Posts/Hour"),
            ("auto_retry_failed_post", "Auto Retry FloodWait"),
            ("auto_dedupe_queue", "Deduplicate Burst Posts"),
            ("priority_pin_queue", "Priority Pin Queue Flag"),
            ("auto_flush_queue", "Auto Flush Queue Flag"),
        ],
    ),
    "analytics": (
        "📊 Analytics, Audit & Reports",
        [
            ("track_post_count", "Track Total Post Count"),
            ("track_video_count", "Track Video Post Count"),
            ("track_audio_count", "Track Audio Post Count"),
            ("track_doc_count", "Track Document Count"),
            ("track_photo_count", "Track Photo Post Count"),
            ("track_total_bytes", "Track Total Media Bytes"),
            ("track_daily_posts", "Track Daily Post Counter"),
            ("track_weekly_posts", "Track Weekly Counter"),
            ("track_monthly_posts", "Track Monthly Counter"),
            ("track_deleted_count", "Track Guard Deletions"),
            ("track_edited_count", "Track Auto-Edited Posts"),
            ("track_forwarded_count", "Track Backup Copies"),
            ("track_pinned_count", "Track Auto-Pinned Posts"),
            ("track_reacted_count", "Track Auto-Reactions"),
            ("track_blocked_count", "Track Blocked Posts"),
            ("track_top_extension", "Track Top File Extension"),
            ("track_peak_hour", "Track Peak Posting Hour"),
            ("audit_admin_edits", "Log Channel Post Audits"),
            ("audit_link_changes", "Log Link Guard Events"),
            ("export_json_snapshot", "Allow JSON Stats Export"),
        ],
    ),
    "sync": (
        "🔄 Backup, Sync & Clone Tools",
        [
            ("sync_to_log_channel", "Sync Posts to Log Channel"),
            ("sync_caption_edits", "Sync Edited Captions"),
            ("sync_media_only", "Sync Media Posts Only"),
            ("sync_text_only", "Sync Text Posts Only"),
            ("sync_pinned_only", "Sync Pinned Posts Only"),
            ("backup_post_index", "Index Post IDs in DB"),
            ("backup_config_json", "Enable Config JSON Dump"),
            ("backup_invite_links", "Track Generated Invites"),
            ("backup_admin_list", "Snapshot Admin Roster"),
            ("backup_word_filters", "Backup Bad-Word List"),
            ("backup_replace_rules", "Backup Replace Rules"),
            ("backup_button_presets", "Backup CTA Button Links"),
            ("backup_header_footer", "Backup Header & Footer"),
            ("clone_strip_forward_tag", "Copy Without Forward Tag"),
            ("clone_preserve_buttons", "Preserve Reply Markup"),
            ("clone_apply_watermark", "Apply Watermark on Copy"),
            ("clone_skip_duplicates", "Skip Duplicate Copies"),
            ("clone_skip_service", "Skip Service Messages"),
            ("clone_throttle_safe", "FloodWait Safe Pacing"),
            ("clone_verify_hash", "Verify Content Hash"),
        ],
    ),
    "utilities": (
        "🛠️ Live Channel Admin Controls",
        [
            ("cmd_quick_post", "Quick Channel Post Tool"),
            ("cmd_quick_edit", "Quick Caption Edit Tool"),
            ("cmd_quick_delete", "Quick Delete Post Tool"),
            ("cmd_quick_pin", "Quick Pin Post Tool"),
            ("cmd_quick_unpin", "Quick Unpin Tool"),
            ("cmd_quick_poll", "Quick Channel Poll Tool"),
            ("cmd_quick_invite", "Quick Invite Generator"),
            ("cmd_quick_stats", "Live Channel Stats Card"),
            ("cmd_quick_admins", "Live Admin List Card"),
            ("cmd_quick_export", "Export Full JSON Backup"),
            ("cmd_quick_clone", "Clone Post to Backup"),
            ("cmd_quick_wipe_links", "Strip Links on Demand"),
            ("cmd_quick_replace", "Find & Replace Engine"),
            ("cmd_quick_watermark", "Custom Watermark Line"),
            ("cmd_quick_protect", "Content Protection Mode"),
            ("cmd_quick_silent", "Silent Publish Toggle"),
            ("cmd_quick_backup", "Snapshot Channel Config"),
            ("cmd_quick_restore", "Restore Default Presets"),
            ("cmd_quick_reset", "Reset All Extra Flags"),
            ("cmd_quick_health", "Channel Health Diagnostics"),
        ],
    ),
}

EXTRA_FEATURE_INDEX: Dict[str, Tuple[str, str]] = {}
for _cat_key, (_cat_title, _items) in EXTRA_CHANNEL_CATEGORIES.items():
    for _fkey, _flabel in _items:
        EXTRA_FEATURE_INDEX[_fkey] = (_cat_key, _flabel)

_REACTION_MAP = {
    "react_fire": "🔥",
    "react_thumbs_up": "👍",
    "react_heart": "❤️",
    "react_star": "⭐",
    "react_clap": "👏",
    "react_rocket": "🚀",
    "react_party": "🎉",
    "react_100": "💯",
    "react_lightning": "⚡",
    "react_trophy": "🏆",
    "react_eyes": "👀",
    "react_mindblown": "🤯",
    "react_crown": "👑",
    "react_gem": "💎",
    "react_check": "✅",
}

_TTL_SECONDS_MAP = {
    "ttl_sticker_10m": 600,
    "ttl_gif_30m": 1800,
    "ttl_text_1h": 3600,
    "ttl_forwarded_1h": 3600,
    "ttl_links_2h": 7200,
    "ttl_promos_6h": 21600,
    "ttl_alerts_12h": 43200,
    "ttl_text_24h": 86400,
    "ttl_photo_24h": 86400,
    "ttl_video_24h": 86400,
    "ttl_doc_24h": 86400,
    "ttl_audio_24h": 86400,
    "ttl_poll_24h": 86400,
}


def get_total_extra_features_count() -> int:
    return len(EXTRA_FEATURE_INDEX)


def _human_size(num: int) -> str:
    val = float(max(0, int(num or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            return f"{val:.2f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.2f} PB"


def _get_extra_state(cfg: Dict[str, Any]) -> Tuple[Dict[str, bool], Dict[str, Any], Dict[str, str], List[str]]:
    feats = dict(cfg.get("extra_features") or {})
    stats = dict(cfg.get("extra_stats") or {})
    repls = dict(cfg.get("extra_replace_rules") or {})
    bads = list(cfg.get("extra_bad_words") or [])
    return feats, stats, repls, bads


def _build_main_panel(chat_id: int, title: str, feats: Dict[str, bool]) -> Tuple[str, InlineKeyboardMarkup]:
    enabled_cnt = sum(1 for k in EXTRA_FEATURE_INDEX if feats.get(k))
    total_cnt = len(EXTRA_FEATURE_INDEX)
    text = (
        f"⚡ <b>{to_smallcaps('channel extra manager pro')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📢 <b>{to_smallcaps('channel')}:</b> {html.escape(title or str(chat_id))}\n"
        f"• 🆔 <b>{to_smallcaps('id')}:</b> <code>{chat_id}</code>\n"
        f"• 🧩 <b>{to_smallcaps('features')}:</b> <code>{enabled_cnt}/{total_cnt} Active</code>\n\n"
        f"💡 {to_smallcaps('select a category below to toggle real-time channel-only automation:')}"
    )
    rows: List[List[InlineKeyboardButton]] = []
    cat_keys = list(EXTRA_CHANNEL_CATEGORIES.keys())
    for i in range(0, len(cat_keys), 2):
        row = []
        for ck in cat_keys[i : i + 2]:
            ctitle, citems = EXTRA_CHANNEL_CATEGORIES[ck]
            on_n = sum(1 for fk, _ in citems if feats.get(fk))
            row.append(
                InlineKeyboardButton(
                    f"{ctitle.split(' ', 1)[0]} {to_smallcaps(ck)} ({on_n}/{len(citems)})",
                    callback_data=f"xmgr{XM_SEP}cat{XM_SEP}{chat_id}{XM_SEP}{ck}{XM_SEP}0",
                )
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton(f"📊 {to_smallcaps('live stats')}", callback_data=f"xmgr{XM_SEP}stats{XM_SEP}{chat_id}"),
        InlineKeyboardButton(f"🩺 {to_smallcaps('health')}", callback_data=f"xmgr{XM_SEP}health{XM_SEP}{chat_id}"),
    ])
    rows.append([
        InlineKeyboardButton(f"📦 {to_smallcaps('export json')}", callback_data=f"xmgr{XM_SEP}export{XM_SEP}{chat_id}"),
        InlineKeyboardButton(f"♻️ {to_smallcaps('reset all')}", callback_data=f"xmgr{XM_SEP}reset{XM_SEP}{chat_id}"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])
    return text, InlineKeyboardMarkup(rows)


def _build_category_panel(chat_id: int, title: str, cat_key: str, page: int, feats: Dict[str, bool]) -> Tuple[str, InlineKeyboardMarkup]:
    ctitle, citems = EXTRA_CHANNEL_CATEGORIES[cat_key]
    per_page = 10
    total_pages = max(1, (len(citems) + per_page - 1) // per_page)
    page = max(0, min(total_pages - 1, int(page)))
    chunk = citems[page * per_page : (page + 1) * per_page]
    on_n = sum(1 for fk, _ in citems if feats.get(fk))

    text = (
        f"⚡ <b>{html.escape(ctitle)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📢 <b>{to_smallcaps('channel')}:</b> {html.escape(title or str(chat_id))}\n"
        f"• ✅ <b>{to_smallcaps('enabled')}:</b> <code>{on_n}/{len(citems)}</code> • Page <code>{page + 1}/{total_pages}</code>\n\n"
        f"👇 {to_smallcaps('tap any feature to toggle on/off:')}"
    )
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        row = []
        for fk, flabel in chunk[i : i + 2]:
            icon = "🟢" if feats.get(fk) else "⚪"
            short = flabel[:22]
            row.append(
                InlineKeyboardButton(
                    f"{icon} {short}",
                    callback_data=f"xmgr{XM_SEP}tog{XM_SEP}{chat_id}{XM_SEP}{cat_key}{XM_SEP}{page}{XM_SEP}{fk}",
                )
            )
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"xmgr{XM_SEP}cat{XM_SEP}{chat_id}{XM_SEP}{cat_key}{XM_SEP}{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"xmgr{XM_SEP}cat{XM_SEP}{chat_id}{XM_SEP}{cat_key}{XM_SEP}{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([
        InlineKeyboardButton(f"🟢 {to_smallcaps('enable all')}", callback_data=f"xmgr{XM_SEP}allon{XM_SEP}{chat_id}{XM_SEP}{cat_key}{XM_SEP}{page}"),
        InlineKeyboardButton(f"⚪ {to_smallcaps('disable all')}", callback_data=f"xmgr{XM_SEP}alloff{XM_SEP}{chat_id}{XM_SEP}{cat_key}{XM_SEP}{page}"),
    ])
    rows.append([
        InlineKeyboardButton(f"🔙 {to_smallcaps('categories')}", callback_data=f"xmgr{XM_SEP}home{XM_SEP}{chat_id}"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])
    return text, InlineKeyboardMarkup(rows)


async def _schedule_delete(client: Client, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(max(1, int(delay)))
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception:
        pass


@Client.on_message(filters.command(["extramanager", "chx", "xmgr"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def extramanager_home_cmd(client: Client, message: Message):
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return
    feats, _, _, _ = _get_extra_state(cfg)
    text, kb = _build_main_panel(chat_obj.id, chat_obj.title or "", feats)
    await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["xfeat", "xtoggle", "xcat", "xstats", "xexport", "xreplace", "xbadword", "xpoll", "xreset"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def extramanager_tools_cmd(client: Client, message: Message):
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    cmd = message.command[0].lower()
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    feats, stats, repls, bads = _get_extra_state(cfg)

    if cmd == "xfeat":
        q = arg.lower()
        matches = [
            (fk, flabel, ck)
            for fk, (ck, flabel) in EXTRA_FEATURE_INDEX.items()
            if (not q or q in fk or q in flabel.lower() or q in ck)
        ]
        lines = [
            f"🧩 <b>{to_smallcaps('channel extra features')} ({len(matches)}/{len(EXTRA_FEATURE_INDEX)})</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for fk, flabel, ck in matches[:25]:
            st = "🟢" if feats.get(fk) else "⚪"
            lines.append(f"{st} <code>{fk}</code> — {html.escape(flabel)} [{ck}]")
        if len(matches) > 25:
            lines.append(f"\n<i>+{len(matches) - 25} more — use /xfeat &lt;keyword&gt; or /xmgr</i>")
        return await message.reply_text("\n".join(lines), quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xtoggle":
        parts = arg.split()
        if not parts:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/xtoggle &lt;feature_key&gt; [on|off]</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        fkey = parts[0].strip().lower()
        if fkey not in EXTRA_FEATURE_INDEX:
            return await message.reply_text(f"❌ <b>Unknown feature:</b> <code>{html.escape(fkey)}</code>\nUse <code>/xfeat</code> to search.", quote=True, parse_mode=enums.ParseMode.HTML)
        if len(parts) > 1:
            new_val = parts[1].strip().lower() in ("on", "1", "true", "yes", "enable")
        else:
            new_val = not bool(feats.get(fkey, False))
        feats[fkey] = new_val
        await db.update_channel_config(chat_obj.id, {"extra_features": feats}, title=chat_obj.title or "")
        return await message.reply_text(
            f"{'🟢' if new_val else '⚪'} <code>{fkey}</code> → <b>{'ENABLED' if new_val else 'DISABLED'}</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "xcat":
        ck = arg.strip().lower()
        if ck not in EXTRA_CHANNEL_CATEGORIES:
            cats = ", ".join(f"<code>{k}</code>" for k in EXTRA_CHANNEL_CATEGORIES)
            return await message.reply_text(f"📂 <b>Categories:</b> {cats}\nUsage: <code>/xcat &lt;category&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        text, kb = _build_category_panel(chat_obj.id, chat_obj.title or "", ck, 0, feats)
        return await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xstats":
        enabled_cnt = sum(1 for k in EXTRA_FEATURE_INDEX if feats.get(k))
        txt = (
            f"📊 <b>{to_smallcaps('channel extra analytics')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📢 <b>Channel:</b> {html.escape(chat_obj.title or str(chat_obj.id))}\n"
            f"• 🧩 <b>Active Features:</b> <code>{enabled_cnt}/{len(EXTRA_FEATURE_INDEX)}</code>\n"
            f"• 📨 <b>Processed Posts:</b> <code>{int(stats.get('posts', 0))}</code>\n"
            f"• 🎬 <b>Videos / Audios / Docs / Photos:</b> <code>{int(stats.get('videos', 0))}/{int(stats.get('audios', 0))}/{int(stats.get('docs', 0))}/{int(stats.get('photos', 0))}</code>\n"
            f"• 📦 <b>Total Media Bytes:</b> <code>{_human_size(int(stats.get('bytes', 0)))}</code>\n"
            f"• ✏️ <b>Auto-Edited / Pinned / Reacted:</b> <code>{int(stats.get('edited', 0))}/{int(stats.get('pinned', 0))}/{int(stats.get('reacted', 0))}</code>\n"
            f"• 🛡️ <b>Blocked / Synced:</b> <code>{int(stats.get('blocked', 0))}/{int(stats.get('synced', 0))}</code>\n"
            f"• 🔁 <b>Replace Rules / Bad Words:</b> <code>{len(repls)}/{len(bads)}</code>"
        )
        return await message.reply_text(txt, quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xexport":
        payload = {
            "channel_id": chat_obj.id,
            "channel_title": chat_obj.title or "",
            "total_supported_features": len(EXTRA_FEATURE_INDEX),
            "enabled_features": sorted([k for k, v in feats.items() if v]),
            "extra_stats": stats,
            "extra_replace_rules": repls,
            "extra_bad_words": bads,
            "exported_at": int(time.time()),
        }
        buf = io.BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
        buf.name = f"extramanager_{abs(chat_obj.id)}.json"
        return await message.reply_document(buf, caption=f"📦 <b>Channel Extra Manager Backup</b> (<code>{chat_obj.id}</code>)", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xreplace":
        if not arg:
            rules_txt = "\n".join(f"• <code>{html.escape(k)}</code> → <code>{html.escape(v)}</code>" for k, v in list(repls.items())[:20]) or "None"
            return await message.reply_text(f"🔁 <b>Replace Rules ({len(repls)}):</b>\n{rules_txt}\n\nUsage: <code>/xreplace old | new</code> or <code>/xreplace clear</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        if arg.lower() in ("clear", "reset"):
            await db.update_channel_config(chat_obj.id, {"extra_replace_rules": {}}, title=chat_obj.title or "")
            return await message.reply_text("✅ <b>All replace rules cleared!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        if "|" not in arg:
            return await message.reply_text("⚠️ Usage: <code>/xreplace old_text | new_text</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        old_t, new_t = [x.strip() for x in arg.split("|", 1)]
        if old_t:
            repls[old_t] = new_t
            await db.update_channel_config(chat_obj.id, {"extra_replace_rules": repls}, title=chat_obj.title or "")
        return await message.reply_text(f"✅ Rule saved: <code>{html.escape(old_t)}</code> → <code>{html.escape(new_t)}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xbadword":
        if not arg:
            bw = ", ".join(f"<code>{html.escape(w)}</code>" for w in bads[:30]) or "None"
            return await message.reply_text(f"🚫 <b>Channel Bad Words ({len(bads)}):</b>\n{bw}\n\nUsage: <code>/xbadword word1, word2</code> or <code>/xbadword clear</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        if arg.lower() in ("clear", "reset"):
            await db.update_channel_config(chat_obj.id, {"extra_bad_words": []}, title=chat_obj.title or "")
            return await message.reply_text("✅ <b>Bad-word list cleared!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        added = [w.strip().lower() for w in arg.split(",") if w.strip()]
        merged = sorted(set(bads) | set(added))
        await db.update_channel_config(chat_obj.id, {"extra_bad_words": merged}, title=chat_obj.title or "")
        return await message.reply_text(f"✅ <b>Saved {len(merged)} channel bad words.</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xpoll":
        parts = [p.strip() for p in arg.split("|") if p.strip()]
        if len(parts) < 3:
            return await message.reply_text("⚠️ Usage: <code>/xpoll Question | Option 1 | Option 2</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        question, options = parts[0][:255], parts[1:11]
        sent = await client.send_poll(chat_obj.id, question=question, options=options, is_anonymous=True)
        return await message.reply_text(f"✅ <b>Channel poll published!</b> Post ID: <code>{sent.id}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "xreset":
        await db.update_channel_config(chat_obj.id, {"extra_features": {}}, title=chat_obj.title or "")
        return await message.reply_text("♻️ <b>All channel extra features reset to OFF.</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^xmgr\|"))
async def extramanager_callback_handler(client: Client, query: CallbackQuery):
    parts = query.data.split(XM_SEP)
    if len(parts) < 3:
        return await query.answer("Invalid callback", show_alert=True)
    action = parts[1]
    try:
        chat_id = int(parts[2])
    except ValueError:
        return await query.answer("Invalid channel ID", show_alert=True)

    cfg = await db.get_channel_config(chat_id)
    if not cfg:
        return await query.answer("Channel not connected!", show_alert=True)
    title = cfg.get("title") or str(chat_id)
    feats, stats, repls, bads = _get_extra_state(cfg)

    if action == "home":
        text, kb = _build_main_panel(chat_id, title, feats)
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer()

    if action == "cat" and len(parts) >= 5:
        ck, page = parts[3], int(parts[4])
        if ck not in EXTRA_CHANNEL_CATEGORIES:
            return await query.answer("Unknown category", show_alert=True)
        text, kb = _build_category_panel(chat_id, title, ck, page, feats)
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer()

    if action == "tog" and len(parts) >= 6:
        ck, page, fk = parts[3], int(parts[4]), parts[5]
        if fk not in EXTRA_FEATURE_INDEX:
            return await query.answer("Unknown feature", show_alert=True)
        feats[fk] = not bool(feats.get(fk, False))
        await db.update_channel_config(chat_id, {"extra_features": feats}, title=title)
        text, kb = _build_category_panel(chat_id, title, ck, page, feats)
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer(f"{fk}: {'ON' if feats[fk] else 'OFF'}")

    if action in ("allon", "alloff") and len(parts) >= 5:
        ck, page = parts[3], int(parts[4])
        if ck not in EXTRA_CHANNEL_CATEGORIES:
            return await query.answer("Unknown category", show_alert=True)
        state = action == "allon"
        for fk, _ in EXTRA_CHANNEL_CATEGORIES[ck][1]:
            feats[fk] = state
        await db.update_channel_config(chat_id, {"extra_features": feats}, title=title)
        text, kb = _build_category_panel(chat_id, title, ck, page, feats)
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer(f"{ck}: ALL {'ON' if state else 'OFF'}")

    if action == "reset":
        await db.update_channel_config(chat_id, {"extra_features": {}}, title=title)
        text, kb = _build_main_panel(chat_id, title, {})
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer("All extra features reset!", show_alert=True)

    if action == "stats":
        enabled_cnt = sum(1 for k in EXTRA_FEATURE_INDEX if feats.get(k))
        txt = (
            f"📊 <b>{to_smallcaps('channel extra analytics')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📢 <b>Channel:</b> {html.escape(title)}\n"
            f"• 🧩 <b>Features Active:</b> <code>{enabled_cnt}/{len(EXTRA_FEATURE_INDEX)}</code>\n"
            f"• 📨 <b>Posts:</b> <code>{int(stats.get('posts', 0))}</code> | ✏️ <b>Edited:</b> <code>{int(stats.get('edited', 0))}</code>\n"
            f"• 🛡️ <b>Blocked:</b> <code>{int(stats.get('blocked', 0))}</code> | 📌 <b>Pinned:</b> <code>{int(stats.get('pinned', 0))}</code>\n"
            f"• 🔥 <b>Reacted:</b> <code>{int(stats.get('reacted', 0))}</code> | 🔄 <b>Synced:</b> <code>{int(stats.get('synced', 0))}</code>\n"
            f"• 📦 <b>Bytes:</b> <code>{_human_size(int(stats.get('bytes', 0)))}</code>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"xmgr{XM_SEP}home{XM_SEP}{chat_id}")]])
        await safe_edit_message(query.message, txt, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer()

    if action == "health":
        try:
            chat_obj = await client.get_chat(chat_id)
            members = getattr(chat_obj, "members_count", 0) or 0
            status_txt = f"🟢 Connected ({members} subscribers)"
        except Exception as e:
            status_txt = f"🔴 Error: {html.escape(str(e))}"
        txt = (
            f"🩺 <b>{to_smallcaps('channel health check')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📢 <b>Channel:</b> {html.escape(title)}\n"
            f"• 📡 <b>Status:</b> {status_txt}\n"
            f"• 🧩 <b>Engine:</b> <code>{len(EXTRA_FEATURE_INDEX)} Channel Features Ready</code>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"xmgr{XM_SEP}home{XM_SEP}{chat_id}")]])
        await safe_edit_message(query.message, txt, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer()

    if action == "export":
        payload = {
            "channel_id": chat_id,
            "channel_title": title,
            "enabled_features": sorted([k for k, v in feats.items() if v]),
            "extra_stats": stats,
            "extra_replace_rules": repls,
            "extra_bad_words": bads,
        }
        buf = io.BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
        buf.name = f"extramanager_{abs(chat_id)}.json"
        await query.message.reply_document(buf, caption=f"📦 <b>Channel Backup</b> (<code>{chat_id}</code>)", parse_mode=enums.ParseMode.HTML)
        return await query.answer("Backup sent!")


@Client.on_message(filters.channel & filters.incoming, group=7)
async def extramanager_channel_post_engine(client: Client, message: Message):
    if not message or not message.chat:
        return
    chat_id = message.chat.id
    cfg = await db.get_channel_config(chat_id)
    if not cfg:
        return
    feats, stats, repls, bads = _get_extra_state(cfg)
    if not any(feats.values()):
        return

    raw_text = message.caption if message.media else (message.text or "")
    lower_text = raw_text.lower()
    media_obj = message.video or message.audio or message.document or message.photo or message.voice or message.animation
    file_size = int(getattr(media_obj, "file_size", 0) or 0) if media_obj else 0
    file_name = str(getattr(media_obj, "file_name", "") or "") if media_obj else ""

    should_delete = False
    if feats.get("block_external_urls") and _URL_RE.search(raw_text):
        should_delete = True
    elif feats.get("block_tme_invites") and _TME_RE.search(raw_text):
        should_delete = True
    elif feats.get("block_forwarded_posts") and (message.forward_date or message.forward_from_chat or message.forward_from):
        should_delete = True
    elif feats.get("block_stickers") and message.sticker:
        should_delete = True
    elif feats.get("block_gifs") and message.animation:
        should_delete = True
    elif feats.get("block_voice_notes") and message.voice:
        should_delete = True
    elif feats.get("block_video_notes") and message.video_note:
        should_delete = True
    elif feats.get("block_polls") and message.poll:
        should_delete = True
    elif feats.get("block_locations") and message.location:
        should_delete = True
    elif feats.get("block_contacts") and message.contact:
        should_delete = True
    elif feats.get("block_dice") and message.dice:
        should_delete = True
    elif feats.get("block_empty_captions") and message.media and not raw_text.strip():
        should_delete = True
    elif feats.get("block_non_media_text") and not message.media and raw_text.strip():
        should_delete = True
    elif feats.get("block_oversized_files") and file_size > 2 * 1024 * 1024 * 1024:
        should_delete = True
    elif feats.get("block_undersized_files") and 0 < file_size < 1024:
        should_delete = True
    elif feats.get("block_bad_words") and bads and any(w in lower_text for w in bads):
        should_delete = True
    elif feats.get("block_duplicate_posts"):
        sig = hashlib.md5(f"{raw_text}|{file_name}|{file_size}".encode("utf-8", errors="ignore")).hexdigest()
        bucket = _RECENT_POST_HASHES.setdefault(chat_id, {})
        now_ts = time.time()
        for k, ts in list(bucket.items()):
            if now_ts - ts > 3600:
                bucket.pop(k, None)
        if sig in bucket:
            should_delete = True
        else:
            bucket[sig] = now_ts

    if should_delete:
        try:
            await message.delete()
            stats["blocked"] = int(stats.get("blocked", 0)) + 1
            await db.update_channel_config(chat_id, {"extra_stats": stats}, title=message.chat.title or "")
        except Exception:
            pass
        return

    stats["posts"] = int(stats.get("posts", 0)) + 1
    if message.video:
        stats["videos"] = int(stats.get("videos", 0)) + 1
    elif message.audio:
        stats["audios"] = int(stats.get("audios", 0)) + 1
    elif message.document:
        stats["docs"] = int(stats.get("docs", 0)) + 1
    elif message.photo:
        stats["photos"] = int(stats.get("photos", 0)) + 1
    if file_size > 0:
        stats["bytes"] = int(stats.get("bytes", 0)) + file_size

    new_text = raw_text
    for old_s, new_s in repls.items():
        if old_s and old_s in new_text:
            new_text = new_text.replace(old_s, new_s)
    if feats.get("strip_links"):
        new_text = _URL_RE.sub("", new_text)
    if feats.get("strip_usernames"):
        new_text = _USER_RE.sub("", new_text)
    if feats.get("strip_hashtags"):
        new_text = _HASH_RE.sub("", new_text)
    if feats.get("replace_underscores"):
        new_text = new_text.replace("_", " ")
    if feats.get("clean_newlines"):
        new_text = _MULTI_NL_RE.sub("\n\n", new_text)
    if feats.get("trim_whitespace"):
        new_text = new_text.strip()
    if feats.get("smallcaps_caption") and new_text:
        new_text = to_smallcaps(new_text)

    extra_lines: List[str] = []
    if feats.get("auto_file_size_tag") and file_size > 0:
        extra_lines.append(f"📦 Size: {_human_size(file_size)}")
    if feats.get("auto_resolution_tag") and getattr(message.video, "height", 0):
        extra_lines.append(f"🎬 Resolution: {message.video.width}x{message.video.height}")
    if feats.get("auto_duration_tag") and getattr(media_obj, "duration", 0):
        extra_lines.append(f"⏱️ Duration: {get_readable_time(int(media_obj.duration))}")
    if feats.get("add_post_number"):
        extra_lines.append(f"🔢 Post #{stats['posts']}")
    if feats.get("brand_channel_handle") and message.chat.username:
        extra_lines.append(f"📢 @{message.chat.username}")
    if feats.get("brand_channel_title") and message.chat.title:
        extra_lines.append(f"✦ {message.chat.title}")
    if feats.get("auto_comment_prompt"):
        extra_lines.append("💬 Drop your feedback in comments!")

    if extra_lines:
        sep = "\n━━━━━━━━━━━━━━━━━━━━━━\n" if feats.get("add_separator_line") else "\n\n"
        new_text = f"{new_text}{sep}" + "\n".join(extra_lines) if new_text else "\n".join(extra_lines)

    if new_text != raw_text and new_text.strip():
        try:
            if feats.get("auto_delay_5s"):
                await asyncio.sleep(5)
            if message.media:
                await message.edit_caption(new_text[:1024])
            else:
                await message.edit_text(new_text[:4096], disable_web_page_preview=bool(feats.get("disable_web_preview")))
            stats["edited"] = int(stats.get("edited", 0)) + 1
        except Exception:
            pass

    for rkey, emoji in _REACTION_MAP.items():
        if feats.get(rkey):
            try:
                await client.send_reaction(chat_id, message.id, emoji)
                stats["reacted"] = int(stats.get("reacted", 0)) + 1
            except Exception:
                pass
            break

    should_pin = (
        (feats.get("auto_pin_video") and message.video)
        or (feats.get("auto_pin_audio") and message.audio)
        or (feats.get("auto_pin_document") and message.document)
        or (feats.get("auto_pin_photo") and message.photo)
        or (feats.get("auto_pin_poll") and message.poll)
    )
    if should_pin:
        try:
            await client.pin_chat_message(chat_id, message.id, disable_notification=True)
            stats["pinned"] = int(stats.get("pinned", 0)) + 1
        except Exception:
            pass

    if (feats.get("auto_forward_backup") or feats.get("sync_to_log_channel")) and LOG_CHANNEL:
        try:
            await message.copy(LOG_CHANNEL, protect_content=bool(feats.get("protect_content_copy")))
            stats["synced"] = int(stats.get("synced", 0)) + 1
        except Exception:
            pass

    for tkey, sec in _TTL_SECONDS_MAP.items():
        if feats.get(tkey):
            asyncio.create_task(_schedule_delete(client, chat_id, message.id, sec))
            break

    await db.update_channel_config(chat_id, {"extra_stats": stats}, title=message.chat.title or "")
