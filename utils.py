import asyncio
import html
import inspect
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple, Union

from pyrogram import enums
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    MessageNotModified,
    PeerIdInvalid,
    UserIsBlocked,
)
from pyrogram.types import InlineKeyboardMarkup, Message

from config import Config
from database.users_chats_db import db
from info import *
from plugins.core_utils import (
    cache,
    LOG_FILE_PATH,
    MEMORY_LOGS,
    FLOODWAIT_STATS,
    execute_with_floodwait,
    pro_floodwait_sleep,
    _flood_wait_seconds,
    _compute_edit_signature,
    _extract_msg_identity,
    rate_limit_manager,
    RedactSecretsFilter,
    redact_secrets,
    setup_logger,
    to_smallcaps,
    task_manager,
)


def setup_logging() -> logging.Logger:
    return setup_logger("MMWCore")


logger = setup_logging()

to_small_caps = to_smallcaps
to_smallcap = to_smallcaps
stylish = to_smallcaps
sc = to_smallcaps


def format_watermark_html() -> str:
    return f"\n\n📢 <b>{to_smallcaps('join')} : <a href='{Config.WATERMARK_URL}'>{html.escape(Config.WATERMARK)}</a></b>"


def make_progress_bar(percent: float, length: int = 10) -> str:
    """Build a visual progress bar string."""
    filled = max(0, min(length, int(length * float(percent) / 100.0)))
    return "▰" * filled + "▱" * (length - filled)


def build_safe_type(cls, **kwargs):
    """Safely instantiate Pyrogram ChatPrivileges or ChatPermissions across library versions."""
    try:
        sig = inspect.signature(cls.__init__)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_var_kw:
            return cls(**kwargs)
        valid_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return cls(**valid_kwargs)
    except Exception:
        try:
            fallback_keys = (
                "can_send_messages",
                "can_send_polls",
                "can_add_web_page_previews",
                "can_change_info",
                "can_invite_users",
                "can_pin_messages",
                "can_manage_chat",
                "can_delete_messages",
                "can_restrict_members",
                "can_promote_members",
                "can_manage_video_chats",
                "can_post_messages",
                "can_edit_messages",
            )
            return cls(**{k: v for k, v in kwargs.items() if k in fallback_keys})
        except Exception:
            return cls()


def is_admin_sync(user_id: Optional[Union[int, str]]) -> bool:
    if user_id is None:
        return False
    try:
        uid = int(user_id)
        return uid in ADMINS or str(uid) in ADMINS
    except (ValueError, TypeError):
        return str(user_id) in ADMINS


async def is_admin(user_id: Optional[Union[int, str]], client=None) -> bool:
    return is_admin_sync(user_id)


async def is_chat_or_global_admin(
    client,
    chat_id: int,
    user_id: Optional[int] = None,
    sender_chat_id: Optional[int] = None,
) -> bool:
    """
    Verify if caller is a Bot ADMIN, Group/Channel Owner, Group/Channel Administrator,
    or anonymous post signed by the target Group/Channel itself.
    """
    if sender_chat_id and int(sender_chat_id) == int(chat_id):
        return True
    if user_id and (is_admin_sync(user_id) or int(user_id) in (777000, 1087968824)):
        return True
    if not user_id:
        return False
    try:
        mem = await client.get_chat_member(int(chat_id), int(user_id))
        return mem.status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
    except Exception as e:
        logger.debug("🔍 ᴀᴅᴍɪɴ ᴄʜᴇᴄᴋ ɴᴏᴛɪᴄᴇ › ᴄʜᴀᴛ=%s ᴜsᴇʀ=%s ᴇʀʀ=%s", chat_id, user_id, e)
        return False


