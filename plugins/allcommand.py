import os
import re
import io
import time
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Any

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from info import ADMINS, LOG_CHANNEL, COMMAND_PREFIXES
from utils import to_smallcap, is_admin_sync

logger = logging.getLogger("ExCommands")


class CommandExtractorEngine:
    """
    Dynamically scans plugins and repository source code in real time.
    Whenever any new command is added to the codebase, this engine
    extracts it instantly without needing manual registry updates.
    """

    ADMIN_KEYWORDS = {
        "admin", "admins", "owner", "auth", "sudo", "broadcast",
        "banned", "unban", "delete", "eval", "restart", "logs", "upad"
    }

    @classmethod
    def _is_admin_context(cls, file_path: str, code_snippet: str, func_name: str) -> bool:
        """Determines if a command requires admin authorization."""
        lowered_path = file_path.lower()
        lowered_code = code_snippet.lower()
        lowered_fn = func_name.lower()

        if any(keyword in lowered_path for keyword in ["broadcast", "banned", "kill", "eval"]):
            return True

        patterns = [
            r"filters\.user\(.*admin",
            r"filters\.user\(.*auth",
            r"admin_only",
            r"is_admin",
            r"is_authorized_admin",
            r"verify_group_and_permissions",
            r"verify_channel_access",
            r"from_user\.id\s*(?:in|==)\s*admins",
            r"from_user\.id\s*(?:not in|!=)\s*admins",
            r"message\.from_user\.id\s*(?:in|==)\s*admins",
        ]
        for pattern in patterns:
            if re.search(pattern, lowered_code):
                return True

        if any(keyword in lowered_fn for keyword in cls.ADMIN_KEYWORDS):
            return True

        return False

    @classmethod
    def scan_py_file(cls, file_path: str) -> List[Dict[str, Any]]:
        """Parses a Python file using Regex to find all registered bot commands."""
        extracted = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            cmd_regex = re.compile(
                r"filters\.command\s*\(\s*(\[[^\]]+\]|\"[^\"]+\"|'[^']+')\s*(?:,\s*(?:prefixes\s*=\s*)?([^\)]+))?\)",
                re.DOTALL
            )

            for match in cmd_regex.finditer(content):
                raw_cmds = match.group(1).strip()
                match_start = match.start()

                context_window = content[match_start: match_start + 600]

                found_cmds = []
                if raw_cmds.startswith("[") and raw_cmds.endswith("]"):
                    items = re.findall(r"['\"]([a-zA-Z0-9_\-]+)['\"]", raw_cmds)
                    found_cmds.extend(items)
                else:
                    item = re.search(r"['\"]([a-zA-Z0-9_\-]+)['\"]", raw_cmds)
                    if item:
                        found_cmds.append(item.group(1))

                fn_match = re.search(r"async\s+def\s+([a-zA-Z0-9_]+)\s*\(", context_window)
                fn_name = fn_match.group(1) if fn_match else "unknown_handler"

                doc_match = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', context_window, re.DOTALL)
                description = ""
                if doc_match:
                    doc = doc_match.group(1) or doc_match.group(2)
                    description = doc.strip().split("\n")[0]

                is_admin = cls._is_admin_context(file_path, context_window, fn_name)

                for cmd in found_cmds:
                    cmd_clean = cmd.strip().lower()
                    if not cmd_clean:
                        continue
                    extracted.append({
                        "command": cmd_clean,
                        "file": os.path.relpath(file_path),
                        "handler": fn_name,
                        "is_admin": is_admin,
                        "desc": description or "Pro Bot Command Handler"
                    })

        except Exception as e:
            logger.warning("⚠️ ғᴀɪʟᴇᴅ ᴛᴏ sᴄᴀɴ %s: %s", file_path, e)

        return extracted

    @classmethod
    def get_all_commands(cls, root_dir: str = ".") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Recursively scans repository plugins and root folders for commands.
        Returns: (admin_commands, user_commands)
        """
        all_entries: Dict[str, Dict[str, Any]] = {}
        target_dirs = ["plugins", "database", "."]

        scanned_files = set()

        for directory in target_dirs:
            if not os.path.exists(directory):
                continue
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".py") and not file.startswith("__"):
                        full_path = os.path.abspath(os.path.join(root, file))
                        if full_path in scanned_files:
                            continue
                        scanned_files.add(full_path)
                        entries = cls.scan_py_file(full_path)
                        for entry in entries:
                            cmd_name = entry["command"]
                            if cmd_name in all_entries:
                                if entry["is_admin"]:
                                    all_entries[cmd_name]["is_admin"] = True
                            else:
                                all_entries[cmd_name] = entry

        admin_cmds = [v for v in all_entries.values() if v["is_admin"]]
        user_cmds = [v for v in all_entries.values() if not v["is_admin"]]

        admin_cmds.sort(key=lambda x: x["command"])
        user_cmds.sort(key=lambda x: x["command"])

        return admin_cmds, user_cmds


def generate_record_file(
    admin_cmds: List[Dict[str, Any]],
    user_cmds: List[Dict[str, Any]],
    bot_name: str,
    admin_user: str
) -> io.BytesIO:
    """Creates a comprehensive text report file containing every detail."""
    buffer = io.BytesIO()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    total_count = len(admin_cmds) + len(user_cmds)

    header = f"""================================================================================
                    BOT COMMANDS AUDIT & EXTRACTION REPORT
