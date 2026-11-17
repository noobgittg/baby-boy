import os
import html
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyrogram.errors import ChatAdminRequired, PeerIdInvalid
from pyrogram.errors.exceptions.bad_request_400 import MessageTooLong

from info import *
from Script import script
from utils import *
from database.users_chats_db import db

logger = logging.getLogger("MMWChatAdmin")


@Client.on_message(filters.new_chat_members & filters.group, group=1)
async def save_group(bot: Client, message: Message):
    r_j_check = [u.id for u in message.new_chat_members]

    if temp.ME in r_j_check:
        logger.info("🎉 ʙᴏᴛ ᴀᴅᴅᴇᴅ ᴛᴏ ɢʀᴏᴜᴘ › ᴄʜᴀᴛ=%s (%s)", message.chat.id, message.chat.title)
        if not await db.get_chat(message.chat.id):
            total = 0
            try:
                total = await bot.get_chat_members_count(message.chat.id)
            except Exception:
                pass
            if LOG_CHANNEL and temp.BOT_CONFIG.get("log_new_groups", True):
                try:
                    adder = message.from_user.mention if message.from_user else "ᴜɴᴋɴᴏᴡɴ"
                    await bot.send_message(
                        LOG_CHANNEL,
                        script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, adder),
                    )
                except Exception as e:
                    logger.debug("⚠️ ʟᴏɢ_ᴄʜᴀɴɴᴇʟ ɴᴇᴡ ɢʀᴏᴜᴘ ᴡᴀʀɴ › %s", e)
            await db.add_chat(message.chat.id, message.chat.title or "Group")

        if message.chat.id in temp.BANNED_CHATS:
            buttons = [[InlineKeyboardButton("🆘 sᴜᴘᴘᴏʀᴛ", url=f"https://t.me/{SUPPORT_CHAT}")]]
            reply_markup = InlineKeyboardMarkup(buttons)

            k = await message.reply(
                text=(
                    "<b>🚫 ᴄʜᴀᴛ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ\n\n"
                    "ᴛʜɪs ʙᴏᴛ ɪs ʀᴇsᴛʀɪᴄᴛᴇᴅ ғʀᴏᴍ ᴡᴏʀᴋɪɴɢ ʜᴇʀᴇ.\n"
                    "ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ ғᴏʀ ᴅᴇᴛᴀɪʟs.</b>"
                ),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
            try:
                await k.pin()
            except Exception:
                pass
            await bot.leave_chat(message.chat.id)
            return

        buttons = [
            [
                InlineKeyboardButton("🛡️ ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
                InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
            ],
            [
                InlineKeyboardButton("⚡ 1-ᴄʟɪᴄᴋ ᴀᴜᴛᴏ-sᴇᴛ", callback_data="autoset|home"),
                InlineKeyboardButton("⚙️ ʟɪᴠᴇ sᴇᴛᴛɪɴɢs", callback_data="hdesk|settings"),
            ],
            [
                InlineKeyboardButton("📢 ᴜᴘᴅᴀᴛᴇs", url=CHNL_LNK),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        await message.reply_text(
            text=(
                f"<b>💖 ᴛʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ᴀᴅᴅɪɴɢ ᴍᴇ ᴛᴏ {html.escape(message.chat.title or 'ᴛʜɪs ɢʀᴏᴜᴘ')}! ✨\n\n"
                "• 🛡️ ᴘʀᴏᴍᴏᴛᴇ ᴍᴇ ᴀs ᴀᴅᴍɪɴ ᴀɴᴅ ᴜsᴇ <code>/helpdesk</code> ᴏʀ <code>/autoset</code> ғᴏʀ 70+ ᴘʀᴏ ɢʀᴏᴜᴘ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ, 16 ʟᴏᴄᴋs, ɴᴏᴛᴇs &amp; ғɪʟᴛᴇʀs.\n"
                "• 📢 ᴜsᴇ <code>/channelhelp</code>, <code>/connect</code>, ᴏʀ <code>/autoforward</code> ᴛᴏ ᴍᴀɴᴀɢᴇ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟs!</b>"
            ),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("leave", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def leave_a_chat(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴄʜᴀᴛ ɪᴅ:</b> <code>/leave -100xxxx</code>", parse_mode=enums.ParseMode.HTML)

    chat = message.command[1]
    try:
        chat = int(chat)
    except ValueError:
        pass

    try:
        buttons = [[InlineKeyboardButton("🆘 sᴜᴘᴘᴏʀᴛ", url=f"https://t.me/{SUPPORT_CHAT}")]]
        reply_markup = InlineKeyboardMarkup(buttons)

        try:
            await bot.send_message(
                chat_id=chat,
                text=(
                    "<b>👋 ʜᴇʟʟᴏ ғʀɪᴇɴᴅs!\n\n"
                    "ᴍʏ ᴀᴅᴍɪɴ ʀᴇǫᴜᴇsᴛᴇᴅ ᴍᴇ ᴛᴏ ʟᴇᴀᴠᴇ ᴛʜɪs ᴄʜᴀᴛ.\n"
                    "ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ ᴛᴏ ᴀᴅᴅ ᴍᴇ ᴀɢᴀɪɴ.</b>"
                ),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        await bot.leave_chat(chat)
        logger.info("👋 ʙᴏᴛ ʟᴇғᴛ ᴄʜᴀᴛ › ᴄʜᴀᴛ=%s ʙʏ_ᴀᴅᴍɪɴ=%s", chat, message.from_user.id)
        await message.reply(f"✅ <b>sᴜᴄᴄᴇssғᴜʟʟʏ ʟᴇғᴛ ᴄʜᴀᴛ:</b> <code>{chat}</code>", parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        logger.error("❌ /leave ғᴀɪʟᴇᴅ › ᴄʜᴀᴛ=%s ᴇʀʀ=%s", chat, e)
        await message.reply(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("disable", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def disable_chat(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴄʜᴀᴛ ɪᴅ:</b> <code>/disable -100xxxx [reason]</code>", parse_mode=enums.ParseMode.HTML)

    r = message.text.split(None, 2)
    chat = r[1]
    reason = r[2] if len(r) > 2 else "ɴᴏ ʀᴇᴀsᴏɴ ᴘʀᴏᴠɪᴅᴇᴅ"

    try:
        chat_ = int(chat)
    except ValueError:
        return await message.reply("❌ <b>ɪɴᴠᴀʟɪᴅ ᴄʜᴀᴛ ɪᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    cha_t = await db.get_chat(chat_)
    if not cha_t:
        return await message.reply("❌ <b>ᴄʜᴀᴛ ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.</b>", parse_mode=enums.ParseMode.HTML)

    if cha_t.get("is_disabled"):
        return await message.reply(
            f"⚠️ <b>ᴄʜᴀᴛ ᴀʟʀᴇᴀᴅʏ ᴅɪsᴀʙʟᴇᴅ.</b>\n"
            f"📌 <b>ʀᴇᴀsᴏɴ:</b> <code>{reason}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.disable_chat(chat_, reason)
    if chat_ not in temp.BANNED_CHATS:
        temp.BANNED_CHATS.append(chat_)

    logger.info("🚫 ᴄʜᴀᴛ ᴅɪsᴀʙʟᴇᴅ › ᴄʜᴀᴛ=%s ʀᴇᴀsᴏɴ=%s", chat_, reason)
    await message.reply(f"✅ <b>ᴄʜᴀᴛ <code>{chat_}</code> ᴅɪsᴀʙʟᴇᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    try:
        buttons = [[InlineKeyboardButton("🆘 sᴜᴘᴘᴏʀᴛ", url=f"https://t.me/{SUPPORT_CHAT}")]]
        await bot.send_message(
            chat_id=chat_,
            text=(
                "<b>🚫 ᴛʜɪs ʙᴏᴛ ʜᴀs ʙᴇᴇɴ ᴅɪsᴀʙʟᴇᴅ ɪɴ ᴛʜɪs ᴄʜᴀᴛ.\n\n"
                f"📌 ʀᴇᴀsᴏɴ: <code>{reason}</code></b>"
            ),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
        )
        await bot.leave_chat(chat_)
    except Exception:
        pass


@Client.on_message(filters.command("enable", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def re_enable_chat(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴄʜᴀᴛ ɪᴅ:</b> <code>/enable -100xxxx</code>", parse_mode=enums.ParseMode.HTML)

    try:
        chat_ = int(message.command[1])
    except ValueError:
        return await message.reply("❌ <b>ɪɴᴠᴀʟɪᴅ ᴄʜᴀᴛ ɪᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    sts = await db.get_chat(chat_)
    if not sts:
        return await message.reply("❌ <b>ᴄʜᴀᴛ ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.</b>", parse_mode=enums.ParseMode.HTML)

    if not sts.get("is_disabled"):
        return await message.reply("ℹ️ <b>ᴄʜᴀᴛ ɪs ɴᴏᴛ ᴅɪsᴀʙʟᴇᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    await db.re_enable_chat(chat_)
    if chat_ in temp.BANNED_CHATS:
        temp.BANNED_CHATS.remove(chat_)

    logger.info("🟢 ᴄʜᴀᴛ ʀᴇ-ᴇɴᴀʙʟᴇᴅ › ᴄʜᴀᴛ=%s", chat_)
    await message.reply(f"✅ <b>ᴄʜᴀᴛ <code>{chat_}</code> ʀᴇ-ᴇɴᴀʙʟᴇᴅ.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("invite", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def gen_invite(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴄʜᴀᴛ ɪᴅ:</b> <code>/invite -100xxxx</code>", parse_mode=enums.ParseMode.HTML)

    try:
        chat = int(message.command[1])
    except ValueError:
        return await message.reply("❌ <b>ɪɴᴠᴀʟɪᴅ ᴄʜᴀᴛ ɪᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    try:
        link = await bot.create_chat_invite_link(chat)
    except ChatAdminRequired:
        return await message.reply("❌ <b>ɪɴᴠɪᴛᴇ ʟɪɴᴋ ғᴀɪʟᴇᴅ: ɪɴsᴜғғɪᴄɪᴇɴᴛ ᴀᴅᴍɪɴ ʀɪɢʜᴛs.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        return await message.reply(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    logger.info("🔗 ɪɴᴠɪᴛᴇ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ › ᴄʜᴀᴛ=%s", chat)
    await message.reply(f"🔗 <b>ɪɴᴠɪᴛᴇ ʟɪɴᴋ:</b>\n{link.invite_link}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["botban", "gban"], prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def ban_a_user(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴜsᴇʀ ɪᴅ ᴏʀ ᴜsᴇʀɴᴀᴍᴇ:</b> <code>/botban @user [reason]</code>", parse_mode=enums.ParseMode.HTML)

    r = message.text.split(None, 2)
    chat = r[1]
    reason = r[2] if len(r) > 2 else "ɴᴏ ʀᴇᴀsᴏɴ ᴘʀᴏᴠɪᴅᴇᴅ"

    try:
        chat = int(chat)
    except ValueError:
        pass

    try:
        k = await bot.get_users(chat)
    except PeerIdInvalid:
        return await message.reply("❌ <b>ɪɴᴠᴀʟɪᴅ ᴜsᴇʀ.</b>", parse_mode=enums.ParseMode.HTML)
    except IndexError:
        return await message.reply("❌ <b>ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴜsᴇʀ, ɴᴏᴛ ᴀ ᴄʜᴀɴɴᴇʟ.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        return await message.reply(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    jar = await db.get_ban_status(k.id)
    if jar.get("is_banned"):
        return await message.reply(
            f"⚠️ {k.mention} <b>ɪs ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴛʜᴇ ʙᴏᴛ.</b>\n"
            f"📌 <b>ʀᴇᴀsᴏɴ:</b> <code>{jar.get('ban_reason')}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.ban_user(k.id, reason)
    if k.id not in temp.BANNED_USERS:
        temp.BANNED_USERS.append(k.id)
    USER_CACHE.invalidate_user(k.id)

    await message.reply(f"✅ {k.mention} (<code>{k.id}</code>) <b>ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜᴇ ʙᴏᴛ.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["botunban", "ungban"], prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def unban_a_user(bot: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply("⚠️ <b>ᴘʀᴏᴠɪᴅᴇ ᴀ ᴜsᴇʀ ɪᴅ ᴏʀ ᴜsᴇʀɴᴀᴍᴇ:</b> <code>/botunban @user</code>", parse_mode=enums.ParseMode.HTML)

    try:
        chat = int(message.command[1])
    except ValueError:
        chat = message.command[1]

    try:
        k = await bot.get_users(chat)
    except PeerIdInvalid:
        return await message.reply("❌ <b>ɪɴᴠᴀʟɪᴅ ᴜsᴇʀ.</b>", parse_mode=enums.ParseMode.HTML)
    except IndexError:
        return await message.reply("❌ <b>ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴜsᴇʀ, ɴᴏᴛ ᴀ ᴄʜᴀɴɴᴇʟ.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        return await message.reply(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    jar = await db.get_ban_status(k.id)
    if not jar.get("is_banned"):
        return await message.reply(f"ℹ️ {k.mention} <b>ɪs ɴᴏᴛ ʙᴀɴɴᴇᴅ.</b>", parse_mode=enums.ParseMode.HTML)

    await db.remove_ban(k.id)
    if k.id in temp.BANNED_USERS:
        temp.BANNED_USERS.remove(k.id)
    USER_CACHE.invalidate_user(k.id)

    await message.reply(f"✅ {k.mention} (<code>{k.id}</code>) <b>ᴜɴʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴛʜᴇ ʙᴏᴛ.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("users", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def list_users(bot: Client, message: Message):
    raju = await message.reply("⏳ <b>ɢᴇᴛᴛɪɴɢ ᴜsᴇʀ ʟɪsᴛ...</b>", parse_mode=enums.ParseMode.HTML)

    users = await db.get_all_users()
    out = f"<b>👥 ᴜsᴇʀs sᴀᴠᴇᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ ({len(users)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"

    for user in users[:60]:
        name = html.escape(str(user.get("name", "User")))
        out += f"• <a href='tg://user?id={user['id']}'>{name}</a> (<code>{user['id']}</code>)"
        if user.get("ban_status", {}).get("is_banned"):
            out += " 🚫 <b>[ʙᴀɴɴᴇᴅ]</b>"
        out += "\n"

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ᴜsᴇʀ ʙʀᴏᴡsᴇʀ", callback_data="admin_users_page_1"),
            InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ᴜsᴇʀs", callback_data="adm_panel#export_users"),
        ]
    ])

    try:
        await raju.edit_text(out, reply_markup=btn, parse_mode=enums.ParseMode.HTML)
    except MessageTooLong:
        with open("users.txt", "w", encoding="utf-8") as outfile:
            outfile.write(out)
        await message.reply_document("users.txt", caption=f"👥 <b>ᴜsᴇʀ ʟɪsᴛ ({len(users)})</b>", reply_markup=btn, parse_mode=enums.ParseMode.HTML)
        await raju.delete()
        os.remove("users.txt")


@Client.on_message(filters.command(["chats", "channels"], prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def list_chats(bot: Client, message: Message):
    raju = await message.reply("⏳ <b>ɢᴇᴛᴛɪɴɢ ᴄʜᴀᴛ &amp; ᴄʜᴀɴɴᴇʟ ʟɪsᴛ...</b>", parse_mode=enums.ParseMode.HTML)

    chats = await db.get_all_chats()
    channels = await db.get_all_channels()
    out = f"<b>💬 ɢʀᴏᴜᴘs ({len(chats)}) &amp; 📢 ᴄʜᴀɴɴᴇʟs ({len(channels)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if chats:
        out += "<b>💬 ʀᴇɢɪsᴛᴇʀᴇᴅ ɢʀᴏᴜᴘs:</b>\n"
        for chat in chats:
            title = html.escape(str(chat.get("title", "ᴜɴᴋɴᴏᴡɴ")))
            out += f"• <b>{title}</b> — <code>{chat['id']}</code>"
            if chat.get("chat_status", {}).get("is_disabled"):
                out += " (🚫 ᴅɪsᴀʙʟᴇᴅ)"
            out += "\n"
        out += "\n"

    if channels:
        out += "<b>📢 ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs:</b>\n"
        for ch in channels:
            title = html.escape(str(ch.get("title", "ᴄʜᴀɴɴᴇʟ")))
            out += f"• <b>{title}</b> — <code>{ch.get('chat_id')}</code>\n"

    try:
        await raju.edit_text(out, parse_mode=enums.ParseMode.HTML)
    except MessageTooLong:
        with open("chats.txt", "w", encoding="utf-8") as outfile:
            outfile.write(out)
        await message.reply_document("chats.txt", caption="💬 <b>ᴄʜᴀᴛ &amp; ᴄʜᴀɴɴᴇʟ ʟɪsᴛ</b>", parse_mode=enums.ParseMode.HTML)
        await raju.delete()
        os.remove("chats.txt")
