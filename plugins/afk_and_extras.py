import asyncio
import html
import logging
import time
from typing import Dict, Set

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from database.users_chats_db import db
from info import COMMAND_PREFIXES
from utils import get_readable_time, is_chat_or_global_admin, safe_edit_message
from plugins.help_desk_features import verify_group_and_permissions

logger = logging.getLogger("GokuAfkExtras")

_ACTIVE_TAGALL_CHATS: Set[int] = set()


@Client.on_message(filters.command("afk", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def set_afk_cmd(client: Client, message: Message):
    """Set user status as AFK (Away From Keyboard) with an optional reason."""
    if not message.from_user:
        return
    reason = message.text.split(None, 1)[1].strip() if len(message.text.split(None, 1)) > 1 else "ʙᴜsʏ / ᴀᴡᴀʏ ғʀᴏᴍ ᴋᴇʏʙᴏᴀʀᴅ"
    await db.set_afk(message.from_user.id, reason)
    await message.reply_text(
        f"💤 <b>{message.from_user.mention} ɪs ɴᴏᴡ ᴀғᴋ!</b>\n"
        f"📌 <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason[:180])}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.incoming & ~filters.service, group=6)
async def afk_watchdog(client: Client, message: Message):
    """Monitor incoming group messages to clear sender AFK or notify when an AFK user is mentioned/replied to."""
    if message.from_user and not message.from_user.is_bot:
        cmd_word = (message.text or "").split()[0].lower().lstrip("/!.") if message.text else ""
        if cmd_word != "afk":
            prev_afk = await db.clear_afk(message.from_user.id)
            if prev_afk:
                dur = get_readable_time(time.time() - float(prev_afk.get("since", time.time())))
                try:
                    await message.reply_text(
                        f"🌅 <b>ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ {message.from_user.mention}!</b> ✨\n"
                        f"⏱️ <i>ʏᴏᴜ ᴡᴇʀᴇ ᴀғᴋ ғᴏʀ <code>{dur}</code>.</i>",
                        quote=True,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

    checked_ids: Set[int] = set()
    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        if not ru.is_bot and (not message.from_user or ru.id != message.from_user.id):
            checked_ids.add(ru.id)
            afk_doc = await db.get_afk(ru.id)
            if afk_doc:
                dur = get_readable_time(time.time() - float(afk_doc.get("since", time.time())))
                try:
                    await message.reply_text(
                        f"💤 <b>{ru.mention} ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴀғᴋ!</b>\n"
                        f"• <b>sɪɴᴄᴇ:</b> <code>{dur} ᴀɢᴏ</code>\n"
                        f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(str(afk_doc.get('reason', 'ʙᴜsʏ')))}</code>",
                        quote=True,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

    for ent in (message.entities or []):
        if ent.type == enums.MessageEntityType.TEXT_MENTION and ent.user and ent.user.id not in checked_ids:
            checked_ids.add(ent.user.id)
            afk_doc = await db.get_afk(ent.user.id)
            if afk_doc:
                dur = get_readable_time(time.time() - float(afk_doc.get("since", time.time())))
                try:
                    await message.reply_text(
                        f"💤 <b>{ent.user.mention} ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴀғᴋ!</b>\n"
                        f"• <b>sɪɴᴄᴇ:</b> <code>{dur} ᴀɢᴏ</code>\n"
                        f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(str(afk_doc.get('reason', 'ʙᴜsʏ')))}</code>",
                        quote=True,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass


@Client.on_message(filters.command(["tagall", "mentionall", "utag"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def tag_all_cmd(client: Client, message: Message):
    """Mention group members in batches of 5 with FloodWait protection."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=False
    )
    if not ok:
        return

    chat_id = message.chat.id
    if chat_id in _ACTIVE_TAGALL_CHATS:
        return await message.reply_text(
            "⚠️ <b>ᴀ ᴛᴀɢ-ᴀʟʟ sᴇssɪᴏɴ ɪs ᴀʟʀᴇᴀᴅʏ ʀᴜɴɴɪɴɢ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ!</b>\n"
            "<i>ᴜsᴇ <code>/canceltag</code> ᴛᴏ sᴛᴏᴘ ɪᴛ.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    note_text = ""
    if len(message.command) > 1:
        note_text = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message:
        note_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not note_text:
        note_text = "📢 <b>ᴀᴛᴛᴇɴᴛɪᴏɴ ᴇᴠᴇʀʏᴏɴᴇ!</b>"

    _ACTIVE_TAGALL_CHATS.add(chat_id)
    logger.info("📢 /tagall sᴛᴀʀᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴀᴅᴍɪɴ=%s", chat_id, message.from_user.id if message.from_user else 0)

    batch = []
    total_tagged = 0
    try:
        async for member in client.get_chat_members(chat_id):
            if chat_id not in _ACTIVE_TAGALL_CHATS:
                break
            u = member.user
            if not u or u.is_bot or u.is_deleted:
                continue
            batch.append(u.mention)
            total_tagged += 1
            if len(batch) == 5:
                try:
                    await client.send_message(
                        chat_id,
                        f"📢 {note_text}\n\n" + " • ".join(batch),
                        parse_mode=enums.ParseMode.HTML,
                    )
                except FloodWait as fw:
                    await asyncio.sleep(int(getattr(fw, "value", 5)) + 1)
                batch.clear()
                await asyncio.sleep(2.2)

        if batch and chat_id in _ACTIVE_TAGALL_CHATS:
            await client.send_message(
                chat_id,
                f"📢 {note_text}\n\n" + " • ".join(batch),
                parse_mode=enums.ParseMode.HTML,
            )
        await message.reply_text(
            f"✅ <b>ᴛᴀɢ-ᴀʟʟ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b> ᴍᴇɴᴛɪᴏɴᴇᴅ <code>{total_tagged}</code> ᴍᴇᴍʙᴇʀs.",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error("❌ /tagall ᴇʀʀᴏʀ › %s", e)
    finally:
        _ACTIVE_TAGALL_CHATS.discard(chat_id)


@Client.on_message(filters.command(["canceltag", "stoptag"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def cancel_tag_cmd(client: Client, message: Message):
    """Stop an active TagAll session in the group."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=False
    )
    if not ok:
        return
    if message.chat.id in _ACTIVE_TAGALL_CHATS:
        _ACTIVE_TAGALL_CHATS.discard(message.chat.id)
        await message.reply_text("🛑 <b>ᴛᴀɢ-ᴀʟʟ sᴇssɪᴏɴ sᴛᴏᴘᴘᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("ℹ️ <b>ɴᴏ ᴀᴄᴛɪᴠᴇ ᴛᴀɢ-ᴀʟʟ sᴇssɪᴏɴ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["zombies", "clean_deleted", "autobannow"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def zombies_cleaner_cmd(client: Client, message: Message):
    """Scan for and ban/remove deleted/deactivated accounts ('Deleted Account') from a group."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members", allow_channel=False
    )
    if not ok:
        return

    cmd = message.command[0].lower()
    clean_now = (cmd == "autobannow") or (len(message.command) > 1 and message.command[1].lower() in ("clean", "kick", "purge", "yes", "ban"))
    wait = await message.reply_text("🔍 <b>sᴄᴀɴɴɪɴɢ ɢʀᴏᴜᴘ ғᴏʀ ᴅᴇʟᴇᴛᴇᴅ / ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs...</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    zombie_ids = []
    total_scanned = 0
    try:
        async for m in client.get_chat_members(message.chat.id):
            total_scanned += 1
            if m.user and m.user.is_deleted:
                zombie_ids.append(m.user.id)
    except Exception as e:
        return await wait.edit_text(f"❌ <b>sᴄᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)

    if not zombie_ids:
        await db.update_helpdesk_config(
            message.chat.id,
            {
                "autoban_stats": {
                    "scanned": total_scanned,
                    "found": 0,
                    "banned": 0,
                    "timestamp": time.time(),
                    "admin_id": message.from_user.id if message.from_user else 0,
                }
            },
        )
        return await wait.edit_text(
            f"✨ <b>ɢʀᴏᴜᴘ ɪs ᴄʟᴇᴀɴ! ɴᴏ ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs ғᴏᴜɴᴅ.</b> (sᴄᴀɴɴᴇᴅ: <code>{total_scanned}</code>)",
            parse_mode=enums.ParseMode.HTML,
        )

    if not clean_now:
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🧹 ʙᴀɴ {len(zombie_ids)} ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs", callback_data="xtra|clean_zombies"),
                InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="close_data"),
            ]
        ])
        return await wait.edit_text(
            f"🧟 <b>ғᴏᴜɴᴅ <code>{len(zombie_ids)}</code> ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛ(s) (ᴏᴜᴛ ᴏғ <code>{total_scanned}</code> sᴄᴀɴɴᴇᴅ)!</b>\n\n"
            "<i>ᴛᴀᴘ ʙᴇʟᴏᴡ ᴏʀ ᴜsᴇ <code>/autobannow</code> ᴛᴏ ʙᴀɴ ᴛʜᴇᴍ:</i>",
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML,
        )

    banned = 0
    for zid in zombie_ids:
        try:
            await client.ban_chat_member(message.chat.id, zid)
            await asyncio.sleep(0.2)
            if cmd != "autobannow":
                await client.unban_chat_member(message.chat.id, zid)
            banned += 1
        except Exception:
            pass
    await db.update_helpdesk_config(
        message.chat.id,
        {
            "autoban_stats": {
                "scanned": total_scanned,
                "found": len(zombie_ids),
                "banned": banned,
                "timestamp": time.time(),
                "admin_id": message.from_user.id if message.from_user else 0,
            }
        },
    )
    logger.info("🧹 /%s ᴄᴏᴍᴘʟᴇᴛᴇᴅ › ᴄʜᴀᴛ=%s sᴄᴀɴɴᴇᴅ=%s ʙᴀɴɴᴇᴅ=%s", cmd, message.chat.id, total_scanned, banned)
    await wait.edit_text(
        f"🧹 <b>ᴀᴜᴛᴏ-ʙᴀɴ sᴄᴀɴ ᴄᴏᴍᴘʟᴇᴛᴇ!</b>\n"
        f"• 👥 <b>sᴄᴀɴɴᴇᴅ:</b> <code>{total_scanned}</code>\n"
        f"• 🚫 <b>ᴅᴇʟᴇᴛᴇᴅ/ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ ʙᴀɴɴᴇᴅ:</b> <code>{banned}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("autobanstats", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def autoban_stats_cmd(client: Client, message: Message):
    """Show the latest auto-ban scan result for the group."""
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ <code>/autobanstats</code> ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    cfg = await db.get_helpdesk_config(message.chat.id)
    st = cfg.get("autoban_stats") or {}
    if not st or not st.get("timestamp"):
        return await message.reply_text(
            "ℹ️ <b>ɴᴏ ᴀᴜᴛᴏ-ʙᴀɴ sᴄᴀɴ ʜᴀs ʙᴇᴇɴ ʀᴜɴ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ ʏᴇᴛ.</b>\n"
            "💡 <i>ʀᴜɴ <code>/autobannow</code> ᴛᴏ sᴄᴀɴ ᴀɴᴅ ʙᴀɴ ᴅᴇʟᴇᴛᴇᴅ/ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs!</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    ago = get_readable_time(max(0, time.time() - float(st.get("timestamp", time.time()))))
    await message.reply_text(
        f"📊 <b>ʟᴀᴛᴇsᴛ ᴀᴜᴛᴏ-ʙᴀɴ sᴄᴀɴ sᴛᴀᴛs — {html.escape(message.chat.title or 'Group')}</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 👥 <b>ᴍᴇᴍʙᴇʀs sᴄᴀɴɴᴇᴅ:</b> <code>{st.get('scanned', 0)}</code>\n"
        f"• 🧟 <b>ᴅᴇʟᴇᴛᴇᴅ ғᴏᴜɴᴅ:</b> <code>{st.get('found', 0)}</code>\n"
        f"• 🚫 <b>ᴀᴄᴄᴏᴜɴᴛs ʙᴀɴɴᴇᴅ:</b> <code>{st.get('banned', 0)}</code>\n"
        f"• ⏱️ <b>ʟᴀsᴛ sᴄᴀɴ:</b> <code>{ago} ᴀɢᴏ</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^xtra\|clean_zombies$"))
async def clean_zombies_cb(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id if query.message else 0
    uid = query.from_user.id if query.from_user else 0
    if not await is_chat_or_global_admin(client, chat_id, user_id=uid):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    await query.answer("🧹 ᴄʟᴇᴀɴɪɴɢ ᴢᴏᴍʙɪᴇs...")
    kicked = 0
    try:
        async for m in client.get_chat_members(chat_id):
            if m.user and m.user.is_deleted:
                try:
                    await client.ban_chat_member(chat_id, m.user.id)
                    await asyncio.sleep(0.2)
                    await client.unban_chat_member(chat_id, m.user.id)
                    kicked += 1
                except Exception:
                    pass
    except Exception:
        pass
    await safe_edit_message(
        query.message,
        f"🧹 <b>sᴜᴄᴄᴇssғᴜʟʟʏ ʀᴇᴍᴏᴠᴇᴅ <code>{kicked}</code> ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs!</b>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["bots", "botlist"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def list_group_bots_cmd(client: Client, message: Message):
    """List all bots present in the group or channel."""
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
        return await message.reply_text("⚠️ <b>ᴜsᴇ <code>/bots</code> ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ ᴏʀ ᴄʜᴀɴɴᴇʟ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    bots = []
    try:
        async for m in client.get_chat_members(message.chat.id):
            if m.user and m.user.is_bot:
                badge = "👮 [ᴀᴅᴍɪɴ]" if m.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER) else "🤖"
                bots.append(f"• {badge} {m.user.mention} (<code>@{m.user.username or 'bot'}</code>)")
    except Exception as e:
        return await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    await message.reply_text(
        f"🤖 <b>ʙᴏᴛs ɪɴ {html.escape(message.chat.title or 'Chat')} ({len(bots)}):</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        + ("\n".join(bots[:60]) if bots else "ɴᴏ ʙᴏᴛs ғᴏᴜɴᴅ."),
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["poll", "cpoll"], prefixes=COMMAND_PREFIXES))
async def create_poll_cmd(client: Client, message: Message):
    """Create an interactive Telegram poll in the group or channel."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=True
    )
    if not ok:
        return

    raw = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if len(parts) < 3:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/poll Question | Option 1 | Option 2 | Option 3</code>\n"
            "<i>(ᴘʀᴏᴠɪᴅᴇ ᴀ ǫᴜᴇsᴛɪᴏɴ ᴀɴᴅ 2–10 ᴏᴘᴛɪᴏɴs sᴇᴘᴀʀᴀᴛᴇᴅ ʙʏ <code>|</code>)</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    question = parts[0][:255]
    options = [o[:100] for o in parts[1:11]]
    try:
        await client.send_poll(
            message.chat.id,
            question=question,
            options=options,
            is_anonymous=False if message.chat.type != enums.ChatType.CHANNEL else True,
        )
        logger.info("📊 /poll ᴄʀᴇᴀᴛᴇᴅ › ᴄʜᴀᴛ=%s ᴏᴘᴛɪᴏɴs=%s", message.chat.id, len(options))
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴘᴏʟʟ ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)
