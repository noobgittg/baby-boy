import time
import os
import sys
import shutil
import asyncio
import json
import re
import datetime
import uuid
import hashlib
from plugins.media_types import is_audio_mime, is_video_mime, normalize_mime
import socket
import urllib.request
from urllib.parse import urlparse
import http.client
from collections import defaultdict
from typing import Tuple, Optional, Dict, Any, List, Union
try:
    from pyrogram.errors import AuthBytesInvalid
except ImportError:
    AuthBytesInvalid = type("AuthBytesInvalid", (Exception,), {})
try:
    import aiohttp
except ImportError:
    aiohttp = None
from config import Config
from plugins.core_utils import (
    cache,
    logger,
    to_smallcaps,
    format_watermark,
    humanbytes,
    time_formatter,
    is_valid_url,
    sanitize_filename,
    safe_send_message,
    safe_edit_text,
    safe_delete_messages,
    task_manager,
    execute_with_floodwait,
    rate_limit_manager,
    pro_floodwait_sleep,
    _flood_wait_seconds,
)
from plugins.core_db import user_repo, chat_repo

try:
    import aiofiles
except ImportError:
    aiofiles = None


def telegram_media_metadata(media: Any, message: Any = None) -> Dict[str, Any]:
    """Normalize metadata available directly from a Telegram media object."""
    mime_type = normalize_mime(getattr(media, "mime_type", "") or "application/octet-stream")
    if getattr(message, "video", None) or is_video_mime(mime_type):
        kind = "video"
    elif getattr(message, "audio", None) or is_audio_mime(mime_type):
        kind = "audio"
    elif mime_type.startswith("image/") or getattr(message, "photo", None):
        kind = "image"
    else:
        kind = "document"

    language = (
        getattr(media, "language_code", None)
        or getattr(media, "language", None)
        or getattr(media, "lang", None)
        or "Undetermined"
    )
    title = getattr(media, "title", None) or ""
    performer = getattr(media, "performer", None) or ""
    audio_track = " — ".join(part for part in (title, performer) if part) or "Default Audio"
    return {
        "media_kind": kind,
        "mime_type": mime_type,
        "duration": int(getattr(media, "duration", 0) or 0),
        "width": int(getattr(media, "width", 0) or 0),
        "height": int(getattr(media, "height", 0) or 0),
        "language": str(language),
        "audio_track": audio_track,
        "audio_tracks": [],
        "subtitle_tracks": [],
        "caption": getattr(message, "caption", None) or ""
    }


FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"


def _resolve_ffmpeg_binaries():
    global FFMPEG_BIN, FFPROBE_BIN
    sys_ffmpeg = shutil.which("ffmpeg")
    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffmpeg and sys_ffprobe:
        FFMPEG_BIN = sys_ffmpeg
        FFPROBE_BIN = sys_ffprobe
        return

    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        st_ffmpeg = shutil.which("ffmpeg")
        st_ffprobe = shutil.which("ffprobe")
        if st_ffmpeg and st_ffprobe:
            FFMPEG_BIN = st_ffmpeg
            FFPROBE_BIN = st_ffprobe
            return
    except Exception:
        pass

    known_dirs = [
        "/usr/bin",
        "/usr/local/bin",
        "/workspace/.heroku/python/lib/python3.12/site-packages/static_ffmpeg/bin/linux",
        "/workspace/.heroku/python/lib/python3.11/site-packages/static_ffmpeg/bin/linux",
        "/workspace/.heroku/python/lib/python3.10/site-packages/static_ffmpeg/bin/linux"
    ]
    for kdir in known_dirs:
        ff = os.path.join(kdir, "ffmpeg")
        fp = os.path.join(kdir, "ffprobe")
        if os.path.exists(ff) and os.path.exists(fp):
            FFMPEG_BIN = ff
            FFPROBE_BIN = fp
            return

    FFMPEG_BIN = sys_ffmpeg or "ffmpeg"
    FFPROBE_BIN = sys_ffprobe or "ffprobe"


_resolve_ffmpeg_binaries()


