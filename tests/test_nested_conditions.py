import unittest

import pandas as pd

from excel_assistant import operations
from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.validation import (
    PlanValidationError,
    repair_plan_from_explicit_request,
    validate_plan_against_data,
)


class NestedConditionTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "Amount": [100.0, None, 0.0, 50.0, None],
                "Paid Date": [None, "2026-01-02", "2026-01-03", "2026-01-04", None],
                "Name": ["A", "B", "C", "D", "E"],
            }
        )
        self.groups = [
            {
                "conditions": [
                    {"column": "Amount", "operator": ">", "value": 0},
                    {"column": "Paid Date", "operator": "is_null", "value": None},
                ],
                "logic": "and",
            },
            {
                "conditions": [
                    {"column": "Amount", "operator": "is_null", "value": None},
                    {"column": "Paid Date", "operator": "not_null", "value": None},
                ],
                "logic": "and",
            },
        ]

    def test_nested_filter_executes_and_groups_joined_by_or(self):
        result = operations.filter_by_conditions(
            self.frame,
            condition_groups=self.groups,
            group_logic="or",
        )

        self.assertEqual(result["Name"].tolist(), ["A", "B"])

    def test_nested_conditional_column_marks_only_matching_rows(self):
        result = operations.add_conditional_column(
            self.frame,
            result_column="Review",
            condition_groups=self.groups,
            group_logic="or",
            true_value="확인필요",
            false_value="",
        )

        self.assertEqual(
            result["Review"].tolist(),
            ["확인필요", "확인필요", "", "", ""],
        )

    def test_existing_flat_conditions_remain_compatible(self):
        result = operations.filter_by_conditions(
            self.frame,
            conditions=[
                {"column": "Amount", "operator": ">", "value": 0},
                {"column": "Paid Date", "operator": "not_null", "value": None},
            ],
            logic="and",
        )

        self.assertEqual(result["Name"].tolist(), ["D"])

    def test_plan_validation_and_preview_support_nested_groups(self):
        plan = ExecutionPlan(
            goal="모순 기록 표시",
            steps=[
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "Review",
                        "condition_groups": self.groups,
                        "group_logic": "or",
                        "true_value": "확인필요",
                        "false_value": "",
                    },
                )
            ],
        )

        preview = validate_plan_against_data(self.frame, plan)

        self.assertEqual(preview.final_rows, 5)
        self.assertEqual(preview.steps[0].after_rows, 5)

    def test_using_flat_and_nested_conditions_together_is_rejected(self):
        plan = ExecutionPlan(
            goal="잘못된 조건",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Amount", "operator": ">", "value": 0}
                        ],
                        "condition_groups": self.groups,
                        "logic": "and",
                        "group_logic": "or",
                    },
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "정확히 하나"):
            validate_plan_against_data(self.frame, plan)

    def test_nested_condition_columns_are_checked(self):
        bad_groups = [
            {
                "conditions": [
                    {"column": "Missing", "operator": ">", "value": 0}
                ],
                "logic": "and",
            }
        ]
        plan = ExecutionPlan(
            goal="없는 열",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {"condition_groups": bad_groups, "group_logic": "or"},
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "Missing"):
            validate_plan_against_data(self.frame, plan)

    def test_ollama_schema_offers_exactly_one_condition_shape(self):
        catalog = catalog_for_prompt()
        selected = {
            name: catalog[name]
            for name in ("filter_by_conditions", "add_conditional_column")
        }
        schema = OllamaPlanner._plan_schema(
            selected,
            source_columns=list(self.frame.columns),
        )
        step_variants = schema["properties"]["steps"]["items"]["oneOf"]
        for variant in step_variants[:2]:
            params = variant["properties"]["params"]
            self.assertEqual(
                params["oneOf"],
                [
                    {"required": ["conditions"]},
                    {"required": ["condition_groups"]},
                ],
            )
        filter_params = step_variants[0]["properties"]["params"]
        nested_column = filter_params["properties"]["condition_groups"]["items"][
            "properties"
        ]["conditions"]["items"]["properties"]["column"]
        self.assertEqual(nested_column["enum"], list(self.frame.columns))

    def test_explicit_or_request_repairs_repeated_conditional_steps(self):
        request = (
            "Amount가 0보다 큰데 Paid Date가 비어 있거나, Paid Date는 있는데 "
            "Amount가 비어 있으면 'Review'라는 새 열에 '확인필요'라고 표시하고, "
            "아니면 빈칸으로 둬."
        )
        approximate = ExecutionPlan(
            goal="모순 표시",
            steps=[
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "Status",
                        "conditions": [
                            {"column": "Amount", "operator": ">=", "value": 0},
                            {"column": "Paid Date", "operator": "is_null", "value": None},
                        ],
                        "logic": "and",
                        "true_value": "",
                        "false_value": "확인필요",
                    },
                ),
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "Status",
                        "conditions": [
                            {"column": "Amount", "operator": "==", "value": None},
                        ],
                        "logic": "and",
                        "true_value": "",
                        "false_value": "확인필요",
                    },
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(self.frame.columns),
        )
        preview = validate_plan_against_data(self.frame, repaired)
        result = operations.add_conditional_column(
            self.frame,
            **repaired.steps[0].params,
        )

        self.assertEqual(len(repaired.steps), 1)
        self.assertEqual(repaired.steps[0].params["result_column"], "Review")
        self.assertEqual(repaired.steps[0].params["true_value"], "확인필요")
        self.assertEqual(repaired.steps[0].params["false_value"], "")
        self.assertEqual(len(repaired.steps[0].params["condition_groups"]), 2)
        self.assertEqual(preview.final_rows, len(self.frame))
        self.assertEqual(
            result["Review"].tolist(),
            ["확인필요", "확인필요", "", "", ""],
        )

    def test_grounded_values_and_numeric_conditions_become_nested_filter(self):
        frame = pd.DataFrame(
            {
                "Store": ["Alpha Art", "Alpha Art", "Morning Art", "Morning Art"],
                "Price": [1200, 800, 700, 900],
                "Quantity": [10, 80, 75, 20],
            }
        )
        request = (
            "Alpha Art에서 Price가 1000 이상인 거래이거나, Morning Art에서 "
            "Quantity가 70 이상인 거래만 남겨줘"
        )
        approximate = ExecutionPlan(
            goal="두 분기 필터",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Store", "operator": "==", "value": "Alpha Art"},
                            {"column": "Price", "operator": ">=", "value": 1000},
                        ],
                        "logic": "and",
                    },
                ),
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Store", "operator": "==", "value": "Morning Art"},
                            {"column": "Quantity", "operator": ">=", "value": 70},
                        ],
                        "logic": "and",
                    },
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(frame.columns),
            build_planning_hints(frame, request),
        )
        result = operations.filter_by_conditions(frame, **repaired.steps[0].params)

        self.assertEqual(len(repaired.steps), 1)
        self.assertEqual(repaired.steps[0].params["group_logic"], "or")
        self.assertEqual(len(repaired.steps[0].params["condition_groups"]), 2)
        self.assertEqual(result["Store"].tolist(), ["Alpha Art", "Morning Art"])


if __name__ == "__main__":
    unittest.main()
