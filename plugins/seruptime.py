import asyncio
import html
import io
import json
import logging
import math
import os
import re
import time
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import requests
except ImportError:
    requests = None

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from database.ia_filterdb import db as primary_motor_db
except Exception:
    primary_motor_db = None

try:
    from database.users_chats_db import db as users_db
except Exception:
    users_db = None

try:
    from info import ADMINS, LOG_CHANNEL, COMMAND_PREFIXES
except Exception:
    ADMINS = []
    LOG_CHANNEL = 0

try:
    from utils import is_admin_sync, setup_logging, temp
    setup_logging()
except Exception:
    def is_admin_sync(uid: Any) -> bool:
        try:
            return int(uid) in ADMINS or str(uid) in ADMINS
        except Exception:
            return False

    class _FallbackTemp:
        ACTIVE_TASKS: Dict[str, Any] = {}
        BANNED_USERS: List[int] = []

    temp = _FallbackTemp()

logger = logging.getLogger(__name__)

CMD_PREFIXES = COMMAND_PREFIXES if "COMMAND_PREFIXES" in globals() else ["/", "!", "."]
DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 30 * 86400
MAX_MONITORS_PER_USER = 25
WIZARD_TIMEOUT_SECONDS = 60
SITES_PER_PAGE = 6
USERS_PER_PAGE = 6
LOCAL_BACKUP_FILE = ".uptime_monitors.json"
LOCAL_USER_META_FILE = ".uptime_users_meta.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 GokuUptimeMonitor/2.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

PLATFORM_BADGES: List[Tuple[str, str]] = [
    ("koyeb.app", "☁️ Koyeb"),
    ("onrender.com", "⚡ Render"),
    ("vercel.app", "▲ Vercel"),
    ("herokuapp.com", "🟣 Heroku"),
    ("herokudns.com", "🟣 Heroku"),
    ("railway.app", "🚂 Railway"),
    ("fly.dev", "🪁 Fly.io"),
    ("hf.space", "🤗 Hugging Face"),
    ("replit.dev", "🌀 Replit"),
    ("repl.co", "🌀 Replit"),
    ("glitch.me", "🎏 Glitch"),
    ("netlify.app", "💠 Netlify"),
    ("pages.dev", "🟠 Cloudflare"),
    ("workers.dev", "🟠 CF Workers"),
    ("web.app", "🔥 Firebase"),
    ("firebaseapp.com", "🔥 Firebase"),
    ("github.io", "🐙 GitHub Pages"),
    ("gitlab.io", "🦊 GitLab Pages"),
    ("surge.sh", "🌊 Surge"),
    ("vercel.sh", "▲ Vercel"),
    ("netlify.com", "💠 Netlify"),
    ("cloudfront.net", "🟠 AWS CloudFront"),
    ("amplifyapp.com", "🟠 AWS Amplify"),
    ("azurestaticapps.net", "🔷 Azure Static Apps"),
    ("web.core.windows.net", "🔷 Azure Storage"),
    ("up.railway.app", "🚂 Railway"),
    ("northflank.app", "🚢 Northflank"),
    ("code.run", "☁️ Google Cloud Run"),
    ("run.app", "☁️ Google Cloud Run"),
    ("a.run.app", "☁️ Google Cloud Run"),
    ("azurewebsites.net", "🔷 Azure App Service"),
    ("azurecontainerapps.io", "🔷 Azure Containers"),
    ("cloudapp.net", "🔷 Azure Cloud"),
    ("digitaloceanspaces.com", "🌊 DigitalOcean"),
    ("ondigitalocean.app", "🌊 DigitalOcean"),
    ("app.github.dev", "🐙 GitHub Codespaces"),
    ("pythonanywhere.com", "🐍 PythonAnywhere"),
    ("pythonanywhere.net", "🐍 PythonAnywhere"),
    ("streamlit.app", "🎈 Streamlit"),
    ("shinyapps.io", "✨ ShinyApps"),
    ("glide.page", "💚 Glide"),
    ("deno.dev", "🦕 Deno Deploy"),
    ("deno.net", "🦕 Deno"),
    ("workers.dev", "🟠 Cloudflare Workers"),
    ("zeabur.app", "🚀 Zeabur"),
    ("qovery.io", "🚀 Qovery"),
    ("on-fleek.app", "🌐 Fleek"),
    ("fleek.co", "🌐 Fleek"),
    ("back4app.io", "☁️ Back4App"),
    ("cleverapps.io", "☁️ Clever Cloud"),
    ("clever-cloud.com", "☁️ Clever Cloud"),
    ("scaleway.app", "☁️ Scaleway"),
    ("runpod.io", "☁️ RunPod"),
    ("modal.run", "☁️ Modal"),
    ("baseten.co", "☁️ Baseten"),
    ("replicate.com", "🤖 Replicate"),
    ("appwrite.io", "🛠 Appwrite"),
    ("supabase.co", "⚡ Supabase"),
    ("pages.github.io", "🐙 GitHub Pages"),
    ("gitlab.io", "🦊 GitLab Pages"),
    ("bitbucket.io", "🪣 Bitbucket Pages"),
    ("firebaseapp.com", "🔥 Firebase"),
]

INTERVAL_PRESETS: List[Tuple[str, int]] = [
    ("10s", 10),
    ("30s", 30),
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("10m", 600),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
    ("6h", 21600),
    ("12h", 43200),
    ("1d", 86400),
]

QUOTA_PRESETS: List[Tuple[str, int]] = [
    ("5 Sites", 5),
    ("10 Sites", 10),
    ("25 (Default)", 25),
    ("50 Sites", 50),
    ("100 Sites", 100),
    ("♾️ Unlimited", 999999),
]

_DB_LOCK = asyncio.Lock()


def _get_uptime_collection():
    if primary_motor_db is not None:
        return primary_motor_db["uptime_monitors"]
    if users_db is not None and getattr(users_db, "db", None) is not None:
        return users_db.db["uptime_monitors"]
    return None


def _get_uptime_users_collection():
    if primary_motor_db is not None:
        return primary_motor_db["uptime_users_meta"]
    if users_db is not None and getattr(users_db, "db", None) is not None:
        return users_db.db["uptime_users_meta"]
    return None


UPTIME_COL = _get_uptime_collection()
UPTIME_USERS_COL = _get_uptime_users_collection()

MONITORS_STORE: Dict[str, Dict[str, Any]] = {}
USER_META_STORE: Dict[int, Dict[str, Any]] = {}
MONITOR_TASKS: Dict[str, asyncio.Task] = {}
USER_WIZARD: Dict[int, Dict[str, Any]] = {}

_HTTP_SESSION: Any = None
_BOT_CLIENT: Optional[Client] = None
_BOOTSTRAPPED: bool = False
_INDEX_ENSURED: bool = False


def is_authorized_admin(user_id: Optional[int]) -> bool:
    """Strictly verify whether `user_id` is listed in ADMINS."""
    if user_id is None:
        return False
    try:
        uid = int(user_id)
        if uid in ADMINS or str(uid) in ADMINS:
            return True
    except (ValueError, TypeError):
        return False
    return bool(is_admin_sync(user_id))


def detect_platform(url: str) -> str:
    """Detect cloud hosting provider from URL hostname."""
    try:
        host = (urlparse(url).hostname or "").lower()
        for domain, label in PLATFORM_BADGES:
            if host == domain or host.endswith("." + domain):
                return label
    except Exception:
        pass
    return "🌐 Web"


