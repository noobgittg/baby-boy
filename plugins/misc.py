import ast
import html
import io
import json
import logging
import operator
from urllib.parse import quote_plus
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from info import COMMAND_PREFIXES
from utils import get_file_id
from plugins.webss_logs import paste_to_pasty
from plugins.help_desk_features import hd_info_cmd

logger = logging.getLogger("GokuMisc")

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_math(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_math(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPERATORS:
        left = _safe_eval_math(node.left)
        right = _safe_eval_math(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return _SAFE_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPERATORS:
        return _SAFE_OPERATORS[type(node.op)](_safe_eval_math(node.operand))
    raise ValueError("Unsupported math expression")


@Client.on_message(filters.command(["whois", "uinfo"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def who_is_handler(client: Client, message: Message):
    """Unified user profile lookup delegating to Helpdesk hd_info_cmd without code duplication."""
    logger.info("🔎 /whois ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s ᴄʜᴀᴛ=%s", message.from_user.id if message.from_user else 0, message.chat.id)
    await hd_info_cmd(client, message)


@Client.on_message(filters.command(["stickerid", "fileid"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def sticker_file_id_cmd(client: Client, message: Message):
    """Inspect sticker_id, unique_id, and media file_id of any replied message."""
    if not message.reply_to_message:
        return await message.reply_text(
            "⚠️ <b>ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴛɪᴄᴋᴇʀ ᴏʀ ᴍᴇᴅɪᴀ ғɪʟᴇ ᴡɪᴛʜ <code>/stickerid</code>!</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    finfo = get_file_id(message.reply_to_message)
    if not finfo:
        return await message.reply_text(
            "❌ <b>ɴᴏ sᴛɪᴄᴋᴇʀ ᴏʀ ᴍᴇᴅɪᴀ ғᴏᴜɴᴅ ɪɴ ᴛʜᴇ ʀᴇᴘʟɪᴇᴅ ᴍᴇssᴀɢᴇ.</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    logger.info("🎭 /stickerid › ᴛʏᴘᴇ=%s ᴄʜᴀᴛ=%s", finfo.message_type, message.chat.id)
    out = (
        f"🎭 <b>ᴍᴇᴅɪᴀ &amp; sᴛɪᴄᴋᴇʀ ɪɴsᴘᴇᴄᴛᴏʀ</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ᴍᴇᴅɪᴀ ᴛʏᴘᴇ:</b> <code>{finfo.message_type}</code>\n"
        f"• <b>ғɪʟᴇ ɪᴅ:</b> <code>{finfo.file_id}</code>\n"
        f"• <b>ᴜɴɪǫᴜᴇ ɪᴅ:</b> <code>{getattr(finfo, 'file_unique_id', 'ɴ/ᴀ')}</code>"
    )
    if getattr(finfo, "set_name", None):
        out += f"\n• <b>sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ:</b> <code>{finfo.set_name}</code>"
    if getattr(finfo, "emoji", None):
        out += f"\n• <b>ᴇᴍᴏᴊɪ:</b> {finfo.emoji}"

    await message.reply_text(out, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["json", "raw"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def message_json_cmd(client: Client, message: Message):
    """Dump raw Pyrogram Message object as formatted JSON."""
    target_msg = message.reply_to_message or message
    raw_str = str(target_msg)
    logger.info("🧾 /json ɪɴᴠᴏᴋᴇᴅ › ᴍsɢ_ɪᴅ=%s ʟᴇɴ=%s", target_msg.id, len(raw_str))

    if len(raw_str) <= 3800:
        await message.reply_text(
            f"🧾 <b>ʀᴀᴡ ᴍᴇssᴀɢᴇ ᴊsᴏɴ</b> ⚡\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{html.escape(raw_str)}</pre>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        bio = io.BytesIO(raw_str.encode("utf-8"))
        bio.name = f"message_{target_msg.id}.json"
        await message.reply_document(
            document=bio,
            caption=f"🧾 <b>ʀᴀᴡ ᴍᴇssᴀɢᴇ ᴊsᴏɴ (ɪᴅ: <code>{target_msg.id}</code>)</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("paste", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def paste_text_cmd(client: Client, message: Message):
    """Upload replied text or argument text to Pasty cloud paste service."""
    content = ""
    if len(message.command) > 1:
        content = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message:
        content = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not content:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/paste &lt;text&gt;</code> ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴛᴇxᴛ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/paste</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    wait = await message.reply_text("⏳ <b>ᴜᴘʟᴏᴀᴅɪɴɢ ᴛᴏ ᴄʟᴏᴜᴅ ᴘᴀsᴛᴇ...</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    ok, paste_url, raw_url = await paste_to_pasty(content, "txt")
    logger.info("📋 /paste › ʟᴇɴ=%s sᴜᴄᴄᴇss=%s", len(content), ok)

    if ok and paste_url:
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🌐 ᴏᴘᴇɴ ᴘᴀsᴛᴇ", url=paste_url),
                InlineKeyboardButton("📄 ʀᴀᴡ ᴛᴇxᴛ", url=raw_url or paste_url),
            ]
        ])
        await wait.edit_text(
            f"✅ <b>ᴘᴀsᴛᴇ ᴄʀᴇᴀᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!</b> 📋\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>ʟɪɴᴋ:</b> <code>{paste_url}</code>\n"
            f"• <b>ʀᴀᴡ:</b> <code>{raw_url}</code>",
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = "paste.txt"
        await wait.delete()
        await message.reply_document(bio, caption="📋 <b>ᴘᴀsᴛᴇ ғɪʟᴇ</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["tr", "translate"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def translate_text_cmd(client: Client, message: Message):
    """Translate text into any target language code (default: en) via Google Translate GTX endpoint."""
    target_lang = "en"
    text_to_tr = ""

    if message.reply_to_message:
        base_txt = message.reply_to_message.text or message.reply_to_message.caption or ""
        if len(message.command) > 1:
            target_lang = message.command[1].strip().lower()
        text_to_tr = base_txt
    elif len(message.command) >= 3:
        target_lang = message.command[1].strip().lower()
        text_to_tr = message.text.split(None, 2)[2].strip()
    elif len(message.command) == 2:
        text_to_tr = message.command[1].strip()

    if not text_to_tr:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b>\n"
            "• ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ: <code>/tr ml</code> ᴏʀ <code>/tr en</code>\n"
            "• ᴅɪʀᴇᴄᴛ: <code>/tr hi Hello world</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    wait = await message.reply_text("🌐 <b>ᴛʀᴀɴsʟᴀᴛɪɴɢ...</b>", quote=True, parse_mode=enums.ParseMode.HTML)
    url = (
        f"https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=auto&tl={quote_plus(target_lang)}&dt=t&q={quote_plus(text_to_tr[:1500])}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json(content_type=None)
                translated = "".join(part[0] for part in data[0] if part and part[0])
                src_lang = data[2] if len(data) > 2 else "auto"
        logger.info("🌐 /tr › %s -> %s (%s chars)", src_lang, target_lang, len(translated))
        await wait.edit_text(
            f"🌐 <b>ᴛʀᴀɴsʟᴀᴛɪᴏɴ ({html.escape(str(src_lang))} ➔ {html.escape(target_lang)})</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>{html.escape(translated)}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error("❌ /tr ғᴀɪʟᴇᴅ › %s", e)
        await wait.edit_text(f"❌ <b>ᴛʀᴀɴsʟᴀᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["calc", "math"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def safe_calculator_cmd(client: Client, message: Message):
    """Evaluate safe arithmetic expressions."""
    if len(message.command) < 2:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/calc (25 * 4) + 150 / 3</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    expr = message.text.split(None, 1)[1].strip()
    try:
        tree = ast.parse(expr, mode="eval")
        res = _safe_eval_math(tree)
        logger.info("🧮 /calc › %s = %s", expr, res)
        await message.reply_text(
            f"🧮 <b>ᴄᴀʟᴄᴜʟᴀᴛᴏʀ</b> ⚡\n"
            f"• <b>ᴇxᴘʀᴇssɪᴏɴ:</b> <code>{html.escape(expr)}</code>\n"
            f"• <b>ʀᴇsᴜʟᴛ:</b> <code>{res}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(
            f"❌ <b>ɪɴᴠᴀʟɪᴅ ᴇxᴘʀᴇssɪᴏɴ:</b> <code>{html.escape(str(e))}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("qr", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def qr_generator_cmd(client: Client, message: Message):
    """Generate a high-resolution QR code image for any text or URL."""
    payload = ""
    if len(message.command) > 1:
        payload = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message:
        payload = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not payload:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/qr &lt;text or url&gt;</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=512x512&margin=12&data={quote_plus(payload[:900])}"
    logger.info("🔳 /qr ɢᴇɴᴇʀᴀᴛᴇᴅ › ᴜsᴇʀ=%s ʟᴇɴ=%s", message.from_user.id if message.from_user else 0, len(payload))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(qr_url, timeout=15) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                img_bytes = await resp.read()
        bio = io.BytesIO(img_bytes)
        bio.name = "qrcode.png"
        await message.reply_photo(
            photo=bio,
            caption=f"🔳 <b>ǫʀ ᴄᴏᴅᴇ ɢᴇɴᴇʀᴀᴛᴇᴅ</b> ⚡\n• <b>ᴅᴀᴛᴀ:</b> <code>{html.escape(payload[:200])}</code>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ <b>ǫʀ ᴇʀʀᴏʀ:</b> <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)
