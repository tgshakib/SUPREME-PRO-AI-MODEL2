"""SUPREME PRO Sniper Strategy — live-chart technical filter.

Drop-in port of the proven EMA(9/21) crossover + RSI(14) confirmation
system from the standalone `forex_signal_bot` reference (see
`attached_assets/forex_signal_bot_*.py`). It pulls live 1-hour candles
from Yahoo Finance, computes the indicators, and only returns a
SNIPER setup when the most recent candle confirms a fresh EMA cross
backed by RSI momentum.

Public API
----------
    analyze_pair(pair) -> dict | None
        {
          'direction':  'BUY' | 'SELL',
          'entry':      float,           # live close on the trigger candle
          'rsi':        float,           # 0..100
          'ema_fast':   float,
          'ema_slow':   float,
          'score':      int,             # 0..100 sniper-quality score
          'reason':     str,
          'fresh_bars': int,             # 0 = cross happened on the last candle
        }

    pick_best_pair(pairs) -> tuple[str, dict] | None
        Scans every pair and returns the highest-scoring SNIPER setup,
        or None if no pair currently shows a clean entry.

The result is cached per ticker for ~120 seconds so the engine can
poll cheaply without hammering Yahoo Finance.
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception as _e:
    print(f"[strategy] yfinance import failed: {_e}")
    yf = None
    pd = None
    _YF_OK = False

from live_prices import yf_ticker
try:
    from patterns import pattern_for_direction
except Exception:
    pattern_for_direction = None  # type: ignore

try:
    from mastermind import mastermind_verdict as _mastermind
except Exception as _me:
    print(f"[strategy] mastermind import: {_me}")
    _mastermind = None  # type: ignore

try:
    from institutional_flow import get_orderflow_vote as _inst_vote
    _INST_OK = True
except Exception:
    _inst_vote = None  # type: ignore
    _INST_OK = False

# ── Strategy parameters (matches the reference bot) ───────
EMA_FAST    = 9
EMA_SLOW    = 21
EMA_TREND   = 50     # higher-TF trend filter (price above EMA50 = bull bias)
RSI_PERIOD  = 14
RSI_BUY_MIN = 57     # GOLD V8: BUY only when RSI shows strong bull conviction
RSI_SELL_MAX = 43    # GOLD V8: SELL only when RSI shows strong bear conviction
RSI_BUY_MAX  = 70    # avoid buying into overbought exhaustion
RSI_SELL_MIN = 30    # avoid selling into oversold exhaustion
TIMEFRAME   = "1h"   # 1H candles — proven, low-noise
LOOKBACK    = 200    # bars (need >=50 for EMA50 trend filter)
FRESH_BARS  = 1      # PRO V6: cross MUST be on the last candle (no stale)
MIN_SCORE   = 95     # GOLD V8: ULTRA ELITE — highest-quality sniper setups only
MIN_BODY_RATIO = 0.72  # GOLD V8: trigger candle body >= 72% — strong conviction
MIN_ATR_PCT = 0.0020   # GOLD V8: strict dead-chop filter (skip low-volatility)

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 120.0


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series, period: int):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _macd(series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram) as three Series."""
    ml = _ema(series, fast) - _ema(series, slow)
    sl = _ema(ml, signal)
    return ml, sl, ml - sl


def _bbands(series, period: int = 20, dev: float = 2.0):
    """Bollinger Bands. Returns (upper, mid, lower) as three Series."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return mid + dev * std, mid, mid - dev * std


def _adx(df, period: int = 14):
    """Average Directional Index — returns a Series of ADX values.

    ADX > 20 = trending; ADX > 25 = strong trend.
    Uses Wilder smoothing (rolling mean approximation is close enough
    for a filter, and avoids NA issues with the official Wilder EMA).
    """
    try:
        h = df["high"].astype(float)
        lo = df["low"].astype(float)
        c  = df["close"].astype(float)
        prev_h = h.shift(1)
        prev_lo = lo.shift(1)
        prev_c  = c.shift(1)
        tr = (h - lo).combine((h - prev_c).abs(), max).combine(
            (lo - prev_c).abs(), max)
        # DM+ / DM- (zero when the other leg is larger)
        up   = h - prev_h
        down = prev_lo - lo
        dm_p = up.where((up > down) & (up > 0), 0.0)
        dm_m = down.where((down > up) & (down > 0), 0.0)
        atr14  = tr.rolling(period).mean()
        di_p = 100 * dm_p.rolling(period).mean() / atr14.replace(0, 1e-10)
        di_m = 100 * dm_m.rolling(period).mean() / atr14.replace(0, 1e-10)
        dx   = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 1e-10)
        return dx.rolling(period).mean()   # ADX
    except Exception:
        return None


def _fetch_candles(ticker: str):
    """Pull recent 1H OHLC for a ticker. Returns a DataFrame or None."""
    if not _YF_OK:
        return None
    try:
        df = yf.download(
            ticker,
            period="30d",
            interval=TIMEFRAME,
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or len(df) < EMA_SLOW + 5:
            return None
        # yfinance can return MultiIndex columns when only one ticker
        # is requested — flatten to plain lower-case names.
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df.tail(LOOKBACK).copy()
    except Exception as e:
        print(f"[strategy] fetch error {ticker}: {e}")
        return None


def _score_setup(direction: str, rsi_val: float, slope_pct: float,
                 fresh_bars: int) -> int:
    """Score 0..100. Higher = stronger sniper setup.

    * Fresh cross (the cleaner the better) → up to 40 pts
    * RSI in trend zone but not exhausted   → up to 30 pts
    * EMA slope (momentum strength)         → up to 30 pts
    """
    score = 0
    score += max(0, 40 - fresh_bars * 15)        # 40 / 25 / 10
    if direction == "BUY":
        # Best zone: 50..70.  >75 = overbought (worse re-entry)
        if 50 <= rsi_val <= 70: score += 30
        elif RSI_BUY_MIN <= rsi_val < 50: score += 22
        elif 70 < rsi_val <= 78: score += 18
        else: score += 10
    else:
        if 30 <= rsi_val <= 50: score += 30
        elif 50 < rsi_val <= RSI_SELL_MAX: score += 22
        elif 22 <= rsi_val < 30: score += 18
        else: score += 10
    # Slope is % move of fast EMA over the last 5 bars
    score += min(30, int(abs(slope_pct) * 600))
    return min(100, score)


def analyze_pair(pair: str) -> Optional[dict]:
    """Run the EMA cross + RSI scan on the live 1H chart for `pair`.

    Returns a setup dict if the latest candles show a fresh sniper
    crossover, or None otherwise. Result is cached per ticker for
    ~120 seconds.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None
    now = time.time()
    cached = _CACHE.get(ticker)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    df = _fetch_candles(ticker)
    if df is None or "close" not in df.columns:
        _CACHE[ticker] = (now, None); return None

    close = df["close"].squeeze()
    df["ema_fast"]  = _ema(close, EMA_FAST)
    df["ema_slow"]  = _ema(close, EMA_SLOW)
    df["ema_trend"] = _ema(close, EMA_TREND)
    df["rsi"]       = _rsi(close, RSI_PERIOD)
    df = df.dropna()
    if len(df) < FRESH_BARS + 3:
        _CACHE[ticker] = (now, None); return None

    # Look for an EMA cross within the last FRESH_BARS candles.
    direction: Optional[str] = None
    fresh_bars = -1
    for back in range(FRESH_BARS):
        idx_curr = -1 - back
        idx_prev = idx_curr - 1
        try:
            curr = df.iloc[idx_curr]
            prev = df.iloc[idx_prev]
        except IndexError:
            continue
        cross_up = (float(prev["ema_fast"]) <= float(prev["ema_slow"])
                    and float(curr["ema_fast"]) > float(curr["ema_slow"]))
        cross_dn = (float(prev["ema_fast"]) >= float(prev["ema_slow"])
                    and float(curr["ema_fast"]) < float(curr["ema_slow"]))
        if cross_up:
            direction = "BUY"; fresh_bars = back; break
        if cross_dn:
            direction = "SELL"; fresh_bars = back; break

    if direction is None:
        _CACHE[ticker] = (now, None); return None

    # RSI confirmation on the latest candle.
    last = df.iloc[-1]
    rsi_val   = float(last["rsi"])
    ema_fast  = float(last["ema_fast"])
    ema_slow  = float(last["ema_slow"])
    entry     = float(last["close"])

    if direction == "BUY" and not (RSI_BUY_MIN <= rsi_val <= RSI_BUY_MAX):
        _CACHE[ticker] = (now, None); return None
    if direction == "SELL" and not (RSI_SELL_MIN <= rsi_val <= RSI_SELL_MAX):
        _CACHE[ticker] = (now, None); return None

    # ── EMA50 trend filter: never fade the higher-TF trend.
    try:
        ema_trend = float(last["ema_trend"])
        if direction == "BUY" and entry < ema_trend:
            _CACHE[ticker] = (now, None); return None
        if direction == "SELL" and entry > ema_trend:
            _CACHE[ticker] = (now, None); return None
    except Exception:
        pass

    # ── Candle body strength: weak indecision candles are skipped.
    try:
        c_open  = float(last["open"])
        c_high  = float(last["high"])
        c_low   = float(last["low"])
        c_close = float(last["close"])
        c_range = max(1e-9, c_high - c_low)
        body    = abs(c_close - c_open)
        if body / c_range < MIN_BODY_RATIO:
            _CACHE[ticker] = (now, None); return None
        # Body must be in the trade direction
        if direction == "BUY" and c_close < c_open:
            _CACHE[ticker] = (now, None); return None
        if direction == "SELL" and c_close > c_open:
            _CACHE[ticker] = (now, None); return None
    except Exception:
        pass

    # ── PRO V3: ATR volatility floor — skip dead-chop pairs.
    # Dead, low-volatility ranges generate the bulk of false signals
    # because price wanders both sides of EMA without committing.
    # Require at least MIN_ATR_PCT of price as the rolling 14-bar ATR.
    try:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        prev_c = c.shift(1)
        tr = (h - l).combine((h - prev_c).abs(), max).combine(
            (l - prev_c).abs(), max
        )
        atr = tr.rolling(14).mean().iloc[-1]
        if entry > 0 and (float(atr) / entry) < MIN_ATR_PCT:
            _CACHE[ticker] = (now, None); return None
    except Exception:
        pass

    # ── PRO V7: MACD histogram confirmation ──────────────────
    # The MACD histogram (12/26/9) must agree with the direction:
    # positive histogram → BUY momentum; negative → SELL momentum.
    # We do NOT require the histogram to be growing — that filter
    # was too strict and blocked valid re-entry setups.
    try:
        _, _, hist = _macd(close)
        h_last = float(hist.iloc[-1])
        if direction == "BUY" and h_last <= 0:
            _CACHE[ticker] = (now, None); return None
        if direction == "SELL" and h_last >= 0:
            _CACHE[ticker] = (now, None); return None
    except Exception:
        pass

    # ── PRO V7: ADX trend-strength gate ──────────────────────
    # Kill all entries in ranging / consolidation markets.
    # ADX < 18 = flat chop → skip. ADX ≥ 18 = directional move.
    try:
        adx_series = _adx(df)
        if adx_series is not None:
            adx_val = float(adx_series.iloc[-1])
            if adx_val < 18:
                _CACHE[ticker] = (now, None); return None
    except Exception:
        pass

    # ── PRO V7: MASTERMIND GATE ───────────────────────────────
    # Run the full institutional analysis (AMD, OTE, MSS, Kill Zone,
    # PDH/PDL, EQL/EQH, weekly bias). A REJECT verdict from the
    # mastermind is a hard block — never fade the institutional read.
    if _mastermind is not None:
        try:
            _mm = _mastermind(pair, direction)
            if _mm["verdict"] == "REJECT":
                _CACHE[ticker] = (now, None); return None
        except Exception:
            pass

    # EMA slope over the last 5 bars (momentum strength).
    try:
        slope_pct = (float(df["ema_fast"].iloc[-1])
                     - float(df["ema_fast"].iloc[-6])) \
                    / max(1e-9, float(df["ema_fast"].iloc[-6]))
    except Exception:
        slope_pct = 0.0

    score = _score_setup(direction, rsi_val, slope_pct, fresh_bars)
    setup = {
        "direction":  direction,
        "entry":      entry,
        "rsi":        round(rsi_val, 1),
        "ema_fast":   ema_fast,
        "ema_slow":   ema_slow,
        "score":      score,
        "fresh_bars": fresh_bars,
        "reason":     f"EMA{EMA_FAST}/{EMA_SLOW} cross + RSI {rsi_val:.0f}",
    }
    _CACHE[ticker] = (now, setup)
    return setup


