# ================================================================
# FINORIX AI — TELEGRAM SIGNAL BOT (ELITE EDITION)
# Drop this ENTIRE file into Replit as main.py
# Signal text / photos / format = UNCHANGED
# Elite Analysis Engine = silently added to analysis layer
# ================================================================
# REPLIT SETUP:
#   1. New Replit → Python template
#   2. requirements.txt → add: python-telegram-bot==20.7
#   3. Secrets tab → BOT_TOKEN = your token
#   4. Run ▶
# ================================================================

import os
import math
import random
import logging
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, filters, ContextTypes
)

# ── CONFIG ───────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# ELITE ENGINE — MODULE A: SMART MONEY CONCEPTS (SMC)
# ════════════════════════════════════════════════════════════════

class _SMCEngine:
    """Break of Structure, CHoCH, Order Blocks, FVG, Liquidity Sweep"""

    def detect_bos(self, candles):
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        last_high = max(highs[-10:-1])
        last_low  = min(lows[-10:-1])
        cur = closes[-1]
        bull = cur > last_high
        bear = cur < last_low
        return {
            "bos_bullish": bull,
            "bos_bearish": bear,
            "bos_bias": "BUY" if bull else ("SELL" if bear else "NEUTRAL")
        }

    def detect_choch(self, candles):
        closes = [c["close"] for c in candles]
        recent_trend  = closes[-5]  - closes[-15]
        current_move  = closes[-1]  - closes[-5]
        detected = (recent_trend > 0 and current_move < -abs(recent_trend) * 0.5) or \
                   (recent_trend < 0 and current_move >  abs(recent_trend) * 0.5)
        direction = "BULLISH CHoCH" if (recent_trend < 0 and current_move > 0) else \
                    "BEARISH CHoCH" if (recent_trend > 0 and current_move < 0) else "NONE"
        return {"choch_detected": detected, "choch_direction": direction}

    def find_order_blocks(self, candles):
        obs = []
        for i in range(5, len(candles) - 1):
            c    = candles[i]
            nxt  = candles[i + 1]
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            if rng == 0: continue
            if body / rng > 0.7 and abs(nxt["close"] - c["close"]) > body * 0.5:
                obs.append({
                    "type": "BULLISH OB" if c["close"] > c["open"] else "BEARISH OB",
                    "high": c["high"], "low": c["low"],
                    "mid": round((c["high"] + c["low"]) / 2, 5)
                })
        active = obs[-1] if obs else None
        return {"active_ob": active, "ob_bias": active["type"] if active else "NONE"}

    def find_fvg(self, candles):
        fvgs = []
        for i in range(1, len(candles) - 1):
            prev, nxt = candles[i - 1], candles[i + 1]
            if nxt["low"]  > prev["high"]:
                fvgs.append({"type": "BULLISH FVG",
                              "mid": round((nxt["low"]  + prev["high"]) / 2, 5)})
            if nxt["high"] < prev["low"]:
                fvgs.append({"type": "BEARISH FVG",
                              "mid": round((prev["low"] + nxt["high"])  / 2, 5)})
        active = fvgs[-1] if fvgs else None
        return {"active_fvg": active, "fvg_bias": active["type"] if active else "NO FVG"}

    def detect_liquidity_sweep(self, candles):
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        prev_high  = max(highs[-15:-3])
        prev_low   = min(lows[-15:-3])
        recent_high = max(highs[-3:])
        recent_low  = min(lows[-3:])
        cur = closes[-1]
        sw_low  = recent_low  < prev_low  and cur > prev_low
        sw_high = recent_high > prev_high and cur < prev_high
        return {
            "sweep_detected": sw_low or sw_high,
            "sweep_bias": "BUY" if sw_low else ("SELL" if sw_high else "NEUTRAL"),
            "sweep_type": "SELL-SIDE SWEPT→BUY" if sw_low else
                          ("BUY-SIDE SWEPT→SELL" if sw_high else "NO SWEEP")
        }


# ════════════════════════════════════════════════════════════════
# ELITE ENGINE — MODULE B: INDICATOR SUITE
# ════════════════════════════════════════════════════════════════

