"""Read market_outcomes.jsonl rows with backward-compatible keys."""

from typing import Any, Dict, Optional


def outcome_gamma_yes_p(row: Dict[str, Any]) -> Optional[float]:
    for k in ("gamma_yes_p", "yes_price"):
        v = row.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def outcome_question_snip(row: Dict[str, Any], limit: int = 180) -> str:
    q = str(row.get("q") or row.get("question") or "")
    return q[:limit]


def outcome_fits_truth(row: Dict[str, Any]) -> Optional[bool]:
    if "fits" in row:
        v = row.get("fits")
        if isinstance(v, bool):
            return v
    v = row.get("yes_fits_truth")
    if isinstance(v, bool):
        return v
    return None


def outcome_closed(row: Dict[str, Any]) -> bool:
    if "closed" in row:
        return bool(row.get("closed"))
    return bool(row.get("market_closed"))
