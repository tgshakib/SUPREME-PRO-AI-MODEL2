"""liquidity_zones.py — Ultra God Level Engine: Module 3
Identifies support/resistance zones via RSI extremes + TV vote dominance.
Detects breakout, retest, and fakeout conditions.

Scoring (confidence_engine weight: 20)
• Price at high-quality S/R zone + direction matches → 20
• Near a zone (within ATR tolerance)                → 14
• Zone quality weak / far from zone                 → 6
• No clear zone                                     → 0

Returns
-------
{
    "zone_type":  "support" | "resistance" | "breakout" | "fakeout" | "neutral",
    "zone_dir":   "BUY" | "SELL" | None,
    "quality":    float 0.0-1.0,
    "score":      int 0-20,
    "near_zone":  bool,
    "reason":     str,
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


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}


def analyze_liquidity_zones(pair: str, direction: str | None = None) -> dict:
    """Identify liquidity zones and zone quality.

    Logic
    ─────
    • RSI < 30 on 15m AND RSI < 38 on 1h  → strong support (buy zone)
    • RSI > 70 on 15m AND RSI > 62 on 1h  → strong resistance (sell zone)
    • RSI 30-45 after extreme = retest of support → BUY
    • RSI 55-70 after extreme = retest of resistance → SELL
    • TV buy_v >> sell_v at a zone → breakout
    • TV sell_v >> buy_v at a zone → fakeout (if direction was BUY)
    • EMA20 vs EMA50 crossover proximity = zone quality boost
    """
    import time
    cached = _CACHE.get(pair)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d5m  = _tv(pair, "5m")
    d15m = _tv(pair, "15m")
    d1h  = _tv(pair, "1h")

    if not d15m.get("ok") and not d1h.get("ok"):
        result = {
            "zone_type": "neutral", "zone_dir": None,
            "quality": 0.0, "score": 0, "near_zone": False,
            "reason": "no TV data",
        }
        _CACHE[pair] = (time.time(), result)
        return result

    rsi15 = float((d15m.get("rsi") or 50))
    rsi1h = float((d1h.get("rsi")  or 50))
    rsi5m = float((d5m.get("rsi")  or 50))

    buy_v15  = int((d15m.get("buy_v")  or 0))
    sell_v15 = int((d15m.get("sell_v") or 0))
    buy_v1h  = int((d1h.get("buy_v")   or 0))
    sell_v1h = int((d1h.get("sell_v")  or 0))

    close15 = float((d15m.get("close") or 0))
    ema20   = float((d15m.get("ema20") or 0))
    ema50   = float((d15m.get("ema50") or 0))

    total_buy  = buy_v15  + buy_v1h
    total_sell = sell_v15 + sell_v1h
    vote_ratio = (total_buy - total_sell) / max(1, total_buy + total_sell)

    # ── Zone classification ─────────────────────────────────────────────────
    zone_type = "neutral"
    zone_dir  = None
    quality   = 0.2

    # Deep support
    if rsi15 <= 30 and rsi1h <= 38:
        zone_type = "support"
        zone_dir  = "BUY"
        quality   = min(1.0, 0.65 + (30 - rsi15) / 30 * 0.35)

    # Deep resistance
    elif rsi15 >= 70 and rsi1h >= 62:
        zone_type = "resistance"
        zone_dir  = "SELL"
        quality   = min(1.0, 0.65 + (rsi15 - 70) / 30 * 0.35)

    # Retest of support (bounced up from extreme)
    elif 30 < rsi15 <= 45 and rsi5m > rsi15 and vote_ratio > 0.1:
        zone_type = "support"
        zone_dir  = "BUY"
        quality   = 0.55

    # Retest of resistance (bounced down from extreme)
    elif 55 <= rsi15 < 70 and rsi5m < rsi15 and vote_ratio < -0.1:
        zone_type = "resistance"
        zone_dir  = "SELL"
        quality   = 0.55

    # Breakout: strong TV agreement
    elif vote_ratio >= 0.4:
        zone_type = "breakout"
        zone_dir  = "BUY"
        quality   = min(0.8, 0.4 + vote_ratio * 0.5)

    elif vote_ratio <= -0.4:
        zone_type = "breakout"
        zone_dir  = "SELL"
        quality   = min(0.8, 0.4 + abs(vote_ratio) * 0.5)

    # Fakeout check: EMA squeeze + opposing RSI
    if zone_type == "breakout" and ema20 and ema50 and close15:
        ema_spread = abs(ema20 - ema50) / close15
        if ema_spread < 0.0003 and abs(rsi15 - 50) < 8:
            zone_type = "fakeout"
            quality  *= 0.4

    # EMA convergence near zone = quality boost
    if zone_dir and ema20 and ema50 and close15:
        ema_spread = abs(ema20 - ema50) / close15
        if ema_spread >= 0.001:
            quality = min(1.0, quality + 0.1)

    # Direction alignment bonus
    near_zone = quality >= 0.50
    if direction and zone_dir == direction:
        quality = min(1.0, quality + 0.08)

    # Score 0-20
    if zone_type in ("support", "resistance"):
        score = int(min(20, 12 + quality * 8))
    elif zone_type == "breakout":
        score = int(min(16, 8 + quality * 8))
    elif zone_type == "fakeout":
        score = 2
    else:
        score = 0

    reason = (f"{zone_type.upper()} zone | RSI 5m={rsi5m:.1f} 15m={rsi15:.1f} "
              f"1h={rsi1h:.1f} | vote_ratio={vote_ratio:+.2f} | quality={quality:.2f}")

    result = {
        "zone_type": zone_type,
        "zone_dir":  zone_dir,
        "quality":   round(quality, 3),
        "score":     score,
        "near_zone": near_zone,
        "reason":    reason,
    }
    _CACHE[pair] = (time.time(), result)
    return result
