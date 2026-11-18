import asyncio
import math
import time

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    UserNotParticipant,
)
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from info import COMMAND_PREFIXES
from plugins.core_utils import (
    cache, to_smallcaps, format_watermark, logger,
    safe_edit_text, safe_reply_text,
)

AUTH_PENDING_CLIENTS = {}
USER_SESSION_CLIENTS = {}
USER_ACCOUNT_IDS = {}
SESSION_LOCKS = {}


def _user_lock(user_id: int):
    lock = SESSION_LOCKS.get(user_id)
    if lock is None:
        lock = SESSION_LOCKS[user_id] = asyncio.Lock()
    return lock


async def _session_state_filter(_, __, message: Message):
    if not message.from_user or not message.text:
        return False
    return await cache.get(f"sess_step_{message.from_user.id}") is not None


SESSION_STATE_FILTER = filters.create(_session_state_filter)


@Client.on_message(filters.private & filters.command(["generate", "session"], prefixes=COMMAND_PREFIXES))
async def session_generator_command(client: Client, message: Message):
    buttons = [[
        InlineKeyboardButton(
            f"⚡ {to_smallcaps('generate pyrogram / pyrofork session')}",
            callback_data="sess_pyro",
        )
    ]]
    await message.reply_text(
        f"✦ **{to_smallcaps('personal string session generator')}** ✦\n\n"
        f"🔐 {to_smallcaps('each Telegram user gets a separate authenticated session linked to their own Telegram user ID.')}\n\n"
        f"• {to_smallcaps('use your own account and never share the generated session string.')}\n"
        f"• {to_smallcaps('the authenticated account stays connected for your /approval commands.')}\n"
        f"• {to_smallcaps('session credentials are kept in process memory only; restarting the bot requires generating again.')}\n\n"
        f"💡 {to_smallcaps('tap below to begin secure login.')}"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex(r"^sess_pyro$"))
async def session_lib_choice(client: Client, query):
    user_id = query.from_user.id
    await query.answer(to_smallcaps("secure session login"))
    await cache.set(
        f"sess_step_{user_id}",
        {"lib": "pyro", "step": "phone", "telegram_user_id": user_id},
        ttl=600,
    )
    await safe_edit_text(
        query.message,
        f"✦ **{to_smallcaps('step 1: phone number')}** ✦\n\n"
        f"📱 {to_smallcaps('send your own phone number in international format, including country code.')}\n"
        f"Example: `+919876543210`\n\n"
        f"🔒 {to_smallcaps('never enter another person’s phone number.')}\n"
        f"{format_watermark()}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"❌ {to_smallcaps('cancel')}", callback_data="cancel_op")
        ]]),
    )