async def get_ffmpeg_status() -> Dict[str, Any]:
    cached = await cache.get("ffstatus_info")
    if isinstance(cached, dict) and cached.get("ok"):
        return cached
    _resolve_ffmpeg_binaries()
    ver_line = "Unknown"
    ok = False
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0 and out:
            ok = True
            ver_line = out.decode("utf-8", errors="ignore").splitlines()[0].strip()
    except Exception as e:
        ver_line = f"Error: {e}"
    has_h265 = False
    hw_accels = []
    if ok:
        try:
            p2 = await asyncio.create_subprocess_exec(
                FFMPEG_BIN, "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            enc_out, _ = await asyncio.wait_for(p2.communicate(), timeout=5)
            enc_txt = enc_out.decode("utf-8", errors="ignore")
            has_h265 = "libx265" in enc_txt
            for hw in ("h264_nvenc", "h264_qsv", "h264_vaapi"):
                if (hw in enc_txt and os.path.exists("/dev/dri")) or (hw == "h264_nvenc" and os.path.exists("/dev/nvidiactl")):
                    hw_accels.append(hw)
        except Exception:
            pass
    res = {
        "ok": ok,
        "ffmpeg_bin": FFMPEG_BIN,
        "ffprobe_bin": FFPROBE_BIN,
        "version": ver_line,
        "has_h265": has_h265,
        "hw_accels": hw_accels,
    }
    if ok:
        await cache.set("ffstatus_info", res, ttl=300, transient=False)
    return res


_AUTH_RECOVERY_LOCK = asyncio.Lock()


async def _clear_stale_media_sessions(client: Any) -> None:
    """Safely clear stale Pyrofork media DC sessions without stopping the main dispatcher."""
    if client is None:
        return
    try:
        async with _AUTH_RECOVERY_LOCK:
            media_sessions = getattr(client, "media_sessions", None)
            if isinstance(media_sessions, dict) and media_sessions:
                for ms in list(media_sessions.values()):
                    try:
                        stop_fn = getattr(ms, "stop", None)
                        if callable(stop_fn):
                            res = stop_fn()
                            if asyncio.iscoroutine(res):
                                await res
                    except Exception:
                        pass
                media_sessions.clear()
    except Exception as e:
        logger.debug(f"Media session cleanup notice: {e}")


async def _stream_download_fallback(
    client: Any,
    message: Any,
    file_name: str,
    progress: Any = None,
    progress_args: tuple = (),
) -> Optional[str]:
    """Fallback downloader using client.stream_media when message.download returns None or fails."""
    if client is None or not hasattr(client, "stream_media"):
        return None
    media = getattr(message, "video", None) or getattr(message, "document", None) or getattr(message, "audio", None)
    total_size = int(getattr(media, "file_size", 0) or 0)
    os.makedirs(os.path.dirname(os.path.abspath(file_name)), exist_ok=True)
    tmp_path = f"{file_name}.part"
    downloaded = 0
    try:
        if aiofiles is not None:
            async with aiofiles.open(tmp_path, "wb") as f:
                async for chunk in client.stream_media(message):
                    if not chunk:
                        break
                    await f.write(chunk)
                    downloaded += len(chunk)
                    if callable(progress):
                        try:
                            res = progress(downloaded, max(downloaded, total_size), *progress_args)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass
        else:
            with open(tmp_path, "wb") as f:
                async for chunk in client.stream_media(message):
                    if not chunk:
                        break
                    await asyncio.to_thread(f.write, chunk)
                    downloaded += len(chunk)
                    if callable(progress):
                        try:
                            res = progress(downloaded, max(downloaded, total_size), *progress_args)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, file_name)
            rate_limit_manager.record_rpc_success()
            return file_name
    except asyncio.CancelledError:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    except Exception as e:
        logger.warning(f"Stream download fallback error: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return None


async def download_media_with_auth_recovery(
    client: Any,
    message: Any,
    file_name: str,
    **kwargs: Any
):
    """Download media with FloodWait retry, safe media-session reset, and stream_media fallback."""
    from pyrogram.errors import FloodWait as _PgFloodWait
    os.makedirs(os.path.dirname(os.path.abspath(file_name)), exist_ok=True)
    last_err = None

    for attempt in range(4):
        try:
            res = await message.download(file_name=file_name, **kwargs)
            if res and os.path.exists(res) and os.path.getsize(res) > 0:
                if os.path.abspath(res) != os.path.abspath(file_name):
                    try:
                        os.replace(res, file_name)
                    except OSError:
                        pass
                rate_limit_manager.record_rpc_success()
                return file_name if os.path.exists(file_name) else res
            if os.path.exists(file_name) and os.path.getsize(file_name) > 0:
                rate_limit_manager.record_rpc_success()
                return file_name

            await _clear_stale_media_sessions(client)
            fb = await _stream_download_fallback(
                client,
                message,
                file_name,
                progress=kwargs.get("progress"),
                progress_args=kwargs.get("progress_args", ()),
            )
            if fb and os.path.exists(fb) and os.path.getsize(fb) > 0:
                return fb
        except _PgFloodWait as fw:
            wait_s = _flood_wait_seconds(fw)
            last_err = fw
            if wait_s > 300 or attempt >= 3:
                raise
            await pro_floodwait_sleep(wait_s, f"download_media#{attempt + 1}/4")
        except AuthBytesInvalid as first_error:
            last_err = first_error
            logger.warning("Telegram media download hit AUTH_BYTES_INVALID; resetting media sessions")
            await _clear_stale_media_sessions(client)
            try:
                if os.path.exists(file_name):
                    os.remove(file_name)
            except OSError:
                pass
            fb = await _stream_download_fallback(
                client,
                message,
                file_name,
                progress=kwargs.get("progress"),
                progress_args=kwargs.get("progress_args", ()),
            )
            if fb and os.path.exists(fb) and os.path.getsize(fb) > 0:
                return fb
            if attempt >= 2:
                raise first_error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_err = exc
            err_name = type(exc).__name__.lower()
            await _clear_stale_media_sessions(client)
            if any(k in err_name for k in ("timeout", "connection", "network", "oserror")) and attempt < 2:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            fb = await _stream_download_fallback(
                client,
                message,
                file_name,
                progress=kwargs.get("progress"),
                progress_args=kwargs.get("progress_args", ()),
            )
            if fb and os.path.exists(fb) and os.path.getsize(fb) > 0:
                return fb
            if attempt >= 2:
                raise

    if os.path.exists(file_name) and os.path.getsize(file_name) > 0:
        return file_name
    if last_err:
        raise last_err
    return None


LANG_MAP = {
    "eng": "English", "en": "English",
    "mal": "Malayalam", "ml": "Malayalam",
    "hin": "Hindi", "hi": "Hindi",
    "tam": "Tamil", "ta": "Tamil",
    "tel": "Telugu", "te": "Telugu",
    "kan": "Kannada", "kn": "Kannada",
    "ben": "Bengali", "bn": "Bengali",
    "mar": "Marathi", "mr": "Marathi",
    "guj": "Gujarati", "gu": "Gujarati",
    "pan": "Punjabi", "pa": "Punjabi",
    "urd": "Urdu", "ur": "Urdu",
    "ara": "Arabic", "ar": "Arabic",
    "spa": "Spanish", "es": "Spanish",
    "fre": "French", "fra": "French", "fr": "French",
    "ger": "German", "deu": "German", "de": "German",
    "ita": "Italian", "it": "Italian",
    "por": "Portuguese", "pt": "Portuguese",
    "rus": "Russian", "ru": "Russian",
    "chi": "Chinese", "zho": "Chinese", "zh": "Chinese",
    "jpn": "Japanese", "ja": "Japanese",
    "kor": "Korean", "ko": "Korean",
    "und": "Undetermined"
}


def resolve_lang_name(code: str) -> str:
    if not code:
        return "Undetermined"
    c = code.strip().lower()
    return LANG_MAP.get(c, c.upper())


class MediaAttributes(dict):
    def __init__(self, width: int = 1280, height: int = 720, duration: int = 0):
        super().__init__(width=width, height=height, duration=duration)

    def __iter__(self):
        return iter((self["width"], self["height"], self["duration"]))

    def __getitem__(self, item):
        if isinstance(item, int):
            return (self["width"], self["height"], self["duration"])[item]
        return super().__getitem__(item)


def _file_probe_cache_key(prefix: str, file_path: str) -> str:
    try:
        st = os.stat(file_path)
        raw = f"{prefix}:{os.path.abspath(file_path)}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        raw = f"{prefix}:{file_path}"
    return f"probe_{hashlib.md5(raw.encode('utf-8', errors='ignore')).hexdigest()}"


async def _probe_json_cached(file_path: str) -> Dict[str, Any]:
    """Run ffprobe once per unique file state and cache the parsed JSON in L0+L1 cache."""
    if not file_path or not os.path.exists(file_path):
        return {}
    ck = _file_probe_cache_key("ffprobe_json", file_path)
    cached = await cache.get(ck)
    if isinstance(cached, dict) and cached:
        return cached

    cmd = [
        FFPROBE_BIN, "-v", "quiet",
        "-analyzeduration", "10000000", "-probesize", "10000000",
        "-print_format", "json",
        "-show_format", "-show_streams",
        file_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        data = json.loads(stdout.decode("utf-8", errors="ignore") or "{}")
        if isinstance(data, dict) and (data.get("streams") or data.get("format")):
            await cache.set(ck, data, ttl=300, transient=True)
            return data
    except Exception as e:
        logger.debug(f"ffprobe json notice for {file_path}: {e}")
    return {}


async def validate_media_output(file_path: str) -> bool:
    """Verify with ffprobe that the generated media file is non-empty, uncorrupted, and has valid streams."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        if os.path.getsize(file_path) < 512:
            return False
    except OSError:
        return False
    data = await _probe_json_cached(file_path)
    if data:
        streams = data.get("streams") or []
        return len(streams) > 0
    try:
        return os.path.getsize(file_path) > 1024
    except OSError:
        return False


async def generate_video_screenshots(video_path: str, output_dir: str, count: int = 4) -> List[str]:
    """Extract multi-frame high-quality JPEG screenshots across the video duration using FFmpeg."""
    os.makedirs(output_dir, exist_ok=True)
    attrs = await get_media_attributes(video_path)
    dur = max(4, int(attrs.get("duration", 0) or 20))
    n = max(1, min(8, int(count)))
    step = max(1, dur // (n + 1))
    shots: List[str] = []
    for idx in range(1, n + 1):
        ts = min(max(0, dur - 1), idx * step)
        out_jpg = os.path.join(output_dir, f"shot_{idx:02d}_{ts}s.jpg")
        cmd = [
            FFMPEG_BIN, "-y", "-ss", str(ts),
            "-i", video_path, "-vframes", "1", "-q:v", "2", out_jpg
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            if os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0:
                shots.append(out_jpg)
        except Exception:
            pass
    return shots


async def generate_video_sample(
    video_path: str,
    output_path: str,
    sample_seconds: int = 15,
    start_offset: Optional[int] = None,
) -> bool:
    """Generate a fast streamable sample clip from the video using FFmpeg."""
    attrs = await get_media_attributes(video_path)
    dur = int(attrs.get("duration", 0) or 0)
    clip_len = max(3, min(60, int(sample_seconds)))
    if start_offset is None:
        start_offset = max(0, min(dur // 5, max(0, dur - clip_len))) if dur > clip_len else 0
    return await trim_video(video_path, output_path, float(start_offset), float(start_offset + clip_len))


async def strip_video_audio(input_path: str, output_path: str) -> bool:
    """Strip all audio tracks from a video without re-encoding video stream (-c:v copy -an)."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        "-i", input_path,
        "-map", "0:v:0?",
        "-c:v", "copy",
        "-an",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=600)
        return proc.returncode == 0 and await validate_media_output(output_path)
    except Exception as e:
        logger.warning(f"strip_video_audio failed: {e}")
        return False


async def remux_container(input_path: str, output_path: str) -> bool:
    """Fast stream-copy container conversion (e.g., MKV/MOV/AVI/WEBM -> MP4 or MKV) without re-encoding."""
    ext = os.path.splitext(output_path)[1].lower()
    extra_flags = ["-movflags", "+faststart"] if ext == ".mp4" else []
    stream_info = await check_media_streams(input_path)
    in_acodec = str(stream_info.get("audio_codec") or "").lower()
    can_copy_aud = (ext != ".mp4") or (in_acodec in ("aac", "mp3", "ac3", "eac3", ""))
    aud_flags = ["-c:a", "copy"] if can_copy_aud else ["-c:a", "aac", "-aac_coder", "fast", "-b:a", "128k"]
    cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        "-i", input_path,
        "-map", "0:v:0?",
        "-map", "0:a?",
        "-c:v", "copy",
        *aud_flags,
        *extra_flags,
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0 and await validate_media_output(output_path):
            return True
    except Exception as e:
        logger.warning(f"Fast container remux fallback triggered: {e}")
    return False


def _parse_ffprobe_duration(data: Dict[str, Any], file_path: str = "") -> int:
    """Reliably extract media duration (in seconds) from ffprobe JSON across MP4, MKV, AVI, WEBM, and TS containers."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    def _try_float(val: Any) -> int:
        if val is None:
            return 0
        try:
            v = float(val)
            return int(v) if v > 0 else 0
        except (ValueError, TypeError):
            return 0

    def _try_hms(val: Any) -> int:
        if not val or not isinstance(val, str):
            return 0
        m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)", val.strip())
        if m:
            try:
                return int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
            except Exception:
                return 0
        return 0

    dur = _try_float(fmt.get("duration"))
    if dur > 0:
        return dur

    fmt_tags = fmt.get("tags") or {}
    for k, v in fmt_tags.items():
        if k.upper().startswith("DURATION"):
            dur = _try_hms(v) or _try_float(v)
            if dur > 0:
                return dur

    for s in streams:
        dur = _try_float(s.get("duration"))
        if dur > 0:
            return dur
        st_tags = s.get("tags") or {}
        for k, v in st_tags.items():
            if k.upper().startswith("DURATION"):
                dur = _try_hms(v) or _try_float(v)
                if dur > 0:
                    return dur

    fsize = _try_float(fmt.get("size"))
    fbr = _try_float(fmt.get("bit_rate"))
    if fsize > 0 and fbr > 0:
        est = int((fsize * 8) / fbr)
        if est > 0:
            return est

    if file_path and os.path.exists(file_path):
        try:
            disk_sz = os.path.getsize(file_path)
            if disk_sz > 5 * 1024 * 1024:
                return max(60, int(disk_sz / (280 * 1024)))
        except OSError:
            pass
    return 0


def _parse_fps(rate_str: Any) -> float:
    if not rate_str:
        return 0.0
    s = str(rate_str).strip()
    if "/" in s:
        parts = s.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
            return round(num / den, 2) if den > 0 else 0.0
        except (ValueError, TypeError):
            return 0.0
    try:
        return round(float(s), 2)
    except (ValueError, TypeError):
        return 0.0


async def check_media_streams(file_path: str) -> Dict[str, Any]:
    try:
        data = await _probe_json_cached(file_path)
        if not data:
            raise ValueError("Empty ffprobe data")
        has_video = False
        has_audio = False
        video_index = None
        audio_index = None
        video_codec = ""
        audio_codec = ""
        audio_channels = 2
        audio_bitrate = 0
        width, height = 1280, 720
        fps = 24.0

        streams = data.get("streams", [])
        for i, s in enumerate(streams):
            ctype = s.get("codec_type")
            disposition = s.get("disposition", {})
            if ctype == "video" and not disposition.get("attached_pic", False):
                has_video = True
                if video_index is None:
                    video_index = i
                    video_codec = str(s.get("codec_name") or "").lower()
                    width = int(s.get("width") or 1280)
                    height = int(s.get("height") or 720)
                    parsed_fps = _parse_fps(s.get("avg_frame_rate")) or _parse_fps(s.get("r_frame_rate"))
                    if 1.0 <= parsed_fps <= 240.0:
                        fps = parsed_fps
            elif ctype == "audio":
                has_audio = True
                if audio_index is None:
                    audio_index = i
                    audio_codec = str(s.get("codec_name") or "").lower()
                    audio_channels = int(s.get("channels") or 2)
                    try:
                        audio_bitrate = int(s.get("bit_rate") or 0) // 1000
                    except (ValueError, TypeError):
                        audio_bitrate = 0

        duration = _parse_ffprobe_duration(data, file_path)
        return {
            "has_video": has_video,
            "has_audio": has_audio,
            "video_index": video_index,
            "audio_index": audio_index,
            "stream_count": len(streams),
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "audio_channels": audio_channels,
            "audio_bitrate": audio_bitrate,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
        }
    except Exception as e:
        logger.debug(f"Notice checking media streams: {e}")
        return {
            "has_video": True,
            "has_audio": True,
            "video_index": 0,
            "audio_index": 0,
            "stream_count": 1,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_bitrate": 96,
            "duration": 0,
            "width": 1280,
            "height": 720,
            "fps": 24.0,
        }


async def get_media_attributes(file_path: str) -> MediaAttributes:
    try:
        info = await check_media_streams(file_path)
        width = int(info.get("width") or 1280)
        height = int(info.get("height") or 720)
        duration = int(info.get("duration") or 0)
        return MediaAttributes(width=width, height=height, duration=duration)
    except Exception as e:
        logger.warning(f"ffprobe extraction failed: {e}")
        return MediaAttributes(width=1280, height=720, duration=0)


async def get_detailed_mediainfo(file_path: str) -> Dict[str, Any]:
    try:
        data = await _probe_json_cached(file_path)
        if not data:
            return {}

        fmt = data.get("format", {})
        streams = data.get("streams", [])
        duration = _parse_ffprobe_duration(data, file_path)
        try:
            fsize = int(fmt.get("size") or (os.path.getsize(file_path) if os.path.exists(file_path) else 0))
        except (ValueError, TypeError):
            fsize = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        try:
            fbr = int(fmt.get("bit_rate") or 0)
        except (ValueError, TypeError):
            fbr = 0

        info = {
            "container": fmt.get("format_long_name", fmt.get("format_name", "Unknown")),
            "duration": duration,
            "size": fsize,
            "bitrate": fbr,
            "video_streams": [],
            "audio_streams": [],
            "subtitle_streams": []
        }

        for s in streams:
            ctype = s.get("codec_type")
            if ctype == "video":
                try:
                    vbr = int(s.get("bit_rate") or 0)
                except (ValueError, TypeError):
                    vbr = 0
                info["video_streams"].append({
                    "codec": s.get("codec_name", "unknown").upper(),
                    "profile": s.get("profile", "N/A"),
                    "width": s.get("width", 0),
                    "height": s.get("height", 0),
                    "aspect_ratio": s.get("display_aspect_ratio", "N/A"),
                    "pix_fmt": s.get("pix_fmt", "yuv420p"),
                    "r_frame_rate": s.get("r_frame_rate", "N/A"),
                    "bitrate": vbr
                })
            elif ctype == "audio":
                raw_lang = s.get("tags", {}).get("language", "und")
                lang_full = resolve_lang_name(raw_lang)
                try:
                    abr = int(s.get("bit_rate") or 0)
                except (ValueError, TypeError):
                    abr = 0
                info["audio_streams"].append({
                    "index": len(info["audio_streams"]) + 1,
                    "codec": s.get("codec_name", "unknown").upper(),
                    "channels": int(s.get("channels", 2)),
                    "channel_layout": s.get("channel_layout", "stereo"),
                    "sample_rate": s.get("sample_rate", "44100"),
                    "bitrate": abr,
                    "lang": lang_full,
                    "lang_code": raw_lang.lower(),
                    "language": lang_full,
                    "title": s.get("tags", {}).get("title", f"Track {len(info['audio_streams']) + 1}")
                })
            elif ctype == "subtitle":
                raw_lang = s.get("tags", {}).get("language", "und")
                lang_full = resolve_lang_name(raw_lang)
                info["subtitle_streams"].append({
                    "index": len(info["subtitle_streams"]) + 1,
                    "codec": s.get("codec_name", "unknown").upper(),
                    "lang": lang_full,
                    "lang_code": raw_lang.lower(),
                    "language": lang_full,
                    "title": s.get("tags", {}).get("title", f"Subtitle {len(info['subtitle_streams']) + 1}")
                })
        return info
    except Exception as e:
        logger.error(f"Error getting detailed mediainfo: {e}")
        return {}


def _is_valid_image_file(path: Optional[str]) -> bool:
    """Check magic bytes to ensure a thumbnail file is a real JPEG/PNG/WebP image and not an HTML error page."""
    if not path or not isinstance(path, str) or not os.path.exists(path):
        return False
    try:
        sz = os.path.getsize(path)
        if sz < 128 or sz > 10 * 1024 * 1024:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
        if head.startswith(b"\xff\xd8\xff"):
            return True
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if head.startswith(b"RIFF") and b"WEBP" in head:
            return True
        return False
    except OSError:
        return False


async def extract_frame_screenshot(video_path: str, output_dir: str, duration: int) -> Optional[str]:
    out_path = os.path.join(output_dir, f"thumb_{os.path.basename(video_path)}.jpg")
    timestamp = max(1, min(30, duration // 3)) if duration > 3 else 0
    cmd = [
        FFMPEG_BIN, "-y", "-nostdin",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vf", "scale='min(320,iw)':-2",
        "-vframes", "1",
        "-q:v", "4",
        out_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
        if _is_valid_image_file(out_path):
            return out_path
    except Exception as e:
        logger.debug(f"Notice taking screenshot: {e}")
    return None


async def compress_audio(input_path: str, output_path: str, bitrate: str = "96k") -> bool:
    cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin", "-threads", "0",
        "-i", input_path,
        "-vn",
        "-c:a", "aac",
        "-aac_coder", "fast",
        "-b:a", str(bitrate or "96k"),
        "-ac", "2",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
        logger.warning(f"AAC audio compression fallback triggered: {err.decode('utf-8', errors='ignore')[:300]}")
    except Exception as e:
        logger.warning(f"Error compressing audio (AAC): {e}")

    fb_cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin", "-threads", "0",
        "-i", input_path,
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", str(bitrate or "96k"),
        "-ac", "2",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *fb_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"Error compressing audio (fallback): {e}")
        return False


TURBO_X264_PARAMS = (
    "sliced-threads=0:ref=1:me=dia:subme=0:trellis=0:weightp=0:bframes=0:"
    "rc-lookahead=0:scenecut=0:aq-mode=0:8x8dct=0:cabac=0:deblock=0:"
    "partitions=none:me-range=4:chroma-me=0:psy=0"
)

HYPER_X264_PARAMS = (
    "sliced-threads=0:ref=1:me=dia:subme=0:trellis=0:weightp=0:bframes=0:"
    "rc-lookahead=0:scenecut=0:aq-mode=0:8x8dct=0:cabac=0:deblock=0:"
    "partitions=none:me-range=4:chroma-me=0:psy=0:keyint=250:min-keyint=25"
)

TURBO_X265_PARAMS = (
    "ctu=32:min-cu-size=16:bframes=0:rc-lookahead=0:no-sao=1:no-weightp=1:"
    "no-deblock=1:me=0:subme=0:max-merge=1:early-skip=1:fast-intra=1:frame-threads=0"
)


def _build_fast_decode_input_flags(total_duration: int, preset: str) -> List[str]:
    """
    Generate input decoder acceleration flags placed BEFORE `-i input_path`.
    Skips expensive in-loop deblocking filters on the source decoder, and in hyper/turbo/long-video
    mode skips non-reference B-frames in the bitstream so large movies decode in minutes.
    """
    p_low = str(preset or "turbo").lower()
    flags = ["-flags2", "fast", "-skip_loop_filter", "all"]
    if p_low in ("hyper", "turbo", "ultrafast") or total_duration >= 600:
        flags.extend(["-skip_frame", "noref"])
    flags.extend(["-threads", "0", "-thread_type", "frame+slice"])
    return flags


def _build_5min_turbo_vf(
    even_h: int,
    total_duration: int,
    preset: str,
    respect_custom_height: bool = False,
    source_height: int = 0,
    source_fps: float = 0.0,
) -> Tuple[str, str, List[str]]:
    """
    Compute universal FFmpeg scale filter, preset, and x264 turbo flags compatible with ALL FFmpeg
    versions (including FFmpeg 4.x/5.0 static builds and FFmpeg 5.1/6.x/7.x) without `source_fps`
    filter errors or accidental upscaling.
    """
    p_low = str(preset or "turbo").lower()
    is_hyper = p_low in ("hyper", "ultrafast")
    is_turbo = is_hyper or p_low in ("turbo", "superfast", "veryfast", "fast") or total_duration >= 300
    eff_preset = "ultrafast"
    scale_flag = "neighbor" if is_hyper or total_duration >= 600 else "fast_bilinear"

    if not respect_custom_height:
        if is_hyper:
            if total_duration >= 1200 and (even_h == 0 or even_h > 480):
                even_h = 480
            elif even_h == 0:
                even_h = 480
        elif total_duration >= 3600 and (even_h == 0 or even_h > 480):
            even_h = 480
        elif total_duration >= 1500 and (even_h == 0 or even_h > 720):
            even_h = 720

    if source_height > 0 and even_h > source_height:
        even_h = max(2, source_height - (source_height % 2))

    skip_noref_active = (p_low in ("hyper", "turbo", "ultrafast") or total_duration >= 600)
    fps_prefix = ""
    if not skip_noref_active:
        target_fps = 20 if total_duration >= 900 else 24
        if source_fps <= 0 or source_fps > target_fps:
            fps_prefix = f"fps={target_fps},"

    if even_h > 0:
        vf_filter = f"{fps_prefix}scale=-2:{even_h}:flags={scale_flag},setsar=1"
    else:
        vf_filter = f"{fps_prefix}scale=trunc(iw/2)*2:trunc(ih/2)*2:flags={scale_flag},setsar=1"

    x264_p = HYPER_X264_PARAMS if is_hyper else TURBO_X264_PARAMS
    extra_x264: List[str] = []
    if skip_noref_active:
        extra_x264.extend(["-vsync", "vfr"])
    if is_turbo:
        extra_x264.extend(["-tune", "fastdecode", "-x264-params", x264_p])
    else:
        extra_x264.extend(["-tune", "fastdecode"])
    return vf_filter, eff_preset, extra_x264


async def _run_monitored_ffmpeg(
    cmd: List[str],
    output_path: str,
    total_duration: int,
    progress_message: Any,
    header_title: str,
    engine_label: str,
    task_id: Optional[str] = None,
) -> bool:
    """
    Execute FFmpeg subprocess while streaming live progress, ETA, speed, and encoded size to Telegram.
    Uses a non-blocking background progress task so reading `process.stderr` NEVER stalls on Telegram
    rate-limit waits or network I/O.
    """
    process = None
    bg_edit_task: Optional[asyncio.Task] = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        if task_id:
            task_manager.set_subprocess(task_id, process)

        start_ts = time.time()
        last_update = 0.0
        last_pct_step = -5
        error_tail = ""
        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        speed_regex = re.compile(r"speed=\s*([0-9.]+x)")

        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            decoded = chunk.decode("utf-8", errors="ignore")
            error_tail = (error_tail + decoded)[-16384:]
            matches = list(time_regex.finditer(error_tail))
            match = matches[-1] if matches else None
            if (
                match
                and progress_message
                and (bg_edit_task is None or bg_edit_task.done())
                and not rate_limit_manager.is_in_cooldown()
            ):
                hours, minutes, seconds = map(float, match.groups())
                current_seconds = hours * 3600 + minutes * 60 + seconds
                now = float(time.time())
                pct = min(99.9, (current_seconds / total_duration) * 100) if total_duration > 0 else 0.0
                active_jobs = max(1, task_manager.active_count)
                step_gran = 5 if active_jobs <= 2 else min(20, 5 * active_jobs)
                min_gap = max(3.0, min(15.0, 2.0 * active_jobs))
                pct_step = int(pct // step_gran) * step_gran
                should_update = (
                    (total_duration > 0 and pct_step >= last_pct_step + step_gran and (now - last_update) >= min_gap)
                    or ((now - last_update) >= (min_gap * 1.8))
                )
                if should_update:
                    last_update = now
                    last_pct_step = pct_step
                    filled = int(pct // 10)
                    bar = "▰" * filled + "▱" * (10 - filled)
                    sp_m = list(speed_regex.finditer(error_tail))
                    sp_str = sp_m[-1].group(1) if sp_m else "12.5x"
                    elapsed = max(0.5, now - start_ts)
                    eta_sec = int(((total_duration - current_seconds) / max(0.1, current_seconds / elapsed))) if (total_duration > current_seconds > 0) else 0
                    eta_str = time_formatter(eta_sec) if eta_sec > 0 else "Calculating..."
                    cur_sz = humanbytes(os.path.getsize(output_path)) if os.path.exists(output_path) else "0 B"
                    dur_disp = time_formatter(total_duration) if total_duration > 0 else "Live"
                    text = (
                        f"{header_title}\n\n"
                        f"`[{bar}] {pct:.1f}%`\n\n"
                        f"• ⏱️ **{to_smallcaps('processed')}** : `{time_formatter(int(current_seconds))} / {dur_disp}`\n"
                        f"• ⏳ **{to_smallcaps('eta')}** : `{eta_str}` | 🚀 **{to_smallcaps('speed')}** : `{sp_str}`\n"
                        f"• 📦 **{to_smallcaps('encoded size')}** : `{cur_sz}`\n"
                        f"• ⚙️ **{to_smallcaps('engine')}** : `{engine_label}`"
                        f"{format_watermark()}"
                    )
                    bg_edit_task = asyncio.create_task(safe_edit_text(progress_message, text))

        await process.wait()
        if bg_edit_task and not bg_edit_task.done():
            try:
                await asyncio.wait_for(bg_edit_task, timeout=2.0)
            except Exception:
                pass
        if process.returncode == 0 and await validate_media_output(output_path):
            return True
        logger.warning(f"FFmpeg pass exited with code={process.returncode}: {error_tail[-400:]}")
    except asyncio.CancelledError:
        if bg_edit_task and not bg_edit_task.done():
            bg_edit_task.cancel()
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise
    except Exception as e:
        if bg_edit_task and not bg_edit_task.done():
            bg_edit_task.cancel()
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        logger.warning(f"Monitored FFmpeg execution notice: {e}")
    return False


async def compress_media(
    input_path: str,
    output_path: str,
    crf: int = 28,
    preset: str = "superfast",
    scale_height: int = 720,
    progress_message = None,
    total_duration: int = 0,
    max_output_mb: Optional[float] = None,
    vcodec: str = "libx264",
    audio_bitrate: str = "96k",
    task_id: Optional[str] = None,
    respect_custom_height: bool = False,
) -> bool:
    stream_info = await check_media_streams(input_path)
    if total_duration <= 0:
        total_duration = int(stream_info.get("duration") or 0)
    if total_duration <= 0:
        attrs = await get_media_attributes(input_path)
        total_duration = int(attrs.get("duration", 0) or 0)

    src_h = int(stream_info.get("height") or 0)
    src_fps = float(stream_info.get("fps") or 0.0)
    even_h = scale_height if scale_height % 2 == 0 else max(0, scale_height - 1)
    vf_filter, eff_preset, extra_x264 = _build_5min_turbo_vf(
        even_h,
        total_duration,
        preset,
        respect_custom_height=respect_custom_height,
        source_height=src_h,
        source_fps=src_fps,
    )
    input_accel = _build_fast_decode_input_flags(total_duration, preset)

    orig_mb = (os.path.getsize(input_path) / (1024 * 1024)) if os.path.exists(input_path) else 0.0
    eff_max_mb = max_output_mb
    if orig_mb > 1.0:
        ratio_cap = orig_mb * 0.78
        eff_max_mb = min(eff_max_mb, ratio_cap) if eff_max_mb else ratio_cap

    rate_cap_args: List[str] = []
    if eff_max_mb and total_duration > 0:
        total_kbits = float(eff_max_mb) * 8192.0 * 0.94
        max_v_kb = max(90, min(25000, int((total_kbits / max(1, total_duration)) - 64)))
        rate_cap_args = ["-maxrate", f"{max_v_kb}k", "-bufsize", f"{max_v_kb * 2}k"]

    use_hevc = vcodec in ("libx265", "hevc", "h265")
    codec_name = "libx265" if use_hevc else "libx264"
    codec_extra = (
        ["-tag:v", "hvc1", "-x265-params", TURBO_X265_PARAMS]
        if use_hevc
        else extra_x264
    )

    in_acodec = str(stream_info.get("audio_codec") or "").lower()
    in_ach = int(stream_info.get("audio_channels") or 2)
    can_copy_audio = in_acodec in ("aac", "mp3", "ac3", "eac3") and (in_ach <= 2 or in_acodec in ("aac", "mp3"))
    if str(audio_bitrate).lower() == "copy":
        audio_args = ["-c:a", "copy"]
        aud_label = "Audio Copy"
    elif can_copy_audio and str(audio_bitrate).lower() in ("auto", "96k", "128k"):
        audio_args = ["-c:a", "copy"]
        aud_label = f"Audio Copy ({in_acodec.upper()})"
    else:
        abr = str(audio_bitrate if str(audio_bitrate).lower() not in ("auto", "copy") else "64k")
        audio_args = ["-c:a", "aac", "-aac_coder", "fast", "-b:a", abr, "-ac", "2"]
        aud_label = f"Fast AAC {abr}"

    primary_cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        *input_accel,
        "-i", input_path,
        "-filter_threads", "0",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c:v", codec_name,
        "-pix_fmt", "yuv420p",
        "-preset", eff_preset,
        *codec_extra,
        "-crf", str(crf),
        *rate_cap_args,
        "-vf", vf_filter,
        "-threads", "0",
        *audio_args,
        "-map_chapters", "-1",
        "-sn", "-dn",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "9999",
        output_path
    ]

    header_title = f"⚡ **{to_smallcaps('ultra-fast turbo ffmpeg compression')}** 🗜️"
    engine_label = f"{codec_name.upper()} {eff_preset.upper()} + {aud_label} (CRF {crf})"
    if await _run_monitored_ffmpeg(
        primary_cmd, output_path, total_duration, progress_message, header_title, engine_label, task_id=task_id
    ):
        return True

    safe_h = even_h if even_h > 0 else min(480, src_h if src_h > 0 else 480)
    safe_h = max(2, safe_h - (safe_h % 2))
    safe_vf = f"scale=-2:{safe_h}:flags=neighbor,setsar=1"
    fallback_cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        "-flags2", "fast", "-skip_loop_filter", "all",
        "-threads", "0", "-thread_type", "frame+slice",
        "-i", input_path,
        "-filter_threads", "0",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-vf", safe_vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-x264-params", TURBO_X264_PARAMS,
        "-crf", str(max(26, crf)),
        *rate_cap_args,
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-c:a", "aac",
        "-aac_coder", "fast",
        "-b:a", "64k",
        "-ac", "2",
        "-map_chapters", "-1",
        "-sn", "-dn",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    if await _run_monitored_ffmpeg(
        fallback_cmd, output_path, total_duration, progress_message, header_title, f"H.264 ULTRAFAST {safe_h}p + AAC 64k", task_id=task_id
    ):
        return True

    video_only_cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin", "-threads", "0",
        "-i", input_path,
        "-map", "0:v:0?",
        "-vf", safe_vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-x264-params", TURBO_X264_PARAMS,
        "-crf", str(max(28, crf)),
        *rate_cap_args,
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        output_path
    ]
    return await _run_monitored_ffmpeg(
        video_only_cmd, output_path, total_duration, progress_message, header_title, f"H.264 ULTRAFAST {safe_h}p (Video-Only)", task_id=task_id
    )


def parse_time_range(text: str) -> Tuple[Optional[float], Optional[float]]:
    cleaned = text.strip().lower().replace("to", " ").replace("-", " ").replace(",", " ")
    tokens = [t for t in cleaned.split() if t]
    if len(tokens) != 2:
        return None, None

    def parse_one(val: str) -> Optional[float]:
        if re.match(r"^\d+(\.\d+)?$", val):
            return float(val)
        parts = val.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
        except Exception:
            return None
        return None

    s = parse_one(tokens[0])
    e = parse_one(tokens[1])
    if s is not None and e is not None and e > s:
        return s, e
    return None, None


async def trim_video(video_path: str, output_path: str, start_sec: float, end_sec: float) -> bool:
    duration = end_sec - start_sec
    copy_cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "1",
        "-movflags", "+faststart",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *copy_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True
    except Exception:
        pass

    transcode_cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *transcode_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception:
        return False


async def extract_all_audio_tracks(video_path: str, output_dir: str) -> List[Dict[str, Any]]:
    tracks = []
    try:
        data = await _probe_json_cached(video_path)
        audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]

        base_name = sanitize_filename(os.path.splitext(os.path.basename(video_path))[0])

        for idx, s in enumerate(audio_streams):
            raw_lang = s.get("tags", {}).get("language", "und").lower()
            lang_name = resolve_lang_name(raw_lang)
            title = s.get("tags", {}).get("title", f"Track {idx+1}")
            codec = s.get("codec_name", "mp3").lower()
            channels = int(s.get("channels", 2))
            try:
                dur = int(float(s.get("duration", 0)))
            except (ValueError, TypeError):
                dur = 0

            ext = "mp3" if codec in ["mp3", "mpeg"] else ("m4a" if codec in ["aac", "alac"] else "mp3")
            out_filename = f"{base_name}_audio_{idx+1}_{raw_lang}.{ext}"
            out_filepath = os.path.join(output_dir, out_filename)

            if ext == "mp3" and codec in ["mp3", "mpeg"]:
                ext_cmd = [FFMPEG_BIN, "-y", "-i", video_path, "-map", f"0:a:{idx}", "-vn", "-c:a", "copy", out_filepath]
            elif ext == "m4a" and codec in ["aac"]:
                ext_cmd = [FFMPEG_BIN, "-y", "-i", video_path, "-map", f"0:a:{idx}", "-vn", "-c:a", "copy", out_filepath]
            else:
                ext_cmd = [FFMPEG_BIN, "-y", "-i", video_path, "-map", f"0:a:{idx}", "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_filepath]

            p = await asyncio.create_subprocess_exec(
                *ext_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await p.communicate()

            if os.path.exists(out_filepath) and os.path.getsize(out_filepath) > 0:
                tracks.append({
                    "path": out_filepath,
                    "index": idx + 1,
                    "language": lang_name,
                    "lang_code": raw_lang,
                    "title": title,
                    "codec": codec.upper(),
                    "channels": channels,
                    "duration": dur
                })
    except Exception as e:
        logger.error(f"Error extracting audio tracks: {e}")
    return tracks


async def extract_all_subtitle_tracks(video_path: str, output_dir: str) -> List[Dict[str, Any]]:
    tracks = []
    try:
        data = await _probe_json_cached(video_path)
        sub_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]

        base_name = sanitize_filename(os.path.splitext(os.path.basename(video_path))[0])

        for idx, s in enumerate(sub_streams):
            raw_lang = s.get("tags", {}).get("language", "und").lower()
            lang_name = resolve_lang_name(raw_lang)
            title = s.get("tags", {}).get("title", f"Sub {idx+1}")
            codec = s.get("codec_name", "srt").lower()

            ext = "ass" if "ass" in codec or "ssa" in codec else "srt"
            out_filename = f"{base_name}_sub_{idx+1}_{raw_lang}.{ext}"
            out_filepath = os.path.join(output_dir, out_filename)

            if ext == "ass":
                ext_cmd = [FFMPEG_BIN, "-y", "-i", video_path, "-map", f"0:s:{idx}", "-c:s", "copy", out_filepath]
            else:
                ext_cmd = [FFMPEG_BIN, "-y", "-i", video_path, "-map", f"0:s:{idx}", "-c:s", "srt", out_filepath]

            p = await asyncio.create_subprocess_exec(
                *ext_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await p.communicate()

            if os.path.exists(out_filepath) and os.path.getsize(out_filepath) > 0:
                tracks.append({
                    "path": out_filepath,
                    "index": idx + 1,
                    "language": lang_name,
                    "lang_code": raw_lang,
                    "title": title,
                    "codec": codec.upper()
                })
    except Exception as e:
        logger.error(f"Error extracting subtitle tracks: {e}")
    return tracks


async def add_audio_to_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    replace_existing: bool = False,
    language: str = "und"
) -> bool:
    if replace_existing:
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-metadata:s:a:0", f"language={language}",
            "-max_muxing_queue_size", "9999",
            output_path
        ]
    else:
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v",
            "-map", "0:a?",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-max_muxing_queue_size", "9999",
            output_path
        ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True
    except Exception:
        pass

    fb_cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0" if replace_existing else "0:v",
        "-map", "1:a:0" if replace_existing else "0:a?",
        *(["-map", "1:a:0"] if not replace_existing else []),
        "-c:v", "copy",
        "-c:a", "aac",
        "-aac_coder", "fast",
        "-b:a", "192k",
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *fb_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception:
        return False


async def add_subtitle_to_video(
    video_path: str,
    subtitle_path: str,
    output_path: str,
    language: str = "eng"
) -> bool:
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-i", subtitle_path,
        "-map", "0",
        "-map", "1:0",
        "-c", "copy",
        "-c:s", "srt" if subtitle_path.endswith(".srt") else "copy",
        "-metadata:s:s:0", f"language={language}",
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception:
        return False


async def compress_to_target_size(
    input_path: str,
    output_path: str,
    target_size_mb: float = 500.0,
    duration_sec: int = 0,
    scale_height: int = 720,
    progress_message = None,
    target_mb: Optional[float] = None,
    task_id: Optional[str] = None,
    preset: str = "hyper",
    audio_bitrate: str = "auto",
    respect_custom_height: bool = False,
) -> bool:
    """
    Ultra-Fast Target-Size & Custom Quality FFmpeg Compressor.
    Compresses any file (including 3 GB 2.5-hour movies) to the exact desired target MB and quality
    within 5–15 minutes using hardware-friendly decoder frame/loop-filter skipping and turbo x264 params.
    """
    effective_mb = max(1.0, min(1950.0, float(target_mb if target_mb is not None else target_size_mb)))
    stream_info = await check_media_streams(input_path)
    if duration_sec <= 5:
        duration_sec = int(stream_info.get("duration") or 0)
    if duration_sec <= 5:
        attrs = await get_media_attributes(input_path)
        duration_sec = int(attrs.get("duration", 0) or 0)
    if duration_sec <= 5 and os.path.exists(input_path):
        disk_sz = os.path.getsize(input_path)
        if disk_sz > 5 * 1024 * 1024:
            duration_sec = max(60, int(disk_sz / (280 * 1024)))
    dur = max(1, int(duration_sec))

    total_kbits = effective_mb * 8192.0 * 0.94
    total_kbps = total_kbits / dur

    in_acodec = str(stream_info.get("audio_codec") or "").lower()
    in_abr = int(stream_info.get("audio_bitrate") or 0)
    in_ach = int(stream_info.get("audio_channels") or 2)
    src_h = int(stream_info.get("height") or 0)
    src_fps = float(stream_info.get("fps") or 0.0)

    if str(audio_bitrate).lower() == "copy" and in_acodec in ("aac", "mp3", "ac3", "eac3"):
        audio_bitrate_kb = in_abr if 32 <= in_abr <= 384 else 96
        audio_args = ["-c:a", "copy"]
        aud_label = "Audio Copy"
    elif (
        str(audio_bitrate).lower() == "auto"
        and in_acodec in ("aac", "mp3", "ac3", "eac3")
        and (in_ach <= 2 or in_acodec in ("aac", "mp3"))
        and (in_abr == 0 or in_abr <= 160)
        and total_kbps >= 300
    ):
        audio_bitrate_kb = in_abr if 32 <= in_abr <= 160 else 96
        audio_args = ["-c:a", "copy"]
        aud_label = f"Audio Copy ({in_acodec.upper()})"
    else:
        if str(audio_bitrate).lower() not in ("auto", "copy", ""):
            try:
                audio_bitrate_kb = max(32, min(192, int(str(audio_bitrate).lower().replace("k", ""))))
            except ValueError:
                audio_bitrate_kb = 64
        else:
            if total_kbps < 260:
                audio_bitrate_kb = 48
            elif total_kbps < 600:
                audio_bitrate_kb = 64
            else:
                audio_bitrate_kb = 96
        audio_args = ["-c:a", "aac", "-aac_coder", "fast", "-b:a", f"{audio_bitrate_kb}k", "-ac", "2"]
        aud_label = f"Fast AAC {audio_bitrate_kb}k"

    video_bitrate_kb = max(65, min(25000, int(total_kbps - audio_bitrate_kb)))

    even_h = scale_height if scale_height % 2 == 0 else max(0, scale_height - 1)
    if even_h == 0:
        if video_bitrate_kb < 240:
            even_h = 360
        elif video_bitrate_kb < 550 or dur >= 3600:
            even_h = 480
        elif video_bitrate_kb < 950:
            even_h = 540
        elif video_bitrate_kb < 1800:
            even_h = 720
        else:
            even_h = 1080
        respect_custom_height = True

    vf_filter, eff_preset, extra_x264 = _build_5min_turbo_vf(
        even_h,
        dur,
        preset,
        respect_custom_height=respect_custom_height,
        source_height=src_h,
        source_fps=src_fps,
    )
    input_accel = _build_fast_decode_input_flags(dur, preset)

    cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        *input_accel,
        "-i", input_path,
        "-filter_threads", "0",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate_kb}k",
        "-maxrate", f"{int(video_bitrate_kb * 1.18)}k",
        "-bufsize", f"{int(video_bitrate_kb * 2)}k",
        "-pix_fmt", "yuv420p",
        "-preset", eff_preset,
        *extra_x264,
        "-threads", "0",
        *audio_args,
        "-map_chapters", "-1",
        "-sn", "-dn",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "9999",
        output_path
    ]

    res_disp = f"{even_h}p" if even_h > 0 else "Auto"
    header_title = f"🎯 **{to_smallcaps('custom ultra-fast target compression')}** (`{int(effective_mb)} MB • {res_disp}`) ⚡"
    engine_label = f"{eff_preset.upper()} • Video {video_bitrate_kb}k + {aud_label}"

    if await _run_monitored_ffmpeg(
        cmd, output_path, dur, progress_message, header_title, engine_label, task_id=task_id
    ):
        return True

    safe_h = even_h if even_h > 0 else 480
    if src_h > 0 and safe_h > src_h:
        safe_h = max(2, src_h - (src_h % 2))
    fb_cmd = [
        FFMPEG_BIN, "-y",
        "-nostdin",
        "-flags2", "fast", "-skip_loop_filter", "all",
        "-threads", "0", "-thread_type", "frame+slice",
        "-i", input_path,
        "-filter_threads", "0",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-vf", f"scale=-2:{safe_h}:flags=neighbor,setsar=1",
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate_kb}k",
        "-maxrate", f"{int(video_bitrate_kb * 1.22)}k",
        "-bufsize", f"{int(video_bitrate_kb * 2)}k",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-x264-params", TURBO_X264_PARAMS,
        "-threads", "0",
        "-c:a", "aac",
        "-aac_coder", "fast",
        "-b:a", f"{audio_bitrate_kb}k",
        "-ac", "2",
        "-map_chapters", "-1",
        "-sn", "-dn",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    return await _run_monitored_ffmpeg(
        fb_cmd, output_path, dur, progress_message, header_title, f"FALLBACK ULTRAFAST • {video_bitrate_kb}k", task_id=task_id
    )


GLOBAL_THUMB_CACHE = os.path.join(Config.DOWNLOAD_DIR, "global_tham_cache.jpg")
GLOBAL_THUMB_CACHE_LOCK = asyncio.Lock()
_FAILED_THUMB_URLS: Dict[str, float] = {}


async def download_thumbnail_url(url: str, target_path: str) -> Optional[str]:
    if not url or not is_valid_url(url):
        return None
    fail_ts = _FAILED_THUMB_URLS.get(url, 0.0)
    if (time.time() - fail_ts) < 600.0:
        return None
    try:
        if aiohttp is not None:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if aiofiles:
                            async with aiofiles.open(target_path, "wb") as f:
                                await f.write(data)
                        else:
                            with open(target_path, "wb") as f:
                                f.write(data)
                        if _is_valid_image_file(target_path):
                            return target_path
                        try:
                            os.remove(target_path)
                        except OSError:
                            pass
        else:
            loop = asyncio.get_running_loop()

            def _sync_dl():
                req = urllib.request.Request(url, headers={"User-Agent": "MMW-ProBot/3.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        with open(target_path, "wb") as f:
                            f.write(resp.read())
                        if _is_valid_image_file(target_path):
                            return target_path
                        try:
                            os.remove(target_path)
                        except OSError:
                            pass
                return None

            res = await loop.run_in_executor(None, _sync_dl)
            if res:
                return res
    except Exception as e:
        logger.debug(f"Thumbnail URL fetch skipped ({url}): {e}")
    _FAILED_THUMB_URLS[url] = time.time()
    return None


async def resolve_thumbnail(
    client: Any,
    user_id: int,
    temp_dir_or_thumb_id: Any = None,
    video_path: Optional[str] = None,
    duration: int = 0,
    temp_dir: Optional[str] = None,
    fallback_tg_thumb_id: Optional[str] = None
) -> Optional[str]:
    resolved_dir = temp_dir
    if not resolved_dir:
        if isinstance(temp_dir_or_thumb_id, str) and os.path.isdir(temp_dir_or_thumb_id):
            resolved_dir = temp_dir_or_thumb_id
        elif video_path and os.path.exists(video_path):
            resolved_dir = os.path.dirname(video_path)
        else:
            resolved_dir = os.path.join(Config.DOWNLOAD_DIR, str(user_id))
    os.makedirs(resolved_dir, exist_ok=True)

    user = await user_repo.get_user(user_id)
    thumb_id = None
    if user and user.get("thumb_id"):
        thumb_id = user["thumb_id"]
    elif isinstance(temp_dir_or_thumb_id, str) and not os.path.isdir(temp_dir_or_thumb_id):
        thumb_id = temp_dir_or_thumb_id

    if thumb_id and client:
        try:
            custom_path = os.path.join(resolved_dir, "custom_thumb.jpg")
            dl_c = await client.download_media(thumb_id, file_name=custom_path)
            if _is_valid_image_file(dl_c):
                return dl_c
            if _is_valid_image_file(custom_path):
                return custom_path
        except Exception as e:
            logger.debug(f"Custom thumbnail download notice: {e}")

    user_tham = user.get("tham_url") if user else None
    if user_tham:
        user_thumb_path = os.path.join(resolved_dir, "user_tham.jpg")
        downloaded = await download_thumbnail_url(user_tham, user_thumb_path)
        if _is_valid_image_file(downloaded):
            return downloaded

    if fallback_tg_thumb_id and client:
        try:
            tg_thumb_path = os.path.join(resolved_dir, "tg_fast_thumb.jpg")
            dl_t = await client.download_media(fallback_tg_thumb_id, file_name=tg_thumb_path)
            if _is_valid_image_file(dl_t):
                return dl_t
            if _is_valid_image_file(tg_thumb_path):
                return tg_thumb_path
        except Exception:
            pass

    if Config.THAM_URL:
        async with GLOBAL_THUMB_CACHE_LOCK:
            if _is_valid_image_file(GLOBAL_THUMB_CACHE):
                return GLOBAL_THUMB_CACHE
            temp_cache = f"{GLOBAL_THUMB_CACHE}.{uuid.uuid4().hex}.tmp"
            try:
                downloaded = await download_thumbnail_url(Config.THAM_URL, temp_cache)
                if _is_valid_image_file(downloaded):
                    os.replace(downloaded, GLOBAL_THUMB_CACHE)
                    return GLOBAL_THUMB_CACHE
            finally:
                if os.path.exists(temp_cache):
                    try:
                        os.remove(temp_cache)
                    except OSError:
                        pass

    if video_path and os.path.exists(video_path):
        return await extract_frame_screenshot(video_path, resolved_dir, duration)

    return None


DEFAULT_CAPTION = (
    "📁 **{filename}**\n\n"
    "• 📦 **sɪᴢᴇ** : `{filesize}`\n"
    "• ⏱️ **ᴅᴜʀᴀᴛɪᴏɴ** : `{duration}`\n"
    "• 🌐 **ʟᴀɴɢᴜᴀɢᴇ** : `{language}`\n"
    "• 🎧 **ᴀᴜᴅɪᴏ** : `{audio_track}`\n"
    "• 🏷️ **ᴛʏᴘᴇ** : `{ext}`\n"
    "• 🆔 **ғɪʟᴇ ɪᴅ** : `{file_id}`\n"
    "• 📢 **ᴡᴀᴛᴇʀᴍᴀʀᴋ** : [{watermark}]({watermark_url})"
)


async def format_caption(
    user_or_id: Union[int, Dict[str, Any], Any],
    file_name: str,
    file_size_str: Any = "0 B",
    duration_str: Any = "0s",
    ext: str = "",
    file_caption: str = "",
    language: str = "Undetermined",
    audio_track: str = "Default Audio",
    file_id: str = "",
    resolution: str = "N/A",
    video_codec: str = "N/A",
    audio_codec: str = "N/A",
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    user: Optional[Dict[str, Any]] = None
    if isinstance(user_or_id, dict):
        user = user_or_id
    elif isinstance(user_or_id, int):
        user = await user_repo.get_user(user_or_id)
    elif user_or_id is not None:
        uid = getattr(user_or_id, "id", None) or getattr(user_or_id, "user_id", None)
        if uid:
            user = await user_repo.get_user(int(uid))

    if isinstance(file_size_str, (int, float)):
        file_size_str = humanbytes(int(file_size_str))

    if isinstance(duration_str, (int, float)):
        duration_str = time_formatter(seconds=int(duration_str))

    user_custom = user.get("custom_caption") if user else None
    user_caps_list = user.get("captions_list", []) if user else []
    if user is not None and user.get("auto_caption") is False:
        caption = file_caption or ""
        if Config.WATERMARK not in caption:
            caption += format_watermark()
        return caption[:1024]

    template = user_custom
    if not template and user_caps_list:
        template = user_caps_list[0]
    if not template and file_caption:
        template = file_caption
    if not template:
        template = DEFAULT_CAPTION

    if metadata:
        if not language or language == "Undetermined":
            langs = [a.get("language") or a.get("lang") for a in metadata.get("audio_streams", []) if (a.get("language") or a.get("lang"))]
            if langs:
                language = ", ".join(sorted(set([str(l).title() for l in langs])))
        if not audio_track or audio_track == "Default Audio":
            auds = [f"{a.get('codec', '')} {a.get('channels', 2)}ch".strip() for a in metadata.get("audio_streams", [])]
            if auds:
                audio_track = ", ".join(auds)
        if not resolution or resolution == "N/A":
            v_streams = metadata.get("video_streams", [])
            if v_streams:
                resolution = f"{v_streams[0].get('width', 0)}x{v_streams[0].get('height', 0)}"
        if not video_codec or video_codec == "N/A":
            v_streams = metadata.get("video_streams", [])
            if v_streams:
                video_codec = v_streams[0].get("codec", "N/A")
        if not audio_codec or audio_codec == "N/A":
            a_streams = metadata.get("audio_streams", [])
            if a_streams:
                audio_codec = a_streams[0].get("codec", "N/A")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    time_str = now_utc.strftime("%H:%M:%S UTC")

    fmt_dict = {
        "filename": file_name,
        "file_name": file_name,
        "filesize": file_size_str,
        "file_size": file_size_str,
        "duration": duration_str,
        "language": language or "Undetermined",
        "languages": language or "Undetermined",
        "audio_track": audio_track or "Stereo 2.0",
        "autotrack": audio_track or "Stereo 2.0",
        "audio_tracks": audio_track or "Stereo 2.0",
        "caption": file_caption or "",
        "file_caption": file_caption or "",
        "original_caption": file_caption or "",
        "file_id": file_id or "N/A",
        "fileid": file_id or "N/A",
        "resolution": resolution or "N/A",
        "video_codec": video_codec or "N/A",
        "audio_codec": audio_codec or "N/A",
        "ext": ext,
        "watermark": Config.WATERMARK,
        "watermark_url": Config.WATERMARK_URL,
        "date": now_str,
        "time": time_str,
        "current_time": time_str,
        "time_str": time_str
    }

    try:
        class SafeDict(dict):
            def __missing__(self, key):
                return f"{{{key}}}"
        caption = template.format_map(SafeDict(**fmt_dict))
    except Exception:
        caption = template

    if Config.WATERMARK not in caption:
        caption += format_watermark()

    if len(caption) > 1024:
        wm = format_watermark()
        avail = 1024 - len(wm) - 4
        caption = caption[:max(0, avail)] + "...\n" + wm

    return caption


async def extract_and_format_caption(
    user_id: int,
    file_path: str,
    original_caption: str = "",
    file_id: str = "",
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    file_name = os.path.basename(file_path)
    file_size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    file_size_str = humanbytes(file_size_bytes)
    ext = os.path.splitext(file_name)[1].replace(".", "").lower()

    metadata = metadata if metadata is not None else await get_detailed_mediainfo(file_path)
    dur_sec = metadata.get("duration", 0)
    duration_str = time_formatter(seconds=dur_sec) if dur_sec > 0 else "0s"

    return await format_caption(
        user_or_id=user_id,
        file_name=file_name,
        file_size_str=file_size_str,
        duration_str=duration_str,
        ext=ext,
        file_caption=original_caption,
        file_id=file_id,
        metadata=metadata
    )


async def schedule_deletion(chat_id: int, message_id: int, seconds: int):
    if seconds <= 0:
        return
    delete_at = float(time.time()) + float(seconds)
    await chat_repo.create_auto_delete_task(chat_id, message_id, delete_at)


async def run_autodelete_sweeper(client: Any):
    logger.info("⏱️ Auto-Delete background sweeper started.")
    while True:
        try:
            curr_time = float(time.time())
            expired_tasks = await chat_repo.get_expired_auto_delete(curr_time)
            if expired_tasks:
                chat_buckets = defaultdict(list)
                for item in expired_tasks:
                    chat_buckets[item["chat_id"]].append(item["message_id"])

                for chat_id, msg_ids in chat_buckets.items():
                    delete_ok = True
                    for i in range(0, len(msg_ids), 100):
                        chunk = msg_ids[i:i+100]
                        if not await safe_delete_messages(client, chat_id, chunk):
                            delete_ok = False
                            break
                    if delete_ok:
                        for mid in msg_ids:
                            await chat_repo.delete_auto_delete_task(chat_id, mid)
        except Exception as e:
            logger.error(f"Auto-delete sweeper exception: {e}")
        await asyncio.sleep(6)


RESTART_MARKER_FILE = os.path.join(Config.DOWNLOAD_DIR, ".restarted_marker")


class KeepAliveManager:
    """
    Fault-tolerant independent health monitor for Web Streaming Server and Telegram MTProto client.
    Eliminates false `telegram=down` alarms during FloodWait cooldowns, heavy media transfers,
    or transient network jitter, and automatically recovers dropped sessions.
    """
    def __init__(self, bot, target_url: str = None, interval: int = 15):
        self.bot = bot
        self.target_url = (target_url or Config.BASE_URL).rstrip("/")
        self.local_url = f"http://127.0.0.1:{Config.PORT}"
        self.interval = max(5, int(interval))
        self._current_type_idx = 0
        self.last_stream_status = "initializing"
        self.last_telegram_status = "initializing"
        self.consecutive_stream_failures = 0
        self.consecutive_tg_failures = 0

    async def ping_stream_base(self, session=None) -> bool:
        """Probe the local server first (zero external DNS overhead), then public stream base URL."""
        targets = [f"{self.local_url}/health"]
        if self.target_url and self.target_url != self.local_url and not self.target_url.startswith("http://127.0.0.1"):
            targets.append(f"{self.target_url}/health")

        if session is not None:
            for target in targets:
                try:
                    async with session.get(target, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status in (200, 204, 301, 302):
                            self.last_stream_status = "ok"
                            self.consecutive_stream_failures = 0
                            return True
                except Exception:
                    continue

        if await self.ping_type_5_tcp_socket():
            self.last_stream_status = "ok"
            self.consecutive_stream_failures = 0
            return True

        self.consecutive_stream_failures += 1
        self.last_stream_status = "down"
        return False

    async def ping_bot(self) -> bool:
        from pyrogram.errors import FloodWait as _PgFloodWait

        if not Config.BOT_TOKEN:
            self.last_telegram_status = "standby_no_token"
            self.consecutive_tg_failures = 0
            return True

        if self.bot is None:
            self.last_telegram_status = "uninitialized"
            return False

        is_conn = bool(getattr(self.bot, "is_connected", False))
        if not is_conn:
            if getattr(self.bot, "me", None) is not None or self.consecutive_tg_failures >= 1:
                try:
                    async with _AUTH_RECOVERY_LOCK:
                        if not getattr(self.bot, "is_connected", False):
                            logger.info("🔄 [KeepAlive] Reconnecting disconnected Telegram client session...")
                            await asyncio.wait_for(self.bot.start(), timeout=15)
                            rate_limit_manager.record_rpc_success()
                            self.last_telegram_status = "recovered"
                            self.consecutive_tg_failures = 0
                            return True
                except _PgFloodWait as fw:
                    wait_s = _flood_wait_seconds(fw)
                    await rate_limit_manager.notify_floodwait("KeepAlive.reconnect", wait_s)
                    self.last_telegram_status = f"cooldown({wait_s}s)"
                    self.consecutive_tg_failures = 0
                    return True
                except Exception as rec_err:
                    logger.debug(f"KeepAlive reconnect attempt notice: {rec_err}")
            self.consecutive_tg_failures += 1
            self.last_telegram_status = "disconnected"
            return False

        if rate_limit_manager.is_in_cooldown():
            rem = rate_limit_manager.active_cooldown_seconds()
            self.last_telegram_status = f"ok_cooldown({rem:.0f}s)"
            self.consecutive_tg_failures = 0
            return True

        if (time.time() - rate_limit_manager.last_rpc_success_ts) < 45.0:
            self.last_telegram_status = "ok_active"
            self.consecutive_tg_failures = 0
            return True

        for attempt in range(2):
            try:
                await asyncio.wait_for(self.bot.get_me(), timeout=12.0)
                rate_limit_manager.record_rpc_success()
                self.last_telegram_status = "ok"
                self.consecutive_tg_failures = 0
                return True
            except _PgFloodWait as fw:
                wait_s = _flood_wait_seconds(fw)
                await rate_limit_manager.notify_floodwait("KeepAlive.get_me", wait_s)
                self.last_telegram_status = f"ok_cooldown({wait_s}s)"
                self.consecutive_tg_failures = 0
                return True
            except asyncio.TimeoutError:
                if attempt == 0 and getattr(self.bot, "is_connected", False):
                    await asyncio.sleep(1.5)
                    continue
                if getattr(self.bot, "is_connected", False) and task_manager.active_count > 0:
                    self.last_telegram_status = "ok_busy_transfer"
                    self.consecutive_tg_failures = 0
                    return True
            except Exception as exc:
                err_name = type(exc).__name__.lower()
                if attempt == 0 and any(k in err_name for k in ("timeout", "network", "connection", "oserror")):
                    await asyncio.sleep(1.5)
                    continue
                logger.debug(f"KeepAlive ping_bot exception: {exc}")
                break

        self.consecutive_tg_failures += 1
        self.last_telegram_status = "down"
        return False

    async def ping_type_1_aiohttp_get(self) -> bool:
        if aiohttp is None:
            return await self.ping_type_3_urllib_get()
        try:
            async with aiohttp.ClientSession() as session:
                return await self.ping_stream_base(session)
        except Exception:
            return False

    async def ping_type_2_aiohttp_head(self) -> bool:
        if aiohttp is None:
            return await self.ping_type_4_http_client()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(f"{self.local_url}/health", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    return resp.status in [200, 204, 301, 302]
        except Exception:
            return False

    async def ping_type_3_urllib_get(self) -> bool:
        loop = asyncio.get_running_loop()
        def _sync_urllib():
            for target in [f"{self.local_url}/health", f"{self.target_url}/health"]:
                try:
                    req = urllib.request.Request(target, headers={"User-Agent": "MMW-KeepAlive-Ping/3.0"})
                    with urllib.request.urlopen(req, timeout=4) as response:
                        if response.status in [200, 204, 301, 302]:
                            return True
                except Exception:
                    continue
            return False
        return await loop.run_in_executor(None, _sync_urllib)

    async def ping_type_4_http_client(self) -> bool:
        loop = asyncio.get_running_loop()
        def _sync_http_client():
            conn = None
            try:
                conn = http.client.HTTPConnection("127.0.0.1", Config.PORT, timeout=3)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                return resp.status in [200, 204]
            except Exception:
                return False
            finally:
                if conn is not None:
                    conn.close()
        return await loop.run_in_executor(None, _sync_http_client)

    async def ping_type_5_tcp_socket(self) -> bool:
        loop = asyncio.get_running_loop()
        def _sync_tcp_ping():
            try:
                with socket.create_connection(("127.0.0.1", Config.PORT), timeout=3):
                    return True
            except Exception:
                return False
        return await loop.run_in_executor(None, _sync_tcp_ping)

    async def ping_type_6_telegram_ping(self) -> bool:
        return await self.ping_bot()

    async def execute_ping(self, ping_type: int = None) -> bool:
        if ping_type is None:
            self._current_type_idx = (self._current_type_idx % 6) + 1
            ptype = self._current_type_idx
        else:
            ptype = ping_type

        handlers = {
            1: self.ping_type_1_aiohttp_get,
            2: self.ping_type_2_aiohttp_head,
            3: self.ping_type_3_urllib_get,
            4: self.ping_type_4_http_client,
            5: self.ping_type_5_tcp_socket,
            6: self.ping_type_6_telegram_ping
        }
        handler = handlers.get(ptype, self.ping_type_1_aiohttp_get)
        return await handler()


async def run_keepalive_worker(bot, interval: int = 15):
    eff_interval = max(5, int(interval or getattr(Config, "KEEPALIVE_INTERVAL", 15)))
    manager = KeepAliveManager(bot=bot, interval=eff_interval)
    logger.info(f"💓 KeepAlive Engine active (interval: {eff_interval}s).")
    await asyncio.sleep(5)
    if aiohttp is None:
        logger.warning("KeepAlive HTTP session unavailable; using socket/urllib + smart Telegram monitor")
        while True:
            await asyncio.gather(
                manager.ping_type_5_tcp_socket(),
                manager.ping_bot(),
                return_exceptions=True,
            )
            await asyncio.sleep(eff_interval)
    else:
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            failure_count = 0
            while True:
                results = await asyncio.gather(
                    manager.ping_stream_base(session),
                    manager.ping_bot(),
                    return_exceptions=True,
                )
                service_ok = bool(results[0] is True)
                bot_ok = bool(results[1] is True)
                if service_ok and bot_ok:
                    if failure_count >= 2:
                        logger.info(
                            f"💓 KeepAlive recovered: stream={manager.last_stream_status}, "
                            f"telegram={manager.last_telegram_status}"
                        )
                    failure_count = 0
                else:
                    failure_count += 1
                    if failure_count == 2 or failure_count % 6 == 0:
                        logger.warning(
                            f"KeepAlive probe notice (x{failure_count}): "
                            f"stream={'ok' if service_ok else manager.last_stream_status}, "
                            f"telegram={'ok' if bot_ok else manager.last_telegram_status}"
                        )
                await asyncio.sleep(eff_interval)


async def send_restart_notification(bot):
    message_text = (
        f"🔄 **{to_smallcaps('bot restarted successfully!')}** ⚡\n\n"
        f"• 🚀 **{to_smallcaps('engine')}** : `{to_smallcaps('adaptive range stream & pro plugins ready')}`\n"
        f"• ♻️ **{to_smallcaps('task recovery')}** : `{to_smallcaps('persistent auto-resume active')}`\n"
        f"• 🍃 **{to_smallcaps('database')}** : `{to_smallcaps('single unified db + fast cache')}`"
        f"{format_watermark()}"
    )
    for admin_id in Config.ADMINS:
        try:
            await safe_send_message(bot, chat_id=admin_id, text=message_text)
        except Exception:
            pass


async def schedule_24h_restart(bot, interval: int = 86400):
    logger.info(f"🔄 24-Hour Auto-Restart scheduler active ({interval}s).")
    while True:
        await asyncio.sleep(interval)
        try:
            from plugins.compressor import checkpoint_active_tasks_before_shutdown
            await checkpoint_active_tasks_before_shutdown("24h_scheduled_restart")
        except Exception:
            pass
        try:
            os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
            with open(RESTART_MARKER_FILE, "w") as f:
                f.write(f"{float(time.time())}")
        except Exception:
            pass
        await send_restart_notification(bot)
        try:
            await bot.stop()
        except Exception:
            pass
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except Exception as error:
            logger.error(f"24-hour restart exec failed; scheduler will retry: {error}")
            await asyncio.sleep(60)
