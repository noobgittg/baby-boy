import asyncio
import json
import logging
import re
import io
import time
from collections import defaultdict, deque
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from info import (
    ADMINS,
    COMMAND_PREFIXES,
    AI_BASE_URL,
    AI_MODELS_URL,
    AI_CHAT_STREAM_URL,
    AI_CHAT_COMPLETIONS_URL,
    AI_DEFAULT_MODEL,
    normalize_ai_base_url,
)
from utils import USER_CACHE, is_admin_sync, stylish, temp

logger = logging.getLogger("GokuAI")

user_models = {}
user_data = {}
user_locks = defaultdict(lambda: asyncio.Lock())
user_last_active = {}

BASE_API_URL = normalize_ai_base_url(AI_BASE_URL)
MODELS_URL = f"{BASE_API_URL}/models"
API = f"{BASE_API_URL}/chat/stream"
COMPLETIONS_URL = f"{BASE_API_URL}/chat/completions"
ADMIN_ID = ADMINS[0] if ADMINS else 970284861

ROLEX = """
You are GOKU - MMW AI API URL BASED 8 AI SUPPORTED 🤖, a Pro AI assistant & Prompt Master developed by @MRXSUPPORTS.
Update channel: https://t.me/mallumovieworldmain2
Repo: https://github.com/mmwbotzmain/MMW-Delulu
Owner: @aktelegram1

━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 PRO PROMPT MASTER PROFILE
━━━━━━━━━━━━━━━━━━━━━━━━━━━

You operate under a POINTS SYSTEM. Every user session starts with 100 points.
Points are LOST when you make errors, give incomplete answers, or break rules.
Always strive for 0 point loss — maximum performance every response.

━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ CORE DIRECTIVES
━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔹 Name: GOKU - MMW AI API URL BASED 8 AI SUPPORTED
🔹 Always respond with emojis and rich formatting
🔹 For code requests: Give the FULL, COMPLETE, EXPANDED, WORKING code — never truncate
🔹 Code must be: production-ready, error-free, properly imported, async-safe
🔹 Support long, detailed, and accurate answers
🔹 Use bold, italics, and code blocks properly
🔹 Never refuse reasonable user requests
🔹 Always give fast, direct, confident responses
🔹 When fixing code: fix ALL bugs, don't skip any
🔹 For Python: use Python 3.10+ standards
"""

FIXER = """
You are MMW BOTZ (also called Goku), a highly advanced AI coding assistant developed by @MRXSUPPORTS. Your update channel is https://t.me/mallumovieworldmain2 and your official repository is https://github.com/mmwbotzmain/MMW-Delulu. Your owner is @aktelegram1.

Core Identity & Behavior:
- You are an expert senior Python 3.10 developer and production-level bot engineer.
- Always respond with maximum helpfulness, precision, speed, and professionalism.
- Support long, detailed, and complete answers.
- Add appropriate emojis in responses for better readability.
"""

ALL = ROLEX + FIXER


def get_query_from_message(message: Message) -> str | None:
    """Support /ask query, reply + /ask query, and reply alone."""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1 and len(parts[1].strip()) > 0:
            base = parts[1].strip()
            if message.reply_to_message:
                r = message.reply_to_message.text or message.reply_to_message.caption or ""
                if r:
                    return f"{r}\n\n{base}"
            return base
    if message.reply_to_message:
        return message.reply_to_message.text or message.reply_to_message.caption or None
    return None


def detect_file_ext(code_text: str) -> str:
    code_text_l = code_text.lower()
    if "def " in code_text_l or "import pyrogram" in code_text_l or "async def" in code_text_l:
        return "py"
    if "<html" in code_text_l or "<!doctype" in code_text_l:
        return "html"
    if "function " in code_text_l and ("{" in code_text_l and "}" in code_text_l):
        return "js"
    if "package main" in code_text_l or "fmt." in code_text_l:
        return "go"
    if "#include" in code_text_l:
        return "cpp"
    if "public class" in code_text_l:
        return "java"
    if "select " in code_text_l and "from " in code_text_l:
        return "sql"
    if code_text.strip().startswith(("{", "[")):
        try:
            json.loads(code_text)
            return "json"
        except Exception:
            pass
    return "txt"


