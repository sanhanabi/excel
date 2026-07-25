from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass, replace
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..catalog import compact_catalog_for_model, resolve_column_lineage
from ..models import (
    ExecutionPlan,
    PlanCorrectionFeedback,
    PlanStep,
    PlanningHints,
    WorkbookProfile,
)
from .base import Planner


STRING_OR_STRINGS = {
    "anyOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}, "minItems": 1},
    ]
}

SCALAR_VALUE = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
    ]
}

SCALAR_OR_LIST = {
    "anyOf": [
        *SCALAR_VALUE["anyOf"],
        {"type": "array", "items": SCALAR_VALUE, "minItems": 1},
    ]
}

STRING_MAP = {
    "type": "object",
    "additionalProperties": {"type": "string"},
}

VALUE_MAP = {
    "type": "object",
    "additionalProperties": SCALAR_VALUE,
}

FILTER_OPERATOR = {
    "type": "string",
    "enum": [
        "==", "!=", ">", ">=", "<", "<=", "contains", "startswith",
        "endswith", "is_null", "not_null", "between", "in", "not_in",
    ],
}

FORMULA_CONDITION_OPERATOR = {
    "type": "string",
    "enum": [
        "==", "!=", ">", ">=", "<", "<=", "contains", "startswith",
        "endswith", "is_null", "not_null",
    ],
}

CONDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "column": {"type": "string"},
        "operator": FILTER_OPERATOR,
        "value": SCALAR_OR_LIST,
    },
    "required": ["column", "operator", "value"],
    "additionalProperties": False,
}

CONDITIONS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": CONDITION_SCHEMA,
}

CONDITION_GROUPS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "conditions": CONDITIONS_SCHEMA,
            "logic": {"type": "string", "enum": ["and", "or"]},
        },
        "required": ["conditions", "logic"],
        "additionalProperties": False,
    },
}

SOURCE_COLUMN_PARAM_NAMES = {
    "column",
    "columns",
    "subset",
    "group_columns",
    "index_columns",
    "pivot_column",
    "value_column",
    "value_columns",
    "left_column",
    "right_column",
    "order_columns",
    "hidden_column",
    "target_columns",
    "start_column",
    "end_column",
    "date_column",
    "condition_column",
    "output_column",
    "label_column",
}

# These functions commonly consume a column produced by an earlier plan step.
# Sequential validation still verifies that the generated column really exists.
GENERATED_COLUMN_CONSUMERS = {
    "sort_rows": {"columns"},
    "select_top_n": {"column"},
    "rank_rows": {"column", "group_columns"},
    "cumulative_sum": {"value_column", "group_columns", "order_columns"},
    "percent_change": {"value_column", "group_columns", "order_columns"},
    "add_subtotals": {"group_columns", "value_columns"},
    "highlight_rows": {"column", "target_columns"},
    "highlight_extremes": {"column"},
    "highlight_missing": {"columns"},
    "format_numbers": {"columns"},
    "color_scale": {"column"},
    "add_total_row": {"value_columns"},
    "add_conditional_summary_row": {
        "condition_column", "column", "value_column", "output_column", "label_column"
    },
    "drop_columns": {"columns"},
    "round_numbers": {"columns"},
    "add_conditional_column": {"column"},
    "combine_columns": {"columns"},
    "extract_text": {"column"},
    "split_column": {"column"},
    "replace_text": {"column"},
}

PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    "remove_empty_rows": {"how": {"type": "string", "enum": ["all", "any"]}},
    "remove_duplicates": {
        "subset": {
            "anyOf": [
                {"type": "null"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "keep": {"type": "string", "enum": ["first", "last"]},
    },
    "drop_rows_missing_keys": {
        "columns": STRING_OR_STRINGS,
        "require": {"type": "string", "enum": ["any", "all"]},
    },
    "normalize_column_names": {"mapping": STRING_MAP},
    "select_columns": {"columns": STRING_OR_STRINGS},
    "reorder_columns": {
        "columns": STRING_OR_STRINGS,
        "keep_remaining": {"type": "boolean"},
    },
    "drop_columns": {"columns": STRING_OR_STRINGS},
    "normalize_text": {
        "columns": STRING_OR_STRINGS,
        "strip": {"type": "boolean"},
        "collapse_whitespace": {"type": "boolean"},
        "case": {"type": "string", "enum": ["preserve", "lower", "upper"]},
    },
    "fill_missing_values": {"values": VALUE_MAP},
    "replace_values": {
        "column": {"type": "string"},
        "replacements": VALUE_MAP,
    },
    "convert_column_type": {
        "column": {"type": "string"},
        "target_type": {
            "type": "string",
            "enum": ["number", "datetime", "date", "string", "boolean"],
        },
        "errors": {"type": "string", "enum": ["raise", "coerce"]},
        "date_format": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "clean_numeric_values": {
        "columns": STRING_OR_STRINGS,
        "errors": {"type": "string", "enum": ["raise", "coerce"]},
        "percent_as_fraction": {"type": "boolean"},
    },
    "round_numbers": {
        "columns": STRING_OR_STRINGS,
        "decimals": {"type": "integer", "minimum": -10, "maximum": 10},
        "mode": {"type": "string", "enum": ["round", "floor", "ceil"]},
    },
    "add_conditional_column": {
        "result_column": {"type": "string", "minLength": 1},
        "conditions": CONDITIONS_SCHEMA,
        "condition_groups": CONDITION_GROUPS_SCHEMA,
        "true_value": SCALAR_VALUE,
        "false_value": SCALAR_VALUE,
        "logic": {"type": "string", "enum": ["and", "or"]},
        "group_logic": {"type": "string", "enum": ["and", "or"]},
    },
    "calculate_date_difference": {
        "start_column": {"type": "string"},
        "end_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "result_column": {"type": "string", "minLength": 1},
        "end_mode": {"type": "string", "enum": ["column", "today"]},
        "unit": {"type": "string", "enum": ["days", "weeks", "months"]},
        "absolute": {"type": "boolean"},
        "as_formula": {"type": "boolean"},
    },
    "combine_columns": {
        "columns": STRING_OR_STRINGS,
        "result_column": {"type": "string", "minLength": 1},
        "separator": {"type": "string"},
        "skip_missing": {"type": "boolean"},
    },
    "extract_text": {
        "column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
        "mode": {
            "type": "string",
            "enum": ["before", "after", "between", "left", "right"],
        },
        "delimiter": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "length": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
        "start_delimiter": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "end_delimiter": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "occurrence": {"type": "string", "enum": ["first", "last"]},
    },
    "split_column": {
        "column": {"type": "string"},
        "result_columns": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 2,
        },
        "delimiter": {"type": "string", "minLength": 1},
        "drop_source": {"type": "boolean"},
    },
    "replace_text": {
        "column": {"type": "string"},
        "old": {"type": "string", "minLength": 1},
        "new": {"type": "string"},
        "case_sensitive": {"type": "boolean"},
    },
    "keep_latest_per_group": {
        "group_columns": STRING_OR_STRINGS,
        "date_column": {"type": "string"},
        "keep_ties": {"type": "boolean"},
    },
    "filter_rows": {
        "column": {"type": "string"},
        "operator": FILTER_OPERATOR,
        "value": {
            **SCALAR_OR_LIST,
            "description": (
                "A single literal comparison value extracted from the user request. "
                "Never return an object or include Korean particles or command words."
            ),
        },
    },
    "filter_by_conditions": {
        "conditions": CONDITIONS_SCHEMA,
        "condition_groups": CONDITION_GROUPS_SCHEMA,
        "logic": {"type": "string", "enum": ["and", "or"]},
        "group_logic": {"type": "string", "enum": ["and", "or"]},
    },
    "filter_relative_dates": {
        "column": {"type": "string"},
        "period": {
            "type": "string",
            "enum": [
                "today", "last_n_days", "older_than_n_days", "this_month",
                "last_month", "this_year", "last_year",
            ],
        },
        "days": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
    },
    "sort_rows": {
        "columns": STRING_OR_STRINGS,
        "ascending": {
            "anyOf": [
                {"type": "boolean"},
                {"type": "array", "items": {"type": "boolean"}, "minItems": 1},
            ]
        },
    },
    "group_sum": {
        "group_columns": STRING_OR_STRINGS,
        "value_column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
    },
    "group_average": {
        "group_columns": STRING_OR_STRINGS,
        "value_column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
    },
    "group_count": {
        "group_columns": STRING_OR_STRINGS,
        "result_column": {"type": "string", "minLength": 1},
    },
    "group_aggregate": {
        "group_columns": STRING_OR_STRINGS,
        "aggregations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "function": {
                        "type": "string",
                        "enum": ["sum", "mean", "count", "nunique", "min", "max", "median"],
                    },
                    "result_column": {"type": "string", "minLength": 1},
                },
                "required": ["column", "function", "result_column"],
                "additionalProperties": False,
            },
        },
    },
    "pivot_table": {
        "index_columns": STRING_OR_STRINGS,
        "pivot_column": {"type": "string"},
        "value_column": STRING_OR_STRINGS,
        "aggfunc": {
            "type": "string",
            "enum": ["sum", "mean", "count", "min", "max"],
        },
        "fill_value": SCALAR_VALUE,
    },
    "select_top_n": {
        "column": {"type": "string"},
        "n": {"type": "integer", "minimum": 1, "maximum": 100000},
        "largest": {"type": "boolean"},
    },
    "mark_duplicates": {
        "subset": {
            "anyOf": [
                {"type": "null"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "result_column": {"type": "string", "minLength": 1},
        "keep": {
            "anyOf": [
                {"type": "string", "enum": ["first", "last"]},
                {"type": "boolean"},
            ]
        },
    },
    "add_date_parts": {
        "column": {"type": "string"},
        "parts": {
            "anyOf": [
                {"type": "string", "enum": ["year", "quarter", "month", "week", "day", "weekday"]},
                {
                    "type": "array",
                    "items": {"type": "string", "enum": ["year", "quarter", "month", "week", "day", "weekday"]},
                    "minItems": 1,
                },
            ]
        },
        "result_prefix": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "calculate_column": {
        "result_column": {"type": "string", "minLength": 1},
        "operator": {
            "type": "string",
            "enum": ["add", "subtract", "multiply", "divide", "percent_of", "absolute_difference"],
        },
        "left_column": {"type": "string"},
        "right_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "as_formula": {"type": "boolean"},
    },
    "rank_rows": {
        "column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
        "ascending": {"type": "boolean"},
        "method": {"type": "string", "enum": ["average", "min", "max", "first", "dense"]},
        "group_columns": {
            "anyOf": [{"type": "null"}, STRING_OR_STRINGS]
        },
    },
    "cumulative_sum": {
        "value_column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
        "group_columns": {"anyOf": [{"type": "null"}, STRING_OR_STRINGS]},
        "order_columns": {"anyOf": [{"type": "null"}, STRING_OR_STRINGS]},
    },
    "percent_change": {
        "value_column": {"type": "string"},
        "result_column": {"type": "string", "minLength": 1},
        "group_columns": {"anyOf": [{"type": "null"}, STRING_OR_STRINGS]},
        "order_columns": {"anyOf": [{"type": "null"}, STRING_OR_STRINGS]},
        "periods": {"type": "integer", "minimum": 1},
    },
    "add_subtotals": {
        "group_columns": STRING_OR_STRINGS,
        "value_columns": STRING_OR_STRINGS,
        "label": {"type": "string"},
        "include_grand_total": {"type": "boolean"},
        "as_formula": {"type": "boolean"},
    },
    "mark_error_values": {
        "columns": {"anyOf": [{"type": "null"}, STRING_OR_STRINGS]},
        "result_column": {"type": "string", "minLength": 1},
        "detail_column": {"type": "string", "minLength": 1},
    },
    "mark_missing_required": {
        "columns": STRING_OR_STRINGS,
        "result_column": {"type": "string", "minLength": 1},
        "detail_column": {"type": "string", "minLength": 1},
    },
    "compare_columns": {
        "left_column": {"type": "string"},
        "right_column": {"type": "string"},
        "difference_column": {"type": "string", "minLength": 1},
        "match_column": {"type": "string", "minLength": 1},
        "tolerance": {"type": "number", "minimum": 0},
    },
    "select_visible_rows": {
        "hidden_column": {"type": "string"},
    },
    "highlight_rows": {
        "column": {"type": "string"},
        "operator": FILTER_OPERATOR,
        "value": SCALAR_OR_LIST,
        "color": {
            "type": "string",
            "enum": ["red", "yellow", "green", "blue", "gray"],
        },
        "target_columns": {
            "anyOf": [{"type": "null"}, STRING_OR_STRINGS],
        },
    },
    "highlight_extremes": {
        "column": {"type": "string"},
        "mode": {
            "type": "string",
            "enum": ["max", "min", "top_n", "bottom_n"],
        },
        "n": {"type": "integer", "minimum": 1, "maximum": 100000},
        "color": {
            "type": "string",
            "enum": ["red", "yellow", "green", "blue", "gray"],
        },
        "include_ties": {"type": "boolean"},
    },
    "highlight_missing": {
        "columns": STRING_OR_STRINGS,
        "color": {
            "type": "string",
            "enum": ["red", "yellow", "green", "blue", "gray"],
        },
    },
    "format_numbers": {
        "columns": STRING_OR_STRINGS,
        "format": {
            "type": "string",
            "enum": [
                "currency", "thousands", "percent", "percent_points",
                "decimal_0", "decimal_1", "decimal_2", "decimal_3",
                "decimal_4", "date", "datetime",
            ],
        },
    },
    "color_scale": {
        "column": {"type": "string"},
        "palette": {
            "type": "string",
            "enum": ["red_yellow_green", "blue_white", "green_white"],
        },
    },
    "add_total_row": {
        "value_columns": STRING_OR_STRINGS,
        "label": {"type": "string", "minLength": 1},
        "aggregate": {
            "type": "string",
            "enum": ["sum", "average", "count", "min", "max"],
        },
    },
    "add_conditional_summary_row": {
        "condition_column": {"type": "string"},
        "operator": FORMULA_CONDITION_OPERATOR,
        "value": SCALAR_VALUE,
        "conditions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "operator": FORMULA_CONDITION_OPERATOR,
                    "value": SCALAR_VALUE,
                },
                "required": ["column", "operator", "value"],
                "additionalProperties": False,
            },
        },
        "logic": {"type": "string", "enum": ["and"]},
        "aggregate": {
            "type": "string",
            "enum": ["sum", "count", "average"],
        },
        "value_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "output_column": {"type": "string"},
        "label": {"type": "string", "minLength": 1},
        "label_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}


class ContextLimitError(ValueError):
    pass


@dataclass(frozen=True)
class InputBudget:
    estimated_tokens: int
    allowed_input_tokens: int
    context_length: int
    reserved_output_tokens: int
    safety_tokens: int
    largest_sections: tuple[tuple[str, int], ...]


SYSTEM_PROMPT = (
    "You are an Excel problem interpreter and execution planner. "
    "Understand the Korean or English user request using the workbook profile. "
    "Use only supplied functions and exact current-sheet column names allowed by "
    "the JSON schema. Observed aliases and grounded hints are clues, not new names. "
    "Never invent functions, columns, Python code, or a cosmetic substitute for an "
    "unsupported request. Return one minimal schema-valid plan."
)


RULES_PROMPT = """RULES:
1. Use the smallest sufficient plan. Do not add cleaning or filtering unless requested.
2. Use only profile columns and listed functions. Preserve an exact profile column name in its requested role. Put uncertainty in assumptions; write goal and descriptions in Korean.
3. If no listed functions can perform the request, set problem_type=unsupported with one internal unsupported_request step. Never fabricate a substitute. Listed pivots, totals, filters, calculations, and formatting are supported; unsupported is for absent actions such as sending email, not mere complexity.
4. pivot_table is for row/index plus column/header cross-tabs and performs its own aggregation. Plain 'X별 Y 합계' uses group_sum. Sort an aggregation result only after aggregation. Example: pivot_table(index_columns=['Region'],pivot_column='Month',value_column='Sales',aggfunc='sum',fill_value=0).
5. matched_values and filter_candidates are data-derived facts. Copy matched values exactly only when relevant.
6. Put multiple measures for one group in one group_aggregate(group_columns=['Region'],aggregations=[{column:'Sales',function:'sum',result_column:'Sales_SUM'},{column:'Fee',function:'mean',result_column:'Fee_AVG'}]). Flat conditions use conditions+logic. Nested '(A AND B) OR (C AND D)' uses condition_groups=[{conditions:[A,B],logic:'and'},{conditions:[C,D],logic:'and'}],group_logic='or'. Never flatten it.
7. calculate_column uses an allowed operator and exactly one of right_column or value. Never generate code or formula text.
8. normalize_column_names coalesces observed aliases before later work. Explain the mapping in assumptions.
9. Output functions come after data functions. Schema enums already constrain colors, formats, palettes, and operators.
10. select_columns is only for an explicit request to discard unlisted columns.
11. To keep rows where keys have values, use drop_rows_missing_keys (require=all when every key must exist). '아직 안 된/미처리/미지급' means keep rows where that status/date/payment column is blank: filter_rows operator=is_null, value=null. drop_rows_missing_keys does the opposite.
12. thousands means comma grouping without a currency symbol; currency includes a symbol.
13. Whole-table summaries use add_total_row. Conditional summaries use add_conditional_summary_row; multiple conditions use conditions with AND. Use add_subtotals only with an explicit group.
14. With CORRECTION FEEDBACK, preserve the complete request and fix only invalid columns, parameters, functions, or order. Do not delete requested work merely to pass validation.
15. add_conditional_column creates IF results; clean_numeric_values parses formatted numbers; keep_latest_per_group keeps newest rows; relative dates use filter_relative_dates, not guessed dates.
16. Preserve every explicit condition and action. Never substitute filtering or replacement for add/summary/extract.

STRING MATCH:
== exact cell; contains substring; startswith prefix; endswith suffix; is_null blank; not_null nonblank. Put only the compared text in value.
Examples: '서울 지점만' -> filter_rows(column='지점',operator='==',value='서울'). '이름에 아트가 들어간' -> filter_rows(column='업체명',operator='contains',value='아트')."""


class OllamaPlanner(Planner):
    def __init__(
        self,
        model: str,
        endpoint: str = "http://127.0.0.1:11434",
        context_length: int = 2048,
        max_tokens: int = 700,
        temperature: float = 0.0,
        timeout_seconds: int = 180,
    ) -> None:
        self._model = model
        self._endpoint = endpoint.rstrip("/")
        self._context_length = context_length
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    def create_plan(
        self,
        user_request: str,
        workbook_profile: WorkbookProfile,
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
        correction_feedback: PlanCorrectionFeedback | None = None,
    ) -> ExecutionPlan:
        available_catalog = self._catalog_for_request(
            function_catalog, planning_hints
        )
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": self._plan_schema(
                available_catalog,
                planning_hints,
                source_columns=workbook_profile.column_names,
            ),
            "messages": [],
            "options": {
                "num_ctx": self._context_length,
                "temperature": self._temperature,
                "seed": 0,
                "num_predict": self._max_tokens,
            },
            "keep_alive": "2m",
        }
        last_format_error: Exception | None = None
        retry_instruction: str | None = None
        for attempt in range(2):
            system_prompt = SYSTEM_PROMPT
            if retry_instruction is not None:
                system_prompt += retry_instruction
            elif attempt:
                system_prompt += (
                    " Previous response format was invalid. Return only one schema-valid "
                    "JSON object without explanations or markdown."
                )
                payload["options"]["seed"] = 1
            messages, input_budget = self._messages_within_budget(
                system_prompt=system_prompt,
                user_request=user_request,
                workbook_profile=workbook_profile,
                function_catalog=available_catalog,
                planning_hints=planning_hints,
                correction_feedback=correction_feedback,
            )
            payload["messages"] = messages
            try:
                result = self._chat_payload(payload, input_budget)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                last_format_error = exc
                continue

            try:
                content = result.get("message", {}).get("content", "")
                raw_plan = json.loads(content)
                if not isinstance(raw_plan, dict):
                    raise ValueError("계획 응답이 JSON 객체가 아닙니다.")
                plan = ExecutionPlan.from_dict(raw_plan)
                plan = self._normalize_unsupported_plan(plan)
                self._validate_plan_shape(plan)
            except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_format_error = exc
                continue
            if plan.problem_type == "unsupported" and attempt == 0:
                retry_instruction = (
                    " Previous response classified the request as unsupported. Re-read the "
                    "complete user request and every available function. If any listed "
                    "function can execute the requested result, return that supported plan. "
                    "Repeat unsupported_request only when no listed function can do it."
                )
                payload["options"]["seed"] = 1
                continue
            return self._repair_plan(plan, user_request, planning_hints)

        raise ValueError(
            "로컬 모델이 작업 계획 형식을 만들지 못했습니다. 요청을 조금 짧게 나누어 "
            "입력하거나 다시 시도해 주세요. 엑셀 파일은 변경되지 않았습니다."
        ) from last_format_error

    def _chat_payload(
        self,
        payload: dict[str, Any],
        input_budget: InputBudget,
    ) -> dict[str, Any]:
        """Execute one structured chat request.

        Planner policy lives in this class while the transport is replaceable.
        The standalone llama.cpp planner overrides only this method so both
        backends use the same prompts, schemas, repairs, and validation flow.
        """

        request = Request(
            f"{self._endpoint}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            raise RuntimeError(
                "Ollama에 연결할 수 없습니다. Ollama가 실행 중인지 확인해 주세요."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Ollama 응답을 읽을 수 없습니다.") from exc
        if not isinstance(result, dict):
            raise ValueError("Ollama 응답이 JSON 객체가 아닙니다.")
        self._guard_actual_prompt_usage(result, input_budget)
        return result

    @staticmethod
    def _normalize_unsupported_plan(plan: ExecutionPlan) -> ExecutionPlan:
        rejection_steps = [
            step for step in plan.steps if step.function == "unsupported_request"
        ]
        if rejection_steps:
            if len(plan.steps) == 1:
                return replace(plan, problem_type="unsupported", steps=[])
            return plan
        if plan.problem_type == "unsupported" and plan.steps:
            return replace(plan, problem_type="other")
        return plan

    @staticmethod
    def _validate_plan_shape(plan: ExecutionPlan) -> None:
        if any(step.function == "unsupported_request" for step in plan.steps):
            raise ValueError("내부 거절 단계의 계획 형식이 올바르지 않습니다.")
        if plan.problem_type == "unsupported":
            if plan.steps:
                raise ValueError("unsupported 계획에는 실행 단계가 없어야 합니다.")
            return
        if not plan.steps:
            raise ValueError("지원 작업 계획에는 실행 단계가 하나 이상 있어야 합니다.")

    @staticmethod
    def _prompt_hint_variants(
        planning_hints: PlanningHints | None,
    ) -> list[PlanningHints | None]:
        if planning_hints is None:
            return [None]
        variants: list[PlanningHints | None] = [planning_hints]
        if planning_hints.filter_candidates:
            variants.append(replace(planning_hints, filter_candidates=[]))
        if planning_hints.to_prompt_dict():
            variants.append(None)
        return variants

    def _messages_within_budget(
        self,
        *,
        system_prompt: str,
        user_request: str,
        workbook_profile: WorkbookProfile,
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None,
        correction_feedback: PlanCorrectionFeedback | None,
    ) -> tuple[list[dict[str, str]], InputBudget]:
        last_error: ContextLimitError | None = None
        for prompt_hints in self._prompt_hint_variants(planning_hints):
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": self._build_prompt(
                        user_request,
                        workbook_profile,
                        function_catalog,
                        prompt_hints,
                        correction_feedback,
                    ),
                },
            ]
            try:
                return messages, self._guard_input_budget(messages)
            except ContextLimitError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise AssertionError("프롬프트 힌트 축소 단계가 비어 있습니다.")

    @property
    def _safety_tokens(self) -> int:
        return max(128, math.ceil(self._context_length * 0.03))

    @property
    def _allowed_input_tokens(self) -> int:
        return self._context_length - self._max_tokens - self._safety_tokens

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        """Conservatively estimate Qwen input size without another tokenizer runtime."""
        ascii_alnum = 0
        ascii_space = 0
        ascii_punctuation = 0
        non_ascii = 0
        for character in text:
            if ord(character) >= 128:
                non_ascii += 1
            elif character.isspace():
                ascii_space += 1
            elif character.isalnum() or character == "_":
                ascii_alnum += 1
            else:
                ascii_punctuation += 1
        raw_estimate = (
            ascii_alnum / 3.5
            + ascii_space / 4
            + ascii_punctuation / 1.5
            + non_ascii * 1.2
        )
        return max(1, math.ceil(raw_estimate * 1.12))

    @classmethod
    def _estimate_message_sections(
        cls,
        messages: list[dict[str, Any]],
    ) -> dict[str, int]:
        sections: dict[str, int] = {}
        label_names = {
            "USER REQUEST": "사용자 요청",
            "WORKBOOK PROFILE": "엑셀 프로필",
            "EXACT SOURCE COLUMNS IN REQUEST": "요청에 명시된 열",
            "DATA-GROUNDED HINTS": "데이터 후보",
            "CORRECTION FEEDBACK": "재계획 정보",
            "AVAILABLE FUNCTIONS": "함수 카탈로그",
            "RULES": "고정 계획 규칙",
        }
        section_pattern = (
            r"\n\n(?=(?:USER REQUEST|WORKBOOK PROFILE|DATA-GROUNDED HINTS|"
            r"EXACT SOURCE COLUMNS IN REQUEST|CORRECTION FEEDBACK|"
            r"AVAILABLE FUNCTIONS|RULES):\n)"
        )
        for message_number, message in enumerate(messages, start=1):
            content = str(message.get("content", ""))
            if message.get("role") == "system":
                sections["시스템 지시"] = cls._estimate_text_tokens(content)
                continue
            for part_number, part in enumerate(
                re.split(section_pattern, content),
                start=1,
            ):
                heading = part.split(":\n", 1)[0]
                label = label_names.get(
                    heading,
                    f"메시지 {message_number}-{part_number}",
                )
                sections[label] = (
                    sections.get(label, 0) + cls._estimate_text_tokens(part)
                )
        return sections

    def _input_budget(self, messages: list[dict[str, Any]]) -> InputBudget:
        sections = self._estimate_message_sections(messages)
        chat_template_overhead = 16 + len(messages) * 12
        estimated_tokens = sum(sections.values()) + chat_template_overhead
        largest_sections = tuple(
            sorted(sections.items(), key=lambda item: item[1], reverse=True)[:3]
        )
        return InputBudget(
            estimated_tokens=estimated_tokens,
            allowed_input_tokens=self._allowed_input_tokens,
            context_length=self._context_length,
            reserved_output_tokens=self._max_tokens,
            safety_tokens=self._safety_tokens,
            largest_sections=largest_sections,
        )

    def _guard_input_budget(
        self,
        messages: list[dict[str, Any]],
    ) -> InputBudget:
        budget = self._input_budget(messages)
        if budget.allowed_input_tokens <= 0:
            raise ContextLimitError(
                "Ollama 컨텍스트 설정이 출력 예약량보다 작습니다. "
                "config.json의 context_length와 max_tokens를 확인해 주세요. "
                "원본 파일은 변경되지 않았습니다."
            )
        if budget.estimated_tokens >= budget.allowed_input_tokens:
            details = ", ".join(
                f"{name} 약 {tokens:,}토큰"
                for name, tokens in budget.largest_sections
            )
            raise ContextLimitError(
                "모델 입력이 컨텍스트 한도를 넘을 가능성이 있어 계획 생성을 중단했습니다. "
                f"설정 한도 {budget.context_length:,}토큰 중 출력 "
                f"{budget.reserved_output_tokens:,}토큰과 안전 여유 "
                f"{budget.safety_tokens:,}토큰을 제외한 입력 한도는 "
                f"{budget.allowed_input_tokens:,}토큰이며, 현재 입력은 약 "
                f"{budget.estimated_tokens:,}토큰입니다. 큰 항목: {details}. "
                "원본 파일은 변경되지 않았습니다."
            )
        return budget

    @staticmethod
    def _guard_actual_prompt_usage(
        result: dict[str, Any],
        budget: InputBudget,
    ) -> None:
        raw_count = result.get("prompt_eval_count")
        if not isinstance(raw_count, int):
            return
        if raw_count >= budget.allowed_input_tokens:
            raise ContextLimitError(
                "Ollama가 처리한 실제 입력이 안전한 컨텍스트 한도에 도달해 "
                "응답을 사용하지 않았습니다. "
                f"설정 한도 {budget.context_length:,}토큰, 실제 입력 "
                f"{raw_count:,}토큰, 허용 입력 {budget.allowed_input_tokens:,}토큰입니다. "
                "입력이 일부 잘렸을 가능성이 있으므로 작업 계획은 실행되지 않았고 "
                "원본 파일은 변경되지 않았습니다."
            )

    @staticmethod
    def _repair_null_comparisons(value: Any) -> Any:
        if isinstance(value, dict):
            repaired = {
                key: OllamaPlanner._repair_null_comparisons(item)
                for key, item in value.items()
            }
            if repaired.get("value") is None:
                if repaired.get("operator") == "==":
                    repaired["operator"] = "is_null"
                elif repaired.get("operator") == "!=":
                    repaired["operator"] = "not_null"
            return repaired
        if isinstance(value, list):
            return [OllamaPlanner._repair_null_comparisons(item) for item in value]
        return value

    @staticmethod
    def _rewrite_column_references(value: Any, lineage: dict[str, str]) -> Any:
        def resolved(column: str) -> str:
            seen: set[str] = set()
            current = column
            while current in lineage and current not in seen:
                seen.add(current)
                current = lineage[current]
            return current

        def rewrite(node: Any, parameter_name: str | None = None) -> Any:
            if parameter_name in SOURCE_COLUMN_PARAM_NAMES:
                if isinstance(node, str):
                    return resolved(node)
                if isinstance(node, list):
                    return [resolved(item) if isinstance(item, str) else rewrite(item) for item in node]
            if isinstance(node, dict):
                return {key: rewrite(item, key) for key, item in node.items()}
            if isinstance(node, list):
                return [rewrite(item) for item in node]
            return node

        return rewrite(value)

    @staticmethod
    def _record_column_lineage(
        lineage: dict[str, str],
        additions: dict[str, str],
    ) -> None:
        for source, result in additions.items():
            for alias, current in list(lineage.items()):
                if current == source:
                    lineage[alias] = result
            lineage[source] = result

    @staticmethod
    def _repair_plan(
        plan: ExecutionPlan,
        user_request: str,
        planning_hints: PlanningHints | None,
    ) -> ExecutionPlan:
        """Apply small, data-independent repairs for common small-model plan splits."""
        normalized_steps = [
            PlanStep(
                function=step.function,
                params=OllamaPlanner._repair_null_comparisons(step.params),
                description=step.description,
            )
            for step in plan.steps
        ]
        merged_steps: list[PlanStep] = []
        for step in normalized_steps:
            if (
                step.function == "group_aggregate"
                and merged_steps
                and merged_steps[-1].function == "group_aggregate"
                and merged_steps[-1].params.get("group_columns")
                == step.params.get("group_columns")
            ):
                previous = merged_steps[-1]
                merged_steps[-1] = PlanStep(
                    function="group_aggregate",
                    params={
                        "group_columns": previous.params["group_columns"],
                        "aggregations": [
                            *previous.params.get("aggregations", []),
                            *step.params.get("aggregations", []),
                        ],
                    },
                    description=(
                        previous.description
                        or "같은 그룹 기준의 여러 집계를 한 번에 계산합니다."
                    ),
                )
            else:
                merged_steps.append(step)

        lineage: dict[str, str] = {}
        repaired_steps: list[PlanStep] = []
        for step in merged_steps:
            repaired_params = OllamaPlanner._rewrite_column_references(
                step.params,
                lineage,
            )
            repaired_step = PlanStep(
                function=step.function,
                params=repaired_params,
                description=step.description,
            )
            repaired_steps.append(repaired_step)
            OllamaPlanner._record_column_lineage(
                lineage,
                resolve_column_lineage(step.function, repaired_params),
            )

        return ExecutionPlan(
            goal=plan.goal,
            steps=repaired_steps,
            column_mapping=plan.column_mapping,
            assumptions=plan.assumptions,
            problem_type=plan.problem_type,
        )

    @staticmethod
    def _catalog_for_request(
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
    ) -> dict[str, Any]:
        if planning_hints is None or not planning_hints.recommended_functions:
            return function_catalog
        selected = {
            name: function_catalog[name]
            for name in planning_hints.recommended_functions
            if name in function_catalog
        }
        return selected or function_catalog

    @staticmethod
    def _plan_schema(
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
        source_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        step_variants: list[dict[str, Any]] = []
        supported_problem_types = [
            "cleaning", "filtering", "sorting", "aggregation",
            "pivoting", "ranking", "calculation", "validation",
            "formatting", "multi_sheet", "other",
        ]
        generated_columns_expected = True
        for function_name, spec in function_catalog.items():
            properties = {
                key: deepcopy(value)
                for key, value in PARAM_SCHEMAS.get(function_name, {}).items()
                if key in spec.get("allowed_params", [])
            }
            if source_columns:
                properties = OllamaPlanner._constrain_source_column_params(
                    function_name,
                    properties,
                    source_columns,
                    allow_generated_columns=generated_columns_expected,
                )
            params_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "required": spec.get("required_params", []),
                "additionalProperties": False,
            }
            if function_name in {"filter_by_conditions", "add_conditional_column"}:
                params_schema["oneOf"] = [
                    {"required": ["conditions"]},
                    {"required": ["condition_groups"]},
                ]
            step_variants.append(
                {
                    "type": "object",
                    "properties": {
                        "function": {"const": function_name},
                        "params": params_schema,
                        "description": {"type": "string"},
                    },
                    "required": ["function", "params", "description"],
                    "additionalProperties": False,
                }
            )
        step_variants.append(
            {
                "type": "object",
                "properties": {
                    "function": {"const": "unsupported_request"},
                    "params": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "description": {"type": "string"},
                },
                "required": ["function", "params", "description"],
                "additionalProperties": False,
            }
        )
        return {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {"oneOf": step_variants},
                    "minItems": 1,
                },
                "problem_type": {
                    "type": "string",
                    "enum": [*supported_problem_types, "unsupported"],
                },
                "column_mapping": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "string",
                        **({"enum": source_columns} if source_columns else {}),
                    },
                },
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "goal", "steps", "problem_type", "column_mapping", "assumptions"
            ],
            "additionalProperties": False,
        }

    @staticmethod
    def _constrain_source_column_params(
        function_name: str,
        properties: dict[str, Any],
        source_columns: list[str],
        allow_generated_columns: bool = False,
    ) -> dict[str, Any]:
        """Replace free-form source-column strings with file-derived enum choices."""
        excluded = (
            GENERATED_COLUMN_CONSUMERS.get(function_name, set())
            if allow_generated_columns
            else set()
        )

        def constrain(node: Any) -> Any:
            if not isinstance(node, dict):
                return node
            constrained = deepcopy(node)
            nested_properties = constrained.get("properties")
            if isinstance(nested_properties, dict):
                for name, schema in list(nested_properties.items()):
                    if name in SOURCE_COLUMN_PARAM_NAMES and name not in excluded:
                        nested_properties[name] = constrain_column_schema(schema)
                    else:
                        nested_properties[name] = constrain(schema)
            if "items" in constrained:
                constrained["items"] = constrain(constrained["items"])
            for variant_key in ("anyOf", "oneOf", "allOf"):
                if isinstance(constrained.get(variant_key), list):
                    constrained[variant_key] = [
                        constrain(item) for item in constrained[variant_key]
                    ]
            return constrained

        def constrain_column_schema(schema: Any) -> Any:
            if not isinstance(schema, dict):
                return schema
            constrained = deepcopy(schema)
            if constrained.get("type") == "string":
                constrained["enum"] = list(source_columns)
            if constrained.get("type") == "array" and isinstance(
                constrained.get("items"), dict
            ):
                items = deepcopy(constrained["items"])
                if items.get("type") == "string":
                    items["enum"] = list(source_columns)
                constrained["items"] = items
            for variant_key in ("anyOf", "oneOf", "allOf"):
                if isinstance(constrained.get(variant_key), list):
                    constrained[variant_key] = [
                        constrain_column_schema(item)
                        for item in constrained[variant_key]
                    ]
            return constrained

        result: dict[str, Any] = {}
        for name, schema in properties.items():
            if name in SOURCE_COLUMN_PARAM_NAMES and name not in excluded:
                result[name] = constrain_column_schema(schema)
            else:
                result[name] = constrain(schema)
        if function_name == "normalize_column_names" and "mapping" in result:
            result["mapping"]["propertyNames"] = {"enum": source_columns}
        if function_name == "fill_missing_values" and "values" in result:
            result["values"]["propertyNames"] = {"enum": source_columns}
        return result

    @staticmethod
    def _build_prompt(
        user_request: str,
        workbook_profile: WorkbookProfile,
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
        correction_feedback: PlanCorrectionFeedback | None = None,
    ) -> str:
        normalized_request = user_request.casefold()
        explicit_columns = sorted(
            (
                column
                for column in workbook_profile.column_names
                if column.casefold() in normalized_request
            ),
            key=lambda column: normalized_request.index(column.casefold()),
        )
        return "\n\n".join(
            [
                "USER REQUEST:\n" + user_request,
                "WORKBOOK PROFILE:\n"
                + json.dumps(workbook_profile.to_prompt_dict(), ensure_ascii=False),
                "EXACT SOURCE COLUMNS IN REQUEST:\n"
                + json.dumps(explicit_columns, ensure_ascii=False),
                "DATA-GROUNDED HINTS:\n"
                + json.dumps(
                    planning_hints.to_prompt_dict() if planning_hints else {},
                    ensure_ascii=False,
                ),
                "CORRECTION FEEDBACK:\n"
                + json.dumps(
                    correction_feedback.to_prompt_dict()
                    if correction_feedback
                    else {},
                    ensure_ascii=False,
                ),
                "AVAILABLE FUNCTIONS:\n"
                + json.dumps(
                    compact_catalog_for_model(function_catalog),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                RULES_PROMPT,
            ]
        )
