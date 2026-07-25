from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


def _as_columns(value: list[str] | str) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _present_mask(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype("string").str.strip().ne("")


def _condition_mask(
    series: pd.Series,
    operator: str,
    value: Any = None,
) -> pd.Series:
    if operator == "is_null":
        return ~_present_mask(series)
    if operator == "not_null":
        return _present_mask(series)

    comparison_value = value
    if pd.api.types.is_datetime64_any_dtype(series):
        if operator in {"between", "in", "not_in"} and isinstance(value, list):
            comparison_value = [pd.to_datetime(item) for item in value]
        else:
            comparison_value = pd.to_datetime(value)

    operators = {
        "==": lambda: series == comparison_value,
        "!=": lambda: series != comparison_value,
        ">": lambda: series > comparison_value,
        ">=": lambda: series >= comparison_value,
        "<": lambda: series < comparison_value,
        "<=": lambda: series <= comparison_value,
        "contains": lambda: series.astype("string").str.contains(
            str(comparison_value), na=False, regex=False
        ),
        "startswith": lambda: series.astype("string").str.startswith(
            str(comparison_value), na=False
        ),
        "endswith": lambda: series.astype("string").str.endswith(
            str(comparison_value), na=False
        ),
        "between": lambda: series.between(
            comparison_value[0], comparison_value[1], inclusive="both"
        ),
        "in": lambda: series.isin(comparison_value),
        "not_in": lambda: ~series.isin(comparison_value),
    }
    if operator not in operators:
        raise ValueError(f"지원하지 않는 비교 연산자입니다: {operator}")
    if operator == "between" and (
        not isinstance(comparison_value, list) or len(comparison_value) != 2
    ):
        raise ValueError("between 연산자는 시작값과 끝값 두 개가 필요합니다.")
    if operator in {"in", "not_in"} and not isinstance(comparison_value, list):
        raise ValueError(f"{operator} 연산자는 값 목록이 필요합니다.")
    return operators[operator]().fillna(False)


def _combine_condition_masks(
    df: pd.DataFrame,
    conditions: list[dict[str, Any]],
    logic: str,
) -> pd.Series:
    if not conditions:
        raise ValueError("조건이 하나 이상 필요합니다.")
    if logic not in {"and", "or"}:
        raise ValueError("조건 결합 방식은 and 또는 or이어야 합니다.")
    masks = [
        _condition_mask(
            df[condition["column"]],
            str(condition["operator"]),
            condition.get("value"),
        )
        for condition in conditions
    ]
    combined = masks[0]
    for mask in masks[1:]:
        combined = combined & mask if logic == "and" else combined | mask
    return combined


def _nested_condition_mask(
    df: pd.DataFrame,
    *,
    conditions: list[dict[str, Any]] | None,
    logic: str,
    condition_groups: list[dict[str, Any]] | None,
    group_logic: str,
) -> pd.Series:
    has_flat = bool(conditions)
    has_groups = bool(condition_groups)
    if has_flat == has_groups:
        raise ValueError("conditions 또는 condition_groups 중 정확히 하나가 필요합니다.")
    if has_flat:
        return _combine_condition_masks(df, list(conditions or []), logic)
    if group_logic not in {"and", "or"}:
        raise ValueError("그룹 결합 방식은 and 또는 or이어야 합니다.")
    group_masks = [
        _combine_condition_masks(
            df,
            list(group.get("conditions") or []),
            str(group.get("logic", "and")),
        )
        for group in condition_groups or []
    ]
    combined = group_masks[0]
    for mask in group_masks[1:]:
        combined = combined & mask if group_logic == "and" else combined | mask
    return combined


def remove_empty_rows(df: pd.DataFrame, how: str = "all") -> pd.DataFrame:
    return df.copy().dropna(how=how).reset_index(drop=True)


def remove_duplicates(
    df: pd.DataFrame,
    subset: list[str] | None = None,
    keep: str = "first",
) -> pd.DataFrame:
    return df.copy().drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)


