import unittest

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.executor import execute_plan
from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.presentation import format_plan
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.validation import PlanValidationError, validate_plan_against_data


class PivotTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "문구점": ["가게A", "가게A", "가게A", "가게B"],
                "품목": ["연필", "연필", "종합장", "연필"],
                "수량": [2, 3, 4, 7],
            }
        )

    def _plan(self) -> ExecutionPlan:
        return ExecutionPlan(
            goal="문구점과 품목별 수량 합계 교차표",
            problem_type="pivoting",
            steps=[
                PlanStep(
                    "pivot_table",
                    {
                        "index_columns": "문구점",
                        "pivot_column": "품목",
                        "value_column": "수량",
                        "aggfunc": "sum",
                        "fill_value": 0,
                    },
                    "문구점을 행, 품목을 열로 두고 수량 합계를 계산합니다.",
                )
            ],
        )

    def test_pivot_sum_creates_cross_tabulation(self):
        plan = self._plan()
        preview = validate_plan_against_data(self.df, plan)
        result = execute_plan(self.df, plan).df

        self.assertEqual(preview.final_rows, 2)
        self.assertEqual(list(result.columns), ["문구점", "연필", "종합장"])
        store_a = result.loc[result["문구점"] == "가게A"].iloc[0]
        store_b = result.loc[result["문구점"] == "가게B"].iloc[0]
        self.assertEqual(store_a["연필"], 5)
        self.assertEqual(store_a["종합장"], 4)
        self.assertEqual(store_b["연필"], 7)
        self.assertEqual(store_b["종합장"], 0)
        self.assertEqual(self.df.shape, (4, 3))

    def test_pivot_request_narrows_catalog_to_the_pivot_contract(self):
        hints = build_planning_hints(
            self.df,
            "문구점은 행으로, 품목은 열로 놓고 수량 합계 표를 만들어줘",
        )
        self.assertEqual(hints.recommended_functions, ["pivot_table"])

        available = OllamaPlanner._catalog_for_request(catalog_for_prompt(), hints)
        self.assertIn("pivot_table", available)
        self.assertEqual(set(available), {"pivot_table"})

    def test_non_numeric_sum_is_rejected(self):
        invalid_df = self.df.assign(수량=["둘", "셋", "넷", "일곱"])
        with self.assertRaises(PlanValidationError):
            validate_plan_against_data(invalid_df, self._plan())

    def test_confirmation_shows_pivot_axes(self):
        preview = validate_plan_against_data(self.df, self._plan())
        message = format_plan(self._plan(), preview)
        self.assertIn("행 기준: 문구점", message)
        self.assertIn("열 기준: 품목", message)
        self.assertIn("값: 수량 (합계)", message)


if __name__ == "__main__":
    unittest.main()
