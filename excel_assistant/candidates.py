from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .models import ExecutionPlan, PlanStep


@dataclass(frozen=True)
class FilterCandidate:
    candidate_id: str
    column: str
    operator: str
    value: str
    row_count: int
    matched_values: tuple[str, ...]

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "column": self.column,
            "operator": self.operator,
            "value": self.value,
            "expected_rows": self.row_count,
            "matched_values": list(self.matched_values),
        }

    def to_plan(self, user_request: str) -> ExecutionPlan:
        operator_text = {
            "==": "정확히 같은",
            "contains": "포함하는",
            "startswith": "시작하는",
            "endswith": "끝나는",
        }[self.operator]
        return ExecutionPlan(
            goal=user_request,
            problem_type="filtering",
            column_mapping={"요청한 값": self.column},
            assumptions=[
                f"실제 데이터에서 {self.row_count:,}행이 일치하는 조건을 선택했습니다."
            ],
            steps=[
                PlanStep(
                    "filter_rows",
                    {
                        "column": self.column,
                        "operator": self.operator,
                        "value": self.value,
                    },
                    f"{self.column}이(가) '{self.value}'와(과) {operator_text} 행만 남깁니다.",
                )
            ],
        )


def _longest_common_substrings(
    left: str,
    right: str,
    minimum_length: int,
) -> set[str]:
    if not left or not right:
        return set()
    previous = [0] * (len(right) + 1)
    longest = 0
    fragments: set[str] = set()
    for left_index, left_char in enumerate(left, start=1):
        current = [0] * (len(right) + 1)
        for right_index, right_char in enumerate(right, start=1):
            if left_char != right_char:
                continue
            length = previous[right_index - 1] + 1
            current[right_index] = length
            if length < minimum_length:
                continue
            fragment = left[left_index - length : left_index]
            if length > longest:
                longest = length
                fragments = {fragment}
            elif length == longest:
                fragments.add(fragment)
        previous = current
    return fragments if longest >= minimum_length else set()


def generate_filter_candidates(
    df: pd.DataFrame,
    user_request: str,
    *,
    minimum_fragment_length: int = 2,
    max_unique_values: int = 100,
    max_candidates: int = 40,
) -> list[FilterCandidate]:
    """Create executable string-filter candidates from request/data overlap."""
    normalized_request = user_request.casefold()
    raw_candidates: list[
        tuple[str, str, str, int, tuple[str, ...], tuple[int, ...]]
    ] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_column in df.columns:
        column = str(raw_column)
        source = df[raw_column]
        unique_values = source.dropna().drop_duplicates()
        if len(unique_values) == 0 or len(unique_values) > max_unique_values:
            continue
        text_values = [str(value).strip() for value in unique_values.tolist()]
        normalized_series = source.fillna("").astype(str).str.strip().str.casefold()
        fragments: set[str] = set()
        for text_value in text_values:
            normalized_value = text_value.casefold()
            if len(normalized_value) > 80:
                continue
            fragments.update(
                _longest_common_substrings(
                    normalized_value,
                    normalized_request,
                    minimum_fragment_length,
                )
            )

        for normalized_fragment in fragments:
            request_index = normalized_request.find(normalized_fragment)
            if request_index < 0:
                continue
            fragment = user_request[
                request_index : request_index + len(normalized_fragment)
            ]
            masks = {
                "==": normalized_series.eq(normalized_fragment),
                "contains": normalized_series.str.contains(
                    normalized_fragment, regex=False, na=False
                ),
                "startswith": normalized_series.str.startswith(
                    normalized_fragment, na=False
                ),
                "endswith": normalized_series.str.endswith(
                    normalized_fragment, na=False
                ),
            }
            for operator, mask in masks.items():
                row_count = int(mask.sum())
                key = (column, operator, fragment)
                if row_count == 0 or key in seen:
                    continue
                seen.add(key)
                matched_values = tuple(
                    dict.fromkeys(
                        source.loc[mask]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .tolist()
                    )
                )[:8]
                row_signature = tuple(
                    index
                    for index, matched in enumerate(mask.tolist())
                    if bool(matched)
                )
                raw_candidates.append(
                    (
                        column,
                        operator,
                        fragment,
                        row_count,
                        matched_values,
                        row_signature,
                    )
                )

    operator_order = {"==": 0, "contains": 1, "startswith": 2, "endswith": 3}
    raw_candidates.sort(
        key=lambda item: (
            -len(item[2]),
            operator_order[item[1]],
            item[0],
            item[3],
        )
    )
    deduplicated: list[
        tuple[str, str, str, int, tuple[str, ...], tuple[int, ...]]
    ] = []
    result_sets: set[tuple[str, str, tuple[int, ...]]] = set()
    for item in raw_candidates:
        result_key = (item[0], item[2], item[5])
        if result_key in result_sets:
            continue
        result_sets.add(result_key)
        deduplicated.append(item)

    return [
        FilterCandidate(
            candidate_id=f"F{index}",
            column=column,
            operator=operator,
            value=value,
            row_count=row_count,
            matched_values=matched_values,
        )
        for index, (
            column,
            operator,
            value,
            row_count,
            matched_values,
            _row_signature,
        )
        in enumerate(deduplicated[:max_candidates], start=1)
    ]
