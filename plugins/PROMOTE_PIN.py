import logging
from pyrogram import Client, filters, enums
from pyrogram.types import ChatPrivileges, Message
from info import COMMAND_PREFIXES
from utils import build_safe_type
from plugins.help_desk_features import (
    verify_group_and_permissions,
    resolve_target_user_and_reason,
    validate_moderation_target,
    dispatch_mod_log,
    PROMOTE_FULL_PRIVS,
)

logger = logging.getLogger("GokuPromoteTier")

HALF_PRIVS = build_safe_type(
    ChatPrivileges,
    can_manage_chat=True,
    can_delete_messages=True,
    can_invite_users=True,
    can_pin_messages=True,
    can_manage_video_chats=True,
)

QUARTER_PRIVS = build_safe_type(
    ChatPrivileges,
    can_manage_chat=True,
    can_invite_users=True,
)


async def _tier_promote_helper(
    client: Client,
    message: Message,
    privileges: ChatPrivileges,
    tier_label: str,
    require_title: bool = False,
):
    ok, _, _, _ = await verify_group_and_permissions(
        client,
        message,
        required_bot_perm="can_promote_members",
        required_user_perm="can_promote_members",
        allow_channel=True,
    )
    if not ok:
        return

    target, custom_title = await resolve_target_user_and_reason(client, message)
    if not await validate_moderation_target(client, message, target, allow_admin_target=True):
        return

    if require_title and not custom_title:
        cmd = message.command[0] if message.command else "ftitle"
        return await message.reply_text(
            f"⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/{cmd} @user &lt;title&gt;</code> (ᴏʀ ʀᴇᴘʟʏ <code>/{cmd} &lt;title&gt;</code>)",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    status_msg = await message.reply_text(
        f"⏳ <b>ᴘʀᴏᴍᴏᴛɪɴɢ {target.mention} ({tier_label})...</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        await client.promote_chat_member(message.chat.id, target.id, privileges=privileges)
        if custom_title:
            try:
                await client.set_administrator_title(message.chat.id, target.id, custom_title[:16])
            except Exception:
                pass
        logger.info(
            "👮 ᴛɪᴇʀ ᴘʀᴏᴍᴏᴛᴇ sᴜᴄᴄᴇss › ᴄʜᴀᴛ=%s ᴛᴀʀɢᴇᴛ=%s ᴛɪᴇʀ=%s ᴛɪᴛʟᴇ=%s",
            message.chat.id,
            target.id,
            tier_label,
            custom_title[:16] if custom_title else "None",
        )
        await status_msg.edit_text(
            f"✅ <b>{tier_label} ᴘʀᴏᴍᴏᴛɪᴏɴ sᴜᴄᴄᴇssғᴜʟ!</b> 👑\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ᴜsᴇʀ:</b> {target.mention} (<code>{target.id}</code>)\n"
            f"• <b>ᴛɪᴇʀ:</b> <code>{tier_label}</code>\n"
            f"• <b>ᴛɪᴛʟᴇ:</b> <code>{custom_title[:16] if custom_title else 'ᴀᴅᴍɪɴ'}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        await dispatch_mod_log(
            client,
            message.chat,
            tier_label,
            message.from_user,
            target,
            extra=f"Title: {custom_title[:16] if custom_title else 'None'}",
        )
    except Exception as e:
        logger.error("❌ ᴛɪᴇʀ ᴘʀᴏᴍᴏᴛᴇ ғᴀɪʟᴇᴅ › ᴄʜᴀᴛ=%s ᴛᴀʀɢᴇᴛ=%s ᴇʀʀ=%s", message.chat.id, target.id, e)
        await status_msg.edit_text(
            f"❌ <b>ᴘʀᴏᴍᴏᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("halfpromote", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def half_promote(client: Client, message: Message):
    """Promote member with half admin privileges (Delete, Invite, Pin, Video Chat)."""
    await _tier_promote_helper(client, message, HALF_PRIVS, "ʜᴀʟғ ᴘʀᴏᴍᴏᴛᴇ")


@Client.on_message(filters.command("qpromote", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def quarter_promote(client: Client, message: Message):
    """Promote member with quarter admin privileges (Invite Users & Manage Chat)."""
    await _tier_promote_helper(client, message, QUARTER_PRIVS, "ǫᴜᴀʀᴛᴇʀ ᴘʀᴏᴍᴏᴛᴇ")


@Client.on_message(filters.command("ftitle", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def full_title(client: Client, message: Message):
    """Full promote a user and assign a custom title in one step."""
    await _tier_promote_helper(client, message, PROMOTE_FULL_PRIVS, "ғᴜʟʟ ᴘʀᴏᴍᴏᴛᴇ + ᴛɪᴛʟᴇ", require_title=True)


@Client.on_message(filters.command("htitle", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def half_title(client: Client, message: Message):
    """Half promote a user and assign a custom title in one step."""
    await _tier_promote_helper(client, message, HALF_PRIVS, "ʜᴀʟғ ᴘʀᴏᴍᴏᴛᴇ + ᴛɪᴛʟᴇ", require_title=True)


@Client.on_message(filters.command("qtitle", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def quarter_title(client: Client, message: Message):
    """Quarter promote a user and assign a custom title in one step."""
    await _tier_promote_helper(client, message, QUARTER_PRIVS, "ǫᴜᴀʀᴛᴇʀ ᴘʀᴏᴍᴏᴛᴇ + ᴛɪᴛʟᴇ", require_title=True)
