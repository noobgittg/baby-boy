import os
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
from fastapi.responses import StreamingResponse, Response
from fastapi.templating import Jinja2Templates

try:
    import aiofiles
except ImportError:
    aiofiles = None

from config import Config
from plugins.core_utils import logger, cache, humanbytes, time_formatter
from plugins.core_db import user_repo, chat_repo, file_repo, FileRepository
from plugins.media_types import media_kind, normalize_mime, browser_media_supported

# ==================== 1. ULTRA-FAST ASYNC BYTESTREAMER ENGINE ====================

CHUNK_SIZE = 1024 * 1024  # 1 MiB native Telegram MTProto chunk size
FIRST_FRAME_SLICE = 256 * 1024  # 256 KiB micro-slices on first chunk for <150ms TTFB
PREFETCH_QUEUE_SIZE = max(16, int(getattr(Config, "STREAM_BUFFER_CHUNKS", 64)))
LOCAL_READ_CHUNK = max(CHUNK_SIZE, int(getattr(Config, "STREAM_CHUNK_SIZE", 4 * 1024 * 1024)))

# Per-file lock to prevent duplicate concurrent bot.get_messages calls when external players open 4-8 range sockets
_MSG_RESOLVE_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def parse_range_header(value: Optional[str], file_size: int):
    """Parse HTTP Range header, including open-ended and suffix byte ranges."""
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


# External 4 Players configuration:
# 1. PLAYit  2. MX Player  3. VLC  4. S Player
FOUR_EXTERNAL_PLAYERS = [
    {"id": "playit", "name": "PLAYit", "badge": "Turbo Range"},
    {"id": "mxplayer", "name": "MX Player", "badge": "HW+ Decoder"},
    {"id": "vlc", "name": "VLC Player", "badge": "All Codecs"},
    {"id": "splayer", "name": "S Player", "badge": "Ultra Smooth"},
]


def build_4_external_player_links(stream_url: str, file_name: str = "video.mp4") -> Dict[str, str]:
    """
    Generates intent and scheme deep-links for the 4 external players:
    1. PLAYit
    2. MX Player (Ad & Pro with HW+ decode_mode=2)
    3. VLC (Android Intent & Universal vlc:// scheme)
    4. S Player (Simple Player & SPlayer fallback)
    """
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
    """Normalize stored metadata for templates and JSON clients."""
    raw = dict(file_doc.get("metadata") or {})
    return {
        "media_kind": raw.get("media_kind") or media_kind(file_doc.get("mime_type") or raw.get("mime_type")),
        "mime_type": normalize_mime(file_doc.get("mime_type") or raw.get("mime_type")),
        "browser_supported": browser_media_supported(file_doc.get("mime_type") or raw.get("mime_type")),
        "duration": _safe_metadata_int(raw.get("duration")),
        "width": _safe_metadata_int(raw.get("width")),
        "height": _safe_metadata_int(raw.get("height")),
        "language": raw.get("language") or "Undetermined",
        "audio_track": raw.get("audio_track") or "Default Audio",
        "audio_tracks": raw.get("audio_tracks") or raw.get("audio_streams") or [],
        "subtitle_tracks": raw.get("subtitle_tracks") or raw.get("subtitle_streams") or [],
        "caption": raw.get("caption") or "",
    }


