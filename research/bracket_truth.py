"""Map observed daily max (°C) to whether a ParsedTemp market's YES would win."""

from forecast.parse_title import (
    BracketKind,
    ParsedTempMarket,
    _RE_ABOVE_F,
    _RE_BELOW_F,
    _RE_RANGE_F,
)


def _truth_fahrenheit(truth_max_c: float) -> float:
    return truth_max_c * (9.0 / 5.0) + 32.0


def yes_bucket_matches_truth(p: ParsedTempMarket, truth_max_c: float) -> bool:
    """
    Approximate Polymarket resolution using integer °C / °F buckets.

    params:
    - p: parsed question bracket.
    - truth_max_c: observed daily max in °C.

    returns:
    - True if YES would resolve.
    """
    raw = p.raw_title or ""
    mr = _RE_RANGE_F.search(raw)
    if mr:
        lo_f = float(mr.group(1))
        hi_f = float(mr.group(2))
        tf = _truth_fahrenheit(truth_max_c)
        hi_int = int(round(tf))
        return lo_f <= hi_int <= hi_f

    mb_f = _RE_BELOW_F.search(raw)
    if mb_f:
        limit_f = float(mb_f.group(1))
        tf = _truth_fahrenheit(truth_max_c)
        return int(round(tf)) <= int(round(limit_f))

    ma_f = _RE_ABOVE_F.search(raw)
    if ma_f:
        limit_f = float(ma_f.group(1))
        tf = _truth_fahrenheit(truth_max_c)
        return int(round(tf)) >= int(round(limit_f))

    if p.bracket == BracketKind.AT_MOST:
        return truth_max_c <= p.threshold_c + 0.25
    if p.bracket == BracketKind.AT_LEAST:
        return truth_max_c >= p.threshold_c - 0.25
    ti = int(round(truth_max_c))
    bi = int(round(p.threshold_c))
    return ti == bi
