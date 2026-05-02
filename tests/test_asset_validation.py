import tempfile
import unittest
from pathlib import Path

from asset_validation import (
    AssetValidationError,
    detect_media_kind,
    validate_audio_file,
    validate_image_file,
    validate_video_file,
)


class AssetValidationTest(unittest.TestCase):
    def test_detect_media_kind_from_supported_suffix(self):
        self.assertEqual(detect_media_kind("clip.MP4"), "video")
        self.assertEqual(detect_media_kind("sound.WAV"), "audio")
        self.assertEqual(detect_media_kind("cover.PNG"), "image")
        self.assertIsNone(detect_media_kind("notes.txt"))

    def test_validate_video_file_returns_normalized_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clip.MP4"
            path.write_bytes(b"video")

            asset = validate_video_file(path)

            self.assertEqual(asset.path, path)
            self.assertEqual(asset.media_kind, "video")

    def test_validate_audio_file_rejects_missing_file_with_gui_message(self):
        with self.assertRaises(AssetValidationError) as context:
            validate_audio_file("missing.mp3")

        self.assertEqual(context.exception.title, "文件无效")
        self.assertEqual(context.exception.message, "选择的音频文件不存在。")

    def test_validate_image_file_rejects_unsupported_suffix_with_gui_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cover.txt"
            path.write_text("not image")

            with self.assertRaises(AssetValidationError) as context:
                validate_image_file(path)

        self.assertEqual(context.exception.title, "格式不支持")
        self.assertEqual(context.exception.message, "请选择常见图片文件，例如 PNG、JPG、WEBP。")

    def test_validate_video_file_can_use_overlay_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "overlay.txt"
            path.write_text("not video")

            with self.assertRaises(AssetValidationError) as context:
                validate_video_file(
                    path,
                    missing_message="选择的视频文件不存在。",
                    unsupported_message="请选择常见视频文件，例如 MP4、MOV、MKV。",
                )

        self.assertEqual(context.exception.title, "格式不支持")
        self.assertEqual(context.exception.message, "请选择常见视频文件，例如 MP4、MOV、MKV。")


if __name__ == "__main__":
    unittest.main()
