"""FINORIX ANALYSIS ENGINE
==========================
Silent multi-system analysis layer. NEVER modifies signal text.
NEVER sends messages. NEVER touches bot structure.
Feeds pure structured data to the signals.py decision layer only.

Five sub-systems:
  1. MTF Trend Strength Detector   — EMA 9/21/50, HH/HL structure, S/R slope
  2. S/R Zone Calculator           — fractal zones, touch confluence, ATR-scaled
  3. Liquidity & Market Structure  — FVGs, swing hunt levels, reversal prob
  4. Non-Martingale Validator      — zone touch, trend alignment, R:R ≥ 1:2
  5. MTF Forex Extension           — 1h/4h/1d/1w confluence for forex pairs

Public entry: `finorix_analyse(pair, is_otc, tf_label) → dict | None`

Return shape (internal only):
{
    "trend_direction":    "BUY" | "SELL" | "NEUTRAL",
    "trend_strength":     0-100,
    "tf_alignment_score": 0-100,
    "zones":              list[dict],           # {high, low, strength, touches}
    "nearest_zone":       dict | None,
    "liquidity_flow":     "BUY" | "SELL" | "NEUTRAL",
    "next_untested":      float | None,
    "reversal_prob":      0-100,
    "signal_valid":       bool,
    "confidence_boost":   int,                  # signed — signals.py adds this
    "rejection_reason":   str | None,
    "forex_tf_score":     0-100,                # 0 if not forex
    "macro_confluence":   bool,
    "direction":          "BUY" | "SELL" | "WAIT",  # alias for vote system
    "grade":              "A+++" | "A++" | "A+" | "A" | "B" | "C",
    "confidence":         0-100,
}
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

try:
    import numpy as np
    import pandas as pd
    _PD_OK = True
except Exception:
    np = None   # type: ignore
    pd = None   # type: ignore
    _PD_OK = False

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_ZONE_CACHE: dict[str, tuple[float, list]] = {}
_TTL      = 18.0    # seconds — aligned with other FINORIX engines
_ZONE_TTL = 60.0    # zones recalculate less often (expensive)

# ── Timeframe map → yfinance interval + period ────────────────────────────────
_TF_MAP: dict[str, tuple[str, str]] = {
    "1m":  ("1m",  "1d"),
    "3m":  ("2m",  "2d"),
    "5m":  ("5m",  "5d"),
    "15m": ("15m", "5d"),
    "30m": ("30m", "10d"),
    "1h":  ("1h",  "30d"),
    "4h":  ("1h",  "60d"),   # yfinance has no 4h; use 1h + resample
    "1d":  ("1d",  "180d"),
    "1w":  ("1wk", "2y"),
}

# ── Forex / OTC classification ─────────────────────────────────────────────────
_FOREX_KEYWORDS = ("USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD",
                   "XAU", "XAG", "GOLD", "SILVER", "DXY", "OIL")
_CRYPTO_KEYWORDS = ("BTC", "ETH", "SOL", "BNB", "XRP", "LTC",
                    "USDT", "AVAX", "DOT", "LINK")


def _is_forex(pair: str) -> bool:
    p = pair.upper()
    return any(k in p for k in _FOREX_KEYWORDS) and not any(k in p for k in _CRYPTO_KEYWORDS)


def _is_crypto(pair: str) -> bool:
    return any(k in pair.upper() for k in _CRYPTO_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def _fetch(pair: str, interval: str, period: str) -> "Optional[pd.DataFrame]":
    """Download OHLCV, normalise column names, return clean DataFrame."""
    if not _PD_OK:
        return None
    try:
        import yfinance as yf
        from live_prices import yf_ticker
        ticker = yf_ticker(pair)
        if not ticker:
            return None
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 20:
            return None
        # Flatten MultiIndex columns (multi-ticker downloads)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        needed = {"open", "high", "low", "close"}
        if not needed.issubset(df.columns):
            return None
        df = df.dropna(subset=list(needed))
        df = df.reset_index(drop=True)
        return df
    except Exception as e:
        log.debug("[finorix_ae] fetch %s %s: %s", pair, interval, e)
        return None


def _fetch_otc(pair: str) -> "Optional[pd.DataFrame]":
    if not _PD_OK:
        return None
    try:
        from otc_realtime_bridge import get_otc_df as _rt
        df = _rt(pair, "5m", count=300)
        if df is not None and len(df) >= 30:
            return df
    except Exception:
        pass
    try:
        from otc_feed import get_otc_df as _otc
        df = _otc(pair, "5m", count=300)
        if df is not None and len(df) >= 30:
            return df
    except Exception:
        pass
    return None


def _load_df(pair: str, tf: str, is_otc: bool) -> "Optional[pd.DataFrame]":
    if is_otc:
        df = _fetch_otc(pair)
        if df is not None:
            return df
    iv, per = _TF_MAP.get(tf, ("5m", "5d"))
    return _fetch(pair, iv, per)


def _resample_4h(df: "pd.DataFrame") -> "pd.DataFrame":
    """Resample 1h OHLCV to 4h bars (yfinance has no native 4h)."""
    if "Datetime" in df.columns:
        df = df.set_index("Datetime")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    r = df.resample("4h").agg({"open": "first", "high": "max",
                                "low": "min", "close": "last"})
    if "volume" in df.columns:
        r["volume"] = df["volume"].resample("4h").sum()
    return r.dropna().reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED MATHS (pure price-action, no indicator wrappers)
# ══════════════════════════════════════════════════════════════════════════════

def _ema(s: "pd.Series", p: int) -> "pd.Series":
    return s.ewm(span=p, adjust=False).mean()


def _atr(df: "pd.DataFrame", p: int = 14) -> "pd.Series":
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr = pd.concat([
        hi - lo,
        (hi - cl.shift()).abs(),
        (lo - cl.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def _pivot_highs(hi: "pd.Series", order: int = 3) -> list[float]:
    n = len(hi)
    out: list[float] = []
    for i in range(order, n - order):
        win = hi.iloc[i - order: i + order + 1]
        if float(hi.iloc[i]) >= float(win.max()) - 1e-9:
            out.append(float(hi.iloc[i]))
    return out


def _pivot_lows(lo: "pd.Series", order: int = 3) -> list[float]:
    n = len(lo)
    out: list[float] = []
    for i in range(order, n - order):
        win = lo.iloc[i - order: i + order + 1]
        if float(lo.iloc[i]) <= float(win.min()) + 1e-9:
            out.append(float(lo.iloc[i]))
    return out


def _hh_hl(df: "pd.DataFrame") -> tuple[bool, bool]:
    ph = _pivot_highs(df["high"].tail(50))
    pl = _pivot_lows(df["low"].tail(50))
    hh = len(ph) >= 2 and ph[-1] > ph[-2]
    hl = len(pl) >= 2 and pl[-1] > pl[-2]
    return hh, hl


def _lh_ll(df: "pd.DataFrame") -> tuple[bool, bool]:
    ph = _pivot_highs(df["high"].tail(50))
    pl = _pivot_lows(df["low"].tail(50))
    lh = len(ph) >= 2 and ph[-1] < ph[-2]
    ll = len(pl) >= 2 and pl[-1] < pl[-2]
    return lh, ll


def _sr_slope(levels: list[float]) -> float:
    """Simple linear regression slope over the last N levels (normalised)."""
    if len(levels) < 2:
        return 0.0
    n = len(levels)
    x = list(range(n))
    mx, my = sum(x) / n, sum(levels) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, levels))
    den = sum((xi - mx) ** 2 for xi in x) + 1e-10
    slope = num / den
    mid = my if my != 0 else 1e-10
    return slope / mid   # normalise to price scale


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM 1 — MTF TREND STRENGTH DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _analyse_trend(df: "pd.DataFrame") -> dict:
    """
    EMA 9/21/50 alignment + HH/HL structure + S/R slope.
    Returns: direction, strength (0-100), tf_contribution (0-1).
    """
    if df is None or len(df) < 55:
        return {"direction": "NEUTRAL", "strength": 0, "score": 0.0}

    c = df["close"]
    e9  = _ema(c, 9)
    e21 = _ema(c, 21)
    e50 = _ema(c, 50)

    # EMA stack
    bull_stack = float(e9.iloc[-1]) > float(e21.iloc[-1]) > float(e50.iloc[-1])
    bear_stack = float(e9.iloc[-1]) < float(e21.iloc[-1]) < float(e50.iloc[-1])

    # EMA slope: last 5 bars
    e9_slope  = (float(e9.iloc[-1])  - float(e9.iloc[-6]))  / (abs(float(e9.iloc[-6]))  + 1e-10)
    e21_slope = (float(e21.iloc[-1]) - float(e21.iloc[-6])) / (abs(float(e21.iloc[-6])) + 1e-10)

    # Candle structure
    hh, hl = _hh_hl(df)
    lh, ll = _lh_ll(df)

    # S/R slope (using pivot highs as resistance series)
    ph = _pivot_highs(df["high"].tail(50))
    pl = _pivot_lows(df["low"].tail(50))
    res_slope = _sr_slope(ph[-5:] if len(ph) >= 5 else ph)
    sup_slope = _sr_slope(pl[-5:] if len(pl) >= 5 else pl)

    # Score bullish factors
    b_score = sum([
        0.25 if bull_stack else 0,
        0.15 if e9_slope > 0.0003 else 0,
        0.15 if e21_slope > 0.0001 else 0,
        0.20 if (hh and hl) else (0.08 if hh else 0),
        0.15 if res_slope > 0 else 0,
        0.10 if sup_slope > 0 else 0,
    ])
    # Score bearish factors
    s_score = sum([
        0.25 if bear_stack else 0,
        0.15 if e9_slope < -0.0003 else 0,
        0.15 if e21_slope < -0.0001 else 0,
        0.20 if (lh and ll) else (0.08 if ll else 0),
        0.15 if res_slope < 0 else 0,
        0.10 if sup_slope < 0 else 0,
    ])

    if b_score > s_score and b_score >= 0.30:
        direction = "BUY"
        strength  = round(min(100, b_score * 110))
    elif s_score > b_score and s_score >= 0.30:
        direction = "SELL"
        strength  = round(min(100, s_score * 110))
    else:
        direction = "NEUTRAL"
        strength  = round(max(b_score, s_score) * 100)

    return {
        "direction": direction,
        "strength":  strength,
        "score":     max(b_score, s_score),
    }


def _mtf_trend(pair: str, is_otc: bool) -> dict:
    """Run trend analysis on up to 4 timeframes, return consensus."""
    tfs = ["1m", "5m", "15m", "1h"] if not _is_forex(pair) else ["5m", "1h", "4h", "1d"]
    results: list[dict] = []

    for tf in tfs:
        df = _load_df(pair, tf, is_otc and tf in ("1m", "5m"))
        if df is None:
            continue
        if tf == "4h":
            df = _resample_4h(df)
        r = _analyse_trend(df)
        r["tf"] = tf
        results.append(r)

    if not results:
        return {"trend_direction": "NEUTRAL", "trend_strength": 0,
                "tf_alignment_score": 0, "tf_results": []}

    buy_n  = sum(1 for r in results if r["direction"] == "BUY")
    sell_n = sum(1 for r in results if r["direction"] == "SELL")
    total  = len(results)

    if buy_n > sell_n:
        td      = "BUY"
        agree_n = buy_n
    elif sell_n > buy_n:
        td      = "SELL"
        agree_n = sell_n
    else:
        td      = "NEUTRAL"
        agree_n = 0

    avg_strength    = sum(r["strength"] for r in results) / max(total, 1)
    alignment_score = round(agree_n / max(total, 1) * 100)
    trend_strength  = round(avg_strength * (alignment_score / 100))

    return {
        "trend_direction":    td,
        "trend_strength":     int(trend_strength),
        "tf_alignment_score": alignment_score,
        "tf_results":         results,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM 2 — INTELLIGENT S/R ZONE CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

def _zone_key(pair: str) -> str:
    return f"zones:{pair}"


def _calc_zones(df: "pd.DataFrame", pair: str) -> list[dict]:
    """
    Extract fractal S/R zones from last 20-50 candles.
    Each zone has a width (ATR-scaled), confluence count, and rejection count.
    """
    if df is None or len(df) < 25:
        return []

    atr_v   = float(_atr(df, 14).iloc[-1]) if not _atr(df, 14).isna().all() else 0
    lookback = min(50, len(df) - 5)
    sub      = df.tail(lookback)

    ph = _pivot_highs(sub["high"], order=2)
    pl = _pivot_lows(sub["low"],   order=2)
    raw_levels = ph + pl

    if not raw_levels:
        return []

    # Cluster nearby levels into zones (within 1.5 × ATR)
    raw_levels.sort()
    zones: list[dict] = []
    used   = [False] * len(raw_levels)
    w      = max(atr_v * 1.5, float(df["close"].iloc[-1]) * 0.0005)

    for i, lvl in enumerate(raw_levels):
        if used[i]:
            continue
        cluster = [lvl]
        used[i] = True
        for j in range(i + 1, len(raw_levels)):
            if not used[j] and abs(raw_levels[j] - lvl) <= w:
                cluster.append(raw_levels[j])
                used[j] = True
        z_low  = min(cluster) - w * 0.3
        z_high = max(cluster) + w * 0.3
        zones.append({
            "high":     z_high,
            "low":      z_low,
            "mid":      (z_high + z_low) / 2,
            "touches":  len(cluster),
            "strength": 0,
            "reject_count": 0,
        })

    # Weight zones
    price = float(df["close"].iloc[-1])
    for z in zones:
        # More touches = stronger
        z["strength"] = min(100, z["touches"] * 20 + 20)
        # Count candle rejections AT this zone
        for _, row in sub.iterrows():
            rng  = float(row["high"]) - float(row["low"]) + 1e-10
            body = abs(float(row["close"]) - float(row["open"]))
            in_zone = z["low"] <= float(row["close"]) <= z["high"] or \
                      z["low"] <= float(row["open"])  <= z["high"]
            rejection = body / rng < 0.40   # wick-dominant candle
            if in_zone and rejection:
                z["reject_count"] += 1
        z["strength"] = min(100, z["strength"] + z["reject_count"] * 10)
        # Boost if volume-weighted (if volume available)
        if "volume" in sub.columns:
            zone_vol = sub.loc[
                (sub["close"] >= z["low"]) & (sub["close"] <= z["high"]), "volume"
            ].sum()
            avg_vol  = float(sub["volume"].mean()) + 1e-9
            if float(zone_vol) > avg_vol * 1.5:
                z["strength"] = min(100, z["strength"] + 15)
        z["distance"] = abs(price - z["mid"])

    zones.sort(key=lambda z: z["distance"])
    return zones[:12]   # keep 12 nearest


def _get_zones(pair: str, df: "pd.DataFrame") -> list[dict]:
    key = _zone_key(pair)
    now = time.time()
    cached = _ZONE_CACHE.get(key)
    if cached and (now - cached[0]) < _ZONE_TTL:
        return cached[1]
    zones = _calc_zones(df, pair)
    _ZONE_CACHE[key] = (now, zones)
    return zones


def _nearest_zone(zones: list[dict], price: float, direction: str) -> "Optional[dict]":
    """Nearest zone that is a valid origin for the given direction."""
    for z in sorted(zones, key=lambda z: z["distance"]):
        if direction == "BUY"  and price >= z["low"] - (z["high"] - z["low"]):
            return z
        if direction == "SELL" and price <= z["high"] + (z["high"] - z["low"]):
            return z
    return zones[0] if zones else None


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM 3 — LIQUIDITY & MARKET STRUCTURE ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

def _analyse_liquidity(df: "pd.DataFrame") -> dict:
    """
    Detect FVGs, swing hunt levels, and reversal probability.
    FVG = 3-bar gap: bar[i-1].high < bar[i+1].low (bullish) or inverse.
    """
    if df is None or len(df) < 15:
        return {"liquidity_flow": "NEUTRAL", "next_untested": None,
                "reversal_prob": 0, "fvg_count": 0, "fvg_direction": "NEUTRAL"}

    n     = len(df)
    price = float(df["close"].iloc[-1])
    atr_v = float(_atr(df, 14).iloc[-1]) if not _atr(df, 14).isna().all() else 0

    # FVG detection (last 30 bars)
    bull_fvgs: list[float] = []
    bear_fvgs: list[float] = []
    for i in range(1, min(30, n - 1)):
        idx = n - 1 - i
        if idx < 1 or idx + 1 >= n:
            continue
        lo_prev = float(df["high"].iloc[idx - 1])
        hi_next = float(df["low"].iloc[idx + 1])
        hi_prev = float(df["low"].iloc[idx - 1])
        lo_next = float(df["high"].iloc[idx + 1])
        if hi_next > lo_prev:                    # bullish FVG
            bull_fvgs.append((lo_prev + hi_next) / 2)
        if lo_next < hi_prev:                    # bearish FVG
            bear_fvgs.append((hi_prev + lo_next) / 2)

    # Recent swing highs/lows (liquidity pools — stop clusters)
    ph = _pivot_highs(df["high"].tail(40))
    pl = _pivot_lows(df["low"].tail(40))

    # Previous S becoming R (or vice-versa) — reversal zone
    reversal_signals = 0
    tol = atr_v * 1.2
    for p in pl:
        if abs(price - p) < tol and float(df["close"].iloc[-1]) < p:
            reversal_signals += 1   # support broken, now resistance
    for p in ph:
        if abs(price - p) < tol and float(df["close"].iloc[-1]) > p:
            reversal_signals += 1   # resistance broken, now support

    # Nearest untested FVG level
    all_fvgs = [(f, "BUY") for f in bull_fvgs] + [(f, "SELL") for f in bear_fvgs]
    all_fvgs.sort(key=lambda x: abs(x[0] - price))
    next_untested = all_fvgs[0][0] if all_fvgs else None
    fvg_dir       = all_fvgs[0][1] if all_fvgs else "NEUTRAL"

    # Liquidity flow direction (which side has more unmitigated levels?)
    above = sum(1 for p in ph if p > price) + sum(1 for f, _ in all_fvgs if f > price)
    below = sum(1 for p in pl if p < price) + sum(1 for f, _ in all_fvgs if f < price)

    if above > below:
        flow = "BUY"     # price will sweep upward pools
    elif below > above:
        flow = "SELL"    # price will sweep downward pools
    else:
        flow = "NEUTRAL"

    reversal_prob = min(100, reversal_signals * 25 + len(all_fvgs[:3]) * 8)

    return {
        "liquidity_flow":  flow,
        "next_untested":   next_untested,
        "reversal_prob":   reversal_prob,
        "fvg_count":       len(bull_fvgs) + len(bear_fvgs),
        "fvg_direction":   fvg_dir,
        "swing_highs":     ph[-3:],
        "swing_lows":      pl[-3:],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM 4 — NON-MARTINGALE SIGNAL VALIDATOR
# ══════════════════════════════════════════════════════════════════════════════

def _validate_signal(
    df: "pd.DataFrame",
    direction: str,
    trend_data: dict,
    zones: list[dict],
    liq_data: dict,
    min_strength: int = 60,
) -> dict:
    """
    Validate before any signal fires.
    Checks:
      • Trend alignment across ≥ 2 TFs
      • Zone confluence (entry must touch identified S/R)
      • Trend strength > min_strength
      • No fading candles at entry (candle body direction matches signal)
      • R:R ≥ 1:2 from zone to next untested level
    """
    reasons: list[str] = []
    boosts:  list[int] = []

    if df is None or len(df) < 10:
        return {"signal_valid": False, "confidence_boost": 0,
                "rejection_reason": "insufficient_data"}

    price     = float(df["close"].iloc[-1])
    atr_v     = float(_atr(df, 14).iloc[-1]) if not _atr(df, 14).isna().all() else 0
    tf_align  = trend_data.get("tf_alignment_score", 0)
    strength  = trend_data.get("trend_strength", 0)
    td        = trend_data.get("trend_direction", "NEUTRAL")

    # ── Check 1: trend alignment ≥ 2 TFs ──────────────────────
    tf_results = trend_data.get("tf_results", [])
    agree_tfs  = sum(1 for r in tf_results if r.get("direction") == direction)
    if agree_tfs < 2:
        reasons.append(f"only {agree_tfs} TF(s) align with {direction}")

    # ── Check 2: zone confluence ───────────────────────────────
    nearest = _nearest_zone(zones, price, direction) if zones else None
    at_zone  = False
    if nearest:
        zone_range = nearest["high"] - nearest["low"] + 1e-10
        at_zone    = abs(price - nearest["mid"]) < zone_range * 2
        if at_zone:
            boosts.append(8 if nearest.get("strength", 0) >= 60 else 4)
        else:
            reasons.append("price not at identified S/R zone")

    # ── Check 3: trend strength ────────────────────────────────
    if strength < min_strength:
        reasons.append(f"trend strength {strength}% < {min_strength}%")
    else:
        boosts.append(5 if strength >= 80 else 3)

    # ── Check 4: no fading candle at entry ────────────────────
    last = df.iloc[-1]
    body = float(last["close"]) - float(last["open"])
    rng  = float(last["high"]) - float(last["low"]) + 1e-10
    body_pct = abs(body) / rng
    bull_c   = body > 0
    fading   = (direction == "BUY" and not bull_c) or \
               (direction == "SELL" and bull_c)
    if fading and body_pct > 0.5:
        reasons.append("fading candle at entry (opposite strong body)")
    elif not fading and body_pct > 0.4:
        boosts.append(5)

    # ── Check 5: R:R ≥ 1:2 ────────────────────────────────────
    next_lvl = liq_data.get("next_untested")
    rr_ok    = False
    if nearest and next_lvl and atr_v > 0:
        sl_dist = abs(price - (nearest["low"] if direction == "BUY" else nearest["high"]))
        tp_dist = abs(price - next_lvl)
        sl_dist = max(sl_dist, atr_v * 0.5)
        rr      = tp_dist / (sl_dist + 1e-9)
        rr_ok   = rr >= 2.0
        if rr_ok:
            boosts.append(7)
        else:
            reasons.append(f"R:R {rr:.1f} < 2.0 minimum")

    # ── Aggregate ─────────────────────────────────────────────
    # Valid when: ≤ 1 soft reason AND at least 2 checks passed
    hard_blocks = [r for r in reasons if
                   "fading" in r or "only 0" in r or "only 1" in r]
    valid = len(hard_blocks) == 0 and agree_tfs >= 2 and strength >= min_strength

    total_boost  = sum(boosts) if valid else 0
    reject_str   = "; ".join(reasons) if reasons else None

    return {
        "signal_valid":      valid,
        "confidence_boost":  total_boost,
        "rejection_reason":  reject_str,
        "agree_tfs":         agree_tfs,
        "at_zone":           at_zone,
        "rr_ok":             rr_ok,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM 5 — MTF FOREX EXTENSION (1h / 4h / 1d / 1w)
# ══════════════════════════════════════════════════════════════════════════════

def _forex_mtf(pair: str, direction: str) -> dict:
    """
    For Forex pairs: check that 1h trend aligns with 1d+ macro structure.
    Macro levels are calculated from weekly/daily pivots.
    """
    if not _is_forex(pair):
        return {"forex_tf_score": 0, "macro_confluence": False}

    tfs   = [("1h", "30d"), ("1d", "180d"), ("1wk", "2y")]
    align = 0
    total = 0
    macro_zones: list[float] = []

    for iv, per in tfs:
        df = _fetch(pair, iv, per)
        if df is None:
            continue
        r = _analyse_trend(df)
        total += 1
        if r["direction"] == direction:
            align += 1
        # Collect macro pivots from higher TF
        if iv in ("1d", "1wk"):
            macro_zones += _pivot_highs(df["high"].tail(20))
            macro_zones += _pivot_lows(df["low"].tail(20))

    if total == 0:
        return {"forex_tf_score": 0, "macro_confluence": False}

    score = round(align / total * 100)

    # Macro confluence: price is near a weekly/daily level
    try:
        from live_prices import get_live_price
        price = get_live_price(pair) or 0
        if price and macro_zones:
            atr_guard = float(price) * 0.005
            near_macro = any(abs(float(price) - z) < atr_guard for z in macro_zones)
        else:
            near_macro = False
    except Exception:
        near_macro = False

    return {
        "forex_tf_score":  score,
        "macro_confluence": near_macro and score >= 50,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  GRADE MAPPER
# ══════════════════════════════════════════════════════════════════════════════

def _grade(conf: float, valid: bool, boost: int) -> str:
    if not valid:
        return "C"
    if conf >= 90 and boost >= 15: return "A+++"
    if conf >= 82 and boost >= 10: return "A++"
    if conf >= 74 and boost >= 5:  return "A+"
    if conf >= 65:                  return "A"
    if conf >= 55:                  return "B"
    return "C"


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def finorix_analyse(pair: str, is_otc: bool = False,
                    tf_label: str = "5m") -> "Optional[dict]":
    """
    Run all 5 analysis systems and return a unified result dict.

    Args:
        pair      : trading pair label e.g. "EUR/USD"
        is_otc    : True for OTC broker pairs
        tf_label  : timeframe hint from signals.py ("1m", "5m", etc.)

    Returns:
        Standardised dict (see module docstring), or None on failure.
        KEY: `confidence_boost` is SIGNED — signals.py adds it to confidence.
             `signal_valid` False means the validator blocked the signal.
             `direction` is the trend direction (BUY/SELL/WAIT).
    """
    if not _PD_OK:
        return None

    now    = time.time()
    cached = _CACHE.get(pair)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    try:
        # ── 1. Base dataframe (primary TF) ────────────────────
        tf_clean = tf_label.lower().replace("min", "m").replace(
            "minute", "m").replace(" ", "").replace("hour", "h")
        tf_key   = tf_clean if tf_clean in _TF_MAP else "5m"
        df       = _load_df(pair, tf_key, is_otc)
        if df is None or len(df) < 25:
            _CACHE[pair] = (now, None)
            return None

        # ── 2. MTF trend ──────────────────────────────────────
        trend_data = _mtf_trend(pair, is_otc)
        td         = trend_data["trend_direction"]
        strength   = trend_data["trend_strength"]
        align      = trend_data["tf_alignment_score"]

        # ── 3. S/R zones ──────────────────────────────────────
        zones   = _get_zones(pair, df)
        price   = float(df["close"].iloc[-1])
        nearest = _nearest_zone(zones, price, td) if zones else None

        # ── 4. Liquidity ─────────────────────────────────────
        liq = _analyse_liquidity(df)

        # ── 5. Non-MG validator ───────────────────────────────
        direction_for_val = td if td != "NEUTRAL" else "BUY"
        val = _validate_signal(df, direction_for_val, trend_data, zones, liq)

        # ── 6. Forex MTF extension ────────────────────────────
        fx_data = _forex_mtf(pair, td) if _is_forex(pair) else \
                  {"forex_tf_score": 0, "macro_confluence": False}

        # ── 7. Aggregate confidence ───────────────────────────
        base = 50
        base += round(strength * 0.30)
        base += round(align    * 0.20)
        if val["signal_valid"]:
            base += val["confidence_boost"]
        if fx_data["macro_confluence"]:
            base += 8
        if liq["reversal_prob"] > 50 and td != "NEUTRAL":
            base -= 5
        conf = max(0, min(100, base))

        # Final direction
        if td == "NEUTRAL" or strength < 40:
            final_dir = "WAIT"
        else:
            final_dir = td

        result = {
            # System 1
            "trend_direction":    td,
            "trend_strength":     strength,
            "tf_alignment_score": align,
            # System 2
            "zones":              zones,
            "nearest_zone":       nearest,
            # System 3
            "liquidity_flow":     liq["liquidity_flow"],
            "next_untested":      liq["next_untested"],
            "reversal_prob":      liq["reversal_prob"],
            "fvg_count":          liq["fvg_count"],
            # System 4
            "signal_valid":       val["signal_valid"],
            "confidence_boost":   val["confidence_boost"] if val["signal_valid"] else 0,
            "rejection_reason":   val["rejection_reason"],
            # System 5
            "forex_tf_score":     fx_data["forex_tf_score"],
            "macro_confluence":   fx_data["macro_confluence"],
            # Vote system aliases
            "direction":          final_dir,
            "grade":              _grade(conf, val["signal_valid"], val["confidence_boost"]),
            "confidence":         conf,
        }

        _CACHE[pair] = (now, result)
        return result

    except Exception as e:
        log.debug("[finorix_ae] %s error: %s", pair, e)
        _CACHE[pair] = (now, None)
        return None


# ── Initialisation hook (called once at bot startup if desired) ───────────────
def init_finorix_engine() -> None:
    """Optional warm-up. Pre-loads zone caches for the most active pairs."""
    if not _PD_OK:
        return
    warmup_pairs = ["EUR/USD", "GBP/USD", "XAU/USD", "USD/JPY", "BTC/USD"]
    for pair in warmup_pairs:
        try:
            finorix_analyse(pair, is_otc=False, tf_label="5m")
        except Exception:
            pass
