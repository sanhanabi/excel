from __future__ import annotations

import json
import unittest

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.excel_io import build_profile
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.planning import (
    UnsupportedRequestError,
    _compact_error,
    create_validated_plan,
)
from excel_assistant.planners.base import Planner
from excel_assistant.validation import PlanValidationError


class ProfileStatisticsTests(unittest.TestCase):
    def test_profile_contains_compact_json_safe_statistics(self):
        frame = pd.DataFrame(
            {
                "금액": [10.0, -5.0, 0.0, None],
                "날짜": pd.to_datetime(
                    ["2026-01-01", "2026-03-15", None, "2026-02-10"]
                ),
                "구분": ["A", "A", "B", None],
                "숫자문자": ["10", "20", "bad", None],
            }
        )

        profile = build_profile(frame, "sample.xlsx", "거래")
        by_name = {item.name: item for item in profile.columns}

        self.assertEqual(by_name["금액"].statistics["min"], -5.0)
        self.assertEqual(by_name["금액"].statistics["max"], 10.0)
        self.assertEqual(by_name["금액"].statistics["sum"], 5.0)
        self.assertEqual(by_name["금액"].statistics["zero_count"], 1)
        self.assertEqual(by_name["금액"].statistics["negative_count"], 1)
        self.assertEqual(
            by_name["날짜"].statistics["min_date"],
            "2026-01-01T00:00:00",
        )
        self.assertEqual(
            by_name["날짜"].statistics["max_date"],
            "2026-03-15T00:00:00",
        )
        self.assertEqual(
            by_name["구분"].statistics["top_values"][0],
            {"value": "A", "count": 2},
        )
        self.assertEqual(
            by_name["숫자문자"].statistics["numeric_parse_ratio"],
            0.6667,
        )
        json.dumps(profile.to_prompt_dict(), ensure_ascii=False, allow_nan=False)

    def test_high_cardinality_text_omits_unhelpful_top_values(self):
        frame = pd.DataFrame({"식별자": [f"ID-{number}" for number in range(30)]})

        profile = build_profile(frame, "sample.xlsx", "거래")

        statistics = profile.columns[0].statistics
        self.assertEqual(statistics["unique_ratio"], 1.0)
        self.assertNotIn("top_values", statistics)


class SequencedPlanner(Planner):
    def __init__(self, plans):
        self.plans = list(plans)
        self.feedback = []
        self.call_count = 0

    def create_plan(
        self,
        user_request,
        workbook_profile,
        function_catalog,
        planning_hints=None,
        correction_feedback=None,
    ):
        self.call_count += 1
        self.feedback.append(correction_feedback)
        return self.plans.pop(0)


class SemanticRetryTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"금액": [30, 10, 20]})
        self.profile = build_profile(self.frame, "sample.xlsx", "거래")
        self.invalid_plan = ExecutionPlan(
            goal="금액 정렬",
            steps=[PlanStep("sort_rows", {"columns": "없는열", "ascending": True})],
        )
        self.valid_plan = ExecutionPlan(
            goal="금액 정렬",
            steps=[PlanStep("sort_rows", {"columns": "금액", "ascending": True})],
        )

    def test_semantic_validation_failure_is_replanned_once(self):
        planner = SequencedPlanner([self.invalid_plan, self.valid_plan])

        result = create_validated_plan(
            planner=planner,
            user_request="싼 값부터 정렬해",
            workbook_profile=self.profile,
            function_catalog=catalog_for_prompt(),
            source_df=self.frame,
        )

        self.assertEqual(planner.call_count, 2)
        self.assertIsNone(planner.feedback[0])
        self.assertIn("없는열", planner.feedback[1].validation_error)
        self.assertEqual(
            planner.feedback[1].failed_plan[0]["params"]["columns"],
            "없는열",
        )
        self.assertEqual(set(planner.feedback[1].failed_plan[0]), {"function", "params"})
        self.assertTrue(result.used_semantic_retry)
        self.assertEqual(result.plan.steps[0].params["columns"], "금액")
        self.assertEqual(result.preview.final_rows, 3)

    def test_second_invalid_plan_stops_without_execution(self):
        planner = SequencedPlanner([self.invalid_plan, self.invalid_plan])

        with self.assertRaisesRegex(
            PlanValidationError,
            "원본 파일은 변경되지 않았습니다",
        ):
            create_validated_plan(
                planner=planner,
                user_request="싼 값부터 정렬해",
                workbook_profile=self.profile,
                function_catalog=catalog_for_prompt(),
                source_df=self.frame,
            )

        self.assertEqual(planner.call_count, 2)

    def test_semantic_retry_cannot_silently_delete_later_steps(self):
        first_plan = ExecutionPlan(
            goal="정렬하고 숫자 형식을 적용",
            steps=[
                PlanStep("sort_rows", {"columns": "없는열", "ascending": True}),
                PlanStep(
                    "format_numbers",
                    {"columns": ["금액"], "format": "thousands"},
                ),
            ],
        )
        reduced_plan = ExecutionPlan(
            goal="정렬",
            steps=[PlanStep("sort_rows", {"columns": "금액", "ascending": True})],
        )
        planner = SequencedPlanner([first_plan, reduced_plan])

        with self.assertRaisesRegex(PlanValidationError, "표시 형식 적용"):
            create_validated_plan(
                planner=planner,
                user_request="금액을 정렬하고 천 단위 콤마를 적용해줘",
                workbook_profile=self.profile,
                function_catalog=catalog_for_prompt(),
                source_df=self.frame,
            )

        self.assertEqual(planner.call_count, 2)

    def test_non_validation_error_is_not_retried(self):
        class BrokenPlanner(Planner):
            def __init__(self):
                self.call_count = 0

            def create_plan(
                self,
                user_request,
                workbook_profile,
                function_catalog,
                planning_hints=None,
                correction_feedback=None,
            ):
                self.call_count += 1
                raise RuntimeError("model unavailable")

        planner = BrokenPlanner()
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            create_validated_plan(
                planner=planner,
                user_request="정렬해",
                workbook_profile=self.profile,
                function_catalog=catalog_for_prompt(),
                source_df=self.frame,
            )
        self.assertEqual(planner.call_count, 1)

    def test_unsupported_plan_is_not_retried_or_validated(self):
        planner = SequencedPlanner(
            [
                ExecutionPlan(
                    goal="이메일을 보낼 수 없습니다.",
                    problem_type="unsupported",
                    steps=[],
                )
            ]
        )

        with self.assertRaisesRegex(
            UnsupportedRequestError,
            "엑셀 표 가공 범위를 벗어납니다",
        ):
            create_validated_plan(
                planner=planner,
                user_request="거래처에 이메일 보내줘",
                workbook_profile=self.profile,
                function_catalog=catalog_for_prompt(),
                source_df=self.frame,
            )

        self.assertEqual(planner.call_count, 1)

    def test_retry_error_is_capped_at_200_characters(self):
        compact = _compact_error(ValueError("오류 " * 500))
        self.assertLessEqual(len(compact), 200)
        self.assertTrue(compact.endswith("…"))


if __name__ == "__main__":
    unittest.main()
