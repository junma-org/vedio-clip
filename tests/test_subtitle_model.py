import unittest

from subtitle_model import (
    SubtitleEntry,
    SubtitleStyle,
    SubtitleTrack,
    SubtitleValidationError,
    add_subtitle_from_marks,
    format_srt_timestamp,
    parse_srt_text,
    serialize_srt_entries,
)


class SubtitleModelTest(unittest.TestCase):
    def test_parse_srt_text_reads_entries(self):
        entries = parse_srt_text(
            """1
00:00:01,500 --> 00:00:03,000
第一句

2
00:00:04.000 --> 00:00:05.250
第二句
第二行
"""
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].as_tuple(), (1.5, 3.0, "第一句"))
        self.assertEqual(entries[1].as_tuple(), (4.0, 5.25, "第二句\n第二行"))

    def test_serialize_srt_entries_writes_standard_timestamps(self):
        text = serialize_srt_entries(
            [
                SubtitleEntry(1.5, 3, "第一句"),
                SubtitleEntry(4, 5.25, "第二句"),
            ]
        )

        self.assertIn("00:00:01,500 --> 00:00:03,000", text)
        self.assertIn("00:00:04,000 --> 00:00:05,250", text)

    def test_add_subtitle_from_marks_validates_text_and_range(self):
        entries = add_subtitle_from_marks([], 1, 3, "字幕", total_duration=10)

        self.assertEqual(entries[0].as_tuple(), (1.0, 3.0, "字幕"))

        with self.assertRaises(SubtitleValidationError):
            add_subtitle_from_marks(entries, 4, 3, "无效")

        with self.assertRaises(SubtitleValidationError):
            add_subtitle_from_marks(entries, 4, 5, "")

    def test_subtitle_track_sorts_entries_and_normalizes_style(self):
        track = SubtitleTrack(
            entries=(SubtitleEntry(10, 12, "后"), SubtitleEntry(1, 2, "前")),
            style=SubtitleStyle(font_size="30", bottom_margin="40"),
        ).normalized()

        self.assertEqual([entry.text for entry in track.entries], ["前", "后"])
        self.assertEqual(track.style.font_size, 30)
        self.assertEqual(track.style.bottom_margin, 40)

    def test_format_srt_timestamp_rounds_milliseconds(self):
        self.assertEqual(format_srt_timestamp(3661.2345), "01:01:01,234")


if __name__ == "__main__":
    unittest.main()
