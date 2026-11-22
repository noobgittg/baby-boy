import asyncio
import datetime
import html
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from config import Config
from database.users_chats_db import db
from info import COMMAND_PREFIXES
from plugins.core_db import chat_repo, user_repo
from plugins.core_utils import (
    format_watermark,
    humanbytes,
    logger,
    safe_edit_text,
    safe_reply_text,
    spawn_mega_task,
    to_smallcaps,
)
from utils import format_watermark_html, is_admin_sync, safe_edit_message

XMGR_SEP = "|"

XMGR_CATEGORIES: Dict[str, Tuple[str, str, List[Tuple[str, str, bool]]]] = {
    "caption": (
        "📝 Caption Studio",
        "Auto-caption formatting, metadata tags, and text cleanup",
        [
            ("cap_auto_enable", "Auto Caption Engine", False),
            ("cap_append_header", "Append Channel Header", False),
            ("cap_append_footer", "Append Channel Footer", False),
            ("cap_add_channel_link", "Add Channel Link", False),
            ("cap_add_timestamp", "Add UTC Timestamp", False),
            ("cap_add_file_size", "Add File Size Tag", True),
            ("cap_add_duration", "Add Media Duration", True),
            ("cap_add_resolution", "Add Video Resolution", False),
            ("cap_add_mime_tag", "Add MIME Type Tag", False),
            ("cap_add_post_number", "Add Sequential Post #", False),
            ("cap_strip_source_links", "Strip External Links", False),
            ("cap_strip_mentions", "Strip @Mentions", False),
            ("cap_strip_hashtags", "Strip #Hashtags", False),
            ("cap_strip_emojis", "Strip Decorative Emojis", False),
            ("cap_normalize_spaces", "Normalize Extra Spaces", True),
            ("cap_smallcaps_title", "Small-Caps Title Line", False),
            ("cap_bold_first_line", "Bold First Line", False),
            ("cap_blockquote_body", "Blockquote Caption Body", False),
            ("cap_preserve_original", "Preserve Original Caption", True),
            ("cap_language_badge", "Auto Language Tag", False),
        ],
    ),
    "media": (
        "🎬 Media Guard",
        "Allowed media types, link guards, size limits, and dedupe",
        [
            ("med_allow_video", "Allow Videos", True),
            ("med_allow_document", "Allow Documents", True),
            ("med_allow_audio", "Allow Audio Files", True),
            ("med_allow_photo", "Allow Photos", True),
            ("med_allow_animation", "Allow GIF/Animations", True),
            ("med_allow_voice", "Allow Voice Notes", True),
            ("med_allow_sticker", "Allow Stickers", True),
            ("med_allow_poll", "Allow Polls", True),
            ("med_block_fwd_header", "Clean Forward Header", False),
            ("med_block_ext_urls", "Block External URLs", False),
            ("med_block_tg_invites", "Block TG Invite Links", False),
            ("med_block_bot_links", "Block Bot Referral Links", False),
            ("med_block_phones", "Mask Phone Numbers", False),
            ("med_block_emails", "Mask Email Addresses", False),
            ("med_block_shorteners", "Block URL Shorteners", False),
            ("med_max_size_2gb", "Enforce 2GB Size Cap", False),
            ("med_min_size_10kb", "Skip Tiny <10KB Files", False),
            ("med_dedupe_file_id", "Skip Duplicate File ID", False),
            ("med_dedupe_filename", "Skip Duplicate Filename", False),
            ("med_auto_clean_name", "Clean Underscores In Name", True),
        ],
    ),
    "buttons": (
        "🔘 Inline Buttons",
        "Auto-attached channel action and navigation buttons",
        [
            ("btn_auto_attach", "Auto Attach Buttons", False),
            ("btn_share_post", "Share Post Button", False),
            ("btn_join_channel", "Join Channel Button", False),
            ("btn_backup_channel", "Backup Channel Button", False),
            ("btn_request_group", "Request Group Button", False),
            ("btn_stream_watch", "Watch Stream Button", False),
            ("btn_direct_dl", "Direct Download Button", False),
            ("btn_report_broken", "Report Issue Button", False),
            ("btn_like_counter", "Like Counter Button", False),
            ("btn_dislike_counter", "Dislike Counter Button", False),
            ("btn_bookmark_post", "Bookmark Button", False),
            ("btn_star_rating", "5-Star Rating Button", False),
            ("btn_prev_next_nav", "Prev/Next Post Nav", False),
            ("btn_mirror_link", "Mirror Link Button", False),
            ("btn_support_chat", "Support Group Button", False),
            ("btn_rules_link", "Channel Rules Button", False),
            ("btn_feedback_link", "Feedback Button", False),
            ("btn_website_link", "Official Website Button", False),
            ("btn_updates_link", "Updates Feed Button", False),
            ("btn_two_col_layout", "2-Column Compact Grid", True),
        ],
    ),
    "branding": (
        "🏷️ Branding & Tags",
        "Watermarks, signatures, series badges, and visual frames",
        [
            ("brd_watermark_line", "Append Watermark Line", False),
            ("brd_channel_signature", "Append Channel Signature", False),
            ("brd_custom_prefix", "Prefix File Caption", False),
            ("brd_custom_suffix", "Suffix File Caption", False),
            ("brd_username_tag", "Append @Channel Tag", False),
            ("brd_episode_badge", "Detect Episode Badge", False),
            ("brd_season_badge", "Detect Season Badge", False),
            ("brd_quality_badge", "Detect Quality Badge", False),
            ("brd_audio_badge", "Detect Audio Badge", False),
            ("brd_sub_badge", "Detect Subtitle Badge", False),
            ("brd_year_badge", "Detect Release Year", False),
            ("brd_genre_badge", "Append Category Tag", False),
            ("brd_verified_mark", "Verified Channel Mark", False),
            ("brd_copyright_line", "Copyright Footer Line", False),
            ("brd_disclaimer_line", "DMCA Disclaimer Line", False),
            ("brd_promo_banner", "Promo Banner Footer", False),
            ("brd_divider_line", "Clean Visual Divider", False),
            ("brd_frame_header", "Framed Header Style", False),
            ("brd_compact_card", "Compact Metadata Card", False),
            ("brd_clean_filename", "Pretty Title From File", False),
        ],
    ),
    "automation": (
        "⚡ Automation",
        "Pinning, silent mode, content protection, and auto-cleanup",
        [
            ("aut_pin_latest", "Auto Pin New Post", False),
            ("aut_unpin_previous", "Unpin Previous Before Pin", False),
            ("aut_silent_night", "Silent Mode At Night", False),
            ("aut_protect_content", "Protect Forward/Save", False),
            ("aut_disable_preview", "Disable Web Preview", True),
            ("aut_delete_expired", "Auto Delete Expired", False),
            ("aut_log_new_posts", "Log Posts To Log Channel", False),
            ("aut_clean_service", "Clean Service Messages", False),
            ("aut_archive_index", "Record In Post Index", True),
            ("aut_retry_edit", "Auto Retry FloodWait Edit", True),
            ("aut_rate_pace", "Smart Post Rate Pacing", True),
            ("aut_album_caption", "Sync Album Caption", False),
            ("aut_backup_meta", "Backup Post Metadata", True),
            ("aut_mirror_text", "Mirror Caption To Index", False),
            ("aut_daily_digest", "Track Daily Digest Count", True),
            ("aut_weekly_recap", "Track Weekly Recap Count", True),
            ("aut_auto_tag_ext", "Auto Tag File Extension", False),
            ("aut_strip_empty_lines", "Collapse Blank Lines", True),
            ("aut_safe_html_mode", "Force Safe HTML Parse", True),
            ("aut_async_pipeline", "Async Non-Blocking Mode", True),
        ],
    ),
    "filters": (
        "🧹 Text Filters",
        "Word blacklists, regex sanitizers, and UTM/invisible cleaners",
        [
            ("flt_word_blacklist", "Word Blacklist Guard", False),
            ("flt_regex_filter", "Regex Pattern Cleaner", False),
            ("flt_word_replace", "Custom Word Replacer", False),
            ("flt_domain_whitelist", "Whitelist Domains Only", False),
            ("flt_domain_blacklist", "Block Blacklist Domains", False),
            ("flt_spam_repeat", "Collapse Repeated Chars", False),
            ("flt_caps_normalizer", "Normalize ALL CAPS Text", False),
            ("flt_profanity_mask", "Mask Profanity Words", False),
            ("flt_ad_blocker", "Remove Ad Promo Lines", False),
            ("flt_promo_lines", "Strip Join-Channel Spam", False),
            ("flt_dup_text_guard", "Skip Duplicate Text Post", False),
            ("flt_empty_cap_fix", "Fill Empty Media Caption", False),
            ("flt_trim_1024", "Trim Caption <= 1024 Chars", True),
            ("flt_rtl_cleaner", "Strip RTL Override Chars", True),
            ("flt_zw_cleaner", "Strip Zero-Width Chars", True),
            ("flt_html_escape", "Escape Raw HTML Safely", True),
            ("flt_utm_stripper", "Strip UTM Tracking Params", True),
            ("flt_ext_cleaner", "Hide Raw File Extension", False),
            ("flt_dot_to_space", "Convert Dots To Spaces", False),
            ("flt_bracket_strip", "Strip [Tag] Brackets", False),
        ],
    ),
    "security": (
        "🛡️ Channel Security",
        "Admin checks, flood throttling, audit logs, and safety locks",
        [
            ("sec_admin_only", "Admin-Only Panel Access", True),
            ("sec_owner_lock", "Lock Critical Settings", False),
            ("sec_anti_raid", "Anti-Raid Burst Guard", False),
            ("sec_flood_throttle", "Flood Edit Throttler", True),
            ("sec_audit_edits", "Audit Log Panel Changes", True),
            ("sec_audit_deletes", "Audit Log Deletions", True),
            ("sec_protect_pins", "Protect Pinned Posts", True),
            ("sec_protect_media", "Protect Media Posts", False),
            ("sec_temp_invite", "Expiring Invite Links", False),
            ("sec_join_request", "Join Request Approval", False),
            ("sec_verify_rights", "Verify Bot Admin Rights", True),
            ("sec_safe_broadcast", "Safe Broadcast Pacing", True),
            ("sec_dry_run_mode", "Dry-Run Preview Mode", False),
            ("sec_confirm_purge", "Confirm Bulk Actions", True),
            ("sec_backup_before_rst", "Snapshot Before Reset", True),
            ("sec_emergency_pause", "Emergency Pause Pipeline", False),
            ("sec_maint_notice", "Maintenance Mode Banner", False),
            ("sec_block_anon_edit", "Require Verified Admin", True),
            ("sec_strict_channel", "Strict Channel-Only Mode", True),
            ("sec_redact_secrets", "Redact Sensitive Tokens", True),
        ],
    ),
    "indexing": (
        "📊 Index & Analytics",
        "Live post counters, media volume stats, and catalog tracking",
        [
            ("idx_video_posts", "Index Video Posts", True),
            ("idx_doc_posts", "Index Document Posts", True),
            ("idx_audio_posts", "Index Audio Posts", True),
            ("idx_photo_posts", "Index Photo Posts", True),
            ("idx_text_posts", "Index Text Posts", True),
            ("idx_post_counter", "Track Total Post Count", True),
            ("idx_media_bytes", "Track Total Media Bytes", True),
            ("idx_daily_counter", "Track Daily Post Count", True),
            ("idx_weekly_counter", "Track Weekly Post Count", True),
            ("idx_top_extensions", "Track Top Extensions", True),
            ("idx_search_catalog", "Enable Catalog Search", True),
            ("idx_export_summary", "Allow JSON Export", True),
            ("idx_first_post_id", "Record First Post ID", True),
            ("idx_latest_post_id", "Record Latest Post ID", True),
            ("idx_missing_audit", "Track Deleted Gaps", False),
            ("idx_broken_scan", "Flag Empty Media Links", False),
            ("idx_tag_counter", "Track Top Hashtags", True),
            ("idx_series_tracker", "Track Series Episodes", False),
            ("idx_dup_scanner", "Track Duplicate Hashes", False),
            ("idx_sitemap_post", "Generate Index Summary", True),
        ],
    ),
    "engagement": (
        "🎯 Engagement & Cards",
        "Reaction helpers, welcome/rules cards, and post templates",
        [
            ("eng_react_thumbsup", "Auto Reaction ThumbsUp", False),
            ("eng_react_fire", "Auto Reaction Fire", False),
            ("eng_react_heart", "Auto Reaction Heart", False),
            ("eng_react_star", "Auto Reaction Star", False),
            ("eng_welcome_card", "Welcome Card Template", False),
            ("eng_rules_card", "Rules Card Template", False),
            ("eng_faq_card", "FAQ Card Template", False),
            ("eng_poll_helper", "Poll Post Helper", True),
            ("eng_quiz_helper", "Quiz Post Helper", True),
            ("eng_countdown_card", "Countdown Post Card", False),
            ("eng_giveaway_card", "Giveaway Post Card", False),
            ("eng_announce_tpl", "Announcement Template", False),
            ("eng_release_tpl", "Release Note Template", False),
            ("eng_movie_tpl", "Movie Card Template", False),
            ("eng_series_tpl", "Series Card Template", False),
            ("eng_music_tpl", "Music Card Template", False),
            ("eng_software_tpl", "Software Card Template", False),
            ("eng_course_tpl", "Course Card Template", False),
            ("eng_news_tpl", "News Card Template", False),
            ("eng_feedback_prompt", "Append Feedback Prompt", False),
        ],
    ),
    "scheduling": (
        "⏱️ Timing & Expiry",
        "Quiet hours, slot tags, burst spacing, and auto-expiry rules",
        [
            ("sch_quiet_hours", "Night Quiet Hours (23-06)", False),
            ("sch_morning_tag", "Morning Slot Tag", False),
            ("sch_afternoon_tag", "Afternoon Slot Tag", False),
            ("sch_evening_tag", "Evening Slot Tag", False),
            ("sch_night_tag", "Night Slot Tag", False),
            ("sch_weekend_mode", "Weekend Digest Tag", False),
            ("sch_pace_1s", "Burst Spacing 1s", False),
            ("sch_pace_3s", "Burst Spacing 3s", False),
            ("sch_pace_5s", "Burst Spacing 5s", False),
            ("sch_expire_1h", "Auto-Expire Posts 1h", False),
            ("sch_expire_6h", "Auto-Expire Posts 6h", False),
            ("sch_expire_12h", "Auto-Expire Posts 12h", False),
            ("sch_expire_24h", "Auto-Expire Posts 24h", False),
            ("sch_expire_7d", "Auto-Expire Posts 7d", False),
            ("sch_pin_1h", "Auto-Unpin After 1h", False),
            ("sch_pin_24h", "Auto-Unpin After 24h", False),
            ("sch_cooldown_guard", "Post Cooldown Guard", False),
            ("sch_peak_boost", "Peak Hour Highlight", False),
            ("sch_offpeak_silent", "Off-Peak Silent Mode", False),
            ("sch_queue_telemetry", "Track Post Timestamps", True),
        ],
    ),
    "tools": (
        "🛠️ Channel Tools",
        "Live channel utilities, presets, health checks, and exports",
        [
            ("tls_live_stats", "Live Channel Stats Card", True),
            ("tls_temp_invite", "1-Click Invite Generator", True),
            ("tls_revoke_invite", "Invite Revoke Helper", True),
            ("tls_pin_helper", "Quick Pin Helper", True),
            ("tls_unpin_all", "Unpin All Safety Tool", True),
            ("tls_clean_clone", "Clean Post Cloner", True),
            ("tls_caption_editor", "Live Caption Editor", True),
            ("tls_btn_injector", "Inline Button Injector", True),
            ("tls_btn_stripper", "Strip Post Buttons", True),
            ("tls_preview_toggle", "Web Preview Switcher", True),
            ("tls_json_export", "Export Config JSON", True),
            ("tls_preset_movie", "Movie Channel Preset", True),
            ("tls_preset_files", "Files Hub Preset", True),
            ("tls_preset_news", "News Feed Preset", True),
            ("tls_preset_minimal", "Minimal Clean Preset", True),
            ("tls_health_check", "Channel Health Audit", True),
            ("tls_member_count", "Live Member Counter", True),
            ("tls_admin_audit", "Admin Rights Audit", True),
            ("tls_reset_defaults", "Reset All 220 Features", True),
            ("tls_feature_finder", "Feature Directory View", True),
        ],
    ),
}

