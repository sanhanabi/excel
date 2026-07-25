from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from excel_assistant.catalog import compact_catalog_for_model, catalog_for_prompt
from excel_assistant.excel_io import build_profile
from excel_assistant.models import MatchedValue, PlanningHints
from excel_assistant.planners.ollama import (
    ContextLimitError,
    OllamaPlanner,
    RULES_PROMPT,
    SYSTEM_PROMPT,
)


class JsonResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value, ensure_ascii=False).encode("utf-8")


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"금액": [300, 100, 200]})
        self.profile = build_profile(self.frame, "memory.xlsx", "거래")

    def test_config_uses_8192_context(self):
        config_path = Path(__file__).resolve().parents[1] / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["planner"]["context_length"], 8192)

    def test_compact_catalog_keeps_every_function_and_parameter(self):
        full = catalog_for_prompt()
        compact = compact_catalog_for_model(full)
        functions = compact["functions"]

        self.assertEqual(set(functions), set(full))
        for name, spec in full.items():
            required = functions[name][1]
            optional = functions[name][2]
            self.assertEqual(required, spec["required_params"])
            self.assertEqual(
                set(required + optional),
                set(spec["allowed_params"]),
            )

    def test_representative_18_column_prompt_stays_below_6500_tokens(self):
        frame = pd.DataFrame(
            {
                f"업무열_{number}": [number, number + 1, number + 2]
                for number in range(18)
            }
        )
        profile = build_profile(frame, "업무자료.xlsx", "거래내역")
        catalog = catalog_for_prompt()
        prompt = OllamaPlanner._build_prompt(
            "회사별 금액 합계를 구하고 큰 순서로 정렬해줘",
            profile,
            catalog,
            PlanningHints(),
        )
        planner = OllamaPlanner(
            model="test-model",
            context_length=8192,
            max_tokens=700,
        )

        budget = planner._input_budget(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )

        self.assertLessEqual(budget.estimated_tokens, 6500)

    def test_grounded_hints_are_capped_and_compact(self):
        hints = PlanningHints(
            matched_values=[
                MatchedValue(f"값{number}", "업체", f"값{number}", number + 1)
                for number in range(8)
            ],
            filter_candidates=[
                {
                    "candidate_id": f"F{number}",
                    "column": "업체",
                    "operator": "contains",
                    "value": f"조각{number}",
                    "expected_rows": number + 1,
                    "matched_values": [f"업체{number}"],
                }
                for number in range(9)
            ],
        )

        compact = hints.to_prompt_dict()
        self.assertEqual(
            compact["filter_candidates"]["legend"],
            ["column", "op", "value", "rows"],
        )
        self.assertEqual(len(compact["matched_values"]["items"]), 5)
        self.assertEqual(len(compact["filter_candidates"]["cands"]), 6)
        serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        self.assertNotIn("candidate_id", serialized)
        self.assertNotIn("업체0", serialized)
        self.assertLessEqual(OllamaPlanner._estimate_text_tokens(serialized), 300)

    def test_rules_fit_budget_and_include_new_safety_distinctions(self):
        self.assertLessEqual(OllamaPlanner._estimate_text_tokens(RULES_PROMPT), 1200)
        self.assertIn("operator=is_null", RULES_PROMPT)
        self.assertIn("drop_rows_missing_keys does the opposite", RULES_PROMPT)
        self.assertIn("add_total_row", RULES_PROMPT)
        self.assertIn("add_subtotals", RULES_PROMPT)
        self.assertIn("exact profile column name", RULES_PROMPT)
        self.assertIn("sending email", RULES_PROMPT)
        self.assertIn("fill_value=0", RULES_PROMPT)
        self.assertIn("group_aggregate(group_columns", RULES_PROMPT)

    def test_prompt_surfaces_only_exact_columns_named_in_request(self):
        profile = build_profile(
            pd.DataFrame(
                {
                    "Method": ["Cash"],
                    "Type": ["Client"],
                    "$ RCVD": [100],
                    "Other": [1],
                }
            ),
            "sample.xlsx",
            "JAN",
        )
        prompt = OllamaPlanner._build_prompt(
            "Method는 행으로, Type은 열로, $ RCVD는 값으로 사용해줘",
            profile,
            catalog_for_prompt(),
        )
        section = prompt.split(
            "EXACT SOURCE COLUMNS IN REQUEST:\n",
            1,
        )[1].split("\n\nDATA-GROUNDED HINTS:", 1)[0]
        self.assertEqual(json.loads(section), ["Method", "Type", "$ RCVD"])

    def test_budget_fallback_removes_candidates_before_all_hints(self):
        hints = PlanningHints(
            matched_values=[MatchedValue("화진아트", "문구점", "화진아트", 4)],
            filter_candidates=[
                {
                    "column": "문구점",
                    "operator": "contains",
                    "value": f"아트{number}",
                    "expected_rows": number + 1,
                }
                for number in range(6)
            ],
        )
        catalog = catalog_for_prompt()
        full_prompt = OllamaPlanner._build_prompt(
            "아트 업체만 남겨줘",
            self.profile,
            catalog,
            hints,
        )
        reduced_hints = PlanningHints(matched_values=hints.matched_values)
        reduced_prompt = OllamaPlanner._build_prompt(
            "아트 업체만 남겨줘",
            self.profile,
            catalog,
            reduced_hints,
        )
        probe = OllamaPlanner("test-model", context_length=20000, max_tokens=700)
        full_estimate = probe._input_budget(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt},
            ]
        ).estimated_tokens
        reduced_estimate = probe._input_budget(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": reduced_prompt},
            ]
        ).estimated_tokens
        chosen_context = next(
            context
            for context in range(1000, 10000)
            if reduced_estimate
            < context - 700 - max(128, (context * 3 + 99) // 100)
            <= full_estimate
        )
        planner = OllamaPlanner(
            "test-model",
            context_length=chosen_context,
            max_tokens=700,
        )

        messages, _budget = planner._messages_within_budget(
            system_prompt=SYSTEM_PROMPT,
            user_request="아트 업체만 남겨줘",
            workbook_profile=self.profile,
            function_catalog=catalog,
            planning_hints=hints,
            correction_feedback=None,
        )

        hint_section = messages[1]["content"].split(
            "DATA-GROUNDED HINTS:\n",
            1,
        )[1].split("\n\nCORRECTION FEEDBACK:", 1)[0]
        self.assertNotIn("filter_candidates", hint_section)
        self.assertIn("matched_values", hint_section)
        self.assertEqual(len(hints.filter_candidates), 6)

    def test_estimated_overflow_stops_before_ollama_call(self):
        planner = OllamaPlanner(
            model="test-model",
            context_length=8192,
            max_tokens=700,
        )
        with patch("excel_assistant.planners.ollama.urlopen") as mocked_urlopen:
            with self.assertRaisesRegex(
                ContextLimitError,
                "현재 입력은 약.*원본 파일은 변경되지 않았습니다",
            ):
                planner.create_plan(
                    "가" * 10000,
                    self.profile,
                    catalog_for_prompt(),
                )
        mocked_urlopen.assert_not_called()

    def test_actual_prompt_count_at_limit_discards_response(self):
        planner = OllamaPlanner(
            model="test-model",
            context_length=8192,
            max_tokens=700,
        )
        response = JsonResponse(
            {
                "prompt_eval_count": planner._allowed_input_tokens,
                "message": {
                    "content": json.dumps(
                        {
                            "problem_type": "sorting",
                            "goal": "금액 오름차순 정렬",
                            "column_mapping": {"값": "금액"},
                            "assumptions": [],
                            "steps": [
                                {
                                    "function": "sort_rows",
                                    "params": {
                                        "columns": "금액",
                                        "ascending": True,
                                    },
                                    "description": "금액을 오름차순 정렬합니다.",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            }
        )
        with patch(
            "excel_assistant.planners.ollama.urlopen",
            return_value=response,
        ) as mocked_urlopen:
            with self.assertRaisesRegex(
                ContextLimitError,
                "실제 입력.*응답을 사용하지 않았습니다",
            ):
                planner.create_plan(
                    "싼 값부터 정렬해",
                    self.profile,
                    catalog_for_prompt(),
                )
        self.assertEqual(mocked_urlopen.call_count, 1)

    def test_actual_prompt_count_below_limit_accepts_valid_plan(self):
        planner = OllamaPlanner(
            model="test-model",
            context_length=8192,
            max_tokens=700,
        )
        response = JsonResponse(
            {
                "prompt_eval_count": 6000,
                "message": {
                    "content": json.dumps(
                        {
                            "problem_type": "sorting",
                            "goal": "금액 오름차순 정렬",
                            "column_mapping": {"값": "금액"},
                            "assumptions": [],
                            "steps": [
                                {
                                    "function": "sort_rows",
                                    "params": {
                                        "columns": "금액",
                                        "ascending": True,
                                    },
                                    "description": "금액을 오름차순 정렬합니다.",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            }
        )
        with patch(
            "excel_assistant.planners.ollama.urlopen",
            return_value=response,
        ):
            plan = planner.create_plan(
                "싼 값부터 정렬해",
                self.profile,
                catalog_for_prompt(),
            )
        self.assertEqual(plan.steps[0].params["columns"], "금액")


if __name__ == "__main__":
    unittest.main()
