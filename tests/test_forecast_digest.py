"""
Smoke tests for forecast digest / preview (like test_telegram_api, test_polymarket_api).

Run from repo root:
  python tests/test_forecast_digest.py

- offline: empty digest HTML, standalone bypass of forecast_digest_enabled
- optional live: Gamma highest-temp scan + Open-Meteo (needs network + .env for full path)
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def test_empty_digest_html() -> None:
    from forecast.digest_runner import build_forecast_digest_html

    settings = types.SimpleNamespace(
        forecast_digest_scope="scan",
        enable_openweather_forecast=False,
    )
    html = build_forecast_digest_html([], settings)
    assert "Forecast digest" in html
    assert "No parsable" in html or "parsable" in html.lower()


def test_standalone_sends_when_runtime_digest_disabled() -> None:
    from forecast.digest_runner import run_forecast_digest_once

    sent: list[str] = []

    def capture(html: str) -> None:
        sent.append(html)

    telegram = MagicMock()
    telegram.is_configured.return_value = True
    telegram.send_html_chunks.side_effect = capture

    client = MagicMock()
    client.get_highest_temperature_markets.return_value = []

    settings = types.SimpleNamespace(
        forecast_digest_enabled=False,
        forecast_digest_scope="scan",
        enable_openweather_forecast=False,
    )

    with patch("forecast.digest_runner.get_effective_settings", return_value=settings):
        run_forecast_digest_once(client, telegram, standalone=False)
    assert sent == []
    client.get_highest_temperature_markets.assert_not_called()

    with patch("forecast.digest_runner.get_effective_settings", return_value=settings):
        run_forecast_digest_once(client, telegram, standalone=True)
    assert len(sent) == 1
    assert "Forecast digest" in sent[0]
    client.get_highest_temperature_markets.assert_called()


def test_preview_payload_empty() -> None:
    from forecast.digest_runner import build_forecast_preview_payload

    settings = types.SimpleNamespace(
        forecast_digest_scope="scan",
        enable_openweather_forecast=False,
    )
    payload = build_forecast_preview_payload([], settings)
    assert payload.get("group_count") == 0
    assert payload.get("groups") == []
    assert (payload.get("digest_meta") or {}).get("gamma_markets_fetched") == 0


def live_gamma_and_open_meteo() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    from config.settings import get_effective_settings
    from forecast.digest_runner import build_forecast_preview_payload
    from polymarket_client import PolymarketClient
    from strategy.bot_runner import build_config
    from strategy.time_utils import build_target_day_label, now_in_report_timezone
    from datetime import timedelta

    get_effective_settings()
    client = PolymarketClient(build_config())
    now = now_in_report_timezone()
    label = build_target_day_label(now)
    prev = build_target_day_label(now - timedelta(days=1))
    extra = [prev] if prev != label else []
    markets = client.get_highest_temperature_markets(
        target_day_label=label,
        extra_target_day_labels=extra,
        max_pages=4,
    )
    print("live: highest-temp markets fetched:", len(markets))
    eff = get_effective_settings()
    # full list can be 500+; Open-Meteo per city-day is slow — smoke-test a slice only
    sample = markets[:60]
    prev_payload = build_forecast_preview_payload(sample, eff)
    print(
        "live: preview group_count:",
        prev_payload.get("group_count"),
        "markets_scanned would be",
        len(markets),
    )
    for g in (prev_payload.get("groups") or [])[:3]:
        print(
            "  -",
            g.get("city_key"),
            g.get("date_iso"),
            "consensus_c",
            g.get("consensus_c"),
            "markets",
            len(g.get("markets") or []),
        )


def main() -> None:
    test_empty_digest_html()
    print("ok: empty digest html")
    test_standalone_sends_when_runtime_digest_disabled()
    print("ok: standalone bypasses forecast_digest_enabled")
    test_preview_payload_empty()
    print("ok: preview payload empty")

    if os.environ.get("FORECAST_TEST_SKIP_LIVE"):
        print("skip live (FORECAST_TEST_SKIP_LIVE set)")
        return

    try:
        live_gamma_and_open_meteo()
        print("ok: live gamma + preview payload")
    except Exception as exc:
        print("live section failed (network or .env):", repr(exc))


if __name__ == "__main__":
    main()