@Client.on_message(filters.private & filters.text & SESSION_STATE_FILTER, group=7)
async def handle_session_text_step(client: Client, message: Message):
    user_id = message.from_user.id
    lock = _user_lock(user_id)
    async with lock:
        state = await cache.get(f"sess_step_{user_id}")
        if not state:
            return
        if state.get("telegram_user_id") != user_id:
            await cache.delete(f"sess_step_{user_id}")
            return
        step = state.get("step")
        text = (message.text or "").strip()

        if text.lower() in ("/cancel", "cancel"):
            await _discard_pending_client(user_id)
            await message.reply_text(f"❎ {to_smallcaps('session generation cancelled.')}")
            return

        if step == "phone":
            phone = text.replace(" ", "")
            if not phone.startswith("+") or not phone[1:].isdigit() or not 8 <= len(phone) <= 16:
                return await message.reply_text(
                    f"⚠️ {to_smallcaps('invalid phone number. Use international format, e.g.')} `+919876543210`"
                )
            status = await message.reply_text(f"⏳ {to_smallcaps('sending Telegram login code...')}")
            temp_client = None
            try:
                temp_client = Client(
                    name=f"mmw_user_session_{user_id}",
                    api_id=Config.API_ID,
                    api_hash=Config.API_HASH,
                    in_memory=True,
                    no_updates=True,
                )
                await temp_client.connect()
                sent_code = await temp_client.send_code(phone)
                AUTH_PENDING_CLIENTS[user_id] = temp_client
                state.update({
                    "phone": phone,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "step": "otp",
                })
                await cache.set(f"sess_step_{user_id}", state, ttl=600)
                await status.edit_text(
                    f"✦ **{to_smallcaps('step 2: enter login code')}** ✦\n\n"
                    f"📩 {to_smallcaps('enter the code Telegram sent to your account. For safety, do not forward it to anyone.')}\n"
                    f"{format_watermark()}"
                )
            except Exception as exc:
                if temp_client:
                    try:
                        await temp_client.disconnect()
                    except Exception:
                        pass
                await cache.delete(f"sess_step_{user_id}")
                logger.warning("Session login code request failed for bot user %s: %s", user_id, type(exc).__name__)
                await status.edit_text(
                    f"❌ {to_smallcaps('could not send login code. Please try /generate again.')}"
                )
            return

        temp_client = AUTH_PENDING_CLIENTS.get(user_id)
        if not temp_client:
            await cache.delete(f"sess_step_{user_id}")
            return await message.reply_text(
                f"⌛ {to_smallcaps('login expired. Start again with')} `/generate`."
            )

        if step == "otp":
            otp_code = text.replace(" ", "").replace("-", "")
            try:
                await temp_client.sign_in(state["phone"], state["phone_code_hash"], otp_code)
                await _finalize_user_session(temp_client, message, user_id)
            except SessionPasswordNeeded:
                state["step"] = "2fa"
                await cache.set(f"sess_step_{user_id}", state, ttl=600)
                await message.reply_text(
                    f"🔐 **{to_smallcaps('two-step verification required')}**\n\n"
                    f"{to_smallcaps('send your Telegram 2FA password here. It is used only for this login attempt.')}"
                )
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await message.reply_text(f"⚠️ {to_smallcaps('invalid or expired login code. Please try again.')}")
            except Exception as exc:
                logger.warning("Session OTP sign-in failed for bot user %s: %s", user_id, type(exc).__name__)
                await _discard_pending_client(user_id)
                await message.reply_text(f"❌ {to_smallcaps('sign-in failed. Start again with')} `/generate`.")
            return

        if step == "2fa":
            try:
                await temp_client.check_password(password=text)
                await _finalize_user_session(temp_client, message, user_id)
            except PasswordHashInvalid:
                await message.reply_text(f"⚠️ {to_smallcaps('incorrect 2FA password. Please try again.')}")
            except Exception as exc:
                logger.warning("Session 2FA failed for bot user %s: %s", user_id, type(exc).__name__)
                await _discard_pending_client(user_id)
                await message.reply_text(f"❌ {to_smallcaps('2FA verification failed. Start again with')} `/generate`.")


async def _finalize_user_session(auth_client: Client, message: Message, user_id: int):
    try:
        me = await auth_client.get_me()
        if not me or me.is_bot:
            raise RuntimeError("The authenticated account is not a user account.")
        if me.id != user_id:
            raise PermissionError("The signed-in Telegram account does not match this bot user.")
        session_string = await auth_client.export_session_string()

        old_client = USER_SESSION_CLIENTS.get(user_id)
        if old_client and old_client is not auth_client:
            try:
                await old_client.disconnect()
            except Exception:
                pass

        USER_SESSION_CLIENTS[user_id] = auth_client
        USER_ACCOUNT_IDS[user_id] = me.id
        AUTH_PENDING_CLIENTS.pop(user_id, None)
        await cache.delete(f"sess_step_{user_id}")

        await message.reply_text(
            f"✦ **{to_smallcaps('your personal string session is ready')}** ✦\n\n"
            f"👤 **{to_smallcaps('linked Telegram user ID')}**: `{me.id}`\n"
            f"📦 **{to_smallcaps('library')}**: `Pyrogram / Pyrofork`\n\n"
            f"`{session_string}`\n\n"
            f"⚠️ **{to_smallcaps('this string grants account access. Never share it, post it, or send it to support.') }**\n\n"
            f"✅ {to_smallcaps('your authenticated client remains connected in this bot process. Use')} `/approval -100xxxxxxxxxx`."
            f"{format_watermark()}",
            disable_web_page_preview=True,
        )
        logger.info("Personal user session activated for Telegram user %s", user_id)
    except Exception:
        await _discard_pending_client(user_id)
        raise


async def _discard_pending_client(user_id: int):
    await cache.delete(f"sess_step_{user_id}")
    pending = AUTH_PENDING_CLIENTS.pop(user_id, None)
    if pending:
        try:
            await pending.disconnect()
        except Exception:
            pass