async def safe_edit_message(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[enums.ParseMode] = enums.ParseMode.HTML,
    disable_web_page_preview: bool = True,
) -> bool:
    """
    Safely edit a Telegram message (or replace media message with text menu when navigating Back/Home)
    using the centralized RateLimitManager, content deduplication, edit coalescing, and FloodWait backoff.
    """
    if not message:
        return False

    chat_id, msg_id = _extract_msg_identity(message)
    sig = _compute_edit_signature(text, reply_markup)
    if rate_limit_manager.check_and_record_signature(chat_id, msg_id, sig):
        return True

    msg_key = (chat_id, msg_id)
    msg_lock = rate_limit_manager._msg_locks[msg_key]
    async with msg_lock:
        if rate_limit_manager.check_and_record_signature(chat_id, msg_id, sig):
            return True

        await rate_limit_manager.wait_for_write_slot(
            rpc_name="EditMessage",
            chat_id=chat_id,
            is_edit=True,
            is_optional_progress=False,
        )

        for attempt in range(5):
            try:
                has_media = getattr(message, "media", None) is not None and getattr(message, "text", None) is None
                if has_media:
                    client = getattr(message, "_client", None)
                    if client and getattr(message, "chat", None):
                        sent = await client.send_message(
                            chat_id=message.chat.id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode,
                            disable_web_page_preview=disable_web_page_preview,
                        )
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        rate_limit_manager.record_rpc_success()
                        if sent:
                            s_chat, s_id = _extract_msg_identity(sent)
                            rate_limit_manager.commit_signature(s_chat, s_id, sig)
                        return bool(sent)
                    await message.edit_caption(
                        caption=text[:1024],
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                    )
                    rate_limit_manager.record_rpc_success()
                    rate_limit_manager.commit_signature(chat_id, msg_id, sig)
                    return True

                await message.edit_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                rate_limit_manager.record_rpc_success()
                rate_limit_manager.commit_signature(chat_id, msg_id, sig)
                cache.auto_clear_after_response_sync()
                return True
            except MessageNotModified:
                rate_limit_manager.record_rpc_success()
                rate_limit_manager.commit_signature(chat_id, msg_id, sig)
                cache.auto_clear_after_response_sync()
                return True
            except FloodWait as fw:
                wait_s = _flood_wait_seconds(fw)
                await pro_floodwait_sleep(min(wait_s, 90), f"safe_edit_message#{attempt+1}/5", chat_id=chat_id)
            except Exception as e:
                logger.debug("⚠️ sᴀғᴇ_ᴇᴅɪᴛ ғᴀʟʟʙᴀᴄᴋ (ᴀᴛᴛᴇᴍᴘᴛ %s) › %s", attempt + 1, e)
                if attempt == 0:
                    parse_mode = enums.ParseMode.DEFAULT
                    continue
                if attempt < 2:
                    await asyncio.sleep(1.0)
                    continue
                break
        return False


_LOG_LINES_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "size": -1,
    "mem_len": -1,
    "lines": [],
}


