"""
时间轴纯逻辑。
不依赖 Qt，便于在无 GUI 环境下测试达人模式的选区和轨道行为。
"""
from dataclasses import dataclass
from math import isfinite

from edit_model import DeleteRange, normalize_delete_ranges
from subtitle_model import SubtitleCue, SubtitleValidationError


class TimelineStateError(ValueError):
    """时间轴参数不合法。"""


def _as_seconds(value, field_name):
    if value is None:
        raise TimelineStateError(f"{field_name}不能为空。")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise TimelineStateError(f"{field_name}必须是有效数字。")
    if not isfinite(seconds):
        raise TimelineStateError(f"{field_name}必须是有限数字。")
    return max(0, seconds)


def _clip_to_duration(seconds, total_duration):
    if total_duration is None:
        return max(0, seconds)

    try:
        duration = float(total_duration)
    except (TypeError, ValueError):
        return max(0, seconds)

    if not isfinite(duration) or duration <= 0:
        return max(0, seconds)

    return max(0, min(seconds, duration))


@dataclass(frozen=True)
class TimelineSelection:
    start: float
    end: float

    def normalized(self, total_duration=None):
        start = _clip_to_duration(_as_seconds(self.start, "选区开始"), total_duration)
        end = _clip_to_duration(_as_seconds(self.end, "选区结束"), total_duration)
        if end < start:
            start, end = end, start
        return TimelineSelection(start=start, end=end)

    @property
    def is_range(self):
        return self.end > self.start

    @property
    def duration(self):
        return max(0, self.end - self.start)

    def collapsed_to(self, seconds, total_duration=None):
        point = _clip_to_duration(_as_seconds(seconds, "播放头"), total_duration)
        return TimelineSelection(point, point)


def selection_from_points(start, end, total_duration=None):
    return TimelineSelection(start, end).normalized(total_duration=total_duration)


def resize_timed_range(start, end, edge, seconds, total_duration=None, min_duration=0.05):
    current = TimelineSelection(start, end).normalized(total_duration=total_duration)
    duration_floor = max(0.001, _as_seconds(min_duration, "最小时长"))
    target = _clip_to_duration(_as_seconds(seconds, "调整时间"), total_duration)

    if edge == "start":
        return TimelineSelection(min(target, current.end - duration_floor), current.end).normalized(
            total_duration=total_duration
        )
    if edge == "end":
        return TimelineSelection(current.start, max(target, current.start + duration_floor)).normalized(
            total_duration=total_duration
        )

    raise TimelineStateError("只能调整开始或结束边界。")


def move_timed_range(start, end, delta, total_duration=None):
    current = TimelineSelection(start, end).normalized(total_duration=total_duration)
    offset = _as_seconds(delta, "移动偏移")
    if float(delta) < 0:
        offset = float(delta)

    new_start = current.start + offset
    new_end = current.end + offset
    if total_duration is not None:
        duration = _clip_to_duration(_as_seconds(total_duration, "视频时长"), None)
        if new_start < 0:
            new_end -= new_start
            new_start = 0
        if new_end > duration:
            shift = new_end - duration
            new_start -= shift
            new_end = duration
    return TimelineSelection(max(0, new_start), max(0, new_end)).normalized(total_duration=total_duration)


def add_delete_range_from_selection(existing_ranges, selection, total_duration=None):
    current = selection.normalized(total_duration=total_duration)
    if not current.is_range:
        raise TimelineStateError("请先在时间轴上拖出一个选区。")

    raw_ranges = []
    for item in existing_ranges or ():
        if isinstance(item, DeleteRange):
            raw_ranges.append(item.as_tuple())
        else:
            raw_ranges.append(item)
    raw_ranges.append((current.start, current.end))
    return tuple(DeleteRange(start, end) for start, end in normalize_delete_ranges(raw_ranges, total_duration))


def delete_current_frame(playhead, fps, existing_ranges, total_duration=None):
    current_time = _clip_to_duration(_as_seconds(playhead, "播放头"), total_duration)
    try:
        frame_rate = float(fps)
    except (TypeError, ValueError):
        frame_rate = 0
    if not isfinite(frame_rate) or frame_rate <= 0:
        frame_rate = 30.0

    frame_duration = 1.0 / frame_rate
    end = current_time + frame_duration
    if total_duration is not None:
        end = _clip_to_duration(end, total_duration)
        if end <= current_time and total_duration:
            current_time = max(0, float(total_duration) - frame_duration)
            end = _clip_to_duration(current_time + frame_duration, total_duration)

    selection = TimelineSelection(current_time, max(current_time + 0.001, end))
    return add_delete_range_from_selection(existing_ranges, selection, total_duration=total_duration)


def add_subtitle_from_selection_or_playhead(
    existing_cues,
    selection,
    playhead,
    text,
    total_duration=None,
    default_duration=2.0,
    style_name="short_speech_bottom",
    source_kind="manual",
    raw_tags="",
):
    cue_text = str(text or "").strip()
    if not cue_text:
        raise SubtitleValidationError("字幕文本不能为空。")

    current = selection.normalized(total_duration=total_duration) if selection else None
    if current and current.is_range:
        start = current.start
        end = current.end
    else:
        start = _clip_to_duration(_as_seconds(playhead, "播放头"), total_duration)
        duration = max(0.1, _as_seconds(default_duration, "默认字幕时长"))
        end = _clip_to_duration(start + duration, total_duration)
        if end <= start:
            if total_duration is not None:
                end = _clip_to_duration(start, total_duration)
                start = max(0, end - duration)
            else:
                end = start + max(0.1, duration)

    cue = SubtitleCue(
        start=start,
        end=end,
        text=cue_text,
        style_name=style_name,
        source_kind=source_kind,
        raw_tags=raw_tags,
    ).normalized()

    cues = []
    for item in existing_cues or ():
        if isinstance(item, SubtitleCue):
            cues.append(item.normalized())
        else:
            try:
                cues.append(SubtitleCue(*item).normalized())
            except (TypeError, ValueError):
                raise SubtitleValidationError("字幕必须包含开始秒数、结束秒数和文本。")
    cues.append(cue)
    return tuple(sorted(cues, key=lambda item: (item.start, item.end, item.text))), cue
