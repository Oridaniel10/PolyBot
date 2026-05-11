import json
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Any, Dict, List, Optional

from config.constants import TERM_DIM
from config.settings import get_effective_settings
from notifications.terminal import print_scan_summary, term_wrap
from polymarket_client import PolymarketClient, gamma_event_ids_for_market
from strategy.market_match import (
    trade_row_matches_gamma_market,
    warn_gamma_trade_mismatch_once,
)
from strategy.momentum import record_samples_for_market_dicts
from strategy.probability import parse_market_probability
from strategy.time_utils import build_target_day_label, now_in_report_timezone
from strategy.trades import process_single_market
from telegram_bot import TelegramBot


def _fetch_extra_market(
    client: PolymarketClient,
    market_id: str,
    condition_id: str,
) -> Optional[Dict[str, Any]]:
    extra = client.get_market_by_id(market_id)
    if not extra and condition_id:
        extra = client.get_market_by_condition_id(condition_id)
    return extra


def run_once(
    client: PolymarketClient, telegram: TelegramBot, state: Dict[str, Any]
) -> Dict[str, Any]:
    settings = get_effective_settings()
    event_cache: Dict[str, List[Dict[str, Any]]] = {}
    now = now_in_report_timezone()
    target_day_label = build_target_day_label(now)
    prev_day_label = build_target_day_label(now - timedelta(days=1))
    extra_day_labels = [prev_day_label] if prev_day_label != target_day_label else []
    active_trades = state.setdefault("active_trades", {})

    # --- parallel pre-fetch: markets scan + flush pending + balance ---
    anchor_ids = [int(mid) for mid in active_trades if mid.isdigit()]
    markets: List[Dict[str, Any]] = []
    balance: Dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_markets = pool.submit(
            client.get_highest_temperature_markets,
            target_day_label=target_day_label,
            extra_target_day_labels=extra_day_labels,
            anchor_ids=anchor_ids,
        )
        fut_flush = pool.submit(client.flush_pending_limit_sells, active_trades)
        fut_balance = pool.submit(client.get_portfolio_balance)

        markets = fut_markets.result()

        by_event_scan: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in markets:
            if not isinstance(m, dict):
                continue
            evs = gamma_event_ids_for_market(m)
            if evs:
                by_event_scan[str(evs[0]).strip()].append(m)
        print(
            f"[scan_coverage] total_markets={len(markets)} "
            f"unique_events={len(by_event_scan)}"
        )
        for eid in sorted(by_event_scan.keys()):
            scan_rows = by_event_scan[eid]
            full_ev = client.get_markets_for_event_id(eid)
            k_ret = len(scan_rows)
            l_all = len(full_ev)
            mids = [str(x.get("id") or "") for x in scan_rows]
            yes_l = [round(parse_market_probability(x), 4) for x in scan_rows]
            print(
                "[event_buckets] "
                f"event_id={eid} n_returned={k_ret} all_event_n={l_all} "
                f"mids={json.dumps(mids)} yes={json.dumps(yes_l)}"
            )

        fut_flush.result()
        balance = fut_balance.result()

    cash = float(balance.get("cash") or 0)
    print_scan_summary(
        markets,
        state,
        settings,
        target_day_label,
        cash,
        secondary_day_label=prev_day_label if extra_day_labels else None,
    )

    scanned_market_ids: set[str] = set()
    sample_markets: Dict[str, Dict[str, Any]] = {}

    for market in markets:
        mid = str(market.get("id") or "")
        if mid:
            scanned_market_ids.add(mid)
            sample_markets[mid] = market
        process_single_market(
            client,
            telegram,
            state,
            market,
            settings,
            event_cache,
            allow_new_buys=True,
        )

    # --- parallel extra exit pass: fetch held markets not in scan ---
    exit_ids = [mid for mid in active_trades if mid not in scanned_market_ids]
    if exit_ids:
        fetched: Dict[str, Optional[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            for market_id in exit_ids:
                cid = str(
                    (active_trades.get(market_id) or {}).get("condition_id") or ""
                )
                futures[pool.submit(_fetch_extra_market, client, market_id, cid)] = (
                    market_id
                )
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    fetched[mid] = fut.result()
                except Exception:
                    fetched[mid] = None

        extra_exit_pass = 0
        for market_id in exit_ids:
            extra = fetched.get(market_id)
            if not extra:
                continue
            tr_row = active_trades.get(market_id) or {}
            if tr_row and not trade_row_matches_gamma_market(tr_row, extra):
                warn_gamma_trade_mismatch_once(tr_row, extra, telegram)
                continue
            extra_exit_pass += 1
            emid = str(extra.get("id") or "")
            if emid:
                sample_markets[emid] = extra
            process_single_market(
                client,
                telegram,
                state,
                extra,
                settings,
                event_cache,
                allow_new_buys=False,
            )
        if extra_exit_pass:
            print(
                term_wrap(
                    TERM_DIM,
                    f"[exit pass] evaluated {extra_exit_pass} held market(s) "
                    f"not in today's '{target_day_label}' scan (take-profit / stop / claim)",
                )
            )

    # sibling sampling coverage: which event buckets were in the main scan vs gamma event.
    for eid, ev_markets in sorted(event_cache.items()):
        all_ids: set[str] = set()
        for m in ev_markets or []:
            if not isinstance(m, dict):
                continue
            sm = str(m.get("id") or "").strip()
            if sm:
                all_ids.add(sm)
        in_scan = {mid for mid in all_ids if mid in scanned_market_ids}
        missing = sorted(all_ids - in_scan)
        print(
            "[sibling_coverage] "
            f"event_id={eid} scanned_siblings={len(in_scan)} "
            f"total_siblings={len(all_ids)} missing_mids={json.dumps(missing)}"
        )

    # --- record price samples: every Gamma sibling in each event we scanned ---
    # previously only scan rows were sampled → plots/momentum saw gaps for non-returned buckets.
    samples = list(sample_markets.values())
    seen_mids: set[str] = {
        str(m.get("id") or "").strip()
        for m in samples
        if isinstance(m, dict) and str(m.get("id") or "").strip()
    }
    touched_events: set[str] = set()
    for m in sample_markets.values():
        if not isinstance(m, dict):
            continue
        evs = gamma_event_ids_for_market(m)
        if not evs:
            continue
        eid = str(evs[0]).strip()
        if not eid or eid in touched_events:
            continue
        touched_events.add(eid)
        sibs = event_cache.get(eid)
        if not sibs:
            sibs = client.get_markets_for_event_id(eid)
            if sibs:
                event_cache[eid] = sibs
        if not sibs:
            continue
        for gm in sibs:
            if not isinstance(gm, dict):
                continue
            smid = str(gm.get("id") or "").strip()
            if smid and smid not in seen_mids:
                seen_mids.add(smid)
                samples.append(gm)

    if len(samples) > len(sample_markets):
        print(
            term_wrap(
                TERM_DIM,
                f"[price_samples] sibling_expand scan_rows={len(sample_markets)} "
                f"sample_total={len(samples)} events={len(touched_events)}",
            )
        )

    # Record Gamma prices for all samples. Active positions get live CLOB prices every 2s
    # from fast_exit_watcher (giving smooth plot curves without parallel connection overload).
    threading.Thread(
        target=record_samples_for_market_dicts, args=(samples,), daemon=True
    ).start()

    return state
