from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

import pandas as pd

from .models import (
    ExecutionPlan,
    PlanCorrectionFeedback,
    PlanPreview,
    PlanningHints,
    WorkbookProfile,
)
from .planners.base import Planner
from .validation import (
    PlanValidationError,
    repair_plan_from_explicit_request,
    validate_plan_against_data,
    validate_plan_covers_request,
)


@dataclass(frozen=True)
class ValidatedPlan:
    plan: ExecutionPlan
    preview: PlanPreview
    first_validation_error: str | None = None

    @property
    def used_semantic_retry(self) -> bool:
        return self.first_validation_error is not None


class UnsupportedRequestError(ValueError):
    pass


def _compact_error(error: Exception, maximum_length: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(error)).strip()
    if len(text) <= maximum_length:
        return text
    return text[: maximum_length - 1].rstrip() + "…"


def create_validated_plan(
    *,
    planner: Planner,
    user_request: str,
    workbook_profile: WorkbookProfile,
    function_catalog: dict[str, Any],
    source_df: pd.DataFrame,
    planning_hints: PlanningHints | None = None,
    postprocess_plan: Callable[[ExecutionPlan], ExecutionPlan] | None = None,
) -> ValidatedPlan:
    """Create a plan and retry once only when deterministic semantic validation fails."""
    correction_feedback: PlanCorrectionFeedback | None = None
    first_validation_error: str | None = None

    for attempt in range(2):
        plan = planner.create_plan(
            user_request,
            workbook_profile,
            function_catalog,
            planning_hints=planning_hints,
            correction_feedback=correction_feedback,
        )
        if plan.problem_type == "unsupported":
            raise UnsupportedRequestError(
                "요청하신 내용은 현재 지원하는 엑셀 표 가공 범위를 벗어납니다. "
                "엑셀 표의 정리·필터·계산·집계·서식 작업으로 다시 입력해 주세요. "
                "원본 파일은 변경되지 않았습니다."
            )
        if postprocess_plan is not None:
            plan = postprocess_plan(plan)
        plan = repair_plan_from_explicit_request(
            user_request,
            plan,
            workbook_profile.column_names,
            planning_hints,
        )
        try:
            validate_plan_covers_request(
                user_request,
                plan,
                workbook_profile.column_names,
                planning_hints,
            )
            preview = validate_plan_against_data(source_df, plan, planning_hints)
        except PlanValidationError as exc:
            error_text = _compact_error(exc)
            if attempt == 1:
                raise PlanValidationError(
                    "내부 재계획 후에도 실행 계획을 검증하지 못했습니다: "
                    f"{error_text} 원본 파일은 변경되지 않았습니다."
                ) from exc
            first_validation_error = error_text
            correction_feedback = PlanCorrectionFeedback(
                validation_error=error_text,
                failed_plan=[
                    {
                        "function": step.function,
                        "params": dict(step.params),
                    }
                    for step in plan.steps
                ],
            )
            continue
        return ValidatedPlan(
            plan=plan,
            preview=preview,
            first_validation_error=first_validation_error,
        )

    raise AssertionError("계획 검증 재시도 횟수 계산이 올바르지 않습니다.")
