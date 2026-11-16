import html
from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.users_chats_db import db
from Script import script
from utils import format_watermark_html, is_admin_sync, safe_edit_message
from plugins.core_db import user_repo, chat_repo, file_repo
from plugins.core_utils import to_smallcaps
from plugins.start import build_home_buttons, build_home_text


async def render_help_menu(target_msg: Message, user, is_admin: bool, is_edit: bool = True):
    mention = user.mention if user else "ғʀɪᴇɴᴅ"
    help_text = (
        f"✦ <b>{to_smallcaps('all-in-one smart help center')}</b> ✦ ⚡\n\n"
        f"👋 <b>{to_smallcaps('hello')} {mention}</b> !\n"
        f"{to_smallcaps('explore our categorized guides below for all auto, media, group, channel, ai & uptime features:')}\n\n"
        f"• ⚡ <b>{to_smallcaps('auto-set wizard')}</b> : {to_smallcaps('1-click setup for user, group & channel')} (<code>/autoset</code>)\n"
        f"• 📝 <b>{to_smallcaps('auto captions')}</b> : {to_smallcaps('dynamic templates & cap[] queue manager')}\n"
        f"• ✏️ <b>{to_smallcaps('auto & pro renamer')}</b> : {to_smallcaps('rename, auto-rename, prefix & suffix')}\n"
        f"• 🔁 <b>{to_smallcaps('auto forward & batch')}</b> : {to_smallcaps('live channel/group cloner & batch copy')} (<code>/autoforward</code>)\n"
        f"• 🎬 <b>{to_smallcaps('auto & file streamer')}</b> : {to_smallcaps('4 external players (playit, mx, vlc, s player)')}\n"
        f"• 🛡️ <b>{to_smallcaps('group helpdesk')}</b> : {to_smallcaps('70+ moderation, 16 locks, antiflood, notes & filters')}\n"
        f"• 📢 <b>{to_smallcaps('channel helper')}</b> : {to_smallcaps('35+ channel post, schedule, template & admin tools')}\n"
        f"• 🗜️ <b>{to_smallcaps('compressor & file pro')}</b> : {to_smallcaps('h.264 presets, trim, extract/add audio & subs')}\n"
        f"• 🤝 <b>{to_smallcaps('auto accept & thumb')}</b> : {to_smallcaps('auto-approve joins & custom thumbnail caching')}\n"
        f"• 🌐 <b>{to_smallcaps('24/7 uptime & ai')}</b> : {to_smallcaps('website/bot monitor')} (<code>/uptime</code>) &amp; {to_smallcaps('multi-model ai')} (<code>/ask</code>)\n"
    )
    if is_admin:
        help_text += f"• 👑 <b>{to_smallcaps('master admin')}</b> : {to_smallcaps('live toggles, broadcast, user/chat manager & logs')}\n"
    help_text += format_watermark_html()

    buttons = [
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
        ],
        [
            InlineKeyboardButton(f"🎨 {to_smallcaps('post & button studio')}", callback_data="postedit|home"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('group/chnl editor')}", callback_data="gfeat|open"),
        ],
        [
            InlineKeyboardButton(f"✏️ {to_smallcaps('renamer')}", callback_data="help_renamer"),
            InlineKeyboardButton(f"📝 {to_smallcaps('captions')}", callback_data="help_caption"),
        ],
        [
            InlineKeyboardButton(f"🎬 {to_smallcaps('file stream')}", callback_data="help_streamer"),
            InlineKeyboardButton(f"🗜️ {to_smallcaps('compressor')}", callback_data="help_compressor"),
        ],
        [
            InlineKeyboardButton(f"🎛️ {to_smallcaps('file pro')}", callback_data="help_filepro"),
            InlineKeyboardButton(f"🖼️ {to_smallcaps('thumbnail')}", callback_data="help_thumbnail"),
        ],
        [
            InlineKeyboardButton(f"🤝 {to_smallcaps('auto accept')}", callback_data="help_accept"),
            InlineKeyboardButton(f"⏱️ {to_smallcaps('auto-delete')}", callback_data="help_autodelete"),
        ],
        [
            InlineKeyboardButton(f"📝 {to_smallcaps('notes & filters')}", callback_data="hdesk|notes_help"),
            InlineKeyboardButton(f"🤖 {to_smallcaps('ai & uptime')}", callback_data="hdesk|ai_help"),
        ],
        [
            InlineKeyboardButton(f"🔑 {to_smallcaps('session')}", callback_data="help_session"),
            InlineKeyboardButton(f"🛠️ {to_smallcaps('tools & cleaner')}", callback_data="help_tools"),
        ],
    ]
    if is_admin:
        buttons.append([
            InlineKeyboardButton(f"👑 {to_smallcaps('master admin panel')}", callback_data="adm_panel#home"),
            InlineKeyboardButton(f"📖 {to_smallcaps('admin guide')}", callback_data="help_admin"),
        ])
    buttons.append([
        InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
        InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
    ])

    if is_edit:
        await safe_edit_message(target_msg, help_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def render_settings_menu(target_msg: Message, user, is_edit: bool = True):
    uid = user.id if user else 0
    fname = user.first_name if user else "User"
    uname = user.username if user else None
    mention = user.mention if user else "ғʀɪᴇɴᴅ"

    u = await user_repo.get_or_create(uid, fname, uname)
    has_thumb = "✅" if u.get("thumb_id") else "🌐 ᴛʜᴀᴍ_ᴜʀʟ"
    has_caption = "✅" if (u.get("custom_caption") or u.get("captions_list")) else "📄 ᴀᴜᴛᴏ"
    timer = u.get("auto_delete_time", 0)
    timer_str = f"{timer}s" if timer > 0 else to_smallcaps("disabled")
    prefix_str = html.escape(str(u.get("prefix") or to_smallcaps("none")))
    suffix_str = html.escape(str(u.get("suffix") or to_smallcaps("none")))
    auto_ren = "🟢 ᴏɴ" if u.get("auto_rename") else "⚪ ᴏғғ"
    auto_comp = "🟢 ᴏɴ" if u.get("auto_compress") else "⚪ ᴏғғ"
    auto_str = "🟢 ᴏɴ" if u.get("auto_stream", True) else "⚪ ᴏғғ"
    auto_cap = "🟢 ᴏɴ" if u.get("auto_caption", True) else "⚪ ᴏғғ"
    up_mode = "🎬 ᴠɪᴅᴇᴏ" if u.get("upload_mode", "video") == "video" else "📁 ᴅᴏᴄ"

    settings_text = (
        f"✦ <b>{to_smallcaps('smart settings & auto dashboard')}</b> ✦ ⚙️\n\n"
        f"👤 <b>{to_smallcaps('user')}</b> : {mention} (<code>{uid}</code>)\n\n"
        f"• 📝 <b>{to_smallcaps('auto caption')}</b> : <code>{auto_cap}</code>\n"
        f"• 🤖 <b>{to_smallcaps('auto rename')}</b> : <code>{auto_ren}</code>\n"
        f"• ⚡ <b>{to_smallcaps('auto stream (4 players)')}</b> : <code>{auto_str}</code>\n"
        f"• 🗜️ <b>{to_smallcaps('auto compress')}</b> : <code>{auto_comp}</code>\n"
        f"• 📤 <b>{to_smallcaps('upload mode')}</b> : <code>{up_mode}</code>\n"
        f"• 🖼️ <b>{to_smallcaps('custom thumbnail')}</b> : <code>{has_thumb}</code>\n"
        f"• 🏷️ <b>{to_smallcaps('caption template')}</b> : <code>{has_caption}</code>\n"
        f"• 🏷️ <b>{to_smallcaps('file prefix')}</b> : <code>{prefix_str}</code>\n"
        f"• 🏷️ <b>{to_smallcaps('file suffix')}</b> : <code>{suffix_str}</code>\n"
        f"• ⏱️ <b>{to_smallcaps('auto-delete timer')}</b> : <code>{timer_str}</code>\n\n"
        f"💡 <b>{to_smallcaps('click any button below to toggle or configure:')}</b>"
        f"{format_watermark_html()}"
    )
    buttons = [
        [
            InlineKeyboardButton(f"📝 {to_smallcaps('auto caption')}: {auto_cap}", callback_data="settings_toggle_autocap"),
            InlineKeyboardButton(f"🤖 {to_smallcaps('auto rename')}: {auto_ren}", callback_data="settings_toggle_autoren"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto stream')}: {auto_str}", callback_data="settings_toggle_autostream"),
            InlineKeyboardButton(f"🗜️ {to_smallcaps('auto compress')}: {auto_comp}", callback_data="settings_toggle_autocomp"),
        ],
        [
            InlineKeyboardButton(f"📤 {to_smallcaps('mode')}: {up_mode}", callback_data="settings_toggle_upmode"),
            InlineKeyboardButton(f"⏱️ {to_smallcaps('timer settings')}", callback_data="open_timer_menu"),
        ],
        [
            InlineKeyboardButton(f"🗂️ {to_smallcaps('my files vault')}", callback_data="pvt_vault#page_0"),
            InlineKeyboardButton(f"🎛️ {to_smallcaps('presets & tasks')}", callback_data="pvt_dash#home"),
        ],
        [
            InlineKeyboardButton(f"🖼️ {to_smallcaps('view thumb')}", callback_data="settings_view_thumb"),
            InlineKeyboardButton(f"📝 {to_smallcaps('view caption')}", callback_data="settings_view_caption"),
        ],
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
        ],
        [
            InlineKeyboardButton(f"🎨 {to_smallcaps('post & button studio')}", callback_data="postedit|home"),
            InlineKeyboardButton(f"⚙️ {to_smallcaps('group/chnl editor')}", callback_data="gfeat|open"),
        ],
        [
            InlineKeyboardButton(f"🔄 {to_smallcaps('reset all')}", callback_data="settings_reset_all"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
        ],
        [
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]
    if is_edit:
        await safe_edit_message(target_msg, settings_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await target_msg.reply_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )


@Client.on_callback_query(filters.regex(r"^(nav_(home|help|about|status|settings)|close_data|start|rules|about)$"))
async def navigation_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    user = query.from_user
    uid = user.id if user else 0
    is_adm = is_admin_sync(uid)

    if data == "close_data":
        await query.answer("🗑️ ᴄʟᴏsᴇᴅ")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data in ("nav_home", "start"):
        await query.answer("🏠 ʜᴏᴍᴇ")
        mention = user.mention if user else "ғʀɪᴇɴᴅ"
        await safe_edit_message(
            query.message,
            text=build_home_text(mention),
            reply_markup=build_home_buttons(uid),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if data in ("nav_help", "rules"):
        await query.answer("📖 ʜᴇʟᴘ ᴄᴇɴᴛᴇʀ")
        await render_help_menu(query.message, user, is_adm, is_edit=True)
        return

    if data in ("nav_about", "about"):
        await query.answer("ℹ️ ᴀʙᴏᴜᴛ ʙᴏᴛ")
        buttons = [
            [
                InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
                InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
            ],
            [
                InlineKeyboardButton(f"📖 {to_smallcaps('help guide')}", callback_data="nav_help"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
            ],
            [
                InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
            ],
        ]
        await safe_edit_message(
            query.message,
            text=script.ABOUT_TXT,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if data == "nav_status":
        await query.answer("📊 sʏsᴛᴇᴍ sᴛᴀᴛᴜs")
        users_count = await db.total_users_count()
        chats_count = await db.total_chat_count()
        chnl_count = await db.total_channels_count()
        files_count = await file_repo.get_total_files()
        fwd_rules = len(await db.get_all_autoforward_rules())
        status_text = (
            f"✦ <b>{to_smallcaps('system & bot live status')}</b> ✦ ⚡\n\n"
            f"• 👥 <b>{to_smallcaps('registered users')}</b> : <code>{users_count}</code>\n"
            f"• 💬 <b>{to_smallcaps('active groups')}</b> : <code>{chats_count}</code>\n"
            f"• 📢 <b>{to_smallcaps('managed channels')}</b> : <code>{chnl_count}</code>\n"
            f"• 📁 <b>{to_smallcaps('indexed stream files')}</b> : <code>{files_count}</code>\n"
            f"• 🔁 <b>{to_smallcaps('auto-forward routes')}</b> : <code>{fwd_rules}</code>\n"
            f"• ⚡ <b>{to_smallcaps('stream mode')}</b> : <code>turbo HTTP 206 byte-range</code>\n"
            f"• 📱 <b>{to_smallcaps('4 external players')}</b> : <code>PLAYit • MX • VLC • S Player</code>\n"
            f"• 🧠 <b>{to_smallcaps('caching')}</b> : <code>O(1) L1/L2 FastMemoryCache Active</code>\n"
            f"• 🚀 <b>{to_smallcaps('server status')}</b> : <code>Online &amp; Fast</code>"
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
        await safe_edit_message(
            query.message,
            text=status_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if data == "nav_settings":
        await query.answer("⚙️ sᴇᴛᴛɪɴɢs")
        await render_settings_menu(query.message, user, is_edit=True)


@Client.on_callback_query(
    filters.regex(r"^help_(renamer|compressor|streamer|thumbnail|caption|autodelete|session|cleaner|admin|tools|filepro|accept)")
)
async def help_subpages(client: Client, query: CallbackQuery):
    cat = query.matches[0].group(1)
    await query.answer(f"📖 {cat}")
    buttons = [
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to help')}", callback_data="nav_help"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ],
        [
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]

    text = ""
    if cat == "renamer":
        text = (
            f"✦ <b>{to_smallcaps('auto & pro renamer — guide')}</b> ✦\n\n"
            f"• ✏️ <b>{to_smallcaps('manual rename')}</b> :\n"
            f"  1. {to_smallcaps('send any file or video and click')} <code>[✏️ {to_smallcaps('rename')}]</code>.\n"
            f"  2. {to_smallcaps('or reply to a file with')} <code>/rename new_name.mp4</code>.\n\n"
            f"• 🤖 <b>{to_smallcaps('auto-rename mode')}</b> :\n"
            f"  • <code>/autorename</code> : {to_smallcaps('toggle automatic renaming on/off.')}\n"
            f"  • <code>/setprefix &lt;text&gt;</code> : {to_smallcaps('set prefix for all files.')}\n"
            f"  • <code>/setsuffix &lt;text&gt;</code> : {to_smallcaps('set suffix before extension.')}\n"
            f"  • <code>/delprefix</code> &amp; <code>/delsuffix</code> : {to_smallcaps('remove saved prefix/suffix.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "compressor":
        text = (
            f"✦ <b>{to_smallcaps('auto & pro compressor — guide')}</b> ✦\n\n"
            f"• 🗜️ <b>{to_smallcaps('manual compression')}</b> :\n"
            f"  • {to_smallcaps('send a video or reply with')} <code>/compress</code> {to_smallcaps('or')} <code>/compress 100MB</code>.\n"
            f"  • {to_smallcaps('choose 720p, 480p, 360p, 1080p, crf 24, crf 20, or target size.')}\n\n"
            f"• 🤖 <b>{to_smallcaps('auto-compress mode')}</b> :\n"
            f"  • <code>/autocompress</code> : {to_smallcaps('toggle automatic 720p hd superfast compression.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "streamer":
        text = (
            f"✦ <b>{to_smallcaps('auto & file streamer (4 players) — guide')}</b> ✦\n\n"
            f"• ⚡ <b>{to_smallcaps('adaptive range stream response')}</b> :\n"
            f"  • <code>/autostream</code> : {to_smallcaps('toggle auto stream link generation on/off.')}\n"
            f"  • <code>/stream</code> {to_smallcaps('or')} <code>/filestream</code> : {to_smallcaps('reply to any file to generate stream link.')}\n"
            f"  • <code>/cautostream</code> : {to_smallcaps('auto-attach 4-player stream buttons in channels.')}\n\n"
            f"• 📱 <b>{to_smallcaps('4 external players supported')}</b> :\n"
            f"  <code>1.</code> ▶️ <b>PLAYit</b> │ <code>2.</code> 🎬 <b>MX Player</b> │ <code>3.</code> 🔊 <b>VLC</b> │ <code>4.</code> 🚀 <b>S Player</b>\n\n"
            f"• 📥 <code>/getfile &lt;id&gt;</code> : {to_smallcaps('retrieve file in telegram.')}\n"
            f"• 🗑️ <code>/revokestream &lt;id&gt;</code> : {to_smallcaps('disable stream link anytime.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "accept":
        text = (
            f"✦ <b>{to_smallcaps('auto-accept join requests — guide')}</b> ✦\n\n"
            f"• 🤝 <b>{to_smallcaps('automatic join approval')}</b> :\n"
            f"  • {to_smallcaps('add the bot as admin to your channel or group with invite users permission.')}\n"
            f"  • {to_smallcaps('the bot automatically accepts all incoming join requests and sends a stylish welcome message!')}\n\n"
            f"• ⚙️ <b>{to_smallcaps('commands')}</b> :\n"
            f"  • <code>/accept</code> {to_smallcaps('or')} <code>/autoapprove</code> : {to_smallcaps('toggle auto-accept on/off.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "thumbnail":
        text = (
            f"✦ <b>{to_smallcaps('smart thumbnail — guide')}</b> ✦\n\n"
            f"• 🖼️ <b>{to_smallcaps('commands')}</b> :\n"
            f"  • {to_smallcaps('send any photo or reply to a photo with')} <code>/setthumb</code>.\n"
            f"  • <code>/setthumb &lt;url&gt;</code> : {to_smallcaps('set direct image url fallback.')}\n"
            f"  • <code>/thumb</code> : {to_smallcaps('view active thumbnail.')}\n"
            f"  • <code>/delthumb</code> : {to_smallcaps('delete custom thumbnail.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "caption":
        text = (
            f"✦ <b>{to_smallcaps('auto dynamic captions — guide')}</b> ✦\n\n"
            f"• 📝 <b>{to_smallcaps('commands')}</b> :\n"
            f"  • <code>/autocaption</code> : {to_smallcaps('toggle auto-caption on/off.')}\n"
            f"  • <code>/setcaption &lt;template&gt;</code> : {to_smallcaps('set custom caption template.')}\n"
            f"  • <code>/addcaption &lt;template&gt;</code> : {to_smallcaps('add to cap[] queue.')}\n"
            f"  • <code>/caption</code> : {to_smallcaps('view active caption & queue.')}\n"
            f"  • <code>/delcaption</code> : {to_smallcaps('reset to default.')}\n"
            f"  • <code>/cautocaption &lt;template&gt;</code> : {to_smallcaps('set channel auto-caption.')}\n\n"
            f"• 🏷️ <b>{to_smallcaps('variables')}</b> : <code>{{filename}}</code>, <code>{{filesize}}</code>, <code>{{duration}}</code>, <code>{{language}}</code>, <code>{{audio_track}}</code>, <code>{{ext}}</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "autodelete":
        text = (
            f"✦ <b>{to_smallcaps('auto-delete timer — guide')}</b> ✦\n\n"
            f"• ⏱️ <code>/autodelete</code> : {to_smallcaps('open interactive timer menu.')}\n"
            f"• ⏱️ <code>/autodelete &lt;seconds&gt;</code> : {to_smallcaps('set custom auto-delete delay.')}\n"
            f"• ⏱️ <code>/cautodel &lt;10m|off&gt;</code> : {to_smallcaps('set channel post auto-delete timer.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "session":
        text = (
            f"✦ <b>{to_smallcaps('string session generator — guide')}</b> ✦\n\n"
            f"• 🔑 <code>/session</code> : {to_smallcaps('interactive step-by-step pyrogram & telethon string session generator in telegram.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "filepro":
        text = (
            f"✦ <b>{to_smallcaps('file pro studio — guide')}</b> ✦\n\n"
            f"• 📊 <code>/mediainfo</code> : {to_smallcaps('inspect full container, video, audio & subtitle streams.')}\n"
            f"• 🎵 <code>/extractaudio</code> : {to_smallcaps('extract all audio tracks separately.')}\n"
            f"• 💬 <code>/extractsub</code> : {to_smallcaps('extract all subtitle tracks (.srt/.ass).')}\n"
            f"• ➕🎵 <code>/addaudio</code> : {to_smallcaps('mux or replace audio track in video.')}\n"
            f"• ➕💬 <code>/addsub</code> : {to_smallcaps('soft-mux subtitle file into video.')}\n"
            f"• ✂️ <code>/trim</code> : {to_smallcaps('trim video segment by start & end time.')}"
            f"{format_watermark_html()}"
        )
    elif cat in ("tools", "cleaner"):
        text = (
            f"✦ <b>{to_smallcaps('tools & service cleaner — guide')}</b> ✦\n\n"
            f"• 🆔 <code>/id</code> &amp; <code>/info</code> &amp; <code>/whois</code> : {to_smallcaps('user & chat id inspector.')}\n"
            f"• ⚡ <code>/ping</code>, <code>/usage</code> &amp; <code>/speedtest</code> : {to_smallcaps('server latency, live cpu/ram & network check.')}\n"
            f"• 🔎 <code>/search &lt;query&gt;</code> : {to_smallcaps('search indexed stream files.')}\n"
            f"• 🌐 <code>/web</code>, <code>/webss</code>, <code>/paste</code>, <code>/tr</code>, <code>/calc</code>, <code>/qr</code> : {to_smallcaps('pro web & utility tools.')}\n"
            f"• 🧹 <code>/clean &lt;count&gt;</code> &amp; <code>/purge</code> : {to_smallcaps('purge group messages & service notices.')}\n"
            f"• ❌ <code>/cancel</code> : {to_smallcaps('cancel active tasks & prompts.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "admin":
        text = (
            f"✦ <b>{to_smallcaps('master admin control — guide')}</b> ✦\n\n"
            f"• 👑 <code>/admin</code> {to_smallcaps('or')} <code>/panel</code> : {to_smallcaps('open master admin control panel.')}\n"
            f"• 📊 <code>/stats</code>, <code>/dbstats</code>, <code>/cache</code>, <code>/logs</code> : {to_smallcaps('view live metrics & logs.')}\n"
            f"• 📢 <code>/broadcast</code>, <code>/pin_broadcast</code>, <code>/grp_broadcast</code>, <code>/chnl_broadcast</code> : {to_smallcaps('multi-target broadcast.')}\n"
            f"• 🚫 <code>/botban &lt;id&gt;</code> &amp; <code>/botunban &lt;id&gt;</code> : {to_smallcaps('manage bot user access.')}\n"
            f"• 💬 <code>/chats</code>, <code>/leave</code>, <code>/disable</code>, <code>/enable</code> : {to_smallcaps('manage groups & channels.')}\n"
            f"• 🔄 <code>/restart</code>, <code>/reboot</code>, <code>/update</code> : {to_smallcaps('server lifecycle controls.')}"
            f"{format_watermark_html()}"
        )

    await safe_edit_message(query.message, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(
    filters.regex(
        r"^settings_(view_thumb|view_caption|reset_all|toggle_autoren|toggle_autocomp|toggle_autostream|toggle_autocap|toggle_upmode)"
    )
)
async def settings_actions(client: Client, query: CallbackQuery):
    action = query.matches[0].group(1)
    user = query.from_user
    user_id = user.id
    buttons = [
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to settings')}", callback_data="nav_settings"),
            InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
        ],
        [
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]

    u = await user_repo.get_or_create(user_id, user.first_name, user.username)

    if action == "toggle_autoren":
        new_val = not bool(u.get("auto_rename", False))
        await user_repo.update_user(user_id, {"auto_rename": new_val})
        await query.answer(to_smallcaps(f"auto rename {'enabled' if new_val else 'disabled'}!"))
        return await render_settings_menu(query.message, user, is_edit=True)

    elif action == "toggle_autocomp":
        new_val = not bool(u.get("auto_compress", False))
        await user_repo.update_user(user_id, {"auto_compress": new_val})
        await query.answer(to_smallcaps(f"auto compress {'enabled' if new_val else 'disabled'}!"))
        return await render_settings_menu(query.message, user, is_edit=True)

    elif action == "toggle_autostream":
        new_val = not bool(u.get("auto_stream", True))
        await user_repo.update_user(user_id, {"auto_stream": new_val})
        await query.answer(to_smallcaps(f"auto stream {'enabled' if new_val else 'disabled'}!"))
        return await render_settings_menu(query.message, user, is_edit=True)

    elif action == "toggle_autocap":
        new_val = not bool(u.get("auto_caption", True))
        await user_repo.update_user(user_id, {"auto_caption": new_val})
        await query.answer(to_smallcaps(f"auto caption {'enabled' if new_val else 'disabled'}!"))
        return await render_settings_menu(query.message, user, is_edit=True)

    elif action == "toggle_upmode":
        curr_mode = u.get("upload_mode", "video")
        new_mode = "document" if curr_mode == "video" else "video"
        await user_repo.update_user(user_id, {"upload_mode": new_mode})
        await query.answer(to_smallcaps(f"upload mode: {new_mode}!"))
        return await render_settings_menu(query.message, user, is_edit=True)

    elif action == "view_thumb":
        thumb_id = u.get("thumb_id")
        if thumb_id:
            await query.answer()
            photo_btns = [
                [
                    InlineKeyboardButton(f"🗑️ {to_smallcaps('delete thumbnail')}", callback_data="thumb_del_cb"),
                ],
                [
                    InlineKeyboardButton(f"🔙 {to_smallcaps('back to settings')}", callback_data="nav_settings"),
                    InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
                ],
            ]
            await query.message.reply_photo(
                photo=thumb_id,
                caption=f"🖼️ <b>{to_smallcaps('your active custom thumbnail')}</b>{format_watermark_html()}",
                reply_markup=InlineKeyboardMarkup(photo_btns),
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await query.answer()
            tham_url = html.escape(str(u.get("tham_url") or Config.THAM_URL))
            await safe_edit_message(
                query.message,
                f"ℹ️ <b>{to_smallcaps('no custom photo thumbnail set.')}</b>\n\n"
                f"• 🔗 <b>{to_smallcaps('automatic tham_url')}</b> : <code>{tham_url}</code>\n\n"
                f"💡 {to_smallcaps('send any photo or reply to a photo with')} <code>/setthumb</code>."
                f"{format_watermark_html()}",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=enums.ParseMode.HTML,
            )

    elif action == "view_caption":
        await query.answer()
        c = u.get("custom_caption")
        cap_list = u.get("captions_list", [])
        text = f"✦ <b>{to_smallcaps('your caption settings')}</b> ✦\n\n"
        if c:
            text += f"• 📝 <b>{to_smallcaps('active caption')}</b> :\n<code>{html.escape(str(c))}</code>\n\n"
        else:
            text += f"• 📝 <b>{to_smallcaps('active caption')}</b> : <code>{to_smallcaps('default auto metadata template')}</code>\n\n"

        if cap_list:
            text += f"📚 <b>{to_smallcaps('saved dynamic captions (cap[])')}</b> :\n"
            for i, cap in enumerate(cap_list, 1):
                text += f"{i}. <code>{html.escape(str(cap))}</code>\n"
            text += "\n"

        text += f"💡 {to_smallcaps('use')} <code>/setcaption &lt;template&gt;</code> {to_smallcaps('to update.')}"
        text += format_watermark_html()
        cap_btns = [
            [
                InlineKeyboardButton(f"🗑️ {to_smallcaps('reset captions')}", callback_data="caption_reset_cb"),
            ],
            [
                InlineKeyboardButton(f"🔙 {to_smallcaps('back to settings')}", callback_data="nav_settings"),
                InlineKeyboardButton(f"🏠 {to_smallcaps('home')}", callback_data="nav_home"),
            ],
        ]
        await safe_edit_message(query.message, text, reply_markup=InlineKeyboardMarkup(cap_btns), parse_mode=enums.ParseMode.HTML)

    elif action == "reset_all":
        await user_repo.update_user(
            user_id,
            {
                "thumb_id": None,
                "custom_caption": None,
                "captions_list": [],
                "prefix": "",
                "suffix": "",
                "auto_rename": False,
                "auto_compress": False,
                "auto_stream": True,
                "auto_caption": True,
                "upload_mode": "video",
                "auto_delete_time": 0,
            },
        )
        await query.answer(to_smallcaps("all settings reset!"), show_alert=True)
        await render_settings_menu(query.message, user, is_edit=True)
