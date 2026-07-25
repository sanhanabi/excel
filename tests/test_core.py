import unittest

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.excel_io import build_profile
from excel_assistant.executor import execute_plan
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.planners.rule_based import RuleBasedPlanner
from excel_assistant.validation import PlanValidationError, validate_plan


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "거래처명": ["가", "나", "가", "나"],
                "결제금액": [100, 50, 200, 80],
            }
        )

    def test_group_sum_and_sort(self):
        plan = ExecutionPlan(
            goal="거래처별 합계",
            steps=[
                PlanStep(
                    "group_sum",
                    {
                        "group_columns": "거래처명",
                        "value_column": "결제금액",
                        "result_column": "총매출",
                    },
                ),
                PlanStep("sort_rows", {"columns": "총매출", "ascending": False}),
            ],
        )
        validate_plan(plan, list(self.df.columns))
        result = execute_plan(self.df, plan).df
        self.assertEqual(result.iloc[0].to_dict(), {"거래처명": "가", "총매출": 300})
        self.assertEqual(self.df.shape, (4, 2))

    def test_unknown_column_is_rejected(self):
        plan = ExecutionPlan(
            goal="정렬",
            steps=[PlanStep("sort_rows", {"columns": "없는열"})],
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(plan, list(self.df.columns))

    def test_rule_based_planner_creates_valid_plan(self):
        profile = build_profile(self.df, "sample.xlsx", "판매")
        plan = RuleBasedPlanner().create_plan(
            "거래처명별 결제금액 합계를 구해서 큰 순으로 정리해줘",
            profile,
            catalog_for_prompt(),
        )
        validate_plan(plan, profile.column_names)
        result = execute_plan(self.df, plan).df
        self.assertEqual(result.iloc[0]["합계"], 300)


if __name__ == "__main__":
    unittest.main()