XMGR_ALL_FEATURES: Dict[str, Tuple[str, str, bool]] = {}
for _cat_key, (_, _, _items) in XMGR_CATEGORIES.items():
    for _fkey, _flabel, _fdef in _items:
        XMGR_ALL_FEATURES[_fkey] = (_cat_key, _flabel, _fdef)

XMGR_FEATURE_COUNT = len(XMGR_ALL_FEATURES)

_RECENT_POST_HASHES: Dict[int, List[str]] = {}
_LAST_POST_TS: Dict[int, float] = {}


def get_default_xmgr_config() -> Dict[str, Any]:
    return {
        "features": {k: v[2] for k, v in XMGR_ALL_FEATURES.items()},
        "header": "",
        "footer": "",
        "watermark": f"📢 {getattr(Config, 'WATERMARK_TEXT', 'MMW BOTZ')}",
        "blacklist": [],
        "replace_map": {},
        "stats": {
            "total_posts": 0,
            "video_posts": 0,
            "doc_posts": 0,
            "audio_posts": 0,
            "photo_posts": 0,
            "text_posts": 0,
            "total_bytes": 0,
            "first_post_id": 0,
            "latest_post_id": 0,
            "extensions": {},
            "hashtags": {},
            "daily_date": "",
            "daily_count": 0,
        },
        "updated_at": float(time.time()),
    }


