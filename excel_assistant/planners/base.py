from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import (
    ExecutionPlan,
    PlanCorrectionFeedback,
    PlanningHints,
    WorkbookProfile,
)


class Planner(ABC):
    def close(self) -> None:
        """Release optional local model resources."""

    @abstractmethod
    def create_plan(
        self,
        user_request: str,
        workbook_profile: WorkbookProfile,
        function_catalog: dict[str, Any],
        planning_hints: PlanningHints | None = None,
        correction_feedback: PlanCorrectionFeedback | None = None,
    ) -> ExecutionPlan:
        raise NotImplementedError
