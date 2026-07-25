from __future__ import annotations

from .catalog import FUNCTION_CATALOG
from .models import ExecutionPlan, PlanPreview


def format_plan(plan: ExecutionPlan, preview: PlanPreview | None = None) -> str:
    lines = [
        "요청을 다음과 같이 이해했습니다.",
        "",
        f"문제 유형: {plan.problem_type}",
        f"목표: {plan.goal}",
    ]
    if plan.column_mapping:
        lines.extend(["", "열 연결:"])
        lines.extend(
            f"- {user_term} → {actual_column}"
            for user_term, actual_column in plan.column_mapping.items()
        )
    if plan.assumptions:
        lines.extend(["", "가정 및 안내:"])
        lines.extend(f"- {item}" for item in plan.assumptions)
    lines.extend(["", "실행할 작업:"])
    for index, step in enumerate(plan.steps, start=1):
        preview_item = (
            preview.steps[index - 1]
            if preview and index <= len(preview.steps)
            else None
        )
        description = step.description or FUNCTION_CATALOG[step.function].description
        lines.append(f"{index}. {description}")
        if step.function == "filter_rows":
            operator_label = {
                "==": "같은",
                "!=": "같지 않은",
                ">": "초과인",
                ">=": "이상인",
                "<": "미만인",
                "<=": "이하인",
                "contains": "포함하는",
                "startswith": "시작하는",
                "endswith": "끝나는",
                "is_null": "비어 있는",
                "not_null": "비어 있지 않은",
                "between": "범위 안에 있는",
                "in": "목록에 포함되는",
                "not_in": "목록에 포함되지 않는",
            }.get(str(step.params.get("operator")), str(step.params.get("operator")))
            value = step.params.get("value")
            if step.params.get("operator") in {"is_null", "not_null"}:
                condition_text = (
                    f"{step.params.get('column')}이(가) {operator_label} 행"
                )
            else:
                condition_text = (
                    f"{step.params.get('column')}이(가) {value!r}와(과) "
                    f"{operator_label} 행"
                )
            lines.append(f"   실제 조건: {condition_text}")
            if preview_item:
                lines.append(
                    f"   실제 동작: 위 조건을 만족하는 "
                    f"{preview_item.after_rows:,}행을 남깁니다."
                )
        if step.function == "filter_by_conditions":
            logic_label = "모두 만족(AND)" if step.params.get("logic", "and") == "and" else "하나 이상 만족(OR)"
            lines.append(f"   조건 결합: {logic_label}")
            for condition in step.params.get("conditions", []):
                lines.append(
                    f"   - {condition.get('column')} {condition.get('operator')} "
                    f"{condition.get('value')!r}"
                )
            if preview_item:
                lines.append(
                    f"   실제 동작: 위 조건을 {logic_label}하는 "
                    f"{preview_item.after_rows:,}행을 남깁니다."
                )
        if step.function == "filter_relative_dates":
            lines.append(
                f"   상대 날짜 필터: {step.params.get('column')} | "
                f"범위: {step.params.get('period')} | "
                f"일수: {step.params.get('days')}"
            )
            if preview_item:
                lines.append(
                    f"   실제 동작: 현재 날짜 기준 "
                    f"{preview_item.after_rows:,}행을 남깁니다."
                )
        if step.function == "drop_rows_missing_keys":
            columns = step.params.get("columns")
            column_list = [columns] if isinstance(columns, str) else list(columns or [])
            column_label = ", ".join(str(column) for column in column_list)
            require = step.params.get("require", "any")
            presence_label = (
                "모두 비어 있지 않은"
                if require == "all"
                else "중 하나 이상이 비어 있지 않은"
            )
            count_label = (
                f" {preview_item.after_rows:,}행"
                if preview_item
                else " 행"
            )
            lines.append(
                f"   실제 동작: {column_label} 열이 {presence_label}"
                f"{count_label}을 남깁니다."
            )
        if step.function == "drop_columns":
            lines.append(f"   제거할 열: {step.params.get('columns')}")
        if step.function == "clean_numeric_values":
            lines.append(
                f"   숫자로 정리할 열: {step.params.get('columns')} | "
                f"퍼센트 분수 변환: {step.params.get('percent_as_fraction', False)}"
            )
        if step.function == "round_numbers":
            lines.append(
                f"   숫자 처리: {step.params.get('columns')} | "
                f"{step.params.get('mode', 'round')} | "
                f"소수 자릿수 {step.params.get('decimals', 0)}"
            )
        if step.function == "add_conditional_column":
            lines.append(
                f"   조건 결과 열: {step.params.get('result_column')} | "
                f"참={step.params.get('true_value')!r}, "
                f"거짓={step.params.get('false_value')!r}"
            )
            for condition in step.params.get("conditions", []):
                lines.append(
                    f"   - {condition.get('column')} {condition.get('operator')} "
                    f"{condition.get('value')!r}"
                )
        if step.function == "calculate_date_difference":
            end_label = (
                "오늘(TODAY)"
                if step.params.get("end_mode", "column") == "today"
                else step.params.get("end_column")
            )
            lines.append(
                f"   날짜 차이: {step.params.get('start_column')} → "
                f"{end_label} | 단위: "
                f"{step.params.get('unit', 'days')} | 결과: "
                f"{step.params.get('result_column')}"
            )
            if step.params.get("as_formula"):
                lines.append("   저장 방식: Excel 날짜 수식으로 기록")
        if step.function == "combine_columns":
            lines.append(
                f"   열 결합: {step.params.get('columns')} → "
                f"{step.params.get('result_column')} | 구분자: "
                f"{step.params.get('separator', ' ')!r}"
            )
        if step.function == "extract_text":
            lines.append(
                f"   문자열 추출: {step.params.get('column')} | "
                f"방식: {step.params.get('mode')} → "
                f"{step.params.get('result_column')}"
            )
        if step.function == "split_column":
            lines.append(
                f"   열 분리: {step.params.get('column')} → "
                f"{step.params.get('result_columns')} | "
                f"구분자: {step.params.get('delimiter')!r}"
            )
        if step.function == "replace_text":
            lines.append(
                f"   부분 치환: {step.params.get('column')}에서 "
                f"{step.params.get('old')!r} → {step.params.get('new', '')!r}"
            )
        if step.function == "keep_latest_per_group":
            lines.append(
                f"   최신 기록 기준: 그룹={step.params.get('group_columns')} | "
                f"날짜={step.params.get('date_column')} | "
                f"동일 날짜 모두 유지={step.params.get('keep_ties', False)}"
            )
        if step.function == "pivot_table":
            aggregation_label = {
                "sum": "합계",
                "mean": "평균",
                "count": "개수",
                "min": "최솟값",
                "max": "최댓값",
            }.get(str(step.params.get("aggfunc", "sum")), str(step.params.get("aggfunc")))
            indexes = step.params.get("index_columns")
            index_label = ", ".join(indexes) if isinstance(indexes, list) else indexes
            lines.append(f"   행 기준: {index_label}")
            lines.append(f"   열 기준: {step.params.get('pivot_column')}")
            lines.append(
                f"   값: {step.params.get('value_column')} ({aggregation_label})"
            )
        if step.function == "group_aggregate":
            groups = step.params.get("group_columns")
            group_label = ", ".join(groups) if isinstance(groups, list) else groups
            lines.append(f"   그룹 기준: {group_label}")
            for item in step.params.get("aggregations", []):
                lines.append(
                    f"   - {item.get('column')} → {item.get('result_column')} "
                    f"({item.get('function')})"
                )
        if step.function == "normalize_column_names":
            for old, new in step.params.get("mapping", {}).items():
                lines.append(f"   열 이름: {old} → {new}")
        if step.function in {"remove_duplicates", "mark_duplicates"}:
            subset = step.params.get("subset") or "전체 열"
            lines.append(f"   중복 기준: {subset}")
        if step.function == "calculate_column":
            right = step.params.get("right_column", step.params.get("value"))
            lines.append(
                f"   계산: {step.params.get('left_column')} "
                f"{step.params.get('operator')} {right} → {step.params.get('result_column')}"
            )
            if step.params.get("as_formula"):
                lines.append("   저장 방식: Excel 실수식으로 기록")
        if step.function == "add_subtotals" and step.params.get("as_formula"):
            lines.append("   저장 방식: SUBTOTAL 실수식으로 기록")
        if step.function == "highlight_rows":
            targets = step.params.get("target_columns") or "행 전체"
            lines.append(
                f"   강조 조건: {step.params.get('column')} "
                f"{step.params.get('operator')} {step.params.get('value')!r} | "
                f"대상: {targets} | 색상: {step.params.get('color')}"
            )
            if preview_item and preview_item.affected_rows is not None:
                lines.append(
                    f"   강조 대상: {preview_item.affected_rows:,}행"
                )
        if step.function == "highlight_extremes":
            lines.append(
                f"   강조 기준: {step.params.get('column')} "
                f"{step.params.get('mode')} {step.params.get('n', 1)}개 | "
                f"색상: {step.params.get('color')}"
            )
        if step.function == "highlight_missing":
            lines.append(
                f"   빈 셀 강조: {step.params.get('columns')} | "
                f"색상: {step.params.get('color')}"
            )
        if step.function == "format_numbers":
            lines.append(
                f"   표시 형식: {step.params.get('columns')} → {step.params.get('format')}"
            )
        if step.function == "color_scale":
            lines.append(
                f"   색상 스케일: {step.params.get('column')} → {step.params.get('palette')}"
            )
        if step.function == "add_total_row":
            lines.append(
                f"   합계 열: {step.params.get('value_columns')} | "
                f"방식: {step.params.get('aggregate', 'sum')} | "
                f"레이블: {step.params.get('label', '전체 합계')}"
            )
        if step.function == "add_conditional_summary_row":
            conditions = step.params.get("conditions") or [
                {
                    "column": step.params.get("condition_column"),
                    "operator": step.params.get("operator"),
                    "value": step.params.get("value"),
                }
            ]
            lines.append(
                f"   조건부 요약 방식: {step.params.get('aggregate')} | "
                f"결과 위치: {step.params.get('output_column')}"
            )
            for condition in conditions:
                lines.append(
                    f"   - {condition.get('column')} {condition.get('operator')} "
                    f"{condition.get('value')!r}"
                )
            if preview_item and preview_item.affected_rows is not None:
                lines.append(
                    f"   조건에 맞는 원본 행: {preview_item.affected_rows:,}행"
                )
        if step.function == "compare_columns":
            lines.append(
                f"   비교: {step.params.get('left_column')} ↔ "
                f"{step.params.get('right_column')} (허용 오차 {step.params.get('tolerance', 0)})"
            )
        if preview_item:
            lines.append(
                f"   예상 행 수: {preview_item.before_rows:,}행 → "
                f"{preview_item.after_rows:,}행"
            )
    if preview:
        lines.extend(
            [
                "",
                f"전체 예상 결과: {preview.initial_rows:,}행 → {preview.final_rows:,}행",
            ]
        )
    lines.extend(["", "이 작업을 실행할까요?"])
    return "\n".join(lines)
