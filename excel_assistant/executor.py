from __future__ import annotations

import pandas as pd

from .catalog import FUNCTION_CATALOG
from .models import ExecutionPlan, ExecutionResult, OutputDirective
from .output import directive_from_step


def execute_plan(df: pd.DataFrame, plan: ExecutionPlan) -> ExecutionResult:
    result = df.copy()
    directives: list[OutputDirective] = []
    for step in plan.steps:
        spec = FUNCTION_CATALOG[step.function]
        if spec.phase == "output":
            directives.append(directive_from_step(step))
            continue
        operation = spec.function
        if operation is None:
            raise TypeError(f"'{step.function}' 데이터 함수가 연결되지 않았습니다.")
        params = dict(step.params)
        as_formula = bool(params.pop("as_formula", False))
        result = operation(result, **params)
        if not isinstance(result, pd.DataFrame):
            raise TypeError(f"'{step.function}' 함수가 DataFrame을 반환하지 않았습니다.")
        if as_formula and step.function == "calculate_column":
            directives.append(OutputDirective("formula_column", params))
        if as_formula and step.function == "add_subtotals":
            directives.append(OutputDirective("formula_subtotals", params))
        if as_formula and step.function == "calculate_date_difference":
            directives.append(OutputDirective("formula_date_difference", params))
    return ExecutionResult(df=result, output_directives=directives)
