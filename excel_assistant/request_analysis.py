from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class ActionRequirement:
    name: str
    label: str
    allowed_functions: frozenset[str]


@dataclass(frozen=True)
class RequestAnalysis:
    actions: tuple[ActionRequirement, ...]
    recommended_functions: tuple[str, ...]

    @property
    def action_names(self) -> set[str]:
        return {item.name for item in self.actions}

    @property
    def needs_conditions(self) -> bool:
        return bool(
            self.action_names.intersection(
                {
                    "filter",
                    "date_range_filter",
                    "conditional_column",
                    "conditional_summary",
                    "highlight",
                }
            )
        )


def _contains_any(text: str, fragments: tuple[str, ...]) -> bool:
    return any(fragment in text for fragment in fragments)


def extract_explicit_date_period(user_request: str) -> tuple[date, date] | None:
    """Return an explicitly named calendar year, half-year, or quarter."""
    text = " ".join(user_request.casefold().split())

    korean = re.search(
        r"(?P<year>(?:19|20)\d{2})\s*년"
        r"(?:\s*(?:(?P<half>[상하])반기|(?P<quarter>[1-4])\s*분기))?",
        text,
    )
    if korean:
        year = int(korean.group("year"))
        if korean.group("half"):
            start_month = 1 if korean.group("half") == "상" else 7
            end_month = 6 if start_month == 1 else 12
        elif korean.group("quarter"):
            quarter = int(korean.group("quarter"))
            start_month = (quarter - 1) * 3 + 1
            end_month = start_month + 2
        else:
            start_month, end_month = 1, 12
        return (
            date(year, start_month, 1),
            date(year, end_month, calendar.monthrange(year, end_month)[1]),
        )

    english_patterns = (
        r"(?P<half>first|second)\s+half\s+(?:of\s+)?(?P<year>(?:19|20)\d{2})",
        r"(?P<year>(?:19|20)\d{2})\s+(?P<half>first|second)\s+half",
        r"q(?P<quarter>[1-4])\s+(?:of\s+)?(?P<year>(?:19|20)\d{2})",
        r"(?P<year>(?:19|20)\d{2})\s+q(?P<quarter>[1-4])",
    )
    for pattern in english_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        year = int(match.group("year"))
        half = match.groupdict().get("half")
        quarter_text = match.groupdict().get("quarter")
        if half:
            start_month = 1 if half == "first" else 7
            end_month = 6 if start_month == 1 else 12
        else:
            quarter = int(quarter_text)
            start_month = (quarter - 1) * 3 + 1
            end_month = start_month + 2
        return (
            date(year, start_month, 1),
            date(year, end_month, calendar.monthrange(year, end_month)[1]),
        )

    year_match = re.search(r"\b(?P<year>(?:19|20)\d{2})\b", text)
    if year_match:
        year = int(year_match.group("year"))
        return date(year, 1, 1), date(year, 12, 31)
    return None


def _requests_period_filter(text: str, period: tuple[date, date] | None) -> bool:
    if period is None:
        return False
    return _contains_any(
        text,
        (
            "만 남",
            "만 유지",
            "거래만",
            "기록만",
            "자료만",
            "데이터만",
            "골라",
            "추려",
            "keep ",
            "only",
            "filter ",
            "transactions from",
            "transactions in",
            "records from",
            "records in",
        ),
    )


def _requests_calculated_column(text: str) -> bool:
    arithmetic = _contains_any(
        text,
        (
            "곱해서",
            "곱하여",
            "곱한",
            "더해서",
            "더하여",
            "빼서",
            "빼고",
            "나눠서",
            "나누어",
            "multiply",
            "multiplied",
            "product of",
            " times ",
            "divide",
            "divided by",
            "subtract",
            "plus",
        ),
    )
    creates_result = _contains_any(
        text,
        (
            "만들",
            "계산",
            "새 열",
            "열 추가",
            "create",
            "calculate",
            "new column",
            "add column",
        ),
    )
    return arithmetic and creates_result


