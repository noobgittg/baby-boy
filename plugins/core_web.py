import os
import re
import time
import math
import asyncio
import hashlib
import mimetypes
from collections import defaultdict
from urllib.parse import quote
from typing import Optional, Dict, Any, AsyncGenerator, List

import uvicorn
from fastapi import FastAPI, APIRouter, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    import aiofiles
except ImportError:
    aiofiles = None

from config import Config
from plugins.core_utils import logger, cache, humanbytes, time_formatter
from plugins.core_db import user_repo, chat_repo, file_repo, FileRepository
from plugins.media_types import media_kind, normalize_mime, browser_media_supported, is_code_file, detect_code_language


CHUNK_SIZE = 1024 * 1024
FIRST_FRAME_SLICE = 64 * 1024
PREFETCH_QUEUE_SIZE = max(16, int(getattr(Config, "STREAM_BUFFER_CHUNKS", 64)))
LOCAL_READ_CHUNK = max(CHUNK_SIZE, int(getattr(Config, "STREAM_CHUNK_SIZE", 4 * 1024 * 1024)))
MAX_CONCURRENT_STREAMS = max(8, min(64, int(getattr(Config, "WORKERS", 32))))
AUTO_CACHE_CLEAR_INTERVAL = 1.0
MAX_LOCK_ENTRIES = 512

_MSG_RESOLVE_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_FILE_DOC_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_CHUNK_FETCH_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_STATS_LOCK = asyncio.Lock()
_STREAM_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_STREAMS)
_PROBE_SEMAPHORE = asyncio.Semaphore(2)
_AUDIO_REMUX_SEMAPHORE = asyncio.Semaphore(2)
_SUB_SEMAPHORE = asyncio.Semaphore(1)
_CACHE_CLEANER_TASK: Optional[asyncio.Task] = None

_EXT_MIME_FALLBACK = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
    ".wmv": "video/x-ms-wmv",
    ".ogv": "video/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wma": "audio/x-ms-wma",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
    ".csv": "text/csv",
    ".srt": "text/plain",
    ".vtt": "text/vtt",
}


def _guess_mime_from_name(file_name: Optional[str], raw_mime: Optional[str]) -> str:
    norm = normalize_mime(raw_mime)
    if norm and norm != "application/octet-stream":
        return norm
    name = str(file_name or "").strip().lower()
    if name:
        _, ext = os.path.splitext(name)
        if ext in _EXT_MIME_FALLBACK:
            return _EXT_MIME_FALLBACK[ext]
        guessed = mimetypes.guess_type(name)[0]
        if guessed:
            return normalize_mime(guessed)
    return norm or "application/octet-stream"


def _detect_real_media_kind(mime_type: str, file_name: Optional[str], raw_kind: Optional[str] = None) -> str:
    guessed_mime = _guess_mime_from_name(file_name, mime_type)
    detected = media_kind(guessed_mime, file_name)
    if detected in ("video", "audio", "image", "code"):
        return detected
    if raw_kind in ("video", "audio", "image", "code"):
        return raw_kind
    return detected or "document"


def _trim_unlocked_locks(lock_map: Dict[str, asyncio.Lock]) -> int:
    if len(lock_map) <= MAX_LOCK_ENTRIES:
        return 0
    removed = 0
    for key in list(lock_map.keys()):
        lock = lock_map.get(key)
        if lock is not None and not lock.locked():
            lock_map.pop(key, None)
            removed += 1
    return removed


async def auto_sweep_stream_cache_once() -> int:
    removed = 0
    try:
        removed += cache.auto_clear_after_response_sync(preserve_workflows=True)
    except Exception:
        pass
    removed += _trim_unlocked_locks(_FILE_DOC_LOCKS)
    removed += _trim_unlocked_locks(_MSG_RESOLVE_LOCKS)
    removed += _trim_unlocked_locks(_CHUNK_FETCH_LOCKS)
    return removed


async def _auto_cache_cleaner_loop():
    while True:
        try:
            await asyncio.sleep(AUTO_CACHE_CLEAR_INTERVAL)
            await auto_sweep_stream_cache_once()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(AUTO_CACHE_CLEAR_INTERVAL)


def start_auto_cache_cleaner() -> Optional[asyncio.Task]:
    global _CACHE_CLEANER_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _CACHE_CLEANER_TASK is None or _CACHE_CLEANER_TASK.done():
        _CACHE_CLEANER_TASK = loop.create_task(_auto_cache_cleaner_loop())
    return _CACHE_CLEANER_TASK


async def stop_auto_cache_cleaner() -> None:
    global _CACHE_CLEANER_TASK
    if _CACHE_CLEANER_TASK is not None and not _CACHE_CLEANER_TASK.done():
        _CACHE_CLEANER_TASK.cancel()
        try:
            await _CACHE_CLEANER_TASK
        except asyncio.CancelledError:
            pass
    _CACHE_CLEANER_TASK = None


def parse_range_header(value: Optional[str], file_size: int):
    if file_size <= 0:
        return None
    if not value:
        return 0, file_size - 1
    try:
        unit, raw = value.strip().split("=", 1)
        if unit.lower() != "bytes" or "," in raw:
            return None
        first, last = raw.split("-", 1)
        if not first and not last:
            return None
        if not first:
            length = min(int(last), file_size)
            return (file_size - length, file_size - 1) if length > 0 else None
        start = int(first)
        end = int(last) if last else file_size - 1
        if start < 0 or start >= file_size or end < start:
            return None
        return start, min(end, file_size - 1)
    except (TypeError, ValueError):
        return None


def content_disposition(disposition: str, file_name: str) -> str:
    safe = "".join(ch if ch.isprintable() and ch not in '\"\\\r\n' else "_" for ch in (file_name or "download.bin"))
    return f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{quote(safe)}"


def compute_etag(file_id: str, file_size: int) -> str:
    digest = hashlib.md5(f"{file_id}:{file_size}".encode("utf-8")).hexdigest()[:16]
    return f"\"{digest}\""


def _js_safe_str(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("</", "<\\/")
    )
    return f'"{escaped}"'


FOUR_EXTERNAL_PLAYERS = [
    {"id": "playit", "name": "PLAYit", "badge": "Turbo Range"},
    {"id": "mxplayer", "name": "MX Player", "badge": "HW+ Decoder"},
    {"id": "vlc", "name": "VLC Player", "badge": "All Codecs"},
    {"id": "splayer", "name": "S Player", "badge": "Ultra Smooth"},
]


def build_4_external_player_links(stream_url: str, file_name: str = "video.mp4") -> Dict[str, str]:
    clean_url_no_scheme = stream_url.replace("https://", "").replace("http://", "")
    scheme = "https" if stream_url.startswith("https://") else "http"
    safe_title = quote(file_name or "video.mp4")
    encoded_stream_url = quote(stream_url, safe="")

    return {
        "playit": f"playit://playerv2/video?url={encoded_stream_url}&title={safe_title}",
        "playit_intent": f"intent:{stream_url}#Intent;package=com.playit.videoplayer;type=video/*;S.title={safe_title};end",
        "mxplayer": f"intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;type=video/*;S.title={safe_title};b.decode_mode=2;end",
        "mxplayer_pro": f"intent:{stream_url}#Intent;package=com.mxtech.videoplayer.pro;type=video/*;S.title={safe_title};b.decode_mode=2;end",
        "vlc": f"vlc://{stream_url}",
        "vlc_intent": f"intent://{clean_url_no_scheme}#Intent;scheme={scheme};package=org.videolan.vlc;type=video/*;S.title={safe_title};end",
        "splayer": f"intent:{stream_url}#Intent;package=com.young.simple.player;type=video/*;S.title={safe_title};end",
        "splayer_alt": f"intent:{stream_url}#Intent;package=com.splayer.video;type=video/*;S.title={safe_title};end",
    }