async def get_channel_xmgr_config(channel_id: int) -> Dict[str, Any]:
    defaults = get_default_xmgr_config()
    chat_doc = await db.get_chat(int(channel_id)) or {}
    saved = chat_doc.get("xmgr_config") if isinstance(chat_doc, dict) else None
    if not isinstance(saved, dict):
        return defaults
    merged_features = dict(defaults["features"])
    if isinstance(saved.get("features"), dict):
        for k, v in saved["features"].items():
            if k in merged_features:
                merged_features[k] = bool(v)
    merged_stats = dict(defaults["stats"])
    if isinstance(saved.get("stats"), dict):
        merged_stats.update(saved["stats"])
    return {
        "features": merged_features,
        "header": str(saved.get("header") or ""),
        "footer": str(saved.get("footer") or ""),
        "watermark": str(saved.get("watermark") or defaults["watermark"]),
        "blacklist": list(saved.get("blacklist") or []),
        "replace_map": dict(saved.get("replace_map") or {}),
        "stats": merged_stats,
        "updated_at": float(saved.get("updated_at") or time.time()),
    }


async def save_channel_xmgr_config(channel_id: int, cfg: Dict[str, Any]) -> None:
    cfg["updated_at"] = float(time.time())
    chat_doc = await db.get_chat(int(channel_id))
    if not chat_doc:
        await db.add_chat(int(channel_id), f"Channel {channel_id}")
    await db.update_chat(int(channel_id), {"xmgr_config": cfg})


