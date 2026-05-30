"""Footprint Candle & Trade Delta Engine
=========================================
Streams live aggTrade data from Binance public WebSocket (no auth needed).
Builds per-candle footprint: buy/sell volume, delta, POC, cumulative delta.

Supported pairs (Binance spot, no API key):
  BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT,
  AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, BCHUSDT

Public entry points
-------------------
  run_footprint_service()       — async coroutine, start as asyncio.create_task()
  get_footprint(pair)           → dict | None
      bias:         "BUY" | "SELL" | "NEUTRAL"
      delta:        float   (last closed candle delta = buy_vol - sell_vol)
      cum_delta:    float   (sum of last 10 candle deltas)
      poc:          float   (price level with most volume in last candle)
      buy_vol:      float
      sell_vol:     float
      candle_count: int
  get_footprint_summary(pair)   → human-readable string
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
_CANDLE_SECONDS   = 60       # 1-minute candles
_CANDLE_HISTORY   = 20       # closed candles kept per pair
_CUM_DELTA_WINDOW = 10       # candles for cumulative delta
_POC_TICK_BUCKETS = 20       # price level buckets per candle for POC
_RECONNECT_DELAY  = 15       # seconds between reconnects
_MAX_AGE_SEC      = 120      # footprint data older than this is stale

_BINANCE_WS_BASE  = "wss://stream.binance.com:9443/stream?streams="

# ── Pairs to subscribe ─────────────────────────────────────────────────────────
_SUPPORTED: Dict[str, str] = {
    # bot pair label (normalised lowercase) → Binance stream name
    "btcusd":   "btcusdt@aggTrade",  "btcusdt": "btcusdt@aggTrade",
    "bitcoin":  "btcusdt@aggTrade",
    "ethusd":   "ethusdt@aggTrade",  "ethusdt": "ethusdt@aggTrade",
    "ethereum": "ethusdt@aggTrade",
    "solusd":   "solusdt@aggTrade",  "solusdt": "solusdt@aggTrade",
    "solana":   "solusdt@aggTrade",
    "bnbusd":   "bnbusdt@aggTrade",  "bnbusdt": "bnbusdt@aggTrade",
    "xrpusd":   "xrpusdt@aggTrade",  "xrpusdt": "xrpusdt@aggTrade",
    "adausd":   "adausdt@aggTrade",  "adausdt": "adausdt@aggTrade",
    "avaxusd":  "avaxusdt@aggTrade", "avaxusdt":"avaxusdt@aggTrade",
    "dotusd":   "dotusdt@aggTrade",  "dotusdt": "dotusdt@aggTrade",
    "linkusd":  "linkusdt@aggTrade", "linkusdt":"linkusdt@aggTrade",
    "ltcusd":   "ltcusdt@aggTrade",  "ltcusdt": "ltcusdt@aggTrade",
    "bchusd":   "bchusdt@aggTrade",  "bchusdt": "bchusdt@aggTrade",
}

# ── In-memory state ────────────────────────────────────────────────────────────
# Per stream-name data
_LOCK = Lock()

# Closed candle history: stream_name → deque of candle dicts
_CANDLES: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_CANDLE_HISTORY))

# Current open candle builder: stream_name → dict
_OPEN_CANDLE: Dict[str, dict] = {}

# Footprint result cache: stream_name → dict
_FOOTPRINT: Dict[str, dict] = {}

_SERVICE_STARTED = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize(pair: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", pair).lower().replace("otc", "")


def _stream_for_pair(pair: str) -> Optional[str]:
    clean = _normalize(pair)
    return _SUPPORTED.get(clean)


def _candle_bucket(ts: float) -> int:
    """Floor timestamp to the start of its 1-minute candle."""
    return int(ts // _CANDLE_SECONDS) * _CANDLE_SECONDS


def _poc_from_levels(vol_by_level: Dict[float, float]) -> float:
    """Price level with the highest volume (Point of Control)."""
    if not vol_by_level:
        return 0.0
    return max(vol_by_level, key=vol_by_level.get)


def _bucket_price(price: float, candle_range: float) -> float:
    """Round price to the nearest bucket for POC aggregation."""
    if candle_range <= 0:
        return price
    tick = candle_range / _POC_TICK_BUCKETS
    return round(round(price / tick) * tick, 6)


def _close_candle(stream: str):
    """Finalise the open candle and push it to _CANDLES."""
    c = _OPEN_CANDLE.get(stream)
    if not c or (c["buy_vol"] + c["sell_vol"]) == 0:
        return
    c["delta"]     = c["buy_vol"] - c["sell_vol"]
    c["candle_range"] = c["high"] - c["low"]
    c["poc"]       = _poc_from_levels(c["vol_by_level"])
    c["closed"]    = True
    with _LOCK:
        _CANDLES[stream].append(c)
        _update_footprint(stream)


def _update_footprint(stream: str):
    """Recompute the footprint summary from closed candles."""
    candles = list(_CANDLES[stream])
    if not candles:
        return
    last = candles[-1]

    # Cumulative delta of last N candles
    recent = candles[-_CUM_DELTA_WINDOW:]
    cum_delta = sum(c.get("delta", 0) for c in recent)

    # Bias
    delta = last.get("delta", 0)
    if delta > 0:
        bias = "BUY"
    elif delta < 0:
        bias = "SELL"
    else:
        bias = "NEUTRAL"

    _FOOTPRINT[stream] = {
        "stream":       stream,
        "delta":        delta,
        "cum_delta":    cum_delta,
        "poc":          last.get("poc", 0.0),
        "buy_vol":      last.get("buy_vol", 0.0),
        "sell_vol":     last.get("sell_vol", 0.0),
        "open":         last.get("open", 0.0),
        "high":         last.get("high", 0.0),
        "low":          last.get("low", 0.0),
        "close":        last.get("close", 0.0),
        "candle_time":  last.get("bucket", 0),
        "bias":         bias,
        "candle_count": len(candles),
        "timestamp":    time.time(),
    }


def _on_trade(stream: str, data: dict):
    """Process a single aggTrade event."""
    try:
        price    = float(data["p"])
        qty      = float(data["q"])
        ts_ms    = int(data["T"])
        is_buyer = not data.get("m", True)  # m=True → market sell (maker=buyer side)
        ts_s     = ts_ms / 1000.0
        bucket   = _candle_bucket(ts_s)

        # Close previous candle if we've moved to a new minute
        current = _OPEN_CANDLE.get(stream)
        if current and current["bucket"] != bucket:
            _close_candle(stream)
            current = None

        # Init new candle
        if current is None:
            current = {
                "bucket":       bucket,
                "open":         price,
                "high":         price,
                "low":          price,
                "close":        price,
                "buy_vol":      0.0,
                "sell_vol":     0.0,
                "vol_by_level": defaultdict(float),
                "closed":       False,
            }
            _OPEN_CANDLE[stream] = current

        # Update candle
        current["high"]  = max(current["high"],  price)
        current["low"]   = min(current["low"],   price)
        current["close"] = price
        candle_range     = current["high"] - current["low"]
        bucketed_price   = _bucket_price(price, candle_range)

        if is_buyer:
            current["buy_vol"]                    += qty
        else:
            current["sell_vol"]                   += qty
        current["vol_by_level"][bucketed_price]   += qty

    except Exception:
        pass


# ── WebSocket stream ────────────────────────────────────────────────────────────

async def _stream_once():
    try:
        import websockets as _ws
    except ImportError:
        logger.error("[footprint] websockets not installed")
        await asyncio.sleep(3600)
        return

    streams = list(set(_SUPPORTED.values()))
    combined = "/".join(streams)
    url = _BINANCE_WS_BASE + combined
    logger.info(f"[footprint] Connecting to Binance ({len(streams)} streams)…")

    async with _ws.connect(url, ping_interval=20, ping_timeout=15) as ws:
        logger.info("[footprint] Connected — streaming trade data")
        async for raw in ws:
            try:
                msg = json.loads(raw)
                # Combined stream wraps in {"stream": "...", "data": {...}}
                if "stream" in msg and "data" in msg:
                    stream = msg["stream"]
                    _on_trade(stream, msg["data"])
                elif "e" in msg:
                    # Single stream fallback
                    sym = msg.get("s", "").lower()
                    stream = f"{sym}@aggTrade"
                    _on_trade(stream, msg)
            except Exception:
                pass


async def run_footprint_service():
    """Auto-reconnecting Binance trade stream.  Start as asyncio.create_task()."""
    global _SERVICE_STARTED
    _SERVICE_STARTED = True
    delay = _RECONNECT_DELAY
    while True:
        try:
            await _stream_once()
            delay = _RECONNECT_DELAY
        except Exception as exc:
            logger.warning(f"[footprint] Stream error: {exc} — reconnecting in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)


# ── Public query API ────────────────────────────────────────────────────────────

def get_footprint(pair: str) -> Optional[dict]:
    """
    Return the latest footprint data for a pair, or None if unavailable.
    Data older than _MAX_AGE_SEC is considered stale and discarded.
    """
    stream = _stream_for_pair(pair)
    if not stream:
        return None
    with _LOCK:
        fp = _FOOTPRINT.get(stream)
    if fp and (time.time() - fp.get("timestamp", 0)) < _MAX_AGE_SEC:
        return fp
    return None


def get_all_footprints() -> Dict[str, dict]:
    """Return all current footprint data keyed by stream name."""
    now = time.time()
    with _LOCK:
        return {
            k: v for k, v in _FOOTPRINT.items()
            if (now - v.get("timestamp", 0)) < _MAX_AGE_SEC
        }


def get_footprint_summary(pair: str) -> str:
    """Format a human-readable footprint summary for Telegram."""
    fp = get_footprint(pair)
    if not fp:
        return f"No footprint data for {pair} (not a Binance spot pair or stream not started yet)"

    bias_emoji = "🟢" if fp["bias"] == "BUY" else ("🔴" if fp["bias"] == "SELL" else "⚪")
    delta      = fp["delta"]
    cum_delta  = fp["cum_delta"]
    sign       = "+" if delta >= 0 else ""
    cum_sign   = "+" if cum_delta >= 0 else ""

    import datetime
    candle_dt = datetime.datetime.utcfromtimestamp(fp["candle_time"]).strftime("%H:%M:%S")

    return (
        f"📊 <b>FOOTPRINT</b> [{candle_dt} UTC]  {fp['stream'].split('@')[0].upper()}\n"
        f"O:{fp['open']:.4f}  H:{fp['high']:.4f}  "
        f"L:{fp['low']:.4f}  C:{fp['close']:.4f}\n"
        f"Buy Vol:  <b>{fp['buy_vol']:,.2f}</b>    "
        f"Sell Vol: <b>{fp['sell_vol']:,.2f}</b>\n"
        f"Delta: <b>{sign}{delta:,.2f}</b>  {bias_emoji} {fp['bias']}\n"
        f"POC: <b>{fp['poc']:.4f}</b>\n"
        f"Cumulative Delta (last {_CUM_DELTA_WINDOW}): <b>{cum_sign}{cum_delta:,.2f}</b>\n"
        f"Candles collected: {fp['candle_count']}"
    )
