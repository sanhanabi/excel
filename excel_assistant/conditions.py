from __future__ import annotations


FILTER_OPERATORS = frozenset(
    {
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "contains",
        "startswith",
        "endswith",
        "is_null",
        "not_null",
        "between",
        "in",
        "not_in",
    }
)


def validate_condition(operator: object, value: object) -> None:
    """Validate the common condition contract used by filters and highlights."""
    if operator not in FILTER_OPERATORS:
        raise ValueError("비교 연산자가 올바르지 않습니다.")
    if operator == "between" and (not isinstance(value, list) or len(value) != 2):
        raise ValueError("between 조건에는 시작값과 끝값 두 개가 필요합니다.")
    if operator in {"in", "not_in"} and not isinstance(value, list):
        raise ValueError(f"{operator} 조건에는 값 목록이 필요합니다.")
    if operator not in {"between", "in", "not_in"} and isinstance(
        value, (dict, list, tuple, set)
    ):
        raise ValueError("필터값은 문자열·숫자 같은 단일 값이어야 합니다.")
    if operator not in {"is_null", "not_null"} and value is None:
        raise ValueError(
            "빈값 비교는 == null 대신 is_null 또는 not_null을 사용해야 합니다."
        )