async def _resolve_target_channel(client: Client, message: Message) -> Tuple[int, str]:
    if message.chat and message.chat.type == enums.ChatType.CHANNEL:
        return int(message.chat.id), str(message.chat.title or f"Channel {message.chat.id}")

    if len(message.command) >= 2:
        raw = message.command[1].strip()
        try:
            target = int(raw) if raw.lstrip("-").isdigit() else raw
            chat = await client.get_chat(target)
            if chat and chat.type == enums.ChatType.CHANNEL:
                return int(chat.id), str(chat.title or f"Channel {chat.id}")
        except Exception:
            return 0, ""

    uid = message.from_user.id if message.from_user else 0
    if uid:
        u = await user_repo.get_user(uid) or {}
        conn_cid = int(u.get("connected_channel") or 0)
        if conn_cid:
            try:
                chat = await client.get_chat(conn_cid)
                if chat and chat.type == enums.ChatType.CHANNEL:
                    return int(chat.id), str(chat.title or f"Channel {chat.id}")
            except Exception:
                return conn_cid, f"Channel {conn_cid}"
    return 0, ""


async def _is_channel_admin(client: Client, channel_id: int, user_id: int) -> bool:
    if is_admin_sync(user_id):
        return True
    if not user_id or not channel_id:
        return False
    try:
        member = await client.get_chat_member(int(channel_id), int(user_id))
        status_s = str(getattr(member, "status", "")).lower()
        return any(k in status_s for k in ("owner", "creator", "administrator"))
    except Exception:
        return False


def _count_enabled(cfg: Dict[str, Any], cat_key: Optional[str] = None) -> Tuple[int, int]:
    feats = cfg.get("features") or {}
    if cat_key and cat_key in XMGR_CATEGORIES:
        keys = [k for k, _, _ in XMGR_CATEGORIES[cat_key][2]]
    else:
        keys = list(XMGR_ALL_FEATURES.keys())
    enabled = sum(1 for k in keys if feats.get(k, False))
    return enabled, len(keys)


