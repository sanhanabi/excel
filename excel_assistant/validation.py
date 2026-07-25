from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import re

import pandas as pd

from .catalog import FUNCTION_CATALOG
from .conditions import validate_condition
from .models import ExecutionPlan, PlanPreview, PlanStep, PlanningHints, StepPreview
from .output import validate_output_step
from .request_analysis import analyze_request, extract_explicit_date_period


class PlanValidationError(ValueError):
    pass


def _plan_conditions(plan: ExecutionPlan) -> list[dict]:
    conditions: list[dict] = []
    for step in plan.steps:
        if step.function in {"filter_rows", "highlight_rows"}:
            conditions.append(
                {
                    "column": step.params.get("column"),
                    "operator": step.params.get("operator"),
                    "value": step.params.get("value"),
                }
            )
        raw_conditions = step.params.get("conditions")
        if isinstance(raw_conditions, list):
            conditions.extend(
                item for item in raw_conditions if isinstance(item, dict)
            )
        raw_groups = step.params.get("condition_groups")
        if isinstance(raw_groups, list):
            for group in raw_groups:
                if not isinstance(group, dict):
                    continue
                group_conditions = group.get("conditions")
                if isinstance(group_conditions, list):
                    conditions.extend(
                        item
                        for item in group_conditions
                        if isinstance(item, dict)
                    )
        if step.function == "add_conditional_summary_row" and step.params.get(
            "condition_column"
        ):
            conditions.append(
                {
                    "column": step.params.get("condition_column"),
                    "operator": step.params.get("operator"),
                    "value": step.params.get("value"),
                }
            )
    return conditions


def _condition_value_matches(actual: object, expected: object) -> bool:
    values = actual if isinstance(actual, list) else [actual]
    for value in values:
        if isinstance(value, str) and isinstance(expected, str):
            if value.strip().casefold() == expected.strip().casefold():
                return True
        elif isinstance(value, (int, float)) and isinstance(expected, (int, float)):
            if float(value) == float(expected):
                return True
        elif value == expected:
            return True
    return False


def _or_linked_matches(user_request: str, hints: PlanningHints) -> list[tuple]:
    request = user_request.casefold()
    linked: list[tuple] = []
    matches = hints.matched_values
    for left_index, left in enumerate(matches):
        for right in matches[left_index + 1 :]:
            if left.column.casefold() != right.column.casefold():
                continue
            left_text = left.exact_value.casefold()
            right_text = right.exact_value.casefold()
            for left_position in [
                item.start() for item in re.finditer(re.escape(left_text), request)
            ]:
                right_position = request.find(
                    right_text,
                    left_position + len(left_text),
                )
                if right_position < 0:
                    continue
                connector = request[
                    left_position + len(left_text) : right_position
                ]
                if re.search(r"이거나|거나|또는|혹은|\bor\b", connector):
                    linked.append((left, right))
                    break
            if linked and linked[-1] == (left, right):
                continue
            for right_position in [
                item.start() for item in re.finditer(re.escape(right_text), request)
            ]:
                left_position = request.find(
                    left_text,
                    right_position + len(right_text),
                )
                if left_position < 0:
                    continue
                connector = request[
                    right_position + len(right_text) : left_position
                ]
                if re.search(r"이거나|거나|또는|혹은|\bor\b", connector):
                    linked.append((left, right))
                    break
    return list(dict.fromkeys(linked))


def _plan_joins_values_with_or(
    plan: ExecutionPlan,
    column: str,
    left_value: str,
    right_value: str,
) -> bool:
    for step in plan.steps:
        if step.function == "filter_rows":
            if (
                str(step.params.get("column", "")).casefold() == column.casefold()
                and step.params.get("operator") == "in"
                and _condition_value_matches(step.params.get("value"), left_value)
                and _condition_value_matches(step.params.get("value"), right_value)
            ):
                return True
        if step.params.get("logic") != "or":
            raw_groups = step.params.get("condition_groups")
            if step.params.get("group_logic") != "or" or not isinstance(
                raw_groups, list
            ):
                continue
            group_values = []
            for group in raw_groups:
                if not isinstance(group, dict):
                    continue
                values = [
                    item.get("value")
                    for item in group.get("conditions", [])
                    if isinstance(item, dict)
                    and str(item.get("column", "")).casefold()
                    == column.casefold()
                ]
                group_values.append(values)
            if any(
                any(_condition_value_matches(value, left_value) for value in values)
                for values in group_values
            ) and any(
                any(_condition_value_matches(value, right_value) for value in values)
                for values in group_values
            ):
                return True
            continue
        raw_conditions = step.params.get("conditions")
        if isinstance(raw_conditions, list):
            values = [
                item.get("value")
                for item in raw_conditions
                if isinstance(item, dict)
                and str(item.get("column", "")).casefold() == column.casefold()
            ]
            if any(
                _condition_value_matches(value, left_value) for value in values
            ) and any(
                _condition_value_matches(value, right_value) for value in values
            ):
                return True
    return False


def _has_condition(
    conditions: list[dict],
    *,
    column: str,
    operator: str,
    value: object,
) -> bool:
    for item in conditions:
        if str(item.get("column", "")).casefold() != column.casefold():
            continue
        if item.get("operator") != operator:
            continue
        if operator in {"is_null", "not_null"} or _condition_value_matches(
            item.get("value"), value
        ):
            return True
    return False