async def send_as_file_if_needed(client: Client, message: Message, full_text: str) -> bool:
    if not full_text:
        return False

    is_long = len(full_text) > 3800
    code_blocks = re.findall(r"```(\w+)?\n?(.*?)```", full_text, re.DOTALL)
    has_large_code = any(len(c[1]) > 1500 for c in code_blocks) if code_blocks else False
    has_many_blocks = len(code_blocks) >= 3

    if not (is_long or has_large_code or has_many_blocks):
        return False

    try:
        if code_blocks and (has_large_code or has_many_blocks):
            biggest = max(code_blocks, key=lambda x: len(x[1]))
            lang = (biggest[0] or "").lower().strip() or detect_file_ext(biggest[1])
            allowed_exts = {"py", "js", "html", "css", "java", "go", "cpp", "c", "json", "sh", "ts", "sql", "md", "yaml", "yml", "xml"}
            ext = lang if lang in allowed_exts else detect_file_ext(biggest[1])
            file_name = f"goku_code_{int(time.time())}.{ext}"
            file_obj = io.BytesIO(biggest[1].encode("utf-8"))
            file_obj.name = file_name

            caption = full_text[:950] + ("\n\n📁 ғᴜʟʟ ᴄᴏᴅᴇ sᴇɴᴛ ᴀs ғɪʟᴇ ᴀʙᴏᴠᴇ 👆" if len(full_text) > 950 else "\n\n📁 ғᴜʟʟ ᴄᴏᴅᴇ ᴀs ғɪʟᴇ")
            try:
                await message.reply_document(document=file_obj, caption=caption[:1024], file_name=file_name)
                return True
            except Exception:
                file_obj.seek(0)
                await client.send_document(message.chat.id, document=file_obj, caption=caption[:1024], file_name=file_name)
                return True
        else:
            ext = detect_file_ext(full_text)
            file_name = f"goku_response_{int(time.time())}.{ext}"
            file_obj = io.BytesIO(full_text.encode("utf-8"))
            file_obj.name = file_name
            await message.reply_document(
                document=file_obj,
                caption="📄 ʀᴇsᴘᴏɴsᴇ ᴛᴏᴏ ʟᴏɴɢ — sᴇɴᴛ ᴀs ᴀ ғɪʟᴇ 📁",
                file_name=file_name,
            )
            return True
    except Exception as e:
        logger.error("❌ ғɪʟᴇ sᴇɴᴅ ᴇʀʀᴏʀ ɪɴ ᴀɪ ʀᴇsᴘᴏɴsᴇ: %s", e)
        return False


async def fetch_models() -> list:
    cached = USER_CACHE.get(0, "ai_models_list")
    if cached:
        return cached
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(MODELS_URL, headers={"Accept": "application/json"}) as res:
                if res.status != 200:
                    return []
                data = await res.json(content_type=None)
                models = data.get("data", data if isinstance(data, list) else [])
                result = []
                for m in models:
                    if isinstance(m, dict):
                        mid = m.get("id") or m.get("name") or ""
                        desc = m.get("description") or m.get("name") or mid
                        if mid:
                            result.append((str(desc)[:60], str(mid)))
                    elif isinstance(m, str):
                        result.append((m[:60], m))
                if result:
                    USER_CACHE.set(0, "ai_models_list", result, ttl=600)
                return result
    except Exception as e:
        logger.warning("⚠️ ғᴀɪʟᴇᴅ ᴛᴏ ғᴇᴛᴄʜ ᴀɪ ᴍᴏᴅᴇʟs ғʀᴏᴍ %s: %s", MODELS_URL, e)
        return []