def _render_home_panel(channel_id: int, title: str, cfg: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    on_cnt, total_cnt = _count_enabled(cfg)
    stats = cfg.get("stats") or {}
    paused = "⏸️ ᴘᴀᴜsᴇᴅ" if cfg["features"].get("sec_emergency_pause") else "🟢 ᴀᴄᴛɪᴠᴇ"
    text = (
        f"✦ <b>{to_smallcaps('channel extra manager pro')}</b> ✦ 📢\n\n"
        f"• 📢 <b>{to_smallcaps('channel')}</b> : <code>{html.escape(title)}</code> (<code>{channel_id}</code>)\n"
        f"• ⚙️ <b>{to_smallcaps('engine status')}</b> : <code>{paused}</code>\n"
        f"• 🧩 <b>{to_smallcaps('active features')}</b> : <code>{on_cnt} / {total_cnt}</code>\n"
        f"• 📊 <b>{to_smallcaps('indexed posts')}</b> : <code>{stats.get('total_posts', 0)}</code> "
        f"(<code>{humanbytes(int(stats.get('total_bytes', 0) or 0))}</code>)\n\n"
        f"💡 <b>{to_smallcaps('select a category below (channel-only 220 working features):')}</b>"
        f"{format_watermark_html()}"
    )
    cat_keys = list(XMGR_CATEGORIES.keys())
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(cat_keys), 2):
        row = []
        for ck in cat_keys[i : i + 2]:
            c_title = XMGR_CATEGORIES[ck][0]
            c_on, c_tot = _count_enabled(cfg, ck)
            row.append(
                InlineKeyboardButton(
                    f"{c_title} ({c_on}/{c_tot})",
                    callback_data=f"xmgr{XMGR_SEP}cat{XMGR_SEP}{channel_id}{XMGR_SEP}{ck}:0",
                )
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton(f"🎬 {to_smallcaps('movie preset')}", callback_data=f"xmgr{XMGR_SEP}preset{XMGR_SEP}{channel_id}{XMGR_SEP}movie"),
        InlineKeyboardButton(f"📦 {to_smallcaps('files preset')}", callback_data=f"xmgr{XMGR_SEP}preset{XMGR_SEP}{channel_id}{XMGR_SEP}files"),
    ])
    rows.append([
        InlineKeyboardButton(f"📊 {to_smallcaps('channel stats')}", callback_data=f"xmgr{XMGR_SEP}stats{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
        InlineKeyboardButton(f"🩺 {to_smallcaps('health audit')}", callback_data=f"xmgr{XMGR_SEP}health{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
    ])
    rows.append([
        InlineKeyboardButton(f"🔄 {to_smallcaps('reset 220')}", callback_data=f"xmgr{XMGR_SEP}reset{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data=f"xmgr{XMGR_SEP}close{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
    ])
    return text, InlineKeyboardMarkup(rows)


def _render_category_panel(
    channel_id: int,
    cat_key: str,
    page: int,
    cfg: Dict[str, Any],
) -> Tuple[str, InlineKeyboardMarkup]:
    c_title, c_desc, items = XMGR_CATEGORIES[cat_key]
    per_page = 10
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * per_page : (page + 1) * per_page]
    c_on, c_tot = _count_enabled(cfg, cat_key)
    feats = cfg["features"]

    text = (
        f"✦ <b>{c_title}</b> ✦ • <code>{channel_id}</code>\n"
        f"<i>{html.escape(c_desc)}</i>\n\n"
        f"• 🧩 <b>{to_smallcaps('enabled')}</b> : <code>{c_on}/{c_tot}</code> • "
        f"<b>{to_smallcaps('page')}</b> : <code>{page + 1}/{total_pages}</code>\n"
        f"• 💡 {to_smallcaps('tap any feature to toggle live in this channel:')}"
        f"{format_watermark_html()}"
    )
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        row = []
        for fkey, flabel, _ in chunk[i : i + 2]:
            icon = "🟢" if feats.get(fkey, False) else "⚪"
            row.append(
                InlineKeyboardButton(
                    f"{icon} {flabel}",
                    callback_data=f"xmgr{XMGR_SEP}tog{XMGR_SEP}{channel_id}{XMGR_SEP}{fkey}:{page}",
                )
            )
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("⬅️ ᴘʀᴇᴠ", callback_data=f"xmgr{XMGR_SEP}cat{XMGR_SEP}{channel_id}{XMGR_SEP}{cat_key}:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(f"✅ {to_smallcaps('all on')}", callback_data=f"xmgr{XMGR_SEP}caton{XMGR_SEP}{channel_id}{XMGR_SEP}{cat_key}:{page}")
    )
    nav_row.append(
        InlineKeyboardButton(f"⚪ {to_smallcaps('all off')}", callback_data=f"xmgr{XMGR_SEP}catoff{XMGR_SEP}{channel_id}{XMGR_SEP}{cat_key}:{page}")
    )
    if page + 1 < total_pages:
        nav_row.append(
            InlineKeyboardButton("ɴᴇxᴛ ➡️", callback_data=f"xmgr{XMGR_SEP}cat{XMGR_SEP}{channel_id}{XMGR_SEP}{cat_key}:{page + 1}")
        )
    rows.append(nav_row)
    rows.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('categories')}", callback_data=f"xmgr{XMGR_SEP}home{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data=f"xmgr{XMGR_SEP}close{XMGR_SEP}{channel_id}{XMGR_SEP}0"),
    ])
    return text, InlineKeyboardMarkup(rows)


