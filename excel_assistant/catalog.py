from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from . import operations


@dataclass(frozen=True)
class ColumnLineageRule:
    source_param: str
    result_param: str
    collection_param: str | None = None


@dataclass(frozen=True)
class FunctionSpec:
    function: Callable[..., pd.DataFrame] | None
    description: str
    required_params: tuple[str, ...] = ()
    allowed_params: tuple[str, ...] = ()
    phase: str = "data"
    column_lineage: tuple[ColumnLineageRule, ...] = ()


FUNCTION_CATALOG: dict[str, FunctionSpec] = {
    "remove_empty_rows": FunctionSpec(
        operations.remove_empty_rows,
        "빈 행을 제거합니다.",
        allowed_params=("how",),
    ),
    "remove_duplicates": FunctionSpec(
        operations.remove_duplicates,
        "중복 행을 제거합니다.",
        allowed_params=("subset", "keep"),
    ),
    "drop_rows_missing_keys": FunctionSpec(
        operations.drop_rows_missing_keys,
        "핵심 열의 값 존재 여부로 행을 유지합니다. require=all이면 지정 열이 모두 있는 행만 유지합니다.",
        required_params=("columns",),
        allowed_params=("columns", "require"),
    ),
    "normalize_column_names": FunctionSpec(
        operations.normalize_column_names,
        "서로 다른 열 이름을 공통 열 이름으로 통일합니다.",
        required_params=("mapping",),
        allowed_params=("mapping",),
    ),
    "select_columns": FunctionSpec(
        operations.select_columns,
        "사용자가 명시적으로 다른 열을 버리라고 요청한 경우에만 지정 열만 남깁니다.",
        required_params=("columns",),
        allowed_params=("columns",),
    ),
    "reorder_columns": FunctionSpec(
        operations.reorder_columns,
        "열 순서를 변경합니다.",
        required_params=("columns",),
        allowed_params=("columns", "keep_remaining"),
    ),
    "drop_columns": FunctionSpec(
        operations.drop_columns,
        "사용자가 명시한 불필요한 열만 제거하고 나머지 열은 유지합니다.",
        required_params=("columns",),
        allowed_params=("columns",),
    ),
    "normalize_text": FunctionSpec(
        operations.normalize_text,
        "문자열의 공백·줄바꿈·대소문자를 정리합니다.",
        required_params=("columns",),
        allowed_params=("columns", "strip", "collapse_whitespace", "case"),
    ),
    "fill_missing_values": FunctionSpec(
        operations.fill_missing_values,
        "열별 지정값으로 결측값을 채웁니다.",
        required_params=("values",),
        allowed_params=("values",),
    ),
    "replace_values": FunctionSpec(
        operations.replace_values,
        "지정 열의 값을 대응표에 따라 치환합니다.",
        required_params=("column", "replacements"),
        allowed_params=("column", "replacements"),
    ),
    "convert_column_type": FunctionSpec(
        operations.convert_column_type,
        "열을 숫자·날짜·문자·불리언 자료형으로 변환합니다.",
        required_params=("column", "target_type"),
        allowed_params=("column", "target_type", "errors", "date_format"),
    ),
    "clean_numeric_values": FunctionSpec(
        operations.clean_numeric_values,
        "쉼표·통화기호·괄호 음수·퍼센트가 섞인 열을 계산 가능한 숫자로 정리합니다.",
        required_params=("columns",),
        allowed_params=("columns", "errors", "percent_as_fraction"),
    ),
    "round_numbers": FunctionSpec(
        operations.round_numbers,
        "숫자 열을 지정 자릿수에서 반올림·내림·올림합니다.",
        required_params=("columns",),
        allowed_params=("columns", "decimals", "mode"),
    ),
    "add_conditional_column": FunctionSpec(
        operations.add_conditional_column,
        "평면 조건 또는 (AND 조건 그룹) OR (AND 조건 그룹) 같은 중첩 조건에 따라 새 열을 만듭니다.",
        required_params=(
            "result_column", "true_value", "false_value"
        ),
        allowed_params=(
            "result_column", "conditions", "true_value", "false_value", "logic",
            "condition_groups", "group_logic",
        ),
    ),
    "calculate_date_difference": FunctionSpec(
        operations.calculate_date_difference,
        "두 날짜 열 또는 TODAY와 시작일 사이의 일·주·완료 월 차이를 새 열로 계산합니다.",
        required_params=("start_column", "result_column"),
        allowed_params=(
            "start_column", "end_column", "result_column", "end_mode", "unit",
            "absolute", "as_formula",
        ),
    ),
    "combine_columns": FunctionSpec(
        operations.combine_columns,
        "여러 열의 값을 지정 구분자로 이어 붙여 새 열을 만듭니다.",
        required_params=("columns", "result_column"),
        allowed_params=("columns", "result_column", "separator", "skip_missing"),
    ),
    "extract_text": FunctionSpec(
        operations.extract_text,
        "문자열의 앞·뒤·구분자 이전·이후·사이 부분을 새 열로 추출합니다.",
        required_params=("column", "result_column", "mode"),
        allowed_params=(
            "column", "result_column", "mode", "delimiter", "length",
            "start_delimiter", "end_delimiter", "occurrence",
        ),
    ),
    "split_column": FunctionSpec(
        operations.split_column,
        "한 열을 리터럴 구분자로 나눠 여러 새 열을 만듭니다.",
        required_params=("column", "result_columns", "delimiter"),
        allowed_params=("column", "result_columns", "delimiter", "drop_source"),
    ),
    "replace_text": FunctionSpec(
        operations.replace_text,
        "한 열 안의 지정 문자열을 정규식 없이 안전하게 치환합니다.",
        required_params=("column", "old"),
        allowed_params=("column", "old", "new", "case_sensitive"),
    ),
    "keep_latest_per_group": FunctionSpec(
        operations.keep_latest_per_group,
        "그룹마다 날짜가 가장 최신인 기록만 유지합니다.",
        required_params=("group_columns", "date_column"),
        allowed_params=("group_columns", "date_column", "keep_ties"),
    ),
    "filter_rows": FunctionSpec(
        operations.filter_rows,
        "지정한 조건에 맞는 행만 남깁니다.",
        required_params=("column", "operator", "value"),
        allowed_params=("column", "operator", "value"),
    ),
    "filter_by_conditions": FunctionSpec(
        operations.filter_by_conditions,
        "평면 조건 또는 여러 조건 그룹을 AND/OR로 결합해 행을 필터링합니다.",
        allowed_params=("conditions", "logic", "condition_groups", "group_logic"),
    ),
    "filter_relative_dates": FunctionSpec(
        operations.filter_relative_dates,
        "오늘을 기준으로 최근 N일·지난달·올해 같은 상대 날짜 범위만 남깁니다.",
        required_params=("column", "period"),
        allowed_params=("column", "period", "days"),
    ),
    "sort_rows": FunctionSpec(
        operations.sort_rows,
        "하나 이상의 열을 기준으로 정렬합니다.",
        required_params=("columns",),
        allowed_params=("columns", "ascending"),
    ),
    "group_sum": FunctionSpec(
        operations.group_sum,
        "그룹별 숫자 합계를 계산합니다.",
        required_params=("group_columns", "value_column", "result_column"),
        allowed_params=("group_columns", "value_column", "result_column"),
        column_lineage=(ColumnLineageRule("value_column", "result_column"),),
    ),
    "group_average": FunctionSpec(
        operations.group_average,
        "그룹별 숫자 평균을 계산합니다.",
        required_params=("group_columns", "value_column", "result_column"),
        allowed_params=("group_columns", "value_column", "result_column"),
        column_lineage=(ColumnLineageRule("value_column", "result_column"),),
    ),
    "group_count": FunctionSpec(
        operations.group_count,
        "그룹별 행 개수를 계산합니다.",
        required_params=("group_columns", "result_column"),
        allowed_params=("group_columns", "result_column"),
    ),
    "group_aggregate": FunctionSpec(
        operations.group_aggregate,
        "그룹별로 여러 열의 합계·평균·건수 등을 한 번에 계산합니다.",
        required_params=("group_columns", "aggregations"),
        allowed_params=("group_columns", "aggregations"),
        column_lineage=(
            ColumnLineageRule(
                "column",
                "result_column",
                collection_param="aggregations",
            ),
        ),
    ),
    "pivot_table": FunctionSpec(
        operations.pivot_table,
        "행 기준과 열 기준으로 교차표를 만들고 값을 집계합니다.",
        required_params=("index_columns", "pivot_column", "value_column"),
        allowed_params=(
            "index_columns", "pivot_column", "value_column", "aggfunc", "fill_value"
        ),
    ),
    "select_top_n": FunctionSpec(
        operations.select_top_n,
        "지정 열을 기준으로 상위 또는 하위 N개를 남깁니다.",
        required_params=("column",),
        allowed_params=("column", "n", "largest"),
    ),
    "mark_duplicates": FunctionSpec(
        operations.mark_duplicates,
        "복합 기준 중복 여부를 새 열에 표시합니다.",
        allowed_params=("subset", "result_column", "keep"),
    ),
    "add_date_parts": FunctionSpec(
        operations.add_date_parts,
        "날짜 열에서 연도·분기·월·주·일·요일 열을 만듭니다.",
        required_params=("column", "parts"),
        allowed_params=("column", "parts", "result_prefix"),
    ),
    "calculate_column": FunctionSpec(
        operations.calculate_column,
        "허용된 사칙연산으로 안전하게 계산 열을 만듭니다.",
        required_params=("result_column", "operator", "left_column"),
        allowed_params=(
            "result_column", "operator", "left_column", "right_column", "value",
            "as_formula",
        ),
    ),
    "rank_rows": FunctionSpec(
        operations.rank_rows,
        "전체 또는 그룹 안에서 값의 순위를 계산합니다.",
        required_params=("column",),
        allowed_params=(
            "column", "result_column", "ascending", "method", "group_columns"
        ),
    ),
    "cumulative_sum": FunctionSpec(
        operations.cumulative_sum,
        "전체 또는 그룹별 누계를 계산합니다.",
        required_params=("value_column",),
        allowed_params=(
            "value_column", "result_column", "group_columns", "order_columns"
        ),
    ),
    "percent_change": FunctionSpec(
        operations.percent_change,
        "이전 행 또는 이전 기간 대비 증감률을 계산합니다.",
        required_params=("value_column",),
        allowed_params=(
            "value_column", "result_column", "group_columns", "order_columns", "periods"
        ),
    ),
    "add_subtotals": FunctionSpec(
        operations.add_subtotals,
        "그룹별 소계와 선택적으로 전체 합계를 추가합니다.",
        required_params=("group_columns", "value_columns"),
        allowed_params=(
            "group_columns", "value_columns", "label", "include_grand_total",
            "as_formula",
        ),
    ),
    "mark_error_values": FunctionSpec(
        operations.mark_error_values,
        "엑셀 오류값이 있는 행과 오류 열을 표시합니다.",
        allowed_params=("columns", "result_column", "detail_column"),
    ),
    "mark_missing_required": FunctionSpec(
        operations.mark_missing_required,
        "필수값이 누락된 행과 누락 열을 표시합니다.",
        required_params=("columns",),
        allowed_params=("columns", "result_column", "detail_column"),
    ),
    "compare_columns": FunctionSpec(
        operations.compare_columns,
        "두 숫자 열의 차이와 허용 오차 이내 일치 여부를 계산합니다.",
        required_params=("left_column", "right_column"),
        allowed_params=(
            "left_column", "right_column", "difference_column", "match_column", "tolerance"
        ),
    ),
    "select_visible_rows": FunctionSpec(
        operations.select_visible_rows,
        "원본에서 숨겨지지 않은 행만 남깁니다.",
        allowed_params=("hidden_column",),
    ),
    "highlight_rows": FunctionSpec(
        None,
        "조건에 맞는 행 또는 지정 셀을 허용된 색상으로 강조합니다.",
        required_params=("column", "operator", "value", "color"),
        allowed_params=("column", "operator", "value", "color", "target_columns"),
        phase="output",
    ),
    "highlight_extremes": FunctionSpec(
        None,
        "숫자 열의 최댓값·최솟값·상하위 N개를 강조합니다.",
        required_params=("column", "mode", "color"),
        allowed_params=("column", "mode", "n", "color", "include_ties"),
        phase="output",
    ),
    "highlight_missing": FunctionSpec(
        None,
        "지정 열의 빈 셀을 허용된 색상으로 강조합니다.",
        required_params=("columns", "color"),
        allowed_params=("columns", "color"),
        phase="output",
    ),
    "format_numbers": FunctionSpec(
        None,
        "지정 열에 표시 형식을 적용합니다. thousands는 콤마만, currency는 통화 기호까지 표시합니다.",
        required_params=("columns", "format"),
        allowed_params=("columns", "format"),
        phase="output",
    ),
    "color_scale": FunctionSpec(
        None,
        "숫자 열에 값 변경을 따라가는 조건부 색상 스케일을 적용합니다.",
        required_params=("column", "palette"),
        allowed_params=("column", "palette"),
        phase="output",
    ),
    "add_total_row": FunctionSpec(
        None,
        "결과 아래에 합계·평균·개수·최솟값·최댓값 SUBTOTAL 수식행을 추가합니다.",
        required_params=("value_columns",),
        allowed_params=("value_columns", "label", "aggregate"),
        phase="output",
    ),
    "add_conditional_summary_row": FunctionSpec(
        None,
        "원본 행을 유지하고 조건부 합계·개수·평균 수식행을 아래에 추가합니다.",
        required_params=("aggregate", "output_column"),
        allowed_params=(
            "condition_column", "operator", "value", "conditions", "logic",
            "aggregate", "value_column", "output_column", "label", "label_column",
        ),
        phase="output",
    ),
}


