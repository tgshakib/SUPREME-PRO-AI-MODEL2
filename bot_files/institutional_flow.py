"""INSTITUTIONAL ORDER FLOW ENGINE — SUPREME PRO AI
=====================================================
Sierra Chart / ATAS / NinjaTrader style bid×ask volume analysis.

Calculates PER-LEVEL bid/ask imbalance, footprint delta, volume clusters,
absorption zones, delta divergence, and liquidity hunts to detect where
BIG PLAYERS are positioned — then rides WITH them for maximum accuracy.

Public API
----------
  analyze(pair, is_otc=False) → dict
    big_player_direction: "BUY" | "SELL" | "NEUTRAL"
    confidence:           float 0-1
    trap_detected:        bool   (stop hunt / liquidity sweep active)
    trap_direction:       "BUY" | "SELL" | None  (enter this way after trap)
    absorption:           bool   (large wall absorbing aggressor flow)
    delta_divergence:     bool   (price ≠ delta direction → reversal warning)
    entry_quality:        "ELITE" | "GOOD" | "WEAK"
    cluster_levels:       list   (key institutional price levels)
    delta:                float  (last-candle net delta)
    cum_delta:            float  (10-candle cumulative delta)
    volume_expansion:     bool   (volume > 1.5× average = institutional surge)
    reason:               str

  get_orderflow_vote(pair, is_otc) → int  (+1 BUY, -1 SELL, 0 NEUTRAL)
    Compact single-vote for wiring into consensus engines.

  get_summary(pair) → str
    Human-readable Telegram-formatted analysis card.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 4.0   # seconds — fast refresh for sniper timing

# ── Import optional dependencies ───────────────────────────────────────────

try:
    from footprint_engine import get_footprint
    _FP_OK = True
except Exception:
    get_footprint = None  # type: ignore
    _FP_OK = False

try:
    from orderbook_engine import get_orderbook_signal
    _OB_OK = True
except Exception:
    get_orderbook_signal = None  # type: ignore
    _OB_OK = False

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    yf = None
    pd = None
    _YF_OK = False

try:
    from live_prices import yf_ticker
    _LP_OK = True
except Exception:
    yf_ticker = None  # type: ignore
    _LP_OK = False


# ── Forex volume-based institutional detection ─────────────────────────────

def _fetch_ohlcv(ticker: str, interval: str = "5m", period: str = "2d"):
    if not _YF_OK or not ticker:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 20:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        if "volume" not in df.columns:
            return None
        return df.tail(100)
    except Exception as exc:
        logger.debug(f"[inst_flow] ohlcv fetch error {ticker}: {exc}")
        return None


def _forex_volume_analysis(pair: str) -> dict:
    """Estimate institutional flow for forex/metal pairs using yfinance OHLCV.

    Since forex has no centralised exchange we approximate:
    - Volume × direction = net buy/sell pressure proxy
    - High-volume candle in direction = institutional participation
    - Pin bar + high volume = absorption (institution defending a level)
    - ATR expansion + volume = institutional surge (big player entering)
    - Consecutive high-vol directional candles = momentum with institution
    """
    ticker = yf_ticker(pair) if yf_ticker else None
    if not ticker:
        return {"ok": False}

    df5  = _fetch_ohlcv(ticker, "5m", "2d")
    df15 = _fetch_ohlcv(ticker, "15m", "7d")
    if df5 is None:
        return {"ok": False}

    try:
        close5  = df5["close"].squeeze().astype(float)
        open5   = df5["open"].squeeze().astype(float)
        high5   = df5["high"].squeeze().astype(float)
        low5    = df5["low"].squeeze().astype(float)
        vol5    = df5["volume"].squeeze().astype(float)

        avg_vol = float(vol5.iloc[-20:].mean())
        if avg_vol <= 0:
            return {"ok": False}

        # ── Signed volume proxy (up-candle = buy pressure, down = sell)
        signed_vol = []
        for i in range(-15, 0):
            c = float(close5.iloc[i])
            o = float(open5.iloc[i])
            v = float(vol5.iloc[i])
            direction = 1 if c >= o else -1
            signed_vol.append(direction * v)

        # 5-bar and 15-bar cumulative delta proxies
        delta5  = sum(signed_vol[-5:])
        delta15 = sum(signed_vol[-15:])

        # Volume expansion
        last_vol   = float(vol5.iloc[-1])
        vol_ratio  = last_vol / max(avg_vol, 1e-10)
        vol_expand = vol_ratio >= 1.5

        # ── Per-level volume cluster approximation
        # Cluster = candle whose volume > 2× average AND wide body
        cluster_levels = []
        for i in range(-10, 0):
            try:
                h = float(high5.iloc[i])
                lo = float(low5.iloc[i])
                c = float(close5.iloc[i])
                o = float(open5.iloc[i])
                v = float(vol5.iloc[i])
                body = abs(c - o)
                rng  = max(h - lo, 1e-10)
                if v > avg_vol * 2.0 and body / rng > 0.5:
                    cluster_levels.append({
                        "price":    round((h + lo) / 2, 6),
                        "vol_ratio": round(v / avg_vol, 1),
                        "direction": "BUY" if c > o else "SELL",
                    })
            except Exception:
                continue

        # ── Absorption detection
        # Large-volume candle that closes as pin bar (upper/lower wick > 60% range)
        # = institutional defence — price tried to move but was absorbed
        absorption = False
        try:
            last_h  = float(high5.iloc[-1])
            last_lo = float(low5.iloc[-1])
            last_c  = float(close5.iloc[-1])
            last_o  = float(open5.iloc[-1])
            last_rng = max(last_h - last_lo, 1e-10)
            upper_wick = last_h - max(last_c, last_o)
            lower_wick = min(last_c, last_o) - last_lo
            if last_vol > avg_vol * 1.8:
                if lower_wick / last_rng > 0.55:   # bullish absorption pin
                    absorption = True
                elif upper_wick / last_rng > 0.55:  # bearish absorption pin
                    absorption = True
        except Exception:
            pass

        # ── Delta divergence proxy
        # Price making new high (last close > prev 5 highs) but delta15 < 0
        # = price going up on net selling = distribution
        delta_div = False
        try:
            recent_highs = [float(high5.iloc[i]) for i in range(-6, -1)]
            last_close   = float(close5.iloc[-1])
            price_new_high = last_close > max(recent_highs)
            price_new_low  = last_close < min([float(low5.iloc[i]) for i in range(-6, -1)])
            if price_new_high and delta15 < 0:
                delta_div = True
            elif price_new_low and delta15 > 0:
                delta_div = True
        except Exception:
            pass

        # ── Liquidity hunt / stop-hunt detection
        # Price briefly sweeps above recent high (or below low) then reverses
        trap_detected = False
        trap_direction = None
        try:
            # Check last 3 bars vs the 20-bar range
            lookback_high = float(high5.iloc[-22:-3].max())
            lookback_low  = float(low5.iloc[-22:-3].min())
            sweep_high = float(high5.iloc[-3:-1].max())
            sweep_low  = float(low5.iloc[-3:-1].min())
            close_now  = float(close5.iloc[-1])
            open_now   = float(open5.iloc[-1])
            atr5 = float((high5 - low5).rolling(14).mean().iloc[-1])

            if sweep_high > lookback_high + atr5 * 0.1 and close_now < lookback_high:
                trap_detected  = True
                trap_direction = "SELL"  # sweep above highs → short after rejection
            elif sweep_low < lookback_low - atr5 * 0.1 and close_now > lookback_low:
                trap_detected  = True
                trap_direction = "BUY"   # sweep below lows → long after recovery
        except Exception:
            pass

        # ── ATR expansion = institutional surge
        try:
            h5  = high5.astype(float)
            l5  = low5.astype(float)
            atr_series = (h5 - l5).rolling(14).mean()
            last_rng14 = float(h5.iloc[-1]) - float(l5.iloc[-1])
            avg_atr14  = float(atr_series.iloc[-2]) if len(atr_series) > 2 else 0.0
            atr_expand = avg_atr14 > 0 and last_rng14 >= 1.4 * avg_atr14
        except Exception:
            atr_expand = False

        # ── Multi-TF volume context (15M)
        vol15_bias = 0  # +1 = buy pressure on 15M
        if df15 is not None and "volume" in df15.columns:
            try:
                c15  = df15["close"].squeeze().astype(float)
                o15  = df15["open"].squeeze().astype(float)
                v15  = df15["volume"].squeeze().astype(float)
                avg15 = float(v15.iloc[-20:].mean())
                sv15  = sum(
                    (1 if float(c15.iloc[i]) >= float(o15.iloc[i]) else -1)
                    * float(v15.iloc[i])
                    for i in range(-8, 0)
                )
                if sv15 > avg15 * 2:
                    vol15_bias = 1
                elif sv15 < -avg15 * 2:
                    vol15_bias = -1
            except Exception:
                pass

        # ── Final direction vote
        vote = 0
        if delta15 > avg_vol * 3:
            vote = 1
        elif delta15 < -avg_vol * 3:
            vote = -1
        elif delta5 > avg_vol * 1.5 and vol15_bias >= 0:
            vote = 1
        elif delta5 < -avg_vol * 1.5 and vol15_bias <= 0:
            vote = -1

        # Trap overrides the raw vote
        if trap_detected and trap_direction:
            vote = 1 if trap_direction == "BUY" else -1

        # Delta divergence = warn against current price direction (reversal)
        if delta_div and vote != 0:
            vote = -vote  # flip — divergence = price going against real flow

        confidence = min(1.0, abs(delta15) / max(avg_vol * 5, 1e-10))
        confidence = round(confidence, 3)

        direction_map = {1: "BUY", -1: "SELL", 0: "NEUTRAL"}
        big_player_dir = direction_map[vote]

        # Entry quality
        quality_pts = 0
        if trap_detected:              quality_pts += 3
        if absorption:                 quality_pts += 2
        if atr_expand and vol_expand:  quality_pts += 2
        if vol15_bias == vote:         quality_pts += 1
        if cluster_levels:             quality_pts += 1
        if quality_pts >= 5:
            entry_quality = "ELITE"
        elif quality_pts >= 3:
            entry_quality = "GOOD"
        else:
            entry_quality = "WEAK"

        return {
            "ok":                 True,
            "big_player_direction": big_player_dir,
            "confidence":         confidence,
            "trap_detected":      trap_detected,
            "trap_direction":     trap_direction,
            "absorption":         absorption,
            "delta_divergence":   delta_div,
            "entry_quality":      entry_quality,
            "cluster_levels":     cluster_levels[:6],
            "delta":              round(delta5, 2),
            "cum_delta":          round(delta15, 2),
            "volume_expansion":   vol_expand or atr_expand,
            "vol_ratio":          round(vol_ratio, 2),
            "vote":               vote,
        }
    except Exception as exc:
        logger.debug(f"[inst_flow] forex analysis error {pair}: {exc}")
        return {"ok": False}


def _crypto_flow_analysis(pair: str) -> dict:
    """Institutional flow for crypto pairs using Binance aggTrade + order book.

    Combines:
    - Footprint engine: real-time bid/ask volume, delta, POC
    - Order book engine: L2 depth bid/ask imbalance, clusters
    - Delta divergence: price vs cum_delta disagreement
    - Absorption: large order book cluster being hit hard
    """
    fp  = get_footprint(pair)  if get_footprint  else None
    ob  = get_orderbook_signal(pair) if get_orderbook_signal else None

    if fp is None and ob is None:
        return {"ok": False}

    # ── Footprint analysis (aggTrade based)
    delta      = float(fp.get("delta",     0)) if fp else 0.0
    cum_delta  = float(fp.get("cum_delta", 0)) if fp else 0.0
    buy_vol    = float(fp.get("buy_vol",   0)) if fp else 0.0
    sell_vol   = float(fp.get("sell_vol",  0)) if fp else 0.0
    poc        = float(fp.get("poc",       0)) if fp else 0.0
    fp_bias    = (fp.get("bias", "NEUTRAL")) if fp else "NEUTRAL"

    # ── Order book analysis (L2 depth)
    ob_dir     = (ob.get("direction",  "NEUTRAL")) if ob else "NEUTRAL"
    ob_imbal   = float(ob.get("imbalance", 0.0))   if ob else 0.0
    ob_conf    = float(ob.get("confidence", 0.0))  if ob else 0.0
    ob_delta   = float(ob.get("delta", 0.0))       if ob else 0.0
    ob_clusters = (ob.get("clusters", []))          if ob else []

    # ── Delta divergence: compare price direction to delta direction
    # fp_bias = direction price is moving, delta direction = buyer/seller aggression
    # If they disagree = institutions distributing/accumulating against price
    delta_div = False
    if fp and ob:
        price_up = fp_bias == "BUY"
        delta_up = cum_delta > 0
        if price_up and not delta_up and cum_delta < -buy_vol * 0.3:
            delta_div = True   # price up but sellers dominating = distribution
        elif not price_up and delta_up and cum_delta > sell_vol * 0.3:
            delta_div = True   # price down but buyers dominating = accumulation

    # ── Absorption: large cluster in order book being absorbed
    # When a large bid/ask wall exists AND the footprint shows heavy opposite aggression
    # that ISN'T moving the price through it = institution absorbing the market
    absorption = False
    if ob_clusters and fp:
        for cl in ob_clusters[:3]:
            cl_imbal = float(cl.get("imbalance", 0))
            # Large buy wall (imbalance > 0.5) but delta is negative = sellers hitting it
            if cl_imbal > 0.5 and delta < 0 and abs(delta) > buy_vol * 0.2:
                absorption = True; break
            # Large sell wall but delta positive = buyers absorbing
            elif cl_imbal < -0.5 and delta > 0 and delta > sell_vol * 0.2:
                absorption = True; break

    # ── Iceberg detection (from order book clusters)
    # Multiple clusters refreshing = iceberg orders = institution
    iceberg = len(ob_clusters) >= 3

    # ── Volume expansion
    total_vol = buy_vol + sell_vol
    vol_expand = total_vol > 0 and abs(delta) / total_vol > 0.35  # delta > 35% of volume

    # ── Trap detection: high buy_vol but price stays flat = exhaustion trap
    trap_detected  = False
    trap_direction = None
    if fp and total_vol > 0:
        buy_pct = buy_vol / total_vol
        sell_pct = sell_vol / total_vol
        # Overwhelming buy volume but flat/down close = bull trap
        if buy_pct > 0.70 and delta < 0:
            trap_detected  = True
            trap_direction = "SELL"
        elif sell_pct > 0.70 and delta > 0:
            trap_detected  = True
            trap_direction = "BUY"

    # ── Combined vote
    votes = []
    if fp_bias == "BUY":   votes.append(1)
    elif fp_bias == "SELL": votes.append(-1)

    if ob_dir == "BUY":    votes.append(1)
    elif ob_dir == "SELL":  votes.append(-1)

    if cum_delta > 0:      votes.append(1)
    elif cum_delta < 0:     votes.append(-1)

    if ob_imbal > 0.2:     votes.append(1)
    elif ob_imbal < -0.2:   votes.append(-1)

    net = sum(votes)
    if delta_div:
        net = -net   # flip on divergence — real flow is against price
    if trap_detected and trap_direction:
        net = 1 if trap_direction == "BUY" else -1

    vote = 1 if net > 0 else (-1 if net < 0 else 0)

    # Confidence
    ob_c = ob_conf if ob else 0.0
    fp_c = min(1.0, abs(cum_delta) / max(total_vol * 2, 1e-10)) if total_vol > 0 else 0.0
    confidence = round((ob_c + fp_c) / 2, 3)

    dir_map = {1: "BUY", -1: "SELL", 0: "NEUTRAL"}
    big_player_dir = dir_map.get(vote, "NEUTRAL")

    # Entry quality
    quality_pts = 0
    if trap_detected:    quality_pts += 3
    if absorption:       quality_pts += 3
    if iceberg:          quality_pts += 2
    if delta_div:        quality_pts += 2
    if vol_expand:       quality_pts += 1
    if ob_conf > 0.7:    quality_pts += 1
    if len(votes) >= 3 and abs(net) >= 3:  quality_pts += 1

    if quality_pts >= 6:
        entry_quality = "ELITE"
    elif quality_pts >= 3:
        entry_quality = "GOOD"
    else:
        entry_quality = "WEAK"

    cluster_levels = [
        {"price": c["price"], "bid_vol": c.get("bid_vol", 0), "ask_vol": c.get("ask_vol", 0),
         "imbalance": c.get("imbalance", 0), "direction": "BUY" if c.get("imbalance", 0) > 0 else "SELL"}
        for c in ob_clusters[:6]
    ]

    reason_parts = []
    if trap_detected:   reason_parts.append(f"🪤 Stop hunt → enter {trap_direction}")
    if absorption:      reason_parts.append("🧱 Institutional absorption detected")
    if iceberg:         reason_parts.append(f"🧊 Iceberg orders at {len(ob_clusters)} levels")
    if delta_div:       reason_parts.append("⚠️ Delta divergence — price ≠ flow")
    if not reason_parts:
        reason_parts.append(f"📊 Flow: bid={buy_vol:.0f} ask={sell_vol:.0f} Δ={delta:+.0f}")

    return {
        "ok":                   True,
        "big_player_direction": big_player_dir,
        "confidence":           confidence,
        "trap_detected":        trap_detected,
        "trap_direction":       trap_direction,
        "absorption":           absorption,
        "delta_divergence":     delta_div,
        "entry_quality":        entry_quality,
        "cluster_levels":       cluster_levels,
        "delta":                round(delta, 4),
        "cum_delta":            round(cum_delta, 4),
        "volume_expansion":     vol_expand,
        "poc":                  poc,
        "ob_imbalance":         round(ob_imbal, 4),
        "vote":                 vote,
        "reason":               " | ".join(reason_parts),
    }


# ── Public API ─────────────────────────────────────────────────────────────

_CRYPTO_KEYS = {
    "btc", "eth", "sol", "bnb", "xrp", "ada", "avax", "dot", "link",
    "ltc", "bch", "etc", "matic", "doge", "bitcoin", "ethereum",
}


def _is_crypto(pair: str) -> bool:
    clean = pair.lower().replace("/", "").replace("-", "").replace(" ", "")
    return any(k in clean for k in _CRYPTO_KEYS)


def analyze(pair: str, is_otc: bool = False) -> dict:
    """Full institutional flow analysis for any pair.

    Returns a unified dict regardless of pair type (crypto vs forex).
    Cached for _TTL seconds.
    """
    now = time.time()
    cache_key = f"{pair}|{'otc' if is_otc else 'live'}"
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    if _is_crypto(pair) and not is_otc:
        result = _crypto_flow_analysis(pair)
    else:
        result = _forex_volume_analysis(pair)

    # Fill in required fields if analysis failed
    if not result.get("ok"):
        result = {
            "ok":                   False,
            "big_player_direction": "NEUTRAL",
            "confidence":           0.0,
            "trap_detected":        False,
            "trap_direction":       None,
            "absorption":           False,
            "delta_divergence":     False,
            "entry_quality":        "WEAK",
            "cluster_levels":       [],
            "delta":                0.0,
            "cum_delta":            0.0,
            "volume_expansion":     False,
            "vote":                 0,
            "reason":               "No order flow data available",
        }

    # Build reason string if missing
    if "reason" not in result or not result["reason"]:
        parts = []
        if result["trap_detected"]:
            parts.append(f"🪤 Liquidity hunt → {result['trap_direction']}")
        if result["absorption"]:
            parts.append("🧱 Absorption at level")
        if result["delta_divergence"]:
            parts.append("⚠️ Delta divergence")
        if result["volume_expansion"]:
            parts.append("💥 Institutional volume surge")
        if not parts:
            parts.append(f"Flow: {result['big_player_direction']}")
        result["reason"] = " | ".join(parts)

    _CACHE[cache_key] = (now, result)
    return result


def get_orderflow_vote(pair: str, is_otc: bool = False) -> int:
    """Single vote: +1 BUY, -1 SELL, 0 NEUTRAL.
    Use this to wire into existing consensus vote arrays."""
    try:
        r = analyze(pair, is_otc)
        if not r.get("ok"):
            return 0
        return int(r.get("vote", 0))
    except Exception:
        return 0


def get_summary(pair: str, is_otc: bool = False) -> str:
    """Human-readable Telegram card for the institutional flow."""
    r = analyze(pair, is_otc)
    if not r.get("ok"):
        return f"📊 <b>INST. FLOW</b>: No data for {pair}"

    d = r["big_player_direction"]
    d_emoji = "🟢" if d == "BUY" else ("🔴" if d == "SELL" else "⚪")
    q = r["entry_quality"]
    q_emoji = "🏆" if q == "ELITE" else ("✅" if q == "GOOD" else "⚠️")
    conf_pct = int(r["confidence"] * 100)

    lines = [
        f"🧠 <b>INSTITUTIONAL ORDER FLOW</b>",
        f"Pair: <b>{pair}</b>",
        f"Big Player: {d_emoji} <b>{d}</b>  Confidence: <b>{conf_pct}%</b>  {q_emoji} <b>{q}</b>",
    ]
    if r.get("delta", 0) != 0:
        dlt = r["delta"]
        cdlt = r["cum_delta"]
        lines.append(f"Delta: <b>{dlt:+.4f}</b>  Cumulative: <b>{cdlt:+.4f}</b>")

    if r.get("trap_detected"):
        lines.append(f"🪤 <b>LIQUIDITY HUNT DETECTED</b> — Enter <b>{r['trap_direction']}</b> on reversal")
    if r.get("absorption"):
        lines.append("🧱 <b>ABSORPTION</b> — Institution defending this level")
    if r.get("delta_divergence"):
        lines.append("⚠️ <b>DELTA DIVERGENCE</b> — Price move not backed by real volume")
    if r.get("volume_expansion"):
        lines.append("💥 <b>VOLUME EXPANSION</b> — Institutional surge detected")

    clusters = r.get("cluster_levels", [])
    if clusters:
        lines.append(f"\n📍 <b>{len(clusters)} Volume Cluster(s):</b>")
        for cl in clusters[:4]:
            p = cl.get("price", 0)
            imb = cl.get("imbalance", cl.get("vol_ratio", 0))
            side = cl.get("direction", "?")
            side_e = "🟢" if side == "BUY" else "🔴"
            lines.append(f"  {side_e} {p:.5f}  Imbalance: {imb:+.2f} [{side}]")

    if r.get("poc"):
        lines.append(f"POC (max volume): <b>{r['poc']:.5f}</b>")

    lines.append(f"\n💬 {r['reason']}")
    return "\n".join(lines)
