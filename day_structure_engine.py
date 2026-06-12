"""DAY-OF-WEEK MARKET STRUCTURE PLAYBOOK ENGINE
================================================
Drop-in addition. ZERO changes to existing signal text/formatting.

Implements 5 weekday playbooks using ONLY raw OHLCV price-action math:
  Monday    (0) — Range Day      : build 4-6h range, fade the edges
  Tuesday   (1) — Breakout Day   : confirm Monday's range break, filter fakes
  Wednesday (2) — Trend Day      : swing structure + BOS continuation
  Thursday  (3) — Reversal Day   : supply/demand zones + volume absorption
  Friday    (4) — Fakeout Day    : delta/volume vs price divergence, fade traps

NO generic indicators. No RSI, MACD, Stochastic, Bollinger Bands, ADX,
TA-Lib, or pandas-ta. All logic computed from raw OHLCV.

Public API:
    analyze_day_structure(df, pair, weekday) → dict
    scan_all_pairs(pair_data, weekday)       → dict[pair, dict]
    day_structure_vote(pair, is_otc)         → dict | None   ← signals.py calls this

Return shape (internal — not sent to Telegram):
    {
      "pair":      str,
      "weekday":   str,
      "playbook":  str,
      "signal":    "BUY" | "SELL" | "WAIT",
      "confidence": 0-100,
      "reasoning": list[str],
      "sl_price":  float,
      "tp_price":  float,
      "direction": "BUY" | "SELL" | "WAIT",   # alias for signals.py vote system
      "grade":     str,                         # A+++ … C
    }

Signal text contract: NEVER touched. This module returns metadata only.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

try:
    import numpy as np
    import pandas as pd
    _PD_OK = True
except Exception:
    np = None  # type: ignore
    pd = None  # type: ignore
    _PD_OK = False

# ── Cache (18s TTL — same cadence as other engines) ──────────────────────────
_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL   = 18.0

# ── Monday range persisted across calls (keyed by pair) ──────────────────────
_MON_RANGE: dict[str, tuple[float, float]] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  PURE PRICE-ACTION HELPERS  (no external indicator libraries)
# ══════════════════════════════════════════════════════════════════════════════

def _atr(df: "pd.DataFrame", p: int = 14) -> "pd.Series":
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr = (hi - lo).combine((hi - cl.shift()).abs(), max).combine(
        (lo - cl.shift()).abs(), max
    )
    return tr.rolling(p).mean()


def _avg_body(df: "pd.DataFrame", p: int = 20) -> "pd.Series":
    return abs(df["close"] - df["open"]).rolling(p).mean()


def _swing_highs(hi: "pd.Series", order: int = 3) -> "pd.Series":
    """Boolean mask: True when bar[i] is a pivot high within ±order bars."""
    n   = len(hi)
    out = pd.Series(False, index=hi.index)
    for i in range(order, n - order):
        win = hi.iloc[i - order: i + order + 1]
        if float(hi.iloc[i]) == float(win.max()):
            out.iloc[i] = True
    return out


def _swing_lows(lo: "pd.Series", order: int = 3) -> "pd.Series":
    n   = len(lo)
    out = pd.Series(False, index=lo.index)
    for i in range(order, n - order):
        win = lo.iloc[i - order: i + order + 1]
        if float(lo.iloc[i]) == float(win.min()):
            out.iloc[i] = True
    return out


def _swing_structure(df: "pd.DataFrame", lookback: int = 30) -> str:
    """'uptrend' | 'downtrend' | 'sideways' from HH/HL or LH/LL sequence."""
    sub = df.tail(lookback)
    sh  = _swing_highs(sub["high"])
    sl  = _swing_lows(sub["low"])
    pivot_highs = sub["high"][sh].values
    pivot_lows  = sub["low"][sl].values
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "sideways"
    hh = pivot_highs[-1] > pivot_highs[-2]
    hl = pivot_lows[-1]  > pivot_lows[-2]
    lh = pivot_highs[-1] < pivot_highs[-2]
    ll = pivot_lows[-1]  < pivot_lows[-2]
    if hh and hl:  return "uptrend"
    if lh and ll:  return "downtrend"
    return "sideways"


def _bos_detected(df: "pd.DataFrame", direction: str, lookback: int = 20) -> bool:
    """Break of Structure: last close breaks the most recent opposing swing."""
    sub = df.tail(lookback)
    cl  = sub["close"]
    if direction == "uptrend":
        sh = _swing_highs(sub["high"])
        pivots = sub["high"][sh].values
        if len(pivots) < 1:
            return False
        return float(cl.iloc[-1]) > float(pivots[-1])
    else:
        sl = _swing_lows(sub["low"])
        pivots = sub["low"][sl].values
        if len(pivots) < 1:
            return False
        return float(cl.iloc[-1]) < float(pivots[-1])


def _rejection_candle(df: "pd.DataFrame", idx: int = -1) -> tuple[bool, str]:
    """True + direction when bar has wick ≥ 1.5× body, opposing-direction close."""
    row   = df.iloc[idx]
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body  = abs(c - o)
    upper = h - max(c, o)
    lower = min(c, o) - l
    if body < 1e-10:
        return False, ""
    if lower >= body * 1.5 and c > o:  # long lower wick, bullish close → buy
        return True, "BUY"
    if upper >= body * 1.5 and c < o:  # long upper wick, bearish close → sell
        return True, "SELL"
    return False, ""


def _absorption_candle(df: "pd.DataFrame", idx: int = -1,
                       vol_mult: float = 1.4, body_pct_max: float = 0.35) -> bool:
    """High volume / delta but tiny net body — stall / absorption."""
    row  = df.iloc[idx]
    rng  = float(row["high"]) - float(row["low"])
    body = abs(float(row["close"]) - float(row["open"]))
    if rng < 1e-10:
        return False
    body_pct = body / rng
    # Volume check
    vol = float(row.get("volume", 0) or 0)
    avg_vol = float(df["volume"].rolling(20).mean().iloc[idx]) if "volume" in df.columns else 0
    high_vol = (avg_vol > 0 and vol > avg_vol * vol_mult)
    return body_pct < body_pct_max and high_vol


def _volume_profile(df: "pd.DataFrame", bins: int = 60
                    ) -> tuple[float, float, float]:
    """POC, VAH, VAL from OHLCV via tick-approximation."""
    if "volume" not in df.columns or df["volume"].sum() == 0:
        # No volume data — use price distribution
        mid = (df["high"] + df["low"]) / 2
        poc = float(mid.iloc[mid.value_counts().idxmax()]) if len(mid) > 0 else float(mid.mean())
        return poc, float(df["high"].quantile(0.7)), float(df["low"].quantile(0.3))
    lo_v, hi_v = float(df["low"].min()), float(df["high"].max())
    if hi_v <= lo_v:
        return (lo_v + hi_v) / 2, hi_v, lo_v
    edges   = np.linspace(lo_v, hi_v, bins + 1)
    vol_arr = np.zeros(bins)
    for _, row in df.iterrows():
        rlo, rhi = float(row["low"]), float(row["high"])
        vol = float(row.get("volume", 1) or 1)
        for i, (b1, b2) in enumerate(zip(edges[:-1], edges[1:])):
            ov = max(0.0, min(rhi, b2) - max(rlo, b1))
            if ov > 0:
                vol_arr[i] += vol * (ov / (rhi - rlo + 1e-9))
    poc_idx = int(vol_arr.argmax())
    poc     = (edges[poc_idx] + edges[poc_idx + 1]) / 2
    target  = vol_arr.sum() * 0.70
    lo_i = hi_i = poc_idx
    acc   = vol_arr[poc_idx]
    while acc < target:
        le = vol_arr[lo_i - 1] if lo_i > 0 else 0
        he = vol_arr[hi_i + 1] if hi_i < bins - 1 else 0
        if le >= he and lo_i > 0:
            lo_i -= 1; acc += le
        elif hi_i < bins - 1:
            hi_i += 1; acc += he
        else:
            break
    val = (edges[lo_i] + edges[lo_i + 1]) / 2
    vah = (edges[hi_i] + edges[hi_i + 1]) / 2
    return poc, vah, val


def _supply_demand_zones(df: "pd.DataFrame", lookback: int = 100
                         ) -> tuple[list[float], list[float]]:
    """
    Supply zones: small consolidation (low avg body) immediately before
    a strong bearish expansion (large body downward).
    Demand zones: same but before a strong bullish expansion.
    Returns (demand_levels, supply_levels).
    """
    sub     = df.tail(lookback)
    ab      = _avg_body(sub, 10)
    demand: list[float] = []
    supply: list[float] = []
    n = len(sub)
    for i in range(5, n - 2):
        curr_body = abs(float(sub["close"].iloc[i]) - float(sub["open"].iloc[i]))
        avg_b     = float(ab.iloc[i])
        if avg_b < 1e-10:
            continue
        base_body = abs(float(sub["close"].iloc[i - 1]) - float(sub["open"].iloc[i - 1]))
        base_ratio = base_body / avg_b
        curr_ratio = curr_body / avg_b
        base_bull  = float(sub["close"].iloc[i - 1]) > float(sub["open"].iloc[i - 1])
        curr_bull  = float(sub["close"].iloc[i])     > float(sub["open"].iloc[i])
        # Demand: small base candle then large bull expansion
        if base_ratio < 0.6 and curr_ratio > 1.8 and curr_bull:
            demand.append(float(sub["low"].iloc[i - 1]))
        # Supply: small base candle then large bear expansion
        if base_ratio < 0.6 and curr_ratio > 1.8 and not curr_bull:
            supply.append(float(sub["high"].iloc[i - 1]))
    return demand, supply


def _cumulative_delta(df: "pd.DataFrame", lookback: int = 10) -> float:
    """
    Footprint-style volume delta approximation.
    Positive = net buying pressure; negative = net selling.
    Uses (close - open) / range × volume as a proxy for delta per bar.
    """
    sub = df.tail(lookback)
    if "volume" not in sub.columns:
        return 0.0
    delta = 0.0
    for _, row in sub.iterrows():
        rng  = float(row["high"]) - float(row["low"]) + 1e-10
        body = float(row["close"]) - float(row["open"])
        vol  = float(row.get("volume", 1) or 1)
        delta += (body / rng) * vol
    return delta


def _grade(confidence: float, reasoning_count: int) -> str:
    if confidence >= 90 and reasoning_count >= 3: return "A+++"
    if confidence >= 82 and reasoning_count >= 2: return "A++"
    if confidence >= 74 and reasoning_count >= 2: return "A+"
    if confidence >= 65 and reasoning_count >= 1: return "A"
    if confidence >= 55 and reasoning_count >= 1: return "B"
    return "C"


def _sl_tp(df: "pd.DataFrame", signal: str, atr_mult_sl: float = 1.5,
           atr_mult_tp: float = 3.0) -> tuple[float, float]:
    price = float(df["close"].iloc[-1])
    atr_v = float(_atr(df).iloc[-1]) if not _atr(df).isna().all() else price * 0.001
    if signal == "BUY":
        return price - atr_v * atr_mult_sl, price + atr_v * atr_mult_tp
    return price + atr_v * atr_mult_sl, price - atr_v * atr_mult_tp


def _wait(pair: str, weekday_name: str, playbook: str) -> dict:
    return {
        "pair": pair, "weekday": weekday_name, "playbook": playbook,
        "signal": "WAIT", "direction": "WAIT", "confidence": 0,
        "reasoning": [], "sl_price": 0.0, "tp_price": 0.0, "grade": "C",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SELF-BACKTEST GATE
#  Looks back 50-100 candles and checks if this weekday's pattern type
#  would have been profitable ≥50% of occurrences.
# ══════════════════════════════════════════════════════════════════════════════

def _backtest_gate(df: "pd.DataFrame", playbook: str,
                   lookback: int = 80) -> bool:
    """Return True when recent pattern win-rate ≥ 50%."""
    sub = df.tail(lookback)
    n   = len(sub)
    if n < 20:
        return True   # not enough history → allow through

    wins = losses = 0
    atr_s = _atr(sub)

    try:
        if playbook == "range":
            # Pattern: rejection at local high/low; win = next bar moves away
            for i in range(10, n - 2):
                rng_hi = float(sub["high"].iloc[max(0, i-6):i].max())
                rng_lo = float(sub["low"].iloc[max(0, i-6):i].min())
                rej, rej_dir = _rejection_candle(sub, i)
                if not rej:
                    continue
                cl_now  = float(sub["close"].iloc[i])
                cl_next = float(sub["close"].iloc[i + 1])
                if rej_dir == "BUY"  and cl_next > cl_now: wins += 1
                elif rej_dir == "SELL" and cl_next < cl_now: wins += 1
                else: losses += 1

        elif playbook == "breakout":
            ab = _avg_body(sub, 20)
            av = sub["volume"].rolling(20).mean() if "volume" in sub.columns else None
            for i in range(20, n - 2):
                avg_b = float(ab.iloc[i])
                body  = abs(float(sub["close"].iloc[i]) - float(sub["open"].iloc[i]))
                if body < avg_b * 1.2:
                    continue
                bull_break = float(sub["close"].iloc[i]) > float(sub["high"].iloc[max(0, i-5):i].max())
                bear_break = float(sub["close"].iloc[i]) < float(sub["low"].iloc[max(0, i-5):i].min())
                if not (bull_break or bear_break):
                    continue
                cl_now  = float(sub["close"].iloc[i])
                cl_next = float(sub["close"].iloc[i + 1])
                if bull_break and cl_next > cl_now:   wins += 1
                elif bear_break and cl_next < cl_now: wins += 1
                else: losses += 1

        elif playbook == "continuation":
            for i in range(20, n - 2):
                struct = _swing_structure(sub.iloc[max(0, i-25):i + 1])
                if struct == "sideways":
                    continue
                bos = _bos_detected(sub.iloc[max(0, i-20):i + 1], struct)
                if not bos:
                    continue
                cl_now  = float(sub["close"].iloc[i])
                cl_next = float(sub["close"].iloc[i + 1])
                if struct == "uptrend"   and cl_next > cl_now: wins += 1
                elif struct == "downtrend" and cl_next < cl_now: wins += 1
                else: losses += 1

        elif playbook in ("reversal", "fakeout"):
            for i in range(10, n - 2):
                rej, rej_dir = _rejection_candle(sub, i)
                if not rej:
                    continue
                cl_now  = float(sub["close"].iloc[i])
                cl_next = float(sub["close"].iloc[i + 1])
                atr_v   = float(atr_s.iloc[i]) if not pd.isna(atr_s.iloc[i]) else 0
                favorable = (cl_next - cl_now if rej_dir == "BUY" else cl_now - cl_next)
                if favorable >= atr_v * 0.5: wins += 1
                else: losses += 1

    except Exception:
        return True   # on any error, allow through

    total = wins + losses
    if total < 3:
        return True   # too few samples → allow through
    return (wins / total) >= 0.50


# ══════════════════════════════════════════════════════════════════════════════
#  WEEKDAY PLAYBOOKS
# ══════════════════════════════════════════════════════════════════════════════

def _monday_range(df: "pd.DataFrame", pair: str) -> dict:
    """Range Day: build 4-6h range from first candles, fade the edges."""
    n    = len(df)
    ab   = _avg_body(df, 20)
    name = "range"
    WD   = "monday"

    # Build range from first ~24 bars (4h at 10m, or 6h at 5m, etc.)
    range_bars = min(24, n // 3)
    if range_bars < 6:
        return _wait(pair, WD, name)
    range_window = df.iloc[:range_bars]
    r_hi = float(range_window["high"].max())
    r_lo = float(range_window["low"].min())
    _MON_RANGE[pair] = (r_hi, r_lo)

    price   = float(df["close"].iloc[-1])
    rej, rej_dir = _rejection_candle(df, -1)
    avg_b   = float(ab.iloc[-1]) if not pd.isna(ab.iloc[-1]) else 0

    # Expansion candle suppresses range signals
    last_body = abs(float(df["close"].iloc[-1]) - float(df["open"].iloc[-1]))
    if last_body > avg_b * 1.5 and (price > r_hi or price < r_lo):
        return _wait(pair, WD, name)   # early breakout — skip range fade

    # SELL on range_high rejection
    if abs(price - r_hi) / max(r_hi, 1e-10) < 0.002 and rej and rej_dir == "SELL":
        sl, tp = _sl_tp(df, "SELL")
        return {
            "pair": pair, "weekday": WD, "playbook": name,
            "signal": "SELL", "direction": "SELL",
            "confidence": 72,
            "reasoning": ["price at Monday range_high", "rejection candle SELL"],
            "sl_price": sl, "tp_price": tp,
            "grade": _grade(72, 2),
        }
    # BUY on range_low rejection
    if abs(price - r_lo) / max(r_lo, 1e-10) < 0.002 and rej and rej_dir == "BUY":
        sl, tp = _sl_tp(df, "BUY")
        return {
            "pair": pair, "weekday": WD, "playbook": name,
            "signal": "BUY", "direction": "BUY",
            "confidence": 72,
            "reasoning": ["price at Monday range_low", "rejection candle BUY"],
            "sl_price": sl, "tp_price": tp,
            "grade": _grade(72, 2),
        }
    return _wait(pair, WD, name)


def _tuesday_breakout(df: "pd.DataFrame", pair: str) -> dict:
    """Breakout Day: confirm Monday's range break, filter fakes."""
    n    = len(df)
    name = "breakout"
    WD   = "tuesday"

    mon = _MON_RANGE.get(pair)
    if mon is None:
        # Monday range not stored — derive from earlier portion of df
        early = df.iloc[:max(6, n // 4)]
        mon   = (float(early["high"].max()), float(early["low"].min()))

    r_hi, r_lo = mon
    ab  = _avg_body(df, 20)
    avg_b = float(ab.iloc[-1]) if not pd.isna(ab.iloc[-1]) else 0
    av  = df["volume"].rolling(20).mean() if "volume" in df.columns else None

    price    = float(df["close"].iloc[-1])
    last_body = abs(float(df["close"].iloc[-1]) - float(df["open"].iloc[-1]))
    vol_now  = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0
    avg_vol  = float(av.iloc[-1]) if av is not None and not pd.isna(av.iloc[-1]) else 0
    strong_vol = avg_vol > 0 and vol_now > avg_vol

    bull_break = price > r_hi and last_body >= avg_b
    bear_break = price < r_lo and last_body >= avg_b

    if not (bull_break or bear_break):
        return _wait(pair, WD, name)

    # Fake-breakout filter: did price close back inside range on prev bar?
    for offset in (-2, -3):
        if abs(offset) >= n:
            break
        prev_cl = float(df["close"].iloc[offset])
        if r_lo < prev_cl < r_hi:
            return _wait(pair, WD, name)   # closed back inside → fake

    # Retest or immediate continuation confirmation
    confirm = strong_vol
    if n >= 3:
        prev1 = float(df["close"].iloc[-2])
        prev2 = float(df["close"].iloc[-3])
        continuation = (bull_break and prev1 > prev2) or (bear_break and prev1 < prev2)
        retest = bull_break and prev1 < r_hi < price or bear_break and prev1 > r_lo > price
        confirm = confirm or continuation or retest

    if not confirm:
        return _wait(pair, WD, name)

    sig = "BUY" if bull_break else "SELL"
    conf = 78 if strong_vol else 68
    reasons = [
        f"price broke Monday {'range_high' if bull_break else 'range_low'}",
        "conviction body on breakout candle",
    ]
    if strong_vol: reasons.append("above-average volume confirms break")
    sl, tp = _sl_tp(df, sig)
    return {
        "pair": pair, "weekday": WD, "playbook": name,
        "signal": sig, "direction": sig,
        "confidence": conf, "reasoning": reasons,
        "sl_price": sl, "tp_price": tp,
        "grade": _grade(conf, len(reasons)),
    }


def _wednesday_continuation(df: "pd.DataFrame", pair: str) -> dict:
    """Trend Continuation Day: swing structure + pullback + BOS."""
    WD   = "wednesday"
    name = "continuation"

    struct = _swing_structure(df, 30)
    if struct == "sideways":
        return _wait(pair, WD, name)

    # Pullback detection: last 3-5 bars retraced into prior structure zone
    trend_dir  = "BUY" if struct == "uptrend" else "SELL"
    pullback_ok = False
    n = len(df)
    if n >= 10:
        sub5    = df.tail(5)
        if struct == "uptrend":
            pullback_ok = float(sub5["low"].min()) < float(df["close"].iloc[-6])
        else:
            pullback_ok = float(sub5["high"].max()) > float(df["close"].iloc[-6])

    if not pullback_ok:
        return _wait(pair, WD, name)

    # BOS confirmation
    if not _bos_detected(df, struct, lookback=20):
        return _wait(pair, WD, name)

    reasons = [
        f"swing structure: {struct}",
        "pullback into prior structure level",
        "break of structure confirmed",
    ]
    conf = 80
    sl, tp = _sl_tp(df, trend_dir)
    return {
        "pair": pair, "weekday": WD, "playbook": name,
        "signal": trend_dir, "direction": trend_dir,
        "confidence": conf, "reasoning": reasons,
        "sl_price": sl, "tp_price": tp,
        "grade": _grade(conf, len(reasons)),
    }


def _thursday_reversal(df: "pd.DataFrame", pair: str) -> dict:
    """Reversal Day: supply/demand zones + volume profile + absorption."""
    WD   = "thursday"
    name = "reversal"

    demand_zones, supply_zones = _supply_demand_zones(df, 100)
    poc, vah, val              = _volume_profile(df.tail(60))
    price                      = float(df["close"].iloc[-1])
    atr_v                      = float(_atr(df).iloc[-1]) if not _atr(df).isna().all() else price * 0.001
    zone_tol                   = atr_v * 1.2

    near_demand = any(abs(price - z) < zone_tol for z in demand_zones)
    near_supply = any(abs(price - z) < zone_tol for z in supply_zones)
    near_val    = abs(price - val) < zone_tol
    near_vah    = abs(price - vah) < zone_tol

    absorption  = _absorption_candle(df, -1)

    reasons: list[str] = []
    sig = "WAIT"

    if near_demand and near_val and absorption:
        sig = "BUY"
        reasons = ["demand zone reached", "VAL aligned", "absorption candle"]
    elif near_demand and absorption:
        sig = "BUY"
        reasons = ["demand zone reached", "absorption candle"]
    elif near_supply and near_vah and absorption:
        sig = "SELL"
        reasons = ["supply zone reached", "VAH aligned", "absorption candle"]
    elif near_supply and absorption:
        sig = "SELL"
        reasons = ["supply zone reached", "absorption candle"]

    if sig == "WAIT":
        return _wait(pair, WD, name)

    conf = 82 if len(reasons) >= 3 else 70
    sl, tp = _sl_tp(df, sig, atr_mult_sl=1.8, atr_mult_tp=3.6)
    return {
        "pair": pair, "weekday": WD, "playbook": name,
        "signal": sig, "direction": sig,
        "confidence": conf, "reasoning": reasons,
        "sl_price": sl, "tp_price": tp,
        "grade": _grade(conf, len(reasons)),
    }


def _friday_fakeout(df: "pd.DataFrame", pair: str) -> dict:
    """Fakeout Day: delta/volume divergence vs price; fade traps. Higher threshold."""
    WD   = "friday"
    name = "fakeout"
    n    = len(df)

    sh_mask = _swing_highs(df["high"])
    sl_mask = _swing_lows(df["low"])
    pivot_highs = df["high"][sh_mask].values
    pivot_lows  = df["low"][sl_mask].values
    price       = float(df["close"].iloc[-1])
    atr_v       = float(_atr(df).iloc[-1]) if not _atr(df).isna().all() else price * 0.001

    # Check if last bar broke a swing level with divergent delta
    broke_high = len(pivot_highs) > 0 and float(df["high"].iloc[-1]) > pivot_highs[-1]
    broke_low  = len(pivot_lows)  > 0 and float(df["low"].iloc[-1])  < pivot_lows[-1]

    if not (broke_high or broke_low):
        return _wait(pair, WD, name)

    delta5  = _cumulative_delta(df, 5)
    delta15 = _cumulative_delta(df, 15)
    last    = df.iloc[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    rng     = h - l + 1e-10
    wick_pct = ((h - max(c, o)) / rng if broke_high else (min(c, o) - l) / rng)
    body_pct = abs(c - o) / rng

    # Real move: delta confirms break + body conviction + follow-through
    real_break = (
        (broke_high and delta5 > 0 and body_pct > 0.4) or
        (broke_low  and delta5 < 0 and body_pct > 0.4)
    )
    # Fakeout: wick-dominant candle + delta divergence
    fakeout = (
        wick_pct > 0.45 and body_pct < 0.30 and
        ((broke_high and delta5 < 0) or (broke_low and delta5 > 0))
    )

    sig     = "WAIT"
    reasons : list[str] = []

    if real_break:
        sig = "BUY" if broke_high else "SELL"
        reasons = [
            f"real break of swing {'high' if broke_high else 'low'}",
            "volume/delta confirms direction",
            "body conviction on breakout bar",
        ]
    elif fakeout:
        sig = "SELL" if broke_high else "BUY"   # fade the trap
        reasons = [
            f"fakeout through swing {'high' if broke_high else 'low'}",
            "wick-dominant bar (trap candle)",
            "delta divergence: volume didn't follow price",
        ]

    if sig == "WAIT":
        return _wait(pair, WD, name)

    # Friday threshold boost: require ≥78 confidence
    base_conf = 80 if fakeout else 74
    if base_conf < 78:
        return _wait(pair, WD, name)

    sl, tp = _sl_tp(df, sig, atr_mult_sl=1.2, atr_mult_tp=2.4)
    return {
        "pair": pair, "weekday": WD, "playbook": name,
        "signal": sig, "direction": sig,
        "confidence": base_conf, "reasoning": reasons,
        "sl_price": sl, "tp_price": tp,
        "grade": _grade(base_conf, len(reasons)),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC — single-pair analysis
# ══════════════════════════════════════════════════════════════════════════════

_WEEKDAY_NAMES = {0: "monday", 1: "tuesday", 2: "wednesday",
                  3: "thursday", 4: "friday", 5: "saturday", 6: "sunday"}
_PLAYBOOK_NAMES = {0: "range", 1: "breakout", 2: "continuation",
                   3: "reversal", 4: "fakeout"}


def analyze_day_structure(df: "pd.DataFrame", pair: str,
                           weekday: Optional[int] = None) -> dict:
    """
    Run the matching weekday playbook for one pair's OHLCV DataFrame.

    Args:
        df      : OHLCV DataFrame with lowercase columns (open/high/low/close[/volume])
        pair    : pair label e.g. "XAU/USD"
        weekday : 0=Mon … 4=Fri. If None, uses current UTC weekday.

    Returns:
        dict with keys: pair, weekday, playbook, signal, direction,
                        confidence, reasoning, sl_price, tp_price, grade
    """
    if not _PD_OK or df is None or len(df) < 20:
        return _wait(pair, "unknown", "none")

    if weekday is None:
        weekday = datetime.now(timezone.utc).weekday()

    if weekday >= 5:   # Saturday / Sunday — no playbook
        return _wait(pair, _WEEKDAY_NAMES.get(weekday, "weekend"), "none")

    playbook = _PLAYBOOK_NAMES[weekday]

    # Self-backtest gate
    if not _backtest_gate(df, playbook):
        return _wait(pair, _WEEKDAY_NAMES[weekday], playbook)

    try:
        if weekday == 0: return _monday_range(df, pair)
        if weekday == 1: return _tuesday_breakout(df, pair)
        if weekday == 2: return _wednesday_continuation(df, pair)
        if weekday == 3: return _thursday_reversal(df, pair)
        if weekday == 4: return _friday_fakeout(df, pair)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("[day_structure] %s error: %s", pair, e)

    return _wait(pair, _WEEKDAY_NAMES[weekday], playbook)


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC — multi-pair scan
# ══════════════════════════════════════════════════════════════════════════════

def scan_all_pairs(pair_data: "dict[str, pd.DataFrame]",
                   weekday: Optional[int] = None) -> "dict[str, dict]":
    """
    Loop over all supplied pairs, run analyze_day_structure for each.

    Args:
        pair_data : {pair_label: OHLCV_DataFrame}
        weekday   : 0-4 or None (auto from UTC clock)

    Returns:
        {pair_label: result_dict}  — pairs that return WAIT are included
        so callers can inspect but should skip signal generation for them.
    """
    if weekday is None:
        weekday = datetime.now(timezone.utc).weekday()
    return {pair: analyze_day_structure(df, pair, weekday)
            for pair, df in pair_data.items()}


# ══════════════════════════════════════════════════════════════════════════════
#  DATA FETCH — live broker WS → yfinance fallback  (for signals.py vote)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_df(pair: str, is_otc: bool) -> "Optional[pd.DataFrame]":
    if not _PD_OK:
        return None
    # OTC: real-time broker candles
    if is_otc:
        try:
            from otc_realtime_bridge import get_otc_df as _rt
            df = _rt(pair, "5m", count=400)
            if df is not None and len(df) >= 30:
                return df
        except Exception:
            pass
        try:
            from otc_feed import get_otc_df as _otc
            df = _otc(pair, "5m", count=400)
            if df is not None and len(df) >= 30:
                return df
        except Exception:
            pass
    # yfinance fallback
    try:
        import yfinance as yf
        from live_prices import yf_ticker
        ticker = yf_ticker(pair)
        if not ticker:
            return None
        df = yf.download(ticker, period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return None
        return df.tail(400).reset_index(drop=True)
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNALS.PY INTEGRATION ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def day_structure_vote(pair: str, is_otc: bool = False) -> Optional[dict]:
    """
    Called by signals.py as one additional weighted vote.
    Returns the analyze_day_structure result dict, or None on failure.
    Uses an 18-second cache — same TTL as all other engines.
    """
    if not _PD_OK:
        return None

    now    = time.time()
    cached = _CACHE.get(pair)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    try:
        df = _fetch_df(pair, is_otc)
        if df is None:
            _CACHE[pair] = (now, None)
            return None
        weekday = datetime.now(timezone.utc).weekday()
        result  = analyze_day_structure(df, pair, weekday)
        _CACHE[pair] = (now, result)
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("[day_structure_vote] %s: %s", pair, e)
        _CACHE[pair] = (now, None)
        return None