================================================================================
Bot Name       : {bot_name}
Extracted By   : {admin_user}
Generated On   : {timestamp}
Total Commands : {total_count}
Admin Commands : {len(admin_cmds)}
User Commands  : {len(user_cmds)}
================================================================================

[+] 🛡️ ADMIN / MODERATOR COMMANDS ({len(admin_cmds)} Commands)
--------------------------------------------------------------------------------
"""
    buffer.write(header.encode("utf-8"))

    for idx, item in enumerate(admin_cmds, 1):
        line = (
            f"{idx:02d}. /{item['command']:<20} | File: {item['file']:<34} "
            f"| Handler: {item['handler']}()\n"
            f"    Description: {item['desc']}\n"
        )
        buffer.write(line.encode("utf-8"))

    mid_section = f"""
--------------------------------------------------------------------------------
[+] 👤 PUBLIC & USER USABLE COMMANDS ({len(user_cmds)} Commands)
--------------------------------------------------------------------------------
"""
    buffer.write(mid_section.encode("utf-8"))

    for idx, item in enumerate(user_cmds, 1):
        line = (
            f"{idx:02d}. /{item['command']:<20} | File: {item['file']:<34} "
            f"| Handler: {item['handler']}()\n"
            f"    Description: {item['desc']}\n"
        )
        buffer.write(line.encode("utf-8"))

    footer = """
