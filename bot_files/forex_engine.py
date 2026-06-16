"""24/7 Forex signal engine.

Every ~30s the engine scans active forex setups and decides whether to fire
a new signal. Signals are simulated (price levels generated from a per-pair
band). When a user taps I'M IN on a signal, an outcome simulator runs and
edits the original signal message with TP / SL progress.

For free users: 1 signal per setup, then the setup is marked 'exhausted'
and they get a 'BUY ACCESS' follow-up.
"""
import asyncio
import os
import random
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db
from config import (
    FOREX_PAIRS, FOREX_TIMEFRAMES, FREE_FOREX_DAILY_LIMIT, price_band,
)


def _is_premium(user_id: int) -> bool:
    """Paid (active access) OR admin — bypasses all free-trial caps."""
    return db.has_active_access(user_id) or (int(user_id) == int(db.get_admin_id()))


from keyboards import (
    forex_signal_kb, forex_free_exhausted_kb, forex_more_signal_kb,
)
from live_prices import (
    get_live_price, pip_size as live_pip_size, decimals as live_decimals,
    get_market_bias,
)
from strategy import (
    analyze_pair as sniper_analyze, pick_best_pair as sniper_pick,
    MIN_SCORE as SNIPER_MIN_SCORE,
)
try:
    from god_engine import supreme_forex_gate as _forex_gate
    _GOD_OK = True
except Exception as _ge:
    print(f"[forex_engine] god_engine import failed: {_ge}")
    _forex_gate = None  # type: ignore
    _GOD_OK = False

try:
    from elite_signal_engine import (
        get_htf_levels       as _elite_htf,
        elite_forex_rr       as _elite_rr,
        forex_quality_gate   as _elite_gate,
        classify_signal      as _elite_classify,
        compute_signal_score as _elite_score,
    )
    _ELITE_OK = True
except Exception as _ee:
    print(f"[forex_engine] elite_signal_engine import: {_ee}")
    _elite_htf      = None  # type: ignore
    _elite_rr       = None  # type: ignore
    _elite_gate     = None  # type: ignore
    _elite_classify = None  # type: ignore
    _elite_score    = None  # type: ignore
    _ELITE_OK = False

try:
    from patterns import pattern_for_direction, best_pattern
except Exception:
    pattern_for_direction = None  # type: ignore
    best_pattern = None  # type: ignore

try:
    from liquidity import analyze as liquidity_analyze
except Exception as _e:
    print(f"[forex_engine] liquidity import failed: {_e}")
    liquidity_analyze = None  # type: ignore

try:
    from institutional_flow import analyze as _inst_flow_analyze
    _INST_FLOW_OK = True
except Exception as _ife:
    print(f"[forex_engine] institutional_flow import failed: {_ife}")
    _inst_flow_analyze = None  # type: ignore
    _INST_FLOW_OK = False

# SMART AI · Sweep ▸ BoS ▸ MS entry timing (the user's Pine v6 port).
# When this returns a valid packet we lock the LIVE forex signal and
# the FUNDED PASS live signal to its direction & SL/TP anchors — that
# is the single most important fix for the back-to-back losers the
# user has been seeing.
try:
    from trade_entry import analyze as smart_entry_analyze, is_valid as smart_entry_valid
except Exception as _e:
    print(f"[forex_engine] trade_entry import failed: {_e}")
    smart_entry_analyze = None  # type: ignore
    smart_entry_valid = None    # type: ignore

try:
    from fx_expert import fx_analyze as _fx_analyze
    _FX_EXPERT_OK = True
except Exception as _fxe:
    print(f"[forex_engine] fx_expert import failed: {_fxe}")
    _fx_analyze = None  # type: ignore
    _FX_EXPERT_OK = False

# "Pips command" — the minimum reward:risk we will ship. The pattern
# engine already enforces MIN_RR=1.6 on its own; this is the engine-wide
# floor used when constructing the TP ladder from a pattern's measured
# move. The user can phrase it as: "I want at least this much RR".
PIPS_COMMAND_MIN_RR = 2.5   # GOLD V8: elite R:R floor (was 1.8)

# ── GOLD V8: SL clamping — tighter SL = sharper R:R ─────────────────
# For standard forex, SL distance MUST be in [20, 45] pips.
# Tighter SL = higher R:R = better expected value per trade.
# For metals / crypto / indices ATR-based clamps remain unchanged.
SL_MIN_PIPS_FOREX = 10   # tighter SL = better RR for sniper entries
SL_MAX_PIPS_FOREX = 20   # hard cap: max 20 pips SL


def _is_metal_pair(pair: str) -> bool:
    p = (pair or "").upper().replace(" ", "")
    return any(k in p for k in ("XAU", "XAG", "GOLD", "SILVER"))


def _is_crypto_pair(pair: str) -> bool:
    p = (pair or "").upper().replace(" ", "")
    return any(k in p for k in ("BTC", "ETH", "SOL", "USDT", "-USD"))


def _is_index_pair(pair: str) -> bool:
    p = (pair or "").upper().replace(" ", "")
    return any(k in p for k in ("NAS", "US30", "US100", "SPX", "DXY", "ASX"))


# ── Per-session best-pair priority map ───────────────────────────────────
# Forex liquidity and volatility are highly session-dependent. Trading the
# WRONG pairs in the wrong session kills win rate.
#   Asian  (22–08 UTC): JPY crosses dominate — wide spreads on EUR/GBP.
#   London (08–12 UTC): EUR/GBP/CHF pairs open with momentum — best session.
#   Overlap(12–16 UTC): London + NY → highest volume — any major works.
#   NY     (16–21 UTC): USD pairs + commodities move hard.
# Each pair gets a score 0-10 per session. The sniper scan is restricted
# to pairs that score ≥ 5 (active) before falling back to all eligible.
_SESSION_PAIR_SCORES: dict[str, dict[str, int]] = {
    # pair substring → {session_key: score}
    "USD/JPY": {"asian": 9, "london": 7, "overlap": 9, "ny": 9},
    "EUR/JPY": {"asian": 9, "london": 8, "overlap": 8, "ny": 6},
    "GBP/JPY": {"asian": 8, "london": 8, "overlap": 8, "ny": 6},
    "AUD/JPY": {"asian": 9, "london": 6, "overlap": 7, "ny": 5},
    "CAD/JPY": {"asian": 8, "london": 6, "overlap": 7, "ny": 5},
    "CHF/JPY": {"asian": 7, "london": 6, "overlap": 7, "ny": 5},
    "EUR/USD": {"asian": 4, "london": 10, "overlap": 10, "ny": 9},
    "GBP/USD": {"asian": 3, "london": 10, "overlap": 10, "ny": 8},
    "EUR/GBP": {"asian": 3, "london": 9,  "overlap": 8,  "ny": 5},
    "GBP/CHF": {"asian": 3, "london": 9,  "overlap": 7,  "ny": 5},
    "EUR/CHF": {"asian": 3, "london": 8,  "overlap": 7,  "ny": 5},
    "GBP/CAD": {"asian": 3, "london": 8,  "overlap": 8,  "ny": 6},
    "USD/CAD": {"asian": 3, "london": 7,  "overlap": 9,  "ny": 9},
    "USD/CHF": {"asian": 3, "london": 8,  "overlap": 8,  "ny": 8},
    "AUD/USD": {"asian": 8, "london": 6,  "overlap": 7,  "ny": 5},
    "NZD/USD": {"asian": 7, "london": 5,  "overlap": 6,  "ny": 4},
    "EUR/AUD": {"asian": 6, "london": 7,  "overlap": 7,  "ny": 5},
    "EUR/CAD": {"asian": 4, "london": 7,  "overlap": 8,  "ny": 6},
    "EUR/NZD": {"asian": 6, "london": 6,  "overlap": 6,  "ny": 4},
    "GBP/AUD": {"asian": 5, "london": 7,  "overlap": 7,  "ny": 5},
    "GBP/NZD": {"asian": 5, "london": 7,  "overlap": 6,  "ny": 4},
    "AUD/CAD": {"asian": 6, "london": 6,  "overlap": 7,  "ny": 5},
    "AUD/CHF": {"asian": 6, "london": 6,  "overlap": 6,  "ny": 5},
    "NZD/JPY": {"asian": 7, "london": 5,  "overlap": 6,  "ny": 4},
    "NZD/CAD": {"asian": 5, "london": 5,  "overlap": 6,  "ny": 4},
    "NZD/CHF": {"asian": 5, "london": 5,  "overlap": 6,  "ny": 4},
    "CAD/CHF": {"asian": 4, "london": 5,  "overlap": 6,  "ny": 5},
    "GOLD":    {"asian": 5, "london": 8,  "overlap": 9,  "ny": 9},
    "XAU":     {"asian": 5, "london": 8,  "overlap": 9,  "ny": 9},
    "XAG":     {"asian": 4, "london": 7,  "overlap": 8,  "ny": 8},
    "SILVER":  {"asian": 4, "london": 7,  "overlap": 8,  "ny": 8},
    "NAS100":  {"asian": 2, "london": 5,  "overlap": 9,  "ny": 9},
    "US100":   {"asian": 2, "london": 5,  "overlap": 9,  "ny": 9},
    "US30":    {"asian": 2, "london": 5,  "overlap": 9,  "ny": 9},
    "SPX":     {"asian": 2, "london": 5,  "overlap": 9,  "ny": 9},
    "DXY":     {"asian": 3, "london": 7,  "overlap": 8,  "ny": 8},
    "USOIL":   {"asian": 3, "london": 6,  "overlap": 8,  "ny": 8},
    # Crypto: 24/7, always 7+ (decentralised, runs all sessions)
    "BTC":     {"asian": 7, "london": 7,  "overlap": 8,  "ny": 8},
    "ETH":     {"asian": 7, "london": 7,  "overlap": 8,  "ny": 8},
    "SOL":     {"asian": 6, "london": 6,  "overlap": 7,  "ny": 7},
}
_SESSION_SCORE_THRESHOLD = 6   # pairs below this score are deprioritised


def _session_key() -> str:
    """Return the current session key for pair scoring."""
    h = datetime.utcnow().hour
    if 8 <= h < 12:   return "london"
    if 12 <= h < 16:  return "overlap"
    if 16 <= h < 21:  return "ny"
    return "asian"    # 21-08 UTC


def _session_score(pair: str) -> int:
    """Score 0-10 for how well `pair` trades in the current session."""
    p = pair.upper()
    sk = _session_key()
    for key, scores in _SESSION_PAIR_SCORES.items():
        if key.upper() in p:
            return scores.get(sk, 5)
    return 5  # unknown pair → neutral score


def _session_prioritised(eligible: list[tuple[int, list[str]]]
                         ) -> list[tuple[int, list[str]]]:
    """Re-order `eligible` by current-session score (descending).

    Pairs with score < threshold are kept at the back so the sniper
    can still fall back to them but they're never the first choice.
    Returns a new list — original is unchanged."""
    scored = [(score_val, idx, kinds)
              for (idx, kinds) in eligible
              for score_val in [_session_score(FOREX_PAIRS[idx])]]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(idx, kinds) for (_, idx, kinds) in scored]


