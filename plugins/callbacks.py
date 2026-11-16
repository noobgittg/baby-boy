import html
from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.users_chats_db import db
from Script import script
from utils import format_watermark_html, is_admin_sync, safe_edit_message
from plugins.core_db import user_repo, chat_repo, file_repo
from plugins.core_utils import to_smallcaps, task_manager, safe_reply_text
from plugins.start import build_home_buttons, build_home_text


async def render_help_menu(target_msg: Message, user, is_admin: bool, is_edit: bool = True):
    mention = user.mention if user else "ғʀɪᴇɴᴅ"
    help_text = (
        f"✦ <b>{to_smallcaps('mmw ultra max v8.0 — smart help center')}</b> ✦ ⚡\n\n"
        f"<blockquote>👋 <b>{to_smallcaps('hello')} {mention}</b> !\n"
        f"{to_smallcaps('explore all ultra media, recovery, stream, group & channel features below:')}</blockquote>\n\n"
        f"• ♻️ <b>{to_smallcaps('persistent task recovery')}</b> : {to_smallcaps('auto-resumes interrupted tasks across restarts')} (<code>/taskreport</code>)\n"
        f"• ⚡ <b>{to_smallcaps('auto-set wizard')}</b> : {to_smallcaps('1-click setup for user, group & channel')} (<code>/autoset</code>)\n"
        f"• 📝 <b>{to_smallcaps('auto captions & renamer')}</b> : {to_smallcaps('dynamic templates, prefix/suffix & custom thumb')}\n"
        f"• 🎬 <b>{to_smallcaps('http 206 4-player stream')}</b> : <code>PLAYit • MX Player • VLC • S Player</code> (<code>/stream</code>)\n"
        f"• 🗜️ <b>{to_smallcaps('compressor & file pro')}</b> : {to_smallcaps('5-min turbo h.264/hevc, trim, mux/extract audio & subs')}\n"
        f"• 🛡️ <b>{to_smallcaps('group helpdesk')}</b> : {to_smallcaps('70+ moderation, 16 locks, antiflood, notes & filters')}\n"
        f"• 📢 <b>{to_smallcaps('channel helper')}</b> : {to_smallcaps('35+ channel post, schedule, template & admin tools')}\n"
    )
    if is_admin:
        help_text += f"• 👑 <b>{to_smallcaps('master admin')}</b> : {to_smallcaps('live toggles, broadcast, user/chat manager & logs')}\n"
    help_text += format_watermark_html()

    buttons = [
        [
            InlineKeyboardButton(f"♻️ {to_smallcaps('task recovery')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
        ],
        [
            InlineKeyboardButton(f"🛡️ {to_smallcaps('group helpdesk')}", callback_data="hdesk|home"),
            InlineKeyboardButton(f"📢 {to_smallcaps('channel helper')}", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
            InlineKeyboardButton(f"🎨 {to_smallcaps('post & button studio')}", callback_data="postedit|home"),
        ],
        [
            InlineKeyboardButton(f"⚙️ {to_smallcaps('group/chnl editor')}", callback_data="gfeat|open"),
            InlineKeyboardButton(f"✏️ {to_smallcaps('renamer')}", callback_data="help_renamer"),
        ],
        [
            InlineKeyboardButton(f"📝 {to_smallcaps('captions')}", callback_data="help_caption"),
            InlineKeyboardButton(f"🎬 {to_smallcaps('file stream')}", callback_data="help_streamer"),
        ],
        [
            InlineKeyboardButton(f"🗜️ {to_smallcaps('compressor')}", callback_data="help_compressor"),
            InlineKeyboardButton(f"🎛️ {to_smallcaps('file pro')}", callback_data="help_filepro"),
        ],
        [
            InlineKeyboardButton(f"🖼️ {to_smallcaps('thumbnail')}", callback_data="help_thumbnail"),
            InlineKeyboardButton(f"🤝 {to_smallcaps('auto accept')}", callback_data="help_accept"),
        ],
        [
            InlineKeyboardButton(f"⏱️ {to_smallcaps('auto-delete')}", callback_data="help_autodelete"),
            InlineKeyboardButton(f"📝 {to_smallcaps('notes & filters')}", callback_data="hdesk|notes_help"),
        ],
        [
            InlineKeyboardButton(f"🌐 {to_smallcaps('uptime & web tools')}", callback_data="hdesk|web_help"),
            InlineKeyboardButton(f"🔑 {to_smallcaps('session')}", callback_data="help_session"),
        ],
        [
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
        await safe_reply_text(
            target_msg,
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
        f"✦ <b>{to_smallcaps('ultra smart settings & auto dashboard')}</b> ✦ ⚙️\n\n"
        f"👤 <b>{to_smallcaps('user')}</b> : {mention} (<code>{uid}</code>)\n\n"
        f"• 📝 <b>{to_smallcaps('auto caption')}</b> : <code>{auto_cap}</code>\n"
        f"• ✏️ <b>{to_smallcaps('auto rename')}</b> : <code>{auto_ren}</code>\n"
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
            InlineKeyboardButton(f"✏️ {to_smallcaps('auto rename')}: {auto_ren}", callback_data="settings_toggle_autoren"),
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
            InlineKeyboardButton(f"♻️ {to_smallcaps('task recovery')}", callback_data="task_rec#report"),
            InlineKeyboardButton(f"🗂️ {to_smallcaps('my files vault')}", callback_data="pvt_vault#page_0"),
        ],
        [
            InlineKeyboardButton(f"🎛️ {to_smallcaps('presets & tasks')}", callback_data="pvt_dash#home"),
            InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
        ],
        [
            InlineKeyboardButton(f"🖼️ {to_smallcaps('view thumb')}", callback_data="settings_view_thumb"),
            InlineKeyboardButton(f"📝 {to_smallcaps('view caption')}", callback_data="settings_view_caption"),
        ],
        [
            InlineKeyboardButton(f"🔁 {to_smallcaps('auto-forward')}", callback_data="autofwd|home"),
            InlineKeyboardButton(f"🎨 {to_smallcaps('post & button studio')}", callback_data="postedit|home"),
        ],
        [
            InlineKeyboardButton(f"⚙️ {to_smallcaps('group/chnl editor')}", callback_data="gfeat|open"),
            InlineKeyboardButton(f"🔄 {to_smallcaps('reset all')}", callback_data="settings_reset_all"),
        ],
        [
            InlineKeyboardButton(f"🏠 {to_smallcaps('back to home')}", callback_data="nav_home"),
            InlineKeyboardButton(f"❌ {to_smallcaps('close')}", callback_data="close_data"),
        ],
    ]
    if is_edit:
        await safe_edit_message(target_msg, settings_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await safe_reply_text(
            target_msg,
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
                InlineKeyboardButton(f"♻️ {to_smallcaps('task recovery')}", callback_data="task_rec#report"),
                InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            ],
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
        rep = task_manager.get_recovery_report()
        status_text = (
            f"✦ <b>{to_smallcaps('mmw ultra max v8.0 — live status')}</b> ✦ ⚡\n\n"
            f"• 👥 <b>{to_smallcaps('registered users')}</b> : <code>{users_count}</code>\n"
            f"• 💬 <b>{to_smallcaps('active groups')}</b> : <code>{chats_count}</code>\n"
            f"• 📢 <b>{to_smallcaps('managed channels')}</b> : <code>{chnl_count}</code>\n"
            f"• 📁 <b>{to_smallcaps('indexed stream files')}</b> : <code>{files_count}</code>\n"
            f"• 🔁 <b>{to_smallcaps('auto-forward routes')}</b> : <code>{fwd_rules}</code>\n"
            f"• ♻️ <b>{to_smallcaps('tasks completed / recovered')}</b> : <code>{rep['completed_tasks']} / {rep['recovered_tasks']}</code>\n"
            f"• ⚡ <b>{to_smallcaps('stream mode')}</b> : <code>turbo HTTP 206 byte-range (4 Players)</code>\n"
            f"• 🚀 <b>{to_smallcaps('server status')}</b> : <code>Online &amp; Ultra-Fast</code>"
            f"{format_watermark_html()}"
        )
        buttons = [
            [
                InlineKeyboardButton(f"♻️ {to_smallcaps('task recovery')}", callback_data="task_rec#report"),
                InlineKeyboardButton(f"⚡ {to_smallcaps('auto-set wizard')}", callback_data="autoset|home"),
            ],
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
            f"• ⚡ <b>{to_smallcaps('auto-rename mode')}</b> :\n"
            f"  • <code>/autorename</code> : {to_smallcaps('toggle automatic renaming on/off.')}\n"
            f"  • <code>/setformat &lt;tpl&gt;</code> : {to_smallcaps('set custom template ({filename}, {resolution}).')}\n"
            f"  • <code>/setprefix &lt;text&gt;</code> &amp; <code>/setsuffix &lt;text&gt;</code>\n"
            f"  • <code>/delprefix</code> &amp; <code>/delsuffix</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "compressor":
        text = (
            f"✦ <b>{to_smallcaps('fault-tolerant compressor & recovery — guide')}</b> ✦\n\n"
            f"• 🗜️ <b>{to_smallcaps('manual & auto compression')}</b> :\n"
            f"  • {to_smallcaps('send a video or reply with')} <code>/compress</code>, <code>/customcompress</code>, {to_smallcaps('or')} <code>/compress 450MB 480p</code>.\n"
            f"  • {to_smallcaps('choose 5-min turbo, 720p, 480p, 360p, 1080p, h.265 hevc, remux, or target size.')}\n"
            f"  • <code>/autocompress</code> : {to_smallcaps('toggle automatic compression.')}\n\n"
            f"• ♻️ <b>{to_smallcaps('automatic task recovery')}</b> :\n"
            f"  • <code>/taskreport</code> &amp; <code>/recover</code> : {to_smallcaps('auto-resumes tasks across restarts & server rebuilds!')}"
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
            f"• 📥 <code>/getfile &lt;id&gt;</code> &amp; 🗑️ <code>/revokestream &lt;id&gt;</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "accept":
        text = (
            f"✦ <b>{to_smallcaps('auto-accept join requests — guide')}</b> ✦\n\n"
            f"• 🤝 <code>/accept</code> {to_smallcaps('or')} <code>/autoapprove</code> : {to_smallcaps('toggle automatic join request approval.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "thumbnail":
        text = (
            f"✦ <b>{to_smallcaps('smart thumbnail — guide')}</b> ✦\n\n"
            f"• 🖼️ <code>/setthumb</code>, <code>/setthumb &lt;url&gt;</code>, <code>/thumb</code>, <code>/delthumb</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "caption":
        text = (
            f"✦ <b>{to_smallcaps('auto dynamic captions — guide')}</b> ✦\n\n"
            f"• 📝 <code>/autocaption</code>, <code>/setcaption</code>, <code>/addcaption</code>, <code>/caption</code>, <code>/delcaption</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "autodelete":
        text = (
            f"✦ <b>{to_smallcaps('auto-delete timer — guide')}</b> ✦\n\n"
            f"• ⏱️ <code>/autodelete</code>, <code>/autodelete &lt;seconds&gt;</code>, <code>/cautodel &lt;10m|off&gt;</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "session":
        text = (
            f"✦ <b>{to_smallcaps('string session generator — guide')}</b> ✦\n\n"
            f"• 🔑 <code>/session</code> : {to_smallcaps('interactive pyrogram & telethon string session generator.')}"
            f"{format_watermark_html()}"
        )
    elif cat == "filepro":
        text = (
            f"✦ <b>{to_smallcaps('file pro studio — guide')}</b> ✦\n\n"
            f"• 📊 <code>/mediainfo</code> • 🎵 <code>/extractaudio</code> • 💬 <code>/extractsub</code> • ➕🎵 <code>/addaudio</code> • ➕💬 <code>/addsub</code> • ✂️ <code>/trim</code> • 🎞️ <code>/sample</code> • 🔇 <code>/mutevideo</code> • 🔄 <code>/remux</code>"
            f"{format_watermark_html()}"
        )
    elif cat in ("tools", "cleaner"):
        text = (
            f"✦ <b>{to_smallcaps('tools, web utilities & service cleaner — guide')}</b> ✦\n\n"
            f"• 🌐 <code>/uptime</code>, <code>/listup</code>, <code>/revoke</code>, <code>/web</code>, <code>/webss</code>, <code>/fwebss</code>\n"
            f"• 🆔 <code>/id</code>, <code>/info</code>, <code>/ping</code>, <code>/usage</code>, <code>/speedtest</code>, <code>/search</code>, <code>/clean</code>, <code>/cancel</code>"
            f"{format_watermark_html()}"
        )
    elif cat == "admin":
        text = (
            f"✦ <b>{to_smallcaps('master admin control — guide')}</b> ✦\n\n"
            f"• 👑 <code>/admin</code>, <code>/panel</code>, <code>/stats</code>, <code>/dbstats</code>, <code>/cache</code>, <code>/logs</code>, <code>/taskreport</code>, <code>/recover</code>, <code>/broadcast</code>, <code>/restart</code>, <code>/update</code>"
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
