import unittest

from edit_model import AudioTrack, OverlayClip
from editor_session import EditorSession, MAX_UNDO_STATES
from subtitle_model import SubtitleCue, SubtitleProject
from timeline_state import TimelineSelection


def subtitle_project_with_text(text):
    return SubtitleProject(
        cues=(SubtitleCue(1, 2, text),),
    ).normalized()


class EditorSessionTest(unittest.TestCase):
    def test_snapshot_and_restore_round_trip(self):
        session = EditorSession(
            delete_ranges=[(5, 6)],
            expert_delete_ranges=[(1, 2)],
            expert_selection=TimelineSelection(3, 4),
            expert_output_resolution=(1280, 720),
            source_audio_muted=True,
            audio_tracks=[AudioTrack("music.mp3", 0.5)],
            media_overlays=[OverlayClip("cover.png", "image", 0, 3)],
            selected_overlay_index=0,
            subtitle_project=subtitle_project_with_text("原字幕"),
        )

        snapshot = session.snapshot(subtitle_row=0)
        session.delete_ranges = []
        session.expert_delete_ranges = []
        session.expert_selection = TimelineSelection(0, 0)
        session.expert_output_resolution = None
        session.source_audio_muted = False
        session.audio_tracks = []
        session.media_overlays = []
        session.selected_overlay_index = -1
        session.subtitle_project = subtitle_project_with_text("新字幕")

        session.restore(snapshot)

        self.assertEqual(session.delete_ranges, [(5, 6)])
        self.assertEqual(session.expert_delete_ranges, [(1, 2)])
        self.assertEqual(session.expert_selection, TimelineSelection(3, 4))
        self.assertEqual(session.expert_output_resolution, (1280, 720))
        self.assertTrue(session.source_audio_muted)
        self.assertEqual(session.audio_tracks, [AudioTrack("music.mp3", 0.5)])
        self.assertEqual(session.media_overlays, [OverlayClip("cover.png", "image", 0, 3)])
        self.assertEqual(session.selected_overlay_index, 0)
        self.assertEqual(session.subtitle_project.cues[0].text, "原字幕")
        self.assertEqual(snapshot.subtitle_row, 0)

    def test_undo_stack_keeps_limit_and_pops_latest(self):
        session = EditorSession()

        for index in range(MAX_UNDO_STATES + 2):
            session.delete_ranges = [(index, index + 1)]
            session.push_undo_state(session.snapshot())

        self.assertEqual(len(session.undo_stack), MAX_UNDO_STATES)
        latest = session.pop_undo_state()

        self.assertEqual(latest.delete_ranges, ((MAX_UNDO_STATES + 1, MAX_UNDO_STATES + 2),))
        self.assertEqual(session.undo_stack[0].delete_ranges, ((2, 3),))

    def test_to_edit_plan_uses_expert_session_state(self):
        session = EditorSession(
            expert_delete_ranges=[(10, 20), (18, 30)],
            expert_output_resolution=("1280", "720"),
            source_audio_muted=True,
            audio_tracks=[AudioTrack("music.mp3", "0.5")],
            media_overlays=[OverlayClip("cover.png", "image", "1", "4")],
            subtitle_project=subtitle_project_with_text("字幕"),
        )

        plan = session.to_edit_plan().validate()

        self.assertEqual(plan.delete_range_tuples(), [(10.0, 30.0)])
        self.assertEqual(plan.output.resolution, (1280, 720))
        self.assertFalse(plan.source_audio_enabled())
        self.assertEqual(plan.audio_tracks[0].volume, 0.5)
        self.assertEqual(plan.media_overlays[0].start, 1.0)
        self.assertEqual(plan.subtitles.cues[0].text, "字幕")

    def test_to_transcription_plan_keeps_audio_flags(self):
        session = EditorSession(
            source_audio_muted=True,
            audio_tracks=[AudioTrack("voice.wav", 1.25)],
        )

        plan = session.to_transcription_plan(has_audio=False).validate()

        self.assertFalse(plan.has_audio)
        self.assertFalse(plan.source_audio_enabled())
        self.assertEqual(plan.audio_tracks[0].volume, 1.25)


if __name__ == "__main__":
    unittest.main()
