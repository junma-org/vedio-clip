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
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_utils import (
    build_ffmpeg_command,
    build_thumbnail_command,
    find_ffmpeg,
    find_ffprobe,
    format_file_size,
    format_time,
    get_video_info,
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

    def __init__(self, ffmpeg_path, input_path, output_path, skip_seconds, resolution, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.skip_seconds = skip_seconds
        self.resolution = resolution
        self.is_running = True
        self._process = None

    def run(self):
        try:
            self.status_changed.emit("正在分析视频...")
            video_info = get_video_info(self.ffmpeg_path, self.input_path)
            total_duration = video_info.get("duration", 0)

            if total_duration <= 0:
                self.finished_error.emit("无法获取视频时长，请确认 ffprobe.exe 可用。")
                return

            remaining_duration = max(0, total_duration - self.skip_seconds)
            if remaining_duration <= 0:
                self.finished_error.emit("跳过时间超过视频总时长")
                return

            cmd = build_ffmpeg_command(
                self.ffmpeg_path,
                self.input_path,
                self.output_path,
                skip_seconds=self.skip_seconds,
                resolution=self.resolution,
            )

            self.status_changed.emit(
                f"正在处理: 跳过前 {format_time(self.skip_seconds)}，"
                f"输出时长约 {format_time(remaining_duration)}"
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
            self._process = None

    def stop(self):
        self.is_running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()


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

        layout.addWidget(settings_box)

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

    def copy_developer_wechat(self):
        QApplication.clipboard().setText(DEVELOPER_WECHAT)
        message = f"已复制微信号: {DEVELOPER_WECHAT}"
        self.status_label.setText(message)
        QToolTip.showText(
            self.developer_button.mapToGlobal(self.developer_button.rect().bottomLeft()),
            message,
            self.developer_button,
        )

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
        resolution = self.res_combo.currentData()
        skip_seconds = self.skip_spin.value()

        self.process_thread = VideoProcessThread(
            self.ffmpeg_path,
            str(self.current_file),
            str(output_path),
            skip_seconds,
            resolution,
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
        self.drop_area.setEnabled(not processing)
        self.skip_spin.setEnabled(not processing)
        self.res_combo.setEnabled(not processing)
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
