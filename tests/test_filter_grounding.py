import json
import unittest
from unittest.mock import patch

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.excel_io import build_profile
from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan, PlanStep, PlanningHints
from excel_assistant.planners.rule_based import RuleBasedPlanner
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.presentation import format_plan
from excel_assistant.validation import (
    PlanValidationError,
    validate_plan_against_data,
)


class FilterGroundingTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "문구점": [
                    "화진아트",
                    "모닝아트",
                    "화진아트",
                    "화진아트",
                    "새싹문구",
                    "화진아트",
                ],
                "품목": ["연필", "크레파스", "리코더", "종합장", "연필", "크레파스"],
                "수량": [46, 62, 77, 57, 72, 92],
            }
        )
        self.requests = [
            "화진아트 기록만 남겨줘",
            "화진아트만 보여줘",
            "문구점이 화진아트인 것만 골라줘",
            "다른 문구점은 빼고 화진아트 자료만 정리해줘",
            "화진아트 거래 내역만 보고 싶어",
        ]

    def test_all_phrasings_ground_to_the_same_real_value(self):
        for request in self.requests:
            with self.subTest(request=request):
                hints = build_planning_hints(self.df, request)
                self.assertEqual(len(hints.matched_values), 1)
                match = hints.matched_values[0]
                self.assertEqual(match.column, "문구점")
                self.assertEqual(match.exact_value, "화진아트")
                self.assertEqual(match.row_count, 4)
                self.assertFalse(
                    any(
                        item["column"] == "문구점"
                        and item["operator"] == "=="
                        and item["value"] == "화진아트"
                        for item in hints.filter_candidates
                    ),
                    "matched_values와 같은 정확 일치 후보는 중복 저장하지 않습니다.",
                )

    def test_grounded_plan_produces_four_rows_for_all_phrasings(self):
        profile = build_profile(self.df, "sample.xlsx", "기본작업-1")
        planner = RuleBasedPlanner()
        for request in self.requests:
            with self.subTest(request=request):
                hints = build_planning_hints(self.df, request)
                plan = planner.create_plan(
                    request,
                    profile,
                    catalog_for_prompt(),
                    planning_hints=hints,
                )
                preview = validate_plan_against_data(self.df, plan, hints)
                self.assertEqual(plan.steps[0].params["column"], "문구점")
                self.assertEqual(plan.steps[0].params["operator"], "==")
                self.assertEqual(plan.steps[0].params["value"], "화진아트")
                self.assertEqual(preview.final_rows, 4)

    def test_wrong_filter_value_is_rejected_before_saving(self):
        request = "화진아트 기록만 남겨줘"
        hints = build_planning_hints(self.df, request)
        wrong_plan = ExecutionPlan(
            goal="화진아트 기록 필터",
            problem_type="filtering",
            steps=[
                PlanStep(
                    "filter_rows",
                    {
                        "column": "문구점",
                        "operator": "==",
                        "value": {"request_text": "화진아트"},
                    },
                )
            ],
        )
        with self.assertRaisesRegex(
            PlanValidationError,
            "필터값은 문자열·숫자 같은 단일 값이어야 합니다",
        ):
            validate_plan_against_data(self.df, wrong_plan, hints)

    def test_zero_row_filter_is_rejected_before_saving(self):
        plan = ExecutionPlan(
            goal="존재하지 않는 값 필터",
            problem_type="filtering",
            steps=[
                PlanStep(
                    "filter_rows",
                    {
                        "column": "문구점",
                        "operator": "contains",
                        "value": "존재하지않음",
                    },
                )
            ],
        )
        with self.assertRaisesRegex(PlanValidationError, "필터 결과가 0행입니다"):
            validate_plan_against_data(self.df, plan)

    def test_null_comparison_must_use_null_operator(self):
        invalid = ExecutionPlan(
            goal="잘못된 빈값 비교",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "문구점", "operator": "==", "value": None},
                )
            ],
        )
        with self.assertRaisesRegex(PlanValidationError, "is_null 또는 not_null"):
            validate_plan_against_data(self.df, invalid)

    def test_repair_plan_normalizes_null_comparisons_recursively(self):
        raw_plan = ExecutionPlan(
            goal="빈 값과 값이 있는 행을 구분",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "문구점", "operator": "==", "value": None},
                ),
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {
                                "column": "품목",
                                "operator": "!=",
                                "value": None,
                            }
                        ]
                    },
                ),
            ],
        )

        repaired = OllamaPlanner._repair_plan(raw_plan, raw_plan.goal, None)

        self.assertEqual(repaired.steps[0].params["operator"], "is_null")
        self.assertEqual(
            repaired.steps[1].params["conditions"][0]["operator"],
            "not_null",
        )

    def test_ollama_schema_allows_general_scalars_but_not_objects(self):
        hints = build_planning_hints(self.df, self.requests[0])
        schema = OllamaPlanner._plan_schema(catalog_for_prompt(), hints)
        variants = schema["properties"]["steps"]["items"]["oneOf"]
        filter_variant = next(
            item
            for item in variants
            if item["properties"]["function"].get("const") == "filter_rows"
        )
        value_schema = filter_variant["properties"]["params"]["properties"]["value"]
        self.assertIn({"type": "string"}, value_schema["anyOf"])
        self.assertIn({"type": "number"}, value_schema["anyOf"])
        self.assertNotIn({"type": "object"}, value_schema["anyOf"])
        self.assertIn(
            "unsupported",
            schema["properties"]["problem_type"]["enum"],
        )
        self.assertNotIn("allOf", schema)
        self.assertEqual(schema["properties"]["steps"]["minItems"], 1)
        self.assertLess(
            list(schema["properties"]).index("steps"),
            list(schema["properties"]).index("problem_type"),
        )
        self.assertEqual(
            schema["properties"]["steps"]["items"]["oneOf"][-1][
                "properties"
            ]["function"]["const"],
            "unsupported_request",
        )

    def test_plan_shape_enforces_supported_and_unsupported_step_counts(self):
        OllamaPlanner._validate_plan_shape(
            ExecutionPlan(
                goal="지원하지 않는 요청",
                problem_type="unsupported",
                steps=[],
            )
        )
        OllamaPlanner._validate_plan_shape(
            ExecutionPlan(
                goal="정렬",
                problem_type="sorting",
                steps=[PlanStep("sort_rows", {"columns": ["수량"]})],
            )
        )
        with self.assertRaisesRegex(ValueError, "실행 단계가 없어야"):
            OllamaPlanner._validate_plan_shape(
                ExecutionPlan(
                    goal="잘못된 거절",
                    problem_type="unsupported",
                    steps=[PlanStep("remove_empty_rows")],
                )
            )
        with self.assertRaisesRegex(ValueError, "하나 이상"):
            OllamaPlanner._validate_plan_shape(
                ExecutionPlan(
                    goal="빈 지원 계획",
                    problem_type="sorting",
                    steps=[],
                )
            )

    def test_internal_unsupported_step_is_removed_before_validation(self):
        normalized = OllamaPlanner._normalize_unsupported_plan(
            ExecutionPlan(
                goal="이메일 전송은 지원하지 않음",
                problem_type="unsupported",
                steps=[PlanStep("unsupported_request", {})],
            )
        )
        self.assertEqual(normalized.steps, [])
        self.assertEqual(normalized.problem_type, "unsupported")
        OllamaPlanner._validate_plan_shape(normalized)

        mislabeled_supported = OllamaPlanner._normalize_unsupported_plan(
            ExecutionPlan(
                goal="합계행 추가",
                problem_type="unsupported",
                steps=[PlanStep("add_total_row", {"value_columns": ["수량"]})],
            )
        )
        self.assertEqual(mislabeled_supported.problem_type, "other")
        self.assertEqual(mislabeled_supported.steps[0].function, "add_total_row")

        mislabeled_rejection = OllamaPlanner._normalize_unsupported_plan(
            ExecutionPlan(
                goal="이메일 전송",
                problem_type="other",
                steps=[PlanStep("unsupported_request", {})],
            )
        )
        self.assertEqual(mislabeled_rejection.problem_type, "unsupported")
        self.assertEqual(mislabeled_rejection.steps, [])

    def test_ollama_schema_restricts_source_columns_to_current_file(self):
        source_columns = ["Date rcvd", "CO", "$ RCVD"]
        hints = build_planning_hints(
            self.df,
            "회사와 받은 날짜가 모두 존재하는 행만 유지",
        )
        schema = OllamaPlanner._plan_schema(
            catalog_for_prompt(),
            hints,
            source_columns=source_columns,
        )
        variants = schema["properties"]["steps"]["items"]["oneOf"]
        missing_keys = next(
            item
            for item in variants
            if item["properties"]["function"].get("const")
            == "drop_rows_missing_keys"
        )
        columns_schema = missing_keys["properties"]["params"]["properties"][
            "columns"
        ]
        string_choice, array_choice = columns_schema["anyOf"]

        self.assertEqual(string_choice["enum"], source_columns)
        self.assertEqual(array_choice["items"]["enum"], source_columns)
        self.assertNotIn("Company", string_choice["enum"])
        self.assertEqual(
            schema["properties"]["column_mapping"]["additionalProperties"][
                "enum"
            ],
            source_columns,
        )

    def test_generated_sort_column_remains_available_for_later_step(self):
        schema = OllamaPlanner._plan_schema(
            catalog_for_prompt(),
            PlanningHints(recommended_functions=["group_sum", "sort_rows"]),
            source_columns=["CO", "$ RCVD"],
        )
        variants = schema["properties"]["steps"]["items"]["oneOf"]
        sort_variant = next(
            item
            for item in variants
            if item["properties"]["function"].get("const") == "sort_rows"
        )
        columns_schema = sort_variant["properties"]["params"]["properties"][
            "columns"
        ]

        self.assertNotIn("enum", columns_schema["anyOf"][0])
        self.assertNotIn("enum", columns_schema["anyOf"][1]["items"])

    def test_sort_column_allows_a_prior_generated_result_name(self):
        schema = OllamaPlanner._plan_schema(
            catalog_for_prompt(),
            source_columns=["CO", "$ RCVD"],
        )
        variants = schema["properties"]["steps"]["items"]["oneOf"]
        sort_variant = next(
            item
            for item in variants
            if item["properties"]["function"].get("const") == "sort_rows"
        )
        columns_schema = sort_variant["properties"]["params"]["properties"][
            "columns"
        ]

        self.assertNotIn("enum", columns_schema["anyOf"][0])
        self.assertNotIn("enum", columns_schema["anyOf"][1]["items"])

    def test_general_string_matching_operators(self):
        cases = [
            ("contains", "아트", 5),
            ("startswith", "화진", 4),
            ("endswith", "아트", 5),
        ]
        for operator, value, expected_rows in cases:
            with self.subTest(operator=operator):
                plan = ExecutionPlan(
                    goal="일반 문자열 필터",
                    problem_type="filtering",
                    steps=[
                        PlanStep(
                            "filter_rows",
                            {
                                "column": "문구점",
                                "operator": operator,
                                "value": value,
                            },
                        )
                    ],
                )
                preview = validate_plan_against_data(self.df, plan)
                self.assertEqual(preview.final_rows, expected_rows)

    def test_filter_fragments_and_row_counts_are_data_derived(self):
        cases = [
            ("화진으로 시작하는 문구점만 남겨줘", "화진", 4),
            ("아트로 끝나는 문구점만 남겨줘", "아트", 5),
            ("이름에 아트가 들어간 문구점만 남겨줘", "아트", 5),
        ]
        for request, expected_value, expected_rows in cases:
            with self.subTest(request=request):
                hints = build_planning_hints(self.df, request)
                self.assertTrue(
                    any(
                        item["column"] == "문구점"
                        and item["value"] == expected_value
                        and item["expected_rows"] == expected_rows
                        for item in hints.filter_candidates
                    )
                )

    def test_grounding_routes_only_request_relevant_function_families(self):
        cases = [
            (
                "Date rcvd와 CO가 모두 존재하는 행만 유지",
                {"filter_rows", "filter_by_conditions", "drop_rows_missing_keys"},
            ),
            ("받은 금액이 큰 것부터 정리해줘", {"sort_rows"}),
            (
                "문구점별 수량 합계를 구해줘",
                {"group_aggregate", "group_sum", "group_average", "group_count"},
            ),
            (
                "빈 칸을 빨간색으로 강조해줘",
                {"highlight_rows", "highlight_extremes", "highlight_missing", "color_scale"},
            ),
        ]
        for request, expected in cases:
            with self.subTest(request=request):
                hints = build_planning_hints(self.df, request)
                self.assertEqual(set(hints.recommended_functions), expected)
                self.assertLessEqual(len(hints.recommended_functions), 12)

    def test_ollama_retries_invalid_plan_once_then_uses_safe_error(self):
        class InvalidResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {"message": {"content": "not valid json"}}
                ).encode("utf-8")

        planner = OllamaPlanner(model="test-model", context_length=8192)
        profile = build_profile(self.df, "sample.xlsx", "기본작업-1")
        hints = build_planning_hints(
            self.df,
            "문구점과 품목 값이 모두 존재하는 행만 유지",
        )

        with patch(
            "excel_assistant.planners.ollama.urlopen",
            side_effect=[InvalidResponse(), InvalidResponse()],
        ) as mocked_urlopen:
            with self.assertRaisesRegex(
                ValueError,
                "엑셀 파일은 변경되지 않았습니다",
            ):
                planner.create_plan(
                    "문구점과 품목 값이 모두 존재하는 행만 유지",
                    profile,
                    catalog_for_prompt(),
                    planning_hints=hints,
                )

        self.assertEqual(mocked_urlopen.call_count, 2)

    def test_ollama_rechecks_tentative_unsupported_once(self):
        class PlanResponse:
            def __init__(self, plan):
                self.plan = plan

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "message": {
                            "content": json.dumps(self.plan, ensure_ascii=False)
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        unsupported = {
            "problem_type": "unsupported",
            "goal": "지원하지 않음",
            "column_mapping": {},
            "assumptions": [],
            "steps": [
                {
                    "function": "unsupported_request",
                    "params": {},
                    "description": "실행 함수가 없습니다.",
                }
            ],
        }
        supported = {
            "problem_type": "sorting",
            "goal": "수량 정렬",
            "column_mapping": {},
            "assumptions": [],
            "steps": [
                {
                    "function": "sort_rows",
                    "params": {"columns": ["수량"], "ascending": True},
                    "description": "수량을 오름차순으로 정렬합니다.",
                }
            ],
        }
        planner = OllamaPlanner(model="test-model", context_length=8192)
        profile = build_profile(self.df, "sample.xlsx", "기본작업-1")
        with patch(
            "excel_assistant.planners.ollama.urlopen",
            side_effect=[PlanResponse(unsupported), PlanResponse(supported)],
        ) as mocked_urlopen:
            plan = planner.create_plan(
                "수량을 정렬해줘",
                profile,
                catalog_for_prompt(),
            )

        self.assertEqual(plan.problem_type, "sorting")
        self.assertEqual(mocked_urlopen.call_count, 2)
        second_payload = json.loads(mocked_urlopen.call_args_list[1].args[0].data)
        self.assertIn(
            "Previous response classified the request as unsupported",
            second_payload["messages"][0]["content"],
        )

    def test_ollama_confirms_real_unsupported_after_one_recheck(self):
        class UnsupportedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                plan = {
                    "problem_type": "unsupported",
                    "goal": "이메일 전송은 지원하지 않음",
                    "column_mapping": {},
                    "assumptions": [],
                    "steps": [
                        {
                            "function": "unsupported_request",
                            "params": {},
                            "description": "실행 함수가 없습니다.",
                        }
                    ],
                }
                return json.dumps(
                    {"message": {"content": json.dumps(plan, ensure_ascii=False)}},
                    ensure_ascii=False,
                ).encode("utf-8")

        planner = OllamaPlanner(model="test-model", context_length=8192)
        profile = build_profile(self.df, "sample.xlsx", "기본작업-1")
        with patch(
            "excel_assistant.planners.ollama.urlopen",
            side_effect=[UnsupportedResponse(), UnsupportedResponse()],
        ) as mocked_urlopen:
            plan = planner.create_plan(
                "담당자에게 이메일을 보내줘",
                profile,
                catalog_for_prompt(),
            )

        self.assertEqual(plan.problem_type, "unsupported")
        self.assertEqual(plan.steps, [])
        self.assertEqual(mocked_urlopen.call_count, 2)

    def test_confirmation_shows_exact_condition_and_row_count(self):
        hints = build_planning_hints(self.df, self.requests[0])
        plan = ExecutionPlan(
            goal="화진아트 기록만 유지",
            problem_type="filtering",
            steps=[
                PlanStep(
                    "filter_rows",
                    {"column": "문구점", "operator": "==", "value": "화진아트"},
                    "화진아트 기록만 남깁니다.",
                )
            ],
        )
        preview = validate_plan_against_data(self.df, plan, hints)
        message = format_plan(plan, preview)
        self.assertIn("실제 조건: 문구점", message)
        self.assertIn("'화진아트'", message)
        self.assertIn("6행 → 4행", message)
        self.assertIn("4행을 남깁니다", message)

    def test_confirmation_explains_missing_key_direction_from_parameters(self):
        frame = pd.DataFrame(
            {
                "Date rcvd": ["2026-01-01", None, "2026-01-03"],
                "COMPANY": ["A", "B", None],
            }
        )
        plan = ExecutionPlan(
            goal="날짜와 회사가 모두 있는 행만 유지",
            steps=[
                PlanStep(
                    "drop_rows_missing_keys",
                    {
                        "columns": ["Date rcvd", "COMPANY"],
                        "require": "all",
                    },
                    "빈 행을 정리합니다.",
                )
            ],
        )

        preview = validate_plan_against_data(frame, plan)
        message = format_plan(plan, preview)

        self.assertIn(
            "Date rcvd, COMPANY 열이 모두 비어 있지 않은 1행을 남깁니다",
            message,
        )

    def test_confirmation_explains_combined_filter_result(self):
        frame = pd.DataFrame(
            {
                "상태": ["완료", "미납", "미납"],
                "금액": [100, 200, 50],
            }
        )
        plan = ExecutionPlan(
            goal="미납 중 고액만 유지",
            steps=[
                PlanStep(
                    "filter_by_conditions",
                    {
                        "conditions": [
                            {"column": "상태", "operator": "==", "value": "미납"},
                            {"column": "금액", "operator": ">=", "value": 100},
                        ],
                        "logic": "and",
                    },
                )
            ],
        )

        preview = validate_plan_against_data(frame, plan)
        message = format_plan(plan, preview)

        self.assertIn("조건 결합: 모두 만족(AND)", message)
        self.assertIn("1행을 남깁니다", message)


if __name__ == "__main__":
    unittest.main()
