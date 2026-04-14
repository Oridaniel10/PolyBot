"""Gamma public-search with event ids + registry merge (research only)."""

from typing import Any, Dict, Iterator, List, Tuple

from polymarket_client import PolymarketClient

from research.jsonl_util import load_json_file, save_json_file
from research.resolution_parse import resolution_row_from_market
from config import constants as C


def iter_highest_temp_with_event_ids(
    client: PolymarketClient,
    *,
    max_pages: int = 16,
    include_inactive: bool = True,
    verbose: bool = False,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """
    yields:
    - (gamma_event_id, market_dict) for highest-temperature pattern markets.
    """
    root = client.config.gamma_base_url.rstrip("/")
    page_cap = max(1, min(40, int(max_pages)))
    # extra phrasing reorders Gamma results; kept titles must still include "highest temperature"
    queries = [
        "highest temperature",
        "highest temperature on",
    ]
    seen_mid: set[str] = set()
    for q in queries:
        for page in range(page_cap):
            try:
                data = client._request(
                    "GET",
                    f"{root}/public-search",
                    params={"q": q, "page": page},
                    with_auth=False,
                )
            except Exception:
                break
            if not isinstance(data, dict):
                break
            events = data.get("events")
            if not isinstance(events, list):
                break
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                eid = str(ev.get("id") or "").strip()
                mk = ev.get("markets")
                if not isinstance(mk, list):
                    continue
                for m in mk:
                    if not isinstance(m, dict):
                        continue
                    if not include_inactive and not m.get("active"):
                        continue
                    if not client._is_highest_temp_market(m, None, None):
                        continue
                    mid = str(m.get("id") or "").strip()
                    if not mid or mid in seen_mid:
                        continue
                    seen_mid.add(mid)
                    yield eid, m
            if verbose:
                print(
                    f"[research] gamma q={q!r} page {page + 1}/{page_cap} "
                    f"unique_markets={len(seen_mid)}",
                    flush=True,
                )
            pag = data.get("pagination")
            if not isinstance(pag, dict) or not pag.get("hasMore"):
                break


def merge_resolution_registry(
    rows: List[Dict[str, Any]],
    *,
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    by_cid: Dict[str, Any] = {}
    for row in rows:
        cid = str(row.get("condition_id") or "").strip()
        if not cid:
            continue
        ovr = overrides.get(cid) or overrides.get(row.get("market_id")) or {}
        merged = {**row, **{k: v for k, v in ovr.items() if v}}
        by_cid[cid] = merged
    return {"by_condition_id": by_cid, "schema": "v2"}


def load_overrides() -> Dict[str, Any]:
    raw = load_json_file(C.RESEARCH_RESOLUTION_OVERRIDES_FILE)
    if not raw:
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k).strip()] = v
    return out


def write_registry_from_markets(
    pairs: List[Tuple[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    rows = [resolution_row_from_market(m, gamma_event_id=eid) for eid, m in pairs]
    merged = merge_resolution_registry(rows, overrides=load_overrides())
    from datetime import datetime, timezone

    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json_file(C.RESEARCH_RESOLUTION_REGISTRY_FILE, merged)
    return merged