def drop_rows_missing_keys(
    df: pd.DataFrame,
    columns: list[str] | str,
    require: str = "any",
) -> pd.DataFrame:
    keys = _as_columns(columns)
    if not keys:
        raise ValueError("기준 열이 필요합니다.")
    present = pd.concat([_present_mask(df[column]) for column in keys], axis=1)
    if require == "any":
        mask = present.any(axis=1)
    elif require == "all":
        mask = present.all(axis=1)
    else:
        raise ValueError("require는 'any' 또는 'all'이어야 합니다.")
    return df.copy().loc[mask].reset_index(drop=True)


def normalize_column_names(
    df: pd.DataFrame,
    mapping: dict[str, str],
) -> pd.DataFrame:
    source = df.copy()
    missing = [column for column in mapping if column not in source.columns]
    if missing:
        raise KeyError(f"이름을 바꿀 열을 찾을 수 없습니다: {', '.join(missing)}")
    for old_name, new_name in mapping.items():
        if old_name == new_name:
            continue
        if new_name in source.columns:
            source[new_name] = source[new_name].combine_first(source[old_name])
            source = source.drop(columns=[old_name])
        else:
            source = source.rename(columns={old_name: new_name})
    return source


def select_columns(df: pd.DataFrame, columns: list[str] | str) -> pd.DataFrame:
    selected = _as_columns(columns)
    return df.copy().loc[:, selected]


def reorder_columns(
    df: pd.DataFrame,
    columns: list[str] | str,
    keep_remaining: bool = True,
) -> pd.DataFrame:
    first = _as_columns(columns)
    remaining = [str(column) for column in df.columns if str(column) not in first]
    ordered = first + remaining if keep_remaining else first
    return df.copy().loc[:, ordered]


def drop_columns(
    df: pd.DataFrame,
    columns: list[str] | str,
) -> pd.DataFrame:
    removed = _as_columns(columns)
    if not removed:
        raise ValueError("제거할 열이 하나 이상 필요합니다.")
    if len(removed) >= len(df.columns):
        raise ValueError("모든 열을 제거할 수는 없습니다.")
    return df.copy().drop(columns=removed)


def normalize_text(
    df: pd.DataFrame,
    columns: list[str] | str,
    strip: bool = True,
    collapse_whitespace: bool = True,
    case: str = "preserve",
) -> pd.DataFrame:
    source = df.copy()
    if case not in {"preserve", "lower", "upper"}:
        raise ValueError("case는 preserve, lower, upper 중 하나여야 합니다.")
    for column in _as_columns(columns):
        original = source[column]
        converted = original.astype("string")
        if strip:
            converted = converted.str.strip()
        if collapse_whitespace:
            converted = converted.str.replace(r"\s+", " ", regex=True)
        if case == "lower":
            converted = converted.str.lower()
        elif case == "upper":
            converted = converted.str.upper()
        source[column] = converted.where(original.notna(), pd.NA)
    return source


def fill_missing_values(
    df: pd.DataFrame,
    values: dict[str, Any],
) -> pd.DataFrame:
    return df.copy().fillna(value=values)


def replace_values(
    df: pd.DataFrame,
    column: str,
    replacements: dict[str, Any],
) -> pd.DataFrame:
    source = df.copy()
    source[column] = source[column].replace(replacements)
    return source


def convert_column_type(
    df: pd.DataFrame,
    column: str,
    target_type: str,
    errors: str = "raise",
    date_format: str | None = None,
) -> pd.DataFrame:
    source = df.copy()
    if errors not in {"raise", "coerce"}:
        raise ValueError("errors는 raise 또는 coerce여야 합니다.")
    if target_type == "number":
        source[column] = pd.to_numeric(source[column], errors=errors)
    elif target_type in {"datetime", "date"}:
        converted = pd.to_datetime(source[column], errors=errors, format=date_format)
        source[column] = converted.dt.normalize() if target_type == "date" else converted
    elif target_type == "string":
        source[column] = source[column].astype("string")
    elif target_type == "boolean":
        truthy = {"true", "1", "yes", "y", "예", "네", "참"}
        falsy = {"false", "0", "no", "n", "아니오", "아니요", "거짓"}

        def to_boolean(value: Any) -> Any:
            if pd.isna(value):
                return pd.NA
            normalized = str(value).strip().casefold()
            if normalized in truthy:
                return True
            if normalized in falsy:
                return False
            if errors == "coerce":
                return pd.NA
            raise ValueError(f"불리언으로 바꿀 수 없는 값입니다: {value!r}")

        source[column] = source[column].map(to_boolean).astype("boolean")
    else:
        raise ValueError(f"지원하지 않는 자료형입니다: {target_type}")
    return source


