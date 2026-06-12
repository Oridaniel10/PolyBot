"""Redis-backed price sample store.

Sorted set per market: key=prices:{market_id}, score=unix_ts, member="{ts:.6f}:{price:.6f}".
Anchor-point query (one point before window) mirrors the ring-buffer behaviour.
All operations are non-fatal: a Redis failure logs a warning and returns empty data.
"""

import time
from typing import List, Optional, Tuple

_client = None
_KEY_PREFIX = "prices:"
_PRICE_TTL_MAX_AGE_SEC = (
    7500.0  # 2h 5m — keeps full 2h history for stagnation stop-loss
)


def connect(host: str = "localhost", port: int = 6379, db: int = 0) -> bool:
    global _client
    try:
        import redis as _redis

        pool = _redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            max_connections=20,
            socket_connect_timeout=3,
            socket_timeout=3,
            decode_responses=True,
        )
        r = _redis.Redis(connection_pool=pool)
        r.ping()
        _client = r
        return True
    except Exception as exc:
        print(
            f"[redis_store] connection failed: {exc!r} — price data will fall back to JSONL"
        )
        _client = None
        return False


def is_connected() -> bool:
    if _client is None:
        return False
    try:
        _client.ping()
        return True
    except Exception:
        return False


def _key(market_id: str) -> str:
    return f"{_KEY_PREFIX}{market_id}"


def _encode(ts: float, price: float) -> str:
    return f"{ts:.6f}:{price:.6f}"


def _decode(member: str) -> Tuple[float, float]:
    ts_s, price_s = member.rsplit(":", 1)
    return float(ts_s), float(price_s)


def save_price(market_id: str, yes_price: float, ts: float) -> None:
    if _client is None:
        return
    try:
        _client.zadd(_key(market_id), {_encode(ts, yes_price): ts})
    except Exception as exc:
        print(f"[redis_store] save_price error: {exc!r}")


def save_prices_pipeline(items: List[Tuple[str, float, float]]) -> None:
    """Batch write (market_id, yes_price, ts) via Redis pipeline."""
    if _client is None or not items:
        return
    try:
        pipe = _client.pipeline(transaction=False)
        for market_id, yes_price, ts in items:
            pipe.zadd(_key(market_id), {_encode(ts, yes_price): ts})
        pipe.execute()
    except Exception as exc:
        print(f"[redis_store] save_prices_pipeline error: {exc!r}")


def get_price_window(
    market_id: str, window_sec: float, now_ts: Optional[float] = None
) -> List[Tuple[float, float]]:
    """Return (ts, price) pairs in [now-window_sec, now] plus one anchor before the cutoff."""
    if _client is None:
        return []
    if now_ts is None:
        now_ts = time.time()
    cutoff = now_ts - window_sec
    try:
        key = _key(market_id)
        members_in = _client.zrangebyscore(key, cutoff, "+inf", withscores=True)
        out: List[Tuple[float, float]] = []
        for member, _score in members_in:
            try:
                out.append(_decode(member))
            except (ValueError, AttributeError):
                continue

        # One anchor point: latest entry strictly before the cutoff
        anchor = _client.zrevrangebyscore(
            key, f"({cutoff}", "-inf", start=0, num=1, withscores=True
        )
        if not anchor:
            anchor = _client.zrevrangebyscore(
                key, cutoff, "-inf", start=0, num=1, withscores=True
            )
        for member, _score in anchor:
            try:
                out.insert(0, _decode(member))
            except (ValueError, AttributeError):
                continue

        out.sort(key=lambda x: x[0])
        return out
    except Exception as exc:
        print(f"[redis_store] get_price_window error: {exc!r}")
        return []


def cleanup_old_entries(
    market_id: str, max_age_sec: float = _PRICE_TTL_MAX_AGE_SEC
) -> int:
    if _client is None:
        return 0
    try:
        cutoff = time.time() - max_age_sec
        return int(_client.zremrangebyscore(_key(market_id), "-inf", cutoff))
    except Exception as exc:
        print(f"[redis_store] cleanup_old_entries error: {exc!r}")
        return 0


def cleanup_all_markets(max_age_sec: float = _PRICE_TTL_MAX_AGE_SEC) -> int:
    if _client is None:
        return 0
    total = 0
    try:
        cutoff = time.time() - max_age_sec
        cursor = 0
        keys: List[str] = []
        while True:
            cursor, batch = _client.scan(cursor, match=f"{_KEY_PREFIX}*", count=200)
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return 0
        pipe = _client.pipeline(transaction=False)
        for k in keys:
            pipe.zremrangebyscore(k, "-inf", cutoff)
        results = pipe.execute()
        total = sum(r for r in results if isinstance(r, int))
    except Exception as exc:
        print(f"[redis_store] cleanup_all_markets error: {exc!r}")
    return total


def get_oldest_price_in_window(
    market_id: str, window_sec: float, now_ts: Optional[float] = None
) -> Optional[float]:
    """Return the oldest known price inside the last window_sec (no anchor trick).

    Used for 2-hour stagnation check: get price from ~2h ago to compare with now.
    """
    if _client is None:
        return None
    if now_ts is None:
        now_ts = time.time()
    cutoff = now_ts - window_sec
    try:
        key = _key(market_id)
        rows = _client.zrangebyscore(
            key, cutoff, "+inf", start=0, num=1, withscores=True
        )
        for member, _score in rows:
            try:
                _ts, price = _decode(member)
                return price
            except (ValueError, AttributeError):
                continue
    except Exception as exc:
        print(f"[redis_store] get_oldest_price_in_window error: {exc!r}")
    return None


def get_max_price_in_window(
    market_id: str, window_sec: float, now_ts: Optional[float] = None
) -> Optional[float]:
    """Return the highest price seen inside the last window_sec."""
    pts = get_price_window(market_id, window_sec, now_ts)
    if not pts:
        return None
    return max(p for _ts, p in pts)


def get_all_market_ids() -> List[str]:
    if _client is None:
        return []
    prefix_len = len(_KEY_PREFIX)
    ids: List[str] = []
    try:
        cursor = 0
        while True:
            cursor, batch = _client.scan(cursor, match=f"{_KEY_PREFIX}*", count=200)
            ids.extend(k[prefix_len:] for k in batch)
            if cursor == 0:
                break
    except Exception as exc:
        print(f"[redis_store] get_all_market_ids error: {exc!r}")
    return ids
