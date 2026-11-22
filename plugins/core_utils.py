import time
import asyncio
import contextvars
import functools
import hashlib
import inspect
import logging
from logging.handlers import RotatingFileHandler
import sys
import os
import re
import math
import shutil
import uuid
from collections import deque, OrderedDict, defaultdict
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import Any, Optional, Dict, List, Tuple, Callable, Awaitable, Set
from config import Config
try:
    from pyrogram.errors import FloodWait, MessageNotModified
except ImportError:
    FloodWait = type("FloodWait", (Exception,), {})
    MessageNotModified = type("MessageNotModified", (Exception,), {})

SMALLCAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 's', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
    '0': '𝟶', '1': '𝟷', '2': '𝟸', '3': '𝟹', '4': '𝟺',
    '5': '𝟻', '6': '𝟼', '7': '𝟽', '8': '𝟾', '9': '𝟿'
}

ALPHA_SMALLCAPS_MAP = {k: v for k, v in SMALLCAPS_MAP.items() if k.isalpha()}
_SMALLCAPS_SPLIT_RE = re.compile(r'(<[^>]+>|https?://\S+|@\w+|/[a-zA-Z0-9_]+|\{[a-zA-Z0-9_:.]*\})')


@functools.lru_cache(maxsize=8192)
def _cached_smallcaps(text: str, convert_digits: bool = False) -> str:
    cmap = SMALLCAPS_MAP if convert_digits else ALPHA_SMALLCAPS_MAP
    tokens = _SMALLCAPS_SPLIT_RE.split(text)
    out = []
    for idx, tok in enumerate(tokens):
        if idx % 2 == 1:
            out.append(tok)
        else:
            out.append("".join(cmap.get(c, c) for c in tok))
    return "".join(out)


def to_smallcaps(text: str, convert_digits: bool = False) -> str:
    """Converts text into clean smallcap stylish font while preserving URLs, HTML tags, @mentions, and /commands."""
    if not text:
        return ""
    s = str(text)
    if len(s) <= 512:
        return _cached_smallcaps(s, bool(convert_digits))
    cmap = SMALLCAPS_MAP if convert_digits else ALPHA_SMALLCAPS_MAP
    tokens = _SMALLCAPS_SPLIT_RE.split(s)
    out = []
    for idx, tok in enumerate(tokens):
        if idx % 2 == 1:
            out.append(tok)
        else:
            out.append("".join(cmap.get(c, c) for c in tok))
    return "".join(out)


sc = to_smallcaps
to_small_caps = to_smallcaps
to_smallcap = to_smallcaps
stylish = to_smallcaps


def style_text(header: str, items: dict = None) -> str:
    out = f"✦ **{to_smallcaps(header)}** ✦\n\n"
    if items:
        for k, v in items.items():
            out += f"• **{to_smallcaps(k)}** : `{v}`\n"
        out += "\n"
    out += f"⚡ **{to_smallcaps('powered by')}** : [{Config.WATERMARK}]({Config.WATERMARK_URL})"
    return out


def format_watermark() -> str:
    return f"\n\n📢 **{to_smallcaps('join')}** : [{Config.WATERMARK}]({Config.WATERMARK_URL})"


def format_watermark_html() -> str:
    import html as _html
    return f"\n\n📢 <b>{to_smallcaps('join')} : <a href='{Config.WATERMARK_URL}'>{_html.escape(str(Config.WATERMARK))}</a></b>"


LOG_FILE_PATH = "TelegramBot.txt"
MEMORY_LOGS: List[str] = []

_SECRET_PATTERNS = [
    re.compile(r"mongodb(?:\+srv)?://[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,50}\b"),
    re.compile(r"(?i)(token|secret|password)\s*[=:]\s*[^\s,;\"']+"),
]


