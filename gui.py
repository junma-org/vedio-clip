"""
GUI 模块。
极简模式保留表单式快剪；达人模式改成专用编辑台，围绕预览、时间轴和字幕列表工作。
"""
import sys
import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPointF, QThread, QTimer, Qt, QRectF, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
    MULTIMEDIA_AVAILABLE = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    QGraphicsVideoItem = None
    MULTIMEDIA_AVAILABLE = False
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edit_model import AudioTrack, DeleteRange, EditPlan, OutputOptions, PlanValidationError, normalize_delete_ranges
from ffmpeg_utils import (
    build_ffmpeg_command_from_plan,
    build_thumbnail_command,
    decode_process_output,
    find_ffmpeg,
    find_ffprobe,
    format_file_size,
    format_time,
    get_video_info,
    prepare_subtitle_file_for_plan,
    run_ffmpeg_with_progress,
)
from subtitle_model import (
    SubtitleCue,
    SubtitleProject,
    SubtitleStyleDef,
    SubtitleValidationError,
    build_default_subtitle_project,
    build_style_preset,
    extract_fade_from_tags,
    load_subtitle_file,
    load_subtitle_text,
    serialize_srt_entries,
    set_fade_on_tags,
)
from timeline_state import (
    TimelineSelection,
    TimelineStateError,
    add_delete_range_from_selection,
    add_subtitle_from_selection_or_playhead,
    delete_current_frame,
)
from timeline_widget import TimelineWidget
from whisper_utils import WhisperError, transcribe_video_to_project


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

STYLE_PRESET_LABELS = {
    "short_speech_bottom": "短视频口播",
    "center_emphasis": "中部强调",
    "top_note": "顶部提示",
}
DEFAULT_STYLE_PRESET = "short_speech_bottom"
DEVELOPER_WECHAT = "Summer_1987s"
FONT_PRESETS = (
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "HarmonyOS Sans SC",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "LXGW WenKai",
    "Smiley Sans",
    "SimHei",
)
FONT_FILE_EXTENSIONS = {".ttf", ".otf", ".ttc"}
RESOLUTION_OPTIONS = (
    ("保持原分辨率", None),
    ("1920 x 1080 横屏", (1920, 1080)),
    ("1280 x 720 横屏", (1280, 720)),
    ("1080 x 1920 竖屏", (1080, 1920)),
    ("720 x 1280 竖屏", (720, 1280)),
    ("1440 x 1920 竖屏", (1440, 1920)),
    ("720 x 480 标清", (720, 480)),
)


def ass_color_to_qcolor(color_text):
    text = str(color_text or "&H00FFFFFF").strip()
    digits = text[2:] if text.startswith("&H") else text
    digits = digits.rjust(8, "0")
    try:
        alpha = 255 - int(digits[0:2], 16)
        blue = int(digits[2:4], 16)
        green = int(digits[4:6], 16)
        red = int(digits[6:8], 16)
    except ValueError:
        return QColor("#ffffff")
    return QColor(red, green, blue, alpha)


def qcolor_to_ass_color(color):
    qcolor = QColor(color)
    return f"&H00{qcolor.blue():02X}{qcolor.green():02X}{qcolor.red():02X}"


def runtime_search_dirs():
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        candidates.append(Path(sys.executable).resolve().parent)
    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd())

    unique = []
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def load_bundled_subtitle_fonts():
    families = []
    seen = set()
    for base_dir in runtime_search_dirs():
        fonts_dir = base_dir / "fonts"
        if not fonts_dir.exists():
            continue
        for font_path in fonts_dir.rglob("*"):
            if font_path.suffix.lower() not in FONT_FILE_EXTENSIONS:
                continue
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id < 0:
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                if family not in seen:
                    families.append(family)
                    seen.add(family)
    return families


def subtitle_font_options():
    installed = set(QFontDatabase.families())
    bundled = load_bundled_subtitle_fonts()
    options = []
    for family in (*bundled, *FONT_PRESETS):
        if family in options:
            continue
        if family in bundled or family in installed:
            options.append(family)
    if not options:
        options.append("Microsoft YaHei")
    return options


class VideoProcessThread(QThread):
    progress_updated = Signal(int)
    status_changed = Signal(str)
    finished_success = Signal(str)
    finished_error = Signal(str)

    def __init__(self, ffmpeg_path, input_path, output_path, edit_plan, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.edit_plan = edit_plan
        self._stop_requested = False

    def run(self):
        subtitle_path = None
        try:
            self.status_changed.emit("正在分析视频...")
            video_info = get_video_info(self.ffmpeg_path, self.input_path)
            total_duration = video_info.get("duration", 0)

            if total_duration <= 0:
                self.finished_error.emit("无法获取视频时长，请确认 ffprobe.exe 可用。")
                return

            try:
                edit_plan = self.edit_plan.with_has_audio(video_info.get("has_audio", True)).validate(total_duration)
            except PlanValidationError as exc:
                self.finished_error.emit(str(exc))
                return

            remaining_duration = edit_plan.output_duration(total_duration)
            subtitle_path = prepare_subtitle_file_for_plan(edit_plan)
            cmd = build_ffmpeg_command_from_plan(
                self.ffmpeg_path,
                self.input_path,
                self.output_path,
                edit_plan,
                subtitle_path=subtitle_path,
                output_duration=remaining_duration,
            )

            action_parts = []
            if edit_plan.skip_seconds > 0:
                action_parts.append(f"跳过前 {format_time(edit_plan.skip_seconds)}")
            for delete_range in edit_plan.delete_ranges:
                action_parts.append(f"删除 {format_time(delete_range.start)}-{format_time(delete_range.end)}")
            if edit_plan.subtitles.has_entries():
                action_parts.append(f"烧录字幕 {len(edit_plan.subtitles.cues)} 条")
            if edit_plan.source_audio_muted and video_info.get("has_audio", True):
                action_parts.append("静音源音")
            if edit_plan.audio_tracks:
                action_parts.append(f"混音 {len(edit_plan.audio_tracks)} 条音频")
            if not action_parts:
                action_parts.append("转换视频")

            self.status_changed.emit(
                f"正在处理: {'，'.join(action_parts)}，输出时长约 {format_time(remaining_duration)}"
            )

            result = run_ffmpeg_with_progress(
                cmd,
                expected_duration=remaining_duration,
                stop_requested=lambda: self._stop_requested,
                progress_callback=self.progress_updated.emit,
            )

            if result["cancelled"]:
                self.finished_error.emit("处理已取消")
                return

            if result["returncode"] == 0:
                self.progress_updated.emit(100)
                self.finished_success.emit(self.output_path)
                return

            detail = result["stderr"]
            if detail:
                first_line = detail.splitlines()[0]
                self.finished_error.emit(f"FFmpeg错误 (返回码: {result['returncode']}): {first_line}")
            else:
                self.finished_error.emit(f"FFmpeg错误 (返回码: {result['returncode']})")
        except Exception as exc:
            self.finished_error.emit(f"处理异常: {exc}")
        finally:
            if subtitle_path:
                Path(subtitle_path).unlink(missing_ok=True)

    def stop(self):
        self._stop_requested = True


class ThumbnailThread(QThread):
    thumbnail_ready = Signal(str, str)
    thumbnail_failed = Signal(str, str)

    def __init__(self, ffmpeg_path, input_path, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.is_running = True
        self._process = None

    def run(self):
        fd, thumbnail_path = tempfile.mkstemp(prefix="videoclipper_preview_", suffix=".jpg")
        os.close(fd)

        try:
            Path(thumbnail_path).unlink(missing_ok=True)
            cmd = build_thumbnail_command(self.ffmpeg_path, self.input_path, thumbnail_path)
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
            _, stderr_data = self._process.communicate(timeout=30)

            if not self.is_running:
                Path(thumbnail_path).unlink(missing_ok=True)
                return

            if self._process.returncode == 0 and Path(thumbnail_path).exists() and Path(thumbnail_path).stat().st_size > 0:
                self.thumbnail_ready.emit(self.input_path, thumbnail_path)
                return

            error_text = decode_process_output(stderr_data).strip()
            Path(thumbnail_path).unlink(missing_ok=True)
            if error_text:
                self.thumbnail_failed.emit(self.input_path, f"无法生成视频预览: {error_text.splitlines()[0]}")
            else:
                self.thumbnail_failed.emit(self.input_path, "无法生成视频预览")
        except subprocess.TimeoutExpired:
            if self._process and self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            Path(thumbnail_path).unlink(missing_ok=True)
            if self.is_running:
                self.thumbnail_failed.emit(self.input_path, "视频预览生成超时")
        except Exception as exc:
            Path(thumbnail_path).unlink(missing_ok=True)
            if self.is_running:
                self.thumbnail_failed.emit(self.input_path, f"预览生成异常: {exc}")
        finally:
            self._process = None

    def stop(self):
        self.is_running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()


class SubtitleTranscribeThread(QThread):
    progress_updated = Signal(int)
    status_changed = Signal(str)
    finished_success = Signal(object)
    finished_error = Signal(str)

    def __init__(self, ffmpeg_path, input_path, edit_plan, video_size, duration, bilingual=True, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.edit_plan = edit_plan
        self.video_size = video_size
        self.duration = duration
        self.bilingual = bilingual
        self._stop_requested = False

    def run(self):
        try:
            project = transcribe_video_to_project(
                self.ffmpeg_path,
                self.input_path,
                self.edit_plan,
                video_size=self.video_size,
                duration=self.duration,
                bilingual=self.bilingual,
                status_callback=self.status_changed.emit,
                progress_callback=self.progress_updated.emit,
                stop_requested=lambda: self._stop_requested,
            )
        except (PlanValidationError, WhisperError) as exc:
            self.finished_error.emit(str(exc))
            return
        except Exception as exc:
            self.finished_error.emit(f"字幕识别异常: {exc}")
            return

        if self._stop_requested:
            self.finished_error.emit("字幕识别已取消。")
            return
        self.finished_success.emit(project)

    def stop(self):
        self._stop_requested = True


class DropArea(QFrame):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)
        self._source_pixmap = QPixmap()
        self._default_style = "QFrame { border: 2px dashed #b8c1cc; border-radius: 12px; background: #fafcff; }"
        self._active_style = "QFrame { border: 2px dashed #2d7ff9; border-radius: 12px; background: #eaf2ff; }"
        self.setStyleSheet(self._default_style)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(140)
        self.preview_label.setMaximumHeight(160)
        self.preview_label.setVisible(False)
        layout.addWidget(self.preview_label)

        self.label = QLabel("拖放视频文件到此处\n或点击这里选择文件")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 16px; color: #445; padding: 12px;")
        layout.addWidget(self.label)

    def set_loading(self):
        self._source_pixmap = QPixmap()
        self.preview_label.clear()
        self.preview_label.setVisible(False)
        self.label.setText("正在生成视频预览...")
        self.label.setStyleSheet("font-size: 14px; color: #556; padding: 12px;")

    def set_thumbnail(self, pixmap):
        self._source_pixmap = pixmap
        self.preview_label.setVisible(True)
        self.label.setText("点击或拖入其他视频可重新选择")
        self.label.setStyleSheet("font-size: 13px; color: #556; padding: 6px;")
        self._refresh_thumbnail()

    def clear_thumbnail(self, message="拖放视频文件到此处\n或点击这里选择文件"):
        self._source_pixmap = QPixmap()
        self.preview_label.clear()
        self.preview_label.setVisible(False)
        self.label.setText(message)
        self.label.setStyleSheet("font-size: 16px; color: #445; padding: 12px;")

    def _refresh_thumbnail(self):
        if self._source_pixmap.isNull() or not self.preview_label.isVisible():
            return

        target_size = self.preview_label.size()
        self.preview_label.setPixmap(
            self._source_pixmap.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_thumbnail()

    def dragEnterEvent(self, event: QDragEnterEvent):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.setStyleSheet(self._active_style)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._default_style)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self._default_style)
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择视频文件",
                "",
                "视频文件 (*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm *.m4v);;所有文件 (*.*)",
            )
            if file_path:
                self.file_dropped.emit(file_path)
        super().mousePressEvent(event)


class PreviewGraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#0f172a"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignCenter)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

    def sync_scene_view(self):
        if self.scene() is None or self.scene().sceneRect().isNull():
            return
        scene_rect = self.scene().sceneRect()
        self.setSceneRect(scene_rect)
        self.resetTransform()
        self.fitInView(scene_rect, Qt.KeepAspectRatio)
        self.centerOn(scene_rect.center())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_scene_view()


