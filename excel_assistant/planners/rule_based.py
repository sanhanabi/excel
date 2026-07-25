from __future__ import annotations

import re
from typing import Any

from ..models import (
    ExecutionPlan,
    PlanCorrectionFeedback,
    PlanStep,
    PlanningHints,
    WorkbookProfile,
)
from .base import Planner


class RuleBasedPlanner(Planner):
    """Small fallback used to exercise the full workflow without an LLM."""

    def create_plan(
        self,
        user_request: str,
        workbook_profile: WorkbookProfile,
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
        correction_feedback: PlanCorrectionFeedback | None = None,
    ) -> ExecutionPlan:
        request = user_request.strip()
        columns = workbook_profile.column_names
        lowered_request = request.lower()
        mentioned = sorted(
            (column for column in columns if column.lower() in lowered_request),
            key=lambda column: lowered_request.find(column.lower()),
        )
        steps: list[PlanStep] = []
        assumptions: list[str] = []

        if (
            planning_hints
            and planning_hints.matched_values
        ):
            match = planning_hints.matched_values[0]
            steps.append(
                PlanStep(
                    "filter_rows",
                    {"column": match.column, "operator": "==", "value": match.exact_value},
                    f"{match.column}이(가) '{match.exact_value}'인 행만 유지",
                )
            )

        if any(word in request for word in ("빈 행", "빈칸 행", "공백 행")):
            steps.append(PlanStep("remove_empty_rows", {"how": "all"}, "완전히 빈 행 제거"))
        if any(word in request for word in ("중복", "겹치는")):
            subset = mentioned or None
            steps.append(PlanStep("remove_duplicates", {"subset": subset}, "중복 행 제거"))

        aggregate = self._aggregate_kind(request)
        if aggregate:
            group_column, value_column = self._choose_group_and_value(columns, mentioned, request)
            if not group_column:
                raise ValueError("그룹으로 묶을 열을 요청에서 찾지 못했습니다. 열 이름을 포함해 주세요.")
            params: dict[str, Any] = {"group_columns": group_column}
            result_column = {"group_sum": "합계", "group_average": "평균", "group_count": "개수"}[aggregate]
            if aggregate != "group_count":
                if not value_column:
                    raise ValueError("계산할 숫자 열을 요청에서 찾지 못했습니다. 열 이름을 포함해 주세요.")
                params["value_column"] = value_column
            params["result_column"] = result_column
            steps.append(PlanStep(aggregate, params, f"{group_column}별 {result_column} 계산"))
            if any(word in request for word in ("큰 순", "많은 순", "높은 순", "내림차순")):
                steps.append(PlanStep("sort_rows", {"columns": result_column, "ascending": False}, f"{result_column} 내림차순 정렬"))
            elif any(word in request for word in ("작은 순", "적은 순", "낮은 순", "오름차순")):
                steps.append(PlanStep("sort_rows", {"columns": result_column, "ascending": True}, f"{result_column} 오름차순 정렬"))
        elif any(word in request for word in ("정렬", "순서", "순으로")):
            if not mentioned:
                raise ValueError("정렬할 열 이름을 요청에 포함해 주세요.")
            ascending = not any(word in request for word in ("내림", "큰 순", "많은 순", "최신"))
            steps.append(PlanStep("sort_rows", {"columns": mentioned, "ascending": ascending}, "요청한 열 기준 정렬"))

        top_match = re.search(r"(?:상위|위에서)\s*(\d+)\s*(?:개|건|명)?", request)
        if top_match and mentioned:
            top_column = result_column if aggregate else mentioned[-1]
            steps.append(
                PlanStep(
                    "select_top_n",
                    {"column": top_column, "n": int(top_match.group(1)), "largest": True},
                    f"{top_column} 기준 상위 {top_match.group(1)}개 선택",
                )
            )

        if not steps:
            raise ValueError(
                "규칙 기반 시험 모드에서 요청을 해석하지 못했습니다. "
                "열 이름과 '중복 제거', '정렬', '합계', '평균', '개수' 같은 작업을 함께 적어 주세요."
            )
        assumptions.append("현재는 모델이 없는 규칙 기반 시험 모드입니다.")
        return ExecutionPlan(
            goal=request,
            steps=steps,
            column_mapping={item: item for item in mentioned},
            assumptions=assumptions,
            problem_type="filtering" if steps[0].function == "filter_rows" else "other",
        )

    @staticmethod
    def _aggregate_kind(request: str) -> str | None:
        if any(word in request for word in ("합계", "총액", "총합", "모두 더")):
            return "group_sum"
        if "평균" in request:
            return "group_average"
        if any(word in request for word in ("개수", "건수", "몇 건")):
            return "group_count"
        return None

    @staticmethod
    def _choose_group_and_value(
        columns: list[str], mentioned: list[str], request: str
    ) -> tuple[str | None, str | None]:
        if not mentioned:
            return None, None
        group_column = mentioned[0]
        value_column = mentioned[-1] if len(mentioned) > 1 else None
        match = re.search(r"(.+?)(?:별|마다)", request)
        if match:
            phrase = match.group(1)
            candidates = [column for column in columns if column in phrase]
            if candidates:
                group_column = candidates[-1]
                remaining = [column for column in mentioned if column != group_column]
                value_column = remaining[-1] if remaining else None
        return group_column, value_column
