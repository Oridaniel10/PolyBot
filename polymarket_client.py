import json
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Account


REQUEST_TIMEOUT_SECONDS = 20
DATA_API_BASE_URL = "https://data-api.polymarket.com"
RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
TEMPERATURE_MARKET_KEYWORDS = ("daily temperature", "temperature")
WEATHER_KEYWORDS = ("high temperature", "low temperature")
HIGHEST_TEMPERATURE_KEYWORDS = (
    "highest temperature",
    "high temperature",
    "hottest",
    "maximum temperature",
)
LIVE_POSITION_MIN_CUR_PRICE = 1e-6
LIVE_POSITION_MIN_CURRENT_VALUE_USD = 0.01
DATA_API_POSITIONS_CACHE_TTL_SEC = 12.0
# Gamma /markets offset scans often miss daily weather; public-search finds events reliably.
HIGHEST_TEMP_PUBLIC_SEARCH_CACHE_TTL_SEC = 120.0
HIGHEST_TEMP_PUBLIC_SEARCH_MAX_PAGES = 8
CLOB_BALANCE_ALLOWANCE_CACHE_TTL_SEC = 50.0
CLOB_ORDER_SIDE_BUY = "BUY"
CLOB_ORDER_SIDE_SELL = "SELL"
CLOB_RETRY_ATTEMPTS = 6
CLOB_RETRY_BASE_SEC = 1.2
SELL_EXECUTION_MARKET = "market"
SELL_EXECUTION_LIMIT_GTC = "limit_gtc"
SELL_EXECUTION_SKIPPED = "skipped"

HIGHEST_TEMPERATURE_QUERY_PATTERNS = (
    r"^highest temperature in .+ on .+\?$",
    r"^highest temperature in .+\?$",
    r"^will the highest temperature in .+ be .+ on .+\?$",
)


def title_matches_highest_temp_target_day(
    title_lower: str, target_day_label: str
) -> bool:
    """
    gamma often uses 'on april 10?' but many markets use 'on april 10, 2026?'.
    require month+day with a word boundary so 'april 1' does not match 'april 10'.
    """
    label = (target_day_label or "").strip().lower()
    if not label:
        return True
    parts = label.split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return f" on {label}?" in title_lower
    day = parts[-1]
    month = " ".join(parts[:-1])
    return bool(re.search(rf" on {re.escape(month)} {re.escape(day)}\b", title_lower))


TEMPERATURE_PATTERNS = (
    r"\bdaily temperature\b",
    r"\btemperature\b",
    r"\bglobal temperature\b",
    r"\bhottest\b",
    r"\bcoldest\b",
    r"\bheatwave\b",
    r"\barctic sea ice\b",
)


def condition_id_variants(condition_id: str) -> List[str]:
    c = (condition_id or "").strip()
    if not c:
        return []
    variants: List[str] = []
    low = c.lower()
    for candidate in (c, low):
        if candidate and candidate not in variants:
            variants.append(candidate)
    hex_body = low[2:] if low.startswith("0x") else low
    if re.fullmatch(r"[0-9a-f]{64}", hex_body):
        with_prefix = "0x" + hex_body
        for candidate in (with_prefix, hex_body):
            if candidate not in variants:
                variants.append(candidate)
    return variants


def condition_ids_equivalent(a: str, b: str) -> bool:
    va = set(condition_id_variants(a))
    vb = set(condition_id_variants(b))
    return bool(va & vb)


def gamma_market_condition_id(market: Dict[str, Any]) -> str:
    raw = (
        market.get("conditionId")
        or market.get("condition_id")
        or market.get("questionID")
        or market.get("questionId")
        or ""
    )
    return str(raw).strip()


def gamma_market_matches_condition(market: Dict[str, Any], condition_id: str) -> bool:
    if not condition_id or not isinstance(market, dict):
        return False
    gc = gamma_market_condition_id(market)
    if not gc:
        return False
    return condition_ids_equivalent(gc, condition_id)


def is_book_live_position(row: Dict[str, Any]) -> bool:
    size = float(row.get("size") or 0)
    if size < 1e-9:
        return False
    cur = float(row.get("curPrice") or 0)
    val = float(row.get("currentValue") or 0)
    return (
        cur > LIVE_POSITION_MIN_CUR_PRICE or val > LIVE_POSITION_MIN_CURRENT_VALUE_USD
    )


def parse_clob_token_ids(market: Dict[str, Any]) -> List[str]:
    raw = market.get("clobTokenIds") or market.get("clob_token_ids")
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        raw = parsed
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def yes_token_id_from_market(market: Dict[str, Any]) -> str:
    tokens = parse_clob_token_ids(market)
    if not tokens:
        raise ValueError("market has no clobTokenIds")
    outcomes_raw = market.get("outcomes")
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    else:
        outcomes = outcomes_raw if isinstance(outcomes_raw, list) else []
    for index, label in enumerate(outcomes):
        if str(label).strip().lower() in ("yes", "y"):
            if index < len(tokens):
                return tokens[index]
    return tokens[0]


def clob_retry_transient(operation: Any) -> Any:
    from py_clob_client.exceptions import PolyApiException

    last_exc: Optional[BaseException] = None
    for attempt in range(CLOB_RETRY_ATTEMPTS):
        try:
            return operation()
        except Exception as exc:
            last_exc = exc
            retryable = False
            if isinstance(exc, PolyApiException):
                if exc.status_code in (408, 425, 429, 502, 503, 504):
                    retryable = True
                msg = exc.error_msg
                if isinstance(msg, str) and "not ready" in msg.lower():
                    retryable = True
            if retryable and attempt < CLOB_RETRY_ATTEMPTS - 1:
                delay = CLOB_RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.4)
                time.sleep(delay)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("clob_retry_transient: empty loop")


