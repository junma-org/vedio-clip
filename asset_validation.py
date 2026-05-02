"""
本地素材校验。
不依赖 Qt，只负责路径、后缀和媒体类型判断。
"""
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mkv",
    ".mov",
    ".flv",
    ".wmv",
    ".webm",
    ".m4v",
}
SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".wav",
    ".wma",
}
SUPPORTED_IMAGE_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}


_SUPPORTED_EXTENSIONS_BY_KIND = {
    "audio": SUPPORTED_AUDIO_EXTENSIONS,
    "image": SUPPORTED_IMAGE_EXTENSIONS,
    "video": SUPPORTED_VIDEO_EXTENSIONS,
}


@dataclass(frozen=True)
class MediaAsset:
    path: Path
    media_kind: str


class AssetValidationError(ValueError):
    def __init__(self, title, message):
        super().__init__(message)
        self.title = title
        self.message = message


def detect_media_kind(file_path):
    suffix = Path(file_path).suffix.lower()
    for media_kind, extensions in _SUPPORTED_EXTENSIONS_BY_KIND.items():
        if suffix in extensions:
            return media_kind
    return None


def validate_media_asset(file_path, media_kind, missing_message, unsupported_message):
    normalized_kind = str(media_kind or "").strip().lower()
    if normalized_kind not in _SUPPORTED_EXTENSIONS_BY_KIND:
        raise AssetValidationError("格式不支持", unsupported_message)

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise AssetValidationError("文件无效", missing_message)
    if detect_media_kind(path) != normalized_kind:
        raise AssetValidationError("格式不支持", unsupported_message)

    return MediaAsset(path=path, media_kind=normalized_kind)


def validate_video_file(
    file_path,
    missing_message="选择的文件不存在，或不是普通文件。",
    unsupported_message="请选择常见视频文件，例如 MP4、AVI、MKV、MOV。",
):
    return validate_media_asset(file_path, "video", missing_message, unsupported_message)


def validate_audio_file(
    file_path,
    missing_message="选择的音频文件不存在。",
    unsupported_message="请选择常见音频文件，例如 MP3、WAV、M4A。",
):
    return validate_media_asset(file_path, "audio", missing_message, unsupported_message)


def validate_image_file(
    file_path,
    missing_message="选择的图片文件不存在。",
    unsupported_message="请选择常见图片文件，例如 PNG、JPG、WEBP。",
):
    return validate_media_asset(file_path, "image", missing_message, unsupported_message)