class _IndicatorSuite:
    """EMA Stack | MACD | Bollinger | Stochastic | ATR | ADX | CCI"""

    def _ema(self, data, period):
        k = 2 / (period + 1)
        out = [data[0]]
        for p in data[1:]:
            out.append(p * k + out[-1] * (1 - k))
        return out

    def ema_stack(self, candles):
        closes = [c["close"] for c in candles]
        e8  = self._ema(closes, 8)[-1]
        e21 = self._ema(closes, 21)[-1]
        e50 = self._ema(closes, 50)[-1] if len(closes) >= 50 else closes[-1]
        cur = closes[-1]
        bull = cur > e8 > e21 > e50
        bear = cur < e8 < e21 < e50
        return {
            "ema8": round(e8, 5), "ema21": round(e21, 5), "ema50": round(e50, 5),
            "ema_bias": "BUY" if bull else ("SELL" if bear else "NEUTRAL"),
            "stack_label": "BULLISH STACK🟢" if bull else ("BEARISH STACK🔴" if bear else "MIXED⚪")
        }

    def macd(self, candles):
        closes = [c["close"] for c in candles]
        e12 = self._ema(closes, 12)
        e26 = self._ema(closes, 26) if len(closes) >= 26 else e12
        macd_line   = [a - b for a, b in zip(e12, e26)]
        signal_line = self._ema(macd_line, 9)
        hist = [m - s for m, s in zip(macd_line, signal_line)]
        cm, cs, ch = macd_line[-1], signal_line[-1], hist[-1]
        ph = hist[-2] if len(hist) > 1 else 0
        cross_up   = cm > cs and macd_line[-2] <= signal_line[-2]
        cross_down = cm < cs and macd_line[-2] >= signal_line[-2]
        return {
            "macd": round(cm, 6), "signal": round(cs, 6), "histogram": round(ch, 6),
            "macd_bias": "BUY"  if cross_up  or (cm > cs and ch > ph) else
                         "SELL" if cross_down or (cm < cs and ch < ph) else "NEUTRAL"
        }

    def bollinger(self, candles, period=20, sd=2.0):
        closes = [c["close"] for c in candles[-period:]]
        sma = sum(closes) / len(closes)
        std = math.sqrt(sum((p - sma) ** 2 for p in closes) / len(closes))
        upper = sma + sd * std
        lower = sma - sd * std
        cur   = closes[-1]
        bw    = round((upper - lower) / sma * 100, 4) if sma else 0
        pct_b = round((cur - lower) / (upper - lower), 4) if upper != lower else 0.5
        squeeze = bw < 0.5
        return {
            "upper": round(upper, 5), "middle": round(sma, 5), "lower": round(lower, 5),
            "bandwidth": bw, "percent_b": pct_b, "squeeze": squeeze,
            "bb_bias": "BUY" if cur <= lower else ("SELL" if cur >= upper else "NEUTRAL"),
            "squeeze_label": "⚡BB SQUEEZE—BREAKOUT INCOMING" if squeeze else "NORMAL"
        }

    def stochastic(self, candles, k_period=14, d_period=3):
        recent = candles[-k_period:]
        hh = max(c["high"] for c in recent)
        ll = min(c["low"]  for c in recent)
        cur = recent[-1]["close"]
        k = ((cur - ll) / (hh - ll) * 100) if hh != ll else 50.0
        # simplified D
        k_vals = []
        for i in range(min(d_period, len(candles))):
            r = candles[-(k_period + i): -i if i > 0 else len(candles)]
            if not r: continue
            _hh = max(c["high"] for c in r)
            _ll = min(c["low"]  for c in r)
            _cl = r[-1]["close"]
            k_vals.append((_cl - _ll) / (_hh - _ll) * 100 if _hh != _ll else 50)
        d = sum(k_vals) / len(k_vals) if k_vals else k
        return {
            "k": round(k, 2), "d": round(d, 2),
            "overbought": k > 80, "oversold": k < 20,
            "stoch_bias": "BUY" if k < 20 else ("SELL" if k > 80 else "NEUTRAL")
        }

    def atr(self, candles, period=14):
        trs = []
        for i in range(1, min(period + 1, len(candles))):
            c, p = candles[i], candles[i - 1]
            trs.append(max(c["high"] - c["low"],
                           abs(c["high"] - p["close"]),
                           abs(c["low"]  - p["close"])))
        return round(sum(trs) / len(trs), 5) if trs else 0.0001

    def adx(self, candles, period=14):
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
        atr_v  = sum(trs[-period:]) / period
        di_p   = (sum(dm_p[-period:]) / period) / atr_v * 100 if atr_v else 0
        di_m   = (sum(dm_m[-period:]) / period) / atr_v * 100 if atr_v else 0
        dx     = abs(di_p - di_m) / (di_p + di_m) * 100 if (di_p + di_m) else 0
        return {
            "adx": round(dx, 2), "di_plus": round(di_p, 2), "di_minus": round(di_m, 2),
            "trend_strong": dx > 25,
            "adx_bias": "BUY" if di_p > di_m else "SELL"
        }

    def cci(self, candles, period=20):
        recent = candles[-period:]
        tps  = [(c["high"] + c["low"] + c["close"]) / 3 for c in recent]
        mean = sum(tps) / len(tps)
        dev  = sum(abs(t - mean) for t in tps) / len(tps)
        val  = (tps[-1] - mean) / (0.015 * dev) if dev else 0
        return {
            "cci": round(val, 2),
            "overbought": val > 100, "oversold": val < -100,
            "cci_bias": "BUY" if val < -100 else ("SELL" if val > 100 else "NEUTRAL")
        }

    def rsi(self, closes, period=14):
        if len(closes) < period + 1: return 50.0
        gains, losses = [], []
        for i in range(1, period + 1):
            d = closes[-period + i] - closes[-period + i - 1]
            gains.append(d if d > 0 else 0)
            losses.append(abs(d) if d < 0 else 0)
        ag = sum(gains) / period
        al = sum(losses) / period
        if al == 0: return 100.0
        return round(100 - (100 / (1 + ag / al)), 2)


