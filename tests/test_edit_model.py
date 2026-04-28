import unittest

from edit_model import (
    AudioTrack,
    DeleteRange,
    EditPlan,
    OutputOptions,
    PlanValidationError,
)
from subtitle_model import SubtitleEntry, SubtitleTrack


class EditModelTest(unittest.TestCase):
    def test_edit_plan_normalizes_delete_ranges(self):
        plan = EditPlan(
            skip_seconds=5,
            delete_ranges=(
                DeleteRange(80, 100),
                DeleteRange(10, 20),
                DeleteRange(18, 30),
            ),
            output=OutputOptions(resolution=("1920", "1080")),
        ).validate()

        self.assertEqual(plan.skip_seconds, 5.0)
        self.assertEqual(plan.output.resolution, (1920, 1080))
        self.assertEqual(plan.delete_range_tuples(), [(10.0, 30.0), (80.0, 100.0)])

    def test_edit_plan_output_duration_merges_skip_and_delete_ranges(self):
        plan = EditPlan(
            skip_seconds=30,
            delete_ranges=(DeleteRange(20, 40), DeleteRange(80, 100)),
        )

        self.assertEqual(plan.output_duration(120), 60)

    def test_edit_plan_rejects_invalid_delete_range(self):
        plan = EditPlan(delete_ranges=(DeleteRange(20, 10),))

        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_edit_plan_rejects_full_removal_when_duration_is_known(self):
        plan = EditPlan(
            skip_seconds=10,
            delete_ranges=(DeleteRange(10, 100),),
        )

        with self.assertRaises(PlanValidationError):
            plan.validate(total_duration=100)

    def test_edit_plan_supports_no_audio_source_flag(self):
        plan = EditPlan().with_has_audio(False).validate()

        self.assertFalse(plan.has_audio)

    def test_edit_plan_supports_source_audio_mute_and_tracks(self):
        plan = EditPlan(
            has_audio=True,
            source_audio_muted=True,
            audio_tracks=(AudioTrack("voice.mp3", "0.75"),),
        ).validate()

        self.assertFalse(plan.source_audio_enabled())
        self.assertTrue(plan.has_output_audio())
        self.assertEqual(plan.audio_tracks[0].volume, 0.75)

    def test_edit_plan_rejects_too_many_audio_tracks(self):
        plan = EditPlan(
            audio_tracks=(
                AudioTrack("a.mp3"),
                AudioTrack("b.mp3"),
                AudioTrack("c.mp3"),
            )
        )

        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_edit_plan_rejects_invalid_audio_volume(self):
        plan = EditPlan(audio_tracks=(AudioTrack("voice.mp3", 2.5),))

        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_edit_plan_normalizes_subtitles(self):
        plan = EditPlan(
            subtitles=SubtitleTrack(
                entries=(
                    SubtitleEntry(5, 6, "后"),
                    SubtitleEntry(1, 2, "前"),
                )
            )
        ).validate()

        self.assertEqual([entry.text for entry in plan.subtitles.entries], ["前", "后"])

    def test_edit_plan_rejects_invalid_subtitle(self):
        plan = EditPlan(
            subtitles=SubtitleTrack(entries=(SubtitleEntry(2, 1, "无效"),))
        )

        with self.assertRaises(PlanValidationError):
            plan.validate()


if __name__ == "__main__":
    unittest.main()
