"""regime_filter.py — Ultra God Level Engine: Module 1
Detects whether the market is trending or ranging.
Uses EMA relationship + TV TA strength score + RSI slope proxy.

Returns
-------
{
    "regime":  "TREND_UP" | "TREND_DOWN" | "RANGE" | "UNKNOWN",
    "quality": float 0.0-1.0,
    "score":   int   0-10,     # confidence_engine weight: 10
    "reason":  str,
}
"""
from __future__ import annotations

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 25.0

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


def _slope(rsi_now: float, rsi_prev: float = 50.0) -> float:
    """Proxy RSI slope from current vs neutral baseline."""
    return rsi_now - rsi_prev


def detect_regime(pair: str) -> dict:
    """Detect market regime for a pair.

    Trend detection rules
    ─────────────────────
    • EMA20 > EMA50 and RSI > 55 → TREND_UP
    • EMA20 < EMA50 and RSI < 45 → TREND_DOWN
    • EMA20 ≈ EMA50 (within 0.05 % of price) and RSI 45-55 → RANGE
    • Strength ≥ 0.7 → high quality trend
    • Strength < 0.3 → range or choppy
    """
    import time
    cached = _CACHE.get(pair)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1h  = _tv(pair, "1h")
    d4h  = _tv(pair, "4h")
    d15m = _tv(pair, "15m")

    if not d1h.get("ok") and not d4h.get("ok"):
        result = {"regime": "UNKNOWN", "quality": 0.0, "score": 0,
                  "reason": "no TV data"}
        _CACHE[pair] = (time.time(), result)
        return result

    # Use 1h as primary TF for regime
    d = d1h if d1h.get("ok") else d4h
    ema20  = float(d.get("ema20", 0) or 0)
    ema50  = float(d.get("ema50", 0) or 0)
    close  = float(d.get("close", 0) or 0)
    rsi    = float(d.get("rsi",  50) or 50)
    strength = float(d.get("strength", 0.0) or 0.0)

    if not close or not ema20 or not ema50:
        result = {"regime": "UNKNOWN", "quality": 0.0, "score": 0,
                  "reason": "missing EMA/close data"}
        _CACHE[pair] = (time.time(), result)
        return result

    ema_gap_pct = abs(ema20 - ema50) / close if close else 0

    # Trend vs range classification
    if ema20 > ema50 * 1.0002 and rsi >= 53:
        regime  = "TREND_UP"
        quality = min(1.0, strength + 0.2 + (rsi - 53) / 60)
    elif ema20 < ema50 * 0.9998 and rsi <= 47:
        regime  = "TREND_DOWN"
        quality = min(1.0, strength + 0.2 + (47 - rsi) / 60)
    elif ema_gap_pct < 0.0005 and 44 <= rsi <= 56:
        regime  = "RANGE"
        quality = max(0.1, 1.0 - strength)
    else:
        regime  = "RANGE"
        quality = 0.4

    # Cross-check with 15m for consistency
    if d15m.get("ok"):
        b15 = d15m.get("bias", "NEUTRAL")
        if regime == "TREND_UP"   and b15 == "SELL": quality *= 0.7
        if regime == "TREND_DOWN" and b15 == "BUY":  quality *= 0.7

    # Score 0-10 based on regime quality
    if regime in ("TREND_UP", "TREND_DOWN"):
        score = int(min(10, 6 + quality * 4))
    else:
        score = int(min(5, quality * 5))

    reason = (f"1h EMA20={'above' if ema20 > ema50 else 'below'} EMA50 | "
              f"RSI={rsi:.1f} | strength={strength:.2f} | gap={ema_gap_pct*100:.3f}%")

    result = {
        "regime":  regime,
        "quality": round(quality, 3),
        "score":   score,
        "reason":  reason,
    }
    _CACHE[pair] = (time.time(), result)
    return result
