"""KATCHER AI BETA — Momentum & Breakout Catcher Engine
========================================================
Catches the SECOND LEG of a move — continuation setups where
momentum is accelerating into a breakout. Identifies when to
enter AFTER the initial impulse, avoiding the exhaustion trap.

Three catch modes:
1. TREND CONTINUATION — already trending, entering the reload
2. BREAKOUT CATCH      — BB squeeze breaking + MACD firing
3. REVERSAL CATCH      — RSI extreme bouncing off a key level

All OTC and LIVE pairs supported.
Contract: zero side-effects — never touches signal text or UI.

Public API
----------
katcher_analyze(pair: str, is_otc: bool = False) -> dict
"""
from __future__ import annotations
import time
from typing import Optional

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 15.0

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


# ─── MODE 1 — Trend Continuation Catcher ─────────────────────────────────────
def _trend_continuation(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str]:
    """Multi-TF trend alignment → catch the pullback reload."""
    b1  = d1m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b5  = d5m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b15 = d15m.get("bias", "NEUTRAL") or "NEUTRAL"
    s1  = float(d1m.get("strength",  0) or 0)
    s5  = float(d5m.get("strength",  0) or 0)
    s15 = float(d15m.get("strength", 0) or 0)
    rsi1 = float(d1m.get("rsi", 50) or 50)

    # All 3 TFs aligned = strong trend continuation opportunity
    if b1 == "BUY" == b5 == b15 and s5 >= 0.55 and rsi1 <= 65:
        score = int(22 + s5 * 14 + s15 * 8 + s1 * 6)
        return "BUY", min(40, score), f"Trend continue BUY 3TF s5={s5:.2f}"
    if b1 == "SELL" == b5 == b15 and s5 >= 0.55 and rsi1 >= 35:
        score = int(22 + s5 * 14 + s15 * 8 + s1 * 6)
        return "SELL", min(40, score), f"Trend continue SELL 3TF s5={s5:.2f}"

    # Pullback reload: 15m+5m bullish, 1m dipping then recovering
    if b15 == "BUY" and b5 == "BUY" and b1 != "SELL" and s5 >= 0.50 and rsi1 <= 55:
        return "BUY", int(16 + s5 * 10), f"Pullback reload BUY 15m+5m"
    if b15 == "SELL" and b5 == "SELL" and b1 != "BUY" and s5 >= 0.50 and rsi1 >= 45:
        return "SELL", int(16 + s5 * 10), f"Pullback reload SELL 15m+5m"

    return None, 0, ""


