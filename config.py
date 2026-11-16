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


class Config:
    API_ID: int = int(re.sub(r"\D", "", _clean_env("API_ID", "2040")) or "2040")
    API_HASH: str = _clean_env("API_HASH", "b18441a1ff607e10a989891a5462e627")
    BOT_TOKEN: str = _clean_env("BOT_TOKEN", "")
    OWNER_ID: int = int(re.sub(r"\D", "", _clean_env("OWNER_ID", "1892771262")) or "1892771262")

    raw_admins = f"{_clean_env('ADMINS', '')} {_clean_env('AUTH_USERS', '')} {OWNER_ID}"
    ADMINS: List[int] = list(dict.fromkeys([int(x) for x in re.findall(r"\d+", raw_admins)]))
    if OWNER_ID not in ADMINS:
        ADMINS.append(OWNER_ID)

    # Single Unified Database Configuration
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

    LOG_CHANNEL: int = int(re.findall(r"-?\d+", _clean_env("LOG_CHANNEL", "0"))[0]) if re.findall(r"-?\d+", _clean_env("LOG_CHANNEL", "0")) else 0
    BIN_CHANNEL: int = int(re.findall(r"-?\d+", _clean_env("BIN_CHANNEL", _clean_env("STORAGE_CHANNEL", str(LOG_CHANNEL))))[0]) if re.findall(r"-?\d+", _clean_env("BIN_CHANNEL", _clean_env("STORAGE_CHANNEL", str(LOG_CHANNEL)))) else 0

    PORT: int = int(re.sub(r"\D", "", _clean_env("PORT", "8080")) or "8080")
    BIND_ADDRESS: str = _clean_env("BIND_ADDRESS", "0.0.0.0")

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

    # Ultra-fast cache and high-throughput streaming configuration
    CACHE_TTL: int = int(re.sub(r"\D", "", _clean_env("CACHE_TTL", "1800")) or "1800")
    CACHE_MAX_ENTRIES: int = int(re.sub(r"\D", "", _clean_env("CACHE_MAX_ENTRIES", "8000")) or "8000")
    STREAM_CHUNK_SIZE: int = int(re.sub(r"\D", "", _clean_env("STREAM_CHUNK_SIZE", str(4 * 1024 * 1024))) or str(4 * 1024 * 1024))
    STREAM_SPEED_LIMIT_MBPS: int = int(re.sub(r"\D", "", _clean_env("STREAM_SPEED_LIMIT_MBPS", "100")) or "100")
    STREAM_BUFFER_CHUNKS: int = int(re.sub(r"\D", "", _clean_env("STREAM_BUFFER_CHUNKS", "64")) or "64")

    DEFAULT_AUTO_DELETE: int = int(re.sub(r"\D", "", _clean_env("AUTO_DELETE_TIME", "0")) or "0")
    FORCE_SUB_CHANNEL: str = _clean_env("FORCE_SUB_CHANNEL", _clean_env("FORCE_SUB", ""))
    WORKERS: int = int(re.sub(r"\D", "", _clean_env("WORKERS", "128")) or "128")
    TG_TRANSMISSION_WORKERS: int = max(1, int(re.sub(r"\D", "", _clean_env("TG_TRANSMISSION_WORKERS", "64")) or "64"))
    # Unlimited concurrent task admission (0 = unlimited queue + controlled parallel execution without FloodWait)
    MAX_CONCURRENT_TASKS: int = int(re.sub(r"\D", "", _clean_env("MAX_CONCURRENT_TASKS", "0")) or "0")
    FFMPEG_PARALLEL_JOBS: int = max(4, int(re.sub(r"\D", "", _clean_env("FFMPEG_PARALLEL_JOBS", "32")) or "32"))
    KEEPALIVE_INTERVAL: int = max(5, int(re.sub(r"\D", "", _clean_env("KEEPALIVE_INTERVAL", "15")) or "15"))
    RESTART_INTERVAL: int = max(3600, int(re.sub(r"\D", "", _clean_env("RESTART_INTERVAL", "86400")) or "86400"))

    # Centralized Telegram API Rate-Limit & Coalescing Configuration
    GLOBAL_RPC_RATE_PER_SEC: float = float(_clean_env("GLOBAL_RPC_RATE_PER_SEC", "24.0") or "24.0")
    PER_CHAT_RPC_RATE_PER_SEC: float = float(_clean_env("PER_CHAT_RPC_RATE_PER_SEC", "1.0") or "1.0")
    PER_CHAT_EDIT_INTERVAL_SEC: float = float(_clean_env("PER_CHAT_EDIT_INTERVAL_SEC", "2.8") or "2.8")
    PROGRESS_EDIT_MIN_INTERVAL_SEC: float = float(_clean_env("PROGRESS_EDIT_MIN_INTERVAL_SEC", "3.5") or "3.5")

    # Fault-Tolerant Persistent Task Recovery Configuration
    TASK_RECOVERY_ENABLED: bool = _clean_env("TASK_RECOVERY_ENABLED", "true").lower() in ("true", "1", "yes", "on")
    TASK_MAX_RETRIES: int = max(1, int(re.sub(r"\D", "", _clean_env("TASK_MAX_RETRIES", "4")) or "4"))
    TASK_WATCHDOG_INTERVAL: int = max(5, int(re.sub(r"\D", "", _clean_env("TASK_WATCHDOG_INTERVAL", "12")) or "12"))

    DOWNLOAD_DIR: str = os.path.join(os.getcwd(), "downloads")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    TASK_JOURNAL_PATH: str = _clean_env(
        "TASK_JOURNAL_PATH",
        os.path.join(DOWNLOAD_DIR, ".persistent_tasks_journal.json")
    )
