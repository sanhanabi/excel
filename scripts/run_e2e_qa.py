from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from excel_assistant.catalog import catalog_for_prompt
from excel_assistant.excel_io import (
    build_profile,
    detect_tables,
    load_detected_table,
)
from excel_assistant.executor import execute_plan
from excel_assistant.grounding import build_planning_hints
from excel_assistant.models import ExecutionPlan
from excel_assistant.planners.factory import build_planner
from excel_assistant.planning import create_validated_plan


@dataclass(frozen=True)
class QaCase:
    name: str
    workbook: str
    sheet: str
    request: str
    validate: Callable[[pd.DataFrame, pd.DataFrame, ExecutionPlan], dict]


class _CapturingPlanner:
    def __init__(self, delegate):
        self.delegate = delegate
        self.last_plan: ExecutionPlan | None = None

    def create_plan(self, *args, **kwargs):
        self.last_plan = self.delegate.create_plan(*args, **kwargs)
        return self.last_plan


def _step(plan: ExecutionPlan, function: str):
    return next(item for item in plan.steps if item.function == function)


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype("string").str.strip().eq("")


def _validate_nested_flag(source, result, plan):
    amount_column = "$ RCVD"
    amount = pd.to_numeric(result[amount_column], errors="coerce")
    date_blank = _blank(result["Date rcvd"])
    expected = ((amount > 0) & date_blank) | ((~date_blank) & amount.isna())
    conditional = _step(plan, "add_conditional_column")
    result_column = conditional.params["result_column"]
    actual = result[result_column].astype(str).eq("확인필요")
    groups = conditional.params.get("condition_groups")
    mismatch_count = int((actual.reset_index(drop=True) != expected.reset_index(drop=True)).sum())
    return {
        "passed": bool(
            isinstance(groups, list)
            and len(groups) == 2
            and conditional.params.get("group_logic") == "or"
            and mismatch_count == 0
        ),
        "expected_flags": int(expected.sum()),
        "actual_flags": int(actual.sum()),
        "uses_nested_groups": isinstance(groups, list) and len(groups) == 2,
        "mismatch_count": mismatch_count,
        "condition_groups": groups,
    }


def _validate_calculated_period_summary(source, result, plan):
    dates = pd.to_datetime(source["날짜"])
    expected = source.assign(
        판매금액=pd.to_numeric(source["단가"]) * pd.to_numeric(source["수량"])
    ).loc[
        (dates >= pd.Timestamp("2020-01-01"))
        & (dates <= pd.Timestamp("2020-06-30"))
    ]
    aggregation = _step(plan, "group_aggregate")
    named = {
        item["result_column"]: pd.NamedAgg(
            column=item["column"],
            aggfunc=item["function"],
        )
        for item in aggregation.params["aggregations"]
    }
    expected = (
        expected.groupby("문구점", as_index=False, dropna=False)
        .agg(**named)
        .sort_values(_step(plan, "sort_rows").params["columns"], ascending=False)
        .reset_index(drop=True)
    )
    comparable = list(expected.columns)
    actual = result.loc[:, comparable].reset_index(drop=True)
    return {
        "passed": actual.round(8).equals(expected.round(8)),
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "expected_total": float(expected.filter(like="합계").sum().sum()),
        "actual_total": float(actual.filter(like="합계").sum().sum()),
    }


def _validate_pivot(source, result, plan):
    expected = pd.pivot_table(
        source,
        index=["문구점"],
        columns="품목",
        values="수량",
        aggfunc="sum",
        fill_value=0,
        observed=True,
    ).reset_index()
    expected.columns.name = None
    expected.columns = [str(item) for item in expected.columns]
    columns = sorted(expected.columns)
    actual = result.loc[:, columns].sort_values("문구점").reset_index(drop=True)
    expected = expected.loc[:, columns].sort_values("문구점").reset_index(drop=True)
    return {
        "passed": actual.equals(expected),
        "expected_shape": list(expected.shape),
        "actual_shape": list(actual.shape),
    }


def _validate_nested_filter(source, result, plan):
    expected = (
        ((source["문구점"] == "화진아트") & (source["단가"] >= 1000))
        | ((source["문구점"] == "모닝아트") & (source["수량"] >= 70))
    )
    expected_rows = source.loc[expected].sort_values("날짜", ascending=False)
    filter_step = _step(plan, "filter_by_conditions")
    groups = filter_step.params.get("condition_groups")
    return {
        "passed": bool(
            isinstance(groups, list)
            and len(groups) == 2
            and result["날짜"].reset_index(drop=True).equals(
                expected_rows["날짜"].reset_index(drop=True)
            )
        ),
        "expected_rows": len(expected_rows),
        "actual_rows": len(result),
        "uses_nested_groups": isinstance(groups, list) and len(groups) == 2,
    }