def normalize_and_validate_url(raw_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Normalize URL and block private/loopback SSRF targets."""
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return None, "URL cannot be empty."
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned

    try:
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, "Invalid URL format. Example: <code>https://myapp.koyeb.app/</code>"

        host = (parsed.hostname or "").lower()
        if not host or "." not in host:
            return None, "Invalid domain name in URL."

        blocked_prefixes = (
            "127.", "10.", "192.168.", "169.254.", "0.",
            "172.16.", "172.17.", "172.18.", "172.19.",
            "172.20.", "172.21.", "172.22.", "172.23.",
            "172.24.", "172.25.", "172.26.", "172.27.",
            "172.28.", "172.29.", "172.30.", "172.31.",
        )
        if host in ("localhost", "0.0.0.0", "::1") or host.startswith(blocked_prefixes):
            return None, "Localhost and private network IP addresses are restricted."

        return cleaned, None
    except Exception as e:
        return None, f"Malformed URL: <code>{html.escape(str(e))}</code>"


_DURATION_TOKEN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(days|day|d|hours|hour|hrs|hr|h|minutes|minute|mins|min|m|seconds|second|secs|sec|s)?",
    re.IGNORECASE,
)


def parse_interval(raw_tokens: List[str]) -> Tuple[Optional[int], Optional[str]]:
    """Parse user-supplied interval tokens into seconds."""
    if not raw_tokens:
        return DEFAULT_INTERVAL_SECONDS, None

    joined = " ".join(t.strip() for t in raw_tokens if t.strip()).strip()
    if not joined:
        return DEFAULT_INTERVAL_SECONDS, None

    if re.fullmatch(r"\d+(?:\.\d+)?", joined):
        secs = int(float(joined))
    else:
        matches = list(_DURATION_TOKEN_RE.finditer(joined))
        if not matches:
            return None, (
                "Invalid interval format.\n"
                "Use formats like <code>30s</code>, <code>1m</code>, <code>5m</code>, "
                "<code>1h</code>, <code>1d</code>, or seconds like <code>45</code>."
            )
        reconstructed = "".join(m.group(0) for m in matches).replace(" ", "")
        if reconstructed.lower() != joined.replace(" ", "").lower():
            return None, (
                "Could not parse interval <code>"
                f"{html.escape(joined)}</code>.\n"
                "Examples: <code>30s</code>, <code>2m</code>, <code>1h</code>, <code>1d</code>."
            )

        total_seconds = 0.0
        for m in matches:
            val = float(m.group(1))
            unit = (m.group(2) or "s").lower()
            if unit.startswith("d"):
                total_seconds += val * 86400
            elif unit.startswith("h"):
                total_seconds += val * 3600
            elif unit.startswith("m"):
                total_seconds += val * 60
            else:
                total_seconds += val
        secs = int(total_seconds)

    if secs < MIN_INTERVAL_SECONDS:
        return None, f"Minimum interval is <code>{MIN_INTERVAL_SECONDS}s</code>."
    if secs > MAX_INTERVAL_SECONDS:
        return None, "Maximum interval is <code>30</code> Days."
    return secs, None


def format_interval_readable(seconds: int) -> str:
    """Format seconds into a clean human-readable string (e.g., '1m (60s)' or '5m')."""
    seconds = max(1, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    short = " ".join(parts)
    if seconds >= 60 and len(parts) == 1 and not short.endswith("s"):
        return f"{short} ({seconds}s)"
    return short


def format_timestamp(ts: Optional[float]) -> str:
    if not ts:
        return "None"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "None"


def format_relative_ago(ts: Optional[float]) -> str:
    if not ts:
        return "Never"
    diff = max(0, int(time.time() - float(ts)))
    if diff < 5:
        return "Just now"
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}m {diff % 60}s ago"
    if diff < 86400:
        return f"{diff // 3600}h {(diff % 3600) // 60}m ago"
    return f"{diff // 86400}d ago"


def make_uptime_bar(percent: float, length: int = 10) -> str:
    filled = max(0, min(length, round(length * percent / 100.0)))
    return "▰" * filled + "▱" * (length - filled)


def _save_local_backup() -> None:
    try:
        serializable = {
            mid: {k: v for k, v in doc.items() if k != "_id"}
            for mid, doc in MONITORS_STORE.items()
        }
        with open(LOCAL_BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.debug("Local uptime backup save warning: %s", e)


def _load_local_backup() -> Dict[str, Dict[str, Any]]:
    if not os.path.isfile(LOCAL_BACKUP_FILE):
        return {}
    try:
        with open(LOCAL_BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_local_user_meta() -> None:
    try:
        serializable = {
            str(uid): {k: v for k, v in meta.items() if k != "_id"}
            for uid, meta in USER_META_STORE.items()
        }
        with open(LOCAL_USER_META_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.debug("Local user meta save warning: %s", e)


def _load_local_user_meta() -> Dict[int, Dict[str, Any]]:
    if not os.path.isfile(LOCAL_USER_META_FILE):
        return {}
    try:
        with open(LOCAL_USER_META_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return {int(k): v for k, v in data.items() if str(k).lstrip("-").isdigit() and isinstance(v, dict)}
    except Exception:
        return {}


async def _ensure_indexes() -> None:
    global _INDEX_ENSURED
    if _INDEX_ENSURED:
        return
    try:
        if UPTIME_COL is not None:
            await UPTIME_COL.create_index("monitor_id", unique=True, background=True)
            await UPTIME_COL.create_index("user_id", background=True)
            await UPTIME_COL.create_index("url", background=True)
        if UPTIME_USERS_COL is not None:
            await UPTIME_USERS_COL.create_index("user_id", unique=True, background=True)
        _INDEX_ENSURED = True
    except Exception as e:
        logger.debug("Uptime index creation notice: %s", e)


async def save_monitor_doc(doc: Dict[str, Any]) -> None:
    async with _DB_LOCK:
        mid = doc["monitor_id"]
        clean_doc = {k: v for k, v in doc.items() if k != "_id"}
        MONITORS_STORE[mid] = clean_doc
        _save_local_backup()
        if UPTIME_COL is not None:
            try:
                await _ensure_indexes()
                await UPTIME_COL.update_one(
                    {"monitor_id": mid},
                    {"$set": clean_doc},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("MongoDB uptime save warning for %s: %s", mid, e)


async def delete_monitor_doc(mid: str) -> bool:
    async with _DB_LOCK:
        existed = mid in MONITORS_STORE
        MONITORS_STORE.pop(mid, None)
        _save_local_backup()
        if UPTIME_COL is not None:
            try:
                res = await UPTIME_COL.delete_one({"monitor_id": mid})
                if res and res.deleted_count > 0:
                    existed = True
            except Exception as e:
                logger.warning("MongoDB uptime delete warning for %s: %s", mid, e)
        return existed


async def get_or_create_user_meta(user_id: int, default_name: str = "") -> Dict[str, Any]:
    uid = int(user_id)
    if uid in USER_META_STORE:
        if default_name and not USER_META_STORE[uid].get("name"):
            USER_META_STORE[uid]["name"] = default_name
        return USER_META_STORE[uid]

    meta: Dict[str, Any] = {
        "user_id": uid,
        "name": default_name or f"User {uid}",
        "max_monitors": MAX_MONITORS_PER_USER,
        "uptime_disabled": False,
        "updated_ts": time.time(),
    }
    if UPTIME_USERS_COL is not None:
        try:
            db_doc = await UPTIME_USERS_COL.find_one({"user_id": uid})
            if db_doc:
                meta.update({k: v for k, v in db_doc.items() if k != "_id"})
        except Exception:
            pass
    USER_META_STORE[uid] = meta
    return meta


async def save_user_meta(user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    async with _DB_LOCK:
        uid = int(user_id)
        meta = await get_or_create_user_meta(uid)
        meta.update(updates)
        meta["updated_ts"] = time.time()
        USER_META_STORE[uid] = meta
        _save_local_user_meta()
        if UPTIME_USERS_COL is not None:
            try:
                await _ensure_indexes()
                clean = {k: v for k, v in meta.items() if k != "_id"}
                await UPTIME_USERS_COL.update_one(
                    {"user_id": uid},
                    {"$set": clean},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("MongoDB user meta save warning for %s: %s", uid, e)
        return meta


async def load_all_monitors_from_db() -> List[Dict[str, Any]]:
    """Load monitors and user metadata from MongoDB and merge with local backup."""
    async with _DB_LOCK:
        local_map = _load_local_backup()
        for mid, doc in local_map.items():
            if isinstance(doc, dict) and "monitor_id" in doc:
                MONITORS_STORE[mid] = doc

        local_meta = _load_local_user_meta()
        for uid, mdoc in local_meta.items():
            USER_META_STORE[int(uid)] = mdoc

        if UPTIME_COL is not None:
            try:
                await _ensure_indexes()
                cursor = UPTIME_COL.find({})
                async for doc in cursor:
                    clean = {k: v for k, v in doc.items() if k != "_id"}
                    mid = clean.get("monitor_id")
                    if mid:
                        MONITORS_STORE[mid] = clean
            except Exception as e:
                logger.warning("MongoDB uptime load warning: %s", e)

        if UPTIME_USERS_COL is not None:
            try:
                cursor_u = UPTIME_USERS_COL.find({})
                async for udoc in cursor_u:
                    clean_u = {k: v for k, v in udoc.items() if k != "_id"}
                    uid = clean_u.get("user_id")
                    if uid is not None:
                        USER_META_STORE[int(uid)] = clean_u
            except Exception as e:
                logger.debug("MongoDB user meta load notice: %s", e)

        _save_local_backup()
        _save_local_user_meta()
        return list(MONITORS_STORE.values())


def get_user_monitors(user_id: int, include_all_for_admin: bool = False) -> List[Dict[str, Any]]:
    uid = int(user_id)
    items = [
        doc for doc in MONITORS_STORE.values()
        if doc.get("user_id") == uid or (include_all_for_admin and is_authorized_admin(uid))
    ]
    items.sort(key=lambda d: d.get("created_ts", 0), reverse=True)
    return items


async def get_all_registered_users_merged() -> List[Dict[str, Any]]:
    """Build a unified list of all registered bot users and all monitor owners."""
    merged: Dict[int, Dict[str, Any]] = {}

    if users_db is not None and getattr(users_db, "col", None) is not None:
        try:
            db_users = await users_db.get_all_users()
            for u in db_users:
                uid = u.get("id")
                if uid is None:
                    continue
                uid = int(uid)
                ban_info = u.get("ban_status") or {}
                merged[uid] = {
                    "user_id": uid,
                    "name": u.get("name") or f"User {uid}",
                    "is_banned": bool(ban_info.get("is_banned", False)) or (uid in getattr(temp, "BANNED_USERS", [])),
                    "ban_reason": ban_info.get("ban_reason", ""),
                    "in_users_db": True,
                }
        except Exception as e:
            logger.debug("get_all_registered_users_merged db fetch warning: %s", e)

    for uid, meta in USER_META_STORE.items():
        entry = merged.setdefault(
            int(uid),
            {
                "user_id": int(uid),
                "name": meta.get("name") or f"User {uid}",
                "is_banned": int(uid) in getattr(temp, "BANNED_USERS", []),
                "ban_reason": "",
                "in_users_db": False,
            },
        )
        if meta.get("name") and (not entry.get("name") or entry["name"].startswith("User ")):
            entry["name"] = meta["name"]
        entry["max_monitors"] = int(meta.get("max_monitors", MAX_MONITORS_PER_USER))
        entry["uptime_disabled"] = bool(meta.get("uptime_disabled", False))

    for doc in MONITORS_STORE.values():
        uid = int(doc.get("user_id", 0))
        if not uid:
            continue
        entry = merged.setdefault(
            uid,
            {
                "user_id": uid,
                "name": doc.get("user_name") or f"User {uid}",
                "is_banned": uid in getattr(temp, "BANNED_USERS", []),
                "ban_reason": "",
                "in_users_db": False,
            },
        )
        if doc.get("user_name") and entry["name"].startswith("User "):
            entry["name"] = doc["user_name"]

    result: List[Dict[str, Any]] = []
    for uid, info in merged.items():
        u_mons = [m for m in MONITORS_STORE.values() if int(m.get("user_id", 0)) == uid]
        total_m = len(u_mons)
        up_m = sum(1 for m in u_mons if m.get("enabled", True) and m.get("status") == "UP")
        down_m = sum(1 for m in u_mons if m.get("enabled", True) and m.get("status") == "DOWN")
        paused_m = sum(1 for m in u_mons if not m.get("enabled", True))
        total_checks = sum(int(m.get("total_checks", 0)) for m in u_mons)
        success_checks = sum(int(m.get("success_checks", 0)) for m in u_mons)
        failures = sum(int(m.get("failures", 0)) for m in u_mons)
        uptime_pct = round((success_checks / total_checks) * 100.0, 1) if total_checks > 0 else 100.0
        last_active = max((float(m.get("last_ping_ts") or m.get("created_ts") or 0) for m in u_mons), default=0.0)

        meta = USER_META_STORE.get(uid, {})
        info.update({
            "max_monitors": int(meta.get("max_monitors", info.get("max_monitors", MAX_MONITORS_PER_USER))),
            "uptime_disabled": bool(meta.get("uptime_disabled", info.get("uptime_disabled", False))),
            "total_monitors": total_m,
            "up_monitors": up_m,
            "down_monitors": down_m,
            "paused_monitors": paused_m,
            "total_checks": total_checks,
            "success_checks": success_checks,
            "failures": failures,
            "uptime_pct": uptime_pct,
            "last_active_ts": last_active,
        })
        result.append(info)

    result.sort(key=lambda u: (u["total_monitors"] > 0, u["total_monitors"], u["total_checks"], u["user_id"]), reverse=True)
    return result


async def get_http_session():
    global _HTTP_SESSION
    if aiohttp is None:
        return None
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=8)
        connector = aiohttp.TCPConnector(limit=100, ssl=False, ttl_dns_cache=300)
        _HTTP_SESSION = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers=HEADERS,
        )
    return _HTTP_SESSION


def _sync_ping_fallback(url: str) -> Tuple[bool, Optional[int], str]:
    if requests is not None:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        is_up = 200 <= resp.status_code < 400
        err_str = "" if is_up else f"HTTP {resp.status_code} - {resp.reason or 'Error'}"
        return is_up, resp.status_code, err_str
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        code = getattr(resp, "status", 200)
        is_up = 200 <= code < 400
        return is_up, code, "" if is_up else f"HTTP {code}"


async def ping_url_once(url: str) -> Tuple[bool, Optional[int], float, str]:
    """Perform a fast HTTP GET request to keep the target service awake."""
    start = time.perf_counter()
    if aiohttp is not None:
        session = await get_http_session()
        try:
            async with session.get(url, allow_redirects=True) as resp:
                await resp.content.read(1024)
                latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
                status = resp.status
                reason = resp.reason or "OK"
                is_up = 200 <= status < 400
                err_str = "" if is_up else f"HTTP {status} - {reason}"
                return is_up, status, latency_ms, err_str
        except asyncio.TimeoutError:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
            return False, None, latency_ms, "Request Timeout (15s)"
        except Exception as e:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
            return False, None, latency_ms, f"{type(e).__name__}: {str(e)[:60]}"

    try:
        is_up, status, err_str = await asyncio.to_thread(_sync_ping_fallback, url)
        latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
        return is_up, status, latency_ms, err_str
    except Exception as e:
        latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
        return False, None, latency_ms, f"{type(e).__name__}: {str(e)[:60]}"


async def execute_monitor_check(mid: str) -> Optional[Dict[str, Any]]:
    """Run a single check for monitor `mid`, update metrics, and trigger alerts."""
    doc = MONITORS_STORE.get(mid)
    if not doc:
        return None

    url = doc.get("url", "")
    prev_status = doc.get("status", "UNKNOWN")

    is_up, code, latency_ms, err_str = await ping_url_once(url)
    now_ts = time.time()

    total_checks = int(doc.get("total_checks", 0)) + 1
    success_checks = int(doc.get("success_checks", 0)) + (1 if is_up else 0)
    failures = int(doc.get("failures", 0)) + (0 if is_up else 1)
    consec_fail = 0 if is_up else (int(doc.get("consecutive_failures", 0)) + 1)

    prev_avg = float(doc.get("avg_latency_ms") or latency_ms)
    new_avg = round((prev_avg * 0.75) + (latency_ms * 0.25), 1) if total_checks > 1 else latency_ms

    new_status = "UP" if is_up else "DOWN"
    doc.update({
        "status": new_status,
        "last_status_code": code,
        "last_latency_ms": latency_ms,
        "avg_latency_ms": new_avg,
        "last_ping_ts": now_ts,
        "total_checks": total_checks,
        "success_checks": success_checks,
        "failures": failures,
        "consecutive_failures": consec_fail,
        "last_error": err_str,
    })

    MONITORS_STORE[mid] = doc
    _save_local_backup()
    state_changed = prev_status != new_status
    if state_changed or total_checks <= 3 or total_checks % 5 == 0 or doc.get("interval", 60) >= 30:
        await save_monitor_doc(doc)

    if doc.get("notify", True) and _BOT_CLIENT is not None:
        if new_status == "DOWN" and prev_status in ("UP", "UNKNOWN") and consec_fail >= 1:
            await _send_status_alert(doc, is_recovery=False)
        elif new_status == "UP" and prev_status == "DOWN":
            await _send_status_alert(doc, is_recovery=True)

    return doc


async def _send_status_alert(doc: Dict[str, Any], is_recovery: bool) -> None:
    if _BOT_CLIENT is None:
        return
    uid = doc.get("user_id")
    if not uid:
        return

    name = html.escape(doc.get("name", "Service"))
    url = html.escape(doc.get("url", ""))
    platform = doc.get("platform", "🌐 Web")
    latency = doc.get("last_latency_ms")
    err = html.escape(doc.get("last_error") or "None")
    ts_str = format_timestamp(doc.get("last_ping_ts"))
    mid = doc.get("monitor_id", "")

    if is_recovery:
        text = (
            "🟢 <b>ᴍᴏɴɪᴛᴏʀ ɪs ᴜᴘ (ʀᴇᴄᴏᴠᴇʀᴇᴅ)!</b> ✨\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>ɴᴀᴍᴇ:</b> <code>{name}</code> ({platform})\n"
            f"🌐 <b>ᴜʀʟ:</b> {url}\n"
            f"📡 <b>sᴛᴀᴛᴜs:</b> <code>🟢 UP (HTTP {doc.get('last_status_code') or 200})</code>\n"
            f"⚡ <b>ʟᴀᴛᴇɴᴄʏ:</b> <code>{latency} ms</code>\n"
            f"🕒 <b>ʀᴇᴄᴏᴠᴇʀᴇᴅ ᴀᴛ:</b> <code>{ts_str}</code>"
        )
    else:
        text = (
            "🔴 <b>ᴍᴏɴɪᴛᴏʀ ɪs ᴅᴏᴡɴ — ɪɴᴄɪᴅᴇɴᴛ ᴀʟᴇʀᴛ!</b> ⚠️\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>ɴᴀᴍᴇ:</b> <code>{name}</code> ({platform})\n"
            f"🌐 <b>ᴜʀʟ:</b> {url}\n"
            f"📡 <b>sᴛᴀᴛᴜs:</b> <code>🔴 DOWN</code>\n"
            f"❗ <b>ʀᴏᴏᴛ ᴄᴀᴜsᴇ:</b> <code>{err}</code>\n"
            f"🕒 <b>ɪɴᴄɪᴅᴇɴᴛ ᴛɪᴍᴇ:</b> <code>{ts_str}</code>"
        )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥 ᴠɪᴇᴡ ᴍᴏɴɪᴛᴏʀ", callback_data=f"up_view#{mid}"),
            InlineKeyboardButton("⚡ ᴘɪɴɢ ɴᴏᴡ", callback_data=f"up_ping#{mid}"),
        ]
    ])
    try:
        await _BOT_CLIENT.send_message(
            chat_id=int(uid),
            text=text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.debug("Alert notification skipped for user %s: %s", uid, e)


async def _monitor_loop(mid: str) -> None:
    """Continuous async keep-alive worker for a single monitor."""
    logger.info("🚀 [Uptime] Started worker for monitor %s", mid)
    try:
        while True:
            doc = MONITORS_STORE.get(mid)
            if not doc:
                break

            if not doc.get("enabled", True):
                await asyncio.sleep(5)
                continue

            await execute_monitor_check(mid)

            doc = MONITORS_STORE.get(mid)
            if not doc:
                break
            interval = max(MIN_INTERVAL_SECONDS, int(doc.get("interval", DEFAULT_INTERVAL_SECONDS)))
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("🛑 [Uptime] Worker cancelled for monitor %s", mid)
        raise
    except Exception as e:
        logger.error("💥 [Uptime] Worker error for %s: %s", mid, e)
    finally:
        MONITOR_TASKS.pop(mid, None)
        if hasattr(temp, "ACTIVE_TASKS"):
            temp.ACTIVE_TASKS.pop(f"uptime_{mid}", None)


def start_monitor_worker(mid: str) -> None:
    """Start or restart the background asyncio task for monitor `mid`."""
    stop_monitor_worker(mid)
    doc = MONITORS_STORE.get(mid)
    if not doc or not doc.get("enabled", True):
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_monitor_loop(mid), name=f"uptime_{mid}")
        MONITOR_TASKS[mid] = task
        if hasattr(temp, "ACTIVE_TASKS"):
            temp.ACTIVE_TASKS[f"uptime_{mid}"] = task
    except RuntimeError:
        pass


def stop_monitor_worker(mid: str) -> None:
    """Cancel and clean up the background asyncio task for monitor `mid`."""
    task = MONITOR_TASKS.pop(mid, None)
    if hasattr(temp, "ACTIVE_TASKS"):
        temp.ACTIVE_TASKS.pop(f"uptime_{mid}", None)
    if task and not task.done():
        task.cancel()


async def resume_uptime_monitors(client: Optional[Client] = None) -> int:
    """Load all persisted monitors from MongoDB/disk and start their background loops."""
    global _BOT_CLIENT, _BOOTSTRAPPED
    if client is not None:
        _BOT_CLIENT = client
    _BOOTSTRAPPED = True

    docs = await load_all_monitors_from_db()
    started = 0
    for doc in docs:
        mid = doc.get("monitor_id")
        if mid and doc.get("enabled", True) and mid not in MONITOR_TASKS:
            start_monitor_worker(mid)
            started += 1
    if started:
        logger.info("✅ [Uptime] Resumed %d active uptime monitor(s)", started)
    return started


async def _ensure_engine_ready(client: Optional[Client] = None) -> None:
    global _BOT_CLIENT
    if client is not None:
        _BOT_CLIENT = client
    if not _BOOTSTRAPPED:
        await resume_uptime_monitors(client)


try:
    _running_loop = asyncio.get_running_loop()
    _running_loop.call_later(3.0, lambda: asyncio.ensure_future(resume_uptime_monitors()))
except RuntimeError:
    pass


def _status_badge(doc: Dict[str, Any]) -> str:
    if not doc.get("enabled", True):
        return "⏸️ PAUSED"
    st = doc.get("status", "UNKNOWN")
    code = doc.get("last_status_code")
    if st == "UP":
        return f"🟢 UP (HTTP {code})" if code else "🟢 UP"
    if st == "DOWN":
        return f"🔴 DOWN (HTTP {code})" if code else "🔴 DOWN"
    return "🟡 UNKNOWN"


def _status_icon(doc: Dict[str, Any]) -> str:
    if not doc.get("enabled", True):
        return "⏸️"
    st = doc.get("status", "UNKNOWN")
    if st == "UP":
        return "🟢"
    if st == "DOWN":
        return "🔴"
    return "🟡"


def format_monitor_details_text(doc: Dict[str, Any], show_owner: bool = False) -> str:
    """Format comprehensive Service Monitor Details card."""
    name = html.escape(str(doc.get("name", "Unnamed")))
    url = html.escape(str(doc.get("url", "")))
    platform = doc.get("platform") or detect_platform(doc.get("url", ""))
    status_str = _status_badge(doc)
    interval_sec = int(doc.get("interval", DEFAULT_INTERVAL_SECONDS))
    interval_str = format_interval_readable(interval_sec)

    latency = doc.get("last_latency_ms")
    avg_lat = doc.get("avg_latency_ms")
    latency_str = f"{latency}ms (ᴀᴠɢ: {avg_lat}ms)" if latency is not None else "Nonems"

    last_ping_ts = doc.get("last_ping_ts")
    last_ping_str = (
        f"{format_timestamp(last_ping_ts)} ({format_relative_ago(last_ping_ts)})"
        if last_ping_ts else "None"
    )

    total_checks = int(doc.get("total_checks", 0))
    success_checks = int(doc.get("success_checks", 0))
    failures = int(doc.get("failures", 0))
    uptime_pct = round((success_checks / total_checks) * 100.0, 1) if total_checks > 0 else 100.0
    bar = make_uptime_bar(uptime_pct)

    added_str = doc.get("added_str") or format_timestamp(doc.get("created_ts"))
    notify_str = "🔔 ᴏɴ" if doc.get("notify", True) else "🔕 ᴏғғ"
    last_err = doc.get("last_error")

    lines = [
        "🖥 <b>Service Monitor Details</b>",
        "",
        f"┏🆔 <b>Monitor ID:</b> <code>{html.escape(str(doc.get('monitor_id', '')))}</code>",
    ]
    if show_owner:
        owner_id = doc.get("user_id", 0)
        owner_name = html.escape(str(doc.get("user_name") or USER_META_STORE.get(int(owner_id), {}).get("name") or f"User {owner_id}"))
        lines.append(f"┣👤 <b>Owner:</b> <a href='tg://user?id={owner_id}'>{owner_name}</a> (<code>{owner_id}</code>)")

    lines.extend([
        f"┣🏷 <b>Name:</b> <code>{name}</code>",
        f"┣🌐 <b>URL:</b> {url}",
        f"┣☁️ <b>Platform:</b> <code>{platform}</code>",
        f"┣📡 <b>Status:</b> {status_str}",
        f"┣⏱️ <b>Interval:</b> <code>{interval_str}</code>",
        f"┣⚡ <b>Latency:</b> <code>{latency_str}</code>",
        f"┣📊 <b>Uptime:</b> <code>{uptime_pct}%</code> [{bar}]",
        f"┣🕒 <b>Last Ping:</b> <code>{last_ping_str}</code>",
        f"┣🔄 <b>Total Checks:</b> <code>{total_checks}</code>",
        f"┣⚠️ <b>Failures:</b> <code>{failures}</code>",
        f"┣🔔 <b>Alerts:</b> <code>{notify_str}</code>",
    ])
    if last_err and doc.get("status") == "DOWN":
        lines.append(f"┣❗ <b>Root Cause:</b> <code>{html.escape(str(last_err))}</code>")
    lines.append(f"┗📅 <b>Added:</b> <code>{added_str}</code>")
    return "\n".join(lines)


def build_monitor_detail_keyboard(
    doc: Dict[str, Any],
    page: int = 0,
    admin_origin: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Build monitor management buttons."""
    mid = doc["monitor_id"]
    enabled = doc.get("enabled", True)
    notify = doc.get("notify", True)
    ctx = admin_origin or "user"

    toggle_btn = (
        InlineKeyboardButton("⏸️ ᴘᴀᴜsᴇ", callback_data=f"up_toggle#{mid}#{page}#{ctx}")
        if enabled
        else InlineKeyboardButton("▶️ ʀᴇsᴜᴍᴇ", callback_data=f"up_toggle#{mid}#{page}#{ctx}")
    )
    notify_btn = (
        InlineKeyboardButton("🔔 ᴀʟᴇʀᴛs: ᴏɴ", callback_data=f"up_notify#{mid}#{page}#{ctx}")
        if notify
        else InlineKeyboardButton("🔕 ᴀʟᴇʀᴛs: ᴏғғ", callback_data=f"up_notify#{mid}#{page}#{ctx}")
    )

    rows = [
        [
            InlineKeyboardButton("⚡ ᴘɪɴɢ ɴᴏᴡ", callback_data=f"up_ping#{mid}#{page}#{ctx}"),
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"up_view#{mid}#{page}#{ctx}"),
        ],
        [
            InlineKeyboardButton("✏️ ʀᴇɴᴀᴍᴇ", callback_data=f"up_edit_name#{mid}#{page}#{ctx}"),
            InlineKeyboardButton("🔗 ᴄʜᴀɴɢᴇ ʟɪɴᴋ", callback_data=f"up_edit_url#{mid}#{page}#{ctx}"),
        ],
        [
            InlineKeyboardButton("⏱️ ᴇᴅɪᴛ ɪɴᴛᴇʀᴠᴀʟ", callback_data=f"up_edit_int#{mid}#{page}#{ctx}"),
            toggle_btn,
        ],
        [
            notify_btn,
            InlineKeyboardButton("📥 .ᴊsᴏɴ", callback_data=f"up_json#{mid}"),
        ],
    ]

    if admin_origin:
        owner_id = doc.get("user_id", 0)
        rows.append([
            InlineKeyboardButton("👤 ɪɴsᴘᴇᴄᴛ ᴏᴡɴᴇʀ", callback_data=f"upad_uview#{owner_id}#0"),
            InlineKeyboardButton("🗑️ ᴅᴇʟᴇᴛᴇ", callback_data=f"up_del_ask#{mid}#{page}#{ctx}"),
        ])
        if admin_origin.startswith("umons_"):
            uid_back = admin_origin.split("_", 1)[1]
            back_cb = f"upad_umons#{uid_back}#{page}"
            back_lbl = "🔙 ᴜsᴇʀ ᴍᴏɴɪᴛᴏʀs"
        else:
            back_cb = f"upad_mons#{page}#all"
            back_lbl = "🔙 ᴀʟʟ ᴍᴏɴɪᴛᴏʀs"
        rows.append([
            InlineKeyboardButton(back_lbl, callback_data=back_cb),
            InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🗑️ ʀᴇᴍᴏᴠᴇ / ʀᴇᴠᴏᴋᴇ", callback_data=f"up_del_ask#{mid}#{page}#user"),
        ])
        rows.append([
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ʟɪsᴛ", callback_data=f"up_list#{page}"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
        ])

    return InlineKeyboardMarkup(rows)


