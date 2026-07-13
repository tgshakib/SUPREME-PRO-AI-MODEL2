"""reversal_engine.py — Reversal Zone Candle Detection Engine
Catches where candles can FLIP direction for 1-candle binary trades.
Works for OTC, LIVE binary, and Forex.

Reversal signals detected (scored 0-100)
─────────────────────────────────────────
1. RSI Extreme Zone     — RSI < 26 → BUY reversal  /  RSI > 74 → SELL reversal
2. RSI Divergence       — RSI opposing TV trend bias (oversold in downtrend, etc.)
3. EMA Bounce Zone      — Price at/near EMA20 or EMA50 → expect bounce
4. TV Vote Flip         — buy_v / sell_v suddenly switched direction vs 5m bias
5. Multi-TF Divergence  — 1m opposing 5m/15m → 1m catching the local reversal
6. Candle Exhaustion    — Very high strength + extreme RSI → exhaustion reversal
7. Heikin Ashi Proxy    — TV bias switching (was SELL, now BUY or NEUTRAL) on 1m
8. Double Zone Stack    — RSI extreme + EMA bounce + vote flip all at same time → elite

Returns
-------
{
    "reversal_dir":   "BUY" | "SELL" | None,
    "zone_quality":   int 0-100,
    "elite":          bool,    # 3+ reversal signals stacking (near-certain flip)
    "signals":        list[str],
    "rsi_1m":         float,
    "zone_type":      str,
}
"""
from __future__ import annotations

import time
from typing import Optional

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 15.0   # short TTL — reversals are time-sensitive

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


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# Individual reversal detectors
# ═══════════════════════════════════════════════════════════════════════════

def _detect_rsi_extreme(rsi1m: float, rsi5m: float) -> tuple[Optional[str], int, str]:
    """RSI extreme zone detection."""
    # Ultra-deep oversold: both TFs below threshold
    if rsi1m <= 22 and rsi5m <= 30:
        score = int(32 + (22 - rsi1m) * 1.5)
        return "BUY", min(40, score), f"Ultra-oversold RSI 1m={rsi1m:.0f}/5m={rsi5m:.0f}"
    if rsi1m <= 26:
        score = int(24 + (26 - rsi1m) * 1.2)
        return "BUY", min(36, score), f"Oversold RSI 1m={rsi1m:.0f}"
    # Ultra-deep overbought
    if rsi1m >= 78 and rsi5m >= 70:
        score = int(32 + (rsi1m - 78) * 1.5)
        return "SELL", min(40, score), f"Ultra-overbought RSI 1m={rsi1m:.0f}/5m={rsi5m:.0f}"
    if rsi1m >= 74:
        score = int(24 + (rsi1m - 74) * 1.2)
        return "SELL", min(36, score), f"Overbought RSI 1m={rsi1m:.0f}"
    return None, 0, ""


