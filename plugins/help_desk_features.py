from collections import defaultdict, deque
from datetime import datetime, timedelta
import asyncio
import html
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from pyrogram import Client, enums, filters, types
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    MessageDeleteForbidden,
    MessageNotModified,
    PeerIdInvalid,
    RPCError,
    UserAdminInvalid,
    UserNotParticipant,
)
from pyrogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatJoinRequest,
    ChatPermissions,
    ChatPrivileges,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.users_chats_db import db
from info import ADMINS, CHNL_LNK, COMMAND_PREFIXES, GRP_LNK, LOG_CHANNEL, SUPPORT_CHAT
from utils import (
    build_safe_type as _build_safe_type,
    extract_user,
    get_file_id,
    get_readable_time,
    is_admin_sync,
    last_online,
    safe_edit_message,
    temp,
)

logger = logging.getLogger("GokuHelpdesk")

HD_SEP = "|"


PROMOTE_STD_PRIVS = _build_safe_type(
    ChatPrivileges,
    can_manage_chat=True,
    can_change_info=True,
    can_delete_messages=True,
    can_invite_users=True,
    can_restrict_members=True,
    can_pin_messages=True,
    can_manage_video_chats=True,
    can_manage_topics=True,
    can_promote_members=False,
)

PROMOTE_FULL_PRIVS = _build_safe_type(
    ChatPrivileges,
    can_manage_chat=True,
    can_change_info=True,
    can_delete_messages=True,
    can_invite_users=True,
    can_restrict_members=True,
    can_pin_messages=True,
    can_promote_members=True,
    can_manage_video_chats=True,
    can_manage_topics=True,
)

DEMOTE_PRIVS = _build_safe_type(
    ChatPrivileges,
    can_manage_chat=False,
    can_change_info=False,
    can_delete_messages=False,
    can_invite_users=False,
    can_restrict_members=False,
    can_pin_messages=False,
    can_promote_members=False,
    can_manage_video_chats=False,
    can_manage_topics=False,
)

MUTE_PERMS = _build_safe_type(
    ChatPermissions,
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_other_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
)

UNMUTE_PERMS = _build_safe_type(
    ChatPermissions,
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_other_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
    can_manage_topics=True,
)

RESTRICT_MEDIA_PERMS = _build_safe_type(
    ChatPermissions,
    can_send_messages=True,
    can_send_media_messages=False,
    can_send_other_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_add_web_page_previews=False,
    can_invite_users=False,
)

SUPPORTED_LOCK_TYPES = (
    "all",
    "button",
    "emoji",
    "forward",
    "url",
    "username",
    "msg",
    "media",
    "document",
    "photo",
    "video",
    "audio",
    "voice",
    "links",
    "stickers",
    "gifs",
    "polls",
    "inline",
    "bots",
    "contact",
)

URL_DETECTOR_RE = re.compile(
    r"(?:https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/)[A-Za-z0-9_.-]+(?:/[^\s]*)?",
    re.IGNORECASE,
)

USERNAME_DETECTOR_RE = re.compile(r"(?:^|\s)@[A-Za-z0-9_]{4,32}\b")
EMOJI_DETECTOR_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)

SPAM_PATTERNS_RE = re.compile(
    r"(?:free\s+crypto|airdrop\s+claim|bit\.ly/|tinyurl\.com/|whatsapp\.com/channel|earn\s+\$\d+)",
    re.IGNORECASE,
)

_FLOOD_TRACKER: Dict[Tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=30))
_LAST_MSG_HASH: Dict[Tuple[int, int], Tuple[str, float, int]] = {}
_PENDING_PURGES: Dict[str, Dict[str, Any]] = {}
_PENDING_VERIFICATIONS: Dict[Tuple[int, int], asyncio.Task] = {}


def parse_duration_arg(raw: str) -> Tuple[int, str]:
    if not raw:
        return 0, ""
    m = re.match(r"^(\d+)([smhdw])$", raw.strip().lower())
    if not m:
        return 0, ""
    val = int(m.group(1))
    unit = m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    secs = max(30, val * mult)
    return secs, f"{val}{unit}"


async def safe_tg_call(coro_fn, *args, **kwargs):
    for attempt in range(3):
        try:
            return await coro_fn(*args, **kwargs)
        except FloodWait as fw:
            wait_s = int(getattr(fw, "value", getattr(fw, "x", 5))) + 1
            if wait_s > 60:
                raise
            await asyncio.sleep(wait_s)
        except Exception:
            raise


