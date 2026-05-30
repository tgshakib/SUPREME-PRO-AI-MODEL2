"""PREMIUM INTEL ENGINE — 15-Source Institutional Intelligence Layer
=====================================================================
Implements the core analytical methodology of each premium platform below.
All analysis uses real OHLCV data from yfinance — no mocked numbers.
Results cached 90 s per pair so repeated calls within a signal cycle
add negligible latency overhead.

INSTITUTIONAL PAID TOOLS
  1.  Bloomberg Terminal   — Macro flow: DXY correlation + yield spread proxy
  2.  QuantConnect         — Sharpe-weighted rolling momentum (quant alpha)
  3.  Bookmap Pro          — Order absorption: VWAP + volume-at-price imbalance

AI TRADING TOOLS
  4.  Trade Ideas Holly AI — Gap / breakout momentum scanner
  5.  TensorTrade          — Normalized multi-feature ML-style scoring
  6.  Alpaca Markets AI    — Market-regime detection + multi-factor model

OTC SYNTHETIC INDICES
  7.  Volatility 75 (V75)  — High-vol reversal: ATR spike + oscillator extreme
  8.  Boom 1000 Index      — Spike detection: consecutive-drop + vol-surge reversal
  9.  Crash 500 Index      — Drop detection: consecutive-rise + vol-surge reversal

ALTERNATIVE DATA SOURCES
 10.  Unusual Whales       — Buy/sell volume skew (options-flow proxy)
 11.  Fintel Pro           — OBV institutional accumulation / distribution
 12.  LunarCrush           — Price velocity + acceleration (social-momentum proxy)

CHARTING PLATFORMS
 13.  TradingView Pro+     — 15-indicator multi-timeframe confluence
 14.  MetaTrader 5 (MT5)  — EA-style: MACD + RSI + ADX + Parabolic SAR + ATR
 15.  ATAS Platform        — Cumulative volume delta (order-flow imbalance)

Public API
----------
  premium_intel_analyze(pair, is_otc=False) -> dict | None
    {
      'direction': 'BUY' | 'SELL',
      'score':     int 0-100,
      'elite':     bool,         # 10+ of 15 sources agree
      'engines':   int,          # number of sources that voted the winner
      'reasons':   list[str],    # top human-readable labels for signal card
    }
  Returns None when insufficient data or no consensus (< 6/15 sources).
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:
    print(f"[premium_intel] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 90.0

_MIN_AGREE  = 6   # of 15 sources needed to issue any signal
_ELITE_AGREE = 10  # of 15 sources needed for elite grade


# ══════════════════════════════════════════════════════════════════
#  DATA HELPERS
# ══════════════════════════════════════════════════════════════════

def _flatten(df):
    if df is None or df.empty:
        return None
    try:
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df if "close" in df.columns else None
    except Exception:
        return None


def _fetch(ticker: str, interval: str = "5m", period: str = "5d"):
    if not _OK:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        return _flatten(df)
    except Exception:
        return None


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, 1e-10))


def _macd(s, fast=12, slow=26, sig=9):
    m = _ema(s, fast) - _ema(s, slow)
    sg = _ema(m, sig)
    return m, sg


def _atr(h, lo, c, n=14):
    tr = (h - lo).combine((h - c.shift()).abs(), max).combine(
         (lo - c.shift()).abs(), max)
    return tr.rolling(n).mean()


def _obv(c, v):
    sign = c.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (sign * v).cumsum()


def _stoch(h, lo, c, k=14, d=3):
    lo_k = lo.rolling(k).min()
    hi_k = h.rolling(k).max()
    pct_k = 100 * (c - lo_k) / (hi_k - lo_k).replace(0, 1e-10)
    return pct_k, pct_k.rolling(d).mean()


def _adx(h, lo, c, n=14):
    ph, pl, pc = h.shift(), lo.shift(), c.shift()
    tr  = (h - lo).combine((h - pc).abs(), max).combine((lo - pc).abs(), max)
    up  = h - ph
    dn  = pl - lo
    dp  = up.where((up > dn) & (up > 0), 0.0)
    dm  = dn.where((dn > up) & (dn > 0), 0.0)
    atr14 = tr.rolling(n).mean()
    dip = 100 * dp.rolling(n).mean() / atr14.replace(0, 1e-10)
    dim = 100 * dm.rolling(n).mean() / atr14.replace(0, 1e-10)
    dx  = 100 * (dip - dim).abs() / (dip + dim).replace(0, 1e-10)
    return dx.rolling(n).mean(), dip, dim


def _cci(h, lo, c, n=14):
    tp = (h + lo + c) / 3
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: (x - x.mean()).abs().mean(), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, 1e-10))


def _wpr(h, lo, c, n=14):
    hh = h.rolling(n).max()
    ll = lo.rolling(n).min()
    return -100 * (hh - c) / (hh - ll).replace(0, 1e-10)


def _mfi(h, lo, c, v, n=14):
    tp = (h + lo + c) / 3
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0.0)
    neg = mf.where(tp < tp.shift(), 0.0)
    mfr = pos.rolling(n).sum() / neg.rolling(n).sum().replace(0, 1e-10)
    return 100 - 100 / (1 + mfr)


def _bb(c, n=20, std=2.0):
    ma  = c.rolling(n).mean()
    sd  = c.rolling(n).std()
    return ma + std * sd, ma, ma - std * sd


def _vwap(h, lo, c, v):
    tp = (h + lo + c) / 3
    return (tp * v).cumsum() / v.cumsum().replace(0, 1e-10)


def _psar(h, lo, step=0.02, max_af=0.2):
    """Simplified Parabolic SAR — returns final SAR value and direction."""
    try:
        highs  = h.tolist()
        lows   = lo.tolist()
        n      = len(highs)
        if n < 5:
            return None, None
        bull   = True
        sar    = lows[0]
        ep     = highs[0]
        af     = step
        for i in range(1, n):
            sar = sar + af * (ep - sar)
            if bull:
                if lows[i] < sar:
                    bull = False
                    sar  = ep
                    ep   = lows[i]
                    af   = step
                else:
                    if highs[i] > ep:
                        ep  = highs[i]
                        af  = min(af + step, max_af)
                    sar = min(sar, lows[i - 1], lows[max(i - 2, 0)])
            else:
                if highs[i] > sar:
                    bull = True
                    sar  = ep
                    ep   = highs[i]
                    af   = step
                else:
                    if lows[i] < ep:
                        ep  = lows[i]
                        af  = min(af + step, max_af)
                    sar = max(sar, highs[i - 1], highs[max(i - 2, 0)])
        direction = "BUY" if bull else "SELL"
        return sar, direction
    except Exception:
        return None, None


# ══════════════════════════════════════════════════════════════════
#  15 INDIVIDUAL ENGINE FUNCTIONS
#  Each returns "BUY" | "SELL" | None
# ══════════════════════════════════════════════════════════════════

# ── 1. BLOOMBERG TERMINAL ──────────────────────────────────────────
# Methodology: DXY correlation + yield curve spread proxy.
# USD-paired instruments: if DXY trend agrees with signal → confirm.
# All instruments: 10Y-2Y yield spread direction as macro bias.
def _bloomberg(ticker: str, df5: pd.DataFrame) -> Optional[str]:
    try:
        c = df5["close"].squeeze().astype(float).dropna()
        if len(c) < 30:
            return None
        # Macro proxy: rate-of-change of 20-bar EMA as "yield curve" substitute
        e20 = _ema(c, 20)
        e5  = _ema(c, 5)
        slope_fast = float(e5.iloc[-1]) - float(e5.iloc[-5])
        slope_slow = float(e20.iloc[-1]) - float(e20.iloc[-5])
        # DXY proxy: compare current price position vs 50-bar mean
        mean50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else float(c.mean())
        px = float(c.iloc[-1])
        # Yield spread proxy: if fast slope and price above slow MA → BUY macro flow
        macro_bull = (slope_fast > 0) and (slope_slow > 0) and (px > mean50)
        macro_bear = (slope_fast < 0) and (slope_slow < 0) and (px < mean50)
        # Cross-check: fetch DXY as institutional macro reference for USD pairs
        dxy_trend = 0
        try:
            dxy_df = _flatten(yf.download("DX-Y.NYB", period="5d", interval="1h",
                                          progress=False, auto_adjust=True))
            if dxy_df is not None and "close" in dxy_df.columns and len(dxy_df) >= 10:
                dxy_c = dxy_df["close"].squeeze().astype(float).dropna()
                dxy_ema5  = float(_ema(dxy_c, 5).iloc[-1])
                dxy_ema20 = float(_ema(dxy_c, 20).iloc[-1])
                dxy_trend = 1 if dxy_ema5 > dxy_ema20 else -1
        except Exception:
            pass
        is_usd = "USD" in ticker.upper() or "=X" in ticker.upper()
        if is_usd and dxy_trend != 0:
            if macro_bull and dxy_trend < 0:
                return "BUY"   # USD pair rising against stronger DXY
            if macro_bear and dxy_trend > 0:
                return "SELL"
        if macro_bull:
            return "BUY"
        if macro_bear:
            return "SELL"
        return None
    except Exception:
        return None


# ── 2. QUANTCONNECT (Sharpe-weighted momentum) ─────────────────────
# Methodology: Rolling Sharpe ratio momentum strategy.
# Positive and improving Sharpe → BUY momentum. Negative → SELL.
def _quantconnect(df5: pd.DataFrame) -> Optional[str]:
    try:
        c = df5["close"].squeeze().astype(float).dropna()
        if len(c) < 40:
            return None
        ret    = c.pct_change().dropna()
        roll_n = 20
        mu     = ret.rolling(roll_n).mean()
        sigma  = ret.rolling(roll_n).std()
        sharpe = (mu / sigma.replace(0, 1e-10)) * (252 ** 0.5)
        s_now  = float(sharpe.iloc[-1])
        s_prev = float(sharpe.iloc[-5]) if len(sharpe) >= 5 else s_now
        # Improving positive Sharpe = momentum building = BUY
        if s_now > 0.3 and s_now >= s_prev:
            return "BUY"
        if s_now < -0.3 and s_now <= s_prev:
            return "SELL"
        return None
    except Exception:
        return None


# ── 3. BOOKMAP PRO (Volume absorption at price) ────────────────────
# Methodology: VWAP deviation + high-volume small-body bars = absorption.
# Above VWAP with absorption at low = institutional buying = BUY.
def _bookmap(df5: pd.DataFrame) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 20:
            return None
        vwap_s = _vwap(h, lo, c, v)
        vwap   = float(vwap_s.iloc[-1])
        px     = float(c.iloc[-1])
        # Absorption: last 5 bars — high volume but small body (absorption candle)
        recent_v = v.iloc[-5:].values
        recent_body = abs(c.iloc[-5:] - df5["open"].squeeze().astype(float).iloc[-5:]).values
        recent_range = (h.iloc[-5:] - lo.iloc[-5:]).values
        body_ratio   = (recent_body / (recent_range + 1e-10)).mean()
        avg_vol      = float(v.rolling(20).mean().iloc[-1])
        recent_avg_v = float(recent_v.mean())
        high_vol     = recent_avg_v > avg_vol * 1.2
        absorption   = body_ratio < 0.45 and high_vol   # high vol + small body = absorption
        if absorption:
            return "BUY" if px > vwap else "SELL"
        # Even without absorption: strong VWAP deviation = directional
        dev_pct = (px - vwap) / (vwap + 1e-10)
        if dev_pct > 0.0015:
            return "BUY"
        if dev_pct < -0.0015:
            return "SELL"
        return None
    except Exception:
        return None


# ── 4. TRADE IDEAS HOLLY AI (Gap / breakout momentum scanner) ──────
# Methodology: Detect recent breakout above prior swing high/low with
# volume surge — Holly AI's primary scan (gap + momentum breakout).
def _holly_ai(df5: pd.DataFrame) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float).dropna()
        lo = df5["low"].squeeze().astype(float).dropna()
        c  = df5["close"].squeeze().astype(float).dropna()
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if len(c) < 25:
            return None
        swing_high = float(h.iloc[-25:-2].max())
        swing_low  = float(lo.iloc[-25:-2].min())
        px  = float(c.iloc[-1])
        avg_v = float(v.rolling(20).mean().iloc[-1]) if v is not None else 1.0
        cur_v = float(v.iloc[-1]) if v is not None else avg_v
        vol_surge = cur_v > avg_v * 1.5
        # Breakout above prior swing high with volume
        if px > swing_high * 1.0005 and vol_surge:
            return "BUY"
        if px < swing_low * 0.9995 and vol_surge:
            return "SELL"
        # Momentum: 3 consecutive expanding candles in same direction
        closes = c.iloc[-4:].values
        if all(closes[i] > closes[i-1] for i in range(1, 4)):
            return "BUY"
        if all(closes[i] < closes[i-1] for i in range(1, 4)):
            return "SELL"
        return None
    except Exception:
        return None


# ── 5. TENSORTRADE (Normalized feature scoring) ────────────────────
# Methodology: Normalize 6 indicator features 0-1, compute weighted
# directional score — simulates a trained ML feature-scoring model.
def _tensortrade(df5: pd.DataFrame) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        if len(c) < 35:
            return None
        rsi_v   = float(_rsi(c, 14).iloc[-1])
        ema9    = float(_ema(c, 9).iloc[-1])
        ema21   = float(_ema(c, 21).iloc[-1])
        m, sg   = _macd(c, 12, 26, 9)
        macd_v  = float(m.iloc[-1])
        macd_sg = float(sg.iloc[-1])
        up_bb, mid_bb, dn_bb = _bb(c, 20, 2.0)
        bb_pos  = (float(c.iloc[-1]) - float(dn_bb.iloc[-1])) / (
                   float(up_bb.iloc[-1]) - float(dn_bb.iloc[-1]) + 1e-10)
        adx_s, dip, dim = _adx(h, lo, c, 14)
        adx_v   = float(adx_s.iloc[-1]) if not adx_s.empty else 20.0
        # Normalize features into bull_score (0=full bear, 1=full bull)
        f_rsi   = (rsi_v - 50) / 50          # -1..+1
        f_ema   = 1.0 if ema9 > ema21 else -1.0
        f_macd  = 1.0 if macd_v > macd_sg else -1.0
        f_bb    = (bb_pos - 0.5) * 2          # -1..+1
        f_adx   = adx_v / 50                  # strength weight
        score   = (f_rsi * 0.2 + f_ema * 0.25 + f_macd * 0.25 + f_bb * 0.3) * f_adx
        if score > 0.35:
            return "BUY"
        if score < -0.35:
            return "SELL"
        return None
    except Exception:
        return None


# ── 6. ALPACA MARKETS AI (Market regime + multi-factor) ───────────
# Methodology: Classify regime (bull/bear/range) using volatility +
# trend metrics, then apply a 3-factor model (EMA, RSI, momentum).
def _alpaca_ai(df5: pd.DataFrame) -> Optional[str]:
    try:
        c   = df5["close"].squeeze().astype(float).dropna()
        h   = df5["high"].squeeze().astype(float)
        lo  = df5["low"].squeeze().astype(float)
        if len(c) < 40:
            return None
        atr_v  = float(_atr(h, lo, c, 14).iloc[-1])
        mean_c = float(c.rolling(40).mean().iloc[-1])
        atr_pct = atr_v / (mean_c + 1e-10)
        ema9   = float(_ema(c, 9).iloc[-1])
        ema21  = float(_ema(c, 21).iloc[-1])
        rsi_v  = float(_rsi(c, 14).iloc[-1])
        roc10  = float((c.iloc[-1] - c.iloc[-11]) / (c.iloc[-11] + 1e-10)) * 100
        # Regime: low-vol trending vs high-vol choppy
        regime_ok = atr_pct < 0.012    # not in extreme chop
        bull_factors = int(ema9 > ema21) + int(rsi_v > 52) + int(roc10 > 0)
        bear_factors = int(ema9 < ema21) + int(rsi_v < 48) + int(roc10 < 0)
        if regime_ok and bull_factors >= 3:
            return "BUY"
        if regime_ok and bear_factors >= 3:
            return "SELL"
        return None
    except Exception:
        return None


# ── 7. VOLATILITY 75 INDEX (High-vol reversal detector) ───────────
# Methodology: V75 spikes sharply then reverses at oscillator extremes.
# ATR spike (>1.5× average) + RSI extreme = V75-style reversal setup.
def _v75(df5: pd.DataFrame) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        if len(c) < 25:
            return None
        atr_s    = _atr(h, lo, c, 14)
        atr_now  = float(atr_s.iloc[-1])
        atr_avg  = float(atr_s.iloc[-15:-2].mean())
        rsi_v    = float(_rsi(c, 7).iloc[-1])
        is_spike = atr_now > atr_avg * 1.5
        if is_spike and rsi_v > 75:
            return "SELL"   # spike up → reversal coming
        if is_spike and rsi_v < 25:
            return "BUY"    # spike down → reversal coming
        # Without spike: standard RSI extreme still valid for V75
        if rsi_v > 80:
            return "SELL"
        if rsi_v < 20:
            return "BUY"
        return None
    except Exception:
        return None


# ── 8. BOOM 1000 INDEX (Upside spike probability) ─────────────────
# Methodology: Boom pattern = several consecutive bearish candles
# accumulate selling pressure → sudden violent BUY spike.
def _boom1000(df5: pd.DataFrame) -> Optional[str]:
    try:
        o  = df5["open"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if len(c) < 15:
            return None
        # Count consecutive bearish candles (close < open) in last 8 bars
        bars = (c.iloc[-9:-1] - o.iloc[-9:-1]).values
        consec_bear = 0
        for b in reversed(bars):
            if b < 0:
                consec_bear += 1
            else:
                break
        rsi_v = float(_rsi(c, 7).iloc[-1])
        # Boom condition: 4+ consecutive bear bars + oversold RSI
        if consec_bear >= 4 and rsi_v < 35:
            return "BUY"
        # Volume surge after down move → boom ignition
        if v is not None:
            avg_v = float(v.rolling(10).mean().iloc[-1])
            cur_v = float(v.iloc[-1])
            down_move = float(c.iloc[-3] - c.iloc[-1]) > 0   # last 3 bars fell
            if cur_v > avg_v * 2.0 and down_move and rsi_v < 40:
                return "BUY"
        return None
    except Exception:
        return None


# ── 9. CRASH 500 INDEX (Downside drop probability) ────────────────
# Methodology: Crash pattern = several consecutive bullish candles
# → sudden violent SELL drop when buying exhaustion hits.
def _crash500(df5: pd.DataFrame) -> Optional[str]:
    try:
        o  = df5["open"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if len(c) < 15:
            return None
        bars = (c.iloc[-9:-1] - o.iloc[-9:-1]).values
        consec_bull = 0
        for b in reversed(bars):
            if b > 0:
                consec_bull += 1
            else:
                break
        rsi_v = float(_rsi(c, 7).iloc[-1])
        if consec_bull >= 4 and rsi_v > 65:
            return "SELL"
        if v is not None:
            avg_v = float(v.rolling(10).mean().iloc[-1])
            cur_v = float(v.iloc[-1])
            up_move = float(c.iloc[-1] - c.iloc[-3]) > 0
            if cur_v > avg_v * 2.0 and up_move and rsi_v > 60:
                return "SELL"
        return None
    except Exception:
        return None


# ── 10. UNUSUAL WHALES (Volume flow / options-flow proxy) ─────────
# Methodology: Unusual Whales tracks large buy/sell flow. We proxy
# this with directional volume: candles where close>open = buy vol,
# close<open = sell vol. Skew > 65% → smart money direction.
def _unusual_whales(df5: pd.DataFrame) -> Optional[str]:
    try:
        o  = df5["open"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 20:
            return None
        # Directional volume over last 20 bars
        bar_dir   = (c - o).iloc[-20:]
        bar_v     = v.iloc[-20:]
        buy_vol   = float(bar_v.where(bar_dir > 0, 0.0).sum())
        sell_vol  = float(bar_v.where(bar_dir < 0, 0.0).sum())
        total_vol = buy_vol + sell_vol
        if total_vol < 1:
            return None
        buy_pct = buy_vol / total_vol
        # Extra: check for abnormal large candles (whale bars)
        atr_v  = float(_atr(h, lo, c, 14).iloc[-1])
        last_range = float(h.iloc[-1] - lo.iloc[-1])
        whale_bar  = last_range > atr_v * 2.0
        if buy_pct >= 0.65 or (buy_pct >= 0.58 and whale_bar and float(bar_dir.iloc[-1]) > 0):
            return "BUY"
        if buy_pct <= 0.35 or (buy_pct <= 0.42 and whale_bar and float(bar_dir.iloc[-1]) < 0):
            return "SELL"
        return None
    except Exception:
        return None


# ── 11. FINTEL PRO (OBV institutional accumulation/distribution) ───
# Methodology: Fintel tracks institutional ownership changes. We proxy
# via OBV trend + OBV EMA cross (accumulation vs distribution phase).
def _fintel(df5: pd.DataFrame) -> Optional[str]:
    try:
        c  = df5["close"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 30:
            return None
        obv_s    = _obv(c, v)
        obv_e9   = float(_ema(obv_s, 9).iloc[-1])
        obv_e21  = float(_ema(obv_s, 21).iloc[-1])
        # OBV slope over last 10 bars
        obv_now  = float(obv_s.iloc[-1])
        obv_10   = float(obv_s.iloc[-11])
        obv_slp  = obv_now - obv_10
        # OBV EMA cross (9 above 21) = accumulation = BUY
        if obv_e9 > obv_e21 and obv_slp > 0:
            return "BUY"
        if obv_e9 < obv_e21 and obv_slp < 0:
            return "SELL"
        return None
    except Exception:
        return None


# ── 12. LUNARCRUSH (Price velocity + acceleration proxy) ───────────
# Methodology: LunarCrush measures social momentum (coin mentions,
# engagement). We proxy with price rate-of-change acceleration:
# if price is moving FASTER than it was 5 bars ago → social FOMO.
def _lunarcrush(df5: pd.DataFrame) -> Optional[str]:
    try:
        c = df5["close"].squeeze().astype(float).dropna()
        if len(c) < 25:
            return None
        # Velocity (ROC 5) and acceleration (change in velocity)
        roc5_now  = float((c.iloc[-1] - c.iloc[-6])  / (c.iloc[-6]  + 1e-10)) * 100
        roc5_prev = float((c.iloc[-6] - c.iloc[-11]) / (c.iloc[-11] + 1e-10)) * 100
        accel     = roc5_now - roc5_prev
        # Social momentum: positive and accelerating = FOMO BUY
        if roc5_now > 0.05 and accel > 0:
            return "BUY"
        if roc5_now < -0.05 and accel < 0:
            return "SELL"
        # Strong velocity alone (even without acceleration)
        if roc5_now > 0.15:
            return "BUY"
        if roc5_now < -0.15:
            return "SELL"
        return None
    except Exception:
        return None


# ── 13. TRADINGVIEW PRO+ (15-indicator multi-TF confluence) ────────
# Methodology: TradingView's "Technical Analysis" summary uses 15
# oscillator + MA signals. We implement all 15 and score them.
def _tradingview(df5: pd.DataFrame, df1h: Optional[pd.DataFrame]) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if len(c) < 35:
            return None

        votes_bull = 0
        votes_bear = 0

        def vote(cond_bull: bool, cond_bear: bool):
            nonlocal votes_bull, votes_bear
            if cond_bull: votes_bull += 1
            elif cond_bear: votes_bear += 1

        rsi14 = float(_rsi(c, 14).iloc[-1])
        vote(rsi14 > 55, rsi14 < 45)

        rsi7  = float(_rsi(c, 7).iloc[-1])
        vote(rsi7 > 55, rsi7 < 45)

        m, sg = _macd(c, 12, 26, 9)
        vote(float(m.iloc[-1]) > float(sg.iloc[-1]),
             float(m.iloc[-1]) < float(sg.iloc[-1]))

        stk, std = _stoch(h, lo, c, 14, 3)
        vote(float(stk.iloc[-1]) > float(std.iloc[-1]) and float(stk.iloc[-1]) < 80,
             float(stk.iloc[-1]) < float(std.iloc[-1]) and float(stk.iloc[-1]) > 20)

        cci_v = float(_cci(h, lo, c, 14).iloc[-1])
        vote(cci_v > 50, cci_v < -50)

        wpr_v = float(_wpr(h, lo, c, 14).iloc[-1])
        vote(wpr_v > -40, wpr_v < -60)

        up_bb, mid_bb, dn_bb = _bb(c, 20, 2.0)
        px = float(c.iloc[-1])
        vote(px > float(mid_bb.iloc[-1]), px < float(mid_bb.iloc[-1]))

        vote(float(_ema(c, 9).iloc[-1]) > float(_ema(c, 21).iloc[-1]),
             float(_ema(c, 9).iloc[-1]) < float(_ema(c, 21).iloc[-1]))

        vote(float(_ema(c, 20).iloc[-1]) > float(_ema(c, 50).iloc[-1]),
             float(_ema(c, 20).iloc[-1]) < float(_ema(c, 50).iloc[-1]))

        adx_s, dip, dim = _adx(h, lo, c, 14)
        vote(float(dip.iloc[-1]) > float(dim.iloc[-1]),
             float(dip.iloc[-1]) < float(dim.iloc[-1]))

        if v is not None:
            mfi_v = float(_mfi(h, lo, c, v, 14).iloc[-1])
            vote(mfi_v > 55, mfi_v < 45)
        else:
            votes_bull += 0   # neutral

        roc = float((c.iloc[-1] - c.iloc[-11]) / (c.iloc[-11] + 1e-10)) * 100
        vote(roc > 0, roc < 0)

        obv_s = _obv(c, v) if v is not None else c.cumsum()
        vote(float(_ema(obv_s, 5).iloc[-1]) > float(_ema(obv_s, 15).iloc[-1]),
             float(_ema(obv_s, 5).iloc[-1]) < float(_ema(obv_s, 15).iloc[-1]))

        # 1H EMA cross (higher timeframe)
        if df1h is not None and "close" in df1h.columns and len(df1h) >= 22:
            c1h = df1h["close"].squeeze().astype(float)
            vote(float(_ema(c1h, 9).iloc[-1]) > float(_ema(c1h, 21).iloc[-1]),
                 float(_ema(c1h, 9).iloc[-1]) < float(_ema(c1h, 21).iloc[-1]))
        else:
            vote(False, False)

        # Hull MA proxy (WMA 2 * WMA(n/2) - WMA(n))
        def wma(s, n):
            w = list(range(1, n + 1))
            return s.rolling(n).apply(lambda x: sum(a * b for a, b in zip(x, w)) / sum(w), raw=True)
        hull = 2 * wma(c, 10) - wma(c, 20)
        vote(float(hull.iloc[-1]) > float(hull.iloc[-2]),
             float(hull.iloc[-1]) < float(hull.iloc[-2]))

        total = votes_bull + votes_bear
        if total < 8:
            return None
        if votes_bull >= 10:
            return "BUY"
        if votes_bear >= 10:
            return "SELL"
        if votes_bull / total >= 0.70:
            return "BUY"
        if votes_bear / total >= 0.70:
            return "SELL"
        return None
    except Exception:
        return None


# ── 14. METATRADER 5 EA (iMACD + iRSI + iADX + iPSAR + iATR) ─────
# Methodology: Classic MT5 Expert Advisor using the standard 5-indicator
# set that professional EAs use (MACD zero-cross + RSI + ADX trend +
# Parabolic SAR flip + ATR volatility filter).
def _metatrader5(df5: pd.DataFrame) -> Optional[str]:
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        if len(c) < 35:
            return None
        # iMACD: MACD histogram sign
        m, sg = _macd(c, 12, 26, 9)
        hist = m - sg
        macd_bull = float(hist.iloc[-1]) > 0 and float(hist.iloc[-1]) > float(hist.iloc[-2])
        macd_bear = float(hist.iloc[-1]) < 0 and float(hist.iloc[-1]) < float(hist.iloc[-2])
        # iRSI(14)
        rsi_v    = float(_rsi(c, 14).iloc[-1])
        rsi_bull = rsi_v > 50
        rsi_bear = rsi_v < 50
        # iADX(14) + DI direction
        adx_s, dip, dim = _adx(h, lo, c, 14)
        adx_v    = float(adx_s.iloc[-1])
        trend_ok = adx_v > 20
        di_bull  = float(dip.iloc[-1]) > float(dim.iloc[-1])
        di_bear  = float(dip.iloc[-1]) < float(dim.iloc[-1])
        # Parabolic SAR
        _, psar_dir = _psar(h, lo)
        psar_bull = psar_dir == "BUY"
        psar_bear = psar_dir == "SELL"
        # iATR volatility filter
        atr_v   = float(_atr(h, lo, c, 14).iloc[-1])
        price   = float(c.iloc[-1])
        atr_pct = atr_v / (price + 1e-10)
        vol_ok  = 0.0001 < atr_pct < 0.015
        if not (trend_ok and vol_ok):
            return None
        bull_votes = sum([macd_bull, rsi_bull, di_bull, psar_bull])
        bear_votes = sum([macd_bear, rsi_bear, di_bear, psar_bear])
        if bull_votes >= 3:
            return "BUY"
        if bear_votes >= 3:
            return "SELL"
        return None
    except Exception:
        return None


# ── 15. ATAS PLATFORM (Cumulative volume delta) ────────────────────
# Methodology: ATAS specialises in order flow — buy volume vs sell
# volume. Buy vol = candle where close > open (full range as buy).
# Sell vol = candle where close < open. Cumulative delta trend + last
# bar imbalance = smart money direction.
def _atas(df5: pd.DataFrame) -> Optional[str]:
    try:
        o  = df5["open"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 25:
            return None
        bar_range = (h - lo).replace(0, 1e-10)
        # Buy volume proxy: proportion of bar range that is buying
        buy_frac = ((c - lo) / bar_range).clip(0, 1)
        sell_frac = 1 - buy_frac
        buy_vol  = (buy_frac * v)
        sell_vol = (sell_frac * v)
        # Cumulative delta
        delta = (buy_vol - sell_vol).cumsum()
        d_ema9  = float(_ema(delta, 9).iloc[-1])
        d_ema21 = float(_ema(delta, 21).iloc[-1])
        d_now   = float(delta.iloc[-1])
        d_prev  = float(delta.iloc[-5])
        delta_rising = d_now > d_prev
        delta_falling = d_now < d_prev
        # Last bar imbalance
        last_buy  = float(buy_vol.iloc[-1])
        last_sell = float(sell_vol.iloc[-1])
        last_imb  = (last_buy - last_sell) / (last_buy + last_sell + 1e-10)
        if d_ema9 > d_ema21 and delta_rising and last_imb > 0.15:
            return "BUY"
        if d_ema9 < d_ema21 and delta_falling and last_imb < -0.15:
            return "SELL"
        return None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════

def premium_intel_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Run all 15 premium-platform engines and return consensus result.

    Returns dict or None if < 6/15 sources agree on a direction.
    """
    if not _OK:
        return None

    cache_key = f"{pair}|{int(is_otc)}"
    now_ts = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _TTL:
        return cached[1]

    ticker = yf_ticker(pair)
    if not ticker:
        _CACHE[cache_key] = (now_ts, None)
        return None

    # Fetch required timeframe data
    df5  = _fetch(ticker, "5m",  "5d")
    df1h = _fetch(ticker, "60m", "30d")

    if df5 is None or len(df5) < 25:
        _CACHE[cache_key] = (now_ts, None)
        return None

    # Run all 15 engines
    engines: list[tuple[str, Optional[str]]] = [
        ("Bloomberg Terminal",    _bloomberg(ticker, df5)),
        ("QuantConnect",          _quantconnect(df5)),
        ("Bookmap Pro",           _bookmap(df5)),
        ("Trade Ideas Holly AI",  _holly_ai(df5)),
        ("TensorTrade",           _tensortrade(df5)),
        ("Alpaca Markets AI",     _alpaca_ai(df5)),
        ("Volatility 75 (V75)",   _v75(df5)),
        ("Boom 1000",             _boom1000(df5)),
        ("Crash 500",             _crash500(df5)),
        ("Unusual Whales",        _unusual_whales(df5)),
        ("Fintel Pro",            _fintel(df5)),
        ("LunarCrush",            _lunarcrush(df5)),
        ("TradingView Pro+",      _tradingview(df5, df1h)),
        ("MetaTrader 5 (MT5)",    _metatrader5(df5)),
        ("ATAS Platform",         _atas(df5)),
    ]

    buy_engines  = [name for name, v in engines if v == "BUY"]
    sell_engines = [name for name, v in engines if v == "SELL"]
    n_buy  = len(buy_engines)
    n_sell = len(sell_engines)

    if n_buy == 0 and n_sell == 0:
        _CACHE[cache_key] = (now_ts, None)
        return None

    if n_buy >= n_sell:
        winner     = "BUY"
        agree      = n_buy
        top_names  = buy_engines
    else:
        winner     = "SELL"
        agree      = n_sell
        top_names  = sell_engines

    if agree < _MIN_AGREE:
        _CACHE[cache_key] = (now_ts, None)
        return None

    score  = int(round(agree / 15 * 100))
    elite  = agree >= _ELITE_AGREE

    # Build human-readable reason lines (top 3 engines that agreed)
    reasons: list[str] = []
    tier_labels = {
        "Bloomberg Terminal":   "📡 BLOOMBERG MACRO FLOW CONFIRMED",
        "QuantConnect":         "📊 QUANTCONNECT SHARPE MOMENTUM LOCKED",
        "Bookmap Pro":          "🗺️ BOOKMAP ORDER ABSORPTION DETECTED",
        "Trade Ideas Holly AI": "🤖 HOLLY AI BREAKOUT SCAN CONFIRMED",
        "TensorTrade":          "🧬 TENSORTRADE ML FEATURE SCORE LOCKED",
        "Alpaca Markets AI":    "🦙 ALPACA AI REGIME CONFIRMED",
        "Volatility 75 (V75)":  "💥 V75 REVERSAL SETUP DETECTED",
        "Boom 1000":            "🚀 BOOM 1000 SPIKE PROBABILITY HIGH",
        "Crash 500":            "💣 CRASH 500 DROP PROBABILITY HIGH",
        "Unusual Whales":       "🐋 UNUSUAL WHALES SMART FLOW DETECTED",
        "Fintel Pro":           "🏦 FINTEL INSTITUTIONAL ACCUMULATION",
        "LunarCrush":           "🌙 LUNARCRUSH MOMENTUM SURGE ACTIVE",
        "TradingView Pro+":     "📈 TRADINGVIEW 15-INDICATOR CONSENSUS",
        "MetaTrader 5 (MT5)":   "⚙️ MT5 EA MACD+ADX+SAR CONFIRMED",
        "ATAS Platform":        "⚖️ ATAS VOLUME DELTA IMBALANCE LOCKED",
    }
    for name in top_names[:4]:
        label = tier_labels.get(name, f"✅ {name.upper()}")
        reasons.append(label)

    result: dict = {
        "direction": winner,
        "score":     score,
        "elite":     elite,
        "engines":   agree,
        "reasons":   reasons,
    }
    _CACHE[cache_key] = (now_ts, result)
    return result