def redact_secrets(text: str) -> str:
    if not text:
        return ""
    cleaned = str(text)
    for secret_val in (Config.BOT_TOKEN, Config.API_HASH, Config.MONGO_URI):
        if secret_val and len(str(secret_val)) > 6:
            cleaned = cleaned.replace(str(secret_val), "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        cleaned = pat.sub("[REDACTED]", cleaned)
    return cleaned


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = redact_secrets(record.getMessage())
                record.args = ()
            else:
                record.msg = redact_secrets(record.msg)
        except Exception:
            pass
        return True


FLOODWAIT_STATS: Dict[str, Any] = {
    "total_events": 0,
    "total_slept_seconds": 0,
    "last_wait_seconds": 0,
    "last_method": "None",
    "last_timestamp": 0.0,
    "deduplicated_edits": 0,
    "coalesced_progress_edits": 0,
    "scheduled_resumed_edits": 0,
}


class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            MEMORY_LOGS.append(msg)
            if len(MEMORY_LOGS) > 2000:
                del MEMORY_LOGS[: len(MEMORY_LOGS) - 2000]
        except Exception:
            pass


def setup_logger(name: str = "MMW_PRO") -> logging.Logger:
    root_logger = logging.getLogger()
    if not getattr(root_logger, "_mmw_unified_configured", False):
        root_logger.handlers.clear()
        root_logger.addHandler(logging.NullHandler())
        root_logger.setLevel(logging.CRITICAL + 10)
        root_logger.propagate = False
        logging.disable(logging.CRITICAL)
        setattr(root_logger, "_mmw_unified_configured", True)
        setattr(root_logger, "_goku_configured", True)
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.setLevel(logging.CRITICAL + 10)
    lg.propagate = False
    return lg


logger = setup_logger()


def _flood_wait_seconds(error: Exception) -> int:
    """Extract exact server-provided wait duration from Pyrogram FloodWait exceptions."""
    try:
        val = getattr(error, "value", None)
        if val is None:
            val = getattr(error, "x", None)
        if val is None:
            m = re.search(r"(\d+)\s*seconds", str(error), re.IGNORECASE)
            if not m:
                m = re.search(r"FLOOD_WAIT_(\d+)", str(error), re.IGNORECASE)
            val = int(m.group(1)) if m else 1
        return max(1, int(val))
    except (TypeError, ValueError):
        return 1


def get_msg_text(msg: Any) -> str:
    """Safely extract text or caption from a Message (or return string directly if msg is already a str)."""
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    txt = getattr(msg, "text", None)
    if isinstance(txt, str):
        return txt
    cap = getattr(msg, "caption", None)
    if isinstance(cap, str):
        return cap
    return str(txt or cap or "")


def _normalize_reply_markup(reply_markup: Any) -> Any:
    """Normalize InlineKeyboardMarkup rows so strings or 1D button lists never raise AttributeError on .text."""
    if reply_markup is None:
        return None
    try:
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        rows = getattr(reply_markup, "inline_keyboard", None)
        if rows is not None:
            norm_rows = []
            for r in rows:
                if isinstance(r, (list, tuple)):
                    row_btns = []
                    for b in r:
                        if isinstance(b, str):
                            row_btns.append(InlineKeyboardButton(b, callback_data="noop"))
                        elif isinstance(b, dict):
                            row_btns.append(
                                InlineKeyboardButton(
                                    str(b.get("text", "Button")),
                                    callback_data=b.get("callback_data"),
                                    url=b.get("url"),
                                )
                            )
                        elif b is not None:
                            if not hasattr(b, "text"):
                                setattr(b, "text", str(b))
                            row_btns.append(b)
                    if row_btns:
                        norm_rows.append(row_btns)
                elif isinstance(r, str):
                    norm_rows.append([InlineKeyboardButton(r, callback_data="noop")])
                elif r is not None:
                    if not hasattr(r, "text"):
                        setattr(r, "text", str(r))
                    norm_rows.append([r])
            reply_markup.inline_keyboard = norm_rows
    except Exception:
        pass
    return reply_markup


def _compute_edit_signature(text: Any, reply_markup: Any = None) -> str:
    """Compute a deterministic hash signature of message text + inline keyboard markup."""
    if not isinstance(text, str):
        text = get_msg_text(text)
    markup_str = ""
    if reply_markup is not None:
        try:
            rows = getattr(reply_markup, "inline_keyboard", None)
            if rows:
                parts = []
                for r in rows:
                    row_iter = r if isinstance(r, (list, tuple)) else [r]
                    for b in row_iter:
                        if isinstance(b, str):
                            parts.append(f"{b}::")
                        elif isinstance(b, dict):
                            parts.append(f"{b.get('text', '')}:{b.get('callback_data', '')}:{b.get('url', '')}")
                        else:
                            parts.append(f"{getattr(b, 'text', '')}:{getattr(b, 'callback_data', '')}:{getattr(b, 'url', '')}")
                markup_str = "|".join(parts)
            else:
                markup_str = str(reply_markup)
        except Exception:
            markup_str = str(reply_markup)
    raw = f"{text or ''}||{markup_str}"
    return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()


def _extract_msg_identity(message: Any) -> Tuple[int, int]:
    if message is None or isinstance(message, (str, bytes, int, float, bool)):
        return (0, 0)
    chat = getattr(message, "chat", None)
    chat_id = int(getattr(chat, "id", 0) or getattr(message, "chat_id", 0) or 0)
    msg_id = int(getattr(message, "id", 0) or getattr(message, "message_id", 0) or 0)
    return (chat_id, msg_id)


class TelegramRateLimitManager:
    """
    Production-Grade Centralized Telegram API Rate-Limit Manager:
    - Per-client and per-chat FloodWait cooldown tracking using server-provided durations.
    - Non-blocking asynchronous token-bucket & interval pacing per chat and globally.
    - Message-edit content deduplication (skips unchanged edits with 0 API calls).
    - Concurrent edit coalescing & automatic deferred execution for essential updates.
    - Telemetry tracking (`last_rpc_success_ts`, `active_cooldown_seconds`) for KeepAlive health.
    """
    def __init__(self):
        self._global_lock = asyncio.Lock()
        self._last_global_write_ts: float = 0.0
        self._client_cooldown_until: float = 0.0
        self._progress_block_until: float = 0.0

        self._chat_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._chat_cooldown_until: Dict[int, float] = defaultdict(float)
        self._chat_last_write_ts: Dict[int, float] = defaultdict(float)
        self._chat_last_edit_ts: Dict[int, float] = defaultdict(float)

        self._msg_locks: Dict[Tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._msg_last_edit_ts: Dict[Tuple[int, int], float] = {}
        self._msg_last_sig: Dict[Tuple[int, int], str] = {}
        self._msg_pending_edit: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._msg_flush_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

        self.last_rpc_success_ts: float = time.time()
        self.last_floodwait_ts: float = 0.0

    def record_rpc_success(self) -> None:
        self.last_rpc_success_ts = time.time()

    def is_in_cooldown(self, chat_id: int = 0) -> bool:
        now = time.time()
        if now < self._client_cooldown_until:
            return True
        if chat_id and now < self._chat_cooldown_until.get(int(chat_id), 0.0):
            return True
        return False

    def active_cooldown_seconds(self, chat_id: int = 0) -> float:
        now = time.time()
        rem = max(0.0, self._client_cooldown_until - now)
        if chat_id:
            rem = max(rem, self._chat_cooldown_until.get(int(chat_id), 0.0) - now)
        return round(rem, 2)

    async def notify_floodwait(self, method_name: str, wait_s: int, chat_id: int = 0) -> None:
        now = time.time()
        eff_wait = max(1, int(wait_s))
        self.last_floodwait_ts = now
        self._client_cooldown_until = max(self._client_cooldown_until, now + eff_wait)
        self._progress_block_until = max(self._progress_block_until, now + eff_wait + 2.0)
        if chat_id:
            self._chat_cooldown_until[int(chat_id)] = max(
                self._chat_cooldown_until.get(int(chat_id), 0.0),
                now + eff_wait + 1.0,
            )

    async def wait_for_write_slot(
        self,
        rpc_name: str = "SendMessage",
        chat_id: int = 0,
        is_edit: bool = False,
        is_optional_progress: bool = False,
    ) -> bool:
        """
        Acquire a rate-limited write slot for Telegram RPCs.
        Returns False if `is_optional_progress` is True and a cooldown or throttle window is active.
        """
        now = time.time()
        if is_optional_progress:
            if now < self._progress_block_until or self.is_in_cooldown(chat_id):
                FLOODWAIT_STATS["coalesced_progress_edits"] += 1
                return False

        while True:
            rem = self.active_cooldown_seconds(chat_id)
            if rem <= 0:
                break
            if is_optional_progress:
                FLOODWAIT_STATS["coalesced_progress_edits"] += 1
                return False
            await asyncio.sleep(min(rem + 0.1, 5.0))

        if chat_id:
            async with self._chat_locks[int(chat_id)]:
                now_m = time.monotonic()
                if is_edit:
                    min_chat_gap = getattr(Config, "PER_CHAT_EDIT_INTERVAL_SEC", 1.8)
                    elapsed = now_m - self._chat_last_edit_ts.get(int(chat_id), 0.0)
                    if elapsed < min_chat_gap:
                        if is_optional_progress:
                            FLOODWAIT_STATS["coalesced_progress_edits"] += 1
                            return False
                    self._chat_last_edit_ts[int(chat_id)] = time.monotonic()
                else:
                    min_chat_gap = 1.0 / max(1.0, float(getattr(Config, "PER_CHAT_RPC_RATE_PER_SEC", 4.0)))
                    elapsed = now_m - self._chat_last_write_ts.get(int(chat_id), 0.0)
                    if elapsed < min_chat_gap:
                        await asyncio.sleep(min_chat_gap - elapsed)
                    self._chat_last_write_ts[int(chat_id)] = time.monotonic()

        async with self._global_lock:
            now_m = time.monotonic()
            global_gap = 1.0 / max(5.0, float(getattr(Config, "GLOBAL_RPC_RATE_PER_SEC", 28.0)))
            gap = global_gap - (now_m - self._last_global_write_ts)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_global_write_ts = time.monotonic()

        return True

    def check_and_record_signature(self, chat_id: int, msg_id: int, sig: str) -> bool:
        """Returns True if signature is unchanged (duplicate edit), False if new."""
        if not chat_id or not msg_id:
            return False
        key = (int(chat_id), int(msg_id))
        if self._msg_last_sig.get(key) == sig:
            FLOODWAIT_STATS["deduplicated_edits"] += 1
            return True
        return False

    def commit_signature(self, chat_id: int, msg_id: int, sig: str) -> None:
        if chat_id and msg_id:
            key = (int(chat_id), int(msg_id))
            self._msg_last_sig[key] = sig
            self._msg_last_edit_ts[key] = time.monotonic()
            if len(self._msg_last_sig) > 4000:
                oldest = next(iter(self._msg_last_sig))
                self._msg_last_sig.pop(oldest, None)
                self._msg_last_edit_ts.pop(oldest, None)

    def clear_message_tracking(self, chat_id: int, msg_id: int) -> None:
        if chat_id and msg_id:
            key = (int(chat_id), int(msg_id))
            self._msg_last_sig.pop(key, None)
            self._msg_last_edit_ts.pop(key, None)
            self._msg_pending_edit.pop(key, None)
            self._msg_locks.pop(key, None)

    def get_stats(self) -> Dict[str, Any]:
        return {
            **FLOODWAIT_STATS,
            "active_cooldown_s": self.active_cooldown_seconds(),
            "last_rpc_success_age_s": round(max(0.0, time.time() - self.last_rpc_success_ts), 1),
            "tracked_message_signatures": len(self._msg_last_sig),
        }


rate_limit_manager = TelegramRateLimitManager()

_TELEGRAM_WRITE_BLOCK_UNTIL = 0.0
_PROGRESS_EDIT_BLOCK_UNTIL = 0.0
_TELEGRAM_WRITE_LOCK = asyncio.Lock()


async def _set_write_cooldown(until: float) -> None:
    global _TELEGRAM_WRITE_BLOCK_UNTIL
    async with _TELEGRAM_WRITE_LOCK:
        _TELEGRAM_WRITE_BLOCK_UNTIL = max(_TELEGRAM_WRITE_BLOCK_UNTIL, until)
        rate_limit_manager._client_cooldown_until = max(rate_limit_manager._client_cooldown_until, until)


def record_floodwait_event(method_name: str, wait_s: int) -> None:
    FLOODWAIT_STATS["total_events"] = int(FLOODWAIT_STATS.get("total_events", 0)) + 1
    FLOODWAIT_STATS["total_slept_seconds"] = int(FLOODWAIT_STATS.get("total_slept_seconds", 0)) + int(wait_s)
    FLOODWAIT_STATS["last_wait_seconds"] = int(wait_s)
    FLOODWAIT_STATS["last_method"] = str(method_name)
    FLOODWAIT_STATS["last_timestamp"] = float(time.time())


async def pro_floodwait_sleep(wait_s: int, context_label: str = "TelegramRPC", chat_id: int = 0) -> None:
    """Pro FloodWait sleep helper using asyncio.sleep() with accurate server duration and chunked cancellation."""
    total_wait = max(1, int(wait_s)) + 1
    record_floodwait_event(context_label, total_wait)
    await rate_limit_manager.notify_floodwait(context_label, total_wait, chat_id=chat_id)
    logger.warning(
        "🌊 [ᴘʀᴏ ғʟᴏᴏᴅᴡᴀɪᴛ sᴏʟᴠᴇʀ] %s › sleeping %ss via asyncio.sleep() (total_events=%s)",
        context_label,
        total_wait,
        FLOODWAIT_STATS["total_events"],
    )
    remaining = float(total_wait)
    while remaining > 0:
        step = min(remaining, 10.0)
        await asyncio.sleep(step)
        remaining -= step


_RESPONSE_CTX_CACHE: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "_RESPONSE_CTX_CACHE", default=None
)
_PERSISTENT_KEY_PREFIXES = (
    "waiting_",
    "rename_name_",
    "custcomp_cfg_",
    "comp_target_",
    "sess_step_",
    "last_media_",
    "user_",
    "chat_",
    "file_",
    "task_",
    "media_fid_",
    "banned_",
    "tg_msg_",
    "chunk_",
    "stats_",
    "web_home_stats",
)
_TRANSIENT_KEY_PREFIXES = (
    "resp_",
    "tmp_",
    "cmd_",
    "cb_",
    "fn_",
    "probe_",
    "ffstatus_",
    "code_",
    "render_",
)


class FastMemoryCache:
    """
    High-performance multi-layer O(1) LRU + TTL + Per-Response Context Caching Engine.
    - L0 Context Cache: Zero-lock per-command / per-callback / per-function memoization that
      automatically clears immediately after each message/callback response completes.
    - L1 LRU+TTL RAM Cache: High-hit-ratio shared cache for user settings, chat configs, stream tokens,
      and active multi-step workflow states.
    """
    def __init__(self, default_ttl: Optional[int] = None, max_entries: Optional[int] = None):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._expires: Dict[str, float] = {}
        self._transient_keys: Set[str] = set()
        self._default_ttl = default_ttl if default_ttl is not None else getattr(Config, "CACHE_TTL", 900)
        self._max_entries = max_entries if max_entries is not None else getattr(Config, "CACHE_MAX_ENTRIES", 12000)
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._auto_clears = 0
        self._response_scopes_cleared = 0

    @asynccontextmanager
    async def response_scope(self, scope_name: str = "handler"):
        ctx_dict: Dict[str, Any] = {}
        token = _RESPONSE_CTX_CACHE.set(ctx_dict)
        try:
            yield ctx_dict
        finally:
            skip_sweep = bool(ctx_dict.get("__skip_sweep__", False))
            ctx_dict.clear()
            _RESPONSE_CTX_CACHE.reset(token)
            if not skip_sweep:
                self._response_scopes_cleared += 1
                self.auto_clear_after_response_sync()

    def get_sync(self, key: str) -> Optional[Any]:
        ctx = _RESPONSE_CTX_CACHE.get()
        if ctx is not None and key in ctx:
            self._hits += 1
            return ctx[key]
        if key not in self._cache:
            return None
        curr_now = float(time.time())
        if curr_now > self._expires.get(key, 0.0):
            self._cache.pop(key, None)
            self._expires.pop(key, None)
            self._transient_keys.discard(key)
            return None
        self._hits += 1
        return self._cache[key]

    async def get(self, key: str) -> Optional[Any]:
        ctx = _RESPONSE_CTX_CACHE.get()
        if ctx is not None and key in ctx:
            self._hits += 1
            return ctx[key]
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            curr_now = float(time.time())
            if curr_now > self._expires.get(key, 0):
                self._cache.pop(key, None)
                self._expires.pop(key, None)
                self._transient_keys.discard(key)
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            val = self._cache[key]
            if ctx is not None:
                ctx[key] = val
            return val

    async def set(self, key: str, value: Any, ttl: Optional[int] = None, transient: bool = False):
        ctx = _RESPONSE_CTX_CACHE.get()
        if ctx is not None:
            ctx[key] = value
        async with self._lock:
            curr_now = float(time.time())
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self._max_entries:
                expired_keys = [k for k, exp in list(self._expires.items()) if curr_now > exp]
                for k in expired_keys:
                    self._cache.pop(k, None)
                    self._expires.pop(k, None)
                    self._transient_keys.discard(k)
                if len(self._cache) >= self._max_entries:
                    oldest_key, _ = self._cache.popitem(last=False)
                    self._expires.pop(oldest_key, None)
                    self._transient_keys.discard(oldest_key)

            self._cache[key] = value
            effective_ttl = ttl if ttl is not None else self._default_ttl
            self._expires[key] = curr_now + float(effective_ttl)
            if transient or key.startswith(_TRANSIENT_KEY_PREFIXES):
                self._transient_keys.add(key)

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Any],
        ttl: Optional[int] = 120,
        transient: bool = True,
    ) -> Any:
        """Fetch from L0/L1 cache or compute once and store (supports both async and sync callables)."""
        val = await self.get(key)
        if val is not None:
            return val
        res = compute_fn()
        if inspect.isawaitable(res):
            res = await res
        if res is not None:
            await self.set(key, res, ttl=ttl, transient=transient)
        return res

    async def delete(self, key: str) -> bool:
        ctx = _RESPONSE_CTX_CACHE.get()
        if ctx is not None:
            ctx.pop(key, None)
        async with self._lock:
            existed = key in self._cache
            self._cache.pop(key, None)
            self._expires.pop(key, None)
            self._transient_keys.discard(key)
            return existed

    def auto_clear_after_response_sync(self, preserve_workflows: bool = True) -> int:
        """
        Fast non-blocking post-response cache sweep executed automatically after every command,
        callback, or message response. Purges L0 context cache, transient keys, and expired entries
        while preserving active workflow states.
        """
        ctx = _RESPONSE_CTX_CACHE.get()
        removed = 0
        if ctx is not None:
            removed += len(ctx)
            ctx.clear()

        curr_now = float(time.time())
        for k in list(self._transient_keys):
            if preserve_workflows and k.startswith(_PERSISTENT_KEY_PREFIXES):
                continue
            self._transient_keys.discard(k)
            if self._cache.pop(k, None) is not None:
                removed += 1
            self._expires.pop(k, None)

        if len(self._expires) > 0:
            for k, exp in list(self._expires.items()):
                if curr_now > exp:
                    self._cache.pop(k, None)
                    self._expires.pop(k, None)
                    self._transient_keys.discard(k)
                    removed += 1

        if len(_PROGRESS_THROTTLE) > 64:
            for pk, ts in list(_PROGRESS_THROTTLE.items()):
                if (curr_now - ts) > 120.0:
                    _PROGRESS_THROTTLE.pop(pk, None)
                    _PROGRESS_STEP.pop(pk, None)

        self._auto_clears += 1
        return removed

    async def auto_clear_after_response(self, preserve_workflows: bool = True) -> int:
        async with self._lock:
            return self.auto_clear_after_response_sync(preserve_workflows=preserve_workflows)

    def cached_function(self, ttl: int = 60, transient: bool = True):
        """Decorator for caching async or sync functions with automatic post-response cleanup."""
        def decorator(func: Callable):
            if asyncio.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    try:
                        key_raw = f"fn_{func.__module__}.{func.__qualname__}:{args}:{ sorted(kwargs.items()) }"
                        key = f"fn_{hashlib.md5(key_raw.encode('utf-8', errors='ignore')).hexdigest()}"
                    except Exception:
                        return await func(*args, **kwargs)
                    cached_val = await self.get(key)
                    if cached_val is not None:
                        return cached_val
                    res = await func(*args, **kwargs)
                    if res is not None:
                        await self.set(key, res, ttl=ttl, transient=transient)
                    return res
                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    try:
                        key_raw = f"fn_{func.__module__}.{func.__qualname__}:{args}:{ sorted(kwargs.items()) }"
                        key = f"fn_{hashlib.md5(key_raw.encode('utf-8', errors='ignore')).hexdigest()}"
                    except Exception:
                        return func(*args, **kwargs)
                    cached_val = self.get_sync(key)
                    if cached_val is not None:
                        return cached_val
                    res = func(*args, **kwargs)
                    ctx = _RESPONSE_CTX_CACHE.get()
                    if ctx is not None and res is not None:
                        ctx[key] = res
                    return res
                return sync_wrapper
        return decorator

    def cached_command(self, func: Callable):
        """Wrap a Pyrogram command handler in an isolated L0 response cache scope that auto-clears after response."""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async with self.response_scope(f"cmd:{func.__name__}"):
                return await func(*args, **kwargs)
        return wrapper

    def cached_callback(self, func: Callable):
        """Wrap a Pyrogram callback handler in an isolated L0 response cache scope that auto-clears after response."""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async with self.response_scope(f"cb:{func.__name__}"):
                return await func(*args, **kwargs)
        return wrapper

    async def clear(self):
        ctx = _RESPONSE_CTX_CACHE.get()
        if ctx is not None:
            ctx.clear()
        async with self._lock:
            self._cache.clear()
            self._expires.clear()
            self._transient_keys.clear()

    async def get_stats(self) -> Dict[str, Any]:
        async with self._lock:
            total_requests = self._hits + self._misses
            hit_ratio = (self._hits / total_requests * 100) if total_requests > 0 else 100.0
            return {
                "cached_keys": len(self._cache),
                "transient_keys": len(self._transient_keys),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio_percent": round(hit_ratio, 2),
                "auto_clears": self._auto_clears,
                "response_scopes_cleared": self._response_scopes_cleared,
            }


