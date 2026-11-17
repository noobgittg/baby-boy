import asyncio
import html
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from database.users_chats_db import db
from info import ADMINS, COMMAND_PREFIXES, LOG_CHANNEL
from utils import (
    format_watermark_html,
    humanbytes,
    is_admin_sync,
    is_chat_or_global_admin,
    parse_Inline_buttons_from_text,
    safe_edit_message,
    temp,
    to_smallcaps,
)

logger = logging.getLogger("MMWAutoForward")

AF_SEP = "|"
_FORWARDED_DEDUP_CACHE: Dict[Tuple[int, int, int], float] = {}
_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/(\d+)/|([A-Za-z0-9_]+)/)(\d+)")


def _parse_tg_link(link: str) -> Tuple[Optional[Union[int, str]], Optional[int]]:
    m = _LINK_RE.search(str(link or "").strip())
    if not m:
        return None, None
    priv_cid, pub_uname, msg_id_str = m.group(1), m.group(2), m.group(3)
    msg_id = int(msg_id_str)
    if priv_cid:
        return int(f"-100{priv_cid}"), msg_id
    return f"@{pub_uname}", msg_id


async def _resolve_chat_and_check_admin(
    client: Client,
    raw_chat: Union[int, str],
    user_id: int,
    sender_chat_id: int = 0,
) -> Tuple[bool, Optional[Any], str]:
    try:
        cid: Union[int, str] = int(raw_chat) if str(raw_chat).lstrip("-").isdigit() else str(raw_chat)
        chat_obj = await client.get_chat(cid)
    except Exception as e:
        return False, None, f"ᴄᴀɴɴᴏᴛ ᴀᴄᴄᴇss ᴄʜᴀᴛ <code>{html.escape(str(raw_chat))}</code> ({html.escape(str(e))})"

    authorized = await is_chat_or_global_admin(
        client,
        chat_obj.id,
        user_id=user_id,
        sender_chat_id=sender_chat_id,
    )
    if not authorized:
        return False, chat_obj, f"ʏᴏᴜ ᴍᴜsᴛ ʙᴇ ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ ᴏғ <code>{html.escape(str(chat_obj.title or chat_obj.id))}</code> (ᴏʀ ʙᴏᴛ ᴀᴅᴍɪɴ)"
    return True, chat_obj, ""


def _format_forward_caption(
    rule: Dict[str, Any],
    message: Message,
    source_title: str,
) -> Optional[str]:
    orig_text = message.caption or message.text or ""
    media = message.video or message.document or message.audio or message.photo
    file_name = getattr(media, "file_name", None) or "media_file"
    file_size = humanbytes(getattr(media, "file_size", 0)) if media else "0 B"
    now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    custom_cap = str(rule.get("custom_caption") or "").strip()
    if custom_cap:
        body = (
            custom_cap.replace("{original_caption}", orig_text)
            .replace("{caption}", orig_text)
            .replace("{filename}", str(file_name))
            .replace("{file_name}", str(file_name))
            .replace("{filesize}", str(file_size))
            .replace("{file_size}", str(file_size))
            .replace("{source_title}", str(source_title))
            .replace("{date}", now_str)
        )
    else:
        body = orig_text

    header = str(rule.get("header_text") or "").strip()
    footer = str(rule.get("footer_text") or "").strip()
    parts = [p for p in (header, body, footer) if p]
    return "\n\n".join(parts) if parts else None


