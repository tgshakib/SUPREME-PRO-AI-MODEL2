"""FX EXPERT IMTIAZ 4.0 PRO — Forex Signal Engine
==================================================
Premium forex indicator engine for SUPREME PRO AI BOT.

Replicates the institutional multi-confluence logic of FX Expert Imtiaz 4.0 Pro:
  • EMA Ribbon (5, 8, 13, 21, 34, 55) — Fibonacci-based trend structure
  • MACD (12, 26, 9)                   — momentum + histogram direction
  • RSI (14)                            — standard momentum with divergence check
  • Stochastic (14, 3, 3)              — secondary reversal / continuation filter
  • ADX (14) with DI+/DI-              — trend strength + directional bias
  • ATR (14)                            — volatility filter (skip low-chop)
  • Market structure (HH/HL/LH/LL)    — swing-based structure confirmation
  • 4H trend bias                       — higher-TF kill switch (no counter-trend)
  • Candle body filter                  — conviction gate
  • Signal grade 0-100 — engine accepts ≥ 70 for forex
  • 120s cache per pair

Public API
----------
  fx_analyze(pair) -> dict | None
    {
      'direction':  'BUY' | 'SELL',
      'grade':      int 0-100,
      'agree':      int,        # number of confluent signals
      'elite':      bool,       # all major engines aligned
      'reasons':    list[str],  # human-readable confluence reasons
      'adx':        float,      # trend strength
      'htf_trend':  str,        # '4H BULL' | '4H BEAR' | 'NEUTRAL'
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
    print(f"[fx_expert] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 120.0

FX_MIN_GRADE = 70
FX_CANDLES   = 200
FX_INTERVAL  = "1h"
FX_PERIOD    = "30d"
FX_4H_PERIOD = "60d"

# Fibonacci EMA ribbon
_RIBBON = [5, 8, 13, 21, 34, 55]


def _flatten(df):
    if hasattr(df.columns, "get_level_values"):
        df.columns = [
            str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
            for c in df.columns
        ]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _macd(series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram)."""
    ml = _ema(series, fast) - _ema(series, slow)
    sl = _ema(ml, signal)
    return ml, sl, ml - sl


def _stochastic(high, low, close, k=14, d=3, smooth=3):
    lowest  = low.rolling(k).min()
    highest = high.rolling(k).max()
    raw_k   = 100 * (close - lowest) / (highest - lowest + 1e-10)
    pct_k   = raw_k.rolling(smooth).mean()
    pct_d   = pct_k.rolling(d).mean()
    return pct_k, pct_d


def _adx(df, period: int = 14):
    """ADX + DI+ + DI-. Returns (adx_series, di_plus, di_minus)."""
    try:
        h  = df["high"].astype(float)
        lo = df["low"].astype(float)
        c  = df["close"].astype(float)
        prev_h = h.shift(1)
        prev_lo = lo.shift(1)
        prev_c  = c.shift(1)
        tr = (h - lo).combine((h - prev_c).abs(), max).combine(
            (lo - prev_c).abs(), max)
        up   = h - prev_h
        down = prev_lo - lo
        dm_p = up.where((up > down) & (up > 0), 0.0)
        dm_m = down.where((down > up) & (down > 0), 0.0)
        atr14  = tr.rolling(period).mean()
        di_p = 100 * dm_p.rolling(period).mean() / atr14.replace(0, 1e-10)
        di_m = 100 * dm_m.rolling(period).mean() / atr14.replace(0, 1e-10)
        dx   = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 1e-10)
        return dx.rolling(period).mean(), di_p, di_m
    except Exception:
        return None, None, None


def _atr(df, period: int = 14):
    try:
        h  = df["high"].astype(float)
        lo = df["low"].astype(float)
        c  = df["close"].astype(float)
        prev_c = c.shift(1)
        tr = (h - lo).combine((h - prev_c).abs(), max).combine(
            (lo - prev_c).abs(), max)
        return tr.rolling(period).mean()
    except Exception:
        return None