async def verify_group_and_permissions(
    client: Client,
    message: Message,
    required_bot_perm: Optional[str] = None,
    required_user_perm: Optional[str] = None,
    require_bot_admin: bool = True,
    allow_channel: bool = True,
) -> Tuple[bool, Optional[str], Optional[types.ChatMember], Optional[types.ChatMember]]:
    allowed_types = (
        (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL)
        if allow_channel
        else (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
    )
    if message.chat.type not in allowed_types:
        await message.reply_text(
            "⚠️ <b>ɢʀᴏᴜᴘ / ᴄʜᴀɴɴᴇʟ ᴄᴏᴍᴍᴀɴᴅ!</b>\n"
            "<i>ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴍᴜsᴛ ʙᴇ ᴜsᴇᴅ ɪɴsɪᴅᴇ ᴀ ᴛᴇʟᴇɢʀᴀᴍ ɢʀᴏᴜᴘ ᴏʀ ᴄʜᴀɴɴᴇʟ ᴡʜᴇʀᴇ ɪ ᴀᴍ ᴀɴ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ (ᴏʀ ᴜsᴇ <code>/connect</code> ғᴏʀ ᴄʜᴀɴɴᴇʟs).</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, "not_group", None, None

    chat_id = message.chat.id
    user = message.from_user

    is_anon_chat_admin = bool(
        message.sender_chat and message.sender_chat.id == chat_id
    )
    user_id = user.id if user else 0
    is_global_admin = is_admin_sync(user_id)

    bot_member: Optional[types.ChatMember] = None
    try:
        bot_member = await client.get_chat_member(chat_id, "self")
    except Exception as e:
        if require_bot_admin:
            await message.reply_text(
                f"❌ <b>ᴄᴀɴɴᴏᴛ ᴠᴇʀɪғʏ ʙᴏᴛ ᴀᴅᴍɪɴ sᴛᴀᴛᴜs:</b> <code>{html.escape(str(e))}</code>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            return False, "bot_check_failed", None, None

    if require_bot_admin:
        if not bot_member or bot_member.status not in (
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        ):
            await message.reply_text(
                "❌ <b>ʙᴏᴛ ɪs ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ!</b>\n\n"
                "👉 <i>ᴘʟᴇᴀsᴇ ᴘʀᴏᴍᴏᴛᴇ ᴍᴇ ᴀs ᴀɴ ᴀᴅᴍɪɴ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ ᴡɪᴛʜ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ᴘᴇʀᴍɪssɪᴏɴs ғɪʀsᴛ.</i>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            return False, "bot_not_admin", bot_member, None

        if required_bot_perm and bot_member.privileges:
            if not getattr(bot_member.privileges, required_bot_perm, False):
                perm_label = required_bot_perm.replace("can_", "").replace("_", " ").upper()
                await message.reply_text(
                    f"❌ <b>ᴍɪssɪɴɢ ʙᴏᴛ ᴘᴇʀᴍɪssɪᴏɴ:</b> <code>{perm_label}</code>\n"
                    "<i>ᴘʟᴇᴀsᴇ ᴇɴᴀʙʟᴇ ᴛʜɪs ᴘᴇʀᴍɪssɪᴏɴ ғᴏʀ ᴛʜᴇ ʙᴏᴛ ɪɴ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ sᴇᴛᴛɪɴɢs.</i>",
                    quote=True,
                    parse_mode=enums.ParseMode.HTML,
                )
                return False, "bot_missing_perm", bot_member, None

    if is_global_admin or is_anon_chat_admin or user_id in (777000, 1087968824):
        return True, None, bot_member, None

    if not user:
        await message.reply_text(
            "⛔ <b>ᴀɴᴏɴʏᴍᴏᴜs sᴇɴᴅᴇʀ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, "anon_denied", bot_member, None

    try:
        user_member = await client.get_chat_member(chat_id, user_id)
    except Exception:
        user_member = None

    if not user_member or user_member.status not in (
        enums.ChatMemberStatus.OWNER,
        enums.ChatMemberStatus.ADMINISTRATOR,
    ):
        await message.reply_text(
            "⛔ <b>ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ!</b>\n"
            "<i>ᴏɴʟʏ ᴛʜᴇ ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ, ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs, ᴏʀ ᴍᴍᴡ ʙᴏᴛᴢ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False, "user_not_admin", bot_member, user_member

    if user_member.status == enums.ChatMemberStatus.OWNER:
        return True, None, bot_member, user_member

    if required_user_perm and user_member.privileges:
        if not getattr(user_member.privileges, required_user_perm, False):
            perm_label = required_user_perm.replace("can_", "").replace("_", " ").upper()
            await message.reply_text(
                f"⛔ <b>ɪɴsᴜғғɪᴄɪᴇɴᴛ ᴀᴅᴍɪɴ ʀɪɢʜᴛs!</b>\n"
                f"<i>ʏᴏᴜ ɴᴇᴇᴅ <code>{perm_label}</code> ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ ʀᴜɴ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ.</i>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            return False, "user_missing_perm", bot_member, user_member

    return True, None, bot_member, user_member


async def is_user_group_or_global_admin(client: Client, chat_id: int, user_id: int) -> bool:
    if not user_id:
        return False
    if is_admin_sync(user_id) or user_id in (777000, 1087968824):
        return True
    try:
        mem = await client.get_chat_member(chat_id, user_id)
        return mem.status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


async def resolve_target_user_and_reason(
    client: Client,
    message: Message,
) -> Tuple[Optional[types.User], str]:
    reason = ""
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        if message.text and len(message.text.split(None, 1)) > 1:
            reason = message.text.split(None, 1)[1].strip()
        return target_user, reason

    if not message.command or len(message.command) < 2:
        return None, ""

    parts = message.text.split(None, 2)
    target_arg = parts[1].strip()
    if len(parts) > 2:
        reason = parts[2].strip()

    if (
        message.entities
        and len(message.entities) > 1
        and message.entities[1].type == enums.MessageEntityType.TEXT_MENTION
        and message.entities[1].user
    ):
        return message.entities[1].user, reason

    try:
        uid_or_uname: Union[int, str] = int(target_arg) if target_arg.lstrip("-").isdigit() else target_arg
        user_obj = await client.get_users(uid_or_uname)
        if isinstance(user_obj, list):
            user_obj = user_obj[0] if user_obj else None
        return user_obj, reason
    except Exception:
        return None, reason


async def validate_moderation_target(
    client: Client,
    message: Message,
    target_user: Optional[types.User],
    allow_admin_target: bool = False,
) -> bool:
    if not target_user:
        await message.reply_text(
            "⚠️ <b>ᴛᴀʀɢᴇᴛ ᴜsᴇʀ ᴍɪssɪɴɢ!</b>\n"
            "<i>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ's ᴍᴇssᴀɢᴇ ᴏʀ ᴘʀᴏᴠɪᴅᴇ <code>@username</code> / <code>user_id</code>.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False

    if target_user.id == temp.ME:
        await message.reply_text(
            "🤖 <b>ɪ ᴄᴀɴɴᴏᴛ ᴘᴇʀғᴏʀᴍ ᴛʜɪs ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ᴀᴄᴛɪᴏɴ ᴏɴ ᴍʏsᴇʟғ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False

    if not allow_admin_target and is_admin_sync(target_user.id):
        await message.reply_text(
            "🛡️ <b>ᴛᴀʀɢᴇᴛ ɪs ᴀ ɢʟᴏʙᴀʟ ᴍᴍᴡ ʙᴏᴛᴢ ᴀᴅᴍɪɴ ᴀɴᴅ ᴄᴀɴɴᴏᴛ ʙᴇ ʀᴇsᴛʀɪᴄᴛᴇᴅ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return False

    try:
        t_mem = await client.get_chat_member(message.chat.id, target_user.id)
        if t_mem.status == enums.ChatMemberStatus.OWNER:
            await message.reply_text(
                "👑 <b>ᴛʜᴇ ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ ᴄᴀɴɴᴏᴛ ʙᴇ ᴍᴏᴅᴇʀᴀᴛᴇᴅ ᴏʀ ᴅᴇᴍᴏᴛᴇᴅ!</b>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            return False
        if not allow_admin_target and t_mem.status == enums.ChatMemberStatus.ADMINISTRATOR:
            await message.reply_text(
                "🛡️ <b>ᴛᴀʀɢᴇᴛ ɪs ᴀ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ!</b>\n"
                "<i>ᴅᴇᴍᴏᴛᴇ ᴛʜᴇᴍ ғɪʀsᴛ ᴡɪᴛʜ <code>/demote</code> ɪғ ʏᴏᴜ ᴡɪsʜ ᴛᴏ ʀᴇsᴛʀɪᴄᴛ ᴛʜᴇᴍ.</i>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            return False
    except UserNotParticipant:
        pass
    except Exception:
        pass

    return True


async def dispatch_mod_log(
    client: Client,
    chat: types.Chat,
    action_title: str,
    admin_user: Optional[types.User],
    target_user: Optional[types.User] = None,
    reason: str = "",
    extra: str = "",
) -> None:
    admin_id = admin_user.id if admin_user else 0
    target_id = target_user.id if target_user else 0
    await db.record_mod_log(
        chat_id=chat.id,
        action=action_title,
        admin_id=admin_id,
        target_id=target_id,
        details=reason or extra,
    )

    cfg = await db.get_helpdesk_config(chat.id)
    log_ch = int(cfg.get("log_channel") or LOG_CHANNEL or 0)
    if not log_ch:
        return

    admin_mention = admin_user.mention if admin_user else "ᴀɴᴏɴʏᴍᴏᴜs ᴀᴅᴍɪɴ"
    target_line = (
        f"• <b>ᴛᴀʀɢᴇᴛ:</b> {target_user.mention} (<code>{target_user.id}</code>)\n"
        if target_user
        else ""
    )
    reason_line = f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>\n" if reason else ""
    extra_line = f"• <b>ᴅᴇᴛᴀɪʟs:</b> {extra}\n" if extra else ""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    log_text = (
        f"🛡️ <b>ᴍᴍᴡ ʙᴏᴛᴢ ʜᴇʟᴘᴅᴇsᴋ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴀᴄᴛɪᴏɴ:</b> <code>{html.escape(action_title)}</code>\n"
        f"• <b>ɢʀᴏᴜᴘ:</b> <b>{html.escape(chat.title or 'Private Group')}</b> (<code>{chat.id}</code>)\n"
        f"• <b>ᴀᴅᴍɪɴ:</b> {admin_mention} (<code>{admin_id}</code>)\n"
        f"{target_line}{reason_line}{extra_line}"
        f"• <b>ᴛɪᴍᴇ:</b> <code>{now_str}</code>"
    )
    try:
        await client.send_message(log_ch, log_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.debug("Moderation log send notice for %s: %s", log_ch, e)


@Client.on_message(filters.command("promote", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_promote_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_promote_members", "can_promote_members"
    )
    if not ok:
        return

    target, custom_title = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target, allow_admin_target=True):
        return

    try:
        await safe_tg_call(
            client.promote_chat_member,
            chat_id=message.chat.id,
            user_id=target.id,
            privileges=PROMOTE_STD_PRIVS,
        )
        if custom_title:
            try:
                await safe_tg_call(
                    client.set_administrator_title,
                    message.chat.id,
                    target.id,
                    custom_title[:16],
                )
            except Exception:
                pass
        await message.reply_text(
            f"👮 <b>ᴀᴅᴍɪɴ ᴘʀᴏᴍᴏᴛɪᴏɴ sᴜᴄᴄᴇssғᴜʟ!</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ʀᴏʟᴇ:</b> <code>sᴛᴀɴᴅᴀʀᴅ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ</code>\n"
            f"• <b>ᴛɪᴛʟᴇ:</b> <code>{html.escape(custom_title[:16] if custom_title else 'ᴀᴅᴍɪɴ')}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "PROMOTE", message.from_user, target, custom_title)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴘʀᴏᴍᴏᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("fullpromote", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_fullpromote_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_promote_members", "can_promote_members"
    )
    if not ok:
        return

    target, custom_title = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target, allow_admin_target=True):
        return

    try:
        await safe_tg_call(
            client.promote_chat_member,
            chat_id=message.chat.id,
            user_id=target.id,
            privileges=PROMOTE_FULL_PRIVS,
        )
        if custom_title:
            try:
                await safe_tg_call(
                    client.set_administrator_title,
                    message.chat.id,
                    target.id,
                    custom_title[:16],
                )
            except Exception:
                pass
        await message.reply_text(
            f"👑 <b>ғᴜʟʟ ᴀᴅᴍɪɴ ᴘʀᴏᴍᴏᴛɪᴏɴ sᴜᴄᴄᴇssғᴜʟ!</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ʀᴏʟᴇ:</b> <code>ғᴜʟʟ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ (ᴀʟʟ ʀɪɢʜᴛs)</code>\n"
            f"• <b>ᴛɪᴛʟᴇ:</b> <code>{html.escape(custom_title[:16] if custom_title else 'sᴇɴɪᴏʀ ᴀᴅᴍɪɴ')}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "FULL_PROMOTE", message.from_user, target, custom_title)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴜʟʟ ᴘʀᴏᴍᴏᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("demote", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_demote_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_promote_members", "can_promote_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target, allow_admin_target=True):
        return

    try:
        await safe_tg_call(
            client.promote_chat_member,
            chat_id=message.chat.id,
            user_id=target.id,
            privileges=DEMOTE_PRIVS,
        )
        await message.reply_text(
            f"✅ <b>ᴀᴅᴍɪɴ ᴅᴇᴍᴏᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>sᴛᴀᴛᴜs:</b> <code>ᴀʟʟ ᴀᴅᴍɪɴ ᴘʀɪᴠɪʟᴇɢᴇs ʀᴇᴍᴏᴠᴇᴅ</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "DEMOTE", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴅᴇᴍᴏᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["title", "settitle"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_set_title_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_promote_members", "can_promote_members"
    )
    if not ok:
        return

    target, custom_title = await resolve_target_user_and_reason(client, message)
    if not target or not custom_title:
        return await message.reply_text(
            "🏷️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀɴ ᴀᴅᴍɪɴ ᴡɪᴛʜ <code>/title &lt;custom title&gt;</code> ᴏʀ <code>/title @admin &lt;title&gt;</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        await safe_tg_call(client.set_administrator_title, message.chat.id, target.id, custom_title[:16])
        await message.reply_text(
            f"🏷️ <b>ᴀᴅᴍɪɴ ᴛɪᴛʟᴇ ᴜᴘᴅᴀᴛᴇᴅ!</b>\n"
            f"• <b>ᴀᴅᴍɪɴ:</b> {target.mention}\n"
            f"• <b>ɴᴇᴡ ᴛɪᴛʟᴇ:</b> <code>{html.escape(custom_title[:16])}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "SET_ADMIN_TITLE", message.from_user, target, custom_title[:16])
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ ᴛᴏ sᴇᴛ ᴛɪᴛʟᴇ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("pin", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_pin_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_pin_messages", "can_pin_messages"
    )
    if not ok:
        return

    if not message.reply_to_message:
        return await message.reply_text(
            "📌 <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/pin</code> (ᴏʀ <code>/pin loud</code> ᴛᴏ ɴᴏᴛɪғʏ ᴍᴇᴍʙᴇʀs).",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    args = [a.lower() for a in message.command[1:]]
    loud = any(x in args for x in ("loud", "notify", "bell"))
    try:
        await safe_tg_call(
            message.reply_to_message.pin,
            disable_notification=not loud,
        )
        await message.reply_text(
            f"📌 <b>ᴍᴇssᴀɢᴇ ᴘɪɴɴᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b> ({'🔔 ɴᴏᴛɪғɪᴇᴅ' if loud else '🔕 sɪʟᴇɴᴛ'})",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "PIN_MESSAGE", message.from_user, extra=f"Message ID: {message.reply_to_message.id}")
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴘɪɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("unpin", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_unpin_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_pin_messages", "can_pin_messages"
    )
    if not ok:
        return

    try:
        if message.reply_to_message:
            await safe_tg_call(message.reply_to_message.unpin)
        else:
            chat_info = await client.get_chat(message.chat.id)
            if chat_info.pinned_message:
                await safe_tg_call(client.unpin_chat_message, message.chat.id, chat_info.pinned_message.id)
            else:
                return await message.reply_text("ℹ️ <b>ɴᴏ ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ ғᴏᴜɴᴅ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await message.reply_text("📌 <b>ᴍᴇssᴀɢᴇ ᴜɴᴘɪɴɴᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await dispatch_mod_log(client, message.chat, "UNPIN_MESSAGE", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴜɴᴘɪɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("unpinall", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_unpinall_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_pin_messages", "can_pin_messages"
    )
    if not ok:
        return

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ʏᴇs, ᴜɴᴘɪɴ ᴀʟʟ", callback_data=f"hdesk{HD_SEP}confirm_unpinall"),
            InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="close_data"),
        ]
    ])
    await message.reply_text(
        "⚠️ <b>ᴄᴏɴғɪʀᴍ ᴜɴᴘɪɴ ᴀʟʟ ᴍᴇssᴀɢᴇs?</b>\n"
        "<i>ᴛʜɪs ᴡɪʟʟ ᴜɴᴘɪɴ ᴇᴠᴇʀʏ ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</i>",
        reply_markup=btn,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["adminlist", "staff", "admins"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_adminlist_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    wait = await message.reply_text("🔎 <i>ғᴇᴛᴄʜɪɴɢ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs...</i>", quote=True, parse_mode=enums.ParseMode.HTML)
    owners: List[str] = []
    admins: List[str] = []
    bots: List[str] = []

    try:
        async for member in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
            u = member.user
            if not u:
                continue
            title_tag = f" [<code>{html.escape(member.custom_title)}</code>]" if member.custom_title else ""
            if member.status == enums.ChatMemberStatus.OWNER:
                owners.append(f"👑 {u.mention}{title_tag}")
            elif u.is_bot:
                bots.append(f"🤖 {u.mention}{title_tag}")
            else:
                admins.append(f"👮 {u.mention}{title_tag}")

        lines = [
            f"🛡️ <b>ɢʀᴏᴜᴘ sᴛᴀғғ &amp; ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs</b>\n"
            f"🏷️ <b>ᴄʜᴀᴛ:</b> <code>{html.escape(message.chat.title or 'Group')}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]
        if owners:
            lines.append("<b>👑 ᴏᴡɴᴇʀ:</b>\n" + "\n".join(owners))
        if admins:
            lines.append(f"\n<b>👮 ᴀᴅᴍɪɴs ({len(admins)}):</b>\n" + "\n".join(admins))
        if bots:
            lines.append(f"\n<b>🤖 ᴀᴅᴍɪɴ ʙᴏᴛs ({len(bots)}):</b>\n" + "\n".join(bots))
        lines.append(f"\n📊 <b>ᴛᴏᴛᴀʟ sᴛᴀғғ:</b> <code>{len(owners) + len(admins) + len(bots)}</code>")

        await wait.edit_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await wait.edit_text(f"❌ <b>ғᴀɪʟᴇᴅ ᴛᴏ ғᴇᴛᴄʜ ᴀᴅᴍɪɴ ʟɪsᴛ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setgtitle", "setgrouptitle"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setgtitle_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_change_info", "can_change_info")
    if not ok:
        return
    if len(message.command) < 2:
        return await message.reply_text("🏷️ <b>ᴜsᴀɢᴇ:</b> <code>/setgtitle New Group Name</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    new_title = message.text.split(None, 1)[1].strip()[:128]
    try:
        await safe_tg_call(client.set_chat_title, message.chat.id, new_title)
        await message.reply_text(f"✅ <b>ɢʀᴏᴜᴘ ᴛɪᴛʟᴇ ᴄʜᴀɴɢᴇᴅ ᴛᴏ:</b> <code>{html.escape(new_title)}</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        await dispatch_mod_log(client, message.chat, "SET_GROUP_TITLE", message.from_user, extra=new_title)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setgdesc", "setdesc", "setdescription"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setgdesc_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_change_info", "can_change_info")
    if not ok:
        return
    new_desc = message.text.split(None, 1)[1].strip()[:255] if len(message.command) >= 2 else ""
    try:
        await safe_tg_call(client.set_chat_description, message.chat.id, new_desc)
        await message.reply_text("✅ <b>ɢʀᴏᴜᴘ ᴅᴇsᴄʀɪᴘᴛɪᴏɴ ᴜᴘᴅᴀᴛᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await dispatch_mod_log(client, message.chat, "SET_GROUP_DESCRIPTION", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setgpic", "setgphoto"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setgpic_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_change_info", "can_change_info")
    if not ok:
        return
    if not message.reply_to_message or not (message.reply_to_message.photo or message.reply_to_message.document):
        return await message.reply_text("🖼️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴡɪᴛʜ <code>/setgpic</code>.", quote=True, parse_mode=enums.ParseMode.HTML)
    photo_path = None
    try:
        photo_path = await client.download_media(message.reply_to_message)
        await safe_tg_call(client.set_chat_photo, message.chat.id, photo=photo_path)
        await message.reply_text("✅ <b>ɢʀᴏᴜᴘ ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ ᴜᴘᴅᴀᴛᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await dispatch_mod_log(client, message.chat, "SET_GROUP_PHOTO", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    finally:
        if photo_path and os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except Exception:
                pass


@Client.on_message(filters.command(["delgpic", "rmgpic", "delgphoto"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_delgpic_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_change_info", "can_change_info")
    if not ok:
        return
    try:
        await safe_tg_call(client.delete_chat_photo, message.chat.id)
        await message.reply_text("🗑️ <b>ɢʀᴏᴜᴘ ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ ʀᴇᴍᴏᴠᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await dispatch_mod_log(client, message.chat, "DELETE_GROUP_PHOTO", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("slowmode", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_slowmode_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_manage_chat", "can_manage_chat")
    if not ok:
        return
    if len(message.command) < 2:
        return await message.reply_text(
            "⏱️ <b>ᴜsᴀɢᴇ:</b> <code>/slowmode &lt;0|10|30|60|300|900|3600&gt;</code> (sᴇᴄᴏɴᴅs, <code>off</code> ᴛᴏ ᴅɪsᴀʙʟᴇ)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].lower().strip()
    secs = 0 if arg in ("off", "0", "disable") else (int(arg) if arg.isdigit() else -1)
    if secs not in (0, 10, 30, 60, 300, 900, 3600):
        return await message.reply_text(
            "⚠️ <b>ᴛᴇʟᴇɢʀᴀᴍ sᴜᴘᴘᴏʀᴛs sʟᴏᴡᴍᴏᴅᴇ ᴠᴀʟᴜᴇs:</b> <code>0, 10, 30, 60, 300, 900, 3600</code> sᴇᴄᴏɴᴅs.",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        if hasattr(client, "set_slow_mode"):
            await safe_tg_call(client.set_slow_mode, message.chat.id, secs)
            await message.reply_text(
                f"⏱️ <b>sʟᴏᴡ ᴍᴏᴅᴇ sᴇᴛ ᴛᴏ:</b> <code>{secs}s</code>" if secs else "⏱️ <b>sʟᴏᴡ ᴍᴏᴅᴇ ᴅɪsᴀʙʟᴇᴅ!</b>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            await dispatch_mod_log(client, message.chat, "SLOWMODE", message.from_user, extra=f"{secs}s")
        else:
            await message.reply_text(
                "ℹ️ <b>sʟᴏᴡ ᴍᴏᴅᴇ ɴᴏᴛɪᴄᴇ:</b> ᴜsᴇ <code>/setflood 5 5 mute</code> ғᴏʀ ɪɴsᴛᴀɴᴛ ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ.",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        await message.reply_text(f"❌ <b>sʟᴏᴡ ᴍᴏᴅᴇ ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["invitelink", "glink", "exportlink"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_invitelink_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_invite_users", "can_invite_users")
    if not ok:
        return
    mode = message.command[1].lower() if len(message.command) >= 2 else "normal"
    try:
        if mode in ("request", "req", "joinreq"):
            inv = await safe_tg_call(
                client.create_chat_invite_link,
                message.chat.id,
                creates_join_request=True,
                name="MMW Helpdesk Approval Link",
            )
            link_url = inv.invite_link
            lbl = "🔐 ᴊᴏɪɴ-ʀᴇǫᴜᴇsᴛ ɪɴᴠɪᴛᴇ ʟɪɴᴋ"
        else:
            link_url = await safe_tg_call(client.export_chat_invite_link, message.chat.id)
            lbl = "🔗 ɢʀᴏᴜᴘ ɪɴᴠɪᴛᴇ ʟɪɴᴋ"
        await message.reply_text(
            f"{lbl}:\n<code>{html.escape(link_url)}</code>\n\n"
            "💡 <i>ᴛɪᴘ: ᴜsᴇ <code>/invitelink request</code> ᴛᴏ ᴄʀᴇᴀᴛᴇ ᴀ ᴊᴏɪɴ-ʀᴇǫᴜᴇsᴛ ʟɪɴᴋ ᴏʀ <code>/revokelink</code> ᴛᴏ ʀᴇᴠᴏᴋᴇ.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        await dispatch_mod_log(client, message.chat, "EXPORT_INVITE_LINK", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("revokelink", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_revokelink_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_invite_users", "can_invite_users")
    if not ok:
        return
    try:
        new_link = await safe_tg_call(client.export_chat_invite_link, message.chat.id)
        await message.reply_text(
            f"♻️ <b>ᴘʀᴇᴠɪᴏᴜs ɪɴᴠɪᴛᴇ ʟɪɴᴋ ʀᴇᴠᴏᴋᴇᴅ!</b>\n"
            f"🔗 <b>ɴᴇᴡ ʟɪɴᴋ:</b> <code>{html.escape(new_link)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        await dispatch_mod_log(client, message.chat, "REVOKE_INVITE_LINK", message.from_user)
    except Exception as e:
        await message.reply_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("ban", prefixes=COMMAND_PREFIXES) & (filters.group | filters.channel))
async def hd_ban_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    dur_secs, dur_label = 0, ""
    if reason:
        r_parts = reason.split(None, 1)
        dur_secs, dur_label = parse_duration_arg(r_parts[0])
        if dur_secs:
            reason = r_parts[1] if len(r_parts) > 1 else f"Timed ban ({dur_label})"
    try:
        if dur_secs:
            until_dt = datetime.now() + timedelta(seconds=dur_secs)
            await safe_tg_call(client.ban_chat_member, message.chat.id, target.id, until_date=until_dt)
        else:
            await safe_tg_call(client.ban_chat_member, message.chat.id, target.id)
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 ᴜɴʙᴀɴ ᴜsᴇʀ", callback_data=f"hdesk{HD_SEP}unban{HD_SEP}{target.id}")]
        ])
        dur_line = f"• <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_label}</code>\n" if dur_label else ""
        await message.reply_text(
            f"🚫 <b>ᴍᴇᴍʙᴇʀ ʙᴀɴɴᴇᴅ!</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"{dur_line}"
            f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason or 'ᴠɪᴏʟᴀᴛɪᴏɴ ᴏғ ɢʀᴏᴜᴘ ʀᴜʟᴇs')}</code>",
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"BAN ({dur_label})" if dur_label else "BAN", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("tban", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_tban_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, rem = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    parts = rem.split(None, 1) if rem else []
    dur_str = parts[0] if parts else "1h"
    reason = parts[1] if len(parts) > 1 else "Temporary Ban"
    secs, dur_label = parse_duration_arg(dur_str)
    if not secs:
        return await message.reply_text(
            "⏳ <b>ᴜsᴀɢᴇ:</b> <code>/tban @user 1h reason</code> (sᴜᴘᴘᴏʀᴛs <code>10m</code>, <code>2h</code>, <code>7d</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        until_dt = datetime.now() + timedelta(seconds=secs)
        await safe_tg_call(client.ban_chat_member, message.chat.id, target.id, until_date=until_dt)
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 ᴜɴʙᴀɴ ɴᴏᴡ", callback_data=f"hdesk{HD_SEP}unban{HD_SEP}{target.id}")]
        ])
        await message.reply_text(
            f"⏳ <b>ᴛᴇᴍᴘᴏʀᴀʀʏ ʙᴀɴ ᴀᴘᴘʟɪᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_label}</code>\n"
            f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>",
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"TEMP_BAN ({dur_label})", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴛʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("unban", prefixes=COMMAND_PREFIXES) & (filters.group | filters.channel))
async def hd_unban_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not target:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ <code>/unban @user</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        await safe_tg_call(client.unban_chat_member, message.chat.id, target.id)
        await message.reply_text(
            f"🔓 <b>ᴍᴇᴍʙᴇʀ ᴜɴʙᴀɴɴᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "UNBAN", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴜɴʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["kick", "punch"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_kick_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    try:
        await safe_tg_call(client.ban_chat_member, message.chat.id, target.id)
        await asyncio.sleep(0.5)
        await safe_tg_call(client.unban_chat_member, message.chat.id, target.id)
        await message.reply_text(
            f"🥾 <b>ᴍᴇᴍʙᴇʀ ᴋɪᴄᴋᴇᴅ ғʀᴏᴍ ɢʀᴏᴜᴘ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason or 'ᴋɪᴄᴋᴇᴅ ʙʏ ᴀᴅᴍɪɴ')}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "KICK", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴋɪᴄᴋ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("mute", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_mute_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    dur_secs, dur_label = 0, ""
    if reason:
        r_parts = reason.split(None, 1)
        dur_secs, dur_label = parse_duration_arg(r_parts[0])
        if dur_secs:
            reason = r_parts[1] if len(r_parts) > 1 else f"Timed mute ({dur_label})"
    try:
        if dur_secs:
            until_dt = datetime.now() + timedelta(seconds=dur_secs)
            await safe_tg_call(
                client.restrict_chat_member,
                message.chat.id,
                target.id,
                permissions=MUTE_PERMS,
                until_date=until_dt,
            )
        else:
            await safe_tg_call(client.restrict_chat_member, message.chat.id, target.id, permissions=MUTE_PERMS)
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔊 ᴜɴᴍᴜᴛᴇ ᴜsᴇʀ", callback_data=f"hdesk{HD_SEP}unmute{HD_SEP}{target.id}")]
        ])
        dur_line = f"• <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_label}</code>\n" if dur_label else ""
        await message.reply_text(
            f"🔇 <b>ᴍᴇᴍʙᴇʀ ᴍᴜᴛᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"{dur_line}"
            f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason or 'ᴍᴜᴛᴇᴅ ʙʏ ᴀᴅᴍɪɴ')}</code>",
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"MUTE ({dur_label})" if dur_label else "MUTE", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("tmute", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_tmute_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, rem = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    parts = rem.split(None, 1) if rem else []
    dur_str = parts[0] if parts else "1h"
    reason = parts[1] if len(parts) > 1 else "Temporary Mute"
    secs, dur_label = parse_duration_arg(dur_str)
    if not secs:
        return await message.reply_text(
            "⏳ <b>ᴜsᴀɢᴇ:</b> <code>/tmute @user 30m reason</code> (sᴜᴘᴘᴏʀᴛs <code>10m</code>, <code>2h</code>, <code>1d</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        until_dt = datetime.now() + timedelta(seconds=secs)
        await safe_tg_call(
            client.restrict_chat_member,
            message.chat.id,
            target.id,
            permissions=MUTE_PERMS,
            until_date=until_dt,
        )
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔊 ᴜɴᴍᴜᴛᴇ ɴᴏᴡ", callback_data=f"hdesk{HD_SEP}unmute{HD_SEP}{target.id}")]
        ])
        await message.reply_text(
            f"🔇 <b>ᴛᴇᴍᴘᴏʀᴀʀʏ ᴍᴜᴛᴇ ᴀᴘᴘʟɪᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_label}</code>\n"
            f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>",
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"TEMP_MUTE ({dur_label})", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴛᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["unmute", "unrestrict"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_unmute_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not target:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ <code>/unmute @user</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        await safe_tg_call(client.restrict_chat_member, message.chat.id, target.id, permissions=UNMUTE_PERMS)
        await message.reply_text(
            f"🔊 <b>ᴍᴇᴍʙᴇʀ ᴜɴᴍᴜᴛᴇᴅ / ᴜɴʀᴇsᴛʀɪᴄᴛᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "UNMUTE_UNRESTRICT", message.from_user, target, reason)
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴜɴᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("restrict", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_restrict_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, rem = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    secs, dur_label = parse_duration_arg(rem.split()[0] if rem else "")
    until_dt = (datetime.now() + timedelta(seconds=secs)) if secs else None
    try:
        if until_dt:
            await safe_tg_call(
                client.restrict_chat_member,
                message.chat.id,
                target.id,
                permissions=RESTRICT_MEDIA_PERMS,
                until_date=until_dt,
            )
        else:
            await safe_tg_call(
                client.restrict_chat_member,
                message.chat.id,
                target.id,
                permissions=RESTRICT_MEDIA_PERMS,
            )
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 ᴜɴʀᴇsᴛʀɪᴄᴛ", callback_data=f"hdesk{HD_SEP}unmute{HD_SEP}{target.id}")]
        ])
        await message.reply_text(
            f"🔒 <b>ᴍᴇᴅɪᴀ &amp; ʟɪɴᴋ ʀᴇsᴛʀɪᴄᴛɪᴏɴ ᴀᴘᴘʟɪᴇᴅ!</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>sᴛᴀᴛᴜs:</b> <code>ᴛᴇxᴛ-ᴏɴʟʏ (ɴᴏ ᴍᴇᴅɪᴀ/sᴛɪᴄᴋᴇʀs/ʟɪɴᴋs)</code>"
            + (f"\n• <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_label}</code>" if dur_label else ""),
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "RESTRICT_MEDIA", message.from_user, target, dur_label)
    except Exception as e:
        await message.reply_text(f"❌ <b>ʀᴇsᴛʀɪᴄᴛ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["approve", "trust"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_approve_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, _ = await resolve_target_user_and_reason(client, message)
    if not target:
        return await message.reply_text(
            "✅ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ <code>/approve @user</code>\n"
            "<i>ᴀᴘᴘʀᴏᴠᴇs ᴀ ᴘᴇɴᴅɪɴɢ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴀɴᴅ ᴇxᴇᴍᴘᴛs ᴛʜᴇ ᴜsᴇʀ ғʀᴏᴍ ɢʀᴏᴜᴘ ʟᴏᴄᴋs &amp; ᴀɴᴛɪ-ғʟᴏᴏᴅ.</i>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        await client.approve_chat_join_request(message.chat.id, target.id)
    except Exception:
        pass

    await db.add_approved_user(message.chat.id, target.id)
    await message.reply_text(
        f"✅ <b>ᴜsᴇʀ ᴀᴘᴘʀᴏᴠᴇᴅ &amp; ᴡʜɪᴛᴇʟɪsᴛᴇᴅ!</b>\n"
        f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
        f"• <b>ᴘʀɪᴠɪʟᴇɢᴇ:</b> <code>ᴇxᴇᴍᴘᴛ ғʀᴏᴍ ʟᴏᴄᴋs, ᴀɴᴛɪ-ғʟᴏᴏᴅ &amp; ᴀɴᴛɪ-sᴘᴀᴍ</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, "APPROVE_USER", message.from_user, target)


@Client.on_message(filters.command(["unapprove", "untrust", "decline"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_unapprove_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, _ = await resolve_target_user_and_reason(client, message)
    if not target:
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ <code>/unapprove @user</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    try:
        await client.decline_chat_join_request(message.chat.id, target.id)
    except Exception:
        pass

    await db.remove_approved_user(message.chat.id, target.id)
    await message.reply_text(
        f"🚫 <b>ᴜsᴇʀ ᴜɴᴀᴘᴘʀᴏᴠᴇᴅ / ᴅᴇᴄʟɪɴᴇᴅ!</b>\n"
        f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, "UNAPPROVE_USER", message.from_user, target)


@Client.on_message(filters.command(["approved", "trusted"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_approved_list_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    cfg = await db.get_helpdesk_config(message.chat.id)
    approved = cfg.get("approved_users", [])
    if not approved:
        return await message.reply_text("📭 <b>ɴᴏ ᴀᴘᴘʀᴏᴠᴇᴅ/ᴡʜɪᴛᴇʟɪsᴛᴇᴅ ᴜsᴇʀs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    lines = [f"✅ <b>ᴀᴘᴘʀᴏᴠᴇᴅ ᴍᴇᴍʙᴇʀs ({len(approved)})</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for idx, uid in enumerate(approved[:40], 1):
        lines.append(f"{idx}. <a href='tg://user?id={uid}'>ᴜsᴇʀ {uid}</a> (<code>{uid}</code>)")
    await message.reply_text("\n".join(lines), quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("del", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_del_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_delete_messages", "can_delete_messages"
    )
    if not ok:
        return
    if not message.reply_to_message:
        return await message.reply_text("🗑️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/del</code> ᴛᴏ ᴅᴇʟᴇᴛᴇ ɪᴛ.", quote=True, parse_mode=enums.ParseMode.HTML)
    try:
        await client.delete_messages(message.chat.id, [message.reply_to_message.id, message.id])
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴅᴇʟᴇᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("purge", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_purge_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_delete_messages", "can_delete_messages"
    )
    if not ok:
        return

    if not message.reply_to_message:
        return await message.reply_text(
            "🧹 <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴏʟᴅᴇsᴛ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴅᴇʟᴇᴛᴇ ᴡɪᴛʜ <code>/purge</code> (ᴏʀ <code>/purge 20</code>).",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    start_id = message.reply_to_message.id
    end_id = message.id
    if len(message.command) >= 2 and message.command[1].isdigit():
        count_req = min(200, max(1, int(message.command[1])))
        end_id = min(end_id, start_id + count_req)

    total_span = max(1, end_id - start_id + 1)
    if total_span > 25:
        pid = f"{message.chat.id}_{start_id}_{end_id}"
        _PENDING_PURGES[pid] = {
            "chat_id": message.chat.id,
            "start_id": start_id,
            "end_id": end_id,
            "admin_id": message.from_user.id if message.from_user else 0,
        }
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ ᴘᴜʀɢᴇ {min(total_span, 200)} ᴍsɢs", callback_data=f"hdesk{HD_SEP}confirm_purge{HD_SEP}{pid}"),
                InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="close_data"),
            ]
        ])
        return await message.reply_text(
            f"⚠️ <b>ᴄᴏɴғɪʀᴍ ʙᴜʟᴋ ᴘᴜʀɢᴇ?</b>\n"
            f"• <b>ʀᴀɴɢᴇ:</b> <code>{start_id} → {end_id}</code> (ᴜᴘ ᴛᴏ <code>{min(total_span, 200)}</code> ᴍᴇssᴀɢᴇs)",
            reply_markup=btn,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    ids = list(range(start_id, end_id + 1))[:100]
    try:
        await client.delete_messages(message.chat.id, ids)
        done = await client.send_message(
            message.chat.id,
            f"🧹 <b>ᴘᴜʀɢᴇᴅ <code>{len(ids)}</code> ᴍᴇssᴀɢᴇs!</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, "PURGE", message.from_user, extra=f"{len(ids)} messages")
        await asyncio.sleep(4)
        await done.delete()
    except Exception as e:
        await message.reply_text(f"❌ <b>ᴘᴜʀɢᴇ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("warn", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_warn_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    target, reason = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target):
        return

    reason = reason or "Violation of group rules"
    admin_id = message.from_user.id if message.from_user else 0
    count, _ = await db.add_warning(message.chat.id, target.id, reason, admin_id)
    cfg = await db.get_helpdesk_config(message.chat.id)
    limit = int(cfg.get("warn_limit", 3) or 3)
    action = str(cfg.get("warn_action", "ban")).lower()

    if count >= limit:
        await db.reset_warnings(message.chat.id, target.id)
        try:
            if action == "mute":
                await safe_tg_call(client.restrict_chat_member, message.chat.id, target.id, permissions=MUTE_PERMS)
                act_str = "🔇 ᴍᴜᴛᴇᴅ"
            elif action == "kick":
                await safe_tg_call(client.ban_chat_member, message.chat.id, target.id)
                await asyncio.sleep(0.5)
                await safe_tg_call(client.unban_chat_member, message.chat.id, target.id)
                act_str = "🥾 ᴋɪᴄᴋᴇᴅ"
            else:
                await safe_tg_call(client.ban_chat_member, message.chat.id, target.id)
                act_str = "🚫 ʙᴀɴɴᴇᴅ"
        except Exception as e:
            act_str = f"⚠️ ᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ ({e})"

        await message.reply_text(
            f"🚨 <b>ᴡᴀʀɴɪɴɢ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ ({count}/{limit})!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ʟᴀsᴛ ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>\n"
            f"• <b>ᴘᴇɴᴀʟᴛʏ:</b> <b>{act_str}</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"WARN_LIMIT_{action.upper()}", message.from_user, target, reason)
        return

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧹 ʀᴇsᴇᴛ ᴡᴀʀɴs", callback_data=f"hdesk{HD_SEP}resetwarn{HD_SEP}{target.id}"),
            InlineKeyboardButton("🚫 ʙᴀɴ ɴᴏᴡ", callback_data=f"hdesk{HD_SEP}ban{HD_SEP}{target.id}"),
        ]
    ])
    await message.reply_text(
        f"⚠️ <b>ᴜsᴇʀ ᴡᴀʀɴᴇᴅ ({count}/{limit})!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
        f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>\n"
        f"• <b>ʟɪᴍɪᴛ ᴀᴄᴛɪᴏɴ:</b> <code>{action.upper()} ᴀᴛ {limit} ᴡᴀʀɴs</code>",
        reply_markup=btn,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, f"WARN ({count}/{limit})", message.from_user, target, reason)


@Client.on_message(filters.command(["resetwarn", "resetwarns", "clearwarns", "rmwarn", "unwarn"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_resetwarn_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    cmd = message.command[0].lower()
    target, _ = await resolve_target_user_and_reason(client, message)
    if not target:
        return await message.reply_text(f"⚠️ <b>ᴜsᴀɢᴇ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ <code>/{cmd} @user</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    if cmd == "unwarn":
        cfg = await db.get_helpdesk_config(message.chat.id)
        limit = int(cfg.get("warn_limit", 3) or 3)
        rem_cnt, _ = await db.remove_one_warning(message.chat.id, target.id)
        await message.reply_text(
            f"✅ <b>ʀᴇᴍᴏᴠᴇᴅ 1 ᴡᴀʀɴɪɴɢ ғᴏʀ {target.mention} (<code>{target.id}</code>)!</b> (ʀᴇᴍᴀɪɴɪɴɢ: <code>{rem_cnt}/{limit}</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(client, message.chat, f"UNWARN ({rem_cnt}/{limit})", message.from_user, target)
        return

    await db.reset_warnings(message.chat.id, target.id)
    await message.reply_text(
        f"✅ <b>ᴡᴀʀɴɪɴɢs ᴄʟᴇᴀʀᴇᴅ ғᴏʀ {target.mention} (<code>{target.id}</code>)!</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, "RESET_WARNINGS", message.from_user, target)


@Client.on_message(filters.command(["warnings", "warns"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_warnings_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    target, _ = await resolve_target_user_and_reason(client, message)
    if not target:
        target = message.from_user
    if not target:
        return

    wdoc = await db.get_warnings(message.chat.id, target.id)
    cfg = await db.get_helpdesk_config(message.chat.id)
    limit = int(cfg.get("warn_limit", 3) or 3)
    count = int(wdoc.get("count", 0))
    reasons = wdoc.get("reasons", [])

    if count == 0:
        return await message.reply_text(
            f"✅ <b>{target.mention} ʜᴀs <code>0/{limit}</code> ᴡᴀʀɴɪɴɢs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    r_lines = [
        f"{idx}. <code>{html.escape(r.get('reason', 'N/A'))}</code>"
        for idx, r in enumerate(reasons[-10:], 1)
    ]
    await message.reply_text(
        f"⚠️ <b>ᴡᴀʀɴɪɴɢ ʜɪsᴛᴏʀʏ ғᴏʀ {target.mention}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴄᴏᴜɴᴛ:</b> <code>{count}/{limit}</code>\n"
        f"• <b>ᴘᴇɴᴀʟᴛʏ ᴍᴏᴅᴇ:</b> <code>{cfg.get('warn_action', 'ban').upper()}</code>\n\n"
        f"<b>📋 ʀᴇᴀsᴏɴs:</b>\n" + "\n".join(r_lines),
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["setwarnlimit", "warnlimit"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setwarnlimit_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_restrict_members", "can_restrict_members")
    if not ok:
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/setwarnlimit 3</code> (1–20)", quote=True, parse_mode=enums.ParseMode.HTML)
    lim = max(1, min(20, int(message.command[1])))
    await db.update_helpdesk_config(message.chat.id, {"warn_limit": lim})
    await message.reply_text(f"✅ <b>ᴡᴀʀɴɪɴɢ ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ:</b> <code>{lim}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setwarnmode", "warnmode"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setwarnmode_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, "can_restrict_members", "can_restrict_members")
    if not ok:
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("ban", "mute", "kick"):
        return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/setwarnmode &lt;ban|mute|kick&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    mode = message.command[1].lower()
    await db.update_helpdesk_config(message.chat.id, {"warn_action": mode})
    await message.reply_text(f"✅ <b>ᴡᴀʀɴɪɴɢ ᴘᴇɴᴀʟᴛʏ ᴍᴏᴅᴇ sᴇᴛ ᴛᴏ:</b> <code>{mode.upper()}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("lock", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_lock_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_delete_messages", "can_restrict_members"
    )
    if not ok:
        return

    if len(message.command) < 2:
        return await message.reply_text(
            "🔒 <b>ᴜsᴀɢᴇ:</b> <code>/lock &lt;type&gt;</code>\n"
            f"<b>sᴜᴘᴘᴏʀᴛᴇᴅ ᴛʏᴘᴇs:</b> <code>{', '.join(SUPPORTED_LOCK_TYPES)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    ltype = message.command[1].lower().strip()
    if ltype not in SUPPORTED_LOCK_TYPES:
        return await message.reply_text(
            f"❌ <b>ᴜɴᴋɴᴏᴡɴ ʟᴏᴄᴋ ᴛʏᴘᴇ!</b>\n<b>sᴜᴘᴘᴏʀᴛᴇᴅ:</b> <code>{', '.join(SUPPORTED_LOCK_TYPES)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    await db.set_group_lock(message.chat.id, ltype, True)
    if ltype == "all":
        try:
            await client.set_chat_permissions(message.chat.id, MUTE_PERMS)
        except Exception:
            pass
    await message.reply_text(
        f"🔒 <b>ʟᴏᴄᴋᴇᴅ <code>{ltype.upper()}</code> ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ!</b>\n"
        "<i>ɴᴏɴ-ᴀᴅᴍɪɴ ᴀɴᴅ ᴜɴᴀᴘᴘʀᴏᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴍᴀᴛᴄʜɪɴɢ ᴛʜɪs ᴛʏᴘᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇᴅ.</i>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, f"LOCK_{ltype.upper()}", message.from_user)


@Client.on_message(filters.command("unlock", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_unlock_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_delete_messages", "can_restrict_members"
    )
    if not ok:
        return

    if len(message.command) < 2:
        return await message.reply_text(
            "🔓 <b>ᴜsᴀɢᴇ:</b> <code>/unlock &lt;type&gt;</code>\n"
            f"<b>sᴜᴘᴘᴏʀᴛᴇᴅ ᴛʏᴘᴇs:</b> <code>{', '.join(SUPPORTED_LOCK_TYPES)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    ltype = message.command[1].lower().strip()
    if ltype not in SUPPORTED_LOCK_TYPES:
        return await message.reply_text(
            f"❌ <b>ᴜɴᴋɴᴏᴡɴ ʟᴏᴄᴋ ᴛʏᴘᴇ!</b>\n<b>sᴜᴘᴘᴏʀᴛᴇᴅ:</b> <code>{', '.join(SUPPORTED_LOCK_TYPES)}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    await db.set_group_lock(message.chat.id, ltype, False)
    if ltype == "all":
        try:
            await client.set_chat_permissions(message.chat.id, UNMUTE_PERMS)
        except Exception:
            pass
    await message.reply_text(
        f"🔓 <b>ᴜɴʟᴏᴄᴋᴇᴅ <code>{ltype.upper()}</code> ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ!</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, f"UNLOCK_{ltype.upper()}", message.from_user)


@Client.on_message(filters.command("locks", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_locks_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    cfg = await db.get_helpdesk_config(message.chat.id)
    locks = cfg.get("locks", {})
    lines = [
        f"🔐 <b>ɢʀᴏᴜᴘ sᴇᴄᴜʀɪᴛʏ ʟᴏᴄᴋs — {html.escape(message.chat.title or 'Group')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    for k in SUPPORTED_LOCK_TYPES:
        state = "🔒 ʟᴏᴄᴋᴇᴅ" if locks.get(k) else "🔓 ᴏᴘᴇɴ"
        lines.append(f"• <code>{k:<10}</code> : <b>{state}</b>")
    lines.append("\n💡 <i>ᴛᴏɢɢʟᴇ ᴠɪᴀ <code>/lock &lt;type&gt;</code>, <code>/unlock &lt;type&gt;</code>, ᴏʀ <code>/helpdesk</code>!</i>")
    await message.reply_text("\n".join(lines), quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setflood", "antiflood"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_antiflood_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    cfg = await db.get_helpdesk_config(message.chat.id)
    if len(message.command) < 2:
        enabled = cfg.get("antiflood_enabled", False)
        return await message.reply_text(
            f"🌊 <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ sᴛᴀᴛᴜs</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>sᴛᴀᴛᴜs:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if enabled else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>\n"
            f"• <b>ᴛʜʀᴇsʜᴏʟᴅ:</b> <code>{cfg.get('flood_limit', 5)} ᴍsɢs / {cfg.get('flood_window', 5)}s</code>\n"
            f"• <b>ᴘᴇɴᴀʟᴛʏ:</b> <code>{cfg.get('flood_action', 'mute').upper()}</code>\n\n"
            "💡 <b>ᴜsᴀɢᴇ:</b>\n"
            "• <code>/antiflood on</code> ᴏʀ <code>/antiflood off</code>\n"
            "• <code>/setflood 5 5 mute</code> (5 ᴍsɢs ɪɴ 5s → ᴍᴜᴛᴇ/ʙᴀɴ/ᴋɪᴄᴋ)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    arg1 = message.command[1].lower()
    if arg1 in ("on", "enable", "true"):
        await db.update_helpdesk_config(message.chat.id, {"antiflood_enabled": True})
        return await message.reply_text("🟢 <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴇɴᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    if arg1 in ("off", "disable", "false"):
        await db.update_helpdesk_config(message.chat.id, {"antiflood_enabled": False})
        return await message.reply_text("🔴 <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if arg1.isdigit():
        flimit = max(3, min(30, int(arg1)))
        fwin = max(2, min(60, int(message.command[2]))) if (len(message.command) >= 3 and message.command[2].isdigit()) else 5
        faction = message.command[3].lower() if (len(message.command) >= 4 and message.command[3].lower() in ("mute", "ban", "kick")) else "mute"
        await db.update_helpdesk_config(
            message.chat.id,
            {
                "antiflood_enabled": True,
                "flood_limit": flimit,
                "flood_window": fwin,
                "flood_action": faction,
            },
        )
        return await message.reply_text(
            f"🌊 <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴄᴏɴғɪɢᴜʀᴇᴅ!</b>\n"
            f"• <b>ʟɪᴍɪᴛ:</b> <code>{flimit} ᴍᴇssᴀɢᴇs ɪɴ {fwin}s</code>\n"
            f"• <b>ᴀᴄᴛɪᴏɴ:</b> <code>{faction.upper()}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("spam", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_spam_cmd(client: Client, message: Message):
    """Configure Anti-Spam repeated message detector (/spam on|off, /spam limit <2-20>, /spam window <5-600>)."""
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    cfg = await db.get_helpdesk_config(message.chat.id)
    if len(message.command) < 2:
        enabled = bool(cfg.get("antidup_enabled", False))
        s_lim = int(cfg.get("spam_limit", 3) or 3)
        s_win = int(cfg.get("spam_window", 30) or 30)
        return await message.reply_text(
            f"🛡️ <b>ᴀɴᴛɪ-sᴘᴀᴍ (ʀᴇᴘᴇᴀᴛᴇᴅ ᴍᴇssᴀɢᴇ) sᴛᴀᴛᴜs</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>sᴛᴀᴛᴜs:</b> <code>{'🟢 ᴏɴ' if enabled else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ʟɪᴍɪᴛ:</b> <code>{s_lim} identical messages</code>\n"
            f"• <b>ᴡɪɴᴅᴏᴡ:</b> <code>{s_win} seconds</code>\n"
            f"• <b>ᴘᴇɴᴀʟᴛʏ:</b> <code>Warn first, then Ban</code>\n\n"
            "💡 <b>ᴜsᴀɢᴇ:</b>\n"
            "• <code>/spam on</code> ᴏʀ <code>/spam off</code>\n"
            "• <code>/spam limit &lt;2-20&gt;</code>\n"
            "• <code>/spam window &lt;5-600&gt;</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    sub = message.command[1].lower().strip()
    if sub in ("on", "enable", "true", "1"):
        await db.update_helpdesk_config(message.chat.id, {"antidup_enabled": True, "antispam_enabled": True})
        return await message.reply_text(
            "🟢 <b>ᴀɴᴛɪ-sᴘᴀᴍ ᴇɴᴀʙʟᴇᴅ!</b> ʀᴇᴘᴇᴀᴛɪɴɢ ɪᴅᴇɴᴛɪᴄᴀʟ ᴍᴇssᴀɢᴇs ᴡɪʟʟ ᴡᴀʀɴ, ᴛʜᴇɴ ʙᴀɴ.",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    if sub in ("off", "disable", "false", "0"):
        await db.update_helpdesk_config(message.chat.id, {"antidup_enabled": False})
        return await message.reply_text(
            "🔴 <b>ᴀɴᴛɪ-sᴘᴀᴍ ᴅɪsᴀʙʟᴇᴅ!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    if sub == "limit":
        if len(message.command) < 3 or not message.command[2].isdigit():
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/spam limit &lt;2-20&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        lim = max(2, min(20, int(message.command[2])))
        await db.update_helpdesk_config(message.chat.id, {"antidup_enabled": True, "spam_limit": lim})
        return await message.reply_text(f"✅ <b>ᴀɴᴛɪ-sᴘᴀᴍ ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ:</b> <code>{lim} identical messages</code>", quote=True, parse_mode=enums.ParseMode.HTML)
    if sub == "window":
        if len(message.command) < 3 or not message.command[2].isdigit():
            return await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/spam window &lt;5-600&gt;</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        win = max(5, min(600, int(message.command[2])))
        await db.update_helpdesk_config(message.chat.id, {"antidup_enabled": True, "spam_window": win})
        return await message.reply_text(f"✅ <b>ᴀɴᴛɪ-sᴘᴀᴍ ᴡɪɴᴅᴏᴡ sᴇᴛ ᴛᴏ:</b> <code>{win} seconds</code>", quote=True, parse_mode=enums.ParseMode.HTML)

    return await message.reply_text(
        "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/spam on|off</code>, <code>/spam limit &lt;2-20&gt;</code>, ᴏʀ <code>/spam window &lt;5-600&gt;</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["raidmode", "antiraid", "emergency"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_raidmode_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(
        client, message, "can_restrict_members", "can_restrict_members"
    )
    if not ok:
        return

    cfg = await db.get_helpdesk_config(message.chat.id)
    curr = bool(cfg.get("raid_mode", False))
    if len(message.command) >= 2:
        a = message.command[1].lower()
        new_val = a in ("on", "enable", "true", "1")
    else:
        new_val = not curr

    await db.update_helpdesk_config(message.chat.id, {"raid_mode": new_val, "emergency_mode": new_val})
    await message.reply_text(
        f"🚨 <b>ᴀɴᴛɪ-ʀᴀɪᴅ &amp; ᴇᴍᴇʀɢᴇɴᴄʏ ᴍᴏᴅᴇ: {'🟢 ᴀᴄᴛɪᴠᴇ' if new_val else '🔴 ᴏғғ'}</b>\n"
        + (
            "<i>ɴᴇᴡ ᴊᴏɪɴɪɴɢ ᴀᴄᴄᴏᴜɴᴛs ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴜɴᴛɪʟ ʀᴀɪᴅ ᴍᴏᴅᴇ ɪs ᴛᴜʀɴᴇᴅ ᴏғғ.</i>"
            if new_val
            else "<i>ɴᴏʀᴍᴀʟ ᴍᴇᴍʙᴇʀ ᴊᴏɪɴɪɴɢ ʀᴇsᴛᴏʀᴇᴅ.</i>"
        ),
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, f"RAID_MODE_{'ON' if new_val else 'OFF'}", message.from_user)


@Client.on_message(filters.command(["welcome", "setwelcome", "resetwelcome", "setgoodbye"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_welcome_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, None, "can_change_info", require_bot_admin=False)
    if not ok:
        return

    cmd = message.command[0].lower()
    if cmd == "welcome":
        cfg = await db.get_helpdesk_config(message.chat.id)
        if len(message.command) < 2:
            st = "🟢 ᴏɴ" if cfg.get("welcome_enabled", True) else "🔴 ᴏғғ"
            return await message.reply_text(
                f"👋 <b>ᴡᴇʟᴄᴏᴍᴇ sᴛᴀᴛᴜs:</b> <code>{st}</code>\n"
                f"• <b>ᴛᴇᴍᴘʟᴀᴛᴇ:</b> <code>{html.escape(str(cfg.get('welcome_text', '')))}</code>\n"
                f"• <b>ᴘʟᴀᴄᴇʜᴏʟᴅᴇʀs:</b> <code>{{mention}} {{first}} {{chat}} {{id}} {{count}}</code>\n\n"
                "💡 <b>ᴜsᴀɢᴇ:</b> <code>/welcome on|off</code>, <code>/setwelcome &lt;text&gt;</code>, <code>/resetwelcome</code>",
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        arg_w = message.command[1].lower().strip()
        en = arg_w in ("on", "enable", "true", "1", "yes")
        await db.update_helpdesk_config(message.chat.id, {"welcome_enabled": en})
        return await message.reply_text(
            f"👋 <b>ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇs:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if en else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "resetwelcome":
        default_cfg = db.default_helpdesk_group_config(message.chat.id)
        await db.update_helpdesk_config(
            message.chat.id,
            {"welcome_enabled": True, "welcome_text": default_cfg["welcome_text"]},
        )
        return await message.reply_text("🔄 <b>ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if len(message.command) < 2:
        return await message.reply_text(
            f"👋 <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} Welcome {{mention}} to {{chat_title}}!</code> (ᴏʀ <code>/{cmd} off</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    raw_val = message.text.split(None, 1)[1].strip()
    is_off = raw_val.lower() in ("off", "disable", "false")
    if cmd == "setgoodbye":
        if is_off:
            await db.update_helpdesk_config(message.chat.id, {"goodbye_enabled": False})
            return await message.reply_text("🔴 <b>ɢᴏᴏᴅʙʏᴇ ᴍᴇssᴀɢᴇs ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        await db.update_helpdesk_config(message.chat.id, {"goodbye_enabled": True, "goodbye_text": raw_val})
        return await message.reply_text("✅ <b>ɢᴏᴏᴅʙʏᴇ ᴍᴇssᴀɢᴇ sᴀᴠᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if is_off:
        await db.update_helpdesk_config(message.chat.id, {"welcome_enabled": False})
        return await message.reply_text("🔴 <b>ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇs ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    await db.update_helpdesk_config(message.chat.id, {"welcome_enabled": True, "welcome_text": raw_val})
    await message.reply_text("✅ <b>ᴄᴜsᴛᴏᴍ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ sᴀᴠᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["setrules", "clearrules"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setrules_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, None, "can_change_info", require_bot_admin=False)
    if not ok:
        return
    cmd = message.command[0].lower()
    if cmd == "clearrules":
        await db.update_helpdesk_config(message.chat.id, {"rules_text": ""})
        return await message.reply_text("🗑️ <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs ᴄʟᴇᴀʀᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text("📜 <b>ᴜsᴀɢᴇ:</b> <code>/setrules 1. Be respectful...</code> (ᴏʀ <code>/clearrules</code>)", quote=True, parse_mode=enums.ParseMode.HTML)
    rules_txt = message.text.split(None, 1)[1].strip()
    await db.update_helpdesk_config(message.chat.id, {"rules_text": rules_txt})
    await message.reply_text("✅ <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs ᴜᴘᴅᴀᴛᴇᴅ!</b> ᴍᴇᴍʙᴇʀs ᴄᴀɴ ᴠɪᴇᴡ ᴛʜᴇᴍ ᴡɪᴛʜ <code>/rules</code>.", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("rules", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_rules_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    cfg = await db.get_helpdesk_config(message.chat.id)
    rules_txt = cfg.get("rules_text") or "📜 ɴᴏ ʀᴜʟᴇs ᴄᴏɴғɪɢᴜʀᴇᴅ ʏᴇᴛ."
    r_btn_spec = (cfg.get("rules_button") or "").strip()
    r_kb = None
    if r_btn_spec and "|" in r_btn_spec:
        lbl, url = [x.strip() for x in r_btn_spec.split("|", 1)]
        if lbl and url.startswith(("http://", "https://", "tg://")):
            r_kb = InlineKeyboardMarkup([[InlineKeyboardButton(lbl, url=url)]])
    await message.reply_text(
        f"📜 <b>ʀᴜʟᴇs ғᴏʀ {html.escape(message.chat.title or 'Group')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n{rules_txt}",
        reply_markup=r_kb,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["setlog", "unsetlog"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_setlog_cmd(client: Client, message: Message):
    ok, _, _, _ = await verify_group_and_permissions(client, message, None, "can_change_info", require_bot_admin=False)
    if not ok:
        return

    cmd = message.command[0].lower()
    if cmd == "unsetlog":
        await db.update_helpdesk_config(message.chat.id, {"log_channel": 0})
        return await message.reply_text("🗑️ <b>ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ᴜɴsᴇᴛ ғᴏʀ ᴛʜɪs ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if len(message.command) < 2 or not message.command[1].lstrip("-").isdigit():
        return await message.reply_text(
            "📋 <b>ᴜsᴀɢᴇ:</b> <code>/setlog -100xxxxxxxxxx</code> (ᴏʀ <code>/unsetlog</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    ch_id = int(message.command[1])
    await db.update_helpdesk_config(message.chat.id, {"log_channel": ch_id})
    await message.reply_text(f"✅ <b>ᴍᴏᴅᴇʀᴀᴛɪᴏɴ ʟᴏɢ ᴄʜᴀɴɴᴇʟ sᴇᴛ ᴛᴏ:</b> <code>{ch_id}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message((filters.command("report", prefixes=COMMAND_PREFIXES) | filters.regex(r"(?i)(?:^|\s)@admins?\b")) & filters.incoming)
async def hd_report_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ <code>/report</code> ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ ʙʏ ʀᴇᴘʟʏɪɴɢ ᴛᴏ ᴀ sᴜsᴘɪᴄɪᴏᴜs ᴍᴇssᴀɢᴇ.</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    if not message.reply_to_message:
        return await message.reply_text("⚠️ <b>ᴘʟᴇᴀsᴇ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʀᴇᴘᴏʀᴛ ᴛᴏ ᴀᴅᴍɪɴs!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    reported_msg = message.reply_to_message
    reported_user = reported_msg.from_user
    reason = message.text.split(None, 1)[1].strip() if len(message.command) >= 2 else "Suspicious / rule-breaking message"

    admin_mentions: List[str] = []
    try:
        async for mem in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
            if mem.user and not mem.user.is_bot:
                admin_mentions.append(f"<a href='tg://user?id={mem.user.id}'>&#8203;</a>")
    except Exception:
        pass

    btns: List[List[InlineKeyboardButton]] = []
    if reported_user:
        btns.append([
            InlineKeyboardButton("🗑️ ᴅᴇʟᴇᴛᴇ ᴍsɢ", callback_data=f"hdesk{HD_SEP}delmsg{HD_SEP}{reported_msg.id}"),
            InlineKeyboardButton("⚠️ ᴡᴀʀɴ", callback_data=f"hdesk{HD_SEP}warn{HD_SEP}{reported_user.id}"),
        ])
        btns.append([
            InlineKeyboardButton("🔇 ᴍᴜᴛᴇ", callback_data=f"hdesk{HD_SEP}mute{HD_SEP}{reported_user.id}"),
            InlineKeyboardButton("🚫 ʙᴀɴ", callback_data=f"hdesk{HD_SEP}ban{HD_SEP}{reported_user.id}"),
        ])

    await message.reply_text(
        f"🚨 <b>ᴍᴇssᴀɢᴇ ʀᴇᴘᴏʀᴛᴇᴅ ᴛᴏ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs!</b>{''.join(admin_mentions[:15])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ʀᴇᴘᴏʀᴛᴇᴅ ʙʏ:</b> {message.from_user.mention if message.from_user else 'Member'}\n"
        f"• <b>ᴏғғᴇɴᴅᴇʀ:</b> {reported_user.mention if reported_user else 'Unknown'}\n"
        f"• <b>ʀᴇᴀsᴏɴ:</b> <code>{html.escape(reason)}</code>",
        reply_markup=InlineKeyboardMarkup(btns) if btns else None,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    await dispatch_mod_log(client, message.chat, "USER_REPORT", message.from_user, reported_user, reason)


@Client.on_message(filters.command(["groupstats", "ginfo", "chatinfo"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_groupstats_cmd(client: Client, message: Message):
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text("⚠️ <b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)

    wait = await message.reply_text("📊 <i>ɢᴀᴛʜᴇʀɪɴɢ ɢʀᴏᴜᴘ sᴛᴀᴛɪsᴛɪᴄs...</i>", quote=True, parse_mode=enums.ParseMode.HTML)
    try:
        chat_full = await client.get_chat(message.chat.id)
        members_cnt = await client.get_chat_members_count(message.chat.id)
        cfg = await db.get_helpdesk_config(message.chat.id)
        locks = cfg.get("locks", {})
        active_locks = [k for k, v in locks.items() if v]
        logs_recent = await db.get_mod_logs(message.chat.id, limit=50)

        text = (
            f"📊 <b>ɢʀᴏᴜᴘ ɪɴғᴏʀᴍᴀᴛɪᴏɴ &amp; sᴛᴀᴛɪsᴛɪᴄs</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴛɪᴛʟᴇ:</b> <code>{html.escape(chat_full.title or 'Group')}</code>\n"
            f"• <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{chat_full.id}</code>\n"
            f"• <b>ᴜsᴇʀɴᴀᴍᴇ:</b> {f'@{chat_full.username}' if chat_full.username else '<code>ᴘʀɪᴠᴀᴛᴇ</code>'}\n"
            f"• <b>ᴍᴇᴍʙᴇʀs:</b> <code>{members_cnt:,}</code>\n"
            f"• <b>ᴀᴘᴘʀᴏᴠᴇᴅ ᴜsᴇʀs:</b> <code>{len(cfg.get('approved_users', []))}</code>\n"
            f"• <b>ᴀᴄᴛɪᴠᴇ ʟᴏᴄᴋs:</b> <code>{', '.join(active_locks) if active_locks else 'ɴᴏɴᴇ'}</code>\n"
            f"• <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ:</b> <code>{'🟢 ᴏɴ' if cfg.get('antiflood_enabled') else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ʀᴀɪᴅ ᴍᴏᴅᴇ:</b> <code>{'🚨 ᴀᴄᴛɪᴠᴇ' if cfg.get('raid_mode') else '🟢 ɴᴏʀᴍᴀʟ'}</code>\n"
            f"• <b>ᴡᴀʀɴ ʟɪᴍɪᴛ:</b> <code>{cfg.get('warn_limit', 3)} ({cfg.get('warn_action', 'ban').upper()})</code>\n"
            f"• <b>ᴍᴏᴅ ᴀᴄᴛɪᴏɴs ʟᴏɢɢᴇᴅ:</b> <code>{len(logs_recent)}</code>"
        )
        if chat_full.description:
            text += f"\n\n📝 <b>ᴅᴇsᴄʀɪᴘᴛɪᴏɴ:</b>\n<i>{html.escape(chat_full.description[:300])}</i>"

        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🛡️ ᴏᴘᴇɴ ʜᴇʟᴘᴅᴇsᴋ", callback_data=f"hdesk{HD_SEP}home"),
                InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
            ]
        ])
        await wait.edit_text(text, reply_markup=btn, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await wait.edit_text(f"❌ <b>ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("id", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_id_cmd(client: Client, message: Message):
    if message.chat.type == enums.ChatType.PRIVATE:
        u = message.from_user
        if not u:
            return
        await message.reply_text(
            f"<b>🆔 ɪᴅ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ɴᴀᴍᴇ:</b> {u.mention}\n"
            f"• <b>ᴜsᴇʀ ɪᴅ:</b> <code>{u.id}</code>\n"
            f"• <b>ᴜsᴇʀɴᴀᴍᴇ:</b> {f'@{u.username}' if u.username else '<code>ɴᴏɴᴇ</code>'}\n"
            f"• <b>ᴅᴀᴛᴀ ᴄᴇɴᴛᴇʀ:</b> <code>{u.dc_id or 'ɴ/ᴀ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
        return

    out = [
        f"<b>🆔 ᴄʜᴀᴛ &amp; ᴍᴇᴍʙᴇʀ ɪᴅ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴄʜᴀᴛ ᴛɪᴛʟᴇ:</b> <code>{html.escape(message.chat.title or 'Group')}</code>\n"
        f"• <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{message.chat.id}</code>"
    ]
    if message.from_user:
        out.append(f"• <b>ʏᴏᴜʀ ɪᴅ:</b> <code>{message.from_user.id}</code>")
    if message.reply_to_message:
        ru = message.reply_to_message.from_user
        if ru:
            out.append(f"• <b>ʀᴇᴘʟɪᴇᴅ ᴜsᴇʀ ɪᴅ:</b> <code>{ru.id}</code> ({ru.mention})")
        finfo = get_file_id(message.reply_to_message)
        if finfo:
            out.append(f"• <b>{finfo.message_type} ғɪʟᴇ_ɪᴅ:</b> <code>{finfo.file_id}</code>")
    await message.reply_text("\n".join(out), quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["info", "userinfo"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_info_cmd(client: Client, message: Message):
    wait = await message.reply_text("🔎 <i>ғᴇᴛᴄʜɪɴɢ ᴍᴇᴍʙᴇʀ ᴘʀᴏғɪʟᴇ...</i>", quote=True, parse_mode=enums.ParseMode.HTML)
    target, _ = await resolve_target_user_and_reason(client, message)
    if not target:
        target = message.from_user
    if not target:
        return await wait.edit_text("❌ <b>ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇsᴏʟᴠᴇ ᴜsᴇʀ.</b>", parse_mode=enums.ParseMode.HTML)

    role_str = "👤 ᴍᴇᴍʙᴇʀ"
    joined_str = "ɴ/ᴀ"
    warns_str = "0"
    approved_str = "ɴᴏ"

    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        try:
            mem = await client.get_chat_member(message.chat.id, target.id)
            if mem.status == enums.ChatMemberStatus.OWNER:
                role_str = "👑 ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ"
            elif mem.status == enums.ChatMemberStatus.ADMINISTRATOR:
                role_str = "👮 ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ"
            elif mem.status == enums.ChatMemberStatus.RESTRICTED:
                role_str = "🔇 ʀᴇsᴛʀɪᴄᴛᴇᴅ"
            elif mem.status == enums.ChatMemberStatus.BANNED:
                role_str = "🚫 ʙᴀɴɴᴇᴅ"
            if mem.joined_date:
                joined_str = mem.joined_date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        wdoc = await db.get_warnings(message.chat.id, target.id)
        cfg = await db.get_helpdesk_config(message.chat.id)
        warns_str = f"{wdoc.get('count', 0)}/{cfg.get('warn_limit', 3)}"
        approved_str = "🟢 ʏᴇs" if await db.is_user_approved(message.chat.id, target.id) else "⚪ ɴᴏ"

    if is_admin_sync(target.id):
        role_str += " │ 🌟 ᴍᴍᴡ ᴀᴅᴍɪɴ"

    text = (
        f"👤 <b>ᴍᴇᴍʙᴇʀ ᴘʀᴏғɪʟᴇ &amp; sᴛᴀᴛᴜs</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ɴᴀᴍᴇ:</b> {target.mention}\n"
        f"• <b>ᴜsᴇʀ ɪᴅ:</b> <code>{target.id}</code>\n"
        f"• <b>ᴜsᴇʀɴᴀᴍᴇ:</b> {f'@{target.username}' if target.username else '<code>ɴᴏɴᴇ</code>'}\n"
        f"• <b>ᴅᴄ ɪᴅ:</b> <code>{target.dc_id or 'ɴ/ᴀ'}</code>\n"
        f"• <b>sᴛᴀᴛᴜs:</b> <code>{last_online(target) or 'ᴀᴄᴛɪᴠᴇ'}</code>\n"
        f"• <b>ʀᴏʟᴇ:</b> <code>{role_str}</code>\n"
        f"• <b>ᴊᴏɪɴᴇᴅ:</b> <code>{joined_str}</code>\n"
        f"• <b>ᴡᴀʀɴɪɴɢs:</b> <code>{warns_str}</code> │ <b>ᴀᴘᴘʀᴏᴠᴇᴅ:</b> <code>{approved_str}</code>"
    )
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data")]])
    await wait.edit_text(text, reply_markup=btn, parse_mode=enums.ParseMode.HTML)


def build_helpdesk_menu_view(section: str, cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, InlineKeyboardMarkup]:
    cfg = cfg or {}
    locks = cfg.get("locks", {})
    if section == "admin":
        text = (
            "👑 <b>ɢʀᴏᴜᴘ ᴀᴅᴍɪɴɪsᴛʀᴀᴛɪᴏɴ ᴄᴏᴍᴍᴀɴᴅs</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/promote @user [title]</code> — ᴘʀᴏᴍᴏᴛᴇ ᴍᴇᴍʙᴇʀ ᴛᴏ ᴀᴅᴍɪɴ\n"
            "• <code>/fullpromote @user [title]</code> — ᴘʀᴏᴍᴏᴛᴇ ᴡɪᴛʜ ғᴜʟʟ ʀɪɢʜᴛs\n"
            "• <code>/demote @user</code> — ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ ᴘʀɪᴠɪʟᴇɢᴇs\n"
            "• <code>/title @admin &lt;title&gt;</code> — sᴇᴛ ᴄᴜsᴛᴏᴍ ᴀᴅᴍɪɴ ᴛɪᴛʟᴇ\n"
            "• <code>/pin [loud]</code> — ᴘɪɴ ʀᴇᴘʟɪᴇᴅ ᴍᴇssᴀɢᴇ\n"
            "• <code>/unpin</code> │ <code>/unpinall</code> — ᴜɴᴘɪɴ ᴍᴇssᴀɢᴇ(s)\n"
            "• <code>/adminlist</code> │ <code>/staff</code> │ <code>/admins</code> — ʟɪsᴛ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs\n"
            "• <code>/setgtitle &lt;name&gt;</code> — ᴄʜᴀɴɢᴇ ɢʀᴏᴜᴘ ᴛɪᴛʟᴇ\n"
            "• <code>/setgdesc &lt;text&gt;</code> — ᴄʜᴀɴɢᴇ ɢʀᴏᴜᴘ ᴅᴇsᴄʀɪᴘᴛɪᴏɴ\n"
            "• <code>/setgpic</code> │ <code>/delgpic</code> — sᴇᴛ ᴏʀ ᴅᴇʟᴇᴛᴇ ɢʀᴏᴜᴘ ᴘʜᴏᴛᴏ\n"
            "• <code>/slowmode &lt;seconds&gt;</code> — ᴄᴏɴғɪɢᴜʀᴇ sʟᴏᴡ ᴍᴏᴅᴇ\n"
            "• <code>/invitelink [request]</code> │ <code>/revokelink</code> — ᴍᴀɴᴀɢᴇ ɪɴᴠɪᴛᴇ ʟɪɴᴋs\n"
            "• <code>/groupstats</code> — ᴠɪᴇᴡ ɢʀᴏᴜᴘ ɪɴғᴏ &amp; sᴛᴀᴛɪsᴛɪᴄs\n"
            "• <code>/gsettings</code> — ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ɢʀᴏᴜᴘ ғᴇᴀᴛᴜʀᴇ &amp; ʟɪᴍɪᴛ ᴇᴅɪᴛᴏʀ\n"
            "• <code>/editstudio</code> │ <code>/editpost</code> │ <code>/editbuttons</code> — ʟɪᴠᴇ ᴘᴏsᴛ ᴇᴅɪᴛᴏʀ\n"
            "• <code>/gbackup</code> │ <code>/grestore</code> — ғᴜʟʟ ɢʀᴏᴜᴘ ᴊsᴏɴ ʙᴀᴄᴋᴜᴘ &amp; ʀᴇsᴛᴏʀᴇ"
        )
    elif section == "members":
        text = (
            "👥 <b>ᴍᴇᴍʙᴇʀ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ &amp; ᴀᴘᴘʀᴏᴠᴀʟs</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/ban @user [reason]</code> — ᴘᴇʀᴍᴀɴᴇɴᴛʟʏ ʙᴀɴ ᴍᴇᴍʙᴇʀ\n"
            "• <code>/tban @user 1h [reason]</code> — ᴛᴇᴍᴘᴏʀᴀʀʏ ʙᴀɴ (m/h/d/w)\n"
            "• <code>/unban @user</code> — ᴜɴʙᴀɴ ᴍᴇᴍʙᴇʀ\n"
            "• <code>/kick @user [reason]</code> — ᴋɪᴄᴋ ᴍᴇᴍʙᴇʀ ғʀᴏᴍ ɢʀᴏᴜᴘ\n"
            "• <code>/mute @user [reason]</code> — ᴍᴜᴛᴇ ᴍᴇᴍʙᴇʀ ɪɴᴅᴇғɪɴɪᴛᴇʟʏ\n"
            "• <code>/tmute @user 30m [reason]</code> — ᴛᴇᴍᴘᴏʀᴀʀʏ ᴍᴜᴛᴇ\n"
            "• <code>/unmute @user</code> — ᴜɴᴍᴜᴛᴇ ᴍᴇᴍʙᴇʀ\n"
            "• <code>/restrict @user [1h]</code> — ʀᴇsᴛʀɪᴄᴛ ᴍᴇᴅɪᴀ &amp; ʟɪɴᴋs\n"
            "• <code>/unrestrict @user</code> — ʀᴇsᴛᴏʀᴇ ғᴜʟʟ ᴘᴇʀᴍɪssɪᴏɴs\n"
            "• <code>/approve @user</code> — ᴀᴘᴘʀᴏᴠᴇ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ &amp; ᴡʜɪᴛᴇʟɪsᴛ\n"
            "• <code>/unapprove @user</code> — ʀᴇᴍᴏᴠᴇ ᴀᴘᴘʀᴏᴠᴀʟ / ᴅᴇᴄʟɪɴᴇ\n"
            "• <code>/approved</code> — ᴠɪᴇᴡ ᴀᴘᴘʀᴏᴠᴇᴅ ᴍᴇᴍʙᴇʀs\n"
            "• <code>/info @user</code> │ <code>/id</code> — ɪɴsᴘᴇᴄᴛ ᴍᴇᴍʙᴇʀ ᴘʀᴏғɪʟᴇ"
        )
    elif section == "moderation":
        text = (
            "🔨 <b>ᴀᴅᴠᴀɴᴄᴇᴅ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ &amp; ᴡᴀʀɴɪɴɢs</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/del</code> — ᴅᴇʟᴇᴛᴇ ʀᴇᴘʟɪᴇᴅ ᴍᴇssᴀɢᴇ\n"
            "• <code>/purge [count]</code> — ʙᴜʟᴋ-ᴅᴇʟᴇᴛᴇ ᴍᴇssᴀɢᴇs ᴡɪᴛʜ sᴀғᴇᴛʏ ᴄᴏɴғɪʀᴍ\n"
            "• <code>/warn @user [reason]</code> │ <code>/unwarn @user</code> — ᴀᴅᴅ ᴏʀ ʀᴇᴍᴏᴠᴇ 1 ᴡᴀʀɴɪɴɢ\n"
            "• <code>/resetwarns @user</code> │ <code>/warns @user</code> — ᴄʟᴇᴀʀ ᴏʀ ᴠɪᴇᴡ ᴡᴀʀɴɪɴɢs\n"
            "• <code>/setwarnlimit &lt;1-20&gt;</code> │ <code>/setwarnmode &lt;ban|mute|kick&gt;</code>\n"
            "• <code>/spam on|off</code> │ <code>/spam limit &lt;2-20&gt;</code> │ <code>/spam window &lt;5-600&gt;</code>\n"
            "• <code>/autobannow</code> │ <code>/autobanstats</code> — sᴄᴀɴ &amp; ʙᴀɴ ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs\n"
            "• <code>/report</code> ᴏʀ <code>@admin</code> — ʀᴇᴘᴏʀᴛ ᴍᴇssᴀɢᴇ ᴛᴏ ᴀᴅᴍɪɴs\n"
            "• <code>/welcome on|off</code> │ <code>/setwelcome &lt;text&gt;</code> │ <code>/resetwelcome</code>\n"
            "• <code>/rules</code> │ <code>/setrules &lt;text&gt;</code> │ <code>/clearrules</code>\n"
            "• <code>/mutechat</code> │ <code>/unmutechat</code> │ <code>/cleanservice on|off</code>\n"
            "• <code>/setlog &lt;channel_id&gt;</code> │ <code>/unsetlog</code>\n"
            "• <code>/setwelcomebutton</code> │ <code>/setrulesbutton</code> │ <code>/stafftitle</code>"
        )
    elif section == "security":
        text = (
            "🔐 <b>ɢʀᴏᴜᴘ sᴇᴄᴜʀɪᴛʏ, ʟᴏᴄᴋs &amp; ᴀɴᴛɪ-ғʟᴏᴏᴅ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/lock &lt;type&gt;</code> — ʟᴏᴄᴋ ᴘᴇʀᴍɪssɪᴏɴ ᴏʀ ᴍᴇᴅɪᴀ ᴛʏᴘᴇ\n"
            "• <code>/unlock &lt;type&gt;</code> — ᴜɴʟᴏᴄᴋ ᴘᴇʀᴍɪssɪᴏɴ ᴏʀ ᴍᴇᴅɪᴀ ᴛʏᴘᴇ\n"
            "• <code>/locks</code> — ᴠɪᴇᴡ ᴀʟʟ 20 sᴇᴄᴜʀɪᴛʏ ʟᴏᴄᴋ sᴛᴀᴛᴇs\n"
            "• <b>ʟᴏᴄᴋ ᴛʏᴘᴇs:</b> <code>all, button, emoji, forward, url, username, msg, media, document, photo, video, audio, voice, links, stickers, gifs, polls, inline, bots, contact</code>\n"
            "• <code>/spam on|off</code> │ <code>/spam limit &lt;2-20&gt;</code> │ <code>/spam window &lt;5-600&gt;</code>\n"
            "• <code>/setflood 5 5 mute</code> — ᴄᴏɴғɪɢᴜʀᴇ ᴀɴᴛɪ-ғʟᴏᴏᴅ\n"
            "• <code>/antiflood &lt;on|off&gt;</code> — ᴛᴏɢɢʟᴇ ᴀɴᴛɪ-ғʟᴏᴏᴅ\n"
            "• <code>/raidmode &lt;on|off&gt;</code> — ᴇᴍᴇʀɢᴇɴᴄʏ ᴀɴᴛɪ-ʀᴀɪᴅ sʜɪᴇʟᴅ\n\n"
            "👇 <i>ᴛᴀᴘ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ᴛᴏɢɢʟᴇ ʟɪᴠᴇ ɢʀᴏᴜᴘ ʟᴏᴄᴋs:</i>"
        )
        lock_rows: List[List[InlineKeyboardButton]] = []
        quick_locks = ["media", "document", "links", "stickers", "gifs", "polls", "forward", "all"]
        for i in range(0, len(quick_locks), 2):
            pair = quick_locks[i : i + 2]
            row = []
            for lk in pair:
                st = "🔒" if locks.get(lk) else "🔓"
                row.append(InlineKeyboardButton(f"{st} {lk.upper()}", callback_data=f"hdesk{HD_SEP}tlock{HD_SEP}{lk}"))
            lock_rows.append(row)
        lock_rows.append([
            InlineKeyboardButton(
                f"🌊 ᴀɴᴛɪ-ғʟᴏᴏᴅ: {'🟢 ᴏɴ' if cfg.get('antiflood_enabled') else '🔴 ᴏғғ'}",
                callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}antiflood_enabled",
            ),
            InlineKeyboardButton(
                f"🚨 ʀᴀɪᴅ ᴍᴏᴅᴇ: {'🟢 ᴏɴ' if cfg.get('raid_mode') else '🔴 ᴏғғ'}",
                callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}raid_mode",
            ),
        ])
        lock_rows.append([
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"hdesk{HD_SEP}home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ])
        return text, InlineKeyboardMarkup(lock_rows)
    elif section == "settings":
        text = (
            "⚙️ <b>ɢʀᴏᴜᴘ ʜᴇʟᴘᴅᴇsᴋ ʟɪᴠᴇ sᴇᴛᴛɪɴɢs</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴡᴇʟᴄᴏᴍᴇ ᴍsɢ:</b> <code>{'🟢 ᴏɴ' if cfg.get('welcome_enabled', True) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ɢᴏᴏᴅʙʏᴇ ᴍsɢ:</b> <code>{'🟢 ᴏɴ' if cfg.get('goodbye_enabled', False) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ:</b> <code>{'🟢 ᴏɴ' if cfg.get('antiflood_enabled', False) else '🔴 ᴏғғ'} ({cfg.get('flood_limit', 5)}/{cfg.get('flood_window', 5)}s)</code>\n"
            f"• <b>ᴀɴᴛɪ-sᴘᴀᴍ:</b> <code>{'🟢 ᴏɴ' if cfg.get('antispam_enabled', False) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴀɴᴛɪ-ᴅᴜᴘʟɪᴄᴀᴛᴇ:</b> <code>{'🟢 ᴏɴ' if cfg.get('antidup_enabled', False) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ʀᴀɪᴅ ᴍᴏᴅᴇ:</b> <code>{'🚨 ᴏɴ' if cfg.get('raid_mode', False) else '🟢 ᴏғғ'}</code>\n"
            f"• <b>ᴠᴇʀɪғʏ ɴᴇᴡ ᴍᴇᴍʙᴇʀs:</b> <code>{'🟢 ᴏɴ' if cfg.get('verify_new_members', False) else '🔴 ᴏғғ'}</code>\n"
            f"• <b>ᴡᴀʀɴ ʟɪᴍɪᴛ:</b> <code>{cfg.get('warn_limit', 3)} ({cfg.get('warn_action', 'ban').upper()})</code>\n\n"
            "👇 <i>ᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ᴛᴏɢɢʟᴇ ɢʀᴏᴜᴘ ᴘʀᴏᴛᴇᴄᴛɪᴏɴs ɪɴsᴛᴀɴᴛʟʏ:</i>"
        )
        s_rows = [
            [
                InlineKeyboardButton(
                    f"👋 ᴡᴇʟᴄᴏᴍᴇ: {'🟢' if cfg.get('welcome_enabled', True) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}welcome_enabled",
                ),
                InlineKeyboardButton(
                    f"👋 ɢᴏᴏᴅʙʏᴇ: {'🟢' if cfg.get('goodbye_enabled', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}goodbye_enabled",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🌊 ᴀɴᴛɪ-ғʟᴏᴏᴅ: {'🟢' if cfg.get('antiflood_enabled', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}antiflood_enabled",
                ),
                InlineKeyboardButton(
                    f"🛡️ ᴀɴᴛɪ-sᴘᴀᴍ: {'🟢' if cfg.get('antispam_enabled', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}antispam_enabled",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🔁 ᴀɴᴛɪ-ᴅᴜᴘ: {'🟢' if cfg.get('antidup_enabled', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}antidup_enabled",
                ),
                InlineKeyboardButton(
                    f"✅ ᴠᴇʀɪғʏ ᴊᴏɪɴs: {'🟢' if cfg.get('verify_new_members', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}verify_new_members",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🚨 ʀᴀɪᴅ ᴍᴏᴅᴇ: {'🟢' if cfg.get('raid_mode', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}raid_mode",
                ),
                InlineKeyboardButton(
                    f"🧹 ᴄʟᴇᴀɴ sᴇʀᴠɪᴄᴇ: {'🟢' if cfg.get('clean_service', False) else '🔴'}",
                    callback_data=f"hdesk{HD_SEP}tcfg{HD_SEP}clean_service",
                ),
            ],
            [
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"hdesk{HD_SEP}home"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
                InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
            ],
        ]
        return text, InlineKeyboardMarkup(s_rows)
    elif section == "notes_help":
        text = (
            "📝 <b>ɴᴏᴛᴇs, ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs, ʙʟᴀᴄᴋʟɪsᴛ, ᴀғᴋ &amp; ᴛᴀɢᴀʟʟ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 <b>ɢʀᴏᴜᴘ &amp; ᴄʜᴀɴɴᴇʟ ɴᴏᴛᴇs:</b>\n"
            "• <code>/save &lt;name&gt; &lt;text&gt;</code> — sᴀᴠᴇ ᴀ ɴᴏᴛᴇ (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴍᴇᴅɪᴀ/ᴛᴇxᴛ)\n"
            "• <code>/get &lt;name&gt;</code> ᴏʀ <code>#name</code> — ғᴇᴛᴄʜ sᴀᴠᴇᴅ ɴᴏᴛᴇ\n"
            "• <code>/notes</code> │ <code>/saved</code> — ʟɪsᴛ ᴀʟʟ ɴᴏᴛᴇs ɪɴ ᴄʜᴀᴛ\n"
            "• <code>/clear &lt;name&gt;</code> │ <code>/clearall</code> — ᴅᴇʟᴇᴛᴇ ɴᴏᴛᴇ(s)\n\n"
            "⚡ <b>ᴄᴜsᴛᴏᴍ ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs (ᴋᴇʏᴡᴏʀᴅ ʀᴇᴘʟɪᴇs):</b>\n"
            "• <code>/filter &lt;keyword&gt; &lt;reply&gt;</code> — ᴀᴅᴅ ᴀᴜᴛᴏ-ʀᴇᴘʟʏ ғɪʟᴛᴇʀ\n"
            "• <code>/filters</code> — ʟɪsᴛ ᴀᴄᴛɪᴠᴇ ғɪʟᴛᴇʀs\n"
            "• <code>/stop &lt;keyword&gt;</code> │ <code>/stopall</code> — ʀᴇᴍᴏᴠᴇ ғɪʟᴛᴇʀ(s)\n\n"
            "🚫 <b>ᴡᴏʀᴅ ʙʟᴀᴄᴋʟɪsᴛ &amp; ᴄᴇɴsᴏʀ:</b>\n"
            "• <code>/addblacklist &lt;word&gt;</code> │ <code>/rmblacklist &lt;word&gt;</code>\n"
            "• <code>/blacklist</code> │ <code>/unblacklistall</code>\n"
            "• <code>/blacklistmode &lt;delete|warn|mute|ban|kick&gt;</code>\n\n"
            "🌟 <b>ᴀғᴋ, ᴛᴀɢᴀʟʟ, ᴘᴏʟʟs &amp; ᴄʟᴇᴀɴᴇʀ:</b>\n"
            "• <code>/afk [reason]</code> — sᴇᴛ ᴀᴡᴀʏ sᴛᴀᴛᴜs ᴡɪᴛʜ ᴀᴜᴛᴏ-ʀᴇᴘʟʏ\n"
            "• <code>/tagall [text]</code> │ <code>/mentionall</code> │ <code>/canceltag</code>\n"
            "• <code>/zombies</code> — ғɪɴᴅ &amp; ᴄʟᴇᴀɴ ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛs\n"
            "• <code>/bots</code> — ʟɪsᴛ ʙᴏᴛs │ <code>/poll Q | Opt1 | Opt2</code>"
        )
    elif section == "web_help":
        text = (
            "🌐 <b>24/7 ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ &amp; ᴡᴇʙ ᴜᴛɪʟɪᴛɪᴇs</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🌐 <b>24/7 ᴜᴘᴛɪᴍᴇ &amp; ᴡᴇʙ ᴛᴏᴏʟs:</b>\n"
            "• <code>/uptime &lt;url&gt;</code> │ <code>/listup</code> │ <code>/revoke</code>\n"
            "• <code>/web &lt;url&gt;</code> │ <code>/webss &lt;url&gt;</code> │ <code>/fwebss &lt;url&gt;</code>\n"
            "• <code>/tr &lt;lang&gt;</code> │ <code>/paste</code> │ <code>/calc</code> │ <code>/qr</code> │ <code>/stickerid</code>"
        )
    else:
        text = (
            "🛡️ <b>ᴍᴍᴡ ʙᴏᴛᴢ ᴘʀᴏ ᴇxᴛʀᴀ ɢʀᴏᴜᴘ &amp; ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴅᴇsᴋ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 <b>ʀᴇǫᴜɪʀᴇᴍᴇɴᴛ:</b> ᴀᴅᴅ ᴛʜᴇ ʙᴏᴛ ᴀs <b>ᴀᴅᴍɪɴ</b> ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴏʀ ᴄʜᴀɴɴᴇʟ.\n"
            "🔐 <b>ᴀᴄᴄᴇss:</b> ɢʀᴏᴜᴘ/ᴄʜᴀɴɴᴇʟ ᴏᴡɴᴇʀ • ᴀᴅᴍɪɴs • ᴍᴍᴡ ʙᴏᴛᴢ ᴀᴅᴍɪɴs\n\n"
            "📂 <b>ᴄᴏᴍᴘʟᴇᴛᴇ ᴍᴏᴅᴜʟᴇs:</b>\n"
            "• 👑 <b>ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ:</b> ᴘʀᴏᴍᴏᴛᴇ, ᴅᴇᴍᴏᴛᴇ, ᴛɪᴛʟᴇ, ᴘɪɴ, ᴜɴᴘɪɴ, sᴛᴀғғ, ɪɴᴠɪᴛᴇs\n"
            "• 👥 <b>ᴍᴇᴍʙᴇʀs:</b> ʙᴀɴ, ᴛʙᴀɴ, ᴋɪᴄᴋ, ᴍᴜᴛᴇ, ᴛᴍᴜᴛᴇ, ʀᴇsᴛʀɪᴄᴛ, ᴀᴘᴘʀᴏᴠᴇ\n"
            "• 🔨 <b>ᴍᴏᴅᴇʀᴀᴛɪᴏɴ:</b> ᴘᴜʀɢᴇ, ᴅᴇʟ, ᴡᴀʀɴ sʏsᴛᴇᴍ, ʀᴇᴘᴏʀᴛs, ʀᴜʟᴇs, ʟᴏɢs\n"
            "• 🔐 <b>sᴇᴄᴜʀɪᴛʏ:</b> 16 ʟᴏᴄᴋs, ᴀɴᴛɪ-ғʟᴏᴏᴅ, ᴀɴᴛɪ-sᴘᴀᴍ, ʀᴀɪᴅ ᴍᴏᴅᴇ, ᴠᴇʀɪғʏ\n"
            "• 📝 <b>ɴᴏᴛᴇs &amp; ғɪʟᴛᴇʀs:</b> sᴀᴠᴇᴅ ɴᴏᴛᴇs, ᴀᴜᴛᴏ-ғɪʟᴛᴇʀs, ʙʟᴀᴄᴋʟɪsᴛ, ᴀғᴋ, ᴛᴀɢᴀʟʟ\n"
            "• 📢 <b>ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ:</b> ᴄᴏɴɴᴇᴄᴛ, ᴄᴘᴏsᴛ, ᴄᴇᴅɪᴛ, ᴀᴜᴛᴏ-ғᴏᴏᴛᴇʀ, ᴄsᴛᴀᴛs\n\n"
            "👇 <b>sᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ʙᴇʟᴏᴡ:</b>"
        )

    bottom_row = (
        [
            InlineKeyboardButton("🏠 ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ]
        if section == "home"
        else [
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"hdesk{HD_SEP}home"),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data"),
        ]
    )
    nav_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👑 ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ", callback_data=f"hdesk{HD_SEP}admin"),
            InlineKeyboardButton("👥 ᴍᴇᴍʙᴇʀs", callback_data=f"hdesk{HD_SEP}members"),
        ],
        [
            InlineKeyboardButton("🔨 ᴍᴏᴅᴇʀᴀᴛɪᴏɴ", callback_data=f"hdesk{HD_SEP}moderation"),
            InlineKeyboardButton("🔐 sᴇᴄᴜʀɪᴛʏ & ʟᴏᴄᴋs", callback_data=f"hdesk{HD_SEP}security"),
        ],
        [
            InlineKeyboardButton("📝 ɴᴏᴛᴇs & ғɪʟᴛᴇʀs", callback_data=f"hdesk{HD_SEP}notes_help"),
            InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟ ʜᴇʟᴘᴇʀ", callback_data="chdesk|home"),
        ],
        [
            InlineKeyboardButton("⚙️ ʟɪᴠᴇ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs", callback_data=f"hdesk{HD_SEP}settings"),
            InlineKeyboardButton("🎛️ ғᴇᴀᴛᴜʀᴇ & ʟɪᴍɪᴛ ᴇᴅɪᴛᴏʀ", callback_data="gfeat|open"),
        ],
        [
            InlineKeyboardButton("🎨 ᴘᴏsᴛ & ʙᴜᴛᴛᴏɴ sᴛᴜᴅɪᴏ", callback_data="postedit|home"),
            InlineKeyboardButton("🌐 ᴜᴘᴛɪᴍᴇ & ᴡᴇʙ ᴛᴏᴏʟs", callback_data=f"hdesk{HD_SEP}web_help"),
        ],
        bottom_row,
    ])
    return text, nav_buttons


@Client.on_message(filters.command(["helpdesk", "ghelp", "modhelp", "grphelp"], prefixes=COMMAND_PREFIXES))
async def hd_helpdesk_cmd(client: Client, message: Message):
    cfg = {}
    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        cfg = await db.get_helpdesk_config(message.chat.id)
    text, markup = build_helpdesk_menu_view("home", cfg)
    await message.reply_text(
        text,
        reply_markup=markup,
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^hdesk\|"))
async def hd_callback_router(client: Client, query: CallbackQuery):
    parts = query.data.split(HD_SEP)
    action = parts[1] if len(parts) > 1 else "home"
    chat_id = query.message.chat.id if query.message else 0
    caller_id = query.from_user.id if query.from_user else 0

    if action == "verify_me":
        target_uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if caller_id != target_uid and not await is_user_group_or_global_admin(client, chat_id, caller_id):
            return await query.answer("⛔ ᴛʜɪs ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ʙᴜᴛᴛᴏɴ ɪs ɴᴏᴛ ғᴏʀ ʏᴏᴜ!", show_alert=True)
        task = _PENDING_VERIFICATIONS.pop((chat_id, target_uid), None)
        if task:
            task.cancel()
        try:
            await client.restrict_chat_member(chat_id, target_uid, permissions=UNMUTE_PERMS)
        except Exception:
            pass
        await query.answer("✅ ᴠᴇʀɪғɪᴇᴅ! ʏᴏᴜ ᴄᴀɴ ɴᴏᴡ ᴄʜᴀᴛ.", show_alert=True)
        try:
            await query.message.edit_text(
                f"✅ <b>ᴍᴇᴍʙᴇʀ <a href='tg://user?id={target_uid}'>{target_uid}</a> ᴠᴇʀɪғɪᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if action in ("home", "admin", "members", "moderation", "security", "settings", "notes_help", "web_help"):
        cfg = await db.get_helpdesk_config(chat_id) if chat_id < 0 else {}
        text, markup = build_helpdesk_menu_view(action, cfg)
        await query.answer()
        try:
            await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        return

    if chat_id < 0 and not await is_user_group_or_global_admin(client, chat_id, caller_id):
        return await query.answer("⛔ ᴀᴅᴍɪɴs ᴏɴʟʏ!", show_alert=True)

    if action == "tlock" and len(parts) > 2:
        if chat_id >= 0:
            return await query.answer("⚠️ ᴏᴘᴇɴ /helpdesk ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ ᴛᴏ ᴛᴏɢɢʟᴇ ʟᴏᴄᴋs!", show_alert=True)
        lk = parts[2]
        cfg = await db.get_helpdesk_config(chat_id)
        curr_locked = bool(cfg.get("locks", {}).get(lk, False))
        cfg = await db.set_group_lock(chat_id, lk, not curr_locked)
        await query.answer(f"{'🔒 ʟᴏᴄᴋᴇᴅ' if not curr_locked else '🔓 ᴜɴʟᴏᴄᴋᴇᴅ'}: {lk.upper()}")
        text, markup = build_helpdesk_menu_view("security", cfg)
        try:
            await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        return

    if action == "tcfg" and len(parts) > 2:
        if chat_id >= 0:
            return await query.answer("⚠️ ᴏᴘᴇɴ /helpdesk ɪɴsɪᴅᴇ ᴀ ɢʀᴏᴜᴘ ᴛᴏ ᴄʜᴀɴɢᴇ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs!", show_alert=True)
        field = parts[2]
        cfg = await db.get_helpdesk_config(chat_id)
        new_val = not bool(cfg.get(field, False))
        cfg = await db.update_helpdesk_config(chat_id, {field: new_val})
        await query.answer(f"✅ {field.upper()}: {'ᴏɴ' if new_val else 'ᴏғғ'}")
        text, markup = build_helpdesk_menu_view("settings", cfg)
        try:
            await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
        return

    if action == "confirm_unpinall":
        try:
            await client.unpin_all_chat_messages(chat_id)
            await query.answer("📌 ᴀʟʟ ᴍᴇssᴀɢᴇs ᴜɴᴘɪɴɴᴇᴅ!", show_alert=True)
            await query.message.edit_text("📌 <b>ᴀʟʟ ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇs ʜᴀᴠᴇ ʙᴇᴇɴ ᴜɴᴘɪɴɴᴇᴅ!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "confirm_purge" and len(parts) > 2:
        pid = parts[2]
        pinfo = _PENDING_PURGES.pop(pid, None)
        if not pinfo:
            return await query.answer("ℹ️ ᴘᴜʀɢᴇ sᴇssɪᴏɴ ᴇxᴘɪʀᴇᴅ.", show_alert=True)
        ids = list(range(pinfo["start_id"], pinfo["end_id"] + 1))[:200]
        await query.answer("🧹 ᴘᴜʀɢɪɴɢ ᴍᴇssᴀɢᴇs...")
        try:
            for i in range(0, len(ids), 100):
                await client.delete_messages(chat_id, ids[i : i + 100])
            await query.message.edit_text(f"🧹 <b>ᴘᴜʀɢᴇᴅ <code>{len(ids)}</code> ᴍᴇssᴀɢᴇs!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.message.edit_text(f"❌ <b>ᴘᴜʀɢᴇ ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)
        return

    if action == "unban" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.unban_chat_member(chat_id, uid)
            await query.answer("🔓 ᴜsᴇʀ ᴜɴʙᴀɴɴᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"🔓 <b>ᴜsᴇʀ <code>{uid}</code> ᴜɴʙᴀɴɴᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "unmute" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.restrict_chat_member(chat_id, uid, permissions=UNMUTE_PERMS)
            await query.answer("🔊 ᴜsᴇʀ ᴜɴᴍᴜᴛᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"🔊 <b>ᴜsᴇʀ <code>{uid}</code> ᴜɴᴍᴜᴛᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "ban" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.ban_chat_member(chat_id, uid)
            await query.answer("🚫 ᴜsᴇʀ ʙᴀɴɴᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"🚫 <b>ᴜsᴇʀ <code>{uid}</code> ʙᴀɴɴᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "mute" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.restrict_chat_member(chat_id, uid, permissions=MUTE_PERMS)
            await query.answer("🔇 ᴜsᴇʀ ᴍᴜᴛᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"🔇 <b>ᴜsᴇʀ <code>{uid}</code> ᴍᴜᴛᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "warn" and len(parts) > 2:
        uid = int(parts[2])
        cnt, _ = await db.add_warning(chat_id, uid, "Reported message violation", caller_id)
        await query.answer(f"⚠️ ᴡᴀʀɴᴇᴅ ({cnt})!", show_alert=True)
        await query.message.edit_text(f"⚠️ <b>ᴜsᴇʀ <code>{uid}</code> ᴡᴀʀɴᴇᴅ ({cnt}) ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        return

    if action == "resetwarn" and len(parts) > 2:
        uid = int(parts[2])
        await db.reset_warnings(chat_id, uid)
        await query.answer("✅ ᴡᴀʀɴɪɴɢs ᴄʟᴇᴀʀᴇᴅ!", show_alert=True)
        await query.message.edit_text(f"✅ <b>ᴡᴀʀɴɪɴɢs ᴄʟᴇᴀʀᴇᴅ ғᴏʀ <code>{uid}</code>!</b>", parse_mode=enums.ParseMode.HTML)
        return

    if action == "delmsg" and len(parts) > 2:
        mid = int(parts[2])
        try:
            await client.delete_messages(chat_id, [mid])
            await query.answer("🗑️ ʀᴇᴘᴏʀᴛᴇᴅ ᴍᴇssᴀɢᴇ ᴅᴇʟᴇᴛᴇᴅ!", show_alert=True)
            await query.message.edit_text("🗑️ <b>ʀᴇᴘᴏʀᴛᴇᴅ ᴍᴇssᴀɢᴇ ᴅᴇʟᴇᴛᴇᴅ ʙʏ ᴀᴅᴍɪɴ.</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "jreq_ok" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.approve_chat_join_request(chat_id, uid)
            await db.add_approved_user(chat_id, uid)
            await query.answer("✅ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴀᴘᴘʀᴏᴠᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"✅ <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ғᴏʀ <code>{uid}</code> ᴀᴘᴘʀᴏᴠᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return

    if action == "jreq_no" and len(parts) > 2:
        uid = int(parts[2])
        try:
            await client.decline_chat_join_request(chat_id, uid)
            await query.answer("🚫 ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴅᴇᴄʟɪɴᴇᴅ!", show_alert=True)
            await query.message.edit_text(f"🚫 <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ғᴏʀ <code>{uid}</code> ᴅᴇᴄʟɪɴᴇᴅ ʙʏ {query.from_user.mention}!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)
        return


@Client.on_chat_join_request()
async def hd_join_request_watchdog(client: Client, request: ChatJoinRequest):
    chat = request.chat
    user = request.from_user
    if not chat or not user:
        return

    cfg = await db.get_helpdesk_config(chat.id)
    if cfg.get("raid_mode"):
        try:
            await client.decline_chat_join_request(chat.id, user.id)
        except Exception:
            pass
        return
    try:
        from plugins.core_db import chat_repo
        chat_doc = await chat_repo.get_or_create_chat(chat.id, chat.title or "Chat", str(chat.type))
        if chat_doc.get("auto_approve_joins", True):
            return
    except Exception:
        pass

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ᴀᴘᴘʀᴏᴠᴇ", callback_data=f"hdesk{HD_SEP}jreq_ok{HD_SEP}{user.id}"),
            InlineKeyboardButton("🚫 ᴅᴇᴄʟɪɴᴇ", callback_data=f"hdesk{HD_SEP}jreq_no{HD_SEP}{user.id}"),
        ]
    ])
    try:
        await client.send_message(
            chat.id,
            f"🔔 <b>ɴᴇᴡ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ</b>\n"
            f"• <b>ᴜsᴇʀ:</b> {user.mention} (<code>{user.id}</code>)\n"
            f"• <b>ᴀᴅᴍɪɴs:</b> ᴛᴀᴘ ʙᴇʟᴏᴡ ᴏʀ ᴜsᴇ <code>/approve {user.id}</code>",
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


@Client.on_message(filters.group & filters.new_chat_members, group=2)
async def hd_welcome_and_raid_handler(client: Client, message: Message):
    chat_id = message.chat.id
    cfg = await db.get_helpdesk_config(chat_id)
    locks = cfg.get("locks", {})

    if cfg.get("clean_service"):
        try:
            await message.delete()
        except Exception:
            pass

    for u in message.new_chat_members:
        if u.id == temp.ME:
            continue

        if u.is_bot and locks.get("bots"):
            adder_id = message.from_user.id if message.from_user else 0
            if not await is_user_group_or_global_admin(client, chat_id, adder_id):
                try:
                    await client.ban_chat_member(chat_id, u.id)
                    await message.reply_text(f"🤖 <b>ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ ʙᴏᴛ {u.mention} ʀᴇᴍᴏᴠᴇᴅ (ʙᴏᴛ ʟᴏᴄᴋ ᴀᴄᴛɪᴠᴇ)!</b>", parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                continue

        if cfg.get("raid_mode"):
            try:
                await client.ban_chat_member(chat_id, u.id, until_date=datetime.now() + timedelta(hours=1))
                continue
            except Exception:
                pass

        if cfg.get("verify_new_members") and not u.is_bot:
            try:
                await client.restrict_chat_member(chat_id, u.id, permissions=MUTE_PERMS)
                vbtn = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ ɪ ᴀᴍ ʜᴜᴍᴀɴ (ᴛᴀᴘ ᴛᴏ ᴠᴇʀɪғʏ)", callback_data=f"hdesk{HD_SEP}verify_me{HD_SEP}{u.id}")]
                ])
                vmsg = await message.reply_text(
                    f"👋 <b>ᴡᴇʟᴄᴏᴍᴇ {u.mention}!</b>\n"
                    "🔒 <i>ᴘʟᴇᴀsᴇ ᴛᴀᴘ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴡɪᴛʜɪɴ 120 sᴇᴄᴏɴᴅs ᴛᴏ ᴠᴇʀɪғʏ ᴀɴᴅ ᴜɴʟᴏᴄᴋ ᴄʜᴀᴛᴛɪɴɢ.</i>",
                    reply_markup=vbtn,
                    parse_mode=enums.ParseMode.HTML,
                )

                async def _expire_verification(cid: int, uid: int, msg_obj: Message):
                    try:
                        await asyncio.sleep(120)
                        _PENDING_VERIFICATIONS.pop((cid, uid), None)
                        await msg_obj.delete()
                    except Exception:
                        pass

                _PENDING_VERIFICATIONS[(chat_id, u.id)] = asyncio.create_task(_expire_verification(chat_id, u.id, vmsg))
                continue
            except Exception:
                pass

        if cfg.get("welcome_enabled", True):
            tpl = cfg.get("welcome_text") or "👋 ʜᴇʏ {mention}, ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ <b>{chat_title}</b>!"
            mem_count = 0
            if "{count}" in tpl:
                try:
                    mem_count = await client.get_chat_members_count(chat_id)
                except Exception:
                    mem_count = 0
            chat_title_esc = html.escape(message.chat.title or "Group")
            rendered = (
                tpl.replace("{mention}", u.mention)
                .replace("{first}", html.escape(u.first_name or "Member"))
                .replace("{id}", str(u.id))
                .replace("{chat_title}", chat_title_esc)
                .replace("{chat}", chat_title_esc)
                .replace("{count}", str(mem_count))
            )
            if temp.MELCOW.get(chat_id):
                try:
                    await temp.MELCOW[chat_id].delete()
                except Exception:
                    pass
            try:
                temp.MELCOW[chat_id] = await message.reply_text(
                    rendered,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📜 ɢʀᴏᴜᴘ ʀᴜʟᴇs", callback_data=f"hdesk{HD_SEP}moderation")]
                    ]),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass


@Client.on_message(filters.group & filters.left_chat_member, group=2)
async def hd_goodbye_handler(client: Client, message: Message):
    cfg = await db.get_helpdesk_config(message.chat.id)
    if cfg.get("clean_service"):
        try:
            await message.delete()
        except Exception:
            pass
    if cfg.get("goodbye_enabled") and message.left_chat_member and message.left_chat_member.id != temp.ME:
        u = message.left_chat_member
        tpl = cfg.get("goodbye_text") or "👋 ɢᴏᴏᴅʙʏᴇ {mention}!"
        rendered = (
            tpl.replace("{mention}", u.mention)
            .replace("{first}", html.escape(u.first_name or "Member"))
            .replace("{chat_title}", html.escape(message.chat.title or "Group"))
        )
        try:
            await message.reply_text(rendered, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


def _check_message_violates_locks(message: Message, locks: Dict[str, Any]) -> Optional[str]:
    if locks.get("all") or locks.get("msg"):
        return "all messages"
    if locks.get("forward") and (message.forward_date or message.forward_from or message.forward_from_chat):
        return "forwarded messages"
    if locks.get("via_bot") or (locks.get("inline") and message.via_bot):
        return "inline bots"
    if locks.get("polls") and message.poll:
        return "polls"
    if locks.get("stickers") and message.sticker:
        return "stickers"
    if locks.get("gifs") and message.animation:
        return "GIFs"
    if locks.get("document") and message.document:
        return "documents"
    if locks.get("photo") and message.photo:
        return "photos"
    if locks.get("video") and (message.video or message.video_note):
        return "videos"
    if locks.get("audio") and message.audio:
        return "audio files"
    if locks.get("voice") and message.voice:
        return "voice notes"
    if locks.get("contact") and message.contact:
        return "contacts"
    if locks.get("media") and (
        message.photo or message.video or message.audio or message.document or message.voice or message.video_note
    ):
        return "media"
    if locks.get("button") and message.reply_markup and getattr(message.reply_markup, "inline_keyboard", None):
        return "inline buttons"
    txt = message.text or message.caption or ""
    ents = list(message.entities or []) + list(message.caption_entities or [])
    if locks.get("emoji"):
        if getattr(message, "dice", None) or EMOJI_DETECTOR_RE.search(txt):
            return "emojis"
        custom_emoji_type = getattr(enums.MessageEntityType, "CUSTOM_EMOJI", None)
        if custom_emoji_type and any(ent.type == custom_emoji_type for ent in ents):
            return "emojis"
    if locks.get("username"):
        if USERNAME_DETECTOR_RE.search(txt) or any(ent.type == enums.MessageEntityType.MENTION for ent in ents):
            return "usernames"
    if locks.get("links") or locks.get("url"):
        if URL_DETECTOR_RE.search(txt):
            return "urls/links"
        for ent in ents:
            if ent.type in (enums.MessageEntityType.URL, enums.MessageEntityType.TEXT_LINK):
                return "urls/links"
    return None


@Client.on_message(filters.group & filters.incoming & ~filters.service, group=3)
async def hd_security_and_flood_watchdog(client: Client, message: Message):
    if not message.from_user or message.from_user.is_self:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    cfg = await db.get_helpdesk_config(chat_id)
    react_emoji = (cfg.get("auto_react_emoji") or "").strip()
    if react_emoji:
        try:
            await client.send_reaction(chat_id, message.id, react_emoji)
        except Exception:
            pass
    if cfg.get("clean_commands") and message.text and message.text.startswith(tuple(COMMAND_PREFIXES)):
        async def _del_cmd_later(msg_obj: Message):
            await asyncio.sleep(8)
            try:
                await msg_obj.delete()
            except Exception:
                pass
        asyncio.create_task(_del_cmd_later(message))
    locks = cfg.get("locks", {})
    any_lock = any(locks.values())
    antiflood = cfg.get("antiflood_enabled", False)
    antidup = cfg.get("antidup_enabled", False)
    antispam = cfg.get("antispam_enabled", False)

    if not (any_lock or antiflood or antidup or antispam):
        return

    if await db.is_user_approved(chat_id, user_id):
        return
    if await is_user_group_or_global_admin(client, chat_id, user_id):
        return

    if any_lock:
        violated = _check_message_violates_locks(message, locks)
        if violated:
            try:
                await message.delete()
            except Exception:
                pass
            return

    raw_txt = (message.text or message.caption or "").strip()
    if antispam and raw_txt and SPAM_PATTERNS_RE.search(raw_txt):
        try:
            await message.delete()
        except Exception:
            pass
        return

    now = time.monotonic()
    if antidup and len(raw_txt) >= 1:
        s_limit = max(2, int(cfg.get("spam_limit", 3) or 3))
        s_window = max(5, float(cfg.get("spam_window", 30) or 30))
        prev = _LAST_MSG_HASH.get((chat_id, user_id))
        if prev and prev[0] == raw_txt.lower() and (now - prev[1]) < s_window:
            count_dup = prev[2] + 1
            _LAST_MSG_HASH[(chat_id, user_id)] = (raw_txt.lower(), prev[1], count_dup)
            if count_dup >= s_limit:
                _LAST_MSG_HASH.pop((chat_id, user_id), None)
                try:
                    await message.delete()
                except Exception:
                    pass
                w_cnt, _ = await db.add_warning(chat_id, user_id, "Repeating identical spam messages", 0)
                if w_cnt >= 2:
                    await db.reset_warnings(chat_id, user_id)
                    try:
                        await client.ban_chat_member(chat_id, user_id)
                        await client.send_message(
                            chat_id,
                            f"🚫 {message.from_user.mention} <b>ʙᴀɴɴᴇᴅ ғᴏʀ ʀᴇᴘᴇᴀᴛᴇᴅ sᴘᴀᴍ ᴍᴇssᴀɢᴇs!</b>",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await client.send_message(
                            chat_id,
                            f"⚠️ {message.from_user.mention} <b>ᴡᴀʀɴᴇᴅ (1/2) ғᴏʀ ʀᴇᴘᴇᴀᴛɪɴɢ ᴛʜᴇ sᴀᴍᴇ ᴍᴇssᴀɢᴇ! ɴᴇxᴛ ᴠɪᴏʟᴀᴛɪᴏɴ ᴡɪʟʟ ʙᴀɴ.</b>",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        pass
                return
        else:
            _LAST_MSG_HASH[(chat_id, user_id)] = (raw_txt.lower(), now, 1)

    if antiflood:
        flimit = int(cfg.get("flood_limit", 5) or 5)
        fwin = float(cfg.get("flood_window", 5) or 5)
        faction = str(cfg.get("flood_action", "mute")).lower()
        dq = _FLOOD_TRACKER[(chat_id, user_id)]
        dq.append(now)
        while dq and (now - dq[0]) > fwin:
            dq.popleft()
        if len(dq) >= flimit:
            dq.clear()
            try:
                await message.delete()
            except Exception:
                pass
            try:
                if faction == "ban":
                    await client.ban_chat_member(chat_id, user_id)
                    act_lbl = "🚫 ʙᴀɴɴᴇᴅ"
                elif faction == "kick":
                    await client.ban_chat_member(chat_id, user_id)
                    await asyncio.sleep(0.5)
                    await client.unban_chat_member(chat_id, user_id)
                    act_lbl = "🥾 ᴋɪᴄᴋᴇᴅ"
                else:
                    await client.restrict_chat_member(
                        chat_id,
                        user_id,
                        permissions=MUTE_PERMS,
                        until_date=datetime.now() + timedelta(minutes=15),
                    )
                    act_lbl = "🔇 ᴍᴜᴛᴇᴅ (15ᴍ)"
                await client.send_message(
                    chat_id,
                    f"🌊 <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴛʀɪɢɢᴇʀᴇᴅ!</b>\n"
                    f"• <b>ᴜsᴇʀ:</b> {message.from_user.mention} (<code>{user_id}</code>)\n"
                    f"• <b>ᴀᴄᴛɪᴏɴ:</b> <b>{act_lbl}</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass


# ─── Extra Pro Group Helper Features (/nightmode, /gautoreact, /cleanblue) ────

@Client.on_message(filters.command(["nightmode", "gautoreact", "cleanblue"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def hd_extra_pro_group_cmds(client: Client, message: Message):
    """Toggle Night Mode (media/sticker/link lock), Group Auto-Reaction, or Clean Blue Command Deletion."""
    ok, chat_id, _, cfg = await verify_group_and_permissions(
        client, message, require_bot_admin=False, allow_channel=False
    )
    if not ok:
        return

    cmd = message.command[0].lower()
    arg = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ""

    if cmd == "nightmode":
        nm = not bool(cfg.get("nightmode_enabled", False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_helpdesk_config(
            chat_id,
            {
                "nightmode_enabled": nm,
                "locks": {"media": nm, "stickers": nm, "gifs": nm, "links": nm},
            },
        )
        return await message.reply_text(
            f"🌙 <b>ɢʀᴏᴜᴘ ɴɪɢʜᴛ ᴍᴏᴅᴇ:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ (ᴍᴇᴅɪᴀ/sᴛɪᴄᴋᴇʀs/ʟɪɴᴋs ʟᴏᴄᴋᴇᴅ)' if nm else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "gautoreact":
        if not arg or arg.lower() in ("off", "none", "disable"):
            await db.update_helpdesk_config(chat_id, {"auto_react_emoji": ""})
            return await message.reply_text("⚪ <b>ɢʀᴏᴜᴘ ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ ᴅɪsᴀʙʟᴇᴅ!</b>", quote=True, parse_mode=enums.ParseMode.HTML)
        emoji_val = arg.split()[0][:4]
        await db.update_helpdesk_config(chat_id, {"auto_react_emoji": emoji_val})
        return await message.reply_text(
            f"🎭 <b>ɢʀᴏᴜᴘ ᴀᴜᴛᴏ-ʀᴇᴀᴄᴛ sᴇᴛ ᴛᴏ:</b> {html.escape(emoji_val)}",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    if cmd == "cleanblue":
        cb = not bool(cfg.get("clean_commands", False)) if not arg else (arg.lower() in ("on", "true", "1", "yes", "enable"))
        await db.update_helpdesk_config(chat_id, {"clean_commands": cb})
        return await message.reply_text(
            f"🧹 <b>ᴀᴜᴛᴏ-ᴄʟᴇᴀɴ ᴄᴏᴍᴍᴀɴᴅ ᴍᴇssᴀɢᴇs:</b> <code>{'🟢 ᴇɴᴀʙʟᴇᴅ' if cb else '🔴 ᴅɪsᴀʙʟᴇᴅ'}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
