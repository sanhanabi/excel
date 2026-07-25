from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .candidates import generate_filter_candidates
from .models import MatchedValue, PlanningHints
from .request_analysis import analyze_request


def _normalized(value: Any) -> str:
    return str(value).strip().casefold()


def _literal_pattern(value: str) -> str:
    escaped = re.escape(value.casefold())
    if re.match(r"[a-z0-9_]", value.casefold()):
        escaped = rf"(?<![a-z0-9_]){escaped}"
    if re.search(r"[a-z0-9_]$", value.casefold()):
        escaped = rf"{escaped}(?![a-z0-9_])"
    return escaped


def build_planning_hints(
    df: pd.DataFrame,
    user_request: str,
    max_unique_values: int = 100,
) -> PlanningHints:
    """Match literal request text to real, low-cardinality text values in the table."""
    normalized_request = _normalized(user_request)
    matches: list[MatchedValue] = []
    column_mentions: list[tuple[int, int, str]] = []
    for raw_column in df.columns:
        column = str(raw_column)
        column_mentions.extend(
            (item.start(), item.end(), column)
            for item in re.finditer(_literal_pattern(column), normalized_request)
        )

    for raw_column in df.columns:
        column = str(raw_column)
        series = df[raw_column]
        unique_values = series.dropna().drop_duplicates()
        if len(unique_values) > max_unique_values:
            continue
        for raw_value in unique_values.tolist():
            if not isinstance(raw_value, str):
                continue
            exact_value = raw_value.strip()
            normalized_value = _normalized(exact_value)
            if len(normalized_value) < 2 or len(normalized_value) > 80:
                continue
            value_positions = [
                item.start()
                for item in re.finditer(
                    _literal_pattern(normalized_value),
                    normalized_request,
                )
            ]
            if not value_positions:
                continue
            belongs_to_column = False
            for position in value_positions:
                preceding = [
                    item
                    for item in column_mentions
                    if item[1] <= position and position - item[1] <= 35
                ]
                if not preceding:
                    belongs_to_column = True
                    break
                nearest = max(preceding, key=lambda item: item[1])
                connector = normalized_request[nearest[1] : position]
                if nearest[2] == column or re.search(
                    r"이거나|거나|또는|혹은|\bor\b",
                    connector,
                ):
                    belongs_to_column = True
                    break
            if not belongs_to_column:
                continue
            row_count = int(
                series.fillna("")
                .astype(str)
                .str.strip()
                .str.casefold()
                .eq(normalized_value)
                .sum()
            )
            matches.append(
                MatchedValue(
                    request_text=exact_value,
                    column=column,
                    exact_value=exact_value,
                    row_count=row_count,
                )
            )

    matches.sort(key=lambda item: (-len(item.exact_value), item.column))
    matches = matches[:5]
    filter_candidates = generate_filter_candidates(
        df,
        user_request,
        max_unique_values=max_unique_values,
        max_candidates=12,
    )
    exact_matches = {
        (item.column, item.exact_value.casefold())
        for item in matches
    }
    filter_candidates = [
        item
        for item in filter_candidates
        if not (
            item.operator == "=="
            and (item.column, item.value.casefold()) in exact_matches
        )
    ][:6]
    analysis = analyze_request(user_request)
    return PlanningHints(
        matched_values=matches,
        filter_candidates=[item.to_prompt_dict() for item in filter_candidates],
        recommended_functions=list(analysis.recommended_functions),
    )