cache = FastMemoryCache()


async def execute_with_floodwait(
    coro_func,
    *args,
    max_retries: int = 6,
    max_wait_seconds: int = 3600,
    **kwargs
):
    """
    Centralized FloodWait & RPC retry wrapper for critical Telegram operations
    (uploads, downloads, copies, forwards, and moderation actions).
    Respects exact FloodWait duration returned by Telegram, sleeps asynchronously,
    and prevents infinite retry loops.
    """
    last_exc = None
    chat_id = int(kwargs.get("chat_id", 0) or 0)
    fn_label = getattr(coro_func, "__name__", str(coro_func))
    for attempt in range(max(1, int(max_retries))):
        try:
            await rate_limit_manager.wait_for_write_slot(rpc_name=fn_label, chat_id=chat_id)
            res = await coro_func(*args, **kwargs)
            rate_limit_manager.record_rpc_success()
            if not any(k in fn_label.lower() for k in ("download", "stream", "send_video", "send_document", "send_audio", "get_file")):
                await asyncio.sleep(0.05)
            return res
        except FloodWait as fw:
            wait_s = _flood_wait_seconds(fw)
            await _set_write_cooldown(time.time() + min(wait_s, 60))
            if wait_s > max_wait_seconds or attempt >= max_retries - 1:
                logger.warning(
                    "🌊 ғʟᴏᴏᴅᴡᴀɪᴛ (%ss) ᴇxᴄᴇᴇᴅᴇᴅ ᴍᴀx_ᴡᴀɪᴛ/ʀᴇᴛʀɪᴇs (ᴀᴛᴛᴇᴍᴘᴛ %s/%s) ғᴏʀ %s",
                    wait_s,
                    attempt + 1,
                    max_retries,
                    fn_label,
                )
                raise
            await pro_floodwait_sleep(wait_s, f"{fn_label}#{attempt+1}/{max_retries}", chat_id=chat_id)
            last_exc = fw
        except MessageNotModified:
            rate_limit_manager.record_rpc_success()
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            err_name = type(exc).__name__.lower()
            if any(k in err_name for k in ("timeout", "connection", "network", "brokenpipe", "oserror", "internal", "serviceunavailable", "badgateway", "slowmode")) and attempt < max_retries - 1:
                await asyncio.sleep(min(4.0, 0.35 * (attempt + 1)))
                continue
            raise
    if last_exc:
        raise last_exc
    return None