def clean_numeric_values(
    df: pd.DataFrame,
    columns: list[str] | str,
    errors: str = "raise",
    percent_as_fraction: bool = False,
) -> pd.DataFrame:
    if errors not in {"raise", "coerce"}:
        raise ValueError("errors는 raise 또는 coerce여야 합니다.")
    source = df.copy()

    def clean_value(value: Any) -> Any:
        if pd.isna(value):
            return pd.NA
        if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
            return value
        text = str(value).strip()
        if not text:
            return pd.NA
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1].strip()
        is_percent = text.endswith("%")
        if is_percent:
            text = text[:-1].strip()
        text = re.sub(r"^(?:KRW|USD|EUR|JPY)\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*(?:원)$", "", text)
        text = text.translate(str.maketrans("", "", ", ₩$€£¥"))
        number = pd.to_numeric(text, errors="raise")
        if negative:
            number = -abs(number)
        if is_percent and percent_as_fraction:
            number = number / 100
        return number

    for column in _as_columns(columns):
        if errors == "raise":
            source[column] = source[column].map(clean_value)
        else:
            def clean_or_missing(value: Any) -> Any:
                try:
                    return clean_value(value)
                except (TypeError, ValueError):
                    return pd.NA

            source[column] = source[column].map(clean_or_missing)
        source[column] = pd.to_numeric(source[column], errors="coerce")
    return source


def round_numbers(
    df: pd.DataFrame,
    columns: list[str] | str,
    decimals: int = 0,
    mode: str = "round",
) -> pd.DataFrame:
    if mode not in {"round", "floor", "ceil"}:
        raise ValueError("mode는 round, floor, ceil 중 하나여야 합니다.")
    if not isinstance(decimals, int) or not -10 <= decimals <= 10:
        raise ValueError("decimals는 -10부터 10 사이의 정수여야 합니다.")
    source = df.copy()
    factor = 10.0 ** decimals
    for column in _as_columns(columns):
        original = source[column]
        numeric = pd.to_numeric(original, errors="coerce")
        invalid = _present_mask(original) & numeric.isna()
        if invalid.any():
            raise ValueError(f"반올림 대상 열에 숫자가 아닌 값이 있습니다: {column}")
        scaled = numeric * factor
        if mode == "round":
            result = np.sign(scaled) * np.floor(np.abs(scaled) + 0.5)
        elif mode == "floor":
            result = np.floor(scaled)
        else:
            result = np.ceil(scaled)
        source[column] = result / factor
    return source


def add_conditional_column(
    df: pd.DataFrame,
    result_column: str,
    conditions: list[dict[str, Any]] | None = None,
    true_value: Any = None,
    false_value: Any = None,
    logic: str = "and",
    condition_groups: list[dict[str, Any]] | None = None,
    group_logic: str = "or",
) -> pd.DataFrame:
    combined = _nested_condition_mask(
        df,
        conditions=conditions,
        logic=logic,
        condition_groups=condition_groups,
        group_logic=group_logic,
    )
    source = df.copy()
    source[result_column] = np.where(combined, true_value, false_value)
    return source