def _market_structure(close, n: int = 20) -> str:
    """Detect HH/HL (bull) or LH/LL (bear) over last n bars.

    Returns 'BUY', 'SELL', or 'NEUTRAL'.
    """
    try:
        seg = close.tail(n).values
        if len(seg) < 6:
            return "NEUTRAL"
        # Split into thirds for swing high/low comparison
        third = n // 3
        lo1 = min(seg[:third])
        lo2 = min(seg[third:2*third])
        lo3 = min(seg[2*third:])
        hi1 = max(seg[:third])
        hi2 = max(seg[third:2*third])
        hi3 = max(seg[2*third:])
        # Higher highs and higher lows → BUY
        hl = lo2 > lo1 and lo3 > lo2
        hh = hi2 > hi1 and hi3 > hi2
        # Lower highs and lower lows → SELL
        ll = lo2 < lo1 and lo3 < lo2
        lh = hi2 < hi1 and hi3 < hi2
        if hh and hl:
            return "BUY"
        if ll and lh:
            return "SELL"
        if hl and not lh:
            return "BUY"
        if ll and not hh:
            return "SELL"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _fetch(ticker: str, interval: str = FX_INTERVAL, period: str = FX_PERIOD):
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
        if df is None or df.empty or len(df) < 60:
            return None
        df = _flatten(df)
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            return None
        return df.tail(FX_CANDLES).copy()
    except Exception as e:
        print(f"[fx_expert] fetch error {ticker} {interval}: {e}")
        return None


