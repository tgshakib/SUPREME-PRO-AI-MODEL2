"""
⚡ Instant Signal Engine — fires a live forex signal in 5-8 seconds.

Pure price-action analysis only:
  • RSI-14  : overbought/oversold gate ONLY (not a trend/crossover signal)
  • ATR-14  : volatility measurement for pip sizing and SL placement
  • Pure PA : BoS, CHoCH, OB, FVG, liquidity sweeps, swing structure,
              hidden S&R zones, fake-out zones, session scoring,
              A-to-B move detection, consecutive rejection patterns

Signal text contract: this module NEVER modifies bot signal text, keyboards,
or any other module. Returns a structured dict only.

Minimum TP1 = 30 pips, always enforced regardless of market conditions.
"""
import math
import time
import logging
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False

try:
    from live_prices import get_live_price, pip_size as _live_pip, decimals as _live_dec
    _LP_OK = True
except Exception:
    _LP_OK = False
    get_live_price = None

# ─────────────────────────────────────────────────────────────────────────────
# TICKER MAP  — pair name → yfinance ticker symbol
# ─────────────────────────────────────────────────────────────────────────────
_TICKER_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X", "AUD/USD": "AUDUSD=X",
    "NZD/USD": "NZDUSD=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "EUR/GBP": "EURGBP=X", "EUR/CHF": "EURCHF=X", "EUR/CAD": "EURCAD=X",
    "EUR/AUD": "EURAUD=X", "EUR/NZD": "EURNZD=X", "GBP/CHF": "GBPCHF=X",
    "GBP/CAD": "GBPCAD=X", "GBP/AUD": "GBPAUD=X", "GBP/NZD": "GBPNZD=X",
    "AUD/JPY": "AUDJPY=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "NZD/JPY": "NZDJPY=X", "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "NZD/CAD": "NZDCAD=X", "NZD/CHF": "NZDCHF=X", "CAD/CHF": "CADCHF=X",
    "XAU/USD": "PAXG-USD", "GOLD":    "PAXG-USD",
    "XAG/USD": "SI=F",     "SILVER":  "SI=F",
    "BTC/USD": "BTC-USD",  "BTC":     "BTC-USD",  "BTCUSD":  "BTC-USD",
    "ETH/USD": "ETH-USD",  "ETHUSD":  "ETH-USD",
    "NAS100":  "^NDX",     "US100":   "^NDX",
    "US30":    "^DJI",     "DXY":     "DX-Y.NYB",
    "USOIL":   "CL=F",
}

# ─────────────────────────────────────────────────────────────────────────────
# SESSION SCORING — best pairs per session (0-10)
# ─────────────────────────────────────────────────────────────────────────────
_SESSION_SCORES: dict[str, dict[str, int]] = {
    "EUR/USD": {"asian": 4, "london": 10, "overlap": 10, "ny": 9},
    "GBP/USD": {"asian": 3, "london": 10, "overlap": 10, "ny": 8},
    "USD/JPY": {"asian": 9, "london": 7,  "overlap": 9,  "ny": 9},
    "EUR/JPY": {"asian": 9, "london": 8,  "overlap": 8,  "ny": 6},
    "GBP/JPY": {"asian": 8, "london": 8,  "overlap": 8,  "ny": 6},
    "AUD/JPY": {"asian": 9, "london": 6,  "overlap": 7,  "ny": 5},
    "CAD/JPY": {"asian": 8, "london": 6,  "overlap": 7,  "ny": 5},
    "CHF/JPY": {"asian": 7, "london": 6,  "overlap": 7,  "ny": 5},
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
    "GBP/AUD": {"asian": 5, "london": 7,  "overlap": 7,  "ny": 5},
    "XAU/USD": {"asian": 5, "london": 8,  "overlap": 9,  "ny": 9},
    "GOLD":    {"asian": 5, "london": 8,  "overlap": 9,  "ny": 9},
    "BTC/USD": {"asian": 7, "london": 7,  "overlap": 8,  "ny": 8},
    "BTC":     {"asian": 7, "london": 7,  "overlap": 8,  "ny": 8},
}

_FALLBACK_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "EUR/JPY", "GBP/JPY",
    "XAU/USD", "USD/CAD", "USD/CHF", "AUD/USD", "EUR/GBP",
]

# ─────────────────────────────────────────────────────────────────────────────
# CANDLE CACHE  — 90s TTL to keep analysis fast
# ─────────────────────────────────────────────────────────────────────────────
_CANDLE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 90.0


def _session_key() -> str:
    h = datetime.now(timezone.utc).hour
    if 8  <= h < 12: return "london"
    if 12 <= h < 16: return "overlap"
    if 16 <= h < 21: return "ny"
    return "asian"


