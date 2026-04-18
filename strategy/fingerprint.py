import hashlib
import json
from typing import Any, Dict, List, Optional

import requests

from polymarket_client import PolymarketClient

def portfolio_snapshot_fingerprint(
    client: PolymarketClient,
    *,
    previous_on_error: Optional[str] = None,
) -> str:
    """
    Stable hash of cash + open position rows (ids, size, avg).

    On network/API failure returns previous_on_error if set, else "" so the
    bot loop never crashes (no fake hash that could trigger a false portfolio alert).
    """
    prev = (previous_on_error or "").strip()

    def pos_key(p: Dict[str, Any]) -> str:
        return str(p.get("market_id") or p.get("condition_id") or p.get("title") or "")

    try:
        balance = client.get_portfolio_balance(force_allowance_refresh=False)
        cash = round(float(balance.get("cash") or 0), 2)
        positions = client.get_open_positions()

        rows: List[Any] = []
        for p in sorted(positions, key=pos_key):
            rows.append(
                [
                    pos_key(p),
                    round(float(p.get("size") or 0), 4),
                    round(float(p.get("avg_price") or 0), 4),
                ]
            )
        blob = json.dumps(
            {"cash": cash, "n": len(rows), "rows": rows},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except (requests.RequestException, OSError, ValueError, TypeError, KeyError):
        if prev:
            return prev
        return ""