def calculate_date_difference(
    df: pd.DataFrame,
    start_column: str,
    end_column: str | None = None,
    result_column: str = "날짜차이",
    end_mode: str = "column",
    unit: str = "days",
    absolute: bool = False,
) -> pd.DataFrame:
    if end_mode not in {"column", "today"}:
        raise ValueError("end_mode는 column 또는 today여야 합니다.")
    if end_mode == "column" and not end_column:
        raise ValueError("column 방식에는 종료 날짜 열이 필요합니다.")
    if end_mode == "today" and end_column is not None:
        raise ValueError("today 방식에는 종료 날짜 열을 함께 사용할 수 없습니다.")
    if unit not in {"days", "weeks", "months"}:
        raise ValueError("unit은 days, weeks, months 중 하나여야 합니다.")
    source = df.copy()
    start = pd.to_datetime(source[start_column], errors="coerce")
    if end_mode == "today":
        end = pd.Series(pd.Timestamp.today().normalize(), index=source.index)
    else:
        end = pd.to_datetime(source[str(end_column)], errors="coerce")
    invalid_start = _present_mask(source[start_column]) & start.isna()
    invalid_end = (
        pd.Series(False, index=source.index)
        if end_mode == "today"
        else _present_mask(source[str(end_column)]) & end.isna()
    )
    if invalid_start.any() or invalid_end.any():
        raise ValueError("날짜 차이 대상 열에 날짜로 해석할 수 없는 값이 있습니다.")
    days = (end.dt.normalize() - start.dt.normalize()).dt.days.astype("Float64")
    if absolute:
        days = days.abs()
    if unit == "days":
        result = days
    elif unit == "weeks":
        result = days / 7
    else:
        forward = end.ge(start)
        earlier = start.where(forward, end)
        later = end.where(forward, start)
        months = (
            (later.dt.year - earlier.dt.year) * 12
            + later.dt.month
            - earlier.dt.month
            - later.dt.day.lt(earlier.dt.day).astype("Int64")
        ).astype("Float64")
        if not absolute:
            months = months.where(forward, -months)
        result = months.where(start.notna() & end.notna())
    source[result_column] = result
    return source


def combine_columns(
    df: pd.DataFrame,
    columns: list[str] | str,
    result_column: str,
    separator: str = " ",
    skip_missing: bool = True,
) -> pd.DataFrame:
    selected = _as_columns(columns)
    if not selected:
        raise ValueError("결합할 열이 하나 이상 필요합니다.")
    source = df.copy()

    def combine_row(row: pd.Series) -> str:
        values: list[str] = []
        for column in selected:
            value = row[column]
            present = not pd.isna(value) and str(value).strip() != ""
            if present:
                values.append(str(value))
            elif not skip_missing:
                values.append("")
        return separator.join(values)

    source[result_column] = source.apply(combine_row, axis=1)
    return source


def extract_text(
    df: pd.DataFrame,
    column: str,
    result_column: str,
    mode: str,
    delimiter: str | None = None,
    length: int | None = None,
    start_delimiter: str | None = None,
    end_delimiter: str | None = None,
    occurrence: str = "first",
) -> pd.DataFrame:
    if mode not in {"before", "after", "between", "left", "right"}:
        raise ValueError("지원하지 않는 문자열 추출 방식입니다.")
    if occurrence not in {"first", "last"}:
        raise ValueError("occurrence는 first 또는 last여야 합니다.")
    if mode in {"before", "after"} and not delimiter:
        raise ValueError("before/after 방식에는 delimiter가 필요합니다.")
    if mode in {"left", "right"} and (not isinstance(length, int) or length < 1):
        raise ValueError("left/right 방식에는 1 이상의 length가 필요합니다.")
    if mode == "between" and (not start_delimiter or not end_delimiter):
        raise ValueError("between 방식에는 시작·끝 구분자가 필요합니다.")
    source = df.copy()
    text = source[column].astype("string")
    if mode == "left":
        result = text.str[:length]
    elif mode == "right":
        result = text.str[-length:]
    elif mode in {"before", "after"}:
        split_index = 0 if mode == "before" else 1
        if occurrence == "first":
            result = text.str.split(str(delimiter), n=1, regex=False).str[split_index]
        else:
            result = text.str.rsplit(str(delimiter), n=1).str[split_index]
    else:
        after_start = text.str.split(str(start_delimiter), n=1, regex=False).str[1]
        result = after_start.str.split(str(end_delimiter), n=1, regex=False).str[0]
    source[result_column] = result.where(source[column].notna(), pd.NA)
    return source


