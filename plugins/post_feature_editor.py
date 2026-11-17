import html
import io
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from config import Config
from database.users_chats_db import db
from info import ADMINS, COMMAND_PREFIXES
from utils import (
    format_watermark_html,
    get_readable_time,
    is_admin_sync,
    is_chat_or_global_admin,
    parse_Inline_buttons_from_text,
    safe_edit_message,
    to_smallcaps,
)

logger = logging.getLogger("MMWPostFeatureEditor")

PE_SEP = "|"
_TG_POST_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/(\d+)/|([A-Za-z0-9_]+)/)(\d+)")
_HANDLE_OR_TME_RE = re.compile(r"(@[A-Za-z0-9_]{4,}|https?://t\.me/[A-Za-z0-9_+/]+)", re.I)


def _parse_post_link(raw: str) -> Tuple[Optional[Union[int, str]], Optional[int]]:
    m = _TG_POST_LINK_RE.search(str(raw or "").strip())
    if not m:
        return None, None
    priv_cid, pub_uname, mid_s = m.group(1), m.group(2), m.group(3)
    mid = int(mid_s)
    if priv_cid:
        return int(f"-100{priv_cid}"), mid
    return f"@{pub_uname}", mid


def _build_custom_url_keyboard(
    raw_spec: str,
    existing_markup: Optional[InlineKeyboardMarkup] = None,
    append: bool = False,
) -> Optional[InlineKeyboardMarkup]:
    rows: List[List[InlineKeyboardButton]] = []
    if append and existing_markup and existing_markup.inline_keyboard:
        for r in existing_markup.inline_keyboard:
            rows.append(list(r))

    for line in (raw_spec or "").splitlines():
        line = line.strip()
        if not line:
            continue
        row_btns: List[InlineKeyboardButton] = []
        segments = [seg.strip() for seg in line.split("||") if seg.strip()]
        for seg in segments:
            if "|" not in seg:
                continue
            label, url = [x.strip() for x in seg.split("|", 1)]
            if not label or not url:
                continue
            if not url.startswith(("http://", "https://", "tg://")):
                url = "https://" + url.lstrip("/")
            row_btns.append(InlineKeyboardButton(label, url=url))
        if row_btns:
            rows.append(row_btns)

    return InlineKeyboardMarkup(rows) if rows else None


async def _resolve_editable_message(
    client: Client,
    message: Message,
    arg_str: str = "",
) -> Tuple[bool, Optional[Message], str]:
    """
    Resolve a target message to edit from:
    1) Replied message in the current chat (Group, Channel, or PM)
    2) A t.me post link in arg_str
    3) A message_id in the user's connected channel
    And verify that caller is Group/Channel Admin/Owner or Bot ADMINS.
    """
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    if message.reply_to_message:
        target_msg = message.reply_to_message
        if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
            ok = await is_chat_or_global_admin(client, message.chat.id, user_id=uid, sender_chat_id=sender_cid)
            if not ok:
                return False, None, "⛔ ᴏɴʟʏ ɢʀᴏᴜᴘ/ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs, ᴏᴡɴᴇʀ, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ᴇᴅɪᴛ ᴘᴏsᴛs ʜᴇʀᴇ!"
        return True, target_msg, arg_str

    tokens = (arg_str or "").strip().split(None, 1)
    first_tok = tokens[0] if tokens else ""
    rest_text = tokens[1].strip() if len(tokens) > 1 else ""

    chat_ref, mid = _parse_post_link(first_tok)
    if chat_ref and mid:
        try:
            chat_obj = await client.get_chat(chat_ref)
            ok = await is_chat_or_global_admin(client, chat_obj.id, user_id=uid, sender_chat_id=sender_cid)
            if not ok:
                return False, None, f"⛔ ʏᴏᴜ ᴍᴜsᴛ ʙᴇ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ ᴏғ <code>{html.escape(chat_obj.title or str(chat_obj.id))}</code> (ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴ)!"
            t_msg = await client.get_messages(chat_obj.id, mid)
            if not t_msg or t_msg.empty:
                return False, None, "❌ ᴛᴀʀɢᴇᴛ ᴍᴇssᴀɢᴇ ɴᴏᴛ ғᴏᴜɴᴅ!"
            return True, t_msg, rest_text
        except Exception as e:
            return False, None, f"❌ ᴄᴀɴɴᴏᴛ ᴀᴄᴄᴇss ᴘᴏsᴛ: <code>{html.escape(str(e))}</code>"

    if first_tok.isdigit():
        conn_cid = await db.get_user_connected_channel(uid) if uid else None
        target_cid = conn_cid or (message.chat.id if message.chat.type != enums.ChatType.PRIVATE else None)
        if not target_cid:
            return False, None, "⚠️ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ, ᴘᴀss ᴀ <code>t.me/...</code> ᴘᴏsᴛ ʟɪɴᴋ, ᴏʀ ᴄᴏɴɴᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ᴡɪᴛʜ <code>/connect</code>!"
        ok = await is_chat_or_global_admin(client, target_cid, user_id=uid, sender_chat_id=sender_cid)
        if not ok:
            return False, None, "⛔ ᴏɴʟʏ ᴄʜᴀᴛ/ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs, ᴏᴡɴᴇʀ, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ᴇᴅɪᴛ ᴛʜɪs ᴘᴏsᴛ!"
        try:
            t_msg = await client.get_messages(target_cid, int(first_tok))
            if not t_msg or t_msg.empty:
                return False, None, f"❌ ᴍᴇssᴀɢᴇ <code>{first_tok}</code> ɴᴏᴛ ғᴏᴜɴᴅ!"
            return True, t_msg, rest_text
        except Exception as e:
            return False, None, f"❌ ᴇʀʀᴏʀ: <code>{html.escape(str(e))}</code>"

    return False, None, "⚠️ ᴘʟᴇᴀsᴇ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ</b> ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴀ <code>https://t.me/...</code> ᴘᴏsᴛ ʟɪɴᴋ / ᴍᴇssᴀɢᴇ ɪᴅ!"


async def _apply_in_place_edit(
    target_msg: Message,
    new_text: Optional[str],
    new_markup: Optional[InlineKeyboardMarkup],
    keep_existing_text: bool = False,
) -> Tuple[bool, str]:
    try:
        if keep_existing_text:
            await target_msg.edit_reply_markup(reply_markup=new_markup)
            return True, "✅ ɪɴʟɪɴᴇ ʙᴜᴛᴛᴏɴs ᴜᴘᴅᴀᴛᴇᴅ!"
        if target_msg.media:
            await target_msg.edit_caption(
                caption=(new_text or "")[:1024],
                reply_markup=new_markup,
                parse_mode=enums.ParseMode.HTML,
            )
            return True, "✅ ᴍᴇᴅɪᴀ ᴄᴀᴘᴛɪᴏɴ & ʙᴜᴛᴛᴏɴs ᴜᴘᴅᴀᴛᴇᴅ!"
        await target_msg.edit_text(
            text=(new_text or "✨")[:4096],
            reply_markup=new_markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True, "✅ ᴍᴇssᴀɢᴇ ᴛᴇxᴛ & ʙᴜᴛᴛᴏɴs ᴜᴘᴅᴀᴛᴇᴅ!"
    except Exception as e:
        return False, f"<code>{html.escape(str(e))}</code>"


def build_editstudio_home_view() -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        f"✦ <b>{to_smallcaps('pro post, media, button & feature editing studio')}</b> ✦ 🎨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 <b>{to_smallcaps('authorized roles')}:</b> ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ • ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ • ʙᴏᴛ ᴀᴅᴍɪɴs\n\n"
        f"✏️ <b>{to_smallcaps('1. live post & caption editing skills')}:</b>\n"
        f"• <code>/editstudio</code> — {to_smallcaps('reply to any post to open 1-click visual editor')}\n"
        f"• <code>/editpost [link/reply] &lt;text&gt;</code> — {to_smallcaps('edit text/caption + inline buttons')}\n"
        f"• <code>/editcaption &lt;caption&gt;</code> — {to_smallcaps('edit media caption keeping buttons')}\n"
        f"• <code>/formatpost &lt;smallcaps|bold|mono|quote|spoiler&gt;</code> — {to_smallcaps('style post in-place')}\n"
        f"• <code>/rebrand [old | new]</code> — {to_smallcaps('replace @handles & links with your brand')}\n"
        f"• <code>/editmedia &lt;target_link_or_id&gt;</code> — {to_smallcaps('swap photo/video/file of existing post')}\n\n"
        f"🔘 <b>{to_smallcaps('2. live inline button builder skills')}:</b>\n"
        f"• <code>/editbuttons Label | https://url</code> — {to_smallcaps('set single or multi-row buttons')}\n"
        f"• <code>/addbtn Label | https://url</code> — {to_smallcaps('append button to existing post')}\n"
        f"• <code>/delbuttons</code> — {to_smallcaps('strip all inline buttons from a post')}\n\n"
        f"⚙️ <b>{to_smallcaps('3. group & channel feature editors')}:</b>\n"
        f"• <code>/gsettings</code> — {to_smallcaps('interactive group feature & limits editor')}\n"
        f"• <code>/csettings</code> — {to_smallcaps('interactive channel feature & template editor')}\n"
        f"• <code>/gbackup</code> │ <code>/grestore</code> │ <code>/cbackup</code> │ <code>/crestore</code>"
        f"{format_watermark_html()}"
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group feature editor')}", callback_data="gfeat|open"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel feature editor')}", callback_data="cfeat|open"),
        ],
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    return text, buttons


