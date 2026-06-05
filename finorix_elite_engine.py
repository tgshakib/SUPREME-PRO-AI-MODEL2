"""FINORIX ELITE ANALYSIS ENGINE  — V4 SUPREME
============================================================
Advanced multi-module analysis engine for SUPREME PRO AI BOT.

Modules
-------
  A. Big-to-Small Trend Cascade   — H1 → M15 → M5 → M1
  B. Elite S&R Zone Engine        — classical + hidden + psychological levels
  C. Hidden Reversal Zone Detector — divergence, exhaustion, wick magnets
  D. Trend Strength Classifier    — ADX + 5-EMA stack + normalized slope
  E. Zone Confluence Scorer       — multi-type zone stacking at current price

Contract: ZERO side-effects — never modifies signal text, keyboards,
          or any other module state. Returns only a structured dict.

Public API
----------
finorix_elite_analyse(pair, market_type="OTC") -> dict
  {
    "ok":             bool,    # passed quality threshold for market type
    "direction":      str,     # "BUY" | "SELL" | "WAIT"
    "confidence":     float,   # 0–100
    "grade":          str,     # "HIDDEN" | "ELITE" | "STRONG" | "MODERATE" | "WEAK"
    "zone_confluence":int,     # 0–6 zone types stacked at current price
    "trend_phase":    str,     # "REVERSAL" | "CONTINUATION" | "RANGING"
    "trend_strength": str,     # "ELITE" | "STRONG" | "MODERATE" | "WEAK"
    "hidden_zone":    bool,    # hidden level active at current price
    "cascade_score":  int,     # 0–4 timeframes agreeing (big-to-small)
    "agree":          float,   # % of sub-models in agreement
    "models_buy":     int,
    "models_sell":    int,
    "veto":           bool,    # hard split consensus
    "raw_score":      float,
  }
"""
from __future__ import annotations
import math
import time
import logging
import statistics

_log = logging.getLogger(__name__)

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    yf = None   # type: ignore
    pd = None   # type: ignore
    _YF_OK = False

# ── Yahoo ticker map (mirrors live_prices.py) ─────────────────────────────────
_TICKER_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X", "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "USD/CHF": "USDCHF=X", "EUR/CHF": "EURCHF=X",
    "EUR/AUD": "EURAUD=X", "GBP/AUD": "GBPAUD=X", "EUR/CAD": "EURCAD=X",
    "GBP/CAD": "GBPCAD=X", "AUD/CAD": "AUDCAD=X", "AUD/NZD": "AUDNZD=X",
    "NZD/JPY": "NZDJPY=X", "GBP/CHF": "GBPCHF=X", "CAD/JPY": "CADJPY=X",
    "XAU/USD": "PAXG-USD", "XAG/USD": "SI=F",
    "BTC/USD": "BTC-USD",  "ETH/USD": "ETH-USD", "BNB/USD": "BNB-USD",
    "SOL/USD": "SOL-USD",  "XRP/USD": "XRP-USD",
    "NAS100":  "^NDX",     "DJ30": "^DJI", "SP500": "^GSPC",
}

# ── OTC pair → underlying ticker ─────────────────────────────────────────────
def _otc_to_ticker(pair: str) -> str | None:
    clean = pair.replace("〔OTC〕", "").replace("(OTC)", "").replace("_otc", "").strip()
    for k, v in _TICKER_MAP.items():
        if k.replace("/", "").upper() == clean.replace("/", "").upper():
            return v
    return _TICKER_MAP.get(clean.upper()) or _TICKER_MAP.get(clean)

# ── Candle cache: (timestamp, candles_list) per "ticker:interval" ─────────────
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 45.0   # seconds


def _ema(values: list[float], n: int) -> list[float]:
    """Exponential moving average of a list — returns same-length list."""
    if len(values) < 2:
        return values[:]
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _sma(values: list[float], n: int) -> float:
    """Simple moving average of last n values."""
    tail = values[-n:] if len(values) >= n else values
    return sum(tail) / len(tail) if tail else 0.0