def _read_all_log_lines() -> List[str]:
    """Read log lines with a fast 0.8s mtime/size cache so rapid filter/header calls don't re-read disk twice."""
    now = time.monotonic()
    fsize = os.path.getsize(LOG_FILE_PATH) if os.path.isfile(LOG_FILE_PATH) else 0
    mlen = len(MEMORY_LOGS)
    if (
        (now - _LOG_LINES_CACHE["ts"]) < 0.8
        and _LOG_LINES_CACHE["size"] == fsize
        and _LOG_LINES_CACHE["mem_len"] == mlen
        and _LOG_LINES_CACHE["lines"]
    ):
        return list(_LOG_LINES_CACHE["lines"])

    lines: List[str] = []
    if fsize > 0 and os.path.isfile(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                if fsize > 1024 * 1024:
                    f.seek(max(0, fsize - 512 * 1024))
                    f.readline()
                lines = [ln.rstrip("\r\n") for ln in f.readlines() if ln.strip()]
        except Exception:
            lines = []
    if not lines and MEMORY_LOGS:
        lines = [ln.strip() for ln in MEMORY_LOGS if ln.strip()]

    _LOG_LINES_CACHE["ts"] = now
    _LOG_LINES_CACHE["size"] = fsize
    _LOG_LINES_CACHE["mem_len"] = mlen
    _LOG_LINES_CACHE["lines"] = lines
    return list(lines)


def get_expanded_logs(
    lines_count: int = 45,
    mode: str = "all",
    search_query: str = "",
    max_chars: int = 3650,
) -> str:
    """Read, filter, and redact expanded application logs with mode/search filtering."""
    lines = _read_all_log_lines()
    if not lines:
        return "ɴᴏ ʟᴏɢ ᴇɴᴛʀɪᴇs ʏᴇᴛ."

    m = (mode or "all").lower().strip()
    if m in ("err", "error", "errors", "warn", "warning"):
        lines = [
            ln for ln in lines
            if any(k in ln.upper() for k in ("ERROR", "WARNING", "CRITICAL", "TRACEBACK", "EXCEPTION", "❌", "⚠️"))
        ]
    elif m in ("flood", "floodwait"):
        lines = [
            ln for ln in lines
            if any(k in ln.lower() for k in ("floodwait", "flood_wait", "ғʟᴏᴏᴅᴡᴀɪᴛ", "🌊", "420", "cooldown"))
        ]
    elif m in ("media", "ffmpeg", "stream", "rename", "compress"):
        lines = [
            ln for ln in lines
            if any(k in ln.lower() for k in ("ffmpeg", "compress", "rename", "stream", "download", "upload", "🗜️", "📥", "📤", "🎬"))
        ]
    elif m in ("tasks", "recovery", "recover"):
        lines = [
            ln for ln in lines
            if any(k in ln.lower() for k in ("recovery", "task", "ʀᴇᴄᴏᴠᴇʀ", "ᴄʜᴇᴄᴋᴘᴏɪɴᴛ", "♻️", "🗜️", "ᴡᴀᴛᴄʜᴅᴏɢ"))
        ]

    if search_query:
        q_low = search_query.lower().strip()
        lines = [ln for ln in lines if q_low in ln.lower()]

    if not lines:
        return f"ɴᴏ ʟᴏɢ ᴇɴᴛʀɪᴇs ᴍᴀᴛᴄʜɪɴɢ ғɪʟᴛᴇʀ '{mode}'."

    tail = lines[-max(1, int(lines_count)) :]
    redacted = redact_secrets("\n".join(tail).strip())
    if len(redacted) > max_chars:
        redacted = "...\n" + redacted[-max_chars:]
    return redacted or "ʟᴏɢ ғɪʟᴇ ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴇᴍᴘᴛʏ."


def get_log_analytics() -> Dict[str, Any]:
    """Compute comprehensive diagnostic statistics across log files, memory buffer, FloodWait solver, and Task Recovery."""
    lines = _read_all_log_lines()
    total_lines = len(lines)
    info_cnt = sum(1 for ln in lines if "INFO" in ln)
    warn_cnt = sum(1 for ln in lines if "WARNING" in ln)
    err_cnt = sum(1 for ln in lines if "ERROR" in ln or "Traceback" in ln)
    flood_cnt = sum(1 for ln in lines if "flood" in ln.lower() or "ғʟᴏᴏᴅᴡᴀɪᴛ" in ln.lower() or "🌊" in ln)
    media_cnt = sum(1 for ln in lines if any(k in ln.lower() for k in ("ffmpeg", "rename", "compress", "stream")))
    file_kb = round(os.path.getsize(LOG_FILE_PATH) / 1024.0, 2) if os.path.isfile(LOG_FILE_PATH) else 0.0
    return {
        "total_lines": total_lines,
        "info_count": info_cnt,
        "warning_count": warn_cnt,
        "error_count": err_cnt,
        "flood_log_lines": flood_cnt,
        "media_log_lines": media_cnt,
        "file_size_kb": file_kb,
        "memory_buffer_count": len(MEMORY_LOGS),
        "floodwait_solver": rate_limit_manager.get_stats(),
        "active_media_tasks": task_manager.active_count,
        "task_recovery": task_manager.get_recovery_report(),
    }


def get_recent_logs_preview(lines_count: int = 45, max_chars: int = 3650) -> str:
    """Read and redact the tail of the application log file."""
    return get_expanded_logs(lines_count=lines_count, mode="all", max_chars=max_chars)


_RATE_LIMIT_BUCKETS: Dict[Tuple[int, str], List[float]] = {}


def check_rate_limit(
    user_id: int,
    scope: str = "default",
    limit: int = 0,
    window: int = 60,
) -> Tuple[bool, int]:
    eff_limit = int(limit or RATE_LIMIT or 0)
    if eff_limit <= 0 or not user_id or is_admin_sync(user_id):
        return True, 0
    eff_win = max(1, int(window or RATE_WINDOW or 60))
    now = time.monotonic()
    key = (int(user_id), str(scope))
    bucket = [t for t in _RATE_LIMIT_BUCKETS.get(key, []) if (now - t) < eff_win]
    if len(bucket) >= eff_limit:
        retry_after = max(1, int(eff_win - (now - bucket[0])))
        _RATE_LIMIT_BUCKETS[key] = bucket
        return False, retry_after
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket
    return True, 0


async def safe_answer_callback(query, text: str = "", show_alert: bool = False) -> bool:
    """Safely answer a CallbackQuery without raising QueryIdInvalid or FloodWait."""
    if not query:
        return False
    try:
        await query.answer(text=text[:200] if text else None, show_alert=show_alert)
        rate_limit_manager.record_rpc_success()
        return True
    except Exception:
        return False


class UserDynamicCache:
    def __init__(self, ttl: int = CACHE_TTL, max_entries: int = CACHE_MAX_ENTRIES):
        self.ttl = max(10, int(ttl))
        self.max_entries = max(100, int(max_entries))
        self._store: "OrderedDict[Tuple[int, str], Tuple[float, int, Any]]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.invalidations = 0

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired_keys = [
            k for k, (ts, custom_ttl, _) in self._store.items()
            if (now - ts) >= custom_ttl
        ]
        for k in expired_keys:
            self._store.pop(k, None)
            self.evictions += 1

    def get(self, user_id: int, subkey: str = "default") -> Any:
        now = time.monotonic()
        ck = (int(user_id), str(subkey))
        entry = self._store.get(ck)
        if entry is None:
            self.misses += 1
            return None
        ts, custom_ttl, val = entry
        if (now - ts) >= custom_ttl:
            self._store.pop(ck, None)
            self.evictions += 1
            self.misses += 1
            return None
        self._store.move_to_end(ck)
        self.hits += 1
        return val

    def set(self, user_id: int, subkey: str, value: Any, ttl: Optional[int] = None) -> None:
        if len(self._store) >= self.max_entries:
            self._purge_expired()
        ck = (int(user_id), str(subkey))
        custom_ttl = int(ttl) if ttl is not None else self.ttl
        if ck in self._store:
            self._store.move_to_end(ck)
        self._store[ck] = (time.monotonic(), custom_ttl, value)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)
            self.evictions += 1

    def invalidate_user(self, user_id: int, subkey: Optional[str] = None) -> int:
        uid = int(user_id)
        if subkey is not None:
            removed = 1 if self._store.pop((uid, str(subkey)), None) is not None else 0
            self.invalidations += removed
            return removed
        keys_to_remove = [k for k in self._store.keys() if k[0] == uid]
        for k in keys_to_remove:
            self._store.pop(k, None)
        self.invalidations += len(keys_to_remove)
        return len(keys_to_remove)

    def clear(self) -> int:
        count = len(self._store)
        self._store.clear()
        self.invalidations += count
        return count

    def stats(self) -> Dict[str, Any]:
        self._purge_expired()
        unique_users = len({uid for uid, _ in self._store.keys()})
        total_reqs = self.hits + self.misses
        hit_rate = round((self.hits / total_reqs) * 100, 1) if total_reqs > 0 else 0.0
        return {
            "entries": len(self._store),
            "unique_users": unique_users,
            "max_entries": self.max_entries,
            "ttl": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "evictions": self.evictions,
            "invalidations": self.invalidations,
        }


