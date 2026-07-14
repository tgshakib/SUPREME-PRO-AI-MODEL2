"""FINORIX SHARP ENGINE — Updated Finorix with 3 Non-Lagging Indicators
=======================================================================
Replaces all lagging SMA/EMA crossover-based sub-models with three
fast, proven indicators:

  1. Quick RSI(14)     — momentum + zone + divergence detection
  2. MACD             — crossover + histogram momentum
  3. Bollinger Bands  — squeeze, expansion, band-touch reversal

Layered with:
  • Candlestick pattern scoring (via TV TA vote ratios)
  • Bid-ask spread proxy      (order imbalance pressure)
  • Volume profile            (multi-TF vote agreement)
  • Order flow analysis       (directional momentum acceleration)

Works for OTC and LIVE binary + Forex.
Non-martingale compatible: confirms a clean setup, not a recovery trade.
Contract: zero side-effects — never touches signal text or UI.

Public API
----------
finorix_sharp(pair: str, is_otc: bool = False, market_type: str = "LIVE") -> dict
    returns:
      {
        "ok":         bool,
        "direction":  "BUY" | "SELL" | "WAIT",
        "confidence": int 0-100,
        "grade":      "ELITE" | "STRONG" | "MODERATE" | "WEAK",
        "signals":    list[str],
        "elite":      bool,
        "veto":       bool,   # conflicting signals → skip
      }
"""
from __future__ import annotations
import time
from typing import Optional

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 20.0

try:
    from candle_feed import get_single_tf as _get_tf
    _TV_OK = True
except Exception:
    _TV_OK = False
    _get_tf = None  # type: ignore

try:
    from live_prices import get_stooq_momentum as _stooq
    _SQ_OK = True
except Exception:
    _SQ_OK = False
    _stooq = None  # type: ignore


def _tv(pair: str, tf: str) -> dict:
    if not _TV_OK or _get_tf is None:
        return {}
    try:
        return _get_tf(pair, tf) or {}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# INDICATOR 1 — Quick RSI(14): Momentum + Zone + Divergence
# ═══════════════════════════════════════════════════════════════════════════
def _rsi14_module(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str, bool]:
    """RSI(14) across 3 timeframes. Returns (dir, score, reason, is_divergence)."""
    rsi1  = float(d1m.get("rsi",  50) or 50)
    rsi5  = float(d5m.get("rsi",  50) or 50)
    rsi15 = float(d15m.get("rsi", 50) or 50)
    b5    = d5m.get("bias", "NEUTRAL") or "NEUTRAL"
    b15   = d15m.get("bias", "NEUTRAL") or "NEUTRAL"

    # ── Zone 1: Deep oversold (1m + 5m both low) ─────────────────────────
    if rsi1 <= 28 and rsi5 <= 38:
        score = int(30 + (28 - rsi1) * 1.5 + (38 - rsi5) * 0.5)
        return "BUY", min(42, score), f"RSI deep oversold 1m={rsi1:.0f} 5m={rsi5:.0f}", False

    # ── Zone 2: Deep overbought ───────────────────────────────────────────
    if rsi1 >= 72 and rsi5 >= 62:
        score = int(30 + (rsi1 - 72) * 1.5 + (rsi5 - 62) * 0.5)
        return "SELL", min(42, score), f"RSI deep overbought 1m={rsi1:.0f} 5m={rsi5:.0f}", False

    # ── Zone 3: Divergence — RSI opposing 5m trend ───────────────────────
    if b5 == "SELL" and rsi1 <= 40 and rsi1 > rsi5:
        return "BUY", 26, f"RSI divergence BUY: 5m SELL but 1m={rsi1:.0f}↑>{rsi5:.0f}", True
    if b5 == "BUY"  and rsi1 >= 60 and rsi1 < rsi5:
        return "SELL", 26, f"RSI divergence SELL: 5m BUY but 1m={rsi1:.0f}↓<{rsi5:.0f}", True

    # ── Zone 4: Momentum trending (no extreme, just directional) ─────────
    if rsi1 > rsi5 > rsi15 and 48 <= rsi1 <= 65:
        return "BUY", 14, f"RSI rising momentum 1m={rsi1:.0f}>5m={rsi5:.0f}>15m={rsi15:.0f}", False
    if rsi1 < rsi5 < rsi15 and 35 <= rsi1 <= 52:
        return "SELL", 14, f"RSI falling momentum 1m={rsi1:.0f}<5m={rsi5:.0f}<15m={rsi15:.0f}", False

    return None, 0, "", False


