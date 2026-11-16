import html
import logging
from pyrogram import Client, enums, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.users_chats_db import db
from info import ADMINS, CHNL_LNK, COMMAND_PREFIXES, GRP_LNK, LOG_CHANNEL
from Script import script
from utils import format_watermark_html, is_admin_sync, temp
from plugins.core_db import user_repo, chat_repo, file_repo
from plugins.core_utils import to_smallcaps, format_watermark, logger


def build_home_buttons(user_id: int = 0) -> InlineKeyboardMarkup:
    is_adm = is_admin_sync(user_id)
    rows = [
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
        ],
        [
            InlineKeyboardButton(f"🗂️ {to_smallcaps('my files vault')}", callback_data="pvt_vault#page_0"),
            InlineKeyboardButton(f"🎛️ {to_smallcaps('presets & tasks')}", callback_data="pvt_dash#home"),
        ],
        [
            InlineKeyboardButton(f"📖 {to_smallcaps('help & media tools')}", callback_data="nav_help"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('settings & auto')}", callback_data="nav_settings"),
        ],
        [
            InlineKeyboardButton(f"🎨 {to_smallcaps('post & feature studio')}", callback_data="postedit|home"),
            InlineKeyboardButton(f"📝 {to_smallcaps('notes & filters')}", callback_data="hdesk|notes_help"),
        ],
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group editor')}", callback_data="gfeat|open"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel editor')}", callback_data="cfeat|open"),
        ],
        [
            InlineKeyboardButton(f"🌐 {to_smallcaps('24/7 uptime')}", callback_data="up_list#0"),
            InlineKeyboardButton(f"📊 {to_smallcaps('system status')}", callback_data="nav_status"),
        ],
        [
            InlineKeyboardButton(f"ℹ️ {to_smallcaps('about bot')}", callback_data="nav_about"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]
    if is_adm:
        rows.append([
            InlineKeyboardButton(f"👑 {to_smallcaps('master admin panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"🌐 {to_smallcaps('uptime admin')}", callback_data="upad_home"),
        ])
    rows.append([
        InlineKeyboardButton(f"🎬 {to_smallcaps('support group')}", url=GRP_LNK),
        InlineKeyboardButton(f"📢 {to_smallcaps('updates channel')}", url=Config.WATERMARK_URL),
    ])

    return InlineKeyboardMarkup(rows)


def build_home_text(mention: str) -> str:
    return (
        f"✦ <b>{to_smallcaps('mmw all-in-one pro manager')}</b> ✦ ⚡\n\n"
        f"👋 <b>{to_smallcaps('hello')} {mention}</b> !\n\n"
        f"🚀 <b>{to_smallcaps('welcome to your unified group, channel, media, stream & ai pro bot!')}</b>\n\n"
        f"💎 <b>{to_smallcaps('all auto & pro features')}</b> :\n"
        f"• ⚡ <b>{to_smallcaps('auto-set wizard')}</b> : {to_smallcaps('1-click setup for user, group & channel')} (<code>/autoset</code>)\n"
        f"• 📝 <b>{to_smallcaps('auto caption')}</b> : {to_smallcaps('dynamic templates ({filename}, {filesize}, cap[])')}\n"
        f"• ✏️ <b>{to_smallcaps('auto rename')}</b> : {to_smallcaps('auto & manual rename with prefix, suffix & thumb')}\n"
        f"• 🔁 <b>{to_smallcaps('auto forward')}</b> : {to_smallcaps('tag-free live channel/group cloner & batch copy')} (<code>/autoforward</code>)\n"
        f"• 🎬 <b>{to_smallcaps('auto & file stream')}</b> : {to_smallcaps('http 206 byte-range + 4 external players (playit, mx, vlc, s player)')}\n"
        f"• 🛡️ <b>{to_smallcaps('group helpdesk')}</b> : {to_smallcaps('70+ admin, 16 locks, antiflood, raid, notes, filters & afk')} (<code>/helpdesk</code>)\n"
        f"• 📢 <b>{to_smallcaps('channel helper')}</b> : {to_smallcaps('35+ channel post, schedule, template & admin tools')} (<code>/channelhelp</code>)\n"
        f"• 🗜️ <b>{to_smallcaps('compressor & file pro')}</b> : {to_smallcaps('ffmpeg h.264 presets, trim, mux/extract audio & subs')}\n"
        f"• 🌐 <b>{to_smallcaps('24/7 uptime & multi-ai')}</b> : {to_smallcaps('live web monitoring')} (<code>/uptime</code>) &amp; {to_smallcaps('ai')} (<code>/ask</code>)\n\n"
        f"💡 <b>{to_smallcaps('quick start')}</b> : {to_smallcaps('send/forward any file or tap a module below!')}"
        f"{format_watermark_html()}"
    )


@Client.on_message(
    filters.command(
        ["start", "help", "helps", "modules", "commands", "guide", "about", "status", "settings"],
        prefixes=COMMAND_PREFIXES,
    )
    & filters.incoming
)
async def command_start_family(client: Client, message: Message):
    user = message.from_user
    uid = user.id if user else 0
    cmd = message.command[0].lower() if message.command else "start"
    logger.info("🚀 /%s ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s ᴛʏᴘᴇ=%s", cmd, uid, message.chat.id, message.chat.type)

    if uid and await user_repo.is_banned(uid):
        return await message.reply_text(
            f"🚫 <b>{to_smallcaps('you are banned from using this bot.')}</b>\n\n"
            f"⚠️ <b>{to_smallcaps('reason')}</b> : <code>{to_smallcaps('violation of terms or admin restriction')}</code>"
            f"{format_watermark_html()}",
            parse_mode=enums.ParseMode.HTML,
        )

    # Group registration & handling
    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        if not await db.get_chat(message.chat.id):
            total = 0
            try:
                total = await client.get_chat_members_count(message.chat.id)
            except Exception:
                pass
            if LOG_CHANNEL and temp.BOT_CONFIG.get("log_new_groups", True):
                try:
                    adder = user.mention if user else "ᴜɴᴋɴᴏᴡɴ"
                    await client.send_message(
                        LOG_CHANNEL,
                        script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, adder),
                    )
                except Exception:
                    pass
            await db.add_chat(message.chat.id, message.chat.title or "Group")

        if cmd in ("help", "helps", "modules", "commands", "guide"):
            from plugins.callbacks import render_help_menu
            return await render_help_menu(message, user, is_admin_sync(uid), is_edit=False)

        buttons = [
            [
                InlineKeyboardButton("🛡️ ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ", callback_data="hdesk|home"),
                InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
            ],
            [
                InlineKeyboardButton("⚡ ᴀᴜᴛᴏ-sᴇᴛ ᴡɪᴢᴀʀᴅ", callback_data="autoset|home"),
                InlineKeyboardButton("⚙️ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs", callback_data="hdesk|settings"),
            ],
            [
                InlineKeyboardButton("📖 ʜᴇʟᴘ & ɢᴜɪᴅᴇ", callback_data="nav_help"),
                InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
            ],
        ]
        if is_admin_sync(uid):
            buttons.insert(2, [InlineKeyboardButton("👑 ᴍᴀsᴛᴇʀ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home")])
        mention = user.mention if user else (message.chat.title or "ғʀɪᴇɴᴅ")
        return await message.reply_text(
            script.START_TXT.format(mention, temp.U_NAME or "MMWProBot", temp.B_NAME or "MMW All-In-One Pro"),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # Register private user
    if user:
        is_new = not await db.is_user_exist(uid)
        await user_repo.get_or_create(uid, user.first_name, user.username)
        await db.add_user(uid, user.first_name)
        if is_new and LOG_CHANNEL and temp.BOT_CONFIG.get("log_new_users", True):
            try:
                await client.send_message(
                    LOG_CHANNEL,
                    script.LOG_TEXT_P.format(uid, user.mention),
                )
            except Exception:
                pass

    if Config.FORCE_SUB_CHANNEL and not is_admin_sync(uid):
        try:
            member = await client.get_chat_member(Config.FORCE_SUB_CHANNEL, uid)
            if str(member.status).lower() in ("kicked", "left", "chatmemberstatus.banned", "chatmemberstatus.left"):
                raise Exception("Not subscribed")
        except Exception:
            invite_link = (
                Config.FORCE_SUB_CHANNEL
                if str(Config.FORCE_SUB_CHANNEL).startswith("http")
                else f"https://t.me/{str(Config.FORCE_SUB_CHANNEL).replace('@', '')}"
            )
            btn = [
                [InlineKeyboardButton(f"📢 {to_smallcaps('join official channel')}", url=invite_link)],
                [InlineKeyboardButton(f"🔄 {to_smallcaps('verify & continue')}", callback_data="nav_home")],
            ]
            return await message.reply_text(
                f"⚠️ <b>{to_smallcaps('access requires channel membership')}</b>\n\n"
                f"👋 <b>{to_smallcaps('hello')} {user.mention if user else 'friend'}</b> !\n"
                f"{to_smallcaps('you must join our official updates channel to unlock all bot features.')}\n\n"
                f"⚡ {to_smallcaps('click the button below to join, then click verify!')}"
                f"{format_watermark_html()}",
                reply_markup=InlineKeyboardMarkup(btn),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )

    # Handle /start deep-link payloads
    if cmd == "start" and len(message.command) > 1:
        payload = message.command[1].strip()
        low_arg = payload.lower()

        if low_arg in ("help", "guide", "modules"):
            from plugins.callbacks import render_help_menu
            return await render_help_menu(message, user, is_admin_sync(uid), is_edit=False)
        if low_arg in ("helpdesk", "grouphelp"):
            from plugins.help_desk_features import build_helpdesk_menu_view
            text, markup = build_helpdesk_menu_view("home", {})
            return await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        if low_arg in ("channelhelp", "chhelp", "cpanel"):
            from plugins.channel_manager import build_channel_help_view
            text, markup = await build_channel_help_view(client, uid, "home")
            return await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        if low_arg in ("autoset", "autosetup"):
            from plugins.autoset import build_autoset_view
            text, markup = await build_autoset_view(client, uid, message.chat.id, message.chat.type)
            return await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        if low_arg in ("autoforward", "forwards"):
            from plugins.autoforward import build_autoforward_panel
            text, markup = await build_autoforward_panel(uid)
            return await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        if low_arg == "about":
            return await message.reply_text(
                script.ABOUT_TXT,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="nav_home")]]),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )

        file_id = payload
        if payload.startswith("file_"):
            file_id = payload[5:]
        elif payload.startswith("getfile_"):
            file_id = payload[8:]
        elif payload.startswith("get_"):
            file_id = payload[4:]

        file_doc = await file_repo.get_file(file_id)
        if file_doc:
            try:
                msg = await client.get_messages(file_doc["chat_id"], file_doc["message_id"])
                if msg and msg.media:
                    await msg.copy(chat_id=message.chat.id)
                    return
                return await message.reply_text(
                    f"❌ <b>{to_smallcaps('original media no longer exists in storage channel!')}</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as e:
                logger.error(f"Error copying media file for start payload {file_id}: {e}")
                return await message.reply_text(
                    f"❌ <b>{to_smallcaps('failed to send file')}</b> : <code>{html.escape(str(e))}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )
        else:
            return await message.reply_text(
                f"❌ <b>{to_smallcaps('file not found or stream link has been revoked!')}</b>"
                f"{format_watermark_html()}",
                parse_mode=enums.ParseMode.HTML,
            )

    is_adm = is_admin_sync(uid)

    if cmd == "start":
        mention = user.mention if user else "ғʀɪᴇɴᴅ"
        await message.reply_text(
            text=build_home_text(mention),
            reply_markup=build_home_buttons(uid),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    elif cmd in ("help", "helps", "modules", "commands", "guide"):
        from plugins.callbacks import render_help_menu
        await render_help_menu(message, user, is_adm, is_edit=False)

    elif cmd == "settings":
        from plugins.callbacks import render_settings_menu
        await render_settings_menu(message, user, is_edit=False)

    elif cmd in ("about", "status"):
        users_count = await db.total_users_count()
        chats_count = await db.total_chat_count()
        chnl_count = await db.total_channels_count()
        files_count = await file_repo.get_total_files()
        fwd_rules = len(await db.get_all_autoforward_rules())
        text = (
            f"✦ <b>{to_smallcaps('system & bot live status')}</b> ✦ ⚡\n\n"
            f"• 🤖 <b>{to_smallcaps('bot name')}</b> : <code>MMW All-In-One Pro</code>\n"
            f"• ⚡ <b>{to_smallcaps('stream mode')}</b> : <code>turbo HTTP 206 byte-range</code>\n"
            f"• 📱 <b>{to_smallcaps('4 external players')}</b> : <code>PLAYit • MX • VLC • S Player</code>\n"
            f"• 👥 <b>{to_smallcaps('registered users')}</b> : <code>{users_count}</code>\n"
            f"• 💬 <b>{to_smallcaps('active groups')}</b> : <code>{chats_count}</code>\n"
            f"• 📢 <b>{to_smallcaps('managed channels')}</b> : <code>{chnl_count}</code>\n"
            f"• 📁 <b>{to_smallcaps('indexed stream files')}</b> : <code>{files_count}</code>\n"
            f"• 🔁 <b>{to_smallcaps('auto-forward routes')}</b> : <code>{fwd_rules}</code>\n"
            f"• 🍃 <b>{to_smallcaps('database & cache')}</b> : <code>Unified MongoDB + L1/L2 FastCache</code>\n"
            f"• 🚀 <b>{to_smallcaps('server health')}</b> : <code>Operational &amp; Ultra-Fast</code>"
            f"{format_watermark_html()}"
        )
        buttons = [
            [
                InlineKeyboardButton(f"🔄 {to_smallcaps('refresh')}", callback_data="nav_status"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
            ],
            [
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ],
        ]
        await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )


@Client.on_message(filters.command("send", prefixes=COMMAND_PREFIXES) & filters.user(ADMINS))
async def send_msg_to_user(bot: Client, message: Message):
    """Admin command to reply to any message and copy it directly to a target user ID."""
    if not message.reply_to_message:
        return await message.reply_text(
            "<b>⚠️ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/send &lt;user_id&gt;</code>!</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>⚠️ ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴛᴀʀɢᴇᴛ ᴜsᴇʀ ɪᴅ: <code>/send 123456789</code></b>",
            parse_mode=enums.ParseMode.HTML,
        )
    target_uid = message.command[1].strip()
    try:
        uid = int(target_uid)
        user = await bot.get_users(uid)
        await message.reply_to_message.copy(chat_id=user.id)
        logger.info("📨 /send ᴅᴇʟɪᴠᴇʀᴇᴅ › ᴀᴅᴍɪɴ=%s ᴛᴀʀɢᴇᴛ=%s", message.from_user.id, user.id)
        await message.reply_text(
            f"✅ <b>ᴍᴇssᴀɢᴇ sᴇɴᴛ ᴛᴏ {user.mention} (<code>{user.id}</code>)!</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error("❌ /send ғᴀɪʟᴇᴅ › ᴛᴀʀɢᴇᴛ=%s ᴇʀʀ=%s", target_uid, e)
        await message.reply_text(f"❌ <b>ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)
