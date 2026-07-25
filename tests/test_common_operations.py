import json
import unittest

import pandas as pd

from excel_assistant import operations
from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.models import ExecutionPlan, PlanStep
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.presentation import format_plan
from excel_assistant.validation import PlanValidationError, validate_plan_against_data


class CommonOperationTests(unittest.TestCase):
    def test_drop_columns_preserves_everything_else(self):
        source = pd.DataFrame({"고객": ["A"], "메모": ["임시"], "금액": [100]})

        result = operations.drop_columns(source, "메모")

        self.assertEqual(result.columns.tolist(), ["고객", "금액"])
        self.assertEqual(source.columns.tolist(), ["고객", "메모", "금액"])
        with self.assertRaisesRegex(ValueError, "모든 열"):
            operations.drop_columns(source, ["고객", "메모", "금액"])

    def test_conditional_column_supports_and_or_conditions(self):
        source = pd.DataFrame(
            {"금액": [50, 150, 200], "상태": ["완료", "완료", "미납"]}
        )
        conditions = [
            {"column": "금액", "operator": ">=", "value": 100},
            {"column": "상태", "operator": "==", "value": "완료"},
        ]

        result = operations.add_conditional_column(
            source,
            result_column="분류",
            conditions=conditions,
            true_value="대상",
            false_value="일반",
            logic="and",
        )

        self.assertEqual(result["분류"].tolist(), ["일반", "대상", "일반"])

    def test_clean_numeric_values_handles_common_excel_text(self):
        source = pd.DataFrame(
            {
                "금액": ["1,200", "₩3,000", "(450)", "USD 20", "5원"],
                "비율": ["12.5%", "100%", None, "0%", "50%"],
            }
        )

        result = operations.clean_numeric_values(source, "금액")
        result = operations.clean_numeric_values(
            result,
            "비율",
            percent_as_fraction=True,
        )

        self.assertEqual(result["금액"].tolist(), [1200, 3000, -450, 20, 5])
        self.assertEqual(result["비율"].dropna().tolist(), [0.125, 1.0, 0.0, 0.5])

    def test_round_numbers_matches_excel_half_away_from_zero(self):
        source = pd.DataFrame({"값": [2.5, -2.5, 149.0, 151.0]})

        rounded = operations.round_numbers(source, "값", decimals=0, mode="round")
        tens = operations.round_numbers(source, "값", decimals=-1, mode="round")

        self.assertEqual(rounded["값"].tolist(), [3.0, -3.0, 149.0, 151.0])
        self.assertEqual(tens["값"].tolist(), [0.0, -0.0, 150.0, 150.0])

    def test_date_difference_supports_days_weeks_and_completed_months(self):
        source = pd.DataFrame(
            {
                "시작": ["2026-01-15", "2026-01-31"],
                "종료": ["2026-02-16", "2026-03-30"],
            }
        )

        days = operations.calculate_date_difference(
            source, "시작", "종료", "일수", unit="days"
        )
        weeks = operations.calculate_date_difference(
            source, "시작", "종료", "주수", unit="weeks"
        )
        months = operations.calculate_date_difference(
            source, "시작", "종료", "개월", unit="months"
        )

        self.assertEqual(days["일수"].tolist(), [32.0, 58.0])
        self.assertAlmostEqual(weeks["주수"].iloc[0], 32 / 7)
        self.assertEqual(months["개월"].tolist(), [1.0, 1.0])

    def test_combine_columns_skips_missing_values(self):
        source = pd.DataFrame(
            {"성": ["김", "박"], "이름": ["민수", None], "코드": [1, 2]}
        )

        result = operations.combine_columns(
            source,
            ["성", "이름", "코드"],
            "고객명",
            separator="-",
        )

        self.assertEqual(result["고객명"].tolist(), ["김-민수-1", "박-2"])

    def test_extract_text_supports_bounded_modes(self):
        source = pd.DataFrame({"코드": ["AB-123-X", "CD-456-Y"]})

        before = operations.extract_text(
            source, "코드", "앞", "before", delimiter="-"
        )
        after = operations.extract_text(
            source, "코드", "뒤", "after", delimiter="-", occurrence="last"
        )
        between = operations.extract_text(
            source,
            "코드",
            "중간",
            "between",
            start_delimiter="-",
            end_delimiter="-",
        )
        left = operations.extract_text(source, "코드", "왼쪽", "left", length=2)

        self.assertEqual(before["앞"].tolist(), ["AB", "CD"])
        self.assertEqual(after["뒤"].tolist(), ["X", "Y"])
        self.assertEqual(between["중간"].tolist(), ["123", "456"])
        self.assertEqual(left["왼쪽"].tolist(), ["AB", "CD"])

    def test_split_column_uses_literal_delimiter_and_preserves_remainder(self):
        source = pd.DataFrame({"코드": ["AB-123-서울", "CD-456-부산-동부", None]})

        result = operations.split_column(
            source,
            "코드",
            ["분류", "번호", "지역"],
            "-",
        )

        self.assertEqual(result["분류"].tolist()[:2], ["AB", "CD"])
        self.assertEqual(result["번호"].tolist()[:2], ["123", "456"])
        self.assertEqual(result["지역"].tolist()[:2], ["서울", "부산-동부"])
        self.assertTrue(pd.isna(result.loc[2, "분류"]))

    def test_replace_text_is_literal_and_can_ignore_case(self):
        source = pd.DataFrame(
            {"회사": ["(주)한빛", "ACME.LTD", "acme.ltd", 100]}
        )

        removed = operations.replace_text(source, "회사", "(주)", "")
        normalized = operations.replace_text(
            removed,
            "회사",
            ".ltd",
            "",
            case_sensitive=False,
        )

        self.assertEqual(normalized["회사"].tolist(), ["한빛", "ACME", "acme", 100])

    def test_relative_date_filter_uses_runtime_today(self):
        today = pd.Timestamp.today().normalize()
        last_month = today.replace(day=1) - pd.Timedelta(1, unit="D")
        source = pd.DataFrame(
            {
                "날짜": [
                    today,
                    today - pd.Timedelta(4, unit="D"),
                    today - pd.Timedelta(31, unit="D"),
                    today - pd.Timedelta(400, unit="D"),
                    last_month,
                ],
                "값": [1, 2, 3, 4, 5],
            }
        )

        recent = operations.filter_relative_dates(
            source, "날짜", "last_n_days", days=5
        )
        overdue = operations.filter_relative_dates(
            source, "날짜", "older_than_n_days", days=30
        )
        previous_month = operations.filter_relative_dates(
            source, "날짜", "last_month"
        )

        self.assertEqual(recent["값"].tolist(), [1, 2])
        self.assertEqual(overdue["값"].tolist(), [3, 4])
        self.assertIn(5, previous_month["값"].tolist())

    def test_keep_latest_per_group_can_keep_or_collapse_ties(self):
        source = pd.DataFrame(
            {
                "고객": ["A", "A", "A", "B"],
                "날짜": ["2026-01-01", "2026-02-01", "2026-02-01", "2026-03-01"],
                "값": [1, 2, 3, 4],
            }
        )

        collapsed = operations.keep_latest_per_group(source, "고객", "날짜")
        tied = operations.keep_latest_per_group(
            source, "고객", "날짜", keep_ties=True
        )

        self.assertEqual(collapsed["값"].tolist(), [3, 4])
        self.assertEqual(tied["값"].tolist(), [2, 3, 4])

    def test_new_functions_validate_execute_and_render_as_one_plan(self):
        source = pd.DataFrame(
            {
                "고객": ["A", "A", "B"],
                "접수일": ["2026-01-01", "2026-02-01", "2026-01-01"],
                "완료일": ["2026-01-05", "2026-02-04", "2026-01-10"],
                "금액": ["1,200", "2,500", "900"],
                "임시": [1, 2, 3],
            }
        )
        plan = ExecutionPlan(
            goal="숫자를 정리하고 분류한 뒤 고객별 최신 기록만 유지",
            steps=[
                PlanStep("clean_numeric_values", {"columns": ["금액"]}),
                PlanStep("round_numbers", {"columns": ["금액"], "decimals": 0}),
                PlanStep(
                    "add_conditional_column",
                    {
                        "result_column": "등급",
                        "conditions": [
                            {"column": "금액", "operator": ">=", "value": 1000}
                        ],
                        "true_value": "고액",
                        "false_value": "일반",
                    },
                ),
                PlanStep(
                    "calculate_date_difference",
                    {
                        "start_column": "접수일",
                        "end_column": "완료일",
                        "result_column": "처리일수",
                    },
                ),
                PlanStep(
                    "combine_columns",
                    {
                        "columns": ["고객", "등급"],
                        "result_column": "고객등급",
                        "separator": "-",
                    },
                ),
                PlanStep(
                    "keep_latest_per_group",
                    {"group_columns": ["고객"], "date_column": "접수일"},
                ),
                PlanStep("drop_columns", {"columns": ["임시"]}),
            ],
        )

        preview = validate_plan_against_data(source, plan)
        message = format_plan(plan, preview)

        self.assertEqual(preview.final_rows, 2)
        self.assertIn("조건 결과 열: 등급", message)
        self.assertIn("최신 기록 기준", message)
        self.assertIn("제거할 열", message)

    def test_new_function_schema_is_closed_and_source_grounded(self):
        schema = OllamaPlanner._plan_schema(
            catalog_for_prompt(),
            source_columns=["고객", "날짜", "금액"],
        )
        serialized = json.dumps(schema, ensure_ascii=False)

        for function_name in (
            "drop_columns",
            "add_conditional_column",
            "clean_numeric_values",
            "round_numbers",
            "calculate_date_difference",
            "combine_columns",
            "extract_text",
            "keep_latest_per_group",
            "split_column",
            "replace_text",
            "filter_relative_dates",
        ):
            self.assertIn(function_name, serialized)
        self.assertIn('"date_column": {"type": "string", "enum":', serialized)

    def test_invalid_extract_contract_is_rejected_before_execution(self):
        source = pd.DataFrame({"코드": ["A-1"]})
        plan = ExecutionPlan(
            goal="잘못된 추출",
            steps=[
                PlanStep(
                    "extract_text",
                    {"column": "코드", "result_column": "결과", "mode": "before"},
                )
            ],
        )

        with self.assertRaisesRegex(PlanValidationError, "구분자가 비어"):
            validate_plan_against_data(source, plan)


if __name__ == "__main__":
    unittest.main()