async def safe_edit_text(message: Any, text: Any = "", **kwargs: Any) -> Any:
    """
    Centralized, deduplicated, and coalesced message editor:
    - Avoids editing unchanged messages (content + reply_markup signature check).
    - Prevents duplicate concurrent edits on the same message via per-message lock & coalescing.
    - Gracefully skips optional intermediate progress edits during FloodWait cooldowns or high load.
    - Automatically clears transient per-response cache when a non-progress response completes.
    """
    global _TELEGRAM_WRITE_BLOCK_UNTIL, _PROGRESS_EDIT_BLOCK_UNTIL
    if message is None:
        return None
    if isinstance(message, str) and not isinstance(text, str) and hasattr(text, "chat"):
        message, text = text, message
    if isinstance(message, (str, bytes, int, float, bool)):
        return None
    if not isinstance(text, str):
        text = get_msg_text(text)
    if "reply_markup" in kwargs and kwargs["reply_markup"] is not None:
        kwargs["reply_markup"] = _normalize_reply_markup(kwargs["reply_markup"])

    chat_id, msg_id = _extract_msg_identity(message)
    reply_markup = kwargs.get("reply_markup")
    sig = _compute_edit_signature(text, reply_markup)

    if rate_limit_manager.check_and_record_signature(chat_id, msg_id, sig):
        return message

    now = time.time()
    is_progress = ("▰" in str(text) or "▱" in str(text))
    is_final_progress = is_progress and ("100.0%" in str(text) or "100%" in str(text))
    is_optional_progress = is_progress and not is_final_progress
    if not is_progress:
        stale_prog = _ACTIVE_PROGRESS_TASKS.pop((chat_id, msg_id), None)
        if stale_prog is not None and not stale_prog.done() and stale_prog is not asyncio.current_task():
            stale_prog.cancel()

    if is_optional_progress:
        if now < _PROGRESS_EDIT_BLOCK_UNTIL or rate_limit_manager.is_in_cooldown(chat_id):
            FLOODWAIT_STATS["coalesced_progress_edits"] += 1
            return message
        last_edit_m = rate_limit_manager._msg_last_edit_ts.get((chat_id, msg_id), 0.0)
        active_tasks = max(1, task_manager.active_count)
        min_edit_gap = max(
            float(getattr(Config, "PROGRESS_EDIT_MIN_INTERVAL_SEC", 3.0)),
            min(12.0, 1.8 * active_tasks),
        )
        if (time.monotonic() - last_edit_m) < min_edit_gap:
            FLOODWAIT_STATS["coalesced_progress_edits"] += 1
            return message

    msg_key = (chat_id, msg_id)
    msg_lock = rate_limit_manager._msg_locks[msg_key]

    if msg_lock.locked():
        if is_optional_progress:
            FLOODWAIT_STATS["coalesced_progress_edits"] += 1
            return message
        rate_limit_manager._msg_pending_edit[msg_key] = {"text": text, "kwargs": dict(kwargs), "sig": sig}

    async with msg_lock:
        pending = rate_limit_manager._msg_pending_edit.pop(msg_key, None)
        if pending is not None:
            text = pending["text"]
            kwargs = pending["kwargs"]
            sig = pending["sig"]
            FLOODWAIT_STATS["scheduled_resumed_edits"] += 1

        if rate_limit_manager.check_and_record_signature(chat_id, msg_id, sig):
            return message

        slot_ok = await rate_limit_manager.wait_for_write_slot(
            rpc_name="EditMessage",
            chat_id=chat_id,
            is_edit=True,
            is_optional_progress=is_optional_progress,
        )
        if not slot_ok:
            return message

        for attempt in range(4):
            try:
                has_media = getattr(message, "media", None) is not None and getattr(message, "text", None) is None
                if has_media:
                    client = getattr(message, "_client", None)
                    if client and getattr(message, "chat", None):
                        sent = await client.send_message(chat_id=message.chat.id, text=text, **kwargs)
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        rate_limit_manager.record_rpc_success()
                        if sent:
                            s_chat, s_id = _extract_msg_identity(sent)
                            rate_limit_manager.commit_signature(s_chat, s_id, sig)
                        if not is_optional_progress:
                            cache.auto_clear_after_response_sync()
                        return sent
                edited = await message.edit_text(text=text, **kwargs)
                rate_limit_manager.record_rpc_success()
                rate_limit_manager.commit_signature(chat_id, msg_id, sig)
                if not is_optional_progress:
                    cache.auto_clear_after_response_sync()
                return edited or message
            except FloodWait as error:
                wait_for = _flood_wait_seconds(error)
                _PROGRESS_EDIT_BLOCK_UNTIL = max(_PROGRESS_EDIT_BLOCK_UNTIL, time.time() + wait_for + 2.0)
                await rate_limit_manager.notify_floodwait("safe_edit_text", wait_for, chat_id=chat_id)
                if is_optional_progress:
                    record_floodwait_event("safe_edit_progress_skip", wait_for)
                    return message
                if attempt >= 3:
                    return message
                await pro_floodwait_sleep(min(wait_for, 120), "safe_edit_text", chat_id=chat_id)
            except MessageNotModified:
                rate_limit_manager.record_rpc_success()
                rate_limit_manager.commit_signature(chat_id, msg_id, sig)
                return message
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.debug(f"⚠️ ᴏᴘᴛɪᴏɴᴀʟ ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀᴛᴜs ᴇᴅɪᴛ sᴋɪᴘᴘᴇᴅ: {error}")
                return message
        return message


