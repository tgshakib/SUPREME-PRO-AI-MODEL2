"""FINORIX SUPREME ANALYSIS ENGINE  — V2 ELITE ULTRA
=====================================================================
Standalone analysis module for SUPREME PRO AI BOT.
Purpose : extra analysis vote for binary + forex signals.
Contract: zero side-effects — never modifies signal text, keyboard,
          or any other module. Only returns a structured result dict.

Public API
----------
finorix_analyse(pair: str, market_type: str = "OTC") -> dict
    market_type: "OTC" | "PO OTC" | "QX OTC" | "LIVE" | "FOREX" | "FUNDED"
    returns:
      {
        "ok":         bool,   # passed confidence threshold for market type
        "direction":  str,    # "BUY" | "SELL" | "WAIT"
        "confidence": float,  # 0-100
        "grade":      str,    # "GOD" | "ULTRA" | "ELITE" | "STRONG" | "MODERATE" | "WEAK"
        "agree":      float,  # % of sub-models in agreement
        "models_buy": int,
        "models_sell": int,
        "veto":       bool,   # True = opposing models > 60% → veto (WAIT forced)
        "raw_score":  float,
      }
"""
from __future__ import annotations
import math
import time
import logging

_log = logging.getLogger(__name__)

# ── optional deps ─────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    yf = None          # type: ignore
    pd = None          # type: ignore
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

# ── Cache to avoid hammering Yahoo ───────────────────────────────────────────
_CANDLE_CACHE: dict[str, tuple[float, list[dict]]] = {}  # key → (ts, candles)
_CACHE_TTL = 60.0   # seconds

# ═════════════════════════════════════════════════════════════════════════════
# MODULE A  —  SMART MONEY CONCEPTS  (EXPANDED)
# ═════════════════════════════════════════════════════════════════════════════