async def resolve_telegram_message(bot: Any, file_doc: Dict[str, Any]):
    """Fetches and caches the Telegram message object with stampede lock protection."""
    file_id = str(file_doc.get("file_id", ""))
    cache_key = f"tg_msg_{file_id}"
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
        cached_msg = await cache.get(cache_key)
        if cached_msg is not None:
            return cached_msg
        try:
            msg = await bot.get_messages(int(chat_id), int(message_id))
            if msg and msg.media:
                await cache.set(cache_key, msg, ttl=1800)
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
    """
    Ultra-Fast Zero-Blocking Async Byte Streamer:
    1. If local file exists on disk (`file_path`), streams asynchronously via `aiofiles` in 4 MiB chunks (with 256 KiB initial frame flush).
    2. Otherwise, streams directly from Telegram (`bot.stream_media`) using a 64-chunk prefetch buffer,
       caching boundary chunks (first & last 1 MiB for MP4/MKV moov/cues headers) in RAM so external player seeking is instant!
    """
    content_length = (end - start) + 1
    file_id = str(file_doc.get("file_id", ""))
    total_file_size = _safe_metadata_int(file_doc.get("file_size", 0))
    last_chunk_index = max(0, (total_file_size - 1) // CHUNK_SIZE) if total_file_size > 0 else -1

    # Route A: Local disk file (zero-blocking async I/O)
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
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    if request is not None and await request.is_disconnected():
                        return
                    read_size = min(LOCAL_READ_CHUNK, remaining)
                    data = await asyncio.to_thread(f.read, read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        return

    offset_chunks = start // CHUNK_SIZE
    first_part_cut = start % CHUNK_SIZE
    last_part_cut = (end % CHUNK_SIZE) + 1
    part_count = math.ceil((end + 1) / CHUNK_SIZE) - offset_chunks

    # Fast-path L1 RAM Cache Hit if single-chunk probe (e.g. external player probing MP4 header/footer moov atom)
    if part_count == 1 and offset_chunks in (0, last_chunk_index):
        cached_raw = await cache.get(f"chunk_{file_id}_{offset_chunks}")
        if cached_raw is not None:
            sliced = _slice_chunk(cached_raw, 1, 1, first_part_cut, last_part_cut)
            for idx in range(0, len(sliced), FIRST_FRAME_SLICE):
                yield sliced[idx : idx + FIRST_FRAME_SLICE]
            return

    # Route B: Direct Telegram MTProto Streaming via Pyrofork stream_media with 64-chunk prefetch buffer
    msg = await resolve_telegram_message(bot, file_doc)
    if not msg:
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=PREFETCH_QUEUE_SIZE)
    producer_error: List[Exception] = []

    async def _producer():
        try:
            current_part = 1
            async for chunk in bot.stream_media(msg, offset=offset_chunks, limit=part_count):
                if not chunk:
                    break
                abs_chunk_idx = offset_chunks + current_part - 1
                if abs_chunk_idx in (0, last_chunk_index) and len(chunk) <= CHUNK_SIZE:
                    await cache.set(f"chunk_{file_id}_{abs_chunk_idx}", chunk, ttl=900)

                sliced = _slice_chunk(chunk, current_part, part_count, first_part_cut, last_part_cut)
                if current_part == 1 and len(sliced) > FIRST_FRAME_SLICE:
                    # Micro-flush first chunk in 256 KiB slices for instant video startup
                    for sub_i in range(0, len(sliced), FIRST_FRAME_SLICE):
                        await queue.put(sliced[sub_i : sub_i + FIRST_FRAME_SLICE])
                else:
                    await queue.put(sliced)

                current_part += 1
                if current_part > part_count:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            producer_error.append(e)
            logger.error(f"Telegram Stream producer error ({file_id}): {e}")
        finally:
            await queue.put(None)

    producer_task = asyncio.create_task(_producer())
    remaining = content_length
    try:
        while remaining > 0:
            if request is not None and await request.is_disconnected():
                break
            item = await queue.get()
            if item is None:
                if producer_error:
                    raise RuntimeError("Telegram media stream failed") from producer_error[0]
                break
            if len(item) > remaining:
                item = item[:remaining]
            remaining -= len(item)
            yield item
    finally:
        if not producer_task.done():
            producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass


# ==================== 2. FASTAPI ROUTERS ====================

pages_router = APIRouter()
stream_router = APIRouter()
api_router = APIRouter(prefix="/api")

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_CURRENT_DIR)

# Resolve HTML templates directory cleanly (`plugins/` contains index.html, watch.html, dl.html)
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


# --- HEALTH & ROOT ENDPOINTS ---

@pages_router.get("/health")
@pages_router.head("/health")
async def root_health():
    """Root /health endpoint required by Render/Koyeb health checks and KeepAlive pinger."""
    return {
        "status": "healthy",
        "service": "MMW-BOT-PRO",
        "stream_speed": "ultra-fast HTTP 206 byte-range streaming",
        "external_players": 4,
        "timestamp": float(time.time()),
        "watermark": Config.WATERMARK,
    }


