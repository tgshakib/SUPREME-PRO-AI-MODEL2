"""momentum_gate.py — Ultra God Level Engine: Module 4
RSI + TV momentum confirmation gate.
Both 1m and 5m momentum must agree before the signal passes.

Scoring (confidence_engine weight: 15)
• RSI confirms + both TF buy/sell vote agree → 15
• RSI confirms, 1 TF agrees                 → 10
• RSI neutral, TV votes dominant            → 7
• Momentum against direction or neutral     → 0

Returns
-------
{
    "pass":      bool,
    "direction": "BUY" | "SELL" | None,
    "rsi_ok":    bool,
    "score":     int 0-15,
    "reason":    str,
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


def check_momentum(pair: str, direction: str | None = None) -> dict:
    """Check RSI + momentum alignment for given direction.

    RSI rules
    ─────────
    BUY  signal: 1m RSI must be 40-72 (not oversold basement, not overbought)
    SELL signal: 1m RSI must be 28-60 (not overbought peak, not oversold)

    TV vote rules
    ─────────────
    BUY:  buy_v > sell_v on both 1m and 5m
    SELL: sell_v > buy_v on both 1m and 5m
    """
    import time
    cached = _CACHE.get(f"{pair}:{direction}")
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m = _tv(pair, "1m")
    d5m = _tv(pair, "5m")

    rsi1 = float(d1m.get("rsi", 50) or 50)
    rsi5 = float(d5m.get("rsi", 50) or 50)
    bv1  = int(d1m.get("buy_v",  0) or 0)
    sv1  = int(d1m.get("sell_v", 0) or 0)
    bv5  = int(d5m.get("buy_v",  0) or 0)
    sv5  = int(d5m.get("sell_v", 0) or 0)

    if direction == "BUY":
        rsi_ok  = 40 <= rsi1 <= 72 and rsi5 <= 70
        vote_ok1 = bv1 >= sv1
        vote_ok5 = bv5 >= sv5
        vote_dom = (bv1 + bv5) > (sv1 + sv5)
    elif direction == "SELL":
        rsi_ok  = 28 <= rsi1 <= 60 and rsi5 >= 30
        vote_ok1 = sv1 >= bv1
        vote_ok5 = sv5 >= bv5
        vote_dom = (sv1 + sv5) > (bv1 + bv5)
    else:
        rsi_ok  = False
        vote_ok1 = False
        vote_ok5 = False
        vote_dom = False

    both_votes = vote_ok1 and vote_ok5

    if rsi_ok and both_votes:
        passed = True
        score  = 15
    elif rsi_ok and (vote_ok1 or vote_ok5):
        passed = True
        score  = 10
    elif vote_dom and not rsi_ok:
        passed = True
        score  = 7
    else:
        passed = False
        score  = 0

    reason = (f"RSI 1m={rsi1:.1f} 5m={rsi5:.1f} | "
              f"votes 1m buy={bv1} sell={sv1} | "
              f"5m buy={bv5} sell={sv5} | "
              f"RSI_ok={rsi_ok} both_votes={both_votes}")

    result = {
        "pass":      passed,
        "direction": direction,
        "rsi_ok":    rsi_ok,
        "score":     score,
        "reason":    reason,
    }
    _CACHE[f"{pair}:{direction}"] = (time.time(), result)
    return result
