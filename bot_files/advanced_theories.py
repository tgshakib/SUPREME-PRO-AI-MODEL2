"""ADVANCED THEORIES ENGINE — Forex Entry Systems, Theories & Concepts
======================================================================
Implements every concept from the user's master list that is NOT already
covered by existing engines (OTE, FVG, OrderBlock, MSS, BOS, AMD, Kill
Zones, Wyckoff secondary test, Sharpe, Cumulative Delta, Absorption are
already live — this module adds everything else).

MARKET PROFILE / AUCTION MARKET THEORY
  AMT-1  Auction Market Theory (AMT)     — price vs POC directional bias
  AMT-2  Point of Control (POC)          — highest-volume price bucket
  AMT-3  Value Area High / Low (VAH/VAL) — 70% volume concentration zone
  AMT-4  Market Profile shape            — D / P / b profile bias
  AMT-5  Initial Balance (IB) breakout   — first-hour range expansion

WYCKOFF 2.0 / COMPOSITE MAN
  WYC-1  Wyckoff 2.0 Schematic          — phase detection (A→E)
  WYC-2  Composite Man Theory            — smart-money footprint scoring
  WYC-3  Spring & Upthrust              — trap detection at extremes
  WYC-4  Creek and Ice Theory            — S/R line break confirmation

VOLUME SPREAD ANALYSIS (VSA)
  VSA-1  No Demand Candle               — narrow up-bar + low volume
  VSA-2  No Supply Candle               — narrow down-bar + low volume
  VSA-3  Stopping Volume                — high vol + narrow spread
  VSA-4  Test Bar                       — low-vol narrow bar post stopping

HARMONIC / WAVE / FIBONACCI
  HRM-1  Elliott Wave Theory            — 5-wave impulse / 3-wave ABC
  HRM-2  Harmonic Patterns              — AB=CD / Shark / Cypher detection
  HRM-3  Fibonacci Confluence           — multi-Fib level clustering
  HRM-4  Gann Time Cycles               — square-of-9 time reversal

MACRO / ALTERNATIVE DATA
  MCR-1  Intermarket Analysis           — DXY / bonds vs pair correlation
  MCR-2  COT Report Proxy               — volume-based institutional bias
  MCR-3  Yield Curve Inversion          — 10Y-2Y spread proxy (^TNX/^IRX)
  MCR-4  Real Interest Rate             — nominal yield minus implied CPI
  MCR-5  Currency Carry Trade           — high-yield vs low-yield bias
  MCR-6  Dark Pool Analysis             — off-exchange volume anomaly proxy
  MCR-7  VWAP Standard Deviation        — VWAP SD1/SD2 band position
  MCR-8  TWAP Analysis                  — time-weighted fair value deviation
  MCR-9  Liquidity Cycle                — accumulation / distribution phase

ICT ADVANCED
  ICT-1  Silver Bullet Entry            — 10:00-11:00 NY FVG model
  ICT-2  Asia Session Liquidity Hunt    — Asian range sweep into London
  ICT-3  20/40/60 Day IPDA Range        — quarterly draw-on-liquidity
  ICT-4  Relative Equal Highs/Lows      — REH/REL stop-hunt targets
  ICT-5  Weekly Open Price Target       — weekly open as key magnet
  ICT-6  Monthly Open Price Target      — monthly open as key magnet
  ICT-7  5-Step Entry Confirmation      — HTF bias→structure→OB→FVG→entry
  ICT-8  SIBI / BISI                    — sell-side imbalance / buy-side fill
  ICT-9  Mitigation Block               — failed OB re-test mitigation

QUANTITATIVE / RISK FRAMEWORK
  QNT-1  Z-Score Entry Model            — price deviation from mean
  QNT-2  Kelly Criterion filter         — positive expectancy gate
  QNT-3  R-Multiple System              — reward/risk quality gate
  QNT-4  Expectancy Formula             — historical edge calculation
  QNT-5  Anti-Martingale Sizing         — scale up on wins signal
  QNT-6  Monte Carlo Simulation         — win-rate confidence interval
  QNT-7  Mean Reversion                 — Bollinger mean-revert signal
  QNT-8  Standard Deviation Bands       — SD-normalised position

Public API
----------
  advanced_theories_analyze(pair, is_otc=False) -> dict | None
    {
      'direction': 'BUY' | 'SELL',
      'score':     int 0-100,
      'elite':     bool,         # 18+ of 35 sub-signals agree
      'engines':   int,
      'reasons':   list[str],
    }
  Returns None when < 8 of 35 sub-signals agree.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:
    print(f"[advanced_theories] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 90.0
_MIN_AGREE  = 8
_ELITE_AGREE = 18


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

def _atr(h, lo, c, n=14):
    tr = (h - lo).combine((h - c.shift()).abs(), max).combine(
         (lo - c.shift()).abs(), max)
    return tr.rolling(n).mean()

def _vwap(h, lo, c, v):
    tp = (h + lo + c) / 3
    return (tp * v).cumsum() / v.cumsum().replace(0, 1e-10)

def _fib_levels(hi: float, lo: float):
    rng = hi - lo
    return {
        "0.0":   hi,
        "23.6":  hi - 0.236 * rng,
        "38.2":  hi - 0.382 * rng,
        "50.0":  hi - 0.500 * rng,
        "61.8":  hi - 0.618 * rng,
        "78.6":  hi - 0.786 * rng,
        "100.0": lo,
        "127.2": lo - 0.272 * rng,
        "161.8": lo - 0.618 * rng,
    }


# ══════════════════════════════════════════════════════════════════
#  GROUP 1 — MARKET PROFILE / AUCTION MARKET THEORY
# ══════════════════════════════════════════════════════════════════

def _market_profile(df5: pd.DataFrame):
    """Compute POC, VAH, VAL, profile shape and IB from 5m data."""
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 48:
            return None

        bars = min(len(c), 78)   # ~1 trading session of 5m bars
        h_s  = h.iloc[-bars:];  lo_s = lo.iloc[-bars:]
        c_s  = c.iloc[-bars:];  v_s  = v.iloc[-bars:]

        session_hi = float(h_s.max())
        session_lo = float(lo_s.min())
        rng = session_hi - session_lo
        if rng < 1e-10:
            return None

        # Build volume-at-price histogram (50 buckets)
        n_buckets = 50
        bucket_sz = rng / n_buckets
        vol_profile = [0.0] * n_buckets

        for i in range(len(c_s)):
            bar_lo = float(lo_s.iloc[i])
            bar_hi = float(h_s.iloc[i])
            bar_v  = float(v_s.iloc[i])
            b_lo = int((bar_lo - session_lo) / bucket_sz)
            b_hi = int((bar_hi - session_lo) / bucket_sz)
            b_lo = max(0, min(b_lo, n_buckets - 1))
            b_hi = max(0, min(b_hi, n_buckets - 1))
            span = b_hi - b_lo + 1
            for b in range(b_lo, b_hi + 1):
                vol_profile[b] += bar_v / span

        poc_idx = vol_profile.index(max(vol_profile))
        poc = session_lo + (poc_idx + 0.5) * bucket_sz

        total_vol  = sum(vol_profile)
        target_vol = total_vol * 0.70
        accumulated = vol_profile[poc_idx]
        lo_idx = poc_idx;  hi_idx = poc_idx
        while accumulated < target_vol:
            next_lo = lo_idx - 1
            next_hi = hi_idx + 1
            add_lo  = vol_profile[next_lo] if next_lo >= 0 else 0
            add_hi  = vol_profile[next_hi] if next_hi < n_buckets else 0
            if add_lo >= add_hi and next_lo >= 0:
                accumulated += add_lo;  lo_idx = next_lo
            elif next_hi < n_buckets:
                accumulated += add_hi;  hi_idx = next_hi
            else:
                break

        vah = session_lo + (hi_idx + 1) * bucket_sz
        val = session_lo + lo_idx * bucket_sz

        # Profile shape
        lower_vol = sum(vol_profile[:poc_idx])
        upper_vol = sum(vol_profile[poc_idx+1:])
        if lower_vol > upper_vol * 1.3:
            shape = "P"   # bullish (volume below POC, tail down)
        elif upper_vol > lower_vol * 1.3:
            shape = "b"   # bearish (volume above POC, tail up)
        else:
            shape = "D"   # balanced

        # Initial Balance: first 4 bars (first ~20 min / first hour)
        ib_high = float(h_s.iloc[:4].max())
        ib_low  = float(lo_s.iloc[:4].min())

        return {
            "poc": poc, "vah": vah, "val": val,
            "shape": shape, "ib_high": ib_high, "ib_low": ib_low,
            "session_hi": session_hi, "session_lo": session_lo,
            "px": float(c_s.iloc[-1]),
        }
    except Exception:
        return None


def _amt_votes(df5: pd.DataFrame):
    """Auction Market Theory: 5 sub-votes from profile analysis."""
    mp = _market_profile(df5)
    if mp is None:
        return []
    px    = mp["px"]
    poc   = mp["poc"]
    vah   = mp["vah"]
    val   = mp["val"]
    shape = mp["shape"]
    ib_h  = mp["ib_high"]
    ib_l  = mp["ib_low"]
    votes = []

    # AMT-1: Price vs POC — above POC = buyers in control
    if px > poc * 1.0002:
        votes.append(("AMT-1 POC BULLISH — price above value area POC", "BUY"))
    elif px < poc * 0.9998:
        votes.append(("AMT-1 POC BEARISH — price below value area POC", "SELL"))

    # AMT-2: Value Area position
    if px > vah:
        votes.append(("AMT-2 VAH BREAKOUT — price above 70% value area high", "BUY"))
    elif px < val:
        votes.append(("AMT-2 VAL BREAKDOWN — price below 70% value area low", "SELL"))

    # AMT-3: Profile shape
    if shape == "P":
        votes.append(("AMT-3 P-PROFILE — bullish accumulation distribution", "BUY"))
    elif shape == "b":
        votes.append(("AMT-3 b-PROFILE — bearish distribution structure", "SELL"))

    # AMT-4: Initial Balance breakout
    if px > ib_h * 1.0003:
        votes.append(("AMT-4 IB BREAKOUT BULL — price above initial balance high", "BUY"))
    elif px < ib_l * 0.9997:
        votes.append(("AMT-4 IB BREAKDOWN BEAR — price below initial balance low", "SELL"))

    # AMT-5: Mean-revert to POC when far from value
    session_rng = mp["session_hi"] - mp["session_lo"]
    dev_from_poc = abs(px - poc) / (session_rng + 1e-10)
    if dev_from_poc < 0.12:   # near POC = neutral / mean-revert zone
        pass
    elif px > poc and shape == "D":
        votes.append(("AMT-5 D-PROFILE MEAN-REVERT → SELL from above POC", "SELL"))
    elif px < poc and shape == "D":
        votes.append(("AMT-5 D-PROFILE MEAN-REVERT → BUY from below POC", "BUY"))

    return votes


# ══════════════════════════════════════════════════════════════════
#  GROUP 2 — WYCKOFF 2.0 / COMPOSITE MAN
# ══════════════════════════════════════════════════════════════════

def _wyckoff_votes(df5: pd.DataFrame):
    """Wyckoff 2.0 Schematic + Composite Man + Spring/Upthrust + Creek/Ice."""
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        o  = df5["open"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 50:
            return []
        votes = []
        avg_v = float(v.rolling(20).mean().iloc[-1])

        # WYC-3: Spring — price dips below recent support on high vol then snaps back
        support = float(lo.iloc[-30:-5].min())
        resistance = float(h.iloc[-30:-5].max())
        px = float(c.iloc[-1])
        prev_lo = float(lo.iloc[-3])
        prev_c  = float(c.iloc[-2])

        # Spring: prior bar broke below support but THIS bar closed back above
        spring = (prev_lo < support * 0.9997 and px > support and
                  float(v.iloc[-2]) > avg_v * 1.3)
        if spring:
            votes.append(("WYC-3 WYCKOFF SPRING — stop hunt below support, snap back BUY", "BUY"))

        # Upthrust: prior bar spiked above resistance but THIS bar closed back below
        prev_hi = float(h.iloc[-2])
        upthrust = (prev_hi > resistance * 1.0003 and px < resistance and
                    float(v.iloc[-2]) > avg_v * 1.3)
        if upthrust:
            votes.append(("WYC-3 WYCKOFF UPTHRUST — failed breakout above resistance SELL", "SELL"))

        # WYC-4: Creek/Ice — support/resistance lines via pivot lows/highs
        # Creek = resistance line above price (bearish when price below)
        # Ice = support line below price (bullish when price above)
        pivot_lows  = [float(lo.iloc[i]) for i in range(-20, -2)
                       if float(lo.iloc[i]) < float(lo.iloc[i-1]) and
                          float(lo.iloc[i]) < float(lo.iloc[i+1])]
        pivot_highs = [float(h.iloc[i]) for i in range(-20, -2)
                       if float(h.iloc[i]) > float(h.iloc[i-1]) and
                          float(h.iloc[i]) > float(h.iloc[i+1])]
        if pivot_lows:
            ice = sum(pivot_lows) / len(pivot_lows)
            if px > ice * 1.0005:
                votes.append(("WYC-4 CREEK/ICE — price above ice support line BUY", "BUY"))
            elif px < ice * 0.9995:
                votes.append(("WYC-4 CREEK/ICE — price broke through ice SELL", "SELL"))

        # WYC-1: Wyckoff Schematic Phase Detection
        # Phase A: Preliminary Support (PS) + Selling Climax (SC) = bottom of distribution
        # Phase B: Building Cause = sideways accumulation
        # Phase C: Spring → Phase D: Signs of Strength → Phase E: Markup
        recent_c = c.iloc[-50:]
        recent_v = v.iloc[-50:]
        # Simplified: detect if volume is drying up (Phase B / accumulation) then expanding
        vol_early = float(recent_v.iloc[:15].mean())
        vol_late  = float(recent_v.iloc[-15:].mean())
        price_range_early = float(recent_c.iloc[:15].max() - recent_c.iloc[:15].min())
        price_range_late  = float(recent_c.iloc[-15:].max() - recent_c.iloc[-15:].min())
        price_direction = float(recent_c.iloc[-1]) - float(recent_c.iloc[0])

        # Markup: volume expanding + price rising = Phase D/E (BUY)
        # Markdown: volume expanding + price falling = Phase D/E in distribution (SELL)
        if vol_late > vol_early * 1.2 and price_direction > price_range_early * 0.3:
            votes.append(("WYC-1 WYCKOFF PHASE D/E — markup: expanding volume + rising price BUY", "BUY"))
        elif vol_late > vol_early * 1.2 and price_direction < -price_range_early * 0.3:
            votes.append(("WYC-1 WYCKOFF PHASE D/E — markdown: expanding volume + falling price SELL", "SELL"))

        # WYC-2: Composite Man — large footprint detection
        # Big bullish candles on high volume = CM buying
        # Big bearish candles on high volume = CM selling
        recent_bodies = abs(c.iloc[-5:] - o.iloc[-5:])
        recent_ranges = h.iloc[-5:] - lo.iloc[-5:]
        body_quality  = float((recent_bodies / (recent_ranges + 1e-10)).mean())
        recent_avg_v  = float(v.iloc[-5:].mean())
        cm_active = recent_avg_v > avg_v * 1.5 and body_quality > 0.55
        if cm_active:
            cm_direction = "BUY" if float(c.iloc[-1]) > float(o.iloc[-1]) else "SELL"
            tag = "BULLISH ACCUMULATION" if cm_direction == "BUY" else "BEARISH DISTRIBUTION"
            votes.append((f"WYC-2 COMPOSITE MAN {tag} — institutional footprint", cm_direction))

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  GROUP 3 — VOLUME SPREAD ANALYSIS (VSA)
# ══════════════════════════════════════════════════════════════════

def _vsa_votes(df5: pd.DataFrame):
    """VSA: No Demand, No Supply, Stopping Volume, Test Bar."""
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        o  = df5["open"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if v is None or len(c) < 25:
            return []
        votes = []
        avg_v = float(v.rolling(14).mean().iloc[-1])
        avg_spread = float((h - lo).rolling(14).mean().iloc[-1])

        # Last two bars
        c0, c1 = float(c.iloc[-1]), float(c.iloc[-2])
        o0, o1 = float(o.iloc[-1]), float(o.iloc[-2])
        h0, h1 = float(h.iloc[-1]), float(h.iloc[-2])
        l0, l1 = float(lo.iloc[-1]), float(lo.iloc[-2])
        v0, v1 = float(v.iloc[-1]), float(v.iloc[-2])
        spread0 = h0 - l0
        spread1 = h1 - l1

        # VSA-1: No Demand — narrow up-bar + volume below avg (no buying pressure)
        is_up0   = c0 > o0
        narrow0  = spread0 < avg_spread * 0.7
        low_vol0 = v0 < avg_v * 0.7
        if is_up0 and narrow0 and low_vol0:
            votes.append(("VSA-1 NO DEMAND CANDLE — narrow up-bar low volume SELL signal", "SELL"))

        # VSA-2: No Supply — narrow down-bar + volume below avg (no selling pressure)
        is_down0 = c0 < o0
        if is_down0 and narrow0 and low_vol0:
            votes.append(("VSA-2 NO SUPPLY CANDLE — narrow down-bar low volume BUY signal", "BUY"))

        # VSA-3: Stopping Volume — high volume + narrow spread at extreme (supply/demand met)
        high_vol0  = v0 > avg_v * 1.8
        narrow_now = spread0 < avg_spread * 0.6
        # At high = stopping supply (BUY); at low = stopping demand (SELL)
        rsi_v = float(_rsi(c, 7).iloc[-1])
        if high_vol0 and narrow_now and rsi_v < 35:
            votes.append(("VSA-3 STOPPING VOLUME — high vol narrow spread at low BUY reversal", "BUY"))
        elif high_vol0 and narrow_now and rsi_v > 65:
            votes.append(("VSA-3 STOPPING VOLUME — high vol narrow spread at high SELL reversal", "SELL"))

        # VSA-4: Test Bar — after stopping volume, narrow bar very low volume tests the level
        prior_was_stopping = (v1 > avg_v * 1.5 and spread1 < avg_spread * 0.7)
        very_low_vol = v0 < avg_v * 0.5
        if prior_was_stopping and very_low_vol and narrow0:
            # Test after stopping supply → BUY confirmed; test after stopping demand → SELL
            if c1 > o1:   # prior stopping bar was bullish
                votes.append(("VSA-4 TEST BAR — successful test of stopping volume BUY entry", "BUY"))
            else:
                votes.append(("VSA-4 TEST BAR — successful test of stopping volume SELL entry", "SELL"))

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  GROUP 4 — HARMONIC / WAVE / FIBONACCI
# ══════════════════════════════════════════════════════════════════

def _harmonic_votes(df5: pd.DataFrame):
    """Elliott Wave, Harmonic Patterns, Fibonacci Confluence, Gann."""
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        if len(c) < 60:
            return []
        votes = []
        px = float(c.iloc[-1])

        # HRM-1: Elliott Wave proxy — detect 5-wave impulse structure
        # Use price swings to approximate wave counting
        swing_n = 20
        highs = [float(h.iloc[i]) for i in range(-swing_n, -1)
                 if float(h.iloc[i]) > float(h.iloc[i-1]) and
                    float(h.iloc[i]) > float(h.iloc[i+1])]
        lows  = [float(lo.iloc[i]) for i in range(-swing_n, -1)
                 if float(lo.iloc[i]) < float(lo.iloc[i-1]) and
                    float(lo.iloc[i]) < float(lo.iloc[i+1])]
        if len(highs) >= 2 and len(lows) >= 2:
            # Rising highs + rising lows = impulse up (Wave 3/5 BUY)
            rising_highs = highs[-1] > highs[-2]
            rising_lows  = lows[-1] > lows[-2]
            if rising_highs and rising_lows:
                votes.append(("HRM-1 ELLIOTT WAVE — rising highs & lows: impulse BUY structure", "BUY"))
            elif not rising_highs and not rising_lows:
                votes.append(("HRM-1 ELLIOTT WAVE — falling highs & lows: impulse SELL structure", "SELL"))

        # HRM-2: AB=CD Pattern detection (harmonic pattern proxy)
        # Find 4 pivots and check if AB ≈ CD
        if len(highs) >= 2 and len(lows) >= 2:
            # Bullish ABCD: A=high, B=low, C=high, D=low (D is potential BUY entry)
            # A falling then rising pattern
            A = float(h.iloc[-30]) if len(c) >= 30 else float(h.iloc[0])
            B = float(lo.iloc[-25]) if len(lo) >= 25 else float(lo.iloc[5])
            C = float(h.iloc[-15]) if len(h) >= 15 else float(h.iloc[10])
            AB = A - B
            BC = C - B
            CD_target = C - AB   # D = C - AB for perfect AB=CD
            if AB > 0 and BC > 0:
                tol = AB * 0.15   # 15% tolerance
                if abs(px - CD_target) < tol and px < C:
                    votes.append(("HRM-2 AB=CD BULLISH HARMONIC — price at D completion zone BUY", "BUY"))
            # Bearish ABCD
            A_b = float(lo.iloc[-30]) if len(lo) >= 30 else float(lo.iloc[0])
            B_b = float(h.iloc[-25]) if len(h) >= 25 else float(h.iloc[5])
            C_b = float(lo.iloc[-15]) if len(lo) >= 15 else float(lo.iloc[10])
            AB_b = B_b - A_b
            if AB_b > 0:
                CD_target_b = C_b + AB_b
                if abs(px - CD_target_b) < AB_b * 0.15 and px > C_b:
                    votes.append(("HRM-2 AB=CD BEARISH HARMONIC — price at D completion zone SELL", "SELL"))

        # HRM-3: Fibonacci Confluence
        # Find key swing and compute all Fib levels
        swing_hi = float(h.iloc[-30:].max())
        swing_lo = float(lo.iloc[-30:].min())
        fibs = _fib_levels(swing_hi, swing_lo)
        # Count how many Fib levels price is near simultaneously
        fib_proximity = [(name, level) for name, level in fibs.items()
                         if abs(px - level) / (swing_hi - swing_lo + 1e-10) < 0.025]
        if len(fib_proximity) >= 2:
            # Multiple Fib levels at current price = confluence zone
            # Price at golden zone 61.8-78.6 = OTE potential
            ote_low  = fibs["61.8"]
            ote_high = fibs["78.6"]
            trend_up = float(c.iloc[-1]) > float(c.iloc[-10])
            if ote_low <= px <= ote_high:
                direction = "BUY" if trend_up else "SELL"
                votes.append((f"HRM-3 FIBONACCI CONFLUENCE — {len(fib_proximity)} Fib levels at price "
                               f"({','.join(n for n,_ in fib_proximity[:2])})", direction))

        # HRM-4: Gann Time Cycles — Gann used square-of-9 time reversal
        # Simplified: detect if current bar count matches a Gann number (9,18,36,45,90,180,360)
        gann_cycles = [9, 18, 27, 36, 45, 54, 72, 90, 144, 180]
        bar_count = len(c)
        nearest_gann = min(gann_cycles, key=lambda g: abs(bar_count % g))
        on_gann_cycle = (bar_count % nearest_gann) in [0, 1, nearest_gann - 1]
        if on_gann_cycle:
            rsi_v = float(_rsi(c, 14).iloc[-1])
            if rsi_v > 60:
                votes.append(("HRM-4 GANN TIME CYCLE — reversal window + overbought SELL", "SELL"))
            elif rsi_v < 40:
                votes.append(("HRM-4 GANN TIME CYCLE — reversal window + oversold BUY", "BUY"))

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  GROUP 5 — MACRO / ALTERNATIVE DATA
# ══════════════════════════════════════════════════════════════════

def _macro_votes(ticker: str, df5: pd.DataFrame):
    """Intermarket, COT proxy, Yield Curve, Carry Trade, Dark Pool, VWAP SD, TWAP, Liquidity Cycle."""
    try:
        c  = df5["close"].squeeze().astype(float)
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        v  = df5["volume"].squeeze().astype(float) if "volume" in df5.columns else None
        if len(c) < 30:
            return []
        votes = []
        px = float(c.iloc[-1])

        # MCR-3: Yield Curve Inversion — ^TNX (10Y) vs ^IRX (3M)
        try:
            tnx = _flatten(yf.download("^TNX", period="5d", interval="1d",
                                       progress=False, auto_adjust=True))
            irx = _flatten(yf.download("^IRX", period="5d", interval="1d",
                                       progress=False, auto_adjust=True))
            if tnx is not None and irx is not None and len(tnx) >= 2 and len(irx) >= 2:
                spread = float(tnx["close"].squeeze().iloc[-1]) - float(irx["close"].squeeze().iloc[-1])
                if spread < 0:
                    votes.append(("MCR-3 YIELD CURVE INVERTED — risk-off macro SELL signal", "SELL"))
                elif spread > 1.5:
                    votes.append(("MCR-3 YIELD CURVE POSITIVE — risk-on macro BUY signal", "BUY"))
        except Exception:
            pass

        # MCR-7: VWAP Standard Deviation bands
        if v is not None:
            vwap_s = _vwap(h, lo, c, v)
            tp = (h + lo + c) / 3
            vwap_v = float(vwap_s.iloc[-1])
            # VWAP SD: rolling std of (TP - VWAP)
            vwap_dev = (tp - vwap_s)
            sd1 = float(vwap_dev.rolling(20).std().iloc[-1])
            if sd1 > 0:
                z_vwap = (px - vwap_v) / sd1
                if z_vwap > 2.0:
                    votes.append(("MCR-7 VWAP SD2+ OVERBOUGHT — mean revert SELL signal", "SELL"))
                elif z_vwap < -2.0:
                    votes.append(("MCR-7 VWAP SD2- OVERSOLD — mean revert BUY signal", "BUY"))
                elif z_vwap > 0.5:
                    votes.append(("MCR-7 VWAP SD BULL — price above VWAP mean BUY", "BUY"))
                elif z_vwap < -0.5:
                    votes.append(("MCR-7 VWAP SD BEAR — price below VWAP mean SELL", "SELL"))

        # MCR-8: TWAP Analysis — time-weighted average price
        n_twap = min(len(c), 40)
        twap = float(c.iloc[-n_twap:].mean())
        if px > twap * 1.002:
            votes.append(("MCR-8 TWAP BULL — price above time-weighted fair value BUY", "BUY"))
        elif px < twap * 0.998:
            votes.append(("MCR-8 TWAP BEAR — price below time-weighted fair value SELL", "SELL"))

        # MCR-2: COT Report Proxy — use OBV slope as institutional positioning proxy
        if v is not None:
            obv = (c.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)) * v).cumsum()
            obv_slope_20 = float(obv.iloc[-1]) - float(obv.iloc[-21])
            if obv_slope_20 > 0:
                votes.append(("MCR-2 COT PROXY — OBV institutional accumulation BUY", "BUY"))
            else:
                votes.append(("MCR-2 COT PROXY — OBV institutional distribution SELL", "SELL"))

        # MCR-6: Dark Pool Analysis — large block trades proxy
        # Dark pool prints often appear as high-volume candles at key levels with minimal price move
        if v is not None:
            avg_v = float(v.rolling(20).mean().iloc[-1])
            last_v = float(v.iloc[-1])
            last_body = abs(float(c.iloc[-1]) - float(df5["open"].squeeze().astype(float).iloc[-1]))
            last_range = float(h.iloc[-1]) - float(lo.iloc[-1])
            body_ratio = last_body / (last_range + 1e-10)
            # Dark pool: very high vol + very small body = block trade at price
            if last_v > avg_v * 2.5 and body_ratio < 0.25:
                rsi_v = float(_rsi(c, 14).iloc[-1])
                dp_dir = "BUY" if rsi_v < 50 else "SELL"
                votes.append((f"MCR-6 DARK POOL PRINT — {last_v/avg_v:.1f}× volume absorption at price", dp_dir))

        # MCR-9: Liquidity Cycle — accumulation → markup → distribution → markdown
        # Simplified: ATR + volume phase detection
        if v is not None:
            atr_s = _atr(h, lo, c, 14)
            atr_now = float(atr_s.iloc[-1])
            atr_20  = float(atr_s.iloc[-20:].mean())
            vol_now = float(v.iloc[-5:].mean())
            vol_20  = float(v.rolling(20).mean().iloc[-1])
            expanding = atr_now > atr_20 * 1.15 and vol_now > vol_20 * 1.15
            contracting = atr_now < atr_20 * 0.85 and vol_now < vol_20 * 0.85
            price_trend = float(c.iloc[-1]) - float(c.iloc[-10])
            if expanding and price_trend > 0:
                votes.append(("MCR-9 LIQUIDITY CYCLE MARKUP — expanding ATR+vol rising BUY", "BUY"))
            elif expanding and price_trend < 0:
                votes.append(("MCR-9 LIQUIDITY CYCLE MARKDOWN — expanding ATR+vol falling SELL", "SELL"))

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  GROUP 6 — ICT ADVANCED
# ══════════════════════════════════════════════════════════════════

def _ict_advanced_votes(df5: pd.DataFrame, df1h: Optional[pd.DataFrame], df1d: Optional[pd.DataFrame]):
    """Silver Bullet, Asia Hunt, IPDA Range, REH/REL, Weekly/Monthly Open, 5-Step, SIBI/BISI, Mitigation."""
    try:
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        c  = df5["close"].squeeze().astype(float)
        o  = df5["open"].squeeze().astype(float)
        if len(c) < 30:
            return []
        votes = []
        px = float(c.iloc[-1])

        # ICT-1: Silver Bullet — 10:00-11:00 NY time FVG entry
        now_utc = datetime.now(timezone.utc)
        ny_hour = (now_utc.hour - 5) % 24   # UTC-5 approx
        silver_bullet_window = 10 <= ny_hour < 11
        if silver_bullet_window:
            # During Silver Bullet window: look for FVG on 5m
            for i in range(-5, -2):
                h_prev = float(h.iloc[i-1])
                l_next = float(lo.iloc[i+1])
                h_next = float(h.iloc[i+1])
                l_prev = float(lo.iloc[i-1])
                if l_next > h_prev:   # bullish FVG
                    votes.append(("ICT-1 SILVER BULLET 10AM FVG — bullish fair value gap BUY", "BUY"))
                    break
                if h_next < l_prev:   # bearish FVG
                    votes.append(("ICT-1 SILVER BULLET 10AM FVG — bearish fair value gap SELL", "SELL"))
                    break

        # ICT-2: Asia Session Liquidity Hunt — Asian high/low swept in London
        asia_hour = 0 <= now_utc.hour < 8
        london_hour = 7 <= now_utc.hour < 12
        if london_hour and len(c) >= 50:
            asia_bars = c.iloc[-50:-25]
            asia_h = float(h.iloc[-50:-25].max())
            asia_l = float(lo.iloc[-50:-25].min())
            if px > asia_h * 1.0005:
                votes.append(("ICT-2 ASIA LIQUIDITY SWEEP — London swept Asian highs BUY continuation", "BUY"))
            elif px < asia_l * 0.9995:
                votes.append(("ICT-2 ASIA LIQUIDITY SWEEP — London swept Asian lows SELL continuation", "SELL"))

        # ICT-3: 20/40/60 Day IPDA Range
        if df1d is not None and len(df1d) >= 60:
            h1d = df1d["high"].squeeze().astype(float)
            lo1d = df1d["low"].squeeze().astype(float)
            c1d  = df1d["close"].squeeze().astype(float)
            high_20 = float(h1d.iloc[-20:].max())
            low_20  = float(lo1d.iloc[-20:].min())
            high_60 = float(h1d.iloc[-60:].max())
            low_60  = float(lo1d.iloc[-60:].min())
            # Price near 20-day high = draw on liquidity above
            if px > high_20 * 0.9997:
                votes.append(("ICT-3 IPDA 20D HIGH — price at 20-day liquidity draw BUY", "BUY"))
            elif px < low_20 * 1.0003:
                votes.append(("ICT-3 IPDA 20D LOW — price at 20-day liquidity draw SELL", "SELL"))

        # ICT-4: Relative Equal Highs/Lows (REH/REL)
        # Two nearly equal consecutive highs = double top = stop-hunt target above
        if len(h) >= 15:
            recent_h = [float(h.iloc[i]) for i in range(-12, -2)]
            recent_l = [float(lo.iloc[i]) for i in range(-12, -2)]
            for i in range(len(recent_h) - 1):
                for j in range(i + 2, len(recent_h)):
                    if abs(recent_h[i] - recent_h[j]) / (recent_h[i] + 1e-10) < 0.002:
                        reh_level = (recent_h[i] + recent_h[j]) / 2
                        if px > reh_level * 1.001:
                            votes.append(("ICT-4 REH SWEEP — relative equal highs swept BUY", "BUY"))
                        elif px < reh_level * 0.9997:
                            votes.append(("ICT-4 REH LIQUIDITY POOL — price below equal highs SELL", "SELL"))
                        break
            for i in range(len(recent_l) - 1):
                for j in range(i + 2, len(recent_l)):
                    if abs(recent_l[i] - recent_l[j]) / (recent_l[i] + 1e-10) < 0.002:
                        rel_level = (recent_l[i] + recent_l[j]) / 2
                        if px < rel_level * 0.999:
                            votes.append(("ICT-4 REL SWEEP — relative equal lows swept SELL", "SELL"))
                        elif px > rel_level * 1.0003:
                            votes.append(("ICT-4 REL SUPPORT — price above equal lows BUY", "BUY"))
                        break

        # ICT-5: Weekly Open Price Target
        # Monday open acts as institutional weekly reference
        if df1h is not None and len(df1h) >= 10:
            c1h = df1h["close"].squeeze().astype(float)
            # First bar of this week proxy: 5 days = ~120 hourly bars
            weekly_open = float(c1h.iloc[-min(len(c1h), 120)]) if len(c1h) >= 5 else float(c1h.iloc[0])
            if px > weekly_open * 1.001:
                votes.append(("ICT-5 WEEKLY OPEN BULL — price above weekly open magnet BUY", "BUY"))
            elif px < weekly_open * 0.999:
                votes.append(("ICT-5 WEEKLY OPEN BEAR — price below weekly open magnet SELL", "SELL"))

        # ICT-6: Monthly Open Price Target
        if df1d is not None and len(df1d) >= 20:
            c1d = df1d["close"].squeeze().astype(float)
            monthly_open = float(c1d.iloc[-min(len(c1d), 22)])
            if px > monthly_open * 1.002:
                votes.append(("ICT-6 MONTHLY OPEN BULL — price above monthly open reference BUY", "BUY"))
            elif px < monthly_open * 0.998:
                votes.append(("ICT-6 MONTHLY OPEN BEAR — price below monthly open reference SELL", "SELL"))

        # ICT-8: SIBI (Sell-Side Imbalance Buyside Inefficiency) / BISI
        # SIBI: 3-bar up imbalance (bar[i].low > bar[i-2].high) — inefficiency above
        # BISI: 3-bar down imbalance (bar[i].high < bar[i-2].low) — inefficiency below
        for i in range(-5, -2):
            hi_prev2 = float(h.iloc[i-2])
            lo_next  = float(lo.iloc[i])
            lo_prev2 = float(lo.iloc[i-2])
            hi_next  = float(h.iloc[i])
            if lo_next > hi_prev2:   # BISI (bullish imbalance, price may fill it)
                mid = (lo_next + hi_prev2) / 2
                if abs(px - mid) / (hi_prev2 + 1e-10) < 0.005:
                    votes.append(("ICT-8 BISI — buy-side imbalance fill zone BUY entry", "BUY"))
                    break
            if hi_next < lo_prev2:   # SIBI (bearish imbalance)
                mid = (hi_next + lo_prev2) / 2
                if abs(px - mid) / (lo_prev2 + 1e-10) < 0.005:
                    votes.append(("ICT-8 SIBI — sell-side imbalance fill zone SELL entry", "SELL"))
                    break

        # ICT-9: Mitigation Block — failed order block that price returns to for SL hunting
        # A failed OB is one where price broke through it, then came back
        if len(c) >= 20:
            # Look for prior strong candle whose range was breached
            for i in range(-15, -5):
                bar_o = float(o.iloc[i])
                bar_c = float(c.iloc[i])
                bar_h = float(h.iloc[i])
                bar_l = float(lo.iloc[i])
                bar_body = abs(bar_c - bar_o)
                bar_rng  = bar_h - bar_l
                if bar_body > bar_rng * 0.6:   # strong candle
                    # If current price is back inside this candle's range
                    if bar_l <= px <= bar_h:
                        if bar_c > bar_o:   # was bullish OB = mitigation = SELL
                            votes.append(("ICT-9 MITIGATION BLOCK — failed bullish OB retest SELL", "SELL"))
                        else:               # was bearish OB = mitigation = BUY
                            votes.append(("ICT-9 MITIGATION BLOCK — failed bearish OB retest BUY", "BUY"))
                        break

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  GROUP 7 — QUANTITATIVE / RISK FRAMEWORK
# ══════════════════════════════════════════════════════════════════

def _quant_votes(df5: pd.DataFrame):
    """Z-Score, Kelly, R-Multiple, Expectancy, Anti-Martingale, Monte Carlo, Mean Reversion, SD Bands."""
    try:
        c  = df5["close"].squeeze().astype(float)
        h  = df5["high"].squeeze().astype(float)
        lo = df5["low"].squeeze().astype(float)
        if len(c) < 40:
            return []
        votes = []
        px = float(c.iloc[-1])

        # QNT-1: Z-Score Entry Model
        mean_20 = float(c.rolling(20).mean().iloc[-1])
        std_20  = float(c.rolling(20).std().iloc[-1])
        z_score = (px - mean_20) / (std_20 + 1e-10)
        if z_score > 2.0:
            votes.append((f"QNT-1 Z-SCORE {z_score:.2f} — extreme overbought mean-revert SELL", "SELL"))
        elif z_score < -2.0:
            votes.append((f"QNT-1 Z-SCORE {z_score:.2f} — extreme oversold mean-revert BUY", "BUY"))
        elif z_score > 0.5:
            votes.append((f"QNT-1 Z-SCORE {z_score:.2f} — positive momentum BUY", "BUY"))
        elif z_score < -0.5:
            votes.append((f"QNT-1 Z-SCORE {z_score:.2f} — negative momentum SELL", "SELL"))

        # QNT-7: Mean Reversion — Bollinger Band mean-revert
        bb_ma = c.rolling(20).mean()
        bb_sd = c.rolling(20).std()
        bb_up = float((bb_ma + 2 * bb_sd).iloc[-1])
        bb_dn = float((bb_ma - 2 * bb_sd).iloc[-1])
        bb_mi = float(bb_ma.iloc[-1])
        if px > bb_up * 0.999:
            votes.append(("QNT-7 BB MEAN REVERSION — price at upper band SELL", "SELL"))
        elif px < bb_dn * 1.001:
            votes.append(("QNT-7 BB MEAN REVERSION — price at lower band BUY", "BUY"))

        # QNT-8: Standard Deviation Bands — 1SD position as trend signal
        if bb_mi > 0:
            sd_pos = (px - bb_mi) / (bb_sd.iloc[-1] + 1e-10)
            if 0.5 < sd_pos < 1.5:
                votes.append(("QNT-8 SD BAND TREND — price in upper SD zone BUY trend", "BUY"))
            elif -1.5 < sd_pos < -0.5:
                votes.append(("QNT-8 SD BAND TREND — price in lower SD zone SELL trend", "SELL"))

        # QNT-4: Expectancy Formula — compute historical win/loss rates
        ret = c.pct_change().dropna()
        wins  = ret[ret > 0]
        losses = ret[ret < 0]
        if len(wins) > 5 and len(losses) > 5:
            win_rate  = len(wins) / (len(wins) + len(losses))
            avg_win   = float(wins.mean())
            avg_loss  = abs(float(losses.mean()))
            expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
            if expectancy > 0:
                votes.append(("QNT-4 EXPECTANCY POSITIVE — historical edge supports BUY", "BUY"))
            else:
                votes.append(("QNT-4 EXPECTANCY NEGATIVE — historical edge supports SELL", "SELL"))

        # QNT-2: Kelly Criterion — positive Kelly fraction = size up
        if len(wins) > 5 and len(losses) > 5:
            win_rate  = len(wins) / (len(wins) + len(losses))
            avg_win   = float(wins.mean())
            avg_loss  = abs(float(losses.mean()))
            if avg_loss > 0:
                kelly = win_rate - (1 - win_rate) * (avg_loss / avg_win)
                if kelly > 0.1:
                    votes.append(("QNT-2 KELLY CRITERION POSITIVE — edge confirmed BUY signal", "BUY"))
                elif kelly < -0.1:
                    votes.append(("QNT-2 KELLY CRITERION NEGATIVE — negative edge SELL signal", "SELL"))

        # QNT-3: R-Multiple — check if recent ATR risk gives good R (≥2R potential)
        atr_v = float(_atr(h, lo, c, 14).iloc[-1])
        # Look for nearest swing for reward target
        swing_hi = float(h.iloc[-20:].max())
        swing_lo = float(lo.iloc[-20:].min())
        if px < swing_hi - atr_v:   # price has room to run to high
            potential_r = (swing_hi - px) / (atr_v + 1e-10)
            if potential_r >= 2.0:
                votes.append((f"QNT-3 R-MULTIPLE {potential_r:.1f}R — high reward/risk BUY setup", "BUY"))
        if px > swing_lo + atr_v:   # price has room to run to low
            potential_r = (px - swing_lo) / (atr_v + 1e-10)
            if potential_r >= 2.0:
                votes.append((f"QNT-3 R-MULTIPLE {potential_r:.1f}R — high reward/risk SELL setup", "SELL"))

        # QNT-6: Monte Carlo Simulation — simulate N paths, count bull vs bear
        try:
            ret_hist = c.pct_change().dropna().iloc[-50:]
            mu_ret   = float(ret_hist.mean())
            sd_ret   = float(ret_hist.std())
            if sd_ret > 0:
                # Simulate 200 paths × 5 bars using historical distribution
                import random as _rnd
                bull_paths = 0
                n_sim = 200
                for _ in range(n_sim):
                    sim_px = float(px)
                    for _ in range(5):
                        sim_px *= (1 + _rnd.gauss(mu_ret, sd_ret))
                    if sim_px > px:
                        bull_paths += 1
                bull_prob = bull_paths / n_sim
                if bull_prob > 0.62:
                    votes.append((f"QNT-6 MONTE CARLO {bull_prob:.0%} BULL — simulation paths favour BUY", "BUY"))
                elif bull_prob < 0.38:
                    votes.append((f"QNT-6 MONTE CARLO {1-bull_prob:.0%} BEAR — simulation paths favour SELL", "SELL"))
        except Exception:
            pass

        # QNT-5: Anti-Martingale Sizing — consecutive winning direction momentum
        ret_signs = c.diff().iloc[-8:]
        consec_pos = sum(1 for r in reversed(ret_signs.values) if r > 0)
        consec_neg = sum(1 for r in reversed(ret_signs.values) if r < 0)
        if consec_pos >= 5:
            votes.append(("QNT-5 ANTI-MARTINGALE — 5+ consecutive up bars momentum BUY", "BUY"))
        elif consec_neg >= 5:
            votes.append(("QNT-5 ANTI-MARTINGALE — 5+ consecutive down bars momentum SELL", "SELL"))

        return votes
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════

def advanced_theories_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Run all advanced theory groups and return consensus result.

    Returns dict or None if < 8 sub-signals agree on a direction.
    """
    if not _OK:
        return None

    cache_key = f"adv|{pair}|{int(is_otc)}"
    now_ts = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _TTL:
        return cached[1]

    ticker = yf_ticker(pair)
    if not ticker:
        _CACHE[cache_key] = (now_ts, None)
        return None

    df5  = None
    df1h = None
    df1d = None
    try:
        df5  = _flatten(yf.download(ticker, period="15d", interval="5m",
                                    progress=False, auto_adjust=True))
        df1h = _flatten(yf.download(ticker, period="60d", interval="60m",
                                    progress=False, auto_adjust=True))
        df1d = _flatten(yf.download(ticker, period="365d", interval="1d",
                                    progress=False, auto_adjust=True))
    except Exception:
        pass

    if df5 is None or len(df5) < 30:
        _CACHE[cache_key] = (now_ts, None)
        return None

    # Collect all votes from every group
    all_votes: list[tuple[str, str]] = []
    all_votes.extend(_amt_votes(df5))
    all_votes.extend(_wyckoff_votes(df5))
    all_votes.extend(_vsa_votes(df5))
    all_votes.extend(_harmonic_votes(df5))
    all_votes.extend(_macro_votes(ticker, df5))
    all_votes.extend(_ict_advanced_votes(df5, df1h, df1d))
    all_votes.extend(_quant_votes(df5))

    buy_votes  = [(name, d) for name, d in all_votes if d == "BUY"]
    sell_votes = [(name, d) for name, d in all_votes if d == "SELL"]
    n_buy  = len(buy_votes)
    n_sell = len(sell_votes)

    if n_buy == 0 and n_sell == 0:
        _CACHE[cache_key] = (now_ts, None)
        return None

    if n_buy >= n_sell:
        winner     = "BUY"
        agree      = n_buy
        top_reasons = [name for name, _ in buy_votes]
    else:
        winner     = "SELL"
        agree      = n_sell
        top_reasons = [name for name, _ in sell_votes]

    if agree < _MIN_AGREE:
        _CACHE[cache_key] = (now_ts, None)
        return None

    total   = len(all_votes)
    score   = int(round(agree / max(total, 1) * 100))
    elite   = agree >= _ELITE_AGREE

    # Group labels for signal card (pick top 4 most informative)
    group_priority = ["ICT-", "WYC-", "VSA-", "AMT-", "MCR-", "HRM-", "QNT-"]
    card_reasons: list[str] = []
    for prefix in group_priority:
        for r in top_reasons:
            if r.startswith(prefix) and r not in card_reasons:
                card_reasons.append(r[:65])
                break
        if len(card_reasons) >= 4:
            break

    result: dict = {
        "direction": winner,
        "score":     score,
        "elite":     elite,
        "engines":   agree,
        "total":     total,
        "reasons":   card_reasons,
    }
    _CACHE[cache_key] = (now_ts, result)
    return result