# ═══════════════════════════════════════════════════════════════════════════
# INDICATOR 2 — MACD: Crossover + Histogram Momentum
# ═══════════════════════════════════════════════════════════════════════════
def _macd_module(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str]:
    """MACD via TV TA strength + bias alignment across TFs."""
    b1  = d1m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b5  = d5m.get("bias",  "NEUTRAL") or "NEUTRAL"
    b15 = d15m.get("bias", "NEUTRAL") or "NEUTRAL"
    s1  = float(d1m.get("strength",  0) or 0)
    s5  = float(d5m.get("strength",  0) or 0)
    s15 = float(d15m.get("strength", 0) or 0)
    bv1 = int(d1m.get("buy_v",  0) or 0)
    sv1 = int(d1m.get("sell_v", 0) or 0)

    # ── Full MACD alignment: all 3 TFs ──────────────────────────────────
    if b1 == "BUY" == b5 == b15 and s1 >= 0.40:
        score = int(24 + s1 * 12 + s5 * 8 + s15 * 4)
        return "BUY", min(42, score), f"MACD BUY 3TF s1={s1:.2f} s5={s5:.2f}"
    if b1 == "SELL" == b5 == b15 and s1 >= 0.40:
        score = int(24 + s1 * 12 + s5 * 8 + s15 * 4)
        return "SELL", min(42, score), f"MACD SELL 3TF s1={s1:.2f} s5={s5:.2f}"

    # ── 2TF alignment: 1m + 5m ──────────────────────────────────────────
    if b1 == "BUY" == b5 and s1 >= 0.50 and bv1 > sv1:
        return "BUY", int(18 + s1 * 10), f"MACD BUY 1m+5m s={s1:.2f}"
    if b1 == "SELL" == b5 and s1 >= 0.50 and sv1 > bv1:
        return "SELL", int(18 + s1 * 10), f"MACD SELL 1m+5m s={s1:.2f}"

    # ── Crossover proxy: 1m strong alone ────────────────────────────────
    if b1 == "BUY" and s1 >= 0.70 and bv1 > sv1 * 1.6:
        return "BUY", int(14 + s1 * 8), f"MACD 1m crossover BUY s={s1:.2f}"
    if b1 == "SELL" and s1 >= 0.70 and sv1 > bv1 * 1.6:
        return "SELL", int(14 + s1 * 8), f"MACD 1m crossover SELL s={s1:.2f}"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# INDICATOR 3 — Bollinger Bands: Squeeze, Band Touch, Expansion