def analyze_request(user_request: str) -> RequestAnalysis:
    """Extract broad Excel actions without depending on workbook-specific values."""
    text = " ".join(user_request.casefold().split())
    explicit_period = extract_explicit_date_period(text)
    explicit_period_filter = _requests_period_filter(text, explicit_period)
    calculated_column = _requests_calculated_column(text)
    actions: list[ActionRequirement] = []
    recommended: list[str] = []

    def add(
        name: str,
        label: str,
        functions: tuple[str, ...],
        recommendations: tuple[str, ...] | None = None,
    ) -> None:
        if any(item.name == name for item in actions):
            return
        actions.append(ActionRequirement(name, label, frozenset(functions)))
        for function in recommendations or functions:
            if function not in recommended:
                recommended.append(function)

    conditional_summary = _contains_any(
        text,
        (
            "조건부 합계", "조건부 평균", "조건부 개수", "sumif", "sumifs",
            "countif", "countifs", "averageif", "averageifs",
            "conditional sum", "conditional average", "conditional count",
        ),
    )
    conditional_column = (
        _contains_any(
            text,
            (
                "새 열", "열을 추가", "열 추가", "new column", "add a column",
                "add column", "create a column", "create column",
            ),
        )
        and _contains_any(
            text,
            ("이면", "아니면", "인 경우", "일 경우", "여부", " if ", "when ", "otherwise"),
        )
    )
    pivot = _contains_any(text, ("피벗", "pivot", "cross-tab", "crosstab")) or (
        _contains_any(text, ("행으로", "행에 놓", "행 기준", " in rows", " as rows", "row field"))
        and _contains_any(text, ("열로", "열에 놓", "열 기준", " in columns", " as columns", "column field"))
    )
    top_n = bool(
        re.search(
            r"(?:상위|하위)\s*\d+\s*개|\b(?:top|bottom)\s+\d+\b",
            text,
        )
    )
    filter_rows = explicit_period_filter or _contains_any(
        text,
        (
            "행만 남", "행만 유지", "기록만 남", "것만 남", "자료만 남", "내역만 남",
            "데이터만 남",
            "필터", "골라", "추려", "제외해", "빼줘", "keep only", "keep rows",
            "keep records", "keep entries", "show only", "filter ", "filter rows", "exclude ", "remove rows",
        ),
    )
    if not top_n and "거래만 남" in text:
        filter_rows = True
    sort_rows = _contains_any(
        text,
        (
            "정렬", "오름차순", "내림차순", "큰 순서", "작은 순서", "큰 것부터", "작은 것부터",
            "최신인 순서", "오래된 순서", "sort ", "sort by", "ascending", "descending",
            "largest first", "highest first", "smallest first", "lowest first",
            "newest first", "latest first", "oldest first", "cheapest first",
            "most expensive first",
        ),
    )
    grouped_summary = (
        _contains_any(text, ("별로", "별 ", "group by", "grouped by", "for each", " by "))
        and _contains_any(
            text,
            ("합계", "평균", "개수", "최솟값", "최댓값", "집계", "sum", "total", "average", "mean", "count", "minimum", "maximum"),
        )
        and not pivot
    )
    total_row = _contains_any(
        text, ("합계 행", "합계행", "total row", "grand total")
    ) and _contains_any(
        text, ("맨 아래", "아래에", "붙여", "추가", "bottom", "append", "add ")
    )

    if conditional_summary:
        add(
            "conditional_summary",
            "조건부 요약행 추가",
            ("add_conditional_summary_row",),
        )
    if conditional_column:
        add(
            "conditional_column",
            "조건에 따른 새 열 생성",
            ("add_conditional_column",),
        )
    if calculated_column:
        add(
            "calculated_column",
            "산술 계산 열 생성",
            ("calculate_column",),
        )
    if pivot:
        add("pivot", "피벗표 생성", ("pivot_table",))
    if grouped_summary:
        add(
            "group_summary",
            "그룹별 집계",
            ("group_sum", "group_average", "group_count", "group_aggregate"),
            ("group_aggregate", "group_sum", "group_average", "group_count"),
        )
    if filter_rows:
        filter_functions = ["filter_rows", "filter_by_conditions", "drop_rows_missing_keys"]
        if _contains_any(
            text,
            (
                "오늘", "이번 달", "지난 달", "올해", "작년", "최근 ", "일 이상", "일 이내",
                "today", "this month", "last month", "this year", "last year",
                "recent ", "last ", "days ago", "older than",
            ),
        ):
            filter_functions.append("filter_relative_dates")
        add("filter", "행 필터링", tuple(filter_functions))
    if explicit_period_filter:
        add(
            "date_range_filter",
            "명시 기간 필터링",
            ("filter_rows", "filter_by_conditions"),
        )
    if sort_rows:
        add("sort", "행 정렬", ("sort_rows",))
    if total_row and not conditional_summary:
        add("total_row", "표 아래 요약행 추가", ("add_total_row",))

    if _contains_any(text, ("반올림", "올림", "내림", "round", "rounding", "floor", "ceil")):
        add("round", "숫자 반올림", ("round_numbers",))
    if (
        _contains_any(text, ("글자", "문자", "텍스트", "character", "characters", "text"))
        and _contains_any(
            text,
            ("앞 ", "뒤 ", "뽑", "추출", "first ", "last ", "extract", "take "),
        )
    ) or _contains_any(text, ("left(", "right(", "mid(")):
        add("extract_text", "문자열 일부 추출", ("extract_text",))
    if _contains_any(text, ("경과일", "며칠", "날짜 차이", "일수 계산", "days since")):
        add("date_difference", "날짜 차이 계산", ("calculate_date_difference",))
    if _contains_any(text, ("나눠", "분리", "쪼개", "split", "separate")) and _contains_any(
        text, ("열", "구분자", "delimiter", "column", "separator")
    ):
        add("split_column", "열 분리", ("split_column",))
    if _contains_any(text, ("합쳐", "결합", "이어 붙", "concat", "textjoin", "combine", "join")) and _contains_any(text, ("열", "column")):
        add("combine_columns", "열 결합", ("combine_columns",))
    if _contains_any(text, ("바꿔", "변경", "치환", "replace", "change ", "substitute")):
        add("replace", "값 또는 텍스트 변경", ("replace_values", "replace_text"))
    if _contains_any(text, ("열 삭제", "열을 삭제", "열 빼", "열 제거", "drop column", "delete column", "remove column")):
        add("drop_columns", "열 삭제", ("drop_columns",))
    if _contains_any(text, ("열만 남", "열만 보여", "열만 선택", "keep only the column", "select column", "show only the column")):
        add("select_columns", "열 선택", ("select_columns",))
    if _contains_any(text, ("열 순서", "열을 앞으로", "열을 뒤로", "reorder column", "column order", "move the column")):
        add("reorder_columns", "열 순서 변경", ("reorder_columns",))
    if _contains_any(text, ("천 단위", "콤마", "통화", "퍼센트 형식", "날짜 형식", "thousands separator", "comma format", "currency format", "percentage format", "date format")):
        add("format", "표시 형식 적용", ("format_numbers",))
    if _contains_any(text, ("강조", "색칠", "색으로", "눈에 띄게", "highlight", "color the", "colour the", "color scale")):
        add(
            "highlight",
            "조건부 강조",
            ("highlight_rows", "highlight_extremes", "highlight_missing", "color_scale"),
        )
    if _contains_any(text, ("중복 제거", "중복을 제거", "remove duplicates", "drop duplicates", "deduplicate")):
        add("remove_duplicates", "중복 제거", ("remove_duplicates",))
    elif _contains_any(text, ("중복 표시", "중복 여부", "중복 찾아", "mark duplicates", "flag duplicates", "find duplicates")):
        add("mark_duplicates", "중복 표시", ("mark_duplicates",))
    if _contains_any(text, ("최신 것만", "최근 것만", "가장 최근인", "가장 최신인", "최근인 거래", "마지막 기록만", "latest only", "newest only", "most recent", "latest record")) and _contains_any(
        text, ("별로", "마다", "그룹", "per ", "for each", "group")
    ):
        add("latest_per_group", "그룹별 최신 행 유지", ("keep_latest_per_group",))
    if _contains_any(text, ("순위", "랭킹", "rank", "ranking")):
        add("rank", "순위 계산", ("rank_rows", "select_top_n"))
    if top_n:
        add("top_n", "상위·하위 N개 선택", ("select_top_n",))
    if _contains_any(text, ("누계", "누적합", "cumulative", "running total")):
        add("cumulative", "누계 계산", ("cumulative_sum",))
    if _contains_any(text, ("증감률", "변화율", "percent change")):
        add("percent_change", "증감률 계산", ("percent_change",))
    if _contains_any(text, ("소계", "subtotal", "sub-total")):
        add("subtotals", "그룹별 소계", ("add_subtotals",))

    return RequestAnalysis(tuple(actions), tuple(recommended[:12]))