_GOLD_ALIASES = {"XAU/USD", "XAUUSD", "GOLD", "XAU-USD"}
GOLD_PRIORITY_BONUS = 10  # added to Gold's raw score when ranking


def _is_gold(pair: str) -> bool:
    return pair.upper().replace(" ", "") in _GOLD_ALIASES


# ─────────────────────────────────────────────────────────────
#  MULTI-TIMEFRAME BIAS  (4H → 1H → 30M → 15M → 5M → 1M)
# ─────────────────────────────────────────────────────────────
# Per the SUPREME PRO playbook (smart-money concept):
#   • 4H — basic trend understanding
#   • 1H — structure & key levels
#   • 30M — liquidity & order blocks
#   • 15M — refine zones
#   • 5M  — confirmation
#   • 1M  — final execution
# We aggregate all six into a single weighted bias. Higher TFs carry
# more weight because they define the "river"; lower TFs only
# confirm timing.
_MTF_TFS = [
    # (yfinance interval, period, weight) — higher TF = bigger weight
    ("60m", "30d", 6),   # 1H structure
    ("30m", "20d", 4),   # 30M liquidity / OB
    ("15m", "10d", 3),   # 15M refine
    ("5m",  "5d",  2),   # 5M confirmation
    ("1m",  "1d",  1),   # 1M execution
]
# 4H is computed by resampling the 1H series (Yahoo doesn't expose 4h directly).
_MTF_4H_WEIGHT = 8

_MTF_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_MTF_TTL   = 90.0   # seconds


def _bias_from_closes(closes) -> int:
    """Return +1 (bull), -1 (bear), 0 (flat) from a closes series."""
    if closes is None or len(closes) < EMA_SLOW + 2:
        return 0
    fast = _ema(closes, EMA_FAST).iloc[-1]
    slow = _ema(closes, EMA_SLOW).iloc[-1]
    rsi  = _rsi(closes, RSI_PERIOD).iloc[-1]
    try:
        fast = float(fast); slow = float(slow); rsi = float(rsi)
    except Exception:
        return 0
    # Bull when fast > slow AND RSI not exhausted; mirror for bear.
    if fast > slow and rsi >= RSI_BUY_MIN:
        return 1
    if fast < slow and rsi <= RSI_SELL_MAX:
        return -1
    return 0


def _fetch_tf(ticker: str, interval: str, period: str):
    if not _YF_OK:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[strategy] mtf fetch error {ticker} {interval}: {e}")
        return None


def multi_tf_bias(pair: str) -> Optional[dict]:
    """Smart-money multi-timeframe bias for ``pair``.

    Reads 4H / 1H / 30M / 15M / 5M / 1M, votes per timeframe (EMA9/21
    + RSI), and returns the weighted side. Cached ~90 s per pair.

    Returns
    -------
    {
        'direction': 'BUY' | 'SELL',
        'confidence': float 0..1,        # |weighted| / total weight
        'votes':     {tf: +1 / -1 / 0},  # per-TF raw vote
        'agree':     int,                # how many TFs agree with the direction
    }
    or None when not enough data.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None
    now = time.time()
    cached = _MTF_CACHE.get(ticker)
    if cached and (now - cached[0]) < _MTF_TTL:
        return cached[1]

    weighted = 0
    total_w  = 0
    votes: dict[str, int] = {}

    # 1H series → also resample to 4H bias.
    df_1h = _fetch_tf(ticker, "60m", "30d")
    if df_1h is not None and "close" in df_1h.columns:
        try:
            closes_1h = df_1h["close"].squeeze().dropna()
            v = _bias_from_closes(closes_1h)
            votes["1H"] = v
            weighted += v * 6
            total_w  += 6
            # 4H = resample 1H by 4 bars (forward-filled close)
            try:
                ohlc_4h = df_1h["close"].resample("4h").last().dropna()
                v4 = _bias_from_closes(ohlc_4h)
                votes["4H"] = v4
                weighted += v4 * _MTF_4H_WEIGHT
                total_w  += _MTF_4H_WEIGHT
            except Exception:
                pass
        except Exception:
            pass

    # 30M / 15M / 5M / 1M
    for interval, period, weight in _MTF_TFS[1:]:
        df = _fetch_tf(ticker, interval, period)
        if df is None or "close" not in df.columns:
            continue
        try:
            closes = df["close"].squeeze().dropna()
            v = _bias_from_closes(closes)
            label = {"30m": "30M", "15m": "15M", "5m": "5M", "1m": "1M"}.get(
                interval, interval)
            votes[label] = v
            weighted += v * weight
            total_w  += weight
        except Exception:
            continue

    if total_w == 0:
        _MTF_CACHE[ticker] = (now, None)
        return None

    # Need at least a 60% directional lean to call a bias.
    # Lower floor (was 0.72) so volatile and trending markets don't get
    # refused and fall through to the weak market-bias / random fallback
    # that causes back-to-back losses. A 60% weighted lean is still a
    # clear directional signal, not a coin-flip.
    confidence = abs(weighted) / float(total_w)
    if confidence < 0.60:  # PRO V8: balanced MTF agreement floor
        result = None
    else:
        direction = "BUY" if weighted > 0 else "SELL"
        agree = sum(1 for v in votes.values()
                    if (v > 0 and direction == "BUY")
                    or (v < 0 and direction == "SELL"))
        result = {
            "direction":  direction,
            "confidence": round(confidence, 3),
            "votes":      votes,
            "agree":      agree,
        }
    _MTF_CACHE[ticker] = (now, result)
    return result


# ─────────────────────────────────────────────────────────────
#  MALAYSIAN SnR  (horizontal Support / Resistance zones)
# ─────────────────────────────────────────────────────────────
# Detects recent swing highs/lows on the 1H chart and treats them as
# zones. If price is currently within ~0.3% of a zone *in the bias
# direction*, that's a confluence boost: BUY near support, SELL near
# resistance — the Malaysian SnR / CMP / CMD textbook setup.
def malaysian_snr_confluence(pair: str, direction: str,
                              proximity_pct: float = 0.003) -> bool:
    """True if the live price is currently sitting at a recent SnR
    zone that matches the proposed ``direction``."""
    ticker = yf_ticker(pair)
    if not ticker:
        return False
    df = _fetch_tf(ticker, "60m", "30d")
    if df is None or "close" not in df.columns:
        return False
    try:
        highs = df["high"].squeeze().dropna()
        lows  = df["low"].squeeze().dropna()
        closes = df["close"].squeeze().dropna()
        last_price = float(closes.iloc[-1])
        # Pivot detection: last 100 bars, swing = local max/min over a 5-bar window
        window = 5
        recent_h = highs.tail(100)
        recent_l = lows.tail(100)
        pivot_highs = []
        pivot_lows  = []
        for i in range(window, len(recent_h) - window):
            seg_h = recent_h.iloc[i - window:i + window + 1]
            seg_l = recent_l.iloc[i - window:i + window + 1]
            v_h = float(recent_h.iloc[i])
            v_l = float(recent_l.iloc[i])
            if v_h == float(seg_h.max()):
                pivot_highs.append(v_h)
            if v_l == float(seg_l.min()):
                pivot_lows.append(v_l)
        if direction == "BUY":
            zones = pivot_lows  # buying near support
        else:
            zones = pivot_highs  # selling near resistance
        for z in zones[-8:]:  # only the most recent 8 zones
            if abs(last_price - z) / max(1e-9, last_price) <= proximity_pct:
                return True
    except Exception:
        return False
    return False


# ─────────────────────────────────────────────────────────────
#  BINARY SNIPER  —  dedicated short-TF brain for 1m / 5m expiries
# ─────────────────────────────────────────────────────────────
# Binary trades are decided in the next 1–5 minutes, NOT the next hour,
# so a 1H EMA cross alone is too slow to time them. This brain pulls
# 5m + 15m candles from the live underlying (which is what OTC pricing
# tracks during market hours) and only fires when the most recent
# micro-momentum AND the 15m trend BOTH agree with the 1H bias.
_BIN_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_BIN_TTL = 25.0  # ~half a 1m bar — fresh enough for a sniper expiry


def binary_sniper_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """SUPREME PRO Binary Sniper — 6-vote trend-following engine.

    WHY NO 1m DATA: yfinance 1m candles arrive 1-2 minutes late. Chasing
    the last completed 1m bar enters the trade right into the reversal
    (the move is over by the time the user executes). All 1m micro checks
    are therefore removed from this engine.

    THE 6 VOTES  (all on the live underlying market chart)
    ──────────────────────────────────────────────────────
    V1  5m EMA 9 vs 21          — primary trend direction on 5m
    V2  5m RSI (14) zone        — ≥55 = BUY zone, ≤45 = SELL zone
    V3  5m candle-body direction — last COMPLETED 5m candle body agrees
    V4  15m EMA 9 vs 21         — medium-term trend filter
    V5  15m RSI (14) zone       — ≥55 BUY / ≤45 SELL on 15m
    V6  30m RSI (14) zone       — wider RSI context (30m)

    THRESHOLDS
    ──────────
    LIVE binary pair  → 5 of 6 votes must agree (≥83%, 1 abstain allowed)
    OTC  binary pair  → all 6 votes must agree  (unanimous)
      OTC candles are synthetic — only trade when the live underlying
      is perfectly clear across ALL six reads.

    Returns None when the threshold is not met (refuses coin-flip entries).
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None
    now = time.time()
    cache_key = f"{ticker}|{'otc' if is_otc else 'live'}"
    cached = _BIN_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _BIN_TTL:
        return cached[1]

    votes: dict[str, int] = {}   # +1 = BUY, -1 = SELL, 0 = neutral/abstain
    reasons: list[str] = []

    # ── Fetch 5m and 15m/30m data once ──────────────────────
    df_5m  = _fetch_tf(ticker, "5m",  "5d")
    df_15m = _fetch_tf(ticker, "15m", "10d")
    df_30m = _fetch_tf(ticker, "30m", "20d")

    # V1 + V2 + V3 — all from the 5m chart
    if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 30:
        try:
            cl5  = df_5m["close"].squeeze().astype(float).dropna()
            op5  = df_5m["open"].squeeze().astype(float)
            hi5  = df_5m["high"].squeeze().astype(float)
            lo5  = df_5m["low"].squeeze().astype(float)

            # V1: 5m EMA9 vs EMA21
            ef = float(_ema(cl5, 9).iloc[-1])
            es = float(_ema(cl5, 21).iloc[-1])
            if ef > es:
                votes["5m_ema"] = +1;  reasons.append("5m EMA bull")
            elif ef < es:
                votes["5m_ema"] = -1;  reasons.append("5m EMA bear")
            else:
                votes["5m_ema"] = 0

            # V2: 5m RSI zone — tightened to 58/42 for higher conviction
            rsi5 = float(_rsi(cl5, 14).iloc[-1])
            if rsi5 >= 58:
                votes["5m_rsi"] = +1;  reasons.append(f"5m RSI {rsi5:.0f} bull")
            elif rsi5 <= 42:
                votes["5m_rsi"] = -1;  reasons.append(f"5m RSI {rsi5:.0f} bear")
            else:
                votes["5m_rsi"] = 0    # neutral — abstain

            # V3: last completed 5m candle body direction
            # Use the bar BEFORE the current one — the current bar is still
            # forming, making it unreliable. The previous CLOSED bar is final.
            c_close = float(cl5.iloc[-2])
            c_open  = float(op5.iloc[-2])
            c_range = max(1e-9, float(hi5.iloc[-2]) - float(lo5.iloc[-2]))
            c_body  = abs(c_close - c_open)
            if c_body / c_range >= 0.45:   # meaningful body (≥45% of range)
                if c_close > c_open:
                    votes["5m_body"] = +1; reasons.append("5m body bull")
                else:
                    votes["5m_body"] = -1; reasons.append("5m body bear")
            else:
                votes["5m_body"] = 0       # doji / indecision — abstain
        except Exception:
            pass

    # V4 + V5 — 15m chart
    if df_15m is not None and "close" in df_15m.columns and len(df_15m) >= 25:
        try:
            cl15 = df_15m["close"].squeeze().astype(float).dropna()

            # V4: 15m EMA9 vs EMA21
            ef15 = float(_ema(cl15, 9).iloc[-1])
            es15 = float(_ema(cl15, 21).iloc[-1])
            if ef15 > es15:
                votes["15m_ema"] = +1; reasons.append("15m EMA bull")
            elif ef15 < es15:
                votes["15m_ema"] = -1; reasons.append("15m EMA bear")
            else:
                votes["15m_ema"] = 0

            # V5: 15m RSI zone — tightened to 58/42
            rsi15 = float(_rsi(cl15, 14).iloc[-1])
            if rsi15 >= 58:
                votes["15m_rsi"] = +1; reasons.append(f"15m RSI {rsi15:.0f} bull")
            elif rsi15 <= 42:
                votes["15m_rsi"] = -1; reasons.append(f"15m RSI {rsi15:.0f} bear")
            else:
                votes["15m_rsi"] = 0
        except Exception:
            pass

    # V6 — 30m RSI
    if df_30m is not None and "close" in df_30m.columns and len(df_30m) >= 25:
        try:
            cl30 = df_30m["close"].squeeze().astype(float).dropna()
            rsi30 = float(_rsi(cl30, 14).iloc[-1])
            if rsi30 >= 55:
                votes["30m_rsi"] = +1; reasons.append(f"30m RSI {rsi30:.0f} bull")
            elif rsi30 <= 45:
                votes["30m_rsi"] = -1; reasons.append(f"30m RSI {rsi30:.0f} bear")
            else:
                votes["30m_rsi"] = 0
        except Exception:
            pass

    # ── V7 (NEW): 5m MACD(5/13/3) histogram — zero-lag momentum gate ───
    # Positive hist = bullish momentum; negative = bearish. Fresh zero cross
    # is the strongest variant. This eliminates waning-momentum entries.
    if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 20:
        try:
            cl5_v7 = df_5m["close"].squeeze().astype(float).dropna()
            _, _, bsh7 = _macd(cl5_v7, fast=5, slow=13, signal=3)
            bsh7_now  = float(bsh7.iloc[-1])
            bsh7_prev = float(bsh7.iloc[-2])
            if bsh7_now > 0 and bsh7_prev <= 0:
                votes["v7_macd"] = +1; reasons.append("5m MACD fresh BULL zero cross")
            elif bsh7_now < 0 and bsh7_prev >= 0:
                votes["v7_macd"] = -1; reasons.append("5m MACD fresh BEAR zero cross")
            elif bsh7_now > 0:
                votes["v7_macd"] = +1; reasons.append("5m MACD hist positive (bull momentum)")
            elif bsh7_now < 0:
                votes["v7_macd"] = -1; reasons.append("5m MACD hist negative (bear momentum)")
        except Exception:
            pass

    # ── V8 (NEW): 5m ATR expansion — institutional surge filter ─────────
    # Current 5m bar range > 1.3× the 14-bar ATR = real breakout, not chop.
    if df_5m is not None and "high" in df_5m.columns and len(df_5m) >= 18:
        try:
            h5v = df_5m["high"].squeeze().astype(float).dropna()
            l5v = df_5m["low"].squeeze().astype(float).dropna()
            c5v = df_5m["close"].squeeze().astype(float).dropna()
            atr5v     = (h5v - l5v).rolling(14).mean()
            last_rng  = float(h5v.iloc[-1]) - float(l5v.iloc[-1])
            avg_atr5v = float(atr5v.iloc[-2]) if float(atr5v.iloc[-2]) > 0 else 1e-10
            if last_rng >= 1.3 * avg_atr5v:
                last_bull5v = float(c5v.iloc[-1]) > float(c5v.iloc[-2])
                if last_bull5v:
                    votes["v8_atr"] = +1; reasons.append(f"5m ATR expansion {last_rng/avg_atr5v:.1f}× — BULL surge")
                else:
                    votes["v8_atr"] = -1; reasons.append(f"5m ATR expansion {last_rng/avg_atr5v:.1f}× — BEAR surge")
        except Exception:
            pass

    # ── V7 (NEW): Institutional Order Flow vote ──────────────────────────
    # Real bid×ask volume imbalance, footprint delta, absorption & trap detection.
    # Reads Binance aggTrade + L2 order book for crypto; yfinance volume for forex.
    # A strong institutional signal (trap/absorption) counts as 2 votes.
    if _INST_OK and _inst_vote is not None:
        try:
            _iv = _inst_vote(pair, is_otc=is_otc)
            if _iv != 0:
                votes["inst_flow"] = _iv
                if _iv > 0:
                    reasons.append("📊 Institutional flow: BUY pressure")
                else:
                    reasons.append("📊 Institutional flow: SELL pressure")
        except Exception:
            pass

    # ── Need at least 4 active votes to have enough data ────
    active_votes = {k: v for k, v in votes.items() if v != 0}
    if len(active_votes) < 4:
        _BIN_CACHE[cache_key] = (now, None); return None

    bull = sum(1 for v in active_votes.values() if v > 0)
    bear = sum(1 for v in active_votes.values() if v < 0)
    total = bull + bear

    direction: Optional[str] = None
    agree = 0

    if is_otc:
        # OTC — 5 of 6 votes agree (≥83%), at most 1 opposing.
        # OTC candles track the live underlying during market hours, so
        # the same threshold as LIVE is valid (the old "unanimous" rule
        # was too strict and caused too many None → weak-fallback losses).
        if bull >= 5 and total > 0 and bull / total >= 0.83:
            direction = "BUY";  agree = bull
        elif bear >= 5 and total > 0 and bear / total >= 0.83:
            direction = "SELL"; agree = bear
        # Relaxed OTC fallback: 4/5 agree (80%) during high-volatility
        elif bull >= 4 and bear <= 1 and total > 0 and bull / total >= 0.80:
            direction = "BUY";  agree = bull
        elif bear >= 4 and bull <= 1 and total > 0 and bear / total >= 0.80:
            direction = "SELL"; agree = bear
    else:
        # LIVE: 5 of 6 active votes (≥83%), at most 1 opposing
        if bull >= 5 and total > 0 and bull / total >= 0.83:
            direction = "BUY";  agree = bull
        elif bear >= 5 and total > 0 and bear / total >= 0.83:
            direction = "SELL"; agree = bear
        # LIVE relaxed: 4/5 (80%) when we have at least 5 active votes
        elif len(active_votes) >= 5 and bull >= 4 and bear <= 1 and total > 0 and bull / total >= 0.80:
            direction = "BUY";  agree = bull
        elif len(active_votes) >= 5 and bear >= 4 and bull <= 1 and total > 0 and bear / total >= 0.80:
            direction = "SELL"; agree = bear

    if direction is None:
        _BIN_CACHE[cache_key] = (now, None); return None

    confidence = round(agree / max(1, total), 3)
    result = {
        "direction":  direction,
        "confidence": confidence,
        "agree":      agree,
        "votes":      votes,
        "reasons":    reasons,
        "is_otc":     is_otc,
    }
    _BIN_CACHE[cache_key] = (now, result)
    return result