def _explicit_column_conditions(
    user_request: str,
    source_columns: list[str],
) -> list[tuple[str, str, object]]:
    """Read only explicit column-adjacent comparisons; never infer business rules."""
    request = user_request.casefold()
    number_pattern = r"(-?\d[\d,]*(?:\.\d+)?)"
    expected: list[tuple[str, str, object]] = []

    def column_pattern(name: str) -> str:
        escaped = re.escape(name.casefold())
        if re.match(r"[a-z0-9_]", name.casefold()):
            escaped = rf"(?<![a-z0-9_]){escaped}"
        if re.search(r"[a-z0-9_]$", name.casefold()):
            escaped = rf"{escaped}(?![a-z0-9_])"
        return escaped

    for column in sorted(source_columns, key=len, reverse=True):
        lowered_column = column.casefold()
        for match in re.finditer(column_pattern(lowered_column), request):
            window = request[match.end() : match.end() + 70]
            next_column_offsets = [
                next_match.start()
                for other_column in source_columns
                if (
                    next_match := re.search(column_pattern(other_column), window)
                ) is not None
            ]
            if next_column_offsets:
                window = window[: min(next_column_offsets)]
            window = re.split(r"[\n.;]", window, maxsplit=1)[0]

            if re.search(
                r"^\s*(?:(?:이|가|은|는)\s*)?"
                r"(?:비어\s*있지\s*않|비어있지않|값이\s*있|존재|있는데|있는\s*(?:행|값)|"
                r"is\s+not\s+(?:blank|empty|null|missing)|"
                r"has\s+(?:a\s+)?value|is\s+present|exists)",
                window,
            ):
                expected.append((column, "not_null", None))
            elif re.search(
                r"^\s*(?:(?:이|가|은|는)\s*)?"
                r"(?:비어\s*있|비어있|빈\s*(?:행|칸|값)|값이\s*없|"
                r"is\s+(?:blank|empty|null|missing)|has\s+no\s+value)",
                window,
            ):
                expected.append((column, "is_null", None))
            elif re.search(r"^\s*(?:이|가|은|는)?\s*있는\s*행", window):
                expected.append((column, "not_null", None))

            comparison_patterns = (
                (
                    rf"^\s*(?:is\s+)?(?:greater\s+than\s+or\s+equal\s+to|"
                    rf"greater\s+than\s+or\s+equal|at\s+least|no\s+less\s+than)\s*{number_pattern}",
                    ">=",
                ),
                (
                    rf"^\s*(?:is\s+)?(?:less\s+than\s+or\s+equal\s+to|"
                    rf"less\s+than\s+or\s+equal|at\s+most|no\s+more\s+than)\s*{number_pattern}",
                    "<=",
                ),
                (
                    rf"^\s*(?:is\s+)?(?:greater\s+than|more\s+than|above|over)\s*{number_pattern}",
                    ">",
                ),
                (
                    rf"^\s*(?:is\s+)?(?:less\s+than|fewer\s+than|below|under)\s*{number_pattern}",
                    "<",
                ),
                (
                    rf"^\s*(?:is\s+)?(?:not\s+equal\s+to|does\s+not\s+equal|!=)\s*{number_pattern}",
                    "!=",
                ),
                (
                    rf"^\s*(?:is\s+|equals?\s+|equal\s+to\s+|=\s*){number_pattern}",
                    "==",
                ),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*보다\s*큰", ">"),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*보다\s*작은", "<"),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*이상", ">="),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*이하", "<="),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*초과", ">"),
                (rf"(?:이|가|은|는)?\s*{number_pattern}\s*미만", "<"),
                (rf"\s*(>=|<=|>|<)\s*{number_pattern}", None),
            )
            for pattern, fixed_operator in comparison_patterns:
                comparison = re.search(pattern, window)
                if comparison is None:
                    continue
                if fixed_operator is None:
                    operator = comparison.group(1)
                    raw_number = comparison.group(2)
                else:
                    operator = fixed_operator
                    raw_number = comparison.group(1)
                numeric = float(raw_number.replace(",", ""))
                value: int | float = int(numeric) if numeric.is_integer() else numeric
                expected.append((column, operator, value))
                break

    return list(dict.fromkeys(expected))


def _explicit_or_condition_groups(
    user_request: str,
    source_columns: list[str],
    planning_hints: PlanningHints | None = None,
) -> list[dict]:
    parts = re.split(r"(?:이거나|거나|또는|혹은|\bor\b)", user_request, flags=re.IGNORECASE)
    if len(parts) < 2:
        return []
    groups: list[dict] = []
    hints = planning_hints or PlanningHints()
    for part in parts[:2]:
        conditions = [
            {"column": column, "operator": operator, "value": value}
            for column, operator, value in _explicit_column_conditions(
                part, source_columns
            )
        ]
        part_text = part.casefold()
        matching_values = [
            match
            for match in hints.matched_values
            if (
                match.request_text.casefold() in part_text
                or match.exact_value.casefold() in part_text
            )
        ]
        for match in matching_values:
            if not (
                match.request_text.casefold() in part_text
                or match.exact_value.casefold() in part_text
            ):
                continue
            if any(
                match.exact_value.casefold() != other.exact_value.casefold()
                and match.exact_value.casefold() in other.exact_value.casefold()
                and other.exact_value.casefold() in part_text
                for other in matching_values
            ):
                continue
            condition = {
                "column": match.column,
                "operator": "==",
                "value": match.exact_value,
            }
            if condition not in conditions:
                conditions.insert(0, condition)
        if len(conditions) < 2:
            return []
        groups.append({"conditions": conditions, "logic": "and"})
    return groups


def _explicit_conditional_column_values(user_request: str) -> dict[str, object]:
    result: dict[str, object] = {}
    column_match = re.search(
        r"['\"]([^'\"]+)['\"]\s*(?:이?라는)?\s*새\s*열",
        user_request,
    )
    if column_match:
        result["result_column"] = column_match.group(1).strip()
        trailing = user_request[column_match.end() :]
        value_match = re.search(r"['\"]([^'\"]+)['\"]\s*(?:이?라고)?\s*표시", trailing)
        if value_match:
            result["true_value"] = value_match.group(1)
    if re.search(r"아니면\s*(?:빈칸|빈\s*칸)|otherwise\s+(?:blank|empty)", user_request, re.IGNORECASE):
        result["false_value"] = ""
    return result


def _validate_requested_aggregates(user_request: str, plan: ExecutionPlan) -> None:
    text = user_request.casefold()
    requested: set[str] = set()
    if "합계" in text or "sum" in text:
        requested.add("sum")
    if "평균" in text or "average" in text or "mean" in text:
        requested.add("mean")
    if "개수" in text or "count" in text:
        requested.add("count")
    if "최솟값" in text or "최소값" in text or "minimum" in text:
        requested.add("min")
    if "최댓값" in text or "최대값" in text or "maximum" in text:
        requested.add("max")
    if not requested:
        return

    actual: set[str] = set()
    direct = {
        "group_sum": "sum",
        "group_average": "mean",
        "group_count": "count",
    }
    for step in plan.steps:
        if step.function in direct:
            actual.add(direct[step.function])
        if step.function == "group_aggregate":
            for item in step.params.get("aggregations", []):
                if not isinstance(item, dict):
                    continue
                function = str(item.get("function"))
                actual.add("count" if function == "size" else function)
        if step.function in {"pivot_table", "add_total_row", "add_conditional_summary_row"}:
            aggregate = step.params.get("aggfunc", step.params.get("aggregate"))
            if aggregate:
                actual.add(str(aggregate))
    missing = requested - actual
    if missing:
        labels = {"sum": "합계", "mean": "평균", "count": "개수", "min": "최솟값", "max": "최댓값"}
        raise PlanValidationError(
            "요청한 집계가 계획에서 누락되었습니다: "
            + ", ".join(labels[item] for item in sorted(missing))
        )


def _requested_arithmetic_operator(user_request: str) -> str | None:
    text = user_request.casefold()
    operator_phrases = (
        ("multiply", ("곱해서", "곱하여", "곱한", "multiply", "multiplied", "product of", " times ")),
        ("divide", ("나눠서", "나누어", "divide", "divided by")),
        ("subtract", ("빼서", "빼고", "subtract", "minus")),
        ("add", ("더해서", "더하여", "더한", "plus")),
    )
    for operator, phrases in operator_phrases:
        if any(phrase in text for phrase in phrases):
            return operator
    return None


def _validate_explicit_calculation(
    user_request: str,
    plan: ExecutionPlan,
    source_columns: list[str],
) -> None:
    analysis = analyze_request(user_request)
    if "calculated_column" not in analysis.action_names:
        return
    requested_operator = _requested_arithmetic_operator(user_request)
    mentioned_columns = {
        column.casefold()
        for column in source_columns
        if column.casefold() in user_request.casefold()
    }
    for step in plan.steps:
        if step.function != "calculate_column":
            continue
        left = str(step.params.get("left_column", "")).casefold()
        right = str(step.params.get("right_column", "")).casefold()
        if requested_operator and step.params.get("operator") != requested_operator:
            continue
        if len(mentioned_columns) >= 2 and (
            left not in mentioned_columns
            or right not in mentioned_columns
            or left == right
        ):
            continue
        return
    raise PlanValidationError(
        "요청한 산술 계산의 연산 방식 또는 원본 열이 계획에서 바뀌었습니다."
    )


