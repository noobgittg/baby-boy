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

_MSG_RESOLVE_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_CHUNK_FETCH_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_STREAM_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_STREAMS)


def parse_range_header(value: Optional[str], file_size: int):
    """Parse HTTP Range header, including open-ended and suffix byte ranges."""
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
    file_id = str(file_doc.get("file_id", ""))
    audio_tracks = list(raw.get("audio_tracks") or raw.get("audio_streams") or [])
    subtitle_tracks = []
    for idx, sub in enumerate(raw.get("subtitle_tracks") or raw.get("subtitle_streams") or []):
        item = dict(sub)
        if file_id and not item.get("url"):
            item["url"] = f"/api/subtitles/{file_id}/{idx}.vtt"
        subtitle_tracks.append(item)

    language = raw.get("language") or "Undetermined"
    if (not language or language == "Undetermined") and audio_tracks:
        langs = [
            str(a.get("language") or a.get("lang") or "").strip()
            for a in audio_tracks
            if str(a.get("language") or a.get("lang") or "").strip() not in ("", "Undetermined", "und")
        ]
        if langs:
            language = ", ".join(dict.fromkeys(langs))

    audio_track = raw.get("audio_track") or "Default Audio"
    if (not audio_track or audio_track == "Default Audio") and audio_tracks:
        auds = [
            f"{str(a.get('codec', 'AUDIO')).upper()} {int(a.get('channels', 2) or 2)}ch".strip()
            for a in audio_tracks
        ]
        if auds:
            audio_track = ", ".join(auds)

    return {
        "media_kind": raw.get("media_kind") or media_kind(file_doc.get("mime_type") or raw.get("mime_type"), file_doc.get("file_name")),
        "mime_type": normalize_mime(file_doc.get("mime_type") or raw.get("mime_type")),
        "browser_supported": browser_media_supported(file_doc.get("mime_type") or raw.get("mime_type"), file_doc.get("file_name")),
        "duration": _safe_metadata_int(raw.get("duration")),
        "width": _safe_metadata_int(raw.get("width")),
        "height": _safe_metadata_int(raw.get("height")),
        "language": language,
        "audio_track": audio_track,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
        "caption": raw.get("caption") or "",
    }