USER_CACHE = UserDynamicCache(ttl=CACHE_TTL, max_entries=CACHE_MAX_ENTRIES)

BANNED = {}


class temp(object):
    BANNED_USERS = []
    BANNED_CHATS = []
    ME = None
    U_NAME = "MMWProBot"
    B_NAME = "MMW All-In-One Pro"
    MELCOW = {}
    SETTINGS = {}
    DEVIL_MODE = False
    MAINTENANCE_MODE = False
    ACTIVE_TASKS = {}
    BOT_CONFIG: Dict[str, Any] = {
        "maintenance_mode": False,
        "protect_content": PROTECT_CONTENT,
        "welcome_new_users": MELCOW_NEW_USERS,
        "auto_clean_service": False,
        "global_antispam": False,
        "log_new_users": True,
        "log_new_groups": True,
        "public_uptime": True,
        "channel_auto_sign": False,
        "auto_stream_global": True,
        "auto_caption_global": True,
        "auto_forward_global": True,
    }


async def auto_clean_expired_cache_job() -> None:
    interval = max(30, int(getattr(Config, "CACHE_CLEANUP_INTERVAL", 300)))
    logger.info("🧹 ᴄᴀᴄʜᴇ ᴄʟᴇᴀɴᴜᴘ ᴡᴏʀᴋᴇʀ ɪɴɪᴛɪᴀʟɪᴢᴇᴅ (%ss ᴄʏᴄʟᴇ)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            before = len(USER_CACHE._store)
            USER_CACHE._purge_expired()
            removed_shared = await cache.auto_clear_after_response(preserve_workflows=True)
            after = len(USER_CACHE._store)
            removed_user = max(0, before - after)
            if removed_user or removed_shared:
                logger.info("🧹 ᴄᴀᴄʜᴇ ᴘᴜʀɢᴇ ᴄʏᴄʟᴇ › ᴜsᴇʀ=%s sʜᴀʀᴇᴅ=%s ʀᴇᴍᴀɪɴɪɴɢ=%s", removed_user, removed_shared, after)
        except asyncio.CancelledError:
            logger.info("🛑 ᴄᴀᴄʜᴇ ᴄʟᴇᴀɴᴜᴘ ᴡᴏʀᴋᴇʀ sᴛᴏᴘᴘᴇᴅ")
            raise
        except Exception as e:
            logger.debug("⚠️ ᴄᴀᴄʜᴇ ᴄʟᴇᴀɴᴜᴘ ɴᴏᴛɪᴄᴇ › %s", e)


def get_readable_time(seconds: Union[int, float]) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}ᴅ")
    if hours:
        parts.append(f"{hours}ʜ")
    if minutes:
        parts.append(f"{minutes}ᴍ")
    if secs or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


