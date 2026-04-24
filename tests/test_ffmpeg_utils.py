import unittest
from pathlib import Path

from ffmpeg_utils import (
    build_ffmpeg_command,
    build_ffmpeg_command_from_plan,
    build_ffmpeg_progress_command,
    calculate_output_duration,
    decode_process_output,
    normalize_delete_ranges,
    prepare_subtitle_file_for_plan,
    _parse_progress_time_seconds,
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

    def test_build_command_burns_ass_subtitles_from_plan(self):
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
            subtitle_path="/tmp/subtitle.ass",
        )

        filter_text = cmd[cmd.index("-vf") + 1]
        self.assertIn("subtitles=filename=", filter_text)
        self.assertIn("subtitle.ass", filter_text)
        self.assertIn("select='gte(t,5)'", filter_text)
        self.assertNotIn("-ss", cmd)

    def test_prepare_subtitle_file_for_plan_writes_ass(self):
        plan = EditPlan(subtitles=SubtitleTrack(entries=(SubtitleEntry(1, 2, "字幕"),)))

        subtitle_path = prepare_subtitle_file_for_plan(plan)
        try:
            self.assertIsNotNone(subtitle_path)
            self.assertTrue(subtitle_path.endswith(".ass"))
            content = Path(subtitle_path).read_text(encoding="utf-8")
            self.assertIn("[Events]", content)
            self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00", content)
            self.assertIn("字幕", content)
        finally:
            if subtitle_path:
                Path(subtitle_path).unlink(missing_ok=True)

    def test_build_progress_command_adds_machine_progress_flags(self):
        cmd = build_ffmpeg_progress_command(["ffmpeg", "-y", "-i", "input.mp4", "output.mp4"])

        self.assertEqual(
            cmd[:7],
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"],
        )

    def test_parse_progress_time_seconds_handles_ffmpeg_microseconds(self):
        self.assertEqual(_parse_progress_time_seconds("out_time_ms", "1000000"), 1)
        self.assertEqual(_parse_progress_time_seconds("out_time_us", "2500000"), 2.5)
        self.assertEqual(_parse_progress_time_seconds("out_time", "00:01:02.500000"), 62.5)

    def test_decode_process_output_replaces_invalid_bytes(self):
        text = decode_process_output(b"abc\x88def")
        self.assertIn("abc", text)
        self.assertTrue(len(text) >= 3)


if __name__ == "__main__":
    unittest.main()
