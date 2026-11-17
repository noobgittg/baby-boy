import os

AUDIO_MIME_TYPES = {
    "audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav", "audio/webm",
    "audio/flac", "audio/x-wav", "audio/x-flac", "audio/3gpp", "audio/3gpp2",
    "audio/amr", "audio/amr-wb", "audio/qcelp", "audio/evrc", "audio/aiff",
    "audio/x-aiff", "audio/alac", "audio/ape", "audio/x-ape", "audio/tta",
    "audio/x-tta", "audio/wavpack", "audio/x-wavpack", "audio/midi", "audio/x-midi",
    "audio/sp-midi", "audio/x-ms-wma", "audio/vnd.wave", "audio/vnd.rn-realaudio",
    "audio/x-pn-realaudio", "audio/basic", "audio/adpcm", "audio/g711-alaw",
    "audio/g711-mlaw", "audio/g722", "audio/g723", "audio/g726", "audio/g729",
    "audio/opus", "audio/x-opus", "audio/vorbis", "audio/eac3", "audio/ac3",
    "audio/dts", "audio/vnd.dts", "audio/vnd.dts.hd", "audio/x-mpegurl", "audio/mpegurl",
    "audio/x-caf", "audio/caf", "audio/x-au", "audio/au", "audio/x-gsm", "audio/gsm",
    "audio/x-speex", "audio/speex", "audio/l8", "audio/l16", "audio/l20", "audio/l24",
    "audio/rtp", "audio/ulaw", "audio/alaw", "audio/aacp", "audio/dtshd", "audio/dsd",
    "audio/x-dsd", "audio/dsf", "audio/dff", "audio/musepack", "audio/x-musepack",
    "audio/tak", "audio/pcm", "audio/x-pcm", "audio/mp1", "audio/mp2", "audio/atrac",
    "audio/atrac3", "application/x-ape", "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
}

VIDEO_MIME_TYPES = {
    "video/mp4", "video/webm", "video/ogg", "video/x-matroska", "video/avi", "video/mpeg",
    "video/h264", "video/h265", "video/hevc", "video/x-h264", "video/x-h265",
    "video/x-hevc", "video/vp8", "video/vp9", "video/av1", "video/3gpp", "video/3gpp2",
    "video/quicktime", "video/x-msvideo", "video/x-flv", "video/x-ms-wmv", "video/x-m4v",
    "video/mp2t", "video/x-f4v", "video/x-fli", "video/x-flc", "video/x-dv", "video/dv",
    "video/x-dirac", "video/x-cdg", "video/iso.segment", "video/vnd.mpegurl", "video/x-ogm",
    "video/x-theora", "video/x-smv", "video/x-sgi-movie", "video/x-ms-asf", "video/x-ms-vob",
    "video/vnd.avi", "video/x-mkv", "video/vnd.dece.hd", "video/vnd.dece.sd",
    "video/vnd.dece.mobile", "video/vnd.dece.mp4", "video/vnd.rn-realvideo",
    "video/x-pn-realvideo", "video/x-av1", "video/x-vp9", "video/mkv", "application/x-matroska",
    "video/mp1s", "video/mp2p", "video/mp2v", "video/vob", "video/m2ts", "video/ts",
    "application/vnd.ms-asf", "application/vnd.rn-realmedia", "application/ogg", "video/mjpeg",
    "video/x-bink", "video/x-smacker", "video/cinepak", "video/avs-video", "video/raw",
    "video/mj2", "application/vnd.apple.mpegurl.video", "application/vnd.ms-sstr+xml",
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml",
}

CODE_EXTENSIONS = {
    ".py": ("python", "text/x-python"),
    ".pyw": ("python", "text/x-python"),
    ".html": ("html", "text/html"),
    ".htm": ("html", "text/html"),
    ".css": ("css", "text/css"),
    ".php": ("php", "application/x-httpd-php"),
    ".phtml": ("php", "application/x-httpd-php"),
    ".js": ("javascript", "application/javascript"),
    ".mjs": ("javascript", "application/javascript"),
    ".ts": ("typescript", "application/typescript"),
    ".json": ("json", "application/json"),
    ".xml": ("xml", "application/xml"),
    ".sql": ("sql", "application/sql"),
    ".sh": ("shell", "application/x-sh"),
    ".yaml": ("yaml", "application/x-yaml"),
    ".yml": ("yaml", "application/x-yaml"),
    ".md": ("markdown", "text/markdown"),
}

CODE_MIME_TYPES = {
    "text/x-python",
    "application/x-python-code",
    "text/x-script.phyton",
    "text/html",
    "application/xhtml+xml",
    "text/css",
    "application/x-httpd-php",
    "application/php",
    "text/x-php",
    "text/php",
    "application/javascript",
    "text/javascript",
    "application/json",
    "application/xml",
    "text/xml",
    "application/sql",
    "application/x-sh",
    "text/x-shellscript",
}


def normalize_mime(value: str | None) -> str:
    return (value or "application/octet-stream").split(";", 1)[0].strip().lower()


def is_audio_mime(value: str | None) -> bool:
    mime = normalize_mime(value)
    return mime.startswith("audio/") or mime in AUDIO_MIME_TYPES


def is_video_mime(value: str | None) -> bool:
    mime = normalize_mime(value)
    return mime.startswith("video/") or mime in VIDEO_MIME_TYPES


def is_code_file(file_name: str | None = None, mime_type: str | None = None) -> bool:
    ext = os.path.splitext(str(file_name or "").strip().lower())[1]
    if ext == ".ts" and is_video_mime(mime_type):
        return False
    if ext in CODE_EXTENSIONS:
        return True
    mime = normalize_mime(mime_type)
    return mime in CODE_MIME_TYPES


def detect_code_language(file_name: str | None = None, mime_type: str | None = None) -> str:
    ext = os.path.splitext(str(file_name or "").strip().lower())[1]
    if ext in CODE_EXTENSIONS and not (ext == ".ts" and is_video_mime(mime_type)):
        return CODE_EXTENSIONS[ext][0]
    mime = normalize_mime(mime_type)
    if "python" in mime:
        return "python"
    if "html" in mime:
        return "html"
    if "css" in mime:
        return "css"
    if "php" in mime:
        return "php"
    if "javascript" in mime:
        return "javascript"
    if "json" in mime:
        return "json"
    return "text"


def media_kind(value: str | None, file_name: str | None = None) -> str:
    mime = normalize_mime(value)
    if is_video_mime(mime):
        return "video"
    if is_audio_mime(mime):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if is_code_file(file_name, mime):
        return "code"
    return "document"


def browser_media_supported(value: str | None, file_name: str | None = None) -> bool:
    mime = normalize_mime(value)
    if is_code_file(file_name, mime):
        return True
    return mime.startswith(("audio/", "video/")) or mime in {
        "application/ogg", "application/vnd.apple.mpegurl", "application/dash+xml", "text/html", "text/css"
    }
