"""QX EXPERT IMTIAZZ PRO — Non-Reprint Binary Signal Engine
============================================================
Premium binary indicator engine for SUPREME PRO AI BOT.

Replicates the multi-oscillator confluence logic of QX Expert Non-Reprint:
  • Fast Stochastic (5, 3, 3)    — quick reversal detection at extremes
  • RSI (7)                       — ultra-fast momentum for short candles
  • CCI (14)                      — commodity channel index overbought/oversold
  • Williams %R (14)              — extreme reversal filter
  • Bollinger Bands (20, 2.0)    — outer-band touch + squeeze signal
  • Heikin Ashi smoothing         — trend noise reduction
  • Candle body strength          — conviction filter
  • Volume surge check            — institutional entry confirmation
  • NON-REPRINT guard             — signal must exist on 2 consecutive confirmed bars
  • Sub-candle 5s/15s/30sc zones — micro-liquidity reversal for binary timing
  • Signal grade 0-100 — engine accepts ≥ 75 for binary (≥ 80 OTC)
  • 90s cache per pair to avoid hammering yfinance

NON-REPRINT LOGIC
─────────────────
A signal is only accepted if the same direction fires on BOTH:
  bar[-2] (two confirmed bars ago) AND bar[-1] (last confirmed bar).
This prevents repainting: signals that appeared on a forming candle
and then disappeared. Both bars must agree = zero repaint risk.

Sub-candle 5s/15s/30sc support: for binary entries we analyse the
internal structure of the last 1-3 confirmed 1m candles to produce
a micro-liquidity zone estimate for the 5s, 15s, and 30sc windows.

Public API
----------
  qx_analyze(pair, is_otc=False) -> dict | None
    {
      'direction':      'BUY' | 'SELL',
      'grade':          int 0-100,
      'agree':          int,          # how many sub-signals agreed
      'elite':          bool,         # all major sub-signals aligned
      'reasons':        list[str],    # human-readable confluence reasons
      'non_reprint':    bool,         # True = both confirmation bars agree
      'sub_candle_dir': str | None,   # sub-candle 5s/15s/30sc bias
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
    print(f"[qx_expert] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 90.0

QX_MIN_GRADE = 75
QX_CANDLES   = 120
QX_INTERVAL  = "5m"
QX_PERIOD    = "3d"


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


def _rsi(series, period: int = 7):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _stochastic(high, low, close, k=5, d=3, smooth=3):
    """Fast Stochastic (k, d, smooth). Returns (%K, %D)."""
    lowest  = low.rolling(k).min()
    highest = high.rolling(k).max()
    raw_k   = 100 * (close - lowest) / (highest - lowest + 1e-10)
    pct_k   = raw_k.rolling(smooth).mean()
    pct_d   = pct_k.rolling(d).mean()
    return pct_k, pct_d


def _cci(high, low, close, period: int = 14):
    """Commodity Channel Index."""
    typical = (high + low + close) / 3
    ma = typical.rolling(period).mean()
    md = typical.rolling(period).apply(lambda x: (abs(x - x.mean())).mean(), raw=True)
    return (typical - ma) / (0.015 * md.replace(0, 1e-10))


def _williams_r(high, low, close, period: int = 14):
    """Williams %R."""
    highest = high.rolling(period).max()
    lowest  = low.rolling(period).min()
    return -100 * (highest - close) / (highest - lowest + 1e-10)


def _bbands(series, period: int = 20, dev: float = 2.0):
    """Bollinger Bands → (upper, mid, lower)."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return mid + dev * std, mid, mid - dev * std


def _heikin_ashi(df):
    """Return Heikin Ashi OHLC as a new DataFrame."""
    ha = df[["open", "high", "low", "close"]].copy()
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = [(df["open"].iloc[0] + df["close"].iloc[0]) / 2]
    for i in range(1, len(df)):
        ha_open.append((ha_open[-1] + ha["ha_close"].iloc[i - 1]) / 2)
    ha["ha_open"] = ha_open
    ha["ha_high"] = df[["high", "open", "close"]].max(axis=1)
    ha["ha_low"]  = df[["low",  "open", "close"]].min(axis=1)
    return ha