def _validate_text_extract(source, result, plan):
    expected = source[
        source["Description"].astype("string").str.contains("FPMT", na=False)
    ].sort_values("Date rcvd", ascending=False)
    extract = _step(plan, "extract_text")
    result_column = extract.params["result_column"]
    prefixes = result["Lookup Code"].astype("string").str[:5]
    actual_prefixes = result[result_column].astype("string").reset_index(drop=True)
    expected_prefixes = prefixes.reset_index(drop=True)
    return {
        "passed": bool(
            len(result) == len(expected)
            and actual_prefixes.equals(expected_prefixes)
            and result["Date rcvd"].reset_index(drop=True).equals(
                expected["Date rcvd"].reset_index(drop=True)
            )
        ),
        "expected_rows": len(expected),
        "actual_rows": len(result),
        "extract_params": dict(extract.params),
        "prefix_mismatches": int((actual_prefixes != expected_prefixes).sum()),
        "date_order_matches": bool(
            result["Date rcvd"].reset_index(drop=True).equals(
                expected["Date rcvd"].reset_index(drop=True)
            )
        ),
    }


def _validate_filtered_group(source, result, plan):
    filtered = source[source["Type"] == "Client"]
    aggregation = _step(plan, "group_aggregate")
    items = aggregation.params["aggregations"]
    valid_lineage = all(
        item["column"] == "$ RCVD"
        for item in items
        if item["function"] in {"sum", "mean"}
    )
    named = {
        item["result_column"]: pd.NamedAgg(
            column=item["column"],
            aggfunc=item["function"],
        )
        for item in items
    }
    expected = filtered.groupby("Method", as_index=False, dropna=False).agg(**named)
    sort_column = _step(plan, "sort_rows").params["columns"][0]
    expected = expected.sort_values(sort_column, ascending=False).reset_index(drop=True)
    actual = result.loc[:, expected.columns].reset_index(drop=True)
    return {
        "passed": bool(valid_lineage and actual.round(8).equals(expected.round(8))),
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "valid_aggregate_lineage": valid_lineage,
    }


def _validate_latest_per_group(source, result, plan):
    dates = pd.to_datetime(source["날짜"])
    expected = source.loc[
        dates.eq(dates.groupby(source["문구점"]).transform("max"))
    ].sort_values("날짜", ascending=False)
    return {
        "passed": bool(
            len(result) == len(expected)
            and result["날짜"].reset_index(drop=True).equals(
                expected["날짜"].reset_index(drop=True)
            )
        ),
        "expected_rows": len(expected),
        "actual_rows": len(result),
    }


def _validate_rank_top(source, result, plan):
    expected_values = (
        pd.to_numeric(source["단가"]) * pd.to_numeric(source["수량"])
    ).nlargest(10).reset_index(drop=True)
    actual_values = pd.to_numeric(result["판매금액"]).reset_index(drop=True)
    rank_step = _step(plan, "rank_rows")
    rank_column = rank_step.params.get("result_column", "순위")
    return {
        "passed": bool(
            len(result) == 10
            and actual_values.equals(expected_values)
            and rank_column in result.columns
        ),
        "expected_rows": 10,
        "actual_rows": len(result),
        "select_params": dict(_step(plan, "select_top_n").params),
        "rank_params": dict(rank_step.params),
        "expected_values": expected_values.tolist(),
        "actual_values": actual_values.tolist(),
    }