async def safe_reply_text(target: Any, text: Any = "", **kwargs: Any) -> Any:
    """Reliable reply creation with centralized rate-limit pacing, FloodWait retry, and auto post-response cache clear."""
    global _TELEGRAM_WRITE_BLOCK_UNTIL
    if target is None:
        return None
    if isinstance(target, str) and not isinstance(text, str) and hasattr(text, "chat"):
        target, text = text, target
    if isinstance(target, (str, bytes, int, float, bool)):
        return None
    if not isinstance(text, str):
        text = get_msg_text(text)
    if "reply_markup" in kwargs and kwargs["reply_markup"] is not None:
        kwargs["reply_markup"] = _normalize_reply_markup(kwargs["reply_markup"])
    chat_id, _ = _extract_msg_identity(target)
    for attempt in range(4):
        try:
            await rate_limit_manager.wait_for_write_slot(rpc_name="SendMessage", chat_id=chat_id)
            sent = await target.reply_text(text, **kwargs)
            rate_limit_manager.record_rpc_success()
            if sent:
                s_chat, s_id = _extract_msg_identity(sent)
                rate_limit_manager.commit_signature(s_chat, s_id, _compute_edit_signature(text, kwargs.get("reply_markup")))
            cache.auto_clear_after_response_sync()
            return sent
        except FloodWait as error:
            wait_for = _flood_wait_seconds(error)
            await _set_write_cooldown(time.time() + min(wait_for, 60))
            if attempt < 3:
                await pro_floodwait_sleep(wait_for, "safe_reply_text", chat_id=chat_id)
                continue
            break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug(f"⚠️ ᴛᴇʟᴇɢʀᴀᴍ ʀᴇᴘʟʏ ғᴀʟʟʙᴀᴄᴋ: {error}")
            break
    client = getattr(target, "_client", None)
    chat = getattr(target, "chat", None)
    if client and chat:
        try:
            send_kw = {k: v for k, v in kwargs.items() if k != "quote"}
            return await safe_send_message(client, chat_id=chat.id, text=text, **send_kw)
        except Exception:
            pass
    return None


