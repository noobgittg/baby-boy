import os
import re
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _clean_env(key: str, default: str = "") -> str:
    raw = os.environ.get(key, default)
    if raw is None:
        return default
    return str(raw).strip().strip("\"'").strip()


def _parse_int(key: str, default: int, minimum: int = None, maximum: int = None) -> int:
    raw = _clean_env(key, str(default))
    m = re.search(r"-?\d+", raw)
    try:
        val = int(m.group(0)) if m else int(default)
    except (ValueError, TypeError):
        val = int(default)
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def _parse_float(key: str, default: float, minimum: float = None, maximum: float = None) -> float:
    raw = _clean_env(key, str(default))
    m = re.search(r"-?\d+(?:\.\d+)?", raw)
    try:
        val = float(m.group(0)) if m else float(default)
    except (ValueError, TypeError):
        val = float(default)
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


class Config:
    API_ID: int = _parse_int("API_ID", 2040, minimum=1)
    API_HASH: str = _clean_env("API_HASH", "")
    BOT_TOKEN: str = _clean_env("BOT_TOKEN", "")
    OWNER_ID: int = _parse_int("OWNER_ID", 1892771262, minimum=0)

    raw_admins = f"{_clean_env('ADMINS', '')} {_clean_env('AUTH_USERS', '')} {OWNER_ID}"
    ADMINS: List[int] = list(dict.fromkeys([int(x) for x in re.findall(r"\d+", raw_admins) if int(x) > 0]))
    if OWNER_ID and OWNER_ID not in ADMINS:
        ADMINS.append(OWNER_ID)

    MONGO_URI: str = (
        _clean_env("MONGO_URI", "")
        or _clean_env("DATABASE_URI", "")
        or _clean_env("DATABASE_URL", "")
        or _clean_env("gen", "")
        or _clean_env("main", "")
        or _clean_env("SECONDDB_URI", "")
        or "mongodb://localhost:27017"
    )
    DATABASE_NAME: str = (
        _clean_env("DATABASE_NAME", "")
        or _clean_env("DB_NAME", "")
        or "MMW_ProBot"
    )

    LOG_CHANNEL: int = _parse_int("LOG_CHANNEL", 0)
    BIN_CHANNEL: int = _parse_int(
        "BIN_CHANNEL",
        _parse_int("STORAGE_CHANNEL", LOG_CHANNEL),
    )

    PORT: int = _parse_int("PORT", 8080, minimum=1, maximum=65535)
    BIND_ADDRESS: str = _clean_env("BIND_ADDRESS", "0.0.0.0") or "0.0.0.0"

    koyeb_app = _clean_env("KOYEB_APP_NAME", "")
    render_url = _clean_env("RENDER_EXTERNAL_URL", "")
    default_base = (
        render_url.rstrip("/")
        if render_url
        else (f"https://{koyeb_app}.koyeb.app" if koyeb_app else f"http://127.0.0.1:{PORT}")
    )
    BASE_URL: str = _clean_env("BASE_URL", _clean_env("URL", default_base)).rstrip("/")

    THAM_URL: str = _clean_env("THAM_URL", "https://envs.sh/thumb.jpg")

    WATERMARK: str = _clean_env("WATERMARK", "MMW BOTZ")
    WATERMARK_URL: str = _clean_env("WATERMARK_URL", "https://t.me/mallumovieworldmain2")

    SUPPORT_CHAT: str = _clean_env("SUPPORT_CHAT", "MRXSUPPORTS")
    GRP_LNK: str = _clean_env("GRP_LNK", "https://t.me/mallumovieworldmain3")
    CHNL_LNK: str = _clean_env("CHNL_LNK", "https://t.me/mallumovieworldmain1")
    MAIN_CHANNEL: str = _clean_env("MAIN_CHANNEL", "https://t.me/mallumovieworldmain1")

    CACHE_TTL: int = _parse_int("CACHE_TTL", 1800, minimum=10)
    CACHE_MAX_ENTRIES: int = _parse_int("CACHE_MAX_ENTRIES", 12000, minimum=100)
    CACHE_CLEANUP_INTERVAL: int = _parse_int("CACHE_CLEANUP_INTERVAL", 300, minimum=30)
    LARGE_FILE_THRESHOLD_BYTES: int = _parse_int("LARGE_FILE_THRESHOLD_BYTES", 2 * 1024 * 1024 * 1024, minimum=1024 * 1024)
    STREAM_CHUNK_SIZE: int = _parse_int("STREAM_CHUNK_SIZE", 4 * 1024 * 1024, minimum=65536)
    STREAM_SPEED_LIMIT_MBPS: int = _parse_int("STREAM_SPEED_LIMIT_MBPS", 100, minimum=1)
    STREAM_BUFFER_CHUNKS: int = _parse_int("STREAM_BUFFER_CHUNKS", 64, minimum=4)

    DEFAULT_AUTO_DELETE: int = _parse_int("AUTO_DELETE_TIME", 0, minimum=0)
    FORCE_SUB_CHANNEL: str = _clean_env("FORCE_SUB_CHANNEL", _clean_env("FORCE_SUB", ""))
    WORKERS: int = _parse_int("WORKERS", 32, minimum=4, maximum=128)
    TG_TRANSMISSION_WORKERS: int = _parse_int("TG_TRANSMISSION_WORKERS", 8, minimum=1, maximum=16)
    MAX_CONCURRENT_TASKS: int = _parse_int("MAX_CONCURRENT_TASKS", 0, minimum=0)
    _default_ffmpeg_jobs = max(2, min(8, (os.cpu_count() or 2) * 2))
    FFMPEG_PARALLEL_JOBS: int = _parse_int("FFMPEG_PARALLEL_JOBS", _default_ffmpeg_jobs, minimum=1, maximum=32)
    KEEPALIVE_INTERVAL: int = _parse_int("KEEPALIVE_INTERVAL", 15, minimum=5)
    RESTART_INTERVAL: int = _parse_int("RESTART_INTERVAL", 86400, minimum=3600)

    GLOBAL_RPC_RATE_PER_SEC: float = _parse_float("GLOBAL_RPC_RATE_PER_SEC", 28.0, minimum=1.0)
    PER_CHAT_RPC_RATE_PER_SEC: float = _parse_float("PER_CHAT_RPC_RATE_PER_SEC", 4.0, minimum=0.5)
    PER_CHAT_EDIT_INTERVAL_SEC: float = _parse_float("PER_CHAT_EDIT_INTERVAL_SEC", 2.0, minimum=0.5)
    PROGRESS_EDIT_MIN_INTERVAL_SEC: float = _parse_float("PROGRESS_EDIT_MIN_INTERVAL_SEC", 3.0, minimum=0.5)

    TASK_RECOVERY_ENABLED: bool = _clean_env("TASK_RECOVERY_ENABLED", "true").lower() in ("true", "1", "yes", "on")
    TASK_MAX_RETRIES: int = _parse_int("TASK_MAX_RETRIES", 4, minimum=1, maximum=10)
    TASK_WATCHDOG_INTERVAL: int = _parse_int("TASK_WATCHDOG_INTERVAL", 15, minimum=5)

    DOWNLOAD_DIR: str = os.path.join(os.getcwd(), "downloads")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    TASK_JOURNAL_PATH: str = _clean_env(
        "TASK_JOURNAL_PATH",
        os.path.join(DOWNLOAD_DIR, ".persistent_tasks_journal.json")
    )