class SubtitleOverlayItem(QGraphicsObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rect = QRectF(0, 0, 1280, 720)
        self._project = build_default_subtitle_project()
        self._current_time = 0.0

    def boundingRect(self):
        return self._rect

    def set_canvas_size(self, width, height):
        width = max(1.0, float(width or 1.0))
        height = max(1.0, float(height or 1.0))
        if self._rect.width() == width and self._rect.height() == height:
            return
        self.prepareGeometryChange()
        self._rect = QRectF(0, 0, width, height)
        self.update()

    def set_project(self, project):
        self._project = project.normalized() if project is not None else build_default_subtitle_project()
        self.update()

    def set_current_time(self, seconds):
        self._current_time = max(0.0, float(seconds or 0.0))
        self.update()

    def paint(self, painter, _option, _widget=None):
        active_cues = self._project.active_cues_at(self._current_time) if self._project.has_entries() else ()
        if not active_cues:
            return

        painter.setRenderHint(QPainter.TextAntialiasing)
        style_map = self._project.style_map()
        scale = self._rect.height() / max(1, self._project.play_res_y)
        for cue in active_cues:
            style = style_map.get(cue.style_name, self._project.style)
            self._draw_cue(painter, cue, style, scale)

    def _cue_opacity(self, cue):
        fade = extract_fade_from_tags(cue.raw_tags)
        if not fade:
            return 1.0

        fade_in_ms, fade_out_ms = fade
        opacity = 1.0
        elapsed_ms = max(0.0, (self._current_time - cue.start) * 1000)
        remaining_ms = max(0.0, (cue.end - self._current_time) * 1000)
        if fade_in_ms > 0 and elapsed_ms < fade_in_ms:
            opacity = min(opacity, elapsed_ms / fade_in_ms)
        if fade_out_ms > 0 and remaining_ms < fade_out_ms:
            opacity = min(opacity, remaining_ms / fade_out_ms)
        return max(0.0, min(1.0, opacity))

    def _draw_cue(self, painter, cue, style, scale):
        painter.save()
        painter.setOpacity(painter.opacity() * self._cue_opacity(cue))

        font = QFont(style.font_name, max(12, int(round(style.font_size * scale))))
        font.setBold(bool(style.bold))
        font.setItalic(bool(style.italic))
        painter.setFont(font)

        primary = ass_color_to_qcolor(style.primary_color)
        outline = ass_color_to_qcolor(style.outline_color)
        margin_l = int(round(style.margin_l * scale))
        margin_r = int(round(style.margin_r * scale))
        margin_v = int(round(style.margin_v * scale))

        draw_rect = self._rect.adjusted(margin_l, 20, -margin_r, -20)
        row = (style.alignment - 1) // 3
        if row == 0:
            draw_rect = draw_rect.adjusted(0, 0, 0, -margin_v)
        elif row == 2:
            draw_rect = draw_rect.adjusted(0, margin_v, 0, 0)

        flags = Qt.TextWordWrap
        column = (style.alignment - 1) % 3
        if column == 0:
            flags |= Qt.AlignLeft
        elif column == 1:
            flags |= Qt.AlignHCenter
        else:
            flags |= Qt.AlignRight

        if row == 0:
            flags |= Qt.AlignBottom
        elif row == 1:
            flags |= Qt.AlignVCenter
        else:
            flags |= Qt.AlignTop

        outline_offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        painter.setPen(QPen(outline, max(1, style.outline * scale)))
        for dx, dy in outline_offsets:
            painter.drawText(draw_rect.translated(dx, dy), flags, cue.text)

        painter.setPen(QPen(primary, 1))
        painter.drawText(draw_rect, flags, cue.text)
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("极简视频剪辑工具")
        self.setMinimumSize(920, 720)

        self.current_mode = "simple"
        self.current_file = None
        self.ffmpeg_path = None
        self.ffprobe_path = None
        self.process_thread = None
        self.transcribe_thread = None
        self.thumbnail_threads = []

        self.delete_ranges = []
        self.expert_delete_ranges = []
        self.expert_selection = TimelineSelection(0, 0)
        self.expert_duration_seconds = 0.0
        self.expert_fps = 30.0
        self.expert_video_size = (1920, 1080)
        self.expert_native_video_size = (1920, 1080)
        self.expert_output_resolution = None
        self.current_video_has_audio = True
        self.source_audio_muted = False
        self.audio_tracks = []
        self.audio_track_volume_spins = []
        self.audio_track_remove_buttons = []
        self.available_subtitle_fonts = subtitle_font_options()
        self.subtitle_project = build_default_subtitle_project(self.expert_video_size)

        self.media_player = None
        self.audio_output = None
        self.video_scene = None
        self.video_item = None
        self.subtitle_overlay_item = None
        self._syncing_expert_position = False
        self._syncing_style_controls = False
        self._syncing_subtitle_editor = False
        self._syncing_resolution_controls = False
        self._syncing_audio_controls = False
        self._subtitle_timing_dirty = False
        self._subtitle_color = QColor("#ffffff")
        self._undo_stack = []
        self._restoring_state = False
        self._saved_window_geometry = None
        self._saved_was_maximized = False
        self._expert_media_path = None

        self.init_ui()
        self.init_shortcuts()
        self.check_ffmpeg()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.simple_mode_button = QPushButton("极简模式")
        self.simple_mode_button.setCheckable(True)
        self.simple_mode_button.clicked.connect(lambda: self.switch_mode("simple"))
        header_layout.addWidget(self.simple_mode_button)

        self.expert_mode_button = QPushButton("达人模式")
        self.expert_mode_button.setCheckable(True)
        self.expert_mode_button.clicked.connect(lambda: self.switch_mode("expert"))
        header_layout.addWidget(self.expert_mode_button)

        header_layout.addStretch()

        self.developer_button = QPushButton(f"联系开发者 wx: {DEVELOPER_WECHAT}")
        self.developer_button.setFlat(True)
        self.developer_button.clicked.connect(self.copy_developer_wechat)
        header_layout.addWidget(self.developer_button)

        layout.addLayout(header_layout)

        self.stack = QStackedWidget()
        self.simple_page = self.create_simple_page()
        self.expert_page = self.create_expert_page()
        self.stack.addWidget(self.simple_page)
        self.stack.addWidget(self.expert_page)
        layout.addWidget(self.stack, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("准备就绪")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #334; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.apply_mode_button_styles()
        self.switch_mode("simple", initial=True)

    def init_shortcuts(self):
        self.undo_action = QAction(self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.triggered.connect(self.undo_last_operation)
        self.addAction(self.undo_action)
        self.update_undo_action_state()

    def create_simple_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self.on_file_dropped)
        layout.addWidget(self.drop_area)

        self.file_label = QLabel("未选择文件")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("color: #556; font-size: 12px;")
        layout.addWidget(self.file_label)

        panel = QFrame()
        panel.setStyleSheet("QFrame { background: #f7f9fc; border: 1px solid #e1e7ef; border-radius: 10px; }")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        skip_layout = QHBoxLayout()
        skip_layout.addWidget(QLabel("剪掉前"))
        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(0, 3600)
        self.skip_spin.setValue(30)
        self.skip_spin.setSuffix(" 秒")
        skip_layout.addWidget(self.skip_spin)
        skip_layout.addStretch()
        panel_layout.addLayout(skip_layout)

        delete_layout = QHBoxLayout()
        self.delete_range_check = QCheckBox("删除区间")
        self.delete_range_check.toggled.connect(self.update_delete_range_controls)
        delete_layout.addWidget(self.delete_range_check)

        self.delete_start_spin = QSpinBox()
        self.delete_start_spin.setRange(0, 24 * 3600)
        self.delete_start_spin.setValue(80)
        self.delete_start_spin.setSuffix(" 秒")
        delete_layout.addWidget(self.delete_start_spin)

        delete_layout.addWidget(QLabel("到"))
        self.delete_end_spin = QSpinBox()
        self.delete_end_spin.setRange(0, 24 * 3600)
        self.delete_end_spin.setValue(100)
        self.delete_end_spin.setSuffix(" 秒")
        delete_layout.addWidget(self.delete_end_spin)

        self.add_delete_range_button = QPushButton("添加区间")
        self.add_delete_range_button.clicked.connect(self.add_delete_range)
        delete_layout.addWidget(self.add_delete_range_button)
        delete_layout.addStretch()
        panel_layout.addLayout(delete_layout)

        self.delete_ranges_list = QListWidget()
        self.delete_ranges_list.setMaximumHeight(90)
        self.delete_ranges_list.itemSelectionChanged.connect(self.update_delete_range_buttons)
        panel_layout.addWidget(self.delete_ranges_list)

        delete_buttons_layout = QHBoxLayout()
        delete_buttons_layout.addStretch()
        self.remove_delete_range_button = QPushButton("删除选中")
        self.remove_delete_range_button.clicked.connect(self.remove_selected_delete_range)
        delete_buttons_layout.addWidget(self.remove_delete_range_button)

        self.clear_delete_ranges_button = QPushButton("清空区间")
        self.clear_delete_ranges_button.clicked.connect(self.clear_delete_ranges)
        delete_buttons_layout.addWidget(self.clear_delete_ranges_button)
        panel_layout.addLayout(delete_buttons_layout)

        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("输出分辨率"))
        self.res_combo = QComboBox()
        for label, resolution in RESOLUTION_OPTIONS:
            self.res_combo.addItem(label, resolution)
        res_layout.addWidget(self.res_combo, 1)
        panel_layout.addLayout(res_layout)

        layout.addWidget(panel)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.start_button_simple = QPushButton("开始处理")
        self.start_button_simple.clicked.connect(self.start_simple_processing)
        button_layout.addWidget(self.start_button_simple)

        self.cancel_button_simple = QPushButton("取消")
        self.cancel_button_simple.clicked.connect(self.cancel_processing)
        button_layout.addWidget(self.cancel_button_simple)
        layout.addLayout(button_layout)

        self.refresh_delete_ranges_list()
        self.update_delete_range_controls(False)
        return page

    def create_expert_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self.expert_open_file_button = QPushButton("打开视频")
        self.expert_open_file_button.clicked.connect(self.open_video_file)
        toolbar.addWidget(self.expert_open_file_button)

        self.back_to_simple_button = QPushButton("返回极简模式")
        self.back_to_simple_button.clicked.connect(lambda: self.switch_mode("simple"))
        toolbar.addWidget(self.back_to_simple_button)

        self.expert_file_label = QLabel("未加载视频")
        self.expert_file_label.setStyleSheet("color: #475569;")
        toolbar.addWidget(self.expert_file_label, 1)

        self.start_button_expert = QPushButton("开始处理")
        self.start_button_expert.clicked.connect(self.start_expert_processing)
        toolbar.addWidget(self.start_button_expert)

        self.cancel_button_expert = QPushButton("取消")
        self.cancel_button_expert.clicked.connect(self.cancel_processing)
        toolbar.addWidget(self.cancel_button_expert)
        layout.addLayout(toolbar)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(12)
        layout.addLayout(content_layout, 1)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)
        content_layout.addLayout(left_panel, 3)

        self.expert_preview_stack = QStackedWidget()
        self.expert_preview_empty = QLabel("打开视频后开始编辑")
        self.expert_preview_empty.setAlignment(Qt.AlignCenter)
        self.expert_preview_empty.setMinimumHeight(360)
        self.expert_preview_empty.setStyleSheet(
            "background: #eef3f9; border: 1px solid #d7e1ed; border-radius: 12px; color: #5c6b7d; font-size: 16px;"
        )
        self.expert_preview_stack.addWidget(self.expert_preview_empty)

        self.video_scene = QGraphicsScene(self)
        self.expert_preview_view = PreviewGraphicsView(self.video_scene, self)
        self.expert_preview_view.setMinimumHeight(360)
        self.expert_preview_container = QWidget()
        preview_container_layout = QGridLayout(self.expert_preview_container)
        preview_container_layout.setContentsMargins(0, 0, 0, 0)
        preview_container_layout.setSpacing(0)
        preview_container_layout.addWidget(self.expert_preview_view, 0, 0)

        resolution_overlay = QFrame()
        resolution_overlay.setStyleSheet(
            "QFrame { background: rgba(15, 23, 42, 185); border-radius: 8px; }"
            "QLabel { color: white; }"
            "QComboBox { min-width: 150px; padding: 3px 6px; }"
        )
        resolution_overlay_layout = QHBoxLayout(resolution_overlay)
        resolution_overlay_layout.setContentsMargins(8, 6, 8, 6)
        resolution_overlay_layout.setSpacing(6)
        resolution_overlay_layout.addWidget(QLabel("分辨率"))
        self.expert_res_combo = QComboBox()
        for label, resolution in RESOLUTION_OPTIONS:
            self.expert_res_combo.addItem(label, resolution)
        self.expert_res_combo.currentIndexChanged.connect(self.on_expert_resolution_changed)
        resolution_overlay_layout.addWidget(self.expert_res_combo)
        preview_container_layout.addWidget(resolution_overlay, 0, 0, Qt.AlignRight | Qt.AlignBottom)
        self.expert_preview_stack.addWidget(self.expert_preview_container)
        left_panel.addWidget(self.expert_preview_stack)

        transport_layout = QHBoxLayout()
        self.expert_play_button = QPushButton("播放")
        self.expert_play_button.clicked.connect(self.toggle_expert_playback)
        transport_layout.addWidget(self.expert_play_button)

        self.expert_current_label = QLabel("0:00")
        transport_layout.addWidget(self.expert_current_label)

        self.expert_position_slider = QSlider(Qt.Horizontal)
        self.expert_position_slider.setRange(0, 0)
        self.expert_position_slider.valueChanged.connect(self.seek_expert_slider)
        transport_layout.addWidget(self.expert_position_slider, 1)

        self.expert_duration_label = QLabel("0:00")
        transport_layout.addWidget(self.expert_duration_label)
        left_panel.addLayout(transport_layout)

        self.timeline_widget = TimelineWidget()
        self.timeline_widget.playheadChanged.connect(self.seek_expert_seconds)
        self.timeline_widget.selectionChanged.connect(self.on_timeline_selection_changed)
        self.timeline_widget.subtitleActivated.connect(self.select_subtitle_row)
        self.timeline_widget.subtitleTimingPreviewed.connect(self.on_subtitle_timing_previewed)
        left_panel.addWidget(self.timeline_widget)

        delete_actions_layout = QHBoxLayout()
        self.delete_selection_button = QPushButton("删除选区")
        self.delete_selection_button.clicked.connect(self.add_expert_delete_range_from_selection)
        delete_actions_layout.addWidget(self.delete_selection_button)

        self.delete_frame_button = QPushButton("删除当前帧")
        self.delete_frame_button.clicked.connect(self.delete_expert_current_frame)
        delete_actions_layout.addWidget(self.delete_frame_button)

        self.remove_expert_range_button = QPushButton("删除选中片段")
        self.remove_expert_range_button.clicked.connect(self.remove_selected_expert_delete_range)
        delete_actions_layout.addWidget(self.remove_expert_range_button)

        self.clear_expert_ranges_button = QPushButton("清空片段")
        self.clear_expert_ranges_button.clicked.connect(self.clear_expert_delete_ranges)
        delete_actions_layout.addWidget(self.clear_expert_ranges_button)
        delete_actions_layout.addStretch()
        left_panel.addLayout(delete_actions_layout)

        self.expert_delete_ranges_list = QListWidget()
        self.expert_delete_ranges_list.setMaximumHeight(86)
        self.expert_delete_ranges_list.itemSelectionChanged.connect(self.update_expert_delete_range_buttons)
        left_panel.addWidget(self.expert_delete_ranges_list)

        right_panel = QFrame()
        right_panel.setObjectName("subtitleSidePanel")
        right_panel.setMinimumWidth(340)
        right_panel.setStyleSheet(
            "QFrame#subtitleSidePanel { background: #f7fafc; border: 1px solid #d8e1ea; border-radius: 0px; }"
            "QFrame#subtitleSidePanel QLabel { color: #475569; font-size: 12px; font-weight: 600; }"
            "QFrame#subtitleSidePanel QComboBox, QFrame#subtitleSidePanel QSpinBox, "
            "QFrame#subtitleSidePanel QDoubleSpinBox { background: #ffffff; border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 4px 6px; color: #0f172a; }"
            "QFrame#subtitleSidePanel QPlainTextEdit, QFrame#subtitleSidePanel QTableWidget, "
            "QFrame#subtitleSidePanel QListWidget { "
            "background: #ffffff; border: 1px solid #d5dde8; border-radius: 4px; color: #0f172a; }"
            "QFrame#subtitleSidePanel QPushButton { background: #ffffff; border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 5px 10px; color: #1e293b; }"
            "QFrame#subtitleSidePanel QPushButton:hover { background: #eef5ff; border-color: #9bb8dc; }"
            "QFrame#subtitleSidePanel QTabWidget::pane { border: 0; }"
            "QFrame#subtitleSidePanel QTabBar::tab { background: transparent; color: #64748b; padding: 7px 12px; }"
            "QFrame#subtitleSidePanel QTabBar::tab:selected { color: #0f172a; border-bottom: 2px solid #2563eb; }"
        )
        content_layout.addWidget(right_panel, 2)

        side_layout = QVBoxLayout(right_panel)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(10)

        self.expert_side_tabs = QTabWidget()
        side_layout.addWidget(self.expert_side_tabs, 1)

        subtitle_tab = QWidget()
        subtitle_layout = QVBoxLayout(subtitle_tab)
        subtitle_layout.setContentsMargins(0, 8, 0, 0)
        subtitle_layout.setSpacing(10)
        self.expert_side_tabs.addTab(subtitle_tab, "字幕")

        audio_tab = QWidget()
        audio_layout = QVBoxLayout(audio_tab)
        audio_layout.setContentsMargins(0, 8, 0, 0)
        audio_layout.setSpacing(10)
        self.expert_side_tabs.addTab(audio_tab, "音频")

        style_layout = QHBoxLayout()
        style_layout.addWidget(QLabel("字幕模板"))
        self.subtitle_style_combo = QComboBox()
        self.subtitle_style_combo.currentIndexChanged.connect(self.on_subtitle_style_changed)
        style_layout.addWidget(self.subtitle_style_combo, 1)
        subtitle_layout.addLayout(style_layout)

        style_edit_layout = QHBoxLayout()
        style_edit_layout.addWidget(QLabel("字体"))
        self.subtitle_font_combo = QComboBox()
        self.subtitle_font_combo.setEditable(True)
        for family in self.available_subtitle_fonts:
            self.subtitle_font_combo.addItem(family)
        if self.subtitle_font_combo.lineEdit() is not None:
            self.subtitle_font_combo.lineEdit().setPlaceholderText("选择或输入字体")
        self.subtitle_font_combo.currentTextChanged.connect(self.on_subtitle_style_controls_changed)
        style_edit_layout.addWidget(self.subtitle_font_combo, 1)
        style_edit_layout.addWidget(QLabel("字号"))
        self.subtitle_font_size_spin = QSpinBox()
        self.subtitle_font_size_spin.setRange(12, 160)
        self.subtitle_font_size_spin.setValue(44)
        self.subtitle_font_size_spin.valueChanged.connect(self.on_subtitle_style_controls_changed)
        style_edit_layout.addWidget(self.subtitle_font_size_spin)
        subtitle_layout.addLayout(style_edit_layout)

        color_effect_layout = QHBoxLayout()
        color_effect_layout.addWidget(QLabel("颜色"))
        self.subtitle_color_button = QPushButton("#FFFFFF")
        self.subtitle_color_button.clicked.connect(self.choose_subtitle_color)
        color_effect_layout.addWidget(self.subtitle_color_button)
        self.subtitle_fade_check = QCheckBox("渐隐渐显")
        self.subtitle_fade_check.toggled.connect(self.on_subtitle_effect_controls_changed)
        color_effect_layout.addWidget(self.subtitle_fade_check)
        self.subtitle_fade_ms_spin = QSpinBox()
        self.subtitle_fade_ms_spin.setRange(50, 3000)
        self.subtitle_fade_ms_spin.setSingleStep(50)
        self.subtitle_fade_ms_spin.setValue(200)
        self.subtitle_fade_ms_spin.setSuffix(" ms")
        self.subtitle_fade_ms_spin.valueChanged.connect(self.on_subtitle_effect_controls_changed)
        color_effect_layout.addWidget(self.subtitle_fade_ms_spin)
        subtitle_layout.addLayout(color_effect_layout)

        import_layout = QHBoxLayout()
        self.recognize_subtitle_button = QPushButton("识别")
        self.recognize_subtitle_button.clicked.connect(self.start_subtitle_transcription)
        import_layout.addWidget(self.recognize_subtitle_button)

        self.bilingual_subtitle_check = QCheckBox("双语")
        self.bilingual_subtitle_check.setChecked(True)
        import_layout.addWidget(self.bilingual_subtitle_check)

        self.import_subtitle_button = QPushButton("导入文件")
        self.import_subtitle_button.clicked.connect(self.import_subtitle_file)
        import_layout.addWidget(self.import_subtitle_button)

        self.import_clipboard_button = QPushButton("导入剪贴板")
        self.import_clipboard_button.clicked.connect(self.import_subtitle_clipboard)
        import_layout.addWidget(self.import_clipboard_button)
        subtitle_layout.addLayout(import_layout)

        self.subtitle_table = QTableWidget(0, 4)
        self.subtitle_table.setHorizontalHeaderLabels(["开始", "结束", "样式", "文本"])
        self.subtitle_table.verticalHeader().setVisible(False)
        self.subtitle_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.subtitle_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.subtitle_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.subtitle_table.itemSelectionChanged.connect(self.on_subtitle_selection_changed)
        self.subtitle_table.cellDoubleClicked.connect(self.seek_to_subtitle_row)
        self.subtitle_table.horizontalHeader().setStretchLastSection(True)
        self.install_subtitle_table_actions()
        subtitle_layout.addWidget(self.subtitle_table, 1)

        time_edit_layout = QHBoxLayout()
        time_edit_layout.addWidget(QLabel("开始"))
        self.subtitle_start_spin = QDoubleSpinBox()
        self.subtitle_start_spin.setRange(0, 24 * 3600)
        self.subtitle_start_spin.setDecimals(2)
        self.subtitle_start_spin.setSingleStep(0.1)
        self.subtitle_start_spin.setSuffix(" 秒")
        self.subtitle_start_spin.valueChanged.connect(self.on_subtitle_timing_controls_changed)
        time_edit_layout.addWidget(self.subtitle_start_spin)

        time_edit_layout.addWidget(QLabel("结束"))
        self.subtitle_end_spin = QDoubleSpinBox()
        self.subtitle_end_spin.setRange(0, 24 * 3600)
        self.subtitle_end_spin.setDecimals(2)
        self.subtitle_end_spin.setSingleStep(0.1)
        self.subtitle_end_spin.setSuffix(" 秒")
        self.subtitle_end_spin.valueChanged.connect(self.on_subtitle_timing_controls_changed)
        time_edit_layout.addWidget(self.subtitle_end_spin)
        subtitle_layout.addLayout(time_edit_layout)

        self.subtitle_text_edit = QPlainTextEdit()
        self.subtitle_text_edit.setPlaceholderText("输入字幕文本")
        self.subtitle_text_edit.setMaximumHeight(120)
        self.subtitle_text_edit.textChanged.connect(self.on_subtitle_text_changed)
        subtitle_layout.addWidget(self.subtitle_text_edit)

        subtitle_button_layout = QHBoxLayout()
        self.add_subtitle_button = QPushButton("加字幕")
        self.add_subtitle_button.clicked.connect(self.add_expert_subtitle)
        subtitle_button_layout.addWidget(self.add_subtitle_button)
        subtitle_button_layout.addStretch()
        subtitle_layout.addLayout(subtitle_button_layout)

        source_audio_layout = QHBoxLayout()
        self.source_audio_check = QCheckBox("源音")
        self.source_audio_check.setChecked(True)
        self.source_audio_check.toggled.connect(self.on_source_audio_toggled)
        source_audio_layout.addWidget(self.source_audio_check)
        source_audio_layout.addStretch()
        self.add_audio_track_button = QPushButton("添加音频")
        self.add_audio_track_button.clicked.connect(self.add_audio_track)
        source_audio_layout.addWidget(self.add_audio_track_button)
        audio_layout.addLayout(source_audio_layout)

        self.audio_tracks_list = QListWidget()
        self.audio_tracks_list.setMaximumHeight(120)
        audio_layout.addWidget(self.audio_tracks_list)

        self.audio_track_controls_layout = QVBoxLayout()
        self.audio_track_controls_layout.setSpacing(8)
        audio_layout.addLayout(self.audio_track_controls_layout)
        audio_layout.addStretch()

        self.refresh_style_combo()
        self.refresh_expert_delete_ranges_list()
        self.refresh_subtitle_table()
        self.refresh_audio_controls()
        return page

    def apply_mode_button_styles(self):
        active_style = "QPushButton { background: #1d4ed8; color: white; border: none; padding: 8px 16px; border-radius: 8px; }"
        inactive_style = "QPushButton { background: #e8eef6; color: #334155; border: none; padding: 8px 16px; border-radius: 8px; }"
        self.simple_mode_button.setStyleSheet(active_style if self.current_mode == "simple" else inactive_style)
        self.expert_mode_button.setStyleSheet(active_style if self.current_mode == "expert" else inactive_style)
        self.simple_mode_button.setChecked(self.current_mode == "simple")
        self.expert_mode_button.setChecked(self.current_mode == "expert")

    def switch_mode(self, mode, initial=False):
        if mode == self.current_mode and not initial:
            return

        previous_mode = self.current_mode
        self.current_mode = mode
        self.apply_mode_button_styles()
        self.stack.setCurrentWidget(self.simple_page if mode == "simple" else self.expert_page)

        if mode == "expert":
            self.enter_expert_mode()
        elif previous_mode == "expert":
            self.leave_expert_mode()

        self.update_processing_buttons()
        self.update_expert_controls_state()

    def enter_expert_mode(self):
        if not self.isMaximized():
            self._saved_window_geometry = self.geometry()
            self._saved_was_maximized = False
            self.showMaximized()
        else:
            self._saved_was_maximized = True

        if self.current_file is not None:
            QTimer.singleShot(0, self.ensure_expert_media_ready)

    def leave_expert_mode(self):
        if self._saved_was_maximized:
            return
        if self.isMaximized():
            self.showNormal()
            if self._saved_window_geometry is not None:
                self.setGeometry(self._saved_window_geometry)

    def copy_developer_wechat(self):
        QApplication.clipboard().setText(DEVELOPER_WECHAT)
        self.status_label.setText(f"已复制微信号: {DEVELOPER_WECHAT}")

    def install_subtitle_table_actions(self):
        self.select_all_subtitles_action = QAction(self.subtitle_table)
        self.select_all_subtitles_action.setShortcut(QKeySequence.SelectAll)
        self.select_all_subtitles_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.select_all_subtitles_action.triggered.connect(self.subtitle_table.selectAll)
        self.subtitle_table.addAction(self.select_all_subtitles_action)

        self.copy_subtitles_action = QAction(self.subtitle_table)
        self.copy_subtitles_action.setShortcut(QKeySequence.Copy)
        self.copy_subtitles_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.copy_subtitles_action.triggered.connect(self.copy_selected_subtitles_as_srt)
        self.subtitle_table.addAction(self.copy_subtitles_action)

        self.delete_subtitles_action = QAction(self.subtitle_table)
        self.delete_subtitles_action.setShortcut(QKeySequence("Del"))
        self.delete_subtitles_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.delete_subtitles_action.triggered.connect(self.remove_selected_subtitles)
        self.subtitle_table.addAction(self.delete_subtitles_action)

    def confirm_clear(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _combo_index_for_data(self, combo, data):
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                return index
        return 0

    def snapshot_editor_state(self):
        subtitle_row = self.current_subtitle_row() if hasattr(self, "subtitle_table") else -1
        return {
            "delete_ranges": list(self.delete_ranges),
            "expert_delete_ranges": list(self.expert_delete_ranges),
            "expert_selection": self.expert_selection,
            "expert_output_resolution": self.expert_output_resolution,
            "source_audio_muted": self.source_audio_muted,
            "audio_tracks": list(self.audio_tracks),
            "subtitle_project": self.subtitle_project,
            "subtitle_row": subtitle_row,
        }

    def push_undo_state(self):
        if self._restoring_state:
            return
        self._undo_stack.append(self.snapshot_editor_state())
        if len(self._undo_stack) > 50:
            self._undo_stack = self._undo_stack[-50:]
        self.update_undo_action_state()

    def clear_undo_stack(self):
        self._undo_stack = []
        self.update_undo_action_state()

    def update_undo_action_state(self):
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(bool(self._undo_stack) and self.process_thread is None and self.transcribe_thread is None)

    def undo_last_operation(self):
        if self.process_thread is not None or self.transcribe_thread is not None or not self._undo_stack:
            return

        state = self._undo_stack.pop()
        self._restoring_state = True
        try:
            self.delete_ranges = list(state["delete_ranges"])
            self.expert_delete_ranges = list(state["expert_delete_ranges"])
            self.expert_selection = state["expert_selection"]
            self.expert_output_resolution = state["expert_output_resolution"]
            self.source_audio_muted = bool(state.get("source_audio_muted", False))
            self.audio_tracks = list(state.get("audio_tracks", []))
            self.subtitle_project = state["subtitle_project"].normalized()
            self._subtitle_timing_dirty = False

            self.refresh_delete_ranges_list()
            self.refresh_expert_delete_ranges_list()
            self.refresh_audio_controls()
            self.refresh_style_combo()
            self.refresh_subtitle_table()
            self.timeline_widget.set_selection(self.expert_selection)
            self.timeline_widget.set_subtitle_cues(self.subtitle_project.cues)

            self._syncing_resolution_controls = True
            self.expert_res_combo.setCurrentIndex(self._combo_index_for_data(self.expert_res_combo, self.expert_output_resolution))
            self._syncing_resolution_controls = False
            self.apply_expert_preview_resolution()

            subtitle_row = state.get("subtitle_row", -1)
            if 0 <= subtitle_row < len(self.subtitle_project.cues):
                self.subtitle_table.selectRow(subtitle_row)
                self.on_subtitle_selection_changed()
            else:
                self.subtitle_table.clearSelection()
                self.on_subtitle_selection_changed()
            self.status_label.setText("已撤销上一次操作")
        finally:
            self._restoring_state = False
            self.update_undo_action_state()
            self.update_expert_controls_state()

    def open_video_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm *.m4v);;所有文件 (*.*)",
        )
        if file_path:
            self.on_file_dropped(file_path)

    def on_file_dropped(self, file_path):
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "文件无效", "选择的文件不存在，或不是普通文件。")
            return
        if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            QMessageBox.warning(self, "格式不支持", "请选择常见视频文件，例如 MP4、AVI、MKV、MOV。")
            return

        self.current_file = path
        self._expert_media_path = None
        self.reset_expert_session()
        self.update_file_labels()
        self.status_label.setText("文件已加载，可以开始处理")
        self.start_thumbnail_generation(path)

        if self.current_mode == "expert":
            self.ensure_expert_media_ready()

        self.update_processing_buttons()
        self.update_expert_controls_state()

    def update_file_labels(self):
        if self.current_file is None:
            self.file_label.setText("未选择文件")
            self.expert_file_label.setText("未加载视频")
            return

        file_size = format_file_size(self.current_file.stat().st_size)
        self.file_label.setText(f"已选择文件:\n{self.current_file}\n大小: {file_size}")
        self.expert_file_label.setText(self.current_file.name)

    def reset_expert_session(self):
        self.expert_delete_ranges = []
        self.expert_selection = TimelineSelection(0, 0)
        self.expert_duration_seconds = 0.0
        self.expert_fps = 30.0
        self.expert_video_size = (1920, 1080)
        self.expert_native_video_size = (1920, 1080)
        self.expert_output_resolution = None
        self.current_video_has_audio = True
        self.source_audio_muted = False
        self.audio_tracks = []
        self._subtitle_timing_dirty = False
        self.clear_undo_stack()
        self._syncing_resolution_controls = True
        self.expert_res_combo.setCurrentIndex(0)
        self._syncing_resolution_controls = False
        self.subtitle_project = build_default_subtitle_project(self.expert_video_size)
        self.timeline_widget.set_duration(0)
        self.timeline_widget.set_playhead(0)
        self.timeline_widget.set_selection(self.expert_selection)
        self.timeline_widget.set_delete_ranges([])
        self.timeline_widget.set_subtitle_cues([])
        self.subtitle_text_edit.clear()
        self.subtitle_start_spin.setValue(0)
        self.subtitle_end_spin.setValue(0)
        self.refresh_style_combo()
        self.load_style_controls(self.current_style_name())
        self.refresh_expert_delete_ranges_list()
        self.refresh_subtitle_table()
        self.refresh_audio_controls()
        self.expert_preview_stack.setCurrentWidget(self.expert_preview_empty)
        self.expert_current_label.setText("0:00")
        self.expert_duration_label.setText("0:00")
        self.expert_position_slider.blockSignals(True)
        self.expert_position_slider.setRange(0, 0)
        self.expert_position_slider.setValue(0)
        self.expert_position_slider.blockSignals(False)
        if self.subtitle_overlay_item is not None:
            self.subtitle_overlay_item.set_project(self.subtitle_project)
            self.subtitle_overlay_item.set_current_time(0)
        self.apply_expert_preview_resolution()

    def ensure_expert_media_ready(self):
        if self.current_file is None or self.ffmpeg_path is None:
            return

        if self._expert_media_path == str(self.current_file):
            return

        video_info = get_video_info(self.ffmpeg_path, self.current_file)
        width = max(1, int(video_info.get("width", 1920) or 1920))
        height = max(1, int(video_info.get("height", 1080) or 1080))
        self.expert_video_size = (width, height)
        self.expert_native_video_size = (width, height)
        self.expert_fps = float(video_info.get("fps", 30) or 30)
        self.expert_duration_seconds = max(0.0, float(video_info.get("duration", 0) or 0))
        self.current_video_has_audio = bool(video_info.get("has_audio", True))

        preset_name = self.current_style_name()
        if preset_name not in STYLE_PRESET_LABELS:
            preset_name = DEFAULT_STYLE_PRESET
        self.subtitle_project = build_default_subtitle_project(self.expert_video_size, preset_name)
        self.refresh_style_combo()
        self.load_style_controls(self.current_style_name())
        self.refresh_subtitle_table()
        self.timeline_widget.set_duration(self.expert_duration_seconds)
        self.timeline_widget.set_playhead(0)
        self.timeline_widget.set_selection(self.expert_selection)
        self.timeline_widget.set_delete_ranges(self.expert_delete_ranges)
        self.timeline_widget.set_subtitle_cues(self.subtitle_project.cues)
        self.refresh_audio_controls()

        if MULTIMEDIA_AVAILABLE:
            self.ensure_expert_player()
            self.media_player.stop()
            self.media_player.setSource(QUrl.fromLocalFile(str(self.current_file)))
            self.apply_expert_preview_resolution()
            self.expert_preview_stack.setCurrentWidget(self.expert_preview_container)
        else:
            self.expert_preview_empty.setText("当前环境不支持视频预览")
            self.expert_preview_stack.setCurrentWidget(self.expert_preview_empty)

        self._expert_media_path = str(self.current_file)
        self.update_expert_controls_state()

    def ensure_expert_player(self):
        if not MULTIMEDIA_AVAILABLE or self.media_player is not None:
            return

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.5)
        self.media_player.setAudioOutput(self.audio_output)

        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.video_item.setOffset(QPointF(0, 0))
        self.video_item.setZValue(0)
        self.video_scene.addItem(self.video_item)
        self.subtitle_overlay_item = SubtitleOverlayItem()
        self.subtitle_overlay_item.setZValue(1)
        self.video_scene.addItem(self.subtitle_overlay_item)
        self.video_scene.setBackgroundBrush(QColor("#000000"))
        self.video_scene.setSceneRect(QRectF(0, 0, 1280, 720))
        self.update_video_item_geometry(1280, 720)
        self.subtitle_overlay_item.set_canvas_size(1280, 720)
        self.media_player.setVideoOutput(self.video_item)

        self.media_player.durationChanged.connect(self.on_expert_duration_changed)
        self.media_player.positionChanged.connect(self.on_expert_position_changed)
        self.media_player.playbackStateChanged.connect(self.on_expert_playback_state_changed)
        self.media_player.errorOccurred.connect(self.on_expert_media_error)
        if hasattr(self.video_item, "nativeSizeChanged"):
            self.video_item.nativeSizeChanged.connect(self.on_expert_native_size_changed)

    def on_expert_native_size_changed(self, size):
        width = max(1.0, float(size.width() or 1.0))
        height = max(1.0, float(size.height() or 1.0))
        self.expert_native_video_size = (int(width), int(height))
        if self.expert_output_resolution is None:
            self.expert_video_size = (int(width), int(height))
        self.apply_expert_preview_resolution()

    def current_preview_size(self):
        return self.expert_output_resolution or self.expert_native_video_size or self.expert_video_size

    def video_item_rect_for_canvas(self, width, height):
        source_width, source_height = self.expert_native_video_size or (width, height)
        source_width = max(1.0, float(source_width or width or 1.0))
        source_height = max(1.0, float(source_height or height or 1.0))
        target_width = max(1.0, float(width or 1.0))
        target_height = max(1.0, float(height or 1.0))
        scale = min(target_width / source_width, target_height / source_height)
        fitted_width = source_width * scale
        fitted_height = source_height * scale
        return QRectF(
            (target_width - fitted_width) / 2,
            (target_height - fitted_height) / 2,
            fitted_width,
            fitted_height,
        )

    def update_video_item_geometry(self, width, height):
        if self.video_item is None:
            return
        video_rect = self.video_item_rect_for_canvas(width, height)
        self.video_item.resetTransform()
        self.video_item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.video_item.setOffset(QPointF(0, 0))
        self.video_item.setSize(video_rect.size())
        self.video_item.setPos(video_rect.left(), video_rect.top())
        self.video_item.update()

    def sync_expert_preview_view(self):
        if hasattr(self, "expert_preview_view"):
            self.expert_preview_view.sync_scene_view()

    def apply_expert_preview_resolution(self):
        if self.video_scene is None:
            return

        width, height = self.current_preview_size()
        width = max(1, int(width or 1))
        height = max(1, int(height or 1))
        self.expert_video_size = (width, height)
        self.video_scene.setSceneRect(QRectF(0, 0, width, height))
        self.update_video_item_geometry(width, height)
        if self.subtitle_overlay_item is not None:
            self.subtitle_overlay_item.set_canvas_size(width, height)
            self.subtitle_overlay_item.set_project(self.subtitle_project)
            self.subtitle_overlay_item.set_current_time(self.current_expert_seconds())
        self.video_scene.update(self.video_scene.sceneRect())
        self.sync_expert_preview_view()
        QTimer.singleShot(0, self.sync_expert_preview_view)

    def on_expert_resolution_changed(self):
        if self._syncing_resolution_controls:
            return

        self.push_undo_state()
        self.expert_output_resolution = self.expert_res_combo.currentData()
        self.apply_expert_preview_resolution()
        label = self.expert_res_combo.currentText()
        self.status_label.setText(f"达人模式输出分辨率: {label}")
        self.update_expert_controls_state()

    def on_expert_duration_changed(self, milliseconds):
        self.expert_duration_seconds = max(self.expert_duration_seconds, milliseconds / 1000)
        self.expert_duration_label.setText(format_time(self.expert_duration_seconds))
        self.timeline_widget.set_duration(self.expert_duration_seconds)

        self._syncing_expert_position = True
        self.expert_position_slider.setRange(0, max(0, int(milliseconds)))
        self._syncing_expert_position = False
        self.update_expert_controls_state()

    def on_expert_position_changed(self, milliseconds):
        seconds = max(0.0, milliseconds / 1000)
        self.expert_current_label.setText(format_time(seconds))
        self.timeline_widget.set_playhead(seconds)
        if self.subtitle_overlay_item is not None:
            self.subtitle_overlay_item.set_current_time(seconds)

        self._syncing_expert_position = True
        self.expert_position_slider.setValue(max(0, int(milliseconds)))
        self._syncing_expert_position = False

    def on_expert_playback_state_changed(self, state):
        if QMediaPlayer is not None and state == QMediaPlayer.PlaybackState.PlayingState:
            self.expert_play_button.setText("暂停")
        else:
            self.expert_play_button.setText("播放")

    def on_expert_media_error(self, *_args):
        if self.media_player is None:
            return
        error_text = self.media_player.errorString()
        if error_text:
            self.status_label.setText(f"达人模式预览失败: {error_text}")

    def toggle_expert_playback(self):
        if self.media_player is None or self.current_file is None:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def seek_expert_slider(self, milliseconds):
        if self._syncing_expert_position or self.media_player is None:
            return
        self.media_player.setPosition(max(0, int(milliseconds)))

    def seek_expert_seconds(self, seconds):
        target_ms = max(0, int(float(seconds) * 1000))
        if self.media_player is not None:
            self.media_player.setPosition(target_ms)
            self.expert_current_label.setText(format_time(float(seconds)))
            if self.subtitle_overlay_item is not None:
                self.subtitle_overlay_item.set_current_time(float(seconds))
        else:
            self.timeline_widget.set_playhead(seconds)
            if self.subtitle_overlay_item is not None:
                self.subtitle_overlay_item.set_current_time(seconds)

    def current_expert_seconds(self):
        if self.media_player is not None:
            return max(0.0, self.media_player.position() / 1000)
        return self.timeline_widget.playhead

    def on_timeline_selection_changed(self, start, end):
        self.expert_selection = TimelineSelection(start, end)
        self.timeline_widget.set_selection(self.expert_selection)
        if self._subtitle_timing_dirty and self.current_subtitle_row() >= 0 and self.expert_selection.is_range:
            self.apply_current_subtitle_from_editor(status="已更新字幕时间", reload_editor=False)
            self._subtitle_timing_dirty = False
        self.update_expert_controls_state()

    def on_subtitle_timing_previewed(self, row, start, end, _playhead):
        if row < 0 or row >= len(self.subtitle_project.cues):
            return
        if self.current_subtitle_row() != row:
            self.subtitle_table.selectRow(row)
        self._subtitle_timing_dirty = True
        self.expert_selection = TimelineSelection(start, end)
        self._syncing_subtitle_editor = True
        try:
            self.subtitle_start_spin.setValue(start)
            self.subtitle_end_spin.setValue(end)
        finally:
            self._syncing_subtitle_editor = False
        self.timeline_widget.set_selection(self.expert_selection)
        self.status_label.setText("已预览字幕时间调整，松开后自动应用")
        self.update_expert_controls_state()

    def refresh_style_combo(self):
        current = self.current_style_name()
        extra_styles = [style.name for style in self.subtitle_project.styles if style.name not in STYLE_PRESET_LABELS]
        options = list(STYLE_PRESET_LABELS.keys()) + [name for name in extra_styles if name not in STYLE_PRESET_LABELS]

        self.subtitle_style_combo.blockSignals(True)
        self.subtitle_style_combo.clear()
        for style_name in options:
            self.subtitle_style_combo.addItem(STYLE_PRESET_LABELS.get(style_name, style_name), style_name)
        target = current if current in options else self.subtitle_project.default_style_name
        index = max(0, self.subtitle_style_combo.findData(target))
        self.subtitle_style_combo.setCurrentIndex(index)
        self.subtitle_style_combo.blockSignals(False)
        self.load_style_controls(self.current_style_name())

    def current_style_name(self):
        style_name = self.subtitle_style_combo.currentData() if hasattr(self, "subtitle_style_combo") else None
        return style_name or DEFAULT_STYLE_PRESET

    def style_for_name(self, style_name):
        style_map = self.subtitle_project.style_map()
        if style_name in style_map:
            return style_map[style_name]
        try:
            return build_style_preset(style_name, self.expert_video_size)
        except SubtitleValidationError:
            return self.subtitle_project.style

    def set_subtitle_color_button(self, color):
        self._subtitle_color = QColor(color)
        text = self._subtitle_color.name().upper()
        foreground = "#111827" if self._subtitle_color.lightness() > 150 else "#ffffff"
        self.subtitle_color_button.setText(text)
        self.subtitle_color_button.setStyleSheet(
            f"QPushButton {{ background: {text}; color: {foreground}; border: 1px solid #94a3b8; padding: 4px 8px; }}"
        )

    def load_style_controls(self, style_name=None):
        if not hasattr(self, "subtitle_font_combo"):
            return
        style = self.style_for_name(style_name or self.current_style_name())
        self._syncing_style_controls = True
        font_index = self.subtitle_font_combo.findText(style.font_name)
        if font_index >= 0:
            self.subtitle_font_combo.setCurrentIndex(font_index)
        else:
            self.subtitle_font_combo.setEditText(style.font_name)
        self.subtitle_font_size_spin.setValue(style.font_size)
        self.set_subtitle_color_button(ass_color_to_qcolor(style.primary_color))
        self._syncing_style_controls = False

    def load_effect_controls(self, cue=None):
        if not hasattr(self, "subtitle_fade_check"):
            return
        fade = extract_fade_from_tags(cue.raw_tags if cue else "")
        self._syncing_style_controls = True
        self.subtitle_fade_check.setChecked(fade is not None)
        if fade is not None:
            self.subtitle_fade_ms_spin.setValue(max(50, min(3000, max(fade))))
        self._syncing_style_controls = False

    def on_subtitle_style_changed(self):
        if self._syncing_style_controls or self._syncing_subtitle_editor:
            return
        self.load_style_controls(self.current_style_name())
        self.apply_subtitle_style_controls_to_selection("已应用字幕模板")

    def on_subtitle_style_controls_changed(self, *_args):
        if self._syncing_style_controls or self._syncing_subtitle_editor:
            return
        self.apply_subtitle_style_controls_to_selection("已更新字幕样式")

    def on_subtitle_effect_controls_changed(self, *_args):
        if self._syncing_style_controls or self._syncing_subtitle_editor:
            return
        self.update_expert_controls_state()
        self.apply_subtitle_effect_controls_to_selection()

    def on_subtitle_text_changed(self):
        if self._syncing_subtitle_editor:
            return
        self.update_expert_controls_state()
        self.apply_current_subtitle_from_editor(status="已更新字幕文本", reload_editor=False)

    def on_subtitle_timing_controls_changed(self, *_args):
        if self._syncing_subtitle_editor:
            return
        self.apply_current_subtitle_from_editor(status="已更新字幕时间", reload_editor=False)

    def choose_subtitle_color(self):
        color = QColorDialog.getColor(self._subtitle_color, self, "选择字幕颜色")
        if color.isValid():
            self.set_subtitle_color_button(color)
            self.apply_subtitle_style_controls_to_selection("已更新字幕颜色")

    def style_from_controls(self, style_name):
        base = self.style_for_name(style_name)
        return SubtitleStyleDef(
            name=style_name,
            font_name=self.subtitle_font_combo.currentText().strip() or base.font_name,
            font_size=self.subtitle_font_size_spin.value(),
            primary_color=qcolor_to_ass_color(self._subtitle_color),
            secondary_color=base.secondary_color,
            outline_color=base.outline_color,
            back_color=base.back_color,
            bold=base.bold,
            italic=base.italic,
            underline=base.underline,
            strike_out=base.strike_out,
            scale_x=base.scale_x,
            scale_y=base.scale_y,
            spacing=base.spacing,
            angle=base.angle,
            border_style=base.border_style,
            outline=base.outline,
            shadow=base.shadow,
            alignment=base.alignment,
            margin_l=base.margin_l,
            margin_r=base.margin_r,
            margin_v=base.margin_v,
            encoding=base.encoding,
        ).normalized()

    def unique_subtitle_style_name(self, project, base_name):
        base = project.normalized()
        existing_names = {style.name for style in base.styles}
        stem = str(base_name or DEFAULT_STYLE_PRESET).strip() or DEFAULT_STYLE_PRESET
        counter = 1
        while True:
            candidate = f"{stem}_custom_{counter}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    def with_style_in_project(self, project, style):
        base = project.normalized()
        target = style.normalized()
        styles = []
        replaced = False
        for existing in base.styles:
            if existing.name == target.name:
                styles.append(target)
                replaced = True
            else:
                styles.append(existing)
        if not replaced:
            styles.append(target)
        return SubtitleProject(
            cues=base.cues,
            styles=tuple(styles),
            script_info=base.script_info,
            enabled=base.enabled,
            play_res_x=base.play_res_x,
            play_res_y=base.play_res_y,
            default_style_name=base.default_style_name,
        ).normalized()

    def scoped_style_for_rows(self, project, style, rows):
        base = project.normalized()
        target = style.normalized()
        row_set = set(rows or ())
        if not row_set:
            return target

        style_map = base.style_map()
        existing = style_map.get(target.name)
        used_outside_selection = any(
            cue.style_name == target.name for index, cue in enumerate(base.cues) if index not in row_set
        )
        if existing is not None and existing != target and used_outside_selection:
            return replace(target, name=self.unique_subtitle_style_name(base, target.name)).normalized()
        return target

    def apply_style_controls_to_project(self, project, style_name):
        return self.with_style_in_project(project, self.style_from_controls(style_name))

    def raw_tags_from_effect_controls(self, original_raw_tags=""):
        if self.subtitle_fade_check.isChecked():
            fade_ms = self.subtitle_fade_ms_spin.value()
            return set_fade_on_tags(original_raw_tags, fade_ms, fade_ms)
        return set_fade_on_tags(original_raw_tags, None, None)

    def ensure_style_exists(self, style_name):
        base = self.subtitle_project.normalized()
        style_map = base.style_map()
        if style_name in style_map:
            return base

        try:
            style = build_style_preset(style_name, self.expert_video_size)
        except SubtitleValidationError:
            return base

        return SubtitleProject(
            cues=base.cues,
            styles=tuple(list(base.styles) + [style]),
            script_info=base.script_info,
            enabled=base.enabled,
            play_res_x=base.play_res_x,
            play_res_y=base.play_res_y,
            default_style_name=base.default_style_name,
        ).normalized()

    def subtitle_project_with_cues(self, project, cues):
        base = project.normalized()
        return SubtitleProject(
            cues=tuple(cues),
            styles=base.styles,
            script_info=base.script_info,
            enabled=base.enabled,
            play_res_x=base.play_res_x,
            play_res_y=base.play_res_y,
            default_style_name=base.default_style_name,
        ).normalized()

    def set_subtitle_project_after_edit(self, project, selected_rows=None, status=None, reload_editor=True, refresh_styles=True):
        updated = project.normalized()
        if updated == self.subtitle_project.normalized():
            return False

        rows = self.selected_subtitle_rows() if selected_rows is None else list(selected_rows)
        focus_widget = QApplication.focusWidget()
        self.push_undo_state()
        self._syncing_subtitle_editor = True
        try:
            self.subtitle_project = updated
            if refresh_styles:
                self.refresh_style_combo()
            self.refresh_subtitle_table()
            self.restore_subtitle_selection(rows)
            if self.subtitle_overlay_item is not None:
                self.subtitle_overlay_item.set_project(self.subtitle_project)
                self.subtitle_overlay_item.set_current_time(self.current_expert_seconds())
            self.update_expert_controls_state()
        finally:
            self._syncing_subtitle_editor = False

        if reload_editor:
            self.on_subtitle_selection_changed()
            if focus_widget is not None:
                focus_widget.setFocus()
        elif focus_widget is not None:
            focus_widget.setFocus()

        if status:
            self.status_label.setText(status)
        return True

    def apply_subtitle_style_controls_to_selection(self, status):
        style_name = self.current_style_name()
        rows = self.selected_subtitle_rows()
        target_style = self.scoped_style_for_rows(self.subtitle_project, self.style_from_controls(style_name), rows)
        project = self.with_style_in_project(self.subtitle_project, target_style)
        style_name = target_style.name

        if rows:
            cues = list(project.cues)
            for row in rows:
                cue = cues[row]
                cues[row] = SubtitleCue(
                    start=cue.start,
                    end=cue.end,
                    text=cue.text,
                    style_name=style_name,
                    source_kind=cue.source_kind,
                    raw_tags=cue.raw_tags,
                    raw_text="",
                    layer=cue.layer,
                )
            project = self.subtitle_project_with_cues(project, cues)

        label = status if rows else f"已准备模板: {self.subtitle_style_combo.currentText()}"
        self.set_subtitle_project_after_edit(project, rows, label, reload_editor=bool(rows), refresh_styles=True)

    def apply_subtitle_effect_controls_to_selection(self):
        rows = self.selected_subtitle_rows()
        if not rows:
            return

        cues = list(self.subtitle_project.cues)
        for row in rows:
            cue = cues[row]
            cues[row] = SubtitleCue(
                start=cue.start,
                end=cue.end,
                text=cue.text,
                style_name=cue.style_name,
                source_kind=cue.source_kind,
                raw_tags=self.raw_tags_from_effect_controls(cue.raw_tags),
                raw_text="",
                layer=cue.layer,
            )
        project = self.subtitle_project_with_cues(self.subtitle_project, cues)
        self.set_subtitle_project_after_edit(project, rows, f"已更新 {len(rows)} 条字幕效果", reload_editor=False, refresh_styles=False)

    def apply_current_subtitle_from_editor(self, status, reload_editor):
        rows = self.selected_subtitle_rows()
        if len(rows) != 1:
            return False

        row = rows[0]
        if row < 0 or row >= len(self.subtitle_project.cues):
            return False

        text = self.subtitle_text_edit.toPlainText()
        start = self.subtitle_start_spin.value()
        end = self.subtitle_end_spin.value()
        if not text.strip() or end <= start:
            return False

        original = self.subtitle_project.cues[row]
        try:
            cue = SubtitleCue(
                start=start,
                end=end,
                text=text,
                style_name=original.style_name,
                source_kind=original.source_kind,
                raw_tags=original.raw_tags,
                raw_text="",
                layer=original.layer,
            ).normalized()
        except SubtitleValidationError:
            return False

        if cue == original:
            return False

        cues = list(self.subtitle_project.cues)
        cues[row] = cue
        project = self.subtitle_project_with_cues(self.subtitle_project, cues)
        selected_row = project.cues.index(cue) if cue in project.cues else row
        return self.set_subtitle_project_after_edit(
            project,
            [selected_row],
            status,
            reload_editor=reload_editor,
            refresh_styles=False,
        )

    def replace_subtitle_project(self, project):
        self.subtitle_project = project.normalized()
        self.refresh_style_combo()
        self.refresh_subtitle_table()
        self.timeline_widget.set_subtitle_cues(self.subtitle_project.cues)
        if self.subtitle_overlay_item is not None:
            self.subtitle_overlay_item.set_project(self.subtitle_project)
            self.subtitle_overlay_item.set_current_time(self.current_expert_seconds())
        self.update_expert_controls_state()

    def refresh_expert_delete_ranges_list(self):
        self.expert_delete_ranges_list.clear()
        for index, (start, end) in enumerate(self.expert_delete_ranges, start=1):
            self.expert_delete_ranges_list.addItem(f"{index}. {format_time(start)} - {format_time(end)}")
        self.timeline_widget.set_delete_ranges(self.expert_delete_ranges)
        self.update_expert_delete_range_buttons()

    def update_expert_delete_range_buttons(self):
        has_selection = self.expert_delete_ranges_list.currentRow() >= 0
        has_ranges = bool(self.expert_delete_ranges)
        enabled = self.current_file is not None and self.process_thread is None and self.transcribe_thread is None
        self.remove_expert_range_button.setEnabled(enabled and has_selection)
        self.clear_expert_ranges_button.setEnabled(enabled and has_ranges)

    def add_expert_delete_range_from_selection(self):
        try:
            ranges = add_delete_range_from_selection(
                self.expert_delete_ranges,
                self.expert_selection,
                total_duration=self.expert_duration_seconds or None,
            )
        except TimelineStateError as exc:
            QMessageBox.warning(self, "删除片段失败", str(exc))
            return

        self.push_undo_state()
        self.expert_delete_ranges = [item.as_tuple() for item in ranges]
        self.expert_selection = TimelineSelection(self.expert_selection.end, self.expert_selection.end)
        self.timeline_widget.set_selection(self.expert_selection)
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText(f"已加入删除片段，共 {len(self.expert_delete_ranges)} 段")
        self.update_expert_controls_state()

    def delete_expert_current_frame(self):
        try:
            ranges = delete_current_frame(
                self.current_expert_seconds(),
                self.expert_fps,
                self.expert_delete_ranges,
                total_duration=self.expert_duration_seconds or None,
            )
        except TimelineStateError as exc:
            QMessageBox.warning(self, "删除当前帧失败", str(exc))
            return

        self.push_undo_state()
        self.expert_delete_ranges = [item.as_tuple() for item in ranges]
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText(f"已删除当前帧，共 {len(self.expert_delete_ranges)} 段")

    def remove_selected_expert_delete_range(self):
        row = self.expert_delete_ranges_list.currentRow()
        if row < 0 or row >= len(self.expert_delete_ranges):
            return
        self.push_undo_state()
        del self.expert_delete_ranges[row]
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText(f"已删除片段，剩余 {len(self.expert_delete_ranges)} 段")

    def clear_expert_delete_ranges(self):
        if not self.expert_delete_ranges:
            return
        if not self.confirm_clear("清空删除片段", f"确定清空全部 {len(self.expert_delete_ranges)} 个删除片段吗？"):
            return
        self.push_undo_state()
        self.expert_delete_ranges = []
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText("已清空删除片段")

    def refresh_subtitle_table(self):
        cues = self.subtitle_project.cues
        self.subtitle_table.setRowCount(len(cues))
        for row, cue in enumerate(cues):
            self.update_subtitle_table_row(row, cue)
        self.timeline_widget.set_subtitle_cues(cues)
        self.update_subtitle_buttons()

    def update_subtitle_table_row(self, row, cue):
        self.subtitle_table.setItem(row, 0, QTableWidgetItem(f"{cue.start:.2f}"))
        self.subtitle_table.setItem(row, 1, QTableWidgetItem(f"{cue.end:.2f}"))
        self.subtitle_table.setItem(row, 2, QTableWidgetItem(cue.style_name))
        self.subtitle_table.setItem(row, 3, QTableWidgetItem(cue.text.replace("\n", " / ")))

    def selected_subtitle_rows(self):
        if not hasattr(self, "subtitle_table") or self.subtitle_table.selectionModel() is None:
            return []
        rows = sorted({index.row() for index in self.subtitle_table.selectionModel().selectedRows()})
        return [row for row in rows if 0 <= row < len(self.subtitle_project.cues)]

    def current_subtitle_row(self):
        rows = self.selected_subtitle_rows()
        current = self.subtitle_table.currentRow()
        if current in rows:
            return current
        return rows[0] if rows else -1

    def restore_subtitle_selection(self, rows):
        rows = [row for row in rows if 0 <= row < len(self.subtitle_project.cues)]
        self.subtitle_table.clearSelection()
        if not rows:
            return
        selection_model = self.subtitle_table.selectionModel()
        for row in rows:
            index = self.subtitle_table.model().index(row, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.subtitle_table.setCurrentCell(rows[0], 0)

    def select_subtitle_row(self, row):
        if row < 0 or row >= len(self.subtitle_project.cues):
            return
        self.subtitle_table.selectRow(row)
        self.on_subtitle_selection_changed()

    def seek_to_subtitle_row(self, row, _column):
        self.select_subtitle_row(row)
        if 0 <= row < len(self.subtitle_project.cues):
            self.seek_expert_seconds(self.subtitle_project.cues[row].start)

    def on_subtitle_selection_changed(self):
        if self._syncing_subtitle_editor:
            return

        row = self.current_subtitle_row()
        self.timeline_widget.set_selected_subtitle_index(row)
        if row < 0 or row >= len(self.subtitle_project.cues):
            self._syncing_subtitle_editor = True
            try:
                self.subtitle_start_spin.setValue(0)
                self.subtitle_end_spin.setValue(0)
                self.subtitle_text_edit.clear()
                self.load_effect_controls(None)
            finally:
                self._syncing_subtitle_editor = False
            self.update_subtitle_buttons()
            return

        cue = self.subtitle_project.cues[row]
        self._subtitle_timing_dirty = False
        self._syncing_subtitle_editor = True
        try:
            self.subtitle_start_spin.setValue(cue.start)
            self.subtitle_end_spin.setValue(cue.end)
            self.subtitle_text_edit.setPlainText(cue.text)
            combo_index = self.subtitle_style_combo.findData(cue.style_name)
            if combo_index >= 0:
                self.subtitle_style_combo.setCurrentIndex(combo_index)
            self.load_style_controls(cue.style_name)
            self.load_effect_controls(cue)
        finally:
            self._syncing_subtitle_editor = False
        self.expert_selection = TimelineSelection(cue.start, cue.end)
        self.timeline_widget.set_selection(self.expert_selection)
        self.update_subtitle_buttons()

    def update_subtitle_buttons(self):
        enabled = self.current_file is not None and self.process_thread is None and self.transcribe_thread is None
        has_selection = bool(self.selected_subtitle_rows())
        if hasattr(self, "copy_subtitles_action"):
            self.copy_subtitles_action.setEnabled(has_selection)
        if hasattr(self, "delete_subtitles_action"):
            self.delete_subtitles_action.setEnabled(enabled and has_selection)
        self.recognize_subtitle_button.setEnabled(enabled and self.ffmpeg_path is not None and self.has_transcribable_audio())

    def has_transcribable_audio(self):
        if self.current_file is None:
            return False
        source_enabled = bool(self.current_video_has_audio and not self.source_audio_muted)
        return source_enabled or any(track.volume > 0 for track in self.audio_tracks)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.deleteLater()

    def refresh_audio_controls(self):
        if not hasattr(self, "audio_tracks_list"):
            return

        self._syncing_audio_controls = True
        self.source_audio_check.setChecked(bool(self.current_video_has_audio and not self.source_audio_muted))
        self._syncing_audio_controls = False

        self.audio_tracks_list.clear()
        for index, track in enumerate(self.audio_tracks, start=1):
            self.audio_tracks_list.addItem(f"{index}. {Path(track.path).name}  {int(track.volume * 100)}%")

        self._clear_layout(self.audio_track_controls_layout)
        self.audio_track_volume_spins = []
        self.audio_track_remove_buttons = []
        for index, track in enumerate(self.audio_tracks):
            row = QHBoxLayout()
            name_label = QLabel(Path(track.path).name)
            name_label.setToolTip(track.path)
            row.addWidget(name_label, 1)

            volume_spin = QDoubleSpinBox()
            volume_spin.setRange(0.0, 2.0)
            volume_spin.setDecimals(2)
            volume_spin.setSingleStep(0.05)
            volume_spin.setValue(track.volume)
            volume_spin.editingFinished.connect(
                lambda spin=volume_spin, row_index=index: self.update_audio_track_volume(row_index, spin.value())
            )
            row.addWidget(volume_spin)
            self.audio_track_volume_spins.append(volume_spin)

            remove_button = QPushButton("删除")
            remove_button.clicked.connect(lambda _checked=False, row_index=index: self.remove_audio_track(row_index))
            row.addWidget(remove_button)
            self.audio_track_remove_buttons.append(remove_button)
            self.audio_track_controls_layout.addLayout(row)

        self.update_audio_buttons()

    def update_audio_buttons(self):
        if not hasattr(self, "source_audio_check"):
            return
        enabled = self.current_file is not None and self.process_thread is None and self.transcribe_thread is None
        self.source_audio_check.setEnabled(enabled and self.current_video_has_audio)
        self.add_audio_track_button.setEnabled(enabled and len(self.audio_tracks) < 2)
        self.audio_tracks_list.setEnabled(enabled)
        for spin in getattr(self, "audio_track_volume_spins", []):
            spin.setEnabled(enabled)
        for button in getattr(self, "audio_track_remove_buttons", []):
            button.setEnabled(enabled)

    def on_source_audio_toggled(self, checked):
        if self._syncing_audio_controls:
            return
        self.push_undo_state()
        self.source_audio_muted = not bool(checked)
        self.update_audio_buttons()
        self.update_subtitle_buttons()
        self.status_label.setText("源音已开启" if checked else "源音已静音")

    def add_audio_track(self):
        if len(self.audio_tracks) >= 2:
            QMessageBox.information(self, "音轨已满", "最多添加 2 条音频。")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "添加音频",
            "",
            "音频文件 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma);;所有文件 (*.*)",
        )
        if not file_path:
            return

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "文件无效", "选择的音频文件不存在。")
            return
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            QMessageBox.warning(self, "格式不支持", "请选择常见音频文件，例如 MP3、WAV、M4A。")
            return

        self.push_undo_state()
        self.audio_tracks.append(AudioTrack(str(path), 1.0))
        self.refresh_audio_controls()
        self.update_expert_controls_state()
        self.status_label.setText(f"已添加音频，共 {len(self.audio_tracks)} 条")

    def update_audio_track_volume(self, index, value):
        if self._syncing_audio_controls or index < 0 or index >= len(self.audio_tracks):
            return
        track = self.audio_tracks[index]
        if abs(float(track.volume) - float(value)) < 0.001:
            return
        self.push_undo_state()
        self.audio_tracks[index] = AudioTrack(track.path, value)
        self.refresh_audio_controls()
        self.update_expert_controls_state()

    def remove_audio_track(self, index):
        if index < 0 or index >= len(self.audio_tracks):
            return
        self.push_undo_state()
        del self.audio_tracks[index]
        self.refresh_audio_controls()
        self.update_expert_controls_state()
        self.status_label.setText(f"已删除音频，剩余 {len(self.audio_tracks)} 条")

    def add_expert_subtitle(self):
        text = self.subtitle_text_edit.toPlainText()
        style_name = self.current_style_name()
        try:
            cues, new_cue = add_subtitle_from_selection_or_playhead(
                self.subtitle_project.cues,
                self.expert_selection,
                self.current_expert_seconds(),
                text,
                total_duration=self.expert_duration_seconds or None,
                default_duration=2.0,
                style_name=style_name,
                raw_tags=self.raw_tags_from_effect_controls(""),
            )
        except (SubtitleValidationError, TimelineStateError) as exc:
            QMessageBox.warning(self, "加字幕失败", str(exc))
            return

        project = self.ensure_style_exists(style_name)
        project = self.apply_style_controls_to_project(project, style_name)
        project = SubtitleProject(
            cues=cues,
            styles=project.styles,
            script_info=project.script_info,
            enabled=True,
            play_res_x=project.play_res_x,
            play_res_y=project.play_res_y,
            default_style_name=project.default_style_name,
        ).normalized()
        self.push_undo_state()
        self.replace_subtitle_project(project)
        row = project.cues.index(new_cue)
        self.select_subtitle_row(row)
        self.status_label.setText(f"已添加字幕，共 {len(project.cues)} 条")

    def remove_selected_subtitles(self):
        rows = set(self.selected_subtitle_rows())
        if not rows:
            return

        cues = [cue for index, cue in enumerate(self.subtitle_project.cues) if index not in rows]
        project = SubtitleProject(
            cues=tuple(cues),
            styles=self.subtitle_project.styles,
            script_info=self.subtitle_project.script_info,
            enabled=True,
            play_res_x=self.subtitle_project.play_res_x,
            play_res_y=self.subtitle_project.play_res_y,
            default_style_name=self.subtitle_project.default_style_name,
        ).normalized()
        self.push_undo_state()
        next_row = min(rows)
        self.replace_subtitle_project(project)
        if project.cues:
            self.select_subtitle_row(min(next_row, len(project.cues) - 1))
        self.status_label.setText(f"已删除字幕，剩余 {len(project.cues)} 条")

    def copy_selected_subtitles_as_srt(self):
        rows = self.selected_subtitle_rows()
        if not rows:
            return
        cues = [self.subtitle_project.cues[row] for row in rows]
        QApplication.clipboard().setText(serialize_srt_entries(cues))
        self.status_label.setText(f"已复制 {len(cues)} 条字幕为 SRT 文本")

    def import_subtitle_file(self):
        if self.current_file is None:
            QMessageBox.information(self, "请先选择视频", "请先打开一个视频。")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入字幕",
            "",
            "字幕文件 (*.ass *.srt);;所有文件 (*.*)",
        )
        if not file_path:
            return

        try:
            project = load_subtitle_file(file_path, video_size=self.expert_video_size)
        except (OSError, SubtitleValidationError) as exc:
            QMessageBox.warning(self, "字幕导入失败", str(exc))
            return

        self.push_undo_state()
        self.replace_subtitle_project(project)
        self.status_label.setText(f"已导入字幕 {len(project.cues)} 条")

    def import_subtitle_clipboard(self):
        if self.current_file is None:
            QMessageBox.information(self, "请先选择视频", "请先打开一个视频。")
            return

        text = QApplication.clipboard().text().strip()
        if not text:
            QMessageBox.information(self, "剪贴板为空", "剪贴板里没有可导入的字幕文本。")
            return

        try:
            project = load_subtitle_text(text, video_size=self.expert_video_size)
        except SubtitleValidationError as exc:
            QMessageBox.warning(self, "字幕导入失败", str(exc))
            return

        self.push_undo_state()
        self.replace_subtitle_project(project)
        self.status_label.setText(f"已导入字幕 {len(project.cues)} 条")

    def build_transcription_plan_from_controls(self):
        try:
            return EditPlan(
                source_audio_muted=self.source_audio_muted,
                audio_tracks=tuple(self.audio_tracks),
                has_audio=self.current_video_has_audio,
            ).validate()
        except PlanValidationError as exc:
            QMessageBox.warning(self, "识别参数无效", str(exc))
            return None

    def start_subtitle_transcription(self):
        if self.current_file is None:
            QMessageBox.information(self, "请先选择视频", "请先打开一个视频。")
            return
        if self.ffmpeg_path is None:
            QMessageBox.warning(self, "未找到 FFmpeg", "请先将 ffmpeg.exe 和 ffprobe.exe 放到软件目录，或安装到系统 PATH。")
            return
        if self.transcribe_thread is not None or self.process_thread is not None:
            return
        if not self.has_transcribable_audio():
            QMessageBox.information(self, "没有可识别音频", "请开启源音，或添加一条音频。")
            return
        if self.subtitle_project.cues and not self.confirm_clear("替换字幕", "识别结果会替换当前字幕，确定继续吗？"):
            return

        edit_plan = self.build_transcription_plan_from_controls()
        if edit_plan is None:
            return

        self.transcribe_thread = SubtitleTranscribeThread(
            self.ffmpeg_path,
            str(self.current_file),
            edit_plan,
            self.expert_video_size,
            self.expert_duration_seconds or None,
            bilingual=self.bilingual_subtitle_check.isChecked(),
            parent=self,
        )
        self.transcribe_thread.progress_updated.connect(self.progress_bar.setValue)
        self.transcribe_thread.status_changed.connect(self.status_label.setText)
        self.transcribe_thread.finished_success.connect(self.on_transcription_success)
        self.transcribe_thread.finished_error.connect(self.on_transcription_error)
        self.progress_bar.setValue(0)
        self.set_processing_state(True)
        self.status_label.setText("字幕识别已开始...")
        self.transcribe_thread.start()

    def on_transcription_success(self, project):
        self.cleanup_transcribe_thread()
        self.set_processing_state(False)
        self.progress_bar.setValue(100)
        self.push_undo_state()
        self.replace_subtitle_project(project)
        if project.cues:
            self.select_subtitle_row(0)
        self.status_label.setText(f"已识别字幕 {len(project.cues)} 条")

    def on_transcription_error(self, message):
        self.cleanup_transcribe_thread()
        self.set_processing_state(False)
        if message == "字幕识别已取消。":
            self.progress_bar.setValue(0)
            self.status_label.setText(message)
            return
        self.status_label.setText(message)
        QMessageBox.warning(self, "字幕识别失败", message)

    def update_expert_controls_state(self):
        enabled = self.current_file is not None and self.process_thread is None and self.transcribe_thread is None
        preview_enabled = enabled and self.media_player is not None

        self.expert_open_file_button.setEnabled(self.process_thread is None and self.transcribe_thread is None)
        self.expert_play_button.setEnabled(preview_enabled)
        self.expert_position_slider.setEnabled(preview_enabled)
        self.timeline_widget.setEnabled(enabled)
        self.delete_selection_button.setEnabled(enabled and self.expert_selection.is_range)
        self.delete_frame_button.setEnabled(enabled and self.expert_duration_seconds > 0)
        self.import_subtitle_button.setEnabled(enabled)
        self.import_clipboard_button.setEnabled(enabled)
        subtitle_rows = self.selected_subtitle_rows() if hasattr(self, "subtitle_table") else []
        single_or_none = len(subtitle_rows) <= 1
        self.subtitle_table.setEnabled(enabled)
        self.subtitle_text_edit.setEnabled(enabled and single_or_none)
        self.subtitle_start_spin.setEnabled(enabled and single_or_none)
        self.subtitle_end_spin.setEnabled(enabled and single_or_none)
        self.add_subtitle_button.setEnabled(enabled and single_or_none and bool(self.subtitle_text_edit.toPlainText().strip()))
        self.subtitle_style_combo.setEnabled(enabled)
        self.subtitle_font_combo.setEnabled(enabled)
        self.subtitle_font_size_spin.setEnabled(enabled)
        self.subtitle_color_button.setEnabled(enabled)
        self.subtitle_fade_check.setEnabled(enabled)
        self.subtitle_fade_ms_spin.setEnabled(enabled and self.subtitle_fade_check.isChecked())
        self.expert_res_combo.setEnabled(enabled)
        self.update_expert_delete_range_buttons()
        self.update_subtitle_buttons()
        self.update_audio_buttons()

    def update_processing_buttons(self):
        busy = self.process_thread is not None or self.transcribe_thread is not None
        can_start = self.current_file is not None and self.ffmpeg_path is not None and not busy
        self.start_button_simple.setEnabled(can_start)
        self.start_button_expert.setEnabled(can_start)
        self.cancel_button_simple.setEnabled(busy)
        self.cancel_button_expert.setEnabled(busy)
        self.update_undo_action_state()

    def update_delete_range_controls(self, enabled):
        processing = self.process_thread is not None or self.transcribe_thread is not None
        active = enabled and not processing
        self.delete_start_spin.setEnabled(active)
        self.delete_end_spin.setEnabled(active)
        self.add_delete_range_button.setEnabled(active)
        self.delete_ranges_list.setEnabled(active)
        self.delete_ranges_list.setVisible(enabled or bool(self.delete_ranges))
        self.remove_delete_range_button.setVisible(enabled or bool(self.delete_ranges))
        self.clear_delete_ranges_button.setVisible(enabled or bool(self.delete_ranges))
        self.update_delete_range_buttons()

    def add_delete_range(self):
        start = self.delete_start_spin.value()
        end = self.delete_end_spin.value()
        if end <= start:
            QMessageBox.warning(self, "删除区间无效", "结束秒数必须大于开始秒数。")
            return
        before = len(self.delete_ranges)
        self.push_undo_state()
        self.delete_ranges = normalize_delete_ranges(self.delete_ranges + [(start, end)])
        self.refresh_delete_ranges_list()
        if len(self.delete_ranges) < before + 1:
            self.status_label.setText("已合并重叠或相邻的删除区间")
        else:
            self.status_label.setText(f"已添加删除区间，共 {len(self.delete_ranges)} 个")

    def remove_selected_delete_range(self):
        row = self.delete_ranges_list.currentRow()
        if row < 0 or row >= len(self.delete_ranges):
            return
        self.push_undo_state()
        del self.delete_ranges[row]
        self.refresh_delete_ranges_list()
        self.status_label.setText(f"已删除选中区间，剩余 {len(self.delete_ranges)} 个")

    def clear_delete_ranges(self):
        if not self.delete_ranges:
            return
        if not self.confirm_clear("清空删除区间", f"确定清空全部 {len(self.delete_ranges)} 个删除区间吗？"):
            return
        self.push_undo_state()
        self.delete_ranges = []
        self.refresh_delete_ranges_list()
        self.status_label.setText("已清空删除区间")

    def refresh_delete_ranges_list(self):
        self.delete_ranges_list.clear()
        for index, (start, end) in enumerate(self.delete_ranges, start=1):
            self.delete_ranges_list.addItem(f"{index}. {format_time(start)} - {format_time(end)}")
        self.update_delete_range_buttons()

    def update_delete_range_buttons(self):
        enabled = self.delete_range_check.isChecked() and self.process_thread is None and self.transcribe_thread is None
        has_ranges = bool(self.delete_ranges)
        has_selection = self.delete_ranges_list.currentRow() >= 0
        self.remove_delete_range_button.setEnabled(enabled and has_selection)
        self.clear_delete_ranges_button.setEnabled(enabled and has_ranges)

    def get_delete_ranges_for_processing(self):
        if self.delete_ranges:
            ranges = self.delete_ranges
        else:
            start = self.delete_start_spin.value()
            end = self.delete_end_spin.value()
            if end <= start:
                QMessageBox.warning(self, "删除区间无效", "结束秒数必须大于开始秒数。")
                return None
            ranges = [(start, end)]

        normalized = normalize_delete_ranges(ranges)
        if not normalized:
            QMessageBox.warning(self, "删除区间无效", "请至少添加一个有效的删除区间。")
            return None

        if normalized != self.delete_ranges:
            self.delete_ranges = normalized
            self.refresh_delete_ranges_list()
        return normalized

    def build_edit_plan_from_controls(self):
        delete_ranges = []
        if self.delete_range_check.isChecked():
            delete_ranges = self.get_delete_ranges_for_processing()
            if delete_ranges is None:
                return None

        plan = EditPlan(
            skip_seconds=self.skip_spin.value(),
            delete_ranges=tuple(DeleteRange(start, end) for start, end in delete_ranges),
            output=OutputOptions(resolution=self.res_combo.currentData()),
        )
        try:
            return plan.validate()
        except PlanValidationError as exc:
            QMessageBox.warning(self, "编辑参数无效", str(exc))
            return None

    def build_expert_edit_plan_from_controls(self):
        try:
            return EditPlan(
                delete_ranges=tuple(DeleteRange(start, end) for start, end in self.expert_delete_ranges),
                output=OutputOptions(resolution=self.expert_output_resolution),
                subtitles=self.subtitle_project,
                source_audio_muted=self.source_audio_muted,
                audio_tracks=tuple(self.audio_tracks),
            ).validate()
        except (PlanValidationError, SubtitleValidationError) as exc:
            QMessageBox.warning(self, "编辑参数无效", str(exc))
            return None

    def start_simple_processing(self):
        self.start_processing(self.build_edit_plan_from_controls())

    def start_expert_processing(self):
        self.start_processing(self.build_expert_edit_plan_from_controls())

    def start_processing(self, edit_plan):
        if self.process_thread is not None or self.transcribe_thread is not None:
            return
        if self.current_file is None:
            QMessageBox.information(self, "请选择文件", "请先选择一个视频文件。")
            return
        if self.ffmpeg_path is None:
            QMessageBox.warning(self, "未找到 FFmpeg", "请先将 ffmpeg.exe 和 ffprobe.exe 放到软件目录，或安装到系统 PATH。")
            return
        if edit_plan is None:
            return

        output_path = self.build_output_path(self.current_file)
        self.process_thread = VideoProcessThread(self.ffmpeg_path, str(self.current_file), str(output_path), edit_plan, self)
        self.process_thread.progress_updated.connect(self.progress_bar.setValue)
        self.process_thread.status_changed.connect(self.status_label.setText)
        self.process_thread.finished_success.connect(self.on_process_success)
        self.process_thread.finished_error.connect(self.on_process_error)
        self.progress_bar.setValue(0)
        self.set_processing_state(True)
        self.status_label.setText("任务已开始...")
        self.process_thread.start()

    def cancel_processing(self):
        if self.process_thread is not None:
            self.status_label.setText("正在取消处理...")
            self.process_thread.stop()
        if self.transcribe_thread is not None:
            self.status_label.setText("正在取消字幕识别...")
            self.transcribe_thread.stop()

    def on_process_success(self, output_path):
        self.cleanup_thread()
        self.set_processing_state(False)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"处理完成: {output_path}")
        self.open_output_directory(output_path)
        QMessageBox.information(self, "处理成功", f"视频已导出:\n{output_path}\n\n已自动打开所在目录。")

    def on_process_error(self, message):
        self.cleanup_thread()
        self.set_processing_state(False)
        if message == "处理已取消":
            self.progress_bar.setValue(0)
            self.status_label.setText(message)
            return
        self.status_label.setText(message)
        QMessageBox.critical(self, "处理失败", message)

    def set_processing_state(self, processing):
        if processing and self.media_player is not None:
            self.media_player.pause()

        self.drop_area.setEnabled(not processing)
        self.skip_spin.setEnabled(not processing)
        self.delete_range_check.setEnabled(not processing)
        self.res_combo.setEnabled(not processing)
        self.update_delete_range_controls(self.delete_range_check.isChecked())
        self.update_expert_controls_state()
        self.update_processing_buttons()

    def cleanup_thread(self):
        if self.process_thread is None:
            return
        self.process_thread.wait(3000)
        self.process_thread.deleteLater()
        self.process_thread = None
        self.update_processing_buttons()

    def cleanup_transcribe_thread(self):
        if self.transcribe_thread is None:
            return
        self.transcribe_thread.wait(3000)
        self.transcribe_thread.deleteLater()
        self.transcribe_thread = None
        self.update_processing_buttons()

    def check_ffmpeg(self):
        self.ffmpeg_path = find_ffmpeg()
        self.ffprobe_path = find_ffprobe(self.ffmpeg_path)
        self.status_label.setText("准备就绪" if self.ffmpeg_path else "未检测到 FFmpeg，暂时无法开始处理")
        self.update_processing_buttons()
        self.update_expert_controls_state()

    def start_thumbnail_generation(self, path):
        if self.ffmpeg_path is None:
            self.drop_area.clear_thumbnail("拖放视频文件到此处\n或点击这里选择文件")
            return

        for thread in list(self.thumbnail_threads):
            thread.stop()

        self.drop_area.set_loading()
        self.status_label.setText("正在生成视频预览...")

        thread = ThumbnailThread(self.ffmpeg_path, str(path), self)
        self.thumbnail_threads.append(thread)
        thread.thumbnail_ready.connect(self.on_thumbnail_ready)
        thread.thumbnail_failed.connect(self.on_thumbnail_failed)
        thread.finished.connect(lambda: self.cleanup_thumbnail_thread(thread))
        thread.start()

    def on_thumbnail_ready(self, video_path, thumbnail_path):
        if self.current_file is None or str(self.current_file) != str(Path(video_path)):
            Path(thumbnail_path).unlink(missing_ok=True)
            return

        pixmap = QPixmap(str(thumbnail_path))
        Path(thumbnail_path).unlink(missing_ok=True)
        if pixmap.isNull():
            self.drop_area.clear_thumbnail("预览生成失败\n点击或拖入其他视频可重新选择")
            self.status_label.setText("文件已加载，预览生成失败，仍可处理")
            return

        self.drop_area.set_thumbnail(pixmap)
        if self.process_thread is None and self.transcribe_thread is None:
            self.status_label.setText("文件已加载，可以开始处理")

    def on_thumbnail_failed(self, video_path, message):
        if self.current_file is None or str(self.current_file) != str(Path(video_path)):
            return
        self.drop_area.clear_thumbnail("预览生成失败\n点击或拖入其他视频可重新选择")
        if self.process_thread is None and self.transcribe_thread is None:
            self.status_label.setText(f"{message}，仍可处理")

    def cleanup_thumbnail_thread(self, thread):
        if thread in self.thumbnail_threads:
            self.thumbnail_threads.remove(thread)
        thread.deleteLater()

    def build_output_path(self, input_path):
        target_dir = input_path.parent
        base_name = f"{input_path.stem}_clipped"
        candidate = target_dir / f"{base_name}.mp4"
        index = 1
        while candidate.exists():
            candidate = target_dir / f"{base_name}_{index}.mp4"
            index += 1
        return candidate

    def open_output_directory(self, output_path):
        output_dir = Path(output_path).resolve().parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir))):
            self.status_label.setText(f"处理完成，但无法自动打开目录: {output_dir}")

    def closeEvent(self, event):
        if self.process_thread is not None:
            self.process_thread.stop()
            self.process_thread.wait(3000)
        if self.transcribe_thread is not None:
            self.transcribe_thread.stop()
            self.transcribe_thread.wait(3000)
        for thread in list(self.thumbnail_threads):
            thread.stop()
            thread.wait(3000)
        if self.media_player is not None:
            self.media_player.stop()
        event.accept()


class VideoClipperApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("VideoClipper")
        self.window = MainWindow()

    def run(self):
        self.window.show()
        return self.app.exec()