async def resolve_telegram_message(bot: Any, file_doc: Dict[str, Any], force_refresh: bool = False):
    """Fetches and caches the Telegram message object with stampede lock protection and expired-reference refresh."""
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

    if len(_MSG_RESOLVE_LOCKS) > 2000:
        _MSG_RESOLVE_LOCKS.clear()

    async with _MSG_RESOLVE_LOCKS[file_id]:
        if not force_refresh:
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
    Ultra-Fast Zero-Blocking Async Byte Streamer with Bounded Concurrency & Backpressure:
    1. If local file exists on disk (`file_path`), streams asynchronously via `aiofiles` in chunks (with 256 KiB initial frame flush).
    2. Otherwise, streams directly from Telegram (`bot.stream_media`) using a bounded prefetch buffer,
       caching boundary chunks (first & last 1 MiB for MP4/MKV moov/cues headers) in RAM so player seeking is responsive.
    """
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
                with open(file_path, "rb") as f:
                    f.seek(start)
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
            return

        offset_chunks = start // CHUNK_SIZE
        first_part_cut = start % CHUNK_SIZE
        last_part_cut = (end % CHUNK_SIZE) + 1
        part_count = math.ceil((end + 1) / CHUNK_SIZE) - offset_chunks

        current_part_start = 1
        remaining_bytes = content_length
        while current_part_start <= part_count:
            abs_idx = offset_chunks + current_part_start - 1
            cached_raw = await cache.get(f"chunk_{file_id}_{abs_idx}")
            if cached_raw is None:
                break
            sliced = _slice_chunk(cached_raw, current_part_start, part_count, first_part_cut, last_part_cut)
            if len(sliced) > remaining_bytes:
                sliced = sliced[:remaining_bytes]
            for idx in range(0, len(sliced), FIRST_FRAME_SLICE):
                if request is not None and await request.is_disconnected():
                    return
                yield sliced[idx : idx + FIRST_FRAME_SLICE]
            remaining_bytes -= len(sliced)
            current_part_start += 1

        if current_part_start > part_count or remaining_bytes <= 0:
            return

        msg = await resolve_telegram_message(bot, file_doc)
        if not msg:
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=PREFETCH_QUEUE_SIZE)
        producer_error: List[Exception] = []

        async def _producer():
            active_msg = msg
            retried_refresh = False
            try:
                current_part = current_part_start
                while current_part <= part_count:
                    try:
                        async for chunk in bot.stream_media(active_msg, offset=offset_chunks + current_part - 1, limit=part_count - current_part + 1):
                            if not chunk:
                                break
                            abs_chunk_idx = offset_chunks + current_part - 1
                            if len(chunk) <= CHUNK_SIZE:
                                is_boundary = abs_chunk_idx in (0, 1, max(0, last_chunk_index - 1), last_chunk_index)
                                await cache.set(f"chunk_{file_id}_{abs_chunk_idx}", chunk, ttl=900 if is_boundary else 300)

                            sliced = _slice_chunk(chunk, current_part, part_count, first_part_cut, last_part_cut)
                            if current_part == current_part_start and len(sliced) > FIRST_FRAME_SLICE:
                                for sub_i in range(0, len(sliced), FIRST_FRAME_SLICE):
                                    await queue.put(sliced[sub_i : sub_i + FIRST_FRAME_SLICE])
                            else:
                                await queue.put(sliced)

                            current_part += 1
                            if current_part > part_count:
                                break
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as inner_err:
                        err_txt = f"{type(inner_err).__name__} {inner_err}".upper()
                        if not retried_refresh and current_part == current_part_start:
                            retried_refresh = True
                            if "AUTH_BYTES_INVALID" in err_txt or "AUTHBYTESINVALID" in err_txt or "AUTH_KEY_UNREGISTERED" in err_txt:
                                reset_fn = getattr(bot, "reset_media_sessions", None)
                                if callable(reset_fn):
                                    await reset_fn()
                                else:
                                    from plugins.core_services import _clear_stale_media_sessions
                                    await _clear_stale_media_sessions(bot)
                                await asyncio.sleep(0.35)
                            refreshed = await resolve_telegram_message(bot, file_doc, force_refresh=True)
                            if refreshed is not None:
                                active_msg = refreshed
                            continue
                        raise inner_err
            except asyncio.CancelledError:
                raise
            except Exception as e:
                producer_error.append(e)
                logger.error(f"Telegram Stream producer error ({file_id}): {e}")
            finally:
                await queue.put(None)

        producer_task = asyncio.create_task(_producer())
        remaining = remaining_bytes
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


async def _prime_stream_iterator(iterator: AsyncGenerator[bytes, None]):
    first_chunk = None
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        return None
    except Exception:
        return None

    async def _chained():
        if first_chunk:
            yield first_chunk
        async for chunk in iterator:
            yield chunk

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
@pages_router.head("/stats")
async def root_stats_redirect(request: Request):
    return await index(request)


@pages_router.get("/bridge")
@pages_router.head("/bridge")
@api_router.get("/bridge")
@api_router.head("/bridge")
async def native_gateway_endpoint(request: Request, action: str = "status", id: str = ""):
    """Native Python HTTP 206 Stream & Telemetry Bridge."""
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


@pages_router.get("/code/{file_id}")
@pages_router.head("/code/{file_id}")
async def code_preview_page(request: Request, file_id: str):
    import html as _html
    file_doc = await FileRepository.get_file_by_id(file_id)
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
<html lang="en" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html.escape(file_name)} — MMW Code Pro Studio</title>
<style>
:root {{
    --bg: #040711;
    --card-bg: rgba(11, 18, 36, 0.88);
    --card-border: rgba(56, 189, 248, 0.25);
    --accent: #38bdf8;
    --accent-2: #a855f7;
    --accent-3: #10b981;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body.code-studio-body {{
    background: var(--bg);
    color: var(--text-main);
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    min-height: 100vh;
    padding: 28px 16px;
}}
.code-container {{
    max-width: 1120px;
    margin: 0 auto;
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 22px;
    padding: 28px;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.75);
}}
.code-header {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding-bottom: 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 20px;
}}
.lang-badge {{
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    background: rgba(56, 189, 248, 0.15);
    color: var(--accent);
    font-size: 0.75rem;
    font-weight: 700;
    margin-bottom: 8px;
}}
.code-header h1 {{ font-size: 1.35rem; margin-bottom: 4px; word-break: break-all; }}
.code-header p {{ color: var(--text-muted); font-size: 0.88rem; }}
.code-actions {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.btn-primary, .btn-secondary {{
    text-decoration: none;
    padding: 10px 18px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.88rem;
    transition: transform 0.15s ease;
    cursor: pointer;
    border: none;
}}
.btn-primary {{ background: linear-gradient(135deg, #38bdf8, #818cf8); color: #040711; }}
.btn-secondary {{ background: rgba(255, 255, 255, 0.06); color: var(--text-main); border: 1px solid var(--card-border); }}
.btn-primary:hover, .btn-secondary:hover {{ transform: translateY(-2px); }}
.progress-wrap {{
    margin-bottom: 16px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 12px 16px;
}}
.progress-track {{
    width: 100%;
    height: 8px;
    background: rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    overflow: hidden;
    margin: 8px 0;
}}
.progress-fill {{
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #38bdf8, #10b981);
    transition: width 0.12s linear;
}}
.progress-meta {{
    display: flex;
    justify-content: space-between;
    font-size: 0.8rem;
    color: var(--text-muted);
}}
.code-pre {{
    background: #02040a;
    border: 1px solid rgba(56, 189, 248, 0.18);
    border-radius: 14px;
    padding: 20px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.88rem;
    line-height: 1.55;
    color: #e2e8f0;
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
      <p>Size: {_html.escape(humanbytes(file_size))} • Instant HTTP 206 Byte-Range Stream</p>
    </div>
    <div class="code-actions">
      <button type="button" class="btn-primary" onclick="streamCodeDownload()">⬇️ Stream Save to Disk</button>
      <a href="{download_url}" class="btn-secondary">📥 Direct Download</a>
      <button type="button" class="btn-secondary" onclick="copyCodeText()">📋 Copy Code</button>
      <button type="button" id="cancelCodeBtn" class="btn-secondary" onclick="cancelCodeStream()" style="display:none;">🛑 Cancel</button>
      <a href="{watch_url}" class="btn-secondary">🎬 Watch Studio</a>
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
let codeAbortCtrl = null;
const STREAM_URL = {stream_url!r};
const FILE_NAME = {file_name!r};
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
    if (e && e.name === "AbortError") {{
      statusEl.textContent = "Status: Cancelled by user";
    }} else {{
      statusEl.textContent = "Status: Error (" + e + ")";
      box.textContent = "Preview error: " + e;
    }}
  }} finally {{
    cancelBtn.style.display = "none";
    codeAbortCtrl = null;
  }}
}}

function cancelCodeStream() {{
  if (codeAbortCtrl) codeAbortCtrl.abort();
}}

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
    file_doc = await FileRepository.get_file_by_id(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Stream link expired or file not found.")
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})

    file_name = file_doc.get("file_name") or file_doc.get("name") or "Media Stream.mp4"
    file_size = _safe_metadata_int(file_doc.get("file_size") or file_doc.get("size", 0))
    file_size_str = humanbytes(file_size)
    mime_type = file_doc.get("mime_type") or "video/mp4"
    file_path = file_doc.get("file_path")
    raw_meta = file_doc.get("metadata") or {}
    bot = getattr(request.app.state, "bot", None)
    if bot is not None and (not raw_meta.get("duration") or (not raw_meta.get("audio_streams") and not raw_meta.get("audio_tracks"))):
        async def _bg_probe_update(fid: str, fpath: Optional[str], meta_base: dict):
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
                        "audio_track": probed.get("audio_track") or meta_base.get("audio_track", "Default Audio"),
                    })
                    if probed.get("video_streams"):
                        meta_base["width"] = probed["video_streams"][0].get("width", meta_base.get("width", 0))
                        meta_base["height"] = probed["video_streams"][0].get("height", meta_base.get("height", 0))
                    await file_repo.update_file(fid, {"metadata": meta_base})
            except Exception:
                pass
        if not await cache.get(f"probe_lock_{file_id}"):
            await cache.set(f"probe_lock_{file_id}", True, ttl=30)
            async def _delayed_probe():
                if not (file_path and os.path.exists(file_path)):
                    await asyncio.sleep(3.0)
                await _bg_probe_update(file_id, file_path, dict(raw_meta))
            asyncio.create_task(_delayed_probe())
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
@pages_router.head("/dl/{file_id}")
async def download_page(request: Request, file_id: str):
    file_doc = await FileRepository.get_file_by_id(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found or link expired.")
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})

    file_name = file_doc.get("file_name", "download.mp4")
    metadata = media_metadata_view(file_doc)
    file_size_bytes = _safe_metadata_int(file_doc.get("file_size", 0))
    file_size = humanbytes(file_size_bytes)
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
        "file_size_bytes": file_size_bytes,
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
    mime_type = normalize_mime(file_doc.get("mime_type") or mimetypes.guess_type(file_name)[0] or "video/mp4")
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
        "X-Stream-Mode": "turbo-http-206-byte-range",
        "X-Stream-Chunk-Size": str(CHUNK_SIZE),
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
        "Content-Type": mimetypes.guess_type(file_name)[0] or "application/octet-stream",
        "Content-Length": str(content_length),
        "Content-Disposition": content_disposition("attachment", file_name),
        "ETag": etag,
        "X-Stream-Mode": "turbo-http-206-byte-range",
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
        media_type="application/octet-stream",
        headers=headers,
    )


@api_router.get("/status")
@api_router.head("/status")
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
        "version": "8.0.0-ultra-max",
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
@api_router.head("/health")
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
@api_router.head("/ping")
async def api_ping():
    return {
        "status": "ok",
        "pong": True,
        "timestamp": float(time.time()),
    }


@api_router.get("/info/{file_id}")
@api_router.head("/info/{file_id}")
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



@api_router.get("/subtitles/{file_id}/{sub_idx}.vtt")
async def api_subtitle_vtt(file_id: str, sub_idx: int):
    cache_key = f"sub_vtt_{file_id}_{sub_idx}"
    cached_vtt = await cache.get(cache_key)
    if cached_vtt is not None:
        return Response(content=cached_vtt, media_type="text/vtt; charset=utf-8")
    file_doc = await FileRepository.get_file_by_id(file_id)
    if not file_doc:
        raise HTTPException(status_code=404, detail="Subtitle source not found")
    fpath = file_doc.get("file_path")
    source = fpath if (fpath and os.path.exists(fpath)) else f"http://127.0.0.1:{Config.PORT}/stream/{file_id}"
    from plugins.core_services import extract_webvtt_subtitle
    vtt = await extract_webvtt_subtitle(source, sub_index=sub_idx, timeout=15.0)
    if not vtt:
        vtt = "WEBVTT\n\n"
    await cache.set(cache_key, vtt, ttl=900)
    return Response(content=vtt, media_type="text/vtt; charset=utf-8")

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


def create_app(bot_instance=None) -> FastAPI:
    fastapi_app = FastAPI(
        title="MMW All-In-One Pro Turbo Stream Server",
        version="8.0.0",
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
                            raw_headers.append((b"cache-control", b"public, max-age=60, stale-while-revalidate=600"))
                        elif path.startswith(("/watch/", "/dl/")):
                            raw_headers.append((b"cache-control", b"public, max-age=60, stale-while-revalidate=300"))
                        elif path.startswith("/api/info/"):
                            raw_headers.append((b"cache-control", b"private, max-age=15"))
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
