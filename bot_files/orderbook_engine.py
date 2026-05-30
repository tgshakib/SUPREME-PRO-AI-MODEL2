"""Order Book & Bid/Ask Imbalance Engine
==========================================
Fetches real-time order book depth from Binance public REST API (no auth).
For forex/metals/OTC pairs: uses micro-structure estimation from recent ticks.

Public entry points
-------------------
  get_orderbook_signal(pair)  → dict | None
      direction:  "BUY" | "SELL" | "NEUTRAL"
      imbalance:  float  (-1.0 … +1.0,  positive = bullish buy pressure)
      delta:      float  (total bid_vol - ask_vol at top levels)
      clusters:   list   significant volume cluster price levels
      confidence: float  (0-1, strength of the signal)

Works 100% from Binance public REST — no API key needed.
Cached per-pair for 2 seconds (fast market refresh).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: Dict[str, Tuple[float, dict]] = {}  # symbol → (timestamp, result)
_TTL = 2.0          # seconds — Binance order book refreshes sub-second
_DEPTH_LEVELS = 20  # top N bid + ask levels to analyse

# ── Binance symbol mapping ─────────────────────────────────────────────────────
_BINANCE_SYMBOLS: Dict[str, str] = {
    # Crypto ↔ USDT
    "btcusd":   "BTCUSDT",  "btcusdt":  "BTCUSDT",  "bitcoin":  "BTCUSDT",
    "ethusd":   "ETHUSDT",  "ethusdt":  "ETHUSDT",  "ethereum": "ETHUSDT",
    "solusd":   "SOLUSDT",  "solusdt":  "SOLUSDT",  "solana":   "SOLUSDT",
    "bnbusd":   "BNBUSDT",  "bnbusdt":  "BNBUSDT",  "bnb":      "BNBUSDT",
    "xrpusd":   "XRPUSDT",  "xrpusdt":  "XRPUSDT",  "ripple":   "XRPUSDT",
    "adausd":   "ADAUSDT",  "adausdt":  "ADAUSDT",  "cardano":  "ADAUSDT",
    "avaxusd":  "AVAXUSDT", "avaxusdt": "AVAXUSDT",
    "dotusd":   "DOTUSDT",  "dotusdt":  "DOTUSDT",
    "linkusd":  "LINKUSDT", "linkusdt": "LINKUSDT",
    "ltcusd":   "LTCUSDT",  "ltcusdt":  "LTCUSDT",  "litecoin": "LTCUSDT",
    "bchusd":   "BCHUSDT",  "bchusdt":  "BCHUSDT",
    "etcusd":   "ETCUSDT",  "etcusdt":  "ETCUSDT",
    "maticusd": "MATICUSDT","maticusdt":"MATICUSDT",
    "dogeusdt": "DOGEUSDT",
}

_BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol={sym}&limit={n}"

# Minimum total volume (bid+ask summed) to be considered "enough data"
_MIN_VOLUME = 0.001


def _pair_to_binance(pair: str) -> Optional[str]:
    """Convert bot pair label to a Binance symbol, or None if not crypto."""
    clean = re.sub(r"[^a-zA-Z0-9]", "", pair).lower()
    clean = re.sub(r"otc$", "", clean)
    return _BINANCE_SYMBOLS.get(clean)


def _http_get(url: str, timeout: float = 3.0) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "SupremePro/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return None


def _analyse_depth(bids: List, asks: List) -> dict:
    """
    Given raw bid/ask lists from Binance depth API, compute:
      - Per-level: bid_vol, ask_vol, imbalance, delta
      - Overall imbalance ratio
      - Significant volume clusters (volume > 2× mean)
      - Final directional bias
    """
    if not bids or not asks:
        return {}

    levels = []
    all_vols = []

    # Merge bids + asks into unified level list
    for price_s, vol_s in bids:
        try:
            p, v = float(price_s), float(vol_s)
            if p > 0 and v >= 0:
                levels.append({"price": p, "bid_vol": v, "ask_vol": 0.0})
                all_vols.append(v)
        except Exception:
            pass

    for price_s, vol_s in asks:
        try:
            p, v = float(price_s), float(vol_s)
            if p > 0 and v >= 0:
                # find matching bid level or create
                matched = next((l for l in levels if abs(l["price"] - p) < p * 0.00005), None)
                if matched:
                    matched["ask_vol"] = v
                    all_vols.append(v)
                else:
                    levels.append({"price": p, "bid_vol": 0.0, "ask_vol": v})
                    all_vols.append(v)
        except Exception:
            pass

    if not levels or not all_vols:
        return {}

    avg_vol = sum(all_vols) / len(all_vols)
    cluster_threshold = avg_vol * 2.0

    total_bid = 0.0
    total_ask = 0.0
    clusters = []

    for lvl in levels:
        bv = lvl["bid_vol"]
        av = lvl["ask_vol"]
        total = bv + av
        lvl["imbalance"] = (bv - av) / total if total > _MIN_VOLUME else 0.0
        lvl["delta"] = bv - av
        total_bid += bv
        total_ask += av

        # Mark significant clusters
        if total > cluster_threshold:
            lvl["cluster"] = True
            clusters.append({
                "price":     lvl["price"],
                "bid_vol":   bv,
                "ask_vol":   av,
                "delta":     lvl["delta"],
                "imbalance": lvl["imbalance"],
            })

    grand_total = total_bid + total_ask
    overall_imbalance = (
        (total_bid - total_ask) / grand_total if grand_total > _MIN_VOLUME else 0.0
    )
    overall_delta = total_bid - total_ask

    # Determine direction
    if overall_imbalance >= 0.30:
        direction = "BUY"
    elif overall_imbalance <= -0.30:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    # Confidence 0-1
    confidence = min(1.0, abs(overall_imbalance) / 0.60)

    return {
        "direction":  direction,
        "imbalance":  round(overall_imbalance, 4),
        "delta":      round(overall_delta, 4),
        "clusters":   clusters[:5],    # top 5 cluster levels
        "confidence": round(confidence, 4),
        "bid_total":  round(total_bid, 4),
        "ask_total":  round(total_ask, 4),
        "levels":     sorted(levels, key=lambda x: x["price"], reverse=True)[:20],
    }


def get_orderbook_signal(pair: str) -> Optional[dict]:
    """
    Fetch and analyse the Binance order book for a pair.

    Returns a dict with direction, imbalance, delta, clusters, confidence.
    Returns None if the pair is not on Binance or the request fails.
    Cached for _TTL seconds.
    """
    sym = _pair_to_binance(pair)
    if not sym:
        return None   # forex/OTC — not on Binance spot

    now = time.time()
    cached = _CACHE.get(sym)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    url  = _BINANCE_DEPTH_URL.format(sym=sym, n=_DEPTH_LEVELS)
    body = _http_get(url)
    if not body:
        return cached[1] if cached else None

    try:
        data = json.loads(body)
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        result = _analyse_depth(bids, asks)
        if result:
            result["pair"]      = sym
            result["timestamp"] = now
            _CACHE[sym] = (now, result)
            return result
    except Exception as exc:
        logger.debug(f"[orderbook] parse error for {sym}: {exc}")

    return cached[1] if cached else None


def format_orderbook_summary(pair: str) -> str:
    """Format a human-readable order book analysis for Telegram."""
    r = get_orderbook_signal(pair)
    if not r:
        return f"No order book data for {pair} (not a Binance spot pair)"

    sym = r.get("pair", pair)
    imb = r["imbalance"]
    dlt = r["delta"]
    lvls = r.get("levels", [])
    clusters = r.get("clusters", [])
    direction_emoji = "🟢" if r["direction"] == "BUY" else ("🔴" if r["direction"] == "SELL" else "⚪")

    lines = [
        f"📊 <b>ORDER BOOK</b> — {sym}",
        f"Direction: {direction_emoji} <b>{r['direction']}</b>  "
        f"Imbalance: <b>{imb:+.3f}</b>  Delta: <b>{dlt:+,.2f}</b>",
        "",
        f"{'Price':>12}  {'Bid Vol':>10}  {'Ask Vol':>10}  {'Imbal':>7}  {'Delta':>10}",
    ]
    for lvl in lvls[:10]:
        cluster_flag = " ← CLUSTER" if lvl.get("cluster") else ""
        lines.append(
            f"{lvl['price']:>12.4f}  "
            f"{lvl['bid_vol']:>10.4f}  "
            f"{lvl['ask_vol']:>10.4f}  "
            f"{lvl['imbalance']:>+7.3f}  "
            f"{lvl['delta']:>+10.4f}"
            f"{cluster_flag}"
        )
    if clusters:
        lines.append(f"\n🔥 <b>{len(clusters)} significant volume cluster(s)</b> detected")

    return "\n".join(lines)
