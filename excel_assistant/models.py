from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlanStep:
    function: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlanStep":
        return cls(
            function=str(value.get("function", "")),
            params=dict(value.get("params") or {}),
            description=str(value.get("description", "")),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    goal: str
    steps: list[PlanStep]
    column_mapping: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    problem_type: str = "other"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionPlan":
        return cls(
            goal=str(value.get("goal", "요청한 엑셀 작업")),
            steps=[PlanStep.from_dict(item) for item in value.get("steps", [])],
            column_mapping={
                str(key): str(column)
                for key, column in dict(value.get("column_mapping") or {}).items()
            },
            assumptions=[str(item) for item in value.get("assumptions", [])],
            problem_type=str(value.get("problem_type", "other")),
        )


@dataclass(frozen=True)
class OutputDirective:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    df: Any
    output_directives: list[OutputDirective] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.df)


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    missing_count: int
    unique_count: int
    sample_values: list[Any]
    statistics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanCorrectionFeedback:
    validation_error: str
    failed_plan: list[dict[str, Any]]

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "validation_error": self.validation_error,
            "failed_plan": self.failed_plan,
        }


@dataclass(frozen=True)
class WorkbookProfile:
    file_name: str
    sheet_name: str
    row_count: int
    columns: list[ColumnProfile]
    observed_column_aliases: dict[str, list[str]] = field(default_factory=dict)

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "sheet_name": self.sheet_name,
            "row_count": self.row_count,
            "columns": [
                {
                    "name": item.name,
                    "dtype": item.dtype,
                    "missing_count": item.missing_count,
                    "unique_count": item.unique_count,
                    "sample_values": item.sample_values,
                    "statistics": item.statistics,
                }
                for item in self.columns
            ],
            "observed_column_aliases": self.observed_column_aliases,
        }


@dataclass(frozen=True)
class MatchedValue:
    request_text: str
    column: str
    exact_value: str
    row_count: int

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "request_text": self.request_text,
            "column": self.column,
            "exact_value": self.exact_value,
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class PlanningHints:
    matched_values: list[MatchedValue] = field(default_factory=list)
    filter_candidates: list[dict[str, Any]] = field(default_factory=list)
    recommended_functions: list[str] = field(default_factory=list)
    recommended_operators: list[str] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.matched_values:
            result["matched_values"] = {
                "legend": ["text", "column", "value", "rows"],
                "items": [
                    [
                        item.request_text,
                        item.column,
                        item.exact_value,
                        item.row_count,
                    ]
                    for item in self.matched_values[:5]
                ],
            }
        if self.filter_candidates:
            result["filter_candidates"] = {
                "legend": ["column", "op", "value", "rows"],
                "cands": [
                    [
                        item.get("column"),
                        item.get("operator"),
                        item.get("value"),
                        item.get("expected_rows", item.get("row_count")),
                    ]
                    for item in self.filter_candidates[:6]
                ],
            }
        if self.recommended_functions:
            result["recommended_functions"] = self.recommended_functions
        if self.recommended_operators:
            result["recommended_operators"] = self.recommended_operators
        return result


@dataclass(frozen=True)
class StepPreview:
    function: str
    params: dict[str, Any]
    before_rows: int
    after_rows: int
    affected_rows: int | None = None


@dataclass(frozen=True)
class PlanPreview:
    initial_rows: int
    final_rows: int
    steps: list[StepPreview]


@dataclass(frozen=True)
class TableCandidate:
    sheet_name: str
    header_row: int
    start_column: int
    end_column: int
    data_start_row: int
    data_end_row: int
    headers: tuple[str, ...]
    nonempty_row_count: int
    confidence: float

    @property
    def row_count(self) -> int:
        return self.nonempty_row_count

    @property
    def label(self) -> str:
        preview = ", ".join(self.headers[:4])
        if len(self.headers) > 4:
            preview += "…"
        return (
            f"{self.sheet_name} · 제목 {self.header_row}행 · "
            f"데이터 {self.row_count}행 · {preview}"
        )