def _as_calendar_date(value: object):
    if value is None or isinstance(value, (int, float)):
        return None
    try:
        parsed = pd.to_datetime(value, errors="raise")
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, pd.DatetimeIndex):
        return None
    return parsed.date()


def _validate_explicit_date_period(
    user_request: str,
    plan: ExecutionPlan,
) -> None:
    analysis = analyze_request(user_request)
    if "date_range_filter" not in analysis.action_names:
        return
    period = extract_explicit_date_period(user_request)
    if period is None:
        return
    start, end = period
    by_column: dict[str, list[dict]] = {}
    for condition in _plan_conditions(plan):
        column = condition.get("column")
        if isinstance(column, str):
            by_column.setdefault(column.casefold(), []).append(condition)
    for conditions in by_column.values():
        has_start = any(
            item.get("operator") == ">="
            and _as_calendar_date(item.get("value")) == start
            for item in conditions
        )
        has_end = any(
            item.get("operator") == "<="
            and _as_calendar_date(item.get("value")) == end
            for item in conditions
        )
        if has_start and has_end:
            return
    raise PlanValidationError(
        "요청한 명시 기간 필터가 계획에서 누락되거나 범위가 바뀌었습니다: "
        f"{start.isoformat()} ~ {end.isoformat()}"
    )