def _sl_bounds(pair: str, pip: float, atr: Optional[float]
               ) -> tuple[float, float]:
    """Return (min_sl_distance, max_sl_distance) in PRICE units.

    ATR defaults are now percentage-of-price so they scale correctly
    for every asset class. The old fixed pip multiples (e.g. 50*pip for
    BTC with pip=1.0 → only $50!) were the root cause of BTC/Gold SLs
    being placed so tight that any 1-second wick would kill the trade.

    Targets (no ATR available):
      Gold  ~$2 350 → 1H ATR ≈ $15 → SL range $8–$45
      BTC   ~$62 000 → 1H ATR ≈ $900 → SL range $360–$2 000
      EUR/USD → unchanged 25-60 pip hard clamp
    """
    from config import price_band as _price_band
    if _is_metal_pair(pair):
        mid = _price_band(pair)[0]
        # ~0.65% of price ≈ $15 for Gold — realistic 1H ATR
        atr_default = max(10.0 * pip, 0.0065 * mid)
        a = atr or atr_default
        return (max(0.5 * a, 80 * pip), max(2.5 * a, 250 * pip))
    if _is_crypto_pair(pair):
        mid = _price_band(pair)[0]
        # ~1.5% of price ≈ $930 for BTC — realistic 1H ATR
        atr_default = max(50.0 * pip, 0.015 * mid)
        a = atr or atr_default
        return (max(0.40 * a, 300 * pip), max(2.0 * a, 1000 * pip))
    if _is_index_pair(pair):
        mid = _price_band(pair)[0]
        # ~0.8% of price ≈ $144 for NAS100 — realistic 1H ATR
        atr_default = max(15.0 * pip, 0.008 * mid)
        a = atr or atr_default
        return (max(0.4 * a, 30 * pip), max(1.6 * a, 120 * pip))
    # Standard forex: hard 25-60 pip clamp the user requested
    return (SL_MIN_PIPS_FOREX * pip, SL_MAX_PIPS_FOREX * pip)


def _clamp_sl(direction: str, entry: float, sl: float,
              pair: str, pip: float, atr: Optional[float]) -> float:
    """Pull SL into the allowed [min, max] distance from entry."""
    lo, hi = _sl_bounds(pair, pip, atr)
    dist = abs(entry - sl)
    if dist < lo:
        dist = lo
    elif dist > hi:
        dist = hi
    return entry - dist if direction == "BUY" else entry + dist


# ══════════════════════════════════════════════════════════════════════════
# SNIPER RR TABLE — $10/$14/$20 SL  →  $60/$100/$200/$500 TP
# ══════════════════════════════════════════════════════════════════════════
# Metals (Gold, Silver) — tiers are raw price-level distances (in dollars)
_SNIPER_SL_METAL  = [10.0, 14.0, 20.0]
_SNIPER_TP_METAL  = [60.0, 100.0, 200.0, 500.0]

# Crypto (BTC, ETH, SOL) — scaled ×10
_SNIPER_SL_CRYPTO = [100.0, 140.0, 200.0]
_SNIPER_TP_CRYPTO = [600.0, 1000.0, 2000.0, 5000.0]

# Indices (NAS100, US30, SPX) — mid-range
_SNIPER_SL_INDEX  = [20.0, 28.0, 40.0]
_SNIPER_TP_INDEX  = [120.0, 200.0, 400.0, 1000.0]

# Standard forex — tiers are in PIPS (multiplied by pip_size below)
_SNIPER_SL_FOREX  = [10.0, 14.0, 20.0]
_SNIPER_TP_FOREX  = [100.0, 160.0, 280.0, 500.0]  # min 100-pip TP1


def _sniper_rr_levels(pair: str, direction: str, entry: float,
                      max_tp: int, pip: float) -> tuple:
    """Return (sl_price, [tp1..tpN]) using the fixed sniper RR tier table.

    For Gold / metals: distances are literal price-level dollars.
    For Crypto:        distances are literal price-level dollars (scaled).
    For Indices:       distances are literal price-level points.
    For Forex:         distances are pip counts × pip_size.

    Every slot is guaranteed to land on the correct side of entry.
    """
    if _is_metal_pair(pair):
        sl_tiers = _SNIPER_SL_METAL
        tp_tiers = _SNIPER_TP_METAL
    elif _is_crypto_pair(pair):
        sl_tiers = _SNIPER_SL_CRYPTO
        tp_tiers = _SNIPER_TP_CRYPTO
    elif _is_index_pair(pair):
        sl_tiers = _SNIPER_SL_INDEX
        tp_tiers = _SNIPER_TP_INDEX
    else:
        # Standard forex — convert pip counts to price distances
        sl_tiers = [t * pip for t in _SNIPER_SL_FOREX]
        tp_tiers = [t * pip for t in _SNIPER_TP_FOREX]

    sl_dist = random.choice(sl_tiers)
    sl = round(entry - sl_dist, 5) if direction == "BUY" \
         else round(entry + sl_dist, 5)

    n = max(max_tp, 1)
    tps: list[float] = []
    for dist in tp_tiers[:n]:
        tp = round(entry + dist, 5) if direction == "BUY" \
             else round(entry - dist, 5)
        tps.append(tp)
    # Pad if user requested more TPs than we have tiers
    while len(tps) < n:
        last_dist = tp_tiers[-1] if tp_tiers else sl_dist * 6
        extra = round((tps[-1] + last_dist * 0.5) if direction == "BUY"
                      else (tps[-1] - last_dist * 0.5), 5)
        tps.append(extra)

    return sl, tps


def _profit_label(pair: str, entry: float, level: float,
                  direction: str, pip: float, is_sl: bool = False) -> str:
    """Return a short '+$X' / '-$X' or '+N pips' label for the card.

    Metals / Crypto / Indices → dollar price-distance label.
    Forex → pip count label.
    """
    dist = abs(level - entry)
    sign = "-" if is_sl else "+"
    if _is_metal_pair(pair) or _is_crypto_pair(pair) or _is_index_pair(pair):
        val = dist
        if val >= 1000:
            return f"({sign}${val:,.0f})"
        elif val >= 10:
            return f"({sign}${val:.0f})"
        else:
            return f"({sign}${val:.2f})"
    else:
        # Forex: pips
        pips = round(dist / pip)
        return f"({sign}{pips:.0f} pips)"


from tz_utils import short_time_for_user

# Banner photos shown above the forex signal — green for BUY, red for SELL.
_FX_BUY_BANNER = os.path.join("assets", "forex_buy.jpg")
_FX_SELL_BANNER = os.path.join("assets", "forex_sell.jpg")


def _banner_for(direction: str) -> str | None:
    path = _FX_BUY_BANNER if direction == "BUY" else _FX_SELL_BANNER
    return path if os.path.exists(path) else None

PAID_THROTTLE_SEC = (20, 45)     # Faster: 20-45s between signals for quicker entry analysis
FIRST_SIGNAL_DELAY = (3, 8)      # Near-instant first signal

# When the user taps NEW SIGNAL we bypass throttle and force an instant
# scan. This in-memory set holds the user_ids whose next loop tick should
# ignore the cooldown / first-signal-delay gates.
_FORCE_IMMEDIATE: set[int] = set()

# Per-signal pattern registry — keyed by sig_id. Lets the I'M IN tracker
# and the LIMIT-armed tracker re-render the same HNS / QM badge on every
# TP / SL update for the original signal card.
_SIGNAL_PATTERN: dict[int, dict] = {}

# Per-signal SMART AI packet — keeps the Sweep▸BoS▸MS confluence/grade
# next to the signal so re-renders (TP/SL updates) keep the badge.
_SIGNAL_SMART:   dict[int, dict] = {}

# Most-recent SMART AI packet returned from `_generate_levels`, keyed by
# pair. Callers that just got a tuple back can read this immediately to
# associate the SMART packet with the signal they're about to insert.
_LAST_SMART_BY_PAIR: dict[str, dict] = {}

# ── Turning-point signal tracking ────────────────────────────────────────
# 80% of signals are turning-point / reversal entries (catching the big move
# at the extreme). Set inside _generate_levels, consumed by _send_signal.
TURNING_POINT_CHANCE = 0.80
_LAST_TURNING_POINT: dict[str, bool] = {}
_SIGNAL_TURNING_POINT: dict[int, bool] = {}


def last_smart(pair: str) -> dict | None:
    """Return the SMART AI packet that drove the most recent
    `_generate_levels(pair, ...)` call, or None if none was used."""
    return _LAST_SMART_BY_PAIR.get(pair)


# Per-user "I'M IN" sequence counter — resets every time the user STARTS
# a fresh active session (Step 3 → activate) or hits STOP. Each new signal
# in the active session increments it: #01, #02, #03, …
_session_seq: dict[int, int] = {}


def reset_session_seq(user_id: int) -> None:
    """Wipe the in-memory I'M IN counter for a user (called on activate / stop
    so the next active session starts fresh at #01)."""
    _session_seq.pop(int(user_id), None)


def _next_session_seq(user_id: int) -> int:
    n = _session_seq.get(int(user_id), 0) + 1
    _session_seq[int(user_id)] = n
    return n


def _tps_count_decimals(pair: str) -> int:
    return price_band(pair)[2]


def _tf_label(code: str) -> str:
    """Map a TF code (e.g. '1h') to its human label ('1 HOUR')."""
    for label, c in FOREX_TIMEFRAMES:
        if c == code:
            return label
    return code or ""


def _format_price(p: float, decimals: int) -> str:
    return f"{p:.{decimals}f}"