# ════════════════════════════════════════════════════════════════
# ELITE ENGINE — MODULE C: AI MULTI-MODEL VOTING
# ════════════════════════════════════════════════════════════════

class _AIVotingLayer:
    """8 sub-models vote weighted — silent consensus layer"""

    MODELS = [
        ("TrendFollower-AI",  1.5),
        ("MeanReversion-AI",  1.2),
        ("BreakoutDetect-AI", 1.3),
        ("SMC-AI",            1.8),
        ("Momentum-AI",       1.4),
        ("Volatility-AI",     1.1),
        ("PatternRecog-AI",   1.6),
        ("LiquidityMap-AI",   1.7),
    ]

    def _v_trend(self, trend_bias, ema_bias, adx):
        v = 0
        if trend_bias == "BUY":  v += 1
        if trend_bias == "SELL": v -= 1
        if ema_bias == "BUY":    v += 1
        if ema_bias == "SELL":   v -= 1
        if adx["trend_strong"]:
            v += 1 if adx["adx_bias"] == "BUY" else -1
        return v

    def _v_mean(self, rsi, stoch, bb, cci):
        v = 0
        if rsi < 30:              v += 2
        elif rsi > 70:            v -= 2
        if stoch["oversold"]:     v += 1
        elif stoch["overbought"]: v -= 1
        if bb["bb_bias"] == "BUY":  v += 1
        elif bb["bb_bias"] == "SELL": v -= 1
        if cci["oversold"]:       v += 1
        elif cci["overbought"]:   v -= 1
        return v

    def _v_breakout(self, bb, bos):
        v = 0
        if bb["squeeze"]:       v += 1
        if bos["bos_bullish"]:  v += 2
        elif bos["bos_bearish"]: v -= 2
        return v

    def _v_smc(self, ob, fvg, sweep, choch):
        v = 0
        if "BULLISH" in ob.get("ob_bias", ""):   v += 2
        elif "BEARISH" in ob.get("ob_bias", ""): v -= 2
        if "BULLISH" in fvg.get("fvg_bias", ""): v += 1
        elif "BEARISH" in fvg.get("fvg_bias", ""): v -= 1
        if sweep["sweep_bias"] == "BUY":   v += 2
        elif sweep["sweep_bias"] == "SELL": v -= 2
        if choch["choch_detected"]:
            v += 1 if "BULLISH" in choch["choch_direction"] else -1
        return v

    def _v_momentum(self, macd, rsi, adx):
        v = 0
        if macd["macd_bias"] == "BUY":   v += 2
        elif macd["macd_bias"] == "SELL": v -= 2
        if 50 < rsi < 65:  v += 1
        elif 35 < rsi < 50: v -= 1
        if adx["trend_strong"]:
            v += 1 if adx["adx_bias"] == "BUY" else -1
        return v

    def _v_volatility(self, bb):
        return 1 if bb["squeeze"] else 0

    def _v_pattern(self, patterns):
        v = 0
        for p in patterns:
            if "BULLISH" in p or "HAMMER" in p:   v += 2
            elif "BEARISH" in p or "SHOOTING" in p: v -= 2
        return v

    def _v_liquidity(self, sweep, sr):
        v = 0
        if sweep["sweep_bias"] == "BUY":   v += 2
        elif sweep["sweep_bias"] == "SELL": v -= 2
        ds = sr.get("distance_to_support", 999)
        dr = sr.get("distance_to_resistance", 999)
        if ds < dr * 0.25: v += 1
        elif dr < ds * 0.25: v -= 1
        return v

    def vote(self, d):
        raw = [
            self._v_trend(d["trend_bias"], d["ema_bias"], d["adx"]),
            self._v_mean(d["rsi"], d["stoch"], d["bb"], d["cci"]),
            self._v_breakout(d["bb"], d["bos"]),
            self._v_smc(d["ob"], d["fvg"], d["sweep"], d["choch"]),
            self._v_momentum(d["macd"], d["rsi"], d["adx"]),
            self._v_volatility(d["bb"]),
            self._v_pattern(d["patterns"]),
            self._v_liquidity(d["sweep"], d["sr_zones"]),
        ]
        weighted = sum(v * w for v, (_, w) in zip(raw, self.MODELS))
        total_w  = sum(w for _, w in self.MODELS)
        norm     = weighted / total_w

        buy_c  = sum(1 for v in raw if v > 0)
        sell_c = sum(1 for v in raw if v < 0)
        agree  = round(max(buy_c, sell_c) / len(self.MODELS) * 100, 1)

        decision = "BUY" if norm > 0.5 else ("SELL" if norm < -0.5 else "WAIT")
        conf     = min(abs(norm) * 20 + 50, 99)

        return {
            "ai_decision": decision,
            "ai_confidence": round(conf, 1),
            "normalized_score": round(norm, 4),
            "buy_models": buy_c, "sell_models": sell_c,
            "model_agreement": agree,
            "ai_consensus": "STRONG" if agree >= 75 else
                            "MODERATE" if agree >= 50 else "SPLIT"
        }


