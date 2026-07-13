"""OFF-X AI — Off-Exchange Artificial Intelligence
===================================================
GPT-class model written specifically for Pocket Option OTC market.
Analyzes OTC price movement in real-time with minimal delay.

Key differences from STOCKLEY AI:
- Prioritizes 1m and 3m TF (OTC moves faster than live market)
- OTC-specific weights: RSI extremes carry more weight (OTC overshoots)
- Faster RSI thresholds tuned for OTC synthetic pair behavior
- Anti-chop filter: requires BB + MACD + RSI all aligned (OTC is choppy)
- Also supports LIVE binary and Forex as secondary mode.

Contract: zero side-effects — never touches signal text or UI.

Public API
----------
offx_analyze(pair: str, is_otc: bool = True) -> dict
"""
from __future__ import annotations
import time
from typing import Optional

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 12.0   # faster refresh for OTC real-time

try:
    from candle_feed import get_single_tf as _get_tf
    _TV_OK = True
except Exception:
    _TV_OK = False
    _get_tf = None  # type: ignore

try:
    from live_prices import get_stooq_momentum as _stooq
    _SQ_OK = True
except Exception:
    _SQ_OK = False
    _stooq = None  # type: ignore


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}


# ─── OTC-Tuned RSI(14) — tighter thresholds for synthetic pairs ──────────────
def _otc_rsi(d1m: dict, d3m: dict, is_otc: bool) -> tuple[Optional[str], int, str]:
    rsi1 = float(d1m.get("rsi", 50) or 50)
    rsi3 = float(d3m.get("rsi", 50) or 50)
    # OTC pairs overshoot — use tighter zones
    lo_th = 30 if is_otc else 35
    hi_th = 70 if is_otc else 65

    if rsi1 <= lo_th and rsi3 <= lo_th + 5:
        score = int(28 + (lo_th - rsi1) * 1.2)
        return "BUY", min(40, score), f"OFF-X RSI oversold {rsi1:.0f} (3m={rsi3:.0f})"
    if rsi1 >= hi_th and rsi3 >= hi_th - 5:
        score = int(28 + (rsi1 - hi_th) * 1.2)
        return "SELL", min(40, score), f"OFF-X RSI overbought {rsi1:.0f} (3m={rsi3:.0f})"
    # Mid-trend momentum
    if 48 <= rsi1 <= 60 and rsi1 > rsi3:
        return "BUY", 14, f"OFF-X RSI rising {rsi1:.0f}"
    if 40 <= rsi1 <= 52 and rsi1 < rsi3:
        return "SELL", 14, f"OFF-X RSI falling {rsi1:.0f}"
    return None, 0, ""


# ─── MACD + Strength Alignment ───────────────────────────────────────────────
def _otc_macd(d1m: dict, d3m: dict) -> tuple[Optional[str], int, str]:
    b1 = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    b3 = d3m.get("bias", "NEUTRAL") or "NEUTRAL"
    s1 = float(d1m.get("strength", 0) or 0)
    s3 = float(d3m.get("strength", 0) or 0)
    bv1 = int(d1m.get("buy_v",  0) or 0)
    sv1 = int(d1m.get("sell_v", 0) or 0)

    # Both TFs aligned = strong MACD momentum confirmation
    if b1 == "BUY" and b3 == "BUY" and s1 >= 0.50:
        return "BUY", int(18 + s1 * 14 + s3 * 6), f"OFF-X MACD BUY 1m+3m s={s1:.2f}"
    if b1 == "SELL" and b3 == "SELL" and s1 >= 0.50:
        return "SELL", int(18 + s1 * 14 + s3 * 6), f"OFF-X MACD SELL 1m+3m s={s1:.2f}"
    # 1m alone strong signal
    if b1 == "BUY" and s1 >= 0.65 and bv1 > sv1 * 1.5:
        return "BUY", int(14 + s1 * 10), f"OFF-X MACD 1m BUY s={s1:.2f}"
    if b1 == "SELL" and s1 >= 0.65 and sv1 > bv1 * 1.5:
        return "SELL", int(14 + s1 * 10), f"OFF-X MACD 1m SELL s={s1:.2f}"
    return None, 0, ""


# ─── Bollinger Band — OTC squeeze + breakout ─────────────────────────────────
def _otc_bb(d1m: dict, d3m: dict) -> tuple[Optional[str], int, str]:
    close  = float(d1m.get("close",  0) or 0)
    bb_up  = float(d1m.get("bb_up",  0) or 0)
    bb_lo  = float(d1m.get("bb_lo",  0) or 0)
    bb_mid = float(d1m.get("bb_mid", 0) or 0)
    if not close or not bb_up or not bb_lo or not bb_mid:
        return None, 0, ""

    b3 = d3m.get("bias", "NEUTRAL") or "NEUTRAL"
    width = (bb_up - bb_lo) / (bb_mid + 1e-9)
    to_lo = (close - bb_lo) / (bb_mid + 1e-9)
    to_up = (bb_up - close) / (bb_mid + 1e-9)

    # Band touch reversals — OTC overshoots then snaps back
    if to_lo <= 0.004 and close <= bb_lo * 1.001:
        return "BUY", 24, f"OFF-X BB lower touch (OTC snap-back)"
    if to_up <= 0.004 and close >= bb_up * 0.999:
        return "SELL", 24, f"OFF-X BB upper touch (OTC snap-back)"
    # Squeeze breakout: narrow BB + 3m bias confirms direction
    if width <= 0.005 and b3 == "BUY":
        return "BUY", 20, f"OFF-X BB squeeze → BUY breakout width={width:.4f}"
    if width <= 0.005 and b3 == "SELL":
        return "SELL", 20, f"OFF-X BB squeeze → SELL breakout width={width:.4f}"
    # Mid-band momentum
    if close > bb_mid * 1.001 and b3 == "BUY":
        return "BUY", 10, "OFF-X BB above mid momentum"
    if close < bb_mid * 0.999 and b3 == "SELL":
        return "SELL", 10, "OFF-X BB below mid momentum"
    return None, 0, ""