def split_column(
    df: pd.DataFrame,
    column: str,
    result_columns: list[str],
    delimiter: str,
    drop_source: bool = False,
) -> pd.DataFrame:
    if not delimiter:
        raise ValueError("열 분리 구분자는 비어 있을 수 없습니다.")
    if len(result_columns) < 2 or len(result_columns) != len(set(result_columns)):
        raise ValueError("서로 다른 결과 열 이름이 두 개 이상 필요합니다.")
    collisions = [name for name in result_columns if name in df.columns]
    if collisions:
        raise ValueError(f"결과 열 이름이 기존 열과 겹칩니다: {', '.join(collisions)}")
    source = df.copy()
    text = source[column].astype("string")
    parts = text.str.split(
        delimiter,
        n=len(result_columns) - 1,
        expand=True,
        regex=False,
    ).reindex(columns=range(len(result_columns)))
    for index, result_column in enumerate(result_columns):
        source[result_column] = parts[index].where(source[column].notna(), pd.NA)
    if drop_source:
        source = source.drop(columns=[column])
    return source


def replace_text(
    df: pd.DataFrame,
    column: str,
    old: str,
    new: str = "",
    case_sensitive: bool = True,
) -> pd.DataFrame:
    if not old:
        raise ValueError("찾을 문자열은 비어 있을 수 없습니다.")
    source = df.copy()

    def replace_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if case_sensitive:
            return value.replace(old, new)
        return re.sub(re.escape(old), lambda _match: new, value, flags=re.IGNORECASE)

    source[column] = source[column].map(replace_value)
    return source


def keep_latest_per_group(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    date_column: str,
    keep_ties: bool = False,
) -> pd.DataFrame:
    groups = _as_columns(group_columns)
    if not groups:
        raise ValueError("그룹 기준 열이 필요합니다.")
    source = df.copy().reset_index(drop=True)
    dates = pd.to_datetime(source[date_column], errors="coerce")
    invalid = _present_mask(source[date_column]) & dates.isna()
    if invalid.any():
        raise ValueError("최신 기록 기준 열에 날짜로 해석할 수 없는 값이 있습니다.")
    latest = dates.groupby(
        [source[column] for column in groups],
        dropna=False,
    ).transform("max")
    if latest.isna().any():
        raise ValueError("날짜가 없는 그룹은 최신 기록을 결정할 수 없습니다.")
    matches = dates.eq(latest)
    if keep_ties:
        return source.loc[matches].reset_index(drop=True)
    selected = source.loc[matches].groupby(groups, dropna=False, sort=False).tail(1)
    return selected.sort_index().reset_index(drop=True)


def filter_rows(
    df: pd.DataFrame,
    column: str,
    operator: str,
    value: Any,
) -> pd.DataFrame:
    source = df.copy()
    return source.loc[_condition_mask(source[column], operator, value)].reset_index(
        drop=True
    )


def filter_by_conditions(
    df: pd.DataFrame,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    condition_groups: list[dict[str, Any]] | None = None,
    group_logic: str = "or",
) -> pd.DataFrame:
    combined = _nested_condition_mask(
        df,
        conditions=conditions,
        logic=logic,
        condition_groups=condition_groups,
        group_logic=group_logic,
    )
    return df.copy().loc[combined].reset_index(drop=True)