def repair_plan_from_explicit_request(
    user_request: str,
    plan: ExecutionPlan,
    source_columns: list[str],
    planning_hints: PlanningHints | None = None,
) -> ExecutionPlan:
    """Copy literal comparison semantics from the request into existing conditions."""
    hints = planning_hints or PlanningHints()
    expected_by_column: dict[str, list[tuple[str, object]]] = {}
    for column, operator, value in _explicit_column_conditions(
        user_request, source_columns
    ):
        expected_by_column.setdefault(column.casefold(), []).append((operator, value))

    def repair_node(node):
        if isinstance(node, dict):
            repaired = {key: repair_node(value) for key, value in node.items()}
            column = repaired.get("column")
            if isinstance(column, str):
                expected = expected_by_column.get(column.casefold(), [])
                if len(expected) == 1 and "operator" in repaired:
                    operator, value = expected[0]
                    repaired["operator"] = operator
                    repaired["value"] = value
            return repaired
        if isinstance(node, list):
            return [repair_node(item) for item in node]
        return deepcopy(node)

    steps: list[PlanStep] = []
    for step in plan.steps:
        params = repair_node(step.params)
        if step.function == "calculate_date_difference" and (
            "오늘" in user_request or "today" in user_request.casefold()
        ):
            params["end_mode"] = "today"
            params["end_column"] = None
        if step.function == "add_conditional_summary_row":
            if isinstance(params.get("conditions"), list) and params["conditions"]:
                params.pop("condition_column", None)
                params.pop("operator", None)
                params.pop("value", None)
            condition_columns = {
                str(item.get("column"))
                for item in params.get("conditions", [])
                if isinstance(item, dict) and item.get("column") is not None
            }
            if params.get("condition_column") is not None:
                condition_columns.add(str(params["condition_column"]))
            target_columns = [
                column
                for column in source_columns
                if (
                    re.search(
                        rf"{re.escape(column.casefold())}\s*(?:열\s*)?아래",
                        user_request.casefold(),
                    )
                    or re.search(
                        rf"(?:below|under)\s+(?:the\s+)?"
                        rf"{re.escape(column.casefold())}\s+column",
                        user_request.casefold(),
                    )
                )
                and column not in condition_columns
            ]
            if len(target_columns) == 1:
                target = target_columns[0]
                params["output_column"] = target
                if params.get("aggregate") in {"sum", "average"}:
                    params["value_column"] = target
        steps.append(
            PlanStep(
                function=step.function,
                params=params,
                description=step.description,
            )
        )

    explicit_nested_groups = _explicit_or_condition_groups(
        user_request, source_columns, hints
    )
    if explicit_nested_groups:
        conditional_indexes = [
            index
            for index, step in enumerate(steps)
            if step.function == "add_conditional_column"
        ]
        if conditional_indexes:
            explicit_values = _explicit_conditional_column_values(user_request)
            existing_results = {
                str(steps[index].params.get("result_column", ""))
                for index in conditional_indexes
            }
            explicit_result = explicit_values.get("result_column")
            if len(existing_results) == 1 or isinstance(explicit_result, str):
                first_index = conditional_indexes[0]
                base_step = steps[first_index]
                params = dict(base_step.params)
                params.pop("conditions", None)
                params.pop("logic", None)
                params["condition_groups"] = explicit_nested_groups
                params["group_logic"] = "or"
                params.update(explicit_values)
                rebuilt = [
                    step
                    for index, step in enumerate(steps)
                    if index not in conditional_indexes
                ]
                rebuilt.insert(
                    first_index,
                    PlanStep(
                        "add_conditional_column",
                        params,
                        "요청에서 OR로 나뉜 조건 그룹을 각각 AND로 검사해 새 열에 표시합니다.",
                    ),
                )
                steps = rebuilt

        filter_indexes = [
            index
            for index, step in enumerate(steps)
            if step.function in {"filter_rows", "filter_by_conditions"}
        ]
        if filter_indexes:
            first_index = filter_indexes[0]
            rebuilt = [
                step
                for index, step in enumerate(steps)
                if index not in filter_indexes
            ]
            rebuilt.insert(
                first_index,
                PlanStep(
                    "filter_by_conditions",
                    {
                        "condition_groups": explicit_nested_groups,
                        "group_logic": "or",
                    },
                    "요청에서 OR로 나뉜 조건 그룹을 각각 AND로 검사해 행을 남깁니다.",
                ),
            )
            steps = rebuilt

    for left, right in _or_linked_matches(user_request, hints):
        if _plan_joins_values_with_or(
            replace(plan, steps=steps),
            left.column,
            left.exact_value,
            right.exact_value,
        ):
            continue
        rewritten: list[PlanStep] = []
        insertion_index: int | None = None
        for step in steps:
            if step.function == "filter_rows" and str(
                step.params.get("column", "")
            ).casefold() == left.column.casefold():
                if insertion_index is None:
                    insertion_index = len(rewritten)
                continue
            if step.function == "filter_by_conditions":
                raw_conditions = step.params.get("conditions")
                if isinstance(raw_conditions, list):
                    remaining = [
                        item
                        for item in raw_conditions
                        if not (
                            isinstance(item, dict)
                            and str(item.get("column", "")).casefold()
                            == left.column.casefold()
                        )
                    ]
                    if len(remaining) != len(raw_conditions):
                        if insertion_index is None:
                            insertion_index = len(rewritten)
                        if not remaining:
                            continue
                        params = dict(step.params)
                        params["conditions"] = remaining
                        params["logic"] = "and"
                        rewritten.append(
                            PlanStep(step.function, params, step.description)
                        )
                        continue
            rewritten.append(step)
        or_step = PlanStep(
            "filter_by_conditions",
            {
                "conditions": [
                    {
                        "column": left.column,
                        "operator": "==",
                        "value": left.exact_value,
                    },
                    {
                        "column": right.column,
                        "operator": "==",
                        "value": right.exact_value,
                    },
                ],
                "logic": "or",
            },
            f"{left.exact_value} 또는 {right.exact_value} 조건을 적용합니다.",
        )
        rewritten.insert(insertion_index or 0, or_step)
        steps = rewritten

    analysis = analyze_request(user_request)
    explicit_period = extract_explicit_date_period(user_request)
    if "date_range_filter" in analysis.action_names and explicit_period is not None:
        period_year = str(explicit_period[0].year)
        referenced_date_columns: list[str] = []
        for step in steps:
            raw_conditions: list[dict] = []
            if step.function == "filter_rows":
                raw_conditions = [step.params]
            elif step.function == "filter_by_conditions" and isinstance(
                step.params.get("conditions"), list
            ):
                raw_conditions = [
                    item
                    for item in step.params["conditions"]
                    if isinstance(item, dict)
                ]
            for condition in raw_conditions:
                column = condition.get("column")
                if (
                    isinstance(column, str)
                    and column in source_columns
                    and period_year in str(condition.get("value", ""))
                    and column not in referenced_date_columns
                ):
                    referenced_date_columns.append(column)
        if not referenced_date_columns:
            named_date_columns = [
                column
                for column in source_columns
                if any(
                    marker in column.casefold()
                    for marker in ("날짜", "일자", "date")
                )
            ]
            if len(named_date_columns) == 1:
                referenced_date_columns = named_date_columns
        if len(referenced_date_columns) == 1:
            date_column = referenced_date_columns[0]
            rewritten = []
            insertion_index: int | None = None
            for step in steps:
                if (
                    step.function == "filter_rows"
                    and str(step.params.get("column", "")).casefold()
                    == date_column.casefold()
                ):
                    if insertion_index is None:
                        insertion_index = len(rewritten)
                    continue
                if step.function == "filter_by_conditions":
                    raw_conditions = step.params.get("conditions")
                    if isinstance(raw_conditions, list):
                        remaining = [
                            item
                            for item in raw_conditions
                            if not (
                                isinstance(item, dict)
                                and str(item.get("column", "")).casefold()
                                == date_column.casefold()
                            )
                        ]
                        if len(remaining) != len(raw_conditions):
                            if insertion_index is None:
                                insertion_index = len(rewritten)
                            if not remaining:
                                continue
                            params = dict(step.params)
                            params["conditions"] = remaining
                            rewritten.append(
                                PlanStep(step.function, params, step.description)
                            )
                            continue
                rewritten.append(step)
            start, end = explicit_period
            period_step = PlanStep(
                "filter_by_conditions",
                {
                    "conditions": [
                        {
                            "column": date_column,
                            "operator": ">=",
                            "value": start.isoformat(),
                        },
                        {
                            "column": date_column,
                            "operator": "<=",
                            "value": end.isoformat(),
                        },
                    ],
                    "logic": "and",
                },
                f"{date_column}이 {start.isoformat()}부터 {end.isoformat()}까지인 행만 남깁니다.",
            )
            rewritten.insert(insertion_index or 0, period_step)
            steps = rewritten

    request_text = user_request.casefold()
    calculated_results = [
        str(step.params.get("result_column"))
        for step in steps
        if step.function == "calculate_column"
        and step.params.get("result_column")
        and str(step.params.get("result_column")).casefold() in request_text
    ]
    calculated_summary_columns: list[str] = []
    if len(calculated_results) == 1:
        calculated_result = calculated_results[0]
        mention_at = request_text.find(calculated_result.casefold())
        aggregate_window = request_text[mention_at : mention_at + 80]
        requested_result_aggregates: set[str] = set()
        if "합계" in aggregate_window or "sum" in aggregate_window:
            requested_result_aggregates.add("sum")
        if (
            "평균" in aggregate_window
            or "average" in aggregate_window
            or "mean" in aggregate_window
        ):
            requested_result_aggregates.add("mean")
        if re.search(r"거래\s*건수|행\s*개수|\bcount\b", request_text):
            requested_result_aggregates.add("count")
        aggregate_labels = {"sum": "합계", "mean": "평균", "count": "개수"}
        rewritten = []
        for step in steps:
            params = dict(step.params)
            description = step.description
            if step.function == "group_aggregate":
                aggregations = [
                    dict(item) if isinstance(item, dict) else item
                    for item in params.get("aggregations", [])
                ]
                for function in requested_result_aggregates:
                    candidates = [
                        item
                        for item in aggregations
                        if isinstance(item, dict)
                        and item.get("function") == function
                    ]
                    if len(candidates) == 1:
                        candidates[0]["column"] = (
                            params.get("group_columns", [calculated_result])[0]
                            if function == "count"
                            else calculated_result
                        )
                        candidates[0]["function"] = (
                            "size" if function == "count" else function
                        )
                        candidates[0]["result_column"] = (
                            f"{calculated_result}_{aggregate_labels[function]}"
                        )
                        if function != "count":
                            calculated_summary_columns.append(
                                str(candidates[0]["result_column"])
                            )
                    elif not candidates:
                        group_columns = _as_columns(params.get("group_columns"))
                        aggregations.append(
                            {
                                "column": (
                                    group_columns[0]
                                    if function == "count" and group_columns
                                    else calculated_result
                                ),
                                "function": "size" if function == "count" else function,
                                "result_column": (
                                    f"{calculated_result}_{aggregate_labels[function]}"
                                ),
                            }
                        )
                        if function != "count":
                            calculated_summary_columns.append(
                                f"{calculated_result}_{aggregate_labels[function]}"
                            )
                params["aggregations"] = aggregations
                if requested_result_aggregates:
                    description = (
                        f"계산한 {calculated_result} 열을 요청한 방식으로 그룹 집계합니다."
                    )
            elif (
                step.function == "group_sum"
                and "sum" in requested_result_aggregates
            ):
                params["value_column"] = calculated_result
            elif (
                step.function == "group_average"
                and "mean" in requested_result_aggregates
            ):
                params["value_column"] = calculated_result
            rewritten.append(PlanStep(step.function, params, description))
        steps = rewritten

    if calculated_summary_columns and "format" in analysis.action_names:
        rewritten = []
        for step in steps:
            params = dict(step.params)
            if step.function == "format_numbers":
                columns = _as_columns(params.get("columns"))
                params["columns"] = list(
                    dict.fromkeys([*columns, *calculated_summary_columns])
                )
            rewritten.append(PlanStep(step.function, params, step.description))
        steps = rewritten

    aggregate_indexes: dict[tuple[str, ...], int] = {}
    for index, step in enumerate(steps):
        if step.function == "group_aggregate":
            aggregate_indexes[tuple(_as_columns(step.params.get("group_columns")))] = index
    count_indexes: set[int] = set()
    for index, step in enumerate(steps):
        if step.function != "group_count":
            continue
        groups = tuple(_as_columns(step.params.get("group_columns")))
        aggregate_index = aggregate_indexes.get(groups)
        if aggregate_index is None or not groups:
            continue
        aggregate_step = steps[aggregate_index]
        params = dict(aggregate_step.params)
        aggregations = [
            dict(item) if isinstance(item, dict) else item
            for item in params.get("aggregations", [])
        ]
        aggregations.append(
            {
                "column": groups[0],
                "function": "size",
                "result_column": str(step.params.get("result_column", "개수")),
            }
        )
        params["aggregations"] = aggregations
        steps[aggregate_index] = PlanStep(
            "group_aggregate",
            params,
            aggregate_step.description,
        )
        count_indexes.add(index)
    if count_indexes:
        steps = [
            step for index, step in enumerate(steps) if index not in count_indexes
        ]

    rank_outputs = {
        str(step.params.get("result_column", "순위")): step
        for step in steps
        if step.function == "rank_rows"
    }
    if rank_outputs:
        rewritten = []
        top_requested = bool(
            re.search(r"상위\s*\d+\s*개|\btop\s+\d+\b", request_text)
        )
        bottom_requested = bool(
            re.search(r"하위\s*\d+\s*개|\bbottom\s+\d+\b", request_text)
        )
        for step in steps:
            params = dict(step.params)
            if step.function == "select_top_n":
                rank_step = rank_outputs.get(str(params.get("column")))
                if rank_step is not None and top_requested:
                    params["largest"] = False
                elif rank_step is not None and bottom_requested:
                    params["largest"] = True
            rewritten.append(PlanStep(step.function, params, step.description))
        steps = rewritten

    duration_columns = {
        str(step.params.get("result_column"))
        for step in steps
        if step.function == "calculate_date_difference"
    }
    aggregate_outputs: dict[str, str] = {}
    for step in steps:
        if step.function == "group_aggregate":
            aggregate_outputs.update(
                {
                    str(item["result_column"]): str(item["function"])
                    for item in step.params.get("aggregations", [])
                    if isinstance(item, dict)
                    and item.get("result_column")
                    and item.get("function")
                }
            )
        elif step.function in {"group_sum", "group_average", "group_count"}:
            default_name = {
                "group_sum": "합계",
                "group_average": "평균",
                "group_count": "개수",
            }[step.function]
            function = {
                "group_sum": "sum",
                "group_average": "mean",
                "group_count": "count",
            }[step.function]
            aggregate_outputs[str(step.params.get("result_column", default_name))] = function

    repaired_sort_steps: list[PlanStep] = []
    for step in steps:
        if step.function != "sort_rows":
            repaired_sort_steps.append(step)
            continue
        params = dict(step.params)
        sort_columns = _as_columns(params.get("columns"))
        sum_outputs = [
            column for column, function in aggregate_outputs.items() if function == "sum"
        ]
        mean_outputs = [
            column for column, function in aggregate_outputs.items() if function == "mean"
        ]
        if len(sum_outputs) == 1 and re.search(
            r"(?:합계|sum|total).{0,40}(?:큰|높|내림차순|largest|highest|descending)",
            request_text,
        ):
            sort_columns = sum_outputs
            params["columns"] = sum_outputs
        elif len(mean_outputs) == 1 and re.search(
            r"(?:평균|average|mean).{0,40}(?:큰|높|내림차순|largest|highest|descending)",
            request_text,
        ):
            sort_columns = mean_outputs
            params["columns"] = mean_outputs
        direction: bool | None = None
        if re.search(
            r"큰\s*순서|내림차순|많은\s*순서|비싼|descending|"
            r"largest\s+first|highest\s+first|most\s+expensive\s+first",
            request_text,
        ):
            direction = False
        elif re.search(
            r"작은\s*순서|오름차순|싼|ascending|smallest\s+first|"
            r"lowest\s+first|cheapest\s+first",
            request_text,
        ):
            direction = True
        elif "오래된" in request_text or "oldest first" in request_text:
            direction = not any(column in duration_columns for column in sort_columns)
        elif (
            "최신" in request_text
            or "최근 순" in request_text
            or "newest first" in request_text
            or "latest first" in request_text
            or "most recent first" in request_text
        ):
            direction = any(column in duration_columns for column in sort_columns)
        if direction is not None:
            params["ascending"] = (
                [direction] * len(sort_columns)
                if isinstance(params.get("ascending"), list)
                else direction
            )
        repaired_sort_steps.append(
            PlanStep(step.function, params, step.description)
        )

    if (
        "total_row" in analysis.action_names
        and not any(step.function == "add_total_row" for step in repaired_sort_steps)
    ):
        sum_outputs = [
            column for column, function in aggregate_outputs.items() if function == "sum"
        ]
        if sum_outputs:
            repaired_sort_steps.append(
                PlanStep(
                    "add_total_row",
                    {
                        "value_columns": sum_outputs,
                        "label": "전체 합계",
                        "aggregate": "sum",
                    },
                    "집계 결과의 전체 합계 행을 표 맨 아래에 추가합니다.",
                )
            )
    aggregate_compatibility = {
        "sum": {"sum", "count"},
        "average": {"mean"},
        "count": {"count"},
        "min": {"min"},
        "max": {"max"},
    }
    normalized_steps: list[PlanStep] = []
    seen_steps: set[tuple[str, str]] = set()
    for step in repaired_sort_steps:
        params = dict(step.params)
        if step.function == "add_total_row" and aggregate_outputs:
            aggregate = str(params.get("aggregate", "sum"))
            compatible = aggregate_compatibility.get(aggregate, set())
            preferred = [
                column
                for column, function in aggregate_outputs.items()
                if function in compatible
            ]
            if preferred:
                requested = _as_columns(params.get("value_columns"))
                selected = [column for column in requested if column in preferred]
                params["value_columns"] = selected or preferred
        signature = (
            step.function,
            json.dumps(params, ensure_ascii=False, sort_keys=True, default=str),
        )
        if signature in seen_steps:
            continue
        seen_steps.add(signature)
        normalized_steps.append(
            PlanStep(step.function, params, step.description)
        )
    return replace(plan, steps=normalized_steps)