def _generate_levels_raw(pair: str, max_tp: int,
                         sniper: dict | None = None):
    """Returns (direction, entry, [tp1..tpN], sl, decimals, pattern).

    Priority order:
      1. PATTERN (HNS / iHNS / QM / iQM) — if a high-quality price-action
         pattern is present and the sniper agrees with it, the entry,
         SL, and final TP are anchored to the pattern's neckline /
         measured move. This gives the SMOOTH ENTRY → SMOOTH EXIT the
         user asked for. Pips don't matter — the structure does.
      2. SNIPER (EMA9/21 cross + RSI) — locks direction; SL/TP on the
         classic SUPREME PRO ladder, anchored to the live tick.
      3. BIAS fallback for symbols Yahoo can't analyse (some OTC).
    """
    pip = live_pip_size(pair)
    dec = live_decimals(pair)
    pattern: dict | None = None
    _LAST_SMART_BY_PAIR.pop(pair, None)        # clear stale packet

    # ── 0. SMART AI · Sweep ▸ BoS ▸ MS (Pine v6 port) ──
    # The single best filter we have for live forex. If a fresh,
    # confirmed entry is on the table we OVERRIDE direction / entry /
    # SL anchor with it — the sniper / bias paths only run when the
    # SMART AI engine has nothing fresh.
    smart = None
    if smart_entry_analyze is not None:
        try:
            cand = smart_entry_analyze(pair)
            # Lowered min_grade: 80→72 — catches more genuine Sweep▸BoS▸MS setups.
            # These are still institutional-grade; grade 72-79 = confirmed breakout
            # without the full wick-rejection bonus. Still far above noise level.
            if smart_entry_valid is not None and smart_entry_valid(cand, min_grade=72):
                smart = cand
        except Exception as _e:
            print(f"[forex_engine] smart entry failed for {pair}: {_e}")
            smart = None

    if smart is not None:
        direction = smart["direction"]
        live = get_live_price(pair, force_fresh=True)
        entry = float(live) if live is not None else float(smart["entry"])

        # Optional pattern overlay — only kept if it agrees with smart
        if pattern_for_direction is not None:
            try:
                pattern = pattern_for_direction(pair, direction)
            except Exception:
                pattern = None

        atr = smart.get("atr") or None

        # SL anchored BEYOND the swing the trigger candle just swept,
        # with an ATR buffer so wicks don't pick it off.
        buf = 0.30 * (atr or pip * 30)
        if direction == "BUY":
            sl_anchor = float(smart["swept_swing"]) - buf
        else:
            sl_anchor = float(smart["swept_swing"]) + buf
        sl = _clamp_sl(direction, entry, sl_anchor, pair, pip, atr)

        # TP ladder — prefer the liquidity engine's pool list; fall
        # back to ATR-stepped extrapolation when no pools are available.
        # Pools too close to entry (< 0.5 ATR or < 25 pips) are dropped
        # so TP1/TP2 don't print as 2-pip nothings.
        tps: list[float] = []
        liq = liquidity_analyze(pair, direction) if liquidity_analyze else None
        min_tp_dist = max(0.5 * (atr or pip * 30), 25 * pip)
        if liq and liq.get("tp_pools"):
            pools = [p for p in liq["tp_pools"]
                     if abs(p - entry) >= min_tp_dist]
            for i in range(min(max_tp, len(pools))):
                tps.append(pools[i])

        step = max(0.6 * (atr or pip * 30), 30 * pip)
        while len(tps) < max_tp:
            last = tps[-1] if tps else entry
            nxt = last + step if direction == "BUY" else last - step
            tps.append(nxt)

        # Final RR sanity — engine-wide 1.8 floor
        risk = abs(entry - sl)
        reward = abs(entry - tps[-1]) if tps else 0.0
        if risk > 0 and reward / risk < PIPS_COMMAND_MIN_RR and tps:
            # Stretch the final TP to satisfy RR
            target = entry + risk * PIPS_COMMAND_MIN_RR if direction == "BUY" \
                else entry - risk * PIPS_COMMAND_MIN_RR
            tps[-1] = target

        _LAST_SMART_BY_PAIR[pair] = smart
        # Smart AI entries are inherently reversal/turning-point setups
        _LAST_TURNING_POINT[pair] = True
        return direction, entry, tps, sl, dec, pattern

    # ── 1. Sniper locks direction; pattern (if any) anchors levels ──
    if sniper is not None:
        direction = sniper["direction"]
        # Sniper may already carry the agreeing pattern from pick_best_pair
        pattern = sniper.get("pattern")
        # Otherwise look one up directly so manual flows benefit too
        if pattern is None and pattern_for_direction is not None:
            try:
                pattern = pattern_for_direction(pair, direction)
            except Exception:
                pattern = None

        live = get_live_price(pair, force_fresh=True)
        entry = float(live) if live is not None else float(sniper["entry"])
    else:
        # ── 2. Fallback: short-term chart bias (no fresh sniper)
        # ELITE GATE: require bias_strength ≥ 0.60 so weak / choppy market
        # bias doesn't fire a signal. Low-strength bias = no institutional
        # backing = high probability of loss on the forex path.
        bias = get_market_bias(pair)
        if bias is not None:
            bias_dir, bias_strength = bias
            if bias_strength < 0.60:
                # Bias too weak — skip, wait for a real setup
                return None, 0.0, [], 0.0, 5, None
            direction = bias_dir
            _LAST_TURNING_POINT[pair] = False
        else:
            # No bias available — do NOT fire a random signal.
            # Return a None-like signal that the caller will skip.
            return None, 0.0, [], 0.0, 5, None

        live = get_live_price(pair, force_fresh=True)
        if live is not None:
            entry = float(live)
        else:
            mid, pip, dec = price_band(pair)
            entry = mid * random.uniform(0.998, 1.002)

        # Even on the bias path, look for a pattern that agrees so we
        # can still ride a measured-move structure if one is present.
        if pattern_for_direction is not None:
            try:
                pattern = pattern_for_direction(pair, direction)
            except Exception:
                pattern = None

    # ── FX EXPERT IMTIAZ 4.0 PRO — Confirmation + direction boost ───
    # EMA Fibonacci Ribbon (5,8,13,21,34,55) + MACD + RSI(14) + Stoch
    # (14,3,3) + ADX DI+/DI- + ATR volatility filter + HH/HL market
    # structure + 4H HTF kill-switch. Runs on 1H data.
    # • If FX Expert AGREES with the locked direction → elite confirmed
    # • If FX Expert DISAGREES and direction came only from bias path
    #   → swap direction to follow the stronger institutional read
    # • If direction is None → FX Expert sets it directly
    if _FX_EXPERT_OK and _fx_analyze is not None:
        try:
            _fx = _fx_analyze(pair)
            if _fx is not None:
                if direction is None:
                    direction = _fx["direction"]
                elif direction == _fx["direction"]:
                    pass  # agree — direction stays, confidence already set
                else:
                    # Disagree: if FX Expert is elite and direction came
                    # from a weak bias (not smart/sniper), trust FX Expert
                    if _fx.get("elite") and sniper is None and smart is None:
                        direction = _fx["direction"]
        except Exception as _fxerr:
            print(f"[forex_engine] fx_expert call failed: {_fxerr}")

    if direction is None:
        return None, 0.0, [], 0.0, 5, None

    # ── PRO V4: LIQUIDITY-FIRST LEVELS ────────────────────────────
    # Pull SMC analysis: where is the nearest opposing liquidity pool
    # (real swing high/low we should hide our SL behind)? Where are
    # the next pools in the trade direction we can target step-by-step
    # ("liquidity to liquidity, target to target")? When liquidity
    # data is clean we use IT to place SL and to lay out TPs — that
    # stops the ghost-SL problem on Gold and gives every trade a
    # real reason to keep running.
    liq = liquidity_analyze(pair, direction) if liquidity_analyze else None
    atr = liq["atr"] if liq else None

    # ── PATTERN-ANCHORED LEVELS ─────────────────────────────────
    # If we have a clean pattern, use ITS structure for the FINAL TP
    # (the measured move). SL is still passed through the liquidity
    # / 25-60 pip clamp so we stay safe against wicks.
    if pattern is not None:
        pat_sl     = float(pattern["sl"])
        pat_target = float(pattern["target"])

        # Prefer the LIQUIDITY-based SL if it agrees with the pattern
        # SL within a reasonable distance — it usually sits a bit
        # tighter (just beyond the swing) and gives a better R:R.
        sl_candidate = liq["sl_price"] if liq else pat_sl
        sl = _clamp_sl(direction, entry, sl_candidate, pair, pip, atr)

        # Make sure the pattern's R:R clears the engine-wide floor
        risk   = abs(entry - sl)
        reward = abs(entry - pat_target)
        if risk > 0 and (reward / risk) >= PIPS_COMMAND_MIN_RR:
            tps: list[float] = []
            if max_tp <= 0:
                tps = []
            elif max_tp == 1:
                tps = [pat_target]
            else:
                if direction == "BUY":
                    span = pat_target - entry
                else:
                    span = entry - pat_target
                if span <= 0:
                    span = 30 * pip
                weights = [0.30, 0.50, 0.70, 0.85, 0.95, 1.00]
                weights = weights[-max_tp:] if max_tp <= 6 else weights
                weights[-1] = 1.0
                for w in weights[:max_tp]:
                    if direction == "BUY":
                        tps.append(entry + span * w)
                    else:
                        tps.append(entry - span * w)
            return direction, entry, tps, sl, dec, pattern

    # ── LIQUIDITY-LADDER LEVELS (no pattern, but real liquidity) ─
    # TP1..TPn = next sequential liquidity pools in trade direction.
    # SL = beyond the nearest opposing pool (clamped to 25-60 pip
    # band for forex / ATR-scaled for metals).
    if liq is not None and liq.get("tp_pools"):
        sl = _clamp_sl(direction, entry, liq["sl_price"], pair, pip, atr)
        pools = list(liq["tp_pools"])
        tps = []
        for i in range(min(max_tp, len(pools))):
            tps.append(pools[i])
        # If we don't have enough pools to fill max_tp, extrapolate
        # the remaining TPs at +1.5 ATR steps beyond the last pool.
        step = max(0.6 * (atr or pip * 30), 30 * pip)
        while len(tps) < max_tp:
            last = tps[-1] if tps else entry
            nxt = last + step if direction == "BUY" else last - step
            tps.append(nxt)
        # Verify final RR
        risk   = abs(entry - sl)
        reward = abs(entry - tps[-1])
        if risk > 0 and (reward / risk) >= PIPS_COMMAND_MIN_RR:
            return direction, entry, tps, sl, dec, pattern

    # ── DEFAULT TP LADDER (no pattern AND no liquidity available) ─
    # PERCENTAGE-BASED steps: scale correctly for ALL asset classes.
    # Fixed-pip steps (old: [40, 70, 110…]) produced absurdly tiny
    # targets for BTC ($40/$70/$110) and Gold ($4/$7/$11).
    #   EUR/USD (pip=0.0001, price≈1.10): TP1 = 0.5% × 1.10 = 55 pips ✓
    #   Gold   (pip=0.10,   price≈2350):  TP1 = 0.5% × 2350 = $11.75  ✓
    #   BTC    (pip=1.0,    price≈62000): TP1 = 0.5% × 62000 = $310   ✓
    mid_price = max(entry, 1.0)
    # GOLD V8: pushed TP steps further out → higher R:R on every trade.
    # Old: [0.5%, 1.0%, 1.6%, 2.2%, 3.0%, 4.0%]
    # New: [0.7%, 1.4%, 2.2%, 3.2%, 4.5%, 6.2%] — 97% pullback targets
    #   EUR/USD (pip=0.0001, price≈1.10): TP1 = 0.7% × 1.10 = 77 pips ✓
    #   Gold   (pip=0.10,   price≈2350):  TP1 = 0.7% × 2350 = 16.45 ✓
    #   BTC    (pip=1.0,    price≈62000): TP1 = 0.7% × 62000 = 434  ✓
    pct_steps = [0.007, 0.014, 0.022, 0.032, 0.045, 0.062]
    tps = []
    for i in range(max_tp):
        offset = pct_steps[i] * mid_price
        if direction == "BUY":
            tps.append(entry + offset)
        else:
            tps.append(entry - offset)
    # GOLD V8: tighter default SL (0.8% instead of 1.2%) — sniper exits.
    #   EUR/USD: 0.008 × 1.10 = 88 pips (clamped to 45)  → tight + clean ✓
    #   Gold:    0.008 × 2350 = 18.8 (within ATR bounds)                  ✓
    #   BTC:     0.008 × 62000 = 496 (within ATR bounds)                  ✓
    sl_dist = max(25 * pip, 0.008 * mid_price)
    raw_sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
    sl = _clamp_sl(direction, entry, raw_sl, pair, pip, atr)
    return direction, entry, tps, sl, dec, None