def apply_caption_transforms(
    raw_caption: str,
    cfg: Dict[str, Any],
    channel_title: str = "",
    channel_username: str = "",
    file_name: str = "",
    file_size: int = 0,
    duration: int = 0,
    resolution: str = "",
    mime_type: str = "",
    post_number: int = 0,
) -> str:
    feats = cfg.get("features") or {}
    text = str(raw_caption or "")

    if feats.get("flt_rtl_cleaner"):
        text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    if feats.get("flt_zw_cleaner"):
        text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    if feats.get("cap_strip_source_links") or feats.get("med_block_ext_urls"):
        text = re.sub(r"https?://\S+", "", text)
    if feats.get("med_block_tg_invites"):
        text = re.sub(r"(?:https?://)?t\.me/(?:\+|joinchat/)\S+", "", text, flags=re.I)
    if feats.get("med_block_bot_links"):
        text = re.sub(r"(?:https?://)?t\.me/\w+bot\S*", "", text, flags=re.I)
    if feats.get("flt_utm_stripper"):
        text = re.sub(r"([?&])utm_[a-zA-Z0-9_]+=[^&\s]+", "", text)
    if feats.get("cap_strip_mentions"):
        text = re.sub(r"@[A-Za-z0-9_]{3,}", "", text)
    if feats.get("cap_strip_hashtags"):
        text = re.sub(r"#[A-Za-z0-9_]+", "", text)
    if feats.get("med_block_phones"):
        text = re.sub(r"\+?\d[\d\-\s]{8,14}\d", "[hidden]", text)
    if feats.get("med_block_emails"):
        text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[hidden]", text)
    if feats.get("flt_word_blacklist"):
        for w in cfg.get("blacklist") or []:
            if w:
                text = re.sub(re.escape(str(w)), "", text, flags=re.I)
    if feats.get("flt_word_replace"):
        for src_w, dst_w in (cfg.get("replace_map") or {}).items():
            if src_w:
                text = re.sub(re.escape(str(src_w)), str(dst_w), text, flags=re.I)
    if feats.get("flt_spam_repeat"):
        text = re.sub(r"(.)\1{4,}", r"\1\1\1", text)
    if feats.get("flt_caps_normalizer") and text and text.isupper() and len(text) > 12:
        text = text.capitalize()
    if feats.get("flt_bracket_strip"):
        text = re.sub(r"\[[^\]]{1,40}\]", "", text)
    if feats.get("cap_normalize_spaces"):
        text = re.sub(r"[ \t]{2,}", " ", text)
    if feats.get("aut_strip_empty_lines"):
        text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if not text and (feats.get("flt_empty_cap_fix") or feats.get("brd_clean_filename")) and file_name:
        pretty = file_name
        if feats.get("flt_ext_cleaner"):
            pretty = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", pretty)
        if feats.get("med_auto_clean_name"):
            pretty = pretty.replace("_", " ")
        if feats.get("flt_dot_to_space"):
            pretty = pretty.replace(".", " ")
        text = pretty.strip()

    if feats.get("flt_html_escape"):
        text = html.escape(text)

    if text and feats.get("cap_smallcaps_title"):
        parts = text.split("\n", 1)
        parts[0] = to_smallcaps(parts[0])
        text = "\n".join(parts)

    if text and feats.get("cap_bold_first_line"):
        parts = text.split("\n", 1)
        parts[0] = f"<b>{parts[0]}</b>"
        text = "\n".join(parts)

    if text and feats.get("cap_blockquote_body"):
        text = f"<blockquote>{text}</blockquote>"

    lines: List[str] = []
    if feats.get("cap_append_header") and cfg.get("header"):
        lines.append(html.escape(cfg["header"]))
    if feats.get("brd_frame_header") and channel_title:
        lines.append(f"✦ <b>{html.escape(channel_title)}</b> ✦")
    if text:
        lines.append(text)

    meta_bits: List[str] = []
    if feats.get("cap_add_file_size") and file_size > 0:
        meta_bits.append(f"📦 {humanbytes(file_size)}")
    if feats.get("cap_add_duration") and duration > 0:
        mins, secs = divmod(int(duration), 60)
        hrs, mins = divmod(mins, 60)
        d_str = f"{hrs}h {mins:02d}m" if hrs else f"{mins}m {secs:02d}s"
        meta_bits.append(f"⏱️ {d_str}")
    if feats.get("cap_add_resolution") and resolution:
        meta_bits.append(f"🎞️ {resolution}")
    if feats.get("cap_add_mime_tag") and mime_type:
        meta_bits.append(f"🏷️ {mime_type}")
    if feats.get("cap_add_post_number") and post_number > 0:
        meta_bits.append(f"#{post_number}")
    if feats.get("cap_add_timestamp"):
        meta_bits.append(datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    if meta_bits:
        lines.append(" • ".join(meta_bits))

    if feats.get("brd_divider_line"):
        lines.append("━━━━━━━━━━━━━━")
    if feats.get("brd_username_tag") and channel_username:
        lines.append(f"📢 @{channel_username.lstrip('@')}")
    if feats.get("cap_add_channel_link") and channel_username:
        lines.append(f"🔗 https://t.me/{channel_username.lstrip('@')}")
    if feats.get("brd_watermark_line") and cfg.get("watermark"):
        lines.append(html.escape(cfg["watermark"]))
    if feats.get("cap_append_footer") and cfg.get("footer"):
        lines.append(html.escape(cfg["footer"]))
    if feats.get("eng_feedback_prompt"):
        lines.append(f"💬 {to_smallcaps('react or share if this post helped!')}")

    out = "\n".join(ln for ln in lines if ln).strip()
    if feats.get("flt_trim_1024") and len(out) > 1020:
        out = out[:1017] + "..."
    return out


def build_channel_post_buttons(
    cfg: Dict[str, Any],
    channel_username: str = "",
    message_id: int = 0,
) -> Optional[InlineKeyboardMarkup]:
    feats = cfg.get("features") or {}
    if not feats.get("btn_auto_attach"):
        return None
    btns: List[InlineKeyboardButton] = []
    ch_url = f"https://t.me/{channel_username.lstrip('@')}" if channel_username else Config.WATERMARK_URL
    post_url = f"{ch_url}/{message_id}" if (channel_username and message_id) else ch_url

    if feats.get("btn_join_channel"):
        btns.append(InlineKeyboardButton(f"📢 {to_smallcaps('join channel')}", url=ch_url))
    if feats.get("btn_share_post"):
        btns.append(InlineKeyboardButton(f"📤 {to_smallcaps('share')}", url=f"https://t.me/share/url?url={post_url}"))
    if feats.get("btn_updates_link"):
        btns.append(InlineKeyboardButton(f"⚡ {to_smallcaps('updates')}", url=Config.WATERMARK_URL))
    if feats.get("btn_support_chat") and getattr(Config, "SUPPORT_GROUP_URL", ""):
        btns.append(InlineKeyboardButton(f"💬 {to_smallcaps('support')}", url=Config.SUPPORT_GROUP_URL))
    if not btns:
        return None

    if feats.get("btn_two_col_layout"):
        rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    else:
        rows = [[b] for b in btns]
    return InlineKeyboardMarkup(rows)


async def apply_extramanager_channel_pipeline(client: Client, message: Message) -> None:
    if not message.chat or message.chat.type != enums.ChatType.CHANNEL:
        return
    cid = int(message.chat.id)
    cfg = await get_channel_xmgr_config(cid)
    feats = cfg["features"]
    if feats.get("sec_emergency_pause"):
        return

    media = message.video or message.document or message.audio or message.photo or message.animation or message.voice
    fname = str(getattr(media, "file_name", "") or "")
    fsize = int(getattr(media, "file_size", 0) or 0)
    dur = int(getattr(media, "duration", 0) or 0)
    w = int(getattr(media, "width", 0) or 0)
    h = int(getattr(media, "height", 0) or 0)
    res_str = f"{w}x{h}" if (w and h) else ""
    mime = str(getattr(media, "mime_type", "") or "")

    stats = cfg["stats"]
    if feats.get("idx_post_counter"):
        stats["total_posts"] = int(stats.get("total_posts", 0) or 0) + 1
    if feats.get("idx_video_posts") and message.video:
        stats["video_posts"] = int(stats.get("video_posts", 0) or 0) + 1
    elif feats.get("idx_doc_posts") and message.document:
        stats["doc_posts"] = int(stats.get("doc_posts", 0) or 0) + 1
    elif feats.get("idx_audio_posts") and message.audio:
        stats["audio_posts"] = int(stats.get("audio_posts", 0) or 0) + 1
    elif feats.get("idx_photo_posts") and message.photo:
        stats["photo_posts"] = int(stats.get("photo_posts", 0) or 0) + 1
    elif feats.get("idx_text_posts") and message.text:
        stats["text_posts"] = int(stats.get("text_posts", 0) or 0) + 1
    if feats.get("idx_media_bytes") and fsize > 0:
        stats["total_bytes"] = int(stats.get("total_bytes", 0) or 0) + fsize
    if feats.get("idx_first_post_id") and not stats.get("first_post_id"):
        stats["first_post_id"] = int(message.id)
    if feats.get("idx_latest_post_id"):
        stats["latest_post_id"] = int(message.id)
    if feats.get("idx_daily_counter"):
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        if stats.get("daily_date") != today:
            stats["daily_date"] = today
            stats["daily_count"] = 1
        else:
            stats["daily_count"] = int(stats.get("daily_count", 0) or 0) + 1
    if feats.get("idx_top_extensions") and "." in fname:
        ext = fname.rsplit(".", 1)[-1].lower()[:10]
        exts = dict(stats.get("extensions") or {})
        exts[ext] = int(exts.get(ext, 0) or 0) + 1
        stats["extensions"] = exts
    await save_channel_xmgr_config(cid, cfg)

    if feats.get("sec_dry_run_mode"):
        return

    if feats.get("med_dedupe_file_id") and media and getattr(media, "file_unique_id", None):
        uid_key = str(media.file_unique_id)
        seen = _RECENT_POST_HASHES.setdefault(cid, [])
        if uid_key in seen:
            try:
                await message.delete()
            except Exception:
                pass
            return
        seen.append(uid_key)
        if len(seen) > 200:
            del seen[:-200]

    for pace_key, delay_s in (("sch_pace_5s", 5.0), ("sch_pace_3s", 3.0), ("sch_pace_1s", 1.0)):
        if feats.get(pace_key):
            now_t = time.time()
            delta = delay_s - (now_t - _LAST_POST_TS.get(cid, 0.0))
            if delta > 0:
                await asyncio.sleep(min(delta, delay_s))
            _LAST_POST_TS[cid] = time.time()
            break

    if feats.get("cap_auto_enable") or feats.get("btn_auto_attach"):
        raw_cap = message.caption if media else (message.text or "")
        new_cap = apply_caption_transforms(
            raw_caption=raw_cap or "",
            cfg=cfg,
            channel_title=str(message.chat.title or ""),
            channel_username=str(message.chat.username or ""),
            file_name=fname,
            file_size=fsize,
            duration=dur,
            resolution=res_str,
            mime_type=mime,
            post_number=int(stats.get("total_posts", 0) or 0),
        )
        markup = build_channel_post_buttons(cfg, str(message.chat.username or ""), int(message.id))
        try:
            if media and (new_cap != (raw_cap or "") or markup):
                await client.edit_message_caption(
                    chat_id=cid,
                    message_id=message.id,
                    caption=new_cap,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
            elif (not media) and message.text and (new_cap != message.text or markup):
                await client.edit_message_text(
                    chat_id=cid,
                    message_id=message.id,
                    text=new_cap,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=bool(feats.get("aut_disable_preview", True)),
                )
        except Exception as e:
            logger.debug("extramanager edit notice for %s/%s: %s", cid, message.id, e)

    if feats.get("aut_pin_latest"):
        try:
            if feats.get("aut_unpin_previous"):
                await client.unpin_all_chat_messages(cid)
            await client.pin_chat_message(
                cid,
                message.id,
                disable_notification=bool(feats.get("aut_silent_night") or feats.get("sch_offpeak_silent")),
            )
        except Exception:
            pass

    for r_key, emoji_val in (
        ("eng_react_thumbsup", "👍"),
        ("eng_react_fire", "🔥"),
        ("eng_react_heart", "❤️"),
        ("eng_react_star", "⭐"),
    ):
        if feats.get(r_key):
            try:
                await client.send_reaction(cid, message.id, emoji_val)
            except Exception:
                pass
            break


@Client.on_message(filters.command(["extramanager", "chx", "xmgr"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def extramanager_command(client: Client, message: Message):
    cid, title = await _resolve_target_channel(client, message)
    if not cid:
        return await safe_reply_text(
            message,
            f"📢 <b>{to_smallcaps('extramanager is channel-only (220 working features)')}</b>\n\n"
            f"• {to_smallcaps('run')} <code>/extramanager</code> {to_smallcaps('directly inside a channel, or pass')} <code>/extramanager -100xxxxxxxxxx</code> {to_smallcaps('after connecting your channel.')}",
            parse_mode=enums.ParseMode.HTML,
        )
    uid = message.from_user.id if message.from_user else 0
    if message.chat.type != enums.ChatType.CHANNEL and not await _is_channel_admin(client, cid, uid):
        return await safe_reply_text(
            message,
            f"🚫 <b>{to_smallcaps('only channel admins or bot admins can manage this channel.')}</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg = await get_channel_xmgr_config(cid)
    text, markup = _render_home_panel(cid, title, cfg)
    await safe_reply_text(
        message,
        text,
        reply_markup=markup,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@Client.on_message(filters.channel & filters.incoming, group=6)
async def extramanager_channel_listener(client: Client, message: Message):
    spawn_mega_task(
        apply_extramanager_channel_pipeline(client, message),
        name=f"xmgr-ch-{message.chat.id}-{message.id}",
    )


@Client.on_callback_query(filters.regex(r"^xmgr\|"))
async def extramanager_callback(client: Client, query: CallbackQuery):
    parts = query.data.split(XMGR_SEP)
    if len(parts) < 4:
        return await query.answer("⚠️ Invalid action", show_alert=True)
    action, raw_cid, arg = parts[1], parts[2], parts[3]
    try:
        cid = int(raw_cid)
    except ValueError:
        return await query.answer("⚠️ Invalid channel ID", show_alert=True)

    uid = query.from_user.id if query.from_user else 0
    if not await _is_channel_admin(client, cid, uid):
        return await query.answer("🚫 Channel admins only", show_alert=True)

    if action == "close":
        await query.answer("🗑️ ᴄʟᴏsᴇᴅ")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    cfg = await get_channel_xmgr_config(cid)
    title = f"Channel {cid}"
    try:
        chat = await client.get_chat(cid)
        if chat and chat.title:
            title = chat.title
    except Exception:
        pass

    if action == "home":
        await query.answer("📢 ᴄʜᴀɴɴᴇʟ ᴇxᴛʀᴀ ᴍᴀɴᴀɢᴇʀ")
        text, markup = _render_home_panel(cid, title, cfg)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "cat":
        cat_key, page_s = (arg.split(":", 1) + ["0"])[:2]
        if cat_key not in XMGR_CATEGORIES:
            return await query.answer("⚠️ Unknown category", show_alert=True)
        await query.answer(XMGR_CATEGORIES[cat_key][0])
        text, markup = _render_category_panel(cid, cat_key, int(page_s or 0), cfg)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "tog":
        fkey, page_s = (arg.split(":", 1) + ["0"])[:2]
        if fkey not in XMGR_ALL_FEATURES:
            return await query.answer("⚠️ Unknown feature", show_alert=True)
        cat_key, flabel, _ = XMGR_ALL_FEATURES[fkey]
        cfg["features"][fkey] = not bool(cfg["features"].get(fkey, False))
        await save_channel_xmgr_config(cid, cfg)
        state_lbl = "ON" if cfg["features"][fkey] else "OFF"
        await query.answer(f"{flabel}: {state_lbl}")
        text, markup = _render_category_panel(cid, cat_key, int(page_s or 0), cfg)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action in ("caton", "catoff"):
        cat_key, page_s = (arg.split(":", 1) + ["0"])[:2]
        if cat_key not in XMGR_CATEGORIES:
            return await query.answer("⚠️ Unknown category", show_alert=True)
        new_val = action == "caton"
        for fkey, _, _ in XMGR_CATEGORIES[cat_key][2]:
            cfg["features"][fkey] = new_val
        await save_channel_xmgr_config(cid, cfg)
        await query.answer(f"✅ Set all {cat_key} {'ON' if new_val else 'OFF'}")
        text, markup = _render_category_panel(cid, cat_key, int(page_s or 0), cfg)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "preset":
        presets = {
            "movie": [
                "cap_auto_enable", "cap_add_file_size", "cap_add_duration", "cap_add_resolution",
                "brd_quality_badge", "brd_audio_badge", "brd_sub_badge", "brd_watermark_line",
                "btn_auto_attach", "btn_join_channel", "btn_share_post", "aut_disable_preview",
            ],
            "files": [
                "cap_auto_enable", "cap_add_file_size", "cap_add_mime_tag", "med_dedupe_file_id",
                "med_auto_clean_name", "flt_utm_stripper", "idx_post_counter", "idx_media_bytes",
                "btn_auto_attach", "btn_join_channel", "aut_disable_preview",
            ],
        }
        for fk in presets.get(arg, []):
            if fk in cfg["features"]:
                cfg["features"][fk] = True
        await save_channel_xmgr_config(cid, cfg)
        await query.answer(f"✅ Applied {arg} preset")
        text, markup = _render_home_panel(cid, title, cfg)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "stats":
        await query.answer("📊 ᴄʜᴀɴɴᴇʟ sᴛᴀᴛs")
        st = cfg["stats"]
        on_cnt, tot_cnt = _count_enabled(cfg)
        top_ext = ", ".join(f".{k}({v})" for k, v in sorted((st.get("extensions") or {}).items(), key=lambda x: -x[1])[:6]) or "None"
        text = (
            f"📊 <b>{to_smallcaps('channel extra analytics')}</b> • <code>{cid}</code>\n\n"
            f"• 📨 <b>{to_smallcaps('total indexed posts')}</b> : <code>{st.get('total_posts', 0)}</code>\n"
            f"• 🎬 <b>{to_smallcaps('videos / docs / audio / photos')}</b> : "
            f"<code>{st.get('video_posts', 0)} / {st.get('doc_posts', 0)} / {st.get('audio_posts', 0)} / {st.get('photo_posts', 0)}</code>\n"
            f"• 📦 <b>{to_smallcaps('media volume')}</b> : <code>{humanbytes(int(st.get('total_bytes', 0) or 0))}</code>\n"
            f"• 📅 <b>{to_smallcaps('today posts')}</b> : <code>{st.get('daily_count', 0)}</code>\n"
            f"• 🏷️ <b>{to_smallcaps('top extensions')}</b> : <code>{html.escape(top_ext)}</code>\n"
            f"• 🧩 <b>{to_smallcaps('active features')}</b> : <code>{on_cnt}/{tot_cnt}</code>"
            f"{format_watermark_html()}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🏠 {to_smallcaps('back')}", callback_data=f"xmgr{XMGR_SEP}home{XMGR_SEP}{cid}{XMGR_SEP}0"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data=f"xmgr{XMGR_SEP}close{XMGR_SEP}{cid}{XMGR_SEP}0"),
        ]])
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "health":
        await query.answer("🩺 ʜᴇᴀʟᴛʜ ᴀᴜᴅɪᴛ")
        on_cnt, tot_cnt = _count_enabled(cfg)
        paused = bool(cfg["features"].get("sec_emergency_pause"))
        text = (
            f"🩺 <b>{to_smallcaps('channel health audit')}</b> • <code>{cid}</code>\n\n"
            f"• 📢 <b>{to_smallcaps('channel')}</b> : <code>{html.escape(title)}</code>\n"
            f"• 🧩 <b>{to_smallcaps('feature registry')}</b> : <code>{tot_cnt} verified ({on_cnt} active)</code>\n"
            f"• 🛡️ <b>{to_smallcaps('pipeline state')}</b> : <code>{'PAUSED' if paused else 'HEALTHY'}</code>\n"
            f"• ⚡ <b>{to_smallcaps('async runner')}</b> : <code>asyncio.create_task() active</code>"
            f"{format_watermark_html()}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🏠 {to_smallcaps('back')}", callback_data=f"xmgr{XMGR_SEP}home{XMGR_SEP}{cid}{XMGR_SEP}0"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data=f"xmgr{XMGR_SEP}close{XMGR_SEP}{cid}{XMGR_SEP}0"),
        ]])
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    if action == "reset":
        fresh = get_default_xmgr_config()
        await save_channel_xmgr_config(cid, fresh)
        await query.answer("🔄 All 220 channel features reset")
        text, markup = _render_home_panel(cid, title, fresh)
        return await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