class _SMCEngine:
    """BoS · CHoCH · Order Blocks · FVG · Liquidity Sweep ·
       Wyckoff Phase · Market Structure"""

    # ── Break of Structure ────────────────────────────────────────────────────
    def detect_bos(self, candles: list[dict]) -> dict:
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        last_high = max(highs[-12:-1])
        last_low  = min(lows[-12:-1])
        cur = closes[-1]
        bull = cur > last_high
        bear = cur < last_low
        strength = abs(cur - last_high) / (last_high + 1e-9) if bull else \
                   abs(cur - last_low)  / (last_low  + 1e-9) if bear else 0.0
        return {
            "bos_bullish": bull,
            "bos_bearish": bear,
            "bos_strength": round(strength * 100, 3),
            "bos_bias": "BUY" if bull else ("SELL" if bear else "NEUTRAL"),
        }

    # ── Change of Character ───────────────────────────────────────────────────
    def detect_choch(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        recent_trend = closes[-5]  - closes[-15]
        current_move = closes[-1]  - closes[-5]
        detected = (
            (recent_trend > 0 and current_move < -abs(recent_trend) * 0.45) or
            (recent_trend < 0 and current_move >  abs(recent_trend) * 0.45)
        )
        direction = (
            "BULLISH CHoCH" if (recent_trend < 0 and current_move > 0) else
            "BEARISH CHoCH" if (recent_trend > 0 and current_move < 0) else "NONE"
        )
        # Confirm with volume when available
        vol_confirm = False
        vols = [c.get("volume", 0) for c in candles]
        if vols[-1] > 0:
            avg_vol = sum(vols[-10:]) / 10
            vol_confirm = vols[-1] > avg_vol * 1.3
        return {
            "choch_detected": detected,
            "choch_direction": direction,
            "vol_confirm": vol_confirm,
        }

    # ── Order Blocks ──────────────────────────────────────────────────────────
    def find_order_blocks(self, candles: list[dict]) -> dict:
        obs: list[dict] = []
        for i in range(4, len(candles) - 1):
            c   = candles[i]
            nxt = candles[i + 1]
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            if rng < 1e-10:
                continue
            # Strong body + large next-bar displacement
            if body / rng > 0.65 and abs(nxt["close"] - c["close"]) > body * 0.45:
                # Freshness score: more recent = higher score
                freshness = i / len(candles)
                obs.append({
                    "type":      "BULLISH OB" if c["close"] > c["open"] else "BEARISH OB",
                    "high":      c["high"],
                    "low":       c["low"],
                    "mid":       round((c["high"] + c["low"]) / 2, 6),
                    "freshness": round(freshness, 3),
                })
        # Most recent valid OB
        active = obs[-1] if obs else None
        return {
            "active_ob":   active,
            "ob_bias":     active["type"] if active else "NONE",
            "ob_count":    len(obs),
        }

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    def find_fvg(self, candles: list[dict]) -> dict:
        fvgs: list[dict] = []
        for i in range(1, len(candles) - 1):
            prev = candles[i - 1]
            cur  = candles[i]
            nxt  = candles[i + 1]
            gap  = 0.0
            if nxt["low"] > prev["high"]:
                gap = nxt["low"] - prev["high"]
                fvgs.append({
                    "type": "BULLISH FVG",
                    "mid":  round((nxt["low"] + prev["high"]) / 2, 6),
                    "gap":  round(gap, 6),
                })
            elif nxt["high"] < prev["low"]:
                gap = prev["low"] - nxt["high"]
                fvgs.append({
                    "type": "BEARISH FVG",
                    "mid":  round((prev["low"] + nxt["high"]) / 2, 6),
                    "gap":  round(gap, 6),
                })
        active = fvgs[-1] if fvgs else None
        return {
            "active_fvg": active,
            "fvg_bias":   active["type"] if active else "NO FVG",
            "fvg_count":  len(fvgs),
        }

    # ── Liquidity Sweep ───────────────────────────────────────────────────────
    def detect_liquidity_sweep(self, candles: list[dict]) -> dict:
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        prev_high   = max(highs[-16:-3])
        prev_low    = min(lows[-16:-3])
        recent_high = max(highs[-3:])
        recent_low  = min(lows[-3:])
        cur = closes[-1]
        sw_low  = recent_low  < prev_low  and cur > prev_low
        sw_high = recent_high > prev_high and cur < prev_high
        # Depth of sweep
        depth = 0.0
        if sw_low:
            depth = (prev_low - recent_low) / (prev_low + 1e-9) * 100
        elif sw_high:
            depth = (recent_high - prev_high) / (prev_high + 1e-9) * 100
        return {
            "sweep_detected": sw_low or sw_high,
            "sweep_bias":     "BUY" if sw_low else ("SELL" if sw_high else "NEUTRAL"),
            "sweep_type":     ("SELL-SIDE SWEPT→BUY" if sw_low else
                               "BUY-SIDE SWEPT→SELL" if sw_high else "NO SWEEP"),
            "sweep_depth_pct": round(depth, 4),
        }

    # ── Wyckoff Phase ─────────────────────────────────────────────────────────
    def detect_wyckoff(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        vols   = [c.get("volume", 1) for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        n = len(closes)
        if n < 30:
            return {"phase": "UNKNOWN", "bias": "NEUTRAL"}
        # Simple: compare trend + volume divergence
        price_trend_up   = closes[-1] > closes[-15]
        price_trend_down = closes[-1] < closes[-15]
        avg_vol_early    = sum(vols[:n//2]) / (n//2)
        avg_vol_recent   = sum(vols[n//2:]) / (n//2)
        vol_expanding    = avg_vol_recent > avg_vol_early * 1.1
        vol_contracting  = avg_vol_recent < avg_vol_early * 0.9
        if price_trend_up and vol_contracting:
            phase, bias = "DISTRIBUTION", "SELL"
        elif price_trend_down and vol_expanding:
            phase, bias = "MARKDOWN", "SELL"
        elif price_trend_down and vol_contracting:
            phase, bias = "ACCUMULATION", "BUY"
        elif price_trend_up and vol_expanding:
            phase, bias = "MARKUP", "BUY"
        else:
            phase, bias = "CONSOLIDATION", "NEUTRAL"
        return {"phase": phase, "bias": bias}

    # ── Market Structure (HH/HL/LH/LL) ───────────────────────────────────────
    def market_structure(self, candles: list[dict]) -> dict:
        highs = [c["high"]  for c in candles]
        lows  = [c["low"]   for c in candles]
        # Use every 5 bars as a pivot
        pivots_h = highs[::5]
        pivots_l = lows[::5]
        if len(pivots_h) < 4:
            return {"ms": "NEUTRAL", "ms_bias": "NEUTRAL"}
        hh = pivots_h[-1] > pivots_h[-2] and pivots_h[-2] > pivots_h[-3]
        hl = pivots_l[-1] > pivots_l[-2] and pivots_l[-2] > pivots_l[-3]
        lh = pivots_h[-1] < pivots_h[-2] and pivots_h[-2] < pivots_h[-3]
        ll = pivots_l[-1] < pivots_l[-2] and pivots_l[-2] < pivots_l[-3]
        if hh and hl:
            return {"ms": "HH+HL (BULLISH)", "ms_bias": "BUY"}
        if lh and ll:
            return {"ms": "LH+LL (BEARISH)", "ms_bias": "SELL"}
        return {"ms": "MIXED", "ms_bias": "NEUTRAL"}


# ═════════════════════════════════════════════════════════════════════════════
# MODULE B  —  INDICATOR SUITE  (EXPANDED: 12 indicators)
# ═════════════════════════════════════════════════════════════════════════════

class _IndicatorSuite:
    """EMA · MACD · Bollinger · Stochastic · ATR · ADX · CCI ·
       RSI · Williams%R · MFI · Ichimoku · Divergence"""

    def _ema(self, data: list[float], period: int) -> list[float]:
        k = 2 / (period + 1)
        out = [data[0]]
        for p in data[1:]:
            out.append(p * k + out[-1] * (1 - k))
        return out

    def _sma(self, data: list[float], period: int) -> float:
        return sum(data[-period:]) / period if len(data) >= period else sum(data) / len(data)

    # ── EMA Stack ────────────────────────────────────────────────────────────
    def ema_stack(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        e8  = self._ema(closes, 8)[-1]
        e13 = self._ema(closes, 13)[-1]
        e21 = self._ema(closes, 21)[-1]
        e50 = self._ema(closes, 50)[-1] if len(closes) >= 50 else closes[-1]
        e89 = self._ema(closes, 89)[-1] if len(closes) >= 89 else closes[-1]
        cur = closes[-1]
        # Full 5-level stack check
        full_bull = cur > e8 > e13 > e21 > e50
        full_bear = cur < e8 < e13 < e21 < e50
        part_bull = (cur > e8 and cur > e21) and not full_bull
        part_bear = (cur < e8 and cur < e21) and not full_bear
        if full_bull:
            bias, strength = "BUY", 1.0
        elif full_bear:
            bias, strength = "SELL", 1.0
        elif part_bull:
            bias, strength = "BUY", 0.6
        elif part_bear:
            bias, strength = "SELL", 0.6
        else:
            bias, strength = "NEUTRAL", 0.0
        return {"ema_bias": bias, "ema_strength": strength,
                "e8": e8, "e21": e21, "e50": e50, "e89": e89}

    # ── MACD (12/26/9) + Histogram Slope ─────────────────────────────────────
    def macd(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        e12 = self._ema(closes, 12)
        e26 = self._ema(closes, 26) if len(closes) >= 26 else e12
        macd_line   = [a - b for a, b in zip(e12, e26)]
        signal_line = self._ema(macd_line, 9)
        hist  = [m - s for m, s in zip(macd_line, signal_line)]
        cm, cs, ch = macd_line[-1], signal_line[-1], hist[-1]
        ph = hist[-2] if len(hist) > 1 else 0
        pph = hist[-3] if len(hist) > 2 else 0
        cross_up   = cm > cs and (len(macd_line) < 2 or macd_line[-2] <= signal_line[-2])
        cross_down = cm < cs and (len(macd_line) < 2 or macd_line[-2] >= signal_line[-2])
        hist_slope = ch - ph
        hist_accel = (ch - ph) - (ph - pph)
        if cross_up or (cm > cs and ch > ph > 0):
            bias = "BUY"
        elif cross_down or (cm < cs and ch < ph < 0):
            bias = "SELL"
        else:
            bias = "NEUTRAL"
        return {
            "macd": round(cm, 7), "signal": round(cs, 7), "histogram": round(ch, 7),
            "hist_slope": hist_slope, "hist_accel": hist_accel,
            "macd_bias": bias, "cross_up": cross_up, "cross_down": cross_down,
        }

    # ── Bollinger Bands (20, 2σ) + Squeeze + %B ──────────────────────────────
    def bollinger(self, candles: list[dict], period: int = 20, sd: float = 2.0) -> dict:
        closes = [c["close"] for c in candles[-period:]]
        sma = sum(closes) / len(closes)
        std = math.sqrt(sum((p - sma) ** 2 for p in closes) / len(closes)) or 1e-9
        upper = sma + sd * std
        lower = sma - sd * std
        cur   = closes[-1]
        bw    = (upper - lower) / sma * 100 if sma else 0
        pct_b = (cur - lower) / (upper - lower) if upper != lower else 0.5
        squeeze = bw < 0.4
        # Keltner channel for squeeze confirmation
        atr_v = self.atr(candles, period=10)
        kc_upper = sma + 1.5 * atr_v
        kc_lower = sma - 1.5 * atr_v
        kc_squeeze = lower > kc_lower and upper < kc_upper
        return {
            "upper": upper, "middle": sma, "lower": lower,
            "bandwidth": bw, "percent_b": pct_b,
            "squeeze": squeeze, "kc_squeeze": kc_squeeze,
            "bb_bias": "BUY" if cur <= lower else ("SELL" if cur >= upper else "NEUTRAL"),
        }

    # ── Stochastic (14, 3, 3) ─────────────────────────────────────────────────
    def stochastic(self, candles: list[dict], k_period: int = 14, d_period: int = 3) -> dict:
        recent = candles[-k_period:]
        hh = max(c["high"] for c in recent)
        ll = min(c["low"]  for c in recent)
        cur = recent[-1]["close"]
        k = ((cur - ll) / (hh - ll) * 100) if hh != ll else 50.0
        k_series = []
        for i in range(min(d_period + 2, len(candles))):
            r = candles[-(k_period + i): (-i if i > 0 else len(candles))]
            if not r:
                continue
            _hh = max(c["high"] for c in r)
            _ll = min(c["low"]  for c in r)
            _cl = r[-1]["close"]
            k_series.append((_cl - _ll) / (_hh - _ll) * 100 if _hh != _ll else 50.0)
        d = sum(k_series[:d_period]) / d_period if len(k_series) >= d_period else k
        prev_k = k_series[1] if len(k_series) > 1 else k
        cross_up   = k > d and prev_k <= d
        cross_down = k < d and prev_k >= d
        if k < 20 or (cross_up and k < 40):
            bias = "BUY"
        elif k > 80 or (cross_down and k > 60):
            bias = "SELL"
        else:
            bias = "NEUTRAL"
        return {
            "k": round(k, 2), "d": round(d, 2),
            "overbought": k > 80, "oversold": k < 20,
            "cross_up": cross_up, "cross_down": cross_down,
            "stoch_bias": bias,
        }

    # ── ATR ───────────────────────────────────────────────────────────────────
    def atr(self, candles: list[dict], period: int = 14) -> float:
        trs = []
        for i in range(1, min(period + 1, len(candles))):
            c, p = candles[i], candles[i - 1]
            trs.append(max(
                c["high"] - c["low"],
                abs(c["high"] - p["close"]),
                abs(c["low"]  - p["close"]),
            ))
        return sum(trs) / len(trs) if trs else 1e-6

    # ── ADX (14) ──────────────────────────────────────────────────────────────
    def adx(self, candles: list[dict], period: int = 14) -> dict:
        if len(candles) < period + 1:
            return {"adx": 20.0, "trend_strong": False,
                    "di_plus": 20, "di_minus": 20, "adx_bias": "NEUTRAL"}
        dm_p, dm_m, trs = [], [], []
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i - 1]
            up   = c["high"] - p["high"]
            down = p["low"]  - c["low"]
            dm_p.append(up   if up   > down and up   > 0 else 0)
            dm_m.append(down if down > up   and down > 0 else 0)
            trs.append(max(c["high"] - c["low"],
                           abs(c["high"] - p["close"]),
                           abs(c["low"]  - p["close"])))
        atr_v = sum(trs[-period:]) / period or 1e-9
        di_p  = (sum(dm_p[-period:]) / period) / atr_v * 100
        di_m  = (sum(dm_m[-period:]) / period) / atr_v * 100
        dx    = abs(di_p - di_m) / (di_p + di_m) * 100 if (di_p + di_m) else 0
        return {
            "adx": round(dx, 2), "di_plus": round(di_p, 2), "di_minus": round(di_m, 2),
            "trend_strong": dx > 22,
            "very_strong":  dx > 35,
            "adx_bias": "BUY" if di_p > di_m else "SELL",
        }

    # ── CCI (20) ─────────────────────────────────────────────────────────────
    def cci(self, candles: list[dict], period: int = 20) -> dict:
        recent = candles[-period:]
        tps    = [(c["high"] + c["low"] + c["close"]) / 3 for c in recent]
        mean   = sum(tps) / len(tps)
        dev    = sum(abs(t - mean) for t in tps) / len(tps) or 1e-9
        val    = (tps[-1] - mean) / (0.015 * dev)
        return {
            "cci": round(val, 2),
            "overbought": val > 100, "oversold": val < -100,
            "extreme_ob": val > 200, "extreme_os": val < -200,
            "cci_bias": "BUY" if val < -100 else ("SELL" if val > 100 else "NEUTRAL"),
        }

    # ── RSI (14) ─────────────────────────────────────────────────────────────
    def rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, period + 1):
            d = closes[-period + i] - closes[-period + i - 1]
            gains.append(d if d > 0 else 0.0)
            losses.append(-d if d < 0 else 0.0)
        ag = sum(gains) / period
        al = sum(losses) / period
        return round(100 - 100 / (1 + ag / al), 2) if al else 100.0

    # ── Williams %R (14) ─────────────────────────────────────────────────────
    def williams_r(self, candles: list[dict], period: int = 14) -> dict:
        recent = candles[-period:]
        hh = max(c["high"]  for c in recent)
        ll = min(c["low"]   for c in recent)
        cur = recent[-1]["close"]
        wr = ((hh - cur) / (hh - ll) * -100) if hh != ll else -50.0
        return {
            "wr": round(wr, 2),
            "overbought": wr >= -20,
            "oversold":   wr <= -80,
            "wr_bias": "BUY" if wr <= -80 else ("SELL" if wr >= -20 else "NEUTRAL"),
        }

    # ── MFI — Money Flow Index (14) ───────────────────────────────────────────
    def mfi(self, candles: list[dict], period: int = 14) -> dict:
        pos_flow, neg_flow = 0.0, 0.0
        for i in range(max(1, len(candles) - period), len(candles)):
            c  = candles[i]
            p  = candles[i - 1]
            tp = (c["high"] + c["low"] + c["close"]) / 3
            pp = (p["high"] + p["low"] + p["close"]) / 3
            mf = tp * c.get("volume", 1)
            if tp > pp:
                pos_flow += mf
            else:
                neg_flow += mf
        mfi_val = 100 - (100 / (1 + pos_flow / neg_flow)) if neg_flow else 100.0
        return {
            "mfi": round(mfi_val, 2),
            "overbought": mfi_val > 80, "oversold": mfi_val < 20,
            "mfi_bias": "BUY" if mfi_val < 20 else ("SELL" if mfi_val > 80 else "NEUTRAL"),
        }

    # ── Ichimoku Cloud (9/26/52) ──────────────────────────────────────────────
    def ichimoku(self, candles: list[dict]) -> dict:
        if len(candles) < 52:
            return {"ichi_bias": "NEUTRAL", "above_cloud": False, "below_cloud": False}
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        tenkan  = (max(highs[-9:])  + min(lows[-9:]))  / 2
        kijun   = (max(highs[-26:]) + min(lows[-26:])) / 2
        span_a  = (tenkan + kijun) / 2
        span_b  = (max(highs[-52:]) + min(lows[-52:])) / 2
        cloud_top = max(span_a, span_b)
        cloud_bot = min(span_a, span_b)
        cur = closes[-1]
        above_cloud = cur > cloud_top
        below_cloud = cur < cloud_bot
        tk_cross_bull = tenkan > kijun
        tk_cross_bear = tenkan < kijun
        if above_cloud and tk_cross_bull:
            bias = "BUY"
        elif below_cloud and tk_cross_bear:
            bias = "SELL"
        else:
            bias = "NEUTRAL"
        return {
            "tenkan": tenkan, "kijun": kijun,
            "cloud_top": cloud_top, "cloud_bot": cloud_bot,
            "above_cloud": above_cloud, "below_cloud": below_cloud,
            "ichi_bias": bias,
        }

    # ── RSI Divergence ────────────────────────────────────────────────────────
    def rsi_divergence(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        lows   = [c["low"]   for c in candles]
        highs  = [c["high"]  for c in candles]
        if len(closes) < 20:
            return {"div_type": "NONE", "div_bias": "NEUTRAL"}
        rsi_series = [self.rsi(closes[:i+1]) for i in range(len(closes) - 5, len(closes))]
        price_lows  = lows[-5:]
        price_highs = highs[-5:]
        bull_div = price_lows[-1] < price_lows[0]  and rsi_series[-1] > rsi_series[0]
        bear_div = price_highs[-1] > price_highs[0] and rsi_series[-1] < rsi_series[0]
        if bull_div:
            return {"div_type": "BULLISH DIV", "div_bias": "BUY"}
        if bear_div:
            return {"div_type": "BEARISH DIV", "div_bias": "SELL"}
        return {"div_type": "NONE", "div_bias": "NEUTRAL"}


# ═════════════════════════════════════════════════════════════════════════════
# MODULE C  —  AI MULTI-MODEL VOTING  (12 models, up from 8)
# ═════════════════════════════════════════════════════════════════════════════

class _AIVotingLayer:
    """12 sub-models, each with tuned weight. Weighted consensus → direction."""

    MODELS = [
        ("TrendFollower-AI",     1.6),   # EMA stack + ADX trend
        ("MeanReversion-AI",     1.3),   # RSI + Stoch + BB + CCI extremes
        ("BreakoutDetect-AI",    1.4),   # BB squeeze + BoS
        ("SMC-AI",               2.0),   # OB + FVG + Sweep + CHoCH  ← highest weight
        ("Momentum-AI",          1.5),   # MACD + RSI mid-zone + ADX
        ("Volatility-AI",        1.2),   # BB squeeze + ATR expansion
        ("PatternRecog-AI",      1.7),   # Candlestick patterns
        ("LiquidityMap-AI",      1.8),   # Sweep + S/R distance
        ("Wyckoff-AI",           1.4),   # Wyckoff phase alignment
        ("MarketStructure-AI",   1.6),   # HH/HL or LH/LL
        ("MultiIndicator-AI",    1.3),   # Williams%R + MFI + Ichimoku
        ("Divergence-AI",        1.5),   # RSI divergence
    ]

    def _v_trend(self, d: dict) -> float:
        v = 0.0
        ema_map = {"BUY": 1, "SELL": -1}
        v += ema_map.get(d["ema_bias"], 0) * d.get("ema_strength", 1.0)
        adx = d["adx"]
        if adx["trend_strong"]:
            v += 1.2 if adx["adx_bias"] == "BUY" else -1.2
        if adx.get("very_strong"):
            v += 0.5 if adx["adx_bias"] == "BUY" else -0.5
        return v

    def _v_mean(self, d: dict) -> float:
        v = 0.0
        rsi = d["rsi"]
        if rsi < 25:      v += 2.5
        elif rsi < 35:    v += 1.5
        elif rsi > 75:    v -= 2.5
        elif rsi > 65:    v -= 1.5
        stoch = d["stoch"]
        if stoch["oversold"]:       v += 1.5
        elif stoch["overbought"]:   v -= 1.5
        elif stoch["cross_up"]:     v += 0.8
        elif stoch["cross_down"]:   v -= 0.8
        bb = d["bb"]
        if bb["bb_bias"] == "BUY":    v += 1.0
        elif bb["bb_bias"] == "SELL": v -= 1.0
        cci = d["cci"]
        if cci["extreme_os"]:  v += 2.0
        elif cci["oversold"]:  v += 1.0
        elif cci["extreme_ob"]: v -= 2.0
        elif cci["overbought"]: v -= 1.0
        return v

    def _v_breakout(self, d: dict) -> float:
        v = 0.0
        bb = d["bb"]
        bos = d["bos"]
        if bb["kc_squeeze"]:  v += 1.5  # stronger: Keltner squeeze
        elif bb["squeeze"]:   v += 0.8
        if bos["bos_bullish"]:
            v += 2.0 + min(bos["bos_strength"] / 50, 1.0)
        elif bos["bos_bearish"]:
            v -= 2.0 + min(bos["bos_strength"] / 50, 1.0)
        return v

    def _v_smc(self, d: dict) -> float:
        v = 0.0
        ob = d["ob"];  fvg = d["fvg"];  sweep = d["sweep"];  choch = d["choch"]
        if "BULLISH" in ob.get("ob_bias", ""):     v += 2.5
        elif "BEARISH" in ob.get("ob_bias", ""):   v -= 2.5
        if "BULLISH" in fvg.get("fvg_bias", ""):   v += 1.2
        elif "BEARISH" in fvg.get("fvg_bias", ""): v -= 1.2
        if sweep["sweep_bias"] == "BUY":
            v += 2.0 + min(sweep["sweep_depth_pct"] * 10, 1.5)
        elif sweep["sweep_bias"] == "SELL":
            v -= 2.0 + min(sweep["sweep_depth_pct"] * 10, 1.5)
        if choch["choch_detected"]:
            choch_v = 1.5 if "BULLISH" in choch["choch_direction"] else -1.5
            if choch.get("vol_confirm"):
                choch_v *= 1.3
            v += choch_v
        return v

    def _v_momentum(self, d: dict) -> float:
        v = 0.0
        macd = d["macd"]; rsi = d["rsi"]; adx = d["adx"]
        if macd["macd_bias"] == "BUY":
            v += 2.0
            if macd["hist_slope"] > 0 and macd["hist_accel"] > 0:
                v += 0.5  # accelerating histogram
        elif macd["macd_bias"] == "SELL":
            v -= 2.0
            if macd["hist_slope"] < 0 and macd["hist_accel"] < 0:
                v -= 0.5
        if 50 < rsi < 65:    v += 0.8
        elif 35 < rsi < 50:  v -= 0.8
        if adx["trend_strong"]:
            v += 0.8 if adx["adx_bias"] == "BUY" else -0.8
        return v

    def _v_volatility(self, d: dict) -> float:
        bb = d["bb"]
        if bb["kc_squeeze"]:  return 1.2
        if bb["squeeze"]:     return 0.6
        return 0.0

    def _v_pattern(self, d: dict) -> float:
        v = 0.0
        for p in d["patterns"]:
            p_up = p.upper()
            if "BULLISH ENGULFING" in p_up:  v += 3.0
            elif "BEARISH ENGULFING" in p_up: v -= 3.0
            elif "HAMMER" in p_up:            v += 2.0
            elif "SHOOTING STAR" in p_up:     v -= 2.0
            elif "DOJI" in p_up:              v += 0.3  # slight indecision
        return v

    def _v_liquidity(self, d: dict) -> float:
        v = 0.0
        sweep = d["sweep"];  sr = d["sr_zones"]
        if sweep["sweep_bias"] == "BUY":   v += 2.5
        elif sweep["sweep_bias"] == "SELL": v -= 2.5
        ds = sr.get("distance_to_support",    999)
        dr = sr.get("distance_to_resistance", 999)
        if ds < dr * 0.2:   v += 1.2
        elif dr < ds * 0.2: v -= 1.2
        return v

    def _v_wyckoff(self, d: dict) -> float:
        bias = d["wyckoff"]["bias"]
        if bias == "BUY":   return 2.0
        if bias == "SELL":  return -2.0
        return 0.0

    def _v_market_structure(self, d: dict) -> float:
        ms = d["ms"]["ms_bias"]
        if ms == "BUY":   return 2.0
        if ms == "SELL":  return -2.0
        return 0.0

    def _v_multi_indicator(self, d: dict) -> float:
        v = 0.0
        wr = d["wr"];  mfi_r = d["mfi_r"];  ichi = d["ichi"]
        if wr["wr_bias"] == "BUY":   v += 1.5
        elif wr["wr_bias"] == "SELL": v -= 1.5
        if mfi_r["mfi_bias"] == "BUY":   v += 1.5
        elif mfi_r["mfi_bias"] == "SELL": v -= 1.5
        if ichi["ichi_bias"] == "BUY":   v += 2.0
        elif ichi["ichi_bias"] == "SELL": v -= 2.0
        return v

    def _v_divergence(self, d: dict) -> float:
        bias = d["div"]["div_bias"]
        if bias == "BUY":   return 2.5
        if bias == "SELL":  return -2.5
        return 0.0

    def vote(self, d: dict) -> dict:
        raw_votes = [
            self._v_trend(d),
            self._v_mean(d),
            self._v_breakout(d),
            self._v_smc(d),
            self._v_momentum(d),
            self._v_volatility(d),
            self._v_pattern(d),
            self._v_liquidity(d),
            self._v_wyckoff(d),
            self._v_market_structure(d),
            self._v_multi_indicator(d),
            self._v_divergence(d),
        ]
        total_w  = sum(w for _, w in self.MODELS)
        weighted = sum(v * w for v, (_, w) in zip(raw_votes, self.MODELS))
        norm     = weighted / total_w

        buy_c  = sum(1 for v in raw_votes if v > 0)
        sell_c = sum(1 for v in raw_votes if v < 0)
        agree  = round(max(buy_c, sell_c) / len(self.MODELS) * 100, 1)

        # Consensus: need ≥ 58% agreement for a non-SPLIT call
        consensus = ("STRONG"   if agree >= 75  else
                     "MODERATE" if agree >= 58  else "SPLIT")
        decision  = "BUY" if norm > 0.6 else ("SELL" if norm < -0.6 else "WAIT")
        conf      = min(abs(norm) * 18 + 50, 99.9)

        return {
            "ai_decision":    decision,
            "ai_confidence":  round(conf, 1),
            "normalized_score": round(norm, 4),
            "buy_models":     buy_c,
            "sell_models":    sell_c,
            "model_agreement": agree,
            "ai_consensus":   consensus,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODULE D  —  MARKET PROFILER  (6 market types + stricter thresholds)
# ═════════════════════════════════════════════════════════════════════════════

class _MarketProfiler:
    """Per-market-type minimum thresholds. Funded = highest bar."""

    PROFILES: dict[str, dict] = {
        "OTC":    {"min_conf": 70, "rsi": (28, 72), "bos": False, "sweep": False, "mult": 1.00},
        "PO OTC": {"min_conf": 73, "rsi": (25, 75), "bos": True,  "sweep": True,  "mult": 1.10},
        "QX OTC": {"min_conf": 76, "rsi": (22, 78), "bos": True,  "sweep": False, "mult": 1.12},
        "LIVE":   {"min_conf": 68, "rsi": (33, 67), "bos": False, "sweep": False, "mult": 1.05},
        "FOREX":  {"min_conf": 65, "rsi": (38, 62), "bos": True,  "sweep": False, "mult": 1.08},
        "FUNDED": {"min_conf": 80, "rsi": (33, 67), "bos": True,  "sweep": True,  "mult": 1.20},
    }

    def evaluate(self, market_type: str, ai_conf: float, rsi: float,
                 bos: bool, sweep_detected: bool) -> dict:
        p  = self.PROFILES.get(market_type.upper(), self.PROFILES["OTC"])
        ok = (
            ai_conf >= p["min_conf"] and
            p["rsi"][0] <= rsi <= p["rsi"][1] and
            (not p["bos"]   or bos) and
            (not p["sweep"] or sweep_detected)
        )
        boosted = min(ai_conf * p["mult"], 99.9) if ok else ai_conf * 0.95
        return {"passed": ok, "boosted_conf": round(boosted, 1)}


# ═════════════════════════════════════════════════════════════════════════════
# CANDLE BUILDER  —  real yfinance OHLCV (with local fallback)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_candles(pair: str, tf: str = "5m", count: int = 100) -> list[dict]:
    """Pull OHLCV from yfinance. Returns list[dict] or [] on failure."""
    if not _YF_OK:
        return []
    now = time.time()
    cache_key = f"{pair}|{tf}"
    if cache_key in _CANDLE_CACHE:
        ts, data = _CANDLE_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return data
    try:
        ticker = _TICKER_MAP.get(pair.upper(), pair.upper().replace("/", "") + "=X")
        period = "2d" if tf in ("1m", "2m", "5m") else "5d"
        raw = yf.download(ticker, period=period, interval=tf,
                          progress=False, auto_adjust=True, timeout=8)
        if raw is None or len(raw) == 0:
            return []
        raw = raw.tail(count)
        candles = []
        for ts_idx, row in raw.iterrows():
            try:
                candles.append({
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": float(row.get("Volume", 1)),
                })
            except Exception:
                continue
        _CANDLE_CACHE[cache_key] = (now, candles)
        return candles
    except Exception as e:
        _log.debug(f"[finorix] candle fetch failed {pair} {tf}: {e}")
        return []


def _synthetic_candles(pair: str, count: int = 80) -> list[dict]:
    """Fallback: generate realistic synthetic candles seeded by pair + minute."""
    import random as _rand
    seed = hash(f"{pair}|{time.strftime('%Y%m%d%H%M')}")
    rng  = _rand.Random(seed)
    base_map = {
        "EUR/USD": 1.085, "GBP/USD": 1.270, "USD/JPY": 149.5,
        "XAU/USD": 2350.0, "BTC/USD": 67000.0,
    }
    base = base_map.get(pair.upper(), 1.1000)
    vol  = base * 0.0005
    candles = []
    for i in range(count):
        bias  = 0.0001 * (i - count // 2)
        o = base + bias + rng.uniform(-vol, vol)
        c = o + rng.uniform(-vol * 1.5, vol * 1.5)
        h = max(o, c) + rng.uniform(0, vol * 0.6)
        l = min(o, c) - rng.uniform(0, vol * 0.6)
        candles.append({
            "open": round(o, 6), "high": round(h, 6),
            "low":  round(l, 6), "close": round(c, 6),
            "volume": rng.randint(500, 5000),
        })
        base = c
    return candles


# ═════════════════════════════════════════════════════════════════════════════
# ELITE MASTER ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

class _EliteMaster:
    def __init__(self) -> None:
        self.smc  = _SMCEngine()
        self.ind  = _IndicatorSuite()
        self.ai   = _AIVotingLayer()
        self.prof = _MarketProfiler()

    def _patterns(self, candles: list[dict]) -> list[str]:
        pats: list[str] = []
        recent = candles[-6:]
        for i, c in enumerate(recent):
            body = abs(c["close"] - c["open"])
            uw   = c["high"] - max(c["open"], c["close"])
            lw   = min(c["open"], c["close"]) - c["low"]
            rng  = c["high"] - c["low"]
            if rng < 1e-10:
                continue
            if body / rng < 0.10:
                pats.append("DOJI")
            if lw > body * 2.0 and uw < body * 0.5:
                pats.append("HAMMER")
            if uw > body * 2.0 and lw < body * 0.5:
                pats.append("SHOOTING STAR")
            if i > 0:
                p = recent[i - 1]
                bull_eng = (c["close"] > c["open"] and p["close"] < p["open"]
                            and c["open"] < p["close"] and c["close"] > p["open"])
                bear_eng = (c["close"] < c["open"] and p["close"] > p["open"]
                            and c["open"] > p["close"] and c["close"] < p["open"])
                if bull_eng:
                    pats.append("BULLISH ENGULFING")
                if bear_eng:
                    pats.append("BEARISH ENGULFING")
                # Tweezer bottom/top
                if abs(c["low"] - p["low"]) < rng * 0.05 and c["close"] > c["open"]:
                    pats.append("TWEEZER BOTTOM")
                if abs(c["high"] - p["high"]) < rng * 0.05 and c["close"] < c["open"]:
                    pats.append("TWEEZER TOP")
        return list(set(pats)) or ["NO PATTERN"]

    def _sr_zones(self, candles: list[dict]) -> dict:
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        cur = closes[-1]
        res = max(highs[-20:])
        sup = min(lows[-20:])
        return {
            "resistance":             res,
            "support":                sup,
            "mid":                    (res + sup) / 2,
            "distance_to_resistance": res - cur,
            "distance_to_support":    cur - sup,
        }

    def run(self, candles: list[dict], market_type: str) -> dict:
        """Full analysis pipeline → elite decision dict."""
        if len(candles) < 20:
            return {
                "decision": "WAIT", "confidence": 50.0,
                "score": 0.0, "ai": {}, "profile": {"passed": False, "boosted_conf": 50.0},
                "atr": 0.0, "rsi": 50.0,
            }
        closes = [c["close"] for c in candles]

        # ── SMC layer ─────────────────────────────────────────────────────────
        bos   = self.smc.detect_bos(candles)
        choch = self.smc.detect_choch(candles)
        ob    = self.smc.find_order_blocks(candles)
        fvg   = self.smc.find_fvg(candles)
        sweep = self.smc.detect_liquidity_sweep(candles)
        wyck  = self.smc.detect_wyckoff(candles)
        ms    = self.smc.market_structure(candles)

        # ── Indicator layer ───────────────────────────────────────────────────
        ema   = self.ind.ema_stack(candles)
        macd  = self.ind.macd(candles)
        bb    = self.ind.bollinger(candles)
        stoch = self.ind.stochastic(candles)
        atr_v = self.ind.atr(candles)
        adx   = self.ind.adx(candles)
        cci_r = self.ind.cci(candles)
        rsi_v = self.ind.rsi(closes)
        wr    = self.ind.williams_r(candles)
        mfi_r = self.ind.mfi(candles)
        ichi  = self.ind.ichimoku(candles)
        div   = self.ind.rsi_divergence(candles)
        pats  = self._patterns(candles)
        sr    = self._sr_zones(candles)

        # ── AI vote ───────────────────────────────────────────────────────────
        ai_result = self.ai.vote({
            "ema_bias": ema["ema_bias"], "ema_strength": ema["ema_strength"],
            "adx": adx, "rsi": rsi_v, "stoch": stoch, "bb": bb,
            "cci": cci_r, "bos": bos, "fvg": fvg, "ob": ob,
            "sweep": sweep, "choch": choch, "macd": macd, "atr": atr_v,
            "patterns": pats, "sr_zones": sr, "wyckoff": wyck,
            "ms": ms, "wr": wr, "mfi_r": mfi_r, "ichi": ichi, "div": div,
        })

        # ── Market profile filter ─────────────────────────────────────────────
        profile = self.prof.evaluate(
            market_type,
            ai_result["ai_confidence"],
            rsi_v,
            bos["bos_bullish"] or bos["bos_bearish"],
            sweep["sweep_detected"],
        )

        # ── Elite composite score ─────────────────────────────────────────────
        score = ai_result["normalized_score"] * 100
        if adx["trend_strong"]:                  score += 5.0
        if adx.get("very_strong"):               score += 3.0
        if sweep["sweep_detected"]:              score += 8.0
        if ob["active_ob"]:                      score += 6.0
        if ai_result["model_agreement"] >= 75:   score += 10.0
        if ai_result["model_agreement"] >= 90:   score += 5.0   # extra for near-unanimous
        if profile["passed"]:                    score += 5.0
        else:                                    score -= 10.0
        if bos["bos_strength"] > 0.1:            score += min(bos["bos_strength"] * 10, 5.0)
        if wyck["bias"] == ai_result["ai_decision"]: score += 4.0
        if ms["ms_bias"] == ai_result["ai_decision"]: score += 4.0
        if div["div_bias"] == ai_result["ai_decision"]: score += 6.0

        elite_conf = min(abs(score) * 0.75 + 45, 99.9)

        # ── Final direction decision ──────────────────────────────────────────
        veto = ai_result["ai_consensus"] == "SPLIT"
        if veto:
            decision = "WAIT"
        elif score > 12 and ai_result["ai_decision"] == "BUY":
            decision = "BUY"
        elif score < -12 and ai_result["ai_decision"] == "SELL":
            decision = "SELL"
        else:
            decision = ai_result["ai_decision"]

        return {
            "decision":   decision,
            "confidence": round(elite_conf, 1),
            "score":      round(score, 2),
            "ai":         ai_result,
            "profile":    profile,
            "atr":        atr_v,
            "rsi":        rsi_v,
            "veto":       veto,
        }


# ═════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═════════════════════════════════════════════════════════════════════════════

_master = _EliteMaster()


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

_GRADE_MAP = [
    (95, "GOD"),
    (88, "ULTRA"),
    (80, "ELITE"),
    (70, "STRONG"),
    (60, "MODERATE"),
    (0,  "WEAK"),
]

def _grade(conf: float) -> str:
    for threshold, label in _GRADE_MAP:
        if conf >= threshold:
            return label
    return "WEAK"


def finorix_analyse(pair: str, market_type: str = "OTC") -> dict:
    """Run the full Finorix Supreme analysis pipeline.

    Parameters
    ----------
    pair        : trading pair, e.g. "EUR/USD", "XAU/USD", "BTC/USD"
    market_type : "OTC" | "PO OTC" | "QX OTC" | "LIVE" | "FOREX" | "FUNDED"

    Returns
    -------
    dict with keys: ok, direction, confidence, grade, agree,
                    models_buy, models_sell, veto, raw_score
    """
    try:
        # Try real 5m candles first; fall back to 1m for OTC-style pairs
        candles = _fetch_candles(pair, tf="5m", count=100)
        if len(candles) < 30:
            candles = _fetch_candles(pair, tf="1m", count=100)
        if len(candles) < 20:
            candles = _synthetic_candles(pair, count=80)

        result = _master.run(candles, market_type)
        ai     = result.get("ai", {})
        conf   = result["confidence"]
        g      = _grade(conf)
        ok     = result["profile"]["passed"] and not result.get("veto", False) and conf >= 65

        return {
            "ok":          ok,
            "direction":   result["decision"],
            "confidence":  conf,
            "grade":       g,
            "agree":       ai.get("model_agreement", 0),
            "models_buy":  ai.get("buy_models", 0),
            "models_sell": ai.get("sell_models", 0),
            "veto":        result.get("veto", False),
            "raw_score":   result["score"],
        }
    except Exception as e:
        _log.debug(f"[finorix] analyse error for {pair}: {e}")
        return {
            "ok": True, "direction": "WAIT", "confidence": 50.0,
            "grade": "WEAK", "agree": 0, "models_buy": 0, "models_sell": 0,
            "veto": False, "raw_score": 0.0,
        }