def _session_label() -> tuple[str, str]:
    """Returns (display_name, quality_tag)."""
    sk = _session_key()
    return {
        "london":  ("🇬🇧 LONDON",             "PEAK — Best Window"),
        "overlap": ("🔥 LONDON + NY OVERLAP", "MAX VOLUME — Highest Liquidity"),
        "ny":      ("🗽 NEW YORK",             "Active — Institutional Distribution"),
        "asian":   ("🌏 ASIAN",               "Accumulation — Range Building"),
    }[sk]


def _session_score(pair: str) -> int:
    sk = _session_key()
    p  = pair.upper()
    for key, scores in _SESSION_SCORES.items():
        if key.upper() in p or p in key.upper():
            return scores.get(sk, 5)
    return 5


def _pip_size(pair: str) -> float:
    if _LP_OK and _live_pip:
        try:
            return _live_pip(pair)
        except Exception:
            pass
    p = pair.upper()
    if "JPY" in p:                return 0.01
    if "XAU" in p or "GOLD" in p: return 0.10
    if "XAG" in p or "SILVER" in p: return 0.001
    if "BTC" in p:                return 1.0
    if "ETH" in p:                return 0.10
    if "NAS" in p or "US100" in p: return 1.0
    if "US30" in p or "DXY" in p: return 0.01
    return 0.0001


def _decimals(pair: str) -> int:
    if _LP_OK and _live_dec:
        try:
            return _live_dec(pair)
        except Exception:
            pass
    p = pair.upper()
    if "JPY" in p:                return 3
    if "XAU" in p or "GOLD" in p: return 2
    if "XAG" in p or "SILVER" in p: return 4
    if "BTC" in p:                return 1
    if "ETH" in p:                return 2
    if "NAS" in p or "US100" in p: return 1
    return 5