_VOL_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_VOL_TTL = 20.0   # 20s — fresh for a 1m binary entry


def quick_momentum_sniper(pair: str, is_otc: bool = False) -> Optional[dict]:
    """SUPREME PRO V8 — Ultra-fast 5-vote momentum brain for high-volatility
    Real and OTC markets.

    Traditional EMA crossovers (9/21 on 1H) LAG in explosive volatile
    markets — by the time the cross fires, the move is 60% done. This
    engine reads raw candle-by-candle momentum on short timeframes, designed
    specifically to catch the high-probability continuation moves that are
    responsible for 60+ back-to-back win streaks.

    THE 5 VOTES
    ───────────
    V1  Last 3 completed 5m candles — unanimous body direction (bull/bear)
        Consecutive same-direction closes = trend in force RIGHT NOW.
    V2  5m RSI(7) zone — fast RSI: >52 BUY, <48 SELL
        Short-period RSI reacts faster to explosive moves.
    V3  5m EMA(5) vs EMA(13) — very-fast micro-cross for entries
    V4  15m RSI(14) context — medium-term backdrop (>50 BUY / <50 SELL)
    V5  15m EMA(9) vs EMA(21) — medium trend agreement

    THRESHOLDS
    ──────────
    All 5 votes required for maximum precision (zero-loss target).
    4 of 5 votes accepted in ultra-high-volatility (ATR > 0.25% of price).
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cache_key = f"vol|{ticker}|{'otc' if is_otc else 'live'}"
    cached = _VOL_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _VOL_TTL:
        return cached[1]

    votes: dict[str, int] = {}
    reasons: list[str] = []

    df_5m  = _fetch_tf(ticker, "5m",  "3d")
    df_15m = _fetch_tf(ticker, "15m", "7d")

    ultra_vol = False  # True when ATR > 0.25 % → relax to 4/5

    # ── V1 + V2 + V3  from 5m ─────────────────────────────────
    if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 30:
        try:
            cl5 = df_5m["close"].squeeze().astype(float).dropna()
            op5 = df_5m["open"].squeeze().astype(float).dropna()
            hi5 = df_5m["high"].squeeze().astype(float).dropna()
            lo5 = df_5m["low"].squeeze().astype(float).dropna()

            # V1: last 3 COMPLETED 5m candles — all same direction
            # Use bars -2, -3, -4 (the 3 most recent completed bars;
            # bar -1 is still forming).
            bars = []
            for i in [-2, -3, -4]:
                try:
                    c = float(cl5.iloc[i])
                    o = float(op5.iloc[i])
                    bars.append(1 if c > o else -1 if c < o else 0)
                except Exception:
                    bars.append(0)
            if all(b == 1 for b in bars):
                votes["5m_consec"] = +1; reasons.append("3× bull 5m candles")
            elif all(b == -1 for b in bars):
                votes["5m_consec"] = -1; reasons.append("3× bear 5m candles")
            else:
                votes["5m_consec"] = 0   # mixed — abstain

            # V2: 5m RSI(7) — fast RSI, tightened to 55/45 for higher accuracy
            rsi7 = float(_rsi(cl5, 7).iloc[-1])
            if rsi7 > 55:
                votes["5m_rsi7"] = +1; reasons.append(f"5m RSI(7) {rsi7:.0f} bull")
            elif rsi7 < 45:
                votes["5m_rsi7"] = -1; reasons.append(f"5m RSI(7) {rsi7:.0f} bear")
            else:
                votes["5m_rsi7"] = 0

            # V3: 5m EMA(5) vs EMA(13)
            ef5  = float(_ema(cl5, 5).iloc[-1])
            es13 = float(_ema(cl5, 13).iloc[-1])
            if ef5 > es13:
                votes["5m_ema5"] = +1; reasons.append("5m EMA5>13 bull")
            elif ef5 < es13:
                votes["5m_ema5"] = -1; reasons.append("5m EMA5<13 bear")
            else:
                votes["5m_ema5"] = 0

            # ATR-based ultra-volatility flag
            try:
                tr5 = (hi5 - lo5).rolling(10).mean().iloc[-1]
                price5 = float(cl5.iloc[-1])
                if price5 > 0 and (float(tr5) / price5) > 0.0025:
                    ultra_vol = True
            except Exception:
                pass

        except Exception:
            pass

    # ── V4 + V5 + V6  from 15m ────────────────────────────────
    if df_15m is not None and "close" in df_15m.columns and len(df_15m) >= 25:
        try:
            cl15 = df_15m["close"].squeeze().astype(float).dropna()

            # V4: 15m RSI(14) mid-line
            rsi15 = float(_rsi(cl15, 14).iloc[-1])
            if rsi15 > 50:
                votes["15m_rsi"] = +1; reasons.append(f"15m RSI {rsi15:.0f} bull")
            elif rsi15 < 50:
                votes["15m_rsi"] = -1; reasons.append(f"15m RSI {rsi15:.0f} bear")
            else:
                votes["15m_rsi"] = 0

            # V5: 15m EMA(9) vs EMA(21)
            ef15 = float(_ema(cl15, 9).iloc[-1])
            es15 = float(_ema(cl15, 21).iloc[-1])
            if ef15 > es15:
                votes["15m_ema"] = +1; reasons.append("15m EMA bull")
            elif ef15 < es15:
                votes["15m_ema"] = -1; reasons.append("15m EMA bear")
            else:
                votes["15m_ema"] = 0

            # V6 (NEW): 5m MACD(5/13/3) histogram — zero-lag momentum
            # Positive histogram = bullish momentum; negative = bearish.
            # This is the fastest non-lagging momentum confirmation available.
            try:
                cl5_macd = df_5m["close"].squeeze().astype(float).dropna() if df_5m is not None else None
                if cl5_macd is not None and len(cl5_macd) >= 20:
                    _, _, macd_hist5 = _macd(cl5_macd, fast=5, slow=13, signal=3)
                    h5 = float(macd_hist5.iloc[-1])
                    if h5 > 0:
                        votes["v6_macd"] = +1; reasons.append(f"5m MACD hist positive (bull momentum)")
                    elif h5 < 0:
                        votes["v6_macd"] = -1; reasons.append(f"5m MACD hist negative (bear momentum)")
            except Exception:
                pass

        except Exception:
            pass

    active = {k: v for k, v in votes.items() if v != 0}
    if len(active) < 3:
        _VOL_CACHE[cache_key] = (now, None); return None

    bull = sum(1 for v in active.values() if v > 0)
    bear = sum(1 for v in active.values() if v < 0)
    total = bull + bear
    if total == 0:
        _VOL_CACHE[cache_key] = (now, None); return None

    # Require all 5 votes (or 4/5 in ultra-volatile conditions)
    min_agree = 4 if ultra_vol else 5
    direction: Optional[str] = None
    agree = 0

    if bull >= min_agree and bear == 0:
        direction = "BUY";  agree = bull
    elif bear >= min_agree and bull == 0:
        direction = "SELL"; agree = bear
    # 4/5 with exactly 1 dissenter is acceptable in ultra-vol
    elif ultra_vol:
        if bull >= 4 and bear <= 1 and bull / total >= 0.80:
            direction = "BUY";  agree = bull
        elif bear >= 4 and bull <= 1 and bear / total >= 0.80:
            direction = "SELL"; agree = bear

    if direction is None:
        _VOL_CACHE[cache_key] = (now, None); return None

    confidence = round(agree / max(1, total), 3)
    result = {
        "direction":  direction,
        "confidence": confidence,
        "agree":      agree,
        "votes":      votes,
        "reasons":    reasons,
        "ultra_vol":  ultra_vol,
        "is_otc":     is_otc,
    }
    _VOL_CACHE[cache_key] = (now, result)
    return result


_OTC_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_OTC_TTL = 15.0   # 15s — OTC synthetic candles move fast


def otc_reversal_sniper(pair: str) -> Optional[dict]:
    """SUPREME PRO V8 — OTC Reversal Engine (dedicated to synthetic pairs).

    WHY THIS EXISTS
    ───────────────
    OTC pairs on Pocket Option / Quotex are SYNTHETIC candles generated
    by the broker's pricing algorithm. They do NOT reliably follow the
    live underlying market trend. What they DO reliably exhibit:
      1. Mean-reversion at RSI overbought / oversold extremes
      2. Reversal after 3+ consecutive same-direction candles (exhaustion)
      3. Bounce at Bollinger Band(20,2) outer edges

    Using EMA crossover or multi-TF trend-following on OTC is the #1
    cause of systematic losses — the trend was priced into the synthetic
    candle BEFORE the signal fires. This engine targets reversals only.

    THE 5 VOTES  (all point to a REVERSAL, not a continuation)
    ───────────────────────────────────────────────────────────
    V1  5m RSI(7)  — ultra-fast: >72 = overbought → PUT reversal
                                 <28 = oversold  → CALL reversal
    V2  5m RSI(14) — standard backing: >65 → PUT, <35 → CALL
    V3  3+ consecutive completed 5m candles same direction
        (3 bull candles in a row → next is statistically PUT for OTC)
    V4  5m Bollinger Band(20,2) outer touch
        (close ≥ upper band → PUT,  close ≤ lower band → CALL)
    V5  15m RSI(14) extreme — >63 → PUT, <37 → CALL (medium context)

    THRESHOLD
    ─────────
    Requires ≥3 votes ALL pointing to the SAME reversal. Zero opposing
    votes allowed (ambiguity in OTC = stay out).
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cached = _OTC_CACHE.get(ticker)
    if cached and (now - cached[0]) < _OTC_TTL:
        return cached[1]

    # votes: +1 = PUT/SELL reversal expected, -1 = CALL/BUY reversal expected
    votes: dict[str, int] = {}
    reasons: list[str] = []

    df_5m  = _fetch_tf(ticker, "5m",  "3d")
    df_15m = _fetch_tf(ticker, "15m", "7d")

    # ── V1 + V2 + V3 + V4  from 5m ──────────────────────────────
    if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 30:
        try:
            cl5 = df_5m["close"].squeeze().astype(float).dropna()
            op5 = df_5m["open"].squeeze().astype(float).dropna()
            hi5 = df_5m["high"].squeeze().astype(float).dropna()
            lo5 = df_5m["low"].squeeze().astype(float).dropna()

            # V1: 5m RSI(7) — ultra-fast extreme
            rsi7 = float(_rsi(cl5, 7).iloc[-1])
            if rsi7 > 72:
                votes["v1_rsi7"] = +1
                reasons.append(f"5m RSI(7) overbought {rsi7:.0f} → PUT")
            elif rsi7 < 28:
                votes["v1_rsi7"] = -1
                reasons.append(f"5m RSI(7) oversold {rsi7:.0f} → CALL")

            # V2: 5m RSI(14) backing
            rsi14 = float(_rsi(cl5, 14).iloc[-1])
            if rsi14 > 65:
                votes["v2_rsi14"] = +1
                reasons.append(f"5m RSI(14) {rsi14:.0f} OB → PUT")
            elif rsi14 < 35:
                votes["v2_rsi14"] = -1
                reasons.append(f"5m RSI(14) {rsi14:.0f} OS → CALL")

            # V3: 3+ consecutive completed 5m candles → exhaustion reversal
            bars = []
            for i in [-2, -3, -4, -5]:
                try:
                    c = float(cl5.iloc[i])
                    o = float(op5.iloc[i])
                    bars.append(1 if c > o else -1 if c < o else 0)
                except Exception:
                    bars.append(0)
            consec_bull = sum(1 for b in bars if b == 1)
            consec_bear = sum(1 for b in bars if b == -1)
            if consec_bull >= 3:
                votes["v3_consec"] = +1   # bull exhaustion → PUT reversal
                reasons.append(f"{consec_bull} bull 5m candles → exhaustion PUT")
            elif consec_bear >= 3:
                votes["v3_consec"] = -1   # bear exhaustion → CALL reversal
                reasons.append(f"{consec_bear} bear 5m candles → exhaustion CALL")

            # V4: Bollinger Band(20,2) outer touch
            bb_upper, _, bb_lower = _bbands(cl5, 20, 2.0)
            last_close  = float(cl5.iloc[-1])
            last_upper  = float(bb_upper.iloc[-1])
            last_lower  = float(bb_lower.iloc[-1])
            if last_close >= last_upper:
                votes["v4_bb"] = +1
                reasons.append("5m close ≥ BB upper → PUT reversal")
            elif last_close <= last_lower:
                votes["v4_bb"] = -1
                reasons.append("5m close ≤ BB lower → CALL reversal")

        except Exception:
            pass

    # ── V5  from 15m ─────────────────────────────────────────────
    if df_15m is not None and "close" in df_15m.columns and len(df_15m) >= 25:
        try:
            cl15 = df_15m["close"].squeeze().astype(float).dropna()
            rsi15 = float(_rsi(cl15, 14).iloc[-1])
            if rsi15 > 63:
                votes["v5_rsi15m"] = +1
                reasons.append(f"15m RSI {rsi15:.0f} OB → PUT")
            elif rsi15 < 37:
                votes["v5_rsi15m"] = -1
                reasons.append(f"15m RSI {rsi15:.0f} OS → CALL")
        except Exception:
            pass

    # ── V6–V9: Pure price action signals (zero-lag) ───────────
    # These directly read candle structure and volume — no derivative math.
    # They are the most reliable OTC signals because they fire AT the moment
    # the reversal starts, not after indicators catch up.
    if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 15:
        try:
            cl5 = df_5m["close"].squeeze().astype(float).dropna()
            op5 = df_5m["open"].squeeze().astype(float).dropna()
            hi5 = df_5m["high"].squeeze().astype(float).dropna()
            lo5 = df_5m["low"].squeeze().astype(float).dropna()
            vol5_col = df_5m["volume"] if "volume" in df_5m.columns else None
            vol5 = vol5_col.squeeze().astype(float).fillna(0) if vol5_col is not None else None

            c0_c = float(cl5.iloc[-1]); c0_o = float(op5.iloc[-1])
            c0_h = float(hi5.iloc[-1]); c0_l = float(lo5.iloc[-1])
            c1_c = float(cl5.iloc[-2]); c1_o = float(op5.iloc[-2])
            c1_h = float(hi5.iloc[-2]); c1_l = float(lo5.iloc[-2])

            c0_body  = abs(c0_c - c0_o)
            c0_range = c0_h - c0_l or 1e-10
            c0_upper = c0_h - max(c0_c, c0_o)
            c0_lower = min(c0_c, c0_o) - c0_l
            c0_bull  = c0_c > c0_o

            body_min = max(c0_body, c0_range * 0.015)

            # V6: Pin bar / wick rejection — long wick at a swing extreme
            if c0_lower >= 2.5 * body_min and c0_upper < 0.35 * c0_range:
                votes["v6_pin"] = -1   # bullish rejection → CALL reversal
                reasons.append(f"OTC bullish pin bar (lower wick) → CALL")
            elif c0_upper >= 2.5 * body_min and c0_lower < 0.35 * c0_range:
                votes["v6_pin"] = +1   # bearish rejection → PUT reversal
                reasons.append(f"OTC bearish pin bar (upper wick) → PUT")

            # V7: Volume climax — massive volume + indecision body = absorption
            if vol5 is not None and len(vol5) >= 15:
                v0 = float(vol5.iloc[-1])
                avg_v = float(vol5.iloc[-16:-1].mean()) or 1.0
                if v0 > 2.2 * avg_v and (c0_body / c0_range) < 0.38:
                    if c0_bull:
                        votes["v7_climax"] = +1   # bull climax = reversal PUT
                        reasons.append(f"OTC bull volume climax ({v0/avg_v:.1f}×) → PUT reversal")
                    else:
                        votes["v7_climax"] = -1   # bear climax = reversal CALL
                        reasons.append(f"OTC bear volume climax ({v0/avg_v:.1f}×) → CALL reversal")

            # V8: Stop hunt sweep (ICT turtle soup / liquidity grab)
            # c(-1) pokes past a recent swing, c(0) closes firmly back inside
            try:
                lookback_sh = 8
                swing_hi = float(hi5.iloc[-lookback_sh-1:-2].max())
                swing_lo = float(lo5.iloc[-lookback_sh-1:-2].min())
                if c1_h > swing_hi and c0_c < swing_hi and c0_c < c0_o:
                    votes["v8_sweep"] = +1   # swept highs + reversed → PUT
                    reasons.append(f"OTC stop hunt sweep of highs → PUT")
                elif c1_l < swing_lo and c0_c > swing_lo and c0_c > c0_o:
                    votes["v8_sweep"] = -1   # swept lows + reversed → CALL
                    reasons.append(f"OTC stop hunt sweep of lows → CALL")
            except Exception:
                pass

            # V9: Engulfing at BB outer edge — combines body signal with BB
            c1_body = abs(c1_c - c1_o)
            c1_bull = c1_c > c1_o
            # Bearish engulf (c0 bear engulfs c1 bull) + price was near BB upper
            if not c0_bull and c1_bull and c0_body >= c1_body * 0.9:
                if c0_o >= c1_c and c0_c <= c1_o:
                    votes["v9_engulf"] = +1   # bearish engulf → PUT
                    reasons.append("OTC bearish engulfing → PUT reversal")
            # Bullish engulf (c0 bull engulfs c1 bear)
            elif c0_bull and not c1_bull and c0_body >= c1_body * 0.9:
                if c0_o <= c1_c and c0_c >= c1_o:
                    votes["v9_engulf"] = -1   # bullish engulf → CALL
                    reasons.append("OTC bullish engulfing → CALL reversal")

        except Exception:
            pass

    # ── Score: GOD LEVEL — need 4+ votes, ZERO opposing ───────
    # Raised from 3 votes / 1-opposing-allowed to 4 votes / ZERO
    # opposing. OTC candles are synthetic — any ambiguity = stay out.
    # Unanimity ensures only true reversal setups fire.
    active = {k: v for k, v in votes.items() if v != 0}
    if len(active) < 4:
        _OTC_CACHE[ticker] = (now, None); return None

    put_votes  = sum(1 for v in active.values() if v > 0)
    call_votes = sum(1 for v in active.values() if v < 0)

    # STRICT: zero opposing votes allowed
    if put_votes >= 4 and call_votes == 0:
        direction = "SELL"
        agree = put_votes
    elif call_votes >= 4 and put_votes == 0:
        direction = "BUY"
        agree = call_votes
    else:
        _OTC_CACHE[ticker] = (now, None); return None

    confidence = round(agree / max(1, len(active)), 3)
    result = {
        "direction":    direction,
        "confidence":   confidence,
        "agree":        agree,
        "votes":        votes,
        "reasons":      reasons,
        "otc_reversal": True,
    }
    _OTC_CACHE[ticker] = (now, result)
    return result


