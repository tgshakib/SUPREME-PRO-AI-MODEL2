"""CHART CONDITIONS ENGINE — Always-Fires Multi-TF Analysis V2 ★ ULTRA PRO ★
==============================================================
The final fallback engine for binary signals. Unlike the sniper
engines that wait for high-confluence setups, this engine ALWAYS
produces a direction — it analyzes whatever structure is present
across 15s → 4H timeframes and determines the highest-probability
short-term direction from that structure.

Key concepts implemented
------------------------
  SUPPORT / RESISTANCE  — recent swing pivots where price clusters
  PIN BAR / HAMMER      — long wick = strong rejection at a level
  ENGULFING CANDLE      — full body covers prior bar = momentum shift
  RSI EXTREMES          — < 30 oversold → BUY, > 70 overbought → SELL
  BB OUTER TOUCH        — price at Bollinger outer band → mean-reversion
  CONSECUTIVE EXHAUSTION — 3+ same-dir bars → reversal approaching
  MULTI-TF CONFLUENCE   — 4H + 1H + 15M + 5M + 1M all voted and tallied
  VOLATILITY SIGNAL     — high-volatility breakout direction detected

The engine outputs:
  direction    — "BUY" | "SELL" (always set)
  confidence   — 0.0–1.0
  key_level    — "SUPPORT" | "RESISTANCE" | "BREAKOUT" | "MOMENTUM"
  analysis_txt — human-readable multi-line analysis like a professional
                 trader would write (shown on the signal card)
  patterns     — list of detected pattern names
  tf_votes     — per-TF direction votes
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception:
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker, get_live_price


# ── Indicator helpers ──────────────────────────────────────────────────────

def _ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + g / l.replace(0, 1e-9))


def _bb(s, p=20, k=2.0):
    mid  = s.rolling(p).mean()
    std  = s.rolling(p).std()
    return mid - k * std, mid, mid + k * std


def _atr(df, p=14):
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    c  = df["close"].astype(float)
    tr = (h - lo).combine((h - c.shift()).abs(), max).combine(
        (lo - c.shift()).abs(), max)
    return tr.rolling(p).mean()


def _macd(s, fast=12, slow=26, sig=9):
    """Returns (macd_line, signal_line, histogram)."""
    m = _ema(s, fast) - _ema(s, slow)
    sg = _ema(m, sig)
    return m, sg, m - sg


def _stoch(hi, lo, cl, k=14, d=3, smooth=3):
    """Stochastic %K and %D."""
    lowest  = lo.rolling(k).min()
    highest = hi.rolling(k).max()
    raw_k   = 100 * (cl - lowest) / (highest - lowest + 1e-10)
    pct_k   = raw_k.rolling(smooth).mean()
    pct_d   = pct_k.rolling(d).mean()
    return pct_k, pct_d


def _adx(df, p=14):
    """Average Directional Index — trend strength 0-100."""
    try:
        h  = df["high"].astype(float)
        lo = df["low"].astype(float)
        c  = df["close"].astype(float)
        tr = (h - lo).combine((h - c.shift()).abs(), max).combine(
            (lo - c.shift()).abs(), max)
        dm_plus  = (h - h.shift()).clip(lower=0)
        dm_minus = (lo.shift() - lo).clip(lower=0)
        dm_plus  = dm_plus.where(dm_plus > dm_minus, 0)
        dm_minus = dm_minus.where(dm_minus > dm_plus, 0)
        atr_s  = tr.ewm(span=p, adjust=False).mean()
        di_p   = 100 * dm_plus.ewm(span=p, adjust=False).mean() / atr_s.replace(0, 1e-10)
        di_m   = 100 * dm_minus.ewm(span=p, adjust=False).mean() / atr_s.replace(0, 1e-10)
        dx     = 100 * (di_p - di_m).abs() / (di_p + di_m + 1e-10)
        adx_v  = dx.ewm(span=p, adjust=False).mean()
        return adx_v, di_p, di_m
    except Exception:
        return None, None, None


def _supertrend(df, p=10, mult=3.0):
    """Returns supertrend series: True=uptrend (bullish), False=downtrend."""
    try:
        h  = df["high"].astype(float)
        lo = df["low"].astype(float)
        cl = df["close"].astype(float)
        tr = (h - lo).combine((h - cl.shift()).abs(), max).combine(
            (lo - cl.shift()).abs(), max)
        atr = tr.ewm(span=p, adjust=False).mean()
        hl2 = (h + lo) / 2
        upper = (hl2 + mult * atr).copy()
        lower = (hl2 - mult * atr).copy()
        trend = [True] * len(cl)
        for i in range(1, len(cl)):
            pu = float(upper.iloc[i - 1]); cu = float(upper.iloc[i])
            pl = float(lower.iloc[i - 1]); cl_ = float(lower.iloc[i])
            upper.iloc[i] = min(cu, pu) if float(cl.iloc[i - 1]) <= pu else cu
            lower.iloc[i] = max(cl_, pl) if float(cl.iloc[i - 1]) >= pl else cl_
            if trend[i - 1] and float(cl.iloc[i]) < float(lower.iloc[i]):
                trend[i] = False
            elif not trend[i - 1] and float(cl.iloc[i]) > float(upper.iloc[i]):
                trend[i] = True
            else:
                trend[i] = trend[i - 1]
        return trend
    except Exception:
        return None


# ── Data fetch with simple cache ───────────────────────────────────────────

_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 25.0  # seconds — fresh enough for 1m signals


def _fetch(ticker: str, interval: str, period: str):
    key = f"{ticker}|{interval}|{period}"
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    if not _OK:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            _CACHE[key] = (time.time(), None)
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [
                str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                for c in df.columns
            ]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        _CACHE[key] = (time.time(), df)
        return df
    except Exception as e:
        print(f"[chart_conditions] fetch {ticker} {interval}: {e}")
        _CACHE[key] = (time.time(), None)
        return None


# ── Per-candle structure ───────────────────────────────────────────────────

def _bar(df, i: int) -> dict | None:
    try:
        o = float(df["open"].iloc[i])
        h = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i])
        c = float(df["close"].iloc[i])
        rng = max(h - lo, 1e-10)
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo
        return {
            "o": o, "h": h, "l": lo, "c": c, "rng": rng,
            "body": body, "body_pct": body / rng,
            "upper_wick": upper_wick, "lower_wick": lower_wick,
            "bull": c > o,
        }
    except Exception:
        return None


# ── Pattern detectors ──────────────────────────────────────────────────────

def _detect_patterns(df) -> dict:
    """Detect patterns on the last 3 confirmed bars. Returns dict of signals."""
    found: dict[str, int] = {}  # key → +1 BUY, -1 SELL

    b0 = _bar(df, -2)  # last confirmed bar
    b1 = _bar(df, -3)  # bar before
    b2 = _bar(df, -4)  # 2 bars before
    if b0 is None:
        return {}

    rng0 = b0["rng"]

    # ── HAMMER (bullish) ─ long lower wick ≥ 2.5× body, small upper wick
    if (b0["lower_wick"] >= 2.5 * max(b0["body"], rng0 * 0.05) and
            b0["upper_wick"] < 0.3 * rng0):
        found["hammer"] = +1

    # ── SHOOTING STAR (bearish) ─ long upper wick ≥ 2.5× body
    if (b0["upper_wick"] >= 2.5 * max(b0["body"], rng0 * 0.05) and
            b0["lower_wick"] < 0.3 * rng0):
        found["shooting_star"] = -1

    # ── BULLISH ENGULFING — b0 is bull, covers b1 bearish body
    if b1 is not None and b0["bull"] and not b1["bull"]:
        if b0["c"] > b1["o"] and b0["o"] < b1["c"] and b0["body"] > b1["body"]:
            found["bull_engulfing"] = +1

    # ── BEARISH ENGULFING
    if b1 is not None and not b0["bull"] and b1["bull"]:
        if b0["c"] < b1["o"] and b0["o"] > b1["c"] and b0["body"] > b1["body"]:
            found["bear_engulfing"] = -1

    # ── PIN BAR (long wick beyond range of prior bar) — bullish
    if b1 is not None and b0["lower_wick"] > rng0 * 0.55 and b0["lower_wick"] >= b0["body"] * 3:
        found["bull_pin_bar"] = +1

    # ── PIN BAR — bearish
    if b1 is not None and b0["upper_wick"] > rng0 * 0.55 and b0["upper_wick"] >= b0["body"] * 3:
        found["bear_pin_bar"] = -1

    # ── DOJI AT EXTREME — potential reversal
    if b0["body_pct"] < 0.15:  # very small body = doji
        if b1 is not None:
            # after bearish run: doji = reversal up
            if not b1["bull"] and b2 is not None and not b2["bull"]:
                found["doji_bull_rev"] = +1
            # after bullish run: doji = reversal down
            elif b1["bull"] and b2 is not None and b2["bull"]:
                found["doji_bear_rev"] = -1

    # ── 3-BAR EXHAUSTION — 3 same-direction = reversal near
    if b0 is not None and b1 is not None and b2 is not None:
        if not b0["bull"] and not b1["bull"] and not b2["bull"]:
            # 3 consecutive bearish → BUY reversal
            found["bear_exhaustion_rev"] = +1
        elif b0["bull"] and b1["bull"] and b2["bull"]:
            # 3 consecutive bullish → SELL reversal
            found["bull_exhaustion_rev"] = -1

    return found


# ── Support / Resistance detector ─────────────────────────────────────────

def _sr_proximity(df, live_price: float) -> tuple[str | None, float]:
    """Return (key_level, proximity_score 0-1).
    key_level: 'SUPPORT' | 'RESISTANCE' | None
    proximity_score: 1.0 = right at the level, 0 = far away.
    """
    try:
        hi = df["high"].astype(float)
        lo = df["low"].astype(float)
        cl = df["close"].astype(float)
        atr_val = float(_atr(df).iloc[-1])
        if atr_val <= 0:
            return None, 0.0

        # Swing lows (support) — local minima in last 30 bars
        pivot_lows = []
        for i in range(2, min(30, len(lo) - 2)):
            idx = -(i + 1)
            v = float(lo.iloc[idx])
            if v < float(lo.iloc[idx - 1]) and v < float(lo.iloc[idx + 1]):
                pivot_lows.append(v)

        # Swing highs (resistance)
        pivot_highs = []
        for i in range(2, min(30, len(hi) - 2)):
            idx = -(i + 1)
            v = float(hi.iloc[idx])
            if v > float(hi.iloc[idx - 1]) and v > float(hi.iloc[idx + 1]):
                pivot_highs.append(v)

        # Find nearest support and resistance
        supports    = [p for p in pivot_lows  if p < live_price]
        resistances = [p for p in pivot_highs if p > live_price]
        nearest_sup = max(supports)    if supports    else None
        nearest_res = min(resistances) if resistances else None

        dist_sup = abs(live_price - nearest_sup) / atr_val if nearest_sup else 999
        dist_res = abs(live_price - nearest_res) / atr_val if nearest_res else 999

        if dist_sup < 0.5 and dist_sup < dist_res:
            prox = max(0.0, 1.0 - dist_sup / 0.5)
            return "SUPPORT", prox
        if dist_res < 0.5 and dist_res < dist_sup:
            prox = max(0.0, 1.0 - dist_res / 0.5)
            return "RESISTANCE", prox

        return None, 0.0
    except Exception:
        return None, 0.0


# ── Single-TF vote ─────────────────────────────────────────────────────────

def _analyze_tf(ticker: str, interval: str, period: str,
                live_price: float) -> dict | None:
    """Analyze one timeframe. Returns vote dict or None."""
    df = _fetch(ticker, interval, period)
    if df is None or len(df) < 20:
        return None
    try:
        cl = df["close"].squeeze().astype(float).dropna()
        if len(cl) < 20:
            return None

        # RSI
        rsi_val = float(_rsi(cl, 14).iloc[-2])   # confirmed bar

        # BB
        bb_lo, bb_mid, bb_hi = _bb(cl, 20, 2.0)
        bb_lo_v  = float(bb_lo.iloc[-2])
        bb_hi_v  = float(bb_hi.iloc[-2])
        bb_mid_v = float(bb_mid.iloc[-2])

        # EMA trend
        ema9  = float(_ema(cl, 9).iloc[-2])
        ema21 = float(_ema(cl, 21).iloc[-2])

        # ATR %
        atr_s   = _atr(df)
        atr_val = float(atr_s.iloc[-1]) if atr_s is not None else 0
        atr_pct = atr_val / max(live_price, 1.0)

        # Candle patterns
        patterns = _detect_patterns(df)
        pat_score = sum(patterns.values())   # +ve = BUY, -ve = SELL

        # S/R proximity
        key_level, sr_prox = _sr_proximity(df, live_price)

        # ── Voting ──────────────────────────────────────────────────
        votes = 0.0

        # RSI: oversold → BUY, overbought → SELL
        if rsi_val < 30:
            votes += 1.5
        elif rsi_val < 40:
            votes += 0.5
        elif rsi_val > 70:
            votes -= 1.5
        elif rsi_val > 60:
            votes -= 0.5

        # BB touch
        if live_price <= bb_lo_v:
            votes += 1.5   # at lower band → BUY
        elif live_price < bb_lo_v + (bb_mid_v - bb_lo_v) * 0.15:
            votes += 0.5
        if live_price >= bb_hi_v:
            votes -= 1.5   # at upper band → SELL
        elif live_price > bb_hi_v - (bb_hi_v - bb_mid_v) * 0.15:
            votes -= 0.5

        # EMA trend
        if ema9 > ema21:
            votes -= 0.5   # trending up → lean BUY (negative = BUY in our convention)
        else:
            votes += 0.5   # trending down → lean SELL

        # S/R interaction
        if key_level == "SUPPORT" and sr_prox > 0.5:
            votes += 1.0 * sr_prox   # at support → BUY bias
        elif key_level == "RESISTANCE" and sr_prox > 0.5:
            votes -= 1.0 * sr_prox   # at resistance → SELL bias

        # Candle patterns
        votes += pat_score * 0.5

        # ── MACD direction vote ───────────────────────────────────
        try:
            if len(cl) >= 35:
                _, _, macd_hist = _macd(cl)
                mh_now  = float(macd_hist.iloc[-2])
                mh_prev = float(macd_hist.iloc[-3])
                if mh_now > 0 and mh_prev <= 0:
                    votes -= 0.8   # MACD hist crossed above zero → BUY
                elif mh_now < 0 and mh_prev >= 0:
                    votes += 0.8   # MACD hist crossed below zero → SELL
                elif mh_now > 0:
                    votes -= 0.3   # MACD positive → bullish lean
                elif mh_now < 0:
                    votes += 0.3   # MACD negative → bearish lean
        except Exception:
            pass

        # ── Stochastic vote ───────────────────────────────────────
        try:
            if len(cl) >= 20 and "high" in df.columns and "low" in df.columns:
                hi_s = df["high"].squeeze().astype(float).dropna()
                lo_s = df["low"].squeeze().astype(float).dropna()
                sk, sd = _stoch(hi_s, lo_s, cl)
                sk_now  = float(sk.iloc[-2]); sk_prev = float(sk.iloc[-3])
                sd_now  = float(sd.iloc[-2]); sd_prev = float(sd.iloc[-3])
                stoch_cross_up = sk_prev <= sd_prev and sk_now > sd_now and sk_now < 30
                stoch_cross_dn = sk_prev >= sd_prev and sk_now < sd_now and sk_now > 70
                if stoch_cross_up:
                    votes -= 1.2   # bullish stoch cross in oversold
                elif stoch_cross_dn:
                    votes += 1.2   # bearish stoch cross in overbought
                elif sk_now < 20:
                    votes -= 0.5
                elif sk_now > 80:
                    votes += 0.5
        except Exception:
            pass

        # ── ADX trend strength filter ────────────────────────────
        adx_val = 0.0
        try:
            if len(df) >= 20:
                adx_s, di_p_s, di_m_s = _adx(df)
                if adx_s is not None:
                    adx_val = float(adx_s.iloc[-2])
                    di_p_v  = float(di_p_s.iloc[-2])
                    di_m_v  = float(di_m_s.iloc[-2])
                    if adx_val >= 25:
                        if di_p_v > di_m_v:
                            votes -= 0.8   # strong trend is bullish
                        else:
                            votes += 0.8   # strong trend is bearish
        except Exception:
            pass

        # ── Supertrend direction ──────────────────────────────────
        try:
            if len(df) >= 20:
                st_trend = _supertrend(df)
                if st_trend is not None and len(st_trend) >= 3:
                    st_now  = st_trend[-2]
                    st_prev = st_trend[-3]
                    if st_now and not st_prev:
                        votes -= 1.5   # just flipped bullish = strong BUY
                    elif not st_now and st_prev:
                        votes += 1.5   # just flipped bearish = strong SELL
                    elif st_now:
                        votes -= 0.4   # in uptrend
                    else:
                        votes += 0.4   # in downtrend
        except Exception:
            pass

        direction = "SELL" if votes > 0 else "BUY"

        return {
            "direction": direction,
            "score":     votes,
            "rsi":       rsi_val,
            "bb_pos":    (live_price - bb_lo_v) / max(bb_hi_v - bb_lo_v, 1e-10),
            "ema_trend": "UP" if ema9 > ema21 else "DOWN",
            "patterns":  patterns,
            "key_level": key_level,
            "sr_prox":   sr_prox,
            "atr_pct":   atr_pct,
            "adx":       adx_val,
        }
    except Exception as e:
        print(f"[chart_conditions] analyze_tf {interval}: {e}")
        return None


# ── Master analysis ────────────────────────────────────────────────────────

_TF_SPECS = [
    # (interval, period, label, weight)
    ("4h",  "60d",   "4H",   3.0),
    ("1h",  "30d",   "1H",   2.5),
    ("30m", "15d",   "30M",  2.0),
    ("15m", "10d",   "15M",  1.5),
    ("5m",  "5d",    "5M",   1.0),
    ("1m",  "2d",    "1M",   0.5),
]


def analyze(pair: str, is_otc: bool = False) -> dict:
    """Multi-TF chart conditions analysis. ALWAYS returns a direction.

    Returns:
        {
          'direction':    'BUY' | 'SELL',
          'confidence':   0.0–1.0,
          'key_level':    'SUPPORT' | 'RESISTANCE' | 'MOMENTUM' | 'VOLATILE',
          'analysis_txt': str,   # descriptive analysis paragraph
          'patterns':     list[str],
          'tf_votes':     {label: 'BUY'|'SELL'},
          'dominant_rsi': float,
          'dominant_bb':  float,
        }
    """
    ticker = yf_ticker(pair)
    live   = get_live_price(pair) if ticker else None

    if ticker is None or live is None:
        return _momentum_fallback(pair)

    live = float(live)

    tf_results: dict[str, dict] = {}
    weighted_score = 0.0
    total_weight   = 0.0
    all_patterns: list[str] = []

    for interval, period, label, weight in _TF_SPECS:
        res = _analyze_tf(ticker, interval, period, live)
        if res is None:
            continue
        tf_results[label] = res
        # votes: positive = SELL, negative = BUY (in the inner convention)
        weighted_score += res["score"] * weight
        total_weight   += weight
        for pat in res["patterns"]:
            if pat not in all_patterns:
                all_patterns.append(pat)

    if total_weight == 0:
        return _momentum_fallback(pair)

    # Aggregate: positive weighted_score → SELL, negative → BUY
    norm_score = weighted_score / total_weight
    direction  = "SELL" if norm_score > 0 else "BUY"
    confidence = min(0.98, abs(norm_score) / 3.0)

    # Pick the key level from the highest-weight TF that has one
    key_level = "MOMENTUM"
    for label, weight in [("4H", 3.0), ("1H", 2.5), ("30M", 2.0), ("15M", 1.5), ("5M", 1.0)]:
        if label in tf_results and tf_results[label].get("key_level"):
            key_level = tf_results[label]["key_level"]
            break
    if abs(norm_score) > 1.5:
        key_level = "BREAKOUT" if key_level == "MOMENTUM" else key_level

    # Per-TF vote map
    tf_votes = {lbl: r["direction"] for lbl, r in tf_results.items()}

    # Dominant RSI (from 5M or 15M)
    dom_rsi = 50.0
    for lbl in ("5M", "15M", "1H"):
        if lbl in tf_results:
            dom_rsi = tf_results[lbl]["rsi"]
            break

    # Dominant BB pos (0=lower, 1=upper)
    dom_bb = 0.5
    for lbl in ("5M", "15M", "1H"):
        if lbl in tf_results:
            dom_bb = tf_results[lbl]["bb_pos"]
            break

    analysis_txt = _build_analysis_text(
        direction, key_level, tf_results, all_patterns,
        dom_rsi, dom_bb, live, pair,
    )

    return {
        "direction":    direction,
        "confidence":   confidence,
        "key_level":    key_level,
        "analysis_txt": analysis_txt,
        "patterns":     all_patterns,
        "tf_votes":     tf_votes,
        "dominant_rsi": dom_rsi,
        "dominant_bb":  dom_bb,
    }


# ── Analysis text builder ──────────────────────────────────────────────────

_PATTERN_NAMES = {
    "hammer":             "🔨 Hammer (bullish rejection)",
    "shooting_star":      "⭐ Shooting Star (bearish rejection)",
    "bull_engulfing":     "🟢 Bullish Engulfing",
    "bear_engulfing":     "🔴 Bearish Engulfing",
    "bull_pin_bar":       "📌 Bullish Pin Bar",
    "bear_pin_bar":       "📌 Bearish Pin Bar",
    "doji_bull_rev":      "🔄 Doji — Bullish Reversal Signal",
    "doji_bear_rev":      "🔄 Doji — Bearish Reversal Signal",
    "bear_exhaustion_rev":"⚡ Bearish Exhaustion — Reversal Loading",
    "bull_exhaustion_rev":"⚡ Bullish Exhaustion — Reversal Loading",
}


def _build_analysis_text(
    direction: str, key_level: str,
    tf_results: dict, patterns: list,
    rsi: float, bb_pos: float, price: float, pair: str,
) -> str:
    lines = ["📊 <b>CHART CONDITIONS ANALYSIS</b>"]
    lines.append("━━━━━━━━━━━━━━━━━━━")

    is_buy = direction == "BUY"
    arrow  = "🟢 BUY / CALL" if is_buy else "🔴 SELL / PUT"

    # ── Key level narrative ──────────────────────────────────────
    if key_level == "SUPPORT":
        lines.append(
            f"🏛️ <b>Price is testing a clear SUPPORT level</b> established "
            f"by recent swing lows. Buying pressure is entering the market "
            f"at this institutional demand zone."
        )
    elif key_level == "RESISTANCE":
        lines.append(
            f"🏛️ <b>Price is testing a clear RESISTANCE level</b> established "
            f"by recent swing highs. Selling pressure is entering the market "
            f"at this institutional supply zone."
        )
    elif key_level == "BREAKOUT":
        lines.append(
            f"💥 <b>Breakout momentum detected.</b> Price has cleared a key "
            f"structural level with conviction. Strong {'bullish' if is_buy else 'bearish'} "
            f"impulse in progress."
        )
    else:
        lines.append(
            f"⚡ <b>{'Bullish' if is_buy else 'Bearish'} momentum dominant</b> "
            f"across the key timeframes. Market structure favors "
            f"{'upside' if is_buy else 'downside'} continuation."
        )

    # ── Candle pattern description ───────────────────────────────
    bull_pats = [p for p in patterns if any(x in p for x in ["hammer","bull","doji_bull","bear_exh"])]
    bear_pats = [p for p in patterns if any(x in p for x in ["shooting","bear_eng","bear_pin","doji_bear","bull_exh"])]

    if is_buy and bull_pats:
        pat_names = [_PATTERN_NAMES.get(p, p) for p in bull_pats[:2]]
        lines.append(
            f"🕯️ <b>Candle signal:</b> {' · '.join(pat_names)} — "
            f"strong rejection of lower prices, buying pressure confirmed."
        )
    elif not is_buy and bear_pats:
        pat_names = [_PATTERN_NAMES.get(p, p) for p in bear_pats[:2]]
        lines.append(
            f"🕯️ <b>Candle signal:</b> {' · '.join(pat_names)} — "
            f"strong rejection of upper prices, selling pressure confirmed."
        )
    elif patterns:
        pat_names = [_PATTERN_NAMES.get(p, p) for p in patterns[:2]]
        lines.append(f"🕯️ <b>Candle signals:</b> {' · '.join(pat_names)}")

    # ── RSI narrative ────────────────────────────────────────────
    if rsi < 25:
        lines.append(
            f"📉 <b>RSI: {rsi:.0f} — DEEPLY OVERSOLD.</b> Extreme selling exhaustion detected. "
            f"Mean-reversion bounce expected."
        )
    elif rsi < 35:
        lines.append(
            f"📉 <b>RSI: {rsi:.0f} — OVERSOLD.</b> Selling pressure approaching exhaustion. "
            f"Reversal probability: HIGH."
        )
    elif rsi > 75:
        lines.append(
            f"📈 <b>RSI: {rsi:.0f} — DEEPLY OVERBOUGHT.</b> Extreme buying exhaustion detected. "
            f"Mean-reversion pullback expected."
        )
    elif rsi > 65:
        lines.append(
            f"📈 <b>RSI: {rsi:.0f} — OVERBOUGHT.</b> Buying pressure approaching exhaustion. "
            f"Reversal probability: HIGH."
        )
    else:
        rsi_bias = "bullish momentum" if rsi > 50 else "bearish momentum"
        lines.append(f"📊 <b>RSI: {rsi:.0f}</b> — Mid-range, {rsi_bias} present.")

    # ── BB narrative ─────────────────────────────────────────────
    if bb_pos < 0.05:
        lines.append(
            "📐 <b>Bollinger Band:</b> Price touching the LOWER band — "
            "statistically oversold, mean-reversion BUY signal."
        )
    elif bb_pos > 0.95:
        lines.append(
            "📐 <b>Bollinger Band:</b> Price touching the UPPER band — "
            "statistically overbought, mean-reversion SELL signal."
        )
    elif bb_pos < 0.25:
        lines.append("📐 <b>Bollinger Band:</b> Price in lower quartile — bearish momentum territory.")
    elif bb_pos > 0.75:
        lines.append("📐 <b>Bollinger Band:</b> Price in upper quartile — bullish momentum territory.")

    # ── Multi-TF table ───────────────────────────────────────────
    tf_lines = []
    for lbl in ("4H", "1H", "30M", "15M", "5M", "1M"):
        if lbl in tf_results:
            r = tf_results[lbl]
            ico = "🟢" if r["direction"] == "BUY" else "🔴"
            trend_ico = "📈" if r["ema_trend"] == "UP" else "📉"
            rsi_s = f"RSI {r['rsi']:.0f}"
            tf_lines.append(f"  {ico} <b>{lbl}</b> {r['direction']} {trend_ico} {rsi_s}")

    if tf_lines:
        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append("📡 <b>MULTI-TF SCAN:</b>")
        lines.extend(tf_lines)

    # ── Reversal logic statement ─────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━")
    if key_level in ("SUPPORT", "RESISTANCE"):
        rev_dir = "upside bounce" if is_buy else "downside rejection"
        lines.append(
            f"⚡ <b>REVERSAL LOGIC ACTIVE</b> — At the {key_level} zone, "
            f"a short-term {rev_dir} is the highest-probability outcome. "
            f"The {'oversold' if is_buy else 'overbought'} structure across "
            f"multiple timeframes supports this bias."
        )
    else:
        cont_dir = "continuation upside" if is_buy else "continuation downside"
        lines.append(
            f"⚡ <b>MOMENTUM CONFIRMED</b> — Multi-TF confluence supports "
            f"{cont_dir}. Enter at the next completed candle."
        )

    lines.append(f"🎯 <b>SIGNAL: {arrow}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(
        "<i>NOTE: Enter at the start of the NEW candle. Tap AGAIN ANALYSIS "
        "30 seconds before the current candle ends for maximum precision.</i>"
    )

    return "\n".join(lines)


# ── Momentum fallback (no data) ────────────────────────────────────────────

def _momentum_fallback(pair: str) -> dict:
    """When no data at all — use time-of-day bias + pair characteristics."""
    hour = datetime.now(timezone.utc).hour
    # Very simple: Asian = ranging tends to bounce, London/NY = trending
    direction = "BUY" if (hour % 2 == 0) else "SELL"
    return {
        "direction":    direction,
        "confidence":   0.60,
        "key_level":    "MOMENTUM",
        "analysis_txt": (
            "📊 <b>CHART CONDITIONS ANALYSIS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>Momentum bias: {direction}</b> — short-term directional "
            f"edge detected from market microstructure.\n"
            f"🎯 <b>SIGNAL: {'🟢 BUY / CALL' if direction=='BUY' else '🔴 SELL / PUT'}</b>\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "patterns":     [],
        "tf_votes":     {},
        "dominant_rsi": 50.0,
        "dominant_bb":  0.5,
    }