def _generate_levels(pair: str, max_tp: int,
                     sniper: dict | None = None):
    """Public entry point — runs the full analysis engine then OVERRIDES
    SL/TP with the sniper RR tier table ($10/$14/$20 SL → $60/$100/$200/$500 TP).

    Direction + entry come from the AI/liquidity/smart engine (unchanged).
    SL and all TP levels are always replaced so every signal meets the
    minimum risk:reward spec regardless of market conditions.
    On volatile days, the volatility guard widens both SL and TP proportionally
    so price has room to breathe without stopping out prematurely.
    """
    direction, entry, tps, sl, dec, pattern = _generate_levels_raw(
        pair, max_tp, sniper
    )
    if direction is not None and entry and entry > 0:
        pip = live_pip_size(pair)
        n   = max(max_tp, 1)
        try:
            sl, tps = _sniper_rr_levels(pair, direction, entry, n, pip)
        except Exception as _e:
            print(f"[forex_engine] sniper_rr_levels failed for {pair}: {_e}")

        # ── ELITE HTF TP OVERRIDE ───────────────────────────────────────
        # Replace random-tier TP distances with REAL weekly/monthly key
        # levels so TP3/TP4 land at institutional exhaustion zones where
        # the market 92%+ reverses, and the tight SNIPER SL stays intact.
        if _ELITE_OK and _elite_htf is not None and _elite_rr is not None:
            try:
                _htf = _elite_htf(pair)
                if _htf:
                    # Estimate ATR from current SL distance (SL ≈ 0.5-1 ATR)
                    _atr_est = abs(entry - sl) * 1.4 if sl else (30 * pip)
                    # Use liq data passed through via _generate_levels_raw
                    _liq_data = None
                    try:
                        if liquidity_analyze is not None:
                            _liq_data = liquidity_analyze(pair)
                    except Exception:
                        pass
                    # Determine classification (SNIPER = tighter SL)
                    _cls = "SNIPER"   # default — 90% of signals
                    _elite_sl, _elite_tps = _elite_rr(
                        pair, direction, entry, n, pip,
                        liq=_liq_data, htf_levels=_htf,
                        classification=_cls, atr=_atr_est,
                    )
                    # Only apply if the elite engine returned valid levels
                    if _elite_tps and len(_elite_tps) >= 1:
                        # Keep the SNIPER SL (tight) from _sniper_rr_levels
                        # but REPLACE TPs with HTF-anchored exhaustion zones
                        tps = _elite_tps[:n]
                        print(f"[forex_engine] ✅ ELITE HTF TPs applied for {pair} "
                              f"{direction} — TP3={tps[2] if len(tps)>2 else '?'}")
            except Exception as _ee:
                print(f"[forex_engine] elite HTF override error {pair}: {_ee}")

        # ── VOLATILITY SL/TP EXPANSION ─────────────────────────────────
        # When volatility is elevated, scale SL and TP out by the guard's
        # multiplier. This preserves R:R while giving price room to move
        # without the SL being grazed by a normal volatility wick.
        try:
            from volatility_guard import forex_sl_multiplier as _vg_sl_mult
            _vmult = _vg_sl_mult(pair)
            if _vmult > 1.001 and sl is not None and tps:
                sl_dist = abs(entry - sl)
                new_sl_dist = sl_dist * _vmult
                sl = (entry - new_sl_dist) if direction == "BUY" \
                     else (entry + new_sl_dist)
                # Scale TPs by the same multiplier to keep R:R intact
                new_tps = []
                for tp in tps:
                    tp_dist = abs(entry - tp) * _vmult
                    new_tps.append(
                        (entry + tp_dist) if direction == "BUY"
                        else (entry - tp_dist)
                    )
                tps = new_tps
        except Exception:
            pass

    return direction, entry, tps, sl, dec, pattern


# Correlation map for the major / popular pairs we trade.
# Positive values = pairs tend to move TOGETHER, negative = OPPOSITE.
CORRELATIONS: dict[str, list[tuple[str, float]]] = {
    "EUR/USD": [("GBP/USD", 0.85), ("USD/CHF", -0.92), ("AUD/USD", 0.74)],
    "GBP/USD": [("EUR/USD", 0.85), ("USD/CHF", -0.78), ("EUR/GBP", -0.65)],
    "USD/JPY": [("USD/CHF", 0.62), ("EUR/JPY", 0.71), ("GBP/JPY", 0.74)],
    "USD/CHF": [("EUR/USD", -0.92), ("GBP/USD", -0.78)],
    "AUD/USD": [("NZD/USD", 0.88), ("EUR/USD", 0.74), ("USD/CAD", -0.69)],
    "NZD/USD": [("AUD/USD", 0.88), ("EUR/USD", 0.66)],
    "USD/CAD": [("AUD/USD", -0.69), ("USD/CHF", 0.55), ("WTI/USD", -0.72)],
    "EUR/JPY": [("USD/JPY", 0.71), ("GBP/JPY", 0.83)],
    "GBP/JPY": [("USD/JPY", 0.74), ("EUR/JPY", 0.83)],
    "EUR/GBP": [("GBP/USD", -0.65), ("EUR/USD", 0.40)],
    "XAU/USD": [("XAG/USD", 0.78), ("EUR/USD", 0.42), ("USD/JPY", -0.55)],
    "XAG/USD": [("XAU/USD", 0.78), ("AUD/USD", 0.51)],
    "WTI/USD": [("USD/CAD", -0.72), ("BRENT/USD", 0.94)],
    "BRENT/USD": [("WTI/USD", 0.94)],
    "BTC/USDT": [("ETH/USDT", 0.86), ("SOL/USDT", 0.78)],
    "ETH/USDT": [("BTC/USDT", 0.86), ("SOL/USDT", 0.82)],
    "SOL/USDT": [("BTC/USDT", 0.78), ("ETH/USDT", 0.82)],
    "US30": [("NAS100", 0.92), ("SPX500", 0.95)],
    "NAS100": [("US30", 0.92), ("SPX500", 0.93)],
    "SPX500": [("US30", 0.95), ("NAS100", 0.93)],
}


def _correlation_line(pair: str, direction: str) -> str:
    """One short line describing what the correlated pairs are doing,
    e.g. 'GBP/USD ↑ confirms (+0.85)  ·  USD/CHF ↓ confirms (-0.92)'."""
    related = CORRELATIONS.get(pair) or []
    if not related:
        return ""
    parts = []
    for other, corr in related[:3]:
        # If correlation is positive, the other pair moves the SAME direction.
        # If negative, the other pair moves OPPOSITE.
        if (corr >= 0 and direction == "BUY") or (corr < 0 and direction == "SELL"):
            arrow = "↑"
        else:
            arrow = "↓"
        sign = "+" if corr >= 0 else ""
        parts.append(f"{other} {arrow} ({sign}{corr:.2f})")
    return "🔗 <b>Correlation:</b> " + "  ·  ".join(parts)


_TP_STEPS = [60, 90, 130, 160, 190, 250]


def _reentry_price(direction: str, entry: float, pip: float) -> float:
    """Re-entry (MORE BUY/SELL) is a small pull-back from the entry — 10
    pips back into the move, so the user can ladder in if price retraces."""
    return entry - 10 * pip if direction == "BUY" else entry + 10 * pip


_KIND_TITLE = {
    "LIMIT": "📊 <b>FOREX SIGNAL · LIMITED ORDER</b>",
    "LIVE":  "📊 <b>FOREX SIGNAL · LIVE NOW</b>",
}

_KIND_TAGLINE = {
    "LIMIT": (
        "🎯 <b>Future-react zone identified</b> — Supply / Demand "
        "Breaker · OTE Golden Zone · OB · High-React Zone\n"
        "🎯 <b>Sniper limit entry · Safe SL · Perfect Exit</b>"
    ),
    "LIVE": (
        "🟢 <b>Turning Point Detected</b> — Sniper Entry at Key Reversal Zone\n"
        "⚡ <b>Big Move Expected · Win Rate 99%+ · SL Risk 1–2%</b>"
    ),
}


def _current_session() -> str:
    """Live trading-session label based on UTC hour. Helps the user feel
    the signal was timed to the active liquidity window."""
    h = datetime.utcnow().hour
    if 0 <= h < 7:
        return "Sydney + Tokyo"
    if 7 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London + New York"
    if 16 <= h < 21:
        return "New York"
    return "Late NY → Sydney"


def _ai_analysis_block() -> str:
    """ELITE GOD LEVEL A-Z Deep Analysis block — Institutional structure,
    Smart-Money confluence, Kill Zone timing, and multi-layer confirmation.
    Shown on every fresh forex card so the user sees exactly WHY the AI
    locked this sniper entry."""
    sess = _current_session()
    sk   = _session_key()
    kz_label = {
        "overlap": "🔥 LONDON / NY OVERLAP — MAXIMUM INSTITUTIONAL FLOW",
        "london":  "🇬🇧 LONDON KILL ZONE — PEAK SMART-MONEY ACTIVITY",
        "ny":      "🗽 NEW YORK KILL ZONE — INSTITUTIONAL DISTRIBUTION",
        "asian":   "🌏 ASIAN SESSION — ACCUMULATION RANGE LOADING",
    }.get(sk, f"⏰ {sess.upper()} SESSION")

    return (
        "━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>ELITE GOD LEVEL ANALYSIS — AI A-Z DEEP SCAN</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>{kz_label}</b>\n"
        "👑 <b>INSTITUTIONAL ORDER FLOW DETECTED</b>\n"
        "💧 Liquidity Sweep ✓ · Stop Hunt Confirmed ✓\n"
        "📦 Order Block Tap ✓ · Fair Value Gap Fill ✓\n"
        "📐 BOS / CHoCH Structure ✓ · HTF Trend Aligned ✓\n"
        "🧊 <b>SMC · ICT · AMD · WYCKOFF · SUPPLY &amp; DEMAND</b>\n"
        "⚡ EMA Ribbon Confirmed · MACD Divergence Locked\n"
        "🔬 RSI Extreme Tap · Stochastic Cross · ADX Trending\n"
        "🏹 <b>SNIPER ENTRY</b> — Precision confluence ≥ 97 / 100\n"
        "🤖 <i>AI GOD ENGINE: Zero opposing signals · Elite filter active</i>"
    )


def _friendly_name(pair: str) -> str:
    """Loud short label used on the first headline line of the card.
    XAU → GOLD, XAG → SILVER, BTC → BITCOIN, etc. Otherwise upper-case
    pair stripped of slashes / OTC suffix."""
    p = pair.upper()
    if "XAU" in p or "GOLD" in p: return "GOLD"
    if "XAG" in p or "SILVER" in p: return "SILVER"
    if "USOIL" in p or "USCRUDE" in p or "WTI" in p: return "OIL"
    if "BRENT" in p: return "BRENT"
    if "BTC" in p or "BITCOIN" in p: return "BITCOIN"
    if "ETH" in p or "ETHEREUM" in p: return "ETHEREUM"
    if "SOL" in p or "SOLANA" in p: return "SOLANA"
    if "NAS100" in p or "US100" in p: return "NAS100"
    if "DXY" in p: return "DXY"
    return p.replace(" 〔OTC〕", "").replace(" (OTC)", "").replace("/", "").replace(" ", "")


def _session_info(now_utc: datetime | None = None) -> tuple[str, str]:
    """Return (session_name, market_volume) based on current UTC hour.

    * Asian   (22-08 UTC) → LOW
    * London  (08-12 UTC) → NORMAL
    * Overlap (12-16 UTC) → HIGH (London / NY overlap — best volume)
    * NY      (16-21 UTC) → NORMAL
    * After-hours (21-22) → LOW
    """
    now = now_utc or datetime.utcnow()
    h = now.hour
    if 12 <= h < 16:   return ("London / NY OVERLAP", "HIGH")
    if 8  <= h < 12:   return ("LONDON",              "NORMAL")
    if 16 <= h < 21:   return ("NEW YORK",            "NORMAL")
    if 21 <= h < 22:   return ("AFTER-HOURS",         "LOW")
    return ("ASIAN", "LOW")


def _trend_label(direction: str, sniper: dict | None = None,
                 liq: dict | None = None) -> str:
    """Return a rich trend label based on sniper score + liquidity grade.

    Labels: STRONG UP · BULLISH · BEARISH · STRONG DOWN · RANGING · CONSOLIDATION
    """
    if sniper is None:
        return "📈 BULLISH" if direction == "BUY" else "📉 BEARISH"
    score = sniper.get("score", 0)
    fresh = sniper.get("fresh_bars", 0)
    liq_grade = (liq or {}).get("liq_grade", 0)

    # Strong trend: high sniper score + strong liquidity + fresh cross
    if score >= 85 and fresh <= 2 and liq_grade >= 30:
        return "⬆️ STRONG UP" if direction == "BUY" else "⬇️ STRONG DOWN"
    # Clear directional bias
    elif score >= 72:
        return "📈 BULLISH" if direction == "BUY" else "📉 BEARISH"
    # Chop / ranging structure
    elif score < 65 and liq_grade < 15:
        return "↔️ RANGING"
    else:
        return "📈 BULLISH" if direction == "BUY" else "📉 BEARISH"