async def safe_send_message(client: Any, **kwargs: Any) -> Any:
    """Reliable message send with centralized rate-limit pacing, FloodWait retry, and auto post-response cache clear."""
    global _TELEGRAM_WRITE_BLOCK_UNTIL
    chat_id = int(kwargs.get("chat_id", 0) or 0)
    for attempt in range(4):
        try:
            await rate_limit_manager.wait_for_write_slot(rpc_name="SendMessage", chat_id=chat_id)
            sent = await client.send_message(**kwargs)
            rate_limit_manager.record_rpc_success()
            cache.auto_clear_after_response_sync()
            return sent
        except FloodWait as error:
            wait_for = _flood_wait_seconds(error)
            await _set_write_cooldown(time.time() + min(wait_for, 60))
            if attempt < 3:
                await pro_floodwait_sleep(wait_for, "safe_send_message", chat_id=chat_id)
                continue
            return None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug(f"⚠️ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇssᴀɢᴇ sᴇɴᴅ sᴋɪᴘᴘᴇᴅ: {error}")
            return None
    return None


async def safe_forward_message(message: Any, chat_id: Any) -> Any:
    """Forward for indexing with bounded FloodWait retry and safe fallback."""
    global _TELEGRAM_WRITE_BLOCK_UNTIL
    target_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0
    for attempt in range(3):
        try:
            await rate_limit_manager.wait_for_write_slot(rpc_name="ForwardMessages", chat_id=target_cid)
            fwd = await message.forward(chat_id)
            rate_limit_manager.record_rpc_success()
            return fwd
        except FloodWait as error:
            wait_for = _flood_wait_seconds(error)
            await _set_write_cooldown(time.time() + min(wait_for, 60))
            if wait_for <= 60 and attempt < 2:
                await pro_floodwait_sleep(wait_for, "safe_forward_message", chat_id=target_cid)
                continue
            logger.warning(f"🌊 ᴛᴇʟᴇɢʀᴀᴍ ғᴏʀᴡᴀʀᴅ ᴄᴏᴏʟᴅᴏᴡᴡɴ ᴀᴄᴛɪᴠᴇ ғᴏʀ {wait_for}s; ɪɴᴅᴇxɪɴɢ ᴏʀɪɢɪɴᴀʟ ᴍsɢ")
            return message
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug(f"⚠️ ᴏᴘᴛɪᴏɴᴀʟ ᴛᴇʟᴇɢʀᴀᴍ ғᴏʀᴡᴀʀᴅ sᴋɪᴘᴘᴇᴅ: {error}")
            return message
    return message


async def safe_delete_messages(client: Any, chat_id: Any, message_ids: Any) -> bool:
    """Delete message batches with bounded FloodWait retry."""
    global _TELEGRAM_WRITE_BLOCK_UNTIL
    target_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0
    for attempt in range(3):
        try:
            await client.delete_messages(chat_id=chat_id, message_ids=message_ids)
            rate_limit_manager.record_rpc_success()
            return True
        except FloodWait as error:
            wait_for = _flood_wait_seconds(error)
            await _set_write_cooldown(time.time() + min(wait_for, 60))
            if wait_for <= 60 and attempt < 2:
                await pro_floodwait_sleep(wait_for, "safe_delete_messages", chat_id=target_cid)
                continue
            logger.warning(f"🌊 ᴛᴇʟᴇɢʀᴀᴍ ᴅᴇʟᴇᴛᴇ ᴄᴏᴏʟᴅᴏᴡɴ ᴀᴄᴛɪᴠᴇ ғᴏʀ {wait_for}s; ᴅᴇғᴇʀʀɪɴɢ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ʙᴀᴛᴄʜ")
            return False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug(f"⚠️ ᴏᴘᴛɪᴏɴᴀʟ ᴛᴇʟᴇɢʀᴀᴍ ᴅᴇʟᴇᴛᴇ sᴋɪᴘᴘᴇᴅ: {error}")
            return False
    return False


DEVIL_MODE_ACTIVE: bool = False


def is_devil_mode_active() -> bool:
    return DEVIL_MODE_ACTIVE


def set_devil_mode(state: bool) -> bool:
    global DEVIL_MODE_ACTIVE
    DEVIL_MODE_ACTIVE = bool(state)
    return DEVIL_MODE_ACTIVE


def get_current_timestamp() -> float:
    return float(time.time())


async def is_admin(*args, **kwargs) -> bool:
    user_id = kwargs.get("user_id")
    if user_id is None and args:
        for arg in args:
            if isinstance(arg, int) and not isinstance(arg, bool):
                user_id = arg
                break
            if isinstance(arg, str) and arg.strip().lstrip("-").isdigit():
                user_id = int(arg.strip())
                break
        if user_id is None:
            user_id = args[-1]
    if not user_id:
        return False
    try:
        uid = int(user_id)
        return uid in Config.ADMINS or str(uid) in Config.ADMINS
    except (ValueError, TypeError):
        return False


async def check_admin(client: Any, message: Any) -> bool:
    user_id = message.from_user.id if message.from_user else 0
    if not await is_admin(client, user_id):
        await safe_reply_text(
            message,
            f"🚫 **{to_smallcaps('access denied!')}**\n\n"
            f"🔒 {to_smallcaps('this command is restricted to bot admins only.')}"
            f"{format_watermark()}"
        )
        return False
    return True


def humanbytes(size: Optional[int]) -> str:
    if not size or size <= 0:
        return "0 B"
    num_size = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_size < 1024.0:
            break
        num_size /= 1024.0
    return f"{num_size:.2f} {unit}"


def time_formatter(milliseconds: int = 0, seconds: Optional[int] = None) -> str:
    if seconds is not None:
        total_seconds = int(seconds)
    elif milliseconds:
        total_seconds = int(milliseconds / 1000)
    else:
        total_seconds = 0

    if total_seconds < 0:
        total_seconds = 0

    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    res = ""
    if days > 0:
        res += f"{days}d "
    if hours > 0:
        res += f"{hours}h "
    if minutes > 0:
        res += f"{minutes}m "
    res += f"{secs}s"
    return res.strip() or "0s"


def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ["http", "https"], result.netloc])
    except Exception:
        return False


def sanitize_filename(name: str) -> str:
    if not name:
        return "file.mp4"
    raw = str(name).replace("\\", "/")
    raw = os.path.basename(raw)
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f\x7f]', "_", raw)
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip(". ")
    if not cleaned:
        return "file.mp4"
    if len(cleaned) > 200:
        base, ext = os.path.splitext(cleaned)
        ext = ext[:20]
        cleaned = f"{base[:max(1, 200 - len(ext))]}{ext}".strip(". ")
    return cleaned or "file.mp4"


def clean_temp_files(*files):
    for f in files:
        if isinstance(f, (list, tuple, set)):
            for sub_f in f:
                clean_temp_files(sub_f)
            continue
        if f and isinstance(f, (str, bytes, os.PathLike)):
            try:
                if os.path.exists(f):
                    if os.path.isdir(f):
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        os.remove(f)
            except Exception:
                pass


_PROGRESS_THROTTLE: Dict[Tuple[int, int, str], float] = {}
_PROGRESS_STEP: Dict[Tuple[int, int, str], int] = {}
_ACTIVE_PROGRESS_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_PROGRESS_TRACKERS: Dict[Tuple[int, int, str], "ProgressTracker"] = {}


def render_progress_bar(percentage: float, width: int = 10) -> str:
    pct = min(100.0, max(0.0, float(percentage or 0.0)))
    filled = min(width, max(0, int(pct / (100.0 / width))))
    return ("▰" * filled) + ("▱" * (width - filled))


