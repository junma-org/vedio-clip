"""
字幕基础模型和 SRT 读写。
只表达字幕内容、时间和全局基础样式，不直接构造 FFmpeg 命令。
"""
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
import re


class SubtitleValidationError(ValueError):
    """字幕参数不合法。"""


_SRT_TIME_RE = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
)


def _as_float(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SubtitleValidationError(f"{field_name}必须是有效数字。")

    if not isfinite(number):
        raise SubtitleValidationError(f"{field_name}必须是有限数字。")

    return number


def _validate_seconds(value, field_name):
    number = _as_float(value, field_name)
    if number < 0:
        raise SubtitleValidationError(f"{field_name}不能小于 0。")
    return number


def _clamp_to_duration(seconds, total_duration):
    if total_duration is None:
        return seconds

    try:
        duration = float(total_duration)
    except (TypeError, ValueError):
        return seconds

    if not isfinite(duration) or duration <= 0:
        return seconds

    return max(0, min(seconds, duration))


def _normalize_text(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class SubtitleEntry:
    start: float
    end: float
    text: str

    def normalized(self):
        start = _validate_seconds(self.start, "字幕开始秒数")
        end = _validate_seconds(self.end, "字幕结束秒数")
        text = _normalize_text(self.text)

        if end <= start:
            raise SubtitleValidationError("字幕结束秒数必须大于开始秒数。")
        if not text:
            raise SubtitleValidationError("字幕文本不能为空。")

        return SubtitleEntry(start, end, text)

    def as_tuple(self):
        entry = self.normalized()
        return (entry.start, entry.end, entry.text)


@dataclass(frozen=True)
class SubtitleStyle:
    font_size: int = 28
    bottom_margin: int = 36

    def normalized(self):
        try:
            font_size = int(self.font_size)
        except (TypeError, ValueError):
            raise SubtitleValidationError("字幕字号必须是整数。")

        try:
            bottom_margin = int(self.bottom_margin)
        except (TypeError, ValueError):
            raise SubtitleValidationError("字幕底部边距必须是整数。")

        if font_size <= 0:
            raise SubtitleValidationError("字幕字号必须大于 0。")
        if bottom_margin < 0:
            raise SubtitleValidationError("字幕底部边距不能小于 0。")

        return SubtitleStyle(font_size=font_size, bottom_margin=bottom_margin)


@dataclass(frozen=True)
class SubtitleTrack:
    entries: tuple = field(default_factory=tuple)
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    enabled: bool = True

    def normalized(self):
        style = self.style.normalized() if self.style is not None else SubtitleStyle()
        if not self.enabled:
            return SubtitleTrack(entries=tuple(), style=style, enabled=False)

        entries = [self._coerce_entry(item).normalized() for item in self.entries or ()]
        entries.sort(key=lambda item: (item.start, item.end, item.text))
        return SubtitleTrack(entries=tuple(entries), style=style, enabled=True)

    def has_entries(self):
        return bool(self.enabled and self.entries)

    def entry_tuples(self):
        return [item.as_tuple() for item in self.normalized().entries]

    @staticmethod
    def _coerce_entry(item):
        if isinstance(item, SubtitleEntry):
            return item

        try:
            start, end, text = item
        except (TypeError, ValueError):
            raise SubtitleValidationError("字幕必须包含开始秒数、结束秒数和文本。")

        return SubtitleEntry(start, end, text)


def parse_srt_timestamp(text):
    """解析 SRT 时间戳为秒。"""
    value = str(text).strip().replace(",", ".")
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        raise SubtitleValidationError(f"无法解析字幕时间: {text}")


def format_srt_timestamp(seconds):
    """把秒数格式化为 SRT 时间戳。"""
    seconds = _validate_seconds(seconds, "字幕秒数")
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def parse_srt_text(text):
    """解析 SRT 文本为字幕条目。"""
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise SubtitleValidationError("字幕文件为空。")

    entries = []
    blocks = re.split(r"\n\s*\n", content)
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.split("\n")]
        time_index = None
        time_match = None

        for index, line in enumerate(lines):
            time_match = _SRT_TIME_RE.search(line)
            if time_match:
                time_index = index
                break

        if time_index is None or time_match is None:
            continue

        subtitle_text = "\n".join(lines[time_index + 1 :])
        entries.append(
            SubtitleEntry(
                parse_srt_timestamp(time_match.group("start")),
                parse_srt_timestamp(time_match.group("end")),
                subtitle_text,
            ).normalized()
        )

    if not entries:
        raise SubtitleValidationError("未找到有效字幕。")

    return tuple(sorted(entries, key=lambda item: (item.start, item.end, item.text)))


def serialize_srt_entries(entries):
    """把字幕条目序列化为 SRT 文本。"""
    track = SubtitleTrack(entries=tuple(entries or ())).normalized()
    lines = []

    for index, entry in enumerate(track.entries, start=1):
        lines.append(str(index))
        lines.append(
            f"{format_srt_timestamp(entry.start)} --> {format_srt_timestamp(entry.end)}"
        )
        lines.extend(entry.text.split("\n"))
        lines.append("")

    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def read_srt_file(path):
    """读取常见编码的 SRT 文件。"""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return parse_srt_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue

    raise SubtitleValidationError("字幕文件编码不支持，请使用 UTF-8 或 GBK。")


def write_srt_file(entries, path):
    """写出 UTF-8 SRT 文件。"""
    Path(path).write_text(serialize_srt_entries(entries), encoding="utf-8")


def add_subtitle_from_marks(existing_entries, in_point, out_point, text, total_duration=None):
    """用入点/出点和文本新增一条字幕，并返回排序后的字幕元组。"""
    if in_point is None:
        raise SubtitleValidationError("请先设置入点。")
    if out_point is None:
        raise SubtitleValidationError("请先设置出点。")

    start = _clamp_to_duration(_validate_seconds(in_point, "入点"), total_duration)
    end = _clamp_to_duration(_validate_seconds(out_point, "出点"), total_duration)
    entry = SubtitleEntry(start, end, text).normalized()
    entries = [SubtitleTrack._coerce_entry(item).normalized() for item in existing_entries or ()]
    entries.append(entry)
    return tuple(sorted(entries, key=lambda item: (item.start, item.end, item.text)))