def validate_plan_covers_request(
    user_request: str,
    plan: ExecutionPlan,
    source_columns: list[str],
    planning_hints: PlanningHints | None = None,
) -> None:
    """Reject executable plans that silently omit or replace explicit requirements."""
    analysis = analyze_request(user_request)
    functions = {step.function for step in plan.steps}
    missing_actions = [
        item.label
        for item in analysis.actions
        if not functions.intersection(item.allowed_functions)
    ]
    if missing_actions:
        raise PlanValidationError(
            "요청한 작업이 계획에서 누락되었습니다: " + ", ".join(missing_actions)
        )

    _validate_explicit_calculation(user_request, plan, source_columns)
    _validate_explicit_date_period(user_request, plan)

    protected_actions = {
        "filter_rows": "filter",
        "filter_by_conditions": "filter",
        "filter_relative_dates": "filter",
        "drop_rows_missing_keys": "filter",
        "replace_values": "replace",
        "replace_text": "replace",
        "drop_columns": "drop_columns",
        "select_columns": "select_columns",
    }
    unexpected = [
        step.function
        for step in plan.steps
        if step.function in protected_actions
        and protected_actions[step.function] not in analysis.action_names
    ]
    if unexpected:
        raise PlanValidationError(
            "사용자가 요청하지 않은 원본 축소·변경 작업이 포함되었습니다: "
            + ", ".join(dict.fromkeys(unexpected))
        )

    conditions = _plan_conditions(plan)
    hints = planning_hints or PlanningHints()
    if analysis.needs_conditions:
        for match in hints.matched_values:
            if not any(
                str(item.get("column", "")).casefold() == match.column.casefold()
                and item.get("operator") in {"==", "contains", "startswith", "endswith", "in"}
                and _condition_value_matches(item.get("value"), match.exact_value)
                for item in conditions
            ):
                raise PlanValidationError(
                    f"요청에 명시된 실제 조건이 계획에서 누락되었습니다: "
                    f"{match.column} = {match.exact_value}"
                )
        for left, right in _or_linked_matches(user_request, hints):
            if not _plan_joins_values_with_or(
                plan,
                left.column,
                left.exact_value,
                right.exact_value,
            ):
                raise PlanValidationError(
                    "요청에서 OR로 연결한 조건이 계획에서 AND로 바뀌었습니다: "
                    f"{left.column} = {left.exact_value} 또는 {right.exact_value}"
                )

    missing_conditions: list[str] = []
    for column, operator, value in _explicit_column_conditions(
        user_request, source_columns
    ):
        if _has_condition(
            conditions,
            column=column,
            operator=operator,
            value=value,
        ):
            continue
        if operator == "not_null":
            if any(
                step.function == "drop_rows_missing_keys"
                and column in _as_columns(step.params.get("columns"))
                for step in plan.steps
            ):
                continue
        rendered = "빈 값" if operator == "is_null" else "값 있음" if operator == "not_null" else value
        missing_conditions.append(f"{column} {operator} {rendered}")
    if missing_conditions:
        raise PlanValidationError(
            "요청의 비교 조건이 계획에서 누락되거나 바뀌었습니다: "
            + ", ".join(missing_conditions)
        )

    if analysis.action_names.intersection(
        {"group_summary", "pivot", "conditional_summary", "total_row"}
    ):
        _validate_requested_aggregates(user_request, plan)


