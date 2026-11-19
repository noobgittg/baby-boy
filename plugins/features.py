import asyncio
import hashlib
import html
import io
import json
import logging
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from database.users_chats_db import db
from info import ADMINS, COMMAND_PREFIXES, LOG_CHANNEL
from plugins.channel_manager import resolve_target_channel_and_verify
from plugins.core_db import mongo
from plugins.core_utils import (
    execute_with_floodwait,
    safe_edit_text,
    safe_reply_text,
    to_smallcaps,
)
from utils import (
    format_watermark_html,
    get_readable_time,
    is_admin_sync,
    is_chat_or_global_admin,
    safe_edit_message,
)

logger = logging.getLogger("MMWExtraChannelMgr")

XM_SEP = "|"
RP_SEP = "|"

_URL_RE = re.compile(r"https?://\S+|www.\S+", re.IGNORECASE)
_TME_RE = re.compile(r"(?:https?://)?(?:t.me|telegram.me)/\S+", re.IGNORECASE)
USER_RE = re.compile(r"@[A-Za-z0-9]{4,}")
HASH_RE = re.compile(r"#[A-Za-z0-9]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)
_RECENT_POST_HASHES: Dict[int, Dict[str, float]] = {}
_SCHEDULED_POST_TASKS: Dict[str, asyncio.Task] = {}
_SCHEDULED_POST_META: Dict[str, Dict[str, Any]] = {}

EXTRA_CHANNEL_CATEGORIES: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {
    "format": (
        "🎨 Auto-Formatting & Styling",
        [
            ("bold_title", "Bold First Line Title"),
            ("italic_footer", "Italic Last Line Footer"),
            ("smallcaps_caption", "SmallCaps Styled Caption"),
            ("strip_links", "Strip External URLs"),
            ("strip_usernames", "Strip @Usernames"),
            ("strip_hashtags", "Strip Tags"),
            ("strip_emojis", "Strip Emojis & Symbols"),
            ("trim_whitespace", "Trim Extra Whitespace"),
            ("uppercase_header", "Uppercase First Line"),
            ("add_separator_line", "Add Divider Line"),
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
            ("auto_forward_backup", "Auto Copy to Log Channel"),
            ("auto_pin_video", "Auto Pin New Videos"),
            ("auto_pin_audio", "Auto Pin New Audios"),
            ("auto_pin_document", "Auto Pin New Documents"),
            ("auto_pin_photo", "Auto Pin New Photos"),
            ("auto_pin_poll", "Auto Pin New Polls"),
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
            ("ttl_sticker_10m", "Auto-Del Sticker After 10m"),
            ("ttl_gif_30m", "Auto-Del GIF After 30m"),
            ("ttl_text_1h", "Auto-Del Text After 1h"),
            ("ttl_forwarded_1h", "Auto-Del Forwards After 1h"),
            ("ttl_links_2h", "Auto-Del Link Posts 2h"),
            ("ttl_promos_6h", "Auto-Del Promo Posts 6h"),
            ("ttl_alerts_12h", "Auto-Del Alert Posts 12h"),
            ("ttl_text_24h", "Auto-Del Text After 24h"),
            ("ttl_photo_24h", "Auto-Del Photo After 24h"),
            ("ttl_video_24h", "Auto-Del Video After 24h"),
            ("ttl_doc_24h", "Auto-Del Doc After 24h"),
            ("ttl_audio_24h", "Auto-Del Audio After 24h"),
            ("ttl_poll_24h", "Auto-Del Poll After 24h"),
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
            ("auto_comment_prompt", "Append Comment Prompt"),
        ],
    ),
    "brand": (
        "🏷️ Metadata & Branding",
        [
            ("brand_channel_handle", "Append @Channel Handle"),
            ("brand_channel_title", "Append Channel Title"),
        ],
    ),
    "sync": (
        "🔄 Backup & Sync",
        [
            ("sync_to_log_channel", "Sync Posts to Log Channel"),
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
        f"⚡ {to_smallcaps('channel extra manager pro')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📢 {to_smallcaps('channel')}: {html.escape(title or str(chat_id))}\n"
        f"• 🆔 {to_smallcaps('id')}: {chat_id}\n"
        f"• 🧩 {to_smallcaps('features')}: {enabled_cnt}/{total_cnt} Active\n\n"
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
            quote=True, parse_mode=enums.ParseMode.HTML,
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
    elif feats.get("block_bot_posts") and message.from_user and message.from_user.is_bot:
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
    elif feats.get("block_games") and message.game:
        should_delete = True
    elif feats.get("block_venues") and message.venue:
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

    if not should_delete and feats.get("block_duplicate_posts"):
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
        new_text = USER_RE.sub("", new_text)
    if feats.get("strip_hashtags"):
        new_text = HASH_RE.sub("", new_text)
    if feats.get("strip_emojis"):
        new_text = _EMOJI_RE.sub("", new_text)
    if feats.get("replace_underscores"):
        new_text = new_text.replace("_", " ")
    if feats.get("clean_newlines"):
        new_text = _MULTI_NL_RE.sub("\n\n", new_text)
    if feats.get("trim_whitespace"):
        new_text = new_text.strip()
    if feats.get("smallcaps_caption") and new_text:
        new_text = to_smallcaps(new_text)
    if feats.get("uppercase_header") and new_text:
        _lines = new_text.split("\n", 1)
        _lines[0] = _lines[0].upper()
        new_text = "\n".join(_lines)
    if feats.get("bold_title") and new_text:
        _lines = new_text.split("\n", 1)
        _lines[0] = f"<b>{_lines[0]}</b>"
        new_text = "\n".join(_lines)
    if feats.get("italic_footer") and new_text:
        _lines = new_text.rsplit("\n", 1)
        _lines[-1] = f"<i>{_lines[-1]}</i>"
        new_text = "\n".join(_lines)
    if feats.get("wrap_blockquote") and new_text:
        new_text = f"<blockquote>{new_text}</blockquote>"
    if feats.get("wrap_spoiler_caption") and new_text:
        new_text = f"<tg-spoiler>{new_text}</tg-spoiler>"

    extra_lines: List[str] = []
    if feats.get("auto_file_size_tag") and file_size > 0:
        extra_lines.append(f"📦 Size: {_human_size(file_size)}")
    if feats.get("auto_resolution_tag") and getattr(message.video, "height", 0):
        extra_lines.append(f"🎬 Resolution: {message.video.width}x{message.video.height}")
    if feats.get("auto_duration_tag"):
        _dur = getattr(media_obj, "duration", 0) or 0
        if _dur:
            extra_lines.append(f"⏱️ Duration: {get_readable_time(int(_dur))}")
    if feats.get("auto_mime_badge"):
        _mime = getattr(media_obj, "mime_type", "") or ""
        if _mime:
            extra_lines.append(f"📄 {_mime}")
    if feats.get("auto_video_badge") and message.video:
        extra_lines.append("🎬 VIDEO")
    if feats.get("auto_audio_badge") and message.audio:
        extra_lines.append("🎵 AUDIO")
    if feats.get("auto_doc_badge") and message.document:
        extra_lines.append("📄 DOCUMENT")
    if feats.get("auto_photo_badge") and message.photo:
        extra_lines.append("🖼️ PHOTO")
    if feats.get("auto_voice_badge") and message.voice:
        extra_lines.append("🎤 VOICE NOTE")
    if feats.get("auto_animation_badge") and message.animation:
        extra_lines.append("🎞️ ANIMATION")
    if feats.get("keep_extension_tag") and file_name and "." in file_name:
        _ext = file_name.rsplit(".", 1)[-1]
        if _ext and len(_ext) <= 6:
            extra_lines.append(f"🔖 .{_ext.upper()}")
    if feats.get("add_post_number"):
        extra_lines.append(f"🔢 Post #{stats['posts']}")
    if feats.get("add_timestamp"):
        import datetime
        extra_lines.append(f"🕐 {datetime.datetime.utcnow().strftime('%H:%M UTC')}")
    if feats.get("add_date_stamp"):
        import datetime
        extra_lines.append(f"📅 {datetime.datetime.utcnow().strftime('%Y-%m-%d')}")
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
        (feats.get("auto_pin_video") and message.video) or
        (feats.get("auto_pin_audio") and message.audio) or
        (feats.get("auto_pin_document") and message.document) or
        (feats.get("auto_pin_photo") and message.photo) or
        (feats.get("auto_pin_poll") and message.poll)
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


def _fed_col():
    return mongo.active_db.federations


def _parse_delay_seconds(raw: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", str(raw or "").strip().lower())
    if not m:
        return 0
    val = int(m.group(1))
    unit = m.group(2) or "s"
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 1)
    return max(0, min(7 * 86400, val * mult))


async def _get_fed(fed_id: str) -> Optional[Dict[str, Any]]:
    doc = await _fed_col().find_one({"fed_id": str(fed_id)}, {"_id": 0})
    if doc:
        doc.pop("_id", None)
    return doc


async def _get_chat_fed(chat_id: int) -> Optional[Dict[str, Any]]:
    cfg = await db.get_helpdesk_config(chat_id)
    fid = cfg.get("fed_id")
    if not fid:
        return None
    return await _get_fed(str(fid))


@Client.on_message(filters.group & filters.command(["antichannel", "antich"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def antichannel_command(client: Client, message: Message):
    cid = message.chat.id
    uid = message.from_user.id if message.from_user else 0
    scid = message.sender_chat.id if message.sender_chat else 0
    if not await is_chat_or_global_admin(client, cid, user_id=uid, sender_chat_id=scid):
        return await safe_reply_text(
            message,
            f"⛔ {to_smallcaps('only group admins or bot admins can toggle anti-channel!')}",
            parse_mode=enums.ParseMode.HTML,
        )
    cfg = await db.get_helpdesk_config(cid)
    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        new_val = arg in ("on", "true", "1", "yes", "enable")
    else:
        new_val = not bool(cfg.get("antichannel_enabled", False))
    await db.update_helpdesk_config(cid, {"antichannel_enabled": new_val})
    badge = "🟢 ᴇɴᴀʙʟᴇᴅ" if new_val else "⚪ ᴅɪsᴀʙʟᴇᴅ"
    logger.info("🛡️ /antichannel › ᴄʜᴀᴛ=%s sᴛᴀᴛᴇ=%s ʙʏ=%s", cid, new_val, uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🛡️ {to_smallcaps('toggle anti-channel')}", callback_data=f"rpro{RP_SEP}antich{RP_SEP}{cid}")],
        [
            InlineKeyboardButton(f"📊 {to_smallcaps('group stats')}", callback_data=f"rpro{RP_SEP}gstats{RP_SEP}{cid}"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    await safe_reply_text(
        message,
        f"✦ {to_smallcaps('rose anti-channel shield')} ✦ 🛡️\n\n"
        f"• 📢 {to_smallcaps('status')}: {badge}\n"
        f"• 💡 {to_smallcaps('blocks messages sent as external channels inside this group.')}"
        f"{format_watermark_html()}",
        reply_markup=kb,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.incoming, group=6)
async def antichannel_enforcer(client: Client, message: Message):
    if not message.sender_chat or not message.chat:
        return
    if int(message.sender_chat.id) == int(message.chat.id):
        return
    linked_chat_id = getattr(message.chat, "linked_chat", None)
    if linked_chat_id and int(getattr(linked_chat_id, "id", 0) or 0) == int(message.sender_chat.id):
        return
    cfg = await db.get_helpdesk_config(message.chat.id)
    if not cfg.get("antichannel_enabled", False):
        return
    try:
        await message.delete()
        await client.ban_chat_member(message.chat.id, message.sender_chat.id)
        logger.info("🛡️ ᴀɴᴛɪ-ᴄʜᴀɴɴᴇʟ ʙʟᴏᴄᴋᴇᴅ › ɢʀᴏᴜᴘ=%s ᴄʜᴀɴɴᴇʟ=%s", message.chat.id, message.sender_chat.id)
    except Exception as exc:
        logger.debug("⚠️ ᴀɴᴛɪ-ᴄʜᴀɴɴᴇʟ ᴇɴғᴏʀᴄᴇ ɴᴏᴛɪᴄᴇ › %s", exc)


async def _build_group_stats_card(client: Client, chat_id: int, title: str = "") -> Tuple[str, InlineKeyboardMarkup]:
    cfg = await db.get_helpdesk_config(chat_id)
    members = 0
    try:
        members = await client.get_chat_members_count(chat_id)
    except Exception:
        pass
    locks = cfg.get("locks") or {}
    active_locks = [k for k, v in locks.items() if v]
    fed_id = cfg.get("fed_id") or "None"
    text = (
        f"✦ {to_smallcaps('group pro analytics & security')} ✦ 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 💬 {to_smallcaps('group')}: {html.escape(title or str(chat_id))} ({chat_id})\n"
        f"• 👥 {to_smallcaps('members')}: {members}\n"
        f"• 🌐 {to_smallcaps('federation')}: {html.escape(str(fed_id))}\n"
        f"• 🌊 {to_smallcaps('anti-flood')}: {'🟢 ᴏɴ' if cfg.get('antiflood_enabled') else '⚪ ᴏғғ'}\n"
        f"• 🛡️ {to_smallcaps('anti-spam')}: {'🟢 ᴏɴ' if cfg.get('antispam_enabled') else '⚪ ᴏғғ'}\n"
        f"• 📢 {to_smallcaps('anti-channel')}: {'🟢 ᴏɴ' if cfg.get('antichannel_enabled') else '⚪ ᴏғғ'}\n"
        f"• 🚨 {to_smallcaps('raid mode')}: {'🔴 ᴏɴ' if cfg.get('raid_mode') else '⚪ ᴏғғ'}\n"
        f"• 🔐 {to_smallcaps('active locks')}: {', '.join(active_locks) if active_locks else 'None'}"
        f"{format_watermark_html()}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('anti-channel')}", callback_data=f"rpro{RP_SEP}antich{RP_SEP}{chat_id}"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('group editor')}", callback_data=f"gfeat|home|{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data=f"rpro{RP_SEP}gstats{RP_SEP}{chat_id}"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    return text, kb


@Client.on_message(filters.group & filters.command(["gstats"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def group_stats_command(client: Client, message: Message):
    text, kb = await _build_group_stats_card(client, message.chat.id, message.chat.title or "")
    await safe_reply_text(message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["newfed"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def new_federation_command(client: Client, message: Message):
    if not message.from_user:
        return
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 {to_smallcaps('usage')}: /newfed <federation_name>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    fed_name = message.text.split(None, 1)[1].strip()[:64]
    fed_id = f"fed_{secrets.token_hex(4)}"
    doc = {
        "fed_id": fed_id,
        "name": fed_name,
        "owner_id": int(message.from_user.id),
        "admins": [int(message.from_user.id)],
        "chats": [],
        "banned_users": {},
        "created_at": float(time.time()),
    }
    await _fed_col().update_one({"fed_id": fed_id}, {"$set": doc}, upsert=True)
    logger.info("🌐 /newfed ᴄʀᴇᴀᴛᴇᴅ › ғᴇᴅ=%s ᴏᴡɴᴇʀ=%s", fed_id, message.from_user.id)
    await safe_reply_text(
        message,
        f"✦ {to_smallcaps('federation created')} ✦ 🌐\n\n"
        f"• 🏷️ {to_smallcaps('name')}: {html.escape(fed_name)}\n"
        f"• 🆔 {to_smallcaps('fed id')}: {fed_id}\n"
        f"• 💡 {to_smallcaps('link any group with')} /joinfed {fed_id}"
        f"{format_watermark_html()}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.command(["joinfed", "leavefed"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def join_leave_fed_command(client: Client, message: Message):
    cid = message.chat.id
    uid = message.from_user.id if message.from_user else 0
    if not await is_chat_or_global_admin(client, cid, user_id=uid):
        return await safe_reply_text(
            message,
            f"⛔ {to_smallcaps('only group admins or bot admins can manage federation links!')}",
            parse_mode=enums.ParseMode.HTML,
        )
    cmd = message.command[0].lower()
    if cmd == "leavefed":
        cfg = await db.get_helpdesk_config(cid)
        old_fid = cfg.get("fed_id")
        await db.update_helpdesk_config(cid, {"fed_id": None})
        if old_fid:
            fed = await _get_fed(str(old_fid))
            if fed:
                chats = [c for c in (fed.get("chats") or []) if int(c) != int(cid)]
                await _fed_col().update_one({"fed_id": str(old_fid)}, {"$set": {"chats": chats}})
        logger.info("🌐 /leavefed › ᴄʜᴀᴛ=%s ғᴇᴅ=%s", cid, old_fid)
        return await safe_reply_text(
            message,
            f"✅ {to_smallcaps('group disconnected from federation!')}{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 <b>{to_smallcaps('usage')}:</b> <code>/joinfed &lt;fed_id&gt;</code>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    fid = message.command[1].strip()
    fed = await _get_fed(fid)
    if not fed:
        return await safe_reply_text(
            message,
            f"❌ <b>{to_smallcaps('federation id not found!')}</b>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    chats = list(dict.fromkeys(list(fed.get("chats") or []) + [int(cid)]))
    await _fed_col().update_one({"fed_id": fid}, {"$set": {"chats": chats}})
    await db.update_helpdesk_config(cid, {"fed_id": fid})
    logger.info("🌐 /joinfed › ᴄʜᴀᴛ=%s ғᴇᴅ=%s", cid, fid)
    await safe_reply_text(
        message,
        f"✅ <b>{to_smallcaps('group joined federation')}</b> <code>{html.escape(fed.get('name', fid))}</code> (<code>{fid}</code>)!"
        f"{format_watermark_html()}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["fban", "unfban", "finfo"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def federation_actions_command(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    cmd = message.command[0].lower()
    fed = await _get_chat_fed(message.chat.id) if message.chat.id < 0 else None

    if cmd == "finfo":
        if len(message.command) > 1:
            fed = await _get_fed(message.command[1].strip())
        if not fed:
            return await safe_reply_text(
                message,
                f"ℹ️ {to_smallcaps('no federation linked. use /newfed or /finfo <fed_id>.')}{format_watermark_html()}",
                parse_mode=enums.ParseMode.HTML,
            )
        return await safe_reply_text(
            message,
            f"✦ {to_smallcaps('federation info')} ✦ 🌐\n\n"
            f"• 🏷️ {to_smallcaps('name')}: {html.escape(str(fed.get('name', 'Fed')))}\n"
            f"• 🆔 {to_smallcaps('fed id')}: {fed.get('fed_id')}\n"
            f"• 👑 {to_smallcaps('owner')}: {fed.get('owner_id')}\n"
            f"• 💬 {to_smallcaps('linked chats')}: {len(fed.get('chats') or [])}\n"
            f"• 🚫 {to_smallcaps('fed-banned users')}: {len(fed.get('banned_users') or {})}"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    if not fed:
        return await safe_reply_text(
            message,
            f"⚠️ <b>{to_smallcaps('link this group to a federation first using /joinfed <fed_id>.')}</b>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    fed_admins = set(int(x) for x in (fed.get("admins") or []))
    if uid != int(fed.get("owner_id") or 0) and uid not in fed_admins and not is_admin_sync(uid):
        return await safe_reply_text(
            message,
            f"⛔ <b>{to_smallcaps('only federation owner/admins or bot admins can run /fban or /unfban!')}</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    target_uid = 0
    reason = "Federation Policy Violation"
    if message.reply_to_message and message.reply_to_message.from_user:
        target_uid = int(message.reply_to_message.from_user.id)
        if len(message.command) > 1:
            reason = message.text.split(None, 1)[1].strip()[:200]
    elif len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        target_uid = int(message.command[1])
        if len(message.command) > 2:
            reason = message.text.split(None, 2)[2].strip()[:200]

    if target_uid <= 0 or target_uid in Config.ADMINS:
        return await safe_reply_text(
            message,
            f"⚠️ <b>{to_smallcaps('reply to a user or provide a valid non-admin user id.')}</b>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    banned_map = dict(fed.get("banned_users") or {})
    linked_chats = list(fed.get("chats") or [])
    affected = 0

    if cmd == "fban":
        banned_map[str(target_uid)] = {"reason": reason, "by": uid, "ts": float(time.time())}
        await _fed_col().update_one({"fed_id": fed["fed_id"]}, {"$set": {"banned_users": banned_map}})
        for gcid in linked_chats:
            try:
                await client.ban_chat_member(int(gcid), target_uid)
                affected += 1
            except Exception:
                pass
        logger.info("🔨 /fban › ғᴇᴅ=%s ᴛᴀʀɢᴇᴛ=%s ᴄʜᴀᴛs=%s", fed["fed_id"], target_uid, affected)
        await safe_reply_text(
            message,
            f"🔨 <b>{to_smallcaps('federation ban applied')}</b>\n\n"
            f"• 👤 <b>{to_smallcaps('user')}:</b> <code>{target_uid}</code>\n"
            f"• 🌐 <b>{to_smallcaps('federation')}:</b> <code>{fed['fed_id']}</code>\n"
            f"• 💬 <b>{to_smallcaps('enforced in')}:</b> <code>{affected}/{len(linked_chats)}</code> {to_smallcaps('chats')}\n"
            f"• 📝 <b>{to_smallcaps('reason')}:</b> <code>{html.escape(reason)}</code>"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        banned_map.pop(str(target_uid), None)
        await _fed_col().update_one({"fed_id": fed["fed_id"]}, {"$set": {"banned_users": banned_map}})
        for gcid in linked_chats:
            try:
                await client.unban_chat_member(int(gcid), target_uid)
                affected += 1
            except Exception:
                pass
        logger.info("🕊️ /unfban › ғᴇᴅ=%s ᴛᴀʀɢᴇᴛ=%s ᴄʜᴀᴛs=%s", fed["fed_id"], target_uid, affected)
        await safe_reply_text(
            message,
            f"🕊️ <b>{to_smallcaps('federation ban lifted')}</b>\n\n"
            f"• 👤 <b>{to_smallcaps('user')}:</b> <code>{target_uid}</code>\n"
            f"• 💬 <b>{to_smallcaps('unbanned in')}:</b> <code>{affected}/{len(linked_chats)}</code> {to_smallcaps('chats')}"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.group & filters.new_chat_members, group=7)
async def federation_join_enforcer(client: Client, message: Message):
    fed = await _get_chat_fed(message.chat.id)
    if not fed:
        return
    banned_map = fed.get("banned_users") or {}
    for member in message.new_chat_members or []:
        if str(member.id) in banned_map:
            try:
                await client.ban_chat_member(message.chat.id, member.id)
                logger.info("🔨 ғᴇᴅ ᴀᴜᴛᴏ-ʙᴀɴ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s ғᴇᴅ=%s", message.chat.id, member.id, fed.get("fed_id"))
            except Exception:
                pass


@Client.on_message(filters.command(["schedulepost", "schedpost"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def schedule_post_command(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    target_chat = message.chat.id
    if message.chat.type == enums.ChatType.PRIVATE:
        conn_cid = await db.get_user_connected_channel(uid)
        if conn_cid:
            target_chat = int(conn_cid)
    if target_chat < 0 and not await is_chat_or_global_admin(client, target_chat, user_id=uid):
        return await safe_reply_text(
            message,
            f"⛔ {to_smallcaps('only chat/channel admins or bot admins can schedule posts!')}",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 {to_smallcaps('usage')}: /schedulepost 10m Hello everyone! {to_smallcaps('or reply to a message with')} /schedulepost 30m"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    delay_sec = _parse_delay_seconds(message.command[1])
    if delay_sec <= 0:
        return await safe_reply_text(
            message,
            f"⚠️ {to_smallcaps('invalid delay! use formats like 30s, 10m, 2h, 1d.')}{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    post_text = message.text.split(None, 2)[2].strip() if len(message.command) > 2 else ""
    reply_src = message.reply_to_message
    if not post_text and not reply_src:
        return await safe_reply_text(
            message,
            f"⚠️ {to_smallcaps('provide post text or reply to a message to schedule.')}{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    job_id = f"sp_{secrets.token_hex(3)}"
    run_at = time.time() + delay_sec
    _SCHEDULED_POST_META[job_id] = {
        "job_id": job_id,
        "user_id": uid,
        "target_chat": target_chat,
        "delay_sec": delay_sec,
        "run_at": run_at,
        "preview": (post_text or "[Replied Message]")[:48],
    }

    async def _runner():
        try:
            await asyncio.sleep(delay_sec)
            if reply_src:
                await execute_with_floodwait(reply_src.copy, chat_id=target_chat)
            else:
                await execute_with_floodwait(client.send_message, chat_id=target_chat, text=post_text)
            logger.info("⏰ sᴄʜᴇᴅᴜʟᴇᴅ ᴘᴏsᴛ sᴇɴᴛ › ᴊᴏʙ=%s ᴄʜᴀᴛ=%s", job_id, target_chat)
        except asyncio.CancelledError:
            logger.info("🗑️ sᴄʜᴇᴅᴜʟᴇᴅ ᴘᴏsᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ › ᴊᴏʙ=%s", job_id)
        except Exception as exc:
            logger.warning("⚠️ sᴄʜᴇᴅᴜʟᴇᴅ ᴘᴏsᴛ ᴇʀʀᴏʀ (%s): %s", job_id, exc)
        finally:
            _SCHEDULED_POST_TASKS.pop(job_id, None)
            _SCHEDULED_POST_META.pop(job_id, None)

    _SCHEDULED_POST_TASKS[job_id] = asyncio.create_task(_runner(), name=f"mmw-sched-{job_id}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑️ {to_smallcaps('cancel scheduled post')}", callback_data=f"rpro{RP_SEP}delsched{RP_SEP}{job_id}")],
    ])
    await safe_reply_text(
        message,
        f"⏰ <b>{to_smallcaps('post scheduled successfully')}</b> ✨\n\n"
        f"• 🆔 <b>{to_smallcaps('job id')}:</b> <code>{job_id}</code>\n"
        f"• 💬 <b>{to_smallcaps('target chat')}:</b> <code>{target_chat}</code>\n"
        f"• ⏳ <b>{to_smallcaps('sends in')}:</b> <code>{delay_sec}s</code>"
        f"{format_watermark_html()}",
        reply_markup=kb,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["listscheduled", "delscheduled"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def manage_scheduled_posts_command(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    cmd = message.command[0].lower()
    if cmd == "delscheduled":
        if len(message.command) < 2:
            return await safe_reply_text(
                message,
                f"📝 {to_smallcaps('usage')}: /delscheduled <job_id>",
                parse_mode=enums.ParseMode.HTML,
            )
        jid = message.command[1].strip()
        meta = _SCHEDULED_POST_META.get(jid)
        if not meta:
            return await safe_reply_text(message, f"❌ {to_smallcaps('scheduled job not found!')}", parse_mode=enums.ParseMode.HTML)
        if int(meta.get("user_id", 0)) != uid and not is_admin_sync(uid):
            return await safe_reply_text(message, f"⛔ {to_smallcaps('unauthorized!')}", parse_mode=enums.ParseMode.HTML)
        task = _SCHEDULED_POST_TASKS.pop(jid, None)
        if task and not task.done():
            task.cancel()
        _SCHEDULED_POST_META.pop(jid, None)
        return await safe_reply_text(message, f"✅ {to_smallcaps('scheduled post cancelled')} ({jid})", parse_mode=enums.ParseMode.HTML)

    items = [m for m in _SCHEDULED_POST_META.values() if is_admin_sync(uid) or int(m.get("user_id", 0)) == uid]
    if not items:
        return await safe_reply_text(message, f"ℹ️ <b>{to_smallcaps('no active scheduled posts.')}</b>", parse_mode=enums.ParseMode.HTML)
    lines = [f"✦ <b>{to_smallcaps('active scheduled posts')}</b> ✦ ⏰\n"]
    btns = []
    now = time.time()
    for m in items[:10]:
        rem = max(0, int(m["run_at"] - now))
        lines.append(f"• <code>{m['job_id']}</code> │ <code>{m['target_chat']}</code> │ <code>{rem}s</code> │ {html.escape(m['preview'])}")
        btns.append([InlineKeyboardButton(f"🗑️ {to_smallcaps('cancel')} {m['job_id']}", callback_data=f"rpro{RP_SEP}delsched{RP_SEP}{m['job_id']}")])
    await safe_reply_text(message, "\n".join(lines) + format_watermark_html(), reply_markup=InlineKeyboardMarkup(btns), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["mytools", "studio"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def private_tools_studio_command(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    is_adm = is_admin_sync(uid)
    kb_rows = [
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('my settings')}", callback_data="nav_settings"),
        ],
        [
            InlineKeyboardButton(f"♻️ {to_smallcaps('task recovery')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"📦 {to_smallcaps('my file vault')}", callback_data="pvt_vault#page_0"),
        ],
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel manager')}", callback_data="chdesk|home"),
        ],
    ]
    if is_adm:
        kb_rows.append([
            InlineKeyboardButton(f"👑 {to_smallcaps('admin panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"📋 {to_smallcaps('expanded logs')}", callback_data="logs_act#refresh"),
        ])
    kb_rows.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])
    await safe_reply_text(
        message,
        f"✦ {to_smallcaps('mmw private & admin quick studio')} ✦ ⚡\n\n"
        f"• ✏️ /rename • 🗜️ /compress • 🎬 /stream\n"
        f"• ♻️ /tasks • 🗑️ /delete_task • 🧹 /clear_tasks\n"
        f"• 🌐 /newfed • 🛡️ /antichannel • ⏰ /schedulepost"
        f"{format_watermark_html()}",
        reply_markup=InlineKeyboardMarkup(kb_rows),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^rpro\|"))
async def rose_pro_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split(RP_SEP)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "antich" and arg.lstrip("-").isdigit():
        cid = int(arg)
        if not await is_chat_or_global_admin(client, cid, user_id=uid):
            return await query.answer(to_smallcaps("group admins only!"), show_alert=True)
        cfg = await db.get_helpdesk_config(cid)
        new_val = not bool(cfg.get("antichannel_enabled", False))
        await db.update_helpdesk_config(cid, {"antichannel_enabled": new_val})
        await query.answer(to_smallcaps(f"anti-channel {'enabled' if new_val else 'disabled'}!"))
        text, kb = await _build_group_stats_card(client, cid, getattr(query.message.chat, "title", "") or "")
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return

    if action == "gstats" and arg.lstrip("-").isdigit():
        cid = int(arg)
        await query.answer(to_smallcaps("refreshed!"))
        text, kb = await _build_group_stats_card(client, cid, getattr(query.message.chat, "title", "") or "")
        await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return

    if action == "delsched" and arg:
        meta = _SCHEDULED_POST_META.get(arg)
        if not meta:
            return await query.answer(to_smallcaps("already sent or cancelled!"), show_alert=True)
        if int(meta.get("user_id", 0)) != uid and not is_admin_sync(uid):
            return await query.answer(to_smallcaps("unauthorized!"), show_alert=True)
        task = _SCHEDULED_POST_TASKS.pop(arg, None)
        if task and not task.done():
            task.cancel()
        _SCHEDULED_POST_META.pop(arg, None)
        await query.answer(to_smallcaps("scheduled post cancelled!"), show_alert=True)
        await safe_edit_text(query.message, f"✅ **{to_smallcaps('scheduled post cancelled')}** (`{arg}`)")
        return await query.answer()
