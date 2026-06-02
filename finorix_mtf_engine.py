"""FINORIX AI MTF Channel Engine  — V3 Supreme
=================================================
Implements the analysis method shown in the FINORIX AI chart screenshot:

  ┌─────────────────────────────────────────────────────┐
  │  EUR/USD  |  TF: M1  |  UP ▲                       │
  │  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  ← upper channel (yellow) │
  │  ━━━━━━━━━━━━━━━━━━━━━━━  ← regression midline     │
  │  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  ← lower channel (yellow) │
  │  ━━━━━━━━━━━━━━━━━━━━━━━  ← resistance (red)        │
  │  ━━━━━━━━━━━━━━━━━━━━━━━  ← support (green)         │
  └─────────────────────────────────────────────────────┘

Three components
─────────────────
A. Linear Regression Channel  — slope tells trend direction
B. Dynamic S/R Levels         — swing-pivot highs/lows as horizontal levels
C. MTF Consensus              — M1 · M5 · M15 · H1 vote → UP / DOWN / RANGING

Works for ALL pair classes
──────────────────────────
  • Live Forex (21 pairs)
  • OTC Forex, Crypto, Commodities, Stocks, Indices
  OTC trend uses yfinance data for the underlying market (OTC synthetic
  prices track the real underlying — trend direction is identical).

Contract: zero side-effects. Never writes to signal text, keyboard, or DB.

Public API
──────────
  finorix_mtf_analyse(pair, market_type="OTC") → dict
  finorix_trend_label(pair) → str  e.g. "UP ▲"
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

_log = logging.getLogger(__name__)

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    yf = None
    pd = None
    _YF_OK = False

# ── Ticker map: all supported pairs → Yahoo Finance symbol ────────────────────
_TICKER_MAP: dict[str, str] = {
    # ── Forex ──────────────────────────────────────────────────────────────
    "EUR/USD": "EURUSD=X",  "GBP/USD": "GBPUSD=X",  "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X",  "USD/CAD": "USDCAD=X",  "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",  "EUR/JPY": "EURJPY=X",  "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",  "USD/CHF": "USDCHF=X",  "EUR/CHF": "EURCHF=X",
    "EUR/AUD": "EURAUD=X",  "GBP/AUD": "GBPAUD=X",  "EUR/CAD": "EURCAD=X",
    "GBP/CAD": "GBPCAD=X",  "AUD/CAD": "AUDCAD=X",  "AUD/NZD": "AUDNZD=X",
    "NZD/JPY": "NZDJPY=X",  "GBP/CHF": "GBPCHF=X",  "CAD/JPY": "CADJPY=X",
    # Minor crosses
    "AUD/CHF": "AUDCHF=X",  "CAD/CHF": "CADCHF=X",  "CHF/JPY": "CHFJPY=X",
    "EUR/NZD": "EURNZD=X",  "GBP/NZD": "GBPNZD=X",  "NZD/CAD": "NZDCAD=X",
    "NZD/CHF": "NZDCHF=X",
    # Exotics
    "USD/MXN": "USDMXN=X",  "USD/INR": "USDINR=X",  "USD/BRL": "USDBRL=X",
    "USD/ZAR": "USDZAR=X",
    # ── Metals ─────────────────────────────────────────────────────────────
    "XAU/USD": "PAXG-USD",  "GOLD": "PAXG-USD",  "XAUUSD": "PAXG-USD",
    "XAG/USD": "SI=F",      "SILVER": "SI=F",
    # ── Energy ─────────────────────────────────────────────────────────────
    "USOIL": "CL=F",        "BRENT": "BZ=F",
    # ── Crypto ─────────────────────────────────────────────────────────────
    "BTC/USD": "BTC-USD",   "BTCUSD": "BTC-USD",   "BITCOIN": "BTC-USD",
    "ETH/USD": "ETH-USD",   "ETHUSD": "ETH-USD",   "ETHEREUM": "ETH-USD",
    "BNB/USD": "BNB-USD",   "BNBUSD": "BNB-USD",
    "SOL/USD": "SOL-USD",   "SOLUSD": "SOL-USD",   "SOLANA": "SOL-USD",
    "XRP/USD": "XRP-USD",   "XRPUSD": "XRP-USD",   "RIPPLE": "XRP-USD",
    "ADA/USD": "ADA-USD",   "ADAUSD": "ADA-USD",
    "AVAX/USD": "AVAX-USD", "AVAXUSD": "AVAX-USD",
    "LTC/USD": "LTC-USD",   "LTCUSD": "LTC-USD",
    "DOT/USD": "DOT-USD",   "DOTUSD": "DOT-USD",
    "LINK/USD": "LINK-USD", "LINKUSD": "LINK-USD",
    "BCH/USD": "BCH-USD",   "BCHUSD": "BCH-USD",
    "DASH/USD": "DASH-USD", "DASHUSD": "DASH-USD",
    "ETC/USD": "ETC-USD",   "ETCUSD": "ETC-USD",
    "TON/USD": "TON-USD",   "TONUSD": "TON-USD",
    "MATIC/USD": "MATIC-USD", "MATICUSD": "MATIC-USD",
    # ── Indices ─────────────────────────────────────────────────────────────
    "NAS100": "^NDX",   "NASDAQ": "^NDX",   "US100": "^NDX",
    "DJ30":   "^DJI",   "DOW":    "^DJI",   "DJI":   "^DJI",
    "SP500":  "^GSPC",  "SPX500": "^GSPC",
    "FTSE":   "^FTSE",  "DAX":    "^GDAXI",
    "NQ":     "^NDX",   "SP":     "^GSPC",
    # ── Stocks ──────────────────────────────────────────────────────────────
    "AAPL": "AAPL",  "APPLE":     "AAPL",
    "TSLA": "TSLA",  "TESLA":     "TSLA",
    "AMZN": "AMZN",  "AMAZON":    "AMZN",
    "GOOGL": "GOOGL", "GOOGLE":   "GOOGL",
    "MSFT": "MSFT",  "MICROSOFT": "MSFT",
    "META": "META",  "FACEBOOK":  "META",
    "NFLX": "NFLX",  "NETFLIX":   "NFLX",
    "NVDA": "NVDA",  "NVIDIA":    "NVDA",
    "BABA": "BABA",
    "JNJ":  "JNJ",
    "PFE":  "PFE",   "PFIZER":    "PFE",
    "BA":   "BA",    "BOEING":    "BA",
    "MCD":  "MCD",   "MCDONALDS": "MCD",
    "INTC": "INTC",  "INTEL":     "INTC",
    "V":    "V",     "VISA":      "V",
    "MA":   "MA",    "MASTERCARD": "MA",
    "DIS":  "DIS",   "DISNEY":    "DIS",
    "IBM":  "IBM",
    "CSCO": "CSCO",  "CISCO":     "CSCO",
    "AMEX": "AXP",
}

# ── Add OTC label variants (strip _otc suffix, add to map) ───────────────────
_OTC_REMAP: dict[str, str] = {}
for _k, _v in list(_TICKER_MAP.items()):
    _OTC_REMAP[_k.replace("/", "").upper() + "_OTC"] = _v
    _OTC_REMAP[_k.replace("/", "").upper() + "OTC"]  = _v
_TICKER_MAP.update(_OTC_REMAP)

# ── Candle cache: (timestamp, candles_list) ───────────────────────────────────
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 45.0   # seconds — fresh enough for 1m decisions


def _resolve_ticker(pair: str) -> str:
    """Convert bot pair label → Yahoo Finance ticker."""
    clean = pair.upper().strip().replace(" ", "").replace("〔OTC〕", "")
    # Direct lookup
    t = _TICKER_MAP.get(clean)
    if t:
        return t
    # Strip _OTC suffix and retry
    if clean.endswith("_OTC"):
        t = _TICKER_MAP.get(clean[:-4])
        if t:
            return t
    # Fallback: forex pattern AAABBB → AAA/BBB=X
    if len(clean) == 6 and clean.isalpha():
        return clean + "=X"
    return clean


def _fetch_candles(pair: str, tf: str, count: int = 120) -> list[dict]:
    """Download OHLCV from yfinance with caching. Returns [] on failure."""
    if not _YF_OK:
        return []
    cache_key = f"{pair}|{tf}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    ticker = _resolve_ticker(pair)
    try:
        period = "1d" if tf in ("1m", "2m") else ("2d" if tf == "5m" else "5d")
        raw = yf.download(ticker, period=period, interval=tf,
                          progress=False, auto_adjust=True, timeout=10)
        if raw is None or len(raw) == 0:
            _CACHE[cache_key] = (now, [])
            return []
        if hasattr(raw.columns, "get_level_values"):
            raw.columns = [
                str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                for c in raw.columns
            ]
        else:
            raw.columns = [str(c).lower() for c in raw.columns]
        raw = raw.tail(count)
        candles: list[dict] = []
        for _, row in raw.iterrows():
            try:
                o = float(row.get("open",  row.get("Open",  0)))
                h = float(row.get("high",  row.get("High",  0)))
                l = float(row.get("low",   row.get("Low",   0)))
                c = float(row.get("close", row.get("Close", 0)))
                v = float(row.get("volume", row.get("Volume", 1)) or 1)
                if c > 0:
                    candles.append({"open": o, "high": h, "low": l,
                                    "close": c, "volume": v})
            except Exception:
                continue
        _CACHE[cache_key] = (now, candles)
        return candles
    except Exception as exc:
        _log.debug(f"[finorix_mtf] fetch {pair} {tf}: {exc}")
        _CACHE[cache_key] = (now, [])
        return []


# ─────────────────────────────────────────────────────────────────────────────
# A.  LINEAR REGRESSION CHANNEL
# ─────────────────────────────────────────────────────────────────────────────

def _regression_channel(candles: list[dict], n: int = 50) -> dict:
    """
    Fit a linear regression line through the last N closing prices.
    Returns:
      slope        : regression slope (positive = uptrend, negative = downtrend)
      upper_band   : regression line + 1.5 × std dev  (upper yellow dash)
      lower_band   : regression line − 1.5 × std dev  (lower yellow dash)
      mid_value    : regression line at last bar
      price_pos    : 0-1  (0=at lower band, 0.5=midline, 1=upper band)
      trend        : "UP" | "DOWN" | "RANGING"
      slope_pct    : slope as % of price per bar (normalised)
    """
    closes = [c["close"] for c in candles[-n:]]
    m = len(closes)
    if m < 10:
        return {"slope": 0, "upper_band": 0, "lower_band": 0,
                "mid_value": closes[-1] if closes else 0,
                "price_pos": 0.5, "trend": "RANGING", "slope_pct": 0.0}

    x_mean = (m - 1) / 2.0
    y_mean = sum(closes) / m
    ss_xy  = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(closes))
    ss_xx  = sum((i - x_mean) ** 2 for i in range(m))
    slope  = ss_xy / ss_xx if ss_xx else 0.0
    intercept = y_mean - slope * x_mean

    # Residuals → standard deviation
    fitted = [intercept + slope * i for i in range(m)]
    residuals = [closes[i] - fitted[i] for i in range(m)]
    std = math.sqrt(sum(r * r for r in residuals) / m) or 1e-9

    mid_at_last = fitted[-1]
    upper = mid_at_last + 1.5 * std
    lower = mid_at_last - 1.5 * std
    cur   = closes[-1]
    band_range = upper - lower
    price_pos  = (cur - lower) / band_range if band_range > 0 else 0.5

    # Normalise slope as % of price per bar
    slope_pct = (slope / (y_mean or 1)) * 100

    # Trend classification — slope must be meaningful relative to noise
    noise_threshold = std / (y_mean or 1) * 0.3
    if slope_pct > noise_threshold:
        trend = "UP"
    elif slope_pct < -noise_threshold:
        trend = "DOWN"
    else:
        trend = "RANGING"

    return {
        "slope":      slope,
        "slope_pct":  round(slope_pct * 1000, 4),   # × 1000 for readability
        "upper_band": round(upper, 6),
        "lower_band": round(lower, 6),
        "mid_value":  round(mid_at_last, 6),
        "std_dev":    round(std, 6),
        "price_pos":  round(price_pos, 3),           # 0=bottom, 1=top of channel
        "trend":      trend,
    }


# ─────────────────────────────────────────────────────────────────────────────
# B.  DYNAMIC S/R LEVELS   (swing pivot high = resistance / low = support)
# ─────────────────────────────────────────────────────────────────────────────

def _find_sr_levels(candles: list[dict], lookback: int = 40) -> dict:
    """
    Identify the most significant horizontal S/R levels using pivot-swing
    detection — same concept as the red (resistance) and green (support)
    lines in the FINORIX AI chart.

    Returns:
      resistance     : nearest swing high above current price
      support        : nearest swing low below current price
      resistance2    : second resistance level
      support2       : second support level
      near_resistance: True if price within 0.15% of resistance
      near_support   : True if price within 0.15% of support
      bias           : "SELL" | "BUY" | "NEUTRAL" (based on proximity)
    """
    recent = candles[-lookback:]
    highs  = [c["high"]  for c in recent]
    lows   = [c["low"]   for c in recent]
    closes = [c["close"] for c in recent]
    cur    = closes[-1]

    # Swing pivot: local max/min over ±3 bars
    pivot_highs: list[float] = []
    pivot_lows:  list[float] = []
    for i in range(3, len(highs) - 3):
        if highs[i] == max(highs[i-3:i+4]):
            pivot_highs.append(highs[i])
        if lows[i] == min(lows[i-3:i+4]):
            pivot_lows.append(lows[i])

    # Merge levels that are within 0.2% of each other (cluster → single level)
    def _cluster(vals: list[float], tol: float = 0.002) -> list[float]:
        sorted_v = sorted(vals)
        clusters: list[float] = []
        for v in sorted_v:
            if clusters and abs(v - clusters[-1]) / (clusters[-1] or 1) < tol:
                clusters[-1] = (clusters[-1] + v) / 2   # merge → midpoint
            else:
                clusters.append(v)
        return clusters

    c_highs = _cluster(pivot_highs) if pivot_highs else [max(highs)]
    c_lows  = _cluster(pivot_lows)  if pivot_lows  else [min(lows)]

    # Nearest resistance above price
    resistances = sorted([h for h in c_highs if h > cur])
    supports    = sorted([l for l in c_lows  if l < cur], reverse=True)

    r1 = resistances[0] if resistances else max(highs)
    r2 = resistances[1] if len(resistances) > 1 else r1
    s1 = supports[0]    if supports    else min(lows)
    s2 = supports[1]    if len(supports)  > 1 else s1

    dist_r = (r1 - cur) / (cur or 1) * 100
    dist_s = (cur - s1) / (cur or 1) * 100
    near_r = dist_r < 0.15
    near_s = dist_s < 0.15

    if near_r and not near_s:
        bias = "SELL"
    elif near_s and not near_r:
        bias = "BUY"
    elif dist_s < dist_r * 0.3:
        bias = "BUY"    # closer to support → bounce expected
    elif dist_r < dist_s * 0.3:
        bias = "SELL"   # closer to resistance → rejection expected
    else:
        bias = "NEUTRAL"

    return {
        "resistance":      round(r1, 6),
        "resistance2":     round(r2, 6),
        "support":         round(s1, 6),
        "support2":        round(s2, 6),
        "dist_to_r_pct":   round(dist_r, 4),
        "dist_to_s_pct":   round(dist_s, 4),
        "near_resistance": near_r,
        "near_support":    near_s,
        "bias":            bias,
    }


# ─────────────────────────────────────────────────────────────────────────────
# C.  SINGLE-TIMEFRAME TREND ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-period + i] - closes[-period + i - 1]
        gains.append(d if d > 0 else 0.0)
        losses.append(-d if d < 0 else 0.0)
    ag = sum(gains) / period
    al = sum(losses) / period
    return round(100 - 100 / (1 + ag / al), 1) if al else 100.0


def _ema(closes: list[float], period: int) -> float:
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    val = closes[0]
    for c in closes[1:]:
        val = c * k + val * (1 - k)
    return val


def _analyse_tf(candles: list[dict]) -> dict:
    """
    Single-timeframe directional analysis combining:
      • Linear regression channel slope
      • RSI(14) zone
      • Price vs EMA(50)
      • S/R proximity
      • Candle structure (last 3 bars)
    Returns: direction "BUY"|"SELL"|"WAIT", strength 0-100, trend "UP"|"DOWN"|"RANGING"
    """
    if len(candles) < 20:
        return {"direction": "WAIT", "strength": 0, "trend": "RANGING",
                "rsi": 50.0, "channel": {}, "sr": {}}

    closes = [c["close"] for c in candles]
    cur    = closes[-1]

    # Regression channel
    ch = _regression_channel(candles, n=min(50, len(candles)))

    # RSI
    rsi_val = _rsi(closes)

    # EMA(50) — trend filter
    ema50 = _ema(closes, 50) if len(closes) >= 50 else _ema(closes, 20)
    price_above_ema = cur > ema50

    # S/R levels
    sr = _find_sr_levels(candles)

    # Recent candle structure (last 3 confirmed bars)
    n = len(candles)
    bull_bars = sum(1 for c in candles[-4:-1] if c["close"] > c["open"])
    bear_bars = sum(1 for c in candles[-4:-1] if c["close"] < c["open"])

    # ── Weighted scoring ─────────────────────────────────────────────────────
    score = 0.0  # positive = BUY, negative = SELL

    # 1. Regression channel slope (strongest signal)
    if ch["trend"] == "UP":
        score += 3.0
        if ch["slope_pct"] > 0.5:
            score += 1.0   # strong slope
    elif ch["trend"] == "DOWN":
        score -= 3.0
        if ch["slope_pct"] < -0.5:
            score -= 1.0

    # 2. Channel position — overbought / oversold within channel
    pp = ch["price_pos"]
    if ch["trend"] == "DOWN" and pp <= 0.2:    # near lower band of DOWN channel = oversold → reversal
        score += 1.5
    elif ch["trend"] == "UP" and pp >= 0.8:   # near upper band of UP channel = overbought → reversal
        score -= 1.5
    elif ch["trend"] == "UP" and pp <= 0.4:   # pullback to mid in uptrend = entry
        score += 1.0
    elif ch["trend"] == "DOWN" and pp >= 0.6: # pullback to mid in downtrend = entry
        score -= 1.0

    # 3. RSI zone
    if rsi_val < 30:
        score += 2.5
    elif rsi_val < 42:
        score += 1.2
    elif rsi_val > 70:
        score -= 2.5
    elif rsi_val > 58:
        score -= 1.2

    # 4. Price vs EMA50
    if price_above_ema:
        score += 1.5
    else:
        score -= 1.5

    # 5. S/R proximity
    if sr["bias"] == "BUY":
        score += 1.5
    elif sr["bias"] == "SELL":
        score -= 1.5

    # 6. Candle momentum
    score += (bull_bars - bear_bars) * 0.5

    # ── Normalise to direction + strength ────────────────────────────────────
    max_score = 11.5
    norm = score / max_score   # -1 to +1

    if norm > 0.20:
        direction = "BUY"
    elif norm < -0.20:
        direction = "SELL"
    else:
        direction = "WAIT"

    strength = round(min(abs(norm) * 100, 99.0), 1)

    return {
        "direction":  direction,
        "strength":   strength,
        "trend":      ch["trend"],
        "rsi":        rsi_val,
        "ema50":      round(ema50, 6),
        "above_ema":  price_above_ema,
        "channel":    ch,
        "sr":         sr,
        "score":      round(score, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# D.  MTF CONSENSUS  (M1 · M5 · M15 · H1)
# ─────────────────────────────────────────────────────────────────────────────

_TF_CONFIG: dict[str, tuple[str, int]] = {
    # tf_label → (yfinance interval, candle count)
    "m1":  ("1m",  100),
    "m5":  ("5m",  100),
    "m15": ("15m", 80),
    "h1":  ("60m", 60),
}

_TF_WEIGHTS: dict[str, float] = {
    "m1":  1.0,
    "m5":  2.0,    # 5m gets most weight for binary signals
    "m15": 2.5,
    "h1":  3.0,    # hourly is the structural anchor
}


def _mtf_consensus(pair: str) -> dict:
    """
    Analyse pair across M1, M5, M15, H1.
    Returns weighted vote → overall direction + per-TF breakdown.
    """
    results: dict[str, dict] = {}
    buy_w = 0.0
    sell_w = 0.0
    wait_w = 0.0

    for tf_label, (interval, count) in _TF_CONFIG.items():
        candles = _fetch_candles(pair, interval, count)
        if len(candles) < 15:
            results[tf_label] = {"direction": "WAIT", "strength": 0,
                                 "trend": "RANGING", "rsi": 50.0,
                                 "channel": {}, "sr": {}}
            wait_w += _TF_WEIGHTS[tf_label]
            continue

        res = _analyse_tf(candles)
        results[tf_label] = res
        w = _TF_WEIGHTS[tf_label]
        if res["direction"] == "BUY":
            buy_w += w
        elif res["direction"] == "SELL":
            sell_w += w
        else:
            wait_w += w

    total_w = buy_w + sell_w + wait_w or 1.0
    buy_pct  = buy_w  / total_w * 100
    sell_pct = sell_w / total_w * 100

    if buy_pct >= 45 and buy_pct > sell_pct + 15:
        consensus = "BUY"
    elif sell_pct >= 45 and sell_pct > buy_pct + 15:
        consensus = "SELL"
    else:
        consensus = "WAIT"

    # Confidence = dominant side % of non-wait weight
    non_wait = buy_w + sell_w or 1.0
    conf = round(max(buy_w, sell_w) / non_wait * 100, 1)

    # Trend label (H1 is the structural anchor)
    h1_trend = results.get("h1", {}).get("trend", "RANGING")
    m5_trend = results.get("m5", {}).get("trend", "RANGING")

    # Majority trend across TFs
    trend_votes = [r.get("trend", "RANGING") for r in results.values()]
    up_count   = trend_votes.count("UP")
    down_count = trend_votes.count("DOWN")
    if up_count > down_count and up_count >= 2:
        overall_trend = "UP"
    elif down_count > up_count and down_count >= 2:
        overall_trend = "DOWN"
    else:
        overall_trend = "RANGING"

    return {
        "direction":    consensus,
        "confidence":   conf,
        "overall_trend": overall_trend,
        "buy_weight":   round(buy_w, 2),
        "sell_weight":  round(sell_w, 2),
        "h1_trend":     h1_trend,
        "m5_trend":     m5_trend,
        "tf":           results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GRADE MAP
# ─────────────────────────────────────────────────────────────────────────────

_GRADES = [(95, "GOD"), (88, "ULTRA"), (80, "ELITE"),
           (70, "STRONG"), (60, "MODERATE"), (0, "WEAK")]


def _grade(conf: float) -> str:
    for threshold, label in _GRADES:
        if conf >= threshold:
            return label
    return "WEAK"


# ─────────────────────────────────────────────────────────────────────────────
# MARKET-TYPE THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

_MARKET_MIN_CONF: dict[str, float] = {
    "OTC":    62.0,
    "PO OTC": 65.0,
    "QX OTC": 65.0,
    "LIVE":   58.0,
    "FOREX":  55.0,
    "FUNDED": 72.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

_RESULT_CACHE: dict[str, tuple[float, dict]] = {}
_RESULT_TTL = 50.0   # seconds


def finorix_mtf_analyse(pair: str, market_type: str = "OTC") -> dict:
    """
    Full FINORIX AI MTF Channel analysis.

    Parameters
    ----------
    pair        : e.g. "EUR/USD", "XAU/USD", "BTCUSD_otc", "AAPL_otc"
    market_type : "OTC"|"PO OTC"|"QX OTC"|"LIVE"|"FOREX"|"FUNDED"

    Returns
    -------
    {
      "ok":           bool     — passed threshold for market type
      "direction":    str      — "BUY" | "SELL" | "WAIT"
      "trend_label":  str      — "UP ▲" | "DOWN ▼" | "RANGING ↔"
      "confidence":   float    — 0-100
      "grade":        str      — "GOD"|"ULTRA"|"ELITE"|"STRONG"|"MODERATE"|"WEAK"
      "mtf":          dict     — per-timeframe breakdown
      "channel":      dict     — regression channel on M5
      "sr":           dict     — S/R levels on M5
      "m5_rsi":       float
      "raw_score":    float
    }
    """
    now = time.time()
    cache_key = f"{pair}|{market_type}"
    cached = _RESULT_CACHE.get(cache_key)
    if cached and now - cached[0] < _RESULT_TTL:
        return cached[1]

    try:
        mtf = _mtf_consensus(pair)

        # M5 channel + SR for the detailed view
        m5_data    = mtf["tf"].get("m5", {})
        channel    = m5_data.get("channel", {})
        sr         = m5_data.get("sr", {})
        m5_rsi     = m5_data.get("rsi", 50.0)
        m5_trend   = m5_data.get("trend", "RANGING")

        direction = mtf["direction"]
        conf      = mtf["confidence"]
        trend     = mtf["overall_trend"]

        # SR bias adds/removes confidence
        sr_bias = sr.get("bias", "NEUTRAL")
        if sr_bias == direction.replace("WAIT", "NEUTRAL"):
            conf = min(conf + 5, 99)
        elif sr_bias not in ("NEUTRAL", "WAIT") and sr_bias != direction:
            conf = max(conf - 5, 0)

        min_conf = _MARKET_MIN_CONF.get(market_type.upper(),
                    _MARKET_MIN_CONF.get(market_type.split()[0].upper(), 62.0))
        ok = direction != "WAIT" and conf >= min_conf

        # Trend label
        if trend == "UP":
            trend_label = "UP ▲"
        elif trend == "DOWN":
            trend_label = "DOWN ▼"
        else:
            trend_label = "RANGING ↔"

        result = {
            "ok":          ok,
            "direction":   direction,
            "trend_label": trend_label,
            "confidence":  round(conf, 1),
            "grade":       _grade(conf),
            "mtf":         mtf["tf"],
            "channel":     channel,
            "sr":          sr,
            "m5_rsi":      m5_rsi,
            "m5_trend":    m5_trend,
            "raw_score":   round(mtf.get("buy_weight", 0) - mtf.get("sell_weight", 0), 3),
        }
        _RESULT_CACHE[cache_key] = (now, result)
        return result

    except Exception as exc:
        _log.debug(f"[finorix_mtf] analyse error {pair}: {exc}")
        fallback = {
            "ok": False, "direction": "WAIT", "trend_label": "RANGING ↔",
            "confidence": 50.0, "grade": "WEAK", "mtf": {}, "channel": {},
            "sr": {}, "m5_rsi": 50.0, "m5_trend": "RANGING", "raw_score": 0.0,
        }
        return fallback


def finorix_trend_label(pair: str) -> str:
    """
    Fast trend label for a pair: "UP ▲" | "DOWN ▼" | "RANGING ↔"
    Uses only the H1 + M5 channels for speed (≤ 1 API call).
    """
    try:
        # H1 is the structural anchor
        h1_candles = _fetch_candles(pair, "60m", 60)
        if len(h1_candles) >= 20:
            h1_ch = _regression_channel(h1_candles, n=40)
            if h1_ch["trend"] != "RANGING":
                return "UP ▲" if h1_ch["trend"] == "UP" else "DOWN ▼"
        # Fall back to M5
        m5_candles = _fetch_candles(pair, "5m", 80)
        if len(m5_candles) >= 20:
            m5_ch = _regression_channel(m5_candles, n=50)
            return "UP ▲" if m5_ch["trend"] == "UP" else (
                   "DOWN ▼" if m5_ch["trend"] == "DOWN" else "RANGING ↔")
    except Exception:
        pass
    return "RANGING ↔"