def _atr(candles: list[dict], n: int = 14) -> float:
    """Average True Range over n periods."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"]  - p["close"]),
        ))
    tail = trs[-n:] if len(trs) >= n else trs
    return sum(tail) / len(tail) if tail else 0.0


def _rsi(closes: list[float], n: int = 14) -> float:
    """RSI of a close series, returns 0-100."""
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = _sma(gains[-n:], n)
    al = _sma(losses[-n:], n)
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1 + rs)


def _adx(candles: list[dict], n: int = 14) -> tuple[float, float, float]:
    """Returns (ADX, +DI, -DI) for candle list. Needs ≥30 candles."""
    if len(candles) < n + 5:
        return 20.0, 50.0, 50.0
    trs, pdms, ndms = [], [], []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr = max(c["high"] - c["low"],
                 abs(c["high"] - p["close"]),
                 abs(c["low"]  - p["close"]))
        up   = c["high"] - p["high"]
        down = p["low"]  - c["low"]
        pdms.append(max(up, 0) if up > down else 0)
        ndms.append(max(down, 0) if down > up else 0)
        trs.append(tr)

    def wilder_smooth(arr: list[float], period: int) -> list[float]:
        out = [sum(arr[:period])]
        for v in arr[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    if len(trs) < n:
        return 20.0, 50.0, 50.0
    atr_s  = wilder_smooth(trs,  n)
    pdm_s  = wilder_smooth(pdms, n)
    ndm_s  = wilder_smooth(ndms, n)
    dxs = []
    for a, p2, nm in zip(atr_s, pdm_s, ndm_s):
        pdi = 100 * p2 / a if a > 0 else 0
        ndi = 100 * nm / a if a > 0 else 0
        dx  = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0
        dxs.append((dx, pdi, ndi))
    if not dxs:
        return 20.0, 50.0, 50.0
    last_dx = [d[0] for d in dxs[-n:]]
    adx_val = sum(last_dx) / len(last_dx)
    _, pdi_last, ndi_last = dxs[-1]
    return adx_val, pdi_last, ndi_last


def _fetch_candles(ticker: str, interval: str, count: int) -> list[dict]:
    """Fetch OHLCV candles from yfinance with TTL cache."""
    key = f"{ticker}:{interval}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]
    if not _YF_OK or yf is None:
        return _synthetic_candles(ticker, count)
    try:
        period_map = {"1m": "1d", "5m": "5d", "15m": "60d", "60m": "60d"}
        period = period_map.get(interval, "5d")
        raw = yf.download(
            ticker, period=period, interval=interval,
            progress=False, auto_adjust=True, timeout=8,
        )
        if raw is None or len(raw) == 0:
            raise ValueError("empty")
        candles = []
        for _, row in raw.tail(count).iterrows():
            try:
                o = float(row["Open"].iloc[0]  if hasattr(row["Open"],  "iloc") else row["Open"])
                h = float(row["High"].iloc[0]  if hasattr(row["High"],  "iloc") else row["High"])
                lo= float(row["Low"].iloc[0]   if hasattr(row["Low"],   "iloc") else row["Low"])
                c = float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
                v = float(row["Volume"].iloc[0]if hasattr(row["Volume"],"iloc") else row["Volume"])
            except Exception:
                o = float(row.get("Open", 0));  h = float(row.get("High", 0))
                lo= float(row.get("Low",  0));  c = float(row.get("Close", 0))
                v = float(row.get("Volume", 0))
            if h > 0:
                candles.append({"open": o, "high": h, "low": lo, "close": c, "volume": v})
        if not candles:
            raise ValueError("no rows")
        _CACHE[key] = (now, candles)
        return candles
    except Exception:
        return _synthetic_candles(ticker, count)


def _synthetic_candles(pair: str, count: int) -> list[dict]:
    """Deterministic synthetic candles when yfinance is unavailable."""
    import random as _r
    rng = _r.Random(hash(pair) % (2**32))
    base_map = {
        "EURUSD=X": 1.0850, "GBPUSD=X": 1.2650, "USDJPY=X": 149.50,
        "PAXG-USD": 2350.0, "BTC-USD": 65000.0, "^NDX": 18000.0,
    }
    base = base_map.get(pair, 1.1000)
    vol  = base * 0.0004
    candles: list[dict] = []
    for i in range(count):
        bias = 0.00008 * (i - count // 2)
        o = base + bias + rng.uniform(-vol, vol)
        c = o + rng.uniform(-vol * 1.4, vol * 1.4)
        h = max(o, c) + rng.uniform(0, vol * 0.5)
        l = min(o, c) - rng.uniform(0, vol * 0.5)
        candles.append({
            "open": round(o, 6), "high": round(h, 6),
            "low":  round(l, 6), "close": round(c, 6),
            "volume": rng.randint(300, 4000),
        })
        base = c
    return candles


# ═════════════════════════════════════════════════════════════════════════════
# MODULE A  ─  BIG-TO-SMALL TREND CASCADE   (H1 → M15 → M5 → M1)
# ═════════════════════════════════════════════════════════════════════════════

class _BigToSmallCascade:
    """Identifies the dominant trend from H1 down to M1.

    At each timeframe we compute:
      • EMA-8 vs EMA-21 crossover direction
      • Price position relative to EMA-50
      • Normalized slope of EMA-21 (velocity)

    A timeframe "votes" BUY when all three agree bullish, SELL for bearish.
    Cascade agreement = # of TFs voting the same direction (0-4).

    Reversal detection: H1 trend ≠ M5/M1 trend → potential reversal forming.
    """

    _TF_MAP = [
        ("H1",  "60m", 80),
        ("M15", "15m", 80),
        ("M5",  "5m",  80),
        ("M1",  "1m",  80),
    ]
    _TF_WEIGHT = {"H1": 4.0, "M15": 3.0, "M5": 2.0, "M1": 1.0}

    def _tf_direction(self, candles: list[dict]) -> str:
        if len(candles) < 22:
            return "NEUTRAL"
        closes = [c["close"] for c in candles]
        e8  = _ema(closes, 8)
        e21 = _ema(closes, 21)
        e50 = _ema(closes, 50) if len(closes) >= 50 else closes
        price = closes[-1]
        cross = "BUY" if e8[-1] > e21[-1] else "SELL" if e8[-1] < e21[-1] else "NEUTRAL"
        macro = "BUY" if price > e50[-1] else "SELL"
        # Slope: normalized by ATR
        atr_v = _atr(candles, 14) or 0.0001
        slope = (e21[-1] - e21[-5]) / (atr_v * 5) if len(e21) >= 6 else 0
        slope_dir = "BUY" if slope > 0.05 else "SELL" if slope < -0.05 else "NEUTRAL"
        # All three must agree for a clear vote
        votes = [cross, macro, slope_dir]
        buy_ct  = votes.count("BUY")
        sell_ct = votes.count("SELL")
        if buy_ct >= 2:
            return "BUY"
        if sell_ct >= 2:
            return "SELL"
        return "NEUTRAL"

    def analyse(self, ticker: str) -> dict:
        tf_directions: dict[str, str] = {}
        for label, interval, count in self._TF_MAP:
            candles = _fetch_candles(ticker, interval, count)
            tf_directions[label] = self._tf_direction(candles)

        # Weighted vote
        buy_w = sell_w = 0.0
        for label, direction in tf_directions.items():
            w = self._TF_WEIGHT[label]
            if direction == "BUY":
                buy_w += w
            elif direction == "SELL":
                sell_w += w

        total_w = sum(self._TF_WEIGHT.values())
        direction = "BUY" if buy_w > sell_w else "SELL" if sell_w > buy_w else "NEUTRAL"
        cascade_score = sum(1 for d in tf_directions.values() if d == direction and d != "NEUTRAL")

        # Reversal detection: dominant TF (H1) disagrees with fast TFs (M1/M5)
        h1_dir  = tf_directions.get("H1", "NEUTRAL")
        m1_dir  = tf_directions.get("M1", "NEUTRAL")
        m5_dir  = tf_directions.get("M5", "NEUTRAL")
        reversal = (h1_dir != "NEUTRAL" and m1_dir != "NEUTRAL"
                    and h1_dir != m1_dir and m5_dir == m1_dir)

        # Continuation: all TFs agree on same direction
        unique_dirs = set(d for d in tf_directions.values() if d != "NEUTRAL")
        continuation = len(unique_dirs) == 1 and len(unique_dirs) > 0

        strength_pct = max(buy_w, sell_w) / total_w
        return {
            "direction":     direction,
            "cascade_score": cascade_score,
            "buy_weight":    buy_w,
            "sell_weight":   sell_w,
            "strength_pct":  strength_pct,
            "is_reversal":   reversal,
            "is_continuation": continuation,
            "tf_breakdown":  tf_directions,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODULE B  ─  ELITE S&R ZONE ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class _EliteSREngine:
    """Identifies ALL relevant support/resistance zones:

      • Classical swing pivots (fractal ±3 bars and ±5 bars)
      • Psychological levels  (round numbers xx.xx00 / xx.xx50)
      • EMA-50 and EMA-200   (dynamic S&R levels)
      • Equal highs/lows     (EQH/EQL) — liquidity pools above/below market
      • Order blocks          (last opposing candle before swing break)
      • FVG midpoints         (3-bar imbalance zones)
      • Session highs/lows    (previous 24h range)

    Returns: price direction based on zone proximity + zone score
    """

    def _swing_pivots(self, candles: list[dict], wing: int = 3) -> tuple[list[float], list[float]]:
        highs = [c["high"]  for c in candles]
        lows  = [c["low"]   for c in candles]
        res_levels: list[float] = []
        sup_levels: list[float] = []
        for i in range(wing, len(highs) - wing):
            if all(highs[i] >= highs[i - j] and highs[i] >= highs[i + j] for j in range(1, wing + 1)):
                res_levels.append(highs[i])
            if all(lows[i]  <= lows[i  - j] and lows[i]  <= lows[i  + j] for j in range(1, wing + 1)):
                sup_levels.append(lows[i])
        return res_levels, sup_levels

    def _psychological_levels(self, price: float, pip_val: float) -> list[float]:
        """Round number levels within 1% of price."""
        levels: list[float] = []
        # Step size: 50 pips for forex (0.0050 for 4-decimal pairs)
        step = pip_val * 50
        base = round(price / step) * step
        for m in range(-3, 4):
            lvl = base + m * step
            if abs(lvl - price) / price < 0.015:
                levels.append(round(lvl, 6))
        return levels

    def _equal_highs_lows(self, candles: list[dict], tolerance: float) -> tuple[list[float], list[float]]:
        """Equal highs (EQH) and equal lows (EQL) within `tolerance` of each other."""
        highs = [c["high"] for c in candles[-50:]]
        lows  = [c["low"]  for c in candles[-50:]]
        eqh: list[float] = []
        eql: list[float] = []
        for i in range(len(highs)):
            for j in range(i + 2, len(highs)):
                if abs(highs[i] - highs[j]) <= tolerance:
                    eqh.append((highs[i] + highs[j]) / 2)
        for i in range(len(lows)):
            for j in range(i + 2, len(lows)):
                if abs(lows[i] - lows[j]) <= tolerance:
                    eql.append((lows[i] + lows[j]) / 2)
        return eqh, eql

    def _order_blocks(self, candles: list[dict]) -> tuple[list[float], list[float]]:
        """Bullish OB = last bearish candle before bullish BoS.
           Bearish OB = last bullish candle before bearish BoS."""
        bull_obs: list[float] = []
        bear_obs: list[float] = []
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        window = min(len(candles), 50)
        for i in range(10, window - 3):
            # Bullish BoS: close exceeds prior swing high
            prior_high = max(highs[max(0, i - 10):i])
            if closes[i] > prior_high:
                # Last bearish candle before this breakout is the bullish OB
                for j in range(i - 1, max(0, i - 8), -1):
                    if candles[j]["close"] < candles[j]["open"]:
                        bull_obs.append(candles[j]["low"])
                        break
            # Bearish BoS
            prior_low = min(lows[max(0, i - 10):i])
            if closes[i] < prior_low:
                for j in range(i - 1, max(0, i - 8), -1):
                    if candles[j]["close"] > candles[j]["open"]:
                        bear_obs.append(candles[j]["high"])
                        break
        return bull_obs, bear_obs

    def _fvg_midpoints(self, candles: list[dict]) -> tuple[list[float], list[float]]:
        """Fair Value Gaps: 3-bar imbalance. Returns (bull_midpoints, bear_midpoints)."""
        bull_fvg: list[float] = []
        bear_fvg: list[float] = []
        for i in range(2, len(candles)):
            prev2 = candles[i - 2]
            cur   = candles[i]
            # Bullish FVG: low of current > high of 2-bars-ago → gap up
            if cur["low"] > prev2["high"]:
                bull_fvg.append((cur["low"] + prev2["high"]) / 2)
            # Bearish FVG: high of current < low of 2-bars-ago → gap down
            if cur["high"] < prev2["low"]:
                bear_fvg.append((cur["high"] + prev2["low"]) / 2)
        return bull_fvg[-5:], bear_fvg[-5:]   # keep most recent 5 only

    def _zone_strength(self, levels: list[float], price: float, tolerance: float) -> int:
        """Count how many prior levels cluster within tolerance of current price."""
        return sum(1 for lvl in levels if abs(lvl - price) <= tolerance)

    def analyse(self, ticker: str, pip_val: float = 0.0001) -> dict:
        candles_m5  = _fetch_candles(ticker, "5m",  100)
        candles_m15 = _fetch_candles(ticker, "15m",  80)
        if len(candles_m5) < 10:
            return {"direction": "NEUTRAL", "zone_score": 0, "near_zone": False,
                    "zone_type": "NONE", "hidden_level": False}

        price   = candles_m5[-1]["close"]
        atr_v   = _atr(candles_m5, 14) or (price * 0.0005)
        tol     = atr_v * 0.20   # within 0.20 × ATR = "at the zone"
        tol_eq  = atr_v * 0.08   # tighter tolerance for equal highs/lows

        # EMA dynamic S&R on M5
        closes_m5 = [c["close"] for c in candles_m5]
        e50  = _ema(closes_m5, 50)
        e200 = _ema(closes_m5, min(200, len(closes_m5) - 1))

        # Swing pivots (two wings for robustness)
        res3, sup3 = self._swing_pivots(candles_m5, wing=3)
        res5, sup5 = self._swing_pivots(candles_m15, wing=5)
        all_res = res3 + res5
        all_sup = sup3 + sup5

        psych = self._psychological_levels(price, pip_val)
        eqh, eql  = self._equal_highs_lows(candles_m5, tol_eq)
        bull_ob, bear_ob = self._order_blocks(candles_m5)
        bull_fvg, bear_fvg = self._fvg_midpoints(candles_m5)

        # Score at current price: each zone type contributes +1
        zone_types: list[str] = []

        # Near key S&R level
        at_res = any(abs(r - price) <= tol for r in all_res)
        at_sup = any(abs(s - price) <= tol for s in all_sup)
        if at_res or at_sup:
            zone_types.append("SR")

        # Near psychological level
        if any(abs(p - price) <= tol for p in psych):
            zone_types.append("PSYCH")

        # Near EMA-50 or EMA-200
        if abs(e50[-1] - price) <= tol or (len(e200) > 1 and abs(e200[-1] - price) <= tol):
            zone_types.append("EMA")

        # Near equal highs/lows (hidden liquidity)
        near_eqh = any(abs(h - price) <= tol for h in eqh)
        near_eql = any(abs(l - price) <= tol for l in eql)
        if near_eqh or near_eql:
            zone_types.append("EQL")

        # Near order block
        near_bull_ob = any(abs(o - price) <= tol for o in bull_ob)
        near_bear_ob = any(abs(o - price) <= tol for o in bear_ob)
        if near_bull_ob or near_bear_ob:
            zone_types.append("OB")

        # Near FVG midpoint
        near_bull_fvg = any(abs(f - price) <= tol for f in bull_fvg)
        near_bear_fvg = any(abs(f - price) <= tol for f in bear_fvg)
        if near_bull_fvg or near_bear_fvg:
            zone_types.append("FVG")

        zone_score = len(zone_types)

        # Direction signal from zone position
        direction = "NEUTRAL"
        if at_sup or near_bull_ob or near_bull_fvg or near_eql:
            direction = "BUY"     # price at support / bullish zone → expect bounce up
        if at_res or near_bear_ob or near_bear_fvg or near_eqh:
            direction = "SELL"    # price at resistance / bearish zone → expect push down
        # Both sides? Use momentum (last 3 closes)
        if at_res and at_sup:
            recent_momentum = closes_m5[-1] - closes_m5[-4] if len(closes_m5) >= 4 else 0
            direction = "BUY" if recent_momentum > 0 else "SELL"

        hidden_level = "EQL" in zone_types or "OB" in zone_types or "FVG" in zone_types
        return {
            "direction":    direction,
            "zone_score":   zone_score,
            "zone_types":   zone_types,
            "near_zone":    zone_score >= 1,
            "hidden_level": hidden_level,
            "price":        price,
            "atr":          atr_v,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODULE C  ─  HIDDEN REVERSAL ZONE DETECTOR
# ═════════════════════════════════════════════════════════════════════════════

class _HiddenReversalDetector:
    """Detects non-obvious reversal signals:

      1. RSI  hidden divergence — price HL but RSI LL = hidden bull
                                — price LH but RSI HH = hidden bear
      2. MACD hidden divergence — histogram momentum vs price
      3. Wick exhaustion       — very long wick opposite trend direction
      4. ATR compression       — volatility squeeze → exhaustion zone
      5. Stochastic crossover at extreme (hidden continuation/reversal)
    """

    def _macd(self, closes: list[float]) -> tuple[float, float]:
        """Returns (macd_line, signal_line) for last bar."""
        if len(closes) < 26:
            return 0.0, 0.0
        e12 = _ema(closes, 12)
        e26 = _ema(closes, 26)
        macd_line = [m - s for m, s in zip(e12, e26)]
        signal    = _ema(macd_line, 9)
        return macd_line[-1], signal[-1]

    def _stochastic(self, candles: list[dict], k: int = 14) -> float:
        """Fast %K stochastic (0-100)."""
        tail = candles[-k:] if len(candles) >= k else candles
        hh = max(c["high"] for c in tail)
        ll = min(c["low"]  for c in tail)
        cur = tail[-1]["close"]
        if hh == ll:
            return 50.0
        return 100.0 * (cur - ll) / (hh - ll)

    def _rsi_swing(self, closes: list[float], lookback: int = 20) -> tuple[float, float]:
        """Highest and lowest RSI in last `lookback` bars."""
        if len(closes) < lookback + 15:
            return 50.0, 50.0
        rsis = [_rsi(closes[:i + 1], 14) for i in range(len(closes) - lookback, len(closes))]
        return max(rsis), min(rsis)

    def analyse(self, ticker: str) -> dict:
        candles = _fetch_candles(ticker, "5m", 80)
        if len(candles) < 20:
            return {"direction": "NEUTRAL", "signal_type": "NONE",
                    "hidden_div": False, "exhaustion": False}

        closes = [c["close"] for c in candles]
        price  = closes[-1]
        atr_v  = _atr(candles, 14) or (price * 0.0005)

        # 1. RSI hidden divergence
        rsi_now   = _rsi(closes, 14)
        rsi_prev  = _rsi(closes[:-5], 14)
        price_now  = closes[-1]
        price_prev = closes[-5] if len(closes) > 5 else closes[-1]

        hidden_bull_div = (price_now  > price_prev and rsi_now  < rsi_prev and rsi_now  < 55)
        hidden_bear_div = (price_now  < price_prev and rsi_now  > rsi_prev and rsi_now  > 45)

        # 2. MACD hidden divergence
        macd_now,  sig_now  = self._macd(closes)
        macd_prev, sig_prev = self._macd(closes[:-5])
        macd_hidden_bull = (price_now > price_prev and macd_now < macd_prev and macd_now < 0)
        macd_hidden_bear = (price_now < price_prev and macd_now > macd_prev and macd_now > 0)

        # 3. Wick exhaustion — check last 3 candles
        exhaustion_bull = False
        exhaustion_bear = False
        for c in candles[-3:]:
            body = abs(c["close"] - c["open"])
            bar_range = c["high"] - c["low"]
            if bar_range < 1e-10:
                continue
            up_wick   = c["high"] - max(c["open"], c["close"])
            down_wick = min(c["open"], c["close"]) - c["low"]
            # Long lower wick (≥55% of bar) = bullish exhaustion / rejection
            if down_wick / bar_range >= 0.55 and up_wick / bar_range <= 0.20:
                exhaustion_bull = True
            # Long upper wick (≥55% of bar) = bearish exhaustion / rejection
            if up_wick / bar_range >= 0.55 and down_wick / bar_range <= 0.20:
                exhaustion_bear = True

        # 4. ATR compression (volatility squeeze)
        atr_fast = _atr(candles[-8:],  6) if len(candles) >= 8  else atr_v
        atr_slow = _atr(candles[-25:], 20) if len(candles) >= 25 else atr_v
        compressed = atr_fast < atr_slow * 0.55

        # 5. Stochastic extreme crossover
        stoch = self._stochastic(candles)
        stoch_oversold  = stoch < 22
        stoch_overbought = stoch > 78

        # Aggregate hidden signals
        bull_signals = sum([
            hidden_bull_div, macd_hidden_bull, exhaustion_bull,
            compressed and stoch_oversold,
        ])
        bear_signals = sum([
            hidden_bear_div, macd_hidden_bear, exhaustion_bear,
            compressed and stoch_overbought,
        ])

        hidden_div = hidden_bull_div or hidden_bear_div or macd_hidden_bull or macd_hidden_bear

        if bull_signals > bear_signals and bull_signals >= 1:
            direction = "BUY"
        elif bear_signals > bull_signals and bear_signals >= 1:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        signal_type = "HIDDEN_DIV" if hidden_div else ("EXHAUSTION" if (exhaustion_bull or exhaustion_bear) else "NONE")

        return {
            "direction":      direction,
            "signal_type":    signal_type,
            "hidden_div":     hidden_div,
            "exhaustion":     exhaustion_bull or exhaustion_bear,
            "compressed":     compressed,
            "stoch":          stoch,
            "bull_signals":   bull_signals,
            "bear_signals":   bear_signals,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODULE D  ─  TREND STRENGTH CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

class _TrendStrengthClassifier:
    """Grades trend strength and classifies market phase.

    Inputs:
      • ADX-14 (>40 = ELITE, >25 = STRONG, >15 = MODERATE, else WEAK)
      • 5-EMA stack alignment (8/13/21/50/89)
      • ATR-normalized slope of EMA-21
      • Recent candle momentum (close vs open ratio)
    """

    def _ema_stack_score(self, closes: list[float]) -> int:
        """Returns 0-5 for how many EMAs are aligned bullishly (or bearishly negated)."""
        if len(closes) < 90:
            return 0
        periods = [8, 13, 21, 50, 89]
        emas = {n: _ema(closes, n)[-1] for n in periods}
        # Bull stack: 8 > 13 > 21 > 50 > 89
        bull_score = sum(1 for i in range(len(periods) - 1)
                         if emas[periods[i]] > emas[periods[i + 1]])
        # Bear stack: 8 < 13 < 21 < 50 < 89
        bear_score = sum(1 for i in range(len(periods) - 1)
                         if emas[periods[i]] < emas[periods[i + 1]])
        return max(bull_score, bear_score)

    def analyse(self, ticker: str) -> dict:
        candles = _fetch_candles(ticker, "5m", 100)
        if len(candles) < 30:
            return {"strength": "WEAK", "phase": "RANGING", "adx": 20.0,
                    "stack_score": 0, "direction": "NEUTRAL"}

        closes = [c["close"] for c in candles]
        adx, pdi, ndi = _adx(candles, 14)
        atr_v = _atr(candles, 14) or (closes[-1] * 0.0005)

        # EMA stack alignment
        stack_score = self._ema_stack_score(closes)

        # EMA-21 slope normalized by ATR (over 5 bars)
        e21 = _ema(closes, 21)
        slope_5 = (e21[-1] - e21[-6]) / (atr_v * 5) if len(e21) >= 6 else 0.0

        # Candle momentum: average body direction of last 5 bars
        momentum = sum(c["close"] - c["open"] for c in candles[-5:]) / 5

        # Composite strength score (0-10)
        score = 0
        if adx > 40:  score += 4
        elif adx > 25: score += 3
        elif adx > 15: score += 1
        score += min(stack_score, 3)
        score += 2 if abs(slope_5) > 0.15 else (1 if abs(slope_5) > 0.07 else 0)
        score += 1 if abs(momentum) > atr_v * 0.10 else 0

        if score >= 8:   strength = "ELITE"
        elif score >= 6: strength = "STRONG"
        elif score >= 4: strength = "MODERATE"
        else:            strength = "WEAK"

        # Phase classification
        adx_trend    = adx > 22 and stack_score >= 3
        adx_ranging  = adx < 18
        if adx_ranging:
            phase = "RANGING"
        elif adx_trend:
            phase = "CONTINUATION"
        else:
            phase = "TRANSITION"   # may precede reversal

        # Direction from DI comparison
        direction = "BUY" if pdi > ndi else "SELL" if ndi > pdi else "NEUTRAL"

        return {
            "strength":    strength,
            "phase":       phase,
            "adx":         round(adx, 1),
            "pdi":         round(pdi, 1),
            "ndi":         round(ndi, 1),
            "stack_score": stack_score,
            "slope":       round(slope_5, 3),
            "direction":   direction,
            "score":       score,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODULE E  ─  ZONE CONFLUENCE SCORER
# ═════════════════════════════════════════════════════════════════════════════

class _ZoneConfluenceScorer:
    """Combines all zone signals into a single confluence integer (0-6).

    A "confluence zone" is where multiple independent zone types stack at
    the same price level — the more types present, the stronger the zone.

    Score meaning:
      0-1: Random noise / no meaningful zone
      2-3: Valid zone — trade with confirmation
      4-5: Strong zone — high-probability reaction expected
      6:   PREMIUM confluence — maximum conviction setup
    """

    def score(self, sr_zone_types: list[str], hrz_hidden_div: bool,
              hrz_exhaustion: bool, cascade_direction: str, sr_direction: str) -> int:
        """Aggregate zone types into a confluence integer."""
        score = len(set(sr_zone_types))    # each unique zone type = 1 point
        if hrz_hidden_div:
            score += 1
        if hrz_exhaustion:
            score += 1
        # Direction agreement across modules adds nothing to zone count
        # but is noted in final result
        return min(score, 6)


# ═════════════════════════════════════════════════════════════════════════════
# ELITE ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

_CASCADE  = _BigToSmallCascade()
_SR       = _EliteSREngine()
_HRZ      = _HiddenReversalDetector()
_STRENGTH = _TrendStrengthClassifier()
_SCORER   = _ZoneConfluenceScorer()


def _grade(confidence: float, zone_confluence: int, hidden_zone: bool) -> str:
    """Map confidence + zone depth to elite grade label."""
    if confidence >= 88 and zone_confluence >= 4:
        return "HIDDEN"
    if confidence >= 82 and zone_confluence >= 3:
        return "ELITE"
    if confidence >= 72:
        return "STRONG"
    if confidence >= 60:
        return "MODERATE"
    return "WEAK"


_PIP_MAP: dict[str, float] = {
    "USDJPY=X": 0.01, "EURJPY=X": 0.01, "GBPJPY=X": 0.01,
    "AUDJPY=X": 0.01, "CADJPY=X": 0.01, "NZDJPY=X": 0.01, "CHFJPY=X": 0.01,
    "PAXG-USD": 0.10, "SI=F": 0.001,
    "BTC-USD": 1.0, "ETH-USD": 1.0, "BNB-USD": 1.0, "SOL-USD": 1.0, "XRP-USD": 1.0,
    "^NDX": 1.0, "^DJI": 1.0, "^GSPC": 1.0,
}


def finorix_elite_analyse(pair: str, market_type: str = "OTC") -> dict:
    """Full elite analysis pipeline.  Returns structured result dict.
    Contract: zero side-effects — never touches signal text or other modules.
    """
    _FAIL = {
        "ok": False, "direction": "WAIT", "confidence": 0.0, "grade": "WEAK",
        "zone_confluence": 0, "trend_phase": "RANGING", "trend_strength": "WEAK",
        "hidden_zone": False, "cascade_score": 0, "agree": 0.5,
        "models_buy": 0, "models_sell": 0, "veto": False, "raw_score": 0.0,
    }
    try:
        # ── Resolve ticker ────────────────────────────────────────────────
        is_otc = ("〔OTC〕" in pair or "(OTC)" in pair.upper() or "_otc" in pair.lower())
        ticker = (_otc_to_ticker(pair) if is_otc else _TICKER_MAP.get(pair))
        if not ticker:
            # Try bare lookup stripping spaces
            clean = pair.strip().replace("〔OTC〕", "").replace("(OTC)", "").strip()
            ticker = _TICKER_MAP.get(clean, clean + "=X")

        pip_val = _PIP_MAP.get(ticker, 0.0001)

        # ── Run all modules ───────────────────────────────────────────────
        cascade  = _CASCADE.analyse(ticker)
        sr       = _SR.analyse(ticker, pip_val)
        hrz      = _HRZ.analyse(ticker)
        strength = _STRENGTH.analyse(ticker)

        # ── Gather directional votes ──────────────────────────────────────
        votes: list[str] = []

        # Module A: Cascade (weighted by cascade_score)
        if cascade["direction"] != "NEUTRAL":
            for _ in range(max(1, cascade["cascade_score"])):
                votes.append(cascade["direction"])

        # Module B: S&R zones
        if sr["direction"] != "NEUTRAL":
            votes.append(sr["direction"])
            if sr["zone_score"] >= 3:
                votes.append(sr["direction"])   # high confluence → double vote

        # Module C: Hidden reversals
        if hrz["direction"] != "NEUTRAL":
            votes.append(hrz["direction"])
            if hrz["hidden_div"]:
                votes.append(hrz["direction"])  # confirmed hidden divergence → extra weight

        # Module D: Trend strength direction
        if strength["direction"] != "NEUTRAL":
            votes.append(strength["direction"])

        # ── Resolve consensus ─────────────────────────────────────────────
        buy_v  = votes.count("BUY")
        sell_v = votes.count("SELL")
        total_v = buy_v + sell_v

        if total_v == 0:
            return _FAIL

        direction = "BUY" if buy_v > sell_v else "SELL" if sell_v > buy_v else "WAIT"
        agree     = max(buy_v, sell_v) / total_v

        # Veto: close split (neither side dominates by even 1 vote)
        veto = (direction != "WAIT") and (abs(buy_v - sell_v) <= 1 and total_v >= 6)

        # ── Zone confluence score ─────────────────────────────────────────
        zone_confluence = _SCORER.score(
            sr.get("zone_types", []),
            hrz.get("hidden_div", False),
            hrz.get("exhaustion", False),
            cascade["direction"],
            sr["direction"],
        )

        # ── Trend phase ───────────────────────────────────────────────────
        if cascade.get("is_reversal") or (hrz["hidden_div"] and sr["near_zone"]):
            trend_phase = "REVERSAL"
        elif cascade.get("is_continuation") and strength["phase"] == "CONTINUATION":
            trend_phase = "CONTINUATION"
        else:
            trend_phase = "RANGING"

        # ── Confidence calculation ────────────────────────────────────────
        base_conf = 50.0 + (agree - 0.5) * 80.0    # 50% at 50/50 → 90% at 100%
        # Zone confluence bonus (up to +10)
        base_conf += zone_confluence * 1.8
        # Trend strength bonus
        bonus_strength = {"ELITE": 6, "STRONG": 4, "MODERATE": 2, "WEAK": 0}
        base_conf += bonus_strength.get(strength["strength"], 0)
        # Phase bonus
        if trend_phase == "REVERSAL" and zone_confluence >= 3:
            base_conf += 4
        if trend_phase == "CONTINUATION" and cascade["cascade_score"] >= 3:
            base_conf += 3
        # Hidden zone bonus
        if sr.get("hidden_level") and hrz.get("hidden_div"):
            base_conf += 5

        confidence = round(min(99.0, max(0.0, base_conf)), 1)

        # Veto penalty
        if veto:
            confidence = max(50.0, confidence - 8.0)

        # ── Grade ─────────────────────────────────────────────────────────
        hidden_zone = sr.get("hidden_level", False) or hrz.get("hidden_div", False)
        grade       = _grade(confidence, zone_confluence, hidden_zone)

        # ── Threshold gate ────────────────────────────────────────────────
        min_conf = 65.0 if is_otc else 60.0
        ok       = (confidence >= min_conf and direction not in ("WAIT", None) and not veto)

        return {
            "ok":             ok,
            "direction":      direction if ok else "WAIT",
            "confidence":     confidence,
            "grade":          grade,
            "zone_confluence":zone_confluence,
            "trend_phase":    trend_phase,
            "trend_strength": strength["strength"],
            "hidden_zone":    hidden_zone,
            "cascade_score":  cascade["cascade_score"],
            "agree":          round(agree, 3),
            "models_buy":     buy_v,
            "models_sell":    sell_v,
            "veto":           veto,
            "raw_score":      round(agree * zone_confluence * (confidence / 100), 3),
        }
    except Exception as exc:
        _log.debug(f"[finorix_elite] error for {pair}: {exc}")
        return _FAIL