def _progress_bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, int(width * done / total))
    return "█" * filled + "░" * (width - filled)


@Client.on_message(filters.private & filters.command("approval", prefixes=COMMAND_PREFIXES))
async def approve_all_join_requests(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if len(message.command) < 2:
        return await message.reply_text(
            f"⚠️ {to_smallcaps('usage')}: `/approval -100xxxxxxxxxx`\n"
            f"{to_smallcaps('first create your personal session with')} `/generate`."
        )
    try:
        chat_id = int(message.command[1])
    except (TypeError, ValueError):
        return await message.reply_text(f"⚠️ {to_smallcaps('invalid chat ID. Example')}: `/approval -1001234567890`")

    user_client = USER_SESSION_CLIENTS.get(user_id)
    if not user_client or USER_ACCOUNT_IDS.get(user_id) != user_id:
        return await message.reply_text(
            f"🔐 {to_smallcaps('no active personal session found for your Telegram ID. Run')} `/generate`."
        )

    status_msg = await message.reply_text(f"🔎 {to_smallcaps('checking your account permissions...')}")
    try:
        me = await user_client.get_me()
        if not me or me.id != user_id:
            return await status_msg.edit_text(
                f"🚫 {to_smallcaps('session identity mismatch. Generate your own session again.')}"
            )
        member = await user_client.get_chat_member(chat_id, me.id)
        status = member.status
        privileges = getattr(member, "privileges", None)
        can_invite = bool(getattr(privileges, "can_invite_users", False))
        is_admin = status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
        if not is_admin or not can_invite:
            return await status_msg.edit_text(
                f"🚫 **{to_smallcaps('permission denied')}**\n\n"
                f"{to_smallcaps('the authenticated account must be the chat owner or an administrator with can_invite_users permission.')}"
            )

        await status_msg.edit_text(f"📥 {to_smallcaps('fetching pending join requests...')}")
        pending_requests = []
        async for request in user_client.get_chat_join_requests(chat_id, limit=0):
            pending_requests.append(request)

        total = len(pending_requests)
        if total == 0:
            return await status_msg.edit_text(
                f"✅ {to_smallcaps('there are no pending join requests.')}\n"
                f"👤 `{me.id}` · 💬 `{chat_id}`"
            )

        approved = 0
        failed = 0
        last_edit = 0.0
        await status_msg.edit_text(
            f"⚙️ **{to_smallcaps('approving pending requests')}**\n\n"
            f"`{_progress_bar(0, total)}` `0/{total}`\n"
            f"✅ `0`  ⚠️ `0`"
        )
        for request in pending_requests:
            try:
                await user_client.approve_chat_join_request(chat_id, request.from_user.id)
                approved += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Join request approval failed: account=%s chat=%s requester=%s error=%s",
                    user_id, chat_id, getattr(request.from_user, "id", 0), type(exc).__name__,
                )
            now = time.monotonic()
            if approved + failed == total or now - last_edit >= 1.5:
                done = approved + failed
                try:
                    await status_msg.edit_text(
                        f"⚙️ **{to_smallcaps('approving pending requests')}**\n\n"
                        f"`{_progress_bar(done, total)}` `{done}/{total}`\n"
                        f"✅ `{approved}`  ⚠️ `{failed}`"
                    )
                    last_edit = now
                except Exception:
                    pass

        await status_msg.edit_text(
            f"✦ **{to_smallcaps('approval process completed')}** ✦\n\n"
            f"📊 `{_progress_bar(total, total)}` `{total}/{total}`\n"
            f"✅ **{to_smallcaps('approved')}**: `{approved}`\n"
            f"⚠️ **{to_smallcaps('failed')}**: `{failed}`\n"
            f"👤 **{to_smallcaps('authenticated account')}**: `{me.id}`\n"
            f"💬 **{to_smallcaps('chat ID')}**: `{chat_id}`\n\n"
            f"🔌 {to_smallcaps('your personal session remains connected.')}"
        )
    except UserNotParticipant:
        await status_msg.edit_text(f"🚫 {to_smallcaps('your authenticated account is not a member of this chat.')}")
    except Exception as exc:
        logger.warning("Approval command failed for user %s chat %s: %s", user_id, chat_id, type(exc).__name__)
        await status_msg.edit_text(
            f"❌ {to_smallcaps('could not process join requests. Check the chat ID, account access, and Telegram permissions.')}"
        )