def _safe_metadata_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def media_metadata_view(file_doc: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict(file_doc.get("metadata") or {})
    file_id = str(file_doc.get("file_id", ""))
    file_name = str(file_doc.get("file_name") or "")
    mime_type = _guess_mime_from_name(file_name, file_doc.get("mime_type") or raw.get("mime_type"))
    kind = _detect_real_media_kind(mime_type, file_name, raw.get("media_kind"))

    audio_tracks = []
    for idx, aud in enumerate(raw.get("audio_tracks") or raw.get("audio_streams") or []):
        item = dict(aud)
        lang_val = str(item.get("language") or item.get("lang") or "Undetermined").strip()
        if not lang_val or lang_val.lower() in ("und", "unknown", "none"):
            lang_val = "Undetermined"
        item["language"] = lang_val
        item["lang"] = lang_val
        codec_u = str(item.get("codec") or "Undetermined").upper()
        ch_n = _safe_metadata_int(item.get("channels") or 0)
        item["codec"] = codec_u
        item["channels"] = ch_n
        item["channel_layout"] = str(item.get("channel_layout") or "Undetermined")
        item["title"] = str(item.get("title") or f"Track {idx + 1}")
        item["default"] = bool(item.get("default", idx == 0))
        if "browser_supported" not in item:
            c_low = codec_u.lower()
            item["browser_supported"] = (c_low in {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_f32le"} and ch_n <= 2)
        item["needs_transcode"] = not bool(item["browser_supported"])
        if file_id and not item.get("url"):
            item["url"] = f"/api/audio/{file_id}/{idx}"
        audio_tracks.append(item)

    subtitle_tracks = []
    for idx, sub in enumerate(raw.get("subtitle_tracks") or raw.get("subtitle_streams") or []):
        item = dict(sub)
        lang_val = str(item.get("language") or item.get("lang") or "Undetermined").strip()
        if not lang_val or lang_val.lower() in ("und", "unknown", "none"):
            lang_val = "Undetermined"
        item["language"] = lang_val
        item["lang"] = lang_val
        codec_u = str(item.get("codec") or "Undetermined").upper()
        item["codec"] = codec_u
        item["lang_code"] = str(item.get("lang_code") or "und").lower()
        item["title"] = str(item.get("title") or f"Subtitle {idx + 1}")
        item["default"] = bool(item.get("default", False))
        if "is_text_subtitle" not in item:
            item["is_text_subtitle"] = codec_u.lower() not in {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "dvb_subtitle", "xsub"}
        if file_id and not item.get("url"):
            item["url"] = f"/api/subtitles/{file_id}/{idx}.vtt"
        subtitle_tracks.append(item)

    language = str(raw.get("language") or "").strip()
    if not language or language.lower() in ("und", "unknown", "none"):
        language = "Undetermined"
    if language == "Undetermined" and audio_tracks:
        langs = [
            a["language"]
            for a in audio_tracks
            if a.get("language") and a["language"] != "Undetermined"
        ]
        if langs:
            language = ", ".join(dict.fromkeys(langs))

    audio_track = str(raw.get("audio_track") or "").strip()
    if not audio_track or audio_track.lower() in ("default audio", "unknown", "none"):
        audio_track = "Undetermined"
    if audio_tracks:
        auds = [
            f"{a['codec']} {a['channels']}ch ({a['language']})" if a["channels"] > 0 else f"{a['codec']} ({a['language']})"
            for a in audio_tracks
        ]
        if auds:
            audio_track = ", ".join(auds)

    _, ext = os.path.splitext(file_name)
    return {
        "media_kind": kind,
        "mime_type": mime_type,
        "extension": ext.lstrip(".").upper() if ext else "Undetermined",
        "browser_supported": browser_media_supported(mime_type, file_name),
        "duration": _safe_metadata_int(raw.get("duration")),
        "width": _safe_metadata_int(raw.get("width")),
        "height": _safe_metadata_int(raw.get("height")),
        "language": language,
        "audio_track": audio_track,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
        "caption": str(raw.get("caption") or "").strip(),
    }


async def _cache_remove(key: str) -> None:
    delete_fn = getattr(cache, "delete", None)
    if callable(delete_fn):
        await delete_fn(key)
    else:
        await cache.set(key, None, ttl=1)


async def get_cached_file_doc(file_id: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    cache_key = f"file_doc_{file_id}"
    if not force_refresh:
        cached_doc = await cache.get(cache_key)
        if cached_doc is not None:
            return cached_doc

    async with _FILE_DOC_LOCKS[file_id]:
        if not force_refresh:
            cached_doc = await cache.get(cache_key)
            if cached_doc is not None:
                return cached_doc
        doc = await FileRepository.get_file_by_id(file_id)
        if doc is None:
            doc = await file_repo.get_file(file_id)
        if doc is not None:
            await cache.set(cache_key, doc, ttl=120)
        return doc


async def invalidate_file_cache(file_id: str) -> None:
    await asyncio.gather(
        _cache_remove(f"file_doc_{file_id}"),
        _cache_remove(f"meta_view_{file_id}"),
        _cache_remove(f"api_info_{file_id}"),
        _cache_remove(f"tg_msg_{file_id}"),
    )


async def get_cached_metadata_view(file_doc: Dict[str, Any]) -> Dict[str, Any]:
    file_id = str(file_doc.get("file_id", ""))
    if not file_id:
        return media_metadata_view(file_doc)
    cache_key = f"meta_view_{file_id}"
    cached_view = await cache.get(cache_key)
    if cached_view is not None:
        return cached_view
    view = media_metadata_view(file_doc)
    await cache.set(cache_key, view, ttl=60)
    return view


async def get_cached_chunk(file_id: str, chunk_idx: int) -> Optional[bytes]:
    return await cache.get(f"chunk_{file_id}_{chunk_idx}")


async def set_cached_chunk(file_id: str, chunk_idx: int, chunk: bytes, is_boundary: bool = False) -> None:
    ttl = 60 if is_boundary else 5
    await cache.set(f"chunk_{file_id}_{chunk_idx}", chunk, ttl=ttl, transient=not is_boundary)


async def get_cached_health_payload() -> Dict[str, Any]:
    cached_val = await cache.get("web_health_payload")
    if cached_val is not None:
        return cached_val
    payload = {
        "status": "healthy",
        "service": "MMW-BOT-PRO",
        "stream_speed": "ultra-fast HTTP 206 zero-buffer streaming",
        "external_players": 4,
        "timestamp": float(time.time()),
        "watermark": Config.WATERMARK,
    }
    await cache.set("web_health_payload", payload, ttl=2, transient=True)
    return payload


async def get_cached_home_stats():
    cached_stats = await cache.get("web_home_stats")
    if cached_stats is not None:
        return cached_stats
    async with _STATS_LOCK:
        cached_stats = await cache.get("web_home_stats")
        if cached_stats is not None:
            return cached_stats
        users_count, chats_count, files_count = await asyncio.gather(
            user_repo.get_total_users(),
            chat_repo.get_total_chats(),
            file_repo.get_total_files(),
        )
        result = (users_count, chats_count, files_count)
        await cache.set("web_home_stats", result, ttl=10)
        return result


async def get_cached_api_info(file_id: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    cache_key = f"api_info_{file_id}"
    if not force_refresh:
        cached_info = await cache.get(cache_key)
        if cached_info is not None:
            return cached_info
    file_doc = await get_cached_file_doc(file_id, force_refresh=force_refresh)
    if not file_doc:
        return None
    stream_url = f"{Config.BASE_URL}/stream/{file_id}"
    fname = file_doc.get("file_name", "video.mp4")
    metadata = media_metadata_view(file_doc) if force_refresh else await get_cached_metadata_view(file_doc)
    payload = {
        "file_id": file_doc["file_id"],
        "file_name": fname,
        "file_size": file_doc.get("file_size"),
        "file_size_formatted": humanbytes(file_doc.get("file_size", 0)),
        "mime_type": metadata.get("mime_type") or file_doc.get("mime_type"),
        "created_at": file_doc.get("created_at"),
        "stream_speed": "turbo HTTP 206 zero-buffer streaming",
        "stream_url": stream_url,
        "watch_url": f"{Config.BASE_URL}/watch/{file_id}",
        "download_url": f"{Config.BASE_URL}/download/{file_id}",
        "direct_download_url": f"{Config.BASE_URL}/file/{file_id}",
        "external_players": build_4_external_player_links(stream_url, fname),
        "metadata": metadata,
        "watermark": Config.WATERMARK,
    }
    await cache.set(cache_key, payload, ttl=30)
    return payload


async def get_cached_user_files(user_id: int, skip: int, limit: int) -> Dict[str, Any]:
    cache_key = f"user_files_{user_id}_{skip}_{limit}"
    cached_payload = await cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload
    files, total = await asyncio.gather(
        file_repo.get_user_files(user_id, skip=skip, limit=limit),
        file_repo.get_user_files_count(user_id),
    )
    items = []
    for f in files:
        fid = f.get("file_id")
        fname = f.get("file_name", "Media.mp4")
        s_url = f"{Config.BASE_URL}/stream/{fid}"
        items.append({
            "file_id": fid,
            "file_name": fname,
            "file_size": f.get("file_size", 0),
            "file_size_formatted": humanbytes(f.get("file_size", 0)),
            "mime_type": _guess_mime_from_name(fname, f.get("mime_type")),
            "created_at": f.get("created_at"),
            "watch_url": f"{Config.BASE_URL}/watch/{fid}",
            "download_url": f"{Config.BASE_URL}/download/{fid}",
            "stream_url": s_url,
            "external_players": build_4_external_player_links(s_url, fname),
        })
    payload = {
        "user_id": user_id,
        "total": total,
        "skip": skip,
        "limit": limit,
        "files": items,
    }
    await cache.set(cache_key, payload, ttl=15)
    return payload


async def get_cached_search_results(query: str) -> List[Dict[str, Any]]:
    q_hash = hashlib.md5(query.lower().encode("utf-8")).hexdigest()[:16]
    cache_key = f"search_q_{q_hash}"
    cached_res = await cache.get(cache_key)
    if cached_res is not None:
        return cached_res
    files = await file_repo.search_files(query, limit=20)
    results = []
    for f in files:
        fid = f.get("file_id")
        fname = f.get("file_name", "Media")
        results.append({
            "file_id": fid,
            "file_name": fname,
            "file_size": f.get("file_size", 0),
            "file_size_formatted": humanbytes(f.get("file_size", 0)),
            "mime_type": _guess_mime_from_name(fname, f.get("mime_type")),
            "created_at": f.get("created_at"),
            "stream_url": f"{Config.BASE_URL}/stream/{fid}",
            "watch_url": f"{Config.BASE_URL}/watch/{fid}",
            "download_url": f"{Config.BASE_URL}/download/{fid}",
            "direct_download_url": f"{Config.BASE_URL}/file/{fid}",
        })
    await cache.set(cache_key, results, ttl=15)
    return results


async def resolve_telegram_message(bot: Any, file_doc: Dict[str, Any], force_refresh: bool = False):
    file_id = str(file_doc.get("file_id", ""))
    cache_key = f"tg_msg_{file_id}"
    if not force_refresh:
        cached_msg = await cache.get(cache_key)
        if cached_msg is not None:
            return cached_msg

    if not bot or not getattr(bot, "is_connected", False):
        return None

    chat_id = file_doc.get("chat_id")
    message_id = file_doc.get("message_id")
    if not chat_id or not message_id:
        return None

    async with _MSG_RESOLVE_LOCKS[file_id]:
        if not force_refresh:
            cached_msg = await cache.get(cache_key)
            if cached_msg is not None:
                return cached_msg
        try:
            msg = await bot.get_messages(int(chat_id), int(message_id))
            if msg and msg.media:
                await cache.set(cache_key, msg, ttl=600)
                return msg
        except Exception as e:
            logger.warning(f"ByteStreamer failed resolving message {chat_id}:{message_id} -> {e}")
    return None


def _slice_chunk(chunk: bytes, current_part: int, part_count: int, first_part_cut: int, last_part_cut: int) -> bytes:
    if part_count == 1:
        return chunk[first_part_cut:last_part_cut]
    if current_part == 1:
        return chunk[first_part_cut:]
    if current_part == part_count:
        return chunk[:last_part_cut]
    return chunk


async def iter_12mbps_file_stream(
    file_path: Optional[str],
    bot: Any,
    file_doc: Dict[str, Any],
    start: int,
    end: int,
    request: Optional[Request] = None
) -> AsyncGenerator[bytes, None]:
    content_length = (end - start) + 1
    file_id = str(file_doc.get("file_id", ""))
    total_file_size = _safe_metadata_int(file_doc.get("file_size", 0))
    last_chunk_index = max(0, (total_file_size - 1) // CHUNK_SIZE) if total_file_size > 0 else -1

    async with _STREAM_SEMAPHORE:
        if file_path and os.path.exists(file_path):
            if aiofiles is not None:
                async with aiofiles.open(file_path, "rb") as f:
                    await f.seek(start)
                    remaining = content_length
                    first_read = True
                    while remaining > 0:
                        if request is not None and await request.is_disconnected():
                            return
                        read_size = min(FIRST_FRAME_SLICE if first_read else LOCAL_READ_CHUNK, remaining)
                        first_read = False
                        data = await f.read(read_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data
            else:
                def _open_and_seek():
                    fh = open(file_path, "rb")
                    fh.seek(start)
                    return fh

                f = await asyncio.to_thread(_open_and_seek)
                try:
                    remaining = content_length
                    first_read = True
                    while remaining > 0:
                        if request is not None and await request.is_disconnected():
                            return
                        read_size = min(FIRST_FRAME_SLICE if first_read else LOCAL_READ_CHUNK, remaining)
                        first_read = False
                        data = await asyncio.to_thread(f.read, read_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data
                finally:
                    await asyncio.to_thread(f.close)
            return

        offset_chunks = start // CHUNK_SIZE
        first_part_cut = start % CHUNK_SIZE
        last_part_cut = (end % CHUNK_SIZE) + 1
        part_count = math.ceil((end + 1) / CHUNK_SIZE) - offset_chunks

        current_part = 1
        remaining_bytes = content_length
        while current_part <= part_count and remaining_bytes > 0:
            abs_idx = offset_chunks + current_part - 1
            cached_raw = await get_cached_chunk(file_id, abs_idx)
            if cached_raw is None:
                break
            sliced = _slice_chunk(cached_raw, current_part, part_count, first_part_cut, last_part_cut)
            if len(sliced) > remaining_bytes:
                sliced = sliced[:remaining_bytes]
            if request is not None and await request.is_disconnected():
                return
            if current_part == 1 and len(sliced) > FIRST_FRAME_SLICE:
                yield sliced[:FIRST_FRAME_SLICE]
                remaining_bytes -= FIRST_FRAME_SLICE
                rest = sliced[FIRST_FRAME_SLICE:]
                if rest:
                    yield rest
                    remaining_bytes -= len(rest)
            else:
                yield sliced
                remaining_bytes -= len(sliced)
            current_part += 1

        if current_part > part_count or remaining_bytes <= 0:
            return

        msg = await resolve_telegram_message(bot, file_doc)
        if not msg:
            return

        active_msg = msg
        retried_refresh = False
        while current_part <= part_count and remaining_bytes > 0:
            try:
                async for raw_chunk in bot.stream_media(
                    active_msg,
                    offset=offset_chunks + current_part - 1,
                    limit=part_count - current_part + 1,
                ):
                    if request is not None and await request.is_disconnected():
                        return
                    if not raw_chunk:
                        break
                    chunk = bytes(raw_chunk) if not isinstance(raw_chunk, bytes) else raw_chunk
                    abs_chunk_idx = offset_chunks + current_part - 1
                    is_boundary = abs_chunk_idx in (0, 1, max(0, last_chunk_index - 1), last_chunk_index)
                    if is_boundary and len(chunk) <= CHUNK_SIZE:
                        await set_cached_chunk(file_id, abs_chunk_idx, chunk, is_boundary=True)

                    sliced = _slice_chunk(chunk, current_part, part_count, first_part_cut, last_part_cut)
                    if len(sliced) > remaining_bytes:
                        sliced = sliced[:remaining_bytes]

                    if current_part == 1 and len(sliced) > FIRST_FRAME_SLICE:
                        yield sliced[:FIRST_FRAME_SLICE]
                        remaining_bytes -= FIRST_FRAME_SLICE
                        rest = sliced[FIRST_FRAME_SLICE:]
                        if rest:
                            yield rest
                            remaining_bytes -= len(rest)
                    elif sliced:
                        yield sliced
                        remaining_bytes -= len(sliced)

                    current_part += 1
                    if current_part > part_count or remaining_bytes <= 0:
                        break
                break
            except asyncio.CancelledError:
                raise
            except Exception as inner_err:
                err_txt = f"{type(inner_err).__name__} {inner_err}".upper()
                if not retried_refresh:
                    retried_refresh = True
                    if "AUTH_BYTES_INVALID" in err_txt or "AUTHBYTESINVALID" in err_txt or "AUTH_KEY_UNREGISTERED" in err_txt:
                        reset_fn = getattr(bot, "reset_media_sessions", None)
                        if callable(reset_fn):
                            await reset_fn()
                        else:
                            from plugins.core_services import _clear_stale_media_sessions
                            await _clear_stale_media_sessions(bot)
                        await asyncio.sleep(0.2)
                    refreshed = await resolve_telegram_message(bot, file_doc, force_refresh=True)
                    if refreshed is not None:
                        active_msg = refreshed
                    continue
                logger.error(f"Telegram Stream direct error ({file_id}): {inner_err}")
                raise RuntimeError("Telegram media stream failed") from inner_err


class SafeStreamingResponse(StreamingResponse):
    async def stream_response(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        try:
            async for chunk in self.body_iterator:
                if not isinstance(chunk, (bytes, memoryview)):
                    chunk = chunk.encode(self.charset)
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except (RuntimeError, ConnectionResetError, BrokenPipeError, asyncio.CancelledError) as exc:
            msg = str(exc)
            if "Response content shorter than Content-Length" in msg or isinstance(exc, (ConnectionResetError, BrokenPipeError, asyncio.CancelledError)):
                logger.debug("Stream closed early before full Content-Length: %s", msg)
                return
            raise
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    pass


async def _prime_stream_iterator(iterator: AsyncGenerator[bytes, None]):
    first_chunk = None
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            await aclose()
        return None
    except Exception:
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:
                pass
        return None

    async def _chained():
        try:
            if first_chunk:
                yield first_chunk
            async for chunk in iterator:
                yield chunk
        finally:
            aclose = getattr(iterator, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    pass

    return _chained()


pages_router = APIRouter()
stream_router = APIRouter()
api_router = APIRouter(prefix="/api")

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_CURRENT_DIR)

_CANDIDATE_TEMPLATE_DIRS = [
    _CURRENT_DIR,
    os.path.join(_CURRENT_DIR, "templates"),
    os.path.join(_ROOT_DIR, "app", "web", "templates"),
]
_TEMPLATE_DIR = _CURRENT_DIR
for _cand in _CANDIDATE_TEMPLATE_DIRS:
    if os.path.exists(os.path.join(_cand, "watch.html")):
        _TEMPLATE_DIR = _cand
        break

templates = Jinja2Templates(directory=_TEMPLATE_DIR)


def _render_template(request: Request, name: str, context: Dict[str, Any]):
    ctx = dict(context)
    ctx.setdefault("request", request)
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx)
    except TypeError:
        return templates.TemplateResponse(name, ctx)


@pages_router.get("/health")
@pages_router.head("/health")
async def root_health():
    return await get_cached_health_payload()


@pages_router.get("/stats")
@pages_router.head("/stats")
async def root_stats_redirect(request: Request):
    return await index(request)


@pages_router.get("/bridge")
@pages_router.head("/bridge")
@api_router.get("/bridge")
@api_router.head("/bridge")
async def native_gateway_endpoint(request: Request, action: str = "status", id: str = ""):
    act = (action or "status").strip().lower()
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(id or ""))
    base_url = str(request.base_url).rstrip("/")

    if act in ("health", "ping"):
        return {
            "status": "healthy",
            "branch": "MALLU",
            "bridge": "Python-FastAPI-HTTP206-Native",
            "rename_limit_gb": 2,
            "compress_any_size": True,
            "stream_any_size": True,
            "timestamp": float(time.time()),
        }

    if act in ("stream", "download", "watch", "dl", "file", "code") and clean_id:
        return RedirectResponse(url=f"{base_url}/{act}/{clean_id}", status_code=307)

    if act == "info" and clean_id:
        return RedirectResponse(url=f"{base_url}/api/info/{clean_id}", status_code=307)

    return {
        "service": "MMW All-In-One Pro Engine (MALLU Branch)",
        "status": "operational",
        "bridge": "Python-FastAPI-HTTP206-Native",
        "backend": base_url,
        "endpoints": {
            "health": "?action=health",
            "stream": "?action=stream&id={file_id}",
            "download": "?action=download&id={file_id}",
            "watch": "?action=watch&id={file_id}",
            "dl": "?action=dl&id={file_id}",
            "code": "?action=code&id={file_id}",
            "info": "?action=info&id={file_id}",
        },
    }


@pages_router.get("/")
@pages_router.head("/")
async def index(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    users_count, chats_count, files_count = await get_cached_home_stats()
    return _render_template(request, "index.html", {
        "request": request,
        "users_count": users_count,
        "chats_count": chats_count,
        "files_count": files_count,
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })


@pages_router.get("/code/{file_id}")
@pages_router.head("/code/{file_id}")
async def code_preview_page(request: Request, file_id: str):
    import html as _html
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Code file not found or link expired.")
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})
    file_name = file_doc.get("file_name") or "code.py"
    file_size = _safe_metadata_int(file_doc.get("file_size", 0))
    lang = detect_code_language(file_name, file_doc.get("mime_type"))
    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/stream/{file_id}"
    download_url = f"{base_url}/download/{file_id}"
    watch_url = f"{base_url}/watch/{file_id}"
    html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>{_html.escape(file_name)} — MMW Code Pro Studio</title>
<style>
:root {{
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --card-border: rgba(14, 165, 233, 0.28);
    --accent: #0284c7;
    --text-main: #0f172a;
    --text-muted: #475569;
    --surface-soft: #f1f5f9;
    --pre-bg: #f8fafc;
    --badge-bg: #e0f2fe;
}}
html[data-theme="dark"] {{
    --bg: #0b1120;
    --card-bg: #111827;
    --card-border: rgba(56, 189, 248, 0.28);
    --accent: #38bdf8;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --surface-soft: #1e293b;
    --pre-bg: #0f172a;
    --badge-bg: rgba(56, 189, 248, 0.16);
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body.code-studio-body {{
    background: var(--bg);
    color: var(--text-main);
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    min-height: 100vh;
    padding: 24px 16px;
}}
.code-container {{
    max-width: 1120px;
    margin: 0 auto;
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 20px;
    padding: 24px;
    box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
}}
.code-header {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    padding-bottom: 16px;
    border-bottom: 1px solid rgba(15, 23, 42, 0.1);
    margin-bottom: 16px;
}}
.lang-badge {{
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    background: var(--badge-bg);
    color: var(--accent);
    font-size: 0.75rem;
    font-weight: 800;
    margin-bottom: 8px;
}}
.code-header h1 {{ font-size: 1.3rem; margin-bottom: 4px; word-break: break-all; color: var(--text-main); }}
.code-header p {{ color: var(--text-muted); font-size: 0.86rem; }}
.code-actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.btn-primary, .btn-secondary {{
    text-decoration: none;
    padding: 10px 16px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.86rem;
    cursor: pointer;
    border: none;
}}
.btn-primary {{ background: linear-gradient(135deg, #0ea5e9, #4f46e5); color: #ffffff; }}
.btn-secondary {{ background: var(--surface-soft); color: var(--text-main); border: 1px solid var(--card-border); }}
.progress-wrap {{
    margin-bottom: 16px;
    background: var(--pre-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 12px 14px;
}}
.progress-track {{
    width: 100%;
    height: 8px;
    background: rgba(15, 23, 42, 0.1);
    border-radius: 999px;
    overflow: hidden;
    margin: 8px 0;
}}
.progress-fill {{
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #0ea5e9, #10b981);
    transition: width 0.12s linear;
}}
.progress-meta {{ display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--text-muted); }}
.code-pre {{
    background: var(--pre-bg);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 18px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.86rem;
    line-height: 1.55;
    color: var(--text-main);
    max-height: 75vh;
}}
</style>
</head>
<body class="code-studio-body">
<div class="code-container">
  <div class="code-header">
    <div>
      <span class="lang-badge">⚡ {_html.escape(lang.upper())} CODE ENGINE</span>
      <h1>{_html.escape(file_name)}</h1>
      <p>Size: {_html.escape(humanbytes(file_size))} • Instant HTTP 206 Zero-Buffer Stream</p>
    </div>
    <div class="code-actions">
      <button type="button" class="btn-primary" onclick="streamCodeDownload()">⬇️ Stream Save to Disk</button>
      <a href="{_html.escape(download_url)}" class="btn-secondary">📥 Direct Download</a>
      <button type="button" class="btn-secondary" onclick="copyCodeText()">📋 Copy Code</button>
      <button type="button" id="cancelCodeBtn" class="btn-secondary" onclick="cancelCodeStream()" style="display:none;">🛑 Cancel</button>
      <a href="{_html.escape(watch_url)}" class="btn-secondary">🎬 Watch Studio</a>
      <button type="button" id="themeToggleBtn" class="btn-secondary" onclick="toggleSiteTheme()">🌙 Dark</button>
    </div>
  </div>
  <div class="progress-wrap">
    <div class="progress-meta">
      <span id="codeStatusMsg">Status: Idle</span>
      <span id="codeProgressPct">0%</span>
    </div>
    <div class="progress-track"><div id="codeProgressBar" class="progress-fill"></div></div>
  </div>
  <pre id="code-box" class="code-pre">Loading code stream...</pre>
</div>
<script>
function applySiteTheme(theme) {{
  const t = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  try {{ localStorage.setItem("mmw_theme", t); }} catch (e) {{}}
  const btn = document.getElementById("themeToggleBtn");
  if (btn) btn.textContent = t === "dark" ? "☀️ Light" : "🌙 Dark";
}}
function toggleSiteTheme() {{
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  applySiteTheme(cur === "dark" ? "light" : "dark");
}}
(function() {{
  let saved = "light";
  try {{ saved = localStorage.getItem("mmw_theme") || "light"; }} catch (e) {{}}
  applySiteTheme(saved);
}})();
let codeAbortCtrl = null;
const STREAM_URL = {_js_safe_str(stream_url)};
const FILE_NAME = {_js_safe_str(file_name)};
const EXPECTED_SIZE = {max(1, file_size)};

async function loadCodePreview() {{
  const box = document.getElementById("code-box");
  const statusEl = document.getElementById("codeStatusMsg");
  const pctEl = document.getElementById("codeProgressPct");
  const barEl = document.getElementById("codeProgressBar");
  const cancelBtn = document.getElementById("cancelCodeBtn");
  codeAbortCtrl = new AbortController();
  cancelBtn.style.display = "inline-block";
  statusEl.textContent = "Status: Streaming code preview...";
  try {{
    const resp = await fetch(STREAM_URL, {{
      headers: {{ "Range": "bytes=0-262143" }},
      signal: codeAbortCtrl.signal
    }});
    if (!resp.ok && resp.status !== 206) throw new Error("HTTP " + resp.status);
    const total = Number(resp.headers.get("Content-Length")) || Math.min(EXPECTED_SIZE, 262144);
    if (!resp.body || !resp.body.getReader) {{
      const txt = await resp.text();
      box.textContent = txt;
      barEl.style.width = "100%";
      pctEl.textContent = "100%";
      statusEl.textContent = "Status: Completed";
      return;
    }}
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let received = 0;
    let output = "";
    while (true) {{
      const {{ done, value }} = await reader.read();
      if (done) break;
      received += value.byteLength;
      output += decoder.decode(value, {{ stream: true }});
      const pct = Math.min(100, Math.round((received / Math.max(1, total)) * 100));
      barEl.style.width = pct + "%";
      pctEl.textContent = pct + "% (" + received + " B)";
    }}
    output += decoder.decode();
    box.textContent = output;
    barEl.style.width = "100%";
    pctEl.textContent = "100%";
    statusEl.textContent = "Status: Completed (" + received + " bytes streamed)";
  }} catch (e) {{
    if (e && e.name === "AbortError") statusEl.textContent = "Status: Cancelled by user";
    else {{
      statusEl.textContent = "Status: Error (" + e + ")";
      box.textContent = "Preview error: " + e;
    }}
  }} finally {{
    cancelBtn.style.display = "none";
    codeAbortCtrl = null;
  }}
}}

function cancelCodeStream() {{ if (codeAbortCtrl) codeAbortCtrl.abort(); }}

function copyCodeText() {{
  const txt = document.getElementById("code-box").textContent || "";
  if (navigator.clipboard) navigator.clipboard.writeText(txt);
  document.getElementById("codeStatusMsg").textContent = "Status: Copied code to clipboard!";
}}

async function streamCodeDownload() {{
  const statusEl = document.getElementById("codeStatusMsg");
  const pctEl = document.getElementById("codeProgressPct");
  const barEl = document.getElementById("codeProgressBar");
  const cancelBtn = document.getElementById("cancelCodeBtn");
  codeAbortCtrl = new AbortController();
  cancelBtn.style.display = "inline-block";
  statusEl.textContent = "Status: Connecting stream download...";
  try {{
    let writable = null;
    if (window.showSaveFilePicker) {{
      const handle = await window.showSaveFilePicker({{ suggestedName: FILE_NAME }});
      writable = await handle.createWritable();
    }}
    const resp = await fetch(STREAM_URL, {{ signal: codeAbortCtrl.signal }});
    if (!resp.ok && resp.status !== 206) throw new Error("HTTP " + resp.status);
    const total = Number(resp.headers.get("Content-Length")) || EXPECTED_SIZE;
    const reader = resp.body.getReader();
    const chunks = [];
    let received = 0;
    const t0 = performance.now();
    while (true) {{
      const {{ done, value }} = await reader.read();
      if (done) break;
      if (writable) await writable.write(value);
      else chunks.push(value);
      received += value.byteLength;
      const pct = Math.min(100, Math.round((received / Math.max(1, total)) * 100));
      const elapsed = Math.max(0.001, (performance.now() - t0) / 1000);
      const speedKbps = ((received / 1024) / elapsed).toFixed(1);
      barEl.style.width = pct + "%";
      pctEl.textContent = pct + "% • " + speedKbps + " KB/s";
      statusEl.textContent = "Status: Streaming to disk...";
    }}
    if (writable) {{
      await writable.close();
    }} else {{
      const blob = new Blob(chunks);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = FILE_NAME;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    }}
    statusEl.textContent = "Status: Download completed (" + received + " bytes)";
  }} catch (e) {{
    if (e && e.name === "AbortError") statusEl.textContent = "Status: Download cancelled";
    else statusEl.textContent = "Status: Download error (" + e + ")";
  }} finally {{
    cancelBtn.style.display = "none";
    codeAbortCtrl = null;
  }}
}}

loadCodePreview();
</script>
</body>
</html>"""
    return Response(content=html_page, media_type="text/html; charset=utf-8")


@pages_router.get("/watch/{file_id}")
@pages_router.head("/watch/{file_id}")
async def watch_page(request: Request, file_id: str):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Stream link expired or file not found.")
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})

    file_name = file_doc.get("file_name") or file_doc.get("name") or "Media Stream.mp4"
    file_size = _safe_metadata_int(file_doc.get("file_size") or file_doc.get("size", 0))
    file_size_str = humanbytes(file_size)
    mime_type = _guess_mime_from_name(file_name, file_doc.get("mime_type"))
    file_path = file_doc.get("file_path")
    raw_meta = file_doc.get("metadata") or {}
    metadata = await get_cached_metadata_view(file_doc)
    bot = getattr(request.app.state, "bot", None)

    needs_probe = (
        metadata.get("media_kind") in ("video", "audio")
        and (
            not raw_meta.get("duration")
            or (not raw_meta.get("audio_streams") and not raw_meta.get("audio_tracks"))
        )
    )
    if bot is not None and needs_probe:
        async def _bg_probe_update(fid: str, fpath: Optional[str], meta_base: dict):
            async with _PROBE_SEMAPHORE:
                try:
                    from plugins.core_services import get_detailed_mediainfo, probe_stream_url_mediainfo
                    probed = None
                    if fpath and os.path.exists(fpath):
                        probed = await get_detailed_mediainfo(fpath)
                    else:
                        local_stream = f"http://127.0.0.1:{Config.PORT}/stream/{fid}"
                        probed = await probe_stream_url_mediainfo(local_stream, timeout=8.0)
                    if probed:
                        meta_base.update({
                            "duration": probed.get("duration") or meta_base.get("duration", 0),
                            "audio_tracks": probed.get("audio_streams") or meta_base.get("audio_tracks", []),
                            "subtitle_tracks": probed.get("subtitle_streams") or meta_base.get("subtitle_tracks", []),
                            "language": probed.get("language") or meta_base.get("language", "Undetermined"),
                            "audio_track": probed.get("audio_track") or meta_base.get("audio_track", "Undetermined"),
                        })
                        if probed.get("video_streams"):
                            meta_base["width"] = probed["video_streams"][0].get("width", meta_base.get("width", 0))
                            meta_base["height"] = probed["video_streams"][0].get("height", meta_base.get("height", 0))
                        await file_repo.update_file(fid, {"metadata": meta_base})
                        await invalidate_file_cache(fid)
                except Exception:
                    pass
        if not await cache.get(f"probe_lock_{file_id}"):
            await cache.set(f"probe_lock_{file_id}", True, ttl=25)
            async def _delayed_probe():
                if not (file_path and os.path.exists(file_path)):
                    await asyncio.sleep(0.35)
                await _bg_probe_update(file_id, file_path, dict(raw_meta))
            asyncio.create_task(_delayed_probe())

    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/stream/{file_id}"
    download_url = f"{base_url}/download/{file_id}"
    dl_page_url = f"{base_url}/dl/{file_id}"
    player_links = build_4_external_player_links(stream_url, file_name)

    response = _render_template(request, "watch.html", {
        "request": request,
        "file_id": file_id,
        "file_name": file_name,
        "file_size": file_size_str,
        "file_size_bytes": file_size,
        "mime_type": mime_type,
        "metadata": metadata,
        "stream_url": stream_url,
        "download_url": download_url,
        "dl_page_url": dl_page_url,
        "player_links": player_links,
        "stream_speed": "ultra-fast HTTP 206 zero-buffer streaming",
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    return response


@pages_router.get("/dl/{file_id}")
@pages_router.head("/dl/{file_id}")
async def download_page(request: Request, file_id: str):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found or link expired.")
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})

    file_name = file_doc.get("file_name", "download.mp4")
    metadata = await get_cached_metadata_view(file_doc)
    file_size_bytes = _safe_metadata_int(file_doc.get("file_size", 0))
    file_size = humanbytes(file_size_bytes)
    mime_type = metadata.get("mime_type") or _guess_mime_from_name(file_name, file_doc.get("mime_type"))
    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/stream/{file_id}"
    download_url = f"{base_url}/download/{file_id}"
    watch_url = f"{base_url}/watch/{file_id}"
    player_links = build_4_external_player_links(stream_url, file_name)

    response = _render_template(request, "dl.html", {
        "request": request,
        "file_id": file_id,
        "file_name": file_name,
        "file_size": file_size,
        "file_size_bytes": file_size_bytes,
        "mime_type": mime_type,
        "metadata": metadata,
        "stream_url": stream_url,
        "download_url": download_url,
        "watch_url": watch_url,
        "player_links": player_links,
        "stream_speed": "ultra-fast HTTP 206 zero-buffer streaming",
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    return response


@stream_router.get("/stream/{file_id}")
@stream_router.head("/stream/{file_id}")
async def stream_media_endpoint(request: Request, file_id: str, range: str = Header(None)):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Media file not found or revoked.")

    file_path = file_doc.get("file_path")
    bot = getattr(request.app.state, "bot", None)

    if file_path and os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
    else:
        file_size = _safe_metadata_int(file_doc.get("file_size", 0))

    if file_size <= 0:
        raise HTTPException(status_code=404, detail="File size invalid or source unavailable.")

    file_name = file_doc.get("file_name", "video.mp4")
    mime_type = _guess_mime_from_name(file_name, file_doc.get("mime_type"))
    etag = compute_etag(file_id, file_size)

    if not range and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    start, end = parsed_range
    status_code = 206 if range else 200
    content_length = (end - start) + 1

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Content-Disposition": content_disposition("inline", file_name),
        "ETag": etag,
        "X-Stream-Mode": "zero-buffer-http-206-byte-range",
        "X-Stream-Chunk-Size": str(CHUNK_SIZE),
        "X-Accel-Buffering": "no",
        "Cache-Control": "public, max-age=86400, no-transform",
        "Connection": "keep-alive",
        "Vary": "Range",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag",
    }

    if range:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers)

    primed = await _prime_stream_iterator(iter_12mbps_file_stream(file_path, bot, file_doc, start, end, request))
    if primed is None:
        raise HTTPException(status_code=503, detail="Upstream media stream temporarily unavailable. Please retry.")

    return SafeStreamingResponse(
        primed,
        status_code=status_code,
        headers=headers,
        media_type=mime_type,
    )


@stream_router.get("/download/{file_id}")
@stream_router.get("/file/{file_id}")
@stream_router.head("/download/{file_id}")
@stream_router.head("/file/{file_id}")
async def download_media_endpoint(request: Request, file_id: str, range: str = Header(None)):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found or revoked.")

    file_path = file_doc.get("file_path")
    bot = getattr(request.app.state, "bot", None)

    if file_path and os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
    else:
        file_size = _safe_metadata_int(file_doc.get("file_size", 0))

    if file_size <= 0:
        raise HTTPException(status_code=404, detail="File missing on server.")

    file_name = file_doc.get("file_name", "download.mp4")
    dl_mime = _guess_mime_from_name(file_name, file_doc.get("mime_type"))
    etag = compute_etag(file_id, file_size)

    if not range and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    start, end = parsed_range
    status_code = 206 if range else 200
    content_length = (end - start) + 1

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": dl_mime,
        "Content-Length": str(content_length),
        "Content-Disposition": content_disposition("attachment", file_name),
        "ETag": etag,
        "X-Stream-Mode": "zero-buffer-http-206-byte-range",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag",
        "Cache-Control": "public, max-age=86400, no-transform",
        "Connection": "keep-alive",
        "Vary": "Range",
    }

    if range:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers)

    primed = await _prime_stream_iterator(iter_12mbps_file_stream(file_path, bot, file_doc, start, end, request))
    if primed is None:
        raise HTTPException(status_code=503, detail="Upstream download stream temporarily unavailable. Please retry.")

    return SafeStreamingResponse(
        primed,
        status_code=status_code,
        media_type=dl_mime,
        headers=headers,
    )


@api_router.get("/status")
@api_router.head("/status")
async def api_status(request: Request):
    (users_count, chats_count, files_count), cache_stats = await asyncio.gather(
        get_cached_home_stats(),
        cache.get_stats(),
    )
    start_time = getattr(request.app.state, "start_time", float(time.time()))
    uptime_sec = int(float(time.time()) - start_time)

    return {
        "status": "operational",
        "service": "MMW All-In-One Pro Engine",
        "version": "8.0.0-ultra-max",
        "stream_speed": "turbo HTTP 206 zero-buffer streaming",
        "external_players": ["PLAYit", "MX Player", "VLC", "S Player"],
        "uptime_seconds": uptime_sec,
        "users": users_count,
        "chats": chats_count,
        "files": files_count,
        "cache": cache_stats,
        "auto_cache_clear_interval_sec": AUTO_CACHE_CLEAR_INTERVAL,
        "server_time": float(time.time()),
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    }


@api_router.get("/health")
@api_router.head("/health")
async def api_health():
    return {
        "status": "healthy",
        "service": "MMW-BOT-PRO",
        "engine": "zero-buffer asynchronous HTTP 206 byte-range streamer",
        "timestamp": float(time.time()),
        "watermark": Config.WATERMARK,
    }


@api_router.get("/stats")
async def api_system_stats(request: Request):
    return await api_status(request)


@api_router.get("/ping")
@api_router.head("/ping")
async def api_ping():
    return {
        "status": "ok",
        "pong": True,
        "timestamp": float(time.time()),
    }


@api_router.get("/info/{file_id}")
@api_router.head("/info/{file_id}")
async def api_file_info(file_id: str, fresh: int = 0):
    payload = await get_cached_api_info(file_id, force_refresh=bool(fresh))
    if not payload:
        raise HTTPException(status_code=404, detail="File not found")
    return payload


@api_router.get("/user/{user_id}/files")
async def api_user_files(user_id: int, skip: int = 0, limit: int = 20):
    lim = max(1, min(50, int(limit)))
    sk = max(0, int(skip))
    return await get_cached_user_files(user_id, sk, lim)


@api_router.get("/logs/summary")
async def api_logs_summary():
    from utils import get_log_analytics
    return get_log_analytics()


@api_router.get("/tasks/report")
async def api_tasks_report():
    from plugins.compressor import generate_task_execution_report
    return await generate_task_execution_report()


@api_router.get("/ratelimit")
async def api_ratelimit_status():
    from plugins.core_utils import rate_limit_manager
    return rate_limit_manager.get_stats()


async def probe_and_cache_media_tracks(file_id: str, file_doc: Dict[str, Any], bot: Any) -> Dict[str, Any]:
    meta = await get_cached_metadata_view(file_doc)
    raw_meta = dict(file_doc.get("metadata") or {})
    already_probed = bool(
        await cache.get(f"tracks_probed_{file_id}")
        or raw_meta.get("audio_tracks")
        or raw_meta.get("audio_streams")
        or raw_meta.get("subtitle_tracks")
        or raw_meta.get("subtitle_streams")
    )
    if already_probed or meta.get("media_kind") in ("image", "text", "code", "pdf"):
        return {**meta, "probed": True}

    fpath = file_doc.get("file_path")
    if bot is None and not (fpath and os.path.exists(fpath)):
        return {**meta, "probed": False}

    async with _PROBE_SEMAPHORE:
        if await cache.get(f"tracks_probed_{file_id}"):
            fresh_doc = await get_cached_file_doc(file_id)
            fresh_meta = await get_cached_metadata_view(fresh_doc or file_doc)
            return {**fresh_meta, "probed": True}
        try:
            from plugins.core_services import get_detailed_mediainfo, probe_stream_url_mediainfo
            probed = None
            if fpath and os.path.exists(fpath):
                probed = await get_detailed_mediainfo(fpath)
            else:
                local_stream = f"http://127.0.0.1:{Config.PORT}/stream/{file_id}"
                probed = await probe_stream_url_mediainfo(local_stream, timeout=14.0)
            if probed:
                raw_meta.update({
                    "duration": probed.get("duration") or raw_meta.get("duration", 0),
                    "audio_tracks": probed.get("audio_streams") or raw_meta.get("audio_tracks", []),
                    "subtitle_tracks": probed.get("subtitle_streams") or raw_meta.get("subtitle_tracks", []),
                    "language": probed.get("language") or raw_meta.get("language", "Undetermined"),
                    "audio_track": probed.get("audio_track") or raw_meta.get("audio_track", "Undetermined"),
                })
                if probed.get("video_streams"):
                    raw_meta["width"] = probed["video_streams"][0].get("width", raw_meta.get("width", 0))
                    raw_meta["height"] = probed["video_streams"][0].get("height", raw_meta.get("height", 0))
                await file_repo.update_file(file_id, {"metadata": raw_meta})
                await invalidate_file_cache(file_id)
                updated_doc = {**file_doc, "metadata": raw_meta}
                await cache.set(f"tracks_probed_{file_id}", True, ttl=180)
                return {**media_metadata_view(updated_doc), "probed": True}
        except Exception:
            pass
    return {**meta, "probed": False}


@api_router.get("/tracks/{file_id}")
async def api_media_tracks(request: Request, file_id: str):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    bot = getattr(request.app.state, "bot", None)
    data = await probe_and_cache_media_tracks(file_id, file_doc, bot)
    return {
        "file_id": file_id,
        "probed": bool(data.get("probed", False)),
        "metadata": data,
    }


@api_router.get("/audio/{file_id}/{audio_idx}")
async def api_audio_track_stream(request: Request, file_id: str, audio_idx: int, t: float = 0.0):
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Audio source not found")
    from plugins.core_services import FFMPEG_BIN, has_ffmpeg
    if not has_ffmpeg():
        raise HTTPException(status_code=503, detail="Audio track switcher unavailable")
    fpath = file_doc.get("file_path")
    source = fpath if (fpath and os.path.exists(fpath)) else f"http://127.0.0.1:{Config.PORT}/stream/{file_id}"
    seek_sec = max(0.0, float(t or 0.0))
    idx = max(0, int(audio_idx))
    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-fflags", "+nobuffer+discardcorrupt", "-flags", "low_delay",
    ]
    if str(source).startswith(("http://", "https://")):
        cmd.extend(["-rw_timeout", "10000000"])
    if seek_sec > 0.05:
        cmd.extend(["-ss", f"{seek_sec:.3f}"])
    meta_view = await get_cached_metadata_view(file_doc)
    a_tracks = meta_view.get("audio_tracks") or []
    trk_channels = int(a_tracks[idx].get("channels", 0)) if (0 <= idx < len(a_tracks)) else 0
    if trk_channels >= 6:
        downmix_af = "aresample=async=1:first_pts=0,pan=stereo|FL=0.5*FC+0.707*FL+0.707*BL+0.5*LFE|FR=0.5*FC+0.707*FR+0.707*BR+0.5*LFE"
    else:
        downmix_af = "aresample=async=1:first_pts=0"
    cmd.extend([
        "-i", source,
        "-map", f"0:a:{idx}",
        "-vn", "-sn", "-dn",
        "-af", downmix_af,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-f", "mp3",
        "pipe:1",
    ])

    async def _audio_gen():
        async with _AUDIO_REMUX_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    await proc.wait()
                except Exception:
                    pass

    return StreamingResponse(
        _audio_gen(),
        media_type="audio/mpeg",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
    )


@api_router.get("/subtitles/{file_id}/{sub_idx}.vtt")
async def api_subtitle_vtt(file_id: str, sub_idx: int):
    cache_key = f"sub_vtt_{file_id}_{sub_idx}"
    cached_vtt = await cache.get(cache_key)
    vtt_headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=300",
        "X-Subtitle-Status": "ok",
    }
    if cached_vtt is not None:
        return Response(content=cached_vtt, media_type="text/vtt; charset=utf-8", headers=vtt_headers)
    file_doc = await get_cached_file_doc(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Subtitle source not found")
    meta = await get_cached_metadata_view(file_doc)
    subs = meta.get("subtitle_tracks") or []
    if 0 <= sub_idx < len(subs) and subs[sub_idx].get("is_text_subtitle") is False:
        return Response(
            content="WEBVTT\n\n",
            media_type="text/vtt; charset=utf-8",
            headers={**vtt_headers, "X-Subtitle-Status": "bitmap_unsupported"},
        )
    fpath = file_doc.get("file_path")
    source = fpath if (fpath and os.path.exists(fpath)) else f"http://127.0.0.1:{Config.PORT}/stream/{file_id}"
    from plugins.core_services import extract_webvtt_subtitle
    async with _SUB_SEMAPHORE:
        vtt = await extract_webvtt_subtitle(source, sub_index=sub_idx, timeout=20.0)
    if vtt and len(vtt.strip()) > 10:
        await cache.set(cache_key, vtt, ttl=600)
        return Response(content=vtt, media_type="text/vtt; charset=utf-8", headers=vtt_headers)
    empty_vtt = "WEBVTT\n\n"
    await cache.set(cache_key, empty_vtt, ttl=5, transient=True)
    return Response(
        content=empty_vtt,
        media_type="text/vtt; charset=utf-8",
        headers={**vtt_headers, "X-Subtitle-Status": "empty"},
    )


@api_router.get("/search")
async def api_search_files(q: str = ""):
    if not q or not q.strip():
        return {"query": "", "count": 0, "results": []}
    results = await get_cached_search_results(q.strip())
    return {"query": q, "count": len(results), "results": results}


def create_app(bot_instance=None) -> FastAPI:
    fastapi_app = FastAPI(
        title="MMW All-In-One Pro Turbo Stream Server",
        version="8.0.0",
        docs_url="/docs",
        redoc_url=None,
    )
    fastapi_app.state.bot = bot_instance
    fastapi_app.state.start_time = float(time.time())

    @fastapi_app.on_event("startup")
    async def _startup_cache_cleaner():
        start_auto_cache_cleaner()

    @fastapi_app.on_event("shutdown")
    async def _shutdown_cache_cleaner():
        await stop_auto_cache_cleaner()

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class SelectiveGZipMiddleware:
        def __init__(self, app, minimum_size: int = 500):
            self.app = app
            self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path.startswith(("/stream/", "/download/", "/file/")):
                    return await self.app(scope, receive, send)
            return await self.gzip(scope, receive, send)

    fastapi_app.add_middleware(SelectiveGZipMiddleware, minimum_size=500)

    class PureASGICacheHeadersMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            path = scope.get("path", "")

            async def _send_with_headers(message):
                if message["type"] == "http.response.start":
                    raw_headers = list(message.get("headers", []))
                    existing = {k.lower() for k, _ in raw_headers}
                    if b"cache-control" not in existing:
                        if path in {"/", "/stats"}:
                            raw_headers.append((b"cache-control", b"public, max-age=30, stale-while-revalidate=300"))
                        elif path.startswith(("/watch/", "/dl/", "/code/")):
                            raw_headers.append((b"cache-control", b"public, max-age=30, stale-while-revalidate=120"))
                        elif path.startswith("/api/info/"):
                            raw_headers.append((b"cache-control", b"private, max-age=10"))
                        elif path.startswith("/api/search"):
                            raw_headers.append((b"cache-control", b"private, max-age=10"))
                        elif path == "/api/status":
                            raw_headers.append((b"cache-control", b"private, max-age=5, stale-while-revalidate=15"))
                    if b"x-content-type-options" not in existing:
                        raw_headers.append((b"x-content-type-options", b"nosniff"))
                    if b"referrer-policy" not in existing:
                        raw_headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                    message = {**message, "headers": raw_headers}
                await send(message)

            async with cache.response_scope(f"http:{path}"):
                await self.app(scope, receive, _send_with_headers)

    fastapi_app.add_middleware(PureASGICacheHeadersMiddleware)

    fastapi_app.include_router(pages_router)
    fastapi_app.include_router(stream_router)
    fastapi_app.include_router(api_router)

    return fastapi_app


class WebSupport:
    def __init__(self, bot=None):
        self.bot = bot
        self.start_time = float(time.time())
        self.app = create_app(bot)
        self.app.state.start_time = self.start_time
        self.server = None

    def get_server(self) -> uvicorn.Server:
        config = uvicorn.Config(
            app=self.app,
            host=Config.BIND_ADDRESS,
            port=Config.PORT,
            log_level="warning",
            access_log=False,
            timeout_keep_alive=65,
            limit_concurrency=2000,
            backlog=2048,
            server_header=False,
            date_header=False,
        )
        self.server = uvicorn.Server(config)
        return self.server

    async def start(self):
        server = self.get_server()
        logger.info(f"⚡ Turbo Web Stream Server active on {Config.BIND_ADDRESS}:{Config.PORT}")
        await server.serve()
