import unittest
import json

import pandas as pd

from excel_assistant import operations
from excel_assistant.catalog import catalog_for_prompt, resolve_column_lineage
from excel_assistant.models import ExecutionPlan, PlanStep, PlanningHints
from excel_assistant.planners.ollama import OllamaPlanner
from excel_assistant.validation import validate_plan_against_data


class AdvancedOperationTests(unittest.TestCase):
    def test_cleaning_schema_and_type_operations(self):
        source = pd.DataFrame(
            {
                "CO": [" A  회사 ", None, ""],
                "COMPANY": [None, "B회사", None],
                "금액문자": ["100", "200", None],
                "비고": [None, " ok\ntext ", None],
            }
        )
        result = operations.normalize_column_names(source, {"CO": "COMPANY"})
        result = operations.drop_rows_missing_keys(result, "COMPANY", require="any")
        result = operations.normalize_text(result, ["COMPANY", "비고"])
        result = operations.fill_missing_values(result, {"비고": "없음"})
        result = operations.convert_column_type(
            result, "금액문자", "number", errors="coerce"
        )
        result = operations.reorder_columns(
            result, ["COMPANY", "금액문자"], keep_remaining=True
        )

        self.assertEqual(result.columns.tolist(), ["COMPANY", "금액문자", "비고"])
        self.assertEqual(result["COMPANY"].tolist(), ["A 회사", "B회사"])
        self.assertEqual(result["비고"].tolist(), ["없음", "ok text"])
        self.assertEqual(result["금액문자"].tolist(), [100, 200])
        self.assertEqual(
            operations.select_columns(result, ["COMPANY"]).columns.tolist(),
            ["COMPANY"],
        )

    def test_extended_single_and_multiple_filters(self):
        source = pd.DataFrame(
            {
                "금액": [10, 20, 30, 40],
                "지역": ["서울", "부산", "서울", None],
            }
        )
        between = operations.filter_rows(source, "금액", "between", [15, 35])
        self.assertEqual(between["금액"].tolist(), [20, 30])
        in_list = operations.filter_rows(source, "지역", "in", ["서울", "부산"])
        self.assertEqual(len(in_list), 3)
        missing = operations.filter_rows(source, "지역", "is_null", None)
        self.assertEqual(missing["금액"].tolist(), [40])
        combined = operations.filter_by_conditions(
            source,
            [
                {"column": "지역", "operator": "==", "value": "서울"},
                {"column": "금액", "operator": ">", "value": 15},
            ],
            logic="and",
        )
        self.assertEqual(combined["금액"].tolist(), [30])

    def test_multi_aggregation_and_multi_value_pivot(self):
        source = pd.DataFrame(
            {
                "회사": ["A", "A", "B"],
                "월": [1, 2, 1],
                "매출": [100, 200, 50],
                "수수료": [10, 20, 5],
            }
        )
        grouped = operations.group_aggregate(
            source,
            "회사",
            [
                {"column": "매출", "function": "sum", "result_column": "매출합계"},
                {"column": "수수료", "function": "sum", "result_column": "수수료합계"},
                {"column": "월", "function": "count", "result_column": "건수"},
            ],
        )
        self.assertEqual(grouped.loc[grouped["회사"] == "A", "매출합계"].item(), 300)
        self.assertEqual(grouped.loc[grouped["회사"] == "A", "건수"].item(), 2)

        pivot = operations.pivot_table(
            source,
            index_columns="회사",
            pivot_column="월",
            value_column=["매출", "수수료"],
            aggfunc="sum",
        )
        self.assertEqual(len(pivot), 2)
        self.assertTrue(any("매출" in column for column in pivot.columns))
        self.assertTrue(any("수수료" in column for column in pivot.columns))

    def test_date_calculation_rank_cumulative_and_percent_change(self):
        source = pd.DataFrame(
            {
                "회사": ["A", "A", "B"],
                "날짜": ["2026-01-01", "2026-02-01", "2026-01-01"],
                "총액": [100, 150, 80],
                "수수료": [10, 15, 8],
            }
        )
        result = operations.add_date_parts(source, "날짜", ["year", "month"])
        result = operations.calculate_column(
            result,
            result_column="순액",
            operator="subtract",
            left_column="총액",
            right_column="수수료",
        )
        result = operations.rank_rows(result, "순액", result_column="순위")
        result = operations.cumulative_sum(
            result,
            "순액",
            result_column="회사누계",
            group_columns="회사",
            order_columns="날짜",
        )
        result = operations.percent_change(
            result,
            "순액",
            result_column="증감률",
            group_columns="회사",
            order_columns="날짜",
        )
        self.assertIn("날짜_연도", result.columns)
        self.assertIn("날짜_월", result.columns)
        self.assertEqual(result.loc[result["총액"] == 150, "순액"].item(), 135)
        self.assertEqual(result.loc[result["총액"] == 150, "회사누계"].item(), 225)
        self.assertAlmostEqual(
            result.loc[result["총액"] == 150, "증감률"].item(), 50.0
        )

    def test_duplicate_error_missing_compare_and_subtotals(self):
        source = pd.DataFrame(
            {
                "거래ID": [1, 1, 2],
                "회사": ["A", "A", "B"],
                "장부금액": [100, 100, 50],
                "계산금액": [100, 99.5, 40],
                "상태": ["정상", "#VALUE!", None],
            }
        )
        result = operations.mark_duplicates(source, ["거래ID"], result_column="중복")
        result = operations.mark_error_values(result, "상태")
        result = operations.mark_missing_required(result, ["회사", "상태"])
        result = operations.compare_columns(
            result,
            "장부금액",
            "계산금액",
            tolerance=1,
        )
        self.assertEqual(result["중복"].tolist(), [True, True, False])
        self.assertEqual(result["오류여부"].tolist(), [False, True, False])
        self.assertEqual(result["필수값누락"].tolist(), [False, False, True])
        self.assertEqual(result["일치여부"].tolist(), [True, True, False])

        subtotals = operations.add_subtotals(
            source, "회사", ["장부금액"], include_grand_total=True
        )
        self.assertEqual(subtotals.iloc[-1]["회사"], "전체 합계")
        self.assertEqual(subtotals.iloc[-1]["장부금액"], 250)

    def test_complex_plan_is_validated_and_previewed(self):
        source = pd.DataFrame(
            {
                "회사": ["A", "A", "B"],
                "매출": [100, 200, 50],
                "수수료": [10, 20, 5],
            }
        )
        plan = ExecutionPlan(
            goal="회사별 매출과 수수료를 집계하고 큰 순서로 정렬",
            steps=[
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": "회사",
                        "aggregations": [
                            {"column": "매출", "function": "sum", "result_column": "매출합계"},
                            {"column": "수수료", "function": "sum", "result_column": "수수료합계"},
                        ],
                    },
                ),
                PlanStep("sort_rows", {"columns": "매출합계", "ascending": False}),
            ],
        )
        preview = validate_plan_against_data(source, plan)
        self.assertEqual(preview.initial_rows, 3)
        self.assertEqual(preview.final_rows, 2)

    def test_small_model_split_aggregations_are_merged_without_keyword_routing(self):
        raw_plan = ExecutionPlan(
            goal="회사별 매출과 수수료를 합쳐 매출이 큰 순서로 정리",
            steps=[
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": "회사",
                        "aggregations": [
                            {"column": "매출", "function": "sum", "result_column": "매출합계"}
                        ],
                    },
                ),
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": "회사",
                        "aggregations": [
                            {"column": "수수료", "function": "sum", "result_column": "수수료합계"}
                        ],
                    },
                ),
            ],
        )
        repaired = OllamaPlanner._repair_plan(
            raw_plan,
            "회사별 매출과 수수료를 합쳐 매출이 큰 순서로 정리",
            PlanningHints(recommended_functions=["group_aggregate", "sort_rows"]),
        )
        self.assertEqual([step.function for step in repaired.steps], ["group_aggregate"])
        self.assertEqual(len(repaired.steps[0].params["aggregations"]), 2)

    def test_catalog_lineage_repairs_consumed_columns_in_later_steps(self):
        self.assertEqual(
            resolve_column_lineage(
                "group_sum",
                {
                    "group_columns": ["Month"],
                    "value_column": "NET",
                    "result_column": "NET_SUM",
                },
            ),
            {"NET": "NET_SUM"},
        )
        raw_plan = ExecutionPlan(
            goal="월별 NET 합계를 구하고 정렬·서식·합계행 적용",
            steps=[
                PlanStep(
                    "group_sum",
                    {
                        "group_columns": ["Month"],
                        "value_column": "NET",
                        "result_column": "NET_SUM",
                    },
                ),
                PlanStep("sort_rows", {"columns": ["NET"], "ascending": False}),
                PlanStep("format_numbers", {"columns": ["NET"], "format": "thousands"}),
                PlanStep("add_total_row", {"value_columns": ["NET"], "label": "합계"}),
            ],
        )

        repaired = OllamaPlanner._repair_plan(raw_plan, raw_plan.goal, None)

        self.assertEqual(repaired.steps[1].params["columns"], ["NET_SUM"])
        self.assertEqual(repaired.steps[2].params["columns"], ["NET_SUM"])
        self.assertEqual(repaired.steps[3].params["value_columns"], ["NET_SUM"])

    def test_group_aggregate_lineage_supports_multiple_results(self):
        raw_plan = ExecutionPlan(
            goal="지역별 매출과 수수료를 집계한 뒤 결과열로 정렬",
            steps=[
                PlanStep(
                    "group_aggregate",
                    {
                        "group_columns": ["Region"],
                        "aggregations": [
                            {
                                "column": "Sales",
                                "function": "sum",
                                "result_column": "Sales_SUM",
                            },
                            {
                                "column": "Fee",
                                "function": "mean",
                                "result_column": "Fee_AVG",
                            },
                        ],
                    },
                ),
                PlanStep("sort_rows", {"columns": ["Sales", "Fee"]}),
            ],
        )

        repaired = OllamaPlanner._repair_plan(raw_plan, raw_plan.goal, None)

        self.assertEqual(
            repaired.steps[1].params["columns"],
            ["Sales_SUM", "Fee_AVG"],
        )

    def test_subtotals_are_rejected_when_groups_approach_one_per_row(self):
        source = pd.DataFrame(
            {
                "Account": ["A", "B", "C", "D"],
                "NET": [10, 20, 30, 40],
            }
        )
        plan = ExecutionPlan(
            goal="계정별 소계",
            steps=[
                PlanStep(
                    "add_subtotals",
                    {
                        "group_columns": ["Account"],
                        "value_columns": ["NET"],
                    },
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "절반을 초과"):
            validate_plan_against_data(source, plan)

    def test_full_ollama_function_schema_is_json_serializable(self):
        schema = OllamaPlanner._plan_schema(catalog_for_prompt())
        serialized = json.dumps(schema)
        self.assertIn("group_aggregate", serialized)
        self.assertIn("calculate_column", serialized)
        self.assertIn("filter_by_conditions", serialized)


if __name__ == "__main__":
    unittest.main()