def filter_relative_dates(
    df: pd.DataFrame,
    column: str,
    period: str,
    days: int | None = None,
) -> pd.DataFrame:
    supported = {
        "today",
        "last_n_days",
        "older_than_n_days",
        "this_month",
        "last_month",
        "this_year",
        "last_year",
    }
    if period not in supported:
        raise ValueError(f"지원하지 않는 상대 날짜 범위입니다: {period}")
    if period in {"last_n_days", "older_than_n_days"} and (
        not isinstance(days, int) or days < 1
    ):
        raise ValueError("상대 날짜 일수는 1 이상의 정수여야 합니다.")
    if period not in {"last_n_days", "older_than_n_days"} and days is not None:
        raise ValueError("선택한 상대 날짜 범위에는 days를 함께 사용할 수 없습니다.")

    source = df.copy()
    dates = pd.to_datetime(source[column], errors="coerce").dt.normalize()
    invalid = _present_mask(source[column]) & dates.isna()
    if invalid.any():
        raise ValueError("상대 날짜 필터 열에 날짜로 해석할 수 없는 값이 있습니다.")
    today = pd.Timestamp.today().normalize()
    if period == "today":
        mask = dates.eq(today)
    elif period == "last_n_days":
        start = today - pd.Timedelta(int(days) - 1, unit="D")
        mask = dates.between(start, today, inclusive="both")
    elif period == "older_than_n_days":
        mask = dates.lt(today - pd.Timedelta(int(days), unit="D"))
    elif period == "this_month":
        mask = dates.dt.to_period("M").eq(today.to_period("M"))
    elif period == "last_month":
        mask = dates.dt.to_period("M").eq(today.to_period("M") - 1)
    elif period == "this_year":
        mask = dates.dt.year.eq(today.year)
    else:
        mask = dates.dt.year.eq(today.year - 1)
    return source.loc[mask.fillna(False)].reset_index(drop=True)


def mark_duplicates(
    df: pd.DataFrame,
    subset: list[str] | None = None,
    result_column: str = "중복여부",
    keep: str | bool = False,
) -> pd.DataFrame:
    source = df.copy()
    source[result_column] = source.duplicated(subset=subset, keep=keep)
    return source


def sort_rows(
    df: pd.DataFrame,
    columns: list[str] | str,
    ascending: list[bool] | bool = True,
) -> pd.DataFrame:
    return df.copy().sort_values(by=columns, ascending=ascending).reset_index(drop=True)


def group_sum(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    value_column: str,
    result_column: str = "합계",
) -> pd.DataFrame:
    grouped = (
        df.copy()
        .groupby(group_columns, dropna=False, as_index=False)[value_column]
        .sum()
    )
    return grouped.rename(columns={value_column: result_column})


def group_average(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    value_column: str,
    result_column: str = "평균",
) -> pd.DataFrame:
    grouped = (
        df.copy()
        .groupby(group_columns, dropna=False, as_index=False)[value_column]
        .mean()
    )
    return grouped.rename(columns={value_column: result_column})


def group_count(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    result_column: str = "개수",
) -> pd.DataFrame:
    return (
        df.copy()
        .groupby(group_columns, dropna=False)
        .size()
        .reset_index(name=result_column)
    )


def group_aggregate(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    aggregations: list[dict[str, str]],
) -> pd.DataFrame:
    groups = _as_columns(group_columns)
    if not aggregations:
        raise ValueError("집계 항목이 하나 이상 필요합니다.")
    supported = {
        "sum", "mean", "count", "size", "nunique", "min", "max", "median"
    }
    named: dict[str, pd.NamedAgg] = {}
    for item in aggregations:
        column = item["column"]
        function = item["function"]
        result_column = item["result_column"]
        if function not in supported:
            raise ValueError(f"지원하지 않는 집계 방식입니다: {function}")
        if result_column in named:
            raise ValueError(f"집계 결과 열 이름이 중복됩니다: {result_column}")
        named[result_column] = pd.NamedAgg(column=column, aggfunc=function)
    return (
        df.copy()
        .groupby(groups, dropna=False, as_index=False)
        .agg(**named)
        .reset_index(drop=True)
    )


def pivot_table(
    df: pd.DataFrame,
    index_columns: list[str] | str,
    pivot_column: str,
    value_column: list[str] | str,
    aggfunc: str = "sum",
    fill_value: Any = 0,
) -> pd.DataFrame:
    supported_aggregations = {"sum", "mean", "count", "min", "max"}
    if aggfunc not in supported_aggregations:
        raise ValueError(f"지원하지 않는 피벗 집계 방식입니다: {aggfunc}")

    indexes = [index_columns] if isinstance(index_columns, str) else list(index_columns)
    if not indexes:
        raise ValueError("피벗의 행 기준 열이 필요합니다.")
    if pivot_column in indexes:
        raise ValueError("피벗의 행 기준 열과 열 기준은 서로 달라야 합니다.")

    result = pd.pivot_table(
        df.copy(),
        index=indexes,
        columns=pivot_column,
        values=value_column,
        aggfunc=aggfunc,
        fill_value=fill_value,
        observed=True,
        dropna=True,
    ).reset_index()
    result.columns.name = None
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            " ".join(str(part) for part in column if str(part)).strip()
            for column in result.columns
        ]
    else:
        result.columns = [str(column) for column in result.columns]
    return result.reset_index(drop=True)