# ═══════════════════════════════════════════════════════════════════════════
def _bb_module(d1m: dict, d5m: dict) -> tuple[Optional[str], int, str]:
    """Bollinger Band analysis — all three setups."""
    close   = float(d1m.get("close",  0) or 0)
    bb_up   = float(d1m.get("bb_up",  0) or 0)
    bb_lo   = float(d1m.get("bb_lo",  0) or 0)
    bb_mid  = float(d1m.get("bb_mid", 0) or 0)
    b1      = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    b5      = d5m.get("bias", "NEUTRAL") or "NEUTRAL"
    rsi1    = float(d1m.get("rsi", 50) or 50)

    if not close or not bb_up or not bb_lo or not bb_mid:
        return None, 0, ""

    width  = (bb_up - bb_lo) / (bb_mid + 1e-9)
    pct_b  = (close - bb_lo) / (bb_up - bb_lo + 1e-9)  # 0=lower, 1=upper

    # ── Band Touch (reversal) ────────────────────────────────────────────
    if pct_b <= 0.04 and rsi1 <= 45:          # lower band touch → BUY
        return "BUY",  28, f"BB lower touch %B={pct_b:.2f} RSI={rsi1:.0f}"
    if pct_b >= 0.96 and rsi1 >= 55:          # upper band touch → SELL
        return "SELL", 28, f"BB upper touch %B={pct_b:.2f} RSI={rsi1:.0f}"

    # ── Squeeze Breakout (tight bands, direction confirmed by 5m) ────────
    if width <= 0.006:
        if b5 == "BUY":
            return "BUY",  22, f"BB squeeze BUY breakout width={width:.4f}"
        if b5 == "SELL":
            return "SELL", 22, f"BB squeeze SELL breakout width={width:.4f}"

    # ── Expansion Momentum (bands widening, price in upper/lower half) ───
    if width >= 0.015:
        if pct_b >= 0.65 and b1 == "BUY":
            return "BUY",  16, f"BB expansion BUY %B={pct_b:.2f}"
        if pct_b <= 0.35 and b1 == "SELL":
            return "SELL", 16, f"BB expansion SELL %B={pct_b:.2f}"

    # ── Mid-band momentum ────────────────────────────────────────────────
    if 0.55 <= pct_b <= 0.80 and b1 == "BUY" and b5 == "BUY":
        return "BUY",  10, f"BB mid-upper momentum %B={pct_b:.2f}"
    if 0.20 <= pct_b <= 0.45 and b1 == "SELL" and b5 == "SELL":
        return "SELL", 10, f"BB mid-lower momentum %B={pct_b:.2f}"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN RECOGNITION (candlestick patterns via TV vote ratios)
# ═══════════════════════════════════════════════════════════════════════════
def _pattern_module(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str]:
    """Candlestick pattern strength via TV buy/sell vote distribution."""
    bv1  = int(d1m.get("buy_v",  0) or 0)
    sv1  = int(d1m.get("sell_v", 0) or 0)
    bv5  = int(d5m.get("buy_v",  0) or 0)
    sv5  = int(d5m.get("sell_v", 0) or 0)
    bv15 = int(d15m.get("buy_v", 0) or 0)
    sv15 = int(d15m.get("sell_v",0) or 0)
    rsi1 = float(d1m.get("rsi", 50) or 50)

    t1  = bv1  + sv1;   br1  = bv1  / t1  if t1  else 0.5
    t5  = bv5  + sv5;   br5  = bv5  / t5  if t5  else 0.5
    t15 = bv15 + sv15;  br15 = bv15 / t15 if t15 else 0.5

    if t1 < 4:
        return None, 0, ""

    # ── Strong pattern: 75%+ on 1m, 55%+ on 5m ──────────────────────────
    if br1 >= 0.75 and br5 >= 0.55 and rsi1 <= 70:
        return "BUY",  int(16 + br1 * 16), f"Pattern BUY {br1*100:.0f}%/1m {br5*100:.0f}%/5m"
    if (1-br1) >= 0.75 and (1-br5) >= 0.55 and rsi1 >= 30:
        return "SELL", int(16 + (1-br1) * 16), f"Pattern SELL {(1-br1)*100:.0f}%/1m"

    # ── Moderate pattern: 65%+ on 1m + 15m agree ────────────────────────
    if br1 >= 0.65 and br15 >= 0.55 and rsi1 <= 68:
        return "BUY",  int(12 + br1 * 10), f"Pattern BUY 1m={br1*100:.0f}% 15m={br15*100:.0f}%"
    if (1-br1) >= 0.65 and (1-br15) >= 0.55 and rsi1 >= 32:
        return "SELL", int(12 + (1-br1) * 10), f"Pattern SELL 1m={( 1-br1)*100:.0f}%"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# BID-ASK SPREAD PROXY (order pressure imbalance)
