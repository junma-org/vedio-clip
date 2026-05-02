"""
达人模式编辑会话状态。
不依赖 Qt，负责保存可撤销的编辑状态并生成统一编辑模型。
"""
from dataclasses import dataclass, field

from edit_model import DeleteRange, EditPlan, OutputOptions
from subtitle_model import SubtitleProject
from timeline_state import TimelineSelection


MAX_UNDO_STATES = 50


def _copy_ranges(ranges):
    copied = []
    for item in ranges or ():
        if isinstance(item, DeleteRange):
            copied.append(item.as_tuple())
            continue
        start, end = item
        copied.append((start, end))
    return tuple(copied)


@dataclass(frozen=True)
class EditorSnapshot:
    delete_ranges: tuple = field(default_factory=tuple)
    expert_delete_ranges: tuple = field(default_factory=tuple)
    expert_selection: TimelineSelection = field(default_factory=lambda: TimelineSelection(0, 0))
    expert_output_resolution: object = None
    source_audio_muted: bool = False
    audio_tracks: tuple = field(default_factory=tuple)
    media_overlays: tuple = field(default_factory=tuple)
    selected_overlay_index: int = -1
    subtitle_project: SubtitleProject = field(default_factory=SubtitleProject)
    subtitle_row: int = -1

    @classmethod
    def from_session(cls, session, subtitle_row=-1):
        return cls(
            delete_ranges=_copy_ranges(session.delete_ranges),
            expert_delete_ranges=_copy_ranges(session.expert_delete_ranges),
            expert_selection=session.expert_selection,
            expert_output_resolution=session.expert_output_resolution,
            source_audio_muted=bool(session.source_audio_muted),
            audio_tracks=tuple(session.audio_tracks or ()),
            media_overlays=tuple(session.media_overlays or ()),
            selected_overlay_index=int(session.selected_overlay_index),
            subtitle_project=session.subtitle_project,
            subtitle_row=int(subtitle_row),
        )


@dataclass
class EditorSession:
    delete_ranges: list = field(default_factory=list)
    expert_delete_ranges: list = field(default_factory=list)
    expert_selection: TimelineSelection = field(default_factory=lambda: TimelineSelection(0, 0))
    expert_output_resolution: object = None
    source_audio_muted: bool = False
    audio_tracks: list = field(default_factory=list)
    media_overlays: list = field(default_factory=list)
    selected_overlay_index: int = -1
    subtitle_project: SubtitleProject = field(default_factory=SubtitleProject)
    undo_stack: list = field(default_factory=list)
    undo_limit: int = MAX_UNDO_STATES

    def snapshot(self, subtitle_row=-1):
        return EditorSnapshot.from_session(self, subtitle_row=subtitle_row)

    def restore(self, snapshot):
        self.delete_ranges = list(snapshot.delete_ranges)
        self.expert_delete_ranges = list(snapshot.expert_delete_ranges)
        self.expert_selection = snapshot.expert_selection
        self.expert_output_resolution = snapshot.expert_output_resolution
        self.source_audio_muted = bool(snapshot.source_audio_muted)
        self.audio_tracks = list(snapshot.audio_tracks)
        self.media_overlays = list(snapshot.media_overlays)
        self.selected_overlay_index = int(snapshot.selected_overlay_index)
        self.subtitle_project = snapshot.subtitle_project.normalized()

    def push_undo_state(self, snapshot):
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack = self.undo_stack[-self.undo_limit:]

    def pop_undo_state(self):
        if not self.undo_stack:
            return None
        return self.undo_stack.pop()

    def clear_undo_stack(self):
        self.undo_stack = []

    def has_undo(self):
        return bool(self.undo_stack)

    def to_edit_plan(self):
        return EditPlan(
            delete_ranges=tuple(DeleteRange(start, end) for start, end in self.expert_delete_ranges),
            output=OutputOptions(resolution=self.expert_output_resolution),
            subtitles=self.subtitle_project,
            source_audio_muted=self.source_audio_muted,
            audio_tracks=tuple(self.audio_tracks),
            media_overlays=tuple(self.media_overlays),
        )

    def to_transcription_plan(self, has_audio=True):
        return EditPlan(
            source_audio_muted=self.source_audio_muted,
            audio_tracks=tuple(self.audio_tracks),
            has_audio=has_audio,
        )