def select_top_n(
    df: pd.DataFrame,
    column: str,
    n: int = 10,
    largest: bool = True,
) -> pd.DataFrame:
    source = df.copy()
    result = source.nlargest(n, column) if largest else source.nsmallest(n, column)
    return result.reset_index(drop=True)


def add_date_parts(
    df: pd.DataFrame,
    column: str,
    parts: list[str] | str,
    result_prefix: str | None = None,
) -> pd.DataFrame:
    source = df.copy()
    dates = pd.to_datetime(source[column], errors="coerce")
    part_names = _as_columns(parts)
    supported = {"year", "quarter", "month", "week", "day", "weekday"}
    labels = {
        "year": "연도",
        "quarter": "분기",
        "month": "월",
        "week": "주",
        "day": "일",
        "weekday": "요일",
    }
    prefix = result_prefix if result_prefix is not None else column
    for part in part_names:
        if part not in supported:
            raise ValueError(f"지원하지 않는 날짜 구성요소입니다: {part}")
        if part == "year":
            values = dates.dt.year
        elif part == "quarter":
            values = dates.dt.quarter
        elif part == "month":
            values = dates.dt.month
        elif part == "week":
            values = dates.dt.isocalendar().week
        elif part == "day":
            values = dates.dt.day
        else:
            values = dates.dt.dayofweek + 1
        source[f"{prefix}_{labels[part]}"] = values
    return source


def calculate_column(
    df: pd.DataFrame,
    result_column: str,
    operator: str,
    left_column: str,
    right_column: str | None = None,
    value: float | int | None = None,
) -> pd.DataFrame:
    source = df.copy()
    left = pd.to_numeric(source[left_column], errors="coerce")
    if right_column is not None:
        right: Any = pd.to_numeric(source[right_column], errors="coerce")
    elif value is not None:
        right = value
    else:
        raise ValueError("오른쪽 열 또는 고정값 중 하나가 필요합니다.")
    if operator == "add":
        result = left + right
    elif operator == "subtract":
        result = left - right
    elif operator == "multiply":
        result = left * right
    elif operator == "divide":
        result = left.div(right).where(right != 0)
    elif operator == "percent_of":
        result = left.div(right).where(right != 0) * 100
    elif operator == "absolute_difference":
        result = (left - right).abs()
    else:
        raise ValueError(f"지원하지 않는 계산 연산자입니다: {operator}")
    source[result_column] = result
    return source


def rank_rows(
    df: pd.DataFrame,
    column: str,
    result_column: str = "순위",
    ascending: bool = False,
    method: str = "dense",
    group_columns: list[str] | str | None = None,
) -> pd.DataFrame:
    source = df.copy()
    if method not in {"average", "min", "max", "first", "dense"}:
        raise ValueError(f"지원하지 않는 순위 방식입니다: {method}")
    if group_columns:
        source[result_column] = source.groupby(
            _as_columns(group_columns), dropna=False
        )[column].rank(ascending=ascending, method=method)
    else:
        source[result_column] = source[column].rank(
            ascending=ascending, method=method
        )
    return source


def cumulative_sum(
    df: pd.DataFrame,
    value_column: str,
    result_column: str = "누계",
    group_columns: list[str] | str | None = None,
    order_columns: list[str] | str | None = None,
) -> pd.DataFrame:
    source = df.copy()
    if order_columns:
        source = source.sort_values(_as_columns(order_columns), kind="stable")
    values = pd.to_numeric(source[value_column], errors="coerce").fillna(0)
    if group_columns:
        source[result_column] = values.groupby(
            [source[column] for column in _as_columns(group_columns)], dropna=False
        ).cumsum()
    else:
        source[result_column] = values.cumsum()
    return source.reset_index(drop=True)