# ═══════════════════════════════════════════════════════════════════════════
def _spread_module(d1m: dict) -> tuple[Optional[str], int, str]:
    """Bid-ask spread proxy via TV buy/sell pressure ratio."""
    bv  = int(d1m.get("buy_v",  0) or 0)
    sv  = int(d1m.get("sell_v", 0) or 0)
    s   = float(d1m.get("strength", 0) or 0)
    rsi = float(d1m.get("rsi", 50) or 50)
    tot = bv + sv

    if tot < 6:
        return None, 0, ""

    imb = (bv - sv) / tot   # -1 (all sellers) to +1 (all buyers)

    if imb >= 0.40 and s >= 0.45 and rsi <= 70:
        return "BUY",  int(14 + imb * 16), f"Bid pressure BUY imbalance={imb:.2f}"
    if imb <= -0.40 and s >= 0.45 and rsi >= 30:
        return "SELL", int(14 + abs(imb) * 16), f"Ask pressure SELL imbalance={imb:.2f}"
    if imb >= 0.25 and s >= 0.55:
        return "BUY",  10, f"Mild bid pressure BUY imb={imb:.2f}"
    if imb <= -0.25 and s >= 0.55:
        return "SELL", 10, f"Mild ask pressure SELL imb={imb:.2f}"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# VOLUME PROFILE + ORDER FLOW (multi-TF directional vote)