class ProgressTracker:
    def __init__(self, total: float = 0.0, initial_processed: float = 0.0, window: int = 8):
        self.start_mono: float = time.monotonic()
        self.last_sample_mono: float = self.start_mono
        self.total: float = max(0.0, float(total or 0.0))
        self.initial_processed: float = max(0.0, float(initial_processed or 0.0))
        self.processed: float = self.initial_processed
        self.samples: deque = deque(maxlen=max(2, int(window)))
        self.last_speed: float = 0.0
        self.last_eta: float = 0.0
        self.last_fps: float = 0.0
        self.last_encoded_bytes: int = 0
        self.last_edit_mono: float = 0.0

    def update(
        self,
        processed: Optional[float] = None,
        total: Optional[float] = None,
        reported_speed: Optional[float] = None,
        fps: Optional[float] = None,
        encoded_bytes: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = time.monotonic()
        if total is not None and float(total) > 0:
            self.total = float(total)
        if processed is not None and float(processed) >= 0:
            new_proc = max(self.initial_processed, float(processed))
            delta_work = new_proc - self.processed
            delta_t = now - self.last_sample_mono
            if delta_work > 0 and delta_t > 0.01:
                self.samples.append((delta_work, delta_t))
                self.last_sample_mono = now
            self.processed = max(self.processed, new_proc)
        if fps is not None and float(fps) > 0:
            self.last_fps = float(fps)
        if encoded_bytes is not None and int(encoded_bytes) > 0:
            self.last_encoded_bytes = int(encoded_bytes)

        elapsed = max(0.001, now - self.start_mono)
        cumulative_work = max(0.0, self.processed - self.initial_processed)
        avg_speed = cumulative_work / elapsed

        if len(self.samples) >= 2:
            win_work = sum(w for w, _ in self.samples)
            win_time = sum(t for _, t in self.samples)
            rolling_speed = (win_work / win_time) if win_time > 0.01 else avg_speed
        elif len(self.samples) == 1:
            w0, t0 = self.samples[0]
            rolling_speed = (w0 / t0) if t0 > 0.01 else avg_speed
        else:
            rolling_speed = avg_speed

        if reported_speed is not None and float(reported_speed) > 0:
            if rolling_speed > 0:
                speed = (0.65 * float(reported_speed)) + (0.35 * rolling_speed)
            else:
                speed = float(reported_speed)
        elif rolling_speed > 0:
            speed = rolling_speed
        else:
            speed = self.last_speed

        if speed > 0:
            self.last_speed = speed

        if self.total > 0:
            pct = min(100.0, max(0.0, (self.processed / self.total) * 100.0))
            remaining = max(0.0, self.total - self.processed)
            if remaining <= 0:
                eta = 0.0
            elif self.last_speed > 0:
                eta = remaining / self.last_speed
            else:
                eta = self.last_eta
            self.last_eta = eta
        else:
            pct = 0.0
            eta = self.last_eta

        return {
            "processed": self.processed,
            "total": self.total,
            "elapsed": elapsed,
            "speed": self.last_speed,
            "eta": self.last_eta,
            "percentage": pct,
            "bar": render_progress_bar(pct, width=10),
            "fps": self.last_fps,
            "encoded_bytes": self.last_encoded_bytes,
            "now_mono": now,
        }

    def dynamic_interval(self, active_jobs: int = 1, is_ffmpeg: bool = False) -> float:
        jobs = max(1, int(active_jobs or 1))
        if is_ffmpeg:
            if self.last_speed >= 2.0:
                base = 1.2
            elif self.last_speed >= 0.8:
                base = 2.0
            elif self.last_speed > 0:
                base = 3.2
            else:
                base = 2.5
        else:
            if self.last_speed >= 3 * 1024 * 1024:
                base = 1.5
            elif self.last_speed >= 512 * 1024:
                base = 2.5
            else:
                base = 3.8
        return min(8.0, max(1.0, base + (jobs - 1) * 0.9))


async def progress_for_pyrogram(
    current: int,
    total: int,
    ud_type: str,
    message: Any,
    start_time: Any = None
):
    if message is None:
        return
    chat_id, msg_id = _extract_msg_identity(message)
    if rate_limit_manager.is_in_cooldown(chat_id):
        return

    if len(_PROGRESS_TRACKERS) > 2000:
        _PROGRESS_TRACKERS.clear()
        _PROGRESS_THROTTLE.clear()
        _PROGRESS_STEP.clear()

    msg_pair = (chat_id, msg_id)
    msg_key = (chat_id, msg_id, str(ud_type))
    is_done = total > 0 and current >= total
    prev_task = _ACTIVE_PROGRESS_TASKS.get(msg_pair)
    if prev_task is not None and not prev_task.done():
        if is_done:
            prev_task.cancel()
        else:
            return

    tracker = _PROGRESS_TRACKERS.get(msg_key)
    if tracker is None:
        initial_proc = 0.0 if (total > 0 and current < total and current < max(1, int(total * 0.05))) else float(max(0, current))
        tracker = ProgressTracker(total=float(total or 0), initial_processed=0.0 if is_done else initial_proc, window=8)
        _PROGRESS_TRACKERS[msg_key] = tracker

    snap = tracker.update(processed=float(current), total=float(total or 0))
    now_mono = snap["now_mono"]
    percentage = 100.0 if is_done else snap["percentage"]
    active_jobs = max(1, task_manager.active_count)
    min_time_gap = tracker.dynamic_interval(active_jobs=active_jobs, is_ffmpeg=False)

    if not is_done:
        if tracker.last_edit_mono > 0 and (now_mono - tracker.last_edit_mono) < min_time_gap:
            return
    else:
        _PROGRESS_TRACKERS.pop(msg_key, None)
        _PROGRESS_THROTTLE.pop(msg_key, None)
        _PROGRESS_STEP.pop(msg_key, None)

    tracker.last_edit_mono = now_mono
    _PROGRESS_THROTTLE[msg_key] = now_mono

    speed = snap["speed"]
    elapsed_sec = int(snap["elapsed"])
    eta_sec = 0 if is_done else int(round(snap["eta"]))

    if total > 0:
        bar = render_progress_bar(percentage, width=10)
        progress = f"[{bar}] `{percentage:.1f}%`\n"
        done_line = f"📦 **{to_smallcaps('done')}** : `{humanbytes(current)} / {humanbytes(total)}`\n"
        eta_disp = time_formatter(seconds=eta_sec) if (eta_sec > 0 or is_done) else "Calculating..."
        eta_line = f"⏳ **{to_smallcaps('eta')}** : `{eta_disp}`\n"
    else:
        progress = "[▰▰▰▰▰▱▱▱▱▱] `sᴛʀᴇᴀᴍɪɴɢ`\n"
        done_line = f"📦 **{to_smallcaps('transferred')}** : `{humanbytes(current)}`\n"
        eta_line = ""

    tmp = (
        f"⚡ **{to_smallcaps(ud_type)}**...\n\n"
        f"{progress}"
        f"🚀 **{to_smallcaps('speed')}** : `{humanbytes(speed)}/s`\n"
        f"{done_line}"
        f"⏱️ **{to_smallcaps('elapsed')}** : `{time_formatter(seconds=elapsed_sec)}`\n"
        f"{eta_line}".rstrip("\n")
        + f"{format_watermark()}"
    )

    async def _do_edit():
        try:
            await safe_edit_text(message, tmp)
        except Exception:
            pass
        finally:
            if _ACTIVE_PROGRESS_TASKS.get(msg_pair) is asyncio.current_task():
                _ACTIVE_PROGRESS_TASKS.pop(msg_pair, None)

    _ACTIVE_PROGRESS_TASKS[msg_pair] = asyncio.create_task(_do_edit())


class MediaTask:
    def __init__(
        self,
        task_id: str,
        user_id: int,
        task_type: str,
        work_dir: str,
        asyncio_task: Optional[asyncio.Task] = None,
        process: Optional[asyncio.subprocess.Process] = None,
        stage: str = "initialized",
        status: str = "running",
        params: Optional[Dict[str, Any]] = None,
    ):
        self.task_id = task_id
        self.user_id = user_id
        self.task_type = task_type
        self.work_dir = work_dir
        self.asyncio_task = asyncio_task
        self.process = process
        self.stage = stage
        self.status = status
        self.params = dict(params or {})
        self.created_at = time.time()
        self.updated_at = time.time()
        self.is_cancelled = False


class TaskManager:
    """
    Fault-tolerant, unlimited-concurrency TaskManager with persistent recovery telemetry.
    Tracks all active media jobs (`compress`, `rename`, `file_pro`, `stream`), subprocesses,
    recovery metrics, and execution reports.
    """
    def __init__(self):
        self._tasks: Dict[str, MediaTask] = {}
        self._background_tasks: set = set()
        self._lock = asyncio.Lock()
        self._recovery_metrics: Dict[str, Any] = {
            "total_detected": 0,
            "completed_tasks": 0,
            "recovered_tasks": 0,
            "failed_tasks": 0,
            "skipped_tasks": 0,
            "total_retries": 0,
            "in_progress_tasks": 0,
            "remaining_errors": [],
            "last_recovery_run_ts": 0.0,
            "status": "ready",
        }

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.is_cancelled and t.status in ("queued", "running", "retrying"))

    def generate_task_id(self, user_id: int, prefix: str = "task") -> str:
        return f"{prefix}_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    def create_workspace(self, user_id: int, task_id: str) -> str:
        work_dir = os.path.abspath(os.path.join(Config.DOWNLOAD_DIR, str(user_id), task_id))
        os.makedirs(work_dir, exist_ok=True)
        return work_dir

    async def register_task(
        self,
        task_id: str,
        user_id: int,
        task_type: str,
        work_dir: str,
        asyncio_task: Optional[asyncio.Task] = None,
        stage: str = "initialized",
        params: Optional[Dict[str, Any]] = None,
    ) -> MediaTask:
        async with self._lock:
            media_task = MediaTask(
                task_id=task_id,
                user_id=user_id,
                task_type=task_type,
                work_dir=work_dir,
                asyncio_task=asyncio_task,
                stage=stage,
                status="running",
                params=params,
            )
            self._tasks[task_id] = media_task
            self._recovery_metrics["in_progress_tasks"] = self.active_count
            return media_task

    def set_subprocess(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        if task_id and task_id in self._tasks:
            self._tasks[task_id].process = process
            self._tasks[task_id].updated_at = time.time()

    def update_stage(self, task_id: str, stage: str, status: str = "running") -> None:
        if task_id and task_id in self._tasks:
            self._tasks[task_id].stage = stage
            self._tasks[task_id].status = status
            self._tasks[task_id].updated_at = time.time()

    def get_task(self, task_id: str) -> Optional[MediaTask]:
        return self._tasks.get(task_id)

    def get_user_tasks(self, user_id: int) -> list:
        return [task for task in self._tasks.values() if task.user_id == user_id]

    def is_user_busy(self, user_id: int) -> bool:
        return any(not task.is_cancelled for task in self._tasks.values() if task.user_id == user_id)

    async def cancel_user_tasks(self, user_id: int) -> int:
        user_tasks = self.get_user_tasks(user_id)
        count = 0
        for t in user_tasks:
            if await self.cancel_task(t.task_id):
                count += 1
        return count

    async def cancel_task(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.is_cancelled = True
            task.status = "cancelled"
            if task.process and task.process.returncode is None:
                try:
                    task.process.kill()
                except Exception:
                    pass
            if task.asyncio_task and not task.asyncio_task.done():
                task.asyncio_task.cancel()
            self._recovery_metrics["in_progress_tasks"] = self.active_count
        try:
            from plugins.core_db import task_repo
            await task_repo.update_task(task_id, {"status": "cancelled", "stage": "cancelled"})
        except Exception:
            pass
        return True

    async def cleanup_task(self, task_id: str, preserve_workspace: bool = False) -> None:
        async with self._lock:
            task = self._tasks.pop(task_id, None)
            self._recovery_metrics["in_progress_tasks"] = self.active_count
        if not preserve_workspace and task and task.work_dir and os.path.exists(task.work_dir):
            try:
                shutil.rmtree(task.work_dir, ignore_errors=True)
            except Exception:
                pass

    def record_metric(
        self,
        detected: int = 0,
        completed: int = 0,
        recovered: int = 0,
        failed: int = 0,
        skipped: int = 0,
        retries: int = 0,
        error: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        self._recovery_metrics["total_detected"] += int(detected)
        self._recovery_metrics["completed_tasks"] += int(completed)
        self._recovery_metrics["recovered_tasks"] += int(recovered)
        self._recovery_metrics["failed_tasks"] += int(failed)
        self._recovery_metrics["skipped_tasks"] += int(skipped)
        self._recovery_metrics["total_retries"] += int(retries)
        self._recovery_metrics["in_progress_tasks"] = self.active_count
        if error:
            errs = self._recovery_metrics["remaining_errors"]
            errs.append(str(error)[:300])
            if len(errs) > 25:
                del errs[:-25]
        if status:
            self._recovery_metrics["status"] = status

    def spawn_background_task(self, coro, name: str = "mmw-user-task") -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error("Background mega task %s error: %s", name, exc)

        task.add_done_callback(_on_done)
        return task

    async def cancel_all_background_tasks(self) -> int:
        pending = [t for t in list(self._background_tasks) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()
        return len(pending)

    def get_recovery_report(self) -> Dict[str, Any]:
        self._recovery_metrics["in_progress_tasks"] = self.active_count
        self._recovery_metrics["background_tasks"] = len(self._background_tasks)
        return dict(self._recovery_metrics)


task_manager = TaskManager()


def spawn_mega_task(coro, name: str = "mmw-user-task") -> asyncio.Task:
    return task_manager.spawn_background_task(coro, name=name)

_max_cfg = int(getattr(Config, "MAX_CONCURRENT_TASKS", 0) or 0)
_ffmpeg_pool = int(getattr(Config, "FFMPEG_PARALLEL_JOBS", 4) or 4)
MEDIA_JOB_SEMAPHORE = asyncio.Semaphore(_max_cfg if _max_cfg > 0 else max(2, _ffmpeg_pool))


def bounded_media_job(func):
    """
    Supports unlimited concurrent queued/active tasks while pacing heavy local FFmpeg/disk
    operations and automatically clearing transient response cache upon completion.
    """
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            async with MEDIA_JOB_SEMAPHORE:
                return await func(*args, **kwargs)
        finally:
            cache.auto_clear_after_response_sync()
    return wrapped


def auto_clear_cache_after(func: Callable):
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _async_wrapped(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            finally:
                cache.auto_clear_after_response_sync(preserve_workflows=True)
        return _async_wrapped
    else:
        @functools.wraps(func)
        def _sync_wrapped(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            finally:
                cache.auto_clear_after_response_sync(preserve_workflows=True)
        return _sync_wrapped
