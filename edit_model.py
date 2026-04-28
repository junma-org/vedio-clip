"""
统一编辑模型。
负责表达剪辑意图、参数归一化和基础校验，不直接构造 FFmpeg 命令。
"""
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Optional, Tuple

from subtitle_model import SubtitleProject, SubtitleValidationError


class PlanValidationError(ValueError):
    """编辑计划参数不合法。"""


MAX_AUDIO_TRACKS = 2


def _as_float(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PlanValidationError(f"{field_name}必须是有效数字。")

    if not isfinite(number):
        raise PlanValidationError(f"{field_name}必须是有限数字。")

    return number


def _validate_seconds(value, field_name):
    number = _as_float(value, field_name)
    if number < 0:
        raise PlanValidationError(f"{field_name}不能小于 0。")
    return number


def normalize_delete_ranges(ranges, total_duration=None):
    """裁剪、排序并合并删除区间，兼容旧的元组列表调用。"""
    normalized = []
    duration = None
    if total_duration is not None:
        duration = max(0, _as_float(total_duration, "视频时长"))

    for item in ranges or []:
        if isinstance(item, DeleteRange):
            start, end = item.start, item.end
        else:
            try:
                start, end = item
            except (TypeError, ValueError):
                continue

        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue

        if not isfinite(start) or not isfinite(end):
            continue

        if duration is not None:
            start = max(0, min(start, duration))
            end = max(0, min(end, duration))
        else:
            start = max(0, start)
            end = max(0, end)

        if end > start:
            normalized.append((start, end))

    normalized.sort(key=lambda item: item[0])

    merged = []
    for start, end in normalized:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    return [(start, end) for start, end in merged]


@dataclass(frozen=True)
class DeleteRange:
    start: float
    end: float

    def validate(self):
        start = _validate_seconds(self.start, "删除区间开始秒数")
        end = _validate_seconds(self.end, "删除区间结束秒数")
        if end <= start:
            raise PlanValidationError("删除区间结束秒数必须大于开始秒数。")
        return DeleteRange(start, end)

    def as_tuple(self):
        return (float(self.start), float(self.end))


@dataclass(frozen=True)
class OutputOptions:
    resolution: Optional[Tuple[int, int]] = None
    video_bitrate: Optional[str] = None
    audio_bitrate: str = "128k"

    def normalized(self):
        resolution = None
        if self.resolution is not None:
            try:
                width, height = self.resolution
            except (TypeError, ValueError):
                raise PlanValidationError("输出分辨率必须包含宽和高。")

            try:
                width = int(width)
                height = int(height)
            except (TypeError, ValueError):
                raise PlanValidationError("输出分辨率必须是整数。")

            if width <= 0 or height <= 0:
                raise PlanValidationError("输出分辨率必须大于 0。")
            resolution = (width, height)

        audio_bitrate = str(self.audio_bitrate or "128k").strip()
        if not audio_bitrate:
            raise PlanValidationError("音频码率不能为空。")

        video_bitrate = self.video_bitrate
        if video_bitrate is not None:
            video_bitrate = str(video_bitrate).strip()
            if not video_bitrate:
                video_bitrate = None

        return OutputOptions(
            resolution=resolution,
            video_bitrate=video_bitrate,
            audio_bitrate=audio_bitrate,
        )


@dataclass(frozen=True)
class AudioTrack:
    path: str
    volume: float = 1.0

    def validate(self):
        path = str(self.path or "").strip()
        if not path:
            raise PlanValidationError("音频文件不能为空。")

        volume = _as_float(self.volume, "音轨音量")
        if volume < 0 or volume > 2:
            raise PlanValidationError("音轨音量必须在 0 到 2 之间。")

        return AudioTrack(path=path, volume=volume)


@dataclass(frozen=True)
class EditPlan:
    skip_seconds: float = 0
    delete_ranges: Tuple[DeleteRange, ...] = field(default_factory=tuple)
    output: OutputOptions = field(default_factory=OutputOptions)
    subtitles: SubtitleProject = field(default_factory=SubtitleProject)
    has_audio: bool = True
    source_audio_muted: bool = False
    audio_tracks: Tuple[AudioTrack, ...] = field(default_factory=tuple)

    def normalized(self, total_duration=None):
        skip_seconds = _validate_seconds(self.skip_seconds, "剪掉开头秒数")
        ranges = [self._coerce_delete_range(item).validate() for item in self.delete_ranges]
        normalized_ranges = normalize_delete_ranges(
            [item.as_tuple() for item in ranges],
            total_duration=total_duration,
        )
        try:
            subtitles = self.subtitles.normalized() if self.subtitles is not None else SubtitleProject()
        except SubtitleValidationError as exc:
            raise PlanValidationError(str(exc))

        audio_tracks = tuple(self._coerce_audio_track(item).validate() for item in (self.audio_tracks or ()))
        if len(audio_tracks) > MAX_AUDIO_TRACKS:
            raise PlanValidationError(f"最多只能添加 {MAX_AUDIO_TRACKS} 条音频。")

        return EditPlan(
            skip_seconds=skip_seconds,
            delete_ranges=tuple(DeleteRange(start, end) for start, end in normalized_ranges),
            output=self.output.normalized(),
            subtitles=subtitles,
            has_audio=bool(self.has_audio),
            source_audio_muted=bool(self.source_audio_muted),
            audio_tracks=audio_tracks,
        )

    def validate(self, total_duration=None):
        plan = self.normalized(total_duration=total_duration)
        if total_duration is not None:
            duration = _validate_seconds(total_duration, "视频时长")
            if duration <= 0:
                raise PlanValidationError("视频时长必须大于 0。")
            if plan.output_duration(duration) <= 0:
                raise PlanValidationError("删除范围覆盖了整个视频，请调整秒数。")
        return plan

    def output_duration(self, total_duration):
        duration = _validate_seconds(total_duration, "视频时长")
        plan = self.normalized(total_duration=duration)
        removal_ranges = []
        if plan.skip_seconds > 0:
            removal_ranges.append((0, plan.skip_seconds))
        removal_ranges.extend(item.as_tuple() for item in plan.delete_ranges)

        merged_ranges = normalize_delete_ranges(removal_ranges, total_duration=duration)
        removed_duration = sum(end - start for start, end in merged_ranges)
        return max(0, duration - removed_duration)

    def with_has_audio(self, has_audio):
        return replace(self, has_audio=bool(has_audio))

    def source_audio_enabled(self):
        return bool(self.has_audio and not self.source_audio_muted)

    def has_output_audio(self):
        plan = self.normalized()
        return bool(plan.source_audio_enabled() or any(track.volume > 0 for track in plan.audio_tracks))

    def delete_range_tuples(self):
        return [item.as_tuple() for item in self.delete_ranges]

    @staticmethod
    def _coerce_delete_range(item):
        if isinstance(item, DeleteRange):
            return item

        try:
            start, end = item
        except (TypeError, ValueError):
            raise PlanValidationError("删除区间必须包含开始秒数和结束秒数。")

        return DeleteRange(start, end)

    @staticmethod
    def _coerce_audio_track(item):
        if isinstance(item, AudioTrack):
            return item
        if isinstance(item, dict):
            return AudioTrack(item.get("path", ""), item.get("volume", 1.0))

        try:
            path, volume = item
        except (TypeError, ValueError):
            raise PlanValidationError("音轨必须包含文件路径和音量。")

        return AudioTrack(path, volume)