================================================================================
Report End. Dynamic scanning ensures that any newly placed commands in 'plugins/'
are always automatically updated on every extraction.
================================================================================
"""
    buffer.write(footer.encode("utf-8"))
    buffer.seek(0)
    buffer.name = f"Commands_Full_Report_{int(time.time())}.txt"
    return buffer


@Client.on_message(filters.command(["excommands", "allcommands", "extractcommands"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def extract_all_commands_handler(client: Client, message: Message):
    """
    STRICTLY FOR ADMINS:
    Scans the entire repository in real time, extracts all commands,
    formats stylish output in small caps with emojis, delivers an in-depth
    record file, and records audit logs.
    """
    user_id = message.from_user.id if message.from_user else 0
    user_name = message.from_user.first_name if message.from_user else "Unknown Admin"

    if not is_admin_sync(user_id):
        logger.warning("🚫 ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ /excommands ᴀᴛᴛᴇᴍᴘᴛ › ᴜsᴇʀ=%s", user_id)
        return await message.reply_text(
            f"⚠️ **{to_smallcap('Access Restricted')}**\n\n"
            f"🔒 {to_smallcap('This command is strictly reserved for Authorized Admins only.')}",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    start_time = time.time()
    status_msg = await message.reply_text(
        f"⚡ **{to_smallcap('Scanning Repository & Plugins')}**...\n"
        f"🔍 {to_smallcap('Parsing all forward slash commands in real-time')}...",
        parse_mode=enums.ParseMode.MARKDOWN,
    )

    try:
        logger.info("⚡ ᴄᴏᴍᴍᴀɴᴅ ᴇxᴛʀᴀᴄᴛɪᴏɴ ɪɴɪᴛɪᴀᴛᴇᴅ › ᴀᴅᴍɪɴ=%s (%s)", user_name, user_id)

        admin_cmds, user_cmds = CommandExtractorEngine.get_all_commands(root_dir=".")
        total_found = len(admin_cmds) + len(user_cmds)
        elapsed_sec = round(time.time() - start_time, 2)

        bot_info = await client.get_me()
        bot_title = bot_info.first_name

        title_tag = f"⚡ **{to_smallcap('Bot Commands Extraction Report')}** ⚡"
        stats_block = (
            f"🤖 **{to_smallcap('Bot')}** : `{bot_title}`\n"
            f"📊 **{to_smallcap('Total Commands')}** : `{total_found}`\n"
            f"🛡️ **{to_smallcap('Admin Commands')}** : `{len(admin_cmds)}`\n"
            f"👤 **{to_smallcap('User Commands')}** : `{len(user_cmds)}`\n"
            f"⏱️ **{to_smallcap('Scan Time')}** : `{elapsed_sec}s`\n"
            f"────────────────────────"
        )

        admin_preview = "\n".join([f"• `/{c['command']}` ➔ 📁 `{c['file'].split('/')[-1]}`" for c in admin_cmds[:120]])
        if len(admin_cmds) > 120:
            admin_preview += f"\n... {to_smallcap('and')} `{len(admin_cmds) - 120}` {to_smallcap('more admin commands')}."

        user_preview = "\n".join([f"• `/{c['command']}` ➔ 📁 `{c['file'].split('/')[-1]}`" for c in user_cmds[:120]])
        if len(user_cmds) > 120:
            user_preview += f"\n... {to_smallcap('and')} `{len(user_cmds) - 120}` {to_smallcap('more user commands')}."

        message_text = (
            f"{title_tag}\n\n"
            f"{stats_block}\n\n"
            f"🛡️ **{to_smallcap('Admin & Moderator Commands')}** ({len(admin_cmds)}):\n"
            f"{admin_preview or '• ' + to_smallcap('None found')}\n\n"
            f"👤 **{to_smallcap('User Usable Commands')}** ({len(user_cmds)}):\n"
            f"{user_preview or '• ' + to_smallcap('None found')}\n\n"
            f"📜 **{to_smallcap('Complete record file Attached Above')}** 👇"
        )

        record_file = generate_record_file(
            admin_cmds=admin_cmds,
            user_cmds=user_cmds,
            bot_name=bot_title,
            admin_user=f"{user_name} ({user_id})",
        )

        caption_text = (
            f"📑 **{to_smallcap('Official Bot Commands Record')}**\n"
            f"📁 {to_smallcap('Contains every forward slash command with source file, handler function, and authorization level.')}\n\n"
            f"✨ {to_smallcap('Auto-updated dynamically on every run.')}"
        )

        await status_msg.delete()
        await message.reply_document(
            document=record_file,
            caption=caption_text,
            parse_mode=enums.ParseMode.MARKDOWN,
        )

        if len(message_text) <= 4096:
            await message.reply_text(
                text=message_text,
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )

        logger.info("✅ ᴇxᴛʀᴀᴄᴛᴇᴅ %s ᴄᴏᴍᴍᴀɴᴅs sᴜᴄᴄᴇssғᴜʟʟʏ ғᴏʀ ᴀᴅᴍɪɴ › %s", total_found, user_id)

        if LOG_CHANNEL:
            try:
                log_text = (
                    f"🔔 **{to_smallcap('Admin Audit Log')} : {to_smallcap('Commands Extracted')}**\n\n"
                    f"👤 **{to_smallcap('Admin')}** : [{user_name}](tg://user?id={user_id}) (`{user_id}`)\n"
                    f"📊 **{to_smallcap('Total Commands')}** : `{total_found}`\n"
                    f"🛡️ **{to_smallcap('Admin Commands')}** : `{len(admin_cmds)}`\n"
                    f"👤 **{to_smallcap('User Commands')}** : `{len(user_cmds)}`\n"
                    f"📅 **{to_smallcap('Time')}** : `{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}`"
                )
                await client.send_message(
                    chat_id=LOG_CHANNEL,
                    text=log_text,
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
            except Exception as log_err:
                logger.warning("⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅɪsᴘᴀᴛᴄʜ ʟᴏɢ ᴍᴇssᴀɢᴇ ᴛᴏ ʟᴏɢ_ᴄʜᴀɴɴᴇʟ: %s", log_err)

    except Exception as e:
        logger.error("❌ ᴇʀʀᴏʀ ᴅᴜʀɪɴɢ ᴄᴏᴍᴍᴀɴᴅ ᴇxᴛʀᴀᴄᴛɪᴏɴ: %s", e, exc_info=True)
        try:
            await status_msg.edit_text(
                f"❌ **{to_smallcap('Extraction Failed')}**\n`{str(e)}`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except Exception:
            await message.reply_text(
                f"❌ **{to_smallcap('Extraction Failed')}**\n`{str(e)}`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
