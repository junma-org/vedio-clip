import tempfile
import unittest
from pathlib import Path

from whisper_utils import ensure_whisper_model, segments_to_subtitle_project


class WhisperUtilsTest(unittest.TestCase):
    def test_segments_to_subtitle_project_builds_bilingual_cues(self):
        project = segments_to_subtitle_project(
            [{"start": 1, "end": 2.5, "text": "你好"}],
            translated_segments=[{"start": 1.1, "end": 2.4, "text": "Hello"}],
            source_language="zh",
            bilingual=True,
            video_size=(1080, 1920),
        )

        self.assertEqual(len(project.cues), 1)
        self.assertEqual(project.cues[0].text, "你好\nHello")
        self.assertEqual((project.play_res_x, project.play_res_y), (1080, 1920))

    def test_segments_to_subtitle_project_skips_translation_for_english_source(self):
        project = segments_to_subtitle_project(
            [{"start": 1, "end": 2, "text": "Hello"}],
            translated_segments=[{"start": 1, "end": 2, "text": "Hello"}],
            source_language="en",
            bilingual=True,
        )

        self.assertEqual(project.cues[0].text, "Hello")

    def test_ensure_whisper_model_uses_existing_model_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            (model_dir / "model.bin").write_bytes(b"model")
            (model_dir / "config.json").write_text("{}", encoding="utf-8")

            result = ensure_whisper_model(model_dir=model_dir, download_fn=lambda *_args, **_kwargs: None)

        self.assertEqual(result, model_dir)

    def test_ensure_whisper_model_accepts_downloaded_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)

            def fake_download(_name, output_dir):
                target = Path(output_dir)
                (target / "model.bin").write_bytes(b"model")
                (target / "config.json").write_text("{}", encoding="utf-8")
                return str(target)

            result = ensure_whisper_model(model_dir=model_dir, download_fn=fake_download)

        self.assertEqual(result, model_dir)


if __name__ == "__main__":
    unittest.main()