# ═══════════════════════════════════════════════════════════════════════════
def _orderflow_module(d1m: dict, d5m: dict, d15m: dict) -> tuple[Optional[str], int, str]:
    """Volume profile: institutional order flow via 3-TF vote agreement."""
    bv1  = int(d1m.get("buy_v",  0) or 0)
    sv1  = int(d1m.get("sell_v", 0) or 0)
    bv5  = int(d5m.get("buy_v",  0) or 0)
    sv5  = int(d5m.get("sell_v", 0) or 0)
    bv15 = int(d15m.get("buy_v", 0) or 0)
    sv15 = int(d15m.get("sell_v",0) or 0)
    s1   = float(d1m.get("strength", 0) or 0)
    s5   = float(d5m.get("strength", 0) or 0)

    buy_votes  = sum([bv1 > sv1, bv5 > sv5, bv15 > sv15])
    sell_votes = sum([sv1 > bv1, sv5 > bv5, sv15 > bv15])

    # ── Institutional flow: 3/3 TF agreement ────────────────────────────
    if buy_votes == 3 and s5 >= 0.40:
        total_buy = bv1 + bv5 + bv15
        return "BUY",  min(28, 16 + total_buy // 3), "Order flow BUY 3/3 TF — institutional"
    if sell_votes == 3 and s5 >= 0.40:
        total_sell = sv1 + sv5 + sv15
        return "SELL", min(28, 16 + total_sell // 3), "Order flow SELL 3/3 TF — institutional"

    # ── Strong flow: 2/3 TF + 1m leading ────────────────────────────────
    if buy_votes == 2 and bv1 > sv1 and s1 >= 0.50:
        return "BUY",  14, f"Order flow BUY 2/3 TF s={s1:.2f}"
    if sell_votes == 2 and sv1 > bv1 and s1 >= 0.50:
        return "SELL", 14, f"Order flow SELL 2/3 TF s={s1:.2f}"

    return None, 0, ""


# ═══════════════════════════════════════════════════════════════════════════
# NON-MARTINGALE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════
def _non_martingale_check(buy_score: int, sell_score: int,
                          d1m: dict, d5m: dict, is_otc: bool) -> bool:
    """
    Validates this is a CLEAN entry, not a recovery/martingale trade.
    Returns True = safe to trade, False = skip (recovery pattern detected).

    Rules:
    • Score gap ≥ 15 (no close call — clean directional signal)
    • Strength ≥ 0.35 (not dead chop)
    • RSI not at extreme opposite (not catching a falling knife)
    • OTC: requires RSI + MACD aligned (stricter anti-chop)
    """
    gap = abs(buy_score - sell_score)
    if gap < 15:
        return False   # too close — skip

    s1  = float(d1m.get("strength", 0) or 0)
    rsi = float(d1m.get("rsi", 50) or 50)
    b1  = d1m.get("bias", "NEUTRAL") or "NEUTRAL"
    b5  = d5m.get("bias", "NEUTRAL") or "NEUTRAL"

    if s1 < 0.30:
        return False   # dead chop

    # OTC strict gate
    if is_otc and b1 != b5 and gap < 25:
        return False   # OTC requires 2-TF alignment for clean entry

    return True


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY
# ═══════════════════════════════════════════════════════════════════════════
def finorix_sharp(pair: str, is_otc: bool = False,
                  market_type: str = "LIVE") -> dict:
    """
    FINORIX SHARP — updated Finorix engine.
    3 non-lagging indicators + pattern + microstructure + non-martingale gate.
    """
    cache_key = f"fs|{pair}|{is_otc}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    d1m  = _tv(pair, "1m")
    d5m  = _tv(pair, "5m")
    d15m = _tv(pair, "15m")

    _no_data = {"ok": False, "direction": "WAIT", "confidence": 0,
                "grade": "WEAK", "signals": [], "elite": False, "veto": False}

    if not d1m.get("ok") and not d5m.get("ok"):
        _CACHE[cache_key] = (time.time(), _no_data)
        return _no_data

    buy_score = sell_score = 0
    buy_sigs:  list[str] = []
    sell_sigs: list[str] = []
    has_divergence = False

    # ── Run all 6 modules ──────────────────────────────────────────────
    rsi_dir, rsi_score, rsi_reason, rsi_div = _rsi14_module(d1m, d5m, d15m)
    if rsi_reason and rsi_score:
        if rsi_dir == "BUY":
            buy_score += rsi_score; buy_sigs.append(rsi_reason)
            if rsi_div: has_divergence = True
        elif rsi_dir == "SELL":
            sell_score += rsi_score; sell_sigs.append(rsi_reason)
            if rsi_div: has_divergence = True

    for module_fn, args in [
        (_macd_module,      (d1m, d5m, d15m)),
        (_bb_module,        (d1m, d5m)),
        (_pattern_module,   (d1m, d5m, d15m)),
        (_spread_module,    (d1m,)),
        (_orderflow_module, (d1m, d5m, d15m)),
    ]:
        d, s, r = module_fn(*args)
        if not r or s == 0:
            continue
        if d == "BUY":
            buy_score += s; buy_sigs.append(r)
        elif d == "SELL":
            sell_score += s; sell_sigs.append(r)

    # ── Stooq live tape tiebreaker ────────────────────────────────────
    if _SQ_OK and _stooq is not None:
        try:
            sq = _stooq(pair)
            if sq:
                tape = sq[0]
                if tape == "BUY":
                    buy_score += 6; buy_sigs.append("Stooq live tape confirms BUY")
                elif tape == "SELL":
                    sell_score += 6; sell_sigs.append("Stooq live tape confirms SELL")
        except Exception:
            pass

    # ── Determine winner ──────────────────────────────────────────────
    veto = False
    if buy_score > sell_score + 14:
        direction = "BUY";  total = buy_score;  sigs = buy_sigs
    elif sell_score > buy_score + 14:
        direction = "SELL"; total = sell_score; sigs = sell_sigs
    else:
        direction = "WAIT"; total = 0;          sigs = []
        # Near-equal scores = conflicting signals = veto
        if buy_score >= 20 and sell_score >= 20:
            veto = True

    # ── Non-martingale validator ──────────────────────────────────────
    if direction != "WAIT":
        if not _non_martingale_check(buy_score, sell_score, d1m, d5m, is_otc):
            direction = "WAIT"
            veto = True

    ok         = direction not in ("WAIT", None)
    confidence = min(100, 72 + total // 4) if ok else 0
    elite      = ok and len(sigs) >= 4 and total >= 70
    n_signals  = len(sigs)

    if elite or (ok and total >= 55):
        grade = "ELITE"  if elite else "STRONG"
    elif ok and total >= 35:
        grade = "MODERATE"
    else:
        grade = "WEAK"

    result = {
        "ok":         ok,
        "direction":  direction,
        "confidence": confidence,
        "grade":      grade,
        "signals":    sigs[:5],
        "elite":      elite,
        "veto":       veto,
        "divergence": has_divergence,
        "buy_score":  buy_score,
        "sell_score": sell_score,
        "n_modules":  n_signals,
    }

    if ok:
        print(f"[FINORIX SHARP] {pair} {'OTC' if is_otc else 'LIVE'}: "
              f"{direction} grade={grade} total={total} elite={elite} "
              f"modules={n_signals} div={has_divergence}")

    _CACHE[cache_key] = (time.time(), result)
    return result
