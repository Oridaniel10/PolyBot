import hashlib
import json
from typing import Any, Dict, List

from polymarket_client import PolymarketClient


def portfolio_snapshot_fingerprint(client: PolymarketClient) -> str:
    balance = client.get_portfolio_balance(force_allowance_refresh=False)
    cash = round(float(balance.get("cash") or 0), 2)
    positions = client.get_open_positions()

    def pos_key(p: Dict[str, Any]) -> str:
        return str(p.get("market_id") or p.get("condition_id") or p.get("title") or "")

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
