"""Unit tests for merged plan reports (no network)."""

import unittest

from quackaloger.models import MoveAction, PlanReport
from quackaloger.runner import merge_plan_reports


class MergePlanReportsTests(unittest.TestCase):
    def test_merge_concatenates_moves(self):
        a = PlanReport()
        b = PlanReport()
        a.moves.append(MoveAction(source="/a", dest="/b", file_type="audio"))
        b.moves.append(MoveAction(source="/c", dest="/d", file_type="video"))
        out = merge_plan_reports(a, b)
        self.assertEqual(len(out.moves), 2)
        self.assertEqual(out.moves[0].source, "/a")
        self.assertEqual(out.moves[1].source, "/c")

    def test_merge_sums_audible_stats(self):
        a = PlanReport()
        b = PlanReport()
        a.audible_stats = {"matched": 2, "unmatched": 1}
        b.audible_stats = {"matched": 3, "unmatched": 0}
        out = merge_plan_reports(a, b)
        self.assertEqual(out.audible_stats["matched"], 5)
        self.assertEqual(out.audible_stats["unmatched"], 1)


if __name__ == "__main__":
    unittest.main()