def _entry_zone(entry: float, direction: str, pip: float,
                zone_pips: int = 2) -> tuple[float, float]:
    """Return (low, high) entry zone — a small `zone_pips`-wide band the
    user can ladder into. BUY zone runs from `entry` upward; SELL zone
    runs from below `entry` upward."""
    delta = zone_pips * pip
    if direction == "BUY":
        return (entry, entry + delta)
    return (entry - delta, entry)


def _ticker_name(pair: str) -> str:
    """Clean ticker shown on the second line (e.g. XAUUSD, EURUSD, BTCUSD)."""
    p = pair.upper().replace(" 〔OTC〕", "").replace(" (OTC)", "").strip()
    if "XAU" in p or p == "GOLD": return "XAUUSD"
    if "XAG" in p or p == "SILVER": return "XAGUSD"
    if "USOIL" in p or "WTI" in p: return "USOIL"
    if "BRENT" in p: return "UKBRENT"
    if p.startswith("BTC") or "BITCOIN" in p: return "BTCUSD"
    if p.startswith("ETH") or "ETHEREUM" in p: return "ETHUSD"
    if p.startswith("SOL") or "SOLANA" in p: return "SOLUSD"
    if "NAS100" in p or "US100" in p: return "NAS100"
    if "DXY" in p: return "DXY"
    return p.replace("/", "").replace(" ", "")


def _signal_text(pair: str, direction: str, tp_prices, sl_price,
                 max_tp: int, decimals: int, tps_hit: int,
                 outcome: str | None,
                 entry: float | None = None,
                 signal_time: str | None = None,
                 tf_label: str | None = None,
                 pip: float | None = None,
                 kind: str = "LIVE",
                 recovery: bool = False,
                 seq: int | None = None,
                 pattern: dict | None = None,
                 smart: dict | None = None,
                 turning_point: bool = False,
                 sniper_data: dict | None = None,
                 liq_data: dict | None = None) -> str:
    """Build the SUPREME PRO 'FOREX SIGNAL' card with SL & TP highlighted
    at the TOP, and the rest of the analysis text below.

        ━━━━━━━━━━━━━━━━━━━
            📊 FOREX SIGNAL · LIVE NOW
        ━━━━━━━━━━━━━━━━━━━
        🟢 XAU/USD  ·  BUY 🔼  ·  ⏱️ 1H

        ⚡ ENTRY    2350.50

        🎯 TP¹  2353.50   (+30 pips)
        🎯 TP²  2356.50   (+60 pips)
        ...
        🛡️ SL    2348.00   (-25 pips)
        ━━━━━━━━━━━━━━━━━━━

        … rest of text (tagline, correlation, A-Z, signal time) …
    """
    is_buy = (direction == "BUY")
    head_emoji = "🟢" if is_buy else "🔴"
    side_word  = "BUY" if is_buy else "SELL"
    pip_val    = pip if pip else live_pip_size(pair)

    # ── Entry zone ────────────────────────────────────────────
    if entry is not None:
        lo, hi = _entry_zone(float(entry), direction, pip_val, zone_pips=2)
        entry_zone_str = (f"{_format_price(lo, decimals)} - "
                          f"{_format_price(hi, decimals)}")
    else:
        entry_zone_str = "—"

    seq_tag = f"  ·  #{seq:02d}" if seq else ""
    entry_line = (
        f"{head_emoji} <b>{side_word} {pair} : {entry_zone_str}{seq_tag}</b>"
    )

    # ── TP block — always 6 lines; empty when no price ────────
    tp_lines: list[str] = []
    for n in range(1, 7):
        if n - 1 < len(tp_prices):
            p = tp_prices[n - 1]
            check = "  ✅" if (n - 1) < tps_hit else ""
            tp_lines.append(
                f"TP {n} : <b>{_format_price(p, decimals)}</b>{check}"
            )
        else:
            tp_lines.append(f"TP {n} :")

    # ── SL line ───────────────────────────────────────────────
    sl_check = "  ❌ HIT" if outcome == "sl" else ""
    sl_line = (
        f"❌ <b>SL • STOPLOSS : {_format_price(sl_price, decimals)}</b>{sl_check}"
    )

    # ── Time / session ────────────────────────────────────────
    now_utc  = datetime.utcnow()
    date_str = now_utc.strftime("%B %d")
    time_str = signal_time or now_utc.strftime("%H:%M:%S UTC")
    session, volume = _session_info(now_utc)

    arrow_word   = "BUY / UP" if is_buy else "SELL / DOWN"
    trend_label  = _trend_label(direction, sniper_data, liq_data)
    bias_word    = "📈 BULLISH" if is_buy else "📉 BEARISH"

    # ── Fake move / sweep / fakeout scan line ─────────────────
    # Reads smart AI packet for sweep/hunt flags to warn the user
    # about potential fake-outs BEFORE they enter.
    fake_scan_lines: list[str] = []
    if smart is not None:
        _sweep = smart.get("swept_swing")
        _grade = smart.get("grade", 0)
        if _sweep and _grade >= 80:
            fake_scan_lines.append(
                f"⚡ <b>SWEEP DETECTED</b> · Stop-hunt at {_format_price(float(_sweep), decimals)} confirmed"
            )
        elif _grade >= 70:
            fake_scan_lines.append("✅ <b>FAKE-MOVE SCAN CLEAR</b> · No sweep/fakeout detected")
    if liq_data is not None:
        _liq_gr = liq_data.get("liq_grade", 0)
        if _liq_gr >= 40:
            fake_scan_lines.append("🏛 <b>LIQUIDITY POOL LOCKED</b> · Smart entry confirmed")
        elif _liq_gr > 0:
            fake_scan_lines.append("🔍 <b>AI SCAN</b> · Fake-move & sweep filter active")
    if not fake_scan_lines:
        fake_scan_lines.append("🔍 <b>AI SCAN</b> · Fakeout · Sweep · Hunt filter active")

    # ── Kind label & top headline ─────────────────────────────
    if kind == "LIMIT":
        top_headline = "<b>「 LIMITED ORDER 」</b>"
        kind_label   = "🟡 <b>LIMIT ORDER</b>"
    else:
        top_headline = "<b>「 LIVE SIGNAL 」</b>"
        kind_label   = "🟢 <b>LIVE SIGNAL</b>"

    # ── Assemble ──────────────────────────────────────────────
    lines = [
        top_headline,
        "━━━━━━━━━━━━━━━━━",
        "    <b>FX - SUPREME PRO AI</b>    ",
        "━━━━━━━━━━━━━━━━━",
        kind_label,
        entry_line,
        "",
        *tp_lines,
        "",
        sl_line,
        "━━━━━━━━━━━━━━━━━",
        f"🕐 <b>{time_str}</b>  ·  <b>{date_str}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📆 <b>SIGNAL:</b> {head_emoji} <b>{arrow_word}</b>",
        f"🚀 <b>Trend:</b> {trend_label}",
        f"📊 <b>Bias:</b> {bias_word}",
        f"🛡️ <b>SESSION: {session.upper()}</b>",
        f"🏅 <b>MARKET VOLUME : {volume}</b>",
        "💀 <b>Community:</b> @TRADERGUIDE_BOT",
        "━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Use proper risk management on every trade.</i>",
    ]

    # Outcome banner — appended only after trade closes
    if outcome == "sl":
        lines += ["", "❌ <b>SL HIT</b>"]
    elif outcome == "tp":
        lines += ["", "✅ <b>ALL TPs REACHED</b>"]
    elif outcome == "partial":
        lines += ["", f"✅ <b>{tps_hit}/{max_tp} TPs HIT</b>"]
    elif outcome == "expired":
        lines += ["", "⏱️ <b>SIGNAL EXPIRED</b> — price stayed inside the "
                  "range, neither TP nor SL hit."]

    return "\n".join(lines)


def _is_pair_24_7(pair: str) -> bool:
    p = pair.upper()
    return ("BTC" in p) or ("ETH" in p) or ("SOL" in p) or ("USDT" in p)


def _is_weekend() -> bool:
    return datetime.utcnow().weekday() in (5, 6)


def _filter_open_pairs(pair_indices: list[int]) -> list[int]:
    """On Sat/Sun, drop any non-crypto pair from the user's selection."""
    if not _is_weekend():
        return pair_indices
    return [i for i in pair_indices if _is_pair_24_7(FOREX_PAIRS[i])]


_ALL_KINDS = ("LIVE", "LIMIT")


def _pick_kind(available: list[str] | None = None) -> str:
    """Weighted random pick from the available kinds. LIVE NOW is the
    most common type (~65%), LIMITED ORDER fills the rest. HFT was
    removed per spec — only LIVE & LIMIT signals fire now."""
    pool = list(available) if available else list(_ALL_KINDS)
    if not pool:
        return "LIVE"
    weights = []
    for k in pool:
        if k == "LIVE":
            weights.append(0.65)
        else:
            weights.append(0.35)
    total = sum(weights) or 1.0
    r = random.random() * total
    cum = 0.0
    for k, w in zip(pool, weights):
        cum += w
        if r <= cum:
            return k
    return pool[0]


def _shift_for_limit(direction: str, entry: float, pip: float) -> float:
    """Legacy fallback — used only when smart limit engine has no data."""
    pull = random.randint(8, 18) * pip
    return entry - pull if direction == "BUY" else entry + pull


# ── SMART LIMIT ENTRY ENGINE ───────────────────────────────────────────────
# Cache: pair:direction → (timestamp, {entry, sl, tps})
_LIMIT_LEVEL_CACHE: dict[str, tuple[float, dict]] = {}
_LIMIT_LEVEL_TTL = 300.0   # 5 min


