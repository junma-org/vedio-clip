import unittest

from timeline_tracks import (
    TIMELINE_TRACKS,
    TRACK_OVERLAY,
    TRACK_SUBTITLE,
    TRACK_VIDEO,
    clip_visible_range,
    track_rect_tuple,
    track_spec,
)


class TimelineTracksTest(unittest.TestCase):
    def test_track_specs_keep_existing_order_and_labels(self):
        self.assertEqual([track.key for track in TIMELINE_TRACKS], [TRACK_OVERLAY, TRACK_VIDEO, TRACK_SUBTITLE])
        self.assertEqual([track.label for track in TIMELINE_TRACKS], ["叠加轨", "视频轨", "字幕轨"])

    def test_track_rect_tuple_matches_existing_offsets(self):
        content = (14, 12, 640, 140)

        self.assertEqual(track_rect_tuple(content, TRACK_OVERLAY), (14, 12, 640, 28))
        self.assertEqual(track_rect_tuple(content, TRACK_VIDEO), (14, 54, 640, 44))
        self.assertEqual(track_rect_tuple(content, TRACK_SUBTITLE), (14, 114, 640, 34))

    def test_track_spec_returns_radius_and_background(self):
        video = track_spec(TRACK_VIDEO)

        self.assertEqual(video.radius, 8)
        self.assertEqual(video.background, "#dce6f2")

    def test_clip_visible_range_keeps_edge_touching_blocks(self):
        self.assertEqual(clip_visible_range(0, 5, 5, 10), (5, 5))
        self.assertEqual(clip_visible_range(10, 12, 5, 10), (10, 10))

    def test_clip_visible_range_rejects_outside_blocks(self):
        self.assertIsNone(clip_visible_range(0, 4.99, 5, 10))
        self.assertIsNone(clip_visible_range(10.01, 12, 5, 10))


if __name__ == "__main__":
    unittest.main()
