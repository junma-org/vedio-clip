"""
达人模式时间轴控件。
负责展示删除区间、字幕块、播放头和可拖拽选区。
"""
from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from timeline_state import TimelineSelection, selection_from_points


class TimelineWidget(QWidget):
    playheadChanged = Signal(float)
    selectionChanged = Signal(float, float)
    subtitleActivated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(128)
        self.setMouseTracking(True)

        self._duration = 0.0
        self._playhead = 0.0
        self._selection = TimelineSelection(0, 0)
        self._delete_ranges = []
        self._subtitle_cues = []
        self._selected_subtitle_index = -1

        self._drag_mode = None
        self._drag_anchor = None
        self._press_pos = QPoint()
        self._handle_radius = 6

    def set_duration(self, seconds):
        self._duration = max(0.0, float(seconds or 0.0))
        self.update()

    def set_playhead(self, seconds):
        if self._duration > 0:
            self._playhead = max(0.0, min(float(seconds or 0.0), self._duration))
        else:
            self._playhead = max(0.0, float(seconds or 0.0))
        self.update()

    @property
    def playhead(self):
        return self._playhead

    def set_selection(self, selection):
        self._selection = selection.normalized(self._duration or None) if selection else TimelineSelection(0, 0)
        self.update()

    def set_delete_ranges(self, delete_ranges):
        self._delete_ranges = list(delete_ranges or [])
        self.update()

    def set_subtitle_cues(self, cues):
        self._subtitle_cues = list(cues or [])
        self.update()

    def set_selected_subtitle_index(self, index):
        self._selected_subtitle_index = int(index)
        self.update()

    def _content_rect(self):
        return self.rect().adjusted(14, 12, -14, -12)

    def _video_track_rect(self):
        rect = self._content_rect()
        return QRectF(rect.left(), rect.top(), rect.width(), 48)

    def _subtitle_track_rect(self):
        rect = self._content_rect()
        return QRectF(rect.left(), rect.top() + 66, rect.width(), 34)

    def _time_to_x(self, seconds):
        rect = self._content_rect()
        if self._duration <= 0 or rect.width() <= 0:
            return rect.left()
        ratio = max(0.0, min(float(seconds) / self._duration, 1.0))
        return rect.left() + ratio * rect.width()

    def _x_to_time(self, x_pos):
        rect = self._content_rect()
        if self._duration <= 0 or rect.width() <= 0:
            return 0.0
        ratio = (x_pos - rect.left()) / rect.width()
        return max(0.0, min(ratio, 1.0)) * self._duration

    def _selection_handles(self):
        if not self._selection.is_range:
            return None
        start_x = self._time_to_x(self._selection.start)
        end_x = self._time_to_x(self._selection.end)
        return start_x, end_x

    def _subtitle_index_at(self, pos):
        subtitle_rect = self._subtitle_track_rect()
        if not subtitle_rect.contains(pos):
            return -1

        for index, cue in enumerate(self._subtitle_cues):
            start_x = self._time_to_x(cue.start)
            end_x = self._time_to_x(cue.end)
            block = QRectF(start_x, subtitle_rect.top(), max(8, end_x - start_x), subtitle_rect.height())
            if block.contains(pos):
                return index
        return -1

    def _maybe_update_cursor(self, pos):
        handles = self._selection_handles()
        if handles:
            start_x, end_x = handles
            if abs(pos.x() - start_x) <= self._handle_radius or abs(pos.x() - end_x) <= self._handle_radius:
                self.setCursor(QCursor(Qt.SizeHorCursor))
                return
        if self._subtitle_index_at(pos) >= 0:
            self.setCursor(QCursor(Qt.PointingHandCursor))
            return
        self.setCursor(QCursor(Qt.ArrowCursor))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._duration <= 0:
            super().mousePressEvent(event)
            return

        pos = event.position().toPoint()
        self._press_pos = pos
        self._maybe_update_cursor(pos)

        subtitle_index = self._subtitle_index_at(pos)
        if subtitle_index >= 0:
            self.subtitleActivated.emit(subtitle_index)
            cue = self._subtitle_cues[subtitle_index]
            self._selection = TimelineSelection(cue.start, cue.end)
            self.selectionChanged.emit(cue.start, cue.end)
            self.playheadChanged.emit(cue.start)
            self.update()
            return

        handles = self._selection_handles()
        if handles:
            start_x, end_x = handles
            if abs(pos.x() - start_x) <= self._handle_radius:
                self._drag_mode = "resize_start"
                return
            if abs(pos.x() - end_x) <= self._handle_radius:
                self._drag_mode = "resize_end"
                return

        seconds = self._x_to_time(pos.x())
        self._drag_mode = "select"
        self._drag_anchor = seconds
        self._selection = TimelineSelection(seconds, seconds)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if not self._drag_mode:
            self._maybe_update_cursor(pos)
            super().mouseMoveEvent(event)
            return

        seconds = self._x_to_time(pos.x())
        if self._drag_mode == "select":
            self._selection = selection_from_points(self._drag_anchor, seconds, self._duration or None)
        elif self._drag_mode == "resize_start":
            self._selection = selection_from_points(seconds, self._selection.end, self._duration or None)
        elif self._drag_mode == "resize_end":
            self._selection = selection_from_points(self._selection.start, seconds, self._duration or None)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._drag_mode:
            super().mouseReleaseEvent(event)
            return

        released_time = self._x_to_time(event.position().x())
        moved = abs(event.position().toPoint().x() - self._press_pos.x()) >= 4

        if self._drag_mode == "select" and not moved:
            self._selection = TimelineSelection(released_time, released_time)
            self.playheadChanged.emit(released_time)
            self.selectionChanged.emit(released_time, released_time)
        else:
            self.selectionChanged.emit(self._selection.start, self._selection.end)
            if not self._selection.is_range:
                self.playheadChanged.emit(self._selection.start)

        self._drag_mode = None
        self._drag_anchor = None
        self.update()

    def leaveEvent(self, event):
        self.setCursor(QCursor(Qt.ArrowCursor))
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f4f7fb"))

        content = QRectF(self._content_rect())
        video_rect = self._video_track_rect()
        subtitle_rect = self._subtitle_track_rect()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#dce6f2"))
        painter.drawRoundedRect(video_rect, 8, 8)
        painter.setBrush(QColor("#e9eef5"))
        painter.drawRoundedRect(subtitle_rect, 8, 8)

        self._paint_ticks(painter, content)
        self._paint_delete_ranges(painter, video_rect)
        self._paint_subtitle_blocks(painter, subtitle_rect)
        self._paint_selection(painter, content)
        self._paint_playhead(painter, content)

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

    def _paint_delete_ranges(self, painter, rect):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(220, 86, 86, 170))
        for start, end in self._delete_ranges:
            start_x = self._time_to_x(start)
            end_x = self._time_to_x(end)
            painter.drawRoundedRect(QRectF(start_x, rect.top(), max(8, end_x - start_x), rect.height()), 7, 7)

    def _paint_subtitle_blocks(self, painter, rect):
        painter.setPen(Qt.NoPen)
        for index, cue in enumerate(self._subtitle_cues):
            start_x = self._time_to_x(cue.start)
            end_x = self._time_to_x(cue.end)
            width = max(10, end_x - start_x)
            color = QColor("#2d7ff9") if index != self._selected_subtitle_index else QColor("#ff8a2a")
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(start_x, rect.top(), width, rect.height()), 7, 7)

    def _paint_selection(self, painter, rect):
        selection = self._selection
        if selection is None:
            return
        start_x = self._time_to_x(selection.start)
        end_x = self._time_to_x(selection.end)
        if selection.is_range:
            painter.setPen(QPen(QColor("#1a5fd0"), 2))
            painter.setBrush(QColor(45, 127, 249, 60))
            painter.drawRoundedRect(QRectF(start_x, rect.top(), end_x - start_x, rect.height()), 8, 8)
            painter.setBrush(QColor("#1a5fd0"))
            painter.drawEllipse(QRectF(start_x - 4, rect.center().y() - 4, 8, 8))
            painter.drawEllipse(QRectF(end_x - 4, rect.center().y() - 4, 8, 8))

    def _paint_playhead(self, painter, rect):
        x_pos = self._time_to_x(self._playhead)
        painter.setPen(QPen(QColor("#111827"), 2))
        painter.drawLine(x_pos, rect.top() - 4, x_pos, rect.bottom() + 4)
