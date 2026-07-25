from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .models import ExecutionPlan, PlanPreview, PlanningHints


def append_plan_log(
    log_path: str | Path,
    *,
    user_request: str,
    source_file: str,
    sheet_name: str,
    status: str,
    plan: ExecutionPlan | None = None,
    planning_hints: PlanningHints | None = None,
    preview: PlanPreview | None = None,
    error: str | None = None,
) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "user_request": user_request,
        "source_file": source_file,
        "sheet_name": sheet_name,
        "planning_hints": asdict(planning_hints) if planning_hints else None,
        "plan": asdict(plan) if plan else None,
        "preview": asdict(preview) if preview else None,
        "error": error,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
