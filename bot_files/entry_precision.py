"""entry_precision.py — Ultra God Level Engine: Module 6
Entry quality assessment.
Blocks "late" entries where price has already moved far from the ideal zone.
Penalises chasing a move that's overextended.

Scoring (confidence_engine weight: 10)
• Price at/near zone, not overextended      → 10
• Price slightly extended but still valid   → 6
• Price too far from zone / chasing         → 0 (SKIP)

Returns
-------
{
    "quality":  "PRECISE" | "ACCEPTABLE" | "LATE" | "UNKNOWN",
    "score":    int 0-10,
    "reason":   str,
}
"""
from __future__ import annotations

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 18.0

try:
    from candle_feed import get_single_tf as _get_tf
    _TV_OK = True
except Exception:
    _TV_OK = False
    _get_tf = None  # type: ignore


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}


def assess_entry(pair: str, direction: str | None = None,
                 zone_quality: float = 0.5) -> dict:
    """Assess entry timing and precision.

    Precise entry: RSI not yet overextended in trade direction,
    close is near EMA20 (within 0.15% of price), and TV
    1m strength is not maxed out (room to move).

    Late entry: close is far from EMA20 (>0.30% of price),
    OR RSI already past 70 (BUY) / below 30 (SELL).
    """
    import time
    cache_key = f"{pair}:{direction}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m = _tv(pair, "1m")
    d5m = _tv(pair, "5m")

    if not d1m.get("ok") and not d5m.get("ok"):
        result = {"quality": "UNKNOWN", "score": 5,
                  "reason": "no TV data — defaulting acceptable"}
        _CACHE[cache_key] = (time.time(), result)
        return result

    d = d1m if d1m.get("ok") else d5m
    close    = float(d.get("close",    0) or 0)
    ema20    = float(d.get("ema20",    0) or 0)
    rsi      = float(d.get("rsi",    50) or 50)
    strength = float(d.get("strength", 0) or 0)

    # Distance from EMA20 as % of price
    dist_pct = abs(close - ema20) / close if close and ema20 else 0.001

    # Late-entry RSI check
    if direction == "BUY":
        rsi_overextended = rsi >= 73
        rsi_at_edge      = 65 <= rsi < 73
    elif direction == "SELL":
        rsi_overextended = rsi <= 27
        rsi_at_edge      = 27 < rsi <= 35
    else:
        rsi_overextended = False
        rsi_at_edge      = False

    # Strength maxed = market already moved hard; late entry
    strength_maxed = strength >= 0.88

    if rsi_overextended or (dist_pct > 0.003 and strength_maxed):
        quality = "LATE"
        score   = 0
    elif rsi_at_edge or dist_pct > 0.0020:
        quality = "ACCEPTABLE"
        score   = 6
    else:
        quality = "PRECISE"
        score   = 10

    # Zone quality boost: if liquidity zone confirmed nearby, precision ++
    if quality == "ACCEPTABLE" and zone_quality >= 0.7:
        quality = "PRECISE"
        score   = 10

    reason = (f"dist_EMA20={dist_pct*100:.3f}% | RSI={rsi:.1f} | "
              f"strength={strength:.2f} | {quality}")

    result = {
        "quality": quality,
        "score":   score,
        "reason":  reason,
    }
    _CACHE[cache_key] = (time.time(), result)
    return result