def align_price_to_tick_sell(price: float, tick: float) -> float:
    if tick <= 0:
        return min(0.99, max(0.01, round(price, 4)))
    if price <= 0:
        price = tick
    steps = math.floor((price + 1e-12) / tick)
    out = steps * tick
    if out < tick:
        out = tick
    ceiling = 1.0 - tick
    if out > ceiling:
        out = ceiling
    return round(out, 12)


def midpoint_price_from_clob(clob: Any, token_id: str) -> float:
    try:
        raw = clob.get_midpoint(token_id)
        if isinstance(raw, dict):
            for key in ("mid", "midpoint"):
                val = raw.get(key)
                if val is not None:
                    return float(val)
    except Exception:
        pass
    return 0.0


def clob_sell_price_hint(
    clob: Any, token_id: str, book: Any, reference_price: float
) -> float:
    candidates: List[float] = []
    if book and getattr(book, "bids", None):
        try:
            candidates.append(float(book.bids[0].price))
        except (TypeError, ValueError, IndexError):
            pass
    if book and getattr(book, "last_trade_price", None):
        try:
            lp = float(book.last_trade_price)
            if lp > 0:
                candidates.append(lp)
        except (TypeError, ValueError):
            pass
    if reference_price and reference_price > 0:
        candidates.append(float(reference_price))
    mid = midpoint_price_from_clob(clob, token_id)
    if mid > 0:
        candidates.append(mid)
    try:
        pr = clob.get_price(token_id, side="SELL")
        if isinstance(pr, dict):
            pv = pr.get("price")
            if pv is not None:
                candidates.append(float(pv))
    except Exception:
        pass
    if not candidates:
        return 0.0
    return min(candidates)


def order_status_lower(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("status")
        or payload.get("order_status")
        or payload.get("state")
        or ""
    ).lower()


def order_fully_filled(payload: Dict[str, Any]) -> bool:
    st = order_status_lower(payload)
    if st in ("filled", "matched", "executed", "complete", "completed"):
        return True
    try:
        orig = float(
            payload.get("original_size")
            or payload.get("size")
            or payload.get("originalSize")
            or 0
        )
        matched = float(
            payload.get("size_matched")
            or payload.get("filled_size")
            or payload.get("filledSize")
            or payload.get("sizeMatched")
            or 0
        )
        if orig > 1e-9 and matched >= orig - 1e-6:
            return True
    except (TypeError, ValueError):
        pass
    return False


def order_terminal_cancelled(payload: Dict[str, Any]) -> bool:
    if order_fully_filled(payload):
        return False
    st = order_status_lower(payload)
    return st in ("cancelled", "canceled", "expired", "rejected", "failed")


def format_sell_attempt_summary(
    market: Dict[str, Any],
    share_size: float,
    ref: float,
    min_order_size: Optional[float],
    limit_px: Optional[float],
    extra_note: str,
) -> str:
    mid = market.get("id", "")
    q = (market.get("question") or market.get("title") or "?")[:160]
    lines = [
        f"market_id={mid}",
        f"question={q}",
        f"attempted_sell_shares={share_size:.6f}",
        f"reference_price~={ref:.4f} (gamma / mark)",
    ]
    if min_order_size is not None:
        lines.append(f"clob_min_order_size={min_order_size:.4f}")
    if limit_px is not None:
        lines.append(f"limit_gtc_price_attempt={limit_px:.4f}")
    lines.append("order_steps=market_FAK -> market_FOK -> limit_GTC")
    if extra_note:
        lines.append(f"note={extra_note}")
    return "\n".join(lines)


@dataclass
class PolymarketConfig:
    api_key: str
    api_secret: str
    api_passphrase: str
    private_key: str
    proxy_address: str
    gamma_base_url: str
    clob_base_url: str
    clob_signature_type: Optional[int] = None