def _cases() -> list[QaCase]:
    amount = "$ RCVD"
    return [
        QaCase(
            "nested_flag",
            "ledger",
            "DEC23",
            f"{amount}가 0보다 큰데 Date rcvd가 비어 있거나, Date rcvd는 "
            f"있는데 {amount}가 비어 있으면 '확인필요'라는 새 열에 "
            "'확인필요'라고 표시하고, 아니면 빈칸으로 두고 Date rcvd가 "
            "오래된 순서로 정렬해줘.",
            _validate_nested_flag,
        ),
        QaCase(
            "calculated_period_summary",
            "sales",
            "기본작업-1",
            "단가와 수량을 곱해서 판매금액을 만들고, 2020년 상반기 거래만 "
            "남겨줘. 문구점별 판매금액 합계와 평균, 거래 건수를 구해서 "
            "합계가 큰 순서로 정리하고 금액에는 천 단위 콤마를 넣어. "
            "맨 아래에는 전체 합계 행도 붙여줘.",
            _validate_calculated_period_summary,
        ),
        QaCase(
            "pivot_quantity",
            "sales",
            "기본작업-1",
            "문구점을 행으로, 품목을 열로 놓고 수량 합계를 피벗표로 "
            "만들어줘. 값이 없는 칸은 0으로 채워줘.",
            _validate_pivot,
        ),
        QaCase(
            "nested_filter_sort",
            "sales",
            "기본작업-1",
            "화진아트에서 단가가 1000 이상인 거래이거나, 모닝아트에서 "
            "수량이 70 이상인 거래만 남기고 날짜가 최신인 순서로 정렬해줘.",
            _validate_nested_filter,
        ),
        QaCase(
            "text_extract_sort",
            "ledger",
            "DEC23",
            "Description에 FPMT가 들어간 기록만 남기고 Lookup Code 앞 5글자를 "
            "Code Prefix라는 새 열로 만든 뒤 Date rcvd 최신순으로 정렬해줘.",
            _validate_text_extract,
        ),
        QaCase(
            "filtered_group_summary",
            "ledger",
            "DEC23",
            f"Type이 Client인 거래만 남긴 뒤 Method별 {amount} 합계와 평균, "
            "거래 건수를 구하고 합계가 큰 순서로 정렬해줘.",
            _validate_filtered_group,
        ),
        QaCase(
            "latest_per_group",
            "sales",
            "기본작업-1",
            "문구점마다 날짜가 가장 최근인 거래 한 건만 남기고 전체를 "
            "날짜 최신순으로 정렬해줘.",
            _validate_latest_per_group,
        ),
        QaCase(
            "rank_top_ten",
            "sales",
            "기본작업-1",
            "단가와 수량을 곱해서 판매금액을 만들고 판매금액이 큰 순위를 "
            "새 열로 추가한 다음 상위 10개 거래만 남겨줘.",
            _validate_rank_top,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="case_names")
    parser.add_argument(
        "--sales-workbook",
        type=Path,
        help="판매 데이터 종단간 점검에 사용할 Excel 파일",
    )
    parser.add_argument(
        "--ledger-workbook",
        type=Path,
        help="원장 데이터 종단간 점검에 사용할 Excel 파일",
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()

    selected = [
        case
        for case in _cases()
        if not args.case_names or case.name in set(args.case_names)
    ]
    known_names = {case.name for case in _cases()}
    unknown = set(args.case_names or []) - known_names
    if unknown:
        parser.error("unknown cases: " + ", ".join(sorted(unknown)))

    paths = {"sales": args.sales_workbook, "ledger": args.ledger_workbook}
    required_workbooks = {case.workbook for case in selected}
    missing_workbooks = [
        name for name in sorted(required_workbooks) if paths[name] is None
    ]
    if missing_workbooks:
        parser.error(
            "선택한 QA 사례에 필요한 파일 인자가 없습니다: "
            + ", ".join(
                f"--{name}-workbook" for name in missing_workbooks
            )
        )
    config = json.loads(args.config.read_text(encoding="utf-8"))
    base_dir = args.config.resolve().parent
    results = []
    cache: dict[tuple[Path, str], pd.DataFrame] = {}
    for case in selected:
        path = paths[case.workbook]
        try:
            key = (path, case.sheet)
            if key not in cache:
                candidate = next(
                    item for item in detect_tables(path) if item.sheet_name == case.sheet
                )
                cache[key] = load_detected_table(path, candidate)
            source = cache[key]
            profile = build_profile(source, path.name, case.sheet, 3)
            hints = build_planning_hints(source, case.request)
            planner = _CapturingPlanner(build_planner(config, base_dir))
            validated = create_validated_plan(
                planner=planner,
                user_request=case.request,
                workbook_profile=profile,
                function_catalog=catalog_for_prompt(),
                source_df=source,
                planning_hints=hints,
            )
            execution = execute_plan(source, validated.plan)
            check = case.validate(source, execution.df, validated.plan)
            results.append(
                {
                    "case": case.name,
                    "passed": bool(check.pop("passed")),
                    "used_retry": validated.used_semantic_retry,
                    "functions": [step.function for step in validated.plan.steps],
                    "check": check,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case.name,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "last_plan": (
                        asdict(planner.last_plan)
                        if "planner" in locals() and planner.last_plan is not None
                        else None
                    ),
                }
            )
        print(json.dumps(results[-1], ensure_ascii=False, default=str), flush=True)

    passed = sum(bool(item["passed"]) for item in results)
    summary = {
        "passed": passed,
        "total": len(results),
        "success_rate": round(passed / len(results), 4) if results else 0,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