def build_interval_picker_keyboard(mid: str, page: int = 0, ctx: str = "user") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    current_row: List[InlineKeyboardButton] = []
    for label, secs in INTERVAL_PRESETS:
        current_row.append(
            InlineKeyboardButton(f"⏱️ {label}", callback_data=f"up_set_int#{mid}#{secs}#{page}#{ctx}")
        )
        if len(current_row) == 4:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([
        InlineKeyboardButton("⌨️ ᴄᴜsᴛᴏᴍ ɪɴᴛᴇʀᴠᴀʟ (s/ᴍ/ʜ/ᴅ)", callback_data=f"up_custom_int#{mid}#{page}#{ctx}")
    ])
    rows.append([
        InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍᴏɴɪᴛᴏʀ", callback_data=f"up_view#{mid}#{page}#{ctx}"),
        InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
    ])
    return InlineKeyboardMarkup(rows)


def build_listup_view(user_id: int, page: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    monitors = get_user_monitors(user_id)
    total = len(monitors)

    if total == 0:
        text = (
            "🌐 <b>ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ ᴅᴀsʜʙᴏᴀʀᴅ</b> ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📭 <b>ɴᴏ ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀs ᴀᴅᴅᴇᴅ ʏᴇᴛ!</b>\n\n"
            "💡 <b>ǫᴜɪᴄᴋ ᴀᴅᴅ ᴄᴏᴍᴍᴀɴᴅs:</b>\n"
            "• <code>/uptime name https://app.koyeb.app</code> <i>(ᴅᴇғᴀᴜʟᴛ 1ᴍ)</i>\n"
            "• <code>/uptime name https://app.onrender.com 30s</code>\n"
            "• <code>/uptime name https://app.vercel.app 5m</code>\n"
            "• <code>/uptime name https://app.herokuapp.com 1h</code>\n\n"
            "👇 <i>ᴏʀ ᴛᴀᴘ <b>➕ Add Server URL</b> ʙᴇʟᴏᴡ:</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ ᴀᴅᴅ sᴇʀᴠᴇʀ ᴜʀʟ ➕", callback_data="up_add_prompt")],
            [
                InlineKeyboardButton("❓ ʜᴇʟᴘ & ɢᴜɪᴅᴇ", callback_data="up_help"),
                InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
                InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
            ],
        ])
        return text, markup

    up_cnt = sum(1 for m in monitors if m.get("enabled", True) and m.get("status") == "UP")
    down_cnt = sum(1 for m in monitors if m.get("enabled", True) and m.get("status") == "DOWN")
    paused_cnt = sum(1 for m in monitors if not m.get("enabled", True))

    total_pages = max(1, math.ceil(total / SITES_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * SITES_PER_PAGE
    page_items = monitors[start_idx : start_idx + SITES_PER_PAGE]

    lines = [
        "🌐 <b>ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ ᴅᴀsʜʙᴏᴀʀᴅ</b> ⚡",
        "━━━━━━━━━━━━━━━━━━━━━━",
        (
            f"📊 <b>ᴛᴏᴛᴀʟ:</b> <code>{total}</code> │ "
            f"🟢 <b>ᴜᴘ:</b> <code>{up_cnt}</code> │ "
            f"🔴 <b>ᴅᴏᴡɴ:</b> <code>{down_cnt}</code> │ "
            f"⏸️ <b>ᴘᴀᴜsᴇᴅ:</b> <code>{paused_cnt}</code>"
        ),
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    buttons: List[List[InlineKeyboardButton]] = []
    for idx, doc in enumerate(page_items, start=start_idx + 1):
        mid = doc["monitor_id"]
        icon = _status_icon(doc)
        name = doc.get("name", "Site")
        platform = doc.get("platform") or detect_platform(doc.get("url", ""))
        interval_str = format_interval_readable(int(doc.get("interval", 60)))
        lat = doc.get("last_latency_ms")
        lat_str = f"{lat:.0f}ms" if isinstance(lat, (int, float)) else "—"
        url_disp = html.escape(doc.get("url", ""))

        lines.append(
            f"<b>{idx}.</b> {icon} <b>{html.escape(name)}</b> ({platform})\n"
            f"   ├ 🔗 {url_disp}\n"
            f"   └ ⏱️ <code>{interval_str}</code> │ ⚡ <code>{lat_str}</code> │ 🔄 <code>{doc.get('total_checks', 0)}</code>"
        )

        btn_label = f"{icon} {name} • {interval_str} ({lat_str})"
        buttons.append([
            InlineKeyboardButton(btn_label[:60], callback_data=f"up_view#{mid}#{page}#user"),
            InlineKeyboardButton("⚙️", callback_data=f"up_edit_int#{mid}#{page}#user"),
            InlineKeyboardButton("🗑️", callback_data=f"up_del_ask#{mid}#{page}#user"),
        ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"📄 <b>ᴘᴀɢᴇ:</b> <code>{page + 1}/{total_pages}</code> • "
        "<i>ᴛᴀᴘ ᴀ sɪᴛᴇ ʙᴇʟᴏᴡ ᴛᴏ ᴇᴅɪᴛ, ʀᴇɴᴀᴍᴇ, ᴄʜᴀɴɢᴇ ʟɪɴᴋ, ᴏʀ ʀᴇᴍᴏᴠᴇ:</i>"
    )

    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"up_list#{page - 1}"))
    nav_row.append(InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"up_list#{page}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"up_list#{page + 1}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("➕ ᴀᴅᴅ sᴇʀᴠᴇʀ ᴜʀʟ", callback_data="up_add_prompt"),
        InlineKeyboardButton("⚡ ᴘɪɴɢ ᴀʟʟ ɴᴏᴡ", callback_data=f"up_ping_all#{page}"),
    ])
    buttons.append([
        InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ .ᴊsᴏɴ", callback_data="up_export_all"),
        InlineKeyboardButton("🗑️ ᴄʟᴇᴀʀ ᴀʟʟ", callback_data="up_clear_ask"),
    ])
    buttons.append([
        InlineKeyboardButton("❓ ʜᴇʟᴘ", callback_data="up_help"),
        InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
        InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def build_help_text() -> str:
    return (
        "⚡ <b>ғᴀsᴛ ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ — ᴘʀᴏ ɢᴜɪᴅᴇ</b> 🌐\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Keep your <b>Koyeb, Render, Vercel, Heroku, Railway, Fly.io, HF Space</b> "
        "and custom web services awake 24/7 with customizable ping intervals.\n\n"
        "📌 <b>1. ᴀᴅᴅ ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ (<code>/uptime</code>):</b>\n"
        "• <code>/uptime name url</code>\n"
        "  ↳ <i>Default interval: 1 minute (60s)</i>\n"
        "• <code>/uptime name url x</code>\n"
        "  ↳ <i>Custom interval in seconds, minutes, hours, or days:</i>\n"
        "  • Seconds: <code>/uptime goku https://app.koyeb.app 30s</code> (or <code>30</code>)\n"
        "  • Minutes: <code>/uptime api https://app.onrender.com 5m</code>\n"
        "  • Hours: <code>/uptime web https://app.vercel.app 2h</code>\n"
        "  • Days: <code>/uptime worker https://app.herokuapp.com 1d</code>\n"
        "  • Pipe format: <code>/uptime name|https://app.koyeb.app|30s</code>\n\n"
        "🗑️ <b>2. ʀᴇᴠᴏᴋᴇ / ᴅᴇʟᴇᴛᴇ ᴍᴏɴɪᴛᴏʀ (<code>/revoke</code>):</b>\n"
        "• <code>/revoke name</code> — Delete by monitor name\n"
        "• <code>/revoke https://app.koyeb.app</code> — Delete by URL\n"
        "• <code>/revoke</code> — Interactive revoke selector\n\n"
        "📋 <b>3. ᴍᴀɴᴀɢᴇ & ᴇᴅɪᴛ sɪᴛᴇs (<code>/listup</code>):</b>\n"
        "• View live status, latency (ms), uptime %, total checks & failures\n"
        "• <b>✏️ Rename</b>, <b>🔗 Change Link</b>, <b>⏱️ Edit Interval</b>\n"
        "• <b>⏸️ Pause / Resume</b>, <b>🔔 Toggle Down/Up Alerts</b>, <b>📥 Export JSON</b>\n\n"
        "👑 <b>4. ᴀᴅᴍɪɴ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ (<code>/upad</code>):</b>\n"
        "• Strictly for <code>ADMINS</code> — full user & global uptime management."
    )


def make_monitor_json_bytes(doc_or_list: Any, filename: str = "monitor.json") -> io.BytesIO:
    clean = (
        [{k: v for k, v in d.items() if k != "_id"} for d in doc_or_list]
        if isinstance(doc_or_list, list)
        else {k: v for k, v in doc_or_list.items() if k != "_id"}
    )
    raw = json.dumps(clean, indent=2, ensure_ascii=False).encode("utf-8")
    bio = io.BytesIO(raw)
    bio.name = filename
    return bio


async def create_and_start_monitor(
    user_id: int,
    name: str,
    url: str,
    interval_sec: int,
    user_name: str = "",
    bypass_quota: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate, create, ping immediately, and start background worker for a new monitor."""
    uid = int(user_id)
    meta = await get_or_create_user_meta(uid, default_name=user_name)

    if not bypass_quota and not is_authorized_admin(uid):
        if meta.get("uptime_disabled", False) or uid in getattr(temp, "BANNED_USERS", []):
            return None, "⛔ Your uptime monitoring access has been restricted by an administrator."

    norm_url, url_err = normalize_and_validate_url(url)
    if url_err or not norm_url:
        return None, url_err

    clean_name = (name or "").strip()[:40]
    if not clean_name:
        parsed_host = urlparse(norm_url).hostname or "service"
        clean_name = parsed_host.split(".")[0][:30]

    if user_name:
        await save_user_meta(uid, {"name": user_name})

    user_monitors = get_user_monitors(uid)
    user_limit = int(meta.get("max_monitors", MAX_MONITORS_PER_USER))
    if not bypass_quota and not is_authorized_admin(uid) and len(user_monitors) >= user_limit:
        return None, f"You have reached your maximum limit of <code>{user_limit}</code> monitors."

    for existing in user_monitors:
        if existing.get("name", "").lower() == clean_name.lower() or existing.get("url", "").rstrip("/") == norm_url.rstrip("/"):
            mid = existing["monitor_id"]
            existing["name"] = clean_name
            existing["url"] = norm_url
            existing["platform"] = detect_platform(norm_url)
            existing["interval"] = interval_sec
            existing["enabled"] = True
            if user_name:
                existing["user_name"] = user_name
            await save_monitor_doc(existing)
            await execute_monitor_check(mid)
            start_monitor_worker(mid)
            return MONITORS_STORE.get(mid, existing), None

    mid = uuid.uuid4().hex[:8].upper()
    now_ts = time.time()
    doc: Dict[str, Any] = {
        "monitor_id": mid,
        "user_id": uid,
        "user_name": user_name or meta.get("name", f"User {uid}"),
        "name": clean_name,
        "url": norm_url,
        "platform": detect_platform(norm_url),
        "interval": int(interval_sec),
        "status": "UNKNOWN",
        "enabled": True,
        "notify": True,
        "last_status_code": None,
        "last_latency_ms": None,
        "avg_latency_ms": None,
        "last_ping_ts": None,
        "total_checks": 0,
        "success_checks": 0,
        "failures": 0,
        "consecutive_failures": 0,
        "last_error": "",
        "created_ts": now_ts,
        "added_str": format_timestamp(now_ts),
    }

    await save_monitor_doc(doc)
    await execute_monitor_check(mid)
    start_monitor_worker(mid)
    return MONITORS_STORE.get(mid, doc), None


async def build_upad_dashboard_view() -> Tuple[str, InlineKeyboardMarkup]:
    """Build the main `/upad` Admin Control Panel overview."""
    all_users = await get_all_registered_users_merged()
    all_mons = list(MONITORS_STORE.values())

    total_users = len(all_users)
    active_owners = sum(1 for u in all_users if u["total_monitors"] > 0)
    banned_cnt = sum(1 for u in all_users if u.get("is_banned") or u.get("uptime_disabled"))

    total_mons = len(all_mons)
    up_mons = sum(1 for m in all_mons if m.get("enabled", True) and m.get("status") == "UP")
    down_mons = sum(1 for m in all_mons if m.get("enabled", True) and m.get("status") == "DOWN")
    paused_mons = sum(1 for m in all_mons if not m.get("enabled", True))
    active_workers = len(MONITOR_TASKS)

    total_checks = sum(int(m.get("total_checks", 0)) for m in all_mons)
    total_success = sum(int(m.get("success_checks", 0)) for m in all_mons)
    total_failures = sum(int(m.get("failures", 0)) for m in all_mons)
    global_uptime = round((total_success / total_checks) * 100.0, 2) if total_checks > 0 else 100.0

    latencies = [float(m["last_latency_ms"]) for m in all_mons if isinstance(m.get("last_latency_ms"), (int, float))]
    avg_cluster_lat = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

    text = (
        "👑 <b>ᴜᴘᴛɪᴍᴇ ᴀᴅᴍɪɴ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ (<code>/upad</code>)</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>ᴜsᴇʀs & ᴀᴄᴄᴇss ᴏᴠᴇʀᴠɪᴇᴡ:</b>\n"
        f"├ 👤 <b>ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀs:</b> <code>{total_users:,}</code>\n"
        f"├ 🌐 <b>ᴍᴏɴɪᴛᴏʀ ᴏᴡɴᴇʀs:</b> <code>{active_owners:,}</code>\n"
        f"└ 🚫 <b>ʙᴀɴɴᴇᴅ / ʀᴇsᴛʀɪᴄᴛᴇᴅ:</b> <code>{banned_cnt:,}</code>\n\n"
        "📡 <b>ɢʟᴏʙᴀʟ ᴜᴘᴛɪᴍᴇ ᴛᴇʟᴇᴍᴇᴛʀʏ:</b>\n"
        f"├ 📊 <b>ᴛᴏᴛᴀʟ ᴍᴏɴɪᴛᴏʀs:</b> <code>{total_mons:,}</code> (⚙️ ᴡᴏʀᴋᴇʀs: <code>{active_workers}</code>)\n"
        f"├ 🟢 <b>ᴏɴʟɪɴᴇ (ᴜᴘ):</b> <code>{up_mons}</code> │ 🔴 <b>ᴅᴏᴡɴ:</b> <code>{down_mons}</code> │ ⏸️ <b>ᴘᴀᴜsᴇᴅ:</b> <code>{paused_mons}</code>\n"
        f"├ 📈 <b>ɢʟᴏʙᴀʟ ᴜᴘᴛɪᴍᴇ:</b> <code>{global_uptime}%</code> [{make_uptime_bar(global_uptime)}]\n"
        f"├ ⚡ <b>ᴀᴠɢ ʟᴀᴛᴇɴᴄʏ:</b> <code>{avg_cluster_lat} ms</code>\n"
        f"└ 🔄 <b>ᴄʜᴇᴄᴋs / ғᴀɪʟs:</b> <code>{total_checks:,}</code> / <code>{total_failures:,}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <i>Select an administrative control module below:</i>"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👥 ᴀʟʟ ᴜsᴇʀs ({total_users})", callback_data="upad_users#0#all"),
            InlineKeyboardButton(f"🌐 ᴀʟʟ ᴍᴏɴɪᴛᴏʀs ({total_mons})", callback_data="upad_mons#0#all"),
        ],
        [
            InlineKeyboardButton(f"🟢 ᴜᴘ ({up_mons})", callback_data="upad_mons#0#up"),
            InlineKeyboardButton(f"🔴 ᴅᴏᴡɴ ({down_mons})", callback_data="upad_mons#0#down"),
            InlineKeyboardButton(f"⏸️ ᴘᴀᴜsᴇᴅ ({paused_mons})", callback_data="upad_mons#0#paused"),
        ],
        [
            InlineKeyboardButton("🔍 sᴇᴀʀᴄʜ ᴜsᴇʀ / sɪᴛᴇ", callback_data="upad_search_prompt"),
            InlineKeyboardButton("⚡ ᴘɪɴɢ ᴀʟʟ ɢʟᴏʙᴀʟ", callback_data="upad_ping_global"),
        ],
        [
            InlineKeyboardButton("⏸️ ᴘᴀᴜsᴇ ᴀʟʟ", callback_data="upad_pause_all"),
            InlineKeyboardButton("▶️ ʀᴇsᴜᴍᴇ ᴀʟʟ", callback_data="upad_resume_all"),
        ],
        [
            InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ɢʟᴏʙᴀʟ .ᴊsᴏɴ", callback_data="upad_export_global"),
            InlineKeyboardButton("🧹 ᴘʀᴜɴᴇ / ᴄʟᴇᴀɴᴜᴘ", callback_data="upad_prune_menu"),
        ],
        [
            InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", callback_data="adm_panel#home"),
            InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data="upad_home"),
        ],
        [
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
            InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
        ],
    ])
    return text, markup


async def build_upad_users_list_view(page: int = 0, flt: str = "all") -> Tuple[str, InlineKeyboardMarkup]:
    """Build paginated list of registered users with filter tabs."""
    all_users = await get_all_registered_users_merged()
    if flt == "active":
        filtered = [u for u in all_users if u["total_monitors"] > 0]
        flt_title = "Users With Monitors"
    elif flt == "banned":
        filtered = [u for u in all_users if u.get("is_banned") or u.get("uptime_disabled")]
        flt_title = "Banned / Restricted Users"
    else:
        filtered = all_users
        flt_title = "All Registered Users"
        flt = "all"

    total = len(filtered)
    total_pages = max(1, math.ceil(total / USERS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * USERS_PER_PAGE
    page_items = filtered[start_idx : start_idx + USERS_PER_PAGE]

    lines = [
        f"👥 <b>ᴀᴅᴍɪɴ ᴜsᴇʀ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ — {flt_title}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>ᴍᴀᴛᴄʜɪɴɢ ᴜsᴇʀs:</b> <code>{total:,}</code> │ 📄 <b>ᴘᴀɢᴇ:</b> <code>{page + 1}/{total_pages}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    buttons: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🔘 ᴀʟʟ" if flt == "all" else "ᴀʟʟ", callback_data="upad_users#0#all"),
            InlineKeyboardButton("🔘 ᴡɪᴛʜ sɪᴛᴇs" if flt == "active" else "ᴡɪᴛʜ sɪᴛᴇs", callback_data="upad_users#0#active"),
            InlineKeyboardButton("🔘 ʀᴇsᴛʀɪᴄᴛᴇᴅ" if flt == "banned" else "ʀᴇsᴛʀɪᴄᴛᴇᴅ", callback_data="upad_users#0#banned"),
        ]
    ]

    if not page_items:
        lines.append("\n📭 <i>No users match this filter.</i>")
    else:
        for idx, u in enumerate(page_items, start=start_idx + 1):
            uid = u["user_id"]
            uname = html.escape(str(u.get("name") or f"User {uid}"))
            status_ico = "🚫" if (u.get("is_banned") or u.get("uptime_disabled")) else "👤"
            tot_m = u["total_monitors"]
            up_m = u["up_monitors"]
            down_m = u["down_monitors"]
            up_pct = u["uptime_pct"]
            last_act = format_relative_ago(u.get("last_active_ts")) if u.get("last_active_ts") else "—"

            lines.append(
                f"<b>{idx}.</b> {status_ico} <a href='tg://user?id={uid}'>{uname}</a> (<code>{uid}</code>)\n"
                f"   └ 🌐 Sites: <code>{tot_m}</code> (🟢<code>{up_m}</code> 🔴<code>{down_m}</code>) │ "
                f"📈 <code>{up_pct}%</code> │ 🕒 <code>{last_act}</code>"
            )
            btn_lbl = f"{status_ico} {u.get('name', uid)} [{uid}] • {tot_m} sites ({up_pct}%)"
            buttons.append([
                InlineKeyboardButton(btn_lbl[:62], callback_data=f"upad_uview#{uid}#{page}")
            ])

    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"upad_users#{page - 1}#{flt}"))
    nav_row.append(InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"upad_users#{page}#{flt}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"upad_users#{page + 1}#{flt}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🔍 sᴇᴀʀᴄʜ ᴜsᴇʀ", callback_data="upad_search_prompt"),
        InlineKeyboardButton("🔙 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def build_upad_user_detail_view(target_uid: int, back_page: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    """Build detailed Admin User Inspector with full permission controls."""
    uid = int(target_uid)
    all_users = await get_all_registered_users_merged()
    u_info = next((u for u in all_users if u["user_id"] == uid), None)

    if u_info is None:
        meta = await get_or_create_user_meta(uid)
        u_info = {
            "user_id": uid,
            "name": meta.get("name", f"User {uid}"),
            "is_banned": uid in getattr(temp, "BANNED_USERS", []),
            "ban_reason": "",
            "max_monitors": meta.get("max_monitors", MAX_MONITORS_PER_USER),
            "uptime_disabled": meta.get("uptime_disabled", False),
            "total_monitors": 0,
            "up_monitors": 0,
            "down_monitors": 0,
            "paused_monitors": 0,
            "total_checks": 0,
            "failures": 0,
            "uptime_pct": 100.0,
            "last_active_ts": 0.0,
        }

    uname = html.escape(str(u_info.get("name") or f"User {uid}"))
    is_banned = bool(u_info.get("is_banned") or u_info.get("uptime_disabled"))
    access_str = "🚫 ʀᴇsᴛʀɪᴄᴛᴇᴅ / ʙᴀɴɴᴇᴅ" if is_banned else "🟢 ᴀᴄᴛɪᴠᴇ & ᴀʟʟᴏᴡᴇᴅ"
    role_str = "👑 ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ" if is_authorized_admin(uid) else "👤 ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀ"
    quota_val = int(u_info.get("max_monitors", MAX_MONITORS_PER_USER))
    quota_str = "♾️ ᴜɴʟɪᴍɪᴛᴇᴅ" if quota_val >= 99999 else f"{quota_val} sɪᴛᴇs"
    last_act = format_timestamp(u_info.get("last_active_ts")) if u_info.get("last_active_ts") else "Never"

    lines = [
        "👤 <b>ᴀᴅᴍɪɴ ᴜsᴇʀ ɪɴsᴘᴇᴄᴛᴏʀ & ᴄᴏɴᴛʀᴏʟs</b> 🛡️",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"┏🆔 <b>User ID:</b> <code>{uid}</code>",
        f"┣🏷 <b>Name:</b> <a href='tg://user?id={uid}'>{uname}</a>",
        f"┣🎖️ <b>Role:</b> <code>{role_str}</code>",
        f"┣🔐 <b>Access Status:</b> <code>{access_str}</code>",
        f"┣🎚️ <b>Monitor Quota:</b> <code>{quota_str}</code>",
        f"┣🌐 <b>Monitors:</b> <code>{u_info['total_monitors']}</code> "
        f"(🟢 <code>{u_info['up_monitors']}</code> │ 🔴 <code>{u_info['down_monitors']}</code> │ ⏸️ <code>{u_info['paused_monitors']}</code>)",
        f"┣📊 <b>Overall Uptime:</b> <code>{u_info['uptime_pct']}%</code> [{make_uptime_bar(u_info['uptime_pct'])}]",
        f"┣🔄 <b>Total Checks / Fails:</b> <code>{u_info['total_checks']:,}</code> / <code>{u_info['failures']:,}</code>",
        f"┗🕒 <b>Last Ping Activity:</b> <code>{last_act}</code>",
    ]

    u_mons = get_user_monitors(uid)
    if u_mons:
        lines.append("\n📋 <b>ᴜsᴇʀ's ᴍᴏɴɪᴛᴏʀs (ᴛᴏᴘ 5):</b>")
        for idx, m in enumerate(u_mons[:5], 1):
            ico = _status_icon(m)
            lines.append(
                f"  {idx}. {ico} <b>{html.escape(m.get('name', 'Site'))}</b> — "
                f"<code>{format_interval_readable(m.get('interval', 60))}</code> ({html.escape(m.get('url', ''))})"
            )

    ban_btn = (
        InlineKeyboardButton("✅ ᴜɴʙᴀɴ / ʀᴇsᴛᴏʀᴇ ᴀᴄᴄᴇss", callback_data=f"upad_uban#{uid}#{back_page}")
        if is_banned
        else InlineKeyboardButton("🚫 ʙᴀɴ / ʀᴇsᴛʀɪᴄᴛ ᴜsᴇʀ", callback_data=f"upad_uban#{uid}#{back_page}")
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🌐 ᴜsᴇʀ's ᴍᴏɴɪᴛᴏʀs ({u_info['total_monitors']})", callback_data=f"upad_umons#{uid}#0"),
            InlineKeyboardButton("➕ ᴀᴅᴅ sɪᴛᴇ ғᴏʀ ᴜsᴇʀ", callback_data=f"upad_uadd#{uid}#{back_page}"),
        ],
        [
            InlineKeyboardButton("✏️ ʀᴇɴᴀᴍᴇ ᴜsᴇʀ", callback_data=f"upad_urename#{uid}#{back_page}"),
            InlineKeyboardButton("🎚️ sᴇᴛ ǫᴜᴏᴛᴀ ʟɪᴍɪᴛ", callback_data=f"upad_uquota#{uid}#{back_page}"),
        ],
        [
            InlineKeyboardButton("⚡ ᴘɪɴɢ ᴜsᴇʀ sɪᴛᴇs", callback_data=f"upad_uping#{uid}#{back_page}"),
            InlineKeyboardButton("⏸️ ᴘᴀᴜsᴇ/ʀᴇsᴜᴍᴇ sɪᴛᴇs", callback_data=f"upad_utoggle_all#{uid}#{back_page}"),
        ],
        [
            ban_btn,
        ],
        [
            InlineKeyboardButton("🗑️ ᴅᴇʟᴇᴛᴇ ᴜsᴇʀ & ʀᴇᴄᴏʀᴅs", callback_data=f"upad_udel_ask#{uid}#{back_page}"),
        ],
        [
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀs", callback_data=f"upad_users#{back_page}#all"),
            InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home"),
        ],
    ])
    return "\n".join(lines), markup


def build_upad_monitors_list_view(
    page: int = 0,
    flt: str = "all",
    owner_uid: Optional[int] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Build paginated list of monitors."""
    all_mons = list(MONITORS_STORE.values())
    if owner_uid is not None:
        all_mons = [m for m in all_mons if int(m.get("user_id", 0)) == int(owner_uid)]
    all_mons.sort(key=lambda d: d.get("created_ts", 0), reverse=True)

    if flt == "up":
        filtered = [m for m in all_mons if m.get("enabled", True) and m.get("status") == "UP"]
    elif flt == "down":
        filtered = [m for m in all_mons if m.get("enabled", True) and m.get("status") == "DOWN"]
    elif flt == "paused":
        filtered = [m for m in all_mons if not m.get("enabled", True)]
    else:
        filtered = all_mons
        flt = "all"

    total = len(filtered)
    total_pages = max(1, math.ceil(total / SITES_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * SITES_PER_PAGE
    page_items = filtered[start_idx : start_idx + SITES_PER_PAGE]

    header_title = (
        f"🌐 <b>ᴜsᴇʀ <code>{owner_uid}</code> ᴍᴏɴɪᴛᴏʀs ({total})</b>"
        if owner_uid is not None
        else f"🌐 <b>ɢʟᴏʙᴀʟ ᴍᴏɴɪᴛᴏʀs [{flt.upper()}] ({total})</b>"
    )
    lines = [
        header_title,
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    buttons: List[List[InlineKeyboardButton]] = []
    if owner_uid is None:
        buttons.append([
            InlineKeyboardButton("🔘 ᴀʟʟ" if flt == "all" else "ᴀʟʟ", callback_data="upad_mons#0#all"),
            InlineKeyboardButton("🔘 🟢 ᴜᴘ" if flt == "up" else "🟢 ᴜᴘ", callback_data="upad_mons#0#up"),
            InlineKeyboardButton("🔘 🔴 ᴅᴏᴡɴ" if flt == "down" else "🔴 ᴅᴏᴡɴ", callback_data="upad_mons#0#down"),
            InlineKeyboardButton("🔘 ⏸️ ᴘᴀᴜsᴇᴅ" if flt == "paused" else "⏸️ ᴘᴀᴜsᴇᴅ", callback_data="upad_mons#0#paused"),
        ])

    ctx = f"umons_{owner_uid}" if owner_uid is not None else "mons"

    if not page_items:
        lines.append("📭 <i>No monitors found in this view.</i>")
    else:
        for idx, doc in enumerate(page_items, start=start_idx + 1):
            mid = doc["monitor_id"]
            ico = _status_icon(doc)
            name = html.escape(str(doc.get("name", "Site")))
            uid = doc.get("user_id", 0)
            int_str = format_interval_readable(int(doc.get("interval", 60)))
            lat = doc.get("last_latency_ms")
            lat_str = f"{lat:.0f}ms" if isinstance(lat, (int, float)) else "—"
            url_disp = html.escape(str(doc.get("url", "")))

            lines.append(
                f"<b>{idx}.</b> {ico} <b>{name}</b> │ 👤 <code>{uid}</code>\n"
                f"   ├ 🔗 {url_disp}\n"
                f"   └ ⏱️ <code>{int_str}</code> │ ⚡ <code>{lat_str}</code> │ 🔄 <code>{doc.get('total_checks', 0)}</code>"
            )
            btn_lbl = f"{ico} {doc.get('name', 'Site')} (UID:{uid}) • {int_str}"
            buttons.append([
                InlineKeyboardButton(btn_lbl[:55], callback_data=f"up_view#{mid}#{page}#{ctx}"),
                InlineKeyboardButton("✏️", callback_data=f"up_edit_name#{mid}#{page}#{ctx}"),
                InlineKeyboardButton("🗑️", callback_data=f"up_del_ask#{mid}#{page}#{ctx}"),
            ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📄 <b>ᴘᴀɢᴇ:</b> <code>{page + 1}/{total_pages}</code> • <i>Tap any monitor to inspect, edit, rename, or delete.</i>")

    nav_row: List[InlineKeyboardButton] = []
    if owner_uid is not None:
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"upad_umons#{owner_uid}#{page - 1}"))
        nav_row.append(InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"upad_umons#{owner_uid}#{page}"))
        if page + 1 < total_pages:
            nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"upad_umons#{owner_uid}#{page + 1}"))
        buttons.append(nav_row)
        buttons.append([
            InlineKeyboardButton("➕ ᴀᴅᴅ sɪᴛᴇ ғᴏʀ ᴜsᴇʀ", callback_data=f"upad_uadd#{owner_uid}#0"),
            InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ", callback_data=f"upad_uview#{owner_uid}#0"),
        ])
    else:
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"upad_mons#{page - 1}#{flt}"))
        nav_row.append(InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=f"upad_mons#{page}#{flt}"))
        if page + 1 < total_pages:
            nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"upad_mons#{page + 1}#{flt}"))
        buttons.append(nav_row)
        buttons.append([
            InlineKeyboardButton("🔍 sᴇᴀʀᴄʜ", callback_data="upad_search_prompt"),
            InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home"),
        ])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def build_upad_search_results_view(query_str: str) -> Tuple[str, InlineKeyboardMarkup]:
    """Search across all registered users and all monitors."""
    q = (query_str or "").strip().lower()
    all_users = await get_all_registered_users_merged()
    matched_users = [
        u for u in all_users
        if q in str(u["user_id"]).lower() or q in str(u.get("name", "")).lower()
    ]
    matched_mons = [
        m for m in MONITORS_STORE.values()
        if q in str(m.get("monitor_id", "")).lower()
        or q in str(m.get("name", "")).lower()
        or q in str(m.get("url", "")).lower()
        or q in str(m.get("user_id", "")).lower()
        or q in str(m.get("platform", "")).lower()
    ]

    lines = [
        f"🔍 <b>ᴀᴅᴍɪɴ sᴇᴀʀᴄʜ ʀᴇsᴜʟᴛs:</b> <code>{html.escape(query_str)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>ᴍᴀᴛᴄʜᴇᴅ ᴜsᴇʀs:</b> <code>{len(matched_users)}</code> │ 🌐 <b>ᴍᴀᴛᴄʜᴇᴅ ᴍᴏɴɪᴛᴏʀs:</b> <code>{len(matched_mons)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    buttons: List[List[InlineKeyboardButton]] = []
    for u in matched_users[:6]:
        uid = u["user_id"]
        uname = u.get("name", f"User {uid}")
        lines.append(f"👤 <b>{html.escape(str(uname))}</b> (<code>{uid}</code>) — <code>{u['total_monitors']}</code> sites")
        buttons.append([
            InlineKeyboardButton(f"👤 User: {uname} [{uid}] ({u['total_monitors']} sites)"[:60], callback_data=f"upad_uview#{uid}#0")
        ])

    for m in matched_mons[:8]:
        mid = m["monitor_id"]
        ico = _status_icon(m)
        lines.append(f"{ico} <b>{html.escape(str(m.get('name', 'Site')))}</b> (UID: <code>{m.get('user_id')}</code>) — {html.escape(str(m.get('url', '')))}")
        buttons.append([
            InlineKeyboardButton(f"{ico} Site: {m.get('name')} ({m.get('url')})"[:60], callback_data=f"up_view#{mid}#0#mons")
        ])

    if not matched_users and not matched_mons:
        lines.append("\n📭 <i>No matching users or monitors found.</i>")

    buttons.append([
        InlineKeyboardButton("🔍 sᴇᴀʀᴄʜ ᴀɢᴀɪɴ", callback_data="upad_search_prompt"),
        InlineKeyboardButton("👑 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


@Client.on_message(
    filters.command(["uptime", "addsite", "monitor"], prefixes=CMD_PREFIXES) & filters.incoming,
    group=-1,
)
async def uptime_command_handler(client: Client, message: Message):
    """Add or update a fast keep-alive uptime monitor with custom interval (s/m/h/d)."""
    await _ensure_engine_ready(client)
    if not message.from_user:
        return

    user_id = message.from_user.id
    user_name = message.from_user.first_name or f"User {user_id}"
    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    arg_body = parts[1].strip() if len(parts) > 1 else ""

    if not arg_body:
        USER_WIZARD[user_id] = {
            "mode": "add",
            "target_uid": user_id,
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await message.reply_text(
            "➕ <b>Add New URL</b>\n\n"
            "Please reply with URL and name in this format:\n"
            "<code>name|https://your-service.onrender.com</code>\n"
            "or with custom interval:\n"
            "<code>name|https://your-service.koyeb.app|30s</code>\n\n"
            "<i>Example: MyApp|https://myapp.onrender.com</i>\n"
            "<i>Or command:</i> <code>/uptime MyApp https://myapp.koyeb.app 1m</code>\n\n"
            f"⏱ <i>This request will expire in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="up_wiz_cancel")]
            ]),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    if "|" in arg_body:
        pipe_parts = [p.strip() for p in arg_body.split("|") if p.strip()]
        if len(pipe_parts) < 2:
            await message.reply_text(
                "❌ <b>Invalid Format!</b>\n\n"
                "Use: <code>/uptime name https://your-app.koyeb.app [interval]</code>\n"
                "Or: <code>name|https://your-app.koyeb.app</code>",
                parse_mode=enums.ParseMode.HTML,
                quote=True,
            )
            message.stop_propagation()
            return
        name = pipe_parts[0]
        url = pipe_parts[1]
        interval_tokens = pipe_parts[2:] if len(pipe_parts) > 2 else []
    else:
        tokens = arg_body.split()
        if len(tokens) == 1:
            if tokens[0].startswith(("http://", "https://")) or "." in tokens[0]:
                url = tokens[0]
                parsed_h = urlparse(url if "://" in url else f"https://{url}").hostname or "app"
                name = parsed_h.split(".")[0]
                interval_tokens = []
            else:
                await message.reply_text(
                    "⚠️ <b>Missing URL!</b>\n\n"
                    "<b>Usage:</b>\n"
                    "• <code>/uptime name https://your-app.koyeb.app</code> <i>(default 1m)</i>\n"
                    "• <code>/uptime name https://your-app.koyeb.app 30s</code>\n"
                    "• <code>/uptime name https://your-app.onrender.com 5m</code>\n"
                    "• <code>/uptime name https://your-app.vercel.app 1h</code>",
                    parse_mode=enums.ParseMode.HTML,
                    quote=True,
                )
                message.stop_propagation()
                return
        else:
            if tokens[0].startswith(("http://", "https://")):
                url = tokens[0]
                parsed_h = urlparse(url).hostname or "app"
                name = parsed_h.split(".")[0]
                interval_tokens = tokens[1:]
            else:
                name = tokens[0]
                url = tokens[1]
                interval_tokens = tokens[2:]

    interval_sec, int_err = parse_interval(interval_tokens)
    if int_err or interval_sec is None:
        await message.reply_text(
            f"❌ <b>Interval Error:</b> {int_err}",
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    wait_msg = await message.reply_text(
        "⚡ <b>ɪɴɪᴛɪᴀʟɪᴢɪɴɢ ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ & ᴘɪɴɢɪɴɢ sᴇʀᴠɪᴄᴇ...</b> ⏳",
        parse_mode=enums.ParseMode.HTML,
        quote=True,
    )

    doc, err = await create_and_start_monitor(user_id, name, url, interval_sec, user_name=user_name)
    if err or not doc:
        await wait_msg.edit_text(
            f"❌ <b>Failed to add monitor:</b>\n{err}",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        message.stop_propagation()
        return

    await wait_msg.edit_text(
        format_monitor_details_text(doc),
        reply_markup=build_monitor_detail_keyboard(doc, page=0),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )
    message.stop_propagation()


@Client.on_message(
    filters.command(["revoke", "deluptime", "rmuptime"], prefixes=CMD_PREFIXES) & filters.incoming,
    group=-1,
)
async def revoke_command_handler(client: Client, message: Message):
    """Revoke and delete an active uptime monitor by name, URL, or ID."""
    await _ensure_engine_ready(client)
    if not message.from_user:
        return

    if len(message.command) >= 2 and message.command[0].lower() == "revoke":
        try:
            from plugins.streamer import revoke_stream_by_id
            if await revoke_stream_by_id(message, message.command[1].strip()):
                message.stop_propagation()
                return
        except Exception:
            pass

    user_id = message.from_user.id
    user_monitors = get_user_monitors(user_id, include_all_for_admin=True)

    if len(message.command) < 2:
        if not user_monitors:
            await message.reply_text(
                "📭 <b>You have no active uptime monitors to revoke.</b>\n"
                "Use <code>/uptime name url</code> to add one!",
                parse_mode=enums.ParseMode.HTML,
                quote=True,
            )
            message.stop_propagation()
            return

        buttons: List[List[InlineKeyboardButton]] = []
        for doc in user_monitors[:15]:
            mid = doc["monitor_id"]
            icon = _status_icon(doc)
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ Revoke: {icon} {doc.get('name')} ({doc.get('url')})"[:60],
                    callback_data=f"up_del_do#{mid}#0#user",
                )
            ])
        buttons.append([InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close")])

        await message.reply_text(
            "🗑️ <b>ʀᴇᴠᴏᴋᴇ ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Usage:</b> <code>/revoke &lt;name or url&gt;</code>\n"
            "👇 <i>Or tap a monitor below to revoke it immediately:</i>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    target = " ".join(message.command[1:]).strip()
    target_lower = target.lower().rstrip("/")
    norm_target = target_lower
    if not norm_target.startswith(("http://", "https://")) and "." in norm_target:
        norm_target = "https://" + norm_target

    if target_lower == "all":
        own_monitors = get_user_monitors(user_id)
        if not own_monitors:
            await message.reply_text("ℹ️ <b>No monitors found to revoke.</b>", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return
        for doc in own_monitors:
            mid = doc["monitor_id"]
            stop_monitor_worker(mid)
            await delete_monitor_doc(mid)
        await message.reply_text(
            f"✅ <b>Revoked all <code>{len(own_monitors)}</code> uptime monitor(s) successfully!</b> 🗑️",
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    matched: List[Dict[str, Any]] = []
    for doc in user_monitors:
        mid = doc.get("monitor_id", "").lower()
        m_name = doc.get("name", "").lower()
        m_url = doc.get("url", "").lower().rstrip("/")
        if target_lower in (mid, m_name, m_url) or norm_target == m_url:
            matched.append(doc)

    if not matched:
        for doc in user_monitors:
            m_name = doc.get("name", "").lower()
            m_url = doc.get("url", "").lower()
            if target_lower in m_name or target_lower in m_url:
                matched.append(doc)

    if not matched:
        await message.reply_text(
            f"❌ <b>No monitor found matching:</b> <code>{html.escape(target)}</code>\n\n"
            "💡 Use <code>/listup</code> to view your active monitors.",
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    removed_lines = []
    for doc in matched:
        mid = doc["monitor_id"]
        stop_monitor_worker(mid)
        await delete_monitor_doc(mid)
        removed_lines.append(
            f"• 🏷 <b>{html.escape(doc.get('name', 'Site'))}</b> — <code>{html.escape(doc.get('url', ''))}</code>"
        )

    await message.reply_text(
        "🗑️ <b>ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ ʀᴇᴠᴏᴋᴇᴅ!</b> ✅\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(removed_lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 ᴏᴘᴇɴ /listup", callback_data="up_list#0")]
        ]),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        quote=True,
    )
    message.stop_propagation()


@Client.on_message(
    filters.command(["listup", "uptimes", "monitors"], prefixes=CMD_PREFIXES) & filters.incoming,
    group=-1,
)
async def listup_command_handler(client: Client, message: Message):
    """List all user-added uptime site links with interactive management buttons."""
    await _ensure_engine_ready(client)
    if not message.from_user:
        return

    text, markup = build_listup_view(message.from_user.id, page=0)
    await message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        quote=True,
    )
    message.stop_propagation()


@Client.on_message(
    filters.command("upad", prefixes=CMD_PREFIXES) & filters.incoming,
    group=-1,
)
async def upad_admin_command_handler(client: Client, message: Message):
    """Strictly ADMINS-only Uptime & User Management Control Panel."""
    await _ensure_engine_ready(client)
    user_id = message.from_user.id if message.from_user else None

    if not is_authorized_admin(user_id):
        logger.warning("🚫 Unauthorized /upad attempt by user_id=%s", user_id)
        await message.reply_text(
            "⛔ <b>ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ:</b> <code>/upad</code> ɪs sᴛʀɪᴄᴛʟʏ ʀᴇsᴇʀᴠᴇᴅ ғᴏʀ ʙᴏᴛ <b>ᴀᴅᴍɪɴs</b> 🔒",
            parse_mode=enums.ParseMode.HTML,
            quote=True,
        )
        message.stop_propagation()
        return

    if len(message.command) > 1:
        query_str = " ".join(message.command[1:]).strip()
        text, markup = await build_upad_search_results_view(query_str)
        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    text, markup = await build_upad_dashboard_view()
    await message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        quote=True,
    )
    message.stop_propagation()


def _is_awaiting_uptime_input(_, __, message: Message) -> bool:
    if not message.from_user or not message.text:
        return False
    if message.text.startswith(tuple(CMD_PREFIXES)):
        return False
    uid = message.from_user.id
    state = USER_WIZARD.get(uid)
    if not state:
        return False
    if time.time() > state.get("expires_at", 0):
        USER_WIZARD.pop(uid, None)
        return False
    return True


uptime_wizard_filter = filters.create(_is_awaiting_uptime_input)


@Client.on_message((filters.private | filters.group) & uptime_wizard_filter, group=-1)
async def handle_uptime_wizard_input(client: Client, message: Message):
    await _ensure_engine_ready(client)
    user_id = message.from_user.id
    state = USER_WIZARD.pop(user_id, None)
    if not state:
        return

    mode = state.get("mode")
    mid = state.get("monitor_id")
    page = int(state.get("page", 0))
    ctx = state.get("ctx", "user")
    target_uid = int(state.get("target_uid", user_id))
    is_admin_flow = bool(state.get("is_admin_panel", False))
    raw_input = (message.text or "").strip()

    if is_admin_flow and not is_authorized_admin(user_id):
        await message.reply_text("⛔ <b>Access Denied: Administrators only!</b>", parse_mode=enums.ParseMode.HTML, quote=True)
        message.stop_propagation()
        return

    if mode == "admin_search":
        text, markup = await build_upad_search_results_view(raw_input)
        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    if mode == "admin_rename_user":
        new_uname = raw_input[:50].strip()
        if not new_uname:
            await message.reply_text("❌ <b>User name cannot be empty.</b>", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return
        await save_user_meta(target_uid, {"name": new_uname})
        if users_db is not None and getattr(users_db, "col", None) is not None:
            try:
                await users_db.col.update_one({"id": int(target_uid)}, {"$set": {"name": new_uname}}, upsert=True)
            except Exception:
                pass
        for doc in MONITORS_STORE.values():
            if int(doc.get("user_id", 0)) == target_uid:
                doc["user_name"] = new_uname
                await save_monitor_doc(doc)

        text, markup = await build_upad_user_detail_view(target_uid, back_page=page)
        await message.reply_text(
            f"✅ <b>Updated display name for user <code>{target_uid}</code> to</b> <code>{html.escape(new_uname)}</code>!\n\n" + text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    if mode == "add":
        if "|" in raw_input:
            parts = [p.strip() for p in raw_input.split("|") if p.strip()]
            if len(parts) < 2:
                await message.reply_text(
                    "❌ <b>Invalid format!</b> Please use <code>name|https://your-service.com</code>",
                    parse_mode=enums.ParseMode.HTML,
                    quote=True,
                )
                message.stop_propagation()
                return
            name, url = parts[0], parts[1]
            int_tokens = parts[2:] if len(parts) > 2 else []
        else:
            tokens = raw_input.split()
            if len(tokens) == 1:
                url = tokens[0]
                parsed_h = urlparse(url if "://" in url else f"https://{url}").hostname or "app"
                name = parsed_h.split(".")[0]
                int_tokens = []
            else:
                name, url = tokens[0], tokens[1]
                int_tokens = tokens[2:]

        interval_sec, int_err = parse_interval(int_tokens)
        if int_err or interval_sec is None:
            await message.reply_text(f"❌ <b>Interval Error:</b> {int_err}", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return

        owner_name = (
            message.from_user.first_name
            if target_uid == user_id
            else USER_META_STORE.get(target_uid, {}).get("name", f"User {target_uid}")
        )
        doc, err = await create_and_start_monitor(
            target_uid,
            name,
            url,
            interval_sec,
            user_name=owner_name,
            bypass_quota=is_admin_flow,
        )
        if err or not doc:
            await message.reply_text(f"❌ <b>Error:</b> {err}", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return

        admin_origin = f"umons_{target_uid}" if is_admin_flow else None
        await message.reply_text(
            format_monitor_details_text(doc, show_owner=is_admin_flow),
            reply_markup=build_monitor_detail_keyboard(doc, page=0, admin_origin=admin_origin),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    doc = MONITORS_STORE.get(mid or "")
    if not doc:
        await message.reply_text("⚠️ <b>Monitor no longer exists.</b>", parse_mode=enums.ParseMode.HTML, quote=True)
        message.stop_propagation()
        return

    admin_origin = ctx if ctx != "user" else None

    if mode == "rename":
        new_name = raw_input[:40].strip()
        if not new_name:
            await message.reply_text("❌ <b>Name cannot be empty.</b>", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return
        doc["name"] = new_name
        await save_monitor_doc(doc)
        await message.reply_text(
            f"✅ <b>Monitor renamed to</b> <code>{html.escape(new_name)}</code>!\n\n"
            + format_monitor_details_text(doc, show_owner=bool(admin_origin)),
            reply_markup=build_monitor_detail_keyboard(doc, page=page, admin_origin=admin_origin),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    if mode == "url":
        norm_url, url_err = normalize_and_validate_url(raw_input)
        if url_err or not norm_url:
            await message.reply_text(f"❌ <b>Invalid URL:</b> {url_err}", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return
        doc["url"] = norm_url
        doc["platform"] = detect_platform(norm_url)
        doc["status"] = "UNKNOWN"
        await save_monitor_doc(doc)
        await execute_monitor_check(mid)
        start_monitor_worker(mid)
        fresh = MONITORS_STORE.get(mid, doc)
        await message.reply_text(
            "✅ <b>Monitor link updated & pinged!</b>\n\n"
            + format_monitor_details_text(fresh, show_owner=bool(admin_origin)),
            reply_markup=build_monitor_detail_keyboard(fresh, page=page, admin_origin=admin_origin),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return

    if mode == "interval":
        secs, int_err = parse_interval(raw_input.split())
        if int_err or secs is None:
            await message.reply_text(f"❌ <b>Interval Error:</b> {int_err}", parse_mode=enums.ParseMode.HTML, quote=True)
            message.stop_propagation()
            return
        doc["interval"] = secs
        await save_monitor_doc(doc)
        start_monitor_worker(mid)
        await message.reply_text(
            f"✅ <b>Ping interval updated to</b> <code>{format_interval_readable(secs)}</code>!\n\n"
            + format_monitor_details_text(doc, show_owner=bool(admin_origin)),
            reply_markup=build_monitor_detail_keyboard(doc, page=page, admin_origin=admin_origin),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            quote=True,
        )
        message.stop_propagation()
        return


@Client.on_callback_query(filters.regex(r"^upad_"))
async def upad_admin_callback_handler(client: Client, query: CallbackQuery):
    """Strictly ADMINS-only callback router for all /upad actions."""
    await _ensure_engine_ready(client)
    user_id = query.from_user.id if query.from_user else None

    if not is_authorized_admin(user_id):
        logger.warning("🚫 Unauthorized upad_ callback attempt by user_id=%s data=%s", user_id, query.data)
        return await query.answer("⛔ Access Denied: Administrators Only!", show_alert=True)

    data = query.data
    parts = data.split("#")
    action = parts[0]

    if action == "upad_home":
        USER_WIZARD.pop(user_id, None)
        await query.answer("👑 Admin Panel Refreshed")
        text, markup = await build_upad_dashboard_view()
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "upad_users":
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        flt = parts[2] if len(parts) > 2 else "all"
        USER_WIZARD.pop(user_id, None)
        await query.answer("👥 Loading users...")
        text, markup = await build_upad_users_list_view(page=page, flt=flt)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "upad_mons":
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        flt = parts[2] if len(parts) > 2 else "all"
        USER_WIZARD.pop(user_id, None)
        await query.answer("🌐 Loading monitors...")
        text, markup = build_upad_monitors_list_view(page=page, flt=flt)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "upad_umons":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await query.answer(f"🌐 User {target_uid} monitors")
        text, markup = build_upad_monitors_list_view(page=page, flt="all", owner_uid=target_uid)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "upad_uview":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        USER_WIZARD.pop(user_id, None)
        await query.answer(f"👤 Inspecting User {target_uid}")
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "upad_uadd":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else user_id
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        USER_WIZARD[user_id] = {
            "mode": "add",
            "target_uid": target_uid,
            "page": back_page,
            "is_admin_panel": True,
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer(f"➕ Add site for User {target_uid}")
        await query.message.edit_text(
            f"➕ <b>Admin: Add Monitor for User <code>{target_uid}</code></b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Reply in chat with:\n"
            "<code>name|https://service.koyeb.app|30s</code>\n"
            "or space-separated:\n"
            "<code>name https://service.onrender.com 1m</code>\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ", callback_data=f"upad_uview#{target_uid}#{back_page}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="upad_home"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_urename":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        USER_WIZARD[user_id] = {
            "mode": "admin_rename_user",
            "target_uid": target_uid,
            "page": back_page,
            "is_admin_panel": True,
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("✏️ Send new user display name")
        await query.message.edit_text(
            f"✏️ <b>Admin: Edit/Rename User <code>{target_uid}</code></b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the new display name or label for this user in chat:\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ", callback_data=f"upad_uview#{target_uid}#{back_page}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="upad_home"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_uquota":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await query.answer("🎚️ Select user monitor quota")
        q_rows: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for lbl, q_val in QUOTA_PRESETS:
            row.append(InlineKeyboardButton(lbl, callback_data=f"upad_setquota#{target_uid}#{q_val}#{back_page}"))
            if len(row) == 2:
                q_rows.append(row)
                row = []
        if row:
            q_rows.append(row)
        q_rows.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ", callback_data=f"upad_uview#{target_uid}#{back_page}")])
        await query.message.edit_text(
            f"🎚️ <b>Set Max Monitor Quota for User <code>{target_uid}</code></b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Select the maximum number of uptime monitors this user is allowed to create:",
            reply_markup=InlineKeyboardMarkup(q_rows),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_setquota":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        q_val = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else MAX_MONITORS_PER_USER
        back_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        await save_user_meta(target_uid, {"max_monitors": q_val})
        await query.answer(f"✅ Quota updated to {q_val}!", show_alert=True)
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return

    if action == "upad_uban":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if is_authorized_admin(target_uid):
            return await query.answer("⛔ Cannot ban or restrict another Administrator!", show_alert=True)

        meta = await get_or_create_user_meta(target_uid)
        currently_banned = bool(meta.get("uptime_disabled", False)) or (target_uid in getattr(temp, "BANNED_USERS", []))
        new_ban_state = not currently_banned

        await save_user_meta(target_uid, {"uptime_disabled": new_ban_state})
        if users_db is not None:
            try:
                if new_ban_state:
                    await users_db.ban_user(target_uid, "Restricted via /upad Admin Panel")
                    if hasattr(temp, "BANNED_USERS") and target_uid not in temp.BANNED_USERS:
                        temp.BANNED_USERS.append(target_uid)
                else:
                    await users_db.remove_ban(target_uid)
                    if hasattr(temp, "BANNED_USERS") and target_uid in temp.BANNED_USERS:
                        temp.BANNED_USERS.remove(target_uid)
            except Exception as e:
                logger.debug("users_db ban sync notice: %s", e)

        for doc in get_user_monitors(target_uid):
            mid = doc["monitor_id"]
            if new_ban_state:
                doc["enabled"] = False
                doc["status"] = "PAUSED"
                stop_monitor_worker(mid)
                await save_monitor_doc(doc)
            else:
                doc["enabled"] = True
                doc["status"] = "UNKNOWN"
                await save_monitor_doc(doc)
                start_monitor_worker(mid)

        await query.answer(
            "🚫 User restricted & monitors paused!" if new_ban_state else "✅ User access restored & monitors resumed!",
            show_alert=True,
        )
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return

    if action == "upad_uping":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        u_mons = get_user_monitors(target_uid)
        if not u_mons:
            return await query.answer("📭 User has no monitors to ping!", show_alert=True)
        await query.answer(f"⚡ Pinging {len(u_mons)} monitor(s)...")
        await asyncio.gather(*(execute_monitor_check(m["monitor_id"]) for m in u_mons), return_exceptions=True)
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        return

    if action == "upad_utoggle_all":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        u_mons = get_user_monitors(target_uid)
        if not u_mons:
            return await query.answer("📭 User has no monitors!", show_alert=True)
        any_enabled = any(m.get("enabled", True) for m in u_mons)
        for doc in u_mons:
            mid = doc["monitor_id"]
            if any_enabled:
                doc["enabled"] = False
                doc["status"] = "PAUSED"
                stop_monitor_worker(mid)
                await save_monitor_doc(doc)
            else:
                doc["enabled"] = True
                doc["status"] = "UNKNOWN"
                await save_monitor_doc(doc)
                start_monitor_worker(mid)
        await query.answer("⏸️ All user monitors paused!" if any_enabled else "▶️ All user monitors resumed!", show_alert=True)
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        return

    if action == "upad_udel_ask":
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        u_mons = get_user_monitors(target_uid)
        await query.answer("⚠️ Confirm deletion")
        await query.message.edit_text(
            f"⚠️ <b>Confirm Delete User <code>{target_uid}</code> Records?</b> 🗑️\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>User's Monitors:</b> <code>{len(u_mons)}</code>\n\n"
            "Choose whether to delete only the user's uptime monitors or purge the user completely from the database:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🗑️ Delete {len(u_mons)} Monitors Only", callback_data=f"upad_udel_mons#{target_uid}#{back_page}")],
                [InlineKeyboardButton("🔥 Purge User + All Monitors", callback_data=f"upad_udel_full#{target_uid}#{back_page}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"upad_uview#{target_uid}#{back_page}")],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action in ("upad_udel_mons", "upad_udel_full"):
        target_uid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        back_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        u_mons = get_user_monitors(target_uid)
        for doc in u_mons:
            mid = doc["monitor_id"]
            stop_monitor_worker(mid)
            await delete_monitor_doc(mid)

        if action == "upad_udel_full":
            async with _DB_LOCK:
                USER_META_STORE.pop(target_uid, None)
                _save_local_user_meta()
                if UPTIME_USERS_COL is not None:
                    try:
                        await UPTIME_USERS_COL.delete_one({"user_id": target_uid})
                    except Exception:
                        pass
                if users_db is not None:
                    try:
                        await users_db.delete_user(target_uid)
                    except Exception:
                        pass
            await query.answer(f"🔥 Purged User {target_uid} & {len(u_mons)} monitors!", show_alert=True)
            text, markup = await build_upad_users_list_view(page=back_page, flt="all")
            await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            return

        await query.answer(f"🗑️ Deleted {len(u_mons)} monitors for User {target_uid}!", show_alert=True)
        text, markup = await build_upad_user_detail_view(target_uid, back_page=back_page)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return

    if action == "upad_search_prompt":
        USER_WIZARD[user_id] = {
            "mode": "admin_search",
            "is_admin_panel": True,
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("🔍 Send search query in chat")
        await query.message.edit_text(
            "🔍 <b>Admin Search — Users & Monitors</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send any <b>User ID, User Name, Monitor Name, Platform, or URL</b> in chat to search across all records:\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home")]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_ping_global":
        all_mons = [m for m in MONITORS_STORE.values() if m.get("enabled", True)]
        if not all_mons:
            return await query.answer("📭 No active monitors to ping!", show_alert=True)
        await query.answer(f"⚡ Pinging all {len(all_mons)} active monitors...")
        await asyncio.gather(*(execute_monitor_check(m["monitor_id"]) for m in all_mons), return_exceptions=True)
        text, markup = await build_upad_dashboard_view()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        return

    if action in ("upad_pause_all", "upad_resume_all"):
        enable_target = action == "upad_resume_all"
        all_mons = list(MONITORS_STORE.values())
        if not all_mons:
            return await query.answer("📭 No monitors found!", show_alert=True)
        for doc in all_mons:
            mid = doc["monitor_id"]
            doc["enabled"] = enable_target
            if not enable_target:
                doc["status"] = "PAUSED"
                stop_monitor_worker(mid)
                await save_monitor_doc(doc)
            else:
                doc["status"] = "UNKNOWN"
                await save_monitor_doc(doc)
                start_monitor_worker(mid)
        await query.answer(
            f"▶️ Resumed all {len(all_mons)} monitors!" if enable_target else f"⏸️ Paused all {len(all_mons)} monitors!",
            show_alert=True,
        )
        text, markup = await build_upad_dashboard_view()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        return

    if action == "upad_export_global":
        all_mons = list(MONITORS_STORE.values())
        if not all_mons:
            return await query.answer("📭 No monitors to export!", show_alert=True)
        await query.answer("📥 Exporting global JSON backup...")
        bio = make_monitor_json_bytes(all_mons, filename=f"global_uptime_backup_{int(time.time())}.json")
        await query.message.reply_document(
            document=bio,
            caption=f"👑 <b>Global Uptime Monitors Backup</b> (<code>{len(all_mons)}</code> total sites)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_prune_menu":
        down_cnt = sum(1 for m in MONITORS_STORE.values() if m.get("status") == "DOWN")
        paused_cnt = sum(1 for m in MONITORS_STORE.values() if not m.get("enabled", True))
        total_cnt = len(MONITORS_STORE)
        await query.answer("🧹 Cleanup & Prune Menu")
        await query.message.edit_text(
            "🧹 <b>Admin Cleanup & Prune Controls</b> 🛡️\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 🔴 <b>Down Monitors:</b> <code>{down_cnt}</code>\n"
            f"• ⏸️ <b>Paused Monitors:</b> <code>{paused_cnt}</code>\n"
            f"• 📊 <b>Total Global Monitors:</b> <code>{total_cnt}</code>\n\n"
            "Select a cleanup operation below (confirmation protected):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🗑️ Delete All DOWN Monitors ({down_cnt})", callback_data="upad_prune_do#down")],
                [InlineKeyboardButton(f"🗑️ Delete All PAUSED Monitors ({paused_cnt})", callback_data="upad_prune_do#paused")],
                [InlineKeyboardButton(f"🔥 Wipe ALL Global Monitors ({total_cnt})", callback_data="upad_prune_ask_all")],
                [InlineKeyboardButton("🔙 ᴀᴅᴍɪɴ ʜᴏᴍᴇ", callback_data="upad_home")],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_prune_ask_all":
        await query.answer("⚠️ Final confirmation required")
        await query.message.edit_text(
            "🚨 <b>WARNING: Wipe All Global Monitors?</b>\n\n"
            f"This will permanently delete all <code>{len(MONITORS_STORE)}</code> monitors across all users. Confirm?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm Wipe All", callback_data="upad_prune_do#all"),
                    InlineKeyboardButton("❌ Cancel", callback_data="upad_prune_menu"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "upad_prune_do":
        mode_p = parts[1] if len(parts) > 1 else ""
        to_delete: List[str] = []
        for mid, doc in list(MONITORS_STORE.items()):
            if mode_p == "down" and doc.get("status") == "DOWN":
                to_delete.append(mid)
            elif mode_p == "paused" and not doc.get("enabled", True):
                to_delete.append(mid)
            elif mode_p == "all":
                to_delete.append(mid)

        for mid in to_delete:
            stop_monitor_worker(mid)
            await delete_monitor_doc(mid)

        await query.answer(f"🧹 Deleted {len(to_delete)} monitor(s)!", show_alert=True)
        text, markup = await build_upad_dashboard_view()
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return


def _can_manage_monitor(user_id: int, doc: Dict[str, Any]) -> bool:
    return int(doc.get("user_id", 0)) == int(user_id) or is_authorized_admin(user_id)


@Client.on_callback_query(filters.regex(r"^up_"))
async def uptime_callback_handler(client: Client, query: CallbackQuery):
    await _ensure_engine_ready(client)
    if not query.from_user:
        return await query.answer("⚠️ Invalid user", show_alert=True)

    user_id = query.from_user.id
    data = query.data

    if data == "up_close":
        USER_WIZARD.pop(user_id, None)
        await query.answer("✅ Closed")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data == "up_wiz_cancel":
        USER_WIZARD.pop(user_id, None)
        await query.answer("❌ Cancelled")
        try:
            await query.message.edit_text(
                "❌ <b>Operation cancelled.</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 ᴏᴘᴇɴ /listup", callback_data="up_list#0")]
                ]),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if data == "up_help":
        await query.answer("📖 Uptime Help")
        try:
            await query.message.edit_text(
                build_help_text(),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("➕ ᴀᴅᴅ sᴇʀᴠᴇʀ ᴜʀʟ", callback_data="up_add_prompt"),
                        InlineKeyboardButton("📋 ᴍʏ ᴍᴏɴɪᴛᴏʀs", callback_data="up_list#0"),
                    ],
                    [
                        InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="up_list#0"),
                        InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="nav_home"),
                        InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="up_close"),
                    ],
                ]),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if data == "up_add_prompt":
        USER_WIZARD[user_id] = {
            "mode": "add",
            "target_uid": user_id,
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("➕ Send name|url in chat")
        try:
            await query.message.edit_text(
                "➕ <b>Add New URL</b>\n\n"
                "Please reply in chat with URL and name in this format:\n"
                "<code>name|https://your-service.onrender.com</code>\n"
                "or with custom interval (s/m/h/d):\n"
                "<code>name|https://your-service.koyeb.app|30s</code>\n\n"
                "<i>Example: MyApp|https://myapp.onrender.com</i>\n\n"
                f"⏱ <i>This request will expire in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="up_list#0"),
                        InlineKeyboardButton("❌ Cancel", callback_data="up_wiz_cancel"),
                    ]
                ]),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if data.startswith("up_list#"):
        page_str = data.split("#", 1)[1]
        page = int(page_str) if page_str.isdigit() else 0
        USER_WIZARD.pop(user_id, None)
        await query.answer("🔄 Updated list")
        text, markup = build_listup_view(user_id, page=page)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if data.startswith("up_ping_all#"):
        page_str = data.split("#", 1)[1]
        page = int(page_str) if page_str.isdigit() else 0
        monitors = get_user_monitors(user_id)
        if not monitors:
            return await query.answer("📭 No monitors to ping!", show_alert=True)
        await query.answer(f"⚡ Pinging {len(monitors)} site(s)...")
        await asyncio.gather(*(execute_monitor_check(m["monitor_id"]) for m in monitors), return_exceptions=True)
        text, markup = build_listup_view(user_id, page=page)
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if data == "up_export_all":
        monitors = get_user_monitors(user_id)
        if not monitors:
            return await query.answer("📭 No monitors to export!", show_alert=True)
        await query.answer("📥 Exporting JSON...")
        bio = make_monitor_json_bytes(monitors, filename=f"uptime_monitors_{user_id}.json")
        await query.message.reply_document(
            document=bio,
            caption=f"📦 <b>Uptime Monitors Export</b> (<code>{len(monitors)}</code> sites)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if data == "up_clear_ask":
        monitors = get_user_monitors(user_id)
        if not monitors:
            return await query.answer("📭 Nothing to clear!", show_alert=True)
        await query.answer("⚠️ Confirm clear all")
        await query.message.edit_text(
            f"⚠️ <b>Confirm Delete All Monitors?</b>\n\n"
            f"Are you sure you want to revoke and delete all <code>{len(monitors)}</code> uptime monitors?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Delete All", callback_data="up_clear_do"),
                    InlineKeyboardButton("❌ Cancel", callback_data="up_list#0"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if data == "up_clear_do":
        monitors = get_user_monitors(user_id)
        for doc in monitors:
            mid = doc["monitor_id"]
            stop_monitor_worker(mid)
            await delete_monitor_doc(mid)
        await query.answer(f"🗑️ Deleted {len(monitors)} monitors!", show_alert=True)
        text, markup = build_listup_view(user_id, page=0)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    parts = data.split("#")
    action = parts[0]
    mid = parts[1] if len(parts) > 1 else ""
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    ctx = parts[3] if len(parts) > 3 else "user"

    if ctx != "user" and not is_authorized_admin(user_id):
        return await query.answer("⛔ Access Denied: Administrators only!", show_alert=True)

    admin_origin = ctx if ctx != "user" else None

    doc = MONITORS_STORE.get(mid)
    if not doc:
        return await query.answer("⚠️ Monitor not found or already deleted.", show_alert=True)
    if not _can_manage_monitor(user_id, doc):
        return await query.answer("⛔ You can only manage your own monitors!", show_alert=True)

    if action == "up_view":
        USER_WIZARD.pop(user_id, None)
        await query.answer(f"🖥 {doc.get('name')}")
        try:
            await query.message.edit_text(
                format_monitor_details_text(doc, show_owner=bool(admin_origin)),
                reply_markup=build_monitor_detail_keyboard(doc, page=page, admin_origin=admin_origin),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "up_ping":
        await query.answer("⚡ Pinging server now...")
        fresh = await execute_monitor_check(mid) or doc
        try:
            await query.message.edit_text(
                format_monitor_details_text(fresh, show_owner=bool(admin_origin)),
                reply_markup=build_monitor_detail_keyboard(fresh, page=page, admin_origin=admin_origin),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "up_toggle":
        new_state = not doc.get("enabled", True)
        doc["enabled"] = new_state
        if not new_state:
            doc["status"] = "PAUSED"
            stop_monitor_worker(mid)
            await save_monitor_doc(doc)
            await query.answer("⏸️ Monitor paused!")
        else:
            doc["status"] = "UNKNOWN"
            await save_monitor_doc(doc)
            await execute_monitor_check(mid)
            start_monitor_worker(mid)
            await query.answer("▶️ Monitor resumed & pinged!")
        fresh = MONITORS_STORE.get(mid, doc)
        try:
            await query.message.edit_text(
                format_monitor_details_text(fresh, show_owner=bool(admin_origin)),
                reply_markup=build_monitor_detail_keyboard(fresh, page=page, admin_origin=admin_origin),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "up_notify":
        doc["notify"] = not doc.get("notify", True)
        await save_monitor_doc(doc)
        state_lbl = "ON 🔔" if doc["notify"] else "OFF 🔕"
        await query.answer(f"Alerts turned {state_lbl}")
        try:
            await query.message.edit_text(
                format_monitor_details_text(doc, show_owner=bool(admin_origin)),
                reply_markup=build_monitor_detail_keyboard(doc, page=page, admin_origin=admin_origin),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "up_json":
        await query.answer("📥 Sending .json")
        bio = make_monitor_json_bytes(doc, filename=f"{doc.get('name', 'monitor')}.json")
        await query.message.reply_document(
            document=bio,
            caption=format_monitor_details_text(doc, show_owner=bool(admin_origin)),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "up_edit_name":
        USER_WIZARD[user_id] = {
            "mode": "rename",
            "monitor_id": mid,
            "page": page,
            "ctx": ctx,
            "is_admin_panel": bool(admin_origin),
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("✏️ Send new name in chat")
        await query.message.edit_text(
            f"✏️ <b>Rename Monitor:</b> <code>{html.escape(doc.get('name', ''))}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Please send the <b>new name</b> for this monitor in chat:\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"up_view#{mid}#{page}#{ctx}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="up_wiz_cancel"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "up_edit_url":
        USER_WIZARD[user_id] = {
            "mode": "url",
            "monitor_id": mid,
            "page": page,
            "ctx": ctx,
            "is_admin_panel": bool(admin_origin),
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("🔗 Send new URL in chat")
        await query.message.edit_text(
            f"🔗 <b>Change URL for:</b> <code>{html.escape(doc.get('name', ''))}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Current URL:</b> {html.escape(doc.get('url', ''))}\n\n"
            "Please send the <b>new URL</b> (e.g. <code>https://new-app.koyeb.app/</code>) in chat:\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"up_view#{mid}#{page}#{ctx}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="up_wiz_cancel"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if action == "up_edit_int":
        await query.answer("⏱️ Select ping interval")
        cur_int = format_interval_readable(int(doc.get("interval", 60)))
        await query.message.edit_text(
            f"⏱️ <b>Edit Ping Interval:</b> <code>{html.escape(doc.get('name', ''))}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Current Interval:</b> <code>{cur_int}</code>\n\n"
            "👇 <i>Choose a preset interval below or tap Custom Interval:</i>",
            reply_markup=build_interval_picker_keyboard(mid, page=page, ctx=ctx),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "up_set_int":
        secs = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 60
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        ctx = parts[4] if len(parts) > 4 else "user"
        admin_origin = ctx if ctx != "user" else None
        doc["interval"] = max(MIN_INTERVAL_SECONDS, secs)
        await save_monitor_doc(doc)
        start_monitor_worker(mid)
        await query.answer(f"✅ Interval set to {format_interval_readable(doc['interval'])}!")
        try:
            await query.message.edit_text(
                format_monitor_details_text(doc, show_owner=bool(admin_origin)),
                reply_markup=build_monitor_detail_keyboard(doc, page=page, admin_origin=admin_origin),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return

    if action == "up_custom_int":
        USER_WIZARD[user_id] = {
            "mode": "interval",
            "monitor_id": mid,
            "page": page,
            "ctx": ctx,
            "is_admin_panel": bool(admin_origin),
            "expires_at": time.time() + WIZARD_TIMEOUT_SECONDS,
        }
        await query.answer("⌨️ Send interval in chat")
        await query.message.edit_text(
            f"⌨️ <b>Custom Interval for:</b> <code>{html.escape(doc.get('name', ''))}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send your desired interval in chat using:\n"
            "• Seconds: <code>15s</code> or <code>45</code>\n"
            "• Minutes: <code>2m</code> or <code>10min</code>\n"
            "• Hours: <code>1h</code> or <code>6hr</code>\n"
            "• Days: <code>1d</code> or <code>2days</code>\n\n"
            f"⏱ <i>Expires in {WIZARD_TIMEOUT_SECONDS} seconds.</i>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"up_edit_int#{mid}#{page}#{ctx}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="up_wiz_cancel"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "up_del_ask":
        await query.answer("⚠️ Confirm removal")
        await query.message.edit_text(
            "⚠️ <b>Confirm Monitor Removal</b> 🗑️\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>Name:</b> <code>{html.escape(doc.get('name', ''))}</code>\n"
            f"🌐 <b>URL:</b> {html.escape(doc.get('url', ''))}\n\n"
            "Are you sure you want to revoke and delete this uptime monitor?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Remove", callback_data=f"up_del_do#{mid}#{page}#{ctx}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"up_view#{mid}#{page}#{ctx}"),
                ]
            ]),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if action == "up_del_do":
        name_del = doc.get("name", "Monitor")
        owner_uid = int(doc.get("user_id", user_id))
        stop_monitor_worker(mid)
        await delete_monitor_doc(mid)
        await query.answer(f"🗑️ Removed {name_del}!", show_alert=True)

        if admin_origin:
            if admin_origin.startswith("umons_"):
                uid_b = int(admin_origin.split("_", 1)[1]) if admin_origin.split("_", 1)[1].lstrip("-").isdigit() else owner_uid
                text, markup = build_upad_monitors_list_view(page=page, flt="all", owner_uid=uid_b)
            else:
                text, markup = build_upad_monitors_list_view(page=page, flt="all")
        else:
            text, markup = build_listup_view(user_id, page=page)

        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
        return
