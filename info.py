import re
import time
from os import environ
from config import Config
from Script import script

id_pattern = re.compile(r'^-?\d+$')


def _clean_env(key: str, default: str = "") -> str:
    raw = environ.get(key, default)
    if raw is None:
        return default
    return str(raw).strip().strip("\"'").strip()


def is_enabled(value, default: bool = True) -> bool:
    cleaned = str(value).strip().strip("\"'").lower()
    if cleaned in ["true", "yes", "1", "enable", "y", "on"]:
        return True
    elif cleaned in ["false", "no", "0", "disable", "n", "off"]:
        return False
    return bool(default)


def _parse_int_env(key: str, default: int = 0) -> int:
    raw = _clean_env(key, str(default))
    if raw and id_pattern.search(raw):
        return int(raw)
    return int(default)


def _parse_prefixes(raw_val: str) -> list:
    cleaned = str(raw_val or "").strip().strip("\"'").strip()
    if not cleaned:
        return ["/", "!", "."]
    parts = [p.strip() for p in re.split(r"[\s,]+", cleaned) if p.strip()]
    for std in ("/", "!", "."):
        if std not in parts:
            parts.append(std)
    return parts


def normalize_ai_base_url(raw_url: str) -> str:
    cleaned = str(raw_url or "https://mmwaiapi.vercel.app/v1").strip().strip("\"'").rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    for suffix in (
        "/v1/models",
        "/v1/chat/completions",
        "/v1/chat/stream",
        "/models",
        "/chat/completions",
        "/chat/stream",
    ):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
            break
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


SESSION = _clean_env("SESSION", "MMW_Pro_Manager")
API_ID = Config.API_ID
API_HASH = Config.API_HASH
BOT_TOKEN = Config.BOT_TOKEN
OWNER_ID = Config.OWNER_ID
COMMAND_PREFIXES = _parse_prefixes(_clean_env("COMMAND_PREFIXES", "/"))

BOT_START_TIME = time.time()
CACHE_TIME = _parse_int_env("CACHE_TIME", 300)
CACHE_TTL = Config.CACHE_TTL
CACHE_MAX_ENTRIES = Config.CACHE_MAX_ENTRIES
RATE_LIMIT = _parse_int_env("RATE_LIMIT", 0)
RATE_WINDOW = _parse_int_env("RATE_WINDOW", 60)
CORS_ORIGINS = _clean_env("CORS_ORIGINS", "*")

PICS = _clean_env("PICS", "https://envs.sh/E6C.mp4").split()

KEEP_ALIVE_URL = _clean_env("KEEP_ALIVE_URL", Config.BASE_URL)
FAST_CACHE_ENABLED = is_enabled(_clean_env("FAST_CACHE_ENABLED", "True"), True)
WORK_DIR = Config.DOWNLOAD_DIR

AI_BASE_URL = normalize_ai_base_url(_clean_env("AI_BASE_URL", "https://mmwaiapi.vercel.app/v1"))
AI_MODELS_URL = f"{AI_BASE_URL}/models"
AI_CHAT_STREAM_URL = f"{AI_BASE_URL}/chat/stream"
AI_CHAT_COMPLETIONS_URL = f"{AI_BASE_URL}/chat/completions"
AI_DEFAULT_MODEL = _clean_env("AI_DEFAULT_MODEL", "llama-3.3-70b")

ADMINS = list(Config.ADMINS)
CHANNELS = [
    int(ch) if id_pattern.search(ch) else ch
    for ch in _clean_env("CHANNELS", "0").split()
]
auth_users = [
    int(user) if id_pattern.search(user) else user
    for user in _clean_env("AUTH_USERS", str(OWNER_ID)).split()
]
AUTH_USERS = list(dict.fromkeys((auth_users + ADMINS) if auth_users else list(ADMINS)))

DATABASE_URI = Config.MONGO_URI
SECONDDB_URI = _clean_env("SECONDDB_URI", DATABASE_URI)
DATABASE_NAME = Config.DATABASE_NAME
DB_NAME = DATABASE_NAME
COLLECTION_NAME = _clean_env("COLLECTION_NAME", "mmw_pro_data") or "mmw_pro_data"