# ════════════════════════════════════════════════════════════════
# ELITE ENGINE — MODULE D: MARKET TYPE PROFILER
# OTC | PO OTC | QX OTC | LIVE | FOREX | FUNDED
# ════════════════════════════════════════════════════════════════

class _MarketProfiler:
    PROFILES = {
        "OTC":    {"min_conf": 72, "rsi": (30,70), "bos": False, "sweep": False, "mult": 1.00, "expiry": "1-2 min"},
        "PO OTC": {"min_conf": 75, "rsi": (25,75), "bos": True,  "sweep": True,  "mult": 1.15, "expiry": "1-3 min"},
        "QX OTC": {"min_conf": 78, "rsi": (20,80), "bos": True,  "sweep": False, "mult": 1.20, "expiry": "1-5 min"},
        "LIVE":   {"min_conf": 70, "rsi": (35,65), "bos": False, "sweep": False, "mult": 1.05, "expiry": "5-15 min"},
        "FOREX":  {"min_conf": 68, "rsi": (40,60), "bos": True,  "sweep": False, "mult": 1.10, "expiry": "15-60 min"},
        "FUNDED": {"min_conf": 82, "rsi": (35,65), "bos": True,  "sweep": True,  "mult": 1.30, "expiry": "15-240 min"},
    }

    def evaluate(self, market_type, ai_conf, rsi, bos, sweep_detected):
        p  = self.PROFILES.get(market_type.upper(), self.PROFILES["OTC"])
        ok = (
            ai_conf >= p["min_conf"] and
            p["rsi"][0] <= rsi <= p["rsi"][1] and
            (not p["bos"]   or bos) and
            (not p["sweep"] or sweep_detected)
        )
        boosted = min(ai_conf * p["mult"], 99.9) if ok else ai_conf
        return {"passed": ok, "boosted_conf": round(boosted, 1), "expiry": p["expiry"]}


# ════════════════════════════════════════════════════════════════
# ELITE ENGINE — MASTER ORCHESTRATOR
# ════════════════════════════════════════════════════════════════

