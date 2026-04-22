import unittest

from edit_model import (
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