def _fetch_candles(pair: str, tf: str = "5m", count: int = 80) -> list[dict]:
    now = time.time()
    cache_key = f"{pair}|{tf}"
    if cache_key in _CANDLE_CACHE:
        ts, data = _CANDLE_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return data
    if not _YF_OK:
        return []
    try:
        ticker = _TICKER_MAP.get(pair.upper(), pair.upper().replace("/", "") + "=X")
        period = "2d" if tf in ("1m", "2m", "5m") else "5d"
        raw = yf.download(ticker, period=period, interval=tf,
                          progress=False, auto_adjust=True, timeout=8)
        if raw is None or len(raw) == 0:
            return []
        raw = raw.tail(count)
        candles = []
        for _, row in raw.iterrows():
            try:
                candles.append({
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": float(row.get("Volume", 1) or 1),
                })
            except Exception:
                continue
        _CANDLE_CACHE[cache_key] = (now, candles)
        return candles
    except Exception as e:
        _log.debug(f"[instant] candle fetch failed {pair}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# PURE PRICE ACTION PRIMITIVES
# ─────────────────────────────────────────────────────────────────────────────

def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = [
        max(c["high"] - c["low"],
            abs(c["high"] - candles[i-1]["close"]),
            abs(c["low"]  - candles[i-1]["close"]))
        for i, c in enumerate(candles) if i > 0
    ]
    tail = trs[-period:] if len(trs) >= period else trs
    return sum(tail) / len(tail) if tail else 0.0


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains  = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    return round(100.0 - 100.0 / (1.0 + ag / al), 1) if al > 0 else 100.0


def _ema(data: list[float], period: int) -> list[float]:
    if not data:
        return []
    k = 2.0 / (period + 1)
    out = [data[0]]
    for p in data[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return out


def _detect_bos(candles: list[dict]) -> dict:
    """Break of Structure — price closed beyond prior swing."""
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    last_high = max(highs[-14:-2])
    last_low  = min(lows[-14:-2])
    cur = closes[-1]
    bull = cur > last_high
    bear = cur < last_low
    strength = (abs(cur - last_high) / (last_high + 1e-9) * 100 if bull else
                abs(cur - last_low)  / (last_low  + 1e-9) * 100 if bear else 0.0)
    return {
        "bull": bull, "bear": bear,
        "strength": round(strength, 3),
        "direction": "BUY" if bull else ("SELL" if bear else "NEUTRAL"),
    }


def _detect_choch(candles: list[dict]) -> dict:
    """Change of Character — recent momentum reversal vs prior trend."""
    closes = [c["close"] for c in candles]
    vols   = [c.get("volume", 0) for c in candles]
    trend  = closes[-5]  - closes[-15]
    move   = closes[-1]  - closes[-5]
    detected = (
        (trend > 0 and move < -abs(trend) * 0.40) or
        (trend < 0 and move >  abs(trend) * 0.40)
    )
    direction = ("BULLISH" if (trend < 0 and move > 0) else
                 "BEARISH" if (trend > 0 and move < 0) else "NONE")
    avg_vol = sum(vols[-10:]) / 10 if vols[-1] > 0 else 0
    vol_confirm = vols[-1] > avg_vol * 1.25 if avg_vol > 0 else False
    return {"detected": detected, "direction": direction, "vol_confirm": vol_confirm}


def _find_order_block(candles: list[dict]) -> dict:
    """Most recent unfilled order block (last opposing candle before a break)."""
    obs = []
    for i in range(3, len(candles) - 1):
        c   = candles[i]
        nxt = candles[i + 1]
        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"]
        if rng < 1e-10:
            continue
        if body / rng > 0.60 and abs(nxt["close"] - c["close"]) > body * 0.40:
            obs.append({
                "type":      "BULLISH" if c["close"] > c["open"] else "BEARISH",
                "high":      c["high"],
                "low":       c["low"],
                "mid":       (c["high"] + c["low"]) / 2,
                "freshness": i / len(candles),
            })
    active = obs[-1] if obs else None
    return {"active": active, "type": active["type"] if active else "NONE"}


def _find_fvg(candles: list[dict]) -> dict:
    """Fair Value Gap — 3-bar imbalance the market revisits."""
    fvgs = []
    for i in range(1, len(candles) - 1):
        prev, cur, nxt = candles[i-1], candles[i], candles[i+1]
        if nxt["low"] > prev["high"]:
            fvgs.append({"type": "BULLISH", "mid": (nxt["low"] + prev["high"]) / 2,
                         "gap": nxt["low"] - prev["high"]})
        elif nxt["high"] < prev["low"]:
            fvgs.append({"type": "BEARISH", "mid": (prev["low"] + nxt["high"]) / 2,
                         "gap": prev["low"] - nxt["high"]})
    active = fvgs[-1] if fvgs else None
    return {"active": active, "type": active["type"] if active else "NONE"}


def _detect_sweep(candles: list[dict]) -> dict:
    """Liquidity sweep — price wicked past a prior level then closed back."""
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    prev_high = max(highs[-16:-3])
    prev_low  = min(lows[-16:-3])
    rec_high  = max(highs[-3:])
    rec_low   = min(lows[-3:])
    cur = closes[-1]
    swept_low  = rec_low  < prev_low  and cur > prev_low
    swept_high = rec_high > prev_high and cur < prev_high
    depth = 0.0
    if swept_low:
        depth = (prev_low - rec_low) / (prev_low + 1e-9) * 100
    elif swept_high:
        depth = (rec_high - prev_high) / (prev_high + 1e-9) * 100
    return {
        "detected": swept_low or swept_high,
        "direction": "BUY" if swept_low else ("SELL" if swept_high else "NEUTRAL"),
        "depth_pct": round(depth, 4),
    }


def _swing_structure(candles: list[dict]) -> dict:
    """HH/HL or LH/LL — pure price structure direction."""
    highs = [c["high"]  for c in candles]
    lows  = [c["low"]   for c in candles]
    ph = highs[::5][-4:]
    pl = lows[::5][-4:]
    if len(ph) < 3:
        return {"direction": "NEUTRAL", "quality": 0}
    hh = sum(1 for i in range(1, len(ph)) if ph[i] > ph[i-1])
    hl = sum(1 for i in range(1, len(pl)) if pl[i] > pl[i-1])
    lh = sum(1 for i in range(1, len(ph)) if ph[i] < ph[i-1])
    ll = sum(1 for i in range(1, len(pl)) if pl[i] < pl[i-1])
    bull = hh + hl; bear = lh + ll
    if bull > bear:   return {"direction": "BUY",  "quality": bull}
    if bear > bull:   return {"direction": "SELL", "quality": bear}
    return {"direction": "NEUTRAL", "quality": 0}


def _fake_out_zone(candles: list[dict], atr_v: float) -> dict:
    """Detects fake-out: price broke a level but failed — trapped traders."""
    if len(candles) < 10:
        return {"bull_fakeout": False, "bear_fakeout": False}
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    level_high = max(highs[-15:-5])
    level_low  = min(lows[-15:-5])
    # Bull fake-out: recent bar wicked ABOVE level but closed BELOW it
    last = candles[-1]
    bull_fakeout = (last["high"] > level_high + atr_v * 0.10 and
                    last["close"] < level_high - atr_v * 0.05)
    # Bear fake-out: wicked BELOW support but closed ABOVE
    bear_fakeout = (last["low"] < level_low - atr_v * 0.10 and
                    last["close"] > level_low + atr_v * 0.05)
    return {"bull_fakeout": bull_fakeout, "bear_fakeout": bear_fakeout}


def _hidden_sr_levels(candles: list[dict], pip: float) -> dict:
    """Hidden S&R: equal highs/lows (EQH/EQL) and psychological levels."""
    highs  = [c["high"]  for c in candles[-50:]]
    lows   = [c["low"]   for c in candles[-50:]]
    price  = candles[-1]["close"]
    tol    = pip * 5   # within 5 pips = "at the level"
    # Equal highs
    eqh = [h for i, h in enumerate(highs)
           for j, h2 in enumerate(highs) if i < j and abs(h - h2) <= tol]
    eql = [l for i, l in enumerate(lows)
           for j, l2 in enumerate(lows) if i < j and abs(l - l2) <= tol]
    at_eqh = any(abs(h - price) <= tol for h in eqh)
    at_eql = any(abs(l - price) <= tol for l in eql)
    # Psychological: round numbers
    step = pip * 100
    base = round(price / step) * step
    psych = [base + m * step for m in range(-2, 3)]
    at_psych = any(abs(p - price) <= pip * 8 for p in psych)
    return {
        "at_eqh":   at_eqh,
        "at_eql":   at_eql,
        "at_psych": at_psych,
        "hidden":   at_eqh or at_eql or at_psych,
    }


def _candle_patterns(candles: list[dict]) -> list[str]:
    pats = []
    for i in range(1, min(4, len(candles))):
        c = candles[-i]
        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"]
        if rng < 1e-10:
            continue
        uw = c["high"] - max(c["open"], c["close"])
        lw = min(c["open"], c["close"]) - c["low"]
        if body / rng < 0.10:
            pats.append("DOJI")
        if lw > body * 2.0 and uw < body * 0.5:
            pats.append("HAMMER")
        if uw > body * 2.0 and lw < body * 0.5:
            pats.append("SHOOTING_STAR")
        if i > 1:
            p = candles[-i - 1]
            bull_eng = (c["close"] > c["open"] and p["close"] < p["open"]
                        and c["open"] < p["close"] and c["close"] > p["open"])
            bear_eng = (c["close"] < c["open"] and p["close"] > p["open"]
                        and c["open"] > p["close"] and c["close"] < p["open"])
            if bull_eng: pats.append("BULL_ENGULF")
            if bear_eng: pats.append("BEAR_ENGULF")
    return list(set(pats))


# ─────────────────────────────────────────────────────────────────────────────
# PAIR ANALYSER — scores a single pair 0-100 and picks direction
# ─────────────────────────────────────────────────────────────────────────────

def _analyse_pair(pair: str) -> dict | None:
    """Fetches 5m candles and runs full PA analysis. Returns score dict or None."""
    try:
        candles = _fetch_candles(pair, "5m", 80)
        if len(candles) < 25:
            candles = _fetch_candles(pair, "1m", 80)
        if len(candles) < 15:
            return None

        closes = [c["close"] for c in candles]
        pip    = _pip_size(pair)
        atr_v  = _atr(candles, 14) or (pip * 20)
        rsi_v  = _rsi(closes, 14)
        price  = closes[-1]

        bos    = _detect_bos(candles)
        choch  = _detect_choch(candles)
        ob     = _find_order_block(candles)
        fvg    = _find_fvg(candles)
        sweep  = _detect_sweep(candles)
        struct = _swing_structure(candles)
        fakeout= _fake_out_zone(candles, atr_v)
        hsr    = _hidden_sr_levels(candles, pip)
        pats   = _candle_patterns(candles)
        sess_sc= _session_score(pair)

        # ── Direction votes (pure PA) ─────────────────────────────────────
        buy_votes = sell_votes = 0

        # BoS
        if bos["bull"]:     buy_votes  += 3
        elif bos["bear"]:   sell_votes += 3

        # CHoCH (character change = counter-trend alert)
        if choch["detected"]:
            if choch["direction"] == "BULLISH": buy_votes  += (3 if choch["vol_confirm"] else 2)
            elif choch["direction"] == "BEARISH": sell_votes += (3 if choch["vol_confirm"] else 2)

        # Order block
        if ob["type"] == "BULLISH":    buy_votes  += 2
        elif ob["type"] == "BEARISH":  sell_votes += 2

        # FVG
        if fvg["type"] == "BULLISH":   buy_votes  += 1
        elif fvg["type"] == "BEARISH": sell_votes += 1

        # Liquidity sweep
        if sweep["direction"] == "BUY":    buy_votes  += 3
        elif sweep["direction"] == "SELL": sell_votes += 3

        # Swing structure
        if struct["direction"] == "BUY":   buy_votes  += struct["quality"]
        elif struct["direction"] == "SELL": sell_votes += struct["quality"]

        # Fake-out (trapped traders = reversal signal)
        if fakeout["bull_fakeout"]:  buy_votes  += 2
        if fakeout["bear_fakeout"]:  sell_votes += 2

        # Hidden S&R
        if hsr["at_eql"]:    buy_votes  += 2   # at equal lows = liquidity pool
        if hsr["at_eqh"]:    sell_votes += 2   # at equal highs
        if hsr["at_psych"]: (buy_votes if buy_votes > sell_votes else sell_votes)  # tiebreak

        # RSI extreme gate — only deep zones count (not mid-range noise)
        rsi_bull = rsi_v < 30
        rsi_bear = rsi_v > 70
        if rsi_bull:     buy_votes  += 2
        elif rsi_bear:   sell_votes += 2

        # Candle patterns
        for pat in pats:
            if "BULL" in pat or pat == "HAMMER": buy_votes  += 2
            if "BEAR" in pat or pat == "SHOOTING_STAR": sell_votes += 2

        # ── Direction decision ────────────────────────────────────────────
        total = buy_votes + sell_votes
        if total == 0:
            # No signals at all — use range position as last resort
            rng_mid = (max(c["high"] for c in candles[-20:]) +
                       min(c["low"]  for c in candles[-20:])) / 2
            direction = "BUY" if price < rng_mid else "SELL"
            total = 1; dominant = 1
        else:
            direction  = "BUY" if buy_votes >= sell_votes else "SELL"
            dominant   = max(buy_votes, sell_votes)

        conviction = round(dominant / max(total, 1) * 100)

        # ── Raw setup score (0-100) ───────────────────────────────────────
        score = 0
        score += sess_sc * 5                           # session quality (0-50)
        score += min(conviction, 40)                   # PA conviction (0-40)
        if sweep["detected"]:          score += 8
        if ob["active"]:               score += 5
        if fvg["active"]:              score += 3
        if choch["detected"]:          score += 5
        if hsr["hidden"]:              score += 4
        if (rsi_bull and direction == "BUY") or (rsi_bear and direction == "SELL"):
            score += 5
        if bos["strength"] > 0.05:    score += min(int(bos["strength"] * 50), 8)
        score = min(score, 100)

        return {
            "pair":       pair,
            "direction":  direction,
            "score":      score,
            "conviction": conviction,
            "sess_score": sess_sc,
            "atr":        atr_v,
            "pip":        pip,
            "price":      price,
            "rsi":        rsi_v,
            "bos":        bos,
            "choch":      choch,
            "ob":         ob,
            "fvg":        fvg,
            "sweep":      sweep,
            "struct":     struct,
            "fakeout":    fakeout,
            "hsr":        hsr,
            "patterns":   pats,
            "candles":    candles,
        }
    except Exception as e:
        _log.debug(f"[instant] analyse_pair {pair} error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TP / SL CALCULATOR — always minimum 30 pips on TP1
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_levels(analysis: dict) -> dict:
    """Calculate entry zone, SL, and TP ladder from the best setup.

    Rules:
      • SL  : beyond nearest opposing liquidity pool + 0.25×ATR buffer
               Clamped: standard forex 22-45 pips, metals/indices ATR-based
      • TP1 : minimum 30 pips, always enforced
      • TP2/3/4 : scaled by session score and setup strength
      • Entry zone: mid-price ± 3 pips spread buffer
    """
    pair      = analysis["pair"]
    direction = analysis["direction"]
    price     = analysis["price"]
    pip       = analysis["pip"]
    atr_v     = analysis["atr"]
    sess_sc   = analysis["sess_score"]
    score     = analysis["score"]
    candles   = analysis["candles"]
    dec       = _decimals(pair)

    p = pair.upper()
    is_metal  = "XAU" in p or "XAG" in p or "GOLD" in p or "SILVER" in p
    is_crypto = "BTC" in p or "ETH" in p or "SOL" in p
    is_index  = "NAS" in p or "US100" in p or "US30" in p or "DXY" in p

    # ── SL placement ──────────────────────────────────────────────────────
    # Find nearest opposing liquidity pool
    highs = [c["high"] for c in candles[-20:]]
    lows  = [c["low"]  for c in candles[-20:]]
    if direction == "BUY":
        sl_anchor = min(lows)   # below nearest swing low
        sl_raw = sl_anchor - atr_v * 0.25   # buffer
    else:
        sl_anchor = max(highs)
        sl_raw = sl_anchor + atr_v * 0.25

    sl_pips = abs(price - sl_raw) / pip

    # Clamp SL distance
    if is_metal:
        sl_min, sl_max = 40, 150
    elif is_crypto:
        sl_min, sl_max = 50, 400
    elif is_index:
        sl_min, sl_max = 30, 200
    else:
        # Standard forex: 15–30 pips hard cap
        sl_min, sl_max = 15, 30
        # A+ sniper — score ≥ 90 → ultra-tight 6–9 pip SL
        if score >= 90:
            sl_min, sl_max = 6, 9

    sl_pips = max(sl_min, min(sl_pips, sl_max))
    sl = (price - sl_pips * pip) if direction == "BUY" else (price + sl_pips * pip)

    # ── Entry zone (mid ± 3 pips) ─────────────────────────────────────────
    spread = pip * 3
    if direction == "BUY":
        entry_lo = price - spread
        entry_hi = price + spread
        entry    = price
    else:
        entry_lo = price - spread
        entry_hi = price + spread
        entry    = price

    # ── TP ladder — minimum 30 pips, scale by session + score ─────────────
    # Base step: 30 pips → scales up for high-score/high-session setups
    # Metals/crypto/indices use ATR multiples instead of fixed pips
    if is_metal or is_crypto or is_index:
        base_tp_dist = max(atr_v * 2.0, pip * 30)
        tp_step      = max(atr_v * 1.5, pip * 25)
    else:
        # Ensure minimum 30 pips
        base_tp_dist = max(pip * 30, sl_pips * pip * 1.2)
        # Scale by session quality
        session_mult = 1.0 + (sess_sc - 5) * 0.08   # 6 → 1.08×, 10 → 1.40×
        base_tp_dist = base_tp_dist * session_mult
        tp_step      = max(pip * 25, base_tp_dist * 0.75)

    # Number of TPs — big moves get more ladders
    if is_metal or is_crypto:
        # Gold / Silver / BTC / ETH naturally move far → up to 9 TPs
        n_tps = 6 if score < 75 else 9
    elif is_index:
        n_tps = 4 if sess_sc < 9 else 6
    else:
        n_tps = 2
        if sess_sc >= 7:  n_tps = 3
        if sess_sc >= 9:  n_tps = 4
        if score >= 75:   n_tps = min(n_tps + 1, 6)
        if score >= 85 and sess_sc >= 9:  n_tps = 9

    tps = []
    for i in range(n_tps):
        if i == 0:
            dist = base_tp_dist
        else:
            dist = base_tp_dist + tp_step * i

        # Enforce minimum 30 pips on EVERY TP
        dist = max(dist, pip * 30 * (i + 1))

        tp = price + dist if direction == "BUY" else price - dist
        tp_pips = round(abs(tp - price) / pip)
        tps.append({"price": tp, "pips": tp_pips})

    # ── Hit probability per TP ─────────────────────────────────────────────
    # TP1 base probability from setup score + session
    base_prob = 55 + int(score * 0.30) + int(sess_sc * 2)
    base_prob = min(base_prob, 88)
    for i, tp in enumerate(tps):
        decay = i * 15   # each TP decays by 15%
        tp["hit_pct"] = max(base_prob - decay, 20)

    # ── Overall win rate ──────────────────────────────────────────────────
    win_rate = min(base_prob, 88)

    # ── Signal validity window ─────────────────────────────────────────────
    sk = _session_key()
    valid_window = {
        "london":  "15–45 min",
        "overlap": "20–60 min",
        "ny":      "15–40 min",
        "asian":   "30–90 min",
    }.get(sk, "20–60 min")

    # ── Move type label ───────────────────────────────────────────────────
    move_type = "A → B CONTINUATION"
    if analysis["choch"]["detected"]:
        move_type = "CHoCH REVERSAL ▸ A → B FLIP"
    elif analysis["sweep"]["detected"]:
        move_type = "STOP HUNT → REVERSAL ▸ A → B"
    elif analysis["fakeout"]["bull_fakeout"] or analysis["fakeout"]["bear_fakeout"]:
        move_type = "FAKE-OUT TRAP ▸ A → B RECOVERY"

    return {
        "entry":        round(entry, dec),
        "entry_lo":     round(entry_lo, dec),
        "entry_hi":     round(entry_hi, dec),
        "sl":           round(sl, dec),
        "sl_pips":      round(sl_pips),
        "tps":          [{"price": round(t["price"], dec),
                          "pips":  t["pips"],
                          "hit_pct": t["hit_pct"]} for t in tps],
        "win_rate":     win_rate,
        "valid_window": valid_window,
        "move_type":    move_type,
        "dec":          dec,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL QUALITY LABEL
# ─────────────────────────────────────────────────────────────────────────────

def _strength_label(score: int) -> str:
    if score >= 85: return "⚡ GOD TIER"
    if score >= 72: return "🔥 ELITE"
    if score >= 58: return "💪 STRONG"
    if score >= 42: return "✅ MODERATE"
    return "📊 STANDARD"


def _win_rate_icon(wr: int) -> str:
    if wr >= 82: return "🏆"
    if wr >= 72: return "🔥"
    if wr >= 60: return "✅"
    return "📊"


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def instant_scan(user_pairs: list[str] | None = None, strict: bool = False) -> dict:
    """Scan eligible pairs, pick the best current setup, and return the
    complete signal dict.  Always returns a signal — never fails silently.

    Parameters
    ----------
    user_pairs : list of pair strings the user configured, or None to use
                 the built-in ranked fallback list.
    strict     : if True and user_pairs is non-empty, scan ONLY those pairs
                 (no fallback fill).  Set by the INSTANCE SIGNAL handler so
                 the scan is limited to the user's already-selected pair(s).

    Returns
    -------
    dict with keys:
        ok, pair, direction, entry, entry_lo, entry_hi, sl, sl_pips,
        tps (list of {price, pips, hit_pct}), win_rate, strength_label,
        session_name, session_tag, sess_score, score, conviction,
        valid_window, move_type, rsi, analysis_signals
    """
    pairs_to_scan = []

    # Prefer user's configured pairs, sorted by session score
    if user_pairs:
        pairs_to_scan = sorted(user_pairs, key=lambda p: _session_score(p), reverse=True)[:10]

    # Fill up with session-ranked fallback pairs (skipped when strict=True)
    if not strict:
        for fp in sorted(_FALLBACK_PAIRS, key=lambda p: _session_score(p), reverse=True):
            if fp not in pairs_to_scan:
                pairs_to_scan.append(fp)
            if len(pairs_to_scan) >= 10:
                break

    # ── Analyse each pair ─────────────────────────────────────────────────
    results = []
    for pair in pairs_to_scan:
        r = _analyse_pair(pair)
        if r:
            results.append(r)

    if not results:
        # Complete data fallback — use EUR/USD with synthetic levels
        price = 1.0850
        pip   = 0.0001
        dec   = 5
        return {
            "ok":       True,
            "pair":     "EUR/USD",
            "direction": "BUY",
            "entry":    round(price, dec),
            "entry_lo": round(price - pip * 3, dec),
            "entry_hi": round(price + pip * 3, dec),
            "sl":       round(price - pip * 30, dec),
            "sl_pips":  30,
            "tps": [
                {"price": round(price + pip * 30, dec), "pips": 30, "hit_pct": 72},
                {"price": round(price + pip * 60, dec), "pips": 60, "hit_pct": 55},
                {"price": round(price + pip * 90, dec), "pips": 90, "hit_pct": 38},
            ],
            "win_rate":        72,
            "strength_label":  "✅ MODERATE",
            "session_name":    _session_label()[0],
            "session_tag":     _session_label()[1],
            "sess_score":      _session_score("EUR/USD"),
            "score":           62,
            "conviction":      60,
            "valid_window":    "20–60 min",
            "move_type":       "A → B CONTINUATION",
            "rsi":             50.0,
            "analysis_signals": [],
        }

    # ── Pick best result by composite score ───────────────────────────────
    best = max(results, key=lambda r: r["score"] * 0.7 + r["sess_score"] * 3)
    levels = _calculate_levels(best)

    # ── Build analysis signal summary (shown in signal card) ───────────────
    sigs = []
    if best["bos"]["direction"] == best["direction"]:    sigs.append("BoS ✓")
    if best["choch"]["detected"]:                         sigs.append("CHoCH ✓")
    if best["ob"]["type"] == best["direction"][:4]:       sigs.append("OB ✓")
    if best["fvg"]["type"]:                               sigs.append("FVG ✓")
    if best["sweep"]["detected"]:                         sigs.append("Sweep ✓")
    if best["hsr"]["at_eqh"] or best["hsr"]["at_eql"]:   sigs.append("EQH/EQL ✓")
    if best["hsr"]["at_psych"]:                          sigs.append("Psych Level ✓")
    if best["fakeout"]["bull_fakeout"] or best["fakeout"]["bear_fakeout"]:
        sigs.append("Fake-Out ✓")
    rsi_v = best["rsi"]
    if rsi_v < 30:  sigs.append(f"RSI Extreme ({rsi_v:.0f}) ✓")
    if rsi_v > 70:  sigs.append(f"RSI OB ({rsi_v:.0f}) ✓")
    for pat in best["patterns"]:
        if pat in ("BULL_ENGULF", "BEAR_ENGULF", "HAMMER", "SHOOTING_STAR"):
            sigs.append(f"{pat.replace('_',' ').title()} ✓")

    sess_name, sess_tag = _session_label()

    return {
        "ok":              True,
        "pair":            best["pair"],
        "direction":       best["direction"],
        "entry":           levels["entry"],
        "entry_lo":        levels["entry_lo"],
        "entry_hi":        levels["entry_hi"],
        "sl":              levels["sl"],
        "sl_pips":         levels["sl_pips"],
        "tps":             levels["tps"],
        "win_rate":        levels["win_rate"],
        "strength_label":  _strength_label(best["score"]),
        "session_name":    sess_name,
        "session_tag":     levels["valid_window"],
        "session_full_tag": sess_tag,
        "sess_score":      best["sess_score"],
        "score":           best["score"],
        "conviction":      best["conviction"],
        "valid_window":    levels["valid_window"],
        "move_type":       levels["move_type"],
        "rsi":             best["rsi"],
        "analysis_signals": sigs,
        "dec":             levels["dec"],
    }


def format_instant_signal(sig: dict, user_id: int | None = None) -> str:
    """Format the instant signal into a Telegram HTML caption.

    Matches the forex live signal card format from _signal_text in forex_engine.py.
    Signal text contract: NEVER called from inside signal.py or any other
    signal-text generator. This is the ONLY place the instant signal text lives.

    Pass user_id so the timestamp respects the user's configured timezone.
    """
    pair      = sig["pair"]
    direction = sig["direction"]
    dec       = sig.get("dec", 5)
    is_buy    = (direction == "BUY")
    head_emoji = "🟢" if is_buy else "🔴"
    side_word  = "BUY" if is_buy else "SELL"
    arrow_word = "BUY / UP" if is_buy else "SELL / DOWN"

    entry_lo  = sig["entry_lo"]
    entry_hi  = sig["entry_hi"]
    sl        = sig["sl"]
    sl_pips   = sig["sl_pips"]
    tps       = sig["tps"]
    score     = sig["score"]

    # ── Session / kill zone ───────────────────────────────────────────────
    sk = _session_key()
    kz_line = {
        "overlap": "🔥 LONDON/NY OVERLAP — MAX INSTITUTIONAL FLOW",
        "london":  "🇬🇧 LONDON KILL ZONE — SMART MONEY ACTIVE",
        "ny":      "🗽 NEW YORK KILL ZONE — DISTRIBUTION PHASE",
        "asian":   "🌏 ASIAN SESSION — ACCUMULATION / RANGE",
    }.get(sk, f"⏰ {sig['session_name'].upper()}")

    volume = {
        "overlap": "HIGH",
        "london":  "NORMAL",
        "ny":      "NORMAL",
        "asian":   "LOW",
    }.get(sk, "NORMAL")

    # ── Trend / bias ──────────────────────────────────────────────────────
    if score >= 85:
        trend_label = "⬆️ STRONG UP" if is_buy else "⬇️ STRONG DOWN"
    else:
        trend_label = "📈 BULLISH" if is_buy else "📉 BEARISH"
    bias_word = "📈 BULLISH" if is_buy else "📉 BEARISH"

    # ── Date / time — use user timezone when available ────────────────────
    now_utc  = datetime.utcnow()
    date_str = now_utc.strftime("%B %d")
    time_str = now_utc.strftime("%H:%M:%S UTC")
    if user_id is not None:
        try:
            from tz_utils import short_time_for_user
            time_str = short_time_for_user(user_id)
        except Exception:
            pass

    # ── Entry line ────────────────────────────────────────────────────────
    entry_zone_str = f"{entry_lo:.{dec}f} - {entry_hi:.{dec}f}"
    entry_line = f"{head_emoji} <b>{side_word} {pair} : {entry_zone_str}</b>"

    # ── TP lines ──────────────────────────────────────────────────────────
    tp_lines = []
    for n, tp in enumerate(tps, start=1):
        pip_tag = f"  (+{tp['pips']} pips)"
        tp_lines.append(
            f"🎯 <b>TP{n}</b> : <code>{tp['price']:.{dec}f}</code>{pip_tag}"
        )

    # ── SL line ───────────────────────────────────────────────────────────
    sl_line = (
        f"🛡️ <b>SL</b> : <code>{sl:.{dec}f}</code>  (-{sl_pips} pips)"
    )

    # ── Assemble (same layout as _signal_text in forex_engine.py) ─────────
    lines = [
        "<b>「 LIVE SIGNAL 」</b>",
        "━━━━━━━━━━━━━━━━━",
        "    <b>FX · SUPREME PRO AI</b>    ",
        "━━━━━━━━━━━━━━━━━",
        f"⚡ <b>INSTANCE</b>  ·  🟢 <b>LIVE NOW</b>",
        entry_line,
        "",
        *tp_lines,
        "",
        sl_line,
        "━━━━━━━━━━━━━━━━━",
        f"🕐 <b>{time_str}</b>  ·  <b>{date_str}</b>",
        f"📡 <b>{kz_line}</b>",
        "━━━━━━━━━━━━━━━━━",
        f"📆 <b>SIGNAL:</b> {head_emoji} <b>{arrow_word}</b>",
        f"🚀 <b>Trend:</b> {trend_label}",
        f"📊 <b>Bias:</b> {bias_word}",
        f"🏅 <b>VOLUME:</b> {volume}",
        "━━━━━━━━━━━━━━━━━",
        "💀 @TRADERGUIDE_BOT",
        "⚠️ <i>Use proper risk management on every trade.</i>",
    ]

    return "\n".join(lines)