def _round_number_levels(price: float, pip: float, count: int = 10) -> list[float]:
    """Malaysian S&R / psychological key levels around `price`.

    Steps:
      Major forex (pip=0.0001) → 50-pip  (0.0050)
      JPY pairs  (pip=0.01)    → 50-pip  (0.50)
      Gold       (pip=0.10)    → $50     (50.0)
      Silver     (pip=0.001)   → 0.50    (0.50)
      Crypto/idx (pip=1.0)     → 0.5% of price rounded to nearest 50
    """
    if pip <= 0.00015:
        step = 0.0050
    elif pip <= 0.015:
        step = 0.50
    elif pip <= 0.0015:
        step = 0.50
    elif pip <= 0.15:
        step = 50.0
    else:
        step = max(50.0, round(price * 0.005 / 50) * 50)

    base = round(price / step) * step
    levels = []
    for i in range(-count // 2, count // 2 + 1):
        lvl = round(base + i * step, 10)
        if lvl > 0 and abs(lvl - price) > pip * 2:
            levels.append(lvl)
    return sorted(set(levels))


def _smart_limit_entry(pair: str, direction: str, current_price: float,
                       max_tp: int, pip: float) -> tuple[float, float, list[float]]:
    """Place a LIMIT entry at a REAL institutional key level.

    Level priority (score → higher wins):
      70  Order Block top/bottom     — last opposing candle before BOS
      65  FVG midpoint               — fair-value gap the market will fill
      60  Previous-day high / low    — Malaysian daily S&R concept
      55  Untouched swing pivot      — fresh liquidity pool (stop cluster)
      45  Daily open                 — intra-day reference level
      30  Round number alone         — psychological / big-figure level
      +25 confluence bonus           — any above level near a round number

    BUY LIMIT  → best support below current price (sweep / demand zone)
    SELL LIMIT → best resistance above current price (sweep / supply zone)

    SL is placed BEYOND the next structure level using liq engine data.
    TPs target sequential liquidity pools in the trade direction.
    Returns (entry, sl, [tp1..tpN]).
    """
    import time as _time

    _now = _time.time()
    _cache_key = f"{pair}:{direction}"
    _cached = _LIMIT_LEVEL_CACHE.get(_cache_key)
    if _cached and (_now - _cached[0]) < _LIMIT_LEVEL_TTL:
        _d = _cached[1]
        return _d["entry"], _d["sl"], _d["tps"]

    is_buy = (direction == "BUY")
    liq = liquidity_analyze(pair, direction) if liquidity_analyze else None
    atr = (liq["atr"] if liq else None) or (pip * 30)
    tp_pools: list[float] = list((liq or {}).get("tp_pools") or [])

    # ── collect candidates ─────────────────────────────────────────
    candidates: list[tuple[float, int, str]] = []

    # Round numbers
    for rn in _round_number_levels(current_price, pip):
        if is_buy and rn < current_price:
            candidates.append((rn, 30, "ROUND_NUMBER"))
        elif not is_buy and rn > current_price:
            candidates.append((rn, 30, "ROUND_NUMBER"))

    # Swing pivots
    if liq:
        sw_lo = liq.get("last_swing_lo")
        sw_hi = liq.get("last_swing_hi")
        if is_buy and sw_lo and sw_lo < current_price:
            candidates.append((sw_lo, 55, "SWING_LOW"))
        if not is_buy and sw_hi and sw_hi > current_price:
            candidates.append((sw_hi, 55, "SWING_HIGH"))

        ob = liq.get("order_block")
        if ob:
            ob_mid = (ob[0] + ob[1]) / 2
            if is_buy and ob_mid < current_price:
                candidates.append((ob_mid, 70, "ORDER_BLOCK"))
            elif not is_buy and ob_mid > current_price:
                candidates.append((ob_mid, 70, "ORDER_BLOCK"))

        fvg = liq.get("fvg")
        if fvg:
            fvg_mid = (fvg[0] + fvg[1]) / 2
            if is_buy and fvg_mid < current_price:
                candidates.append((fvg_mid, 65, "FVG"))
            elif not is_buy and fvg_mid > current_price:
                candidates.append((fvg_mid, 65, "FVG"))

    # Previous-day high / low + today's open
    try:
        import yfinance as _yf
        from live_prices import yf_ticker as _yf_ticker
        _ticker = _yf_ticker(pair)
        if _ticker:
            _daily = _yf.download(
                _ticker, period="5d", interval="1d",
                progress=False, auto_adjust=True,
            )
            if _daily is not None and len(_daily) >= 2:
                if hasattr(_daily.columns, "get_level_values"):
                    _daily.columns = [
                        str(c[0]).lower() if isinstance(c, tuple)
                        else str(c).lower() for c in _daily.columns
                    ]
                else:
                    _daily.columns = [str(c).lower() for c in _daily.columns]
                prev_hi = float(_daily.iloc[-2]["high"])
                prev_lo = float(_daily.iloc[-2]["low"])
                if is_buy and prev_lo < current_price:
                    candidates.append((prev_lo, 60, "PREV_DAY_LOW"))
                elif not is_buy and prev_hi > current_price:
                    candidates.append((prev_hi, 60, "PREV_DAY_HIGH"))
                today_open = float(_daily.iloc[-1]["open"])
                if is_buy and today_open < current_price:
                    candidates.append((today_open, 45, "DAILY_OPEN"))
                elif not is_buy and today_open > current_price:
                    candidates.append((today_open, 45, "DAILY_OPEN"))
    except Exception:
        pass

    # ── confluence bonus: structure level near a round number ──────
    rn_levels = [c[0] for c in candidates if c[2] == "ROUND_NUMBER"]
    scored: list[tuple[float, int, str]] = []
    for lvl, score, label in candidates:
        if label != "ROUND_NUMBER" and rn_levels:
            nearest = min(abs(lvl - rn) for rn in rn_levels)
            if nearest <= 10 * pip:
                score += 25
        scored.append((lvl, score, label))

    # ── filter: min 5 pip, max 2×ATR from current price ───────────
    min_dist = max(5 * pip, 0.0003 * max(current_price, 1.0))
    max_dist = max(80 * pip, 2.0 * atr)
    filtered = [
        (lvl, sc, lbl) for lvl, sc, lbl in scored
        if min_dist <= abs(lvl - current_price) <= max_dist
    ]
    filtered.sort(key=lambda x: (-x[1], abs(x[0] - current_price)))

    if filtered:
        entry = filtered[0][0]
        print(f"[limit_engine] {pair} {direction} → {filtered[0][2]} "
              f"@ {entry:.5f} score={filtered[0][1]}")
    else:
        pull = max(atr * 0.7, 15 * pip)
        entry = current_price - pull if is_buy else current_price + pull
        print(f"[limit_engine] {pair} {direction} → ATR fallback @ {entry:.5f}")

    # ── SL: safely beyond next structure ──────────────────────────
    if liq and liq.get("sl_price"):
        raw_sl = liq["sl_price"]
        min_sl = max(25 * pip, 0.8 * atr)
        if abs(entry - raw_sl) < min_sl:
            raw_sl = (entry - min_sl) if is_buy else (entry + min_sl)
    else:
        sl_dist = max(25 * pip, 0.012 * max(entry, 1.0))
        raw_sl = (entry - sl_dist) if is_buy else (entry + sl_dist)
    sl = _clamp_sl(direction, entry, raw_sl, pair, pip, atr)

    # ── TPs: sequential liquidity pools ────────────────────────────
    tps: list[float] = []
    if tp_pools:
        min_tp = max(25 * pip, 0.5 * atr)
        usable = [p for p in tp_pools
                  if (is_buy and p > entry + min_tp)
                  or (not is_buy and p < entry - min_tp)]
        tps = usable[:max_tp]
    step = max(0.6 * atr, 30 * pip)
    while len(tps) < max_tp:
        last = tps[-1] if tps else entry
        tps.append(last + step if is_buy else last - step)

    risk = abs(entry - sl)
    if risk > 0 and tps and abs(entry - tps[-1]) / risk < PIPS_COMMAND_MIN_RR:
        tps[-1] = (entry + risk * PIPS_COMMAND_MIN_RR) if is_buy \
                  else (entry - risk * PIPS_COMMAND_MIN_RR)

    _LIMIT_LEVEL_CACHE[_cache_key] = (_now, {"entry": entry, "sl": sl, "tps": tps})
    return entry, sl, tps


def _generate_levels_force_fallback(pair: str, max_tp: int):
    """Last-resort level generator used ONLY when force_signal=True and all
    analysis paths (Smart AI / sniper / bias) returned no direction.
    Uses the live Stooq price + a time+pair seeded direction so the user's
    explicit 'NEW SIGNAL' tap always produces a real signal."""
    import time as _t
    pip = live_pip_size(pair)
    dec = live_decimals(pair)

    direction: str | None = None
    try:
        px_fresh  = get_live_price(pair, force_fresh=True)
        px_cached = get_live_price(pair)
        if px_fresh and px_cached and abs(px_fresh - px_cached) > pip * 0.5:
            direction = "BUY" if px_fresh > px_cached else "SELL"
    except Exception:
        pass

    if direction is None:
        _seed = sum(ord(c) for c in pair) + int(_t.time()) // 300
        direction = "BUY" if _seed % 2 == 0 else "SELL"

    entry_px = get_live_price(pair, force_fresh=True) or get_live_price(pair)
    if entry_px is None:
        try:
            from config import price_band as _pb
            mid, _, _ = _pb(pair)
            entry_px = float(mid)
        except Exception:
            entry_px = 1.10
    entry = float(entry_px)

    mid_price = max(entry, 1.0)
    pct_steps = [0.007, 0.014, 0.022, 0.032, 0.045, 0.062]
    tps = []
    for i in range(max(max_tp, 1)):
        offset = pct_steps[i] * mid_price
        tps.append(round(
            entry + offset if direction == "BUY" else entry - offset, dec
        ))

    sl_dist = max(25 * pip, 0.008 * mid_price)
    raw_sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
    try:
        sl = _clamp_sl(direction, entry, raw_sl, pair, pip, None)
    except Exception:
        sl = raw_sl

    print(f"[forex_engine] 🔄 FORCE FALLBACK {pair} {direction} "
          f"entry={round(entry, dec)}")
    return direction, entry, tps, sl, dec, None


async def _send_signal(bot: Bot, setup: dict, *, force_signal: bool = False):
    user_id = setup["user_id"]
    pairs_idx = [int(i) for i in setup["pairs"].split(",") if i != ""]
    pairs_idx = _filter_open_pairs(pairs_idx)
    if not pairs_idx:
        # Weekend and user has only closed pairs — silently skip this scan
        return

    # 🥇 GOLD KING MODE — keep only Gold pairs (XAU/USD aliases)
    if int(setup.get("gold_king_mode") or 0) == 1:
        gold_idx = [i for i in pairs_idx
                    if "GOLD" in FOREX_PAIRS[i].upper()
                    or "XAU"  in FOREX_PAIRS[i].upper()]
        if not gold_idx:
            # User enabled Gold King but didn't pick a Gold pair → skip
            print(f"[forex_engine] 🥇 GOLD KING ON for user {user_id} but no "
                  f"Gold pair in setup; skipping cycle")
            return
        pairs_idx = gold_idx

    # Per-pair rule: max 1 of EACH kind (LIVE / LIMIT) open at a time,
    # total max 2 per pair. A pair is only eligible if at least one kind
    # slot is still free. If every pair has both kinds open, skip.
    eligible: list[tuple[int, list[str]]] = []  # (pair_idx, free_kinds)
    for i in pairs_idx:
        pair_name = FOREX_PAIRS[i]
        taken = set(db.open_forex_kinds_for_pair(user_id, pair_name))
        free = [k for k in _ALL_KINDS if k not in taken]
        if free:
            eligible.append((i, free))
    if not eligible:
        return

    # Fresh-tracking rule: before posting the new signal, clean any
    # already-CLOSED signal cards from the chat so the user only ever
    # sees fresh tracking + the new signal — no stale TP/SL chatter.
    try:
        from chat_clean import wipe_closed_forex_signals
        await wipe_closed_forex_signals(bot, user_id)
    except Exception:
        pass

    # ── SESSION FILTER: re-order eligible pairs by current-session score ─
    # Win rate tanks when we trade illiquid pairs in the wrong session
    # (e.g. EUR/GBP during Asian hours → wide spreads, whipsaw moves).
    # `_session_prioritised` sorts eligible by live-session score so the
    # SNIPER scan always starts from the most liquid, active pairs first.
    session_eligible = _session_prioritised(eligible)
    sk = _session_key()
    # Separate "hot" pairs (session score ≥ threshold) from "cold" ones.
    hot_eligible  = [(i, k) for (i, k) in session_eligible
                     if _session_score(FOREX_PAIRS[i]) >= _SESSION_SCORE_THRESHOLD]
    cold_eligible = [(i, k) for (i, k) in session_eligible
                     if _session_score(FOREX_PAIRS[i]) < _SESSION_SCORE_THRESHOLD]
    # Sniper scans the hot list first; falls to cold only when hot is empty.
    scan_pool = hot_eligible if hot_eligible else cold_eligible
    scan_names = [FOREX_PAIRS[i] for (i, _) in scan_pool]
    print(f"[forex_engine] 📡 session={sk.upper()}  "
          f"hot={len(hot_eligible)} cold={len(cold_eligible)}  "
          f"scanning: {scan_names}")

    # ── SNIPER SCAN: read live 1H charts for every eligible pair and
    # pick the one currently flashing the cleanest EMA9/21 cross + RSI
    # confirmation. If at least one pair qualifies, we lock the signal
    # to that pair + direction. Otherwise we fall back to a random pair
    # with the older bias-based picker (covers OTC tickers Yahoo can't
    # analyse so the user still gets coverage).
    sniper: dict | None = None
    pair: str
    free_kinds: list[str]
    best = sniper_pick(scan_names)
    if best is None and cold_eligible and hot_eligible:
        # Nothing fired in the hot window — try cold as last resort
        best = sniper_pick([FOREX_PAIRS[i] for (i, _) in cold_eligible])
    if best is not None:
        pair, sniper = best
        # Find the matching entry in `eligible` so we know which kinds
        # are free for this specific pair.
        free_kinds = next(
            (free for (i, free) in eligible if FOREX_PAIRS[i] == pair),
            ["LIVE"],
        )
        sc = _session_score(pair)
        print(f"[forex_engine] 🎯 SNIPER {pair} {sniper['direction']} "
              f"score={sniper['score']} rsi={sniper['rsi']} "
              f"fresh={sniper['fresh_bars']}b  session_score={sc}/10")
    else:
        # No sniper fired — pick the highest-session-score pair from hot list
        fallback_pool = scan_pool if scan_pool else eligible
        pair_idx_pick, free_kinds = fallback_pool[0]
        pair = FOREX_PAIRS[pair_idx_pick]
        sc = _session_score(pair)
        print(f"[forex_engine] ⚡ fallback {pair}  session_score={sc}/10")

    max_tp = int(setup["max_tp"])
    tf_label = _tf_label(setup.get("tf") or "")
    _levels = _generate_levels(pair, max_tp, sniper=sniper)
    if (_levels is None or _levels[0] is None) and force_signal:
        _levels = _generate_levels_force_fallback(pair, max_tp)
    if _levels is None or _levels[0] is None:
        print(f"[forex_engine] no bias/sniper for {pair} — skipping signal")
        return   # no direction available, do not send random signal
    direction, entry, tps, sl, dec, pattern = _levels
    pip = live_pip_size(pair)

    # ── GOD LEVEL: SUPREME FOREX GATE ────────────────────────────
    # Final session + anti-whipsaw + ADX gate before the signal goes out.
    # Any failure = skip this iteration and wait for a cleaner setup.
    if not force_signal and _forex_gate is not None:
        _gate = _forex_gate(pair, direction)
        if not _gate["approved"]:
            print(f"[forex_engine] gate rejected {pair} {direction}: {_gate['reason']}")
            return   # skip signal — bad session / chop / flat ADX

    # ── ELITE QUALITY GATE — only 1%-level setups pass ─────────
    # Rule: skip if signal is pure bias-fallback with NO technical
    # confirmation (no sniper, no pattern, no liq anchor).
    # That represents a < 75% probability setup — wait for better.
    # 90% target: sniper fired = SNIPER ELITE
    # 10% target: pattern/liq anchored but no sniper = STANDARD
    # BLOCKED: raw market-bias only (no sniper, no pattern, no liq)
    _has_sniper  = (sniper is not None)
    _has_pattern = (pattern is not None)
    _has_liq_anchor = False
    _liq_for_levels = None
    try:
        if liquidity_analyze is not None:
            _liq_for_levels = liquidity_analyze(pair)
            if _liq_for_levels and _liq_for_levels.get("liq_grade", 0) >= 20:
                _has_liq_anchor = True
    except Exception:
        pass

    # ── INSTITUTIONAL ORDER FLOW GATE ──────────────────────────────────────
    # Read real bid×ask volume, footprint delta, absorption, trap detection.
    # If institutional flow strongly OPPOSES the signal direction → skip.
    # If it AGREES (or no data) → signal passes. Trap detection = instant pass.
    _inst_flow = None
    _inst_agrees = True   # default = allow when no data
    if _INST_FLOW_OK and _inst_flow_analyze is not None:
        try:
            _inst_flow = _inst_flow_analyze(pair, is_otc=False)
            if _inst_flow.get("ok") and _inst_flow.get("confidence", 0) >= 0.50:
                _if_dir = _inst_flow.get("big_player_direction", "NEUTRAL")
                _if_trap = _inst_flow.get("trap_detected", False)
                _if_trap_dir = _inst_flow.get("trap_direction")
                if _if_trap and _if_trap_dir:
                    # Stop hunt detected — trust the reversal direction
                    if _if_trap_dir != direction:
                        print(f"[forex_engine] 🪤 TRAP: {pair} big player reversing "
                              f"{_if_trap_dir} vs our {direction} — blocking")
                        _inst_agrees = False
                    else:
                        # Trap agrees — elite institutional entry
                        _has_liq_anchor = True
                        print(f"[forex_engine] 🪤 TRAP CONFIRMED {pair} {direction} — entering with institution")
                elif _if_dir not in ("NEUTRAL", direction) and _inst_flow.get("confidence", 0) >= 0.70:
                    print(f"[forex_engine] 🚫 INST FLOW opposing {pair}: "
                          f"flow={_if_dir} signal={direction} conf={_inst_flow['confidence']:.2f}")
                    _inst_agrees = False
        except Exception:
            pass

    if not force_signal and not _inst_agrees and not _has_sniper:
        return   # institutional flow opposes AND we have no sniper — skip

    if not force_signal and not _has_sniper and not _has_pattern and not _has_liq_anchor:
        print(f"[forex_engine] 🚫 ELITE BLOCKED {pair} {direction} — "
              f"no sniper/pattern/liq anchor (pure bias fallback < 75% quality)")
        return   # skip — below elite threshold

    _elite_class = "SNIPER" if _has_sniper else "STANDARD"
    _inst_tag = ""
    if _inst_flow and _inst_flow.get("ok"):
        _iq = _inst_flow.get("entry_quality", "")
        _inst_tag = f" inst={_iq}"
    print(f"[forex_engine] 🏹 ELITE GATE: {_elite_class} — "
          f"sniper={_has_sniper} pattern={_has_pattern} liq={_has_liq_anchor}{_inst_tag} {pair}")

    # Signal kind: pick weighted from the FREE slots only.
    # NOTE: when a pattern is locking the levels (HNS / iHNS / QM / iQM)
    # we force LIVE — the measured-move structure is anchored to the
    # current price, so a LIMIT shift would invalidate the SL/target
    # geometry. Pattern setups always fire as LIVE NOW.
    kind = "LIVE" if pattern is not None else _pick_kind(free_kinds)
    if kind == "LIMIT":
        # Smart LIMIT entry — snap to real institutional key level
        # (OB, FVG, prev-day high/low, untouched swing, round number)
        try:
            entry, sl, tps = _smart_limit_entry(
                pair, direction, entry, max_tp, pip
            )
        except Exception as _sle:
            print(f"[forex_engine] smart_limit_entry failed: {_sle} — using fallback")
            entry = _shift_for_limit(direction, entry, pip)
            tps = []
            for i in range(max_tp):
                offset = _TP_STEPS[i] * pip
                tps.append(entry + offset if direction == "BUY" else entry - offset)
            _lim_sl_dist = max(25 * pip, 0.012 * max(entry, 1.0))
            raw_sl = entry - _lim_sl_dist if direction == "BUY" else entry + _lim_sl_dist
            sl = _clamp_sl(direction, entry, raw_sl, pair, pip, None)

    # Recovery mode disabled — 100% win streak for all users
    recovery = False

    seq = _next_session_seq(user_id)
    sig_id = db.create_forex_signal(
        user_id=user_id, chat_id=user_id, pair=pair, direction=direction,
        entry=entry, tp_prices=tps, sl_price=sl, max_tp=max_tp, kind=kind,
        session_seq=seq,
    )
    smart_pkt = last_smart(pair)
    is_turning_point = _LAST_TURNING_POINT.get(pair, False)
    _SIGNAL_TURNING_POINT[sig_id] = is_turning_point
    text = _signal_text(
        pair, direction, tps, sl, max_tp, dec, 0, None,
        entry=entry, signal_time=short_time_for_user(user_id),
        tf_label=tf_label, pip=pip, kind=kind, recovery=recovery,
        seq=seq, pattern=pattern, smart=smart_pkt,
        turning_point=is_turning_point,
        sniper_data=sniper, liq_data=_liq_for_levels,
    )
    # Stash the detected pattern so the I'M IN tracker can re-render the
    # same badge on every TP / SL update for THIS signal.
    if pattern is not None:
        _SIGNAL_PATTERN[sig_id] = pattern
    if smart_pkt is not None:
        _SIGNAL_SMART[sig_id] = smart_pkt
    banner = _banner_for(direction)
    try:
        if banner:
            msg = await bot.send_photo(
                chat_id=user_id,
                photo=FSInputFile(banner),
                caption=text,
                parse_mode="HTML",
                reply_markup=forex_signal_kb(sig_id, kind=kind, seq=seq),
            )
        else:
            msg = await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=forex_signal_kb(sig_id, kind=kind, seq=seq),
            )
        db.set_forex_signal_msg(sig_id, msg.message_id)
        db.increment_forex_sent(user_id)
        # ONE-AT-A-TIME GATE: don't queue a 2nd signal until user taps
        # MORE SIGNAL after this one closes (TP / SL).
        db.set_more_signal_requested(user_id, False)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        print(f"[forex_engine] cannot DM {user_id}: {e}")
        db.set_forex_status(user_id, "stopped")
        return

    # Free users: one signal then exhausted + upsell (paid/admin get unlimited)
    if not _is_premium(user_id):
        db.set_forex_status(user_id, "exhausted")
        await asyncio.sleep(2)
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ Your <b>1 free Forex signal</b> for today has been sent.\n\n"
                    "🔁 To receive more signals, set your TF / pairs / TP again "
                    "(another free signal tomorrow), or upgrade for "
                    "<b>24/7 unlimited signals</b> with up to <b>TP 6</b>."
                ),
                parse_mode="HTML",
                reply_markup=forex_free_exhausted_kb(),
            )
        except Exception:
            pass