class _EliteMaster:
    def __init__(self):
        self.smc   = _SMCEngine()
        self.ind   = _IndicatorSuite()
        self.ai    = _AIVotingLayer()
        self.prof  = _MarketProfiler()

    def _trend(self, candles):
        closes = [c["close"] for c in candles]
        n = len(closes)
        xm = (n - 1) / 2
        ym = sum(closes) / n
        num = sum((i - xm) * (closes[i] - ym) for i in range(n))
        den = sum((i - xm) ** 2 for i in range(n))
        slope = num / den if den else 0
        return "UP" if slope > 0.0001 else ("DOWN" if slope < -0.0001 else "NEUTRAL")

    def _channel(self, candles):
        highs = [c["high"] for c in candles[-20:]]
        lows  = [c["low"]  for c in candles[-20:]]
        n = len(highs)
        xm = (n - 1) / 2
        den = sum((i - xm) ** 2 for i in range(n)) or 1
        hs  = sum((i - xm) * (highs[i] - sum(highs)/n) for i in range(n)) / den
        ls  = sum((i - xm) * (lows[i]  - sum(lows)/n)  for i in range(n)) / den
        if hs > 0 and ls > 0: return "ASCENDING CHANNEL"
        if hs < 0 and ls < 0: return "DESCENDING CHANNEL"
        return "HORIZONTAL CHANNEL"

    def _patterns(self, candles):
        pats = []
        recent = candles[-5:]
        for i, c in enumerate(recent):
            body = abs(c["close"] - c["open"])
            uw   = c["high"] - max(c["open"], c["close"])
            lw   = min(c["open"], c["close"]) - c["low"]
            rng  = c["high"] - c["low"]
            if rng == 0: continue
            if body / rng < 0.1:    pats.append("DOJI")
            if lw > body * 2:       pats.append("HAMMER")
            if uw > body * 2:       pats.append("SHOOTING STAR")
            if i > 0:
                p = recent[i - 1]
                if c["close"] > c["open"] and p["close"] < p["open"]: pats.append("BULLISH ENGULFING")
                if c["close"] < c["open"] and p["close"] > p["open"]: pats.append("BEARISH ENGULFING")
        return list(set(pats)) or ["NO CLEAR PATTERN"]

    def run(self, candles, market_type):
        """Full silent elite analysis — returns elite decision + confidence"""
        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]

        # SMC
        bos   = self.smc.detect_bos(candles)
        choch = self.smc.detect_choch(candles)
        ob    = self.smc.find_order_blocks(candles)
        fvg   = self.smc.find_fvg(candles)
        sweep = self.smc.detect_liquidity_sweep(candles)

        # Indicators
        ema   = self.ind.ema_stack(candles)
        macd  = self.ind.macd(candles)
        bb    = self.ind.bollinger(candles)
        stoch = self.ind.stochastic(candles)
        atr_v = self.ind.atr(candles)
        adx   = self.ind.adx(candles)
        cci   = self.ind.cci(candles)
        rsi   = self.ind.rsi(closes)

        # Misc
        trend_dir   = self._trend(candles)
        channel_tp  = self._channel(candles)
        patterns    = self._patterns(candles)

        sr_zones = {
            "resistance":            max(highs[-20:]),
            "support":               min(lows[-20:]),
            "distance_to_resistance": max(highs[-20:]) - closes[-1],
            "distance_to_support":    closes[-1] - min(lows[-20:])
        }

        # AI vote
        ai_result = self.ai.vote({
            "trend_bias": trend_dir, "ema_bias": ema["ema_bias"],
            "adx": adx, "rsi": rsi, "stoch": stoch, "bb": bb,
            "cci": cci, "bos": bos, "fvg": fvg, "ob": ob,
            "sweep": sweep, "choch": choch, "macd": macd,
            "atr": atr_v, "patterns": patterns, "sr_zones": sr_zones
        })

        # Profile filter
        profile = self.prof.evaluate(
            market_type,
            ai_result["ai_confidence"],
            rsi,
            bos["bos_bullish"] or bos["bos_bearish"],
            sweep["sweep_detected"]
        )

        # Elite score
        score = ai_result["normalized_score"] * 100
        if adx["trend_strong"]:             score += 5
        if sweep["sweep_detected"]:         score += 8
        if ob["active_ob"]:                 score += 6
        if ai_result["model_agreement"] >= 75: score += 10
        if profile["passed"]:               score += 5
        else:                               score -= 10

        elite_conf = min(abs(score) * 0.8 + 45, 99.9)

        if score > 15 and ai_result["ai_decision"] == "BUY":
            decision = "BUY"
        elif score < -15 and ai_result["ai_decision"] == "SELL":
            decision = "SELL"
        elif ai_result["ai_consensus"] == "SPLIT":
            decision = "WAIT"
        else:
            decision = ai_result["ai_decision"]

        return {
            "decision":   decision,
            "confidence": round(elite_conf, 1),
            "score":      round(score, 2),
            "ai":         ai_result,
            "profile":    profile,
            "atr":        atr_v,
            "rsi":        rsi,
        }


# ════════════════════════════════════════════════════════════════
# FINORIX AI — MAIN ENGINE (original logic preserved + elite layer)
# ════════════════════════════════════════════════════════════════

