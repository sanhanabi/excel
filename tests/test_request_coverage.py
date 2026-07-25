import unittest

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.request_analysis import analyze_request
from excel_assistant.validation import (
    PlanValidationError,
    repair_plan_from_explicit_request,
    validate_plan_covers_request,
)


class RequestCoverageTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "Type": ["Client", "Company", "Finance Company"],
                "Method": ["ACH", "Check", "ACH"],
                "Commission": [10.0, 0.0, 20.0],
                "PAID DATE": [None, "2026-01-01", None],
                "Lookup Code": ["ABCDE-1", "FGHIJ-2", "KLMNO-3"],
            }
        )

    def _validate(self, request, plan):
        validate_plan_covers_request(
            request,
            plan,
            list(self.frame.columns),
            build_planning_hints(self.frame, request),
        )

    def test_catalog_is_narrowed_to_explicit_actions(self):
        hints = build_planning_hints(
            self.frame,
            "ACH인 행만 남기고 Commission 큰 순서로 정렬해줘",
        )
        available = OllamaPlanner._catalog_for_request(catalog_for_prompt(), hints)

        self.assertEqual(
            set(available),
            {"filter_rows", "filter_by_conditions", "drop_rows_missing_keys", "sort_rows"},
        )

    def test_latest_per_group_and_top_n_are_routed_without_generic_filter(self):
        latest = analyze_request("문구점마다 날짜가 가장 최근인 거래 한 건만 남겨줘")
        ranked = analyze_request(
            "판매금액이 큰 순위를 새 열로 추가한 다음 상위 10개 거래만 남겨줘"
        )

        self.assertIn("latest_per_group", latest.action_names)
        self.assertIn("rank", ranked.action_names)
        self.assertIn("top_n", ranked.action_names)
        self.assertNotIn("filter", ranked.action_names)

    def test_aggregate_sort_column_is_repaired_to_single_sum_output(self):
        request = "Method별 $ RCVD 합계와 평균을 구하고 합계가 큰 순서로 정렬해줘"
        approximate = ExecutionPlan(
            goal="Method별 수금 집계",
            steps=[
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": ["Method"],
                        "aggregations": [
                            {
                                "column": "$ RCVD",
                                "function": "sum",
                                "result_column": "수금합계",
                            },
                            {
                                "column": "$ RCVD",
                                "function": "mean",
                                "result_column": "수금평균",
                            },
                        ],
                    },
                ),
                PlanStep(
                    "group_count",
                    {"group_columns": ["Method"], "result_column": "거래건수"},
                ),
                PlanStep(
                    "sort_rows",
                    {"columns": ["$ RCVD"], "ascending": False},
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            ["Method", "$ RCVD"],
        )

        self.assertEqual(
            [step.function for step in repaired.steps],
            ["group_aggregate", "sort_rows"],
        )
        self.assertEqual(repaired.steps[1].params["columns"], ["수금합계"])
        self.assertIn(
            {
                "column": "Method",
                "function": "size",
                "result_column": "거래건수",
            },
            repaired.steps[0].params["aggregations"],
        )

    def test_top_n_on_rank_column_selects_smallest_rank_numbers(self):
        request = "판매금액이 큰 순위를 추가하고 상위 10개 거래만 남겨줘"
        approximate = ExecutionPlan(
            goal="상위 순위",
            steps=[
                PlanStep(
                    "rank_rows",
                    {
                        "column": "Commission",
                        "result_column": "순위",
                        "ascending": False,
                    },
                ),
                PlanStep(
                    "select_top_n",
                    {"column": "순위", "n": 10, "largest": True},
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(self.frame.columns),
        )

        self.assertFalse(repaired.steps[1].params["largest"])

    def test_missing_grounded_filter_value_is_rejected(self):
        request = (
            "ACH로 받은 Client 기록 중 Commission이 0보다 큰 행만 남기고 "
            "Commission이 큰 순서로 정렬해줘"
        )
        incomplete = ExecutionPlan(
            goal="금액 필터와 정렬",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "Commission", "operator": ">", "value": 0},
                ),
                PlanStep("sort_rows", {"columns": "Commission", "ascending": False}),
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "실제 조건이 계획에서 누락"):
            self._validate(request, incomplete)

    def test_changed_numeric_comparison_is_rejected(self):
        request = "Method가 Check이고 Commission이 0보다 큰 행만 남겨줘"
        changed = ExecutionPlan(
            goal="Check 필터",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Method", "operator": "==", "value": "Check"},
                            {"column": "Commission", "operator": ">=", "value": 0},
                        ],
                        "logic": "and",
                    },
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "비교 조건이 계획에서"):
            self._validate(request, changed)

    def test_short_column_name_is_not_found_inside_longer_column(self):
        request = "Commission이 0보다 큰 행만 남겨줘"
        correct = ExecutionPlan(
            goal="양수 Commission 필터",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "Commission", "operator": ">", "value": 0},
                )
            ],
        )

        self._validate(request, correct)

    def test_explicit_comparison_repair_preserves_user_operator(self):
        request = "Commission이 0보다 큰 행만 남겨줘"
        approximate = ExecutionPlan(
            goal="양수 Commission 필터",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "Commission", "operator": ">=", "value": 0.01},
                )
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(self.frame.columns),
        )

        self.assertEqual(repaired.steps[0].params["operator"], ">")
        self.assertEqual(repaired.steps[0].params["value"], 0)

    def test_grounding_does_not_match_short_value_inside_another_word(self):
        frame = self.frame.assign(**{"Payment ID": ["ECK", "ABC", "DEF"]})
        hints = build_planning_hints(frame, "Method가 Check인 행만 남겨줘")

        self.assertFalse(
            any(item.exact_value == "ECK" for item in hints.matched_values)
        )

    def test_explicit_column_owns_same_text_value_in_another_column(self):
        frame = pd.DataFrame(
            {
                "Description": ["SERVICE FEE", "OTHER"],
                "CO": ["FEE", "OTHER"],
                "$ RCVD": [100, 200],
            }
        )
        hints = build_planning_hints(
            frame,
            "Description에 FEE가 들어간 행만 남겨줘",
        )

        self.assertFalse(
            any(item.column == "CO" and item.exact_value == "FEE" for item in hints.matched_values)
        )

    def test_pivot_fill_value_is_not_mistaken_for_null_filter(self):
        request = "Method를 행으로 놓고 $ RCVD 합계를 피벗표로 만들되 빈칸은 0으로 채워줘"
        plan = ExecutionPlan(
            goal="피벗",
            steps=[
                PlanStep(
                    "pivot_table",
                    {
                        "index_columns": "Method",
                        "pivot_column": "Type",
                        "value_column": "$ RCVD",
                        "aggfunc": "sum",
                        "fill_value": 0,
                    },
                )
            ],
        )

        self._validate(request, plan)

    def test_conditional_summary_uses_explicit_target_column(self):
        request = (
            "Method가 ACH이고 Type이 Client인 거래의 Commission 조건부 합계를 "
            "Commission 열 아래에 추가해줘"
        )
        approximate = ExecutionPlan(
            goal="조건부 합계",
            steps=[
                PlanStep(
                    "add_conditional_summary_row",
                    {
                        "conditions": [
                            {"column": "Method", "operator": "==", "value": "ACH"},
                            {"column": "Type", "operator": "==", "value": "Client"},
                        ],
                        "logic": "and",
                        "condition_column": "Method",
                        "operator": "==",
                        "value": "ACH",
                        "aggregate": "sum",
                        "value_column": "Commission_SUM",
                        "output_column": "Commission_SUM",
                    },
                )
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(self.frame.columns),
            build_planning_hints(self.frame, request),
        )

        params = repaired.steps[0].params
        self.assertEqual(params["value_column"], "Commission")
        self.assertEqual(params["output_column"], "Commission")
        self.assertNotIn("condition_column", params)
        self.assertNotIn("operator", params)
        self.assertNotIn("value", params)
        self._validate(request, repaired)

    def test_oldest_duration_sort_is_repaired_to_descending(self):
        request = (
            "PAID DATE가 비어 있고 받은 날부터 오늘까지 경과일을 "
            "'미정산일수' 열로 계산하고 오래된 순서로 정렬해줘"
        )
        approximate = ExecutionPlan(
            goal="미정산 경과일",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "PAID DATE", "operator": "is_null", "value": None},
                ),
                PlanStep(
                    "calculate_date_difference",
                    {
                        "start_column": "PAID DATE",
                        "end_column": None,
                        "end_mode": "today",
                        "result_column": "미정산일수",
                        "unit": "days",
                    },
                ),
                PlanStep(
                    "sort_rows",
                    {"columns": ["미정산일수"], "ascending": True},
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            list(self.frame.columns),
        )

        self.assertFalse(repaired.steps[-1].params["ascending"])

    def test_missing_later_extract_action_is_rejected(self):
        request = "Client 행만 남기고 Lookup Code의 앞 5글자를 새 열로 뽑아줘"
        incomplete = ExecutionPlan(
            goal="Client 필터",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "Type", "operator": "==", "value": "Client"},
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "문자열 일부 추출"):
            self._validate(request, incomplete)

    def test_or_values_cannot_be_turned_into_sequential_and_filters(self):
        request = (
            "Type이 Finance Company이거나 Company인 행 중 "
            "PAID DATE가 비어 있는 것만 남겨줘"
        )
        wrong = ExecutionPlan(
            goal="회사 유형과 미지급 필터",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "Type", "operator": "==", "value": "Finance Company"},
                            {"column": "PAID DATE", "operator": "is_null", "value": None},
                        ],
                        "logic": "and",
                    },
                ),
                PlanStep(
                    "filter_rows",
                    {"column": "Type", "operator": "contains", "value": "Company"},
                ),
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "OR로 연결한 조건"):
            self._validate(request, wrong)

        repaired = repair_plan_from_explicit_request(
            request,
            wrong,
            list(self.frame.columns),
            build_planning_hints(self.frame, request),
        )
        self._validate(request, repaired)
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

    def test_unrequested_source_replacement_is_rejected(self):
        request = (
            "Type이 Client이고 PAID DATE가 비어 있으면 새 열 '미정산 여부'에 "
            "'확인', 아니면 빈칸을 넣어줘"
        )
        dangerous = ExecutionPlan(
            goal="미정산 열 생성",
            steps=[
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "미정산 여부",
                        "conditions": [
                            {"column": "Type", "operator": "==", "value": "Client"},
                            {"column": "PAID DATE", "operator": "is_null", "value": None},
                        ],
                        "true_value": "확인",
                        "false_value": "",
                        "logic": "and",
                    },
                ),
                PlanStep(
                    "replace_values",
                    {"column": "Type", "replacements": {"Client": "변경"}},
                ),
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "요청하지 않은 원본"):
            self._validate(request, dangerous)

    def test_correct_conditional_column_covers_every_condition(self):
        request = (
            "Type이 Client이고 PAID DATE가 비어 있으면 새 열 '미정산 여부'에 "
            "'확인', 아니면 빈칸을 넣어줘"
        )
        correct = ExecutionPlan(
            goal="미정산 열 생성",
            steps=[
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "미정산 여부",
                        "conditions": [
                            {"column": "Type", "operator": "==", "value": "Client"},
                            {"column": "PAID DATE", "operator": "is_null", "value": None},
                        ],
                        "true_value": "확인",
                        "false_value": "",
                        "logic": "and",
                    },
                )
            ],
        )

        self._validate(request, correct)

    def test_group_summary_requires_every_named_aggregate(self):
        request = "Type별 Commission 합계와 평균을 계산해줘"
        incomplete = ExecutionPlan(
            goal="Type별 합계",
            steps=[
                PlanStep(
                    "group_sum",
                    {
                        "group_columns": "Type",
                        "value_column": "Commission",
                        "result_column": "Commission_SUM",
                    },
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "평균"):
            self._validate(request, incomplete)


if __name__ == "__main__":
    unittest.main()
