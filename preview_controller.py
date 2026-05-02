"""
覆盖素材预览控制。
依赖 Qt 多媒体，只管理预览项和内部播放器，不处理编辑模型校验。
"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
    MULTIMEDIA_AVAILABLE = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    QGraphicsVideoItem = None
    MULTIMEDIA_AVAILABLE = False


class OverlayPreviewController:
    def __init__(self, scene=None, parent=None):
        self.scene = scene
        self.parent = parent
        self._records = []

    def set_scene(self, scene):
        self.scene = scene

    def clear(self):
        for record in self._records:
            player = record.get("player")
            if player is not None:
                player.stop()
                player.deleteLater()
            audio_output = record.get("audio_output")
            if audio_output is not None:
                audio_output.deleteLater()
            item = record.get("item")
            if item is not None:
                scene = self.scene or item.scene()
                if scene is not None:
                    scene.removeItem(item)
        self._records = []

    def refresh(self, clips, current_seconds=0, base_player=None):
        if self.scene is None:
            return

        self.clear()
        for clip in clips or ():
            if clip.media_kind == "image":
                self._add_image_clip(clip)
            elif MULTIMEDIA_AVAILABLE:
                self._add_video_clip(clip)

        self.sync_geometry()
        self.sync_at(current_seconds, base_player=base_player)

    def update_clips(self, clips, current_seconds=0, base_player=None):
        clips = list(clips or ())
        if len(self._records) != len(clips):
            self.refresh(clips, current_seconds=current_seconds, base_player=base_player)
            return

        for record, clip in zip(self._records, clips):
            record["clip"] = clip
        self.sync_at(current_seconds, base_player=base_player)

    def sync_geometry(self):
        if self.scene is None:
            return

        scene_rect = self.scene.sceneRect()
        width = max(1, int(scene_rect.width()))
        height = max(1, int(scene_rect.height()))
        for record in self._records:
            item = record.get("item")
            if item is None:
                continue
            item.setPos(0, 0)
            pixmap = record.get("pixmap")
            if pixmap is not None and not pixmap.isNull() and isinstance(item, QGraphicsPixmapItem):
                item.setPixmap(pixmap.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            elif QGraphicsVideoItem is not None and isinstance(item, QGraphicsVideoItem):
                item.setSize(scene_rect.size())

    def sync_at(self, seconds, base_player=None):
        base_playing = (
            base_player is not None
            and QMediaPlayer is not None
            and base_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        for record in self._records:
            clip = record.get("clip")
            item = record.get("item")
            if clip is None or item is None:
                continue
            visible = clip.start <= seconds < clip.end
            if item.isVisible() != visible:
                item.setVisible(visible)
            player = record.get("player")
            if player is None:
                continue
            if not visible:
                if player.playbackState() != QMediaPlayer.PlaybackState.PausedState:
                    player.pause()
                continue
            target_ms = max(0, int((clip.source_start + seconds - clip.start) * 1000))
            if abs(player.position() - target_ms) > 150:
                player.setPosition(target_ms)
            if base_playing:
                if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                    player.play()
            elif player.playbackState() != QMediaPlayer.PlaybackState.PausedState:
                player.pause()

    def _add_image_clip(self, clip):
        pixmap = QPixmap(clip.path)
        if pixmap.isNull():
            return
        item = QGraphicsPixmapItem()
        item.setZValue(1)
        item.setVisible(False)
        self.scene.addItem(item)
        self._records.append({
            "clip": clip,
            "item": item,
            "pixmap": pixmap,
            "player": None,
            "audio_output": None,
        })

    def _add_video_clip(self, clip):
        item = QGraphicsVideoItem()
        item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        item.setZValue(1)
        item.setVisible(False)
        player = QMediaPlayer(self.parent)
        audio_output = QAudioOutput(self.parent)
        audio_output.setMuted(True)
        player.setAudioOutput(audio_output)
        player.setVideoOutput(item)
        player.setSource(QUrl.fromLocalFile(clip.path))
        self.scene.addItem(item)
        self._records.append({
            "clip": clip,
            "item": item,
            "pixmap": QPixmap(),
            "player": player,
            "audio_output": audio_output,
        })