async def build_autoforward_panel(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    rules = await db.get_user_autoforward_rules(user_id)
    if is_admin_sync(user_id) and not rules:
        rules = await db.get_all_autoforward_rules()

    global_on = temp.BOT_CONFIG.get("auto_forward_global", True)
    g_badge = "🟢 ᴀᴄᴛɪᴠᴇ" if global_on else "🔴 ᴘᴀᴜsᴇᴅ"

    text = (
        f"✦ <b>{to_smallcaps('pro auto-forward & batch clone center')}</b> ✦ ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🌐 <b>{to_smallcaps('global engine')}:</b> <code>{g_badge}</code>\n"
        f"• 🔗 <b>{to_smallcaps('active routes')}:</b> <code>{len(rules)}</code>\n\n"
    )

    buttons: List[List[InlineKeyboardButton]] = []
    if rules:
        text += f"📋 <b>{to_smallcaps('configured auto-forward routes')}:</b>\n"
        for idx, r in enumerate(rules[:10], 1):
            rid = r["rule_id"]
            st = "🟢" if r.get("enabled", True) else "⚪"
            mode = "📋 ᴄᴏᴘʏ" if r.get("copy_mode", True) else "⏩ ғᴡᴅ"
            cnt = r.get("forwarded_count", 0)
            s_title = html.escape(str(r.get("source_title", r.get("source_chat_id")))[:22])
            t_title = html.escape(str(r.get("target_title", r.get("target_chat_id")))[:22])
            text += (
                f"  <code>{idx}.</code> {st} <b>{s_title}</b> ➔ <b>{t_title}</b> "
                f"[<code>{mode}</code> │ <code>{cnt} ᴘᴏsᴛs</code>]\n"
            )
            buttons.append([
                InlineKeyboardButton(
                    f"{st} #{idx} {r.get('source_title', 'Src')[:12]} ➔ {r.get('target_title', 'Dst')[:12]}",
                    callback_data=f"autofwd{AF_SEP}view{AF_SEP}{rid}",
                )
            ])
        text += "\n"
    else:
        text += (
            f"ℹ️ <i>{to_smallcaps('no auto-forward routes configured yet.')}</i>\n\n"
            f"⚡ <b>{to_smallcaps('quick setup commands')}:</b>\n"
            f"• <code>/addforward -100source -100target</code>\n"
            f"• <code>/clonepost https://t.me/channel/123</code>\n"
            f"• <code>/fbatch https://t.me/ch/10 https://t.me/ch/50</code>\n\n"
        )

    text += (
        f"💡 <i>{to_smallcaps('supports channel owner, channel admins & bot admins with tag-free copy, custom caption, header/footer & media filter!')}</i>"
        f"{format_watermark_html()}"
    )

    buttons.append([
        InlineKeyboardButton(f"📖 {to_smallcaps('commands guide')}", callback_data=f"autofwd{AF_SEP}help"),
        InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data=f"autofwd{AF_SEP}home"),
    ])
    if rules:
        buttons.append([
            InlineKeyboardButton(f"🗑️ {to_smallcaps('clear all routes')}", callback_data=f"autofwd{AF_SEP}clear"),
        ])
    buttons.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])
    return text, InlineKeyboardMarkup(buttons)