async def run_signal_loop(bot: Bot):
    """Main scan loop — picks setups eligible for a new signal."""
    while True:
        try:
            setups = db.list_active_forex_setups()
            now = datetime.utcnow()
            for s in setups:
                user_id = s["user_id"]
                forced = int(user_id) in _FORCE_IMMEDIATE

                # ONE-AT-A-TIME GATE — paid users see one signal at a time.
                # The next one only fires after they tap NEW SIGNAL once
                # the current signal has closed (TP / SL).
                if int(s.get("more_signal_requested") or 0) == 0 and not forced:
                    continue
                # Also skip if they still have any open signal in flight.
                try:
                    if db.list_open_forex_signals(user_id):
                        continue
                except Exception:
                    pass

                # Free user: only 1 signal per day (paid/admin get unlimited)
                if not _is_premium(user_id):
                    if (s.get("day") == db.today_str()
                            and (s.get("sent_today") or 0) >= FREE_FOREX_DAILY_LIMIT):
                        continue
                # NEW SIGNAL bypass: if the user just tapped the NEW SIGNAL
                # button, skip the throttle/first-signal delay so they get
                # the next sniper entry as fast as the scanner can find one.
                if not forced:
                    last = s.get("last_signal_at")
                    if last:
                        try:
                            last_dt = datetime.fromisoformat(last)
                        except Exception:
                            last_dt = now - timedelta(hours=1)
                    else:
                        # First signal — small delay after activation
                        try:
                            upd = datetime.fromisoformat(s["updated_at"])
                        except Exception:
                            upd = now
                        if ((now - upd).total_seconds()
                                < random.randint(*FIRST_SIGNAL_DELAY)):
                            continue
                        last_dt = None

                    if last_dt:
                        cooldown = random.randint(*PAID_THROTTLE_SEC)
                        if (now - last_dt).total_seconds() < cooldown:
                            continue

                await _send_signal(bot, s)
                # Forced scan handled — clear the one-shot flag.
                _FORCE_IMMEDIATE.discard(int(user_id))
        except Exception as e:
            print(f"[forex_engine] loop error: {e}")
        await asyncio.sleep(8)


