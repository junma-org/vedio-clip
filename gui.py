"""
GUI模块 - PySide6界面
负责所有界面展示和用户交互
"""
import sys
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QPixmap
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA_AVAILABLE = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
    MULTIMEDIA_AVAILABLE = False
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_utils import (
    build_ffmpeg_command_from_plan,
    build_thumbnail_command,
    find_ffmpeg,
    find_ffprobe,
    format_file_size,
    format_time,
    get_video_info,
    prepare_subtitle_file_for_plan,
)
from edit_model import (
    DeleteRange,
    EditPlan,
    OutputOptions,
    PlanValidationError,
    normalize_delete_ranges,
)
from expert_mode import add_delete_range_from_marks, build_expert_edit_plan
from subtitle_model import (
    SubtitleStyle,
    SubtitleTrack,
    SubtitleValidationError,
    add_subtitle_from_marks,
    read_srt_file,
)


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

DEVELOPER_WECHAT = "Summer_1987s"


class VideoProcessThread(QThread):
    """视频处理后台线程"""

    progress_updated = Signal(int)
    status_changed = Signal(str)
    finished_success = Signal(str)
    finished_error = Signal(str)

    def __init__(
        self,
        ffmpeg_path,
        input_path,
        output_path,
        edit_plan,
        parent=None,
    ):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.edit_plan = edit_plan
        self.is_running = True
        self._process = None

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
                edit_plan = self.edit_plan.with_has_audio(
                    video_info.get("has_audio", True)
                ).validate(total_duration)
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
            )

            action_parts = []
            if edit_plan.skip_seconds > 0:
                action_parts.append(f"跳过前 {format_time(edit_plan.skip_seconds)}")
            for delete_range in edit_plan.delete_ranges:
                action_parts.append(
                    f"删除 {format_time(delete_range.start)}-{format_time(delete_range.end)}"
                )
            if edit_plan.subtitles.has_entries():
                action_parts.append(f"烧录字幕 {len(edit_plan.subtitles.entries)} 条")
            if not action_parts:
                action_parts.append("转换视频")

            self.status_changed.emit(
                f"正在处理: {'，'.join(action_parts)}，输出时长约 {format_time(remaining_duration)}"
            )

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=creationflags,
            )

            while self.is_running:
                line = self._process.stderr.readline()
                if not line:
                    if self._process.poll() is not None:
                        break
                    continue

                if "time=" not in line:
                    continue

                try:
                    time_start = line.find("time=") + 5
                    time_end = line.find(" ", time_start)
                    if time_end == -1:
                        time_end = len(line)
                    time_str = line[time_start:time_end].strip()

                    parts = time_str.split(":")
                    if len(parts) == 3:
                        current_seconds = (
                            float(parts[0]) * 3600
                            + float(parts[1]) * 60
                            + float(parts[2])
                        )
                        progress = min(int((current_seconds / remaining_duration) * 100), 100)
                        self.progress_updated.emit(progress)
                except Exception:
                    pass

            if self._process and not self.is_running and self._process.poll() is None:
                self._process.terminate()

            returncode = self._process.wait() if self._process else 1

            if returncode == 0 and self.is_running:
                self.progress_updated.emit(100)
                self.finished_success.emit(self.output_path)
            elif not self.is_running:
                self.finished_error.emit("处理已取消")
            else:
                self.finished_error.emit(f"FFmpeg错误 (返回码: {returncode})")

        except Exception as exc:
            self.finished_error.emit(f"处理异常: {exc}")
        finally:
            if subtitle_path:
                self._remove_temp_subtitle_file(subtitle_path)
            self._process = None

    def stop(self):
        self.is_running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()

    @staticmethod
    def _remove_temp_subtitle_file(path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


class ThumbnailThread(QThread):
    """后台提取视频第一帧，避免大文件阻塞界面。"""

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
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )
            self._process.communicate(timeout=30)

            if not self.is_running:
                Path(thumbnail_path).unlink(missing_ok=True)
                return

            if (
                self._process.returncode == 0
                and Path(thumbnail_path).exists()
                and Path(thumbnail_path).stat().st_size > 0
            ):
                self.thumbnail_ready.emit(self.input_path, thumbnail_path)
                return

            Path(thumbnail_path).unlink(missing_ok=True)
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


