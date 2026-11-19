import asyncio
import html
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
from info import COMMAND_PREFIXES
from plugins.core_db import mongo
from plugins.core_utils import (
    execute_with_floodwait,
    logger,
    safe_edit_text,
    safe_reply_text,
    to_smallcaps,
)
from utils import format_watermark_html, is_admin_sync, is_chat_or_global_admin, safe_edit_message

RP_SEP = "|"
_SCHEDULED_POST_TASKS: Dict[str, asyncio.Task] = {}
_SCHEDULED_POST_META: Dict[str, Dict[str, Any]] = {}


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
            f"⛔ <b>{to_smallcaps('only group admins or bot admins can toggle anti-channel!')}</b>",
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
        f"✦ <b>{to_smallcaps('rose anti-channel shield')}</b> ✦ 🛡️\n\n"
        f"• 📢 <b>{to_smallcaps('status')}:</b> <code>{badge}</code>\n"
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
        f"✦ <b>{to_smallcaps('group pro analytics & security')}</b> ✦ 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 💬 <b>{to_smallcaps('group')}:</b> {html.escape(title or str(chat_id))} (<code>{chat_id}</code>)\n"
        f"• 👥 <b>{to_smallcaps('members')}:</b> <code>{members}</code>\n"
        f"• 🌐 <b>{to_smallcaps('federation')}:</b> <code>{html.escape(str(fed_id))}</code>\n"
        f"• 🌊 <b>{to_smallcaps('anti-flood')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('antiflood_enabled') else '⚪ ᴏғғ'}</code>\n"
        f"• 🛡️ <b>{to_smallcaps('anti-spam')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('antispam_enabled') else '⚪ ᴏғғ'}</code>\n"
        f"• 📢 <b>{to_smallcaps('anti-channel')}:</b> <code>{'🟢 ᴏɴ' if cfg.get('antichannel_enabled') else '⚪ ᴏғғ'}</code>\n"
        f"• 🚨 <b>{to_smallcaps('raid mode')}:</b> <code>{'🔴 ᴏɴ' if cfg.get('raid_mode') else '⚪ ᴏғғ'}</code>\n"
        f"• 🔐 <b>{to_smallcaps('active locks')}:</b> <code>{', '.join(active_locks) if active_locks else 'None'}</code>"
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
            f"📝 <b>{to_smallcaps('usage')}:</b> <code>/newfed &lt;federation_name&gt;</code>{format_watermark_html()}",
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
        f"✦ <b>{to_smallcaps('federation created')}</b> ✦ 🌐\n\n"
        f"• 🏷️ <b>{to_smallcaps('name')}:</b> <code>{html.escape(fed_name)}</code>\n"
        f"• 🆔 <b>{to_smallcaps('fed id')}:</b> <code>{fed_id}</code>\n"
        f"• 💡 {to_smallcaps('link any group with')} <code>/joinfed {fed_id}</code>"
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
            f"⛔ <b>{to_smallcaps('only group admins or bot admins can manage federation links!')}</b>",
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
            f"✅ <b>{to_smallcaps('group disconnected from federation!')}</b>{format_watermark_html()}",
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
                f"ℹ️ <b>{to_smallcaps('no federation linked. use /newfed or /finfo <fed_id>.')}</b>{format_watermark_html()}",
                parse_mode=enums.ParseMode.HTML,
            )
        return await safe_reply_text(
            message,
            f"✦ <b>{to_smallcaps('federation info')}</b> ✦ 🌐\n\n"
            f"• 🏷️ <b>{to_smallcaps('name')}:</b> <code>{html.escape(str(fed.get('name', 'Fed')))}</code>\n"
            f"• 🆔 <b>{to_smallcaps('fed id')}:</b> <code>{fed.get('fed_id')}</code>\n"
            f"• 👑 <b>{to_smallcaps('owner')}:</b> <code>{fed.get('owner_id')}</code>\n"
            f"• 💬 <b>{to_smallcaps('linked chats')}:</b> <code>{len(fed.get('chats') or [])}</code>\n"
            f"• 🚫 <b>{to_smallcaps('fed-banned users')}:</b> <code>{len(fed.get('banned_users') or {})}</code>"
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
            f"⛔ <b>{to_smallcaps('only chat/channel admins or bot admins can schedule posts!')}</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 <b>{to_smallcaps('usage')}:</b> <code>/schedulepost 10m Hello everyone!</code> {to_smallcaps('or reply to a message with')} <code>/schedulepost 30m</code>"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    delay_sec = _parse_delay_seconds(message.command[1])
    if delay_sec <= 0:
        return await safe_reply_text(
            message,
            f"⚠️ <b>{to_smallcaps('invalid delay! use formats like 30s, 10m, 2h, 1d.')}</b>{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )
    post_text = message.text.split(None, 2)[2].strip() if len(message.command) > 2 else ""
    reply_src = message.reply_to_message
    if not post_text and not reply_src:
        return await safe_reply_text(
            message,
            f"⚠️ <b>{to_smallcaps('provide post text or reply to a message to schedule.')}</b>{format_watermark_html()}",
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
                f"📝 <b>{to_smallcaps('usage')}:</b> <code>/delscheduled &lt;job_id&gt;</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        jid = message.command[1].strip()
        meta = _SCHEDULED_POST_META.get(jid)
        if not meta:
            return await safe_reply_text(message, f"❌ <b>{to_smallcaps('scheduled job not found!')}</b>", parse_mode=enums.ParseMode.HTML)
        if int(meta.get("user_id", 0)) != uid and not is_admin_sync(uid):
            return await safe_reply_text(message, f"⛔ <b>{to_smallcaps('unauthorized!')}</b>", parse_mode=enums.ParseMode.HTML)
        task = _SCHEDULED_POST_TASKS.pop(jid, None)
        if task and not task.done():
            task.cancel()
        _SCHEDULED_POST_META.pop(jid, None)
        return await safe_reply_text(message, f"✅ <b>{to_smallcaps('scheduled post cancelled')}</b> (<code>{jid}</code>)", parse_mode=enums.ParseMode.HTML)

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
        f"✦ <b>{to_smallcaps('mmw private & admin quick studio')}</b> ✦ ⚡\n\n"
        f"• ✏️ <code>/rename</code> • 🗜️ <code>/compress</code> • 🎬 <code>/stream</code>\n"
        f"• ♻️ <code>/tasks</code> • 🗑️ <code>/delete_task</code> • 🧹 <code>/clear_tasks</code>\n"
        f"• 🌐 <code>/newfed</code> • 🛡️ <code>/antichannel</code> • ⏰ <code>/schedulepost</code>"
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
        return

    await query.answer()