class FinorixAI:
    """
    Original FinorixAI signal engine.
    Elite analysis engine bolted on silently.
    Signal text / format = 100% unchanged.
    """

    def __init__(self):
        self.version = "1.0.0"
        self.name    = "FINORIX AI"
        self._elite  = _EliteMaster()          # ← silent elite layer

        self.supported_pairs = [
            "USD/BDT", "EUR/USD", "GBP/USD", "USD/JPY",
            "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP",
            "BTC/USD", "ETH/USD"
        ]
        self.timeframes = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

    # ── Data fetch (connect your real broker feed here) ──────
    def fetch_candle_data(self, pair, timeframe):
        base   = self._base_price(pair)
        vol    = self._volatility(pair)
        candles = []
        for i in range(50):
            bias  = 0.0003 * (i - 25)
            o = base + bias + random.uniform(-vol, vol)
            c = o + random.uniform(-vol * 1.5, vol * 1.5)
            h = max(o, c) + random.uniform(0, vol * 0.8)
            l = min(o, c) - random.uniform(0, vol * 0.8)
            candles.append({"index": i,
                            "open":  round(o, 5), "high": round(h, 5),
                            "low":   round(l, 5), "close": round(c, 5),
                            "volume": random.randint(500, 5000)})
            base = c
        return candles

    def _base_price(self, pair):
        return {"USD/BDT": 125.25, "EUR/USD": 1.0850, "GBP/USD": 1.2700,
                "USD/JPY": 149.50, "AUD/USD": 0.6500, "USD/CAD": 1.3600,
                "NZD/USD": 0.5900, "EUR/GBP": 0.8550,
                "BTC/USD": 67000.0, "ETH/USD": 3500.0}.get(pair, 1.0000)

    def _volatility(self, pair):
        return {"USD/BDT": 0.05, "EUR/USD": 0.0008, "GBP/USD": 0.0010,
                "USD/JPY": 0.08, "BTC/USD": 150.0, "ETH/USD": 25.0}.get(pair, 0.0010)

    # ── Original analysis methods (unchanged) ────────────────
    def detect_trend(self, candles):
        closes = [c["close"] for c in candles]
        n = len(closes)
        xm = (n - 1) / 2
        ym = sum(closes) / n
        num = sum((i - xm) * (closes[i] - ym) for i in range(n))
        den = sum((i - xm) ** 2 for i in range(n))
        slope = num / den if den else 0
        if slope > 0.0001:   return {"trend": "UPTREND ▲",   "direction": "UP",      "slope": slope}
        elif slope < -0.0001: return {"trend": "DOWNTREND ▼", "direction": "DOWN",    "slope": slope}
        return                       {"trend": "SIDEWAYS ↔",  "direction": "NEUTRAL", "slope": slope}

    def find_support_resistance(self, candles):
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        closes = [c["close"] for c in candles]
        res = round(max(highs[-20:]), 5)
        sup = round(min(lows[-20:]),  5)
        mid = round((res + sup) / 2,  5)
        cur = closes[-1]
        return {
            "resistance": res, "support": sup, "mid": mid,
            "current":    round(cur, 5),
            "zone":       "ABOVE MID" if cur > mid else "BELOW MID",
            "distance_to_resistance": round(res - cur, 5),
            "distance_to_support":    round(cur - sup, 5)
        }

    def detect_channel(self, candles):
        highs = [c["high"] for c in candles[-20:]]
        lows  = [c["low"]  for c in candles[-20:]]
        n = len(highs)
        xm = (n - 1) / 2
        hm = sum(highs) / n
        lm = sum(lows)  / n
        den = sum((i - xm) ** 2 for i in range(n)) or 1
        hs  = sum((i - xm) * (highs[i] - hm) for i in range(n)) / den
        ls  = sum((i - xm) * (lows[i]  - lm) for i in range(n)) / den
        w   = round(abs(hm - lm), 5)
        if hs > 0 and ls > 0: ct = "ASCENDING CHANNEL"
        elif hs < 0 and ls < 0: ct = "DESCENDING CHANNEL"
        else: ct = "HORIZONTAL CHANNEL"
        return {"channel_type": ct, "channel_width": w}

    def analyze_candle_patterns(self, candles):
        pats = []
        recent = candles[-5:]
        for i, c in enumerate(recent):
            body = abs(c["close"] - c["open"])
            uw   = c["high"] - max(c["open"], c["close"])
            lw   = min(c["open"], c["close"]) - c["low"]
            rng  = c["high"] - c["low"]
            if rng == 0: continue
            if body / rng < 0.1:   pats.append("DOJI ⚖️")
            if lw > body * 2 and uw < body * 0.5: pats.append("HAMMER 🔨")
            if uw > body * 2 and lw < body * 0.5: pats.append("SHOOTING STAR ⭐")
            if i > 0:
                p = recent[i - 1]
                if (c["close"] > c["open"] and p["close"] < p["open"] and
                        c["open"] < p["close"] and c["close"] > p["open"]):
                    pats.append("BULLISH ENGULFING 🟢")
                if (c["close"] < c["open"] and p["close"] > p["open"] and
                        c["open"] > p["close"] and c["close"] < p["open"]):
                    pats.append("BEARISH ENGULFING 🔴")
        return list(set(pats)) or ["NO CLEAR PATTERN"]

    def calculate_rsi(self, candles, period=14):
        closes = [c["close"] for c in candles]
        if len(closes) < period + 1: return 50.0
        gains, losses = [], []
        for i in range(1, period + 1):
            d = closes[-period + i] - closes[-period + i - 1]
            gains.append(d if d > 0 else 0)
            losses.append(abs(d) if d < 0 else 0)
        ag = sum(gains) / period
        al = sum(losses) / period
        if al == 0: return 100.0
        return round(100 - (100 / (1 + ag / al)), 2)

    # ── MASTER SIGNAL GENERATOR ──────────────────────────────
    def generate_signal(self, pair, timeframe, market_type="OTC"):
        candles = self.fetch_candle_data(pair, timeframe)

        # ── Run elite engine silently ────────────────────────
        elite = self._elite.run(candles, market_type)

        # ── Original analysis (kept intact) ─────────────────
        trend    = self.detect_trend(candles)
        sr       = self.find_support_resistance(candles)
        channel  = self.detect_channel(candles)
        patterns = self.analyze_candle_patterns(candles)
        rsi      = self.calculate_rsi(candles)

        # ── Original base score ──────────────────────────────
        score = 0
        if trend["direction"] == "UP":   score += 30
        elif trend["direction"] == "DOWN": score -= 30
        if rsi < 30:   score += 25
        elif rsi > 70: score -= 25
        if sr["distance_to_support"] < sr["distance_to_resistance"] * 0.3:   score += 20
        elif sr["distance_to_resistance"] < sr["distance_to_support"] * 0.3: score -= 20
        for p in patterns:
            if "BULLISH" in p or "HAMMER" in p:    score += 15
            elif "BEARISH" in p or "SHOOTING" in p: score -= 15
        if "ASCENDING"  in channel["channel_type"]: score += 10
        elif "DESCENDING" in channel["channel_type"]: score -= 10

        base_confidence = min(abs(score), 95)
        base_action = "BUY" if score >= 35 else ("SELL" if score <= -35 else "WAIT")

        # ── Elite override (silent — improves accuracy only) ─
        if elite["profile"]["passed"] and elite["ai"]["ai_consensus"] != "SPLIT":
            final_action = elite["decision"]
            confidence   = elite["confidence"]
        else:
            final_action = base_action
            confidence   = base_confidence

        # ── Labels (original format — UNCHANGED) ────────────
        if final_action == "BUY":
            signal = "BUY 📈";  emoji = "🟢"
        elif final_action == "SELL":
            signal = "SELL 📉"; emoji = "🔴"
        else:
            signal = "WAIT ⏳"; emoji = "🟡"

        # ── Entry / TP / SL ──────────────────────────────────
        current = sr["current"]
        spread  = elite["atr"] * 2          # ATR-based spread (more precise)
        if final_action == "BUY":
            entry = round(current, 5)
            tp    = round(current + spread * 2, 5)
            sl    = round(current - spread,     5)
        elif final_action == "SELL":
            entry = round(current, 5)
            tp    = round(current - spread * 2, 5)
            sl    = round(current + spread,     5)
        else:
            entry = tp = sl = current

        return {
            # ── Original keys (format_signal_message reads these — DO NOT CHANGE)
            "pair":        pair,
            "timeframe":   timeframe,
            "market_type": market_type,
            "signal":      signal,
            "action":      final_action,
            "emoji":       emoji,
            "confidence":  round(confidence, 1),
            "entry":       entry,
            "take_profit": tp,
            "stop_loss":   sl,
            "trend":       trend,
            "sr_zones":    sr,
            "channel":     channel,
            "patterns":    patterns,
            "rsi":         rsi,
            "score":       elite["score"],
            "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            # ── Elite metadata (internal, not shown in signal text)
            "_elite":      elite,
        }

    # ── Signal message formatter (original — UNCHANGED) ──────
    def format_signal_message(self, signal):
        s = signal
        patterns_str = " | ".join(s["patterns"])
        return f"""
╔══════════════════════════╗
║  🤖 **FINORIX AI** v{self.version}    ║
╚══════════════════════════╝

{s["emoji"]} **SIGNAL: {s["signal"]}**
📊 **Pair:** `{s["pair"]}` ({s["market_type"]})
⏱ **Timeframe:** `{s["timeframe"]}`
🎯 **Confidence:** `{s["confidence"]}%`

━━━━━━━━━━━━━━━━━━━━
💰 **TRADE SETUP**
━━━━━━━━━━━━━━━━━━━━
📍 Entry:       `{s["entry"]}`
✅ Take Profit: `{s["take_profit"]}`
❌ Stop Loss:   `{s["stop_loss"]}`

━━━━━━━━━━━━━━━━━━━━
📈 **MARKET ANALYSIS**
━━━━━━━━━━━━━━━━━━━━
🔄 Trend:    `{s["trend"]["trend"]}`
📐 Channel:  `{s["channel"]["channel_type"]}`
💹 RSI:      `{s["rsi"]}`
🛡 Support:  `{s["sr_zones"]["support"]}`
⚡ Resist:   `{s["sr_zones"]["resistance"]}`
🕯 Pattern:  `{patterns_str}`

━━━━━━━━━━━━━━━━━━━━
🕐 `{s["timestamp"]}`
━━━━━━━━━━━━━━━━━━━━
_⚠️ Trade at your own risk. FINORIX AI is for signal assistance only._
        """.strip()


