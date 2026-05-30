"""Deep Price-Action Pattern Engine — HNS / Inverse-HNS / QM (Quasimodo).

This module gives the SUPREME PRO sniper a real chartist's eye. It scans
the live 1H candles for the classic high-conviction reversal structures:

    * Head & Shoulders            (HNS)        → bearish reversal
    * Inverse Head & Shoulders    (iHNS)       → bullish reversal
    * Quasimodo / Over-and-Under  (QM bear)    → bearish reversal
    * Quasimodo  (QM bull)        (iQM)        → bullish reversal

For every pattern we compute:
    direction  — 'BUY' / 'SELL'
    entry      — neckline / QM-line trigger price
    sl         — protective stop (above RS / below RS, beyond the QM swing)
    target     — measured-move take-profit (height of the head / QM leg)
    rr         — reward : risk based on the measured move
    name       — human label used in the signal card
    score      — 0..100 pattern-quality score (symmetry + R:R + freshness)

Public API
----------
    detect_patterns(pair) -> list[dict]      # all valid patterns, freshest first
    best_pattern(pair)    -> dict | None     # highest-scoring pattern
    pattern_for_direction(pair, direction) -> dict | None  # filter by side

The scanner caches per-ticker for ~120 s so it can be polled cheaply
alongside the existing EMA/RSI sniper.
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception as _e:  # pragma: no cover
    print(f"[patterns] yfinance import failed: {_e}")
    yf = None
    pd = None
    _YF_OK = False

from live_prices import yf_ticker, pip_size as live_pip_size

# ── Tunables ──────────────────────────────────────────────
TIMEFRAME      = "1h"      # 1H candles — same as the sniper
LOOKBACK_BARS  = 250       # how many recent candles to scan
PIVOT_WINDOW   = 5         # swing high/low confirmation window
SHOULDER_TOL   = 0.18      # left/right shoulder allowed to differ by 18% of head height
NECKLINE_TOL   = 0.10      # the two troughs that form the neckline allowed to differ by 10%
RECENT_BARS    = 25        # pattern must complete within the last N bars
MIN_RR         = 1.6       # minimum reward:risk to ship a pattern
MAX_RR_USED    = 5.0       # cap projected RR so the score doesn't run away
PATTERN_TTL    = 120.0     # cache TTL seconds


_CACHE: dict[str, tuple[float, list[dict]]] = {}


# ─────────────────────────────────────────────────────────
#  Data fetch
# ─────────────────────────────────────────────────────────
def _fetch(ticker: str):
    if not _YF_OK:
        return None
    try:
        df = yf.download(
            ticker,
            period="60d",
            interval=TIMEFRAME,
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or len(df) < PIVOT_WINDOW * 4:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df.tail(LOOKBACK_BARS).copy().reset_index(drop=True)
    except Exception as e:
        print(f"[patterns] fetch error {ticker}: {e}")
        return None


# ─────────────────────────────────────────────────────────
#  Swing detection
# ─────────────────────────────────────────────────────────
def _pivots(df, window: int = PIVOT_WINDOW):
    """Return (highs, lows) where each is a list of (idx, price).

    A pivot high at i means highs[i] is the max of [i-window..i+window].
    A pivot low at i means lows[i]  is the min of that same window.
    """
    highs: list[tuple[int, float]] = []
    lows:  list[tuple[int, float]] = []
    h = df["high"].astype(float).tolist()
    l = df["low"].astype(float).tolist()
    n = len(df)
    for i in range(window, n - window):
        seg_h = h[i - window:i + window + 1]
        seg_l = l[i - window:i + window + 1]
        if h[i] == max(seg_h):
            highs.append((i, h[i]))
        if l[i] == min(seg_l):
            lows.append((i, l[i]))
    return highs, lows


# ─────────────────────────────────────────────────────────
#  Head & Shoulders  (bearish)
# ─────────────────────────────────────────────────────────
def _scan_hns(df, highs, lows, n: int) -> list[dict]:
    """Classical HNS: 3 pivot highs (LS, H, RS) with H > LS, H > RS,
    LS ≈ RS. Two pivot lows between them form the neckline; trigger
    is a candle close BELOW the neckline."""
    found: list[dict] = []
    if len(highs) < 3 or len(lows) < 2:
        return found

    for i in range(len(highs) - 2):
        ls_idx, ls = highs[i]
        h_idx,  hp = highs[i + 1]
        rs_idx, rs = highs[i + 2]

        # head must be the highest of the three
        if hp <= ls or hp <= rs:
            continue
        # shoulders within tolerance of each other (relative to head height)
        head_height_rough = max(1e-9, hp - min(ls, rs))
        if abs(ls - rs) / head_height_rough > SHOULDER_TOL:
            continue
        # pattern must be reasonably recent
        if (n - 1) - rs_idx > RECENT_BARS:
            continue

        # find the troughs between LS-H and H-RS
        t1 = [(j, p) for (j, p) in lows if ls_idx < j < h_idx]
        t2 = [(j, p) for (j, p) in lows if h_idx  < j < rs_idx]
        if not t1 or not t2:
            continue
        t1_idx, t1p = min(t1, key=lambda x: x[1])
        t2_idx, t2p = min(t2, key=lambda x: x[1])
        avg_neck = (t1p + t2p) / 2.0
        if abs(t1p - t2p) / max(1e-9, avg_neck) > NECKLINE_TOL:
            continue

        # neckline trigger: most recent close below the neckline
        last_close = float(df["close"].iloc[-1])
        if last_close >= avg_neck:
            # not yet triggered → no actionable signal
            continue

        head_height = hp - avg_neck                   # measured move
        sl    = max(rs, ls) + 0.10 * head_height       # SL just above right shoulder
        entry = last_close
        target = avg_neck - head_height                # measured-move target
        risk   = abs(entry - sl)
        reward = abs(entry - target)
        if risk <= 0:
            continue
        rr = reward / risk
        if rr < MIN_RR:
            continue

        score = _score(head_height, ls, rs, rr, n - 1 - rs_idx)
        found.append({
            "name":      "Head & Shoulders",
            "code":      "HNS",
            "direction": "SELL",
            "entry":     float(entry),
            "sl":        float(sl),
            "target":    float(target),
            "rr":        float(round(rr, 2)),
            "score":     int(score),
            "neckline":  float(avg_neck),
            "head":      float(hp),
            "ls":        float(ls),
            "rs":        float(rs),
            "fresh":     int(n - 1 - rs_idx),
        })
    return found


# ─────────────────────────────────────────────────────────
#  Inverse Head & Shoulders  (bullish)
# ─────────────────────────────────────────────────────────
def _scan_inv_hns(df, highs, lows, n: int) -> list[dict]:
    """Inverse HNS: 3 pivot lows (LS, H, RS) with H < LS, H < RS,
    LS ≈ RS. Two pivot highs between them form the neckline; trigger
    is a candle close ABOVE the neckline."""
    found: list[dict] = []
    if len(lows) < 3 or len(highs) < 2:
        return found

    for i in range(len(lows) - 2):
        ls_idx, ls = lows[i]
        h_idx,  hp = lows[i + 1]
        rs_idx, rs = lows[i + 2]

        if hp >= ls or hp >= rs:
            continue
        head_depth_rough = max(1e-9, max(ls, rs) - hp)
        if abs(ls - rs) / head_depth_rough > SHOULDER_TOL:
            continue
        if (n - 1) - rs_idx > RECENT_BARS:
            continue

        t1 = [(j, p) for (j, p) in highs if ls_idx < j < h_idx]
        t2 = [(j, p) for (j, p) in highs if h_idx  < j < rs_idx]
        if not t1 or not t2:
            continue
        t1_idx, t1p = max(t1, key=lambda x: x[1])
        t2_idx, t2p = max(t2, key=lambda x: x[1])
        avg_neck = (t1p + t2p) / 2.0
        if abs(t1p - t2p) / max(1e-9, avg_neck) > NECKLINE_TOL:
            continue

        last_close = float(df["close"].iloc[-1])
        if last_close <= avg_neck:
            continue

        head_depth = avg_neck - hp
        sl    = min(rs, ls) - 0.10 * head_depth
        entry = last_close
        target = avg_neck + head_depth
        risk   = abs(entry - sl)
        reward = abs(target - entry)
        if risk <= 0:
            continue
        rr = reward / risk
        if rr < MIN_RR:
            continue

        score = _score(head_depth, ls, rs, rr, n - 1 - rs_idx)
        found.append({
            "name":      "Inverse Head & Shoulders",
            "code":      "iHNS",
            "direction": "BUY",
            "entry":     float(entry),
            "sl":        float(sl),
            "target":    float(target),
            "rr":        float(round(rr, 2)),
            "score":     int(score),
            "neckline":  float(avg_neck),
            "head":      float(hp),
            "ls":        float(ls),
            "rs":        float(rs),
            "fresh":     int(n - 1 - rs_idx),
        })
    return found


# ─────────────────────────────────────────────────────────
#  Quasimodo  (bearish: HH → HL → HH → LL break  →  retest of HH zone)
#  Bullish QM is the mirror image.
# ─────────────────────────────────────────────────────────
def _scan_qm(df, highs, lows, n: int) -> list[dict]:
    """Quasimodo / Over-and-Under reversal pattern.

    Bearish QM (SELL):
        L1 (low) → H1 (high) → L2 (higher low) → H2 (higher high)
        → L3 (lower low that breaks below L2) → price retraces UP
        toward the H2 zone — that's the QM-line short entry.
    Bullish QM (BUY) is the mirror.
    """
    found: list[dict] = []
    last_close = float(df["close"].iloc[-1])

    # Build a single sorted swing series (label H/L) so we can read
    # the pivot rhythm sequentially.
    pivots = sorted(
        [(idx, p, "H") for (idx, p) in highs] +
        [(idx, p, "L") for (idx, p) in lows],
        key=lambda x: x[0],
    )
    if len(pivots) < 5:
        return found

    # Walk the last few pivots looking for the QM rhythm.
    for k in range(4, len(pivots)):
        seq = pivots[k - 4:k + 1]
        labels = "".join(s[2] for s in seq)
        # Bearish QM rhythm: L H L H L
        if labels == "LHLHL":
            l1 = seq[0]; h1 = seq[1]; l2 = seq[2]; h2 = seq[3]; l3 = seq[4]
            if not (h2[1] > h1[1] and l2[1] > l1[1] and l3[1] < l2[1]):
                continue
            # must be recent
            if (n - 1) - l3[0] > RECENT_BARS:
                continue
            # entry zone = retest of H2 (price has to come back up)
            if last_close > h2[1]:
                continue  # already broke above the QM line — invalid
            entry = float(min(last_close * 1.0, h2[1]))   # short at/near H2
            sl    = float(h2[1] + 0.20 * (h2[1] - l3[1]))
            leg   = h2[1] - l3[1]
            target = float(l3[1] - 0.50 * leg)
            risk   = abs(entry - sl)
            reward = abs(entry - target)
            if risk <= 0:
                continue
            rr = reward / risk
            if rr < MIN_RR:
                continue
            score = _score(leg, h1[1], h2[1], rr, n - 1 - l3[0])
            found.append({
                "name":      "Quasimodo (Bearish)",
                "code":      "QM",
                "direction": "SELL",
                "entry":     entry,
                "sl":        sl,
                "target":    target,
                "rr":        float(round(rr, 2)),
                "score":     int(score),
                "qm_line":   float(h2[1]),
                "fresh":     int(n - 1 - l3[0]),
            })

        # Bullish QM rhythm: H L H L H
        elif labels == "HLHLH":
            h1 = seq[0]; l1 = seq[1]; h2 = seq[2]; l2 = seq[3]; h3 = seq[4]
            if not (l2[1] < l1[1] and h2[1] < h1[1] and h3[1] > h2[1]):
                continue
            if (n - 1) - h3[0] > RECENT_BARS:
                continue
            if last_close < l2[1]:
                continue   # already broke below QM line — invalid
            entry = float(max(last_close * 1.0, l2[1]))   # long at/near L2
            sl    = float(l2[1] - 0.20 * (h3[1] - l2[1]))
            leg   = h3[1] - l2[1]
            target = float(h3[1] + 0.50 * leg)
            risk   = abs(entry - sl)
            reward = abs(target - entry)
            if risk <= 0:
                continue
            rr = reward / risk
            if rr < MIN_RR:
                continue
            score = _score(leg, l1[1], l2[1], rr, n - 1 - h3[0])
            found.append({
                "name":      "Quasimodo (Bullish)",
                "code":      "iQM",
                "direction": "BUY",
                "entry":     entry,
                "sl":        sl,
                "target":    target,
                "rr":        float(round(rr, 2)),
                "score":     int(score),
                "qm_line":   float(l2[1]),
                "fresh":     int(n - 1 - h3[0]),
            })

    return found


# ─────────────────────────────────────────────────────────
#  Scoring  — 0..100
# ─────────────────────────────────────────────────────────
def _score(scale: float, side_a: float, side_b: float,
           rr: float, fresh_bars: int) -> int:
    """Quality score combining shoulder symmetry, R:R and freshness.

    * Symmetry  → up to 35 pts (closer side_a ≈ side_b → more pts)
    * R:R       → up to 40 pts (capped at MAX_RR_USED)
    * Freshness → up to 25 pts (the more recent the trigger, the better)
    """
    # symmetry
    diff = abs(side_a - side_b) / max(1e-9, scale)
    sym_pts = max(0, int(35 * (1.0 - min(1.0, diff / SHOULDER_TOL))))
    # rr
    rr_pts  = int(40 * (min(rr, MAX_RR_USED) / MAX_RR_USED))
    # freshness
    fresh_pts = max(0, 25 - fresh_bars)
    return min(100, sym_pts + rr_pts + fresh_pts)


# ─────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────
def detect_patterns(pair: str) -> list[dict]:
    """Return every valid HNS / iHNS / QM / iQM detected on the live 1H
    chart for `pair`, sorted by score (best first). Cached ~120 s per
    ticker."""
    ticker = yf_ticker(pair)
    if not ticker:
        return []
    now = time.time()
    cached = _CACHE.get(ticker)
    if cached and (now - cached[0]) < PATTERN_TTL:
        return cached[1]

    df = _fetch(ticker)
    if df is None or "close" not in df.columns:
        _CACHE[ticker] = (now, [])
        return []

    highs, lows = _pivots(df)
    n = len(df)
    out: list[dict] = []
    out += _scan_hns(df, highs, lows, n)
    out += _scan_inv_hns(df, highs, lows, n)
    out += _scan_qm(df, highs, lows, n)
    out.sort(key=lambda p: p["score"], reverse=True)

    # Attach a pip-distance breakdown so callers can speak the user's
    # "pips command" language without re-computing it everywhere.
    pip = live_pip_size(pair)
    for p in out:
        try:
            p["risk_pips"]   = round(abs(p["entry"] - p["sl"])      / pip, 1)
            p["target_pips"] = round(abs(p["entry"] - p["target"])  / pip, 1)
        except Exception:
            p["risk_pips"]   = 0.0
            p["target_pips"] = 0.0

    _CACHE[ticker] = (now, out)
    return out


def best_pattern(pair: str) -> Optional[dict]:
    """Return the single best-scoring pattern on `pair`, or None."""
    pats = detect_patterns(pair)
    return pats[0] if pats else None


def pattern_for_direction(pair: str, direction: str) -> Optional[dict]:
    """Return the best pattern that AGREES with `direction` (BUY/SELL)."""
    pats = detect_patterns(pair)
    for p in pats:
        if p["direction"] == direction:
            return p
    return None