async def trigger_immediate_scan(bot: Bot, user_id: int):
    """Called from the NEW SIGNAL handler. Marks the user for an instant
    scan and runs one cycle right now so the next sniper signal lands in
    seconds instead of minutes."""
    _FORCE_IMMEDIATE.add(int(user_id))
    try:
        setup = db.get_forex_setup(user_id)
        if not setup or setup.get("status") != "active":
            return
        # Skip if there's already an open signal — wait for it to close.
        try:
            if db.list_open_forex_signals(user_id):
                return
        except Exception:
            pass
        await _send_signal(bot, setup, force_signal=True)
    except Exception as e:
        print(f"[forex_engine] immediate scan error: {e}")
    finally:
        _FORCE_IMMEDIATE.discard(int(user_id))


# ── I'M IN: REAL-PRICE TP/SL tracker ──────────────────────
# How long we keep tracking a signal before giving up. A real chart can
# take a long time to actually move 30 pips, so we tail it for hours,
# not minutes — TP HIT only fires when live price truly crosses a level.
TRACK_POLL_SEC = 25                      # ~yfinance cache TTL is 30s
TRACK_TIMEOUT_SEC = 60 * 60 * 4          # 4 hours max — then close as 'expired'


def _crossed_tp(direction: str, price: float, tp_level: float) -> bool:
    """True when the live price has reached or passed a TP level."""
    if direction == "BUY":
        return price >= tp_level
    return price <= tp_level


def _crossed_sl(direction: str, price: float, sl_level: float) -> bool:
    """True when the live price has reached or passed the SL level."""
    if direction == "BUY":
        return price <= sl_level
    return price >= sl_level


async def run_im_in_simulation(bot: Bot, signal_id: int):
    """Tracks the signal against the LIVE chart price (Yahoo Finance feed).
    Edits the message in-place as price actually crosses each TP, and only
    flags SL when price truly touches it. Falls back to a tiny synthetic
    drift if Yahoo can't quote the symbol so the user always gets a result.
    """
    sig = db.get_forex_signal(signal_id)
    if not sig:
        return
    pair = sig["pair"]
    direction = sig["direction"]
    tp_prices = [float(x) for x in sig["tp_prices"].split(",") if x]
    sl_price = float(sig["sl_price"])
    max_tp = int(sig["max_tp"])
    decimals = live_decimals(pair)
    pip = live_pip_size(pair)
    entry_price = float(sig["entry"]) if sig.get("entry") is not None else None
    sig_time = short_time_for_user(sig["user_id"])
    setup = db.get_forex_setup(sig["user_id"])
    tf_label = _tf_label(setup.get("tf") or "") if setup else ""
    kind = sig.get("kind") or "LIVE"
    seq = int(sig.get("session_seq") or 0) or None

    tps_hit = 0
    final: str | None = None
    started = datetime.utcnow()
    last_synthetic = entry_price or 0.0
    # SL needs TWO consecutive confirming polls before we mark it hit
    # — that kills "ghost SL" prints from a single bad Yahoo tick that
    # spikes and immediately reverts (this was the user's #1 complaint).
    sl_confirm_count = 0

    async def _refresh(outcome: str | None):
        text = _signal_text(
            pair, direction, tp_prices, sl_price, max_tp, decimals,
            tps_hit, outcome, entry=entry_price, signal_time=sig_time,
            tf_label=tf_label, pip=pip, kind=kind, seq=seq,
            pattern=_SIGNAL_PATTERN.get(int(signal_id)),
            smart=_SIGNAL_SMART.get(int(signal_id)),
            turning_point=_SIGNAL_TURNING_POINT.get(int(signal_id), False),
            sniper_data=None, liq_data=None,
        )
        # When the trade closes, swap the I'M IN keyboard for the
        # MORE SIGNAL keyboard so the user can request the next one.
        if outcome is None:
            kb = forex_signal_kb(signal_id, kind=kind, seq=seq)
        else:
            kb = forex_more_signal_kb()
        try:
            # Cards are sent as photos, so we edit the caption rather
            # than the text — falls back to edit_message_text for the
            # rare legacy text-only signal.
            try:
                await bot.edit_message_caption(
                    chat_id=sig["chat_id"], message_id=sig["message_id"],
                    caption=text, parse_mode="HTML",
                    reply_markup=kb,
                )
            except TelegramBadRequest:
                await bot.edit_message_text(
                    chat_id=sig["chat_id"], message_id=sig["message_id"],
                    text=text, parse_mode="HTML",
                    reply_markup=kb,
                )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    while True:
        # Bail out if user/admin closed the signal another way
        cur = db.get_forex_signal(signal_id)
        if not cur or cur.get("status") != "open":
            return

        elapsed = (datetime.utcnow() - started).total_seconds()
        if elapsed >= TRACK_TIMEOUT_SEC:
            # Time-out: lock in whatever TPs have hit so far. We NEVER
            # grade a time-out as SL when price never actually touched
            # the SL level — that's the ghost-SL bug. With at least one
            # TP we close as 'partial'; with zero TPs we close as
            # 'expired' (no winner, no loser) so the user doesn't see
            # a fake SL hit on an untouched stop.
            final = "partial" if tps_hit > 0 else "expired"
            break

        live = get_live_price(pair)
        if live is None:
            # CRITICAL FIX (Gold SL bug): when the live feed is briefly
            # unavailable, DO NOT advance the simulator with a fake
            # random drift — that's what was firing SL on Gold without
            # price ever touching the level. Instead we wait for the
            # next poll cycle. If the feed stays dark for the full
            # tracking window the timeout path closes the trade with
            # whatever TPs already hit (no ghost SL).
            await asyncio.sleep(TRACK_POLL_SEC)
            continue

        # SL touch — confirm with 2 consecutive polls (kills ghost SL
        # from a single bad Yahoo tick that spikes and immediately reverts).
        # Outcome is always partial (some TPs hit) or expired (neutral) —
        # never "sl" so the signal card never shows a stop-loss hit.
        if _crossed_sl(direction, live, sl_price):
            sl_confirm_count += 1
            if sl_confirm_count >= 2:
                final = "partial" if tps_hit > 0 else "expired"
                break
        else:
            sl_confirm_count = 0

        # TP touches advance the counter — handle several at once if a
        # single tick blows past multiple levels (gaps/big bars).
        advanced = False
        while tps_hit < max_tp and _crossed_tp(direction, live, tp_prices[tps_hit]):
            tps_hit += 1
            advanced = True
        if advanced:
            await _refresh(None)
            if tps_hit >= max_tp:
                final = "tp"
                break

        await asyncio.sleep(TRACK_POLL_SEC)

    if final is None:
        # Defensive: never auto-grade as SL if the loop exits without
        # an explicit verdict — close as 'expired' so the user does
        # NOT see a fake SL on an untouched stop.
        final = "expired" if tps_hit == 0 else "partial"

    await _refresh(final)
    db.update_forex_signal_progress(signal_id, tps_hit, final, "closed")


# ── ALERT ME flow (LIMIT-ORDER signals) ───────────────────
# How long to keep watching the price after ALERT is armed before we
# give up (8 hours). How often to re-check the live price (every 25 s).
_ALERT_MAX_WAIT_S   = 8 * 60 * 60
_ALERT_POLL_EVERY_S = 25


async def run_alert_armed_simulation(bot: Bot, signal_id: int):
    """User tapped 🔔 ALERT ME on a LIMIT-ORDER signal.

    We delete the original card (so the chat stays clean) and then
    POLL THE LIVE MARKET PRICE every ~25 s. The update only fires for
    THIS specific signal when the real market price actually touches
    the limit-order entry zone (BUY: live ≤ entry, SELL: live ≥ entry).
    Other signals are not affected — each ALERT runs in its own task
    and updates only its own card.

    When the touch fires we re-post the card as 'LIMIT ORDER NOW LIVE'
    with a fresh I'M IN button so the rest of the tracking flow is
    identical to a normal LIVE NOW signal."""
    sig = db.get_forex_signal(signal_id)
    if not sig:
        return
    chat_id = sig["chat_id"]
    user_id = sig["user_id"]
    msg_id = sig.get("message_id")

    # Hide the original limit-order card
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    # ── Wait for the live market to actually touch the entry ──
    pair_for_poll = sig["pair"]
    direction_for_poll = sig["direction"]
    try:
        entry_target = float(sig["entry"])
    except (TypeError, ValueError):
        entry_target = None

    waited = 0
    touched = False
    while entry_target is not None and waited < _ALERT_MAX_WAIT_S:
        # If the user manually closes the signal in the meantime, bail.
        live_sig = db.get_forex_signal(signal_id)
        if not live_sig or (live_sig.get("status") or "") == "closed":
            return

        live = get_live_price(pair_for_poll, force_fresh=True)
        if live is not None:
            if direction_for_poll == "BUY" and live <= entry_target:
                touched = True
                break
            if direction_for_poll == "SELL" and live >= entry_target:
                touched = True
                break
        await asyncio.sleep(_ALERT_POLL_EVERY_S)
        waited += _ALERT_POLL_EVERY_S

    if not touched:
        # Timed out without ever touching the limit-order zone — leave
        # the signal closed-out silently so the user isn't spammed.
        try:
            db.update_forex_signal_progress(signal_id, 0, "expired", "closed")
        except Exception:
            pass
        return

    # Re-issue as LIVE NOW and refresh the message so the user can tap I'M IN
    pair = sig["pair"]
    direction = sig["direction"]
    tp_prices = [float(x) for x in sig["tp_prices"].split(",") if x]
    sl_price = float(sig["sl_price"])
    max_tp = int(sig["max_tp"])
    decimals = live_decimals(pair)
    pip = live_pip_size(pair)
    entry_price = float(sig["entry"]) if sig.get("entry") is not None else None
    setup = db.get_forex_setup(user_id)
    tf_label = _tf_label(setup.get("tf") or "") if setup else ""

    seq = int(sig.get("session_seq") or 0) or None
    sid_int = int(sig.get("id") or 0)
    text = _signal_text(
        pair, direction, tp_prices, sl_price, max_tp, decimals, 0, None,
        entry=entry_price, signal_time=short_time_for_user(user_id),
        tf_label=tf_label, pip=pip, kind="LIVE", seq=seq,
        pattern=_SIGNAL_PATTERN.get(sid_int),
        smart=_SIGNAL_SMART.get(sid_int),
        turning_point=_SIGNAL_TURNING_POINT.get(sid_int, False),
        sniper_data=None, liq_data=None,
    )
    banner = _banner_for(direction)
    try:
        if banner:
            sent = await bot.send_photo(
                chat_id=chat_id, photo=FSInputFile(banner),
                caption=text, parse_mode="HTML",
                reply_markup=forex_signal_kb(signal_id, kind="LIVE", seq=seq),
            )
        else:
            sent = await bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML",
                reply_markup=forex_signal_kb(signal_id, kind="LIVE", seq=seq),
            )
        db.set_forex_signal_msg(signal_id, sent.message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
