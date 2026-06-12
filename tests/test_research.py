"""Unit tests for research resolution parsing and bracket truth."""

import unittest

from forecast.parse_title import parse_highest_temp_title
from research.bracket_truth import yes_bucket_matches_truth
from research.resolution_parse import (
    extract_station_code_hint,
    resolution_row_from_market,
)


class TestResolutionParse(unittest.TestCase):
    def test_station_hint_from_rules_text(self) -> None:
        text = (
            "Resolved using https://www.wunderground.com/history/daily/kr/incheon/RKSI"
        )
        self.assertEqual(extract_station_code_hint(text), "RKSI")

    def test_resolution_row_shape(self) -> None:
        m = {
            "id": "1",
            "conditionId": "0xabc",
            "question": "Will the highest temperature in London be 15°C on April 12?",
            "description": "",
            "resolutionSource": "",
        }
        row = resolution_row_from_market(m, gamma_event_id="99")
        self.assertEqual(row["city_key"], "london")
        self.assertEqual(row["bracket"], "exact")
        self.assertEqual(row["market_id"], "1")


class TestBracketTruth(unittest.TestCase):
    def test_exact_c(self) -> None:
        p = parse_highest_temp_title(
            "Will the highest temperature in London be 17°C on April 14?"
        )
        assert p is not None
        self.assertTrue(yes_bucket_matches_truth(p, 17.2))
        self.assertFalse(yes_bucket_matches_truth(p, 18.6))

    def test_range_f(self) -> None:
        p = parse_highest_temp_title(
            "Will the highest temperature in Miami be 80-81°F on April 14?"
        )
        assert p is not None
        self.assertTrue(yes_bucket_matches_truth(p, 26.7))


class TestRunBackfillDry(unittest.TestCase):
    def test_dry_run_exits_zero(self) -> None:
        from research.run_backfill import run_backfill_main

        code = run_backfill_main(
            ["--dry-run", "--days", "2", "--max-markets", "2", "--search-pages", "1"]
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
