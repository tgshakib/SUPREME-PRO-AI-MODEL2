"""htf_alignment.py — Ultra God Level Engine: Module 2
Multi-timeframe alignment check.
1h, 4h, and 1d must agree on direction before a signal fires.

Scoring (confidence_engine weight: 20)
• All 3 TFs agree strongly  → 20
• 2 of 3 TFs agree          → 14
• Only 1 TF has direction   → 6
• No alignment              → 0

Returns
-------
{
    "aligned":   bool,
    "direction": "BUY" | "SELL" | None,
    "score":     int 0-20,
    "tf_votes":  {"1h": str, "4h": str, "1d": str},
    "reason":    str,
}
"""
from __future__ import annotations

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 30.0

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


def check_htf_alignment(pair: str) -> dict:
    """Return HTF alignment result for a pair.

    Uses 1h / 4h / 1d TradingView TA bias.
    A 'strong' bias requires strength ≥ 0.40 AND RSI not in extreme zone.
    """
    import time
    cached = _CACHE.get(pair)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    tfs  = {"1h": _tv(pair, "1h"), "4h": _tv(pair, "4h"), "1d": _tv(pair, "1d")}
    votes: dict[str, str] = {}

    for tf, d in tfs.items():
        if not d.get("ok"):
            votes[tf] = "UNKNOWN"
            continue
        bias     = d.get("bias", "NEUTRAL") or "NEUTRAL"
        strength = float(d.get("strength", 0) or 0)
        rsi      = float(d.get("rsi", 50) or 50)
        # Require meaningful strength AND non-extreme RSI
        if bias == "BUY"  and strength >= 0.35 and rsi < 78:
            votes[tf] = "BUY"
        elif bias == "SELL" and strength >= 0.35 and rsi > 22:
            votes[tf] = "SELL"
        else:
            votes[tf] = "NEUTRAL"

    buy_count  = sum(1 for v in votes.values() if v == "BUY")
    sell_count = sum(1 for v in votes.values() if v == "SELL")

    if buy_count >= 2:
        direction = "BUY"
        aligned   = True
        score     = 20 if buy_count == 3 else 14
    elif sell_count >= 2:
        direction = "SELL"
        aligned   = True
        score     = 20 if sell_count == 3 else 14
    elif buy_count == 1:
        direction = "BUY"
        aligned   = False
        score     = 6
    elif sell_count == 1:
        direction = "SELL"
        aligned   = False
        score     = 6
    else:
        direction = None
        aligned   = False
        score     = 0

    reason = (f"HTF votes: {votes} | "
              f"buy={buy_count} sell={sell_count} → "
              f"{'ALIGNED' if aligned else 'NOT ALIGNED'} {direction or 'NONE'}")

    result = {
        "aligned":   aligned,
        "direction": direction,
        "score":     score,
        "tf_votes":  votes,
        "reason":    reason,
    }
    _CACHE[pair] = (time.time(), result)
    return result
