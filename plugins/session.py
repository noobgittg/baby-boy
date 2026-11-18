import asyncio
import time
from info import COMMAND_PREFIXES
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait,
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
)
from config import Config
from plugins.core_utils import cache, to_smallcaps, format_watermark, logger, safe_edit_text, safe_reply_text
from plugins.core_db import user_repo
from utils import make_progress_bar

ACTIVE_SESS_CLIENTS = {}
USER_SESSION_CLIENTS = {}
USER_SESSION_LOCKS = {}

def _get_user_lock(user_id: int) -> asyncio.Lock:
    lock = USER_SESSION_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        USER_SESSION_LOCKS[user_id] = lock
    return lock

async def _save_user_session(user_id: int, session_str: str):
    await cache.set(f"user_string_session_{user_id}", session_str, ttl=86400 * 30)
    try:
        await user_repo.update_user(
            user_id,
            {"string_session": session_str, "session_updated_at": float(time.time())},
        )
    except Exception as e:
        logger.warning(f"Failed to persist string session for user {user_id} in DB: {e}")

async def _get_user_session_string(user_id: int) -> str:
    cached = await cache.get(f"user_string_session_{user_id}")
    if isinstance(cached, str) and cached.strip():
        return cached.strip()
    try:
        user_doc = await user_repo.get_user(user_id)
        if user_doc:
            sess = user_doc.get("string_session")
            if isinstance(sess, str) and sess.strip():
                await cache.set(f"user_string_session_{user_id}", sess.strip(), ttl=86400 * 30)
                return sess.strip()
    except Exception as e:
        logger.warning(f"Failed to load string session for user {user_id} from DB: {e}")
    return ""

async def _clear_user_session_record(user_id: int):
    await cache.delete(f"user_string_session_{user_id}")
    old_cli = USER_SESSION_CLIENTS.pop(user_id, None)
    if old_cli:
        try:
            await old_cli.disconnect()
        except Exception:
            pass
    try:
        await user_repo.update_user(user_id, {"string_session": None})
    except Exception:
        pass

async def get_connected_user_client(user_id: int) -> Client:
    existing = USER_SESSION_CLIENTS.get(user_id)
    if existing and getattr(existing, "is_connected", False):
        return existing

    session_str = await _get_user_session_string(user_id)
    if not session_str:
        raise ValueError("NO_SESSION")

    user_cli = Client(
        name=f"user_sess_{user_id}",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=session_str,
        in_memory=True,
    )
    await user_cli.connect()
    try:
        await user_cli.get_me()
    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated) as e:
        try:
            await user_cli.disconnect()
        except Exception:
            pass
        await _clear_user_session_record(user_id)
        raise ValueError(f"SESSION_INVALID:{e}")
    except Exception:
        try:
            await user_cli.disconnect()
        except Exception:
            pass
        raise

    USER_SESSION_CLIENTS[user_id] = user_cli
    return user_cli

