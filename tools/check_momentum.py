#!/usr/bin/env python3
"""Quick diagnostic: show momentum calculations for all markets in the ring buffer.

Usage:
    python tools/check_momentum.py              # all markets with data
    python tools/check_momentum.py 12345        # specific market_id
    python tools/check_momentum.py --top 10     # top 10 movers
"""

import sys
import os
import time

# ensure repo root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategy.momentum import (
    _price_ring,
    _price_ring_lock,
    load_samples_for_market,
    warm_ring_buffer_from_disk,
)


WINDOWS = [
    ("1m", 60),
    ("2m", 120),
    ("3m", 180),
    ("4m", 240),
    ("5m", 300),
    ("15m", 900),
    ("2h", 7200),
]


def momentum_for_market(market_id: str, now_ts: float) -> dict:
    """Calculate momentum for all windows for a single market."""
    result = {"market_id": market_id}
    with _price_ring_lock:
        ring = _price_ring.get(market_id)
        n_samples = len(ring) if ring else 0
        latest_price = ring[-1][1] if ring else 0.0
        oldest_price = ring[0][1] if ring else 0.0
        latest_ts = ring[-1][0] if ring else 0.0
    result["n_samples"] = n_samples
    result["latest_price"] = latest_price
    result["oldest_price"] = oldest_price
    result["data_age_sec"] = round(now_ts - latest_ts, 1) if latest_ts > 0 else -1

    for label, wsec in WINDOWS:
        pts = load_samples_for_market(market_id, wsec, now_ts)
        if len(pts) < 2:
            result[label] = {
                "abs": 0.0,
                "pct": 0.0,
                "old": 0.0,
                "new": 0.0,
                "pts": len(pts),
            }
        else:
            old_p = pts[0][1]
            new_p = pts[-1][1]
            abs_rise = new_p - old_p
            pct_rise = ((new_p - old_p) / old_p * 100) if old_p > 1e-9 else 0.0
            result[label] = {
                "abs": round(abs_rise, 4),
                "pct": round(pct_rise, 2),
                "old": round(old_p, 4),
                "new": round(new_p, 4),
                "pts": len(pts),
            }
    return result


def print_market_momentum(m: dict) -> None:
    mid = m["market_id"]
    age = m["data_age_sec"]
    age_str = f"{age:.0f}s ago" if age >= 0 else "no data"
    print(f"\n{'═' * 70}")
    print(f"  Market: {mid}")
    print(
        f"  Samples: {m['n_samples']}  |  Latest: {m['latest_price']:.4f}  |  Last update: {age_str}"
    )
    print(f"{'─' * 70}")
    print(
        f"  {'Window':<8} {'Old':>8} {'New':>8} {'Abs Rise':>10} {'Pct Rise':>10} {'Points':>8}"
    )
    print(f"  {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 10} {'─' * 10} {'─' * 8}")
    for label, _ in WINDOWS:
        w = m[label]
        abs_str = f"{w['abs']:+.4f}"
        pct_str = f"{w['pct']:+.2f}%"
        # color: green if rising, red if dropping
        if w["abs"] > 0.001:
            abs_str = f"\033[92m{abs_str}\033[0m"
            pct_str = f"\033[92m{pct_str}\033[0m"
        elif w["abs"] < -0.001:
            abs_str = f"\033[91m{abs_str}\033[0m"
            pct_str = f"\033[91m{pct_str}\033[0m"
        print(
            f"  {label:<8} {w['old']:>8.4f} {w['new']:>8.4f} {abs_str:>18} {pct_str:>18} {w['pts']:>8}"
        )
    print(f"{'═' * 70}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Check momentum calculations from ring buffer"
    )
    parser.add_argument("market_id", nargs="?", help="Specific market_id to check")
    parser.add_argument(
        "--top", type=int, default=0, help="Show top N movers by 15m absolute rise"
    )
    parser.add_argument(
        "--all", action="store_true", help="Show ALL markets (can be long)"
    )
    args = parser.parse_args()

    print("\n🔄 Warming ring buffer from disk...")
    loaded = warm_ring_buffer_from_disk()
    now_ts = time.time()

    with _price_ring_lock:
        all_ids = list(_price_ring.keys())
    print(f"📊 Ring buffer: {loaded} samples loaded, {len(all_ids)} markets tracked\n")

    if not all_ids:
        print("⚠️  No price samples in ring buffer. Bot may not be running yet.")
        return

    if args.market_id:
        # specific market
        if args.market_id not in all_ids:
            print(f"⚠️  Market {args.market_id} not found in ring buffer.")
            print(
                f"   Available markets ({len(all_ids)}): {', '.join(sorted(all_ids)[:20])}..."
            )
            return
        m = momentum_for_market(args.market_id, now_ts)
        print_market_momentum(m)
        return

    # compute all
    results = []
    for mid in all_ids:
        results.append(momentum_for_market(mid, now_ts))

    if args.top > 0:
        # sort by 15m absolute rise descending
        results.sort(key=lambda x: abs(x["15m"]["abs"]), reverse=True)
        results = results[: args.top]
        print(f"🏆 Top {len(results)} movers by 15m absolute change:")
    elif not args.all:
        # default: show markets with non-zero 5m momentum
        active = [
            r
            for r in results
            if abs(r["5m"]["abs"]) > 0.005 or abs(r["15m"]["abs"]) > 0.01
        ]
        if active:
            active.sort(key=lambda x: abs(x["15m"]["abs"]), reverse=True)
            results = active
            print(f"📈 Showing {len(results)} markets with notable 5m/15m movement:")
        else:
            results.sort(key=lambda x: abs(x["15m"]["abs"]), reverse=True)
            results = results[:15]
            print(
                f"😴 No significant movement. Showing top {len(results)} by 15m change:"
            )

    for m in results:
        print_market_momentum(m)

    # summary table
    print(f"\n{'─' * 70}")
    print(f"  SUMMARY: {len(all_ids)} markets tracked | data freshness check:")
    stale = [r for r in results if r["data_age_sec"] > 120]
    if stale:
        print(f"  ⚠️  {len(stale)} markets with stale data (>2 min since last sample)")
    else:
        print("  ✅ All shown markets have fresh data (<2 min)")


if __name__ == "__main__":
    main()
