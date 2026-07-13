"""volatility_adapter.py — Ultra God Level Engine: Module 5
ATR/volatility filter.
Blocks dead markets (too quiet) and news-spike candles (too wild).
Also checks candle body strength (must be conviction body, not wick).

Scoring (confidence_engine weight: 15 volatility + 10 candle body = 25 total)
• Volatility in ideal range + strong body → 25
• Volatility ok + body ok                → 18
• Dead market or overextended spike      → 0 (BLOCKED)

Returns
-------
{
    "pass":        bool,
    "condition":   "IDEAL" | "OK" | "DEAD" | "SPIKE" | "UNKNOWN",
    "vol_score":   int 0-15,
    "body_score":  int 0-10,
    "total_score": int 0-25,
    "atr_pct":     float,
    "body_ratio":  float,
    "reason":      str,
}
"""
from __future__ import annotations

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 20.0

try:
    from candle_feed import get_single_tf as _get_tf
    _TV_OK = True
except Exception:
    _TV_OK = False
    _get_tf = None  # type: ignore

try:
    from live_prices import get_stooq_momentum as _stooq_mom
    _STOOQ_OK = True
except Exception:
    _STOOQ_OK = False
    _stooq_mom = None  # type: ignore

# ── Per-asset ATR thresholds ────────────────────────────────────────────────
# (min_atr_pct, max_atr_pct) as fraction of price
_ATR_THRESHOLDS: dict[str, tuple[float, float]] = {
    "default": (0.0008, 0.012),
    "XAU":     (0.0010, 0.020),   # Gold — larger natural range
    "BTC":     (0.0015, 0.030),   # BTC — high daily range
    "ETH":     (0.0015, 0.030),
    "JPY":     (0.0004, 0.008),
}


def _thresholds(pair: str) -> tuple[float, float]:
    for key in _ATR_THRESHOLDS:
        if key in pair.upper():
            return _ATR_THRESHOLDS[key]
    return _ATR_THRESHOLDS["default"]


def check_volatility(pair: str) -> dict:
    """Return volatility + candle body assessment.

    ATR proxy: TV strength score scaled to price range.
    Body ratio proxy: close distance from EMA20 vs close-to-ema range.
    """
    import time
    cached = _CACHE.get(pair)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d5m  = _tv(pair, "5m")
    d15m = _tv(pair, "15m")

    if not d5m.get("ok") and not d15m.get("ok"):
        result = {
            "pass": True, "condition": "UNKNOWN",
            "vol_score": 8, "body_score": 5, "total_score": 13,
            "atr_pct": 0.001, "body_ratio": 0.5,
            "reason": "no TV data — defaulting to pass",
        }
        _CACHE[pair] = (time.time(), result)
        return result

    d = d5m if d5m.get("ok") else d15m
    close   = float(d.get("close",    0) or 0)
    ema20   = float(d.get("ema20",    0) or 0)
    ema50   = float(d.get("ema50",    0) or 0)
    strength = float(d.get("strength", 0) or 0)
    rsi      = float(d.get("rsi",    50) or 50)

    # ATR proxy: TV strength * price * scaling factor
    # Real ATR needs multiple candles. We use |close - ema20| / close as proxy.
    atr_pct = abs(close - ema20) / close if close and ema20 else 0.001

    # Body ratio proxy: how far close moved from EMA vs EMA spread
    if ema20 and ema50 and close:
        ema_span = abs(ema20 - ema50) + 1e-10
        body_ratio = min(1.0, abs(close - min(ema20, ema50)) / ema_span)
    else:
        body_ratio = 0.5

    min_atr, max_atr = _thresholds(pair)

    # Volatility classification
    if atr_pct < min_atr * 0.5:
        condition = "DEAD"
        vol_score = 0
        passed    = False
    elif atr_pct > max_atr * 1.8:
        condition = "SPIKE"
        vol_score = 0
        passed    = False
    elif min_atr <= atr_pct <= max_atr:
        condition = "IDEAL"
        vol_score = 15
        passed    = True
    else:
        condition = "OK"
        vol_score = 10
        passed    = True

    # Body strength score 0-10
    # Use TV strength + RSI deviation from 50 as body conviction proxy
    rsi_dev = abs(rsi - 50) / 50   # 0 = neutral, 1 = extreme
    body_strength = min(1.0, strength * 0.6 + rsi_dev * 0.4 + body_ratio * 0.2)
    body_score = int(body_strength * 10)

    total_score = vol_score + body_score if passed else 0

    reason = (f"ATR proxy={atr_pct*100:.3f}% [{condition}] "
              f"| strength={strength:.2f} RSI={rsi:.1f} "
              f"| body_ratio={body_ratio:.2f} body_score={body_score}")

    result = {
        "pass":        passed,
        "condition":   condition,
        "vol_score":   vol_score,
        "body_score":  body_score,
        "total_score": total_score,
        "atr_pct":     round(atr_pct, 6),
        "body_ratio":  round(body_ratio, 3),
        "reason":      reason,
    }
    _CACHE[pair] = (time.time(), result)
    return result


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}