def _as_columns(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _condition_columns(step_params: dict) -> list[str]:
    conditions: list = []
    flat_conditions = step_params.get("conditions")
    if isinstance(flat_conditions, list):
        conditions.extend(flat_conditions)
    groups = step_params.get("condition_groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("conditions"), list):
                conditions.extend(group["conditions"])
    return [
        str(item.get("column"))
        for item in conditions
        if isinstance(item, dict) and item.get("column") is not None
    ]


def _validate_filter_operator(number: int, operator: object, value: object) -> None:
    try:
        validate_condition(operator, value)
    except ValueError as exc:
        raise PlanValidationError(f"{number}단계의 {exc}") from exc


def validate_plan(plan: ExecutionPlan, initial_columns: list[str]) -> None:
    if not plan.steps:
        raise PlanValidationError("실행할 작업 단계가 없습니다.")

    current_columns = set(initial_columns)
    output_phase_started = False
    for number, step in enumerate(plan.steps, start=1):
        spec = FUNCTION_CATALOG.get(step.function)
        if spec is None:
            raise PlanValidationError(
                f"{number}단계의 함수 '{step.function}'은 등록되어 있지 않습니다."
            )
        missing = [key for key in spec.required_params if key not in step.params]
        if missing:
            raise PlanValidationError(
                f"{number}단계에 필수 값이 없습니다: {', '.join(missing)}"
            )
        unknown = set(step.params) - set(spec.allowed_params)
        if unknown:
            raise PlanValidationError(
                f"{number}단계에 허용되지 않은 값이 있습니다: {', '.join(sorted(unknown))}"
            )
        if spec.phase == "output":
            output_phase_started = True
        elif output_phase_started:
            raise PlanValidationError(
                f"{number}단계의 데이터 작업은 서식·수식 출력 작업보다 먼저 와야 합니다."
            )
        if "as_formula" in step.params and not isinstance(
            step.params["as_formula"], bool
        ):
            raise PlanValidationError(f"{number}단계의 as_formula는 참/거짓이어야 합니다.")
        if step.params.get("as_formula"):
            formula_columns = [
                str(step.params[key])
                for key in (
                    "left_column", "right_column", "start_column", "end_column"
                )
                if step.params.get(key) is not None
            ]
            if any(column.startswith("_") for column in formula_columns):
                raise PlanValidationError(
                    f"{number}단계의 수식은 저장 전에 제거되는 내부 열을 참조할 수 없습니다."
                )

        referenced: list[str] = []
        for key in (
            "column", "columns", "subset", "group_columns", "index_columns",
            "pivot_column", "value_column", "value_columns", "left_column",
            "right_column", "order_columns", "target_columns", "hidden_column",
            "start_column", "end_column", "date_column", "condition_column",
            "output_column", "label_column",
        ):
            if key in step.params and step.params[key] is not None:
                referenced.extend(_as_columns(step.params[key]))
        referenced.extend(_condition_columns(step.params))
        if step.function == "normalize_column_names":
            mapping = step.params.get("mapping")
            if not isinstance(mapping, dict) or not mapping:
                raise PlanValidationError(f"{number}단계의 열 이름 대응표가 비어 있습니다.")
            referenced.extend(str(column) for column in mapping)
        if step.function in {"fill_missing_values"}:
            values = step.params.get("values")
            if not isinstance(values, dict) or not values:
                raise PlanValidationError(f"{number}단계의 채울 값 대응표가 비어 있습니다.")
            referenced.extend(str(column) for column in values)
        if step.function == "group_aggregate":
            aggregations = step.params.get("aggregations")
            if not isinstance(aggregations, list) or not aggregations:
                raise PlanValidationError(f"{number}단계의 집계 항목이 비어 있습니다.")
            referenced.extend(
                str(item.get("column"))
                for item in aggregations
                if isinstance(item, dict) and item.get("column") is not None
            )
        missing_columns = [item for item in referenced if item not in current_columns]
        if missing_columns:
            raise PlanValidationError(
                f"{number}단계에서 찾을 수 없는 열을 사용했습니다: "
                f"{', '.join(missing_columns)}"
            )

        if step.function in {"group_sum", "group_average", "group_count"}:
            groups = _as_columns(step.params["group_columns"])
            default_name = {
                "group_sum": "합계",
                "group_average": "평균",
                "group_count": "개수",
            }[step.function]
            result_name = str(step.params.get("result_column", default_name))
            if not result_name.strip():
                raise PlanValidationError(
                    f"{number}단계의 결과 열 이름이 비어 있습니다."
                )
            current_columns = set(groups + [result_name])

        if step.function == "group_aggregate":
            groups = _as_columns(step.params["group_columns"])
            aggregations = step.params["aggregations"]
            supported = {
                "sum", "mean", "count", "size", "nunique", "min", "max", "median"
            }
            result_names: list[str] = []
            for item in aggregations:
                if not isinstance(item, dict):
                    raise PlanValidationError(f"{number}단계의 집계 항목 형식이 잘못되었습니다.")
                if item.get("function") not in supported:
                    raise PlanValidationError(f"{number}단계의 집계 방식이 올바르지 않습니다.")
                result_name = str(item.get("result_column", "")).strip()
                if not result_name:
                    raise PlanValidationError(f"{number}단계의 집계 결과 열 이름이 비어 있습니다.")
                result_names.append(result_name)
            if len(result_names) != len(set(result_names)):
                raise PlanValidationError(f"{number}단계의 집계 결과 열 이름이 중복됩니다.")
            current_columns = set(groups + result_names)

        if step.function == "pivot_table":
            indexes = _as_columns(step.params["index_columns"])
            pivot_column = step.params["pivot_column"]
            aggfunc = step.params.get("aggfunc", "sum")
            if not indexes:
                raise PlanValidationError(
                    f"{number}단계의 피벗 행 기준 열 형식이 올바르지 않습니다."
                )
            if pivot_column in indexes:
                raise PlanValidationError(
                    f"{number}단계의 피벗 행 기준과 열 기준은 서로 달라야 합니다."
                )
            if aggfunc not in {"sum", "mean", "count", "min", "max"}:
                raise PlanValidationError(
                    f"{number}단계의 피벗 집계 방식이 올바르지 않습니다."
                )
            if isinstance(step.params.get("fill_value", 0), (dict, list, tuple, set)):
                raise PlanValidationError(
                    f"{number}단계의 피벗 빈칸 값은 단일 값이어야 합니다."
                )
            values = _as_columns(step.params["value_column"])
            current_columns = set(indexes + values)

        if step.function == "select_top_n":
            n = step.params.get("n", 10)
            if not isinstance(n, int) or n < 1 or n > 100000:
                raise PlanValidationError(f"{number}단계의 n은 1 이상의 정수여야 합니다.")

        if step.function == "filter_rows":
            _validate_filter_operator(
                number, step.params.get("operator"), step.params.get("value")
            )

        if step.function in {"filter_by_conditions", "add_conditional_column"}:
            conditions = step.params.get("conditions")
            condition_groups = step.params.get("condition_groups")
            has_conditions = isinstance(conditions, list) and bool(conditions)
            has_groups = isinstance(condition_groups, list) and bool(condition_groups)
            if has_conditions == has_groups:
                raise PlanValidationError(
                    f"{number}단계는 conditions 또는 condition_groups 중 "
                    "정확히 하나를 사용해야 합니다."
                )
            if has_conditions:
                if step.params.get("logic", "and") not in {"and", "or"}:
                    raise PlanValidationError(
                        f"{number}단계의 조건 결합 방식이 올바르지 않습니다."
                    )
                groups_to_validate = [
                    {"conditions": conditions, "logic": step.params.get("logic", "and")}
                ]
            else:
                if step.params.get("group_logic", "or") not in {"and", "or"}:
                    raise PlanValidationError(
                        f"{number}단계의 조건 그룹 결합 방식이 올바르지 않습니다."
                    )
                groups_to_validate = condition_groups
            for group in groups_to_validate:
                if not isinstance(group, dict):
                    raise PlanValidationError(
                        f"{number}단계의 조건 그룹 형식이 잘못되었습니다."
                    )
                group_conditions = group.get("conditions")
                if not isinstance(group_conditions, list) or not group_conditions:
                    raise PlanValidationError(
                        f"{number}단계의 조건 그룹이 비어 있습니다."
                    )
                if group.get("logic", "and") not in {"and", "or"}:
                    raise PlanValidationError(
                        f"{number}단계의 그룹 내부 결합 방식이 올바르지 않습니다."
                    )
                for condition in group_conditions:
                    if not isinstance(condition, dict):
                        raise PlanValidationError(
                            f"{number}단계의 필터 조건 형식이 잘못되었습니다."
                        )
                    _validate_filter_operator(
                        number, condition.get("operator"), condition.get("value")
                    )

        if step.function == "sort_rows":
            columns = _as_columns(step.params["columns"])
            ascending = step.params.get("ascending", True)
            if not columns:
                raise PlanValidationError(f"{number}단계의 정렬 열 형식이 올바르지 않습니다.")
            if isinstance(ascending, list) and len(ascending) != len(columns):
                raise PlanValidationError(
                    f"{number}단계의 정렬 열과 정렬 방향 개수가 다릅니다."
                )

        if step.function == "normalize_column_names":
            mapping = {str(key): str(value) for key, value in step.params["mapping"].items()}
            current_columns = {
                mapping.get(column, column) for column in current_columns
            }

        if step.function == "select_columns":
            current_columns = set(_as_columns(step.params["columns"]))

        if step.function == "drop_columns":
            removed = set(_as_columns(step.params["columns"]))
            if not removed or len(removed) >= len(current_columns):
                raise PlanValidationError(
                    f"{number}단계에서 모든 열을 제거할 수는 없습니다."
                )
            current_columns -= removed

        if step.function == "reorder_columns" and not step.params.get("keep_remaining", True):
            current_columns = set(_as_columns(step.params["columns"]))

        if step.function == "convert_column_type":
            if step.params.get("target_type") not in {
                "number", "datetime", "date", "string", "boolean"
            }:
                raise PlanValidationError(f"{number}단계의 변환 자료형이 올바르지 않습니다.")
            if step.params.get("errors", "raise") not in {"raise", "coerce"}:
                raise PlanValidationError(f"{number}단계의 변환 오류 처리 방식이 올바르지 않습니다.")

        if step.function == "clean_numeric_values":
            if step.params.get("errors", "raise") not in {"raise", "coerce"}:
                raise PlanValidationError(
                    f"{number}단계의 숫자 정리 오류 처리 방식이 올바르지 않습니다."
                )

        if step.function == "round_numbers":
            decimals = step.params.get("decimals", 0)
            if not isinstance(decimals, int) or not -10 <= decimals <= 10:
                raise PlanValidationError(
                    f"{number}단계의 반올림 자릿수가 올바르지 않습니다."
                )
            if step.params.get("mode", "round") not in {"round", "floor", "ceil"}:
                raise PlanValidationError(
                    f"{number}단계의 반올림 방식이 올바르지 않습니다."
                )

        if step.function == "calculate_date_difference":
            end_mode = step.params.get("end_mode", "column")
            end_column = step.params.get("end_column")
            if end_mode not in {"column", "today"}:
                raise PlanValidationError(
                    f"{number}단계의 종료 날짜 방식이 올바르지 않습니다."
                )
            if end_mode == "column" and not end_column:
                raise PlanValidationError(
                    f"{number}단계에는 종료 날짜 열이 필요합니다."
                )
            if end_mode == "today" and end_column is not None:
                raise PlanValidationError(
                    f"{number}단계의 TODAY 방식에는 종료 날짜 열을 함께 사용할 수 없습니다."
                )
            if step.params.get("unit", "days") not in {"days", "weeks", "months"}:
                raise PlanValidationError(
                    f"{number}단계의 날짜 차이 단위가 올바르지 않습니다."
                )
            current_columns.add(str(step.params["result_column"]))

        if step.function == "combine_columns":
            if not _as_columns(step.params.get("columns")):
                raise PlanValidationError(f"{number}단계의 결합 대상 열이 비어 있습니다.")
            current_columns.add(str(step.params["result_column"]))

        if step.function == "split_column":
            result_columns = step.params.get("result_columns")
            if (
                not isinstance(result_columns, list)
                or len(result_columns) < 2
                or not all(isinstance(item, str) and item.strip() for item in result_columns)
                or len(result_columns) != len(set(result_columns))
            ):
                raise PlanValidationError(
                    f"{number}단계에는 서로 다른 결과 열 이름이 두 개 이상 필요합니다."
                )
            collisions = [item for item in result_columns if item in current_columns]
            if collisions:
                raise PlanValidationError(
                    f"{number}단계의 분리 결과 열이 기존 열과 겹칩니다: "
                    f"{', '.join(collisions)}"
                )
            if not step.params.get("delimiter"):
                raise PlanValidationError(f"{number}단계의 열 분리 구분자가 비어 있습니다.")
            if step.params.get("drop_source", False):
                current_columns.discard(str(step.params["column"]))
            current_columns.update(result_columns)

        if step.function == "replace_text" and not step.params.get("old"):
            raise PlanValidationError(f"{number}단계의 찾을 문자열이 비어 있습니다.")

        if step.function == "filter_relative_dates":
            period = step.params.get("period")
            days = step.params.get("days")
            relative_periods = {
                "today", "last_n_days", "older_than_n_days", "this_month",
                "last_month", "this_year", "last_year",
            }
            if period not in relative_periods:
                raise PlanValidationError(
                    f"{number}단계의 상대 날짜 범위가 올바르지 않습니다."
                )
            if period in {"last_n_days", "older_than_n_days"}:
                if not isinstance(days, int) or days < 1:
                    raise PlanValidationError(
                        f"{number}단계의 상대 날짜 일수가 올바르지 않습니다."
                    )
            elif days is not None:
                raise PlanValidationError(
                    f"{number}단계의 날짜 범위에는 days를 함께 사용할 수 없습니다."
                )

        if step.function == "extract_text":
            mode = step.params.get("mode")
            if mode not in {"before", "after", "between", "left", "right"}:
                raise PlanValidationError(
                    f"{number}단계의 문자열 추출 방식이 올바르지 않습니다."
                )
            if mode in {"before", "after"} and not step.params.get("delimiter"):
                raise PlanValidationError(
                    f"{number}단계의 문자열 추출 구분자가 비어 있습니다."
                )
            if mode in {"left", "right"} and not isinstance(
                step.params.get("length"), int
            ):
                raise PlanValidationError(
                    f"{number}단계의 문자열 추출 길이가 올바르지 않습니다."
                )
            if mode == "between" and (
                not step.params.get("start_delimiter")
                or not step.params.get("end_delimiter")
            ):
                raise PlanValidationError(
                    f"{number}단계의 문자열 시작·끝 구분자가 필요합니다."
                )
            current_columns.add(str(step.params["result_column"]))

        if step.function == "add_conditional_column":
            current_columns.add(str(step.params["result_column"]))

        if step.function == "add_date_parts":
            parts = _as_columns(step.params["parts"])
            supported = {"year", "quarter", "month", "week", "day", "weekday"}
            if not parts or not set(parts).issubset(supported):
                raise PlanValidationError(f"{number}단계의 날짜 구성요소가 올바르지 않습니다.")
            labels = {
                "year": "연도", "quarter": "분기", "month": "월",
                "week": "주", "day": "일", "weekday": "요일",
            }
            prefix = step.params.get("result_prefix") or step.params["column"]
            current_columns.update(f"{prefix}_{labels[part]}" for part in parts)

        if step.function == "calculate_column":
            if (step.params.get("right_column") is None) == (step.params.get("value") is None):
                raise PlanValidationError(
                    f"{number}단계는 오른쪽 열과 고정값 중 정확히 하나만 사용해야 합니다."
                )
            if step.params.get("operator") not in {
                "add", "subtract", "multiply", "divide", "percent_of", "absolute_difference"
            }:
                raise PlanValidationError(f"{number}단계의 계산 연산자가 올바르지 않습니다.")
            current_columns.add(str(step.params["result_column"]))

        if step.function in {"rank_rows", "cumulative_sum", "percent_change"}:
            default = {"rank_rows": "순위", "cumulative_sum": "누계", "percent_change": "증감률"}[step.function]
            current_columns.add(str(step.params.get("result_column", default)))

        if step.function == "mark_duplicates":
            current_columns.add(str(step.params.get("result_column", "중복여부")))

        if step.function == "mark_error_values":
            current_columns.update(
                {
                    str(step.params.get("result_column", "오류여부")),
                    str(step.params.get("detail_column", "오류내용")),
                }
            )

        if step.function == "mark_missing_required":
            current_columns.update(
                {
                    str(step.params.get("result_column", "필수값누락")),
                    str(step.params.get("detail_column", "누락열")),
                }
            )

        if step.function == "compare_columns":
            tolerance = step.params.get("tolerance", 0)
            if not isinstance(tolerance, (int, float)) or tolerance < 0:
                raise PlanValidationError(f"{number}단계의 허용 오차가 올바르지 않습니다.")
            current_columns.update(
                {
                    str(step.params.get("difference_column", "차이")),
                    str(step.params.get("match_column", "일치여부")),
                }
            )


def validate_plan_against_data(
    df: pd.DataFrame,
    plan: ExecutionPlan,
    planning_hints: PlanningHints | None = None,
) -> PlanPreview:
    """Validate semantics by executing the approved operations on an in-memory copy."""
    validate_plan(plan, [str(column) for column in df.columns])
    hints = planning_hints or PlanningHints()

    result = df.copy()
    previews: list[StepPreview] = []
    for number, step in enumerate(plan.steps, start=1):
        before_rows = len(result)
        spec = FUNCTION_CATALOG[step.function]
        if spec.phase == "output":
            try:
                affected_rows = validate_output_step(result, step)
            except Exception as exc:
                raise PlanValidationError(
                    f"{number}단계의 출력 설정을 적용할 수 없습니다: {exc}"
                ) from exc
            previews.append(
                StepPreview(
                    function=step.function,
                    params=dict(step.params),
                    before_rows=before_rows,
                    after_rows=before_rows,
                    affected_rows=affected_rows,
                )
            )
            continue
        if step.function == "pivot_table" and step.params.get(
            "aggfunc", "sum"
        ) in {"sum", "mean"}:
            value_columns = _as_columns(step.params["value_column"])
            nonnumeric = [
                column
                for column in value_columns
                if not pd.api.types.is_numeric_dtype(result[column])
            ]
            if nonnumeric:
                raise PlanValidationError(
                    f"{number}단계의 피벗 합계·평균 대상은 숫자 열이어야 합니다: "
                    f"{', '.join(nonnumeric)}"
                )
        if step.function == "add_subtotals" and before_rows > 0:
            group_columns = _as_columns(step.params.get("group_columns"))
            group_count = len(result.loc[:, group_columns].drop_duplicates())
            if group_count > before_rows * 0.5:
                raise PlanValidationError(
                    f"{number}단계의 소계 그룹이 {group_count}개로 전체 {before_rows}행의 "
                    "절반을 초과합니다. 행마다 소계가 생길 수 있으므로 그룹 기준을 "
                    "확인해 주세요."
                )
        operation = spec.function
        if operation is None:
            raise PlanValidationError(f"{number}단계의 데이터 함수가 연결되지 않았습니다.")
        params = dict(step.params)
        params.pop("as_formula", None)
        try:
            result = operation(result, **params)
        except Exception as exc:
            raise PlanValidationError(
                f"{number}단계를 실제 데이터에 적용할 수 없습니다: {exc}"
            ) from exc
        after_rows = len(result)
        if step.function in {
            "filter_rows", "filter_by_conditions", "filter_relative_dates"
        } and before_rows > 0 and after_rows == 0:
            raise PlanValidationError(
                f"{number}단계 필터 결과가 0행입니다. "
                f"조건: {step.params}. "
                "파일을 저장하지 않습니다."
            )
        previews.append(
            StepPreview(
                function=step.function,
                params=dict(step.params),
                before_rows=before_rows,
                after_rows=after_rows,
            )
        )

    return PlanPreview(
        initial_rows=len(df),
        final_rows=len(result),
        steps=previews,
    )