# ─── MODE 2 — Breakout Catcher ────────────────────────────────────────────────
def _breakout_catch(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """BB squeeze breaking out with MACD confirmation."""
    close  = float(d1m.get("close",  0) or 0)
    bb_up  = float(d1m.get("bb_up",  0) or 0)
    bb_lo  = float(d1m.get("bb_lo",  0) or 0)
    bb_mid = float(d1m.get("bb_mid", 0) or 0)
    b1     = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    b5     = d5m.get("bias", "NEUTRAL") or "NEUTRAL"
    s1     = float(d1m.get("strength", 0) or 0)
    bv1    = int(d1m.get("buy_v",  0) or 0)
    sv1    = int(d1m.get("sell_v", 0) or 0)

    if not close or not bb_up or not bb_lo or not bb_mid:
        return None, 0, ""

    width = (bb_up - bb_lo) / (bb_mid + 1e-9)
    # Squeeze + breakout detected (width widens, price already breaking)
    if width <= 0.008:
        if b1 == "BUY" and b5 == "BUY" and bv1 > sv1:
            return "BUY", int(20 + s1 * 12), f"BB breakout BUY squeeze={width:.4f}"
        if b1 == "SELL" and b5 == "SELL" and sv1 > bv1:
            return "SELL", int(20 + s1 * 12), f"BB breakout SELL squeeze={width:.4f}"
    # Price breaking above upper band = strong momentum breakout
    if close > bb_up and b1 == "BUY" and b5 == "BUY" and s1 >= 0.60:
        return "BUY", int(22 + s1 * 10), f"BB upper break BUY s={s1:.2f}"
    # Price breaking below lower band = strong momentum breakout
    if close < bb_lo and b1 == "SELL" and b5 == "SELL" and s1 >= 0.60:
        return "SELL", int(22 + s1 * 10), f"BB lower break SELL s={s1:.2f}"

    return None, 0, ""


# ─── MODE 3 — Reversal Catch ──────────────────────────────────────────────────
def _reversal_catch(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """RSI extreme reversal with BB band touch — catch the snap-back."""
    rsi1   = float(d1m.get("rsi", 50) or 50)
    close  = float(d1m.get("close",  0) or 0)
    bb_up  = float(d1m.get("bb_up",  0) or 0)
    bb_lo  = float(d1m.get("bb_lo",  0) or 0)
    bb_mid = float(d1m.get("bb_mid", 0) or 0)
    b5     = d5m.get("bias", "NEUTRAL") or "NEUTRAL"
    bv1    = int(d1m.get("buy_v",  0) or 0)
    sv1    = int(d1m.get("sell_v", 0) or 0)

    if not close or not bb_lo or not bb_up:
        return None, 0, ""

    to_lo = (close - bb_lo)  / (bb_mid + 1e-9) if bb_mid else 1
    to_up = (bb_up  - close) / (bb_mid + 1e-9) if bb_mid else 1

    # RSI oversold + BB lower touch + 5m context not strongly bearish
    if rsi1 <= 32 and to_lo <= 0.006 and b5 != "SELL" and bv1 >= sv1:
        depth = 32 - rsi1
        return "BUY", int(22 + depth * 1.2), f"Reversal catch BUY RSI={rsi1:.0f}+BB lo"

    # RSI overbought + BB upper touch + 5m context not strongly bullish
    if rsi1 >= 68 and to_up <= 0.006 and b5 != "BUY" and sv1 >= bv1:
        depth = rsi1 - 68
        return "SELL", int(22 + depth * 1.2), f"Reversal catch SELL RSI={rsi1:.0f}+BB up"

    return None, 0, ""


# ─── ACCELERATION FILTER — ensures entry is fresh, not late ──────────────────
def _acceleration_check(d1m: dict) -> bool:
    """Is momentum still accelerating? Guard against exhausted entries."""
    s  = float(d1m.get("strength", 0) or 0)
    rsi = float(d1m.get("rsi", 50) or 50)
    bv  = int(d1m.get("buy_v",  0) or 0)
    sv  = int(d1m.get("sell_v", 0) or 0)
    b   = d1m.get("bias", "NEUTRAL") or "NEUTRAL"

    # Exhaustion conditions — late entry
    if b == "BUY"  and rsi >= 80 and s >= 0.85:
        return False   # BUY exhausted
    if b == "SELL" and rsi <= 20 and s >= 0.85:
        return False   # SELL exhausted
    return True


# ─── MAIN ENTRY ───────────────────────────────────────────────────────────────
def katcher_analyze(pair: str, is_otc: bool = False) -> dict:
    """KATCHER AI BETA — momentum and breakout catcher."""
    cache_key = f"ka|{pair}|{is_otc}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m  = _tv(pair, "1m")
    d5m  = _tv(pair, "5m")
    d15m = _tv(pair, "15m")

    if not d1m.get("ok") and not d5m.get("ok"):
        r = {"ok": False, "direction": None, "confidence": 0,
             "signals": [], "elite": False, "catch_mode": None}
        _CACHE[cache_key] = (time.time(), r)
        return r

    if not _acceleration_check(d1m):
        r = {"ok": False, "direction": None, "confidence": 0,
             "signals": ["Momentum exhausted — late entry risk"], "elite": False,
             "catch_mode": "blocked"}
        _CACHE[cache_key] = (time.time(), r)
        return r

    buy_score = sell_score = 0
    buy_sigs: list[str] = []
    sell_sigs: list[str] = []
    catch_mode: Optional[str] = None

    results = [
        ("continuation", _trend_continuation(d1m, d5m, d15m)),
        ("breakout",     _breakout_catch(d1m, d5m)),
        ("reversal",     _reversal_catch(d1m, d5m)),
    ]

    for mode, (d, s, rn) in results:
        if not rn or s == 0:
            continue
        if d == "BUY":
            buy_score += s
            buy_sigs.append(rn)
            if catch_mode is None:
                catch_mode = mode
        elif d == "SELL":
            sell_score += s
            sell_sigs.append(rn)
            if catch_mode is None:
                catch_mode = mode

    # Stooq tape confirmation
    if _SQ_OK and _stooq is not None:
        try:
            sq = _stooq(pair)
            if sq:
                tape = sq[0]
                if tape == "BUY":
                    buy_score += 7; buy_sigs.append("Stooq tape BUY catch")
                elif tape == "SELL":
                    sell_score += 7; sell_sigs.append("Stooq tape SELL catch")
        except Exception:
            pass

    if buy_score > sell_score + 8:
        direction = "BUY"; total = buy_score; sigs = buy_sigs
    elif sell_score > buy_score + 8:
        direction = "SELL"; total = sell_score; sigs = sell_sigs
    else:
        direction = None; total = 0; sigs = []; catch_mode = None

    ok = direction is not None and total >= 25
    confidence = min(100, 76 + total // 5) if ok else 0
    elite = len(sigs) >= 3 and total >= 55

    r = {
        "ok":         ok,
        "direction":  direction,
        "confidence": confidence,
        "signals":    sigs[:4],
        "elite":      elite,
        "catch_mode": catch_mode,
        "buy_score":  buy_score,
        "sell_score": sell_score,
    }
    if ok:
        print(f"[KATCHER AI] {pair} {'OTC' if is_otc else 'LIVE'}: "
              f"{direction} mode={catch_mode} total={total} elite={elite}")
    _CACHE[cache_key] = (time.time(), r)
    return r