@pages_router.get("/stats")
async def root_stats_redirect(request: Request):
    return await index(request)


@pages_router.get("/")
@pages_router.head("/")
async def index(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    cached_stats = await cache.get("web_home_stats")
    if cached_stats:
        users_count, chats_count, files_count = cached_stats
    else:
        users_count, chats_count, files_count = await asyncio.gather(
            user_repo.get_total_users(),
            chat_repo.get_total_chats(),
            file_repo.get_total_files(),
        )
        await cache.set("web_home_stats", (users_count, chats_count, files_count), ttl=15)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "users_count": users_count,
        "chats_count": chats_count,
        "files_count": files_count,
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })


@pages_router.get("/watch/{file_id}")
async def watch_page(request: Request, file_id: str):
    file_doc = await FileRepository.get_file_by_id(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Stream link expired or file not found.")

    file_name = file_doc.get("file_name") or file_doc.get("name") or "Media Stream.mp4"
    file_size = _safe_metadata_int(file_doc.get("file_size") or file_doc.get("size", 0))
    file_size_str = humanbytes(file_size)
    mime_type = file_doc.get("mime_type") or "video/mp4"
    file_path = file_doc.get("file_path")
    raw_meta = file_doc.get("metadata") or {}
    if file_path and os.path.exists(file_path) and not raw_meta.get("audio_streams") and not raw_meta.get("audio_tracks"):
        async def _bg_probe_update(fid: str, fpath: str, meta_base: dict):
            try:
                from plugins.core_services import get_detailed_mediainfo
                probed = await get_detailed_mediainfo(fpath)
                if probed:
                    meta_base.update({
                        "duration": probed.get("duration") or meta_base.get("duration", 0),
                        "audio_tracks": probed.get("audio_streams", []),
                        "subtitle_tracks": probed.get("subtitle_streams", []),
                    })
                    if probed.get("video_streams"):
                        meta_base["width"] = probed["video_streams"][0].get("width", meta_base.get("width", 0))
                        meta_base["height"] = probed["video_streams"][0].get("height", meta_base.get("height", 0))
                    await file_repo.update_file(fid, {"metadata": meta_base})
            except Exception:
                pass
        asyncio.create_task(_bg_probe_update(file_id, file_path, dict(raw_meta)))
    metadata = media_metadata_view(file_doc)

    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/stream/{file_id}"
    download_url = f"{base_url}/download/{file_id}"
    dl_page_url = f"{base_url}/dl/{file_id}"
    player_links = build_4_external_player_links(stream_url, file_name)

    response = templates.TemplateResponse("watch.html", {
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
        "stream_speed": "ultra-fast HTTP 206 byte-range streaming",
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return response


@pages_router.get("/dl/{file_id}")
async def download_page(request: Request, file_id: str):
    file_doc = await FileRepository.get_file_by_id(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found or link expired.")

    file_name = file_doc.get("file_name", "download.mp4")
    metadata = media_metadata_view(file_doc)
    file_size = humanbytes(_safe_metadata_int(file_doc.get("file_size", 0)))
    mime_type = file_doc.get("mime_type", "video/mp4")
    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/stream/{file_id}"
    download_url = f"{base_url}/download/{file_id}"
    watch_url = f"{base_url}/watch/{file_id}"
    player_links = build_4_external_player_links(stream_url, file_name)

    response = templates.TemplateResponse("dl.html", {
        "request": request,
        "file_id": file_id,
        "file_name": file_name,
        "file_size": file_size,
        "mime_type": mime_type,
        "metadata": metadata,
        "stream_url": stream_url,
        "download_url": download_url,
        "watch_url": watch_url,
        "player_links": player_links,
        "stream_speed": "ultra-fast HTTP 206 byte-range streaming",
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    })
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return response


# --- RANGE STREAM & DOWNLOAD ENDPOINTS ---

@stream_router.get("/stream/{file_id}")
@stream_router.head("/stream/{file_id}")
async def stream_media_endpoint(request: Request, file_id: str, range: str = Header(None)):
    file_doc = await FileRepository.get_file_by_id(file_id)
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
    mime_type = file_doc.get("mime_type") or mimetypes.guess_type(file_name)[0] or "video/mp4"
    etag = compute_etag(file_id, file_size)

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
        "X-Stream-Mode": "turbo-http-206-byte-range",
        "X-Stream-Chunk-Size": str(CHUNK_SIZE),
        "Cache-Control": "public, max-age=86400",
        "Connection": "keep-alive",
        "Vary": "Range",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag",
    }

    if range:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers)

    return StreamingResponse(
        iter_12mbps_file_stream(file_path, bot, file_doc, start, end, request),
        status_code=status_code,
        headers=headers,
        media_type=mime_type,
    )


@stream_router.get("/download/{file_id}")
@stream_router.get("/file/{file_id}")
@stream_router.head("/download/{file_id}")
@stream_router.head("/file/{file_id}")
async def download_media_endpoint(request: Request, file_id: str, range: str = Header(None)):
    """Handles high-speed multi-connection direct file downloads with HTTP 206 Range support."""
    file_doc = await FileRepository.get_file_by_id(file_id)
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
    etag = compute_etag(file_id, file_size)

    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    start, end = parsed_range
    status_code = 206 if range else 200
    content_length = (end - start) + 1

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mimetypes.guess_type(file_name)[0] or "application/octet-stream",
        "Content-Length": str(content_length),
        "Content-Disposition": content_disposition("attachment", file_name),
        "ETag": etag,
        "X-Stream-Mode": "turbo-http-206-byte-range",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag",
        "Cache-Control": "public, max-age=86400",
        "Connection": "keep-alive",
        "Vary": "Range",
    }

    if range:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers)

    return StreamingResponse(
        iter_12mbps_file_stream(file_path, bot, file_doc, start, end, request),
        status_code=status_code,
        media_type="application/octet-stream",
        headers=headers,
    )