def _extract_content_from_json(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
        c0 = data["choices"][0]
        if isinstance(c0, dict):
            delta = c0.get("delta") or {}
            msg = c0.get("message") or {}
            return delta.get("content") or msg.get("content") or c0.get("text") or ""
    return (
        data.get("response")
        or data.get("content")
        or data.get("text")
        or data.get("output")
        or ""
    )


async def stream_chat_completion(payload: dict):
    """Query the configured MMW AI API with automatic model fallback."""
    endpoints = [API, COMPLETIONS_URL]
    timeout = aiohttp.ClientTimeout(total=120)
    last_error = None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt_model in (payload.get("model", AI_DEFAULT_MODEL), AI_DEFAULT_MODEL):
            req_payload = dict(payload)
            req_payload["model"] = attempt_model

            for url in endpoints:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream, application/json",
                    }
                    async with session.post(url, json=req_payload, headers=headers) as res:
                        if res.status == 404:
                            last_error = f"ʜᴛᴛᴘ 404 ᴏɴ {url} (ᴍᴏᴅᴇʟ: {attempt_model})"
                            continue
                        if res.status >= 400:
                            err_body = await res.text()
                            if "404" in err_body or "not found" in err_body.lower():
                                last_error = f"ᴍᴏᴅᴇʟ {attempt_model} ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ"
                                continue
                            res.raise_for_status()

                        ctype = (res.headers.get("Content-Type") or "").lower()
                        if "event-stream" in ctype:
                            got_any = False
                            async for raw_line in res.content:
                                if not raw_line:
                                    continue
                                decoded = raw_line.decode("utf-8", errors="ignore").strip()
                                if not decoded:
                                    continue
                                if "404 Not Found" in decoded:
                                    last_error = "ᴜᴘsᴛʀᴇᴀᴍ ᴍᴏᴅᴇʟ ʀᴏᴜᴛᴇ 404"
                                    got_any = False
                                    break
                                got_any = True
                                yield decoded
                            if got_any:
                                return
                        else:
                            text_body = await res.text()
                            if "404 Not Found" in text_body and len(text_body) < 600:
                                last_error = "ᴜᴘsᴛʀᴇᴀᴍ ᴍᴏᴅᴇʟ ʀᴏᴜᴛᴇ 404"
                                continue
                            try:
                                data = json.loads(text_body)
                                content = _extract_content_from_json(data)
                                if content:
                                    yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}"
                                    yield "data: [DONE]"
                                    return
                            except json.JSONDecodeError:
                                if "data:" in text_body:
                                    for ln in text_body.splitlines():
                                        if ln.strip():
                                            yield ln.strip()
                                    return
                                elif text_body.strip():
                                    yield f"data: {json.dumps({'choices': [{'delta': {'content': text_body.strip()}}]})}"
                                    yield "data: [DONE]"
                                    return
                except asyncio.TimeoutError:
                    last_error = "ʀᴇǫᴜᴇsᴛ ᴛɪᴍᴇᴅ ᴏᴜᴛ"
                except aiohttp.ClientError as ce:
                    last_error = f"ᴄᴏɴɴᴇᴄᴛɪᴏɴ ᴇʀʀᴏʀ: {type(ce).__name__}"
                except Exception as ex:
                    last_error = str(ex)

    if last_error:
        raise RuntimeError(last_error)