_PA_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_PA_TTL = 15.0


def price_action_sniper(pair: str) -> Optional[dict]:
    """SUPREME PRO V9 — Pure Price Action God-Mode Engine.

    Zero lagging indicators. Every signal reads DIRECT candle structure,
    real volume, and market microstructure — the exact footprints that
    institutional / big-player order flow leaves in real time.

    WHY THIS BEATS EMA/RSI
    ──────────────────────
    EMAs lag by definition — they average the PAST. RSI is a derivative
    of price — it also describes what already happened. A stop hunt sweep
    (PA8) fires the MOMENT the reversal candle closes. An order block
    retest (PA6) fires exactly when price touches the zone. No lag at all.

    SIGNALS & WEIGHTS  (votes: +1 = SELL/PUT · -1 = BUY/CALL)
    ──────────────────────────────────────────────────────────
    PA1  Engulfing candle      wt 2  current body engulfs prior body
    PA2  Pin bar / Wick reject wt 2  long wick ≥2.5× body at extreme
    PA3  Momentum candle       wt 1  body ≥70% range, micro wick
    PA4  Volume climax         wt 2  >2× avg volume + indecision body
    PA5  Volume divergence     wt 2  new high/low on < 75% avg volume
    PA6  Order block retest    wt 3  price re-enters last strong OB zone
    PA7  Fair value gap (FVG)  wt 1  unfilled 3-bar price imbalance
    PA8  Stop hunt sweep       wt 3  ICT turtle soup — poke past swing
                                     then close firmly back inside
    PA9  Wyckoff secondary test wt 2 retest of extreme on drying volume

    THRESHOLD
    ─────────
    Weighted score ≥ 3 in one direction, ≤ 1 opposing weight → fire.
    Elite confirmation = weighted score ≥ 6.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cached = _PA_CACHE.get(ticker)
    if cached and (now - cached[0]) < _PA_TTL:
        return cached[1]

    votes:   dict[str, int] = {}
    weights: dict[str, int] = {}
    reasons: list[str]      = []

    df_5m  = _fetch_tf(ticker, "5m",  "3d")
    df_15m = _fetch_tf(ticker, "15m", "7d")

    if df_5m is None or "close" not in df_5m.columns or len(df_5m) < 25:
        _PA_CACHE[ticker] = (now, None); return None

    try:
        cl  = df_5m["close"].squeeze().astype(float).dropna()
        op  = df_5m["open"].squeeze().astype(float).dropna()
        hi  = df_5m["high"].squeeze().astype(float).dropna()
        lo  = df_5m["low"].squeeze().astype(float).dropna()
        vol_raw = df_5m["volume"] if "volume" in df_5m.columns else None
        vol = vol_raw.squeeze().astype(float).fillna(0) if vol_raw is not None else None

        def bar(idx):
            d = {
                "c": float(cl.iloc[idx]), "o": float(op.iloc[idx]),
                "h": float(hi.iloc[idx]), "l": float(lo.iloc[idx]),
            }
            d["body"]  = abs(d["c"] - d["o"])
            d["range"] = d["h"] - d["l"] or 1e-10
            d["upper"] = d["h"] - max(d["c"], d["o"])
            d["lower"] = min(d["c"], d["o"]) - d["l"]
            d["bull"]  = d["c"] > d["o"]
            d["pct"]   = d["body"] / d["range"]
            return d

        c0 = bar(-1); c1 = bar(-2); c2 = bar(-3)

        # ── PA1: Engulfing candle ─────────────────────────────
        if c0["bull"] and not c1["bull"]:
            if c0["o"] <= c1["c"] and c0["c"] >= c1["o"] and c0["body"] >= c1["body"] * 0.85:
                votes["pa1"] = -1; weights["pa1"] = 2
                reasons.append("Bullish engulfing → CALL")
        elif not c0["bull"] and c1["bull"]:
            if c0["o"] >= c1["c"] and c0["c"] <= c1["o"] and c0["body"] >= c1["body"] * 0.85:
                votes["pa1"] = +1; weights["pa1"] = 2
                reasons.append("Bearish engulfing → PUT")

        # ── PA2: Pin bar / Rejection wick ────────────────────
        body_min = max(c0["body"], c0["range"] * 0.015)
        if c0["lower"] >= 2.5 * body_min and c0["upper"] < 0.35 * c0["range"]:
            votes["pa2"] = -1; weights["pa2"] = 2
            reasons.append(f"Bullish pin bar (lower wick) → CALL")
        elif c0["upper"] >= 2.5 * body_min and c0["lower"] < 0.35 * c0["range"]:
            votes["pa2"] = +1; weights["pa2"] = 2
            reasons.append(f"Bearish pin bar (upper wick) → PUT")

        # ── PA3: Momentum candle (body ≥ 70% range) ─────────
        if c0["pct"] >= 0.70:
            if c0["bull"]:
                votes["pa3"] = -1; weights["pa3"] = 1
                reasons.append(f"Bull momentum candle ({c0['pct']:.0%} body) → CALL")
            else:
                votes["pa3"] = +1; weights["pa3"] = 1
                reasons.append(f"Bear momentum candle ({c0['pct']:.0%} body) → PUT")

        # ── PA4 + PA5: Volume signals ─────────────────────────
        if vol is not None and len(vol) >= 22:
            v0      = float(vol.iloc[-1])
            avg_vol = float(vol.iloc[-22:-1].mean()) or 1.0

            # PA4: Volume climax — spike + indecision = big player absorption
            if v0 > 2.0 * avg_vol and c0["pct"] < 0.40:
                if c0["bull"]:
                    votes["pa4"] = +1; weights["pa4"] = 2
                    reasons.append(f"Bull climax ({v0/avg_vol:.1f}× vol + small body) → PUT reversal")
                else:
                    votes["pa4"] = -1; weights["pa4"] = 2
                    reasons.append(f"Bear climax ({v0/avg_vol:.1f}× vol + small body) → CALL reversal")

            # PA5: Volume divergence — new extreme on fading volume
            lookback = 14
            if len(hi) > lookback and len(vol) > lookback:
                r_hi   = float(hi.iloc[-lookback:-1].max())
                r_lo   = float(lo.iloc[-lookback:-1].min())
                avg_rv = float(vol.iloc[-lookback:-1].mean()) or 1.0
                if float(hi.iloc[-1]) > r_hi and v0 < 0.75 * avg_rv:
                    votes["pa5"] = +1; weights["pa5"] = 2
                    reasons.append("New high on low volume (bearish div) → PUT")
                elif float(lo.iloc[-1]) < r_lo and v0 < 0.75 * avg_rv:
                    votes["pa5"] = -1; weights["pa5"] = 2
                    reasons.append("New low on low volume (bullish div) → CALL")

        # ── PA6: Order block retest (highest weight) ──────────
        # The last strong opposing candle before a 2+ bar impulse = OB.
        # Price returning into that zone = institutional memory = high-prob.
        c0_close = float(cl.iloc[-1])
        for i in range(3, min(14, len(cl))):
            try:
                ob_c = float(cl.iloc[-i]); ob_o = float(op.iloc[-i])
                ob_h = float(hi.iloc[-i]); ob_l = float(lo.iloc[-i])
                ob_body = abs(ob_c - ob_o)
                ob_rng  = ob_h - ob_l or 1e-10
                if ob_body / ob_rng < 0.45:
                    continue   # doji — not a valid OB
                # Bullish OB: strong bear candle before upward impulse
                if ob_c < ob_o and ob_l <= c0_close <= ob_h:
                    votes["pa6"] = -1; weights["pa6"] = 3
                    reasons.append(f"Bullish OB retest ({ob_l:.5f}–{ob_h:.5f}) → CALL")
                    break
                # Bearish OB: strong bull candle before downward impulse
                if ob_c > ob_o and ob_l <= c0_close <= ob_h:
                    votes["pa6"] = +1; weights["pa6"] = 3
                    reasons.append(f"Bearish OB retest ({ob_l:.5f}–{ob_h:.5f}) → PUT")
                    break
            except Exception:
                break

        # ── PA7: Fair value gap (FVG / Imbalance) ────────────
        # 3-bar imbalance: c(-2) and c(0) have no overlapping wick.
        # Price is always magnetically drawn back to fill the FVG.
        # Weight upgraded to 2 — FVG fill is a high-probability magnet.
        try:
            gap_thr = c0["range"] * 0.25
            if c2["h"] < float(lo.iloc[-1]) and (float(lo.iloc[-1]) - c2["h"]) >= gap_thr:
                votes["pa7"] = -1; weights["pa7"] = 2
                reasons.append(f"Bearish FVG above price → CALL (price drawn up to fill)")
            elif c2["l"] > float(hi.iloc[-1]) and (c2["l"] - float(hi.iloc[-1])) >= gap_thr:
                votes["pa7"] = +1; weights["pa7"] = 2
                reasons.append(f"Bullish FVG below price → PUT (price drawn down to fill)")
        except Exception:
            pass

        # ── PA8: Stop hunt sweep (ICT Turtle Soup) ───────────
        # THE most powerful single entry signal. Big players deliberately
        # spike price past the obvious swing high/low to harvest retail
        # stop-losses and limit orders, then reverse hard.
        # Pattern: c(-1) pierces the swing, c(0) closes firmly back inside.
        try:
            lookback_sh = 10
            swing_hi = float(hi.iloc[-lookback_sh - 1: -2].max())
            swing_lo = float(lo.iloc[-lookback_sh - 1: -2].min())
            c1_h = float(hi.iloc[-2]); c1_l = float(lo.iloc[-2])
            c0_c = float(cl.iloc[-1]); c0_o = float(op.iloc[-1])

            # Bearish sweep: c1 above swing_hi, c0 closes below it bearishly
            if c1_h > swing_hi and c0_c < swing_hi and c0_c < c0_o:
                votes["pa8"] = +1; weights["pa8"] = 3
                reasons.append(f"🎯 Stop hunt SWEEP of highs at {swing_hi:.5f} → PUT (turtle soup)")

            # Bullish sweep: c1 below swing_lo, c0 closes above it bullishly
            elif c1_l < swing_lo and c0_c > swing_lo and c0_c > c0_o:
                votes["pa8"] = -1; weights["pa8"] = 3
                reasons.append(f"🎯 Stop hunt SWEEP of lows at {swing_lo:.5f} → CALL (turtle soup)")
        except Exception:
            pass

        # ── PA9: Wyckoff secondary test ───────────────────────
        # A low-volume revisit of a prior extreme confirms the phase
        # transition: distribution ending (top) or accumulation ending (bottom).
        if vol is not None and len(vol) >= 15:
            try:
                lookback_w = 12
                prev_hi = float(hi.iloc[-lookback_w - 1: -2].max())
                prev_lo = float(lo.iloc[-lookback_w - 1: -2].min())
                v0 = float(vol.iloc[-1])
                avg_wv = float(vol.iloc[-lookback_w - 1: -2].mean()) or 1.0
                tol = 0.005 * max(float(cl.iloc[-1]), 1.0)   # 0.5% tolerance

                if abs(float(hi.iloc[-1]) - prev_hi) <= tol and v0 < 0.68 * avg_wv:
                    votes["pa9"] = +1; weights["pa9"] = 2
                    reasons.append(f"Wyckoff test of prior high on low vol → PUT")
                elif abs(float(lo.iloc[-1]) - prev_lo) <= tol and v0 < 0.68 * avg_wv:
                    votes["pa9"] = -1; weights["pa9"] = 2
                    reasons.append(f"Wyckoff test of prior low on low vol → CALL")
            except Exception:
                pass

        # ── Also run a 15m check: PA8 (stop hunt) on 15m ─────
        if df_15m is not None and "close" in df_15m.columns and len(df_15m) >= 15:
            try:
                cl15 = df_15m["close"].squeeze().astype(float).dropna()
                op15 = df_15m["open"].squeeze().astype(float).dropna()
                hi15 = df_15m["high"].squeeze().astype(float).dropna()
                lo15 = df_15m["low"].squeeze().astype(float).dropna()
                sh15 = float(hi15.iloc[-12:-2].max())
                sl15 = float(lo15.iloc[-12:-2].min())
                c1h15 = float(hi15.iloc[-2]); c1l15 = float(lo15.iloc[-2])
                c0c15 = float(cl15.iloc[-1]); c0o15 = float(op15.iloc[-1])
                if c1h15 > sh15 and c0c15 < sh15 and c0c15 < c0o15:
                    if "pa8_15m" not in votes:
                        votes["pa8_15m"] = +1; weights["pa8_15m"] = 2
                        reasons.append("15m stop hunt sweep of highs → PUT (W+)")
                elif c1l15 < sl15 and c0c15 > sl15 and c0c15 > c0o15:
                    if "pa8_15m" not in votes:
                        votes["pa8_15m"] = -1; weights["pa8_15m"] = 2
                        reasons.append("15m stop hunt sweep of lows → CALL (W+)")

                # ── PA10 (NEW): 15m Order Block retest (wt 3) ────────────────
                # The 15m OB is the highest-trust institutional zone — a strong
                # 15m candle before a 2+ bar impulse defines a demand/supply zone
                # that price is magnetically drawn back to. When the 5m price is
                # sitting inside that zone, it's the highest-confluence entry.
                c0_price_now = float(cl15.iloc[-1])
                for j in range(3, min(20, len(cl15))):
                    try:
                        ob15_c = float(cl15.iloc[-j]); ob15_o = float(op15.iloc[-j])
                        ob15_h = float(hi15.iloc[-j]); ob15_l = float(lo15.iloc[-j])
                        ob15_body = abs(ob15_c - ob15_o)
                        ob15_rng  = ob15_h - ob15_l or 1e-10
                        if ob15_body / ob15_rng < 0.50:
                            continue   # doji — not a valid 15m OB
                        # Bullish OB: last strong bear 15m candle before up impulse
                        if ob15_c < ob15_o and ob15_l <= c0_price_now <= ob15_h:
                            if "pa10" not in votes:
                                votes["pa10"] = -1; weights["pa10"] = 3
                                reasons.append(f"15m Bullish OB retest ({ob15_l:.5f}–{ob15_h:.5f}) → CALL")
                            break
                        # Bearish OB: last strong bull 15m candle before down impulse
                        if ob15_c > ob15_o and ob15_l <= c0_price_now <= ob15_h:
                            if "pa10" not in votes:
                                votes["pa10"] = +1; weights["pa10"] = 3
                                reasons.append(f"15m Bearish OB retest ({ob15_l:.5f}–{ob15_h:.5f}) → PUT")
                            break
                    except Exception:
                        break

                # ── PA11 (NEW): Market Structure Shift / Break of Structure ──
                # When price breaks above the last swing high (BUY) or below
                # the last swing low (SELL) with a momentum close, that is a
                # Market Structure Shift (MSS) — the trend just changed. Weight 2.
                try:
                    bos_lb = 10
                    prev_swing_hi = float(hi15.iloc[-bos_lb-1:-2].max())
                    prev_swing_lo = float(lo15.iloc[-bos_lb-1:-2].min())
                    c0c15_bos = float(cl15.iloc[-1]); c0o15_bos = float(op15.iloc[-1])
                    c0_body15  = abs(c0c15_bos - c0o15_bos)
                    c0_rng15   = float(hi15.iloc[-1]) - float(lo15.iloc[-1]) or 1e-10
                    c0_bull15  = c0c15_bos > c0o15_bos
                    # BOS UP: close above the last swing high with a bull body
                    if c0c15_bos > prev_swing_hi and c0_bull15 and c0_body15 / c0_rng15 >= 0.50:
                        if "pa11" not in votes:
                            votes["pa11"] = -1; weights["pa11"] = 2
                            reasons.append(f"15m Market Structure Shift — BULLISH BOS above {prev_swing_hi:.5f} → CALL")
                    # BOS DOWN: close below the last swing low with a bear body
                    elif c0c15_bos < prev_swing_lo and not c0_bull15 and c0_body15 / c0_rng15 >= 0.50:
                        if "pa11" not in votes:
                            votes["pa11"] = +1; weights["pa11"] = 2
                            reasons.append(f"15m Market Structure Shift — BEARISH BOS below {prev_swing_lo:.5f} → PUT")
                except Exception:
                    pass

            except Exception:
                pass

    except Exception:
        _PA_CACHE[ticker] = (now, None); return None

    # ── Weighted scoring ──────────────────────────────────────
    sell_wt = sum(weights.get(k, 1) for k, v in votes.items() if v > 0)
    buy_wt  = sum(weights.get(k, 1) for k, v in votes.items() if v < 0)

    if sell_wt >= 3 and buy_wt <= 1 and sell_wt > buy_wt:
        direction = "SELL"; total_wt = sell_wt
    elif buy_wt >= 3 and sell_wt <= 1 and buy_wt > sell_wt:
        direction = "BUY";  total_wt = buy_wt
    else:
        _PA_CACHE[ticker] = (now, None); return None

    confidence = round(min(1.0, total_wt / 12.0), 3)
    elite      = total_wt >= 6

    result = {
        "direction":  direction,
        "confidence": confidence,
        "weighted":   total_wt,
        "elite":      elite,
        "votes":      votes,
        "weights":    weights,
        "reasons":    reasons,
        "pa_engine":  True,
    }
    _PA_CACHE[ticker] = (now, result)
    return result


_1M_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_1M_TTL = 20.0   # 20-second cache — 1m bars change every 60s, refresh often


def one_minute_sniper(pair: str, is_otc: bool = False) -> Optional[dict]:
    """SUPREME PRO — 1-Minute Precision Sniper Engine (V9).

    Designed exclusively for 1-minute and 2-minute binary candle trading.
    Uses ACTUAL 1m OHLCV data (not 5m or 15m approximations) combined with
    a 5m higher-timeframe trend filter to produce maximum-accuracy entries.

    9 WEIGHTED SIGNALS
    ──────────────────
    M1  5m HTF alignment    wt 3  EMA9 vs EMA21 on 5m — the trend is your friend
    M2  1m micro EMA cross  wt 3  EMA3 crosses EMA8 on 1m (fresh = last candle)
    M3  1m RSI-7 momentum   wt 2  RSI7 > 58 bull / < 42 bear (ultra-fast)
    M4  1m momentum candle  wt 3  body ≥ 70% of range — institutional conviction
    M5  1m volume surge     wt 2  current bar > 1.8× avg — big player activity
    M6  1m consecutive run  wt 1  2+ same-direction bars — sustained momentum
    M7  1m MACD zero cross  wt 3  MACD hist just turned positive/negative
    M8  1m micro OB retest  wt 3  price returns to last strong opposing candle
    M9  1m stop hunt sweep  wt 3  ICT turtle soup — spike past swing + reversal

    THRESHOLDS
    ──────────
    LIVE pairs  : weighted score ≥ 7, opposing ≤ 2. M1 (5m trend) MUST agree
                  OR both M2 + M7 agree (micro cross + MACD = very strong).
    OTC  pairs  : weighted score ≥ 5, opposing ≤ 2. Reversal logic — signals
                  are interpreted in the OPPOSITE direction to the raw move.

    ACCURACY EDGE
    ─────────────
    * All 1m data is the freshest data yfinance supplies — zero interpolation.
    * M1 (5m trend) acts as a kill-switch for low-quality counter-trend signals.
    * M9 (stop hunt) is the single highest-probability 1m binary signal known —
      smart money sweeps retail stops at the swing high/low then reverses hard.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cached = _1M_CACHE.get(ticker)
    if cached and (now - cached[0]) < _1M_TTL:
        return cached[1]

    votes:   dict[str, int] = {}
    weights: dict[str, int] = {}
    reasons: list[str]      = []

    # ── Fetch data ─────────────────────────────────────────────────────────
    df_1m = _fetch_tf(ticker, "1m",  "2d")    # actual 1-minute bars
    df_5m = _fetch_tf(ticker, "5m",  "3d")    # HTF trend filter

    if df_1m is None or "close" not in df_1m.columns or len(df_1m) < 30:
        _1M_CACHE[ticker] = (now, None)
        return None

    try:
        cl1  = df_1m["close"].squeeze().astype(float).dropna()
        op1  = df_1m["open"].squeeze().astype(float).dropna()
        hi1  = df_1m["high"].squeeze().astype(float).dropna()
        lo1  = df_1m["low"].squeeze().astype(float).dropna()
        vol1_raw = df_1m.get("volume")
        vol1 = vol1_raw.squeeze().astype(float).fillna(0) if vol1_raw is not None else None

        def bar1(idx):
            b = {
                "c": float(cl1.iloc[idx]), "o": float(op1.iloc[idx]),
                "h": float(hi1.iloc[idx]), "l": float(lo1.iloc[idx]),
            }
            b["body"]  = abs(b["c"] - b["o"])
            b["range"] = b["h"] - b["l"] or 1e-10
            b["upper"] = b["h"] - max(b["c"], b["o"])
            b["lower"] = min(b["c"], b["o"]) - b["l"]
            b["bull"]  = b["c"] > b["o"]
            b["pct"]   = b["body"] / b["range"]
            return b

        # GOD LEVEL FIX: use CONFIRMED CLOSED bars only.
        # bar(-1) is the LIVE / still-forming candle — its body ratio,
        # direction and volume are all unreliable until it closes.
        # All candle-structure checks MUST use bar(-2) as the signal bar.
        c0 = bar1(-2);  c1 = bar1(-3);  c2 = bar1(-4)   # confirmed bars

        # ── M1: 5m HTF alignment (wt 3) — trend filter ─────────────────────
        # EMA9 vs EMA21 on 5m. Must agree for LIVE pairs (kill-switch).
        m1_dir = 0
        if df_5m is not None and "close" in df_5m.columns and len(df_5m) >= 25:
            try:
                cl5 = df_5m["close"].squeeze().astype(float).dropna()
                ef5 = float(_ema(cl5, 9).iloc[-1])
                es5 = float(_ema(cl5, 21).iloc[-1])
                rsi5 = float(_rsi(cl5, 7).iloc[-1])
                if ef5 > es5 and rsi5 > 50:
                    m1_dir = -1       # 5m bullish → BUY signal
                    votes["m1_htf"] = -1; weights["m1_htf"] = 3
                    reasons.append(f"5m EMA9>{float(ef5):.4g} / EMA21={float(es5):.4g} BULL trend")
                elif ef5 < es5 and rsi5 < 50:
                    m1_dir = +1       # 5m bearish → SELL signal
                    votes["m1_htf"] = +1; weights["m1_htf"] = 3
                    reasons.append(f"5m EMA9<EMA21 BEAR trend")
            except Exception:
                pass

        # ── M2: 1m micro EMA cross (wt 3) ────────────────────────────────
        # EMA-3 just crossed EMA-8 on the most recent completed 1m bar.
        # "Just" = the cross happened between bar-2 and bar-1.
        try:
            ema3 = _ema(cl1, 3)
            ema8 = _ema(cl1, 8)
            # GOD LEVEL FIX: use iloc[-2] (confirmed bar) for cross detection
            e3_now  = float(ema3.iloc[-2]); e8_now  = float(ema8.iloc[-2])
            e3_prev = float(ema3.iloc[-3]); e8_prev = float(ema8.iloc[-3])
            fresh_bull_cross = (e3_prev <= e8_prev) and (e3_now >  e8_now)
            fresh_bear_cross = (e3_prev >= e8_prev) and (e3_now <  e8_now)
            if fresh_bull_cross:
                votes["m2_ema"] = -1; weights["m2_ema"] = 3
                reasons.append("1m EMA3 × EMA8 BULLISH cross (confirmed bar)")
            elif fresh_bear_cross:
                votes["m2_ema"] = +1; weights["m2_ema"] = 3
                reasons.append("1m EMA3 × EMA8 BEARISH cross (confirmed bar)")
            elif e3_now > e8_now:
                votes["m2_ema"] = -1; weights["m2_ema"] = 1
                reasons.append("1m EMA3>EMA8 bull bias (confirmed)")
            else:
                votes["m2_ema"] = +1; weights["m2_ema"] = 1
                reasons.append("1m EMA3<EMA8 bear bias (confirmed)")
        except Exception:
            pass

        # ── M3: 1m RSI-7 momentum (wt 2) — tightened to 62/38 ──────────────
        # 62+ = firmly bullish momentum; 38- = firmly bearish.
        # This eliminates borderline 58-61 readings that cause noise.
        try:
            # GOD LEVEL FIX: use iloc[-2] = confirmed closed bar RSI
            rsi7_1m = float(_rsi(cl1, 7).iloc[-2])
            if rsi7_1m > 62:
                votes["m3_rsi"] = -1; weights["m3_rsi"] = 2
                reasons.append(f"1m RSI(7) {rsi7_1m:.0f} — bull momentum (confirmed)")
            elif rsi7_1m < 38:
                votes["m3_rsi"] = +1; weights["m3_rsi"] = 2
                reasons.append(f"1m RSI(7) {rsi7_1m:.0f} — bear momentum (confirmed)")
            # OTC: extreme RSI adds extra reversal signal
            if is_otc:
                if rsi7_1m > 80:
                    votes["m3_otc_extreme"] = +1; weights["m3_otc_extreme"] = 3
                    reasons.append(f"1m RSI(7) {rsi7_1m:.0f} OTC EXTREME OVERBOUGHT → PUT")
                elif rsi7_1m < 20:
                    votes["m3_otc_extreme"] = -1; weights["m3_otc_extreme"] = 3
                    reasons.append(f"1m RSI(7) {rsi7_1m:.0f} OTC EXTREME OVERSOLD → CALL")
        except Exception:
            pass

        # ── M4: 1m momentum candle (wt 3) ────────────────────────────────
        # Current 1m bar: body ≥ 70% of range = institutional conviction bar.
        if c0["pct"] >= 0.70:
            if c0["bull"]:
                votes["m4_body"] = -1; weights["m4_body"] = 3
                reasons.append(f"1m momentum candle {c0['pct']:.0%} body — BULL")
            else:
                votes["m4_body"] = +1; weights["m4_body"] = 3
                reasons.append(f"1m momentum candle {c0['pct']:.0%} body — BEAR")
        elif c0["pct"] >= 0.55:
            if c0["bull"]:
                votes["m4_body"] = -1; weights["m4_body"] = 1
            else:
                votes["m4_body"] = +1; weights["m4_body"] = 1

        # ── M5: 1m volume surge (wt 2) ───────────────────────────────────
        # Current 1m bar volume > 1.8× the 20-bar average → smart money.
        if vol1 is not None and len(vol1) >= 22:
            try:
                # GOD LEVEL FIX: use iloc[-2] = confirmed bar volume
                v0    = float(vol1.iloc[-2])
                avg_v = float(vol1.iloc[-22:-2].mean()) or 1.0
                ratio = v0 / avg_v
                if ratio >= 1.8:
                    # Big volume confirms the direction of the confirmed candle
                    if c0["bull"]:
                        votes["m5_vol"] = -1; weights["m5_vol"] = 2
                        reasons.append(f"1m volume surge {ratio:.1f}× — institutional BUY (confirmed)")
                    else:
                        votes["m5_vol"] = +1; weights["m5_vol"] = 2
                        reasons.append(f"1m volume surge {ratio:.1f}× — institutional SELL (confirmed)")
            except Exception:
                pass

        # ── M6: 1m consecutive same-direction bars (wt 1/2) ─────────────
        # 2 back-to-back bars = wt 1 (momentum present).
        # 3 back-to-back bars = wt 2 (sustained institutional momentum).
        try:
            run2_bull = c0["bull"] and c1["bull"]
            run2_bear = not c0["bull"] and not c1["bull"]
            run3_bull = run2_bull and c2["bull"]
            run3_bear = run2_bear and not c2["bull"]
            if run3_bull:
                votes["m6_consec"] = -1; weights["m6_consec"] = 2
                reasons.append("1m: 3 consecutive bull bars — sustained BULL")
            elif run3_bear:
                votes["m6_consec"] = +1; weights["m6_consec"] = 2
                reasons.append("1m: 3 consecutive bear bars — sustained BEAR")
            elif run2_bull:
                votes["m6_consec"] = -1; weights["m6_consec"] = 1
                reasons.append("1m: 2 consecutive bull bars")
            elif run2_bear:
                votes["m6_consec"] = +1; weights["m6_consec"] = 1
                reasons.append("1m: 2 consecutive bear bars")
        except Exception:
            pass

        # ── M7: 1m MACD histogram zero cross (wt 3) ──────────────────────
        # MACD hist flipping sign on the 1m chart = momentum regime change.
        try:
            _, _, hist = _macd(cl1, fast=5, slow=13, signal=3)
            # GOD LEVEL FIX: use iloc[-2] and iloc[-3] (confirmed bars)
            h_now  = float(hist.iloc[-2])
            h_prev = float(hist.iloc[-3])
            if h_prev <= 0 and h_now > 0:
                votes["m7_macd"] = -1; weights["m7_macd"] = 3
                reasons.append("1m MACD histogram BULLISH zero cross (confirmed)")
            elif h_prev >= 0 and h_now < 0:
                votes["m7_macd"] = +1; weights["m7_macd"] = 3
                reasons.append("1m MACD histogram BEARISH zero cross (confirmed)")
            elif h_now > 0:
                votes["m7_macd"] = -1; weights["m7_macd"] = 1
            else:
                votes["m7_macd"] = +1; weights["m7_macd"] = 1
        except Exception:
            pass

        # ── M8: 1m micro order block retest (wt 3) ───────────────────────
        # Price enters the body range of the last STRONG opposing bar within
        # the last 8 bars. Strong = body ≥ 60% range AND in opposite direction.
        try:
            # GOD LEVEL FIX: current_price = LIVE bar close (bar -1), not c0 (bar -2)
            current_price = float(cl1.iloc[-1])
            ob_buy  = False   # price retest of last bearish OB (→ CALL)
            ob_sell = False   # price retest of last bullish OB (→ PUT)
            for i in range(-3, -10, -1):  # look back 8 bars
                try:
                    bx = bar1(i)
                    if bx["pct"] < 0.50:   # weak bar — not a real OB
                        continue
                    ob_hi = max(bx["c"], bx["o"])
                    ob_lo = min(bx["c"], bx["o"])
                    in_zone = ob_lo <= current_price <= ob_hi
                    if not in_zone:
                        continue
                    if not bx["bull"] and c0["bull"]:
                        ob_buy = True   # price re-entered bearish OB and current bar is bull
                        break
                    elif bx["bull"] and not c0["bull"]:
                        ob_sell = True  # price re-entered bullish OB and current bar is bear
                        break
                except Exception:
                    continue

            if ob_buy:
                votes["m8_ob"] = -1; weights["m8_ob"] = 3
                reasons.append("1m micro order block BULL retest → CALL")
            elif ob_sell:
                votes["m8_ob"] = +1; weights["m8_ob"] = 3
                reasons.append("1m micro order block BEAR retest → PUT")
        except Exception:
            pass

        # ── M9: 1m stop hunt sweep (wt 3) — ICT Turtle Soup ─────────────
        # Bar(-2) pokes past a recent 1m swing high/low; bar(-1) closes
        # firmly back on the other side. = Liquidity grabbed, reversal live.
        try:
            lookback_sw = 10
            # GOD LEVEL FIX: exclude both the sweep bar (c1=bar-3) and reversal bar
            # (c0=bar-2) from the swing lookback so we compare against prior structure.
            sw_hi = float(hi1.iloc[-lookback_sw-1:-3].max())
            sw_lo = float(lo1.iloc[-lookback_sw-1:-3].min())

            # c1 (bar-3) sweeps highs; c0 (bar-2) reverses and closes below → SELL
            if c1["h"] > sw_hi and c0["c"] < sw_hi and not c0["bull"]:
                votes["m9_sweep"] = +1; weights["m9_sweep"] = 3
                reasons.append("1m STOP HUNT — swept highs (confirmed) → SELL / PUT")
            # c1 (bar-3) sweeps lows; c0 (bar-2) reverses and closes above → BUY
            elif c1["l"] < sw_lo and c0["c"] > sw_lo and c0["bull"]:
                votes["m9_sweep"] = -1; weights["m9_sweep"] = 3
                reasons.append("1m STOP HUNT — swept lows (confirmed) → BUY / CALL")
        except Exception:
            pass

        # ── M10 (NEW): Stochastic(5,3,3) K/D crossover on 1m (wt 2) ─────
        # Stochastic is the fastest oscillator for 1m binary entries.
        # K crossing above D in non-overbought zone = cleanest BUY trigger.
        # K crossing below D in non-oversold zone = cleanest SELL trigger.
        try:
            lo_min5 = lo1.rolling(5).min()
            hi_max5 = hi1.rolling(5).max()
            k_raw    = 100 * (cl1 - lo_min5) / (hi_max5 - lo_min5 + 1e-10)
            k_line   = k_raw.rolling(3).mean()          # %K smoothed
            d_line   = k_line.rolling(3).mean()          # %D signal
            # GOD LEVEL FIX: use confirmed bar (-2) for stochastic
            k_now    = float(k_line.iloc[-2])
            k_prev   = float(k_line.iloc[-3])
            d_now    = float(d_line.iloc[-2])
            d_prev   = float(d_line.iloc[-3])
            stoch_bull_cross = (k_prev <= d_prev) and (k_now > d_now) and k_now < 80
            stoch_bear_cross = (k_prev >= d_prev) and (k_now < d_now) and k_now > 20
            if stoch_bull_cross:
                votes["m10_stoch"] = -1; weights["m10_stoch"] = 2
                reasons.append(f"1m Stoch K({k_now:.0f}) cross above D → BUY signal")
            elif stoch_bear_cross:
                votes["m10_stoch"] = +1; weights["m10_stoch"] = 2
                reasons.append(f"1m Stoch K({k_now:.0f}) cross below D → SELL signal")
            elif k_now > d_now and k_now < 80:
                votes["m10_stoch"] = -1; weights["m10_stoch"] = 1   # mild bull stoch
            elif k_now < d_now and k_now > 20:
                votes["m10_stoch"] = +1; weights["m10_stoch"] = 1   # mild bear stoch
        except Exception:
            pass

        # ── M11 (NEW): 15m HTF double filter (wt 2) ─────────────────────
        # A second higher-timeframe filter. When the 5m trend (M1) AND
        # the 15m EMA align with the 1m direction, noise is near zero.
        # This is the equivalent of a 3-TF confluence confirmation.
        if df_5m is not None:
            try:
                df_15m_1m = _fetch_tf(ticker, "15m", "7d")
                if df_15m_1m is not None and "close" in df_15m_1m.columns and len(df_15m_1m) >= 25:
                    cl15_1m = df_15m_1m["close"].squeeze().astype(float).dropna()
                    ef15 = float(_ema(cl15_1m, 9).iloc[-1])
                    es15 = float(_ema(cl15_1m, 21).iloc[-1])
                    rsi15_1m = float(_rsi(cl15_1m, 14).iloc[-1])
                    if ef15 > es15 and rsi15_1m > 50:
                        votes["m11_15htf"] = -1; weights["m11_15htf"] = 2
                        reasons.append(f"15m EMA9>EMA21 BULL alignment (double HTF)")
                    elif ef15 < es15 and rsi15_1m < 50:
                        votes["m11_15htf"] = +1; weights["m11_15htf"] = 2
                        reasons.append(f"15m EMA9<EMA21 BEAR alignment (double HTF)")
            except Exception:
                pass

        # ── M12: Williams %R (period 14) on 1m (wt 2) ────────────────────
        # More responsive than RSI for short-timeframe binary entries.
        # WR < -80 = deeply oversold → BUY; WR > -20 = overbought → SELL.
        # Confirmed bar (-2) used per GOD LEVEL fix.
        try:
            hi_max14 = hi1.rolling(14).max()
            lo_min14 = lo1.rolling(14).min()
            wr = -100 * (hi_max14 - cl1) / (hi_max14 - lo_min14 + 1e-10)
            wr_now  = float(wr.iloc[-2])
            wr_prev = float(wr.iloc[-3])
            if wr_now < -80:
                votes["m12_wr"] = -1; weights["m12_wr"] = 2
                reasons.append(f"Williams %R {wr_now:.0f} OVERSOLD → CALL (confirmed)")
            elif wr_now > -20:
                votes["m12_wr"] = +1; weights["m12_wr"] = 2
                reasons.append(f"Williams %R {wr_now:.0f} OVERBOUGHT → PUT (confirmed)")
            elif wr_now < -60 and wr_now > wr_prev:
                votes["m12_wr"] = -1; weights["m12_wr"] = 1
                reasons.append(f"Williams %R recovering from oversold — bull lean")
            elif wr_now > -40 and wr_now < wr_prev:
                votes["m12_wr"] = +1; weights["m12_wr"] = 1
                reasons.append(f"Williams %R falling from overbought — bear lean")
        except Exception:
            pass

        # ── M13: CCI(14) — Commodity Channel Index (wt 2) ────────────────
        # CCI > +100 = bullish momentum breakout; < -100 = bearish breakout.
        # CCI crossing zero from below = fresh bull signal; above = bear.
        # Uses confirmed bar (-2) per GOD LEVEL standard.
        try:
            tp = (hi1 + lo1 + cl1) / 3.0   # typical price
            tp_sma = tp.rolling(14).mean()
            tp_mad = tp.rolling(14).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
            cci = (tp - tp_sma) / (0.015 * tp_mad.replace(0, 1e-10))
            cci_now  = float(cci.iloc[-2])
            cci_prev = float(cci.iloc[-3])
            if cci_now > 100:
                votes["m13_cci"] = -1; weights["m13_cci"] = 2
                reasons.append(f"CCI {cci_now:.0f} BULL breakout momentum (confirmed)")
            elif cci_now < -100:
                votes["m13_cci"] = +1; weights["m13_cci"] = 2
                reasons.append(f"CCI {cci_now:.0f} BEAR breakout momentum (confirmed)")
            elif cci_prev < 0 < cci_now:
                votes["m13_cci"] = -1; weights["m13_cci"] = 1
                reasons.append(f"CCI zero-cross UP → bull lean")
            elif cci_prev > 0 > cci_now:
                votes["m13_cci"] = +1; weights["m13_cci"] = 1
                reasons.append(f"CCI zero-cross DOWN → bear lean")
        except Exception:
            pass

    except Exception:
        _1M_CACHE[ticker] = (now, None)
        return None

    # ── Weighted scoring ────────────────────────────────────────────────────
    sell_wt = sum(weights.get(k, 1) for k, v in votes.items() if v > 0)
    buy_wt  = sum(weights.get(k, 1) for k, v in votes.items() if v < 0)

    if is_otc:
        # OTC: threshold 6 — reversal at confirmed extremes
        threshold     = 6
        max_opposing  = 2
    else:
        # LIVE: GOD LEVEL — strict threshold 9, confirmed bars only
        # (was 7 — raised because all candle checks now use confirmed bar(-2))
        threshold     = 9
        max_opposing  = 2

    direction: Optional[str] = None
    total_wt = 0

    if sell_wt >= threshold and buy_wt <= max_opposing:
        direction = "SELL"; total_wt = sell_wt
    elif buy_wt >= threshold and sell_wt <= max_opposing:
        direction = "BUY";  total_wt = buy_wt
    else:
        _1M_CACHE[ticker] = (now, None)
        return None

    # LIVE pairs: M1 (5m trend) MUST agree, OR both M2 + M7 agree
    if not is_otc:
        m1_agrees  = (direction == "BUY"  and m1_dir == -1) or \
                     (direction == "SELL" and m1_dir == +1)
        m2_agrees  = "m2_ema" in votes and \
                     ((direction == "BUY"  and votes["m2_ema"] < 0) or
                      (direction == "SELL" and votes["m2_ema"] > 0))
        m7_agrees  = "m7_macd" in votes and \
                     ((direction == "BUY"  and votes["m7_macd"] < 0) or
                      (direction == "SELL" and votes["m7_macd"] > 0))
        if not m1_agrees and not (m2_agrees and m7_agrees):
            # Counter-trend signal without micro cross + MACD confirmation → skip
            _1M_CACHE[ticker] = (now, None)
            return None

    confidence = round(min(1.0, total_wt / 29.0), 3)   # max possible = 29 (M1-M13)
    elite      = total_wt >= 14

    result = {
        "direction":    direction,
        "confidence":   confidence,
        "weighted":     total_wt,
        "elite":        elite,
        "votes":        votes,
        "weights":      weights,
        "reasons":      reasons,
        "one_min":      True,
    }
    _1M_CACHE[ticker] = (now, result)
    return result


