import asyncio
import html
import io
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from pyrogram import Client, enums, filters, types
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.users_chats_db import db
from info import ADMINS, COMMAND_PREFIXES, LOG_CHANNEL
from Script import script
from utils import (
    get_file_id,
    get_readable_time,
    is_admin_sync,
    is_chat_or_global_admin,
    parse_Inline_buttons_from_text,
    safe_edit_message,
    to_smallcaps,
)

logger = logging.getLogger("MMWChannelMgr")

CH_SEP = "|"
_MSG_LINK_RE = re.compile(r"t\.me/(?:c/(\d+)/|([A-Za-z0-9_]+)/)(\d+)")


def _parse_delay_seconds(raw: str) -> int:
    s = str(raw or "").strip().lower()
    if s.isdigit():
        return int(s)
    m = re.match(r"^(\d+)\s*([smhd])$", s)
    if not m:
        return 0
    val, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return val * mult


async def resolve_target_channel_and_verify(
    client: Client,
    message: Message,
    explicit_arg: Optional[str] = None,
) -> Tuple[bool, Optional[types.Chat], Optional[Dict[str, Any]]]:
    """
    Resolve target channel from:
    1) Current chat if command is executed directly inside a channel
    2) Explicit channel_id / @username argument if provided
    3) User's connected channel via /connect
    Then verify that caller is Channel Owner, Channel Admin, or Bot ADMINS.
    """
    caller_id = message.from_user.id if message.from_user else 0
    sender_chat_id = message.sender_chat.id if message.sender_chat else 0
    target_chat_id: Optional[Union[int, str]] = None

    if message.chat.type == enums.ChatType.CHANNEL:
        target_chat_id = message.chat.id
    elif explicit_arg and (explicit_arg.startswith("-100") or explicit_arg.startswith("@")):
        target_chat_id = int(explicit_arg) if explicit_arg.lstrip("-").isdigit() else explicit_arg
    elif caller_id:
        conn_id = await db.get_user_connected_channel(caller_id)
        if conn_id:
            target_chat_id = conn_id

    if not target_chat_id:
        await message.reply_text(
            "📢 <b>ɴᴏ ᴄʜᴀɴɴᴇʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ!</b>\n\n"
            "• ᴜsᴇ <code>/connect -100xxxxxxxxxx</code> ᴏʀ <code>/connect @channel_username</code> ғɪʀsᴛ,\n"
            "• ᴏʀ ʀᴜɴ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅ ᴅɪʀᴇᴄᴛʟʏ ɪɴsɪᴅᴇ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ!",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, None, None

    try:
        chat_obj = await client.get_chat(target_chat_id)
    except Exception as e:
        await message.reply_text(
            f"❌ <b>ᴄᴀɴɴᴏᴛ ᴀᴄᴄᴇss ᴄʜᴀɴɴᴇʟ <code>{html.escape(str(target_chat_id))}</code>:</b>\n"
            f"<i>ᴍᴀᴋᴇ sᴜʀᴇ ɪ ᴀᴍ ᴀᴅᴅᴇᴅ ᴀs ᴀɴ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ! ({html.escape(str(e))})</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, None, None

    if chat_obj.type != enums.ChatType.CHANNEL:
        await message.reply_text(
            "⚠️ <b>ᴛᴀʀɢᴇᴛ ᴄʜᴀᴛ ɪs ɴᴏᴛ ᴀ ᴄʜᴀɴɴᴇʟ!</b> ᴜsᴇ <code>/helpdesk</code> ғᴏʀ ɢʀᴏᴜᴘs.",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, None, None

    authorized = await is_chat_or_global_admin(
        client,
        chat_obj.id,
        user_id=caller_id,
        sender_chat_id=sender_chat_id,
    )
    if not authorized:
        logger.warning(
            "⛔ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴄʜᴀɴɴᴇʟ ᴄᴍᴅ › ᴜsᴇʀ=%s ᴄʜᴀɴɴᴇʟ=%s",
            caller_id,
            chat_obj.id,
        )
        await message.reply_text(
            "⛔ <b>ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ!</b>\n"
            "<i>ᴏɴʟʏ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴏᴡɴᴇʀ, ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ᴍᴀɴᴀɢᴇ ᴛʜɪs ᴄʜᴀɴɴᴇʟ.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, None, None

    cfg = await db.get_channel_config(chat_obj.id, title=chat_obj.title or "Channel")
    return True, chat_obj, cfg


def _extract_msg_id(arg: str) -> Optional[int]:
    if not arg:
        return None
    cleaned = arg.strip()
    if cleaned.isdigit():
        return int(cleaned)
    m = _MSG_LINK_RE.search(cleaned)
    if m:
        return int(m.group(3))
    return None


def _apply_channel_template(
    raw_text: str,
    cfg: Dict[str, Any],
    existing_markup: Optional[InlineKeyboardMarkup] = None,
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    cleaned_body, inline_markup = parse_Inline_buttons_from_text(raw_text or "")
    header = (cfg.get("header_text") or "").strip()
    footer = (cfg.get("footer_text") or "").strip()

    parts = []
    if header:
        parts.append(header)
    if cleaned_body:
        parts.append(cleaned_body)
    if footer:
        parts.append(footer)

    final_text = "\n\n".join(parts).strip()

    rows: List[List[InlineKeyboardButton]] = []
    if inline_markup and inline_markup.inline_keyboard:
        rows.extend(inline_markup.inline_keyboard)
    elif existing_markup and existing_markup.inline_keyboard:
        rows.extend(existing_markup.inline_keyboard)

    btn_label = (cfg.get("button_text") or "").strip()
    btn_url = (cfg.get("button_url") or "").strip()
    if btn_label and btn_url.startswith(("http://", "https://", "tg://")):
        rows.append([InlineKeyboardButton(btn_label, url=btn_url)])

    return final_text, (InlineKeyboardMarkup(rows) if rows else None)


async def build_channel_help_view(
    client: Client,
    user_id: int,
    section: str = "home",
    channel_id: Optional[int] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    conn_id = channel_id or (await db.get_user_connected_channel(user_id) if user_id else None)
    cfg = await db.get_channel_config(conn_id) if conn_id else {}
    ch_title = html.escape(str(cfg.get("title", "ɴᴏ ᴄʜᴀɴɴᴇʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ")))
    conn_badge = f"<code>{conn_id}</code> ({ch_title})" if conn_id else "<code>ɴᴏɴᴇ (ᴜsᴇ /connect)</code>"

    if section == "posting":
        text = (
            "📤 <b>ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛɪɴɢ, sᴄʜᴇᴅᴜʟᴇ, ʙᴀᴛᴄʜ &amp; ᴘɪɴs</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ:</b> {conn_badge}\n\n"
            "• <code>/cpost &lt;text&gt;</code> — ᴘᴜʙʟɪsʜ ғᴏʀᴍᴀᴛᴛᴇᴅ ᴘᴏsᴛ (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇᴅɪᴀ/ᴍᴇssᴀɢᴇ)\n"
            "• <code>/cschedule &lt;10m/1h&gt; &lt;text&gt;</code> — sᴄʜᴇᴅᴜʟᴇ ᴀ ᴛɪᴍᴇᴅ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ\n"
            "• <code>/cedit &lt;msg_id/link&gt; &lt;new_text&gt;</code> — ᴇᴅɪᴛ ᴀɴ ᴇxɪsᴛɪɴɢ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ\n"
            "• <code>/cdel &lt;msg_id/link&gt;</code> — ᴅᴇʟᴇᴛᴇ ᴀ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ\n"
            "• <code>/ccopy &lt;post_link&gt;</code> — ᴄʟᴏɴᴇ ᴀɴʏ ᴘᴏsᴛ ᴛᴏ ᴄʜᴀɴɴᴇʟ (ɴᴏ ғᴏʀᴡᴀʀᴅ ᴛᴀɢ)\n"
            "• <code>/cbatch &lt;start_link&gt; &lt;end_link&gt;</code> — ʙᴀᴛᴄʜ ᴄʟᴏɴᴇ ᴘᴏsᴛs ᴛᴏ ᴄʜᴀɴɴᴇʟ\n"
            "• <code>/cpin [msg_id/link] [loud]</code> — ᴘɪɴ ᴀ ᴄʜᴀɴɴᴇʟ ᴍᴇssᴀɢᴇ\n"
            "• <code>/cunpin [msg_id/link]</code> │ <code>/cunpinall</code> — ᴜɴᴘɪɴ ᴄʜᴀɴɴᴇʟ ᴍᴇssᴀɢᴇs\n"
            "• <code>/cpoll Question | Opt 1 | Opt 2</code> — ᴘᴏsᴛ ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ᴘᴏʟʟ\n\n"
            "💡 <b>ɪɴʟɪɴᴇ ʙᴜᴛᴛᴏɴ sʏɴᴛᴀx ɪɴ /cpost:</b>\n"
            "<code>[📢 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ](buttonurl:https://t.me/mallumovieworldmain1)</code>"
        )
    elif section == "templates":
        text = (
            "🎨 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ, ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ &amp; ᴛᴇᴍᴘʟᴀᴛᴇs</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ:</b> {conn_badge}\n"
            f"• <b>ᴀᴜᴛᴏ-ᴛᴀɢ ɴᴇᴡ ᴘᴏsᴛs:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_tag_enabled') else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_caption_enabled', True) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ (4 ᴘʟᴀʏᴇʀs):</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_stream_enabled') else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ ᴇᴍᴏᴊɪ:</b> <code>{html.escape(cfg.get('auto_react_emoji') or 'ᴏғғ')}</code>\n"
            f"• <b>ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇʀ:</b> <code>{cfg.get('auto_delete_seconds', 0)}s</code>\n\n"
            "📌 <b>ᴀᴜᴛᴏ &amp; ᴛᴇᴍᴘʟᴀᴛᴇ ᴄᴏᴍᴍᴀɴᴅs:</b>\n"
            "• <code>/csetheader &lt;text|off&gt;</code> — sᴇᴛ ᴀᴜᴛᴏ ʜᴇᴀᴅᴇʀ\n"
            "• <code>/csetfooter &lt;text|off&gt;</code> — sᴇᴛ ᴀᴜᴛᴏ ғᴏᴏᴛᴇʀ / sɪɢɴᴀᴛᴜʀᴇ\n"
            "• <code>/csetbutton Label | https://url | off</code> — sᴇᴛ ᴀᴜᴛᴏ ᴜʀʟ ʙᴜᴛᴛᴏɴ\n"
            "• <code>/cautotag &lt;on|off&gt;</code> — ᴀᴜᴛᴏ-ᴀᴘᴘʟʏ ᴛᴇᴍᴘʟᴀᴛᴇ ᴛᴏ ᴀʟʟ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛs\n"
            "• <code>/cautocaption &lt;template|on|off&gt;</code> — sᴇᴛ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ\n"
            "• <code>/cautostream &lt;on|off&gt;</code> — ᴀᴜᴛᴏ-ᴀᴛᴛᴀᴄʜ 4-ᴘʟᴀʏᴇʀ sᴛʀᴇᴀᴍ ʙᴜᴛᴛᴏɴs\n"
            "• <code>/cautoreact &lt;emoji|off&gt;</code> — ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ ᴏɴ ɴᴇᴡ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛs\n"
            "• <code>/cautodel &lt;seconds|off&gt;</code> — ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛs"
        )
    elif section == "branding":
        text = (
            "🏷️ <b>ᴄʜᴀɴɴᴇʟ ʙʀᴀɴᴅɪɴɢ, ɪɴᴠɪᴛᴇs, ᴀᴅᴍɪɴs &amp; ʙᴀᴄᴋᴜᴘ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ:</b> {conn_badge}\n\n"
            "• <code>/ctitle &lt;new_title&gt;</code> — ᴄʜᴀɴɢᴇ ᴄʜᴀɴɴᴇʟ ᴛɪᴛʟᴇ\n"
            "• <code>/cdesc &lt;new_bio&gt;</code> — ᴄʜᴀɴɢᴇ ᴄʜᴀɴɴᴇʟ ᴅᴇsᴄʀɪᴘᴛɪᴏɴ\n"
            "• <code>/cpic</code> — ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴛᴏ sᴇᴛ ᴄʜᴀɴɴᴇʟ ᴘɪᴄᴛᴜʀᴇ\n"
            "• <code>/cdelpic</code> — ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ ᴘɪᴄᴛᴜʀᴇ\n"
            "• <code>/cinvite</code> │ <code>/crevoke</code> — ɢᴇɴᴇʀᴀᴛᴇ ᴏʀ ʀᴇᴠᴏᴋᴇ ɪɴᴠɪᴛᴇ ʟɪɴᴋ\n"
            "• <code>/cadmins</code> — ʟɪsᴛ ᴄʜᴀɴɴᴇʟ ᴏᴡɴᴇʀ &amp; ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs\n"
            "• <code>/cstats</code> — ᴠɪᴇᴡ ᴄʜᴀɴɴᴇʟ sᴜʙsᴄʀɪʙᴇʀs &amp; ᴄᴏɴғɪɢ sᴛᴀᴛs\n"
            "• <code>/cbackup</code> │ <code>/crestore</code> — ᴇxᴘᴏʀᴛ &amp; ʀᴇsᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ ᴊsᴏɴ\n"
            "• <code>/csettings</code> — ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ ғᴇᴀᴛᴜʀᴇ &amp; ᴛᴇᴍᴘʟᴀᴛᴇ ᴇᴅɪᴛᴏʀ\n"
            "• <code>/creplace</code> │ <code>/cblacklist</code> │ <code>/csignature</code> │ <code>/cclone</code>\n"
            "• <code>/editstudio</code> │ <code>/editpost</code> │ <code>/editbuttons</code> │ <code>/editmedia</code>"
        )
    elif section == "settings":
        def _b(k: str, d: bool = False) -> str:
            return "🟢 ᴏɴ" if cfg.get(k, d) else "🔴 ᴏғғ"

        text = (
            "⚙️ <b>ᴄʜᴀɴɴᴇʟ ʟɪᴠᴇ sᴇᴛᴛɪɴɢs &amp; ᴀᴜᴛᴏᴍᴀᴛɪᴏɴ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ᴀᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟ:</b> {conn_badge}\n\n"
            f"• 🏷️ <b>ᴀᴜᴛᴏ-ᴛᴀɢ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛs:</b> <code>{_b('auto_tag_enabled')}</code>\n"
            f"• 📝 <b>ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ ᴍᴇᴅɪᴀ:</b> <code>{_b('auto_caption_enabled', True)}</code>\n"
            f"• 🎬 <b>ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ (4 ᴘʟᴀʏᴇʀs):</b> <code>{_b('auto_stream_enabled')}</code>\n"
            f"• 🔒 <b>ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ (ɴᴏ ғᴏʀᴡᴀʀᴅ):</b> <code>{_b('protect_posts')}</code>\n"
            f"• 🔕 <b>sɪʟᴇɴᴛ ʙʀᴏᴀᴅᴄᴀsᴛ ᴘᴏsᴛs:</b> <code>{_b('silent_posts')}</code>\n"
            f"• 🌐 <b>ᴅɪsᴀʙʟᴇ ʟɪɴᴋ ᴘʀᴇᴠɪᴇᴡ:</b> <code>{_b('disable_preview', True)}</code>\n\n"
            "👇 <i>ᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ᴛᴏɢɢʟᴇ ᴄʜᴀɴɴᴇʟ sᴇᴛᴛɪɴɢs ɪɴsᴛᴀɴᴛʟʏ:</i>"
        )
        rows = [
            [
                InlineKeyboardButton(
                    f"🏷️ ᴀᴜᴛᴏ-ᴛᴀɢ: {_b('auto_tag_enabled')}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}auto_tag_enabled",
                ),
                InlineKeyboardButton(
                    f"📝 ᴀᴜᴛᴏ-ᴄᴀᴘ: {_b('auto_caption_enabled', True)}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}auto_caption_enabled",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🎬 ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ: {_b('auto_stream_enabled')}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}auto_stream_enabled",
                ),
                InlineKeyboardButton(
                    f"🔒 ᴘʀᴏᴛᴇᴄᴛ: {_b('protect_posts')}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}protect_posts",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🔕 sɪʟᴇɴᴛ: {_b('silent_posts')}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}silent_posts",
                ),
                InlineKeyboardButton(
                    f"🌐 ɴᴏ ᴘʀᴇᴠɪᴇᴡ: {_b('disable_preview', True)}",
                    callback_data=f"chdesk{CH_SEP}toggle{CH_SEP}disable_preview",
                ),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"chdesk{CH_SEP}home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
                InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
            ],
        ]
        return text, InlineKeyboardMarkup(rows)
    else:
        text = (
            "📢 <b>ᴍᴍᴡ ʙᴏᴛᴢ ᴘʀᴏ ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇʀ &amp; ʜᴇʟᴘᴇʀ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 <b>ʀᴇǫᴜɪʀᴇᴍᴇɴᴛ:</b> ᴀᴅᴅ ᴛʜᴇ ʙᴏᴛ ᴀs <b>ᴀᴅᴍɪɴ</b> ɪɴ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ.\n"
            "🔐 <b>ᴀᴄᴄᴇss:</b> ᴄʜᴀɴɴᴇʟ ᴏᴡɴᴇʀ • ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs • ʙᴏᴛ ᴀᴅᴍɪɴs\n"
            f"🔗 <b>ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟ:</b> {conn_badge}\n\n"
            "📂 <b>ᴄʜᴀɴɴᴇʟ ᴍᴏᴅᴜʟᴇs:</b>\n"
            "• 🔗 <b>ᴄᴏɴɴᴇᴄᴛ:</b> <code>/connect &lt;id/@username&gt;</code>, <code>/disconnect</code>, <code>/connections</code>\n"
            "• 📤 <b>ᴘᴏsᴛɪɴɢ:</b> <code>/cpost</code>, <code>/cschedule</code>, <code>/cedit</code>, <code>/cdel</code>, <code>/cpin</code>, <code>/cunpin</code>, <code>/ccopy</code>, <code>/cbatch</code>\n"
            "• 🎨 <b>ᴀᴜᴛᴏ &amp; ᴛᴇᴍᴘʟᴀᴛᴇs:</b> <code>/csetheader</code>, <code>/csetfooter</code>, <code>/csetbutton</code>, <code>/cautotag</code>, <code>/cautocaption</code>, <code>/cautostream</code>, <code>/cautoreact</code>, <code>/cautodel</code>\n"
            "• 🏷️ <b>ʙʀᴀɴᴅɪɴɢ:</b> <code>/ctitle</code>, <code>/cdesc</code>, <code>/cpic</code>, <code>/cinvite</code>, <code>/cadmins</code>, <code>/cstats</code>, <code>/cbackup</code>\n\n"
            "👇 <b>sᴇʟᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ᴍᴏᴅᴜʟᴇ ʙᴇʟᴏᴡ:</b>"
        )

    bottom_row = (
        [
            InlineKeyboardButton("🛡️ ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ]
        if section == "home"
        else [
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"chdesk{CH_SEP}home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ]
    )
    nav_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 ᴘᴏsᴛɪɴɢ & sᴄʜᴇᴅᴜʟᴇ", callback_data=f"chdesk{CH_SEP}posting"),
            InlineKeyboardButton("🎨 ᴀᴜᴛᴏ & ᴛᴇᴍᴘʟᴀᴛᴇs", callback_data=f"chdesk{CH_SEP}templates"),
        ],
        [
            InlineKeyboardButton("🏷️ ʙʀᴀɴᴅɪɴɢ & ɪɴғᴏ", callback_data=f"chdesk{CH_SEP}branding"),
            InlineKeyboardButton("⚙️ ʟɪᴠᴇ sᴇᴛᴛɪɴɢs", callback_data=f"chdesk{CH_SEP}settings"),
        ],
        [
            InlineKeyboardButton("🎛️ ᴄʜᴀɴɴᴇʟ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ", callback_data="cfeat|open"),
            InlineKeyboardButton("🎨 ᴘᴏsᴛ & ʙᴜᴛᴛᴏɴ sᴛᴜᴅɪᴏ", callback_data="postedit|home"),
        ],
        [
            InlineKeyboardButton("🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ", callback_data="autofwd|home"),
            InlineKeyboardButton("⚡ ᴀᴜᴛᴏ-sᴇᴛ", callback_data="autoset|home"),
        ],
        bottom_row,
    ])
    return text, nav_buttons


@Client.on_message(filters.command(["channelhelp", "chhelp", "cpanel"], prefixes=COMMAND_PREFIXES))
async def channel_help_cmd(client: Client, message: Message):
    """Open the interactive Pro Channel Manager & Helper panel."""
    uid = message.from_user.id if message.from_user else 0
    cid = message.chat.id if message.chat.type == enums.ChatType.CHANNEL else None
    logger.info("📢 /channelhelp ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s", uid, message.chat.id)
    text, markup = await build_channel_help_view(client, uid, "home", channel_id=cid)
    await message.reply_text(
        text,
        reply_markup=markup,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("connect", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def connect_channel_cmd(client: Client, message: Message):
    """Connect a Telegram channel to manage it from PM or group."""
    uid = message.from_user.id if message.from_user else 0
    if not uid:
        return await message.reply_text("⚠️ <b>ᴘʟᴇᴀsᴇ ʀᴜɴ <code>/connect</code> ғʀᴏᴍ ʏᴏᴜʀ ᴜsᴇʀ ᴀᴄᴄᴏᴜɴᴛ.</b>", parse_mode=enums.ParseMode.HTML)

    target_arg = message.command[1].strip() if len(message.command) > 1 else None
    if not target_arg and message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        target_arg = str(message.forward_from_chat.id)
    elif not target_arg and message.reply_to_message and message.reply_to_message.forward_from_chat:
        target_arg = str(message.reply_to_message.forward_from_chat.id)

    if not target_arg:
        return await message.reply_text(
            "🔗 <b>ʜᴏᴡ ᴛᴏ ᴄᴏɴɴᴇᴄᴛ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ:</b>\n\n"
            "1️⃣ ᴀᴅᴅ ᴍᴇ ᴀs ᴀɴ <b>ᴀᴅᴍɪɴ</b> ɪɴ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ.\n"
            "2️⃣ sᴇɴᴅ <code>/connect -100xxxxxxxxxx</code> ᴏʀ <code>/connect @channel_username</code>\n"
            "<i>(ᴏʀ ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ʀᴇᴘʟʏ ᴡɪᴛʜ <code>/connect</code>)</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message, explicit_arg=target_arg)
    if not ok or not chat_obj:
        return

    await db.connect_user_channel(uid, chat_obj.id, title=chat_obj.title or "Channel")
    members_cnt = 0
    try:
        members_cnt = await client.get_chat_members_count(chat_obj.id)
    except Exception:
        pass

    if LOG_CHANNEL:
        try:
            await client.send_message(
                LOG_CHANNEL,
                script.LOG_TEXT_C.format(
                    html.escape(chat_obj.title or "Channel"),
                    chat_obj.id,
                    members_cnt,
                    message.from_user.mention,
                ),
            )
        except Exception:
            pass

    text, markup = await build_channel_help_view(client, uid, "home", channel_id=chat_obj.id)
    await message.reply_text(
        f"✅ <b>ᴄʜᴀɴɴᴇʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b> 🎉\n"
        f"• <b>ᴛɪᴛʟᴇ:</b> <b>{html.escape(chat_obj.title or 'Channel')}</b>\n"
        f"• <b>ɪᴅ:</b> <code>{chat_obj.id}</code> │ <b>sᴜʙsᴄʀɪʙᴇʀs:</b> <code>{members_cnt:,}</code>\n\n"
        + text,
        reply_markup=markup,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("disconnect", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def disconnect_channel_cmd(client: Client, message: Message):
    """Disconnect the currently connected channel."""
    uid = message.from_user.id if message.from_user else 0
    conn = await db.get_user_connected_channel(uid)
    if not conn:
        return await message.reply_text("ℹ️ <b>ʏᴏᴜ ᴅᴏ ɴᴏᴛ ʜᴀᴠᴇ ᴀɴ ᴀᴄᴛɪᴠᴇ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    await db.disconnect_user_channel(uid)
    await message.reply_text(
        f"🔌 <b>ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ ғʀᴏᴍ ᴄʜᴀɴɴᴇʟ <code>{conn}</code>!</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["connections", "mychannels"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def list_connections_cmd(client: Client, message: Message):
    """View active and previously connected channels."""
    uid = message.from_user.id if message.from_user else 0
    active_cid = await db.get_user_connected_channel(uid)
    ch_list = await db.get_user_channels(uid)

    if not active_cid and not ch_list:
        return await message.reply_text(
            "ℹ️ <b>ɴᴏ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs ғᴏᴜɴᴅ.</b>\n"
            "ᴜsᴇ <code>/connect -100xxxxxxxxxx</code> ᴛᴏ ᴄᴏɴɴᴇᴄᴛ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ!",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    lines = ["📢 <b>ʏᴏᴜʀ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs</b> ⚡\n━━━━━━━━━━━━━━━━━━━━━━"]
    for ch in ch_list:
        cid = ch.get("chat_id")
        title = html.escape(str(ch.get("title", "Channel")))
        mark = "🟢 <b>[ᴀᴄᴛɪᴠᴇ]</b>" if cid == active_cid else "⚪"
        lines.append(f"• {mark} <b>{title}</b> — <code>{cid}</code>")

    await message.reply_text("\n".join(lines), quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("cpost", prefixes=COMMAND_PREFIXES))
async def channel_post_cmd(client: Client, message: Message):
    """Publish a formatted post or media message to the connected/current channel."""
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    raw_text = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    reply = message.reply_to_message

    if not raw_text and not reply:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b>\n"
            "• <code>/cpost Hello subscribers! [📢 Join](buttonurl:https://t.me/...)</code>\n"
            "• ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴘʜᴏᴛᴏ/ᴠɪᴅᴇᴏ/ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/cpost [custom caption]</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    base_text = raw_text or (reply.text or reply.caption or "" if reply else "")
    final_text, markup = _apply_channel_template(base_text, cfg)
    protect = bool(cfg.get("protect_posts", False))
    silent = bool(cfg.get("silent_posts", False))
    no_preview = bool(cfg.get("disable_preview", True))

    try:
        if reply and reply.media:
            sent = await reply.copy(
                chat_id=chat_obj.id,
                caption=final_text[:1024] if final_text else None,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                protect_content=protect,
                disable_notification=silent,
            )
        else:
            sent = await client.send_message(
                chat_id=chat_obj.id,
                text=final_text or "✨",
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=no_preview,
                protect_content=protect,
                disable_notification=silent,
            )
        logger.info("📤 /cpost ᴘᴜʙʟɪsʜᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s ᴍsɢ_ɪᴅ=%s", chat_obj.id, sent.id)
        if message.chat.id != chat_obj.id:
            await message.reply_text(
                f"✅ <b>ᴘᴏsᴛ ᴘᴜʙʟɪsʜᴇᴅ ᴛᴏ {html.escape(chat_obj.title or 'Channel')}!</b> 📢\n"
                f"• <b>ᴍᴇssᴀɢᴇ ɪᴅ:</b> <code>{sent.id}</code>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            try:
                await message.delete()
            except Exception:
                pass
    except Exception as e:
        logger.error("❌ /cpost ғᴀɪʟᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s ᴇʀʀ=%s", chat_obj.id, e)
        await message.reply_text(f"❌ <b>ᴘᴏsᴛ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("cschedule", prefixes=COMMAND_PREFIXES))
async def channel_schedule_post_cmd(client: Client, message: Message):
    """Schedule a delayed post or media message to the connected/current channel."""
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    parts = message.text.split(None, 2) if message.text else []
    reply = message.reply_to_message
    if len(parts) < 2:
        return await message.reply_text(
            "⏱️ <b>sᴄʜᴇᴅᴜʟᴇ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ:</b>\n"
            "• <code>/cschedule 10m Hello Channel!</code>\n"
            "• ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇᴅɪᴀ ᴡɪᴛʜ <code>/cschedule 30m [custom caption]</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    delay_s = _parse_delay_seconds(parts[1])
    if delay_s <= 0 or delay_s > 7 * 86400:
        return await message.reply_text(
            "⚠️ <b>ɪɴᴠᴀʟɪᴅ ᴅᴇʟᴀʏ!</b> ᴜsᴇ <code>30s</code>, <code>10m</code>, <code>2h</code>, ᴏʀ <code>1d</code> (ᴍᴀx 7 ᴅᴀʏs).",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    raw_text = parts[2].strip() if len(parts) > 2 else ""
    if not raw_text and not reply:
        return await message.reply_text("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴘᴏsᴛ ᴛᴇxᴛ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    base_text = raw_text or (reply.text or reply.caption or "" if reply else "")
    final_text, markup = _apply_channel_template(base_text, cfg)
    protect = bool(cfg.get("protect_posts", False))
    silent = bool(cfg.get("silent_posts", False))
    no_preview = bool(cfg.get("disable_preview", True))

    await message.reply_text(
        f"⏱️ <b>ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ sᴄʜᴇᴅᴜʟᴇᴅ!</b> 🚀\n"
        f"• 📢 <b>ᴄʜᴀɴɴᴇʟ:</b> {html.escape(chat_obj.title or 'Channel')} (<code>{chat_obj.id}</code>)\n"
        f"• ⏳ <b>ᴘᴜʙʟɪsʜɪɴɢ ɪɴ:</b> <code>{get_readable_time(delay_s)}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )

    async def _delayed_publish():
        await asyncio.sleep(delay_s)
        try:
            if reply and reply.media:
                await reply.copy(
                    chat_id=chat_obj.id,
                    caption=final_text[:1024] if final_text else None,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                    protect_content=protect,
                    disable_notification=silent,
                )
            else:
                await client.send_message(
                    chat_id=chat_obj.id,
                    text=final_text or "✨",
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=no_preview,
                    protect_content=protect,
                    disable_notification=silent,
                )
            logger.info("⏰ sᴄʜᴇᴅᴜʟᴇᴅ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ ᴘᴜʙʟɪsʜᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s", chat_obj.id)
        except Exception as e:
            logger.error("❌ sᴄʜᴇᴅᴜʟᴇᴅ ᴘᴏsᴛ ғᴀɪʟᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s ᴇʀʀ=%s", chat_obj.id, e)

    asyncio.create_task(_delayed_publish())


@Client.on_message(filters.command("cedit", prefixes=COMMAND_PREFIXES))
async def channel_edit_cmd(client: Client, message: Message):
    """Edit an existing message or caption in the channel."""
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    msg_id: Optional[int] = None
    new_text = ""

    if message.reply_to_message and message.chat.id == chat_obj.id:
        msg_id = message.reply_to_message.id
        new_text = message.text.split(None, 1)[1].strip() if len(message.text.split(None, 1)) > 1 else ""
    elif len(message.command) >= 3:
        msg_id = _extract_msg_id(message.command[1])
        new_text = message.text.split(None, 2)[2].strip()

    if not msg_id or not new_text:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/cedit &lt;msg_id_or_link&gt; &lt;new_text&gt;</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    final_text, markup = _apply_channel_template(new_text, cfg)
    try:
        target_msg = await client.get_messages(chat_obj.id, msg_id)
        await safe_edit_message(target_msg, final_text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        logger.info("✏️ /cedit sᴜᴄᴄᴇss › ᴄʜᴀɴɴᴇʟ=%s ᴍsɢ_ɪᴅ=%s", chat_obj.id, msg_id)
        await message.reply_text(
            f"✅ <b>ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ <code>{msg_id}</code> ᴜᴘᴅᴀᴛᴇᴅ!</b> ✏️",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴇᴅɪᴛ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("cdel", prefixes=COMMAND_PREFIXES))
async def channel_del_cmd(client: Client, message: Message):
    """Delete a post in the channel."""
    ok, chat_obj, _ = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj:
        return

    msg_id = (
        message.reply_to_message.id
        if (message.reply_to_message and message.chat.id == chat_obj.id)
        else (_extract_msg_id(message.command[1]) if len(message.command) > 1 else None)
    )
    if not msg_id:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/cdel &lt;msg_id_or_link&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        await client.delete_messages(chat_obj.id, [msg_id])
        logger.info("🗑️ /cdel sᴜᴄᴄᴇss › ᴄʜᴀɴɴᴇʟ=%s ᴍsɢ_ɪᴅ=%s", chat_obj.id, msg_id)
        await message.reply_text(f"🗑️ <b>ᴅᴇʟᴇᴛᴇᴅ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ <code>{msg_id}</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴅᴇʟᴇᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["cpin", "cunpin", "cunpinall"], prefixes=COMMAND_PREFIXES))
async def channel_pin_cmds(client: Client, message: Message):
    """Pin or unpin messages in the channel."""
    ok, chat_obj, _ = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj:
        return

    cmd = message.command[0].lower()
    if cmd == "cunpinall":
        try:
            await client.unpin_all_chat_messages(chat_obj.id)
            logger.info("📌 /cunpinall › ᴄʜᴀɴɴᴇʟ=%s", chat_obj.id)
            return await message.reply_text(
                f"📌 <b>ᴜɴᴘɪɴɴᴇᴅ ᴀʟʟ ᴍᴇssᴀɢᴇs ɪɴ {html.escape(chat_obj.title or 'Channel')}!</b>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    msg_id = (
        message.reply_to_message.id
        if (message.reply_to_message and message.chat.id == chat_obj.id)
        else (_extract_msg_id(message.command[1]) if len(message.command) > 1 else None)
    )
    if not msg_id:
        return await message.reply_text(f"⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} &lt;msg_id_or_link&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        if cmd == "cpin":
            loud = "loud" in message.text.lower()
            await client.pin_chat_message(chat_obj.id, msg_id, disable_notification=not loud)
            await message.reply_text(f"📌 <b>ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ <code>{msg_id}</code> ɪɴ {html.escape(chat_obj.title or 'Channel')}!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        else:
            await client.unpin_chat_message(chat_obj.id, msg_id)
            await message.reply_text(f"📌 <b>ᴜɴᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ <code>{msg_id}</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["ctitle", "cdesc", "cpic", "cdelpic"], prefixes=COMMAND_PREFIXES))
async def channel_branding_cmds(client: Client, message: Message):
    """Update channel title, description/bio, or photo."""
    ok, chat_obj, _ = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj:
        return

    cmd = message.command[0].lower()
    try:
        if cmd == "ctitle":
            if len(message.command) < 2:
                return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/ctitle &lt;new_title&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
            new_title = message.text.split(None, 1)[1].strip()[:128]
            await client.set_chat_title(chat_obj.id, new_title)
            await db.update_channel_config(chat_obj.id, {"title": new_title}, title=new_title)
            await message.reply_text(f"✅ <b>ᴄʜᴀɴɴᴇʟ ᴛɪᴛʟᴇ ᴜᴘᴅᴀᴛᴇᴅ ᴛᴏ:</b> <code>{html.escape(new_title)}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

        elif cmd == "cdesc":
            if len(message.command) < 2:
                return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/cdesc &lt;new_description&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
            new_desc = message.text.split(None, 1)[1].strip()[:255]
            await client.set_chat_description(chat_obj.id, new_desc)
            await message.reply_text("✅ <b>ᴄʜᴀɴɴᴇʟ ᴅᴇsᴄʀɪᴘᴛɪᴏɴ ᴜᴘᴅᴀᴛᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

        elif cmd == "cpic":
            if not message.reply_to_message or not message.reply_to_message.photo:
                return await message.reply_text("⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴡɪᴛʜ <code>/cpic</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
            photo_path = await client.download_media(message.reply_to_message.photo.file_id)
            await client.set_chat_photo(chat_obj.id, photo=photo_path)
            await message.reply_text("🖼️ <b>ᴄʜᴀɴɴᴇʟ ᴘʜᴏᴛᴏ ᴜᴘᴅᴀᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

        elif cmd == "cdelpic":
            await client.delete_chat_photo(chat_obj.id)
            await message.reply_text("🗑️ <b>ᴄʜᴀɴɴᴇʟ ᴘʜᴏᴛᴏ ʀᴇᴍᴏᴠᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["cinvite", "crevoke"], prefixes=COMMAND_PREFIXES))
async def channel_invite_cmds(client: Client, message: Message):
    """Export or revoke channel invite links."""
    ok, chat_obj, _ = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj:
        return

    cmd = message.command[0].lower()
    try:
        if cmd == "cinvite":
            link = await client.create_chat_invite_link(chat_obj.id)
            await message.reply_text(
                f"🔗 <b>ᴄʜᴀɴɴᴇʟ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ({html.escape(chat_obj.title or 'Channel')}):</b>\n"
                f"<code>{link.invite_link}</code>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            curr = await client.export_chat_invite_link(chat_obj.id)
            await message.reply_text(
                f"♻️ <b>ᴘʀɪᴍᴀʀʏ ᴄʜᴀɴɴᴇʟ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ʀᴇɢᴇɴᴇʀᴀᴛᴇᴅ:</b>\n<code>{curr}</code>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["cadmins", "cstats", "cbackup", "cexport"], prefixes=COMMAND_PREFIXES))
async def channel_info_cmds(client: Client, message: Message):
    """List channel administrators, view channel statistics, or export JSON backup."""
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    cmd = message.command[0].lower()
    if cmd == "cadmins":
        lines = [f"👮 <b>ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs — {html.escape(chat_obj.title or 'Channel')}</b> ⚡\n━━━━━━━━━━━━━━━━━━━━━━"]
        try:
            async for m in client.get_chat_members(chat_obj.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                if m.user:
                    role = "👑 ᴏᴡɴᴇʀ" if m.status == enums.ChatMemberStatus.OWNER else "👮 ᴀᴅᴍɪɴ"
                    bot_tag = " 🤖" if m.user.is_bot else ""
                    lines.append(f"• {role}: {m.user.mention}{bot_tag} (<code>{m.user.id}</code>)")
        except Exception as e:
            lines.append(f"❌ <code>{html.escape(str(e))}</code>")
        return await message.reply_text("\n".join(lines), quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd in ("cbackup", "cexport"):
        payload = json.dumps(cfg, indent=2, default=str)
        bio = io.BytesIO(payload.encode("utf-8"))
        bio.name = f"channel_backup_{chat_obj.id}.json"
        logger.info("📦 /cbackup ᴇxᴘᴏʀᴛᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s", chat_obj.id)
        return await message.reply_document(
            document=bio,
            caption=f"📦 <b>ᴄʜᴀɴɴᴇʟ ᴄᴏɴғɪɢ ʙᴀᴄᴋᴜᴘ — {html.escape(chat_obj.title or 'Channel')} (<code>{chat_obj.id}</code>)</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    subs = 0
    try:
        subs = await client.get_chat_members_count(chat_obj.id)
    except Exception:
        pass
    uname = f"@{chat_obj.username}" if chat_obj.username else "<code>ᴘʀɪᴠᴀᴛᴇ</code>"
    await message.reply_text(
        f"📊 <b>ᴄʜᴀɴɴᴇʟ sᴛᴀᴛɪsᴛɪᴄs &amp; ᴄᴏɴғɪɢ</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴛɪᴛʟᴇ:</b> <b>{html.escape(chat_obj.title or 'Channel')}</b>\n"
        f"• <b>ɪᴅ:</b> <code>{chat_obj.id}</code>\n"
        f"• <b>ᴜsᴇʀɴᴀᴍᴇ:</b> {uname}\n"
        f"• <b>sᴜʙsᴄʀɪʙᴇʀs:</b> <code>{subs:,}</code>\n"
        f"• <b>ᴀᴜᴛᴏ-ᴛᴀɢ:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_tag_enabled') else '🔴 ᴏғғ'}</code>\n"
        f"• <b>ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ:</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_caption_enabled', True) else '🔴 ᴏғғ'}</code>\n"
        f"• <b>ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ (4 ᴘʟᴀʏᴇʀs):</b> <code>{'🟢 ᴏɴ' if cfg.get('auto_stream_enabled') else '🔴 ᴏғғ'}</code>\n"
        f"• <b>ᴘʀᴏᴛᴇᴄᴛ ᴘᴏsᴛs:</b> <code>{'🟢 ᴏɴ' if cfg.get('protect_posts') else '🔴 ᴏғғ'}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(
    filters.command(
        ["csetheader", "csetfooter", "csetbutton", "cautotag", "cautocaption", "cautostream", "cautoreact", "cautodel"],
        prefixes=COMMAND_PREFIXES,
    )
)
async def channel_template_cmds(client: Client, message: Message):
    """Configure automatic channel post header, footer, URL button, auto-tag, auto-caption, auto-stream, auto-react, and auto-delete."""
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    cmd = message.command[0].lower()
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""

    if cmd == "csetheader":
        if not arg:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/csetheader &lt;text|off&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        val = "" if arg.lower() in ("off", "none", "clear", "reset") else arg
        await db.update_channel_config(chat_obj.id, {"header_text": val}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>ᴄʜᴀɴɴᴇʟ ʜᴇᴀᴅᴇʀ {'ᴄʟᴇᴀʀᴇᴅ' if not val else 'ᴜᴘᴅᴀᴛᴇᴅ'}!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "csetfooter":
        if not arg:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/csetfooter &lt;text|off&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        val = "" if arg.lower() in ("off", "none", "clear", "reset") else arg
        await db.update_channel_config(chat_obj.id, {"footer_text": val}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>ᴄʜᴀɴɴᴇʟ ғᴏᴏᴛᴇʀ {'ᴄʟᴇᴀʀᴇᴅ' if not val else 'ᴜᴘᴅᴀᴛᴇᴅ'}!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "csetbutton":
        if not arg:
            return await message.reply_text(
                "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/csetbutton 📢 Join Channel | https://t.me/mallumovieworldmain1</code>\n"
                "<i>(ᴏʀ <code>/csetbutton off</code> ᴛᴏ ʀᴇᴍᴏᴠᴇ)</i>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        if arg.lower() in ("off", "none", "clear", "reset"):
            await db.update_channel_config(chat_obj.id, {"button_text": "", "button_url": ""}, title=chat_obj.title or "")
            return await message.reply_text("✅ <b>ᴄʜᴀɴɴᴇʟ ᴅᴇғᴀᴜʟᴛ ʙᴜᴛᴛᴏɴ ʀᴇᴍᴏᴠᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        if "|" not in arg:
            return await message.reply_text("⚠️ <b>sᴇᴘᴀʀᴀᴛᴇ ʟᴀʙᴇʟ ᴀɴᴅ ᴜʀʟ ᴡɪᴛʜ <code>|</code>:</b>\n<code>/csetbutton Label | https://...</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        lbl, url = [x.strip() for x in arg.split("|", 1)]
        await db.update_channel_config(chat_obj.id, {"button_text": lbl, "button_url": url}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>ᴄʜᴀɴɴᴇʟ ʙᴜᴛᴛᴏɴ sᴀᴠᴇᴅ:</b> <code>{html.escape(lbl)}</code> ➔ <code>{html.escape(url)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cautotag":
        new_state = not bool(cfg.get("auto_tag_enabled", False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_channel_config(chat_obj.id, {"auto_tag_enabled": new_state}, title=chat_obj.title or "")
        return await message.reply_text(
            f"🏷️ <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴛᴀɢ:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if new_state else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cautocaption":
        if not arg:
            new_state = not bool(cfg.get("auto_caption_enabled", True))
            await db.update_channel_config(chat_obj.id, {"auto_caption_enabled": new_state}, title=chat_obj.title or "")
            return await message.reply_text(
                f"📝 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if new_state else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>\n"
                f"💡 <i>ᴜsᴇ <code>/cautocaption &lt;template&gt;</code> ᴛᴏ sᴇᴛ ᴀ ᴄᴜsᴛᴏᴍ ᴄʜᴀɴɴᴇʟ ᴄᴀᴘᴛɪᴏɴ!</i>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        if arg.lower() in ("off", "disable", "0", "false"):
            await db.update_channel_config(chat_obj.id, {"auto_caption_enabled": False, "custom_caption": ""}, title=chat_obj.title or "")
            return await message.reply_text("🔴 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        if arg.lower() in ("on", "enable", "1", "true"):
            await db.update_channel_config(chat_obj.id, {"auto_caption_enabled": True}, title=chat_obj.title or "")
            return await message.reply_text("🟢 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴄᴀᴘᴛɪᴏɴ ᴇɴᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await db.update_channel_config(chat_obj.id, {"auto_caption_enabled": True, "custom_caption": arg}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>ᴄʜᴀɴɴᴇʟ ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ sᴀᴠᴇᴅ!</b>\n<code>{html.escape(arg)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cautostream":
        new_state = not bool(cfg.get("auto_stream_enabled", False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_channel_config(chat_obj.id, {"auto_stream_enabled": new_state}, title=chat_obj.title or "")
        return await message.reply_text(
            f"🎬 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-sᴛʀᴇᴀᴍ (4 ᴘʟᴀʏᴇʀs):</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if new_state else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cautoreact":
        if not arg or arg.lower() in ("off", "none", "disable"):
            await db.update_channel_config(chat_obj.id, {"auto_react_emoji": ""}, title=chat_obj.title or "")
            return await message.reply_text("⚪ <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        emoji_val = arg.split()[0][:4]
        await db.update_channel_config(chat_obj.id, {"auto_react_emoji": emoji_val}, title=chat_obj.title or "")
        return await message.reply_text(f"🎭 <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ sᴇᴛ ᴛᴏ:</b> {html.escape(emoji_val)}", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "cautodel":
        if not arg or arg.lower() in ("off", "0", "disable"):
            await db.update_channel_config(chat_obj.id, {"auto_delete_seconds": 0}, title=chat_obj.title or "")
            return await message.reply_text("⏱️ <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        sec = _parse_delay_seconds(arg)
        if sec <= 0:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/cautodel 10m</code> ᴏʀ <code>/cautodel off</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        await db.update_channel_config(chat_obj.id, {"auto_delete_seconds": sec}, title=chat_obj.title or "")
        return await message.reply_text(
            f"⏱️ <b>ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ sᴇᴛ ᴛᴏ:</b> <code>{get_readable_time(sec)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_callback_query(filters.regex(r"^chdesk\|"))
async def channel_help_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split(CH_SEP)
    action = parts[1] if len(parts) > 1 else "home"
    chat_id = query.message.chat.id if (query.message and query.message.chat.type == enums.ChatType.CHANNEL) else None

    logger.info("📢 ᴄʜᴅᴇsᴋ_ᴄʙ › ᴜsᴇʀ=%s ᴀᴄᴛɪᴏɴ=%s", uid, action)

    if action in ("home", "posting", "templates", "branding", "settings"):
        await query.answer()
        text, markup = await build_channel_help_view(client, uid, action, channel_id=chat_id)
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    if action == "toggle" and len(parts) > 2:
        field = parts[2]
        conn_id = chat_id or await db.get_user_connected_channel(uid)
        if not conn_id:
            return await query.answer("⚠️ ᴄᴏɴɴᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ ᴡɪᴛʜ /connect!", show_alert=True)
        if not await is_chat_or_global_admin(client, conn_id, user_id=uid):
            return await query.answer("⛔ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)
        cfg = await db.get_channel_config(conn_id)
        new_val = not bool(cfg.get(field, False))
        await db.update_channel_config(conn_id, {field: new_val})
        await query.answer(f"✅ {field}: {'ᴏɴ' if new_val else 'ᴏғғ'}")
        text, markup = await build_channel_help_view(client, uid, "settings", channel_id=conn_id)
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return


@Client.on_message(filters.channel & ~filters.service, group=4)
async def channel_auto_template_watchdog(client: Client, message: Message):
    """Automatically append configured header/footer/button and auto-react to new channel posts."""
    if not message.chat or message.chat.type != enums.ChatType.CHANNEL:
        return
    txt_or_cap = message.text or message.caption or ""
    if txt_or_cap.startswith(tuple(COMMAND_PREFIXES)):
        return

    cfg = await db.get_channel_config(message.chat.id, title=message.chat.title or "Channel")
    blocked_words = cfg.get("blocked_words") or []
    if blocked_words and txt_or_cap:
        low_txt = txt_or_cap.lower()
        if any(str(w).lower() in low_txt for w in blocked_words if w):
            try:
                await message.delete()
                logger.info("🚫 ᴄʜᴀɴɴᴇʟ ʙʟᴏᴄᴋᴇᴅ ᴡᴏʀᴅ ᴘᴏsᴛ ᴅᴇʟᴇᴛᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s ᴍsɢ=%s", message.chat.id, message.id)
            except Exception:
                pass
            return
    rep_rules = cfg.get("replace_rules") or {}
    if rep_rules and txt_or_cap:
        for old_w, new_w in rep_rules.items():
            if old_w in txt_or_cap:
                txt_or_cap = txt_or_cap.replace(old_w, new_w)

    if not message.media:
        react_emoji = (cfg.get("auto_react_emoji") or "").strip()
        if react_emoji:
            try:
                await client.send_reaction(message.chat.id, message.id, react_emoji)
            except Exception:
                pass

    if not cfg.get("auto_tag_enabled"):
        return

    header = (cfg.get("header_text") or "").strip()
    footer = (cfg.get("footer_text") or "").strip()
    btn_text = (cfg.get("button_text") or "").strip()
    btn_url = (cfg.get("button_url") or "").strip()

    if not (header or footer or (btn_text and btn_url)):
        return

    if footer and footer in txt_or_cap:
        return

    new_text, new_markup = _apply_channel_template(txt_or_cap, cfg, existing_markup=message.reply_markup)
    try:
        if message.text is not None:
            await message.edit_text(
                new_text,
                reply_markup=new_markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=bool(cfg.get("disable_preview", True)),
            )
        elif message.media:
            await message.edit_caption(
                caption=new_text[:1024],
                reply_markup=new_markup,
                parse_mode=enums.ParseMode.HTML,
            )
        logger.info("🏷️ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴛᴀɢ ᴀᴘᴘʟɪᴇᴅ › ᴄʜᴀɴɴᴇʟ=%s ᴍsɢ_ɪᴅ=%s", message.chat.id, message.id)
    except Exception as e:
        logger.debug("⚠️ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴛᴀɢ sᴋɪᴘ › %s", e)
