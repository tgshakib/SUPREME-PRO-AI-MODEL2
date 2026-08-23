"""Higher-timeframe market-structure guardrails for binary and FX signals.

This module is deliberately conservative.  It does not claim to predict
markets; it classifies the available indicator feed as continuation,
reversal-pressure, or insufficient data and lets callers decide whether to
wait.  The feed currently exposes TradingView/yfinance indicators rather than
a broker-native Axi stream, so volume is used only when the source provides it.
"""
from __future__ import annotations

import time

try:
    from candle_feed import get_single_tf
except Exception:
    get_single_tf = None  # type: ignore

_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_TTL = 45.0


def _direction(value: str | None) -> str | None:
    return value if value in ("BUY", "SELL") else None


def analyze_market_structure(
    pair: str,
    direction: str | None = None,
    *,
    market: str = "forex",
) -> dict:
    """Return a conservative HTF structure verdict.

    Binary checks 10m/15m/30m/1h/4h.  FX additionally checks 1d/1W.
    The 10m feed is resampled from 5m bars when a source does not expose a
    native 10m interval.
    """
    wanted = (
        ("10m", "15m", "30m", "1h", "4h")
        if market.lower() == "binary"
        else ("10m", "15m", "30m", "1h", "4h", "1d", "1W")
    )
    key = ((pair or "").upper(), f"{market}:{direction or ''}")
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]

    if get_single_tf is None:
        return _result("UNKNOWN", None, 0, {}, "HTF feed unavailable")

    tfs: dict[str, dict] = {}
    for tf in wanted:
        try:
            tfs[tf] = get_single_tf(pair, tf) or {}
        except Exception:
            tfs[tf] = {}

    known = {
        tf: _direction(data.get("bias"))
        for tf, data in tfs.items()
        if data.get("ok")
    }
    if not known:
        result = _result("UNKNOWN", None, 0, tfs, "No usable HTF candles")
        _CACHE[key] = (time.time(), result)
        return result

    requested = _direction(direction)
    htf = [known.get(tf) for tf in ("1h", "4h", "1d", "1W")]
    htf = [v for v in htf if v]
    short = [known.get(tf) for tf in ("10m", "15m", "30m")]
    short = [v for v in short if v]

    buy = htf.count("BUY")
    sell = htf.count("SELL")
    dominant = "BUY" if buy > sell else "SELL" if sell > buy else None
    htf_agree = max(buy, sell)
    strength_values = [
        float(d.get("strength", 0) or 0)
        for d in tfs.values()
        if d.get("ok")
    ]
    avg_strength = (
        sum(strength_values) / len(strength_values)
        if strength_values else 0.0
    )
    extreme = any(
        (float(d.get("rsi", 50) or 50) >= 78)
        or (float(d.get("rsi", 50) or 50) <= 22)
        for d in tfs.values()
        if d.get("ok")
    )

    # An opposite short TF at an extreme is the common "HTF sweep/reversal"
    # failure mode described by the user.
    reversal_pressure = bool(
        dominant
        and short
        and short[0] != dominant
        and extreme
    )
    if reversal_pressure:
        phase = "REVERSAL_PRESSURE"
    elif dominant and htf_agree >= (3 if market.lower() != "binary" else 2):
        phase = "CONTINUATION"
    else:
        phase = "TRANSITION"

    if requested and dominant and requested != dominant:
        approved = False
        reason = f"HTF direction {dominant} opposes requested {requested}"
    elif reversal_pressure:
        approved = False
        reason = "HTF reversal pressure / sweep risk — wait for confirmation"
    elif dominant is None:
        approved = False
        reason = "Higher timeframes are split"
    else:
        min_agree = 2 if market.lower() == "binary" else 3
        approved = htf_agree >= min_agree and avg_strength >= 0.30
        reason = (
            f"{phase.replace('_', ' ').title()} · "
            f"HTF {dominant} {htf_agree}/{len(htf)} · "
            f"strength {avg_strength:.2f}"
        )

    score = min(
        100,
        int(htf_agree * 18 + avg_strength * 40)
        - (25 if reversal_pressure else 0),
    )
    result = _result(phase, dominant, max(0, score), tfs, reason)
    result["approved"] = approved
    result["reversal_pressure"] = reversal_pressure
    result["avg_strength"] = round(avg_strength, 3)
    _CACHE[key] = (time.time(), result)
    return result


def _result(phase: str, direction: str | None, score: int,
            tfs: dict, reason: str) -> dict:
    return {
        "approved": False,
        "phase": phase,
        "direction": direction,
        "score": score,
        "tfs": tfs,
        "reason": reason,
        "reversal_pressure": phase == "REVERSAL_PRESSURE",
        "avg_strength": 0.0,
    }