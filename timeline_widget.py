"""
达人模式时间轴控件。
负责展示删除区间、字幕块、播放头和可拖拽选区。
"""
from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from edit_model import OverlayClip
from timeline_state import (
    TimelineSelection,
    move_overlay_clip,
    move_timed_range,
    resize_overlay_clip,
    resize_timed_range,
    selection_from_points,
)


class TimelineWidget(QWidget):
    playheadChanged = Signal(float)
    selectionChanged = Signal(float, float)
    subtitleActivated = Signal(int)
    subtitleTimingPreviewed = Signal(int, float, float, float)
    overlayActivated = Signal(int)
    overlayTimingPreviewed = Signal(int, object, float)
    overlayTimingChanged = Signal(int, object, float)
    viewStartChanged = Signal(float)
    zoomStepRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(164)
        self.setMouseTracking(True)

        self._duration = 0.0
        self._playhead = 0.0
        self._zoom = 1.0
        self._view_start = 0.0
        self._selection = TimelineSelection(0, 0)
        self._delete_ranges = []
        self._overlay_clips = []
        self._selected_overlay_index = -1
        self._subtitle_cues = []
        self._selected_subtitle_index = -1

        self._drag_mode = None
        self._drag_anchor = None
        self._drag_original_selection = None
        self._drag_subtitle_index = -1
        self._drag_overlay_index = -1
        self._drag_original_overlay = None
        self._subtitle_timing_preview = None
        self._overlay_timing_preview = None
        self._press_pos = QPoint()
        self._handle_radius = 6

    def set_duration(self, seconds):
        self._duration = max(0.0, float(seconds or 0.0))
        self._clamp_view_start()
        self.update()

    def set_playhead(self, seconds):
        if self._duration > 0:
            self._playhead = max(0.0, min(float(seconds or 0.0), self._duration))
        else:
            self._playhead = max(0.0, float(seconds or 0.0))
        self._ensure_time_visible(self._playhead)
        self.update()

    def _preview_playhead(self, seconds):
        if self._duration > 0:
            self._playhead = max(0.0, min(float(seconds or 0.0), self._duration))
        else:
            self._playhead = max(0.0, float(seconds or 0.0))

    def set_zoom(self, zoom):
        self._zoom = max(1.0, min(float(zoom or 1.0), 12.0))
        self._ensure_time_visible(self._playhead, center=True)
        self.update()

    @property
    def zoom(self):
        return self._zoom

    def set_view_start(self, seconds):
        self._view_start = max(0.0, float(seconds or 0.0))
        self._clamp_view_start()
        self.update()

    @property
    def view_start(self):
        return self._view_start

    def visible_duration(self):
        if self._duration <= 0:
            return 1.0
        return max(0.05, self._duration / max(1.0, self._zoom))

    def _visible_end(self):
        return self._view_start + self.visible_duration()

    def _clamp_view_start(self):
        if self._duration <= 0 or self._zoom <= 1.0:
            self._view_start = 0.0
            return
        max_start = max(0.0, self._duration - self.visible_duration())
        self._view_start = max(0.0, min(self._view_start, max_start))

    def _ensure_time_visible(self, seconds, center=False):
        if self._duration <= 0 or self._zoom <= 1.0:
            self._view_start = 0.0
            return
        visible = self.visible_duration()
        seconds = max(0.0, min(float(seconds or 0.0), self._duration))
        if center:
            self._view_start = seconds - visible / 2
            self._clamp_view_start()
            return
        margin = visible * 0.08
        if seconds < self._view_start:
            self._view_start = seconds - margin
            self._clamp_view_start()
        elif seconds > self._visible_end():
            self._view_start = seconds - visible + margin
            self._clamp_view_start()

    @property
    def playhead(self):
        return self._playhead

    def set_selection(self, selection):
        self._selection = selection.normalized(self._duration or None) if selection else TimelineSelection(0, 0)
        self.update()

    def set_delete_ranges(self, delete_ranges):
        self._delete_ranges = list(delete_ranges or [])
        self.update()

    def set_overlay_clips(self, clips):
        self._overlay_clips = [clip.validate() if isinstance(clip, OverlayClip) else OverlayClip(*clip).validate() for clip in (clips or [])]
        self._overlay_timing_preview = None
        self.update()

    def set_selected_overlay_index(self, index):
        self._selected_overlay_index = int(index)
        self.update()

    def set_subtitle_cues(self, cues):
        self._subtitle_cues = list(cues or [])
        self._subtitle_timing_preview = None
        self.update()

    def set_selected_subtitle_index(self, index):
        self._selected_subtitle_index = int(index)
        self.update()

    def _content_rect(self):
        return self.rect().adjusted(14, 12, -14, -12)

    def _video_track_rect(self):
        rect = self._content_rect()
        return QRectF(rect.left(), rect.top() + 42, rect.width(), 44)

    def _overlay_track_rect(self):
        rect = self._content_rect()
        return QRectF(rect.left(), rect.top(), rect.width(), 28)

    def _subtitle_track_rect(self):
        rect = self._content_rect()
        return QRectF(rect.left(), rect.top() + 102, rect.width(), 34)

    def _time_to_x(self, seconds):
        rect = self._content_rect()
        if self._duration <= 0 or rect.width() <= 0:
            return rect.left()
        ratio = (float(seconds) - self._view_start) / self.visible_duration()
        return rect.left() + ratio * rect.width()

    def _x_to_time(self, x_pos):
        rect = self._content_rect()
        if self._duration <= 0 or rect.width() <= 0:
            return 0.0
        ratio = (x_pos - rect.left()) / rect.width()
        return max(0.0, min(self._view_start + max(0.0, min(ratio, 1.0)) * self.visible_duration(), self._duration))

    def _selection_handles(self):
        if not self._selection.is_range:
            return None
        start_x = self._time_to_x(self._selection.start)
        end_x = self._time_to_x(self._selection.end)
        return start_x, end_x

    def _subtitle_times(self, index, cue):
        if self._subtitle_timing_preview and self._subtitle_timing_preview[0] == index:
            return self._subtitle_timing_preview[1], self._subtitle_timing_preview[2]
        return cue.start, cue.end

    def _overlay_clip(self, index):
        if self._overlay_timing_preview and self._overlay_timing_preview[0] == index:
            return self._overlay_timing_preview[1]
        return self._overlay_clips[index]

    def _visible_block_rect(self, start, end, track_rect, min_width=8):
        visible_start = self._view_start
        visible_end = self._visible_end()
        if end < visible_start or start > visible_end:
            return None
        start_x = self._time_to_x(max(start, visible_start))
        end_x = self._time_to_x(min(end, visible_end))
        return QRectF(start_x, track_rect.top(), max(min_width, end_x - start_x), track_rect.height())

    def _overlay_hit_at(self, pos):
        overlay_rect = self._overlay_track_rect()
        if not overlay_rect.contains(pos):
            return -1, None

        for index in reversed(range(len(self._overlay_clips))):
            clip = self._overlay_clip(index)
            block = self._visible_block_rect(clip.start, clip.end, overlay_rect, min_width=10)
            if block is None or not block.contains(pos):
                continue
            start_x = self._time_to_x(clip.start)
            end_x = self._time_to_x(clip.end)
            if abs(pos.x() - start_x) <= self._handle_radius:
                return index, "start"
            if abs(pos.x() - end_x) <= self._handle_radius:
                return index, "end"
            return index, None
        return -1, None

    def _subtitle_hit_at(self, pos):
        subtitle_rect = self._subtitle_track_rect()
        if not subtitle_rect.contains(pos):
            return -1, None

        for index, cue in enumerate(self._subtitle_cues):
            start, end = self._subtitle_times(index, cue)
            block = self._visible_block_rect(start, end, subtitle_rect, min_width=8)
            if block is None or not block.contains(pos):
                continue
            start_x = self._time_to_x(start)
            end_x = self._time_to_x(end)
            if abs(pos.x() - start_x) <= self._handle_radius:
                return index, "start"
            if abs(pos.x() - end_x) <= self._handle_radius:
                return index, "end"
            return index, None
        return -1, None

    def _subtitle_index_at(self, pos):
        index, _edge = self._subtitle_hit_at(pos)
        return index

    def _maybe_update_cursor(self, pos):
        overlay_index, overlay_edge = self._overlay_hit_at(pos)
        if overlay_index >= 0:
            self.setCursor(QCursor(Qt.SizeHorCursor if overlay_edge else Qt.OpenHandCursor))
            return
        handles = self._selection_handles()
        if handles:
            start_x, end_x = handles
            if abs(pos.x() - start_x) <= self._handle_radius or abs(pos.x() - end_x) <= self._handle_radius:
                self.setCursor(QCursor(Qt.SizeHorCursor))
                return
        subtitle_index, subtitle_edge = self._subtitle_hit_at(pos)
        if subtitle_index >= 0:
            self.setCursor(QCursor(Qt.SizeHorCursor if subtitle_edge else Qt.PointingHandCursor))
            return
        self.setCursor(QCursor(Qt.ArrowCursor))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._duration <= 0:
            super().mousePressEvent(event)
            return

        pos = event.position().toPoint()
        self._press_pos = pos
        self._maybe_update_cursor(pos)

        overlay_index, overlay_edge = self._overlay_hit_at(pos)
        if overlay_index >= 0:
            if self._overlay_timing_preview and self._overlay_timing_preview[0] != overlay_index:
                self._overlay_timing_preview = None
            self.overlayActivated.emit(overlay_index)
            clip = self._overlay_clip(overlay_index)
            self._selected_overlay_index = overlay_index
            self._selection = TimelineSelection(clip.start, clip.end)
            self.selectionChanged.emit(clip.start, clip.end)
            self._drag_overlay_index = overlay_index
            self._drag_original_overlay = clip
            self._drag_anchor = self._x_to_time(pos.x())
            if overlay_edge == "start":
                self._drag_mode = "overlay_resize_start"
                self._preview_playhead(clip.start)
                self.playheadChanged.emit(clip.start)
            elif overlay_edge == "end":
                self._drag_mode = "overlay_resize_end"
                self._preview_playhead(clip.end)
                self.playheadChanged.emit(clip.end)
            else:
                self._drag_mode = "overlay_move"
                self._preview_playhead(clip.start)
                self.playheadChanged.emit(clip.start)
            self.update()
            return

        subtitle_index, subtitle_edge = self._subtitle_hit_at(pos)
        if subtitle_index >= 0:
            if self._subtitle_timing_preview and self._subtitle_timing_preview[0] != subtitle_index:
                self._subtitle_timing_preview = None
            self.subtitleActivated.emit(subtitle_index)
            cue = self._subtitle_cues[subtitle_index]
            self._selected_subtitle_index = subtitle_index
            self._selection = TimelineSelection(cue.start, cue.end)
            self.selectionChanged.emit(cue.start, cue.end)
            self._drag_subtitle_index = subtitle_index
            self._drag_original_selection = self._selection
            self._drag_anchor = self._x_to_time(pos.x())
            if subtitle_edge == "start":
                self._drag_mode = "subtitle_resize_start"
                self._preview_playhead(cue.start)
                self.playheadChanged.emit(cue.start)
            elif subtitle_edge == "end":
                self._drag_mode = "subtitle_resize_end"
                self._preview_playhead(cue.end)
                self.playheadChanged.emit(cue.end)
            else:
                self._drag_mode = "subtitle_move"
                self._preview_playhead(cue.start)
                self.playheadChanged.emit(cue.start)
            self.update()
            return

        handles = self._selection_handles()
        if handles:
            start_x, end_x = handles
            if abs(pos.x() - start_x) <= self._handle_radius:
                self._drag_mode = "resize_start"
                self._drag_original_selection = self._selection
                return
            if abs(pos.x() - end_x) <= self._handle_radius:
                self._drag_mode = "resize_end"
                self._drag_original_selection = self._selection
                return

        seconds = self._x_to_time(pos.x())
        self._subtitle_timing_preview = None
        self._drag_mode = "select"
        self._drag_anchor = seconds
        self._drag_original_selection = None
        self._selection = TimelineSelection(seconds, seconds)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if not self._drag_mode:
            self._maybe_update_cursor(pos)
            super().mouseMoveEvent(event)
            return

        seconds = self._x_to_time(pos.x())
        if self._drag_mode.startswith("overlay_"):
            self._preview_overlay_timing(seconds)
            return
        if self._drag_mode.startswith("subtitle_"):
            self._preview_subtitle_timing(seconds)
            return

        if self._drag_mode == "select":
            self._selection = selection_from_points(self._drag_anchor, seconds, self._duration or None)
        elif self._drag_mode == "resize_start":
            self._selection = selection_from_points(seconds, self._selection.end, self._duration or None)
        elif self._drag_mode == "resize_end":
            self._selection = selection_from_points(self._selection.start, seconds, self._duration or None)
        self._preview_playhead(seconds)
        self.playheadChanged.emit(seconds)
        self.update()

    def _preview_overlay_timing(self, seconds):
        if self._drag_overlay_index < 0 or self._drag_original_overlay is None:
            return

        original = self._drag_original_overlay
        playhead = seconds
        if self._drag_mode == "overlay_resize_start":
            clip = resize_overlay_clip(
                original,
                "start",
                seconds,
                total_duration=self._duration or None,
            )
            playhead = clip.start
        elif self._drag_mode == "overlay_resize_end":
            clip = resize_overlay_clip(
                original,
                "end",
                seconds,
                total_duration=self._duration or None,
            )
            playhead = clip.end
        else:
            delta = seconds - float(self._drag_anchor or 0.0)
            clip = move_overlay_clip(
                original,
                delta,
                total_duration=self._duration or None,
            )
            playhead = clip.start

        self._selection = TimelineSelection(clip.start, clip.end)
        self._overlay_timing_preview = (self._drag_overlay_index, clip)
        self._preview_playhead(playhead)
        self.overlayTimingPreviewed.emit(self._drag_overlay_index, clip, playhead)
        self.update()

    def _preview_subtitle_timing(self, seconds):
        if self._drag_subtitle_index < 0 or self._drag_original_selection is None:
            return

        original = self._drag_original_selection
        playhead = seconds
        if self._drag_mode == "subtitle_resize_start":
            selection = resize_timed_range(
                original.start,
                original.end,
                "start",
                seconds,
                total_duration=self._duration or None,
            )
            playhead = selection.start
        elif self._drag_mode == "subtitle_resize_end":
            selection = resize_timed_range(
                original.start,
                original.end,
                "end",
                seconds,
                total_duration=self._duration or None,
            )
            playhead = selection.end
        else:
            delta = seconds - float(self._drag_anchor or 0.0)
            selection = move_timed_range(
                original.start,
                original.end,
                delta,
                total_duration=self._duration or None,
            )
            playhead = selection.start

        self._selection = selection
        self._subtitle_timing_preview = (self._drag_subtitle_index, selection.start, selection.end)
        self._preview_playhead(playhead)
        self.subtitleTimingPreviewed.emit(self._drag_subtitle_index, selection.start, selection.end, playhead)
        self.playheadChanged.emit(playhead)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._drag_mode:
            super().mouseReleaseEvent(event)
            return

        released_time = self._x_to_time(event.position().x())
        moved = abs(event.position().toPoint().x() - self._press_pos.x()) >= 4

        was_subtitle_drag = self._drag_mode and self._drag_mode.startswith("subtitle_")
        was_overlay_drag = self._drag_mode and self._drag_mode.startswith("overlay_")

        if was_overlay_drag:
            if not moved:
                self._overlay_timing_preview = None
            elif self._overlay_timing_preview and self._overlay_timing_preview[0] == self._drag_overlay_index:
                clip = self._overlay_timing_preview[1]
                self.overlayTimingChanged.emit(self._drag_overlay_index, clip, clip.start)
                self.selectionChanged.emit(clip.start, clip.end)
        elif was_subtitle_drag and not moved:
            self._subtitle_timing_preview = None
        elif self._drag_mode == "select" and not moved:
            self._selection = TimelineSelection(released_time, released_time)
            self.playheadChanged.emit(released_time)
            self.selectionChanged.emit(released_time, released_time)
        else:
            self.selectionChanged.emit(self._selection.start, self._selection.end)
            if not self._selection.is_range:
                self.playheadChanged.emit(self._selection.start)

        self._drag_mode = None
        self._drag_anchor = None
        self._drag_original_selection = None
        self._drag_subtitle_index = -1
        self._drag_overlay_index = -1
        self._drag_original_overlay = None
        self.update()

    def leaveEvent(self, event):
        self.setCursor(QCursor(Qt.ArrowCursor))
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if self._duration <= 0:
            super().wheelEvent(event)
            return

        angle_delta = event.angleDelta()
        raw_delta = angle_delta.x() if angle_delta.x() else angle_delta.y()
        if raw_delta == 0:
            super().wheelEvent(event)
            return

        if event.modifiers() & Qt.ControlModifier:
            self.zoomStepRequested.emit(5 if raw_delta > 0 else -5)
            event.accept()
            return

        if self._zoom <= 1.0:
            super().wheelEvent(event)
            return

        step_seconds = self.visible_duration() * 0.12
        direction = -1 if raw_delta > 0 else 1
        before = self._view_start
        self.set_view_start(self._view_start + direction * step_seconds)
        if abs(self._view_start - before) > 0.0001:
            self.viewStartChanged.emit(self._view_start)
        event.accept()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f4f7fb"))

        content = QRectF(self._content_rect())
        overlay_rect = self._overlay_track_rect()
        video_rect = self._video_track_rect()
        subtitle_rect = self._subtitle_track_rect()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#dbe7f2"))
        painter.drawRoundedRect(overlay_rect, 7, 7)
        painter.setBrush(QColor("#dce6f2"))
        painter.drawRoundedRect(video_rect, 8, 8)
        painter.setBrush(QColor("#e9eef5"))
        painter.drawRoundedRect(subtitle_rect, 8, 8)

        self._paint_ticks(painter, content)
        self._paint_overlay_blocks(painter, overlay_rect)
        self._paint_delete_ranges(painter, video_rect)
        self._paint_subtitle_blocks(painter, subtitle_rect)
        self._paint_selection(painter, content)
        self._paint_playhead(painter, content)

        painter.setPen(QColor("#526277"))
        painter.drawText(overlay_rect.adjusted(10, 0, -10, 0), Qt.AlignLeft | Qt.AlignVCenter, "叠加轨")
        painter.setPen(QColor("#526277"))
        painter.drawText(video_rect.adjusted(10, 0, -10, 0), Qt.AlignLeft | Qt.AlignVCenter, "视频轨")
        painter.drawText(subtitle_rect.adjusted(10, 0, -10, 0), Qt.AlignLeft | Qt.AlignVCenter, "字幕轨")

    def _paint_ticks(self, painter, rect):
        if self._duration <= 0:
            return
        painter.setPen(QPen(QColor("#b7c5d6"), 1))
        for index in range(6):
            ratio = index / 5
            x_pos = rect.left() + ratio * rect.width()
            painter.drawLine(x_pos, rect.top(), x_pos, rect.bottom())

    def _paint_overlay_blocks(self, painter, rect):
        painter.setPen(Qt.NoPen)
        for index in range(len(self._overlay_clips)):
            clip = self._overlay_clip(index)
            block = self._visible_block_rect(clip.start, clip.end, rect, min_width=10)
            if block is None:
                continue
            color = QColor("#14a38b") if clip.media_kind == "image" else QColor("#5d72e8")
            if index == self._selected_overlay_index:
                color = QColor("#f59f00")
            painter.setBrush(color)
            painter.drawRoundedRect(block, 6, 6)

    def _paint_delete_ranges(self, painter, rect):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(220, 86, 86, 170))
        for start, end in self._delete_ranges:
            block = self._visible_block_rect(start, end, rect, min_width=8)
            if block is not None:
                painter.drawRoundedRect(block, 7, 7)

    def _paint_subtitle_blocks(self, painter, rect):
        painter.setPen(Qt.NoPen)
        for index, cue in enumerate(self._subtitle_cues):
            start, end = self._subtitle_times(index, cue)
            block = self._visible_block_rect(start, end, rect, min_width=10)
            if block is None:
                continue
            color = QColor("#2d7ff9") if index != self._selected_subtitle_index else QColor("#ff8a2a")
            painter.setBrush(color)
            painter.drawRoundedRect(block, 7, 7)

    def _paint_selection(self, painter, rect):
        selection = self._selection
        if selection is None:
            return
        if selection.is_range:
            visible_start = max(selection.start, self._view_start)
            visible_end = min(selection.end, self._visible_end())
            if visible_end <= visible_start:
                return
            start_x = self._time_to_x(visible_start)
            end_x = self._time_to_x(visible_end)
            painter.setPen(QPen(QColor("#1a5fd0"), 2))
            painter.setBrush(QColor(45, 127, 249, 60))
            painter.drawRoundedRect(QRectF(start_x, rect.top(), end_x - start_x, rect.height()), 8, 8)
            painter.setBrush(QColor("#1a5fd0"))
            if selection.start >= self._view_start:
                painter.drawEllipse(QRectF(start_x - 4, rect.center().y() - 4, 8, 8))
            if selection.end <= self._visible_end():
                painter.drawEllipse(QRectF(end_x - 4, rect.center().y() - 4, 8, 8))

    def _paint_playhead(self, painter, rect):
        x_pos = self._time_to_x(self._playhead)
        if x_pos < rect.left() - 2 or x_pos > rect.right() + 2:
            return
        painter.setPen(QPen(QColor("#111827"), 2))
        painter.drawLine(x_pos, rect.top() - 4, x_pos, rect.bottom() + 4)
