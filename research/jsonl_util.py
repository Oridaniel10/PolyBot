"""JSON / JSONL helpers under data/research/."""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from config import constants as C


def ensure_research_dir() -> Path:
    C.RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    return C.RESEARCH_DIR


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )


def rewrite_jsonl_excluding(path: Path, exclude_pred) -> int:
    """
    rewrite path keeping rows where exclude_pred(row) is false.
    returns number of rows removed.
    """
    if not path.is_file():
        return 0
    kept: List[Dict[str, Any]] = []
    removed = 0
    for row in iter_jsonl(path):
        if exclude_pred(row):
            removed += 1
        else:
            kept.append(row)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in kept),
        encoding="utf-8",
    )
    return removed


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True) + "\n")


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def load_idempotent_keys(
    path: Path, key_fn=lambda r: r.get("idempotency_key")
) -> Set[str]:
    out: Set[str] = set()
    for row in iter_jsonl(path):
        k = key_fn(row)
        if isinstance(k, str) and k:
            out.add(k)
    return out


def load_truth_city_dates_with_success(path: Path) -> Set[Tuple[str, str]]:
    """
    (city_key, event_date) pairs with numeric truth_max_c (any source).
    legacy rows may use idempotency_key truth|city|date instead.
    """
    out: Set[Tuple[str, str]] = set()
    for row in iter_jsonl(path):
        tv = row.get("truth_max_c")
        if tv is None:
            continue
        try:
            float(tv)
        except (TypeError, ValueError):
            continue
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        if ck and ds:
            out.add((ck, ds))
            continue
        ik = row.get("idempotency_key")
        if isinstance(ik, str) and ik.startswith("truth|"):
            parts = ik.split("|")
            if len(parts) >= 3:
                out.add((parts[1].lower(), parts[2]))
    return out


def load_forecast_idempotency_success(path: Path) -> Set[str]:
    """idempotency_key with numeric temp_max_c (failed/null rows are refetched)."""
    out: Set[str] = set()
    for row in iter_jsonl(path):
        k = row.get("idempotency_key")
        if not isinstance(k, str) or not k:
            continue
        v = row.get("temp_max_c")
        if v is None:
            continue
        try:
            float(v)
        except (TypeError, ValueError):
            continue
        out.add(k)
    return out
