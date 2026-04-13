import json
import time
from typing import Any, Dict, List, Set, Tuple

from config.constants import FLOW_SAMPLES_FILE
from polymarket_client import (
    PolymarketClient,
    gamma_event_ids_for_market,
    gamma_market_condition_id,
    yes_token_id_from_market,
)


def _mid_from_book(book: Dict[str, Any]) -> float:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    try:
        if bids and asks:
            bp = float(bids[0].get("price") or 0)
            ap = float(asks[0].get("price") or 0)
            if bp > 0 and ap > 0:
                return (bp + ap) / 2.0
        raw = book.get("last_trade_price")
        if raw is not None:
            v = float(raw)
            if v > 0:
                return v
    except (TypeError, ValueError, IndexError):
        pass
    return 0.0


def _volume_by_condition(live_blocks: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for block in live_blocks:
        for mv in block.get("markets") or []:
            if not isinstance(mv, dict):
                continue
            m = str(mv.get("market") or "").strip().lower()
            try:
                v = float(mv.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if m:
                out[m] = v
    return out


def _build_mid_to_event(
    client: PolymarketClient,
    markets: List[Dict[str, Any]],
    active_trades: Dict[str, Any],
) -> Dict[str, str]:
    mid_to_eid: Dict[str, str] = {}
    for m in markets:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        eids = gamma_event_ids_for_market(m)
        if eids:
            mid_to_eid[mid] = eids[0]
    for mid in active_trades:
        mid = str(mid).strip()
        if not mid or mid in mid_to_eid:
            continue
        gm = client.get_market_by_id(mid)
        if not gm:
            continue
        eids = gamma_event_ids_for_market(gm)
        if eids:
            mid_to_eid[mid] = eids[0]
    return mid_to_eid


def record_flow_samples(
    client: PolymarketClient,
    markets: List[Dict[str, Any]],
    state: Dict[str, Any],
    event_cache: Dict[str, List[Dict[str, Any]]],
    settings: Any,
) -> None:
    if not getattr(settings, "enable_flow_sampling", True):
        return
    active_trades = state.get("active_trades") or {}
    mid_to_eid = _build_mid_to_event(client, markets, active_trades)
    if not mid_to_eid:
        return
    eid_to_mids: Dict[str, Set[str]] = {}
    for mid, eid in mid_to_eid.items():
        eid_to_mids.setdefault(eid, set()).add(mid)

    held_eids: Set[str] = set()
    for mid in active_trades:
        eid = mid_to_eid.get(str(mid).strip())
        if eid:
            held_eids.add(eid)

    all_eids = sorted(eid_to_mids.keys())
    priority = [e for e in all_eids if e in held_eids]
    rest = [e for e in all_eids if e not in held_eids]
    max_e = int(getattr(settings, "flow_max_events_per_tick", 8))
    max_e = max(1, min(40, max_e))
    chosen = (priority + rest)[:max_e]

    FLOW_SAMPLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    lines_to_write: List[str] = []

    for eid in chosen:
        if eid not in event_cache:
            event_cache[eid] = client.get_markets_for_event_id(eid)
        siblings = event_cache.get(eid) or []
        if not siblings:
            continue
        cids = [
            gamma_market_condition_id(m)
            for m in siblings
            if gamma_market_condition_id(m)
        ]
        oi_map = client.get_data_api_open_interest_map(cids)
        live_blocks = client.get_data_api_live_volume(eid)
        vol_c = _volume_by_condition(live_blocks)

        token_pairs: List[Tuple[str, str, str]] = []
        for m in siblings:
            mid = str(m.get("id") or "").strip()
            cid = gamma_market_condition_id(m)
            if not mid or not cid:
                continue
            try:
                tid = yes_token_id_from_market(m)
            except ValueError:
                continue
            token_pairs.append((tid, mid, cid))

        if not token_pairs:
            continue
        tids = [x[0] for x in token_pairs]
        books = client.post_clob_order_books(tids)
        by_asset: Dict[str, Dict[str, Any]] = {}
        for b in books:
            aid = str(b.get("asset_id") or "").strip().lower()
            if aid:
                by_asset[aid] = b

        for tid, mid, cid in token_pairs:
            book = by_asset.get(str(tid).strip().lower()) or {}
            ymid = _mid_from_book(book) if book else 0.0
            ck = cid.strip().lower()
            oi_val = 0.0
            for k, v in oi_map.items():
                if k.strip().lower() == ck:
                    oi_val = v
                    break
            lv = 0.0
            for k, v in vol_c.items():
                if k == ck:
                    lv = v
                    break
            row = {
                "ts": ts,
                "event_id": str(eid),
                "market_id": mid,
                "condition_id": cid,
                "yes_mid": round(ymid, 6),
                "oi": oi_val,
                "live_vol": lv,
            }
            lines_to_write.append(json.dumps(row, ensure_ascii=True, separators=(",", ":")))

    if not lines_to_write:
        return
    try:
        with FLOW_SAMPLES_FILE.open("a", encoding="utf-8") as f:
            for ln in lines_to_write:
                f.write(ln + "\n")
    except OSError:
        return

    _maybe_prune_flow_file(ts)


def _maybe_prune_flow_file(now_ts: float) -> None:
    if not FLOW_SAMPLES_FILE.is_file():
        return
    try:
        if FLOW_SAMPLES_FILE.stat().st_size < 800_000:
            return
    except OSError:
        return
    cutoff = now_ts - 7200.0
    try:
        text = FLOW_SAMPLES_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    kept: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if float(o.get("ts") or 0) >= cutoff:
                kept.append(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    try:
        FLOW_SAMPLES_FILE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass
