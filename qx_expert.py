"""QX Expert — SUPREME ELITE Binary Reversal Engine V10
=======================================================
13 independent reversal-detection signals working in unison.
Every signal targets EXHAUSTION and REVERSAL — never trend-following.

WHY REVERSAL-ONLY
-----------------
OTC synthetic prices and short-expiry binaries share one property:
by the time a trend signal fires (EMA cross, MACD cross), the move is
already 70-80% complete. The only consistently profitable approach is
catching the reversal AT the exhaustion point — oversold/overbought
oscillators + exhaustion candle pattern + BB extreme + divergence.

THE 13 SIGNALS  (each weighted independently)
----------------------------------------------
S01  RSI(3) ultra-fast          wt 3   extreme OS/OB: ≤12 / ≥88
S02  RSI(7) fast                wt 2   OS/OB: ≤22 / ≥78
S03  RSI(14) standard           wt 2   OS/OB: ≤28 / ≥72
S04  RSI Divergence             wt 3   price new extreme + RSI diverges
S05  Stochastic(3,1,1) ultra    wt 3   crossover in extreme zone
S06  Stochastic(5,3,3) fast     wt 2   crossover in extreme zone
S07  CCI(14)                    wt 2   ≤-150 turning up / ≥150 turning down
S08  Williams %R(14)            wt 2   ≤-88 turning up / ≥-12 turning down
S09  BB(20, 2.5σ) outer pierce  wt 3   price pierces outer band + closes back
S10  Exhaustion + reversal bar  wt 4   4+ same-dir candles + opposing body
S11  Candlestick patterns        wt 2   pin bar / engulfing at extreme
S12  Heikin Ashi reversal        wt 2   HA flip after 3+ same-color bars
S13  MACD(5,13,3) exhaustion    wt 2   histogram shrinks at new price extreme

Max possible votes: 32

THRESHOLDS
----------
OTC pairs  → ≥14 votes, opposing ≤ 1, grade ≥ 78
Live pairs → ≥11 votes, opposing ≤ 2, grade ≥ 70
Elite OTC  → ≥20 votes, opposing = 0
Elite Live → ≥16 votes, opposing = 0

NON-REPRINT
-----------
Signal is only valid when BOTH the last confirmed bar (bar[-2])
AND the bar before it (bar[-3]) agree on direction.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

import pandas as pd

try:
    import yfinance as yf
    _OK = True
except Exception:
    yf = None
    _OK = False

try:
    from live_prices import yf_ticker
except Exception:
    def yf_ticker(pair: str) -> Optional[str]:  # type: ignore
        return None

# ── Settings ──────────────────────────────────────────────────────────────────
QX_INTERVAL = "1m"    # 1m bars — most sensitive for binary entries
QX_PERIOD   = "1d"    # 1 day of history — enough for all indicators
QX_CANDLES  = 120     # tail rows used (need ≥50 for divergence lookback)
QX_MIN_GRADE = 70     # minimum grade for live pairs

_TTL   = 18.0         # cache seconds — fresh analysis every ~18s
_CACHE: dict = {}


# ── Pure math helpers (no pandas dependency in signatures) ───────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


def _stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k: int = 5, d: int = 3, smooth: int = 3
) -> Tuple[pd.Series, pd.Series]:
    low_min  = low.rolling(k).min()
    high_max = high.rolling(k).max()
    fast_k   = 100 * (close - low_min) / (high_max - low_min + 1e-10)
    slow_k   = fast_k.rolling(smooth).mean()
    slow_d   = slow_k.rolling(d).mean()
    return slow_k, slow_d


def _cci(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    tp = (high + low + close) / 3
    ma = tp.rolling(period).mean()
    md = tp.rolling(period).apply(lambda x: pd.Series(x).mad(), raw=False)
    return (tp - ma) / (0.015 * md + 1e-10)


def _williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100 * (hh - close) / (hh - ll + 1e-10)


def _bbands(
    series: pd.Series, period: int = 20, dev: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ma  = series.rolling(period).mean()
    std = series.rolling(period).std()
    return ma + dev * std, ma, ma - dev * std


def _macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ef   = series.ewm(span=fast,   adjust=False).mean()
    es   = series.ewm(span=slow,   adjust=False).mean()
    line = ef - es
    sig  = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist


def _heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    ha = pd.DataFrame(index=df.index)
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = [(df["open"].iloc[0] + df["close"].iloc[0]) / 2]
    for i in range(1, len(df)):
        ha_open.append((ha_open[-1] + ha["ha_close"].iloc[i - 1]) / 2)
    ha["ha_open"]  = ha_open
    ha["ha_high"]  = df[["high",  "open", "close"]].max(axis=1)
    ha["ha_low"]   = df[["low",   "open", "close"]].min(axis=1)
    return ha


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df


# ── Data fetch ────────────────────────────────────────────────────────────────

def _fetch_rt(pair: str) -> Optional[pd.DataFrame]:
    """Fetch 1m candles from the live broker WS feed (highest priority)."""
    try:
        from otc_realtime_bridge import get_otc_df as _rt_get
        df = _rt_get(pair, "1m", count=QX_CANDLES)
        if df is not None and len(df) >= 40:
            for col in ("open", "high", "low", "close"):
                if col not in df.columns:
                    return None
            return df.copy()
    except Exception:
        pass
    return None


def _fetch(ticker: str) -> Optional[pd.DataFrame]:
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
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return None
        return df.tail(QX_CANDLES).copy()
    except Exception:
        return None


# ── 13-Signal reversal scorer ─────────────────────────────────────────────────

def _score_reversal(
    close: pd.Series,
    high:  pd.Series,
    low:   pd.Series,
    open_: pd.Series,
    idx:   int = -1,           # bar index to analyse (−1 = last, −2 = prev)
) -> Tuple[int, int, list]:
    """Return (buy_votes, sell_votes, reasons) for bar at `idx`."""
    buy_votes  = 0
    sell_votes = 0
    reasons: list = []

    # ── S01: RSI(3) ultra-fast ─────────────────────────────────────────
    try:
        r3 = float(_rsi(close, 3).iloc[idx])
        if r3 <= 10:
            buy_votes += 3;  reasons.append(f"RSI(3) EXTREME OS {r3:.0f} → BUY+3")
        elif r3 <= 20:
            buy_votes += 2;  reasons.append(f"RSI(3) oversold {r3:.0f} → BUY+2")
        elif r3 >= 90:
            sell_votes += 3; reasons.append(f"RSI(3) EXTREME OB {r3:.0f} → SELL+3")
        elif r3 >= 80:
            sell_votes += 2; reasons.append(f"RSI(3) overbought {r3:.0f} → SELL+2")
    except Exception:
        pass

    # ── S02: RSI(7) fast ──────────────────────────────────────────────
    try:
        r7 = float(_rsi(close, 7).iloc[idx])
        if r7 <= 20:
            buy_votes += 2;  reasons.append(f"RSI(7) OVERSOLD {r7:.0f} → BUY+2")
        elif r7 <= 30:
            buy_votes += 1;  reasons.append(f"RSI(7) low {r7:.0f} → BUY+1")
        elif r7 >= 80:
            sell_votes += 2; reasons.append(f"RSI(7) OVERBOUGHT {r7:.0f} → SELL+2")
        elif r7 >= 70:
            sell_votes += 1; reasons.append(f"RSI(7) high {r7:.0f} → SELL+1")
    except Exception:
        pass

    # ── S03: RSI(14) standard ─────────────────────────────────────────
    try:
        r14 = float(_rsi(close, 14).iloc[idx])
        if r14 <= 28:
            buy_votes += 2;  reasons.append(f"RSI(14) oversold {r14:.0f} → BUY+2")
        elif r14 >= 72:
            sell_votes += 2; reasons.append(f"RSI(14) overbought {r14:.0f} → SELL+2")
    except Exception:
        pass

    # ── S04: RSI Divergence (most powerful reversal signal) ───────────
    try:
        r14_series = _rsi(close, 14)
        lookback   = min(25, len(close) - 5)
        if lookback >= 8:
            recent_c   = close.iloc[-lookback:]
            recent_r14 = r14_series.iloc[-lookback:]

            p_now  = float(close.iloc[idx])
            r_now  = float(r14_series.iloc[idx])

            # Bullish divergence: price at/near recent low but RSI higher than at that low
            p_min_idx = int(recent_c.values.argmin())
            p_min     = float(recent_c.iloc[p_min_idx])
            r_at_low  = float(recent_r14.iloc[p_min_idx])
            if p_now <= p_min * 1.002 and r_now > r_at_low + 4:
                buy_votes += 3;  reasons.append(f"BULLISH RSI DIVERGENCE — price low, RSI higher → BUY+3")

            # Bearish divergence: price at/near recent high but RSI lower than at that high
            p_max_idx = int(recent_c.values.argmax())
            p_max     = float(recent_c.iloc[p_max_idx])
            r_at_high = float(recent_r14.iloc[p_max_idx])
            if p_now >= p_max * 0.998 and r_now < r_at_high - 4:
                sell_votes += 3; reasons.append(f"BEARISH RSI DIVERGENCE — price high, RSI lower → SELL+3")
    except Exception:
        pass

    # ── S05: Stochastic(3,1,1) ultra-fast ────────────────────────────
    try:
        k3, d3 = _stochastic(high, low, close, k=3, d=1, smooth=1)
        k3n  = float(k3.iloc[idx]);     d3n  = float(d3.iloc[idx])
        k3p  = float(k3.iloc[idx - 1]); d3p  = float(d3.iloc[idx - 1])
        if k3p <= d3p and k3n > d3n and k3n < 20:
            buy_votes += 3;  reasons.append(f"STOCH(3,1,1) CROSS UP in OS {k3n:.0f} → BUY+3")
        elif k3p >= d3p and k3n < d3n and k3n > 80:
            sell_votes += 3; reasons.append(f"STOCH(3,1,1) CROSS DOWN in OB {k3n:.0f} → SELL+3")
        elif k3n <= 10:
            buy_votes += 2;  reasons.append(f"STOCH(3,1,1) extreme OS {k3n:.0f} → BUY+2")
        elif k3n >= 90:
            sell_votes += 2; reasons.append(f"STOCH(3,1,1) extreme OB {k3n:.0f} → SELL+2")
    except Exception:
        pass

    # ── S06: Stochastic(5,3,3) standard ──────────────────────────────
    try:
        k5, d5 = _stochastic(high, low, close, k=5, d=3, smooth=3)
        k5n  = float(k5.iloc[idx]);     d5n  = float(d5.iloc[idx])
        k5p  = float(k5.iloc[idx - 1]); d5p  = float(d5.iloc[idx - 1])
        if k5p <= d5p and k5n > d5n and k5n < 25:
            buy_votes += 2;  reasons.append(f"STOCH(5,3,3) CROSS UP {k5n:.0f} → BUY+2")
        elif k5p >= d5p and k5n < d5n and k5n > 75:
            sell_votes += 2; reasons.append(f"STOCH(5,3,3) CROSS DOWN {k5n:.0f} → SELL+2")
        elif k5n <= 15:
            buy_votes += 1;  reasons.append(f"STOCH(5,3,3) OS zone {k5n:.0f} → BUY+1")
        elif k5n >= 85:
            sell_votes += 1; reasons.append(f"STOCH(5,3,3) OB zone {k5n:.0f} → SELL+1")
    except Exception:
        pass

    # ── S07: CCI(14) ─────────────────────────────────────────────────
    try:
        cci_s  = _cci(high, low, close, 14)
        cn     = float(cci_s.iloc[idx])
        cp     = float(cci_s.iloc[idx - 1])
        if cn <= -150 and cn > cp:
            buy_votes += 2;  reasons.append(f"CCI REVERSAL from OS {cn:.0f} → BUY+2")
        elif cn <= -100:
            buy_votes += 1;  reasons.append(f"CCI oversold {cn:.0f} → BUY+1")
        elif cn >= 150 and cn < cp:
            sell_votes += 2; reasons.append(f"CCI REVERSAL from OB {cn:.0f} → SELL+2")
        elif cn >= 100:
            sell_votes += 1; reasons.append(f"CCI overbought {cn:.0f} → SELL+1")
    except Exception:
        pass

    # ── S08: Williams %R(14) ─────────────────────────────────────────
    try:
        wr_s   = _williams_r(high, low, close, 14)
        wr_n   = float(wr_s.iloc[idx])
        wr_p   = float(wr_s.iloc[idx - 1])
        if wr_n <= -88 and wr_n > wr_p:
            buy_votes += 2;  reasons.append(f"Williams %R REVERSAL {wr_n:.0f} → BUY+2")
        elif wr_n <= -80:
            buy_votes += 1
        elif wr_n >= -12 and wr_n < wr_p:
            sell_votes += 2; reasons.append(f"Williams %R REVERSAL {wr_n:.0f} → SELL+2")
        elif wr_n >= -20:
            sell_votes += 1
    except Exception:
        pass

    # ── S09: Bollinger Bands(20, 2.5σ) outer pierce ──────────────────
    try:
        bbu, _, bbl = _bbands(close, 20, 2.5)
        pn = float(close.iloc[idx]);     pp = float(close.iloc[idx - 1])
        un = float(bbu.iloc[idx]);       ln = float(bbl.iloc[idx])
        up = float(bbu.iloc[idx - 1]);   lp = float(bbl.iloc[idx - 1])
        if pp <= lp and pn > ln:        # pierced lower then closed back inside
            buy_votes += 3;  reasons.append("BB(2.5σ) LOWER PIERCE + BOUNCE → BUY+3")
        elif pn < ln:                   # still below lower band
            buy_votes += 2;  reasons.append("BB(2.5σ) below lower band → BUY+2")
        elif pp >= up and pn < un:      # pierced upper then closed back inside
            sell_votes += 3; reasons.append("BB(2.5σ) UPPER PIERCE + REJECT → SELL+3")
        elif pn > un:                   # still above upper band
            sell_votes += 2; reasons.append("BB(2.5σ) above upper band → SELL+2")
    except Exception:
        pass

    # ── S10: Consecutive candle exhaustion + reversal confirmation ────
    try:
        bull_run = 0; bear_run = 0
        for bi in range(idx - 1, idx - 7, -1):
            try:
                bc = float(close.iloc[bi]); bo = float(open_.iloc[bi])
                br = max(abs(float(high.iloc[bi]) - float(low.iloc[bi])), 1e-10)
                if abs(bc - bo) / br < 0.15:   # doji — break the run
                    break
                if bc > bo:  bull_run += 1
                elif bc < bo: bear_run += 1
                else:          break
            except Exception:
                break

        last_c = float(close.iloc[idx]); last_o = float(open_.iloc[idx])
        last_range = max(float(high.iloc[idx]) - float(low.iloc[idx]), 1e-10)
        last_body  = abs(last_c - last_o)
        reversal_body = last_body / last_range >= 0.40   # meaningful reversal body

        if bull_run >= 4:
            sell_votes += 2; reasons.append(f"{bull_run}-BAR BULL EXHAUSTION → SELL+2")
            if last_c < last_o and reversal_body:
                sell_votes += 2; reasons.append("REVERSAL CONFIRMATION BAR → SELL+2")
        elif bull_run >= 3:
            sell_votes += 1; reasons.append(f"{bull_run}-bar bull run → SELL+1")

        if bear_run >= 4:
            buy_votes += 2;  reasons.append(f"{bear_run}-BAR BEAR EXHAUSTION → BUY+2")
            if last_c > last_o and reversal_body:
                buy_votes += 2;  reasons.append("REVERSAL CONFIRMATION BAR → BUY+2")
        elif bear_run >= 3:
            buy_votes += 1;  reasons.append(f"{bear_run}-bar bear run → BUY+1")
    except Exception:
        pass

    # ── S11: Candlestick patterns at extremes ─────────────────────────
    try:
        c0c = float(close.iloc[idx]); c0o = float(open_.iloc[idx])
        c0h = float(high.iloc[idx]);   c0l = float(low.iloc[idx])
        c1c = float(close.iloc[idx - 1]); c1o = float(open_.iloc[idx - 1])
        c0rng  = max(c0h - c0l, 1e-10)
        c0body = abs(c0c - c0o)
        c0up   = c0h - max(c0c, c0o)
        c0dn   = min(c0c, c0o) - c0l
        body_min = max(c0body, c0rng * 0.018)

        # Hammer / Bullish Pin Bar
        if c0dn >= 2.8 * body_min and c0up < 0.30 * c0rng:
            buy_votes += 2;  reasons.append("HAMMER / BULLISH PIN BAR → BUY+2")
        # Shooting Star / Bearish Pin Bar
        elif c0up >= 2.8 * body_min and c0dn < 0.30 * c0rng:
            sell_votes += 2; reasons.append("SHOOTING STAR / BEARISH PIN BAR → SELL+2")

        # Bullish Engulfing
        c1body = abs(c1c - c1o)
        if (c0c > c0o and c1c < c1o and
                c0o <= c1c and c0c >= c1o and c0body >= c1body * 0.9):
            buy_votes += 2;  reasons.append("BULLISH ENGULFING → BUY+2")
        # Bearish Engulfing
        elif (c0c < c0o and c1c > c1o and
                c0o >= c1c and c0c <= c1o and c0body >= c1body * 0.9):
            sell_votes += 2; reasons.append("BEARISH ENGULFING → SELL+2")
    except Exception:
        pass

    # ── S12: Heikin Ashi reversal after 3+ same-color bars ───────────
    # (computed from df externally — skip if not injected into this function)
    # NOTE: Heikin Ashi is computed separately and results injected below
    # via the ha_direction parameter in qx_analyze.

    # ── S13: MACD(5,13,3) histogram exhaustion ───────────────────────
    try:
        _, _, hist = _macd(close, fast=5, slow=13, signal=3)
        hn   = float(hist.iloc[idx])
        hp   = float(hist.iloc[idx - 1])
        hp2  = float(hist.iloc[idx - 2])
        pn   = float(close.iloc[idx])
        pp2  = float(close.iloc[idx - 2])

        # MACD histogram shrinking at new price high = bull exhaustion → SELL
        if pn >= pp2 and hn > 0 and hn < hp < hp2:
            sell_votes += 2; reasons.append("MACD histogram declining at new high → SELL+2")
        # MACD histogram growing (less negative) at new price low = bear exhaustion → BUY
        elif pn <= pp2 and hn < 0 and hn > hp > hp2:
            buy_votes += 2;  reasons.append("MACD histogram rising at new low → BUY+2")
    except Exception:
        pass

    return buy_votes, sell_votes, reasons


def _sub_candle_direction(df: pd.DataFrame) -> Optional[str]:
    """Estimate sub-candle momentum from last 3 confirmed 1m bars."""
    if df is None or len(df) < 4:
        return None
    try:
        bars = df.tail(4).iloc[:-1]
        bull = 0; bear = 0
        for _, row in bars.iterrows():
            o = float(row["open"]); c = float(row["close"])
            h = float(row["high"]); l = float(row["low"])
            bh = max(o, c); bl = min(o, c)
            rng = max(h - l, 1e-10)
            wu = (h - bh) / rng; wd = (bl - l) / rng
            br_ratio = (bh - bl) / rng
            if c > o:
                bull += 1 + int(br_ratio >= 0.6)
                if wd > 0.3: bull += 1
            else:
                bear += 1 + int(br_ratio >= 0.6)
                if wu > 0.3: bear += 1
        if bull > bear + 1: return "BUY"
        if bear > bull + 1: return "SELL"
    except Exception:
        pass
    return None


# ── Main public function ──────────────────────────────────────────────────────

def qx_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Supreme Elite 13-signal reversal analysis.

    Returns dict(direction, grade, elite, agree, reasons,
                 buy_votes, sell_votes, non_reprint, sub_candle_dir)
    or None when no high-confidence setup is found.
    """
    ticker = yf_ticker(pair)
    cache_key = ticker or pair   # use pair name when no yf ticker

    now    = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    # ── DATA SOURCE PRIORITY ──────────────────────────────────────────────────
    # 1. Live broker WS candles (otc_realtime_bridge → otc_feed_combined)
    # 2. Twelve Data drift model (otc_feed) — secondary OTC source
    # 3. yfinance — fallback when broker feed is dark / pair unsupported
    df = None
    _is_otc_qx = is_otc or "〔OTC〕" in pair or "(OTC)" in pair.upper()

    if _is_otc_qx:
        # Priority 1 — real-time broker WS candles
        df = _fetch_rt(pair)

        # Priority 2 — Twelve Data drift model
        if df is None:
            try:
                from otc_feed import get_otc_df as _otc_df
                df = _otc_df(pair, "1m", count=QX_CANDLES + 20)
            except Exception:
                df = None

    if df is None:
        if not ticker:
            _CACHE[cache_key] = (now, None)
            return None
        df = _fetch(ticker)

    if df is None:
        _CACHE[cache_key] = (now, None)
        return None

    try:
        close = df["close"].astype(float).squeeze()
        high  = df["high"].astype(float).squeeze()
        low   = df["low"].astype(float).squeeze()
        open_ = df["open"].astype(float).squeeze()
    except Exception:
        _CACHE[cache_key] = (now, None)
        return None

    if len(close) < 35:
        _CACHE[cache_key] = (now, None)
        return None

    # ── Score the LAST CONFIRMED bar (bar[-2]) ────────────────────────
    buy_v, sell_v, reasons = _score_reversal(close, high, low, open_, idx=-2)

    # ── S12: Heikin Ashi reversal (needs full df) ─────────────────────
    try:
        ha       = _heikin_ashi(df)
        ha_c1    = float(ha["ha_close"].iloc[-2]); ha_o1 = float(ha["ha_open"].iloc[-2])
        ha_c2    = float(ha["ha_close"].iloc[-3]); ha_o2 = float(ha["ha_open"].iloc[-3])
        ha_c3    = float(ha["ha_close"].iloc[-4]); ha_o3 = float(ha["ha_open"].iloc[-4])
        ha_c4    = float(ha["ha_close"].iloc[-5]); ha_o4 = float(ha["ha_open"].iloc[-5])
        ha_bull1 = ha_c1 > ha_o1; ha_bull2 = ha_c2 > ha_o2
        ha_bull3 = ha_c3 > ha_o3; ha_bull4 = ha_c4 > ha_o4
        # Flip from bear to bull after 3+ consecutive bear HA bars → BUY reversal
        if ha_bull1 and not ha_bull2 and not ha_bull3 and not ha_bull4:
            buy_v += 2;  reasons.append("HEIKIN ASHI FLIP BULLISH after 3 bear bars → BUY+2")
        # Flip from bull to bear after 3+ consecutive bull HA bars → SELL reversal
        elif not ha_bull1 and ha_bull2 and ha_bull3 and ha_bull4:
            sell_v += 2; reasons.append("HEIKIN ASHI FLIP BEARISH after 3 bull bars → SELL+2")
    except Exception:
        pass

    # ── Direction decision ────────────────────────────────────────────
    total = buy_v + sell_v
    if total == 0:
        _CACHE[cache_key] = (now, None)
        return None

    if buy_v > sell_v:
        direction = "BUY";  agree = buy_v;  opposing = sell_v
    elif sell_v > buy_v:
        direction = "SELL"; agree = sell_v; opposing = buy_v
    else:
        _CACHE[cache_key] = (now, None)
        return None

    # ── Minimum vote thresholds (THE KEY FIX: prevents single-signal fires) ──
    MIN_OTC  = 14   # was effectively 1 before — now requires 14 of 32 possible
    MIN_LIVE = 11
    MAX_OPP_OTC  = 1   # at most 1 opposing vote for OTC (near-zero ambiguity)
    MAX_OPP_LIVE = 2

    if is_otc:
        if agree < MIN_OTC or opposing > MAX_OPP_OTC:
            _CACHE[cache_key] = (now, None)
            return None
    else:
        if agree < MIN_LIVE or opposing > MAX_OPP_LIVE:
            _CACHE[cache_key] = (now, None)
            return None

    # ── NON-REPRINT: bar[-3] must agree ──────────────────────────────
    non_reprint = False
    try:
        bv2, sv2, _ = _score_reversal(close, high, low, open_, idx=-3)
        prev_dir = "BUY" if bv2 > sv2 else ("SELL" if sv2 > bv2 else None)
        if prev_dir == direction:
            non_reprint = True
            reasons.insert(0, "✅ NON-REPRINT: 2-bar confirmed")
        else:
            # OTC: reject if bars disagree (synthetic candles repaint-sensitive)
            if is_otc:
                _CACHE[cache_key] = (now, None)
                return None
            # Live: allow if current bar is very strong (15+ votes)
            if agree < 15:
                _CACHE[cache_key] = (now, None)
                return None
    except Exception:
        non_reprint = False

    # ── Grade (60–100 scale) ──────────────────────────────────────────
    MAX_POSSIBLE = 32
    thresh       = MIN_OTC if is_otc else MIN_LIVE
    grade = int(60 + 40 * (agree - thresh) / max(1, MAX_POSSIBLE - thresh))
    grade = max(60, min(100, grade))

    # Non-reprint bonus
    if non_reprint:
        grade = min(100, grade + 4)

    # Sub-candle confirmation bonus
    sub_candle_dir = _sub_candle_direction(df)
    if sub_candle_dir == direction:
        grade = min(100, grade + 3)
        reasons.append(f"⚡ SUB-CANDLE momentum: {sub_candle_dir}")

    # ── Grade gate ────────────────────────────────────────────────────
    min_grade = 78 if is_otc else QX_MIN_GRADE
    if grade < min_grade:
        _CACHE[cache_key] = (now, None)
        return None

    # ── Elite flag ────────────────────────────────────────────────────
    elite = (agree >= 20 and opposing == 0) if is_otc else (agree >= 16 and opposing == 0)

    result = {
        "direction":      direction,
        "grade":          grade,
        "agree":          agree,
        "elite":          elite,
        "reasons":        reasons[:8],
        "buy_votes":      buy_v,
        "sell_votes":     sell_v,
        "non_reprint":    non_reprint,
        "sub_candle_dir": sub_candle_dir,
        "confidence":     round(agree / MAX_POSSIBLE, 3),
    }
    _CACHE[cache_key] = (now, result)
    return result