def _detect_rsi_divergence(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """RSI divergence: RSI direction opposing TV bias (classic reversal setup)."""
    bias1   = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    bias5   = d5m.get("bias", "NEUTRAL") or "NEUTRAL"
    rsi1    = float(d1m.get("rsi", 50) or 50)
    rsi5    = float(d5m.get("rsi", 50) or 50)
    bv1     = int(d1m.get("buy_v",  0) or 0)
    sv1     = int(d1m.get("sell_v", 0) or 0)
    bv5     = int(d5m.get("buy_v",  0) or 0)
    sv5     = int(d5m.get("sell_v", 0) or 0)

    # RSI oversold but TV still shows SELL bias → hidden BUY divergence
    if bias5 == "SELL" and rsi1 <= 35 and bv1 > sv1:
        depth = 35 - rsi1
        return "BUY", int(20 + depth * 0.8), f"RSI-BUY divergence: 5m SELL bias but 1m RSI={rsi1:.0f} oversold"

    # RSI overbought but TV still shows BUY bias → hidden SELL divergence
    if bias5 == "BUY" and rsi1 >= 65 and sv1 > bv1:
        depth = rsi1 - 65
        return "SELL", int(20 + depth * 0.8), f"RSI-SELL divergence: 5m BUY bias but 1m RSI={rsi1:.0f} overbought"

    # Moderate divergence: RSI moving opposite to 5m trend
    if bias5 == "SELL" and rsi1 <= 42 and rsi1 < rsi5:
        return "BUY", 14, f"Moderate BUY divergence RSI 1m={rsi1:.0f}<5m={rsi5:.0f}"
    if bias5 == "BUY" and rsi1 >= 58 and rsi1 > rsi5:
        return "SELL", 14, f"Moderate SELL divergence RSI 1m={rsi1:.0f}>5m={rsi5:.0f}"

    return None, 0, ""


def _detect_ema_bounce(d1m: dict) -> tuple[Optional[str], int, str]:
    """Price at EMA20 or EMA50 — expect bounce (reversal at dynamic support/resistance)."""
    close = float(d1m.get("close",  0) or 0)
    ema20 = float(d1m.get("ema20",  0) or 0)
    ema50 = float(d1m.get("ema50",  0) or 0)
    rsi   = float(d1m.get("rsi",   50) or 50)
    bias  = d1m.get("bias", "NEUTRAL") or "NEUTRAL"

    if not close or not ema20:
        return None, 0, ""

    dist20 = abs(close - ema20) / close
    dist50 = abs(close - ema50) / close if ema50 else 1.0

    # Price bouncing off EMA20 from below (BUY reversal at support)
    if dist20 <= 0.0012 and close < ema20 and rsi <= 52:
        score = int(18 + max(0, 52 - rsi) * 0.4)
        return "BUY", min(28, score), f"EMA20 support bounce dist={dist20*100:.2f}%"

    # Price rejecting off EMA20 from above (SELL reversal at resistance)
    if dist20 <= 0.0012 and close > ema20 and rsi >= 48:
        score = int(18 + max(0, rsi - 48) * 0.4)
        return "SELL", min(28, score), f"EMA20 resistance reject dist={dist20*100:.2f}%"

    # EMA50 bounce (stronger support/resistance)
    if dist50 <= 0.0018 and ema50 > 0:
        if close < ema50 and rsi <= 55:
            return "BUY", 22, f"EMA50 support bounce dist={dist50*100:.2f}%"
        if close > ema50 and rsi >= 45:
            return "SELL", 22, f"EMA50 resistance reject dist={dist50*100:.2f}%"

    return None, 0, ""


def _detect_vote_flip(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """TV vote flip — buy_v suddenly switching vs trend direction."""
    bv1  = int(d1m.get("buy_v",  0) or 0)
    sv1  = int(d1m.get("sell_v", 0) or 0)
    bv5  = int(d5m.get("buy_v",  0) or 0)
    sv5  = int(d5m.get("sell_v", 0) or 0)
    bias5 = d5m.get("bias", "NEUTRAL") or "NEUTRAL"

    # 5m trending SELL but 1m now showing strong BUY votes → reversal on 1m
    if bias5 == "SELL" and bv1 > sv1 * 1.5 and bv1 >= 8:
        return "BUY", int(16 + min(10, bv1 - sv1) * 0.8), f"Vote flip: 5m SELL but 1m buy_v={bv1}>sell_v={sv1}"

    # 5m trending BUY but 1m now showing strong SELL votes → reversal on 1m
    if bias5 == "BUY" and sv1 > bv1 * 1.5 and sv1 >= 8:
        return "SELL", int(16 + min(10, sv1 - bv1) * 0.8), f"Vote flip: 5m BUY but 1m sell_v={sv1}>buy_v={bv1}"

    return None, 0, ""


def _detect_mtf_divergence(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str]:
    """Multi-TF divergence: 1m opposing 5m AND 15m (local reversal vs higher trend)."""
    b1  = d1m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b5  = d5m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b15 = d15m.get("bias", "NEUTRAL") or "NEUTRAL"
    s1  = float(d1m.get("strength", 0) or 0)
    rsi1 = float(d1m.get("rsi", 50) or 50)

    # 1m turning BUY against 5m+15m SELL → local bottom reversal
    if b1 == "BUY" and b5 == "SELL" and b15 == "SELL" and s1 >= 0.4:
        if rsi1 <= 45:   # 1m also not yet overbought → real reversal
            return "BUY", int(18 + s1 * 10), f"MTF divergence: 1m BUY vs 5m/15m SELL"

    # 1m turning SELL against 5m+15m BUY → local top reversal
    if b1 == "SELL" and b5 == "BUY" and b15 == "BUY" and s1 >= 0.4:
        if rsi1 >= 55:   # 1m also not yet oversold → real reversal
            return "SELL", int(18 + s1 * 10), f"MTF divergence: 1m SELL vs 5m/15m BUY"

    return None, 0, ""


def _detect_exhaustion(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """Candle exhaustion: very strong trend + extreme RSI = imminent reversal."""
    strength1 = float(d1m.get("strength", 0) or 0)
    strength5 = float(d5m.get("strength", 0) or 0)
    rsi1      = float(d1m.get("rsi", 50) or 50)
    rsi5      = float(d5m.get("rsi", 50) or 50)
    bias1     = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    bias5     = d5m.get("bias", "NEUTRAL") or "NEUTRAL"

    # Both TFs strongly bearish at extreme RSI low → exhaustion flip to BUY
    if (bias1 == "SELL" and bias5 == "SELL"
            and strength1 >= 0.75 and strength5 >= 0.65
            and rsi1 <= 28):
        score = int(20 + (28 - rsi1) * 0.8 + strength1 * 8)
        return "BUY", min(32, score), f"Bearish exhaustion: strength={strength1:.2f} RSI={rsi1:.0f}"

    # Both TFs strongly bullish at extreme RSI high → exhaustion flip to SELL
    if (bias1 == "BUY" and bias5 == "BUY"
            and strength1 >= 0.75 and strength5 >= 0.65
            and rsi1 >= 72):
        score = int(20 + (rsi1 - 72) * 0.8 + strength1 * 8)
        return "SELL", min(32, score), f"Bullish exhaustion: strength={strength1:.2f} RSI={rsi1:.0f}"

    return None, 0, ""


def _detect_ha_flip(d1m: dict) -> tuple[Optional[str], int, str]:
    """Heikin Ashi proxy: TV bias switching from SELL→BUY or BUY→SELL on 1m."""
    # The TV TA recommendation switches = HA-like candle flip detected
    bias1 = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    bv1   = int(d1m.get("buy_v",  0) or 0)
    sv1   = int(d1m.get("sell_v", 0) or 0)
    s1    = float(d1m.get("strength", 0) or 0)
    rsi1  = float(d1m.get("rsi", 50) or 50)

    # Bias just flipped to BUY with decent votes → HA-like bullish flip
    if bias1 == "BUY" and bv1 >= sv1 * 1.3 and s1 >= 0.35 and rsi1 <= 58:
        return "BUY", int(14 + s1 * 8), f"HA-proxy BUY flip: bias=BUY bv={bv1} sv={sv1}"

    # Bias just flipped to SELL with decent votes → HA-like bearish flip
    if bias1 == "SELL" and sv1 >= bv1 * 1.3 and s1 >= 0.35 and rsi1 >= 42:
        return "SELL", int(14 + s1 * 8), f"HA-proxy SELL flip: bias=SELL sv={sv1} bv={bv1}"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

def detect_reversal(pair: str, is_otc: bool = False) -> dict:
    """Run all reversal detectors and return combined result.

    Scoring
    ───────
    Each detector contributes a score (0-40 points).
    Total score > 70  → elite reversal zone (multiple signals stacking)
    Total score 45-70 → strong reversal
    Total score 25-44 → moderate reversal (signal given but lower weight)
    Total score < 25  → no clear reversal

    The direction with the most score wins.
    If BUY and SELL scores are within 10 points → conflicting, no signal.
    """
    import time
    cache_key = f"{pair}|{is_otc}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m  = _tv(pair, "1m")
    d5m  = _tv(pair, "5m")
    d15m = _tv(pair, "15m")

    if not d1m.get("ok") and not d5m.get("ok"):
        result = {
            "reversal_dir": None, "zone_quality": 0,
            "elite": False, "signals": ["no TV data"],
            "rsi_1m": 50.0, "zone_type": "none",
        }
        _CACHE[cache_key] = (time.time(), result)
        return result

    rsi1m = float(d1m.get("rsi", 50) or 50)
    rsi5m = float(d5m.get("rsi", 50) or 50)

    buy_score  = 0
    sell_score = 0
    buy_sigs:  list[str] = []
    sell_sigs: list[str] = []

    detectors = [
        _detect_rsi_extreme(rsi1m, rsi5m),
        _detect_rsi_divergence(d1m, d5m),
        _detect_ema_bounce(d1m),
        _detect_vote_flip(d1m, d5m),
        _detect_mtf_divergence(d1m, d5m, d15m),
        _detect_exhaustion(d1m, d5m),
        _detect_ha_flip(d1m),
    ]

    for rev_dir, score, reason in detectors:
        if not reason or score == 0:
            continue
        if rev_dir == "BUY":
            buy_score += score
            buy_sigs.append(reason)
        elif rev_dir == "SELL":
            sell_score += score
            sell_sigs.append(reason)

    # Stooq live tape as tiebreaker for OTC
    if _STOOQ_OK and _stooq_mom is not None:
        try:
            sq = _stooq_mom(pair)
            if sq:
                tape_dir = sq[0]
                if tape_dir == "BUY":
                    buy_score  += 8
                    buy_sigs.append("Stooq live-tape confirms BUY momentum")
                elif tape_dir == "SELL":
                    sell_score += 8
                    sell_sigs.append("Stooq live-tape confirms SELL momentum")
        except Exception:
            pass

    # Determine winner
    if buy_score > sell_score + 10:
        rev_dir    = "BUY"
        total      = buy_score
        signals    = buy_sigs
    elif sell_score > buy_score + 10:
        rev_dir    = "SELL"
        total      = sell_score
        signals    = sell_sigs
    else:
        rev_dir    = None
        total      = max(buy_score, sell_score)
        signals    = buy_sigs + sell_sigs

    # Quality and elite flag
    zone_quality = min(100, total)
    elite        = len(signals) >= 3 and zone_quality >= 70

    # Zone type label
    if rev_dir == "BUY":
        if rsi1m <= 26:    zone_type = "extreme_oversold"
        elif buy_score >= 40: zone_type = "reversal_zone_buy"
        else:              zone_type = "bounce_zone"
    elif rev_dir == "SELL":
        if rsi1m >= 74:    zone_type = "extreme_overbought"
        elif sell_score >= 40: zone_type = "reversal_zone_sell"
        else:              zone_type = "rejection_zone"
    else:
        zone_type = "no_reversal"

    result = {
        "reversal_dir": rev_dir,
        "zone_quality": zone_quality,
        "elite":        elite,
        "signals":      signals[:5],   # cap for log readability
        "rsi_1m":       round(rsi1m, 1),
        "zone_type":    zone_type,
        "buy_score":    buy_score,
        "sell_score":   sell_score,
    }

    if rev_dir:
        print(f"[reversal] {pair} {'OTC' if is_otc else 'LIVE'}: "
              f"{rev_dir} zone_quality={zone_quality} elite={elite} "
              f"[{zone_type}] signals={len(signals)}")

    _CACHE[cache_key] = (time.time(), result)
    return result
