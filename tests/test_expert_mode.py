import unittest

from edit_model import PlanValidationError
from expert_mode import (
    add_delete_range_from_marks,
    build_delete_range_from_marks,
    build_expert_edit_plan,
)


class ExpertModeTest(unittest.TestCase):
    def test_build_delete_range_from_marks_uses_in_and_out_points(self):
        delete_range = build_delete_range_from_marks(10.5, 20.25)

        self.assertEqual(delete_range.as_tuple(), (10.5, 20.25))

    def test_build_delete_range_from_marks_clamps_to_duration(self):
        delete_range = build_delete_range_from_marks(-5, 120, total_duration=100)

        self.assertEqual(delete_range.as_tuple(), (0.0, 100.0))

    def test_build_delete_range_from_marks_rejects_missing_mark(self):
        with self.assertRaises(PlanValidationError):
            build_delete_range_from_marks(None, 20)

    def test_build_delete_range_from_marks_rejects_out_before_in(self):
        with self.assertRaises(PlanValidationError):
            build_delete_range_from_marks(30, 20)

    def test_add_delete_range_from_marks_normalizes_ranges(self):
        ranges = add_delete_range_from_marks([(10, 20), (50, 60)], 18, 30)

        self.assertEqual([item.as_tuple() for item in ranges], [(10.0, 30.0), (50.0, 60.0)])

    def test_build_expert_edit_plan_reuses_edit_plan_ranges(self):
        plan = build_expert_edit_plan([(15, 25), (20, 30)])

        self.assertEqual(plan.skip_seconds, 0.0)
        self.assertEqual(plan.delete_range_tuples(), [(15.0, 30.0)])


if __name__ == "__main__":
    unittest.main()
