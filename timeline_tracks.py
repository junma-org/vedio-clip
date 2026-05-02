"""
时间轴轨道布局纯逻辑。
只描述当前已有轨道，避免绘制和命中检测继续散落在控件里。
"""
from dataclasses import dataclass


TRACK_OVERLAY = "overlay"
TRACK_VIDEO = "video"
TRACK_SUBTITLE = "subtitle"


@dataclass(frozen=True)
class TimelineTrackSpec:
    key: str
    label: str
    top_offset: float
    height: float
    radius: float
    background: str


TIMELINE_TRACKS = (
    TimelineTrackSpec(TRACK_OVERLAY, "叠加轨", 0, 28, 7, "#dbe7f2"),
    TimelineTrackSpec(TRACK_VIDEO, "视频轨", 42, 44, 8, "#dce6f2"),
    TimelineTrackSpec(TRACK_SUBTITLE, "字幕轨", 102, 34, 8, "#e9eef5"),
)
_TRACK_BY_KEY = {track.key: track for track in TIMELINE_TRACKS}


def track_spec(track_key):
    return _TRACK_BY_KEY[track_key]


def track_rect_tuple(content_rect, track_key):
    left, top, width, _height = content_rect
    spec = track_spec(track_key)
    return (left, top + spec.top_offset, width, spec.height)


def clip_visible_range(start, end, visible_start, visible_end):
    if end < visible_start or start > visible_end:
        return None
    return max(start, visible_start), min(end, visible_end)
