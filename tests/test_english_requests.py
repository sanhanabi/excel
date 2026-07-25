import unittest

import pandas as pd

from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.request_analysis import analyze_request
from excel_assistant.validation import (
    repair_plan_from_explicit_request,
    validate_plan_covers_request,
)


class EnglishRequestTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "Type": ["Client", "Company", "Finance Company"],
                "Method": ["ACH", "Check", "ACH"],
                "Description": ["FPMT A", "FEE", "FPMT B"],
                "Lookup Code": ["ABCDE-1", "FGHIJ-2", "KLMNO-3"],
                "Commission": [10.0, 0.0, 20.0],
                "$ RCVD": [100.0, 200.0, 300.0],
                "Date rcvd": pd.to_datetime(
                    ["2026-01-01", "2026-01-02", "2026-01-03"]
                ),
                "PAID DATE": [None, "2026-01-10", None],
            }
        )

    def _hints(self, request):
        return build_planning_hints(self.frame, request)

    def _repair_and_validate(self, request, plan):
        hints = self._hints(request)
        repaired = repair_plan_from_explicit_request(
            request,
            plan,
            list(self.frame.columns),
            hints,
        )
        validate_plan_covers_request(
            request,
            repaired,
            list(self.frame.columns),
            hints,
        )
        return repaired

    def test_korean_and_english_filter_sort_route_to_same_functions(self):
        korean = (
            "ACH로 받은 Client 기록 중 Commission이 0보다 큰 행만 남기고 "
            "Commission이 큰 순서로 정렬해줘"
        )
        english = (
            "Keep rows where Method is ACH, Type is Client, and Commission is "
            "greater than 0, then sort by Commission, largest first."
        )

        self.assertEqual(
            self._hints(korean).recommended_functions,
            self._hints(english).recommended_functions,
        )

    def test_english_comparison_and_sort_direction_are_preserved(self):
        request = (
            "Keep rows where Method is Check and Commission is greater than 0, "
            "then sort by Commission, largest first."
        )
        approximate = ExecutionPlan(
            goal="Filter and sort",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Method", "operator": "==", "value": "Check"},
                            {"column": "Commission", "operator": ">=", "value": 0.01},
                        ],
                        "logic": "and",
                    },
                ),
                PlanStep(
                    "sort_rows",
                    {"columns": ["Commission"], "ascending": True},
                ),
            ],
        )

        repaired = self._repair_and_validate(request, approximate)

        condition = repaired.steps[0].params["conditions"][1]
        self.assertEqual(condition["operator"], ">")
        self.assertEqual(condition["value"], 0)
        self.assertFalse(repaired.steps[1].params["ascending"])

    def test_english_or_and_blank_conditions_are_preserved(self):
        request = (
            "Keep rows where Type is either Finance Company or Company and "
            "PAID DATE is blank."
        )
        wrong = ExecutionPlan(
            goal="Filter companies",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Type", "operator": "==", "value": "Finance Company"},
                            {"column": "PAID DATE", "operator": "==", "value": None},
                        ],
                        "logic": "and",
                    },
                )
            ],
        )

        repaired = self._repair_and_validate(request, wrong)
        or_step = next(
            step
            for step in repaired.steps
            if step.function == "filter_by_conditions"
            and step.params.get("logic") == "or"
        )

        self.assertEqual(
            {item["value"] for item in or_step.params["conditions"]},
            {"Finance Company", "Company"},
        )
        self.assertTrue(
            any(
                item.get("column") == "PAID DATE"
                and item.get("operator") == "is_null"
                for step in repaired.steps
                for item in step.params.get("conditions", [])
            )
        )

    def test_english_action_phrases_route_to_expected_families(self):
        cases = {
            "Put Method in rows and Type in columns, and sum $ RCVD in a pivot table.": {
                "pivot_table"
            },
            "Group by Type and calculate the sum and average of Commission.": {
                "group_aggregate", "group_sum", "group_average", "group_count"
            },
            "Add a new column named Review; if Type is Client, write Check, otherwise leave it blank.": {
                "add_conditional_column"
            },
            "Keep rows where Description contains FPMT and extract the first 5 characters of Lookup Code.": {
                "filter_rows", "filter_by_conditions", "drop_rows_missing_keys", "extract_text"
            },
            "Highlight rows where Commission is greater than 100.": {
                "highlight_rows", "highlight_extremes", "highlight_missing", "color_scale"
            },
        }

        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(
                    set(analyze_request(request).recommended_functions),
                    expected,
                )

    def test_english_today_and_oldest_duration_sort_are_repaired(self):
        request = (
            "Keep rows where PAID DATE is blank and Date rcvd is not blank, "
            "calculate days since Date rcvd until today in a new Age Days column, "
            "then sort oldest first."
        )
        approximate = ExecutionPlan(
            goal="Outstanding age",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "PAID DATE", "operator": "==", "value": None},
                            {"column": "Date rcvd", "operator": "!=", "value": None},
                        ],
                        "logic": "and",
                    },
                ),
                PlanStep(
                    "calculate_date_difference",
                    {
                        "start_column": "Date rcvd",
                        "end_column": "PAID DATE",
                        "end_mode": "column",
                        "result_column": "Age Days",
                        "unit": "days",
                    },
                ),
                PlanStep(
                    "sort_rows",
                    {"columns": ["Age Days"], "ascending": True},
                ),
            ],
        )

        repaired = self._repair_and_validate(request, approximate)

        self.assertEqual(repaired.steps[1].params["end_mode"], "today")
        self.assertIsNone(repaired.steps[1].params["end_column"])
        self.assertFalse(repaired.steps[2].params["ascending"])

    def test_english_duplicate_filters_are_removed(self):
        request = "Keep rows where PAID DATE is blank."
        duplicate = PlanStep(
            "filter_rows",
            {"column": "PAID DATE", "operator": "is_null", "value": None},
        )
        plan = ExecutionPlan(
            goal="Unpaid rows",
            steps=[duplicate, duplicate],
        )

        repaired = self._repair_and_validate(request, plan)

        self.assertEqual(len(repaired.steps), 1)

    def test_english_group_total_row_targets_only_summable_result(self):
        request = (
            "Group by Type and calculate the sum and average of Commission, "
            "then add a total row at the bottom."
        )
        plan = ExecutionPlan(
            goal="Grouped summary",
            steps=[
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": ["Type"],
                        "aggregations": [
                            {
                                "column": "Commission",
                                "function": "sum",
                                "result_column": "Commission_SUM",
                            },
                            {
                                "column": "Commission",
                                "function": "mean",
                                "result_column": "Commission_AVG",
                            },
                        ],
                    },
                ),
                PlanStep(
                    "add_total_row",
                    {
                        "value_columns": ["Type", "Commission_SUM", "Commission_AVG"],
                        "aggregate": "sum",
                    },
                ),
            ],
        )

        repaired = self._repair_and_validate(request, plan)

        self.assertEqual(
            repaired.steps[-1].params["value_columns"],
            ["Commission_SUM"],
        )


if __name__ == "__main__":
    unittest.main()
