import unittest

from subtitle_model import SubtitleValidationError
from timeline_state import (
    TimelineSelection,
    TimelineStateError,
    add_delete_range_from_selection,
    add_subtitle_from_selection_or_playhead,
    delete_current_frame,
    move_timed_range,
    resize_timed_range,
    selection_from_points,
)


class TimelineStateTest(unittest.TestCase):
    def test_selection_from_points_normalizes_reverse_drag(self):
        selection = selection_from_points(8, 3, total_duration=10)

        self.assertEqual(selection.start, 3.0)
        self.assertEqual(selection.end, 8.0)

    def test_add_delete_range_from_selection_merges_ranges(self):
        ranges = add_delete_range_from_selection(
            [(1, 2), (5, 7)],
            TimelineSelection(6, 9),
            total_duration=12,
        )

        self.assertEqual([item.as_tuple() for item in ranges], [(1.0, 2.0), (5.0, 9.0)])

    def test_add_delete_range_from_selection_rejects_collapsed_selection(self):
        with self.assertRaises(TimelineStateError):
            add_delete_range_from_selection([], TimelineSelection(3, 3), total_duration=10)

    def test_delete_current_frame_uses_fps(self):
        ranges = delete_current_frame(1.0, 25, [], total_duration=10)

        self.assertAlmostEqual(ranges[0].start, 1.0)
        self.assertAlmostEqual(ranges[0].end, 1.04)

    def test_add_subtitle_from_selection_or_playhead_uses_default_duration_when_no_selection(self):
        cues, new_cue = add_subtitle_from_selection_or_playhead(
            [],
            TimelineSelection(2, 2),
            4.5,
            "字幕",
            total_duration=10,
            default_duration=2.0,
        )

        self.assertEqual(len(cues), 1)
        self.assertEqual(new_cue.as_tuple(), (4.5, 6.5, "字幕"))

    def test_add_subtitle_from_selection_or_playhead_keeps_default_duration_near_end(self):
        _cues, new_cue = add_subtitle_from_selection_or_playhead(
            [],
            TimelineSelection(10, 10),
            10,
            "字幕",
            total_duration=10,
            default_duration=2.0,
        )

        self.assertEqual(new_cue.as_tuple(), (8.0, 10.0, "字幕"))

    def test_add_subtitle_from_selection_or_playhead_uses_selection_range(self):
        cues, new_cue = add_subtitle_from_selection_or_playhead(
            [],
            TimelineSelection(3, 5),
            0,
            "字幕",
            total_duration=10,
        )

        self.assertEqual(new_cue.as_tuple(), (3.0, 5.0, "字幕"))

    def test_add_subtitle_from_selection_or_playhead_rejects_blank_text(self):
        with self.assertRaises(SubtitleValidationError):
            add_subtitle_from_selection_or_playhead([], TimelineSelection(1, 2), 0, "   ")

    def test_add_subtitle_from_selection_or_playhead_preserves_raw_tags(self):
        _cues, new_cue = add_subtitle_from_selection_or_playhead(
            [],
            TimelineSelection(1, 2),
            0,
            "字幕",
            raw_tags="{\\fad(200,200)}",
        )

        self.assertEqual(new_cue.raw_tags, "{\\fad(200,200)}")

    def test_resize_timed_range_keeps_min_duration(self):
        selection = resize_timed_range(2, 5, "start", 4.99, total_duration=10, min_duration=0.1)

        self.assertAlmostEqual(selection.start, 4.9)
        self.assertAlmostEqual(selection.end, 5.0)

    def test_move_timed_range_clamps_to_duration(self):
        selection = move_timed_range(8, 10, 3, total_duration=10)

        self.assertEqual(selection, TimelineSelection(8.0, 10.0))

        selection = move_timed_range(1, 3, -5, total_duration=10)
        self.assertEqual(selection, TimelineSelection(0.0, 2.0))


if __name__ == "__main__":
    unittest.main()