@Client.on_message(filters.command(["autoforward", "forwards", "autofwd"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def autoforward_panel_cmd(client: Client, message: Message):
    """Open the interactive Auto-Forward & Batch Clone control center."""
    uid = message.from_user.id if message.from_user else 0
    logger.info("🔁 /autoforward ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s", uid, message.chat.id)
    text, markup = await build_autoforward_panel(uid)
    await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command(["addforward", "setforward"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def add_autoforward_cmd(client: Client, message: Message):
    """Add an automatic forwarding route from a source chat/channel to a target chat/channel."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    if len(message.command) < 3:
        return await message.reply_text(
            f"✦ <b>{to_smallcaps('add auto-forward route')}</b> ✦ ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📝 <b>{to_smallcaps('usage')}:</b>\n"
            f"  <code>/addforward &lt;source_id/@user&gt; &lt;target_id/@user&gt; [copy|forward]</code>\n\n"
            f"• 💡 <b>{to_smallcaps('example')}:</b>\n"
            f"  <code>/addforward -1001234567890 -1009876543210 copy</code>\n\n"
            f"• 🛡️ <b>{to_smallcaps('note')}:</b> {to_smallcaps('bot must be admin in both chats, and you must be admin/owner of the target chat or bot admin.')}",
            parse_mode=enums.ParseMode.HTML,
        )

    raw_src = message.command[1].strip()
    raw_dst = message.command[2].strip()
    mode_arg = message.command[3].strip().lower() if len(message.command) > 3 else "copy"
    copy_mode = mode_arg != "forward"

    ok_src, src_chat, err_src = await _resolve_chat_and_check_admin(client, raw_src, uid, sender_cid)
    if not ok_src or not src_chat:
        return await message.reply_text(f"❌ <b>{to_smallcaps('source error')}:</b> {err_src}", parse_mode=enums.ParseMode.HTML)

    ok_dst, dst_chat, err_dst = await _resolve_chat_and_check_admin(client, raw_dst, uid, sender_cid)
    if not ok_dst or not dst_chat:
        return await message.reply_text(f"❌ <b>{to_smallcaps('target error')}:</b> {err_dst}", parse_mode=enums.ParseMode.HTML)

    if src_chat.id == dst_chat.id:
        return await message.reply_text(f"⚠️ <b>{to_smallcaps('source and target chats cannot be identical!')}</b>", parse_mode=enums.ParseMode.HTML)

    rule = await db.add_autoforward_rule(
        user_id=uid,
        source_chat_id=src_chat.id,
        target_chat_id=dst_chat.id,
        source_title=src_chat.title or str(src_chat.id),
        target_title=dst_chat.title or str(dst_chat.id),
        copy_mode=copy_mode,
    )
    rid = rule["rule_id"]
    logger.info("✅ ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ʀᴜʟᴇ ᴄʀᴇᴀᴛᴇᴅ › ɪᴅ=%s ᴜsᴇʀ=%s", rid, uid)

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"⚙️ {to_smallcaps('configure route')}", callback_data=f"autofwd{AF_SEP}view{AF_SEP}{rid}"),
            InlineKeyboardButton(f"📋 {to_smallcaps('all routes')}", callback_data=f"autofwd{AF_SEP}home"),
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ])
    await message.reply_text(
        f"✅ <b>{to_smallcaps('auto-forward route activated!')}</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📤 <b>{to_smallcaps('source')}:</b> {html.escape(src_chat.title or str(src_chat.id))} (<code>{src_chat.id}</code>)\n"
        f"• 📥 <b>{to_smallcaps('target')}:</b> {html.escape(dst_chat.title or str(dst_chat.id))} (<code>{dst_chat.id}</code>)\n"
        f"• 🏷️ <b>{to_smallcaps('rule id')}:</b> <code>{rid}</code>\n"
        f"• ⚙️ <b>{to_smallcaps('mode')}:</b> <code>{'ᴄᴏᴘʏ (ɴᴏ ғᴏʀᴡᴀʀᴅ ᴛᴀɢ)' if copy_mode else 'ғᴏʀᴡᴀʀᴅ (ᴡɪᴛʜ ᴛᴀɢ)'}</code>",
        reply_markup=btn,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["rmforward", "delforward", "stopforward"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def remove_autoforward_cmd(client: Client, message: Message):
    """Remove an existing auto-forward route by rule_id or source/target IDs."""
    if len(message.command) < 2:
        return await message.reply_text(
            f"⚠️ <b>{to_smallcaps('usage')}:</b> <code>/rmforward &lt;rule_id&gt;</code>\n"
            f"💡 <i>{to_smallcaps('use /autoforward to view your active rule ids.')}</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    rid = message.command[1].strip()
    if len(message.command) >= 3 and rid.lstrip("-").isdigit():
        rid = f"{int(rid)}_{int(message.command[2].strip())}"

    removed = await db.remove_autoforward_rule(rid)
    if removed:
        await message.reply_text(f"🗑️ <b>{to_smallcaps('auto-forward route removed')}:</b> <code>{html.escape(rid)}</code> ✅", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"❌ <b>{to_smallcaps('route not found')}:</b> <code>{html.escape(rid)}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("clearforwards", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def clear_autoforwards_cmd(client: Client, message: Message):
    """Clear all auto-forward routes belonging to the user."""
    uid = message.from_user.id if message.from_user else 0
    cnt = await db.clear_user_autoforwards(uid)
    await message.reply_text(
        f"🧹 <b>{to_smallcaps(f'cleared {cnt} auto-forward routes!')}</b> ✅",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["fsetcaption", "fsetheader", "fsetfooter"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def customize_autoforward_template_cmd(client: Client, message: Message):
    """Set custom caption, header, or footer on an auto-forward route."""
    cmd = message.command[0].lower()
    parts = message.text.split(None, 2) if message.text else []
    if len(parts) < 2:
        return await message.reply_text(
            f"⚠️ <b>{to_smallcaps('usage')}:</b> <code>/{cmd} &lt;rule_id&gt; &lt;text or off&gt;</code>\n"
            f"• 🏷️ <b>{to_smallcaps('variables')}:</b> <code>{{original_caption}}</code>, <code>{{filename}}</code>, <code>{{filesize}}</code>, <code>{{source_title}}</code>, <code>{{date}}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    rid = parts[1].strip()
    val = parts[2].strip() if len(parts) > 2 else ""
    if val.lower() in ("off", "none", "clear", "reset"):
        val = ""

    field_map = {
        "fsetcaption": "custom_caption",
        "fsetheader": "header_text",
        "fsetfooter": "footer_text",
    }
    field = field_map[cmd]
    updated = await db.update_autoforward_rule(rid, {field: val})
    if not updated:
        return await message.reply_text(f"❌ <b>{to_smallcaps('route not found')}:</b> <code>{html.escape(rid)}</code>", parse_mode=enums.ParseMode.HTML)

    await message.reply_text(
        f"✅ <b>{to_smallcaps(f'{field} updated for route')} <code>{html.escape(rid)}</code>!</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["clonepost", "ccopy"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def clone_single_post_cmd(client: Client, message: Message):
    """Clone/copy a single Telegram post link into a target or connected channel/chat without forward tag."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    if len(message.command) < 2:
        return await message.reply_text(
            f"✦ <b>{to_smallcaps('instant post cloner (no forward tag)')}</b> ✦ ⚡\n\n"
            f"• 📝 <b>{to_smallcaps('usage')}:</b> <code>/clonepost &lt;https://t.me/channel/123&gt; [target_chat_id]</code>\n"
            f"• 💡 <i>{to_smallcaps('if target_chat_id is omitted, clones to your connected channel or current chat.')}</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    src_chat_ref, msg_id = _parse_tg_link(message.command[1])
    if not src_chat_ref or not msg_id:
        return await message.reply_text(f"❌ <b>{to_smallcaps('invalid telegram post link!')}</b>", parse_mode=enums.ParseMode.HTML)

    target_chat_id: Union[int, str] = message.chat.id
    if len(message.command) > 2:
        raw_t = message.command[2].strip()
        target_chat_id = int(raw_t) if raw_t.lstrip("-").isdigit() else raw_t
    elif message.chat.type == enums.ChatType.PRIVATE:
        conn = await db.get_user_connected_channel(uid)
        if conn:
            target_chat_id = conn

    if target_chat_id != message.chat.id:
        ok, _, err = await _resolve_chat_and_check_admin(client, target_chat_id, uid, sender_cid)
        if not ok:
            return await message.reply_text(f"❌ <b>{to_smallcaps('target access denied')}:</b> {err}", parse_mode=enums.ParseMode.HTML)

    try:
        src_msg = await client.get_messages(src_chat_ref, msg_id)
        if not src_msg or src_msg.empty:
            return await message.reply_text(f"❌ <b>{to_smallcaps('source message not found or inaccessible!')}</b>", parse_mode=enums.ParseMode.HTML)
        copied = await src_msg.copy(chat_id=target_chat_id)
        logger.info("📋 /clonepost sᴜᴄᴄᴇss › %s/%s ➔ %s (ʙʏ %s)", src_chat_ref, msg_id, target_chat_id, uid)
        await message.reply_text(
            f"✅ <b>{to_smallcaps('post cloned successfully!')}</b> 🚀\n"
            f"• 📥 <b>{to_smallcaps('target chat')}:</b> <code>{target_chat_id}</code>\n"
            f"• 🆔 <b>{to_smallcaps('new message id')}:</b> <code>{copied.id if copied else 'N/A'}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error("❌ /clonepost ғᴀɪʟᴇᴅ › %s", e)
        await message.reply_text(f"❌ <b>{to_smallcaps('clone failed')}:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["fbatch", "cbatch"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def batch_clone_posts_cmd(client: Client, message: Message):
    """Batch-copy a range of messages from a source channel/group to a target or connected channel."""
    uid = message.from_user.id if message.from_user else 0
    sender_cid = message.sender_chat.id if message.sender_chat else 0

    if len(message.command) < 3:
        return await message.reply_text(
            f"✦ <b>{to_smallcaps('pro batch cloner & forwarder')}</b> ✦ ⚡\n\n"
            f"• 📝 <b>{to_smallcaps('usage')}:</b>\n"
            f"  <code>/fbatch &lt;first_post_link&gt; &lt;last_post_link&gt; [target_chat_id]</code>\n\n"
            f"• 💡 <b>{to_smallcaps('example')}:</b>\n"
            f"  <code>/fbatch https://t.me/mychannel/10 https://t.me/mychannel/35</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    src1, start_id = _parse_tg_link(message.command[1])
    src2, end_id = _parse_tg_link(message.command[2])
    if not src1 or not start_id or not end_id or src1 != src2:
        return await message.reply_text(
            f"❌ <b>{to_smallcaps('please provide two valid links from the same source channel!')}</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    total_span = end_id - start_id + 1
    if total_span > 200:
        return await message.reply_text(
            f"⚠️ <b>{to_smallcaps('batch limit is 200 messages per run to prevent floodwaits.')}</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    target_chat_id: Union[int, str] = message.chat.id
    if len(message.command) > 3:
        raw_t = message.command[3].strip()
        target_chat_id = int(raw_t) if raw_t.lstrip("-").isdigit() else raw_t
    elif message.chat.type == enums.ChatType.PRIVATE:
        conn = await db.get_user_connected_channel(uid)
        if conn:
            target_chat_id = conn

    if target_chat_id != message.chat.id:
        ok, _, err = await _resolve_chat_and_check_admin(client, target_chat_id, uid, sender_cid)
        if not ok:
            return await message.reply_text(f"❌ <b>{to_smallcaps('target access denied')}:</b> {err}", parse_mode=enums.ParseMode.HTML)

    sts = await message.reply_text(
        f"📡 <b>{to_smallcaps('starting batch clone...')}</b>\n"
        f"• 📦 <b>{to_smallcaps('range')}:</b> <code>{start_id} ➔ {end_id}</code> ({total_span} ᴍsɢs)",
        parse_mode=enums.ParseMode.HTML,
    )

    copied_cnt = 0
    skipped_cnt = 0
    for mid in range(start_id, end_id + 1):
        try:
            msg = await client.get_messages(src1, mid)
            if not msg or msg.empty or msg.service:
                skipped_cnt += 1
                continue
            await msg.copy(chat_id=target_chat_id)
            copied_cnt += 1
            await asyncio.sleep(0.8)
            if (copied_cnt + skipped_cnt) % 10 == 0:
                await safe_edit_message(
                    sts,
                    f"📡 <b>{to_smallcaps('batch clone in progress...')}</b> ⚡\n"
                    f"• ✅ <b>{to_smallcaps('copied')}:</b> <code>{copied_cnt}</code>\n"
                    f"• ⏭️ <b>{to_smallcaps('skipped')}:</b> <code>{skipped_cnt}</code>\n"
                    f"• 📊 <b>{to_smallcaps('progress')}:</b> <code>{copied_cnt + skipped_cnt}/{total_span}</code>",
                )
        except FloodWait as fw:
            wait_s = int(getattr(fw, "value", 5)) + 2
            wait_s = min(max(wait_s, 1), 90)
            logger.warning("🌊 ʙᴀᴛᴄʜ ᴄʟᴏɴᴇ ғʟᴏᴏᴅᴡᴀɪᴛ › sleeping %ss (retrying msg %s)", wait_s, mid)
            await asyncio.sleep(wait_s)
            try:
                msg_retry = await client.get_messages(src1, mid)
                if msg_retry and not msg_retry.empty and not msg_retry.service:
                    await msg_retry.copy(chat_id=target_chat_id)
                    copied_cnt += 1
            except Exception:
                skipped_cnt += 1
        except Exception:
            skipped_cnt += 1

    logger.info("✅ /fbatch ᴄᴏᴍᴘʟᴇᴛᴇᴅ › ᴄᴏᴘɪᴇᴅ=%s sᴋɪᴘᴘᴇᴅ=%s ᴛᴀʀɢᴇᴛ=%s", copied_cnt, skipped_cnt, target_chat_id)
    await safe_edit_message(
        sts,
        f"✅ <b>{to_smallcaps('batch clone completed!')}</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📥 <b>{to_smallcaps('target')}:</b> <code>{target_chat_id}</code>\n"
        f"• ✅ <b>{to_smallcaps('copied posts')}:</b> <code>{copied_cnt}</code>\n"
        f"• ⏭️ <b>{to_smallcaps('skipped/empty')}:</b> <code>{skipped_cnt}</code>",
    )


@Client.on_callback_query(filters.regex(r"^autofwd\|"))
async def autoforward_callback_router(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    parts = query.data.split(AF_SEP)
    action = parts[1] if len(parts) > 1 else "home"

    if action == "home":
        await query.answer("🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ᴄᴇɴᴛᴇʀ")
        text, markup = await build_autoforward_panel(uid)
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    if action == "help":
        await query.answer("📖 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ɢᴜɪᴅᴇ")
        help_txt = (
            f"✦ <b>{to_smallcaps('pro auto-forward & batch clone guide')}</b> ✦ ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <code>/autoforward</code> — {to_smallcaps('open interactive route manager')}\n"
            f"• <code>/addforward &lt;src&gt; &lt;dst&gt; [copy|forward]</code> — {to_smallcaps('create live auto-forward route')}\n"
            f"• <code>/rmforward &lt;rule_id&gt;</code> — {to_smallcaps('delete a route')}\n"
            f"• <code>/clearforwards</code> — {to_smallcaps('remove all your routes')}\n"
            f"• <code>/fsetcaption &lt;rule_id&gt; &lt;template&gt;</code> — {to_smallcaps('custom caption template')}\n"
            f"• <code>/fsetheader &lt;rule_id&gt; &lt;text&gt;</code> — {to_smallcaps('prepend custom header')}\n"
            f"• <code>/fsetfooter &lt;rule_id&gt; &lt;text&gt;</code> — {to_smallcaps('append custom footer')}\n"
            f"• <code>/clonepost &lt;link&gt; [target]</code> — {to_smallcaps('copy single post without tag')}\n"
            f"• <code>/fbatch &lt;start_link&gt; &lt;end_link&gt; [target]</code> — {to_smallcaps('batch copy range of posts')}\n"
            f"{format_watermark_html()}"
        )
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"autofwd{AF_SEP}home"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ]
        ])
        await safe_edit_message(query.message, help_txt, reply_markup=btn, parse_mode=enums.ParseMode.HTML)
        return

    if action == "clear":
        cnt = await db.clear_user_autoforwards(uid)
        await query.answer(f"🗑️ ᴄʟᴇᴀʀᴇᴅ {cnt} ʀᴏᴜᴛᴇs!", show_alert=True)
        text, markup = await build_autoforward_panel(uid)
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    if action in ("view", "toggle", "copy", "media", "strip", "del") and len(parts) > 2:
        rid = parts[2]
        rule = await db.get_autoforward_rule(rid)
        if not rule:
            return await query.answer("❌ ʀᴏᴜᴛᴇ ɴᴏᴛ ғᴏᴜɴᴅ!", show_alert=True)
        if rule.get("user_id") != uid and not is_admin_sync(uid):
            return await query.answer("⛔ ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ!", show_alert=True)

        if action == "del":
            await db.remove_autoforward_rule(rid)
            await query.answer("🗑️ ʀᴏᴜᴛᴇ ᴅᴇʟᴇᴛᴇᴅ!", show_alert=True)
            text, markup = await build_autoforward_panel(uid)
            await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
            return

        if action == "toggle":
            rule = await db.update_autoforward_rule(rid, {"enabled": not rule.get("enabled", True)})
        elif action == "copy":
            rule = await db.update_autoforward_rule(rid, {"copy_mode": not rule.get("copy_mode", True)})
        elif action == "media":
            rule = await db.update_autoforward_rule(rid, {"media_only": not rule.get("media_only", False)})
        elif action == "strip":
            rule = await db.update_autoforward_rule(rid, {"strip_buttons": not rule.get("strip_buttons", False)})

        await query.answer("⚙️ ᴜᴘᴅᴀᴛᴇᴅ!")
        st = "🟢 ᴇɴᴀʙʟᴇᴅ" if rule.get("enabled", True) else "⚪ ᴘᴀᴜsᴇᴅ"
        cm = "📋 ᴄᴏᴘʏ (ɴᴏ ᴛᴀɢ)" if rule.get("copy_mode", True) else "⏩ ғᴏʀᴡᴀʀᴅ (ᴡɪᴛʜ ᴛᴀɢ)"
        mo = "🎬 ᴍᴇᴅɪᴀ ᴏɴʟʏ" if rule.get("media_only", False) else "📨 ᴀʟʟ ᴘᴏsᴛs"
        sb = "🚫 sᴛʀɪᴘ ʙᴜᴛᴛᴏɴs" if rule.get("strip_buttons", False) else "✅ ᴋᴇᴇᴘ ʙᴜᴛᴛᴏɴs"

        detail_txt = (
            f"✦ <b>{to_smallcaps('auto-forward route inspector')}</b> ✦ ⚙️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🏷️ <b>{to_smallcaps('rule id')}:</b> <code>{rid}</code>\n"
            f"• 📤 <b>{to_smallcaps('source')}:</b> {html.escape(str(rule.get('source_title')))} (<code>{rule.get('source_chat_id')}</code>)\n"
            f"• 📥 <b>{to_smallcaps('target')}:</b> {html.escape(str(rule.get('target_title')))} (<code>{rule.get('target_chat_id')}</code>)\n"
            f"• 🔋 <b>{to_smallcaps('status')}:</b> <code>{st}</code>\n"
            f"• 📤 <b>{to_smallcaps('delivery')}:</b> <code>{cm}</code>\n"
            f"• 🎞️ <b>{to_smallcaps('filter')}:</b> <code>{mo}</code>\n"
            f"• 🔘 <b>{to_smallcaps('buttons')}:</b> <code>{sb}</code>\n"
            f"• 📊 <b>{to_smallcaps('forwarded')}:</b> <code>{rule.get('forwarded_count', 0)} ᴘᴏsᴛs</code>\n\n"
            f"• 📝 <b>{to_smallcaps('custom caption')}:</b> <code>{html.escape(rule.get('custom_caption') or 'ᴅᴇғᴀᴜʟᴛ')}</code>\n"
            f"• ⬆️ <b>{to_smallcaps('header')}:</b> <code>{html.escape(rule.get('header_text') or 'ɴᴏɴᴇ')}</code>\n"
            f"• ⬇️ <b>{to_smallcaps('footer')}:</b> <code>{html.escape(rule.get('footer_text') or 'ɴᴏɴᴇ')}</code>"
            f"{format_watermark_html()}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🔋 {st}", callback_data=f"autofwd{AF_SEP}toggle{AF_SEP}{rid}"),
                InlineKeyboardButton(f"📤 {cm}", callback_data=f"autofwd{AF_SEP}copy{AF_SEP}{rid}"),
            ],
            [
                InlineKeyboardButton(f"🎞️ {mo}", callback_data=f"autofwd{AF_SEP}media{AF_SEP}{rid}"),
                InlineKeyboardButton(f"🔘 {sb}", callback_data=f"autofwd{AF_SEP}strip{AF_SEP}{rid}"),
            ],
            [
                InlineKeyboardButton(f"🗑️ {to_smallcaps('delete route')}", callback_data=f"autofwd{AF_SEP}del{AF_SEP}{rid}"),
            ],
            [
                InlineKeyboardButton(f"🔙 {to_smallcaps('back')}", callback_data=f"autofwd{AF_SEP}home"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ],
        ])
        await safe_edit_message(query.message, detail_txt, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message((filters.channel | filters.group) & filters.incoming & ~filters.service, group=7)
async def live_autoforward_dispatcher(client: Client, message: Message):
    """Real-time watcher that dispatches incoming posts across all matching Auto-Forward routes."""
    if not temp.BOT_CONFIG.get("auto_forward_global", True):
        return
    if message.text and message.text.startswith(tuple(COMMAND_PREFIXES)):
        return

    rules = await db.get_matching_autoforward_rules(message.chat.id)
    if not rules:
        return

    has_media = bool(message.media)
    source_title = message.chat.title or str(message.chat.id)

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if rule.get("media_only", False) and not has_media:
            continue

        target_cid = int(rule["target_chat_id"])
        rid = rule["rule_id"]
        dedup_key = (int(message.chat.id), int(message.id), target_cid)
        now_ts = time.monotonic()
        if dedup_key in _FORWARDED_DEDUP_CACHE and (now_ts - _FORWARDED_DEDUP_CACHE[dedup_key]) < 300:
            continue
        _FORWARDED_DEDUP_CACHE[dedup_key] = now_ts
        if len(_FORWARDED_DEDUP_CACHE) > 5000:
            cutoff = now_ts - 300
            for k_old, ts_old in list(_FORWARDED_DEDUP_CACHE.items()):
                if ts_old < cutoff:
                    _FORWARDED_DEDUP_CACHE.pop(k_old, None)
        copy_mode = rule.get("copy_mode", True)
        strip_btns = rule.get("strip_buttons", False)
        reply_markup = None if strip_btns else message.reply_markup

        try:
            if not copy_mode:
                await message.forward(target_cid)
            else:
                formatted_text = _format_forward_caption(rule, message, source_title)
                if formatted_text:
                    cleaned_text, parsed_markup = parse_Inline_buttons_from_text(formatted_text)
                    final_markup = parsed_markup or reply_markup
                else:
                    cleaned_text = None
                    final_markup = reply_markup

                if has_media:
                    await message.copy(
                        chat_id=target_cid,
                        caption=cleaned_text[:1024] if cleaned_text is not None else None,
                        reply_markup=final_markup,
                    )
                elif cleaned_text:
                    await client.send_message(
                        chat_id=target_cid,
                        text=cleaned_text[:4096],
                        reply_markup=final_markup,
                        disable_web_page_preview=True,
                    )
                else:
                    await message.copy(chat_id=target_cid, reply_markup=final_markup)

            new_cnt = int(rule.get("forwarded_count", 0)) + 1
            await db.update_autoforward_rule(rid, {"forwarded_count": new_cnt})
            logger.info("🔁 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅᴇᴅ ᴍsɢ %s › %s ➔ %s (ᴄᴏᴜɴᴛ=%s)", message.id, message.chat.id, target_cid, new_cnt)
        except FloodWait as fw:
            wait_s = int(getattr(fw, "value", 3)) + 2
            wait_s = min(max(wait_s, 1), 60)
            logger.warning("🌊 ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ғʟᴏᴏᴅᴡᴀɪᴛ › sleeping %ss", wait_s)
            await asyncio.sleep(wait_s)
        except Exception as e:
            logger.debug("⚠️ ᴀᴜᴛᴏ-ғᴏʀᴡᴀʀᴅ ᴇʀʀᴏʀ (%s) › %s", rid, e)