# --- API ENDPOINTS ---

@api_router.get("/status")
async def api_status(request: Request):
    users_count, chats_count, files_count, cache_stats = await asyncio.gather(
        user_repo.get_total_users(),
        chat_repo.get_total_chats(),
        file_repo.get_total_files(),
        cache.get_stats(),
    )
    start_time = getattr(request.app.state, "start_time", float(time.time()))
    uptime_sec = int(float(time.time()) - start_time)

    return {
        "status": "operational",
        "service": "MMW All-In-One Pro Engine",
        "version": "7.0.0-pro-turbo",
        "stream_speed": "turbo HTTP 206 byte-range streaming",
        "external_players": ["PLAYit", "MX Player", "VLC", "S Player"],
        "uptime_seconds": uptime_sec,
        "users": users_count,
        "chats": chats_count,
        "files": files_count,
        "cache": cache_stats,
        "server_time": float(time.time()),
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
    }


@api_router.get("/health")
async def api_health():
    return {
        "status": "healthy",
        "service": "MMW-BOT-PRO",
        "engine": "turbo asynchronous HTTP 206 byte-range streamer",
        "timestamp": float(time.time()),
        "watermark": Config.WATERMARK,
    }


@api_router.get("/stats")
async def api_system_stats(request: Request):
    return await api_status(request)


@api_router.get("/ping")
async def api_ping():
    return {
        "status": "ok",
        "pong": True,
        "timestamp": float(time.time()),
    }