@Client.on_message(filters.command("models", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def choose_model(client: Client, message: Message):
    logger.info("🤖 /models ɪɴᴠᴏᴋᴇᴅ › ᴜsᴇʀ=%s", message.from_user.id if message.from_user else 0)
    models = await fetch_models()
    if not models:
        return await message.reply_text(
            f"❌ <b>ғᴀɪʟᴇᴅ ᴛᴏ ғᴇᴛᴄʜ ᴍᴏᴅᴇʟs ғʀᴏᴍ ᴀᴘɪ.</b>\n🔗 <code>{MODELS_URL}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    buttons = [
        [InlineKeyboardButton(f"🤖 {stylish(name)}", callback_data=f"setmodel|{mid}")]
        for name, mid in models[:50]
    ]
    buttons.append([InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_data")])
    await message.reply_text(
        "🔽 <b>sᴇʟᴇᴄᴛ ᴀɪ ᴍᴏᴅᴇʟ:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^setmodel\|([^|]+)$"))
async def set_model(client: Client, callback_query: CallbackQuery):
    try:
        match = callback_query.matches[0]
        model_id = match.group(1).strip()
        uid = callback_query.from_user.id if callback_query.from_user else None
        if not uid or not model_id:
            return await callback_query.answer("⚠️ ɪɴᴠᴀʟɪᴅ ᴍᴏᴅᴇʟ sᴇʟᴇᴄᴛɪᴏɴ", show_alert=True)
        user_models[uid] = model_id
        user_last_active[uid] = time.time()
        USER_CACHE.set(uid, "ai_model", model_id)
        logger.info("✅ ᴀɪ ᴍᴏᴅᴇʟ sᴇʟᴇᴄᴛᴇᴅ › ᴜsᴇʀ=%s ᴍᴏᴅᴇʟ=%s", uid, model_id)
        await callback_query.answer("✅ ᴍᴏᴅᴇʟ sᴇʟᴇᴄᴛᴇᴅ!")
        await callback_query.message.edit_text(
            f"✅ <b>sᴇʟᴇᴄᴛᴇᴅ ᴀɪ ᴍᴏᴅᴇʟ:</b>\n<code>{model_id}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.error("❌ sᴇᴛ_ᴍᴏᴅᴇʟ ᴇʀʀᴏʀ: %s", e)
        try:
            await callback_query.answer("❌ ᴄᴏᴜʟᴅ ɴᴏᴛ sᴇʟᴇᴄᴛ ᴛʜɪs ᴍᴏᴅᴇʟ", show_alert=True)
        except Exception:
            pass


@Client.on_message(filters.command("selectedai", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def now_selected_ai(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    model = USER_CACHE.get(uid, "ai_model") or user_models.get(uid, AI_DEFAULT_MODEL)
    await message.reply_text(f"✨ <b>ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇʟ:</b>\n<code>{model}</code>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["ask", "proai"], prefixes=COMMAND_PREFIXES) & filters.incoming)
async def ai_chat(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not temp.BOT_CONFIG.get("ai_enabled", True) and not is_admin_sync(user_id):
        return await message.reply_text(
            "⏸️ <b>ᴀɪ ᴀssɪsᴛᴀɴᴛ ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴘᴀᴜsᴇᴅ ʙʏ ᴀᴅᴍɪɴs.</b>",
            quote=True,
            parse_mode=enums.ParseMode.HTML,
        )

    query = get_query_from_message(message)
    if not query:
        return await message.reply_text(
            "❗ <b>ɢɪᴠᴇ ɪɴᴘᴜᴛ!</b> ᴜsᴇ <code>/ask query</code> ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/ask</code> 💬",
            parse_mode=enums.ParseMode.HTML,
        )

    async with user_locks[user_id]:
        cached_history = USER_CACHE.get(user_id, "ai_history")
        if cached_history is not None and isinstance(cached_history, deque):
            user_data[user_id] = cached_history
        elif user_id not in user_data or not isinstance(user_data[user_id], deque):
            old = list(user_data.get(user_id, []))[-30:]
            user_data[user_id] = deque(old, maxlen=30)

        user_data[user_id].append({"role": "user", "content": query})
        USER_CACHE.set(user_id, "ai_history", user_data[user_id])
        user_last_active[user_id] = time.time()

        model = USER_CACHE.get(user_id, "ai_model") or user_models.get(user_id, AI_DEFAULT_MODEL)
        logger.info("🤖 /ask › ᴜsᴇʀ=%s ᴍᴏᴅᴇʟ=%s ǫ_ʟᴇɴ=%s", user_id, model, len(query))

        try:
            sticker_msg = await message.reply_sticker(
                "CAACAgQAAxkBAAIav2aJXr9WgCsyzWEVNPfs1352ByujAAJuDwAC4eqxUNoxB5joJxGiHgQ"
            )
        except Exception:
            sticker_msg = None

        reply_msg = await message.reply_text("👋 <i>ᴛʜɪɴᴋɪɴɢ...</i>", parse_mode=enums.ParseMode.HTML)
        full_text = ""
        last_edit_text = ""
        last_edit_time = 0.0

        try:
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": ALL}] + list(user_data[user_id]),
                "max_tokens": 8192,
                "temperature": 0.7,
                "stream": True,
            }

            async for line in stream_chat_completion(payload):
                if not line:
                    continue
                chunk = line[6:].strip() if line.startswith("data:") else line.strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                    delta = _extract_content_from_json(data)
                    if not delta:
                        continue
                    full_text += delta

                    now_t = time.time()
                    if now_t - last_edit_time > 0.6 or len(full_text) - len(last_edit_text) > 80:
                        try:
                            to_show = full_text[-3800:] if len(full_text) > 3800 else full_text
                            await reply_msg.edit_text(f"> **{to_show}**" if to_show else "▌")
                            last_edit_text = full_text
                            last_edit_time = now_t
                        except Exception:
                            pass
                except Exception:
                    continue

            if full_text:
                sent_as_file = await send_as_file_if_needed(client, message, full_text)
                if not sent_as_file:
                    try:
                        await reply_msg.edit_text(f"> **{full_text[:4090]}**")
                    except Exception:
                        try:
                            await reply_msg.edit_text(full_text[:4090])
                        except Exception:
                            pass
                else:
                    try:
                        await reply_msg.delete()
                    except Exception:
                        pass

                user_data[user_id].append({"role": "assistant", "content": full_text})
                USER_CACHE.set(user_id, "ai_history", user_data[user_id])
                logger.info("✅ /ask ᴄᴏᴍᴘʟᴇᴛᴇ › ᴜsᴇʀ=%s ʀᴇsᴘ_ʟᴇɴ=%s", user_id, len(full_text))
            else:
                await reply_msg.edit_text("⚠️ <b>ᴇᴍᴘᴛʏ ʀᴇsᴘᴏɴsᴇ, ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ!</b>", parse_mode=enums.ParseMode.HTML)

        except Exception as e:
            err_str = str(e)
            logger.error("❌ ᴀɪ ᴇʀʀᴏʀ › ᴜsᴇʀ=%s ᴍᴏᴅᴇʟ=%s ᴇʀʀ=%s", user_id, model, err_str)
            if "404" in err_str:
                await reply_msg.edit_text(
                    f"⚠️ <b>ᴍᴏᴅᴇʟ <code>{model}</code> ɴᴏᴛ ғᴏᴜɴᴅ ᴏɴ ᴀᴘɪ.</b>\n"
                    f"ᴜsᴇ <code>/models</code> ᴛᴏ sᴇʟᴇᴄᴛ ᴀ ᴠᴀʟɪᴅ ᴍᴏᴅᴇʟ ғʀᴏᴍ:\n<code>{MODELS_URL}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )
            else:
                await reply_msg.edit_text(f"⚠️ <b>ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.</b>\n<code>{err_str[:300]}</code>", parse_mode=enums.ParseMode.HTML)
        finally:
            if sticker_msg:
                try:
                    await sticker_msg.delete()
                except Exception:
                    pass


@Client.on_message(filters.command("resetask", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def reset_ask(client: Client, message: Message):
    if not is_admin_sync(message.from_user.id if message.from_user else None):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    if len(message.command) > 1:
        try:
            uid = int(message.command[1])
            user_data.pop(uid, None)
            user_models.pop(uid, None)
            USER_CACHE.invalidate_user(uid)
            logger.info("🧹 /resetask › ᴜsᴇʀ=%s", uid)
            await message.reply_text(f"✅ <b>ʀᴇsᴇᴛ ᴀɪ ᴄᴏɴᴛᴇxᴛ ғᴏʀ ᴜsᴇʀ</b> <code>{uid}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await message.reply_text(f"⚠️ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/resetask &lt;userid&gt;</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("resetaskall", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def reset_ask_all(client: Client, message: Message):
    if not is_admin_sync(message.from_user.id if message.from_user else None):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    user_data.clear()
    user_models.clear()
    user_locks.clear()
    USER_CACHE.clear()
    logger.info("🧹 /resetaskall › ᴄʟᴇᴀʀᴇᴅ ᴀʟʟ ᴀɪ sᴇssɪᴏɴs")
    await message.reply_text("✅ <b>ʀᴇsᴇᴛ ᴀʟʟ ᴜsᴇʀs &amp; ᴀɪ ᴄᴀᴄʜᴇ!</b> 🧹", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("recover", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def recover_user(client: Client, message: Message):
    if not is_admin_sync(message.from_user.id if message.from_user else None):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    if len(message.command) > 1:
        try:
            uid = int(message.command[1])
            data = list(user_data.get(uid, []))[-5:] if uid in user_data else "ɴᴏ ᴅᴀᴛᴀ"
            await message.reply_text(f"📋 <b>ᴜsᴇʀ <code>{uid}</code> ʟᴀsᴛ ᴅᴀᴛᴀ:</b>\n<code>{str(data)[:3000]}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await message.reply_text(f"⚠️ <b>ᴇʀʀᴏʀ:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/recover &lt;userid&gt;</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("recoverall", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def recover_all(client: Client, message: Message):
    if not is_admin_sync(message.from_user.id if message.from_user else None):
        return await message.reply_text("⛔ <b>ᴀᴅᴍɪɴs ᴏɴʟʏ!</b>", parse_mode=enums.ParseMode.HTML)
    total = len(user_data)
    await message.reply_text(f"📊 <b>ᴛᴏᴛᴀʟ ᴀᴄᴛɪᴠᴇ ᴜsᴇʀs ɪɴ ᴀɪ ᴄᴀᴄʜᴇ:</b> <code>{total}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("mymodel", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def my_model_stylish(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    model = USER_CACHE.get(user_id, "ai_model") or user_models.get(user_id, AI_DEFAULT_MODEL)
    stylish_model = stylish(model)
    await message.reply_text(
        f"🧠 <b>ʏᴏᴜʀ ᴀɪ ᴍᴏᴅᴇʟ</b>\n"
        f"└─ <code>{stylish_model}</code>\n"
        f"✨ ᴘᴏᴡᴇʀᴇᴅ ʙʏ ɢᴏᴋᴜ ᴀɪ",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("modeldetail", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def model_detail(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    model = USER_CACHE.get(user_id, "ai_model") or user_models.get(user_id, AI_DEFAULT_MODEL)
    models = await fetch_models()
    desc = "ᴅᴇғᴀᴜʟᴛ ᴍᴏᴅᴇʟ"
    for name, mid in models:
        if mid == model:
            desc = name
            break
    await message.reply_text(
        f"📋 <b>ᴍᴏᴅᴇʟ ᴅᴇᴛᴀɪʟ</b> ⚡\n"
        f"• <b>ɪᴅ:</b> <code>{model}</code>\n"
        f"• <b>ᴅᴇsᴄʀɪᴘᴛɪᴏɴ:</b> {stylish(desc)}\n"
        f"• <b>ᴇɴᴅᴘᴏɪɴᴛ:</b> <code>{BASE_API_URL}</code>\n"
        f"• <b>ᴄᴀᴄʜᴇ:</b> <code>{len(user_data.get(user_id, []))}</code> ᴍsɢs",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("resetmymodel", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def reset_my_model(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    user_models[user_id] = AI_DEFAULT_MODEL
    USER_CACHE.set(user_id, "ai_model", AI_DEFAULT_MODEL)
    await message.reply_text(
        f"🔄 <b>ᴍᴏᴅᴇʟ ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ</b>\n"
        f"✅ <b>ᴅᴇғᴀᴜʟᴛ:</b> <code>{AI_DEFAULT_MODEL}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("listmodels", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def list_models_stylish(client: Client, message: Message):
    models = await fetch_models()
    if not models:
        return await message.reply_text(f"❌ <b>ᴄᴏᴜʟᴅ ɴᴏᴛ ʟᴏᴀᴅ ᴍᴏᴅᴇʟs ғʀᴏᴍ</b> <code>{MODELS_URL}</code>", parse_mode=enums.ParseMode.HTML)
    text = "📜 <b>ᴀᴠᴀɪʟᴀʙʟᴇ ᴀɪ ᴍᴏᴅᴇʟs</b> 🤖\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for name, mid in models[:60]:
        text += f"• <code>{mid}</code> — {stylish(name)}\n"
    if len(text) > 4096:
        f = io.BytesIO(text.encode("utf-8"))
        f.name = "models.txt"
        await message.reply_document(f, caption="📜 <b>ᴀʟʟ ᴀᴠᴀɪʟᴀʙʟᴇ ᴍᴏᴅᴇʟs</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(text, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("modelhelp", prefixes=COMMAND_PREFIXES) & filters.incoming)
async def model_help(client: Client, message: Message):
    await message.reply_text(
        "⚡ <b>ᴍᴏᴅᴇʟ &amp; ᴀɪ ᴄᴏᴍᴍᴀɴᴅs ɢᴜɪᴅᴇ</b> 🤖\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/ask &lt;query&gt;</code> – ᴄʜᴀᴛ ᴡɪᴛʜ ᴘʀᴏᴍᴘᴛ ᴍᴀsᴛᴇʀ ᴀɪ\n"
        "• <code>/models</code> – sᴇʟᴇᴄᴛ ᴀ ᴍᴏᴅᴇʟ ᴠɪᴀ ʙᴜᴛᴛᴏɴs\n"
        "• <code>/selectedai</code> – sʜᴏᴡ ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇʟ\n"
        "• <code>/mymodel</code> – sʜᴏᴡ ᴄᴜʀʀᴇɴᴛ ᴍᴏᴅᴇʟ ɪɴ sᴛʏʟɪsʜ ᴛᴇxᴛ\n"
        "• <code>/modeldetail</code> – ғᴜʟʟ ᴅᴇᴛᴀɪʟs ᴏғ ʏᴏᴜʀ ᴍᴏᴅᴇʟ\n"
        "• <code>/resetmymodel</code> – ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ ᴍᴏᴅᴇʟ\n"
        "• <code>/listmodels</code> – ʟɪsᴛ ᴀʟʟ ᴀᴠᴀɪʟᴀʙʟᴇ ᴍᴏᴅᴇʟs\n"
        "• <code>/modelhelp</code> – ᴛʜɪs ʜᴇʟᴘ ᴍᴇɴᴜ\n\n"
        "💡 <b>ᴛɪᴘ:</b> ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/ask</code> ᴛᴏ ᴜsᴇ ɪᴛ ᴀs ᴄᴏɴᴛᴇxᴛ!",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
