"""MULTI-TIMEFRAME LIQUIDITY REVERSE ZONE ENGINE
================================================
SUPREME PRO AI BOT — Institutional-grade liquidity analysis.

Replicates the core logic of QX Expert Non-Reprint and Fx Expert Imtiazz 4.0 Pro
liquidity zone identification across ALL timeframes from smallest to largest:

    Timeframe stack (smallest → largest):
    1m → 5m → 15m → 30m → 1h → 4h → 1D

For BINARY 1-MIN signals: sub-candle 5s/15s/30s zones approximated
from 1m candle internal structure (wicks, body midpoints, pivot math).

WHAT THIS ENGINE DOES
─────────────────────
1. For each TF in the stack, identify:
   • Swing high pools (where stop-losses cluster above)
   • Swing low pools (where stop-losses cluster below)
   • Order blocks (last opposing candle before a strong move)
   • Fair Value Gaps (3-bar imbalances the market revisits)
   • BOS / CHoCH structure breaks

2. Find CONFLUENCE ZONES where 3+ timeframes agree:
   • A "liquidity reverse zone UP" = multiple TFs show untested
     swing low pools + FVG below price + OB below price
   • A "liquidity reverse zone DOWN" = multiple TFs show untested
     swing high pools + FVG above price + OB above price

3. Score each zone 0-100 (liq_score):
   • +15 per TF that confirms the zone direction
   • +20 if a liquidity sweep just happened on ANY TF
   • +10 for each OB that price is currently retesting
   • +10 for each active FVG below/above price
   • +5 for BOS in zone direction on any TF

4. Grade: SUPREME (85+), ELITE (70+), STRONG (55+), VALID (40+)

Public API
──────────
    mtf_liquidity_analyze(pair, direction, is_otc=False) -> dict | None
    {
        'direction':      'BUY' | 'SELL',
        'liq_score':      int 0-100,
        'grade':          'SUPREME' | 'ELITE' | 'STRONG' | 'VALID',
        'tf_agree':       list[str],       # TFs that confirmed
        'tf_count':       int,             # how many TFs agreed
        'sweep_tf':       str | None,      # TF where sweep detected
        'zone_high':      float,           # zone resistance/ceiling
        'zone_low':       float,           # zone support/floor
        'notes':          list[str],       # human-readable summary
        'sub_candle_zones': list[dict],    # 5s/15s/30s zones (binary only)
    }
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:
    print(f"[multi_tf_liquidity] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[tuple[str, str, bool], tuple[float, Optional[dict]]] = {}
_TTL = 60.0

_TF_STACK = [
    ("1m",  "5d",   "1 MIN"),
    ("5m",  "7d",   "5 MIN"),
    ("15m", "10d",  "15 MIN"),
    ("30m", "20d",  "30 MIN"),
    ("1h",  "30d",  "1H"),
    ("4h",  "60d",  "4H"),
    ("1d",  "365d", "1D"),
]

_PIVOT_W = {
    "1m":  2,
    "5m":  3,
    "15m": 3,
    "30m": 3,
    "1h":  3,
    "4h":  4,
    "1d":  4,
}


def _flatten(df):
    if hasattr(df.columns, "get_level_values"):
        df.columns = [
            str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
            for c in df.columns
        ]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df


def _fetch(ticker: str, interval: str, period: str):
    if not _OK or yf is None:
        return None
    try:
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 20:
            return None
        df = _flatten(df)
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return None
        return df.tail(200).copy()
    except Exception as e:
        print(f"[multi_tf_liquidity] fetch {ticker} {interval}: {e}")
        return None


def _swing_highs(df, w: int = 3):
    h = df["high"].astype(float).tolist()
    n = len(h)
    out = []
    for i in range(w, n - w):
        seg = h[i - w: i + w + 1]
        if h[i] == max(seg) and h[i] > h[i - 1]:
            out.append(h[i])
    return out


def _swing_lows(df, w: int = 3):
    l = df["low"].astype(float).tolist()
    n = len(l)
    out = []
    for i in range(w, n - w):
        seg = l[i - w: i + w + 1]
        if l[i] == min(seg) and l[i] < l[i - 1]:
            out.append(l[i])
    return out


def _atr(df, period: int = 14) -> float:
    try:
        h = df["high"].astype(float)
        lo = df["low"].astype(float)
        c = df["close"].astype(float)
        prev = c.shift(1)
        tr = (h - lo).combine((h - prev).abs(), max).combine((lo - prev).abs(), max)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0


def _order_block(df, direction: str) -> Optional[tuple[float, float]]:
    n = len(df)
    if n < 8:
        return None
    o = df["open"].astype(float).tolist()
    c = df["close"].astype(float).tolist()
    h = df["high"].astype(float).tolist()
    l = df["low"].astype(float).tolist()
    for i in range(n - 4, max(0, n - 40), -1):
        if direction == "bull":
            if c[i] < o[i] and all(c[j] > o[j] for j in range(i + 1, min(i + 4, n))):
                return (l[i], h[i])
        else:
            if c[i] > o[i] and all(c[j] < o[j] for j in range(i + 1, min(i + 4, n))):
                return (l[i], h[i])
    return None


def _fvg(df, direction: str) -> Optional[tuple[float, float]]:
    n = len(df)
    if n < 4:
        return None
    h = df["high"].astype(float).tolist()
    l = df["low"].astype(float).tolist()
    for i in range(n - 1, 2, -1):
        if direction == "bull":
            if h[i - 2] < l[i]:
                return (h[i - 2], l[i])
        else:
            if l[i - 2] > h[i]:
                return (h[i], l[i - 2])
    return None


def _bos(df, direction: str) -> bool:
    try:
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        recent_hi = float(h.iloc[-20:-1].max())
        recent_lo = float(l.iloc[-20:-1].min())
        last_close = float(c.iloc[-1])
        if direction == "bull" and last_close > recent_hi:
            return True
        if direction == "bear" and last_close < recent_lo:
            return True
    except Exception:
        pass
    return False


def _sweep(df, direction: str) -> bool:
    """Detect if recent candle swept a swing high/low then closed back."""
    if len(df) < 5:
        return False
    try:
        h = df["high"].astype(float).tolist()
        l = df["low"].astype(float).tolist()
        c = df["close"].astype(float).tolist()
        o = df["open"].astype(float).tolist()
        w = 3
        n = len(df)
        swing_highs = _swing_highs(df, w)
        swing_lows = _swing_lows(df, w)
        last_h = h[-1]; last_l = l[-1]; last_c = c[-1]; last_o = o[-1]
        if direction == "bull":
            for sl in swing_lows[-5:]:
                if last_l < sl and last_c > sl and last_c > last_o:
                    return True
        else:
            for sh in swing_highs[-5:]:
                if last_h > sh and last_c < sh and last_c < last_o:
                    return True
    except Exception:
        pass
    return False


def _sub_candle_zones(df_1m, direction: str, current_price: float, atr: float):
    """
    Approximate 5s / 15s / 30s liquidity zones from 1m candle internal structure.
    Each 1m candle contains ~12 5-second candles. We model their micro-structure
    by analysing the OHLC wicks and body midpoints of the most recent 3-5 bars.

    Returns a list of zone dicts: {label, low, high, type, score}
    """
    zones = []
    if df_1m is None or len(df_1m) < 5:
        return zones

    try:
        bars = df_1m.tail(5)
        for i, (idx, row) in enumerate(bars.iterrows()):
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            body_hi = max(o, c)
            body_lo = min(o, c)
            wick_up = h - body_hi
            wick_dn = body_lo - l
            bar_age = 4 - i  # 0 = most recent, 4 = oldest

            if direction == "bull":
                if wick_dn > 0.3 * (h - l) and body_lo > l:
                    score = max(10, 40 - bar_age * 6)
                    zones.append({
                        "label":  "30s" if bar_age == 0 else ("15s" if bar_age == 1 else "5s"),
                        "low":    l,
                        "high":   body_lo,
                        "type":   "wick_support",
                        "score":  score,
                    })
                if l < current_price and body_lo < current_price:
                    score = max(8, 35 - bar_age * 5)
                    zones.append({
                        "label":  "30s" if bar_age == 0 else ("15s" if bar_age == 1 else "5s"),
                        "low":    body_lo - atr * 0.15,
                        "high":   body_lo,
                        "type":   "body_support",
                        "score":  score,
                    })
            else:
                if wick_up > 0.3 * (h - l) and body_hi < h:
                    score = max(10, 40 - bar_age * 6)
                    zones.append({
                        "label":  "30s" if bar_age == 0 else ("15s" if bar_age == 1 else "5s"),
                        "low":    body_hi,
                        "high":   h,
                        "type":   "wick_resistance",
                        "score":  score,
                    })
                if h > current_price and body_hi > current_price:
                    score = max(8, 35 - bar_age * 5)
                    zones.append({
                        "label":  "30s" if bar_age == 0 else ("15s" if bar_age == 1 else "5s"),
                        "low":    body_hi,
                        "high":   body_hi + atr * 0.15,
                        "type":   "body_resistance",
                        "score":  score,
                    })
    except Exception as e:
        print(f"[multi_tf_liquidity] sub_candle_zones error: {e}")

    zones.sort(key=lambda z: z["score"], reverse=True)
    return zones[:6]


def mtf_liquidity_analyze(
    pair: str,
    direction: str,
    is_otc: bool = False,
) -> Optional[dict]:
    """
    Run full multi-timeframe liquidity reverse zone analysis.
    direction: 'BUY' or 'SELL'
    Returns None if no valid zone found.
    """
    if not _OK:
        return None
    direction = (direction or "").upper()
    if direction not in {"BUY", "SELL"}:
        return None
    side = "bull" if direction == "BUY" else "bear"

    ticker = yf_ticker(pair)
    if not ticker:
        return None

    cache_key = (ticker, direction, is_otc)
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    liq_score = 0
    tf_agree: list[str] = []
    notes: list[str] = []
    sweep_tf: Optional[str] = None
    all_zone_highs: list[float] = []
    all_zone_lows: list[float] = []
    current_price = 0.0
    df_1m = None
    atr_1m = 0.0

    for interval, period, tf_label in _TF_STACK:
        df = _fetch(ticker, interval, period)
        if df is None or len(df) < 20:
            continue

        if interval == "1m":
            df_1m = df
            atr_1m = _atr(df, 14)

        try:
            current_price = float(df["close"].iloc[-1])
        except Exception:
            continue

        w = _PIVOT_W.get(interval, 3)
        swing_hi = _swing_highs(df, w)
        swing_lo = _swing_lows(df, w)

        tf_score = 0
        tf_voted = False

        if direction == "BUY":
            below_lows = [p for p in swing_lo if p < current_price]
            if below_lows:
                nearest_pool = max(below_lows)
                pool_dist = (current_price - nearest_pool) / max(1e-9, current_price)
                if pool_dist <= 0.005:
                    tf_score += 15
                    notes.append(f"{tf_label}: Price at swing-low pool {nearest_pool:.5g}")
                    tf_voted = True
                    all_zone_lows.append(nearest_pool)
                    if pool_dist <= 0.001:
                        tf_score += 5
        else:
            above_highs = [p for p in swing_hi if p > current_price]
            if above_highs:
                nearest_pool = min(above_highs)
                pool_dist = (nearest_pool - current_price) / max(1e-9, current_price)
                if pool_dist <= 0.005:
                    tf_score += 15
                    notes.append(f"{tf_label}: Price at swing-high pool {nearest_pool:.5g}")
                    tf_voted = True
                    all_zone_highs.append(nearest_pool)
                    if pool_dist <= 0.001:
                        tf_score += 5

        ob = _order_block(df, side)
        if ob is not None:
            ob_lo, ob_hi = ob
            if direction == "BUY" and ob_lo < current_price <= ob_hi + atr_1m:
                tf_score += 10
                notes.append(f"{tf_label}: Bullish OB at {ob_lo:.5g}–{ob_hi:.5g}")
                all_zone_lows.append(ob_lo)
                all_zone_highs.append(ob_hi)
                tf_voted = True
            elif direction == "SELL" and ob_lo <= current_price < ob_hi + atr_1m:
                tf_score += 10
                notes.append(f"{tf_label}: Bearish OB at {ob_lo:.5g}–{ob_hi:.5g}")
                all_zone_lows.append(ob_lo)
                all_zone_highs.append(ob_hi)
                tf_voted = True

        fvg = _fvg(df, side)
        if fvg is not None:
            fvg_lo, fvg_hi = fvg
            if direction == "BUY" and fvg_lo <= current_price <= fvg_hi * 1.003:
                tf_score += 10
                notes.append(f"{tf_label}: Bullish FVG {fvg_lo:.5g}–{fvg_hi:.5g}")
                all_zone_lows.append(fvg_lo)
                all_zone_highs.append(fvg_hi)
                tf_voted = True
            elif direction == "SELL" and fvg_lo * 0.997 <= current_price <= fvg_hi:
                tf_score += 10
                notes.append(f"{tf_label}: Bearish FVG {fvg_lo:.5g}–{fvg_hi:.5g}")
                all_zone_lows.append(fvg_lo)
                all_zone_highs.append(fvg_hi)
                tf_voted = True

        if _sweep(df, side):
            tf_score += 20
            sweep_tf = tf_label
            notes.append(f"{tf_label}: ⚡ LIQUIDITY SWEEP DETECTED")
            tf_voted = True

        if _bos(df, side):
            tf_score += 5
            tf_voted = True

        if tf_voted:
            tf_agree.append(tf_label)

        liq_score += tf_score

    liq_score = min(100, liq_score)

    if liq_score < 25 or len(tf_agree) < 2:
        _CACHE[cache_key] = (now, None)
        return None

    if liq_score >= 85:
        grade = "SUPREME"
    elif liq_score >= 70:
        grade = "ELITE"
    elif liq_score >= 55:
        grade = "STRONG"
    elif liq_score >= 40:
        grade = "VALID"
    else:
        _CACHE[cache_key] = (now, None)
        return None

    atr_buf = atr_1m if atr_1m > 0 else (current_price * 0.0005)
    if all_zone_lows:
        zone_low = min(all_zone_lows) - atr_buf * 0.3
    else:
        zone_low = current_price * 0.999
    if all_zone_highs:
        zone_high = max(all_zone_highs) + atr_buf * 0.3
    else:
        zone_high = current_price * 1.001

    sub_zones = []
    if df_1m is not None and atr_1m > 0:
        sub_zones = _sub_candle_zones(df_1m, side, current_price, atr_1m)

    result = {
        "direction":        direction,
        "liq_score":        liq_score,
        "grade":            grade,
        "tf_agree":         tf_agree,
        "tf_count":         len(tf_agree),
        "sweep_tf":         sweep_tf,
        "zone_high":        zone_high,
        "zone_low":         zone_low,
        "notes":            notes[:6],
        "sub_candle_zones": sub_zones,
        "current_price":    current_price,
    }
    _CACHE[cache_key] = (now, result)
    return result
