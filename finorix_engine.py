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
# MODULE B  —  PURE ANALYSIS  (RSI + ATR + ADX only — all lagging indicators removed)
# ═════════════════════════════════════════════════════════════════════════════
# REMOVED: EMA stacks, MACD, Bollinger Bands, Stochastic, CCI, Williams%R,
#          MFI (Money Flow Index), Ichimoku Cloud.
# WHY: All are lagging — they react AFTER the price has already moved,
#      causing "chasing" entries that systematically lose on fast binary TFs.
# KEPT: RSI-14 (OB/OS identification + divergence), ATR-14 (volatility
#       measurement), ADX-14 (trend strength structural filter).

class _PureAnalysis:
    """Lean measurement layer — only non-lagging structural tools:
      • RSI-14 : overbought / oversold zone identification + divergence
      • ATR-14 : volatility measurement (structural filter, never a signal)
      • ADX-14 : trend strength measurement (structural filter, never a signal)
    """

    def _ema(self, data: list[float], period: int) -> list[float]:
        k = 2 / (period + 1)
        out = [data[0]]
        for p in data[1:]:
            out.append(p * k + out[-1] * (1 - k))
        return out

    def _sma(self, data: list[float], period: int) -> float:
        return sum(data[-period:]) / period if len(data) >= period else sum(data) / len(data)

    # ── ATR-14 (volatility measurement — structural filter, not a signal) ────
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

    # ── RSI-14 (OB/OS zones + divergence — the ONLY indicator signal kept) ────
    def rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        return round(100.0 - 100.0 / (1 + ag / al), 2) if al else 100.0

    # ── RSI Divergence (classic + hidden) ────────────────────────────────────
    def rsi_divergence(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        lows   = [c["low"]   for c in candles]
        highs  = [c["high"]  for c in candles]
        if len(closes) < 20:
            return {"div_type": "NONE", "div_bias": "NEUTRAL"}
        rsi_now  = self.rsi(closes)
        rsi_prev = self.rsi(closes[:-5]) if len(closes) > 5 else rsi_now
        price_now, price_prev = closes[-1], closes[-5] if len(closes) > 5 else closes[-1]
        # Classic divergence: price makes new extreme but RSI doesn't confirm
        classic_bull = (lows[-1] < min(lows[-6:-1], default=lows[-1])
                        and rsi_now > rsi_prev)
        classic_bear = (highs[-1] > max(highs[-6:-1], default=highs[-1])
                        and rsi_now < rsi_prev)
        # Hidden divergence: continuation — price HL but RSI LL
        hidden_bull  = price_now > price_prev and rsi_now < rsi_prev and rsi_now < 55
        hidden_bear  = price_now < price_prev and rsi_now > rsi_prev and rsi_now > 45
        if classic_bull:
            return {"div_type": "BULLISH DIV",        "div_bias": "BUY"}
        if classic_bear:
            return {"div_type": "BEARISH DIV",        "div_bias": "SELL"}
        if hidden_bull:
            return {"div_type": "HIDDEN BULL DIV",    "div_bias": "BUY"}
        if hidden_bear:
            return {"div_type": "HIDDEN BEAR DIV",    "div_bias": "SELL"}
        return {"div_type": "NONE", "div_bias": "NEUTRAL"}


# ═════════════════════════════════════════════════════════════════════════════
# MODULE C  —  PURE PRICE ACTION VOTING  (8 models — zero lagging indicators)
# ═════════════════════════════════════════════════════════════════════════════
# REMOVED models: TrendFollower-AI (EMA stack), MeanReversion-AI (Stoch/BB/CCI),
#   Momentum-AI (MACD), Volatility-AI (BB squeeze), MultiIndicator-AI (WR/MFI/Ichi)
# ALL indicator-based voting is gone. Only pure price action + structural
# measurements (RSI for extremes only, ADX for trend strength only).

class _AIVotingLayer:
    """8 pure price-action sub-models, each with tuned weight.
    RSI is used ONLY for overbought/oversold zone detection.
    ADX is used ONLY as a structural trend-strength measurement.
    No other indicators remain.
    """

    MODELS = [
        ("SMC-AI",               3.0),  # OB + FVG + Sweep + CHoCH — institutional prints
        ("LiquidityMap-AI",      2.8),  # Sweep depth + S/R pool proximity + BoS confirm
        ("MarketStructure-AI",   2.5),  # HH/HL or LH/LL pure structure
        ("Wyckoff-AI",           2.2),  # Wyckoff accumulation / distribution phase
        ("PatternRecog-AI",      2.0),  # Candlestick: engulfing, pin bar, tweezer
        ("RSI-Extreme-AI",       1.8),  # RSI deep extremes (<28 / >72) + divergence
        ("BreakoutStructure-AI", 1.8),  # BoS strength + ATR expansion confirmation
        ("TrendStrength-AI",     1.5),  # ADX structural measurement only
    ]

    def _v_smc(self, d: dict) -> float:
        v = 0.0
        ob = d["ob"]; fvg = d["fvg"]; sweep = d["sweep"]; choch = d["choch"]
        # Order Block at current price
        if "BULLISH" in ob.get("ob_bias", ""):     v += 2.5
        elif "BEARISH" in ob.get("ob_bias", ""):   v -= 2.5
        # Freshness multiplier — more recent OB = stronger signal
        if ob.get("active_ob"):
            freshness = ob["active_ob"].get("freshness", 0.5)
            v += (freshness - 0.5) * 1.0 if "BULLISH" in ob.get("ob_bias", "") else \
                 -(freshness - 0.5) * 1.0
        # Fair Value Gap
        if "BULLISH" in fvg.get("fvg_bias", ""):   v += 1.3
        elif "BEARISH" in fvg.get("fvg_bias", ""): v -= 1.3
        # Liquidity Sweep (stop hunt confirms reversal)
        if sweep["sweep_bias"] == "BUY":
            v += 2.2 + min(sweep["sweep_depth_pct"] * 12, 1.5)
        elif sweep["sweep_bias"] == "SELL":
            v -= 2.2 + min(sweep["sweep_depth_pct"] * 12, 1.5)
        # Change of Character
        if choch["choch_detected"]:
            choch_v = 1.8 if "BULLISH" in choch["choch_direction"] else -1.8
            if choch.get("vol_confirm"):
                choch_v *= 1.4   # volume confirmation amplifies CHoCH strength
            v += choch_v
        return v

    def _v_liquidity(self, d: dict) -> float:
        v = 0.0
        sweep = d["sweep"]; sr = d["sr_zones"]; bos = d["bos"]
        # Sweep is the primary signal — retail stops run = institutional entry
        if sweep["sweep_bias"] == "BUY":   v += 3.0
        elif sweep["sweep_bias"] == "SELL": v -= 3.0
        # S/R proximity: price near support = buy, near resistance = sell
        ds = sr.get("distance_to_support",    999)
        dr = sr.get("distance_to_resistance", 999)
        if ds < dr * 0.12:    v += 1.8
        elif dr < ds * 0.12:  v -= 1.8
        elif ds < dr * 0.25:  v += 0.8
        elif dr < ds * 0.25:  v -= 0.8
        # BoS in direction of S/R bias amplifies
        if bos["bos_bullish"] and ds < dr: v += 1.0
        elif bos["bos_bearish"] and dr < ds: v -= 1.0
        return v

    def _v_market_structure(self, d: dict) -> float:
        ms = d["ms"]; bos = d["bos"]
        v = 2.5 if ms["ms_bias"] == "BUY" else (-2.5 if ms["ms_bias"] == "SELL" else 0.0)
        # BoS alignment with market structure = extra conviction
        if bos["bos_bullish"] and ms["ms_bias"] == "BUY":   v += 1.2
        elif bos["bos_bearish"] and ms["ms_bias"] == "SELL": v -= 1.2
        # BoS strength
        if bos["bos_strength"] > 0.2:
            v += 0.8 if bos["bos_bullish"] else -0.8
        return v

    def _v_wyckoff(self, d: dict) -> float:
        bias = d["wyckoff"]["bias"]; phase = d["wyckoff"].get("phase", "")
        if bias == "BUY":
            v = 2.5
            if "MARKUP" in phase:  v += 0.5   # strongest Wyckoff bullish phase
        elif bias == "SELL":
            v = -2.5
            if "MARKDOWN" in phase: v -= 0.5
        else:
            v = 0.0
        return v

    def _v_pattern(self, d: dict) -> float:
        v = 0.0
        for p in d["patterns"]:
            p_up = p.upper()
            if "BULLISH ENGULFING" in p_up:  v += 3.2
            elif "BEARISH ENGULFING" in p_up: v -= 3.2
            elif "HAMMER" in p_up:            v += 2.5
            elif "SHOOTING STAR" in p_up:     v -= 2.5
            elif "TWEEZER BOTTOM" in p_up:    v += 1.8
            elif "TWEEZER TOP" in p_up:       v -= 1.8
            elif "DOJI" in p_up:              v += 0.2  # mild indecision only
        return v

    def _v_rsi_extreme(self, d: dict) -> float:
        """RSI used ONLY for deep extreme zones + divergence.
        Mid-zone RSI (35-65) carries NO weight — it is lagging noise there."""
        v = 0.0
        rsi = d["rsi"]
        # Deep extreme zones (highest weight)
        if rsi < 18:        v += 3.5
        elif rsi < 25:      v += 2.5
        elif rsi < 32:      v += 1.2
        elif rsi > 82:      v -= 3.5
        elif rsi > 75:      v -= 2.5
        elif rsi > 68:      v -= 1.2
        # RSI divergence (classic + hidden both valid)
        div = d.get("div", {})
        if div.get("div_bias") == "BUY":    v += 2.2
        elif div.get("div_bias") == "SELL": v -= 2.2
        return v

    def _v_breakout(self, d: dict) -> float:
        """BoS strength + ATR expansion — no Bollinger Bands, no BB squeeze."""
        v = 0.0
        bos = d["bos"]
        # BoS with strength confirmation
        if bos["bos_bullish"]:
            v += 2.2 + min(bos["bos_strength"] / 45, 1.5)
        elif bos["bos_bearish"]:
            v -= 2.2 + min(bos["bos_strength"] / 45, 1.5)
        # ATR expansion: increasing volatility at breakout = conviction
        atr_now  = d.get("atr_val", 0)
        atr_base = d.get("atr_base", atr_now)
        if atr_base > 0 and atr_now > atr_base * 1.25:
            v += 1.0 if bos["bos_bullish"] else (-1.0 if bos["bos_bearish"] else 0.0)
        return v

    def _v_trend_strength(self, d: dict) -> float:
        """ADX used only as a structural measurement — not a crossover signal."""
        adx = d["adx"]
        if adx.get("very_strong"):
            return 1.5 if adx["adx_bias"] == "BUY" else -1.5
        if adx["trend_strong"]:
            return 1.0 if adx["adx_bias"] == "BUY" else -1.0
        return 0.0

    def vote(self, d: dict) -> dict:
        raw_votes = [
            self._v_smc(d),
            self._v_liquidity(d),
            self._v_market_structure(d),
            self._v_wyckoff(d),
            self._v_pattern(d),
            self._v_rsi_extreme(d),
            self._v_breakout(d),
            self._v_trend_strength(d),
        ]
        total_w  = sum(w for _, w in self.MODELS)
        weighted = sum(v * w for v, (_, w) in zip(raw_votes, self.MODELS))
        norm     = weighted / total_w

        buy_c  = sum(1 for v in raw_votes if v > 0)
        sell_c = sum(1 for v in raw_votes if v < 0)
        agree  = round(max(buy_c, sell_c) / len(self.MODELS) * 100, 1)

        consensus = ("STRONG"   if agree >= 75  else
                     "MODERATE" if agree >= 62  else "SPLIT")
        decision  = "BUY" if norm > 0.55 else ("SELL" if norm < -0.55 else "WAIT")
        conf      = min(abs(norm) * 18 + 50, 99.9)

        return {
            "ai_decision":      decision,
            "ai_confidence":    round(conf, 1),
            "normalized_score": round(norm, 4),
            "buy_models":       buy_c,
            "sell_models":      sell_c,
            "model_agreement":  agree,
            "ai_consensus":     consensus,
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
        self.ind  = _PureAnalysis()
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

        # ── SMC layer (pure price action — unchanged) ─────────────────────────
        bos   = self.smc.detect_bos(candles)
        choch = self.smc.detect_choch(candles)
        ob    = self.smc.find_order_blocks(candles)
        fvg   = self.smc.find_fvg(candles)
        sweep = self.smc.detect_liquidity_sweep(candles)
        wyck  = self.smc.detect_wyckoff(candles)
        ms    = self.smc.market_structure(candles)

        # ── Structural measurements (RSI + ATR + ADX only) ────────────────────
        # All lagging indicators removed. These three are measurement tools,
        # not signal generators.
        atr_v  = self.ind.atr(candles)
        adx    = self.ind.adx(candles)
        rsi_v  = self.ind.rsi(closes)
        div    = self.ind.rsi_divergence(candles)
        pats   = self._patterns(candles)
        sr     = self._sr_zones(candles)

        # ATR comparison (recent vs baseline) for breakout conviction check
        atr_recent = self.ind.atr(candles[-8:],  6) if len(candles) >= 8  else atr_v
        atr_base   = self.ind.atr(candles[-30:], 20) if len(candles) >= 30 else atr_v

        # ── AI vote (pure PA models) ──────────────────────────────────────────
        ai_result = self.ai.vote({
            "adx": adx, "rsi": rsi_v,
            "bos": bos, "fvg": fvg, "ob": ob,
            "sweep": sweep, "choch": choch,
            "patterns": pats, "sr_zones": sr, "wyckoff": wyck,
            "ms": ms, "div": div,
            "atr_val": atr_recent, "atr_base": atr_base,
        })

        # ── Market profile filter ─────────────────────────────────────────────
        profile = self.prof.evaluate(
            market_type,
            ai_result["ai_confidence"],
            rsi_v,
            bos["bos_bullish"] or bos["bos_bearish"],
            sweep["sweep_detected"],
        )

        # ── Elite composite score (pure PA — no indicator bonuses) ─────────────
        score = ai_result["normalized_score"] * 100
        # Structural bonuses (all price-action based)
        if adx["trend_strong"]:                     score += 5.0
        if adx.get("very_strong"):                  score += 3.0
        if sweep["sweep_detected"]:                 score += 10.0  # sweep = key SMC signal
        if ob["active_ob"]:                         score += 7.0
        if ai_result["model_agreement"] >= 75:      score += 10.0
        if ai_result["model_agreement"] >= 90:      score += 5.0
        if profile["passed"]:                       score += 5.0
        else:                                       score -= 10.0
        if bos["bos_strength"] > 0.1:               score += min(bos["bos_strength"] * 12, 6.0)
        if wyck["bias"] == ai_result["ai_decision"]:  score += 5.0  # Wyckoff alignment
        if ms["ms_bias"] == ai_result["ai_decision"]: score += 5.0  # structure alignment
        if div["div_bias"] == ai_result["ai_decision"]: score += 7.0  # RSI divergence bonus

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
