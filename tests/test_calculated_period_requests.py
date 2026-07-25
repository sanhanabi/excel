import unittest

from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.request_analysis import analyze_request, extract_explicit_date_period
from excel_assistant.validation import (
    PlanValidationError,
    repair_plan_from_explicit_request,
    validate_plan_covers_request,
)


class CalculatedPeriodRequestTests(unittest.TestCase):
    columns = ["날짜", "문구점", "단가", "수량"]

    @staticmethod
    def _correct_plan() -> ExecutionPlan:
        return ExecutionPlan(
            goal="상반기 판매금액 계산",
            steps=[
                PlanStep(
                    "calculate_column",
                    {
                        "result_column": "판매금액",
                        "operator": "multiply",
                        "left_column": "단가",
                        "right_column": "수량",
                    },
                ),
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {
                                "column": "날짜",
                                "operator": ">=",
                                "value": "2020-01-01",
                            },
                            {
                                "column": "날짜",
                                "operator": "<=",
                                "value": "2020-06-30",
                            },
                        ],
                        "logic": "and",
                    },
                ),
            ],
        )

    def test_korean_calculation_and_half_year_route_to_required_functions(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 "
            "2020년 상반기 거래만 남겨줘."
        )

        analysis = analyze_request(request)

        self.assertIn("calculated_column", analysis.action_names)
        self.assertIn("date_range_filter", analysis.action_names)
        self.assertIn("calculate_column", analysis.recommended_functions)
        self.assertIn("filter_by_conditions", analysis.recommended_functions)
        self.assertEqual(
            tuple(item.isoformat() for item in extract_explicit_date_period(request)),
            ("2020-01-01", "2020-06-30"),
        )

    def test_english_calculation_and_half_year_route_like_korean(self):
        request = (
            "Multiply Unit Price by Quantity to create Sales Amount, then keep "
            "only transactions from the first half of 2020."
        )

        analysis = analyze_request(request)

        self.assertIn("calculated_column", analysis.action_names)
        self.assertIn("date_range_filter", analysis.action_names)
        self.assertIn("calculate_column", analysis.recommended_functions)
        self.assertIn("filter_by_conditions", analysis.recommended_functions)

    def test_missing_calculation_is_rejected(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 "
            "2020년 상반기 거래만 남겨줘."
        )
        plan = ExecutionPlan(
            goal="상반기 필터",
            steps=self._correct_plan().steps[1:],
        )

        with self.assertRaisesRegex(PlanValidationError, "산술 계산 열 생성"):
            validate_plan_covers_request(request, plan, self.columns)

    def test_wrong_calculation_operator_is_rejected(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 "
            "2020년 상반기 거래만 남겨줘."
        )
        correct = self._correct_plan()
        wrong_step = PlanStep(
            "calculate_column",
            {
                **correct.steps[0].params,
                "operator": "add",
            },
        )
        plan = ExecutionPlan(
            goal=correct.goal,
            steps=[wrong_step, correct.steps[1]],
        )

        with self.assertRaisesRegex(PlanValidationError, "연산 방식"):
            validate_plan_covers_request(request, plan, self.columns)

    def test_wrong_half_year_boundaries_are_rejected(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 "
            "2020년 상반기 거래만 남겨줘."
        )
        correct = self._correct_plan()
        wrong_filter = PlanStep(
            "filter_by_conditions",
            {
                "conditions": [
                    {"column": "날짜", "operator": ">=", "value": "2020-01-01"},
                    {"column": "날짜", "operator": "<=", "value": "2020-12-31"},
                ],
                "logic": "and",
            },
        )
        plan = ExecutionPlan(
            goal=correct.goal,
            steps=[correct.steps[0], wrong_filter],
        )

        with self.assertRaisesRegex(PlanValidationError, "2020-06-30"):
            validate_plan_covers_request(request, plan, self.columns)

    def test_correct_calculation_and_period_are_accepted(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 "
            "2020년 상반기 거래만 남겨줘."
        )

        validate_plan_covers_request(request, self._correct_plan(), self.columns)

    def test_explicit_period_and_calculated_result_lineage_are_repaired(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고, 2020년 상반기 거래만 "
            "남겨줘. 문구점별 판매금액 합계와 평균, 거래 건수를 구하고 "
            "금액에는 천 단위 콤마를 넣어줘."
        )
        approximate = ExecutionPlan(
            goal="상반기 문구점 집계",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "날짜", "operator": "contains", "value": "2020"},
                ),
                self._correct_plan().steps[0],
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": ["문구점"],
                        "aggregations": [
                            {
                                "column": "문구점",
                                "function": "sum",
                                "result_column": "판매금액_합계",
                            },
                            {
                                "column": "단가",
                                "function": "mean",
                                "result_column": "단가_평균",
                            },
                            {
                                "column": "수량",
                                "function": "count",
                                "result_column": "거래건수",
                            },
                        ],
                    },
                ),
                PlanStep(
                    "format_numbers",
                    {
                        "columns": ["판매금액_합계"],
                        "format": "thousands",
                    },
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            self.columns,
        )

        period_step = repaired.steps[0]
        self.assertEqual(period_step.function, "filter_by_conditions")
        self.assertEqual(
            [item["value"] for item in period_step.params["conditions"]],
            ["2020-01-01", "2020-06-30"],
        )
        aggregation_step = next(
            step for step in repaired.steps if step.function == "group_aggregate"
        )
        by_function = {
            item["function"]: item
            for item in aggregation_step.params["aggregations"]
        }
        self.assertEqual(by_function["sum"]["column"], "판매금액")
        self.assertEqual(by_function["mean"]["column"], "판매금액")
        self.assertEqual(by_function["mean"]["result_column"], "판매금액_평균")
        self.assertIn("판매금액", aggregation_step.description)
        format_step = next(
            step for step in repaired.steps if step.function == "format_numbers"
        )
        self.assertEqual(
            format_step.params["columns"],
            ["판매금액_합계", "판매금액_평균"],
        )
        validate_plan_covers_request(request, repaired, self.columns)

    def test_missing_requested_transaction_count_is_added_to_group_aggregate(self):
        request = (
            "단가와 수량을 곱해서 판매금액을 만들고 문구점별 판매금액 합계와 "
            "평균, 거래 건수를 구해줘. 맨 아래에는 전체 합계 행도 붙여줘."
        )
        approximate = ExecutionPlan(
            goal="문구점별 판매 요약",
            steps=[
                self._correct_plan().steps[0],
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": ["문구점"],
                        "aggregations": [
                            {
                                "column": "문구점",
                                "function": "sum",
                                "result_column": "판매금액_합계",
                            },
                            {
                                "column": "단가",
                                "function": "mean",
                                "result_column": "단가_평균",
                            },
                        ],
                    },
                ),
            ],
        )

        repaired = repair_plan_from_explicit_request(
            request,
            approximate,
            self.columns,
        )
        aggregation = next(
            step for step in repaired.steps if step.function == "group_aggregate"
        )

        self.assertIn(
            {
                "column": "문구점",
                "function": "size",
                "result_column": "판매금액_개수",
            },
            aggregation.params["aggregations"],
        )
        total = next(
            step for step in repaired.steps if step.function == "add_total_row"
        )
        self.assertEqual(total.params["value_columns"], ["판매금액_합계"])
        validate_plan_covers_request(request, repaired, self.columns)


if __name__ == "__main__":
    unittest.main()