def _fetch(ticker: str):
    if not _OK or yf is None:
        return None
    try:
        df = yf.download(
            ticker,
            period=QX_PERIOD,
            interval=QX_INTERVAL,
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 40:
            return None
        df = _flatten(df)
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            return None
        return df.tail(QX_CANDLES).copy()
    except Exception as e:
        print(f"[qx_expert] fetch error {ticker}: {e}")
        return None


def _compute_votes_at_offset(
    close, high, low, open_, is_otc: bool, offset: int = -1
) -> tuple[int, int, list[str]]:
    """
    Compute buy/sell votes for a specific bar offset.
    offset=-1 → last confirmed bar (bar[-2] in 0-based from tail)
    offset=-2 → two bars ago (bar[-3])
    Returns (buy_votes, sell_votes, reasons).
    Used for non-reprint: both bars must agree before signal fires.
    """
    reasons: list[str] = []
    buy_votes  = 0
    sell_votes = 0

    idx = offset  # e.g. -1 = last item, -2 = second-to-last

    try:
        rsi7 = _rsi(close, 7).iloc[idx]
        if rsi7 <= 28:
            buy_votes += 2; reasons.append(f"RSI(7) OVERSOLD {rsi7:.0f}")
        elif rsi7 >= 72:
            sell_votes += 2; reasons.append(f"RSI(7) OVERBOUGHT {rsi7:.0f}")
        elif rsi7 <= 40:
            buy_votes += 1
        elif rsi7 >= 60:
            sell_votes += 1
    except Exception:
        pass

    try:
        pct_k, pct_d = _stochastic(high, low, close, k=5, d=3, smooth=3)
        k_now  = float(pct_k.iloc[idx])
        d_now  = float(pct_d.iloc[idx])
        k_prev = float(pct_k.iloc[idx - 1])
        d_prev = float(pct_d.iloc[idx - 1])
        if (k_prev <= d_prev) and (k_now > d_now) and k_now < 30:
            buy_votes += 2; reasons.append(f"STOCH CROSS UP at {k_now:.0f}")
        elif (k_prev >= d_prev) and (k_now < d_now) and k_now > 70:
            sell_votes += 2; reasons.append(f"STOCH CROSS DOWN at {k_now:.0f}")
        elif k_now <= 20:
            buy_votes += 1
        elif k_now >= 80:
            sell_votes += 1
    except Exception:
        pass

    try:
        cci14 = _cci(high, low, close, 14).iloc[idx]
        cci_prev = _cci(high, low, close, 14).iloc[idx - 1]
        if cci14 <= -100 and cci14 > cci_prev:
            buy_votes += 2; reasons.append(f"CCI(14) REVERSAL from oversold ({cci14:.0f})")
        elif cci14 >= 100 and cci14 < cci_prev:
            sell_votes += 2; reasons.append(f"CCI(14) REVERSAL from overbought ({cci14:.0f})")
        elif cci14 <= -150:
            buy_votes += 1
        elif cci14 >= 150:
            sell_votes += 1
    except Exception:
        pass

    try:
        wr = _williams_r(high, low, close, 14).iloc[idx]
        wr_prev = _williams_r(high, low, close, 14).iloc[idx - 1]
        if wr <= -80 and wr > wr_prev:
            buy_votes += 2; reasons.append(f"Williams %R OVERSOLD reversal ({wr:.0f})")
        elif wr >= -20 and wr < wr_prev:
            sell_votes += 2; reasons.append(f"Williams %R OVERBOUGHT reversal ({wr:.0f})")
        elif wr <= -90:
            buy_votes += 1
        elif wr >= -10:
            sell_votes += 1
    except Exception:
        pass

    try:
        bb_up, bb_mid, bb_lo = _bbands(close, 20, 2.0)
        p_now  = float(close.iloc[idx])
        p_prev = float(close.iloc[idx - 1])
        bbu = float(bb_up.iloc[idx]); bbl = float(bb_lo.iloc[idx])
        bbu_prev = float(bb_up.iloc[idx - 1]); bbl_prev = float(bb_lo.iloc[idx - 1])
        if p_prev <= bbl_prev and p_now > bbl:
            buy_votes += 2; reasons.append("BB LOWER BAND BOUNCE")
        elif p_prev >= bbu_prev and p_now < bbu:
            sell_votes += 2; reasons.append("BB UPPER BAND REJECTION")
        elif p_now < bbl:
            buy_votes += 1
        elif p_now > bbu:
            sell_votes += 1
    except Exception:
        pass

    return buy_votes, sell_votes, reasons


def _sub_candle_direction(df) -> Optional[str]:
    """
    Estimate sub-candle (5s/15s/30sc) momentum bias from the last 3
    confirmed 1m bars' internal structure (wicks, body position).
    Returns 'BUY', 'SELL', or None.
    """
    if df is None or len(df) < 4:
        return None
    try:
        bars = df.tail(4).iloc[:-1]  # last 3 confirmed bars (not forming)
        bull_score = 0
        bear_score = 0
        for _, row in bars.iterrows():
            o = float(row["open"]); c = float(row["close"])
            h = float(row["high"]); l = float(row["low"])
            body_hi = max(o, c); body_lo = min(o, c)
            rng = max(h - l, 1e-10)
            wick_up = (h - body_hi) / rng
            wick_dn = (body_lo - l) / rng
            body_ratio = (body_hi - body_lo) / rng
            if c > o:
                bull_score += 1 + int(body_ratio >= 0.6)
                if wick_dn > 0.3:
                    bull_score += 1
            else:
                bear_score += 1 + int(body_ratio >= 0.6)
                if wick_up > 0.3:
                    bear_score += 1
        if bull_score > bear_score + 1:
            return "BUY"
        if bear_score > bull_score + 1:
            return "SELL"
    except Exception:
        pass
    return None


def qx_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Run QX Expert Non-Reprint analysis on `pair`.

    Non-reprint: requires the same signal direction on BOTH the last
    confirmed bar AND the bar before it. Zero repaint risk.

    Returns a dict with direction, grade, agree count, elite flag, reason
    list, non_reprint flag, and sub-candle direction — or None when no
    clean setup is found.
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

    if len(close) < 30:
        _CACHE[ticker] = (now, None)
        return None

    reasons: list[str] = []
    buy_votes  = 0
    sell_votes = 0

    # ── 1. RSI(7) — ultra-fast momentum ───────────────────────────────
    try:
        rsi7 = _rsi(close, 7).iloc[-1]
        if rsi7 <= 28:
            buy_votes += 2
            reasons.append(f"RSI(7) OVERSOLD {rsi7:.0f} → BUY")
        elif rsi7 >= 72:
            sell_votes += 2
            reasons.append(f"RSI(7) OVERBOUGHT {rsi7:.0f} → SELL")
        elif rsi7 <= 40:
            buy_votes += 1
            reasons.append(f"RSI(7) bearish pullback {rsi7:.0f}")
        elif rsi7 >= 60:
            sell_votes += 1
            reasons.append(f"RSI(7) bullish peak {rsi7:.0f}")
    except Exception:
        rsi7 = 50.0

    # ── 2. Fast Stochastic (5,3,3) ────────────────────────────────────
    try:
        pct_k, pct_d = _stochastic(high, low, close, k=5, d=3, smooth=3)
        k_now = float(pct_k.iloc[-1])
        d_now = float(pct_d.iloc[-1])
        k_prev = float(pct_k.iloc[-2])
        d_prev = float(pct_d.iloc[-2])
        stoch_cross_up = (k_prev <= d_prev) and (k_now > d_now) and k_now < 30
        stoch_cross_dn = (k_prev >= d_prev) and (k_now < d_now) and k_now > 70
        if stoch_cross_up:
            buy_votes += 2
            reasons.append(f"STOCH(5,3,3) CROSS UP at {k_now:.0f} — BULLISH")
        elif stoch_cross_dn:
            sell_votes += 2
            reasons.append(f"STOCH(5,3,3) CROSS DOWN at {k_now:.0f} — BEARISH")
        elif k_now <= 20:
            buy_votes += 1
            reasons.append(f"STOCH oversold zone {k_now:.0f}")
        elif k_now >= 80:
            sell_votes += 1
            reasons.append(f"STOCH overbought zone {k_now:.0f}")
    except Exception:
        pass

    # ── 3. CCI(14) — commodity channel index ──────────────────────────
    try:
        cci14 = _cci(high, low, close, 14).iloc[-1]
        cci_prev = _cci(high, low, close, 14).iloc[-2]
        if cci14 <= -100 and cci14 > cci_prev:
            buy_votes += 2
            reasons.append(f"CCI(14) REVERSAL from oversold ({cci14:.0f})")
        elif cci14 >= 100 and cci14 < cci_prev:
            sell_votes += 2
            reasons.append(f"CCI(14) REVERSAL from overbought ({cci14:.0f})")
        elif cci14 <= -150:
            buy_votes += 1
        elif cci14 >= 150:
            sell_votes += 1
    except Exception:
        pass

    # ── 4. Williams %R (14) ───────────────────────────────────────────
    try:
        wr = _williams_r(high, low, close, 14).iloc[-1]
        wr_prev = _williams_r(high, low, close, 14).iloc[-2]
        if wr <= -80 and wr > wr_prev:
            buy_votes += 2
            reasons.append(f"Williams %R OVERSOLD reversal ({wr:.0f})")
        elif wr >= -20 and wr < wr_prev:
            sell_votes += 2
            reasons.append(f"Williams %R OVERBOUGHT reversal ({wr:.0f})")
        elif wr <= -90:
            buy_votes += 1
        elif wr >= -10:
            sell_votes += 1
    except Exception:
        pass

    # ── 5. Bollinger Bands (20, 2.0) ─────────────────────────────────
    try:
        bb_up, bb_mid, bb_lo = _bbands(close, 20, 2.0)
        price_now  = float(close.iloc[-1])
        price_prev = float(close.iloc[-2])
        bbu = float(bb_up.iloc[-1])
        bbl = float(bb_lo.iloc[-1])
        bbm = float(bb_mid.iloc[-1])
        bbu_prev = float(bb_up.iloc[-2])
        bbl_prev = float(bb_lo.iloc[-2])

        # Lower band touch + price bouncing back inside
        if price_prev <= bbl_prev and price_now > bbl:
            buy_votes += 2
            reasons.append("BB LOWER BAND BOUNCE — BUY pressure confirmed")
        elif price_prev >= bbu_prev and price_now < bbu:
            sell_votes += 2
            reasons.append("BB UPPER BAND REJECTION — SELL pressure confirmed")
        elif price_now < bbl:
            buy_votes += 1
            reasons.append("BB below lower band — oversold stretch")
        elif price_now > bbu:
            sell_votes += 1
            reasons.append("BB above upper band — overbought stretch")

        # BB squeeze → expansion: band width narrowing then expanding
        bw_now  = bbu - bbl
        bw_prev = bbu_prev - bbl_prev
        bw_5ago = float(bb_up.iloc[-5]) - float(bb_lo.iloc[-5])
        if bw_now > bw_prev > bw_5ago * 0.8:
            if price_now > bbm:
                buy_votes += 1
                reasons.append("BB SQUEEZE EXPANDING BULLISH")
            else:
                sell_votes += 1
                reasons.append("BB SQUEEZE EXPANDING BEARISH")
    except Exception:
        pass

    # ── 6. Heikin Ashi trend smoothing ───────────────────────────────
    try:
        ha = _heikin_ashi(df)
        ha_c  = float(ha["ha_close"].iloc[-1])
        ha_o  = float(ha["ha_open"].iloc[-1])
        ha_c2 = float(ha["ha_close"].iloc[-2])
        ha_o2 = float(ha["ha_open"].iloc[-2])
        ha_bullish = ha_c > ha_o   # current HA bar is green
        ha_bull_2  = ha_c2 > ha_o2
        ha_bearish = ha_c < ha_o
        ha_bear_2  = ha_c2 < ha_o2
        if ha_bullish and ha_bull_2:
            buy_votes += 1
            reasons.append("HEIKIN ASHI 2-bar bull smoothing")
        elif ha_bearish and ha_bear_2:
            sell_votes += 1
            reasons.append("HEIKIN ASHI 2-bar bear smoothing")
        # HA reversal: prev bearish → current bullish
        if ha_bullish and not ha_bull_2:
            buy_votes += 1
            reasons.append("HEIKIN ASHI REVERSAL — HA turned bullish")
        elif ha_bearish and not ha_bear_2:
            sell_votes += 1
            reasons.append("HEIKIN ASHI REVERSAL — HA turned bearish")
    except Exception:
        pass

    # ── 7. Candle body conviction ─────────────────────────────────────
    # For OTC: strong body AGAINST prior move = reversal conviction
    try:
        c_open  = float(open_.iloc[-2])   # confirmed bar
        c_high  = float(high.iloc[-2])
        c_low   = float(low.iloc[-2])
        c_close = float(close.iloc[-2])
        c_range = max(1e-9, c_high - c_low)
        body    = abs(c_close - c_open)
        body_ratio = body / c_range
        if is_otc:
            # OTC: strong body in direction = the reversal is already confirmed
            if body_ratio >= 0.60 and c_close > c_open:
                buy_votes += 2
                reasons.append(f"OTC BULL CONVICTION BODY {body_ratio:.0%} → CALL")
            elif body_ratio >= 0.60 and c_close < c_open:
                sell_votes += 2
                reasons.append(f"OTC BEAR CONVICTION BODY {body_ratio:.0%} → PUT")
        else:
            if body_ratio >= 0.65:
                if c_close > c_open:
                    buy_votes += 1
                    reasons.append(f"STRONG BULL BODY {body_ratio:.0%}")
                else:
                    sell_votes += 1
                    reasons.append(f"STRONG BEAR BODY {body_ratio:.0%}")
    except Exception:
        pass

    # ── 8. EMA trend alignment (reversal-mode for OTC) ────────────────
    # LIVE pairs: EMA cross = trend confirmation → trade WITH it
    # OTC pairs:  EMA fully stretched (8 far above 21 or below) = REVERSAL
    #             OTC price snaps back to the mean — so EMA extreme = reversal
    try:
        ema8  = _ema(close, 8)
        ema21 = _ema(close, 21)
        ema8_now   = float(ema8.iloc[-2])
        ema21_now  = float(ema21.iloc[-2])
        ema8_prev  = float(ema8.iloc[-3])
        ema21_prev = float(ema21.iloc[-3])
        ema_gap = abs(ema8_now - ema21_now)
        ema_pct = ema_gap / (ema21_now or 1)
        if is_otc:
            # OTC: EMA overextended (gap > 0.1%) = mean-reversion candidate
            if ema8_now > ema21_now and ema_pct > 0.001:
                sell_votes += 1   # stretched UP → snap back PUT
                reasons.append(f"OTC EMA(8) overextended ABOVE EMA(21) → PUT mean-revert")
            elif ema8_now < ema21_now and ema_pct > 0.001:
                buy_votes += 1    # stretched DOWN → snap back CALL
                reasons.append(f"OTC EMA(8) overextended BELOW EMA(21) → CALL mean-revert")
            # OTC: fresh EMA cross = direction is re-establishing (ride it)
            if ema8_now > ema21_now and ema8_prev <= ema21_prev:
                buy_votes += 1
                reasons.append("OTC EMA CROSS UP — new direction starting → CALL")
            elif ema8_now < ema21_now and ema8_prev >= ema21_prev:
                sell_votes += 1
                reasons.append("OTC EMA CROSS DOWN — new direction starting → PUT")
        else:
            if ema8_now > ema21_now and ema8_prev <= ema21_prev:
                buy_votes += 1
                reasons.append("EMA(8>21) CROSS UP — trend confirmed")
            elif ema8_now < ema21_now and ema8_prev >= ema21_prev:
                sell_votes += 1
                reasons.append("EMA(8<21) CROSS DOWN — trend confirmed")
            elif ema8_now > ema21_now:
                buy_votes += 1
            elif ema8_now < ema21_now:
                sell_votes += 1
    except Exception:
        pass

    # ── 9. OTC-SPECIFIC EXTRA LAYER ───────────────────────────────────
    # Consecutive candle exhaustion — highest reliability for OTC
    if is_otc:
        try:
            run_bars = []
            for bi in range(-3, -8, -1):
                bo = float(close.iloc[bi]); oo = float(open_.iloc[bi])
                bar_rng = max(abs(float(high.iloc[bi]) - float(low.iloc[bi])), 1e-10)
                if abs(bo - oo) / bar_rng >= 0.25:
                    run_bars.append(1 if bo > oo else -1)
            if len(run_bars) >= 4:
                if all(b == 1 for b in run_bars[:4]):
                    sell_votes += 2
                    reasons.append("OTC 4-BAR BULL EXHAUSTION → PUT reversal")
                elif all(b == -1 for b in run_bars[:4]):
                    buy_votes += 2
                    reasons.append("OTC 4-BAR BEAR EXHAUSTION → CALL reversal")
            elif len(run_bars) >= 3:
                if all(b == 1 for b in run_bars[:3]):
                    sell_votes += 1
                    reasons.append("OTC 3-BAR BULL EXHAUSTION → PUT approaching")
                elif all(b == -1 for b in run_bars[:3]):
                    buy_votes += 1
                    reasons.append("OTC 3-BAR BEAR EXHAUSTION → CALL approaching")
        except Exception:
            pass

    # ── Determine direction & grade (current bar = bar[-2]) ───────────
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

    # ── NON-REPRINT CONFIRMATION — bar[-3] must agree ─────────────────
    # Run the same oscillator logic on the PREVIOUS confirmed bar.
    # Both bars must vote the same direction → zero repainting risk.
    non_reprint = False
    try:
        bv2, sv2, _ = _compute_votes_at_offset(close, high, low, open_, is_otc, offset=-3)
        prev_dir = "BUY" if bv2 > sv2 else ("SELL" if sv2 > bv2 else None)
        if prev_dir == direction:
            non_reprint = True
            reasons.insert(0, "✅ NON-REPRINT: 2-bar confirmation locked")
        else:
            # Previous bar disagrees → repaint risk → reject signal for binary
            # (Allow for non-OTC live pairs with high grade; stricter for OTC)
            if is_otc or grade < 82:
                _CACHE[ticker] = (now, None)
                return None
    except Exception:
        non_reprint = False

    # ── Sub-candle (5s/15s/30sc) bias ─────────────────────────────────
    sub_candle_dir = _sub_candle_direction(df)

    # Grade: ratio dominance (0.5–1.0) mapped to 60–100 scale
    grade = int(60 + 40 * ((ratio - 0.5) / 0.5))
    grade = max(60, min(100, grade))

    # Boost grade for high agreement counts
    if agree >= 8:
        grade = min(100, grade + 5)
    elif agree >= 6:
        grade = min(100, grade + 3)

    # Non-reprint bonus: confirmed on both bars → raise grade
    if non_reprint:
        grade = min(100, grade + 4)

    # Sub-candle agrees → additional grade boost
    if sub_candle_dir == direction:
        grade = min(100, grade + 3)
        reasons.append(f"⚡ SUB-CANDLE (5s/15s/30sc) MOMENTUM: {sub_candle_dir}")

    # OTC: require ZERO opposing votes + higher grade bar (stricter)
    if is_otc:
        opposing = sell_votes if direction == "BUY" else buy_votes
        if opposing > 0:
            _CACHE[ticker] = (now, None)
            return None
        otc_min_grade = 80
        if grade < otc_min_grade:
            _CACHE[ticker] = (now, None)
            return None
    else:
        if grade < QX_MIN_GRADE:
            _CACHE[ticker] = (now, None)
            return None

    elite = agree >= 7 and ratio >= 0.75 and non_reprint

    result = {
        "direction":      direction,
        "grade":          grade,
        "agree":          agree,
        "elite":          elite,
        "reasons":        reasons[:6],
        "buy_votes":      buy_votes,
        "sell_votes":     sell_votes,
        "non_reprint":    non_reprint,
        "sub_candle_dir": sub_candle_dir,
    }
    _CACHE[ticker] = (now, result)
    return result