def resolve_column_lineage(
    function_name: str,
    params: dict[str, Any],
) -> dict[str, str]:
    """Resolve consumed-column to produced-column mappings from catalog contracts."""
    spec = FUNCTION_CATALOG.get(function_name)
    if spec is None:
        return {}
    resolved: dict[str, str] = {}
    for rule in spec.column_lineage:
        if rule.collection_param is None:
            items: list[Any] = [params]
        else:
            raw_items = params.get(rule.collection_param)
            items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            source = item.get(rule.source_param)
            result = item.get(rule.result_param)
            if not isinstance(source, str) or not source.strip():
                continue
            if not isinstance(result, str) or not result.strip():
                continue
            if source != result:
                resolved[source] = result
    return resolved


def catalog_for_prompt() -> dict[str, Any]:
    return {
        name: {
            "description": spec.description,
            "required_params": list(spec.required_params),
            "allowed_params": list(spec.allowed_params),
            "phase": spec.phase,
        }
        for name, spec in FUNCTION_CATALOG.items()
    }


def compact_catalog_for_model(
    function_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Remove repeated catalog metadata without removing any function choices."""
    functions: dict[str, Any] = {}
    for name, raw_spec in function_catalog.items():
        spec = dict(raw_spec)
        required = list(spec.get("required_params") or [])
        allowed = list(spec.get("allowed_params") or [])
        optional = [item for item in allowed if item not in required]
        item: list[Any] = [str(spec.get("description", "")), required, optional]
        if spec.get("phase", "data") == "output":
            item.append("output")
        functions[name] = item
    return {
        "legend": ["purpose", "required", "optional", "phase_if_output"],
        "functions": functions,
    }