format_uptime_short = get_readable_time


async def broadcast_messages(
    user_id: int,
    message: Message,
    pin: bool = False,
    is_chat: bool = False,
) -> Tuple[bool, str]:
    retries = 0
    target_id = int(user_id)
    while retries < 4:
        try:
            await rate_limit_manager.wait_for_write_slot(rpc_name="SendMessage", chat_id=target_id)
            sent = await message.copy(chat_id=target_id)
            rate_limit_manager.record_rpc_success()
            if pin and sent:
                for p_attempt in range(2):
                    try:
                        await sent.pin(both_sides=True)
                        break
                    except FloodWait as fw:
                        await pro_floodwait_sleep(min(_flood_wait_seconds(fw), 60), "broadcast_pin", chat_id=target_id)
                    except Exception:
                        try:
                            await sent.pin()
                        except Exception:
                            pass
                        break
            logger.debug("📤 ʙʀᴏᴀᴅᴄᴀsᴛ ᴅᴇʟɪᴠᴇʀᴇᴅ › ᴛᴀʀɢᴇᴛ=%s ᴘɪɴ=%s", target_id, pin)
            return True, "Success"
        except FloodWait as e:
            wait_s = _flood_wait_seconds(e)
            logger.warning("🌊 ʙʀᴏᴀᴅᴄᴀsᴛ ғʟᴏᴏᴅᴡᴀɪᴛ › ᴛᴀʀɢᴇᴛ=%s ᴡᴀɪᴛ=%ss (attempt %s/4)", target_id, wait_s, retries + 1)
            if wait_s > 180:
                return False, "FloodLimit"
            await pro_floodwait_sleep(wait_s, "broadcast_messages", chat_id=target_id)
            retries += 1
        except InputUserDeactivated:
            if not is_chat:
                await db.delete_user(target_id)
                logger.info("🗑️ ʙʀᴏᴀᴅᴄᴀsᴛ ʀᴇᴍᴏᴠᴇᴅ ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴜsᴇʀ › %s", target_id)
            return False, "Deleted"
        except UserIsBlocked:
            logger.debug("🚫 ʙʀᴏᴀᴅᴄᴀsᴛ ʙʟᴏᴄᴋᴇᴅ ʙʏ ᴜsᴇʀ › %s", target_id)
            return False, "Blocked"
        except PeerIdInvalid:
            if not is_chat:
                await db.delete_user(target_id)
            logger.debug("⚠️ ʙʀᴏᴀᴅᴄᴀsᴛ ɪɴᴠᴀʟɪᴅ ᴘᴇᴇʀ › %s", target_id)
            return False, "Deleted" if not is_chat else "Error"
        except Exception as e:
            err_l = f"{type(e).__name__} {e}".lower()
            if "deactivated" in err_l and not is_chat:
                await db.delete_user(target_id)
                return False, "Deleted"
            if "blocked" in err_l:
                return False, "Blocked"
            logger.debug("❌ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴘʏ ғᴀɪʟᴇᴅ › ᴛᴀʀɢᴇᴛ=%s ᴇʀʀ=%s", target_id, e)
            return False, "Error"
    return False, "Error"


async def get_settings(group_id):
    settings = temp.SETTINGS.get(group_id)
    if not settings:
        settings = await db.get_settings(group_id)
        temp.SETTINGS[group_id] = settings
    return settings