def percent_change(
    df: pd.DataFrame,
    value_column: str,
    result_column: str = "증감률",
    group_columns: list[str] | str | None = None,
    order_columns: list[str] | str | None = None,
    periods: int = 1,
) -> pd.DataFrame:
    source = df.copy()
    if order_columns:
        source = source.sort_values(_as_columns(order_columns), kind="stable")
    values = pd.to_numeric(source[value_column], errors="coerce")
    if group_columns:
        source[result_column] = values.groupby(
            [source[column] for column in _as_columns(group_columns)], dropna=False
        ).pct_change(periods=periods) * 100
    else:
        source[result_column] = values.pct_change(periods=periods) * 100
    return source.reset_index(drop=True)


def add_subtotals(
    df: pd.DataFrame,
    group_columns: list[str] | str,
    value_columns: list[str] | str,
    label: str = "소계",
    include_grand_total: bool = True,
) -> pd.DataFrame:
    source = df.copy()
    groups = _as_columns(group_columns)
    values = _as_columns(value_columns)
    records: list[dict[str, Any]] = []
    for _, group in source.groupby(groups, dropna=False, sort=False):
        records.extend(group.to_dict(orient="records"))
        subtotal = {column: pd.NA for column in source.columns}
        subtotal[groups[0]] = label
        for column in values:
            subtotal[column] = pd.to_numeric(group[column], errors="coerce").sum()
        records.append(subtotal)
    if include_grand_total:
        total = {column: pd.NA for column in source.columns}
        total[groups[0]] = "전체 합계"
        for column in values:
            total[column] = pd.to_numeric(source[column], errors="coerce").sum()
        records.append(total)
    return pd.DataFrame.from_records(records, columns=source.columns) if records else source


def mark_error_values(
    df: pd.DataFrame,
    columns: list[str] | str | None = None,
    result_column: str = "오류여부",
    detail_column: str = "오류내용",
) -> pd.DataFrame:
    source = df.copy()
    selected = _as_columns(columns) if columns else [str(item) for item in source.columns]
    error_pattern = re.compile(r"^#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|SPILL!|CALC!)$")

    def details(row: pd.Series) -> str:
        found = [
            f"{column}={row[column]}"
            for column in selected
            if isinstance(row[column], str) and error_pattern.match(row[column].strip())
        ]
        return "; ".join(found)

    source[detail_column] = source.apply(details, axis=1)
    source[result_column] = source[detail_column].ne("")
    return source


def mark_missing_required(
    df: pd.DataFrame,
    columns: list[str] | str,
    result_column: str = "필수값누락",
    detail_column: str = "누락열",
) -> pd.DataFrame:
    source = df.copy()
    required = _as_columns(columns)
    missing_masks = {column: ~_present_mask(source[column]) for column in required}
    source[detail_column] = [
        ", ".join(column for column in required if bool(missing_masks[column].iloc[index]))
        for index in range(len(source))
    ]
    source[result_column] = source[detail_column].ne("")
    return source


def compare_columns(
    df: pd.DataFrame,
    left_column: str,
    right_column: str,
    difference_column: str = "차이",
    match_column: str = "일치여부",
    tolerance: float = 0.0,
) -> pd.DataFrame:
    source = df.copy()
    left = pd.to_numeric(source[left_column], errors="coerce")
    right = pd.to_numeric(source[right_column], errors="coerce")
    difference = left - right
    source[difference_column] = difference
    source[match_column] = difference.abs().le(float(tolerance)) & left.notna() & right.notna()
    return source


def select_visible_rows(
    df: pd.DataFrame,
    hidden_column: str = "_숨김행",
) -> pd.DataFrame:
    if hidden_column not in df.columns:
        raise KeyError("숨김 행 정보가 로드되지 않았습니다.")
    return df.copy().loc[~df[hidden_column].fillna(False).astype(bool)].reset_index(drop=True)
