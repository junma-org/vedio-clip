import unittest

from subtitle_model import (
    SubtitleEntry,
    SubtitleStyle,
    SubtitleTrack,
    SubtitleValidationError,
    add_subtitle_from_marks,
    build_style_preset,
    extract_fade_from_tags,
    format_srt_timestamp,
    load_ass_text,
    load_subtitle_text,
    parse_srt_text,
    serialize_ass_project,
    serialize_srt_entries,
    set_fade_on_tags,
    strip_fade_from_tags,
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

    def test_load_subtitle_text_converts_srt_into_ass_project(self):
        project = load_subtitle_text(
            """1
00:00:01,000 --> 00:00:02,000
你好
""",
            source_hint="clipboard",
            video_size=(1080, 1920),
        )

        self.assertEqual(project.play_res_x, 1080)
        self.assertEqual(project.play_res_y, 1920)
        self.assertEqual(project.cues[0].text, "你好")
        self.assertEqual(project.cues[0].style_name, "short_speech_bottom")

    def test_load_ass_text_preserves_style_name_and_raw_tags(self):
        project = load_ass_text(
            """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: custom,Arial,42,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,20,20,24,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
Dialogue: 0,0:00:01.00,0:00:03.00,custom,,0000,0000,0000,,{\\b1}Hello\\NWorld
"""
        )

        self.assertEqual(project.play_res_x, 1280)
        self.assertEqual(project.cues[0].style_name, "custom")
        self.assertEqual(project.cues[0].raw_tags, "{\\b1}")
        self.assertEqual(project.cues[0].text, "Hello\nWorld")

    def test_serialize_ass_project_writes_dialogue_and_styles(self):
        project = SubtitleTrack(
            entries=(SubtitleEntry(1, 2, "字幕"),),
            style=SubtitleStyle(font_size=36, bottom_margin=64),
            play_res_x=1080,
            play_res_y=1920,
        ).normalized()

        content = serialize_ass_project(project)

        self.assertIn("[V4+ Styles]", content)
        self.assertIn("Style: short_speech_bottom", content)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00,short_speech_bottom", content)
        self.assertIn("字幕", content)

    def test_serialize_ass_project_writes_fade_tags(self):
        project = SubtitleTrack(entries=(SubtitleEntry(1, 2, "字幕", raw_tags="{\\fad(200,200)}"),))

        content = serialize_ass_project(project)

        self.assertIn("{\\fad(200,200)}字幕", content)

    def test_format_srt_timestamp_rounds_milliseconds(self):
        self.assertEqual(format_srt_timestamp(3661.2345), "01:01:01,234")

    def test_short_speech_preset_sits_in_lower_third_for_vertical_video(self):
        style = build_style_preset("short_speech_bottom", (1080, 1920))

        self.assertEqual(style.alignment, 2)
        self.assertEqual(style.margin_v, 710)

    def test_fade_tag_helpers_preserve_other_ass_tags(self):
        raw_tags = "{\\b1\\fad(100,200)}{\\i1}"

        self.assertEqual(extract_fade_from_tags(raw_tags), (100, 200))
        self.assertEqual(strip_fade_from_tags(raw_tags), "{\\b1}{\\i1}")
        self.assertEqual(set_fade_on_tags(raw_tags, 250, 300), "{\\fad(250,300)}{\\b1}{\\i1}")


if __name__ == "__main__":
    unittest.main()
