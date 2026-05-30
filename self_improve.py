"""SUPREME PRO AI — Self-Improving Engine (V9)

Every signal is tracked. Outcomes are auto-detected by re-checking the live
price after expiry. The engine then learns per-pair, per-engine win rates and
tightens or relaxes its analysis thresholds accordingly.

The more volatile the market and the more signals generated, the faster the
bot improves.

PIPELINE
────────
Signal generated → record_signal() → schedule_outcome_check() (async)
After expiry     → check live price → mark win/loss → update_learning()
Next signal      → get_adaptive_thresholds() uses updated win-rate stats

VOLATILITY MODES (ATR as % of price)
─────────────────────────────────────
  low      (< 0.40%)  → PA threshold – 0.5  (slightly more permissive)
  normal   (0.40–1.0%)→ base thresholds
  high     (1.0–2.5%) → PA threshold + 1.5, OTC vote + 1  (stricter)
  extreme  (> 2.5%)   → PA threshold + 3.0, OTC vote + 2  (very strict)

ADAPTIVE LEARNING (per pair × engine, 20+ samples required)
─────────────────────────────────────────────────────────────
  win_rate < 52%  → raise threshold (harder to fire that engine)
  win_rate > 76%  → lower threshold (accept more opportunities)
  Monthly retune  → full recalibration from all accumulated outcomes

AUTO-OUTCOME DETECTION
──────────────────────
  Binary: after expiry_minutes + 45 s buffer, compare live price to entry.
          BUY + price_now > entry → WIN ; SELL + price_now < entry → WIN.
          100 % automatic — no user input needed.
  Forex:  SL / TP1 hit detection runs in the existing expiry_watcher.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import database as db

log = logging.getLogger(__name__)

# ── Adaptive parameter hard bounds (ELITE SUPREMACY — aggressive self-correction) ──
PA_WT_BASE    = 7.0   # price-action weighted-score gate  (base, raised: 5→7)
PA_WT_MIN     = 5.5   # never go below — avoids noise flood (raised: 3.5→5.5)
PA_WT_MAX     = 14.0  # never go above — would starve signal flow (raised: 9→14)

OTC_VOTE_BASE = 5     # OTC reversal vote gate  (base, raised: 3→5)
OTC_VOTE_MIN  = 4     # minimum floor (raised: 3→4)
OTC_VOTE_MAX  = 9     # maximum ceiling (raised: 6→9)

# ── Volatility → threshold delta map ──────────────────────────────────────
# Higher penalties for bad market conditions — bot avoids trading noise
_VOL_ADJUST: Dict[str, Dict] = {
    "low":     {"pa": -0.5, "otc":  0},
    "normal":  {"pa":  0.0, "otc":  0},
    "high":    {"pa": +2.5, "otc":  2},   # was +1.5/+1 — more aggressive
    "extreme": {"pa": +4.5, "otc":  3},   # was +3.0/+2 — extreme markets blocked
}

# ── Learning confidence gate ───────────────────────────────────────────────
# Target win-rate is 85 %. The AI self-corrects the moment it dips below 85 %.
# This ensures the bot is ALWAYS operating at elite level. Even a few losses
# cause immediate threshold tightening — back-to-back wins only mode.
MIN_SAMPLE    = 8     # outcomes before adapting (8 trades = fast learning)
TIGHTEN_RATE  = 0.85  # below 85 % win-rate → raise threshold (was 0.80)
RELAX_RATE    = 0.93  # above 93 % win-rate → slightly relax (was 0.87)

# ── ATR cache (avoid hammering yfinance every call) ───────────────────────
_ATR_CACHE: Dict[str, Tuple[float, str]] = {}   # pair → (atr_pct, vol_mode, ts)
_ATR_TTL = 120.0   # seconds


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOLATILITY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _compute_atr_pct(pair: str) -> Tuple[float, str]:
    """Return (atr_as_pct_of_price, vol_mode_label).

    Uses the last 14 × 15-minute bars to compute a classic ATR.  Falls back
    to (0.0, 'normal') on any data failure so the rest of the engine is
    unaffected.
    """
    try:
        from live_prices import yf_ticker, get_live_price
        import yfinance as yf  # type: ignore

        ticker_sym = yf_ticker(pair)
        if not ticker_sym:
            return 0.0, "normal"

        df = yf.download(ticker_sym, period="1d", interval="15m",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return 0.0, "normal"

        hi  = df["High"].astype(float)
        lo  = df["Low"].astype(float)
        cl  = df["Close"].astype(float)
        cl1 = cl.shift(1)

        tr = (hi - lo).combine(
            (hi - cl1).abs(), max
        ).combine(
            (lo - cl1).abs(), max
        )
        atr14 = tr.rolling(14, min_periods=3).mean().iloc[-1]
        price  = cl.iloc[-1]
        if price <= 0:
            return 0.0, "normal"

        atr_pct = float(atr14 / price * 100.0)

        if atr_pct < 0.40:
            mode = "low"
        elif atr_pct < 1.00:
            mode = "normal"
        elif atr_pct < 2.50:
            mode = "high"
        else:
            mode = "extreme"

        return round(atr_pct, 4), mode

    except Exception as exc:
        log.debug("[SelfImprove] ATR computation failed for %s: %s", pair, exc)
        return 0.0, "normal"


def get_vol_mode(pair: str) -> Tuple[float, str]:
    """Return (atr_pct, vol_mode) with 2-minute cache."""
    now = time.time()
    cached = _ATR_CACHE.get(pair)
    if cached and (now - cached[2]) < _ATR_TTL:
        return cached[0], cached[1]

    atr_pct, mode = _compute_atr_pct(pair)
    _ATR_CACHE[pair] = (atr_pct, mode, now)
    return atr_pct, mode


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — ADAPTIVE THRESHOLD CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

def get_adaptive_thresholds(pair: str) -> Dict:
    """Return current analysis thresholds for this pair.

    Combines:
      1. Base thresholds (PA_WT_BASE, OTC_VOTE_BASE)
      2. Volatility mode adjustment
      3. Per-pair learning adjustment (if ≥ MIN_SAMPLE outcomes recorded)

    Returns:
        {
            "pa_threshold":   float,   # min weighted score for PA engine to fire
            "otc_vote_min":   int,     # min votes for OTC reversal engine
            "vol_mode":       str,     # low / normal / high / extreme
            "atr_pct":        float,   # current ATR as % of price
            "learn_pa":       float,   # learned PA delta applied
            "learn_otc":      int,     # learned OTC delta applied
        }
    """
    atr_pct, vol_mode = get_vol_mode(pair)
    vol_adj = _VOL_ADJUST.get(vol_mode, _VOL_ADJUST["normal"])

    pa_thr  = PA_WT_BASE  + vol_adj["pa"]
    otc_min = OTC_VOTE_BASE + vol_adj["otc"]

    learn_pa  = 0.0
    learn_otc = 0

    # Per-engine learned adjustments (both pa_v9 and otc_reversal)
    for engine, is_otc_engine in [("pa_v9", False), ("otc_reversal", True)]:
        stats = db.get_ai_engine_stats(pair, engine)
        if not stats:
            continue
        total = stats.get("total_signals", 0)
        if total < MIN_SAMPLE:
            continue

        wins = stats.get("win_count", 0)
        win_rate = wins / total

        if win_rate < TIGHTEN_RATE:
            # Engine below 80 % win-rate on this pair → AGGRESSIVE tightening.
            # severity = how far below 80 %:
            #   79 % → 0.01 gap → small nudge
            #   65 % → 0.15 gap → medium push
            #   50 % → 0.30 gap → large push (nearly random = nearly max gate)
            severity = max(0.0, TIGHTEN_RATE - win_rate)   # 0.0 .. 0.80
            # Scale: every 10 pp below 80 % adds ~1.0 to PA threshold (up to +4.0)
            delta = round(min(4.0, severity * 13.0), 1)
            if is_otc_engine:
                learn_otc = max(learn_otc, int(delta))
            else:
                learn_pa  = max(learn_pa,  delta)
            log.info("[SelfImprove] AUTO-TIGHTEN %s/%s wr=%.1f%% (below 80%%) Δ=+%.1f",
                     pair, engine, win_rate * 100, delta)

        elif win_rate > RELAX_RATE:
            # Win-rate above 87 % — engine is very accurate, ease gate slightly
            # so it can generate slightly more signals without losing quality.
            severity = win_rate - RELAX_RATE               # 0.0 .. ~0.13
            delta = round(min(0.5, severity * 4.0), 1)    # max relax = -0.5 PA / -1 OTC
            if is_otc_engine:
                learn_otc = min(learn_otc, -int(delta))
            else:
                learn_pa  = min(learn_pa,  -delta)
            log.info("[SelfImprove] AUTO-RELAX %s/%s wr=%.1f%% (above 87%%) Δ=-%.1f",
                     pair, engine, win_rate * 100, delta)

    pa_thr  = max(PA_WT_MIN,   min(PA_WT_MAX,  pa_thr  + learn_pa))
    otc_min = max(OTC_VOTE_MIN, min(OTC_VOTE_MAX, otc_min + learn_otc))

    return {
        "pa_threshold": pa_thr,
        "otc_vote_min": otc_min,
        "vol_mode":     vol_mode,
        "atr_pct":      atr_pct,
        "learn_pa":     learn_pa,
        "learn_otc":    learn_otc,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — SIGNAL RECORDING
# ══════════════════════════════════════════════════════════════════════════════

def record_signal(
    user_id:        int,
    pair:           str,
    market:         str,
    direction:      str,
    timeframe:      str,
    engine:         str,   # pa_v9 | otc_reversal | vol_sniper | bin_sniper | mtf | fallback
    confidence:     int,
    weighted_score: float,
    entry_price:    Optional[float],
    expiry_minutes: int,
    atr_pct:        float,
    vol_mode:       str,
) -> int:
    """Insert a signal into signal_outcomes and return its row ID."""
    try:
        sig_id = db.insert_signal_outcome(
            user_id        = user_id,
            pair           = pair,
            market         = market,
            direction      = direction,
            timeframe      = timeframe,
            engine         = engine,
            confidence     = confidence,
            weighted_score = weighted_score,
            entry_price    = entry_price,
            expiry_minutes = expiry_minutes,
            atr_pct        = atr_pct,
            vol_mode       = vol_mode,
            timestamp      = int(time.time()),
        )
        log.debug("[SelfImprove] Recorded signal #%d %s %s %s wt=%.1f vol=%s",
                  sig_id, pair, engine, direction, weighted_score, vol_mode)
        return sig_id
    except Exception as exc:
        log.warning("[SelfImprove] record_signal failed: %s", exc)
        return -1


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — AUTO OUTCOME DETECTION (binary)
# ══════════════════════════════════════════════════════════════════════════════

def _detect_candle_pattern(c_open: float, c_high: float,
                            c_low: float, c_close: float,
                            entry_price: float) -> str:
    """Return 'refund', 'green', or 'red' for the given OHLC candle.

    Refund patterns (indecisive candles that brokers typically void):
      • Doji            — body < 10 % of full range
      • Dragon Fly Doji — long lower wick, tiny upper wick + tiny body
      • Small Weak Doji — very tight range AND tiny body (< 0.02 % of price)
    Otherwise returns the candle colour ('green' close≥open, 'red' close<open).
    """
    candle_range = c_high - c_low
    if candle_range <= 0:
        return "refund"

    body_size  = abs(c_close - c_open)
    body_ratio = body_size / candle_range
    lower_wick = min(c_open, c_close) - c_low
    upper_wick = c_high - max(c_open, c_close)

    price_ref = entry_price if entry_price > 0 else max(c_close, 0.0001)

    # Doji: body smaller than 10 % of total candle range
    if body_ratio < 0.10:
        return "refund"

    # Dragon Fly Doji: long lower wick (>55 % range), tiny upper wick, tiny body
    if (body_ratio < 0.13
            and lower_wick > 0.55 * candle_range
            and upper_wick < 0.15 * candle_range):
        return "refund"

    # Small Weak Doji: extremely tight range (<0.02 % of price) + small body
    if body_ratio < 0.15 and candle_range < 0.0002 * price_ref:
        return "refund"

    return "green" if c_close >= c_open else "red"


async def _check_and_record_outcome(
    signal_id:      int,
    pair:           str,
    market:         str,
    direction:      str,
    entry_price:    float,
    expiry_minutes: int,
    engine:         str,
    user_id:        int  = 0,
    bot              = None,
    chat_id:        int  = 0,
) -> None:
    """Wait expiry + 45 s buffer then evaluate outcome via candle colour.

    Resolution order:
      1. Fetch the last completed 1-minute candle at expiry time.
      2. If candle is Doji / Dragon Fly / Small Weak Doji → REFUND (no alert).
      3. Check candle colour: CALL wins on green candle, PUT wins on red candle.
      4. Fallback: price-based comparison (close vs entry) when candle data
         is unavailable (OTC pairs, data gaps, yfinance outage, etc.).

    Runs as a fire-and-forget asyncio task — never raises to the caller.
    When user_id / bot / chat_id are supplied the daily alert system is
    notified of the outcome so it can fire loss / win streak messages.
    """
    await asyncio.sleep(expiry_minutes * 60 + 45)

    try:
        from live_prices import get_live_price, yf_ticker
        price_now = get_live_price(pair)

        # ── Candle-colour primary check ───────────────────────────────────
        candle_outcome: Optional[str] = None   # "win" | "loss" | "refund"
        try:
            import yfinance as _yf
            ticker_sym = yf_ticker(pair)
            if ticker_sym:
                df = _yf.download(ticker_sym, period="1d", interval="1m",
                                  progress=False, auto_adjust=True)
                if df is not None and len(df) >= 2:
                    # Use the last *completed* candle (second-to-last row;
                    # the last row is the still-forming current candle).
                    row = df.iloc[-2]
                    cols = df.columns

                    def _gcol(name: str) -> Optional[float]:
                        lo = name.lower()
                        hi = name.capitalize()
                        if lo in cols:
                            return float(row[lo])
                        if hi in cols:
                            return float(row[hi])
                        for c in cols:
                            if isinstance(c, tuple) and c[0].lower() == lo:
                                return float(row[c])
                        return None

                    c_o = _gcol("open")
                    c_h = _gcol("high")
                    c_l = _gcol("low")
                    c_c = _gcol("close")

                    if all(v is not None for v in [c_o, c_h, c_l, c_c]):
                        pattern = _detect_candle_pattern(
                            c_o, c_h, c_l, c_c,      # type: ignore[arg-type]
                            entry_price or 0.0,
                        )
                        if pattern == "refund":
                            db.mark_signal_outcome(signal_id, "refund")
                            log.info(
                                "[SelfImprove] Signal #%d %s %s → REFUND "
                                "(Doji/DragonFly/WeakDoji — no alert sent)",
                                signal_id, pair, direction,
                            )
                            return   # skip daily-alert hook entirely

                        # CALL/BUY wins on green candle; PUT/SELL wins on red
                        if direction == "BUY":
                            won = (pattern == "green")
                        else:
                            won = (pattern == "red")

                        candle_outcome = "win" if won else "loss"
                        log.info(
                            "[SelfImprove] Signal #%d %s %s candle=%s → %s",
                            signal_id, pair, direction,
                            pattern.upper(), candle_outcome.upper(),
                        )
        except Exception as _ce:
            log.debug("[SelfImprove] Candle pattern check failed for %s: %s",
                      pair, _ce)

        # ── Price-based fallback (OTC / data gap) ─────────────────────────
        if candle_outcome is not None:
            outcome = candle_outcome
            won     = (outcome == "win")
        else:
            if price_now is None or entry_price is None or entry_price <= 0:
                db.mark_signal_outcome(signal_id, "unknown")
                return
            if direction == "BUY":
                won = price_now > entry_price
            else:
                won = price_now < entry_price
            outcome = "win" if won else "loss"
            log.info(
                "[SelfImprove] Signal #%d %s %s → %s  "
                "(price fallback entry=%.5f now=%.5f)",
                signal_id, pair, direction, outcome.upper(),
                entry_price, price_now or 0.0,
            )

        db.mark_signal_outcome(signal_id, outcome, price_now)

        # Feed the learning engine
        _update_learning(pair, engine, won)

        log.info("[SelfImprove] Signal #%d %s %s → %s  (entry=%.5f now=%.5f)",
                 signal_id, pair, direction, outcome.upper(),
                 entry_price, price_now or 0.0)

        # ── Daily Alert hook (binary signals only) ────────────────────────
        if bot is not None and chat_id > 0 and user_id > 0:
            try:
                from daily_alert import record_outcome as _daily_alert_fn
                await _daily_alert_fn(user_id, outcome, market, bot, chat_id)
            except Exception as _da_exc:
                log.warning("[SelfImprove] daily alert hook error: %s", _da_exc)

    except Exception as exc:
        log.warning("[SelfImprove] outcome check failed for #%d: %s", signal_id, exc)
        try:
            db.mark_signal_outcome(signal_id, "unknown")
        except Exception:
            pass


def schedule_outcome_check(
    signal_id:      int,
    pair:           str,
    market:         str,
    direction:      str,
    entry_price:    Optional[float],
    expiry_minutes: int,
    engine:         str,
    user_id:        int  = 0,
    bot              = None,
    chat_id:        int  = 0,
) -> None:
    """Fire-and-forget — schedule the outcome check coroutine.

    Safe to call from sync code; grabs the running loop automatically.
    Skips if entry_price is unavailable (can't evaluate outcome).
    Pass user_id / bot / chat_id to enable daily-alert notifications.
    """
    if signal_id < 0 or entry_price is None or entry_price <= 0:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            _check_and_record_outcome(
                signal_id, pair, market, direction,
                entry_price, expiry_minutes, engine,
                user_id=user_id, bot=bot, chat_id=chat_id,
            )
        )
    except RuntimeError:
        pass  # no running loop (e.g. test environment)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — LEARNING UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def _update_learning(pair: str, engine: str, won: bool) -> None:
    """Increment win/loss counter for (pair, engine) in ai_learning table."""
    try:
        db.update_ai_engine_learning(pair, engine, won)
        # Invalidate ATR cache so next signal re-computes thresholds fresh
        _ATR_CACHE.pop(pair, None)
    except Exception as exc:
        log.warning("[SelfImprove] update_learning failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — MONTHLY AUTO-RETUNE
# ══════════════════════════════════════════════════════════════════════════════

def monthly_retune_if_due() -> Optional[str]:
    """Run a full retune if it has been ≥ 30 days since the last one.

    Returns a short summary string (for logging) or None if skipped.
    """
    try:
        last_ts = db.get_setting("last_ai_retune_ts")
        if last_ts and (time.time() - float(last_ts)) < 30 * 86_400:
            return None  # not due yet

        return _run_retune()
    except Exception as exc:
        log.warning("[SelfImprove] monthly_retune_if_due error: %s", exc)
        return None


def _run_retune() -> str:
    """Full retune: analyse last-30-day outcomes, log summary, reset stats."""
    cutoff = int(time.time()) - 30 * 86_400
    rows = db.get_signal_outcomes_since(cutoff)

    if not rows:
        db.set_setting("last_ai_retune_ts", str(int(time.time())))
        return "Retune skipped — no outcomes recorded yet."

    # Aggregate stats per (pair, engine)
    stats: Dict[str, Dict] = {}
    for row in rows:
        key = f"{row['pair']}|{row['engine']}"
        if key not in stats:
            stats[key] = {"pair": row["pair"], "engine": row["engine"],
                          "win": 0, "loss": 0, "unknown": 0}
        if row["outcome"] == "win":
            stats[key]["win"] += 1
        elif row["outcome"] == "loss":
            stats[key]["loss"] += 1
        else:
            stats[key]["unknown"] += 1

    pairs_analyzed = len({v["pair"] for v in stats.values()})
    engines_ok = 0

    summary_lines = ["📊 MONTHLY AI RETUNE REPORT", ""]
    for key, s in sorted(stats.items()):
        total = s["win"] + s["loss"]
        if total < 5:
            continue
        wr = s["win"] / total
        wr_pct = f"{wr*100:.1f}%"
        grade = "✅" if wr >= 0.65 else ("⚠️" if wr >= 0.52 else "❌")
        summary_lines.append(
            f"{grade} {s['pair']} / {s['engine']}: "
            f"{s['win']}W {s['loss']}L  WR={wr_pct}"
        )
        engines_ok += 1

    # Persist new stats so the adaptive engine re-reads them
    db.set_setting("last_ai_retune_ts", str(int(time.time())))

    summary = "\n".join(summary_lines)
    log.info("[SelfImprove] Monthly retune complete. %d pairs, %d engine combos.",
             pairs_analyzed, engines_ok)

    # Store retune log
    try:
        db.insert_retune_log(pairs_analyzed, engines_ok, summary)
    except Exception:
        pass

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — ADMIN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def get_improvement_report(days: int = 7) -> str:
    """Generate a human-readable improvement report for the admin.

    Shows per-pair/engine win rates, volatility distribution, and the
    current adaptive thresholds for the top 5 most-active pairs.
    """
    cutoff = int(time.time()) - days * 86_400
    rows = db.get_signal_outcomes_since(cutoff)

    if not rows:
        return (
            f"🤖 <b>AI SELF-IMPROVE ENGINE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"No tracked outcomes yet for the last {days} days.\n"
            f"Signals are being recorded — results appear after expiry."
        )

    stats: Dict[str, Dict] = {}
    vol_dist: Dict[str, int] = {"low": 0, "normal": 0, "high": 0, "extreme": 0}

    for row in rows:
        key = f"{row['pair']}|{row['engine']}"
        if key not in stats:
            stats[key] = {"pair": row["pair"], "engine": row["engine"],
                          "win": 0, "loss": 0, "unk": 0}
        if row["outcome"] == "win":
            stats[key]["win"] += 1
        elif row["outcome"] == "loss":
            stats[key]["loss"] += 1
        else:
            stats[key]["unk"] += 1

        vm = row.get("vol_mode") or "normal"
        vol_dist[vm] = vol_dist.get(vm, 0) + 1

    total_signals = len(rows)
    total_wins  = sum(s["win"] for s in stats.values())
    total_losses = sum(s["loss"] for s in stats.values())
    overall_wr  = (total_wins / max(1, total_wins + total_losses)) * 100

    lines = [
        f"🤖 <b>AI SELF-IMPROVE ENGINE — {days}-DAY REPORT</b>",
        f"━━━━━━━━━━━━━━━━━━━",
        f"📡 Signals tracked: <b>{total_signals}</b>",
        f"🏆 Overall win rate: <b>{overall_wr:.1f}%</b>  "
        f"({total_wins}W / {total_losses}L)",
        f"",
        f"🌡 <b>Volatility distribution</b>",
        f"  Low={vol_dist['low']}  Normal={vol_dist['normal']}  "
        f"High={vol_dist['high']}  Extreme={vol_dist['extreme']}",
        f"",
        f"📈 <b>Engine performance (top pairs)</b>",
    ]

    # Sort by volume, show top 12
    sorted_stats = sorted(stats.values(),
                          key=lambda x: x["win"] + x["loss"], reverse=True)
    for s in sorted_stats[:12]:
        total = s["win"] + s["loss"]
        if total < 3:
            continue
        wr = s["win"] / total
        bar_filled = int(wr * 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        grade = "🟢" if wr >= 0.65 else ("🟡" if wr >= 0.52 else "🔴")
        lines.append(
            f"{grade} <b>{s['pair']}</b> · {s['engine']}\n"
            f"    [{bar}] {wr*100:.1f}%  ({s['win']}W/{s['loss']}L)"
        )

    # Show adaptive thresholds for busiest pairs
    top_pairs = list({s["pair"] for s in sorted_stats[:5]})
    if top_pairs:
        lines += ["", "⚙️ <b>Current adaptive thresholds</b>"]
        for p in top_pairs:
            t = get_adaptive_thresholds(p)
            lines.append(
                f"  <b>{p}</b>: PA≥{t['pa_threshold']:.1f}  "
                f"OTC≥{t['otc_vote_min']}  vol={t['vol_mode']}  "
                f"ATR={t['atr_pct']:.3f}%"
            )

    # Last retune
    last_ts = db.get_setting("last_ai_retune_ts")
    if last_ts:
        last_dt = datetime.utcfromtimestamp(float(last_ts))
        lines.append(f"\n🔄 Last retune: {last_dt.strftime('%Y-%m-%d %H:%M')} UTC")
    else:
        lines.append("\n🔄 First retune not yet run (due after 30 days).")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — BACKGROUND PENDING CHECKER
# (picks up any outcomes that were missed if the bot restarted mid-expiry)
# ══════════════════════════════════════════════════════════════════════════════

async def recover_pending_outcomes() -> None:
    """On bot startup, re-schedule outcome checks for signals whose expiry
    window has already passed but whose outcome is still NULL.

    This handles the case where the bot was restarted mid-expiry.
    """
    try:
        pending = db.get_pending_outcomes()
        for row in pending:
            remaining_sleep = max(
                0,
                (row["timestamp"] + row["expiry_minutes"] * 60 + 45) - int(time.time())
            )
            entry = row.get("entry_price") or 0.0
            if entry <= 0:
                db.mark_signal_outcome(row["id"], "unknown")
                continue

            # If remaining time is 0, the check runs immediately
            async def _deferred(r=row, sleep=remaining_sleep):
                if sleep > 0:
                    await asyncio.sleep(sleep)
                await _check_and_record_outcome(
                    r["id"], r["pair"], r["market"], r["direction"],
                    r["entry_price"], r["expiry_minutes"], r["engine"],
                )

            asyncio.get_event_loop().create_task(_deferred())

        if pending:
            log.info("[SelfImprove] Recovered %d pending outcome checks.", len(pending))

    except Exception as exc:
        log.warning("[SelfImprove] recover_pending_outcomes error: %s", exc)