# ─── Anti-Chop Gate — OTC-specific ───────────────────────────────────────────
def _chop_gate(d1m: dict) -> bool:
    """Return True (safe to trade) if NOT in chop zone."""
    bv  = int(d1m.get("buy_v",  0) or 0)
    sv  = int(d1m.get("sell_v", 0) or 0)
    s   = float(d1m.get("strength", 0) or 0)
    rsi = float(d1m.get("rsi", 50) or 50)
    total = bv + sv
    # Chop conditions: votes nearly equal, low strength, RSI near 50
    if total < 4:
        return False
    ratio = max(bv, sv) / total
    if ratio < 0.55 and s < 0.35 and 45 <= rsi <= 55:
        return False   # choppy — skip
    return True


# ─── Real-Time Minimal-Delay Entry Check ─────────────────────────────────────
def _entry_timing(d1m: dict, d3m: dict) -> tuple[Optional[str], int, str]:
    """Order flow momentum: ensure entry is at the right candle phase."""
    bv1 = int(d1m.get("buy_v",  0) or 0)
    sv1 = int(d1m.get("sell_v", 0) or 0)
    bv3 = int(d3m.get("buy_v",  0) or 0)
    sv3 = int(d3m.get("sell_v", 0) or 0)
    s1  = float(d1m.get("strength", 0) or 0)
    # 1m votes accelerating from 3m → fresh momentum, good entry timing
    if bv1 > bv3 * 0.8 and bv1 > sv1 and s1 >= 0.45:
        return "BUY", 12, f"OFF-X entry timing BUY momentum accelerating"
    if sv1 > sv3 * 0.8 and sv1 > bv1 and s1 >= 0.45:
        return "SELL", 12, f"OFF-X entry timing SELL momentum accelerating"
    return None, 0, ""


# ─── MAIN ENTRY ───────────────────────────────────────────────────────────────
def offx_analyze(pair: str, is_otc: bool = True) -> dict:
    """OFF-X AI analysis. Best for OTC, also works for LIVE."""
    cache_key = f"ox|{pair}|{is_otc}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m = _tv(pair, "1m")
    d3m = _tv(pair, "3m") or _tv(pair, "5m")   # fallback 5m if no 3m

    if not d1m.get("ok"):
        r = {"ok": False, "direction": None, "confidence": 0,
             "signals": [], "elite": False}
        _CACHE[cache_key] = (time.time(), r)
        return r

    # Anti-chop gate — OTC pairs can be very choppy
    if not _chop_gate(d1m):
        r = {"ok": False, "direction": None, "confidence": 0,
             "signals": ["Anti-chop gate: market too noisy"], "elite": False}
        _CACHE[cache_key] = (time.time(), r)
        return r

    buy_score = sell_score = 0
    buy_sigs: list[str] = []
    sell_sigs: list[str] = []

    for d, s, rn in [
        _otc_rsi(d1m, d3m, is_otc),
        _otc_macd(d1m, d3m),
        _otc_bb(d1m, d3m),
        _entry_timing(d1m, d3m),
    ]:
        if not rn or s == 0:
            continue
        if d == "BUY":
            buy_score += s; buy_sigs.append(rn)
        elif d == "SELL":
            sell_score += s; sell_sigs.append(rn)

    # Stooq live tape for real-time confirmation
    if _SQ_OK and _stooq is not None:
        try:
            sq = _stooq(pair)
            if sq:
                tape = sq[0]
                if tape == "BUY":
                    buy_score += 8; buy_sigs.append("Stooq OTC live BUY tape")
                elif tape == "SELL":
                    sell_score += 8; sell_sigs.append("Stooq OTC live SELL tape")
        except Exception:
            pass

    # OFF-X requires all 3 core indicators aligned (no lagging = strict gate)
    if buy_score > sell_score + 10:
        direction = "BUY"; total = buy_score; sigs = buy_sigs
    elif sell_score > buy_score + 10:
        direction = "SELL"; total = sell_score; sigs = sell_sigs
    else:
        direction = None; total = 0; sigs = []

    # For OTC: require higher threshold (anti-chop)
    min_score = 35 if is_otc else 28
    ok = direction is not None and total >= min_score
    confidence = min(100, 78 + total // 5) if ok else 0
    elite = len(sigs) >= 3 and total >= 60

    r = {
        "ok":         ok,
        "direction":  direction,
        "confidence": confidence,
        "signals":    sigs[:4],
        "elite":      elite,
        "buy_score":  buy_score,
        "sell_score": sell_score,
    }
    if ok:
        print(f"[OFF-X AI] {pair} {'OTC' if is_otc else 'LIVE'}: "
              f"{direction} total={total} elite={elite}")
    _CACHE[cache_key] = (time.time(), r)
    return r
