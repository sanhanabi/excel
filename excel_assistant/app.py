from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .catalog import catalog_for_prompt
from .excel_io import (
    build_profile,
    combine_detected_files,
    detect_tables,
    load_detected_table,
    save_result,
)
from .executor import execute_plan
from .grounding import build_planning_hints
from .models import TableCandidate
from .plan_log import append_plan_log
from .planning import UnsupportedRequestError, create_validated_plan
from .planners import build_planner
from .presentation import format_plan


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def infer_cross_sheet_column_aliases(
    request: str,
    selected: TableCandidate,
    candidates: list[TableCandidate],
) -> dict[str, str]:
    """Resolve literal alternate headers mentioned in the request."""
    observed = build_cross_sheet_column_aliases(selected, candidates)
    possible: dict[str, set[str]] = {}
    for target, aliases in observed.items():
        for header_text in aliases:
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(header_text)}(?![A-Za-z0-9_])"
            if re.search(pattern, request, flags=re.IGNORECASE):
                possible.setdefault(header_text, set()).add(target)
    return {
        requested: next(iter(targets))
        for requested, targets in possible.items()
        if len(targets) == 1
    }


def build_cross_sheet_column_aliases(
    selected: TableCandidate,
    candidates: list[TableCandidate],
) -> dict[str, list[str]]:
    """Collect observed header variants only from strongly parallel sheet schemas."""
    selected_headers = [str(header).strip() for header in selected.headers]
    selected_folded = [header.casefold() for header in selected_headers]
    aliases: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.sheet_name == selected.sheet_name:
            continue
        other_headers = [str(header).strip() for header in candidate.headers]
        if len(other_headers) != len(selected_headers) or not selected_headers:
            continue
        unchanged = sum(
            left == right
            for left, right in zip(
                selected_folded,
                [header.casefold() for header in other_headers],
            )
        )
        required_matches = max(2, int(len(selected_headers) * 0.6))
        if unchanged < required_matches:
            continue
        for target, alternate in zip(selected_headers, other_headers):
            if (
                target
                and alternate
                and target.casefold() != alternate.casefold()
                and alternate.casefold() not in selected_folded
            ):
                aliases.setdefault(target, set()).add(alternate)
    return {
        target: sorted(values, key=str.casefold)
        for target, values in aliases.items()
        if values
    }


def apply_column_aliases(request: str, aliases: dict[str, str]) -> str:
    resolved = request
    for requested in sorted(aliases, key=len, reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(requested)}(?![A-Za-z0-9_])"
        resolved = re.sub(
            pattern,
            aliases[requested],
            resolved,
            flags=re.IGNORECASE,
        )
    return resolved


class ExcelAssistantApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("엑셀도우미")
        self.geometry("820x620")
        self.minsize(720, 540)
        self._base_dir = application_dir()
        self._config = self._load_config()
        self._file_paths: list[Path] = []
        self._candidate_paths: list[Path] = []
        self._table_candidates: list[TableCandidate] = []
        self._build_ui()

    def _load_config(self) -> dict:
        config_path = self._base_dir / "config.json"
        if not config_path.exists():
            return {"planner": {"type": "rule_based"}, "profile": {"sample_count": 3}}
        with config_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=22)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="엑셀도우미", font=("맑은 고딕", 20, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text="엑셀 안의 표를 자동으로 찾고, 자연어 요청을 검증된 함수로 실행합니다.",
        ).pack(anchor="w", pady=(4, 20))

        file_frame = ttk.LabelFrame(container, text="1. 엑셀 파일", padding=12)
        file_frame.pack(fill="x")
        self.file_label = ttk.Label(file_frame, text="선택된 파일이 없습니다.")
        self.file_label.pack(side="left", fill="x", expand=True)
        ttk.Button(file_frame, text="파일 선택", command=self._choose_file).pack(side="right")

        table_frame = ttk.LabelFrame(container, text="2. 작업할 시트", padding=12)
        table_frame.pack(fill="x", pady=(12, 0))
        self.table_combo = ttk.Combobox(table_frame, state="readonly")
        self.table_combo.pack(fill="x")
        self.table_combo.bind("<<ComboboxSelected>>", self._show_table_details)
        self.table_detail_var = tk.StringVar(
            value="파일을 선택하면 시트별 주 데이터 영역을 자동으로 찾습니다."
        )
        ttk.Label(table_frame, textvariable=self.table_detail_var, wraplength=740).pack(
            anchor="w", pady=(8, 0)
        )
        self.combine_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            table_frame,
            text="여러 파일·시트의 주 데이터 합치기 (결과에 원본파일·원본시트 표시)",
            variable=self.combine_all_var,
        ).pack(anchor="w", pady=(8, 0))

        action_frame = ttk.Frame(container)
        action_frame.pack(side="bottom", fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="준비됨")
        ttk.Label(action_frame, textvariable=self.status_var).pack(
            side="left", fill="x", expand=True
        )
        self.run_button = ttk.Button(
            action_frame,
            text="작업 내용 확인",
            command=self._start_planning,
        )
        self.run_button.pack(side="right")

        request_frame = ttk.LabelFrame(container, text="3. 원하는 작업", padding=12)
        request_frame.pack(fill="both", expand=True, pady=(18, 0))
        self.request_text = tk.Text(
            request_frame,
            height=6,
            wrap="word",
            font=("맑은 고딕", 11),
        )
        self.request_text.pack(fill="both", expand=True)
        self.request_text.insert(
            "1.0", "예: 지점명별 월불입액 합계를 구해서 큰 순서로 정리해줘"
        )

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilenames(
            title="엑셀 파일 선택 (여러 개 선택 가능)",
            filetypes=[("Excel 파일", "*.xlsx *.xlsm"), ("모든 파일", "*.*")],
        )
        if not selected:
            return
        self._file_paths = [Path(item) for item in selected]
        if len(self._file_paths) == 1:
            file_text = self._file_paths[0].name
        else:
            preview = ", ".join(path.name for path in self._file_paths[:3])
            suffix = "…" if len(self._file_paths) > 3 else ""
            file_text = f"{len(self._file_paths)}개 파일: {preview}{suffix}"
        self.file_label.configure(text=file_text)
        self._set_busy(True, "시트별 주 데이터 영역을 분석하는 중입니다...")
        threading.Thread(
            target=self._detect_tables_in_background,
            args=(list(self._file_paths),),
            daemon=True,
        ).start()

    def _detect_tables_in_background(self, paths: list[Path]) -> None:
        try:
            sources = [
                (path, candidate)
                for path in paths
                for candidate in detect_tables(path)
            ]
        except UnsupportedRequestError as exc:
            self._write_plan_log(
                request=request,
                path=path,
                sheet_name=candidate.sheet_name,
                status="unsupported",
                plan=plan,
                hints=hints,
                error=str(exc),
            )
            self.after(0, lambda error=exc: self._show_unsupported(error))
            return
        except Exception as exc:
            self.after(0, lambda error=exc: self._show_error(error))
            return
        self.after(0, lambda: self._show_detected_tables(sources))

    def _show_detected_tables(
        self,
        sources: list[tuple[Path, TableCandidate]],
    ) -> None:
        self._candidate_paths = [path for path, _ in sources]
        self._table_candidates = [candidate for _, candidate in sources]
        multiple_files = len({path.resolve() for path in self._candidate_paths}) > 1
        labels = [
            f"{path.name} · {candidate.sheet_name}"
            if multiple_files
            else candidate.sheet_name
            for path, candidate in sources
        ]
        self.table_combo.configure(values=labels)
        if sources:
            self.table_combo.current(0)
            self._show_table_details()
            self._set_busy(False, f"작업 가능한 파일·시트 {len(sources)}개를 찾았습니다.")
        else:
            self.table_combo.set("")
            self.table_detail_var.set("주 데이터 영역이 있는 시트를 찾지 못했습니다.")
            self._set_busy(False, "작업 가능한 시트를 찾지 못했습니다.")

    def _selected_candidate(self) -> TableCandidate | None:
        index = self.table_combo.current()
        if index < 0 or index >= len(self._table_candidates):
            return None
        return self._table_candidates[index]

    def _selected_path(self) -> Path | None:
        index = self.table_combo.current()
        if index < 0 or index >= len(self._candidate_paths):
            return None
        return self._candidate_paths[index]

    def _show_table_details(self, _event=None) -> None:
        candidate = self._selected_candidate()
        path = self._selected_path()
        if candidate is None or path is None:
            return
        prefix = f"파일: {path.name} | " if len(self._file_paths) > 1 else ""
        self.table_detail_var.set(
            f"{prefix}주 데이터: {candidate.data_start_row}~{candidate.data_end_row}행 | "
            f"열 {len(candidate.headers)}개: {', '.join(candidate.headers)}"
        )

    @staticmethod
    def _month_number(sheet_name: str) -> int | None:
        korean = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*월", sheet_name)
        if korean:
            return int(korean.group(1))
        english_months = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUNE": 6, "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9,
            "OCT": 10, "NOV": 11, "DEC": 12,
        }
        upper_name = sheet_name.upper()
        for token, month in sorted(
            english_months.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if re.search(rf"(?:^|[^A-Z]){token}(?:[^A-Z]|$)", upper_name):
                return month
        return None

    def _candidates_for_request(self, request: str) -> list[TableCandidate]:
        month_range = re.search(
            r"(1[0-2]|[1-9])\s*월\s*부터\s*(1[0-2]|[1-9])\s*월\s*까지",
            request,
        )
        if not month_range:
            return list(self._table_candidates)
        start_month, end_month = map(int, month_range.groups())
        if start_month > end_month:
            return list(self._table_candidates)
        selected = [
            candidate
            for candidate in self._table_candidates
            if (month := ExcelAssistantApp._month_number(candidate.sheet_name)) is not None
            and start_month <= month <= end_month
        ]
        return selected or list(self._table_candidates)

    def _sources_for_request(
        self,
        request: str,
    ) -> list[tuple[Path, TableCandidate]]:
        selected_candidates = self._candidates_for_request(request)
        selected_ids = {id(candidate) for candidate in selected_candidates}
        selected = [
            (path, candidate)
            for path, candidate in zip(self._candidate_paths, self._table_candidates)
            if id(candidate) in selected_ids
        ]
        return selected or list(zip(self._candidate_paths, self._table_candidates))

    def _start_planning(self) -> None:
        request = self.request_text.get("1.0", "end").strip()
        candidate = self._selected_candidate()
        selected_path = self._selected_path()
        if not self._file_paths:
            messagebox.showwarning("확인 필요", "먼저 엑셀 파일을 선택해 주세요.")
            return
        if candidate is None or selected_path is None:
            messagebox.showwarning("확인 필요", "작업할 시트를 선택해 주세요.")
            return
        if not request:
            messagebox.showwarning("확인 필요", "원하는 작업을 입력해 주세요.")
            return
        combine_all = self.combine_all_var.get() or (
            len(self._table_candidates) > 1
            and any(
                phrase in request
                for phrase in (
                    "모든 시트", "전체 시트", "시트 합쳐", "시트를 합쳐",
                    "월별 시트", "여러 시트", "전 시트", "월부터",
                )
            )
        )
        selected_sources = (
            self._sources_for_request(request)
            if combine_all
            else [(selected_path, candidate)]
        )
        parallel_candidates = [
            item
            for path, item in zip(self._candidate_paths, self._table_candidates)
            if path.resolve() == selected_path.resolve()
        ]
        column_aliases = (
            {}
            if combine_all
            else infer_cross_sheet_column_aliases(
                request, candidate, parallel_candidates
            )
        )
        if column_aliases:
            explanations = "\n".join(
                f"- {requested} → {actual}"
                for requested, actual in column_aliases.items()
            )
            if not messagebox.askyesno(
                "열 이름 확인",
                f"{candidate.sheet_name} 시트에는 요청한 열 이름이 없습니다.\n\n"
                f"같은 구조의 다른 시트를 기준으로 다음 열에 연결할 수 있습니다.\n"
                f"{explanations}\n\n이 연결을 사용해 계속할까요?",
            ):
                self.status_var.set("열 이름 확인이 취소되었습니다.")
                return
        planning_request = apply_column_aliases(request, column_aliases)
        self._set_busy(True, "표를 읽고 작업을 이해하는 중입니다...")
        threading.Thread(
            target=self._plan_in_background,
            args=(
                selected_path,
                candidate,
                request,
                planning_request,
                column_aliases,
                combine_all,
                selected_sources,
            ),
            daemon=True,
        ).start()

    def _plan_in_background(
        self,
        path: Path,
        candidate: TableCandidate,
        request: str,
        planning_request: str,
        column_aliases: dict[str, str],
        combine_all: bool,
        selected_sources: list[tuple[Path, TableCandidate]],
    ) -> None:
        plan = None
        hints = None
        planner = None
        try:
            if combine_all:
                df = combine_detected_files(selected_sources)
                profile_sheet_name = f"여러 파일·시트 ({len(selected_sources)}개 표)"
            else:
                needs_hidden_metadata = (
                    "보이는 행" in request
                    or (
                        "숨" in request
                        and "행" in request
                        and any(
                            phrase in request
                            for phrase in ("제외", "빼", "제거", "무시")
                        )
                    )
                )
                df = load_detected_table(
                    path,
                    candidate,
                    include_source_metadata=needs_hidden_metadata,
                )
                profile_sheet_name = candidate.sheet_name
            profile = build_profile(
                df,
                file_name=path.name,
                sheet_name=profile_sheet_name,
                sample_count=int(self._config.get("profile", {}).get("sample_count", 3)),
            )
            if not combine_all:
                parallel_candidates = [
                    item
                    for source_path, item in zip(
                        self._candidate_paths,
                        self._table_candidates,
                    )
                    if source_path.resolve() == path.resolve()
                ]
                profile = replace(
                    profile,
                    observed_column_aliases=build_cross_sheet_column_aliases(
                        candidate,
                        parallel_candidates,
                    ),
                )
            hints = build_planning_hints(df, planning_request)
            if combine_all and len(
                {tuple(item.headers) for _, item in selected_sources}
            ) > 1:
                hints = replace(
                    hints,
                    recommended_functions=list(
                        dict.fromkeys(
                            [*hints.recommended_functions, "normalize_column_names"]
                        )
                    ),
                )
            planner = build_planner(self._config, self._base_dir)

            def postprocess_plan(generated_plan):
                final_plan = generated_plan
                if column_aliases:
                    final_plan = replace(
                        final_plan,
                        column_mapping={
                            **final_plan.column_mapping,
                            **column_aliases,
                        },
                        assumptions=[
                            *final_plan.assumptions,
                            *[
                                f"요청한 '{requested}' 열을 현재 시트의 '{actual}' 열에 연결합니다."
                                for requested, actual in column_aliases.items()
                            ],
                        ],
                    )
                if combine_all:
                    final_plan = replace(
                        final_plan,
                        assumptions=[
                            *final_plan.assumptions,
                            f"원본을 저장하지 않고 선택된 파일·시트 {len(selected_sources)}개의 "
                            "행 방향으로 결합하고 원본파일·원본시트 열을 추가합니다.",
                        ],
                        problem_type=(
                            "multi_sheet"
                            if final_plan.problem_type == "other"
                            else final_plan.problem_type
                        ),
                    )
                return final_plan

            validated = create_validated_plan(
                planner=planner,
                user_request=planning_request,
                workbook_profile=profile,
                function_catalog=catalog_for_prompt(),
                source_df=df,
                planning_hints=hints,
                postprocess_plan=postprocess_plan,
            )
            plan = validated.plan
            preview = validated.preview
            self._write_plan_log(
                request=request,
                path=path,
                sheet_name=profile_sheet_name,
                status=(
                    "validated_after_retry"
                    if validated.used_semantic_retry
                    else "validated"
                ),
                plan=plan,
                hints=hints,
                preview=preview,
                error=validated.first_validation_error,
            )
        except Exception as exc:
            self._write_plan_log(
                request=request,
                path=path,
                sheet_name=candidate.sheet_name,
                status="rejected",
                plan=plan,
                hints=hints,
                error=str(exc),
            )
            self.after(0, lambda error=exc: self._show_error(error))
            return
        finally:
            if planner is not None:
                planner.close()
        self.after(
            0,
            lambda: self._confirm_and_execute(
                df,
                plan,
                preview,
                path,
                candidate,
                combine_all,
                [source_path for source_path, _ in selected_sources],
            ),
        )

    def _write_plan_log(
        self,
        *,
        request: str,
        path: Path,
        sheet_name: str,
        status: str,
        plan=None,
        hints=None,
        preview=None,
        error: str | None = None,
    ) -> None:
        try:
            append_plan_log(
                self._base_dir / "logs" / "plans.jsonl",
                user_request=request,
                source_file=str(path),
                sheet_name=sheet_name,
                status=status,
                plan=plan,
                planning_hints=hints,
                preview=preview,
                error=error,
            )
        except OSError:
            pass

    def _confirm_and_execute(
        self,
        df,
        plan,
        preview,
        source_path: Path,
        candidate: TableCandidate,
        combine_all: bool,
        source_paths: list[Path],
    ) -> None:
        self._set_busy(False, "작업 계획이 준비되었습니다.")
        if not messagebox.askyesno("작업 내용 확인", format_plan(plan, preview)):
            self.status_var.set("작업이 취소되었습니다.")
            return
        default_name = (
            "통합_결과.xlsx"
            if combine_all and len(source_paths) > 1
            else f"{source_path.stem}_결과.xlsx"
        )
        output = filedialog.asksaveasfilename(
            title="결과 파일 저장",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel 파일", "*.xlsx")],
        )
        if not output:
            self.status_var.set("저장이 취소되었습니다.")
            return
        self._set_busy(True, "확정한 작업을 실행하는 중입니다...")
        threading.Thread(
            target=self._execute_in_background,
            args=(
                df,
                plan,
                Path(output),
                source_path,
                candidate,
                combine_all,
                source_paths,
            ),
            daemon=True,
        ).start()

    def _execute_in_background(
        self,
        df,
        plan,
        output: Path,
        source_path: Path,
        candidate: TableCandidate,
        combine_all: bool,
        source_paths: list[Path],
    ) -> None:
        try:
            result = execute_plan(df, plan)
            save_result(
                result,
                output,
                sheet_name=("통합_결과" if combine_all else f"{candidate.sheet_name}_결과"),
                source_path=source_path,
                source_table=None if combine_all else candidate,
                source_paths=source_paths if combine_all else None,
            )
        except Exception as exc:
            self.after(0, lambda error=exc: self._show_error(error))
            return
        self.after(0, lambda: self._show_success(output, len(result)))

    def _show_error(self, exc: Exception) -> None:
        self._set_busy(False, "오류가 발생했습니다.")
        messagebox.showerror("작업 실패", str(exc))

    def _show_unsupported(self, exc: Exception) -> None:
        self._set_busy(False, "현재 지원하지 않는 요청입니다.")
        messagebox.showinfo("지원하지 않는 작업", str(exc))

    def _show_success(self, output: Path, row_count: int) -> None:
        self._set_busy(False, f"완료: {row_count:,}행을 저장했습니다.")
        messagebox.showinfo("완료", f"새 엑셀 파일을 저장했습니다.\n\n{output}")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_var.set(status)
        self.run_button.configure(state="disabled" if busy else "normal")


def main() -> None:
    app = ExcelAssistantApp()
    app.mainloop()