class DropArea(QFrame):
    """支持拖放和点击选择文件的区域"""

    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)
        self._source_pixmap = QPixmap()
        self._default_style = (
            "QFrame { border: 2px dashed #b8c1cc; border-radius: 12px; background: #fafcff; }"
        )
        self._active_style = (
            "QFrame { border: 2px dashed #2d7ff9; border-radius: 12px; background: #eaf2ff; }"
        )
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
        self.label.setText("正在生成视频预览...\n点击或拖入其他视频可重新选择")
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
        if target_size.width() <= 0 or target_size.height() <= 0:
            target_size = self.size()

        self.preview_label.setPixmap(
            self._source_pixmap.scaled(
                target_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
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
        if not urls:
            return

        file_path = urls[0].toLocalFile()
        if file_path:
            self.file_dropped.emit(file_path)

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


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("极简视频剪辑工具")
        self.setMinimumSize(560, 600)

        self.current_file = None
        self.ffmpeg_path = None
        self.ffprobe_path = None
        self.process_thread = None
        self.thumbnail_threads = []
        self.delete_ranges = []
        self.expert_delete_ranges = []
        self.expert_in_point = None
        self.expert_out_point = None
        self.subtitle_entries = []
        self.expert_duration_seconds = 0
        self._syncing_expert_position = False
        self.media_player = None
        self.audio_output = None

        self.init_ui()
        self.check_ffmpeg()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        top_layout = QHBoxLayout()
        top_layout.addStretch()
        self.developer_button = QPushButton(f"联系开发者 👉 wx: {DEVELOPER_WECHAT}")
        self.developer_button.setCursor(Qt.PointingHandCursor)
        self.developer_button.setFlat(True)
        self.developer_button.setStyleSheet(
            "QPushButton { color: #2d5f8b; font-size: 12px; border: none; padding: 2px 4px; }"
            "QPushButton:hover { color: #17466f; text-decoration: underline; }"
        )
        self.developer_button.clicked.connect(self.copy_developer_wechat)
        top_layout.addWidget(self.developer_button)
        layout.addLayout(top_layout)

        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self.on_file_dropped)
        layout.addWidget(self.drop_area)

        self.file_label = QLabel("未选择文件")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("color: #556; font-size: 12px;")
        layout.addWidget(self.file_label)

        self.mode_tabs = QTabWidget()
        self.mode_tabs.currentChanged.connect(self.on_mode_changed)

        settings_box = QFrame()
        settings_box.setObjectName("settingsPanel")
        settings_box.setStyleSheet(
            "QFrame#settingsPanel { background: #f7f9fc; border: 1px solid #e1e7ef; border-radius: 10px; }"
        )
        settings_layout = QVBoxLayout(settings_box)
        settings_layout.setContentsMargins(14, 14, 14, 14)
        settings_layout.setSpacing(10)

        skip_layout = QHBoxLayout()
        skip_layout.addWidget(QLabel("剪掉前:"))
        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(0, 3600)
        self.skip_spin.setValue(30)
        self.skip_spin.setSuffix(" 秒")
        self.skip_spin.setMinimumWidth(120)
        skip_layout.addWidget(self.skip_spin)
        skip_layout.addStretch()
        settings_layout.addLayout(skip_layout)

        delete_layout = QHBoxLayout()
        self.delete_range_check = QCheckBox("删除区间:")
        self.delete_range_check.toggled.connect(self.update_delete_range_controls)
        delete_layout.addWidget(self.delete_range_check)

        self.delete_start_spin = QSpinBox()
        self.delete_start_spin.setRange(0, 24 * 3600)
        self.delete_start_spin.setValue(80)
        self.delete_start_spin.setSuffix(" 秒")
        self.delete_start_spin.setMinimumWidth(110)
        delete_layout.addWidget(self.delete_start_spin)

        delete_layout.addWidget(QLabel("到"))

        self.delete_end_spin = QSpinBox()
        self.delete_end_spin.setRange(0, 24 * 3600)
        self.delete_end_spin.setValue(100)
        self.delete_end_spin.setSuffix(" 秒")
        self.delete_end_spin.setMinimumWidth(110)
        delete_layout.addWidget(self.delete_end_spin)

        self.add_delete_range_button = QPushButton("添加区间")
        self.add_delete_range_button.clicked.connect(self.add_delete_range)
        delete_layout.addWidget(self.add_delete_range_button)
        delete_layout.addStretch()
        settings_layout.addLayout(delete_layout)

        self.delete_ranges_list = QListWidget()
        self.delete_ranges_list.setMaximumHeight(92)
        self.delete_ranges_list.setAlternatingRowColors(True)
        self.delete_ranges_list.itemSelectionChanged.connect(self.update_delete_range_buttons)
        settings_layout.addWidget(self.delete_ranges_list)

        delete_buttons_layout = QHBoxLayout()
        delete_buttons_layout.addStretch()
        self.remove_delete_range_button = QPushButton("删除选中")
        self.remove_delete_range_button.clicked.connect(self.remove_selected_delete_range)
        delete_buttons_layout.addWidget(self.remove_delete_range_button)

        self.clear_delete_ranges_button = QPushButton("清空区间")
        self.clear_delete_ranges_button.clicked.connect(self.clear_delete_ranges)
        delete_buttons_layout.addWidget(self.clear_delete_ranges_button)
        settings_layout.addLayout(delete_buttons_layout)
        self.refresh_delete_ranges_list()
        self.update_delete_range_controls(False)

        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("输出分辨率:"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("保持原分辨率", None)
        self.res_combo.addItem("1920 x 1080 (横屏16:9)", (1920, 1080))
        self.res_combo.addItem("1440 x 1920 (竖屏9:16)", (1440, 1920))
        self.res_combo.addItem("1080 x 1920 (竖屏9:16)", (1080, 1920))
        self.res_combo.addItem("720 x 1280 (竖屏9:16)", (720, 1280))
        self.res_combo.addItem("720 x 480 (标清)", (720, 480))
        self.res_combo.setMinimumWidth(240)
        res_layout.addWidget(self.res_combo)
        res_layout.addStretch()
        settings_layout.addLayout(res_layout)

        self.mode_tabs.addTab(settings_box, "极简模式")
        self.expert_tab = self.create_expert_mode_tab()
        self.mode_tabs.addTab(self.expert_tab, "达人模式")
        layout.addWidget(self.mode_tabs)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("准备就绪")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #334; font-size: 12px;")
        layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()

        self.start_button = QPushButton("开始处理")
        self.start_button.clicked.connect(self.start_processing)
        self.start_button.setEnabled(False)
        button_layout.addWidget(self.start_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setEnabled(False)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)
        layout.addStretch()

    def create_expert_mode_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        tab_layout.addWidget(scroll_area)

        panel = QFrame()
        panel.setObjectName("expertPanel")
        panel.setStyleSheet(
            "QFrame#expertPanel { background: #f7f9fc; border: 1px solid #e1e7ef; border-radius: 10px; }"
        )
        scroll_area.setWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        if MULTIMEDIA_AVAILABLE:
            self.media_player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(0.5)
            self.media_player.setAudioOutput(self.audio_output)

            self.expert_video_widget = QVideoWidget()
            self.expert_video_widget.setMinimumHeight(240)
            self.media_player.setVideoOutput(self.expert_video_widget)
            self.media_player.durationChanged.connect(self.on_expert_duration_changed)
            self.media_player.positionChanged.connect(self.on_expert_position_changed)
            self.media_player.playbackStateChanged.connect(self.on_expert_playback_state_changed)
            self.media_player.errorOccurred.connect(self.on_expert_media_error)
            layout.addWidget(self.expert_video_widget)
        else:
            self.expert_video_widget = QLabel("当前环境不支持视频预览")
            self.expert_video_widget.setAlignment(Qt.AlignCenter)
            self.expert_video_widget.setMinimumHeight(180)
            self.expert_video_widget.setStyleSheet("color: #667; background: #edf1f6;")
            layout.addWidget(self.expert_video_widget)

        seek_layout = QHBoxLayout()
        self.expert_play_button = QPushButton("播放")
        self.expert_play_button.clicked.connect(self.toggle_expert_playback)
        seek_layout.addWidget(self.expert_play_button)

        self.expert_current_label = QLabel("0:00")
        self.expert_current_label.setMinimumWidth(46)
        seek_layout.addWidget(self.expert_current_label)

        self.expert_position_slider = QSlider(Qt.Horizontal)
        self.expert_position_slider.setRange(0, 0)
        self.expert_position_slider.valueChanged.connect(self.seek_expert_slider)
        seek_layout.addWidget(self.expert_position_slider, 1)

        self.expert_duration_label = QLabel("0:00")
        self.expert_duration_label.setMinimumWidth(46)
        seek_layout.addWidget(self.expert_duration_label)
        layout.addLayout(seek_layout)

        position_layout = QHBoxLayout()
        position_layout.addWidget(QLabel("定位到:"))
        self.expert_position_spin = QDoubleSpinBox()
        self.expert_position_spin.setRange(0, 0)
        self.expert_position_spin.setDecimals(1)
        self.expert_position_spin.setSingleStep(0.1)
        self.expert_position_spin.setSuffix(" 秒")
        self.expert_position_spin.setMinimumWidth(140)
        self.expert_position_spin.valueChanged.connect(self.seek_expert_seconds)
        position_layout.addWidget(self.expert_position_spin)
        position_layout.addStretch()
        layout.addLayout(position_layout)

        mark_layout = QHBoxLayout()
        self.expert_in_label = QLabel("入点: 未设置")
        self.expert_out_label = QLabel("出点: 未设置")
        mark_layout.addWidget(self.expert_in_label)
        mark_layout.addWidget(self.expert_out_label)
        mark_layout.addStretch()
        layout.addLayout(mark_layout)

        mark_buttons_layout = QHBoxLayout()
        self.set_in_point_button = QPushButton("设为入点")
        self.set_in_point_button.clicked.connect(self.set_expert_in_point)
        mark_buttons_layout.addWidget(self.set_in_point_button)

        self.set_out_point_button = QPushButton("设为出点")
        self.set_out_point_button.clicked.connect(self.set_expert_out_point)
        mark_buttons_layout.addWidget(self.set_out_point_button)

        self.add_expert_range_button = QPushButton("加入删除区间")
        self.add_expert_range_button.clicked.connect(self.add_expert_delete_range)
        mark_buttons_layout.addWidget(self.add_expert_range_button)
        mark_buttons_layout.addStretch()
        layout.addLayout(mark_buttons_layout)

        self.expert_delete_ranges_list = QListWidget()
        self.expert_delete_ranges_list.setMaximumHeight(100)
        self.expert_delete_ranges_list.setAlternatingRowColors(True)
        self.expert_delete_ranges_list.itemSelectionChanged.connect(self.update_expert_delete_range_buttons)
        layout.addWidget(self.expert_delete_ranges_list)

        expert_buttons_layout = QHBoxLayout()
        expert_buttons_layout.addStretch()
        self.remove_expert_range_button = QPushButton("删除选中")
        self.remove_expert_range_button.clicked.connect(self.remove_selected_expert_delete_range)
        expert_buttons_layout.addWidget(self.remove_expert_range_button)

        self.clear_expert_ranges_button = QPushButton("清空区间")
        self.clear_expert_ranges_button.clicked.connect(self.clear_expert_delete_ranges)
        expert_buttons_layout.addWidget(self.clear_expert_ranges_button)
        layout.addLayout(expert_buttons_layout)

        subtitle_header_layout = QHBoxLayout()
        subtitle_header_layout.addWidget(QLabel("字幕文本:"))
        self.subtitle_text_input = QLineEdit()
        self.subtitle_text_input.setPlaceholderText("输入要烧录的字幕")
        self.subtitle_text_input.textChanged.connect(self.update_expert_controls_state)
        subtitle_header_layout.addWidget(self.subtitle_text_input, 1)
        layout.addLayout(subtitle_header_layout)

        subtitle_style_layout = QHBoxLayout()
        subtitle_style_layout.addWidget(QLabel("字号:"))
        self.subtitle_font_size_spin = QSpinBox()
        self.subtitle_font_size_spin.setRange(12, 96)
        self.subtitle_font_size_spin.setValue(28)
        subtitle_style_layout.addWidget(self.subtitle_font_size_spin)

        subtitle_style_layout.addWidget(QLabel("底部边距:"))
        self.subtitle_bottom_margin_spin = QSpinBox()
        self.subtitle_bottom_margin_spin.setRange(0, 300)
        self.subtitle_bottom_margin_spin.setValue(36)
        self.subtitle_bottom_margin_spin.setSuffix(" px")
        subtitle_style_layout.addWidget(self.subtitle_bottom_margin_spin)
        subtitle_style_layout.addStretch()
        layout.addLayout(subtitle_style_layout)

        subtitle_buttons_layout = QHBoxLayout()
        self.add_subtitle_button = QPushButton("加入字幕")
        self.add_subtitle_button.clicked.connect(self.add_expert_subtitle)
        subtitle_buttons_layout.addWidget(self.add_subtitle_button)

        self.import_srt_button = QPushButton("导入 SRT")
        self.import_srt_button.clicked.connect(self.import_expert_srt)
        subtitle_buttons_layout.addWidget(self.import_srt_button)
        subtitle_buttons_layout.addStretch()
        layout.addLayout(subtitle_buttons_layout)

        self.subtitle_entries_list = QListWidget()
        self.subtitle_entries_list.setMaximumHeight(120)
        self.subtitle_entries_list.setAlternatingRowColors(True)
        self.subtitle_entries_list.itemSelectionChanged.connect(self.update_subtitle_buttons)
        layout.addWidget(self.subtitle_entries_list)

        subtitle_list_buttons_layout = QHBoxLayout()
        subtitle_list_buttons_layout.addStretch()
        self.remove_subtitle_button = QPushButton("删除选中字幕")
        self.remove_subtitle_button.clicked.connect(self.remove_selected_subtitle)
        subtitle_list_buttons_layout.addWidget(self.remove_subtitle_button)

        self.clear_subtitles_button = QPushButton("清空字幕")
        self.clear_subtitles_button.clicked.connect(self.clear_subtitles)
        subtitle_list_buttons_layout.addWidget(self.clear_subtitles_button)
        layout.addLayout(subtitle_list_buttons_layout)

        self.refresh_expert_mark_labels()
        self.refresh_expert_delete_ranges_list()
        self.refresh_subtitle_entries_list()
        self.update_expert_controls_state()
        return tab

    def copy_developer_wechat(self):
        QApplication.clipboard().setText(DEVELOPER_WECHAT)
        message = f"已复制微信号: {DEVELOPER_WECHAT}"
        self.status_label.setText(message)
        QToolTip.showText(
            self.developer_button.mapToGlobal(self.developer_button.rect().bottomLeft()),
            message,
            self.developer_button,
        )

    def on_mode_changed(self, _index):
        self.update_start_button_state()
        self.update_expert_controls_state()

    def is_expert_mode_active(self):
        return self.mode_tabs.currentWidget() == self.expert_tab

    def media_playing_state(self):
        if QMediaPlayer is None:
            return None
        if hasattr(QMediaPlayer, "PlayingState"):
            return QMediaPlayer.PlayingState
        return QMediaPlayer.PlaybackState.PlayingState

    def update_expert_media_source(self, path):
        self.expert_delete_ranges = []
        self.expert_in_point = None
        self.expert_out_point = None
        self.subtitle_entries = []
        self.expert_duration_seconds = 0

        self.refresh_expert_mark_labels()
        self.refresh_expert_delete_ranges_list()
        self.refresh_subtitle_entries_list()
        self.on_expert_duration_changed(0)
        self.on_expert_position_changed(0)

        if self.media_player is not None:
            self.media_player.stop()
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))

        self.update_expert_controls_state()

    def toggle_expert_playback(self):
        if self.media_player is None or self.current_file is None:
            return

        if self.media_player.playbackState() == self.media_playing_state():
            self.media_player.pause()
        else:
            self.media_player.play()

    def on_expert_duration_changed(self, milliseconds):
        self.expert_duration_seconds = max(0, milliseconds / 1000)
        self.expert_duration_label.setText(format_time(self.expert_duration_seconds))

        self._syncing_expert_position = True
        self.expert_position_slider.setRange(0, max(0, int(milliseconds)))
        self.expert_position_spin.setRange(0, max(0, self.expert_duration_seconds))
        self._syncing_expert_position = False
        self.update_expert_controls_state()

    def on_expert_position_changed(self, milliseconds):
        seconds = max(0, milliseconds / 1000)
        self.expert_current_label.setText(format_time(seconds))

        self._syncing_expert_position = True
        if not self.expert_position_slider.isSliderDown():
            self.expert_position_slider.setValue(max(0, int(milliseconds)))
        self.expert_position_spin.setValue(seconds)
        self._syncing_expert_position = False

    def on_expert_playback_state_changed(self, state):
        if state == self.media_playing_state():
            self.expert_play_button.setText("暂停")
        else:
            self.expert_play_button.setText("播放")

    def on_expert_media_error(self, *_args):
        if self.current_file is None or self.media_player is None:
            return

        error_text = self.media_player.errorString()
        if error_text:
            self.status_label.setText(f"达人模式预览失败: {error_text}")

    def seek_expert_slider(self, milliseconds):
        if self._syncing_expert_position or self.media_player is None:
            return

        self.media_player.setPosition(max(0, int(milliseconds)))

    def seek_expert_seconds(self, seconds):
        if self._syncing_expert_position or self.media_player is None:
            return

        self.media_player.setPosition(max(0, int(seconds * 1000)))

    def current_expert_seconds(self):
        if self.media_player is not None:
            return max(0, self.media_player.position() / 1000)
        return self.expert_position_spin.value()

    def set_expert_in_point(self):
        self.expert_in_point = self.current_expert_seconds()
        self.refresh_expert_mark_labels()
        self.update_expert_controls_state()

    def set_expert_out_point(self):
        self.expert_out_point = self.current_expert_seconds()
        self.refresh_expert_mark_labels()
        self.update_expert_controls_state()

    def refresh_expert_mark_labels(self):
        if self.expert_in_point is None:
            self.expert_in_label.setText("入点: 未设置")
        else:
            self.expert_in_label.setText(f"入点: {format_time(self.expert_in_point)}")

        if self.expert_out_point is None:
            self.expert_out_label.setText("出点: 未设置")
        else:
            self.expert_out_label.setText(f"出点: {format_time(self.expert_out_point)}")

    def add_expert_delete_range(self):
        try:
            before_count = len(self.expert_delete_ranges)
            ranges = add_delete_range_from_marks(
                self.expert_delete_ranges,
                self.expert_in_point,
                self.expert_out_point,
                total_duration=self.expert_duration_seconds or None,
            )
        except PlanValidationError as exc:
            QMessageBox.warning(self, "删除区间无效", str(exc))
            return

        self.expert_delete_ranges = [item.as_tuple() for item in ranges]
        self.refresh_expert_delete_ranges_list()

        if len(self.expert_delete_ranges) < before_count + 1:
            self.status_label.setText("达人模式已合并重叠或相邻的删除区间")
        else:
            self.status_label.setText(f"达人模式已添加删除区间，共 {len(self.expert_delete_ranges)} 个")

    def remove_selected_expert_delete_range(self):
        row = self.expert_delete_ranges_list.currentRow()
        if row < 0 or row >= len(self.expert_delete_ranges):
            return

        del self.expert_delete_ranges[row]
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText(f"达人模式已删除选中区间，剩余 {len(self.expert_delete_ranges)} 个")

    def clear_expert_delete_ranges(self):
        if not self.expert_delete_ranges:
            return

        self.expert_delete_ranges = []
        self.refresh_expert_delete_ranges_list()
        self.status_label.setText("达人模式已清空删除区间")

    def add_expert_subtitle(self):
        try:
            entries = add_subtitle_from_marks(
                self.subtitle_entries,
                self.expert_in_point,
                self.expert_out_point,
                self.subtitle_text_input.text(),
                total_duration=self.expert_duration_seconds or None,
            )
        except SubtitleValidationError as exc:
            QMessageBox.warning(self, "字幕无效", str(exc))
            return

        self.subtitle_entries = list(entries)
        self.subtitle_text_input.clear()
        self.refresh_subtitle_entries_list()
        self.status_label.setText(f"已添加字幕，共 {len(self.subtitle_entries)} 条")

    def import_expert_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 SRT 字幕",
            "",
            "SRT 字幕 (*.srt);;所有文件 (*.*)",
        )
        if not file_path:
            return

        try:
            self.subtitle_entries = list(read_srt_file(file_path))
        except (OSError, SubtitleValidationError) as exc:
            QMessageBox.warning(self, "字幕导入失败", str(exc))
            return

        self.refresh_subtitle_entries_list()
        self.status_label.setText(f"已导入字幕 {len(self.subtitle_entries)} 条")

    def remove_selected_subtitle(self):
        row = self.subtitle_entries_list.currentRow()
        if row < 0 or row >= len(self.subtitle_entries):
            return

        del self.subtitle_entries[row]
        self.refresh_subtitle_entries_list()
        self.status_label.setText(f"已删除选中字幕，剩余 {len(self.subtitle_entries)} 条")

    def clear_subtitles(self):
        if not self.subtitle_entries:
            return

        self.subtitle_entries = []
        self.refresh_subtitle_entries_list()
        self.status_label.setText("已清空字幕")

    def refresh_subtitle_entries_list(self):
        if not hasattr(self, "subtitle_entries_list"):
            return

        self.subtitle_entries_list.clear()
        for index, entry in enumerate(self.subtitle_entries, start=1):
            preview = entry.text.replace("\n", " / ")
            self.subtitle_entries_list.addItem(
                f"{index}. {format_time(entry.start)} - {format_time(entry.end)}  {preview}"
            )

        self.update_subtitle_buttons()

    def update_subtitle_buttons(self):
        if not hasattr(self, "remove_subtitle_button"):
            return

        enabled = self.current_file is not None and self.process_thread is None
        has_entries = bool(self.subtitle_entries)
        has_selection = self.subtitle_entries_list.currentRow() >= 0
        self.remove_subtitle_button.setEnabled(enabled and has_selection)
        self.clear_subtitles_button.setEnabled(enabled and has_entries)

    def refresh_expert_delete_ranges_list(self):
        self.expert_delete_ranges_list.clear()
        for index, (start, end) in enumerate(self.expert_delete_ranges, start=1):
            self.expert_delete_ranges_list.addItem(f"{index}. {format_time(start)} - {format_time(end)}")

        self.update_expert_delete_range_buttons()

    def update_expert_delete_range_buttons(self):
        enabled = self.current_file is not None and self.process_thread is None
        has_ranges = bool(self.expert_delete_ranges)
        has_selection = self.expert_delete_ranges_list.currentRow() >= 0
        self.remove_expert_range_button.setEnabled(enabled and has_selection)
        self.clear_expert_ranges_button.setEnabled(enabled and has_ranges)

    def update_expert_controls_state(self):
        if not hasattr(self, "expert_play_button"):
            return

        processing = self.process_thread is not None
        can_preview = self.current_file is not None and self.media_player is not None and not processing
        self.expert_play_button.setEnabled(can_preview)
        self.expert_position_slider.setEnabled(can_preview)
        self.expert_position_spin.setEnabled(can_preview)
        self.set_in_point_button.setEnabled(can_preview)
        self.set_out_point_button.setEnabled(can_preview)
        self.add_expert_range_button.setEnabled(
            can_preview and self.expert_in_point is not None and self.expert_out_point is not None
        )
        self.expert_delete_ranges_list.setEnabled(self.current_file is not None and not processing)
        self.update_expert_delete_range_buttons()

        if hasattr(self, "add_subtitle_button"):
            can_edit_subtitles = self.current_file is not None and not processing
            self.subtitle_text_input.setEnabled(can_edit_subtitles)
            self.subtitle_font_size_spin.setEnabled(can_edit_subtitles)
            self.subtitle_bottom_margin_spin.setEnabled(can_edit_subtitles)
            self.import_srt_button.setEnabled(can_edit_subtitles)
            self.add_subtitle_button.setEnabled(
                can_preview
                and self.expert_in_point is not None
                and self.expert_out_point is not None
                and bool(self.subtitle_text_input.text().strip())
            )
            self.subtitle_entries_list.setEnabled(can_edit_subtitles)
            self.update_subtitle_buttons()

    def build_expert_edit_plan_from_controls(self):
        try:
            subtitles = SubtitleTrack(
                entries=tuple(self.subtitle_entries),
                style=SubtitleStyle(
                    font_size=self.subtitle_font_size_spin.value(),
                    bottom_margin=self.subtitle_bottom_margin_spin.value(),
                ),
            )
            return build_expert_edit_plan(self.expert_delete_ranges, subtitles=subtitles)
        except (PlanValidationError, SubtitleValidationError) as exc:
            QMessageBox.warning(self, "编辑参数无效", str(exc))
            return None

    def update_delete_range_controls(self, enabled):
        processing = self.process_thread is not None
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

        before_count = len(self.delete_ranges)
        self.delete_ranges = normalize_delete_ranges(self.delete_ranges + [(start, end)])
        self.refresh_delete_ranges_list()

        if len(self.delete_ranges) < before_count + 1:
            self.status_label.setText("已合并重叠或相邻的删除区间")
        else:
            self.status_label.setText(f"已添加删除区间，共 {len(self.delete_ranges)} 个")

    def remove_selected_delete_range(self):
        row = self.delete_ranges_list.currentRow()
        if row < 0 or row >= len(self.delete_ranges):
            return

        del self.delete_ranges[row]
        self.refresh_delete_ranges_list()
        self.status_label.setText(f"已删除选中区间，剩余 {len(self.delete_ranges)} 个")

    def clear_delete_ranges(self):
        if not self.delete_ranges:
            return

        self.delete_ranges = []
        self.refresh_delete_ranges_list()
        self.status_label.setText("已清空删除区间")

    def refresh_delete_ranges_list(self):
        self.delete_ranges_list.clear()
        for index, (start, end) in enumerate(self.delete_ranges, start=1):
            self.delete_ranges_list.addItem(f"{index}. {format_time(start)} - {format_time(end)}")

        self.update_delete_range_controls(self.delete_range_check.isChecked())

    def update_delete_range_buttons(self):
        enabled = self.delete_range_check.isChecked() and self.process_thread is None
        has_ranges = bool(self.delete_ranges)
        has_selection = self.delete_ranges_list.currentRow() >= 0
        self.remove_delete_range_button.setEnabled(enabled and has_selection)
        self.clear_delete_ranges_button.setEnabled(enabled and has_ranges)

    def get_delete_ranges_for_processing(self):
        if self.delete_ranges:
            ranges = self.delete_ranges
        else:
            delete_start = self.delete_start_spin.value()
            delete_end = self.delete_end_spin.value()
            if delete_end <= delete_start:
                QMessageBox.warning(self, "删除区间无效", "结束秒数必须大于开始秒数。")
                return None
            ranges = [(delete_start, delete_end)]

        normalized_ranges = normalize_delete_ranges(ranges)
        if not normalized_ranges:
            QMessageBox.warning(self, "删除区间无效", "请至少添加一个有效的删除区间。")
            return None

        if normalized_ranges != self.delete_ranges:
            self.delete_ranges = normalized_ranges
            self.refresh_delete_ranges_list()

        return normalized_ranges

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

    def check_ffmpeg(self):
        self.ffmpeg_path = find_ffmpeg()
        self.ffprobe_path = find_ffprobe(self.ffmpeg_path)

        if self.ffmpeg_path:
            self.status_label.setText("准备就绪")
        else:
            self.ffprobe_path = None
            self.status_label.setText("未检测到 FFmpeg，暂时无法开始处理")

        self.update_start_button_state()

    def on_file_dropped(self, file_path):
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "文件无效", "选择的文件不存在，或不是普通文件。")
            return

        if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            QMessageBox.warning(self, "格式不支持", "请选择常见视频文件，例如 MP4、AVI、MKV、MOV。")
            return

        self.current_file = path
        file_size = format_file_size(path.stat().st_size)
        self.file_label.setText(f"已选择文件:\n{path}\n大小: {file_size}")
        self.status_label.setText("文件已加载，可以开始处理")
        self.start_thumbnail_generation(path)
        self.update_expert_media_source(path)
        self.update_start_button_state()

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
        thumbnail = Path(thumbnail_path)
        if self.current_file is None or str(Path(video_path)) != str(self.current_file):
            self.remove_temp_file(thumbnail)
            return

        pixmap = QPixmap(str(thumbnail))
        self.remove_temp_file(thumbnail)

        if pixmap.isNull():
            self.drop_area.clear_thumbnail("预览生成失败\n点击或拖入其他视频可重新选择")
            self.status_label.setText("文件已加载，预览生成失败，仍可处理")
            return

        self.drop_area.set_thumbnail(pixmap)
        if self.process_thread is None:
            self.status_label.setText("文件已加载，可以开始处理")

    def on_thumbnail_failed(self, video_path, message):
        if self.current_file is None or str(Path(video_path)) != str(self.current_file):
            return

        self.drop_area.clear_thumbnail("预览生成失败\n点击或拖入其他视频可重新选择")
        if self.process_thread is None:
            self.status_label.setText(f"{message}，仍可处理")

    def cleanup_thumbnail_thread(self, thread):
        if thread in self.thumbnail_threads:
            self.thumbnail_threads.remove(thread)
        thread.deleteLater()

    @staticmethod
    def remove_temp_file(path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def update_start_button_state(self):
        can_start = self.current_file is not None and self.ffmpeg_path is not None and self.process_thread is None
        if can_start and self.is_expert_mode_active() and self.media_player is None:
            can_start = False
        self.start_button.setEnabled(can_start)

    def start_processing(self):
        if self.current_file is None:
            QMessageBox.information(self, "请选择文件", "请先选择一个视频文件。")
            return

        if self.ffmpeg_path is None:
            QMessageBox.warning(
                self,
                "未找到 FFmpeg",
                "请先将 ffmpeg.exe 和 ffprobe.exe 放到软件目录，或安装到系统 PATH。",
            )
            return

        output_path = self.build_output_path(self.current_file)
        if self.is_expert_mode_active():
            edit_plan = self.build_expert_edit_plan_from_controls()
        else:
            edit_plan = self.build_edit_plan_from_controls()
        if edit_plan is None:
            return

        self.process_thread = VideoProcessThread(
            self.ffmpeg_path,
            str(self.current_file),
            str(output_path),
            edit_plan,
            self,
        )
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

    def on_process_success(self, output_path):
        self.cleanup_thread()
        self.set_processing_state(False)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"处理完成: {output_path}")
        self.open_output_directory(output_path)
        QMessageBox.information(self, "处理成功", f"视频已导出:\n{output_path}\n\n已自动打开所在目录。")

    def open_output_directory(self, output_path):
        output_dir = Path(output_path).resolve().parent
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir)))
        if not opened:
            self.status_label.setText(f"处理完成，但无法自动打开目录: {output_dir}")

    def on_process_error(self, message):
        self.cleanup_thread()
        self.set_processing_state(False)
        if message == "处理已取消":
            self.progress_bar.setValue(0)
            self.status_label.setText(message)
            return

        self.status_label.setText(message)
        QMessageBox.critical(self, "处理失败", message)

    def cleanup_thread(self):
        if self.process_thread is None:
            return

        self.process_thread.wait(3000)
        self.process_thread.deleteLater()
        self.process_thread = None
        self.update_start_button_state()

    def set_processing_state(self, processing):
        if processing and self.media_player is not None:
            self.media_player.pause()

        self.drop_area.setEnabled(not processing)
        self.skip_spin.setEnabled(not processing)
        self.delete_range_check.setEnabled(not processing)
        self.update_delete_range_controls(self.delete_range_check.isChecked())
        self.res_combo.setEnabled(not processing)
        self.update_expert_controls_state()
        self.cancel_button.setEnabled(processing)
        if processing:
            self.start_button.setEnabled(False)
        else:
            self.update_start_button_state()

    def build_output_path(self, input_path):
        target_dir = input_path.parent
        base_name = f"{input_path.stem}_clipped"
        candidate = target_dir / f"{base_name}.mp4"
        index = 1

        while candidate.exists():
            candidate = target_dir / f"{base_name}_{index}.mp4"
            index += 1

        return candidate

    def closeEvent(self, event):
        if self.process_thread is not None:
            self.process_thread.stop()
            self.process_thread.wait(3000)

        for thread in list(self.thumbnail_threads):
            thread.stop()
            thread.wait(3000)

        if self.media_player is not None:
            self.media_player.stop()

        event.accept()


class VideoClipperApp:
    """应用入口包装"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("VideoClipper")
        self.window = MainWindow()

    def run(self):
        self.window.show()
        return self.app.exec()