@Client.on_message(filters.private & filters.command("generate", prefixes=COMMAND_PREFIXES))
async def generate_session_command(client: Client, message: Message):
    user_id = message.from_user.id
    await cache.set(f"sess_step_{user_id}", {"lib": "pyro", "step": "phone", "source": "generate"}, ttl=600)
    buttons = [[InlineKeyboardButton(f"❌ {to_smallcaps('cancel')}", callback_data="cancel_op")]]
    await message.reply_text(
        f"✦ **{to_smallcaps('user string session generator')}** ✦\n\n"
        f"🔗 **{to_smallcaps('linked telegram id')}** : `{user_id}`\n\n"
        f"📱 **{to_smallcaps('step 1: send your phone number')}**\n\n"
        f"• {to_smallcaps('send your phone number in international format (including country code).')}\n"
        f"• **{to_smallcaps('example')}** : `+919876543210`\n\n"
        f"💡 {to_smallcaps('send')} `/cancel` {to_smallcaps('anytime to abort.')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

@Client.on_message(filters.private & filters.command("session", prefixes=COMMAND_PREFIXES))
async def session_generator_command(client: Client, message: Message):
    btn = [
        [
            InlineKeyboardButton(f"⚡ {to_smallcaps('pyrogram / pyrofork')}", callback_data="sess_pyro"),
            InlineKeyboardButton(f"🚀 {to_smallcaps('telethon')}", callback_data="sess_tele"),
        ],
        [
            InlineKeyboardButton(f"🔙 {to_smallcaps('back to home')}", callback_data="nav_home"),
        ],
    ]

    await message.reply_text(
        f"✦ **{to_smallcaps('string session generator')}** ✦\n\n"
        f"🔐 **{to_smallcaps('generate secure telegram sessions')}**\n\n"
        f"• ⚡ **{to_smallcaps('pyrogram / pyrofork')}** : {to_smallcaps('used for modern userbots & pyrogram v2 apps.')}\n"
        f"• 🚀 **{to_smallcaps('telethon')}** : {to_smallcaps('used for telethon-based userbots & scripts.')}\n\n"
        f"🛡️ **{to_smallcaps('security guarantee')}** :\n"
        f"• {to_smallcaps('100% in-memory generation (zero disk storage).')}\n"
        f"• {to_smallcaps('the session string is sent directly to your saved messages.')}\n\n"
        f"💡 **{to_smallcaps('select your target library below:')}**"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup(btn),
    )

@Client.on_callback_query(filters.regex(r"^sess_(pyro|tele)"))
async def session_lib_choice(client: Client, query: CallbackQuery):
    lib = query.matches[0].group(1)
    await query.answer("🔐 sᴇssɪᴏɴ ɢᴇɴᴇʀᴀᴛᴏʀ")
    user_id = query.from_user.id
    lib_name = "Pyrogram / Pyrofork" if lib == "pyro" else "Telethon"

    await cache.set(f"sess_step_{user_id}", {"lib": lib, "step": "phone"}, ttl=600)

    buttons = [[InlineKeyboardButton(f"❌ {to_smallcaps('cancel')}", callback_data="cancel_op")]]
    await safe_edit_text(
        query.message,
        f"✦ **{to_smallcaps(f'{lib_name} session generator')}** ✦\n\n"
        f"📱 **{to_smallcaps('step 1: send your phone number')}**\n\n"
        f"• {to_smallcaps('send your phone number in international format (including country code).')}\n"
        f"• **{to_smallcaps('example')}** : `+919876543210`\n\n"
        f"💡 {to_smallcaps('send')} `/cancel` {to_smallcaps('anytime to abort.')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def handle_session_text_step(client: Client, message: Message):
    user_id = message.from_user.id
    state = await cache.get(f"sess_step_{user_id}")
    if not state:
        return

    step = state.get("step")
    lib = state.get("lib", "pyro")
    text = (message.text or "").strip()

    if step == "phone":
        phone = text.replace(" ", "")
        if not phone.startswith("+") or len(phone) < 8:
            return await message.reply_text(
                f"⚠️ **{to_smallcaps('invalid phone number format!')}**\n\n"
                f"• {to_smallcaps('please include country code, e.g.')} `+919876543210`"
                f"{format_watermark()}"
            )
        status = await message.reply_text(f"⏳ **{to_smallcaps('sending login otp code...')}**")
        try:
            temp_client = Client(
                name=f"sess_{user_id}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                in_memory=True,
            )
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone)
            ACTIVE_SESS_CLIENTS[user_id] = temp_client
            state["phone"] = phone
            state["phone_code_hash"] = sent_code.phone_code_hash
            state["step"] = "otp"
            await cache.set(f"sess_step_{user_id}", state, ttl=600)
            await status.edit_text(
                f"✦ **{to_smallcaps('step 2: enter otp code')}** ✦\n\n"
                f"📩 {to_smallcaps('an otp code has been sent to your telegram account.')}\n"
                f"• {to_smallcaps('send the code with spaces between digits (e.g.')} `1 2 3 4 5` {to_smallcaps(') so telegram does not block it!')}"
                f"{format_watermark()}"
            )
        except Exception as e:
            await cache.delete(f"sess_step_{user_id}")
            return await status.edit_text(f"❌ **{to_smallcaps('failed to send otp')}** : `{e}`{format_watermark()}")

    elif step == "otp":
        otp_code = text.replace(" ", "").replace("-", "")
        temp_client = ACTIVE_SESS_CLIENTS.get(user_id)
        if not temp_client:
            await cache.delete(f"sess_step_{user_id}")
            return await message.reply_text(f"❌ **{to_smallcaps('session expired! start again with')}** `/generate`")

        try:
            await temp_client.sign_in(state["phone"], state["phone_code_hash"], otp_code)
            await finalize_session_export(temp_client, message, user_id, lib)
        except SessionPasswordNeeded:
            state["step"] = "2fa"
            await cache.set(f"sess_step_{user_id}", state, ttl=600)
            return await message.reply_text(
                f"🔐 **{to_smallcaps('step 3: two-step verification (2fa)')}** ✦\n\n"
                f"• {to_smallcaps('your account has 2fa enabled. please send your cloud password now:')}"
                f"{format_watermark()}"
            )
        except (PhoneCodeInvalid, PhoneCodeExpired) as e:
            return await message.reply_text(f"❌ **{to_smallcaps('invalid or expired otp code')}** : `{e}`")
        except Exception as e:
            await cleanup_sess_client(user_id)
            return await message.reply_text(f"❌ **{to_smallcaps('sign-in error')}** : `{e}`")

    elif step == "2fa":
        temp_client = ACTIVE_SESS_CLIENTS.get(user_id)
        if not temp_client:
            await cache.delete(f"sess_step_{user_id}")
            return await message.reply_text(f"❌ **{to_smallcaps('session expired! start again with')}** `/generate`")
        try:
            await temp_client.check_password(password=text)
            await finalize_session_export(temp_client, message, user_id, lib)
        except PasswordHashInvalid:
            return await message.reply_text(f"❌ **{to_smallcaps('invalid 2fa password! please try again.')}**")
        except Exception as e:
            await cleanup_sess_client(user_id)
            return await message.reply_text(f"❌ **{to_smallcaps('2fa error')}** : `{e}`")

async def finalize_session_export(temp_client: Client, message: Message, user_id: int, lib: str):
    try:
        session_str = await temp_client.export_session_string()
        await _save_user_session(user_id, session_str)

        old_cli = USER_SESSION_CLIENTS.pop(user_id, None)
        if old_cli and old_cli is not temp_client:
            try:
                await old_cli.disconnect()
            except Exception:
                pass
        USER_SESSION_CLIENTS[user_id] = temp_client
        ACTIVE_SESS_CLIENTS.pop(user_id, None)
        await cache.delete(f"sess_step_{user_id}")

        caption_txt = (
            f"✦ **{to_smallcaps('string session generated')}** ✦\n\n"
            f"🔐 **{to_smallcaps('library')}** : `{'Pyrogram / Pyrofork' if lib == 'pyro' else 'Telethon / Pyrogram'}`\n"
            f"🔗 **{to_smallcaps('linked user id')}** : `{user_id}`\n\n"
            f"`{session_str}`\n\n"
            f"✅ **{to_smallcaps('session linked and active for')}** `/approval -100xxxx`\n"
            f"⚠️ **{to_smallcaps('do not share this string with anyone!')}**"
            f"{format_watermark()}"
        )
        try:
            await temp_client.send_message("me", caption_txt)
        except Exception:
            pass
        await message.reply_text(caption_txt)
    except Exception as e:
        await cleanup_sess_client(user_id)
        await message.reply_text(f"❌ **{to_smallcaps('session export failed')}** : `{e}`{format_watermark()}")

async def cleanup_sess_client(user_id: int):
    await cache.delete(f"sess_step_{user_id}")
    cli = ACTIVE_SESS_CLIENTS.pop(user_id, None)
    if cli:
        try:
            await cli.disconnect()
        except Exception:
            pass

@Client.on_message(filters.command("approval", prefixes=COMMAND_PREFIXES))
async def approval_command_handler(client: Client, message: Message):
    if not message.from_user:
        return await safe_reply_text(
            message,
            f"⚠️ **{to_smallcaps('this command must be run from a user account.')}**{format_watermark()}",
        )

    user_id = message.from_user.id
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"✦ **{to_smallcaps('bulk join request approval')}** ✦\n\n"
            f"• **{to_smallcaps('usage')}** : `/approval -1001234567890`\n"
            f"• {to_smallcaps('uses your own linked string session from')} `/generate`."
            f"{format_watermark()}",
        )

    raw_chat = message.command[1].strip()
    try:
        target_chat_id = int(raw_chat)
    except ValueError:
        return await safe_reply_text(
            message,
            f"⚠️ **{to_smallcaps('invalid chat id!')}** {to_smallcaps('please provide a valid channel or group id like')} `-1001234567890`."
            f"{format_watermark()}",
        )

    status_msg = await safe_reply_text(
        message,
        f"⏳ **{to_smallcaps('connecting your linked session and checking permissions...')}**",
    )

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            user_cli = await get_connected_user_client(user_id)
        except ValueError as ve:
            err_code = str(ve)
            if err_code == "NO_SESSION":
                return await safe_edit_text(
                    status_msg,
                    f"🔐 **{to_smallcaps('no linked string session found for your account!')}**\n\n"
                    f"• {to_smallcaps('please generate your session first using')} `/generate`."
                    f"{format_watermark()}",
                )
            return await safe_edit_text(
                status_msg,
                f"❌ **{to_smallcaps('your saved session expired or was revoked.')}**\n\n"
                f"• {to_smallcaps('please generate a new session using')} `/generate`."
                f"{format_watermark()}",
            )
        except Exception as e:
            return await safe_edit_text(
                status_msg,
                f"❌ **{to_smallcaps('failed to connect your session')}** : `{e}`{format_watermark()}",
            )

        try:
            me = await user_cli.get_me()
            member = await user_cli.get_chat_member(target_chat_id, me.id)
        except Exception as e:
            return await safe_edit_text(
                status_msg,
                f"❌ **{to_smallcaps('unable to verify chat membership in')}** `{target_chat_id}` : `{e}`{format_watermark()}",
            )

        is_owner = member.status == enums.ChatMemberStatus.OWNER
        is_admin = member.status == enums.ChatMemberStatus.ADMINISTRATOR
        privileges = getattr(member, "privileges", None)
        can_invite = bool(getattr(privileges, "can_invite_users", False)) if privileges else False

        if not (is_owner or (is_admin and can_invite)):
            return await safe_edit_text(
                status_msg,
                f"🚫 **{to_smallcaps('permission denied!')}**\n\n"
                f"• **{to_smallcaps('account')}** : `{me.id}`\n"
                f"• **{to_smallcaps('chat')}** : `{target_chat_id}`\n"
                f"• {to_smallcaps('your authenticated account must be owner or admin with')} `can_invite_users` {to_smallcaps('permission.')}"
                f"{format_watermark()}",
            )

        await safe_edit_text(
            status_msg,
            f"🔍 **{to_smallcaps('fetching pending join requests for')}** `{target_chat_id}`...",
        )

        pending_user_ids = []
        try:
            async for req in user_cli.get_chat_join_requests(target_chat_id):
                req_user = getattr(req, "user", None)
                if req_user and getattr(req_user, "id", None):
                    pending_user_ids.append(req_user.id)
        except Exception as e:
            return await safe_edit_text(
                status_msg,
                f"❌ **{to_smallcaps('failed to fetch pending requests')}** : `{e}`{format_watermark()}",
            )

        total = len(pending_user_ids)
        if total == 0:
            return await safe_edit_text(
                status_msg,
                f"✦ **{to_smallcaps('no pending requests')}** ✦\n\n"
                f"• **{to_smallcaps('chat')}** : `{target_chat_id}`\n"
                f"• **{to_smallcaps('session')}** : `🟢 {to_smallcaps('connected (active)')}`"
                f"{format_watermark()}",
            )

        approved = 0
        failed = 0
        last_edit = 0.0
        edit_interval = 2.5

        for idx, req_uid in enumerate(pending_user_ids, start=1):
            try:
                await user_cli.approve_chat_join_request(target_chat_id, req_uid)
                approved += 1
            except FloodWait as fw:
                await asyncio.sleep(float(getattr(fw, "value", 5) or 5))
                try:
                    await user_cli.approve_chat_join_request(target_chat_id, req_uid)
                    approved += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1

            now = time.monotonic()
            if idx == total or (now - last_edit) >= edit_interval:
                last_edit = now
                pct = (idx / total) * 100.0
                bar = make_progress_bar(pct, length=12)
                await safe_edit_text(
                    status_msg,
                    f"✦ **{to_smallcaps('approving join requests')}** ✦\n\n"
                    f"• **{to_smallcaps('chat')}** : `{target_chat_id}`\n"
                    f"• **{to_smallcaps('progress')}** : `[{bar}] {pct:.1f}%`\n"
                    f"• **{to_smallcaps('processed')}** : `{idx}/{total}`\n"
                    f"• **{to_smallcaps('approved')}** : `{approved}` | **{to_smallcaps('failed')}** : `{failed}`"
                    f"{format_watermark()}",
                )

        final_bar = make_progress_bar(100.0, length=12)
        await safe_edit_text(
            status_msg,
            f"✦ **{to_smallcaps('approval completed')}** ✦\n\n"
            f"• **{to_smallcaps('chat')}** : `{target_chat_id}`\n"
            f"• **{to_smallcaps('account')}** : `{me.id}`\n"
            f"• **{to_smallcaps('progress')}** : `[{final_bar}] 100.0%`\n"
            f"• **{to_smallcaps('approved')}** : `{approved}`\n"
            f"• **{to_smallcaps('failed')}** : `{failed}`\n"
            f"• **{to_smallcaps('total')}** : `{total}`\n"
            f"• **{to_smallcaps('session')}** : `🟢 {to_smallcaps('connected (active)')}`"
            f"{format_watermark()}",
        )
