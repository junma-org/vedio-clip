import unittest
from pathlib import Path

from ffmpeg_utils import (
    build_ffmpeg_command,
    build_ffmpeg_command_from_plan,
    calculate_output_duration,
    normalize_delete_ranges,
    prepare_subtitle_file_for_plan,
)
from edit_model import DeleteRange, EditPlan, OutputOptions
from subtitle_model import SubtitleEntry, SubtitleStyle, SubtitleTrack


class FfmpegUtilsTest(unittest.TestCase):
    def test_calculate_output_duration_merges_overlapping_ranges(self):
        duration = calculate_output_duration(
            120,
            skip_seconds=30,
            delete_ranges=[(20, 40), (80, 100)],
        )

        self.assertEqual(duration, 60)

    def test_calculate_output_duration_clips_ranges_to_video_duration(self):
        duration = calculate_output_duration(
            90,
            skip_seconds=0,
            delete_ranges=[(80, 120)],
        )

        self.assertEqual(duration, 80)

    def test_normalize_delete_ranges_merges_overlapping_and_adjacent_ranges(self):
        ranges = normalize_delete_ranges(
            [
                (80, 100),
                (10, 20),
                (18, 30),
                (30, 40),
                (100, 100),
            ]
        )

        self.assertEqual(ranges, [(10.0, 40.0), (80.0, 100.0)])

    def test_build_command_uses_filters_for_delete_ranges(self):
        cmd = build_ffmpeg_command(
            "ffmpeg",
            "input.mp4",
            "output.mp4",
            skip_seconds=30,
            delete_ranges=[(80, 100)],
            has_audio=True,
        )

        self.assertIn("-vf", cmd)
        self.assertIn("select='gte(t,30)*not(between(t,80,100))',setpts=N/FRAME_RATE/TB", cmd)
        self.assertIn("-af", cmd)
        self.assertIn("aselect='gte(t,30)*not(between(t,80,100))',asetpts=N/SR/TB", cmd)
        self.assertNotIn("-ss", cmd)

    def test_build_command_uses_filters_for_multiple_delete_ranges(self):
        cmd = build_ffmpeg_command(
            "ffmpeg",
            "input.mp4",
            "output.mp4",
            skip_seconds=0,
            delete_ranges=[(80, 100), (10, 20), (18, 30)],
            has_audio=True,
        )

        self.assertIn(
            "select='not(between(t,10,30))*not(between(t,80,100))',setpts=N/FRAME_RATE/TB",
            cmd,
        )
        self.assertIn(
            "aselect='not(between(t,10,30))*not(between(t,80,100))',asetpts=N/SR/TB",
            cmd,
        )

    def test_build_command_from_plan_uses_plan_options(self):
        plan = EditPlan(
            skip_seconds=5,
            delete_ranges=(DeleteRange(10, 20),),
            output=OutputOptions(resolution=(1280, 720), audio_bitrate="96k"),
            has_audio=False,
        )

        cmd = build_ffmpeg_command_from_plan("ffmpeg", "input.mp4", "output.mp4", plan)

        self.assertIn(
            "select='gte(t,5)*not(between(t,10,20))',setpts=N/FRAME_RATE/TB,scale=1280:720",
            cmd,
        )
        self.assertIn("-an", cmd)
        self.assertNotIn("-af", cmd)

    def test_build_command_keeps_existing_skip_behavior_without_delete_ranges(self):
        cmd = build_ffmpeg_command(
            "ffmpeg",
            "input.mp4",
            "output.mp4",
            skip_seconds=30,
            has_audio=True,
        )

        self.assertIn("-ss", cmd)
        self.assertNotIn("-vf", cmd)

    def test_build_command_burns_subtitles_from_plan(self):
        plan = EditPlan(
            skip_seconds=5,
            subtitles=SubtitleTrack(
                entries=(SubtitleEntry(10, 12, "字幕"),),
                style=SubtitleStyle(font_size=32, bottom_margin=48),
            ),
        )

        cmd = build_ffmpeg_command_from_plan(
            "ffmpeg",
            "input.mp4",
            "output.mp4",
            plan,
            subtitle_path="/tmp/subtitle.srt",
        )

        filter_text = cmd[cmd.index("-vf") + 1]
        self.assertIn("subtitles=filename=", filter_text)
        self.assertIn("FontSize=32", filter_text)
        self.assertIn("MarginV=48", filter_text)
        self.assertIn("select='gte(t,5)'", filter_text)
        self.assertNotIn("-ss", cmd)

    def test_prepare_subtitle_file_for_plan_writes_srt(self):
        plan = EditPlan(
            subtitles=SubtitleTrack(entries=(SubtitleEntry(1, 2, "字幕"),))
        )

        subtitle_path = prepare_subtitle_file_for_plan(plan)
        try:
            self.assertIsNotNone(subtitle_path)
            content = Path(subtitle_path).read_text(encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:02,000", content)
            self.assertIn("字幕", content)
        finally:
            if subtitle_path:
                Path(subtitle_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