async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current[key] = value
    temp.SETTINGS[group_id] = current
    USER_CACHE.invalidate_user(int(group_id))
    await db.update_settings(group_id, current)


def get_size(size):
    if not size:
        return "0ʙ"
    size = float(size)
    for unit in ["ʙ", "ᴋʙ", "ᴍʙ", "ɢʙ", "ᴛʙ"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} ᴘʙ"


def split_list(l, n):
    for i in range(0, len(l), n):
        yield l[i : i + n]


def get_file_id(msg: Message):
    if msg and msg.media:
        for message_type in (
            "photo",
            "animation",
            "audio",
            "document",
            "video",
            "video_note",
            "voice",
            "sticker",
        ):
            obj = getattr(msg, message_type, None)
            if obj:
                setattr(obj, "message_type", message_type)
                return obj
    return None


def extract_user(message: Message) -> Tuple[Union[int, str, None], Optional[str]]:
    user_id = None
    user_first_name = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
        user_first_name = message.reply_to_message.from_user.first_name
    elif message.command and len(message.command) > 1:
        if (
            message.entities
            and len(message.entities) > 1
            and message.entities[1].type == enums.MessageEntityType.TEXT_MENTION
            and message.entities[1].user
        ):
            required_entity = message.entities[1]
            user_id = required_entity.user.id
            user_first_name = required_entity.user.first_name
        else:
            user_id = message.command[1]
            user_first_name = user_id
        try:
            user_id = int(user_id)
        except ValueError:
            pass
    elif message.from_user:
        user_id = message.from_user.id
        user_first_name = message.from_user.first_name
    return (user_id, user_first_name)


def last_online(from_user):
    if not from_user:
        return "ɴ/ᴀ"
    status_str = ""
    if from_user.is_bot:
        status_str += "🤖 ʙᴏᴛ"
    elif from_user.status == enums.UserStatus.RECENTLY:
        status_str += "ʀᴇᴄᴇɴᴛʟʏ"
    elif from_user.status == enums.UserStatus.LAST_WEEK:
        status_str += "ᴡɪᴛʜɪɴ ᴛʜᴇ ʟᴀsᴛ ᴡᴇᴇᴋ"
    elif from_user.status == enums.UserStatus.LAST_MONTH:
        status_str += "ᴡɪᴛʜɪɴ ᴛʜᴇ ʟᴀsᴛ ᴍᴏɴᴛʜ"
    elif from_user.status == enums.UserStatus.LONG_AGO:
        status_str += "ᴀ ʟᴏɴɢ ᴛɪᴍᴇ ᴀɢᴏ"
    elif from_user.status == enums.UserStatus.ONLINE:
        status_str += "🟢 ᴄᴜʀʀᴇɴᴛʟʏ ᴏɴʟɪɴᴇ"
    elif from_user.status == enums.UserStatus.OFFLINE and from_user.last_online_date:
        status_str += from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    else:
        status_str += "ᴀᴄᴛɪᴠᴇ"
    return status_str


def humanbytes(size):
    if not size:
        return "0 ʙ"
    power = 2**10
    n = 0
    Dic_powerN = {0: " ", 1: "ᴋɪ", 2: "ᴍɪ", 3: "ɢɪ", 4: "ᴛɪ"}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN.get(n, "ᴘɪ") + "ʙ"


def parse_Inline_buttons_from_text(raw_text: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Parse markdown-style button tags from note/filter/channel post text:
    Syntax: [Button Text](buttonurl:https://example.com) or [Button Text](buttonurl:https://example.com:same)
    Returns (cleaned_text, InlineKeyboardMarkup_or_None).
    """
    from pyrogram.types import InlineKeyboardButton

    if not raw_text:
        return "", None

    btn_pattern = re.compile(r"\[([^\[\]]+)\]\(buttonurl:(https?://[^\s\)]+?)(?::same)?\)")
    rows: List[List[InlineKeyboardButton]] = []

    for match in re.finditer(r"\[([^\[\]]+)\]\(buttonurl:(https?://[^\s\)]+?)(\:same)?\)", raw_text):
        label = match.group(1).strip()
        url = match.group(2).strip()
        same_line = bool(match.group(3))
        btn = InlineKeyboardButton(label, url=url)
        if same_line and rows:
            rows[-1].append(btn)
        else:
            rows.append([btn])

    cleaned = btn_pattern.sub("", raw_text).strip()
    return cleaned, (InlineKeyboardMarkup(rows) if rows else None)