@api_router.get("/info/{file_id}")
async def api_file_info(file_id: str):
    file_doc = await file_repo.get_file(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    stream_url = f"{Config.BASE_URL}/stream/{file_id}"
    fname = file_doc.get("file_name", "video.mp4")
    return {
        "file_id": file_doc["file_id"],
        "file_name": fname,
        "file_size": file_doc.get("file_size"),
        "file_size_formatted": humanbytes(file_doc.get("file_size", 0)),
        "mime_type": file_doc.get("mime_type"),
        "created_at": file_doc.get("created_at"),
        "stream_speed": "turbo HTTP 206 byte-range streaming",
        "stream_url": stream_url,
        "watch_url": f"{Config.BASE_URL}/watch/{file_id}",
        "download_url": f"{Config.BASE_URL}/download/{file_id}",
        "direct_download_url": f"{Config.BASE_URL}/file/{file_id}",
        "external_players": build_4_external_player_links(stream_url, fname),
        "metadata": media_metadata_view(file_doc),
        "watermark": Config.WATERMARK,
    }


@api_router.get("/user/{user_id}/files")
async def api_user_files(user_id: int, skip: int = 0, limit: int = 20):
    """Return paginated JSON list of a user's indexed files and 4-player stream links."""
    lim = max(1, min(50, int(limit)))
    sk = max(0, int(skip))
    files, total = await asyncio.gather(
        file_repo.get_user_files(user_id, skip=sk, limit=lim),
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
            "mime_type": f.get("mime_type", "video/mp4"),
            "created_at": f.get("created_at"),
            "watch_url": f"{Config.BASE_URL}/watch/{fid}",
            "download_url": f"{Config.BASE_URL}/download/{fid}",
            "stream_url": s_url,
            "external_players": build_4_external_player_links(s_url, fname),
        })
    return {
        "user_id": user_id,
        "total": total,
        "skip": sk,
        "limit": lim,
        "files": items,
    }


@api_router.get("/logs/summary")
async def api_logs_summary():
    """Return live application log analytics, RateLimitManager, and Task Recovery health."""
    from utils import get_log_analytics
    return get_log_analytics()


@api_router.get("/tasks/report")
async def api_tasks_report():
    """Return persistent task recovery and completion execution report."""
    from plugins.compressor import generate_task_execution_report
    return await generate_task_execution_report()


@api_router.get("/ratelimit")
async def api_ratelimit_status():
    """Return centralized TelegramRateLimitManager telemetry."""
    from plugins.core_utils import rate_limit_manager
    return rate_limit_manager.get_stats()


@api_router.get("/search")
async def api_search_files(q: str = ""):
    if not q or not q.strip():
        return {"query": "", "count": 0, "results": []}
    files = await file_repo.search_files(q.strip(), limit=20)
    results = []
    for f in files:
        fid = f.get("file_id")
        results.append({
            "file_id": fid,
            "file_name": f.get("file_name", "Media"),
            "file_size": f.get("file_size", 0),
            "file_size_formatted": humanbytes(f.get("file_size", 0)),
            "mime_type": f.get("mime_type", "application/octet-stream"),
            "created_at": f.get("created_at"),
            "stream_url": f"{Config.BASE_URL}/stream/{fid}",
            "watch_url": f"{Config.BASE_URL}/watch/{fid}",
            "download_url": f"{Config.BASE_URL}/download/{fid}",
            "direct_download_url": f"{Config.BASE_URL}/file/{fid}",
        })
    return {"query": q, "count": len(results), "results": results}


# ==================== 3. FASTAPI APP FACTORY & WEB SUPPORT ====================

def create_app(bot_instance=None) -> FastAPI:
    fastapi_app = FastAPI(
        title="MMW All-In-One Pro Turbo Stream Server",
        version="7.0.0",
        docs_url="/docs",
        redoc_url=None,
    )
    fastapi_app.state.bot = bot_instance
    fastapi_app.state.start_time = float(time.time())

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

    @fastapi_app.middleware("http")
    async def public_page_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/stats"}:
            response.headers.setdefault("Cache-Control", "public, max-age=60, stale-while-revalidate=600")
        elif path.startswith("/watch/") or path.startswith("/dl/"):
            response.headers.setdefault("Cache-Control", "public, max-age=60, stale-while-revalidate=300")
        elif path.startswith("/api/info/"):
            response.headers.setdefault("Cache-Control", "private, max-age=15")
        elif path.startswith("/api/search"):
            response.headers.setdefault("Cache-Control", "private, max-age=10")
        elif path == "/api/status":
            response.headers.setdefault("Cache-Control", "private, max-age=5, stale-while-revalidate=15")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

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