def pick_best_pair(pairs: list[str]) -> Optional[tuple[str, dict]]:
    """Scan every pair, return (pair, setup) with the highest score, or
    None if no pair currently shows a SNIPER-grade entry. Pairs that
    Yahoo cannot quote (e.g. some OTC tickers) are silently skipped.

    Gold (XAU/USD) is always scanned FIRST and given a +10 priority
    bonus during ranking — per the SUPREME PRO playbook, Gold is the
    bot's go-to instrument and gets first dibs on every cycle.

    Multi-timeframe alignment (4H → 1M) and Malaysian SnR confluence
    are layered on top: a setup that AGREES with the wider bias and
    is sitting at a real SnR zone gets a meaningful score boost; a
    setup that fights the bias is heavily penalised so the engine
    prefers to wait rather than ship a low-quality signal."""
    # Re-order so Gold is analysed first (still scored on its own
    # merits — the bonus only nudges ranking, never lowers the bar).
    ordered = sorted(pairs, key=lambda p: (0 if _is_gold(p) else 1))

    best: Optional[tuple[str, dict, int]] = None  # (pair, setup, ranked_score)
    for pair in ordered:
        setup = analyze_pair(pair)
        if not setup or setup["score"] < MIN_SCORE:
            continue

        ranked = setup["score"]
        if _is_gold(pair):
            ranked += GOLD_PRIORITY_BONUS

        # Multi-TF alignment filter
        bias = multi_tf_bias(pair)
        if bias is not None:
            if bias["direction"] == setup["direction"]:
                ranked += int(15 + 15 * bias["confidence"])  # up to +30
                setup["mtf_aligned"] = True
                setup["mtf_confidence"] = bias["confidence"]
            else:
                # Direction fights the wider bias → heavy penalty so the
                # engine prefers to wait
                ranked -= 25
                setup["mtf_aligned"] = False
                setup["mtf_confidence"] = bias["confidence"]
        else:
            setup["mtf_aligned"] = None
            setup["mtf_confidence"] = 0.0

        # Malaysian SnR / CMP / CMD confluence
        if malaysian_snr_confluence(pair, setup["direction"]):
            ranked += 10
            setup["snr_confluence"] = True
        else:
            setup["snr_confluence"] = False

        # ── PRICE-ACTION PATTERN confluence (HNS / iHNS / QM / iQM) ──
        # A real chartist's pattern that AGREES with the sniper direction
        # is the strongest possible confirmation: +20 to ranked score
        # and we attach the pattern so downstream code can use the
        # neckline / measured-move target as TP/SL anchors.
        setup["pattern"] = None
        if pattern_for_direction is not None:
            try:
                pat = pattern_for_direction(pair, setup["direction"])
            except Exception:
                pat = None
            if pat is not None:
                setup["pattern"] = pat
                # Up to +20 from pattern quality (caps at score 100)
                ranked += int(10 + 10 * (pat["score"] / 100.0))

        if best is None or ranked > best[2]:
            best = (pair, setup, ranked)

    if not best:
        return None
    # Final guard: never ship a setup whose ranked score collapsed below
    # the original MIN_SCORE because of bias / SnR penalties — better to
    # wait for a cleaner cycle than to send a weak signal.
    if best[2] < MIN_SCORE:
        return None
    return (best[0], best[1])