class PolymarketClient:
    def __init__(self, config: PolymarketConfig) -> None:
        self.config = config
        self.session = requests.Session()
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_CODES,
            allowed_methods=frozenset(["GET", "POST", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.wallet_address = (
            Account.from_key(config.private_key).address if config.private_key else ""
        )
        self.trading_address = config.proxy_address or self.wallet_address
        self._condition_market_cache: Dict[str, str] = {}
        self._slug_market_cache: Dict[str, str] = {}
        self._temp_id_range: Optional[tuple] = None
        self._temp_id_range_lock = threading.Lock()
        self._highest_temp_public_search_cache: Optional[
            tuple[float, tuple[str, ...], List[Dict[str, Any]]]
        ] = None
        self._data_positions_cache_monotonic: float = 0.0
        self._data_positions_cache_rows: List[Dict[str, Any]] = []
        self._balance_allowance_monotonic: float = 0.0
        self._balance_allowance_cash_usd: float = 0.0
        self._balance_allowance_extras: Dict[str, Any] = {}

    def invalidate_data_positions_cache(self) -> None:
        self._data_positions_cache_monotonic = 0.0
        self._data_positions_cache_rows = []

    def invalidate_balance_allowance_cache(self) -> None:
        self._balance_allowance_monotonic = 0.0

    def resolve_clob_signature_type(self) -> Optional[int]:
        if self.config.clob_signature_type is not None:
            return self.config.clob_signature_type
        proxy = (self.config.proxy_address or "").strip()
        if not proxy or not self.wallet_address:
            return None
        if proxy.lower() == self.wallet_address.lower():
            return None
        return 1

    def make_clob_client(self) -> Any:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        funder = (self.config.proxy_address or "").strip() or None
        sig_type = self.resolve_clob_signature_type()
        kwargs: Dict[str, Any] = {
            "host": self.config.clob_base_url.rstrip("/"),
            "chain_id": 137,
            "key": self.config.private_key,
            "creds": ApiCreds(
                self.config.api_key,
                self.config.api_secret,
                self.config.api_passphrase,
            ),
            "funder": funder,
        }
        if sig_type is not None:
            kwargs["signature_type"] = sig_type
        return ClobClient(**kwargs)

    def resolve_market_id_from_slug(self, slug: str) -> Optional[str]:
        slug = (slug or "").strip()
        if not slug:
            return None
        cached = self._slug_market_cache.get(slug)
        if cached:
            return cached
        url = f"{self.config.gamma_base_url.rstrip('/')}/markets"
        try:
            payload = self._request(
                "GET",
                url,
                params={"slug": slug},
                with_auth=False,
            )
        except requests.RequestException:
            return None
        if isinstance(payload, list) and payload:
            market_id = str(payload[0].get("id") or "")
            if market_id:
                self._slug_market_cache[slug] = market_id
                return market_id
        return None

    def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        mid = str(market_id).strip()
        if not mid:
            return None
        base = f"{self.config.gamma_base_url.rstrip('/')}/markets"
        try:
            payload = self._request("GET", f"{base}/{mid}", with_auth=False)
        except requests.RequestException:
            payload = None
        if isinstance(payload, dict) and str(payload.get("id") or "") == mid:
            return payload
        try:
            lst = self._request("GET", base, params={"id": mid}, with_auth=False)
        except requests.RequestException:
            return None
        if isinstance(lst, list) and lst:
            first = lst[0]
            if str(first.get("id") or "") == mid:
                return first
        return None

    def get_markets_for_event_id(self, event_id: str) -> List[Dict[str, Any]]:
        """
        sibling markets under the same Gamma event.

        uses /events/{id} which reliably embeds the correct market list.
        /markets?event_id=X is unreliable — Gamma silently ignores unknown
        params and returns unrelated popular markets instead.
        """
        eid = str(event_id or "").strip()
        if not eid:
            return []
        root = self.config.gamma_base_url.rstrip("/")
        try:
            ev = self._request("GET", f"{root}/events/{eid}", with_auth=False)
        except requests.RequestException:
            ev = None
        if isinstance(ev, dict):
            mk = ev.get("markets")
            if isinstance(mk, list):
                return [m for m in mk if isinstance(m, dict)]
        return []

    def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        loads a gamma market dict by condition id (tries common query param names).

        validates condition id on the returned object — gamma may ignore bad params
        and return an unrelated list; taking [0] would sell the wrong token.
        """
        if not condition_id:
            return None
        root = condition_id.strip()
        cached_mid = self._condition_market_cache.get(root)
        if isinstance(cached_mid, str) and cached_mid:
            got = self.get_market_by_id(cached_mid)
            if got and gamma_market_matches_condition(got, condition_id):
                return got
            for v in condition_id_variants(condition_id):
                self._condition_market_cache.pop(v, None)
        url = f"{self.config.gamma_base_url.rstrip('/')}/markets"
        for variant in condition_id_variants(condition_id):
            for param_key in ("condition_ids", "conditionId"):
                try:
                    payload = self._request(
                        "GET",
                        url,
                        params={param_key: variant},
                        with_auth=False,
                    )
                except requests.RequestException:
                    continue
                if not isinstance(payload, list) or not payload:
                    continue
                for m in payload:
                    if not isinstance(m, dict):
                        continue
                    if not gamma_market_matches_condition(m, condition_id):
                        continue
                    mid = str(m.get("id") or "")
                    if mid:
                        for v in condition_id_variants(condition_id):
                            self._condition_market_cache[v] = mid
                        return m
        return None

    def resolve_market_id_from_condition(self, condition_id: str) -> Optional[str]:
        m = self.get_market_by_condition_id(condition_id)
        return str(m.get("id") or "") if m else None

    def get_data_api_positions(
        self, *, force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        user = (self.trading_address or self.wallet_address or "").strip()
        if not user:
            return []
        now = time.monotonic()
        if (
            not force_refresh
            and self._data_positions_cache_rows
            and (now - self._data_positions_cache_monotonic)
            < DATA_API_POSITIONS_CACHE_TTL_SEC
        ):
            return self._data_positions_cache_rows
        url = f"{DATA_API_BASE_URL.rstrip('/')}/positions"
        try:
            response = self.session.get(
                url, params={"user": user}, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            return []
        rows = data if isinstance(data, list) else []
        self._data_positions_cache_rows = rows
        self._data_positions_cache_monotonic = time.monotonic()
        return rows

    def _auth_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["API-KEY"] = self.config.api_key
        if self.config.api_secret:
            headers["API-SECRET"] = self.config.api_secret
        if self.config.api_passphrase:
            headers["API-PASSPHRASE"] = self.config.api_passphrase
        if self.trading_address:
            headers["POLY-ADDRESS"] = self.trading_address
        if self.wallet_address:
            headers["POLY-SIGNER"] = self.wallet_address
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        with_auth: bool = True,
    ) -> Any:
        headers = self._auth_headers() if with_auth else {"Accept": "application/json"}
        for attempt in range(RETRY_TOTAL + 1):
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in RETRY_STATUS_CODES and attempt < RETRY_TOTAL:
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_time = float(retry_after)
                else:
                    sleep_time = (2**attempt) + random.uniform(0.0, 0.5)
                time.sleep(sleep_time)
                continue
            response.raise_for_status()
            if not response.text:
                return {}
            return response.json()
        return {}

    def get_daily_temperature_markets(self) -> List[Dict[str, Any]]:
        targeted_markets = self.get_targeted_weather_markets()
        filtered_targeted = self._filter_temperature_markets(targeted_markets)
        if filtered_targeted:
            return filtered_targeted

        deep_scan_markets = self.get_markets(limit=1000, max_pages=20, active_only=True)
        return self._filter_temperature_markets(deep_scan_markets)

    def _filter_temperature_markets(
        self, markets: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        filtered_markets: List[Dict[str, Any]] = []
        seen_market_ids = set()
        for market in markets:
            market_id = str(market.get("id") or "")
            if not market_id or market_id in seen_market_ids:
                continue
            if self.is_temperature_market(market):
                filtered_markets.append(market)
                seen_market_ids.add(market_id)
        return filtered_markets

    @staticmethod
    def _merge_target_day_labels(
        target_day_label: Optional[str],
        extra_target_day_labels: Optional[List[str]],
    ) -> List[str]:
        labels: List[str] = []
        if target_day_label and str(target_day_label).strip():
            labels.append(str(target_day_label).strip())
        for raw in extra_target_day_labels or []:
            s = str(raw or "").strip()
            if s and s not in labels:
                labels.append(s)
        return labels

    def _is_highest_temp_market(
        self,
        market: Dict[str, Any],
        target_day_label: Optional[str],
        extra_target_day_labels: Optional[List[str]] = None,
    ) -> bool:
        title = (market.get("question") or market.get("title") or "").strip().lower()
        if "highest temperature" not in title:
            return False
        if not any(re.match(p, title) for p in HIGHEST_TEMPERATURE_QUERY_PATTERNS):
            return False
        labels = self._merge_target_day_labels(
            target_day_label, extra_target_day_labels
        )
        if labels and not any(
            title_matches_highest_temp_target_day(title, lb) for lb in labels
        ):
            return False
        return True

    def _discover_temp_id_range(
        self,
        anchor_ids: List[int],
        target_day_label: Optional[str],
        extra_target_day_labels: Optional[List[str]] = None,
    ) -> tuple:
        """Find the range of Gamma market IDs that contain temperature markets."""
        if anchor_ids:
            lo = min(anchor_ids) - 300
            hi = max(anchor_ids) + 300
            return (lo, hi)
        # No anchors: scan Gamma pages starting from high offsets
        for start_offset in range(30000, 80000, 5000):
            try:
                markets = self.get_markets(
                    limit=1000,
                    max_pages=1,
                    active_only=True,
                    extra_params={"offset": start_offset},
                )
                for m in markets:
                    if self._is_highest_temp_market(
                        m, target_day_label, extra_target_day_labels
                    ):
                        mid = int(m.get("id") or 0)
                        if mid > 0:
                            return (mid - 300, mid + 600)
            except Exception:
                continue
        return (0, 0)

    def _public_search_highest_temperature_markets(
        self,
        target_day_label: Optional[str],
        extra_target_day_labels: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """
        Gamma list endpoints often omit weather; public-search returns multi-market events.
        """
        root = self.config.gamma_base_url.rstrip("/")
        labels = self._merge_target_day_labels(
            target_day_label, extra_target_day_labels
        )
        specific_queries: List[str] = []
        for lb in labels:
            q = str(lb or "").strip().lower()
            if q:
                specific_queries.append(f"highest temperature {q}")
        query_phases: List[List[str]] = []
        if specific_queries:
            query_phases.append(specific_queries)
            query_phases.append(["highest temperature"])
        else:
            query_phases.append(["highest temperature"])

        seen: Set[str] = set()
        out: List[Dict[str, Any]] = []

        def run_queries(query_list: List[str]) -> None:
            for q in query_list:
                for page in range(HIGHEST_TEMP_PUBLIC_SEARCH_MAX_PAGES):
                    try:
                        data = self._request(
                            "GET",
                            f"{root}/public-search",
                            params={"q": q, "page": page},
                            with_auth=False,
                        )
                    except (requests.RequestException, ValueError, TypeError):
                        break
                    if not isinstance(data, dict):
                        break
                    events = data.get("events")
                    if not isinstance(events, list):
                        break
                    for ev in events:
                        mk = ev.get("markets")
                        if not isinstance(mk, list):
                            continue
                        for m in mk:
                            if not isinstance(m, dict):
                                continue
                            if not m.get("active"):
                                continue
                            if not self._is_highest_temp_market(
                                m, target_day_label, extra_target_day_labels
                            ):
                                continue
                            mid = str(m.get("id") or "")
                            if mid and mid not in seen:
                                seen.add(mid)
                                out.append(m)
                    pag = data.get("pagination")
                    if not isinstance(pag, dict) or not pag.get("hasMore"):
                        break

        for i, phase in enumerate(query_phases):
            run_queries(phase)
            if out and i == 0 and len(query_phases) > 1:
                break
        return out

    def get_highest_temperature_markets(
        self,
        *,
        target_day_label: Optional[str] = None,
        extra_target_day_labels: Optional[List[str]] = None,
        max_pages: int = 80,
        anchor_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        labels_key = tuple(
            self._merge_target_day_labels(
                target_day_label, extra_target_day_labels
            )
        )
        now_m = time.monotonic()
        cached = self._highest_temp_public_search_cache
        if cached is not None:
            ts, key, lst = cached
            if (
                now_m - ts < HIGHEST_TEMP_PUBLIC_SEARCH_CACHE_TTL_SEC
                and key == labels_key
            ):
                return list(lst)

        search_hits = self._public_search_highest_temperature_markets(
            target_day_label, extra_target_day_labels
        )
        if search_hits:
            found_ids = [
                int(m["id"])
                for m in search_hits
                if str(m.get("id", "")).isdigit()
            ]
            if found_ids:
                with self._temp_id_range_lock:
                    self._temp_id_range = (
                        min(found_ids) - 40,
                        max(found_ids) + 40,
                    )
            self._highest_temp_public_search_cache = (
                now_m,
                labels_key,
                search_hits,
            )
            return search_hits

        # --- step 1: determine the ID range to scan ---
        with self._temp_id_range_lock:
            id_range = self._temp_id_range

        if id_range is None or id_range == (0, 0):
            anchors = anchor_ids or []
            id_range = self._discover_temp_id_range(
                anchors, target_day_label, extra_target_day_labels
            )
            with self._temp_id_range_lock:
                self._temp_id_range = id_range

        lo, hi = id_range
        if lo == 0 and hi == 0:
            return []

        # --- step 2: fetch individual markets by ID (parallel, throttled) ---
        root = self.config.gamma_base_url.rstrip("/")
        highest_markets: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        lock = threading.Lock()

        def fetch_one(mid: int) -> Optional[Dict[str, Any]]:
            try:
                resp = self.session.get(
                    f"{root}/markets/{mid}",
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if resp.status_code == 404:
                    return None
                if resp.status_code in RETRY_STATUS_CODES:
                    time.sleep(1.0)
                    resp = self.session.get(
                        f"{root}/markets/{mid}",
                        headers={"Accept": "application/json"},
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                if resp.status_code != 200:
                    return None
                m = resp.json()
                if not isinstance(m, dict):
                    return None
                if not m.get("active"):
                    return None
                if self._is_highest_temp_market(
                    m, target_day_label, extra_target_day_labels
                ):
                    return m
                return None
            except Exception:
                return None

        ids_to_scan = list(range(lo, hi + 1))
        # 5 workers → ~50 req/s → 250 req/10s (under 300 limit)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, mid): mid for mid in ids_to_scan}
            for fut in as_completed(futures):
                m = fut.result()
                if m is None:
                    continue
                mid_str = str(m.get("id") or "")
                with lock:
                    if mid_str and mid_str not in seen:
                        seen.add(mid_str)
                        highest_markets.append(m)

        # --- step 3: tighten cached range to actual found IDs + padding ---
        found_ids = [
            int(m["id"]) for m in highest_markets if str(m.get("id", "")).isdigit()
        ]
        if found_ids:
            with self._temp_id_range_lock:
                self._temp_id_range = (min(found_ids) - 20, max(found_ids) + 20)

        return highest_markets

    def get_targeted_weather_markets(self) -> List[Dict[str, Any]]:
        query_params = [
            {"search": "daily temperature", "active": "true", "limit": 200},
            {"search": "temperature", "active": "true", "limit": 200},
            {"search": "weather", "active": "true", "limit": 200},
            {"category": "weather", "active": "true", "limit": 200},
            {"tag": "weather", "active": "true", "limit": 200},
        ]

        merged: List[Dict[str, Any]] = []
        seen_market_ids = set()
        for params in query_params:
            page_markets = self.get_markets(limit=200, max_pages=2, extra_params=params)
            for market in page_markets:
                market_id = str(market.get("id") or "")
                if not market_id or market_id in seen_market_ids:
                    continue
                merged.append(market)
                seen_market_ids.add(market_id)
        return merged

    def is_temperature_market(self, market: Dict[str, Any]) -> bool:
        title = (market.get("question") or market.get("title") or "").lower()
        description = (market.get("description") or "").lower()
        category = (market.get("category") or "").lower()
        tags = market.get("tags") or market.get("tag") or []
        tags_text = (
            " ".join(str(tag).lower() for tag in tags)
            if isinstance(tags, list)
            else str(tags).lower()
        )
        haystack = f"{title} {description} {category} {tags_text}"
        if "goodweather" in haystack or "mayweather" in haystack:
            return False
        if any(re.search(pattern, haystack) for pattern in TEMPERATURE_PATTERNS):
            return True
        return any(
            keyword in haystack
            for keyword in TEMPERATURE_MARKET_KEYWORDS + WEATHER_KEYWORDS
        )

    def get_markets(
        self,
        *,
        limit: int = 500,
        max_pages: int = 1,
        active_only: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        url = f"{self.config.gamma_base_url.rstrip('/')}/markets"
        all_markets: List[Dict[str, Any]] = []
        seen_market_ids = set()

        for page in range(max_pages):
            params: Dict[str, Any] = {
                "limit": limit,
                "offset": page * limit,
            }
            if active_only:
                params["active"] = "true"
            if extra_params:
                params.update(extra_params)
                params.setdefault(
                    "offset", page * int(extra_params.get("limit", limit))
                )

            payload = self._request("GET", url, with_auth=False, params=params)
            if isinstance(payload, list):
                markets = payload
            elif isinstance(payload, dict) and isinstance(payload.get("markets"), list):
                markets = payload["markets"]
            elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                markets = payload["data"]
            else:
                markets = []

            if not markets:
                break

            for market in markets:
                market_id = str(market.get("id") or "")
                if not market_id or market_id in seen_market_ids:
                    continue
                all_markets.append(market)
                seen_market_ids.add(market_id)

            page_limit = int(params.get("limit", limit))
            if len(markets) < page_limit:
                break

        return all_markets

    def compute_positions_market_value_usd(self) -> float:
        total = 0.0
        for row in self.get_data_api_positions():
            size = float(row.get("size") or 0)
            if size < 1e-9:
                continue
            current_value = float(row.get("currentValue") or 0)
            cur_price = float(row.get("curPrice") or 0)
            marked = cur_price * size
            if current_value > 1e-6:
                total += current_value
            elif marked > 1e-6:
                total += marked
        return total

    def get_open_positions(self) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for row in self.get_data_api_positions():
            size = float(row.get("size") or 0)
            if size < 1e-9:
                continue
            if not is_book_live_position(row):
                continue
            condition_id = str(row.get("conditionId") or "")
            slug = str(row.get("slug") or "").strip()
            market_id = ""
            if condition_id:
                resolved = self.resolve_market_id_from_condition(condition_id)
                if resolved:
                    market_id = resolved
            if not market_id and slug:
                resolved_slug = self.resolve_market_id_from_slug(slug)
                if resolved_slug:
                    market_id = resolved_slug
            normalized.append(
                {
                    "market_id": market_id,
                    "marketId": market_id,
                    "condition_id": condition_id,
                    "size": size,
                    "avg_price": float(row.get("avgPrice") or 0),
                    "cur_price": float(row.get("curPrice") or 0),
                    "title": row.get("title") or "",
                    "outcome": row.get("outcome") or "",
                    "cash_pnl": float(row.get("cashPnl") or 0),
                    "current_value": float(row.get("currentValue") or 0),
                }
            )
        if normalized:
            return normalized
        if not self.has_private_auth():
            return []
        endpoints = [
            f"{self.config.clob_base_url.rstrip('/')}/positions",
            f"{self.config.gamma_base_url.rstrip('/')}/positions",
        ]
        for endpoint in endpoints:
            try:
                payload = self._request("GET", endpoint, with_auth=True)
                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict):
                    return payload.get("positions", [])
            except requests.RequestException:
                continue
        return []

    def get_portfolio_balance(
        self, *, force_allowance_refresh: bool = False
    ) -> Dict[str, Any]:
        positions_value = self.compute_positions_market_value_usd()
        if not self.has_private_auth():
            return {
                "cash": 0.0,
                "positions_market_value": positions_value,
                "total_value": positions_value,
                "auth_warning": "missing_api_secret_or_passphrase",
            }
        now = time.monotonic()
        allowance_fresh = (
            force_allowance_refresh
            or (now - self._balance_allowance_monotonic)
            >= CLOB_BALANCE_ALLOWANCE_CACHE_TTL_SEC
        )
        if not allowance_fresh and self._balance_allowance_extras:
            cash_usd = self._balance_allowance_cash_usd
            total = cash_usd + positions_value
            out = dict(self._balance_allowance_extras)
            out["cash"] = cash_usd
            out["positions_market_value"] = positions_value
            out["total_value"] = total
            out["balance_allowance_cached"] = True
            return out
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

            clob = self.make_clob_client()
            payload = clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            if isinstance(payload, dict):
                raw_balance = int(payload.get("balance") or 0)
                cash_usd = raw_balance / 1_000_000.0
                self._balance_allowance_monotonic = time.monotonic()
                self._balance_allowance_cash_usd = cash_usd
                self._balance_allowance_extras = {
                    "raw_balance": str(raw_balance),
                    "allowances": payload.get("allowances"),
                    "source": "py_clob_client",
                }
                total = cash_usd + positions_value
                return {
                    "cash": cash_usd,
                    "positions_market_value": positions_value,
                    "total_value": total,
                    "raw_balance": str(raw_balance),
                    "allowances": payload.get("allowances"),
                    "source": "py_clob_client",
                }
        except Exception:
            pass
        endpoints = [
            f"{self.config.clob_base_url.rstrip('/')}/balance",
            f"{self.config.gamma_base_url.rstrip('/')}/balance",
        ]
        for endpoint in endpoints:
            try:
                payload = self._request("GET", endpoint, with_auth=True)
                if isinstance(payload, dict):
                    raw_cash = (
                        payload.get("cash")
                        or payload.get("balance")
                        or payload.get("available")
                        or 0
                    )
                    try:
                        cash_usd = float(raw_cash)
                    except (TypeError, ValueError):
                        cash_usd = 0.0
                    if cash_usd > 1e6:
                        cash_usd /= 1_000_000.0
                    merged = dict(payload)
                    merged["cash"] = cash_usd
                    merged["positions_market_value"] = positions_value
                    merged["total_value"] = cash_usd + positions_value
                    return merged
            except requests.RequestException:
                continue
        return {
            "cash": 0.0,
            "positions_market_value": positions_value,
            "total_value": positions_value,
        }

    def has_private_auth(self) -> bool:
        return bool(
            self.config.api_key
            and self.config.api_secret
            and self.config.api_passphrase
        )

    def get_yes_shares_on_exchange_for_market_id(self, market_id: str) -> float:
        mid = str(market_id).strip()
        if not mid:
            return 0.0
        total = 0.0
        for row in self.get_data_api_positions():
            size = float(row.get("size") or 0)
            if size < 1e-12:
                continue
            outcome = str(row.get("outcome") or "").strip().lower()
            if outcome in ("no", "n"):
                continue
            row_mid = ""
            cid = str(row.get("conditionId") or "")
            if cid:
                resolved = self.resolve_market_id_from_condition(cid)
                if resolved:
                    row_mid = resolved
            if not row_mid:
                slug = str(row.get("slug") or "").strip()
                if slug:
                    rs = self.resolve_market_id_from_slug(slug)
                    if rs:
                        row_mid = rs
            if row_mid == mid or (cid and condition_ids_equivalent(mid, cid)):
                total += size
        return total

    def flush_pending_limit_sells(self, active_trades: Dict[str, Any]) -> None:
        from py_clob_client.exceptions import PolyApiException

        if not active_trades:
            return
        clob: Any = None
        for market_id, trade in list(active_trades.items()):
            oid = trade.get("pending_limit_sell_order_id")
            if not oid:
                continue
            if clob is None:
                clob = self.make_clob_client()
            try:
                info = clob_retry_transient(lambda: clob.get_order(oid))
            except PolyApiException as err:
                if err.status_code == 404:
                    trade.pop("pending_limit_sell_order_id", None)
                    trade.pop("pending_limit_sell_price", None)
                continue
            except Exception:
                continue
            if not isinstance(info, dict):
                continue
            if order_fully_filled(info):
                active_trades.pop(market_id, None)
                self.invalidate_data_positions_cache()
                self.invalidate_balance_allowance_cache()
                continue
            if order_terminal_cancelled(info):
                trade.pop("pending_limit_sell_order_id", None)
                trade.pop("pending_limit_sell_price", None)

    def get_clob_yes_price(self, market: Dict[str, Any]) -> float:
        """Get the real best-ask price for a YES token.
        Uses Gamma bestAsk first (no API call), CLOB orderbook as fallback."""
        # Gamma already provides bestAsk on the market object
        best_ask = market.get("bestAsk")
        if best_ask is not None:
            try:
                val = float(best_ask)
                if 0 < val <= 1:
                    return val
            except (TypeError, ValueError):
                pass
        # fallback: hit CLOB orderbook directly
        try:
            token_id = yes_token_id_from_market(market)
        except (ValueError, KeyError):
            return 0.0
        clob = self.make_clob_client()
        try:
            book = clob.get_order_book(token_id)
            if book and getattr(book, "asks", None):
                return float(book.asks[0].price)
        except Exception:
            pass
        mid = midpoint_price_from_clob(clob, token_id)
        if mid > 0:
            return mid
        return 0.0

    def get_clob_min_order_size_yes(self, market: Dict[str, Any]) -> Optional[float]:
        try:
            token_id = yes_token_id_from_market(market)
            book = self.make_clob_client().get_order_book(token_id)
            if book and getattr(book, "min_order_size", None) is not None:
                return float(book.min_order_size)
        except Exception:
            pass
        return None

    def topup_yes_if_needed_for_min_sell(
        self,
        market: Dict[str, Any],
        exchange_yes: float,
        position_key: str,
    ) -> float:
        """
        CLOB often requires min ~5 shares to sell. If we hold slightly less,
        market-buy a small YES slice (capped in USD) so the following sell can submit.
        """
        from config import constants as C

        min_sz = self.get_clob_min_order_size_yes(market)
        if min_sz is None or exchange_yes + 1e-9 >= min_sz:
            return exchange_yes
        extra = min_sz - exchange_yes + C.CLOB_SELL_TOPUP_BUFFER_SHARES
        px = self.get_clob_yes_price(market)
        if px <= 0 or px >= 1.0:
            return exchange_yes
        usd = extra * px * C.CLOB_SELL_TOPUP_SLIPPAGE_MULT
        usd = min(max(usd, C.CLOB_SELL_TOPUP_MIN_USD), C.CLOB_SELL_TOPUP_MAX_USD)
        try:
            self.place_market_buy_yes(market, usd)
        except Exception:
            return exchange_yes
        return self.get_yes_shares_on_exchange_for_market_id(position_key)

    def place_market_buy_yes(
        self, market: Dict[str, Any], usd_amount: float
    ) -> Dict[str, Any]:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.exceptions import PolyApiException

        if usd_amount <= 0:
            raise ValueError("usd_amount must be positive")
        token_id = yes_token_id_from_market(market)
        clob = self.make_clob_client()
        errors: List[str] = []
        for order_type in (OrderType.FAK, OrderType.FOK):
            try:

                def make_buy_exec(ot: Any) -> Any:
                    def exec_buy() -> Any:
                        mo = MarketOrderArgs(
                            token_id=token_id,
                            amount=usd_amount,
                            side=CLOB_ORDER_SIDE_BUY,
                            order_type=ot,
                        )
                        signed = clob.create_market_order(mo)
                        return clob.post_order(signed, orderType=ot)

                    return exec_buy

                posted = clob_retry_transient(make_buy_exec(order_type))
                out = posted if isinstance(posted, dict) else {"raw": posted}
                self.invalidate_data_positions_cache()
                self.invalidate_balance_allowance_cache()
                return out
            except Exception as err:
                errors.append(f"{order_type}:{err!r}")
                continue
        raise PolyApiException(error_msg="buy failed: " + " | ".join(errors))

    def place_market_sell_yes(
        self,
        market: Dict[str, Any],
        share_size: float,
        *,
        reference_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
        from py_clob_client.exceptions import PolyApiException

        ref = float(reference_price or 0) or 0.0
        if share_size <= 0:
            raise ValueError("share_size must be positive")
        token_id = yes_token_id_from_market(market)
        clob = self.make_clob_client()
        errors: List[str] = []
        book = None
        min_sz: Optional[float] = None
        try:
            book = clob.get_order_book(token_id)
            if book and book.min_order_size:
                min_sz = float(book.min_order_size)
        except Exception as err:
            errors.append(f"orderbook_prefetch:{err!r}")

        if min_sz is not None and share_size + 1e-12 < min_sz:
            summary = format_sell_attempt_summary(
                market,
                share_size,
                ref,
                min_sz,
                None,
                "aborted before submit — size below exchange minimum",
            )
            return {
                "sell_execution": SELL_EXECUTION_SKIPPED,
                "skip_reason": "below_min_order_size",
                "min_order_size": min_sz,
                "attempted_shares": share_size,
                "reference_price": ref,
                "sell_attempt_summary": summary,
            }

        def post_market_sell(mo: Any, post_ot: Any) -> Any:
            def inner() -> Any:
                signed = clob.create_market_order(mo)
                return clob.post_order(signed, orderType=post_ot)

            return clob_retry_transient(inner)

        for order_type in (OrderType.FAK, OrderType.FOK):
            try:
                mo = MarketOrderArgs(
                    token_id=token_id,
                    amount=share_size,
                    side=CLOB_ORDER_SIDE_SELL,
                    order_type=order_type,
                )
                posted = post_market_sell(mo, order_type)
                out = posted if isinstance(posted, dict) else {"raw": posted}
                out["sell_execution"] = SELL_EXECUTION_MARKET
                out["sell_attempt_summary"] = format_sell_attempt_summary(
                    market, share_size, ref, min_sz, None, "filled via market order"
                )
                self.invalidate_data_positions_cache()
                self.invalidate_balance_allowance_cache()
                return out
            except Exception as err:
                errors.append(f"auto_{order_type}:{err!r}")
                continue

        if book is None:
            try:
                book = clob.get_order_book(token_id)
                if book and book.min_order_size and min_sz is None:
                    min_sz = float(book.min_order_size)
            except Exception as err:
                errors.append(f"orderbook:{err!r}")

        try:
            tick_f = float(clob.get_tick_size(token_id))
        except (TypeError, ValueError):
            tick_f = 0.01

        raw_limit = clob_sell_price_hint(clob, token_id, book, ref)
        if raw_limit <= 0:
            raw_limit = ref if ref > 0 else tick_f
        limit_px = align_price_to_tick_sell(raw_limit, tick_f)
        if limit_px <= 0:
            limit_px = align_price_to_tick_sell(max(tick_f, ref or tick_f), tick_f)

        if min_sz is not None and share_size + 1e-12 < min_sz:
            summary = format_sell_attempt_summary(
                market,
                share_size,
                ref,
                min_sz,
                limit_px,
                "second min check after book refresh",
            )
            return {
                "sell_execution": SELL_EXECUTION_SKIPPED,
                "skip_reason": "below_min_order_size",
                "min_order_size": min_sz,
                "attempted_shares": share_size,
                "reference_price": ref,
                "sell_attempt_summary": summary,
            }

        try:

            def exec_limit() -> Any:
                oa = OrderArgs(
                    token_id=token_id,
                    price=limit_px,
                    size=share_size,
                    side=CLOB_ORDER_SIDE_SELL,
                )
                signed = clob.create_order(oa)
                return clob.post_order(signed, orderType=OrderType.GTC)

            posted = clob_retry_transient(exec_limit)
            out = posted if isinstance(posted, dict) else {"raw": posted}
            out["sell_execution"] = SELL_EXECUTION_LIMIT_GTC
            out["limit_price"] = limit_px
            out["sell_attempt_summary"] = format_sell_attempt_summary(
                market,
                share_size,
                ref,
                min_sz,
                limit_px,
                "posted as GTC limit (no immediate book match for market order)",
            )
            self.invalidate_data_positions_cache()
            self.invalidate_balance_allowance_cache()
            return out
        except Exception as err:
            errors.append(f"limit_gtc:{err!r}")

        summary = format_sell_attempt_summary(
            market, share_size, ref, min_sz, limit_px, ""
        )
        raise PolyApiException(
            error_msg=f"sell failed\n{summary}\nerrors={' | '.join(errors)}"
        )

    def get_data_api_live_volume(self, event_id: str) -> List[Dict[str, Any]]:
        eid = str(event_id or "").strip()
        if not eid.isdigit():
            return []
        url = f"{DATA_API_BASE_URL.rstrip('/')}/live-volume"
        try:
            response = self.session.get(
                url, params={"id": int(eid)}, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def get_data_api_open_interest_map(
        self, condition_ids: List[str]
    ) -> Dict[str, float]:
        ids = [
            str(c).strip()
            for c in condition_ids
            if c and str(c).strip().lower().startswith("0x")
        ]
        if not ids:
            return {}
        url = f"{DATA_API_BASE_URL.rstrip('/')}/oi"
        out: Dict[str, float] = {}
        chunk = 20
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            joined = ",".join(part)
            try:
                response = self.session.get(
                    url,
                    params={"market": joined},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                rows = response.json()
            except (requests.RequestException, ValueError, TypeError):
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mk = str(row.get("market") or "").strip()
                try:
                    val = float(row.get("value") or 0)
                except (TypeError, ValueError):
                    continue
                if mk:
                    out[mk] = val
        return out

    def post_clob_order_books(self, token_ids: List[str]) -> List[Dict[str, Any]]:
        if not token_ids:
            return []
        base = f"{self.config.clob_base_url.rstrip('/')}/books"
        merged: List[Dict[str, Any]] = []
        chunk = 40
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        for i in range(0, len(token_ids), chunk):
            part = token_ids[i : i + chunk]
            body = [{"token_id": str(t).strip()} for t in part if str(t).strip()]
            if not body:
                continue
            try:
                response = self.session.post(
                    base,
                    json=body,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError, TypeError):
                continue
            if isinstance(data, list):
                merged.extend([x for x in data if isinstance(x, dict)])
        return merged

    def claim_market(self, market_id: str) -> Dict[str, Any]:
        claim_payload = {
            "market_id": market_id,
            "wallet_address": self.trading_address or self.wallet_address,
        }
        endpoint = f"{self.config.clob_base_url.rstrip('/')}/claim"
        return self._request("POST", endpoint, json_body=claim_payload, with_auth=True)


def gamma_event_ids_for_market(market: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ev = market.get("events")
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, dict):
                rid = str(item.get("id") or "").strip()
                if rid and rid not in out:
                    out.append(rid)
    for key in ("eventId", "event_id"):
        rid = str(market.get(key) or "").strip()
        if rid and rid not in out:
            out.append(rid)
    return out
