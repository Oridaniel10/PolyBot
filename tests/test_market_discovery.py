import unittest
from unittest.mock import patch

from polymarket_client import (
    PolymarketClient,
    PolymarketConfig,
    gamma_market_is_active,
    gamma_public_search_has_more,
    highest_temp_event_slug_candidates,
)


class TestGammaPagination(unittest.TestCase):
    def test_has_more_camel(self) -> None:
        self.assertTrue(
            gamma_public_search_has_more(
                {"pagination": {"hasMore": True}, "events": []}
            )
        )

    def test_has_more_snake(self) -> None:
        self.assertTrue(
            gamma_public_search_has_more({"pagination": {"has_more": True}})
        )

    def test_has_more_root(self) -> None:
        self.assertTrue(gamma_public_search_has_more({"hasMore": True}))

    def test_no_more(self) -> None:
        self.assertFalse(
            gamma_public_search_has_more({"pagination": {"hasMore": False}})
        )


class TestHighestTempFilters(unittest.TestCase):
    def setUp(self) -> None:
        cfg = PolymarketConfig(
            api_key="",
            api_secret="",
            api_passphrase="",
            private_key="",
            proxy_address="",
            gamma_base_url="https://gamma-api.polymarket.com",
            clob_base_url="https://clob.polymarket.com",
        )
        self.client = PolymarketClient(cfg)

    def test_bucket_with_year(self) -> None:
        m = {
            "question": "Will the highest temperature in London be 61°F or below on May 19, 2026?",
            "active": True,
        }
        self.assertTrue(
            self.client._is_highest_temp_market(m, "May 19", None)
        )

    def test_event_title_includes_buckets(self) -> None:
        m = {
            "question": "Will the highest temperature in London be between 62–63°F on May 19?",
            "active": True,
        }
        self.assertTrue(
            self.client._is_highest_temp_market(
                m,
                "May 19",
                None,
                event_title="Highest temperature in London on May 19?",
            )
        )

    def test_wrong_day_filtered(self) -> None:
        m = {
            "question": "Will the highest temperature in Dallas be 73°F or below on May 17?",
            "active": True,
        }
        self.assertFalse(self.client._is_highest_temp_market(m, "May 19", None))


class TestSlugCandidates(unittest.TestCase):
    def test_slug_variants(self) -> None:
        cands = highest_temp_event_slug_candidates("Tel Aviv", "May 19", year=2026)
        self.assertIn("highest-temperature-in-tel-aviv-on-may-19", cands)
        self.assertIn("highest-temperature-in-tel-aviv-on-may-19-2026", cands)


class TestPublicSearchCollection(unittest.TestCase):
    def test_collect_from_search_payload(self) -> None:
        cfg = PolymarketConfig(
            api_key="",
            api_secret="",
            api_passphrase="",
            private_key="",
            proxy_address="",
            gamma_base_url="https://gamma-api.polymarket.com",
            clob_base_url="https://clob.polymarket.com",
        )
        client = PolymarketClient(cfg)
        payload = {
            "events": [
                {
                    "title": "Highest temperature in London on May 19?",
                    "markets": [
                        {
                            "id": "1",
                            "question": "Will the highest temperature in London be 61°F or below on May 19?",
                            "active": True,
                        }
                    ],
                }
            ],
            "pagination": {"hasMore": False},
        }
        with patch.object(client, "_request", return_value=payload):
            hits = client._public_search_highest_temperature_markets(
                "May 19", None, max_pages=1
            )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "1")


class TestGammaMarketActive(unittest.TestCase):
    def test_string_active(self) -> None:
        self.assertTrue(gamma_market_is_active({"active": "true"}))


if __name__ == "__main__":
    unittest.main()