LOG_CHANNEL = Config.LOG_CHANNEL
STORAGE_CHANNEL = Config.BIN_CHANNEL or LOG_CHANNEL
BIN_CHANNEL = Config.BIN_CHANNEL
SUPPORT_CHAT = Config.SUPPORT_CHAT
GRP_LNK = Config.GRP_LNK
CHNL_LNK = Config.CHNL_LNK
MAIN_CHANNEL = Config.MAIN_CHANNEL
MOVIE_UPDATE_CHANNEL = _parse_int_env("MOVIE_UPDATE_CHANNEL")

_raw_update_ch = _parse_int_env("UPDATE_CHANNEL", MOVIE_UPDATE_CHANNEL)
if str(_raw_update_ch).startswith("-100") and len(str(_raw_update_ch)) < 14 and len(str(MOVIE_UPDATE_CHANNEL)) == 14:
    UPDATE_CHANNEL = MOVIE_UPDATE_CHANNEL
else:
    UPDATE_CHANNEL = _raw_update_ch

MELCOW_NEW_USERS = is_enabled(_clean_env("MELCOW_NEW_USERS", "True"), True)
PROTECT_CONTENT = is_enabled(_clean_env("PROTECT_CONTENT", "False"), False)
CUSTOM_FILE_CAPTION = _clean_env("CUSTOM_FILE_CAPTION", f"{script.CUSTOM_FILE_CAPTION}")
BATCH_FILE_CAPTION = _clean_env("BATCH_FILE_CAPTION", CUSTOM_FILE_CAPTION)

LOG_STR = "⚡ ᴍᴍᴡ ᴀʟʟ-ɪɴ-ᴏɴᴇ ᴘʀᴏ ɢʀᴏᴜᴘ & ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇʀ + sᴛʀᴇᴀᴍ + ᴀɪ ᴄᴏɴꜰɪɢ:\n"
LOG_STR += "✅ ᴀᴜᴛᴏ ᴄᴀᴘᴛɪᴏɴ, ᴀᴜᴛᴏ ʀᴇɴᴀᴍᴇ, ᴀᴜᴛᴏ ꜰᴏʀᴡᴀʀᴅ, ᴀᴜᴛᴏ sᴛʀᴇᴀᴍ & ᴀᴜᴛᴏ sᴇᴛ: ᴀᴄᴛɪᴠᴇ ⚡\n"
LOG_STR += "✅ ᴘʀᴏ ɢʀᴏᴜᴘ ᴍɢᴍᴛ & ʜᴇʟᴘᴅᴇsᴋ sʏsᴛᴇᴍ (/helpdesk): ᴀᴄᴛɪᴠᴇ 🛡️\n"
LOG_STR += "✅ ᴘʀᴏ ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇʀ & ʜᴇʟᴘᴇʀ (/channelhelp): ᴀᴄᴛɪᴠᴇ 📢\n"
LOG_STR += "✅ ɴᴏᴛᴇs, ᴀᴜᴛᴏ-ꜰɪʟᴛᴇʀs, ʙʟᴀᴄᴋʟɪsᴛ, ᴀꜰᴋ & ᴛᴀɢᴀʟʟ: ᴀᴄᴛɪᴠᴇ 📝\n"
LOG_STR += "✅ 24/7 ᴜᴘᴛɪᴍᴇ ᴍᴏɴɪᴛᴏʀ & ᴍᴜʟᴛɪ-ᴍᴏᴅᴇʟ ᴀɪ: ᴀᴄᴛɪᴠᴇ 🚀\n"
LOG_STR += "✅ ᴅᴜᴀʟ-ʟᴀʏᴇʀ ꜰᴀsᴛ ᴄᴀᴄʜᴇ (ʀᴀᴍ + ᴍᴏɴɢᴏᴅʙ): ᴀᴄᴛɪᴠᴇ 💾\n"
