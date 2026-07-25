import unittest

import pandas as pd

from excel_assistant.candidates import generate_filter_candidates


class CandidateGenerationTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "업체": [
                    "동부아트",
                    "서부아트",
                    "동부아트",
                    "푸른상사",
                    "중앙문구",
                ],
                "수량": [1, 2, 3, 4, 5],
            }
        )

    def test_partial_text_creates_data_backed_contains_candidate(self):
        candidates = generate_filter_candidates(self.df, "아트 행만 남겨")
        candidate = next(
            item
            for item in candidates
            if item.column == "업체"
            and item.operator == "contains"
            and item.value == "아트"
        )
        self.assertEqual(candidate.row_count, 3)
        self.assertEqual(set(candidate.matched_values), {"동부아트", "서부아트"})
        self.assertFalse(
            any(
                item.column == "업체"
                and item.operator == "endswith"
                and item.value == "아트"
                for item in candidates
            )
        )

    def test_full_cell_value_creates_exact_candidate(self):
        candidates = generate_filter_candidates(self.df, "동부아트 기록만 남겨")
        candidate = next(
            item
            for item in candidates
            if item.column == "업체"
            and item.operator == "=="
            and item.value == "동부아트"
        )
        self.assertEqual(candidate.row_count, 2)
        self.assertFalse(
            any(
                item.column == "업체"
                and item.operator == "contains"
                and item.value == "동부아트"
                for item in candidates
            )
        )

    def test_candidates_do_not_depend_on_a_specific_fixture_value(self):
        candidates = generate_filter_candidates(self.df, "상사가 들어간 업체만")
        candidate = next(
            item
            for item in candidates
            if item.column == "업체"
            and item.operator == "contains"
            and item.value == "상사"
        )
        self.assertEqual(candidate.row_count, 1)


if __name__ == "__main__":
    unittest.main()
