import os
import time
from pyrogram import Client, enums, filters
from pyrogram.types import Message
from config import Config
from info import COMMAND_PREFIXES
from plugins.core_db import user_repo
from plugins.core_utils import (
    execute_with_floodwait,
    format_watermark,
    logger,
    safe_edit_text,
    safe_reply_text,
    time_formatter,
    to_smallcaps,
)


async def _get_user_string(user_id: int) -> str:
    if user_id:
        u = await user_repo.get_user(user_id)
        if u and u.get("userbot_session"):
            return str(u["userbot_session"]).strip()
    if Config.OWNER_ID:
        owner_doc = await user_repo.get_user(Config.OWNER_ID)
        if owner_doc and owner_doc.get("userbot_session"):
            return str(owner_doc["userbot_session"]).strip()
    return (
        os.environ.get("USER_SESSION", "").strip()
        or os.environ.get("STRING_SESSION", "").strip()
    )


def _render_bar(done: int, total: int) -> str:
    pct = min(100.0, (done * 100.0 / total)) if total > 0 else 100.0
    filled = min(10, max(0, int(pct // 10)))
    return f"[{'▰' * filled}{'▱' * (10 - filled)}] `{pct:.1f}%`"


@Client.on_message(filters.private & filters.command(["setuserbot", "deluserbot"], prefixes=COMMAND_PREFIXES))
async def manage_userbot_session_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if message.command[0].lower() == "deluserbot":
        await user_repo.update_user(uid, {"userbot_session": None})
        return await safe_reply_text(
            message,
            f"🗑️ **{to_smallcaps('saved userbot session deleted!')}**{format_watermark()}",
        )
    if len(message.command) < 2:
        return await safe_reply_text(
            message,
            f"📝 **{to_smallcaps('usage')}** : `/setuserbot <pyrogram_string>`\n"
            f"💡 {to_smallcaps('generate a pyrogram string first with')} `/session`."
            f"{format_watermark()}",
        )
    sess_str = message.text.split(None, 1)[1].strip()
    try:
        await message.delete()
    except Exception:
        pass
    await user_repo.get_or_create(uid, message.from_user.first_name, message.from_user.username)
    await user_repo.update_user(uid, {"userbot_session": sess_str})
    logger.info("🔐 /setuserbot sᴀᴠᴇᴅ › ᴜsᴇʀ=%s", uid)
    await client.send_message(
        chat_id=message.chat.id,
        text=(
            f"✅ **{to_smallcaps('userbot string session saved!')}** 🔐\n\n"
            f"💡 {to_smallcaps('now use')} `/approval` {to_smallcaps('in your group/channel or')} `/approval -100xxxxxxxxxx`."
            f"{format_watermark()}"
        ),
    )


@Client.on_message(
    filters.command(["approval", "approveall"], prefixes=COMMAND_PREFIXES)
    & (filters.private | filters.group | filters.channel)
)
async def approve_pending_joins_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        target_chat = int(message.command[1])
    elif message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
        target_chat = message.chat.id
    else:
        return await safe_reply_text(
            message,
            f"📝 **{to_smallcaps('usage')}** : `/approval <chat_id>` {to_smallcaps('or run')} `/approval` {to_smallcaps('inside your group/channel.')}"
            f"{format_watermark()}",
        )

    session_str = await _get_user_string(uid)
    if not session_str:
        return await safe_reply_text(
            message,
            f"⚠️ **{to_smallcaps('no userbot string session found!')}**\n\n"
            f"• 1. {to_smallcaps('run')} `/session` {to_smallcaps('in private chat to generate a pyrogram string.')}\n"
            f"• 2. {to_smallcaps('save it with')} `/setuserbot <string>`."
            f"{format_watermark()}",
        )

    status = await safe_reply_text(
        message,
        f"⏳ **{to_smallcaps('verifying userbot admin access & scanning pending requests...')}**",
    )

    try:
        ub = Client(
            name=f"MMW_UB_{uid or abs(target_chat)}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_str,
            in_memory=True,
            no_updates=True,
        )
        async with ub:
            me = await ub.get_me()
            member = await ub.get_chat_member(target_chat, me.id)
            is_owner = member.status == enums.ChatMemberStatus.OWNER
            can_invite = bool(
                member.status == enums.ChatMemberStatus.ADMINISTRATOR
                and member.privileges
                and member.privileges.can_invite_users
            )
            if not (is_owner or can_invite):
                return await safe_edit_text(
                    status,
                    f"🚫 **{to_smallcaps('access denied!')}**\n\n"
                    f"• 👤 **{to_smallcaps('userbot')}** : `{me.first_name}` (`{me.id}`)\n"
                    f"• 💡 {to_smallcaps('the session account must be owner or admin with invite users permission in')} `{target_chat}`."
                    f"{format_watermark()}",
                )

            pending_users = []
            async for req in ub.get_chat_join_requests(target_chat):
                if req and req.user:
                    pending_users.append(req.user.id)

            total = len(pending_users)
            if total == 0:
                return await safe_edit_text(
                    status,
                    f"ℹ️ **{to_smallcaps('no pending join requests found in')}** `{target_chat}`.{format_watermark()}",
                )

            approved = 0
            failed = 0
            start_ts = time.time()
            last_edit = 0.0

            for idx, target_uid in enumerate(pending_users, 1):
                try:
                    await execute_with_floodwait(
                        ub.approve_chat_join_request,
                        chat_id=target_chat,
                        user_id=target_uid,
                    )
                    approved += 1
                except Exception:
                    failed += 1

                now = time.time()
                if idx == total or (now - last_edit) >= 3.0:
                    last_edit = now
                    elapsed = time_formatter(seconds=int(max(1, now - start_ts)))
                    await safe_edit_text(
                        status,
                        f"⚡ **{to_smallcaps('approving pending join requests...')}**\n\n"
                        f"{_render_bar(idx, total)}\n"
                        f"• ✅ **{to_smallcaps('approved')}** : `{approved} / {total}`\n"
                        f"• ❌ **{to_smallcaps('failed')}** : `{failed}`\n"
                        f"• ⏱️ **{to_smallcaps('elapsed')}** : `{elapsed}`"
                        f"{format_watermark()}",
                    )

            total_time = time_formatter(seconds=int(max(1, time.time() - start_ts)))
            logger.info(
                "🤝 /approval ᴅᴏɴᴇ › ᴄʜᴀᴛ=%s ᴀᴘᴘʀᴏᴠᴇᴅ=%s ғᴀɪʟᴇᴅ=%s ᴜʙ=%s",
                target_chat,
                approved,
                failed,
                me.id,
            )
            await safe_edit_text(
                status,
                f"✦ **{to_smallcaps('pending join approval completed')}** ✦ ✅\n\n"
                f"• 💬 **{to_smallcaps('chat')}** : `{target_chat}`\n"
                f"• 👤 **{to_smallcaps('userbot')}** : `{me.first_name}` (`{me.id}`)\n"
                f"• ✅ **{to_smallcaps('approved')}** : `{approved} / {total}`\n"
                f"• ❌ **{to_smallcaps('failed')}** : `{failed}`\n"
                f"• ⏱️ **{to_smallcaps('time taken')}** : `{total_time}`"
                f"{format_watermark()}",
            )
    except Exception as exc:
        logger.warning("⚠️ /approval ᴇʀʀᴏʀ › ᴄʜᴀᴛ=%s ᴇʀʀ=%s", target_chat, exc)
        await safe_edit_text(
            status,
            f"❌ **{to_smallcaps('approval failed')}** : `{exc}`\n\n"
            f"💡 {to_smallcaps('verify your string session and make sure the userbot is added as admin in')} `{target_chat}`."
            f"{format_watermark()}",
        )