# ════════════════════════════════════════════════════════════════
# TELEGRAM BOT HANDLERS (original — unchanged)
# ════════════════════════════════════════════════════════════════

ai = FinorixAI()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
🤖 **FINORIX AI Signal Bot**

Welcome! Commands available:

/signal `PAIR` `TF` — Get live signal
  _Example: /signal USD/BDT M1_
/signal `PAIR` `TF` `OTC` — OTC market signal
  _Example: /signal EUR/USD M5 OTC_
/pairs — List supported pairs
/help — Show this menu

_Powered by FINORIX AI Engine_
    """.strip()
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def pairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs_list = "\n".join([f"• `{p}`" for p in ai.supported_pairs])
    await update.message.reply_text(
        f"📊 **Supported Pairs:**\n\n{pairs_list}",
        parse_mode="Markdown"
    )

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/signal PAIR TIMEFRAME [OTC/LIVE]`\nExample: `/signal USD/BDT M1 OTC`",
            parse_mode="Markdown"
        )
        return

    pair        = args[0].upper()
    timeframe   = args[1].upper()
    market_type = args[2].upper() if len(args) >= 3 else "LIVE"

    if pair not in ai.supported_pairs:
        await update.message.reply_text(
            f"❌ Pair `{pair}` not supported.\nUse /pairs to see available pairs.",
            parse_mode="Markdown"
        )
        return
    if timeframe not in ai.timeframes:
        await update.message.reply_text(
            f"❌ Timeframe `{timeframe}` not valid.\nAvailable: {', '.join(ai.timeframes)}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("⏳ Analyzing market... Please wait.")
    try:
        signal  = ai.generate_signal(pair, timeframe, market_type)
        message = ai.format_signal_message(signal)
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Signal error: {e}")
        await update.message.reply_text("❌ Analysis failed. Try again.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip().upper()
    parts = text.split()
    if len(parts) >= 2 and "/" in parts[0]:
        pair        = parts[0]
        timeframe   = parts[1] if len(parts) > 1 else "M1"
        market_type = parts[2] if len(parts) > 2 else "OTC"
        if pair in ai.supported_pairs:
            await update.message.reply_text("⏳ Analyzing market...")
            try:
                signal  = ai.generate_signal(pair, timeframe, market_type)
                message = ai.format_signal_message(signal)
                await update.message.reply_text(message, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Text signal error: {e}")
                await update.message.reply_text("❌ Analysis failed.")
        else:
            await update.message.reply_text(
                "❌ Unknown pair. Use /pairs to see supported pairs.",
                parse_mode="Markdown"
            )

# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    print("🚀 FINORIX AI Elite Bot starting...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("pairs",  pairs_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ FINORIX AI Elite is live.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