def _htf_bias(ticker: str) -> str:
    """4H trend bias by resampling 1H data. Returns '4H BULL', '4H BEAR', or 'NEUTRAL'."""
    try:
        df1h = _fetch(ticker, interval="1h", period="60d")
        if df1h is None or len(df1h) < 20:
            return "NEUTRAL"
        # Resample 1H → 4H
        df1h.index = pd.to_datetime(df1h.index, utc=True)
        df4h = df1h["close"].resample("4h").last().dropna()
        if len(df4h) < 15:
            return "NEUTRAL"
        e8  = float(_ema(df4h, 8).iloc[-1])
        e21 = float(_ema(df4h, 21).iloc[-1])
        rsi4h = float(_rsi(df4h, 14).iloc[-1])
        price4h = float(df4h.iloc[-1])
        if e8 > e21 and price4h > e21 and rsi4h > 50:
            return "4H BULL"
        if e8 < e21 and price4h < e21 and rsi4h < 50:
            return "4H BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def fx_analyze(pair: str) -> Optional[dict]:
    """Run FX Expert Imtiaz 4.0 Pro analysis on `pair`.

    Returns a dict with direction, grade, agree count, elite flag, reasons,
    ADX value, and HTF trend — or None when no clean setup is found.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cached = _CACHE.get(ticker)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    df = _fetch(ticker)
    if df is None:
        _CACHE[ticker] = (now, None)
        return None

    try:
        close = df["close"].astype(float).squeeze()
        high  = df["high"].astype(float).squeeze()
        low   = df["low"].astype(float).squeeze()
        open_ = df["open"].astype(float).squeeze()
    except Exception:
        _CACHE[ticker] = (now, None)
        return None

    if len(close) < 60:
        _CACHE[ticker] = (now, None)
        return None

    reasons: list[str] = []
    buy_votes  = 0
    sell_votes = 0

    # ── 1. EMA Fibonacci Ribbon (5, 8, 13, 21, 34, 55) ───────────────
    try:
        ribbons = {p: float(_ema(close, p).iloc[-1]) for p in _RIBBON}
        price_now = float(close.iloc[-1])
        # Full ribbon alignment: all EMAs stacked bull (5>8>13>21>34>55)
        ribbon_vals = [ribbons[p] for p in _RIBBON]
        fully_bull = all(ribbon_vals[i] > ribbon_vals[i+1] for i in range(len(ribbon_vals)-1))
        fully_bear = all(ribbon_vals[i] < ribbon_vals[i+1] for i in range(len(ribbon_vals)-1))
        # Price above/below the ribbon
        above_all = price_now > max(ribbon_vals)
        below_all = price_now < min(ribbon_vals)
        # Partial alignment (fast EMAs stacked)
        partial_bull = ribbons[5] > ribbons[8] > ribbons[13] > ribbons[21]
        partial_bear = ribbons[5] < ribbons[8] < ribbons[13] < ribbons[21]

        if fully_bull and above_all:
            buy_votes += 3
            reasons.append("EMA RIBBON FULLY BULLISH — price above all 6 EMAs")
        elif fully_bear and below_all:
            sell_votes += 3
            reasons.append("EMA RIBBON FULLY BEARISH — price below all 6 EMAs")
        elif partial_bull:
            buy_votes += 2
            reasons.append("EMA RIBBON BULLISH (5>8>13>21 stacked)")
        elif partial_bear:
            sell_votes += 2
            reasons.append("EMA RIBBON BEARISH (5<8<13<21 stacked)")
        elif price_now > ribbons[21]:
            buy_votes += 1
        elif price_now < ribbons[21]:
            sell_votes += 1
    except Exception:
        pass

    # ── 2. MACD (12, 26, 9) ───────────────────────────────────────────
    try:
        ml, sl, hist = _macd(close)
        h_now  = float(hist.iloc[-1])
        h_prev = float(hist.iloc[-2])
        ml_now = float(ml.iloc[-1])
        sl_now = float(sl.iloc[-1])
        ml_prev = float(ml.iloc[-2])
        sl_prev = float(sl.iloc[-2])
        macd_cross_up = (ml_prev <= sl_prev) and (ml_now > sl_now) and ml_now < 0
        macd_cross_dn = (ml_prev >= sl_prev) and (ml_now < sl_now) and ml_now > 0
        hist_growing_bull = h_now > 0 and h_now > h_prev
        hist_growing_bear = h_now < 0 and h_now < h_prev

        if macd_cross_up:
            buy_votes += 3
            reasons.append("MACD BULLISH CROSS (from below zero) — strong momentum")
        elif macd_cross_dn:
            sell_votes += 3
            reasons.append("MACD BEARISH CROSS (from above zero) — strong momentum")
        elif hist_growing_bull:
            buy_votes += 2
            reasons.append(f"MACD histogram growing bullish ({h_now:.5f})")
        elif hist_growing_bear:
            sell_votes += 2
            reasons.append(f"MACD histogram growing bearish ({h_now:.5f})")
        elif h_now > 0:
            buy_votes += 1
        elif h_now < 0:
            sell_votes += 1
    except Exception:
        pass

    # ── 3. RSI (14) with zone + divergence check ──────────────────────
    try:
        rsi14 = _rsi(close, 14)
        r_now  = float(rsi14.iloc[-1])
        r_prev = float(rsi14.iloc[-3])
        if r_now > 50 and r_prev <= 50:
            buy_votes += 2
            reasons.append(f"RSI(14) CROSSED ABOVE 50 ({r_now:.0f}) — bull momentum shift")
        elif r_now < 50 and r_prev >= 50:
            sell_votes += 2
            reasons.append(f"RSI(14) CROSSED BELOW 50 ({r_now:.0f}) — bear momentum shift")
        elif r_now >= 55:
            buy_votes += 1
        elif r_now <= 45:
            sell_votes += 1
        # RSI divergence: price making new high/low but RSI doesn't confirm
        price_5 = float(close.iloc[-6])
        rsi_5   = float(rsi14.iloc[-6])
        p_now   = float(close.iloc[-1])
        if p_now > price_5 and r_now < rsi_5 and r_now > 60:
            sell_votes += 1
            reasons.append("RSI BEARISH DIVERGENCE detected")
        elif p_now < price_5 and r_now > rsi_5 and r_now < 40:
            buy_votes += 1
            reasons.append("RSI BULLISH DIVERGENCE detected")
    except Exception:
        pass

    # ── 4. Stochastic (14, 3, 3) ──────────────────────────────────────
    try:
        pct_k, pct_d = _stochastic(high, low, close, k=14, d=3, smooth=3)
        k_now  = float(pct_k.iloc[-1])
        d_now  = float(pct_d.iloc[-1])
        k_prev = float(pct_k.iloc[-2])
        d_prev = float(pct_d.iloc[-2])
        stoch_cross_up = (k_prev <= d_prev) and (k_now > d_now) and k_now < 40
        stoch_cross_dn = (k_prev >= d_prev) and (k_now < d_now) and k_now > 60

        if stoch_cross_up:
            buy_votes += 2
            reasons.append(f"STOCH(14,3,3) BULLISH CROSS at {k_now:.0f}")
        elif stoch_cross_dn:
            sell_votes += 2
            reasons.append(f"STOCH(14,3,3) BEARISH CROSS at {k_now:.0f}")
        elif k_now > d_now and k_now > 50:
            buy_votes += 1
        elif k_now < d_now and k_now < 50:
            sell_votes += 1
    except Exception:
        pass

    # ── 5. ADX (14) with DI+ / DI- ───────────────────────────────────
    adx_val = 0.0
    try:
        adx_s, dip_s, dim_s = _adx(df, 14)
        if adx_s is not None:
            adx_val  = float(adx_s.iloc[-1])
            dip_val  = float(dip_s.iloc[-1])
            dim_val  = float(dim_s.iloc[-1])
            if adx_val < 18:
                # Flat chop — zero out confidence, return None
                _CACHE[ticker] = (now, None)
                return None
            if adx_val >= 25 and dip_val > dim_val:
                buy_votes += 2
                reasons.append(f"ADX STRONG TREND {adx_val:.0f} — DI+ dominates")
            elif adx_val >= 25 and dim_val > dip_val:
                sell_votes += 2
                reasons.append(f"ADX STRONG TREND {adx_val:.0f} — DI- dominates")
            elif adx_val >= 18 and dip_val > dim_val:
                buy_votes += 1
            elif adx_val >= 18 and dim_val > dip_val:
                sell_votes += 1
    except Exception:
        pass

    # ── 6. ATR volatility gate ────────────────────────────────────────
    try:
        atr_s = _atr(df, 14)
        if atr_s is not None:
            atr_val = float(atr_s.iloc[-1])
            price_val = float(close.iloc[-1])
            atr_pct = atr_val / max(1e-9, price_val)
            if atr_pct < 0.0006:
                _CACHE[ticker] = (now, None)
                return None
    except Exception:
        pass

    # ── 7. Market structure (HH/HL or LH/LL) ─────────────────────────
    try:
        ms = _market_structure(close, n=24)
        if ms == "BUY":
            buy_votes += 2
            reasons.append("MARKET STRUCTURE: HH + HL (Higher Highs & Lows)")
        elif ms == "SELL":
            sell_votes += 2
            reasons.append("MARKET STRUCTURE: LH + LL (Lower Highs & Lows)")
    except Exception:
        pass

    # ── 8. Candle body conviction ─────────────────────────────────────
    try:
        c_open  = float(open_.iloc[-1])
        c_high  = float(high.iloc[-1])
        c_low   = float(low.iloc[-1])
        c_close = float(close.iloc[-1])
        c_range = max(1e-9, c_high - c_low)
        body    = abs(c_close - c_open)
        body_ratio = body / c_range
        if body_ratio >= 0.60:
            if c_close > c_open:
                buy_votes += 1
                reasons.append(f"BULL CONVICTION CANDLE {body_ratio:.0%} body")
            else:
                sell_votes += 1
                reasons.append(f"BEAR CONVICTION CANDLE {body_ratio:.0%} body")
    except Exception:
        pass

    # ── 9. 4H HTF trend kill-switch ───────────────────────────────────
    htf = "NEUTRAL"
    try:
        htf = _htf_bias(ticker)
        if htf == "4H BULL":
            buy_votes += 2
            reasons.insert(0, "4H TREND BULLISH — trading with the macro flow")
        elif htf == "4H BEAR":
            sell_votes += 2
            reasons.insert(0, "4H TREND BEARISH — trading with the macro flow")
    except Exception:
        pass

    # ── Direction + grade ─────────────────────────────────────────────
    total = buy_votes + sell_votes
    if total == 0:
        _CACHE[ticker] = (now, None)
        return None

    if buy_votes > sell_votes:
        direction = "BUY"
        ratio = buy_votes / total
        agree = buy_votes
    elif sell_votes > buy_votes:
        direction = "SELL"
        ratio = sell_votes / total
        agree = sell_votes
    else:
        _CACHE[ticker] = (now, None)
        return None

    # HTF kill-switch: never trade against the 4H macro trend
    if htf == "4H BULL" and direction == "SELL" and sell_votes <= 4:
        _CACHE[ticker] = (now, None)
        return None
    if htf == "4H BEAR" and direction == "BUY" and buy_votes <= 4:
        _CACHE[ticker] = (now, None)
        return None

    grade = int(60 + 40 * ((ratio - 0.5) / 0.5))
    grade = max(60, min(100, grade))

    # Bonus for high agree count
    if agree >= 12:
        grade = min(100, grade + 6)
    elif agree >= 9:
        grade = min(100, grade + 4)
    elif agree >= 7:
        grade = min(100, grade + 2)

    # ADX strength bonus
    if adx_val >= 30:
        grade = min(100, grade + 3)
    elif adx_val >= 25:
        grade = min(100, grade + 1)

    elite = agree >= 10 and ratio >= 0.72 and htf != "NEUTRAL"

    if grade < FX_MIN_GRADE:
        _CACHE[ticker] = (now, None)
        return None

    result = {
        "direction": direction,
        "grade":     grade,
        "agree":     agree,
        "elite":     elite,
        "reasons":   reasons[:4],
        "adx":       round(adx_val, 1),
        "htf_trend": htf,
        "buy_votes": buy_votes,
        "sell_votes": sell_votes,
    }
    _CACHE[ticker] = (now, result)
    return result