def build_post_inspector_view(target_msg: Message) -> Tuple[str, InlineKeyboardMarkup]:
    cid = target_msg.chat.id
    mid = target_msg.id
    raw_txt = target_msg.text or target_msg.caption or ""
    preview = html.escape(raw_txt[:220]) + ("..." if len(raw_txt) > 220 else "")
    has_btns = bool(target_msg.reply_markup and getattr(target_msg.reply_markup, "inline_keyboard", None))
    btn_cnt = sum(len(r) for r in target_msg.reply_markup.inline_keyboard) if has_btns else 0
    mtype = str(target_msg.media.value if target_msg.media else "text").upper()

    text = (
        f"✦ <b>{to_smallcaps('live post & button inspector')}</b> ✦ 🎨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 💬 <b>{to_smallcaps('chat id')}:</b> <code>{cid}</code> │ 🆔 <b>{to_smallcaps('msg id')}:</b> <code>{mid}</code>\n"
        f"• 📦 <b>{to_smallcaps('post type')}:</b> <code>{mtype}</code> │ 🔘 <b>{to_smallcaps('buttons')}:</b> <code>{btn_cnt}</code>\n\n"
        f"📝 <b>{to_smallcaps('content preview')}:</b>\n"
        f"<blockquote>{preview or '<i>(ɴᴏ ᴛᴇxᴛ/ᴄᴀᴘᴛɪᴏɴ)</i>'}</blockquote>\n\n"
        f"👇 <b>{to_smallcaps('tap any 1-click live editing skill below')}:</b>"
        f"{format_watermark_html()}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✨ {to_smallcaps('small-caps style')}", callback_data=f"postedit{PE_SEP}fmt{PE_SEP}smallcaps{PE_SEP}{cid}{PE_SEP}{mid}"),
            InlineKeyboardButton(f"🅱️ {to_smallcaps('bold style')}", callback_data=f"postedit{PE_SEP}fmt{PE_SEP}bold{PE_SEP}{cid}{PE_SEP}{mid}"),
        ],
        [
            InlineKeyboardButton(f"💻 {to_smallcaps('monospace')}", callback_data=f"postedit{PE_SEP}fmt{PE_SEP}mono{PE_SEP}{cid}{PE_SEP}{mid}"),
            InlineKeyboardButton(f"❝ {to_smallcaps('blockquote')}", callback_data=f"postedit{PE_SEP}fmt{PE_SEP}quote{PE_SEP}{cid}{PE_SEP}{mid}"),
        ],
        [
            InlineKeyboardButton(f"🏷️ {to_smallcaps('rebrand @links')}", callback_data=f"postedit{PE_SEP}rebrand{PE_SEP}{cid}{PE_SEP}{mid}"),
            InlineKeyboardButton(f"📢 {to_smallcaps('add brand button')}", callback_data=f"postedit{PE_SEP}addwmbtn{PE_SEP}{cid}{PE_SEP}{mid}"),
        ],
        [
            InlineKeyboardButton(f"🚫 {to_smallcaps('strip buttons')}", callback_data=f"postedit{PE_SEP}delbtns{PE_SEP}{cid}{PE_SEP}{mid}"),
            InlineKeyboardButton(f"📌 {to_smallcaps('pin post')}", callback_data=f"postedit{PE_SEP}pin{PE_SEP}{cid}{PE_SEP}{mid}"),
        ],
        [
            InlineKeyboardButton(f"📋 {to_smallcaps('clone (no tag)')}", callback_data=f"postedit{PE_SEP}clone{PE_SEP}{cid}{PE_SEP}{mid}"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data=f"postedit{PE_SEP}inspect{PE_SEP}{cid}{PE_SEP}{mid}"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('studio home')}", callback_data=f"postedit{PE_SEP}home"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    return text, kb


@Client.on_message(filters.command(["editstudio", "posteditor", "editor"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def editstudio_cmd(client: Client, message: Message):
    """Open the interactive Post, Media, Button & Feature Editing Studio."""
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    if message.reply_to_message or arg:
        ok, target_msg, _ = await _resolve_editable_message(client, message, arg)
        if ok and target_msg:
            text, kb = build_post_inspector_view(target_msg)
            return await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    text, kb = build_editstudio_home_view()
    await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command(["editpost", "editmsg"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def editpost_cmd(client: Client, message: Message):
    """Edit text or caption (plus inline [Label](buttonurl:...) buttons) of a replied post or channel message."""
    raw_arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    ok, target_msg, new_body = await _resolve_editable_message(client, message, raw_arg)
    if not ok or not target_msg:
        return await message.reply_text(
            f"{new_body}\n\n"
            f"📝 <b>{to_smallcaps('usage')}:</b>\n"
            f"• {to_smallcaps('reply to a message with')} <code>/editpost New text [Join](buttonurl:https://t.me/...)</code>\n"
            f"• {to_smallcaps('or')} <code>/editpost https://t.me/channel/123 New text</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    if not new_body:
        return await message.reply_text(
            f"⚠️ <b>{to_smallcaps('please provide the new text/caption to apply!')}</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    cleaned_text, parsed_markup = parse_Inline_buttons_from_text(new_body)
    final_markup = parsed_markup if parsed_markup else target_msg.reply_markup
    success, status_note = await _apply_in_place_edit(target_msg, cleaned_text, final_markup)
    if not success:
        return await message.reply_text(f"❌ <b>{to_smallcaps('edit failed')}:</b> {status_note}", quote=True, parse_mode=enums.ParseMode.HTML)

    logger.info("✏️ /editpost › ᴄʜᴀᴛ=%s ᴍsɢ=%s ʙʏ=%s", target_msg.chat.id, target_msg.id, message.from_user.id if message.from_user else 0)
    await message.reply_text(
        f"{status_note}\n• 💬 <b>{to_smallcaps('chat')}:</b> <code>{target_msg.chat.id}</code> │ 🆔 <b>{to_smallcaps('msg')}:</b> <code>{target_msg.id}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["editcaption", "ecap"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def editcaption_cmd(client: Client, message: Message):
    """Edit the caption of a replied media post or channel post while preserving inline buttons."""
    raw_arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    ok, target_msg, new_cap = await _resolve_editable_message(client, message, raw_arg)
    if not ok or not target_msg:
        return await message.reply_text(
            f"{new_cap}\n• <b>{to_smallcaps('usage')}:</b> <code>/editcaption &lt;new_caption&gt;</code> (ʀᴇᴘʟʏ ᴛᴏ ᴍᴇᴅɪᴀ)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    if not new_cap:
        return await message.reply_text("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴛʜᴇ ɴᴇᴡ ᴄᴀᴘᴛɪᴏɴ ᴛᴇxᴛ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    cleaned_cap, parsed_markup = parse_Inline_buttons_from_text(new_cap)
    final_markup = parsed_markup if parsed_markup else target_msg.reply_markup
    success, note = await _apply_in_place_edit(target_msg, cleaned_cap, final_markup)
    if not success:
        return await message.reply_text(f"❌ <b>{to_smallcaps('caption edit failed')}:</b> {note}", quote=True, parse_mode=enums.ParseMode.HTML)
    await message.reply_text(f"✅ <b>{to_smallcaps('caption updated on message')} <code>{target_msg.id}</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["editbuttons", "setbuttons", "addbtn", "delbuttons", "clearbuttons", "rmbuttons"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def editbuttons_cmd(client: Client, message: Message):
    """Replace, append, or strip inline URL buttons on any group or channel post."""
    cmd = message.command[0].lower()
    raw_arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    ok, target_msg, spec = await _resolve_editable_message(client, message, raw_arg)
    if not ok or not target_msg:
        return await message.reply_text(
            f"{spec}\n\n"
            f"🔘 <b>{to_smallcaps('button editor usage')}:</b>\n"
            f"• <code>/editbuttons 📢 Join | https://t.me/mallumovieworldmain1</code>\n"
            f"• <code>/addbtn 🎬 Watch | https://... || ⬇️ Download | https://...</code>\n"
            f"• <code>/delbuttons</code> — {to_smallcaps('remove all inline buttons')}",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("delbuttons", "clearbuttons", "rmbuttons"):
        success, note = await _apply_in_place_edit(target_msg, None, None, keep_existing_text=True)
        if not success:
            return await message.reply_text(f"❌ <b>{to_smallcaps('failed')}:</b> {note}", quote=True, parse_mode=enums.ParseMode.HTML)
        return await message.reply_text(f"🗑️ <b>{to_smallcaps('all inline buttons removed from message')} <code>{target_msg.id}</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if not spec or "|" not in spec:
        return await message.reply_text(
            f"⚠️ <b>{to_smallcaps('provide button label and url separated by')} <code>|</code>:</b>\n"
            f"<code>/{cmd} 📢 Join Channel | {Config.WATERMARK_URL}</code>\n"
            f"<i>({to_smallcaps('use')} <code>||</code> {to_smallcaps('for side-by-side buttons on the same row')})</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    append_mode = cmd == "addbtn"
    new_kb = _build_custom_url_keyboard(spec, existing_markup=target_msg.reply_markup, append=append_mode)
    if not new_kb:
        return await message.reply_text("⚠️ <b>ɪɴᴠᴀʟɪᴅ ʙᴜᴛᴛᴏɴ ғᴏʀᴍᴀᴛ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    success, note = await _apply_in_place_edit(target_msg, None, new_kb, keep_existing_text=True)
    if not success:
        return await message.reply_text(f"❌ <b>{to_smallcaps('button update failed')}:</b> {note}", quote=True, parse_mode=enums.ParseMode.HTML)

    await message.reply_text(
        f"✅ <b>{to_smallcaps('inline buttons updated on message')} <code>{target_msg.id}</code>!</b> 🔘",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["rebrand", "fixcaption"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def rebrand_post_cmd(client: Client, message: Message):
    """Replace @usernames/links or custom find|replace text in a replied post or channel message."""
    raw_arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    ok, target_msg, spec = await _resolve_editable_message(client, message, raw_arg)
    if not ok or not target_msg:
        return await message.reply_text(
            f"{spec}\n\n"
            f"🏷️ <b>{to_smallcaps('usage')}:</b>\n"
            f"• {to_smallcaps('reply to any post with')} <code>/rebrand</code> ({to_smallcaps('replaces all @usernames/t.me links with')} <code>{Config.WATERMARK}</code>)\n"
            f"• {to_smallcaps('or')} <code>/rebrand @oldchannel | @newchannel</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    orig_txt = target_msg.text or target_msg.caption or ""
    if not orig_txt:
        return await message.reply_text("⚠️ <b>ᴛᴀʀɢᴇᴛ ᴘᴏsᴛ ʜᴀs ɴᴏ ᴛᴇxᴛ ᴏʀ ᴄᴀᴘᴛɪᴏɴ ᴛᴏ ʀᴇʙʀᴀɴᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if spec and "|" in spec:
        find_s, rep_s = [x.strip() for x in spec.split("|", 1)]
        updated_txt = orig_txt.replace(find_s, rep_s)
    else:
        replacement = spec.strip() if spec else f"@{Config.WATERMARK_URL.rstrip('/').split('/')[-1]}"
        updated_txt = _HANDLE_OR_TME_RE.sub(replacement, orig_txt)

    success, note = await _apply_in_place_edit(target_msg, html.escape(updated_txt), target_msg.reply_markup)
    if not success:
        return await message.reply_text(f"❌ <b>{to_smallcaps('rebrand failed')}:</b> {note}", quote=True, parse_mode=enums.ParseMode.HTML)

    await message.reply_text(
        f"🏷️ <b>{to_smallcaps('post rebranded successfully')} (<code>{target_msg.id}</code>)!</b> ✨",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("formatpost", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def formatpost_cmd(client: Client, message: Message):
    """Apply smallcaps, bold, italic, mono, quote, or spoiler styling to a post in-place."""
    raw_arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    ok, target_msg, style_arg = await _resolve_editable_message(client, message, raw_arg)
    if not ok or not target_msg:
        return await message.reply_text(
            f"{style_arg}\n• <b>{to_smallcaps('usage')}:</b> <code>/formatpost &lt;smallcaps|bold|italic|mono|quote|spoiler&gt;</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    style = (style_arg or "smallcaps").strip().lower()
    raw_txt = target_msg.text or target_msg.caption or ""
    if not raw_txt:
        return await message.reply_text("⚠️ <b>ᴛᴀʀɢᴇᴛ ᴘᴏsᴛ ʜᴀs ɴᴏ ᴛᴇxᴛ/ᴄᴀᴘᴛɪᴏɴ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if style in ("smallcaps", "sc", "smallcap"):
        formatted = html.escape(to_smallcaps(raw_txt))
    elif style in ("bold", "b"):
        formatted = f"<b>{html.escape(raw_txt)}</b>"
    elif style in ("italic", "i"):
        formatted = f"<i>{html.escape(raw_txt)}</i>"
    elif style in ("mono", "code", "c"):
        formatted = f"<code>{html.escape(raw_txt)}</code>"
    elif style in ("quote", "blockquote", "q"):
        formatted = f"<blockquote>{html.escape(raw_txt)}</blockquote>"
    elif style in ("spoiler", "sp"):
        formatted = f"<spoiler>{html.escape(raw_txt)}</spoiler>"
    else:
        return await message.reply_text(
            "⚠️ <b>sᴜᴘᴘᴏʀᴛᴇᴅ sᴛʏʟᴇs:</b> <code>smallcaps, bold, italic, mono, quote, spoiler</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    success, note = await _apply_in_place_edit(target_msg, formatted, target_msg.reply_markup)
    if not success:
        return await message.reply_text(f"❌ <b>{to_smallcaps('format failed')}:</b> {note}", quote=True, parse_mode=enums.ParseMode.HTML)

    await message.reply_text(
        f"✨ <b>{to_smallcaps(f'applied {style} style to message')} <code>{target_msg.id}</code>!</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["editmedia", "replacemedia", "swapmedia"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def editmedia_cmd(client: Client, message: Message):
    """Replace the photo/video/audio/document of an existing post in-place while preserving buttons."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0
    src_media_msg = message.reply_to_message if (message.reply_to_message and message.reply_to_message.media) else None

    if not src_media_msg or len(message.command) < 2:
        return await message.reply_text(
            f"🖼️ <b>{to_smallcaps('in-place media replacement skill')}:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"1️⃣ {to_smallcaps('reply to the new photo/video/document with:')}\n"
            f"   <code>/editmedia &lt;target_post_link_or_msg_id&gt; [optional new caption]</code>\n"
            f"💡 <i>{to_smallcaps('replaces the media of the existing channel/group post in-place without changing its message id or losing buttons!')}</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    target_ref = message.command[1].strip()
    custom_cap = message.text.split(None, 2)[2].strip() if len(message.text.split(None, 2)) > 2 else None

    chat_ref, mid = _parse_post_link(target_ref)
    if not chat_ref:
        conn_cid = await db.get_user_connected_channel(uid) if uid else None
        chat_ref = conn_cid or message.chat.id
        mid = int(target_ref) if target_ref.isdigit() else None

    if not chat_ref or not mid:
        return await message.reply_text("❌ <b>ɪɴᴠᴀʟɪᴅ ᴛᴀʀɢᴇᴛ ᴘᴏsᴛ ʟɪɴᴋ ᴏʀ ᴍᴇssᴀɢᴇ ɪᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        chat_obj = await client.get_chat(chat_ref)
        if not await is_chat_or_global_admin(client, chat_obj.id, user_id=uid, sender_chat_id=sender_cid):
            return await message.reply_text("⛔ <b>ᴏɴʟʏ ᴄʜᴀᴛ/ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴs, ᴏᴡɴᴇʀ, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ʀᴇᴘʟᴀᴄᴇ ᴍᴇᴅɪᴀ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        target_post = await client.get_messages(chat_obj.id, mid)
        if not target_post or target_post.empty:
            return await message.reply_text("❌ <b>ᴛᴀʀɢᴇᴛ ᴘᴏsᴛ ɴᴏᴛ ғᴏᴜɴᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

        final_cap = custom_cap if custom_cap is not None else (target_post.caption or src_media_msg.caption or "")
        cleaned_cap, parsed_kb = parse_Inline_buttons_from_text(final_cap)
        final_kb = parsed_kb or target_post.reply_markup

        if src_media_msg.photo:
            media_input = InputMediaPhoto(src_media_msg.photo.file_id, caption=cleaned_cap[:1024], parse_mode=enums.ParseMode.HTML)
        elif src_media_msg.video:
            media_input = InputMediaVideo(src_media_msg.video.file_id, caption=cleaned_cap[:1024], parse_mode=enums.ParseMode.HTML)
        elif src_media_msg.audio:
            media_input = InputMediaAudio(src_media_msg.audio.file_id, caption=cleaned_cap[:1024], parse_mode=enums.ParseMode.HTML)
        elif src_media_msg.document:
            media_input = InputMediaDocument(src_media_msg.document.file_id, caption=cleaned_cap[:1024], parse_mode=enums.ParseMode.HTML)
        else:
            return await message.reply_text("⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ, ᴠɪᴅᴇᴏ, ᴀᴜᴅɪᴏ, ᴏʀ ᴅᴏᴄᴜᴍᴇɴᴛ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

        await client.edit_message_media(
            chat_id=chat_obj.id,
            message_id=mid,
            media=media_input,
            reply_markup=final_kb,
        )
        logger.info("🖼️ /editmedia sᴜᴄᴄᴇss › ᴄʜᴀᴛ=%s ᴍsɢ=%s ʙʏ=%s", chat_obj.id, mid, uid)
        await message.reply_text(
            f"✅ <b>{to_smallcaps('media replaced in-place on post')} <code>{mid}</code>!</b> 🖼️",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ <b>{to_smallcaps('media swap failed')}:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^postedit\|"))
async def postedit_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split(PE_SEP)
    action = parts[1] if len(parts) > 1 else "home"

    if action == "home":
        await query.answer("🎨 ᴘᴏsᴛ & ғᴇᴀᴛᴜʀᴇ sᴛᴜᴅɪᴏ")
        text, kb = build_editstudio_home_view()
        return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    if action in ("inspect", "rebrand", "addwmbtn", "delbtns", "pin", "clone") and len(parts) >= 4:
        cid, mid = int(parts[2]), int(parts[3])
        if not await is_chat_or_global_admin(client, cid, user_id=uid):
            return await query.answer("⛔ ᴀᴅᴍɪɴs / ᴏᴡɴᴇʀ ᴏɴʟʏ!", show_alert=True)
        try:
            target_msg = await client.get_messages(cid, mid)
        except Exception as e:
            return await query.answer(f"❌ {e}", show_alert=True)
        if not target_msg or target_msg.empty:
            return await query.answer("❌ ᴍᴇssᴀɢᴇ ɴᴏ ʟᴏɴɢᴇʀ ᴇxɪsᴛs!", show_alert=True)

        if action == "rebrand":
            orig_txt = target_msg.text or target_msg.caption or ""
            rep = f"@{Config.WATERMARK_URL.rstrip('/').split('/')[-1]}"
            new_txt = _HANDLE_OR_TME_RE.sub(rep, orig_txt) if orig_txt else f"⚡ {Config.WATERMARK}"
            await _apply_in_place_edit(target_msg, html.escape(new_txt), target_msg.reply_markup)
            await query.answer("🏷️ ʀᴇʙʀᴀɴᴅᴇᴅ @ʟɪɴᴋs!", show_alert=True)
        elif action == "addwmbtn":
            kb = _build_custom_url_keyboard(f"📢 {Config.WATERMARK} | {Config.WATERMARK_URL}", existing_markup=target_msg.reply_markup, append=True)
            await _apply_in_place_edit(target_msg, None, kb, keep_existing_text=True)
            await query.answer("📢 ʙʀᴀɴᴅ ʙᴜᴛᴛᴏɴ ᴀᴅᴅᴇᴅ!", show_alert=True)
        elif action == "delbtns":
            await _apply_in_place_edit(target_msg, None, None, keep_existing_text=True)
            await query.answer("🚫 ʙᴜᴛᴛᴏɴs ʀᴇᴍᴏᴠᴇᴅ!", show_alert=True)
        elif action == "pin":
            try:
                await client.pin_chat_message(cid, mid, disable_notification=True)
                await query.answer("📌 ᴘᴏsᴛ ᴘɪɴɴᴇᴅ!", show_alert=True)
            except Exception as e:
                return await query.answer(f"❌ {e}", show_alert=True)
        elif action == "clone":
            try:
                await target_msg.copy(chat_id=query.message.chat.id)
                await query.answer("📋 ᴘᴏsᴛ ᴄʟᴏɴᴇᴅ ᴡɪᴛʜᴏᴜᴛ ᴛᴀɢ!", show_alert=True)
            except Exception as e:
                return await query.answer(f"❌ {e}", show_alert=True)
        else:
            await query.answer("🔄 ʀᴇғʀᴇsʜᴇᴅ")

        fresh_msg = await client.get_messages(cid, mid)
        text, kb = build_post_inspector_view(fresh_msg or target_msg)
        return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    if action == "fmt" and len(parts) >= 5:
        style, cid, mid = parts[2], int(parts[3]), int(parts[4])
        if not await is_chat_or_global_admin(client, cid, user_id=uid):
            return await query.answer("⛔ ᴀᴅᴍɪɴs / ᴏᴡɴᴇʀ ᴏɴʟʏ!", show_alert=True)
        target_msg = await client.get_messages(cid, mid)
        if not target_msg or target_msg.empty:
            return await query.answer("❌ ᴍᴇssᴀɢᴇ ɴᴏᴛ ғᴏᴜɴᴅ!", show_alert=True)
        raw_txt = target_msg.text or target_msg.caption or ""
        if not raw_txt:
            return await query.answer("⚠️ ᴘᴏsᴛ ʜᴀs ɴᴏ ᴛᴇxᴛ/ᴄᴀᴘᴛɪᴏɴ!", show_alert=True)

        if style == "smallcaps":
            styled = html.escape(to_smallcaps(raw_txt))
        elif style == "bold":
            styled = f"<b>{html.escape(raw_txt)}</b>"
        elif style == "mono":
            styled = f"<code>{html.escape(raw_txt)}</code>"
        else:
            styled = f"<blockquote>{html.escape(raw_txt)}</blockquote>"

        await _apply_in_place_edit(target_msg, styled, target_msg.reply_markup)
        await query.answer(f"✨ ᴀᴘᴘʟɪᴇᴅ {style} sᴛʏʟᴇ!")
        fresh_msg = await client.get_messages(cid, mid)
        text, kb = build_post_inspector_view(fresh_msg or target_msg)
        return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


async def build_group_feature_editor_view(chat_id: int, chat_title: str = "") -> Tuple[str, InlineKeyboardMarkup]:
    cfg = await db.get_helpdesk_config(chat_id)
    locks = cfg.get("locks", {})

    def _st(val: Any) -> str:
        return "🟢 ᴏɴ" if val else "⚪ ᴏғғ"

    flim = int(cfg.get("flood_limit", 5) or 5)
    fwin = int(cfg.get("flood_window", 5) or 5)
    fact = str(cfg.get("flood_action", "mute")).upper()
    wlim = int(cfg.get("warn_limit", 3) or 3)
    wact = str(cfg.get("warn_action", "ban")).upper()
    blmode = str(cfg.get("blacklist_action", "delete")).upper()
    greact = cfg.get("auto_react_emoji") or "ᴏғғ"

    text = (
        f"✦ <b>{to_smallcaps('interactive group feature & limits editor')}</b> ✦ 🛡️\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 💬 <b>{to_smallcaps('group')}:</b> {html.escape(chat_title or str(chat_id))} (<code>{chat_id}</code>)\n"
        f"• 🌊 <b>{to_smallcaps('anti-flood')}:</b> <code>{_st(cfg.get('antiflood_enabled'))} ({flim} ᴍsɢs / {fwin}s ➔ {fact})</code>\n"
        f"• ⚠️ <b>{to_smallcaps('warn system')}:</b> <code>{wlim} ᴡᴀʀɴs ➔ {wact}</code>\n"
        f"• 🚫 <b>{to_smallcaps('blacklist mode')}:</b> <code>{blmode}</code> │ 🎭 <b>{to_smallcaps('auto-react')}:</b> <code>{greact}</code>\n"
        f"• 🔗 <b>{to_smallcaps('anti-link')}:</b> <code>{_st(locks.get('links'))}</code> │ ⏩ <b>{to_smallcaps('anti-forward')}:</b> <code>{_st(locks.get('forward'))}</code>\n"
        f"• 🤖 <b>{to_smallcaps('anti-bot')}:</b> <code>{_st(locks.get('bots'))}</code> │ 🌙 <b>{to_smallcaps('night mode')}:</b> <code>{_st(cfg.get('nightmode_enabled'))}</code>\n"
        f"• 🧹 <b>{to_smallcaps('clean service')}:</b> <code>{_st(cfg.get('clean_service'))}</code> │ 🔵 <b>{to_smallcaps('clean cmds')}:</b> <code>{_st(cfg.get('clean_commands'))}</code>\n\n"
        f"👇 <b>{to_smallcaps('tap any button to edit limits, penalties & features live')}:</b>"
        f"{format_watermark_html()}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🌊 ғʟᴏᴏᴅ: {_st(cfg.get('antiflood_enabled'))}", callback_data=f"gfeat|toggle|{chat_id}|antiflood_enabled"),
            InlineKeyboardButton(f"🔢 ʟɪᴍɪᴛ: {flim}/{fwin}s", callback_data=f"gfeat|cycle_flimit|{chat_id}"),
            InlineKeyboardButton(f"🔨 ғʟᴏᴏᴅ: {fact}", callback_data=f"gfeat|cycle_fact|{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"⚠️ ᴡᴀʀɴ ʟɪᴍɪᴛ: {wlim}", callback_data=f"gfeat|cycle_wlimit|{chat_id}"),
            InlineKeyboardButton(f"🔨 ᴡᴀʀɴ: {wact}", callback_data=f"gfeat|cycle_wact|{chat_id}"),
            InlineKeyboardButton(f"🚫 ʙʟ: {blmode}", callback_data=f"gfeat|cycle_blmode|{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"🔗 ᴀɴᴛɪ-ʟɪɴᴋ: {_st(locks.get('links'))}", callback_data=f"gfeat|lock|{chat_id}|links"),
            InlineKeyboardButton(f"⏩ ᴀɴᴛɪ-ғᴡᴅ: {_st(locks.get('forward'))}", callback_data=f"gfeat|lock|{chat_id}|forward"),
        ],
        [
            InlineKeyboardButton(f"🤖 ᴀɴᴛɪ-ʙᴏᴛ: {_st(locks.get('bots'))}", callback_data=f"gfeat|lock|{chat_id}|bots"),
            InlineKeyboardButton(f"🛡️ ᴀɴᴛɪ-sᴘᴀᴍ: {_st(cfg.get('antispam_enabled'))}", callback_data=f"gfeat|toggle|{chat_id}|antispam_enabled"),
        ],
        [
            InlineKeyboardButton(f"🔁 ᴀɴᴛɪ-ᴅᴜᴘ: {_st(cfg.get('antidup_enabled'))}", callback_data=f"gfeat|toggle|{chat_id}|antidup_enabled"),
            InlineKeyboardButton(f"🌙 ɴɪɢʜᴛ ᴍᴏᴅᴇ: {_st(cfg.get('nightmode_enabled'))}", callback_data=f"gfeat|night|{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"🧹 ᴄʟᴇᴀɴ sᴠᴄ: {_st(cfg.get('clean_service'))}", callback_data=f"gfeat|toggle|{chat_id}|clean_service"),
            InlineKeyboardButton(f"🔵 ᴄʟᴇᴀɴ ᴄᴍᴅs: {_st(cfg.get('clean_commands'))}", callback_data=f"gfeat|toggle|{chat_id}|clean_commands"),
        ],
        [
            InlineKeyboardButton(f"🎭 ʀᴇᴀᴄᴛ: {greact}", callback_data=f"gfeat|cycle_react|{chat_id}"),
            InlineKeyboardButton(f"✅ ᴠᴇʀɪғʏ: {_st(cfg.get('verify_new_members'))}", callback_data=f"gfeat|toggle|{chat_id}|verify_new_members"),
        ],
        [
            InlineKeyboardButton("🔙 ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ],
    ])
    return text, kb


@Client.on_message(filters.command(["gsettings", "editgroup", "grpconfig"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def gsettings_cmd(client: Client, message: Message):
    """Open the interactive Group Feature & Limits Editor for Group Admin/Owner or Bot ADMINS."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    target_cid = message.chat.id if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) else None
    if len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        target_cid = int(message.command[1])

    if not target_cid or target_cid > 0:
        return await message.reply_text(
            f"🛡️ <b>{to_smallcaps('group feature editor')}:</b>\n"
            f"• {to_smallcaps('run')} <code>/gsettings</code> {to_smallcaps('directly inside your group, or')}\n"
            f"• <code>/gsettings -100xxxxxxxxxx</code> {to_smallcaps('from private chat!')}",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if not await is_chat_or_global_admin(client, target_cid, user_id=uid, sender_chat_id=sender_cid):
        return await message.reply_text("⛔ <b>ᴏɴʟʏ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs, ᴏᴡɴᴇʀ, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ᴇᴅɪᴛ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇs!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    title = message.chat.title or str(target_cid)
    if target_cid != message.chat.id:
        try:
            cobj = await client.get_chat(target_cid)
            title = cobj.title or str(target_cid)
        except Exception:
            pass

    text, kb = await build_group_feature_editor_view(target_cid, title)
    await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^gfeat\|"))
async def gfeat_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split("|")
    action = parts[1] if len(parts) > 1 else "open"

    if action == "open":
        if query.message and query.message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            cid = query.message.chat.id
            if not await is_chat_or_global_admin(client, cid, user_id=uid):
                return await query.answer("⛔ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)
            await query.answer("🛡️ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ")
            text, kb = await build_group_feature_editor_view(cid, query.message.chat.title or "")
            return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        all_chats = await db.get_all_chats()
        if is_admin_sync(uid) and all_chats:
            cid = int(all_chats[0]["id"])
            await query.answer("🛡️ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ")
            text, kb = await build_group_feature_editor_view(cid, all_chats[0].get("title", "Group"))
            return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        return await query.answer("💡 ʀᴜɴ /gsettings ɪɴsɪᴅᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴏʀ /gsettings <chat_id>!", show_alert=True)

    if len(parts) < 3 or not parts[2].lstrip("-").isdigit():
        return await query.answer("⚠️ ɪɴᴠᴀʟɪᴅ ɢʀᴏᴜᴘ!", show_alert=True)

    cid = int(parts[2])
    if not await is_chat_or_global_admin(client, cid, user_id=uid):
        return await query.answer("⛔ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    cfg = await db.get_helpdesk_config(cid)

    if action == "toggle" and len(parts) > 3:
        field = parts[3]
        new_val = not bool(cfg.get(field, False))
        await db.update_helpdesk_config(cid, {field: new_val})
        await query.answer(f"✅ {field}: {'ᴏɴ' if new_val else 'ᴏғғ'}")
    elif action == "lock" and len(parts) > 3:
        ltype = parts[3]
        curr = bool(cfg.get("locks", {}).get(ltype, False))
        await db.set_group_lock(cid, ltype, not curr)
        await query.answer(f"🔐 {ltype.upper()}: {'ʟᴏᴄᴋᴇᴅ' if not curr else 'ᴜɴʟᴏᴄᴋᴇᴅ'}")
    elif action == "night":
        nm = not bool(cfg.get("nightmode_enabled", False))
        await db.update_helpdesk_config(cid, {"nightmode_enabled": nm, "locks": {"media": nm, "stickers": nm, "gifs": nm, "links": nm}})
        await query.answer(f"🌙 ɴɪɢʜᴛ ᴍᴏᴅᴇ: {'ᴏɴ' if nm else 'ᴏғғ'}")
    elif action == "cycle_flimit":
        seq = [3, 5, 8, 10, 15, 20]
        curr = int(cfg.get("flood_limit", 5) or 5)
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else 5
        await db.update_helpdesk_config(cid, {"flood_limit": nxt, "antiflood_enabled": True})
        await query.answer(f"🌊 ғʟᴏᴏᴅ ʟɪᴍɪᴛ: {nxt} ᴍsɢs")
    elif action == "cycle_fact":
        seq = ["mute", "ban", "kick"]
        curr = str(cfg.get("flood_action", "mute")).lower()
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else "mute"
        await db.update_helpdesk_config(cid, {"flood_action": nxt})
        await query.answer(f"🔨 ғʟᴏᴏᴅ ᴘᴇɴᴀʟᴛʏ: {nxt.upper()}")
    elif action == "cycle_wlimit":
        seq = [2, 3, 4, 5, 8, 10]
        curr = int(cfg.get("warn_limit", 3) or 3)
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else 3
        await db.update_helpdesk_config(cid, {"warn_limit": nxt})
        await query.answer(f"⚠️ ᴡᴀʀɴ ʟɪᴍɪᴛ: {nxt}")
    elif action == "cycle_wact":
        seq = ["ban", "mute", "kick"]
        curr = str(cfg.get("warn_action", "ban")).lower()
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else "ban"
        await db.update_helpdesk_config(cid, {"warn_action": nxt})
        await query.answer(f"🔨 ᴡᴀʀɴ ᴘᴇɴᴀʟᴛʏ: {nxt.upper()}")
    elif action == "cycle_blmode":
        seq = ["delete", "warn", "mute", "ban", "kick"]
        curr = str(cfg.get("blacklist_action", "delete")).lower()
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else "delete"
        await db.update_helpdesk_config(cid, {"blacklist_action": nxt})
        await query.answer(f"🚫 ʙʟᴀᴄᴋʟɪsᴛ ᴍᴏᴅᴇ: {nxt.upper()}")
    elif action == "cycle_react":
        seq = ["", "🔥", "❤️", "⚡", "👍", "🎉", "🤩"]
        curr = str(cfg.get("auto_react_emoji") or "")
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else "🔥"
        await db.update_helpdesk_config(cid, {"auto_react_emoji": nxt})
        await query.answer(f"🎭 ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ: {nxt or 'ᴏғғ'}")
    else:
        await query.answer("🛡️ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ")

    title = query.message.chat.title if (query.message and query.message.chat.id == cid) else str(cid)
    text, kb = await build_group_feature_editor_view(cid, title)
    await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(
    filters.command(
        ["antilink", "antiforward", "antibot", "setantispam", "setantidup", "lockall", "unlockall", "mutechat", "unmutechat", "cleanservice", "silentpin", "loudpin", "stafftitle", "admintitle", "setwelcomebutton", "welcomebutton", "setrulesbutton", "rulesbutton", "gbackup", "grestore"],
        prefixes=COMMAND_PREFIXES,
    )
    & filters.incoming
)
async def extra_group_admin_skills_cmd(client: Client, message: Message):
    """Extra Group Management & Feature Editing Commands for Group Admin/Owner and Bot ADMINS."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0
    cmd = message.command[0].lower()

    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ʀᴜɴ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    cid = message.chat.id
    if not await is_chat_or_global_admin(client, cid, user_id=uid, sender_chat_id=sender_cid):
        return await message.reply_text("⛔ <b>ᴏɴʟʏ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs, ᴏᴡɴᴇʀ, ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    cfg = await db.get_helpdesk_config(cid)
    locks = cfg.get("locks", {})

    if cmd in ("antilink", "antiforward", "antibot"):
        lk_map = {"antilink": "links", "antiforward": "forward", "antibot": "bots"}
        lk = lk_map[cmd]
        new_val = not bool(locks.get(lk, False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.set_group_lock(cid, lk, new_val)
        return await message.reply_text(
            f"🛡️ <b>{to_smallcaps(cmd)}:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ (ʟᴏᴄᴋᴇᴅ)' if new_val else '⚪ ᴅɪsᴀʙʟᴇᴅ (ᴏᴘᴇɴ)'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("setantispam", "setantidup"):
        field = "antispam_enabled" if cmd == "setantispam" else "antidup_enabled"
        new_val = not bool(cfg.get(field, False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_helpdesk_config(cid, {field: new_val})
        return await message.reply_text(
            f"🛡️ <b>{to_smallcaps(field)}:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if new_val else '⚪ ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("lockall", "unlockall", "mutechat", "unmutechat"):
        from plugins.help_desk_features import MUTE_PERMS, UNMUTE_PERMS
        lock_state = cmd in ("lockall", "mutechat")
        await db.set_group_lock(cid, "all", lock_state)
        try:
            await client.set_chat_permissions(cid, MUTE_PERMS if lock_state else UNMUTE_PERMS)
        except Exception:
            pass
        return await message.reply_text(
            f"{'🔒' if lock_state else '🔓'} <b>{to_smallcaps('group chat permissions')}:</b> <code>{'ᴍᴜᴛᴇᴅ / ʟᴏᴄᴋᴇᴅ' if lock_state else 'ᴜɴᴍᴜᴛᴇᴅ / ᴏᴘᴇɴ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cleanservice":
        new_val = not bool(cfg.get("clean_service", False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_helpdesk_config(cid, {"clean_service": new_val})
        return await message.reply_text(
            f"🧹 <b>{to_smallcaps('clean service messages')}:</b> <code>{'🟢 ᴏɴ' if new_val else '🔴 ᴏғғ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("silentpin", "loudpin"):
        if not message.reply_to_message:
            return await message.reply_text(f"⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/{cmd}</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        try:
            await client.pin_chat_message(cid, message.reply_to_message.id, disable_notification=(cmd == "silentpin"))
            return await message.reply_text(
                f"📌 <b>{to_smallcaps('message pinned')} ({'🔕 sɪʟᴇɴᴛ' if cmd == 'silentpin' else '🔔 ʟᴏᴜᴅ'})!</b>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>{html.escape(str(e))}</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd in ("stafftitle", "admintitle"):
        target_u = message.reply_to_message.from_user if message.reply_to_message else None
        title_txt = arg
        if not target_u and len(message.command) >= 3:
            try:
                target_u = await client.get_users(message.command[1])
                title_txt = message.text.split(None, 2)[2].strip()
            except Exception:
                pass
        if not target_u or not title_txt:
            return await message.reply_text(
                "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/stafftitle @admin Custom Title</code> (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀᴅᴍɪɴ)",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        try:
            await client.set_administrator_title(cid, target_u.id, title_txt[:16])
            return await message.reply_text(
                f"🏷️ <b>{to_smallcaps('updated admin title for')} {target_u.mention} ➔ <code>{html.escape(title_txt[:16])}</code>!</b>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>{html.escape(str(e))}</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd in ("setwelcomebutton", "welcomebutton", "setrulesbutton", "rulesbutton"):
        field = "welcome_button" if "welcome" in cmd else "rules_button"
        if not arg:
            return await message.reply_text(
                f"⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} 📜 Read Rules | https://t.me/...</code> (ᴏʀ <code>/{cmd} off</code>)",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        val = "" if arg.lower() in ("off", "none", "clear") else arg
        await db.update_helpdesk_config(cid, {field: val})
        return await message.reply_text(
            f"✅ <b>{to_smallcaps(field)} {'ᴄʟᴇᴀʀᴇᴅ' if not val else 'sᴀᴠᴇᴅ'}!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "gbackup":
        notes = await db.get_all_group_notes(cid)
        flts = await db.get_all_group_filters(cid)
        bl = await db.get_group_blacklist(cid)
        payload = {
            "chat_id": cid,
            "title": message.chat.title or "Group",
            "exported_at": time.time(),
            "helpdesk_config": cfg,
            "notes": notes,
            "filters": flts,
            "blacklist": bl,
        }
        bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
        bio.name = f"group_backup_{cid}.json"
        return await message.reply_document(
            document=bio,
            caption=(
                f"📦 <b>{to_smallcaps('full group configuration backup')}</b>\n"
                f"• 💬 <b>{to_smallcaps('group')}:</b> {html.escape(message.chat.title or 'Group')} (<code>{cid}</code>)\n"
                f"• 📝 <b>{to_smallcaps('notes')}:</b> <code>{len(notes)}</code> │ ⚡ <b>{to_smallcaps('filters')}:</b> <code>{len(flts)}</code>\n"
                f"• 💡 <i>{to_smallcaps('reply to this file with')} <code>/grestore</code> {to_smallcaps('to restore settings!')}</i>"
            ),
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "grestore":
        if not message.reply_to_message or not message.reply_to_message.document:
            return await message.reply_text("⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ <code>group_backup_*.json</code> ғɪʟᴇ ᴡɪᴛʜ <code>/grestore</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        try:
            dl_path = await client.download_media(message.reply_to_message.document.file_id)
            with open(dl_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("helpdesk_config"), dict):
                restored_cfg = {k: v for k, v in data["helpdesk_config"].items() if k not in ("_id", "chat_id")}
                await db.update_helpdesk_config(cid, restored_cfg)
            for k, v in (data.get("notes") or {}).items():
                if isinstance(v, dict):
                    await db.save_group_note(cid, k, v.get("content", ""), v.get("file_id"), v.get("media_type"))
            for k, v in (data.get("filters") or {}).items():
                if isinstance(v, dict):
                    await db.save_group_filter(cid, k, v.get("reply_text", ""), v.get("file_id"), v.get("media_type"))
            for w in (data.get("blacklist") or []):
                await db.add_blacklist_word(cid, str(w))
            return await message.reply_text(
                f"✅ <b>{to_smallcaps('group configuration, locks, notes, filters & blacklist restored!')}</b> 🎉",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>{to_smallcaps('restore failed')}:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


async def build_channel_feature_editor_view(channel_id: int, title: str = "") -> Tuple[str, InlineKeyboardMarkup]:
    cfg = await db.get_channel_config(channel_id, title=title or "Channel")

    def _b(k: str, d: bool = False) -> str:
        return "🟢 ᴏɴ" if cfg.get(k, d) else "⚪ ᴏғғ"

    del_s = int(cfg.get("auto_delete_seconds", 0) or 0)
    del_lbl = get_readable_time(del_s) if del_s > 0 else "ᴏғғ"
    react_lbl = cfg.get("auto_react_emoji") or "ᴏғғ"
    rep_rules = cfg.get("replace_rules", {})
    bl_words = cfg.get("blocked_words", [])
    sig = cfg.get("signature_text") or "ɴᴏɴᴇ"

    text = (
        f"✦ <b>{to_smallcaps('interactive channel feature & template editor')}</b> ✦ 📢\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📢 <b>{to_smallcaps('channel')}:</b> {html.escape(cfg.get('title') or str(channel_id))} (<code>{channel_id}</code>)\n"
        f"• 🏷️ <b>{to_smallcaps('auto-tag template')}:</b> <code>{_b('auto_tag_enabled')}</code>\n"
        f"• 📝 <b>{to_smallcaps('auto-caption')}:</b> <code>{_b('auto_caption_enabled', True)}</code>\n"
        f"• 🎬 <b>{to_smallcaps('auto-stream (4 players)')}:</b> <code>{_b('auto_stream_enabled')}</code>\n"
        f"• ⏱️ <b>{to_smallcaps('auto-delete timer')}:</b> <code>{del_lbl}</code> │ 🎭 <b>{to_smallcaps('auto-react')}:</b> <code>{react_lbl}</code>\n"
        f"• 🔁 <b>{to_smallcaps('word replacements')}:</b> <code>{len(rep_rules)}</code> │ 🚫 <b>{to_smallcaps('blocked words')}:</b> <code>{len(bl_words)}</code>\n"
        f"• ✍️ <b>{to_smallcaps('signature')}:</b> <code>{html.escape(str(sig)[:30])}</code>\n\n"
        f"👇 <b>{to_smallcaps('tap any button below to edit channel features live')}:</b>"
        f"{format_watermark_html()}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🏷️ ᴀᴜᴛᴏ-ᴛᴀɢ: {_b('auto_tag_enabled')}", callback_data=f"cfeat|toggle|{channel_id}|auto_tag_enabled"),
            InlineKeyboardButton(f"📝 ᴀᴜᴛᴏ-ᴄᴀᴘ: {_b('auto_caption_enabled', True)}", callback_data=f"cfeat|toggle|{channel_id}|auto_caption_enabled"),
        ],
        [
            InlineKeyboardButton(f"🎬 4-ᴘʟᴀʏᴇʀ sᴛʀᴇᴀᴍ: {_b('auto_stream_enabled')}", callback_data=f"cfeat|toggle|{channel_id}|auto_stream_enabled"),
            InlineKeyboardButton(f"🔒 ᴘʀᴏᴛᴇᴄᴛ: {_b('protect_posts')}", callback_data=f"cfeat|toggle|{channel_id}|protect_posts"),
        ],
        [
            InlineKeyboardButton(f"🔕 sɪʟᴇɴᴛ: {_b('silent_posts')}", callback_data=f"cfeat|toggle|{channel_id}|silent_posts"),
            InlineKeyboardButton(f"🌐 ɴᴏ ᴘʀᴇᴠɪᴇᴡ: {_b('disable_preview', True)}", callback_data=f"cfeat|toggle|{channel_id}|disable_preview"),
        ],
        [
            InlineKeyboardButton(f"⏱️ ᴀᴜᴛᴏ-ᴅᴇʟ: {del_lbl}", callback_data=f"cfeat|cycle_del|{channel_id}"),
            InlineKeyboardButton(f"🎭 ʀᴇᴀᴄᴛ: {react_lbl}", callback_data=f"cfeat|cycle_react|{channel_id}"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('apply pro watermark preset')}", callback_data=f"cfeat|apply_wm|{channel_id}"),
        ],
        [
            InlineKeyboardButton("🔙 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ],
    ])
    return text, kb


@Client.on_message(filters.command(["csettings", "editchannel", "chnlconfig"], prefixes=COMMAND_PREFIXES))
async def csettings_cmd(client: Client, message: Message):
    """Open the interactive Channel Feature & Template Editor for Channel Admin/Owner or Bot ADMINS."""
    from plugins.channel_manager import resolve_target_channel_and_verify
    arg = message.command[1].strip() if len(message.command) > 1 else None
    ok, chat_obj, _ = await resolve_target_channel_and_verify(client, message, explicit_arg=arg)
    if not ok or not chat_obj:
        return
    text, kb = await build_channel_feature_editor_view(chat_obj.id, chat_obj.title or "Channel")
    await message.reply_text(text, reply_markup=kb, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^cfeat\|"))
async def cfeat_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split("|")
    action = parts[1] if len(parts) > 1 else "open"

    if action == "open":
        conn_id = await db.get_user_connected_channel(uid)
        if not conn_id and is_admin_sync(uid):
            all_ch = await db.get_all_channels()
            if all_ch:
                conn_id = int(all_ch[0].get("chat_id", 0))
        if not conn_id:
            return await query.answer("📢 ᴄᴏɴɴᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ ᴡɪᴛʜ /connect <channel_id>!", show_alert=True)
        if not await is_chat_or_global_admin(client, conn_id, user_id=uid):
            return await query.answer("⛔ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)
        await query.answer("📢 ᴄʜᴀɴɴᴇʟ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ")
        text, kb = await build_channel_feature_editor_view(conn_id)
        return await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    if len(parts) < 3 or not parts[2].lstrip("-").isdigit():
        return await query.answer("⚠️ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ!", show_alert=True)

    cid = int(parts[2])
    if not await is_chat_or_global_admin(client, cid, user_id=uid):
        return await query.answer("⛔ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    cfg = await db.get_channel_config(cid)

    if action == "toggle" and len(parts) > 3:
        field = parts[3]
        new_val = not bool(cfg.get(field, False))
        await db.update_channel_config(cid, {field: new_val})
        await query.answer(f"✅ {field}: {'ᴏɴ' if new_val else 'ᴏғғ'}")
    elif action == "cycle_del":
        seq = [0, 300, 900, 1800, 3600, 21600, 86400]
        curr = int(cfg.get("auto_delete_seconds", 0) or 0)
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else 300
        await db.update_channel_config(cid, {"auto_delete_seconds": nxt})
        await query.answer(f"⏱️ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ: {get_readable_time(nxt) if nxt else 'ᴏғғ'}")
    elif action == "cycle_react":
        seq = ["", "🔥", "❤️", "⚡", "👍", "🎉", "🤩"]
        curr = str(cfg.get("auto_react_emoji") or "")
        nxt = seq[(seq.index(curr) + 1) % len(seq)] if curr in seq else "🔥"
        await db.update_channel_config(cid, {"auto_react_emoji": nxt})
        await query.answer(f"🎭 ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ: {nxt or 'ᴏғғ'}")
    elif action == "apply_wm":
        await db.update_channel_config(
            cid,
            {
                "auto_tag_enabled": True,
                "auto_caption_enabled": True,
                "auto_stream_enabled": True,
                "footer_text": f"⚡ <b>{Config.WATERMARK}</b>",
                "button_text": f"📢 {Config.WATERMARK}",
                "button_url": Config.WATERMARK_URL,
            },
        )
        await query.answer("⚡ ᴘʀᴏ ᴡᴀᴛᴇʀᴍᴀʀᴋ + 4-ᴘʟᴀʏᴇʀ sᴛʀᴇᴀᴍ ᴘʀᴇsᴇᴛ ᴀᴘᴘʟɪᴇᴅ!", show_alert=True)
    else:
        await query.answer("📢 ᴄʜᴀɴɴᴇʟ ғᴇᴀᴛᴜʀᴇ ᴇᴅɪᴛᴏʀ")

    text, kb = await build_channel_feature_editor_view(cid, cfg.get("title", "Channel"))
    await safe_edit_message(query.message, text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(
    filters.command(["creplace", "cwordreplace", "cblacklist", "csignature", "cwatermark", "cclone", "crestore"], prefixes=COMMAND_PREFIXES)
)
async def extra_channel_admin_skills_cmd(client: Client, message: Message):
    """Extra Channel Management & Editing Commands for Channel Admin/Owner and Bot ADMINS."""
    from plugins.channel_manager import _apply_channel_template, resolve_target_channel_and_verify
    ok, chat_obj, cfg = await resolve_target_channel_and_verify(client, message)
    if not ok or not chat_obj or not cfg:
        return

    cmd = message.command[0].lower()
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""

    if cmd in ("creplace", "cwordreplace"):
        rep_rules: Dict[str, str] = dict(cfg.get("replace_rules") or {})
        if not arg:
            lines = [f"• <code>{html.escape(k)}</code> ➔ <code>{html.escape(v)}</code>" for k, v in rep_rules.items()]
            return await message.reply_text(
                f"🔁 <b>{to_smallcaps('channel word replacement rules')} ({len(rep_rules)}):</b>\n"
                + ("\n".join(lines) if lines else "<i>(ɴᴏɴᴇ)</i>")
                + "\n\n💡 <b>ᴜsᴀɢᴇ:</b> <code>/creplace @old | @new</code> (ᴏʀ <code>/creplace off</code>)",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        if arg.lower() in ("off", "clear", "reset"):
            await db.update_channel_config(chat_obj.id, {"replace_rules": {}}, title=chat_obj.title or "")
            return await message.reply_text("🧹 <b>ᴄʜᴀɴɴᴇʟ ᴡᴏʀᴅ ʀᴇᴘʟᴀᴄᴇᴍᴇɴᴛ ʀᴜʟᴇs ᴄʟᴇᴀʀᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        if "|" not in arg:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/creplace old_text | new_text</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        old_w, new_w = [x.strip() for x in arg.split("|", 1)]
        rep_rules[old_w] = new_w
        await db.update_channel_config(chat_obj.id, {"replace_rules": rep_rules}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>{to_smallcaps('replacement rule saved')}:</b> <code>{html.escape(old_w)}</code> ➔ <code>{html.escape(new_w)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cblacklist":
        if not arg:
            words = cfg.get("blocked_words", [])
            return await message.reply_text(
                f"🚫 <b>{to_smallcaps('channel blocked words')}:</b> <code>{', '.join(words) if words else 'ɴᴏɴᴇ'}</code>\n"
                f"💡 <b>ᴜsᴀɢᴇ:</b> <code>/cblacklist spamword1, spamword2</code> (ᴏʀ <code>/cblacklist off</code>)",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        if arg.lower() in ("off", "clear", "reset"):
            await db.update_channel_config(chat_obj.id, {"blocked_words": []}, title=chat_obj.title or "")
            return await message.reply_text("🧹 <b>ᴄʜᴀɴɴᴇʟ ʙʟᴏᴄᴋᴇᴅ ᴡᴏʀᴅs ᴄʟᴇᴀʀᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        wlist = [w.strip() for w in arg.split(",") if w.strip()]
        await db.update_channel_config(chat_obj.id, {"blocked_words": wlist}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✅ <b>{to_smallcaps('channel blocked words updated')} (<code>{len(wlist)}</code>)!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd in ("csignature", "cwatermark"):
        if not arg:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/csignature ✍️ Posted by @Channel</code> (ᴏʀ <code>/csignature off</code>)", quote=True, parse_mode=enums.ParseMode.HTML)
        val = "" if arg.lower() in ("off", "clear", "none") else arg
        await db.update_channel_config(chat_obj.id, {"signature_text": val, "footer_text": val}, title=chat_obj.title or "")
        return await message.reply_text(
            f"✍️ <b>{to_smallcaps('channel signature')} {'ᴄʟᴇᴀʀᴇᴅ' if not val else 'sᴀᴠᴇᴅ'}!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cclone":
        if not arg:
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/cclone https://t.me/source_channel/123</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        src_ref, mid = _parse_post_link(arg)
        if not src_ref or not mid:
            return await message.reply_text("❌ <b>ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏsᴛ ʟɪɴᴋ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        try:
            src_msg = await client.get_messages(src_ref, mid)
            if not src_msg or src_msg.empty:
                return await message.reply_text("❌ <b>sᴏᴜʀᴄᴇ ᴘᴏsᴛ ɴᴏᴛ ғᴏᴜɴᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
            raw_txt = src_msg.text or src_msg.caption or ""
            for old_w, new_w in (cfg.get("replace_rules") or {}).items():
                raw_txt = raw_txt.replace(old_w, new_w)
            final_txt, final_kb = _apply_channel_template(raw_txt, cfg)
            if src_msg.media:
                sent = await src_msg.copy(
                    chat_id=chat_obj.id,
                    caption=final_txt[:1024] if final_txt else None,
                    reply_markup=final_kb,
                    parse_mode=enums.ParseMode.HTML,
                )
            else:
                sent = await client.send_message(
                    chat_id=chat_obj.id,
                    text=final_txt or "✨",
                    reply_markup=final_kb,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=bool(cfg.get("disable_preview", True)),
                )
            return await message.reply_text(
                f"✅ <b>{to_smallcaps('post cloned & branded into')} {html.escape(chat_obj.title or 'Channel')} (<code>{sent.id}</code>)!</b> 🚀",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>{html.escape(str(e))}</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "crestore":
        if not message.reply_to_message or not message.reply_to_message.document:
            return await message.reply_text("⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ <code>channel_backup_*.json</code> ғɪʟᴇ ᴡɪᴛʜ <code>/crestore</code>!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        try:
            dl_path = await client.download_media(message.reply_to_message.document.file_id)
            with open(dl_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            clean_data = {k: v for k, v in data.items() if k not in ("_id", "chat_id")}
            await db.update_channel_config(chat_obj.id, clean_data, title=chat_obj.title or "")
            return await message.reply_text(
                f"✅ <b>{to_smallcaps('channel configuration & templates restored for')} {html.escape(chat_obj.title or 'Channel')}!</b> 🎉",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            return await message.reply_text(f"❌ <b>{html.escape(str(e))}</b>", quote=True, parse_mode=enums.ParseMode.HTML)
