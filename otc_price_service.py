"""Unified OTC Real-Time Price Service
======================================
Streams live tick prices from BOTH brokers simultaneously:
  • Quotex   — pyquotex WebSocket  (all QX OTC pairs)
  • Pocket Option — Socket.IO WS    (all PO OTC pairs)

Both streams write into a shared thread-safe price buffer.
The signal engines call  get_live_otc_price(pair_label)  to get
the freshest available price, preferring PO when both are live.

How to use in bot.py
---------------------
    from otc_price_service import run_otc_price_service
    asyncio.create_task(run_otc_price_service())
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Credentials ──────────────────────────────────────────────────────────────
# pyquotex uses email+password directly — no SSID / Chrome needed.
# Keep these names aligned with qx_auth.py; never store credentials in source.
QX_EMAIL    = os.environ.get("QUOTEX_EMAIL", "").strip()
QX_PASSWORD = os.environ.get("QUOTEX_PASSWORD", "").strip()
QX_SSID     = os.environ.get("QUOTEX_SSID", "").strip()

# The Pocket Option auth manager supplies PO_SSID at runtime. Do not embed a
# token here; an absent value simply lets po_auth.py recover it.
PO_SSID = os.environ.get("PO_SSID", "").strip()

# ── Stream settings ───────────────────────────────────────────────────────────
_CANDLE_PERIOD   = 60      # seconds (1-minute candles match pocket_option_ws)
_PRICE_MAX_AGE   = 3.0     # seconds — fresh tick window (ultra-tight for real-time accuracy)
_PRICE_STALE_AGE = 90.0    # seconds — stale fallback: return last known rather than None
# Same Socket.IO endpoint used by pocket_option_ws.py (the working one)
_PO_WS_URL       = "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket"
_PO_SUB_DELAY    = 0.05   # seconds between subscribe frames (faster subscription)
_QX_BATCH        = 5
_QX_BATCH_DELAY  = 0.2
_QX_RECONNECT    = 15
_PO_RECONNECT    = 15

# ── Shared price buffer ───────────────────────────────────────────────────────
# key: normalised asset name lowercase  e.g. "eurusd_otc"
# val: {"price": float, "time": float, "source": "qx"|"po"}
_PRICES: Dict[str, Dict] = {}
# Keep each broker's latest tick separately.  The old shared latest-price map
# is retained for legacy callers, but it cannot answer "what did the selected
# broker say?" after the other broker has emitted a newer tick.
_BROKER_PRICES: Dict[str, Dict[str, Dict]] = defaultdict(dict)
# Recent broker-native ticks make a short OTC momentum read possible while the
# completed-candle stream is reconnecting.
_BROKER_TICKS: Dict[Tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=40))
_LOCK   = Lock()

# ── ALL OTC pairs for each broker ─────────────────────────────────────────────
# Quotex — internal WS asset names (lowercase_otc)
_QX_OTC_PAIRS: list[str] = [
    # Major Forex
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc",
    "USDCAD_otc", "AUDUSD_otc", "NZDUSD_otc",
    # Minor / Cross
    "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "AUDNZD_otc",
    "CADCHF_otc", "CADJPY_otc", "CHFJPY_otc",
    "EURAUD_otc", "EURCAD_otc", "EURCHF_otc", "EURGBP_otc",
    "EURJPY_otc", "EURNZD_otc",
    "GBPAUD_otc", "GBPCAD_otc", "GBPCHF_otc", "GBPJPY_otc", "GBPNZD_otc",
    "NZDCAD_otc", "NZDCHF_otc", "NZDJPY_otc",
    # Exotic / EM
    "USDARS_otc", "USDBDT_otc", "USDBRL_otc", "USDCOP_otc",
    "USDDZD_otc", "USDEGP_otc", "USDIDR_otc", "USDINR_otc",
    "USDMXN_otc", "USDNGN_otc", "USDPHP_otc", "USDPKR_otc",
    "USDZAR_otc",
    # Metals
    "XAUUSD_otc", "XAGUSD_otc",
    # Energy
    "UKBRENT_otc", "USCRUDE_otc",
    # Crypto
    "BTCUSD_otc",  "ETHUSD_otc",  "ETCUSD_otc",  "LTCUSD_otc",
    "BCHUSD_otc",  "BNBUSD_otc",  "SOLUSD_otc",  "AVAXUSD_otc",
    "DOTUSD_otc",  "LINKUSD_otc", "DASHUSD_otc", "AXSUSD_otc",
    "TONUSD_otc",  "TRUMPUSD_otc",
    # Stocks
    "AMEX_otc", "BA_otc", "FB_otc", "INTC_otc",
    "JNJ_otc",  "MCD_otc", "PFE_otc",
]

# Pocket Option — asset names as recognised by the PO WebSocket
_PO_OTC_PAIRS: list[str] = [
    # Major Forex
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc",
    "USDCAD_otc", "AUDUSD_otc", "NZDUSD_otc",
    # Minor / Cross
    "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "AUDNZD_otc",
    "CADCHF_otc", "CADJPY_otc", "CHFJPY_otc",
    "EURAUD_otc", "EURCAD_otc", "EURCHF_otc", "EURGBP_otc",
    "EURJPY_otc", "EURNZD_otc",
    "GBPAUD_otc", "GBPCAD_otc", "GBPCHF_otc", "GBPJPY_otc", "GBPNZD_otc",
    "NZDCAD_otc", "NZDCHF_otc", "NZDJPY_otc",
    # Exotic / EM
    "USDMXN_otc", "USDINR_otc", "USDBRL_otc", "USDCOP_otc",
    "USDARS_otc", "USDPKR_otc", "USDNGN_otc", "USDEGP_otc",
    "USDIDR_otc", "USDPHP_otc", "USDZAR_otc", "USDBDT_otc", "USDDZD_otc",
    # Metals
    "XAUUSD_otc", "XAGUSD_otc",
    # Energy
    "USOIL_otc", "BRENT_otc",
    # Indices
    "NQ_otc", "SP_otc", "DJI_otc",
    # Crypto
    "BTCUSD_otc",   "ETHUSD_otc",   "LTCUSD_otc",   "BCHUSD_otc",
    "ETCUSD_otc",   "BNBUSD_otc",   "SOLUSD_otc",   "AVAXUSD_otc",
    "DOTUSD_otc",   "LINKUSD_otc",  "DASHUSD_otc",  "AXSUSD_otc",
    "TONUSD_otc",   "XRPUSD_otc",   "ADAUSD_otc",   "MATICUSD_otc",
    # Stocks
    "AAPL_otc", "AMZN_otc", "TSLA_otc", "GOOGL_otc", "MSFT_otc",
    "META_otc", "NFLX_otc", "NVDA_otc", "BABA_otc",  "JNJ_otc",
    "PFE_otc",  "BA_otc",   "MCD_otc",  "INTC_otc",  "AMEX_otc",
    "CSCO_otc", "V_otc",    "MA_otc",   "DIS_otc",   "IBM_otc",
]

# ── Pair label → normalised asset key ────────────────────────────────────────
# Maps bot display labels to the lowercase asset key used in _PRICES
_LABEL_MAP: Dict[str, str] = {
    # Auto-generated forex (handled by code below)
    # Manual overrides for non-standard names:
    "gold":            "xauusd_otc",
    "xauusd":          "xauusd_otc",
    "silver":          "xagusd_otc",
    "xagusd":          "xagusd_otc",
    "bitcoin":         "btcusd_otc",
    "btc":             "btcusd_otc",
    "btcusd":          "btcusd_otc",
    "ethereum":        "ethusd_otc",
    "eth":             "ethusd_otc",
    "ethusd":          "ethusd_otc",
    "ethereumclassic": "etcusd_otc",
    "ethereumclassic": "etcusd_otc",
    "etcusd":          "etcusd_otc",
    "litecoin":        "ltcusd_otc",
    "ltcusd":          "ltcusd_otc",
    "bitcoincash":     "bchusd_otc",
    "bchusd":          "bchusd_otc",
    "binancecoin":     "bnbusd_otc",
    "bnbusd":          "bnbusd_otc",
    "solana":          "solusd_otc",
    "solusd":          "solusd_otc",
    "avalanche":       "avaxusd_otc",
    "avaxusd":         "avaxusd_otc",
    "polkadot":        "dotusd_otc",
    "dotusd":          "dotusd_otc",
    "chainlink":       "linkusd_otc",
    "linkusd":         "linkusd_otc",
    "dash":            "dashusd_otc",
    "dashusd":         "dashusd_otc",
    "axieinfinity":    "axsusd_otc",
    "axsusd":          "axsusd_otc",
    "toncoin":         "tonusd_otc",
    "tonusd":          "tonusd_otc",
    "trump":           "trumpusd_otc",
    "trumpusd":        "trumpusd_otc",
    "ripple":          "xrpusd_otc",
    "xrpusd":          "xrpusd_otc",
    "cardano":         "adausd_otc",
    "adausd":          "adausd_otc",
    "polygon":         "maticusd_otc",
    "maticusd":        "maticusd_otc",
    # Stocks
    "americanexpress": "amex_otc",
    "amex":            "amex_otc",
    "boeing":          "ba_otc",
    "boeingcompany":   "ba_otc",
    "ba":              "ba_otc",
    "facebook":        "fb_otc",
    "facebookinc":     "fb_otc",
    "meta":            "meta_otc",
    "fb":              "fb_otc",
    "intel":           "intc_otc",
    "intc":            "intc_otc",
    "johnson":         "jnj_otc",
    "jnj":             "jnj_otc",
    "mcdonalds":       "mcd_otc",
    "mcd":             "mcd_otc",
    "pfizer":          "pfe_otc",
    "pfe":             "pfe_otc",
    "apple":           "aapl_otc",
    "aapl":            "aapl_otc",
    "amazon":          "amzn_otc",
    "amzn":            "amzn_otc",
    "tesla":           "tsla_otc",
    "tsla":            "tsla_otc",
    "google":          "googl_otc",
    "googl":           "googl_otc",
    "alphabet":        "googl_otc",
    "microsoft":       "msft_otc",
    "msft":            "msft_otc",
    "netflix":         "nflx_otc",
    "nflx":            "nflx_otc",
    "nvidia":          "nvda_otc",
    "nvda":            "nvda_otc",
    "alibaba":         "baba_otc",
    "baba":            "baba_otc",
    "cisco":           "csco_otc",
    "csco":            "csco_otc",
    "visa":            "v_otc",
    "mastercard":      "ma_otc",
    "disney":          "dis_otc",
    "dis":             "dis_otc",
    "ibm":             "ibm_otc",
    # Energy
    "ukbrent":         "brent_otc",
    "brent":           "brent_otc",
    "uscrude":         "uscrude_otc",
    "usoil":           "usoil_otc",
    "wti":             "usoil_otc",
    # Indices
    "nas100":          "nq_otc",
    "us100":           "nq_otc",
    "nasdaq":          "nq_otc",
    "sp500":           "sp_otc",
    "spx500":          "sp_otc",
    "dow":             "dji_otc",
    "dji":             "dji_otc",
}


def _normalize_pair(pair: str) -> str:
    """Convert any bot pair label to a lowercase asset key for _PRICES lookup.

    Examples:
      "EUR/USD 〔OTC〕"         → "eurusd_otc"
      "AUD/CAD 〔OTC〕"         → "audcad_otc"
      "Bitcoin 〔OTC〕"         → "btcusd_otc"
      "FACEBOOK INC 〔OTC〕"    → "fb_otc"
      "UKBrent 〔OTC〕"         → "brent_otc"
    """
    # Strip OTC suffixes and decorations
    s = pair.upper().strip()
    s = re.sub(r"\s*〔OTC〕\s*$", "", s)
    s = re.sub(r"\s*\(OTC\)\s*$", "", s)
    # Remove non-alphanumeric except slash/space for now
    clean = re.sub(r"[^A-Z0-9/]", "", s).lower()
    # Remove slashes
    clean = clean.replace("/", "")

    # Check manual map first
    if clean in _LABEL_MAP:
        return _LABEL_MAP[clean]

    # Auto-convert 6-letter forex pairs: "eurusd" → "eurusd_otc"
    if re.match(r"^[a-z]{6}$", clean):
        return f"{clean}_otc"

    # Partial match in label map
    for key, val in _LABEL_MAP.items():
        if key in clean or clean in key:
            return val

    # Final fallback: append _otc
    return f"{clean}_otc"


# ── Shared buffer API ─────────────────────────────────────────────────────────

def _write_price(asset_key: str, price: float, source: str):
    """Write a live price into the shared buffer (thread-safe).

    Always updates the timestamp on every incoming tick so the freshness
    check never rejects a valid price that happened to repeat its value.
    """
    if price <= 0:
        return
    key = asset_key.lower()
    now = time.time()
    with _LOCK:
        existing = _PRICES.get(key)
        # Always write — even if price is identical, update timestamp so
        # the 8-second max-age guard stays satisfied on quiet markets.
        if existing is None or price != existing["price"] or (now - existing["time"]) > 2.0:
            _PRICES[key] = {"price": price, "time": now, "source": source}
        if source in ("qx", "po"):
            broker_entry = {"price": float(price), "time": now, "source": source}
            _BROKER_PRICES[key][source] = broker_entry
            _BROKER_TICKS[(key, source)].append(broker_entry)


def get_selected_broker_ticks(
    pair: str, broker: str, *, max_age_sec: float = 90.0, limit: int = 12,
) -> list[Dict]:
    """Return recent ticks from exactly one OTC broker.

    A QX tick must never replace a PO tick (or the reverse) for a user who
    explicitly selected that broker.  Entries older than the source window are
    excluded so reconnecting feeds cannot create signals from old prices.
    """
    if broker not in ("po", "qx"):
        return []
    now = time.time()
    key = _normalize_pair(pair)
    with _LOCK:
        ticks = list(_BROKER_TICKS.get((key, broker), ()))
    return [
        dict(tick) for tick in ticks[-limit:]
        if 0 <= now - float(tick.get("time") or 0) <= max_age_sec
    ]


def get_live_otc_price(
    pair: str,
    broker_only: bool = True,
    broker: Optional[str] = None,
) -> Optional[float]:
    """Return the freshest live OTC price for a bot pair label.

    Source priority (freshness checked per-source):
      1. Broker tick  (qx / po)  — freshest; up to _PRICE_STALE_AGE (90 s)
      2. Stooq bridge (stooq)    — real-time forex mid-price when WS is down;
                                   up to 15 s stale (Stooq TTL = 5 s)
      3. yfinance     (yf)       — BLOCKED by default; real-market prices can
                                   differ 5-15%+ from broker OTC synthetic feed.

    broker_only=False → legacy behaviour (includes yfinance; not recommended).
    """
    key = _normalize_pair(pair)
    with _LOCK:
        if broker in ("po", "qx"):
            entry = dict(_BROKER_PRICES.get(key, {}).get(broker) or {})
        else:
            entry = _PRICES.get(key)
    if not entry:
        return None
    source = entry.get("source", "")
    # yfinance is always blocked for OTC (real-market ≠ synthetic broker price)
    if broker_only and source == "yf":
        return None
    age = time.time() - entry["time"]
    # Broker ticks: accept up to 90 s stale
    if source in ("qx", "po") and age < _PRICE_STALE_AGE:
        return entry["price"]
    # Stooq bridge: accept up to 15 s (Stooq's own 5 s TTL + network margin)
    if source == "stooq" and age < 15:
        return entry["price"]
    return None


def get_live_otc_source(pair: str) -> Optional[str]:
    """Return the price source for a pair: 'qx', 'po', 'yf', or None."""
    key = _normalize_pair(pair)
    with _LOCK:
        entry = _PRICES.get(key)
    return entry.get("source") if entry else None


def get_otc_status() -> Dict[str, int]:
    """Return {"qx": N, "po": N, "yf": N} count of live prices per source."""
    now = time.time()
    with _LOCK:
        qx = sum(
            1 for sources in _BROKER_PRICES.values()
            for v in [sources.get("qx")]
            if v and now - v["time"] < _PRICE_MAX_AGE
        )
        po = sum(
            1 for sources in _BROKER_PRICES.values()
            for v in [sources.get("po")]
            if v and now - v["time"] < _PRICE_MAX_AGE
        )
        yf = sum(1 for v in _PRICES.values()
                 if v.get("source") == "yf" and now - v["time"] < 60)
    return {"qx": qx, "po": po, "yf": yf}


# ── Pocket Option Socket.IO stream ────────────────────────────────────────────

async def _po_stream_once(ssid: str):
    """Single PO WebSocket session — subscribes all OTC pairs, reads ticks."""
    try:
        import websockets as _ws
    except ImportError:
        logger.error("[otc_svc] websockets not installed — PO stream disabled")
        await asyncio.sleep(3600)
        return

    headers = {
        "Origin": "https://po.trade",
        "Referer": "https://po.trade/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        # Send SSID under multiple cookie names PO accepts
        "Cookie": f"ssid={ssid}; token={ssid}",
        "Sec-Fetch-Dest": "websocket",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Site": "same-site",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }
    logger.info("[otc_svc:po] Connecting to Pocket Option …")
    async with _ws.connect(
        _PO_WS_URL,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=15,
        close_timeout=10,
    ) as ws:
        # Socket.IO handshake
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        raw = str(raw)
        ping_interval = 25.0
        if raw.startswith("0"):
            try:
                hs = json.loads(raw[1:])
                ping_interval = hs.get("pingInterval", 25000) / 1000
            except Exception:
                pass

        await ws.send("40")
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        if not str(resp).startswith("40"):
            raise ConnectionError(f"PO namespace rejected: {resp!r}")

        # Authenticate
        auth_msg = json.dumps(["auth", {"session": ssid, "isDemo": 1}])
        await ws.send(f"42{auth_msg}")
        logger.info("[otc_svc:po] Auth frame sent …")
        auth_resp = str(await asyncio.wait_for(ws.recv(), timeout=15))
        if "failauth" in auth_resp.lower() or (
            "error" in auth_resp.lower() and "successauth" not in auth_resp.lower()
        ):
            raise ConnectionError(f"PO auth rejected: {auth_resp[:200]}")
        logger.info(f"[otc_svc:po] Authenticated — subscribing {len(_PO_OTC_PAIRS)} pairs …")

        # Subscribe all OTC pairs
        for asset in _PO_OTC_PAIRS:
            sub = json.dumps(["subscribeSymbol", {"asset": asset, "period": _CANDLE_PERIOD}])
            await ws.send(f"42{sub}")
            await asyncio.sleep(_PO_SUB_DELAY)
        logger.info("[otc_svc:po] All pairs subscribed — streaming live prices")

        # Heartbeat task
        async def _hb():
            while True:
                await asyncio.sleep(ping_interval)
                try:
                    await ws.send("2")
                except Exception:
                    break
        hb = asyncio.create_task(_hb())

        try:
            async for raw_msg in ws:
                msg = str(raw_msg)
                if msg == "2":
                    await ws.send("3")
                    continue
                if msg == "3":
                    continue
                if not msg.startswith("42"):
                    continue
                try:
                    payload = json.loads(msg[2:])
                    if not isinstance(payload, list) or len(payload) < 2:
                        continue
                    event, data = payload[0], payload[1]

                    if event in ("newcandle", "candle", "tick"):
                        asset = data.get("asset", data.get("symbol", ""))
                        price = (
                            data.get("close") or data.get("price")
                            or data.get("bid") or data.get("ask")
                        )
                        if asset and price:
                            _write_price(asset, float(price), "po")
                            # Also write with normalised key so lookups match
                            _write_price(_normalize_pair(asset), float(price), "po")

                    elif event in ("candles", "history"):
                        asset   = data.get("asset", data.get("symbol", ""))
                        candles = data.get("candles", data.get("data", []))
                        if asset and candles:
                            last = candles[-1]
                            price = last.get("close") or last.get("price")
                            if price:
                                _write_price(asset, float(price), "po")
                                _write_price(_normalize_pair(asset), float(price), "po")

                    elif event == "price":
                        asset = data.get("asset", data.get("symbol", ""))
                        price = data.get("price") or data.get("close")
                        if asset and price:
                            _write_price(asset, float(price), "po")
                            _write_price(_normalize_pair(asset), float(price), "po")

                    elif event in ("successauth", "authSuccess", "auth"):
                        logger.info("[otc_svc:po] Auth confirmed — prices streaming")

                except Exception:
                    pass
        finally:
            hb.cancel()


def _get_active_po_ssid() -> str:
    """Return the current PO SSID — always reads the live env var so that
    po_auth.py SSID refreshes are picked up on the next reconnect."""
    return os.environ.get("PO_SSID", PO_SSID) or PO_SSID


async def _run_po_loop():
    """Auto-reconnecting PO price stream."""
    delay = _PO_RECONNECT
    while True:
        try:
            ssid = _get_active_po_ssid()
            await _po_stream_once(ssid)
        except ConnectionError as exc:
            logger.error(f"[otc_svc:po] Auth/connection error: {exc}")
            # Immediately request a fresh SSID on any auth failure
            try:
                from po_auth import refresh_ssid_now as _po_refresh
                logger.info("[otc_svc:po] Auth failed — requesting SSID refresh …")
                _po_refresh()
            except Exception:
                pass
            logger.info(f"[otc_svc:po] Retrying in {delay}s …")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)   # cap at 60s — reconnect fast
        except Exception as exc:
            logger.warning(f"[otc_svc:po] Stream error: {exc} — reconnecting in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)   # cap at 60s
        else:
            delay = _PO_RECONNECT


# ── Quotex pyquotex stream ─────────────────────────────────────────────────────
# Uses pyquotex (iahmedani fork, Python 3.11 compatible via --ignore-requires-python)
# Pure WebSocket auth — email + password only, no Chrome, no SSID needed.
# API summary:
#   connect()                        → (bool, str) — (success, reason)
#   check_connect()                  → async bool
#   start_candles_stream(asset, period) → async, subscribes WS ticks
#   get_realtime_price(asset)        → async list[dict] from internal buffer
#   close()                          → async

def _seed_pyquotex_session(token: str) -> None:
    """Pre-write session.json so pyquotex skips HTTP login entirely.

    pyquotex checks session_data["token"] in _connect_unlocked():
    if it is set, api.authenticate() (the Cloudflare-blocked HTTP step)
    is completely bypassed and pyquotex goes straight to WebSocket.
    """
    try:
        import json as _json
        from pathlib import Path
        session_file = Path("session.json")
        try:
            all_sess = _json.loads(session_file.read_text()) if session_file.exists() else {}
        except Exception:
            all_sess = {}
        all_sess[QX_EMAIL] = {
            "cookies": f"token={token}",
            "token":   token,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        session_file.write_text(_json.dumps(all_sess, indent=4))
        logger.debug("[otc_svc:qx] session.json seeded — HTTP auth will be skipped")
    except Exception as exc:
        logger.debug(f"[otc_svc:qx] session.json seed error: {exc}")


async def _qx_stream_once():
    """Single Quotex WebSocket session using pyquotex.

    Two-path auth strategy:
      1. If QUOTEX_SSID is set: seed session.json → pyquotex skips
         Cloudflare-blocked HTTP login and goes straight to WebSocket.
      2. No SSID: pyquotex attempts HTTP login with curl_cffi
         (Chrome TLS fingerprint) — works on non-datacenter IPs.

    Auto-subscribes all 66 OTC pairs and streams real-time tick prices
    into the shared price buffer.
    """
    try:
        from pyquotex.stable_api import Quotex, ProxyConfig
    except ImportError:
        logger.error(
            "[otc_svc:qx] pyquotex is unavailable. Its supported release "
            "requires Python 3.12+, while this workflow currently uses Python 3.11."
        )
        await asyncio.sleep(3600)
        return

    if not QX_EMAIL or not QX_PASSWORD:
        logger.error("[otc_svc:qx] QUOTEX_EMAIL and QUOTEX_PASSWORD are not configured")
        await asyncio.sleep(300)
        return

    # ── Token bypass: skip Cloudflare HTTP auth ────────────────────────────
    token = os.environ.get("QUOTEX_SSID", "").strip()
    if not token:
        try:
            from qx_auth import get_current_ssid as _gcs
            token = _gcs() or ""
        except Exception:
            pass

    if not token or len(token) <= 10:
        # qx_auth.py owns email/password login and SSID renewal.  Starting a
        # second direct login here causes concurrent Cloudflare challenges and
        # makes successful token persistence unreliable.
        logger.info("[otc_svc:qx] Waiting for managed Quotex SSID …")
        await asyncio.sleep(60)
        return

    _seed_pyquotex_session(token)
    logger.info("[otc_svc:qx] SSID seeded → skipping Cloudflare HTTP auth")
    _proxy_cfg = None   # WS does not need a browser-TLS HTTP client

    client = Quotex(
        email=QX_EMAIL,
        password=QX_PASSWORD,
        lang="en",
        proxy_config=_proxy_cfg,
    )
    logger.info("[otc_svc:qx] Connecting via pyquotex …")

    try:
        check, reason = await client.connect()
    except Exception as exc:
        raise ConnectionError(f"QX connect() raised: {exc}")

    if not check:
        raise ConnectionError(f"QX connect() failed: {reason}")

    logger.info("[otc_svc:qx] ✅ Connected — subscribing OTC pairs …")

    # ── Subscribe all OTC pairs ───────────────────────────────────────────────
    asset_map: Dict[str, str] = {}   # normalised pair key → asset name (UPPER_OTC)
    subscribed = 0

    for i in range(0, len(_QX_OTC_PAIRS), _QX_BATCH):
        batch = _QX_OTC_PAIRS[i: i + _QX_BATCH]
        for pair in batch:
            asset_name = pair.upper()   # e.g. "EURUSD_OTC"
            try:
                await client.start_candles_stream(asset_name, _CANDLE_PERIOD)
                asset_map[pair] = asset_name
                subscribed += 1
            except Exception:
                pass
        await asyncio.sleep(_QX_BATCH_DELAY)

    logger.info(
        f"[otc_svc:qx] Subscribed {subscribed}/{len(_QX_OTC_PAIRS)} QX OTC pairs — streaming"
    )

    if subscribed == 0:
        await client.close()
        logger.info("[otc_svc:qx] 0 pairs subscribed — market may be closed, retrying in 60s")
        await asyncio.sleep(60)
        return

    # ── Real-time price loop ──────────────────────────────────────────────────
    _no_data_streak = 0
    try:
        while True:
            await asyncio.sleep(0.5)   # 2× per second

            # Verify connection is still alive
            try:
                still_up = await client.check_connect()
            except Exception:
                still_up = False
            if not still_up:
                logger.warning("[otc_svc:qx] Connection dropped — reconnecting")
                break

            got_any = False
            for pair, asset in asset_map.items():
                try:
                    ticks = await client.get_realtime_price(asset)
                    if not ticks:
                        continue
                    latest = ticks[-1]
                    for k in ("price", "close", "bid", "ask", "value"):
                        v = latest.get(k)
                        if v is not None:
                            fv = float(v)
                            if fv > 0:
                                _write_price(asset, fv, "qx")
                                _write_price(_normalize_pair(asset), fv, "qx")
                                got_any = True
                                break
                except Exception:
                    pass

            # Stale detection: 60s of no ticks → connection is silently dead
            if got_any:
                _no_data_streak = 0
            else:
                _no_data_streak += 1
                if _no_data_streak > 120:   # 60s × 2 polls/s = 120 cycles
                    logger.warning("[otc_svc:qx] No ticks for 60s — reconnecting")
                    break

    finally:
        try:
            await client.close()
        except Exception:
            pass


async def _run_qx_loop():
    """Auto-reconnecting Quotex price stream (pyquotex — email/password auth).

    Connects directly via WebSocket — no SSID, no Chrome.
    Retries with exponential backoff on any failure.
    """
    delay = _QX_RECONNECT
    _fail_count = 0
    while True:
        try:
            await _qx_stream_once()
            _fail_count = 0   # clean session
            delay = _QX_RECONNECT
        except ConnectionError as exc:
            _fail_count += 1
            logger.error(f"[otc_svc:qx] Auth/connection error (#{_fail_count}): {exc}")
            wait = min(15 * _fail_count, 120)
            logger.info(f"[otc_svc:qx] Retrying QX stream in {wait}s …")
            await asyncio.sleep(wait)
            delay = _QX_RECONNECT
        except Exception as exc:
            _fail_count += 1
            logger.warning(
                f"[otc_svc:qx] Stream error (#{_fail_count}): {exc} — "
                f"reconnecting in {min(delay, 30)}s"
            )
            await asyncio.sleep(min(delay, 30))
            delay = min(delay * 1.5, 60)


# ── Public entry point ────────────────────────────────────────────────────────

async def _sync_from_po_candles():
    """Read prices from the existing pocket_option_ws candle buffer every 2s.

    pocket_option_ws.py runs on its own daemon thread using the working
    Socket.IO endpoint and is already authenticated.  Its candle buffer
    contains real broker OTC prices for all subscribed pairs.  We scrape
    those closes into _PRICES so signals get correct OTC prices even
    before the async WebSocket streams connect.
    """
    try:
        from pocket_option_ws import get_candles as _po_c, _PAIR_TO_ASSET
    except ImportError:
        logger.warning("[otc_svc] pocket_option_ws not available — candle sync disabled")
        return

    logger.info("[otc_svc] PO candle sync started (pocket_option_ws buffer)")
    while True:
        try:
            seen = set()
            for _asset in set(_PAIR_TO_ASSET.values()):
                if _asset in seen:
                    continue
                seen.add(_asset)
                bars = _po_c(_asset, 60)
                if not bars:
                    continue
                last = bars[-1]
                px = float(last.get("close") or last.get("price") or 0)
                if px > 0:
                    _write_price(_asset, px, "po")
                    # Also write normalised key so pair lookups always hit
                    _write_price(_normalize_pair(_asset), px, "po")
        except Exception as exc:
            logger.debug(f"[otc_svc] candle sync tick error: {exc}")
        await asyncio.sleep(1)   # sync every 1s instead of 2s


# ── yfinance ticker map for OTC pairs ─────────────────────────────────────────
_YF_OTC_MAP: Dict[str, str] = {
    # Major Forex
    "eurusd_otc": "EURUSD=X", "gbpusd_otc": "GBPUSD=X",
    "usdjpy_otc": "JPY=X",    "usdchf_otc": "CHF=X",
    "usdcad_otc": "CAD=X",    "audusd_otc": "AUDUSD=X",
    "nzdusd_otc": "NZDUSD=X",
    # Crosses
    "audcad_otc": "AUDCAD=X", "audchf_otc": "AUDCHF=X",
    "audjpy_otc": "AUDJPY=X", "audnzd_otc": "AUDNZD=X",
    "cadchf_otc": "CADCHF=X", "cadjpy_otc": "CADJPY=X",
    "chfjpy_otc": "CHFJPY=X",
    "euraud_otc": "EURAUD=X", "eurcad_otc": "EURCAD=X",
    "eurchf_otc": "EURCHF=X", "eurgbp_otc": "EURGBP=X",
    "eurjpy_otc": "EURJPY=X", "eurnzd_otc": "EURNZD=X",
    "gbpaud_otc": "GBPAUD=X", "gbpcad_otc": "GBPCAD=X",
    "gbpchf_otc": "GBPCHF=X", "gbpjpy_otc": "GBPJPY=X",
    "gbpnzd_otc": "GBPNZD=X",
    "nzdcad_otc": "NZDCAD=X", "nzdchf_otc": "NZDCHF=X",
    "nzdjpy_otc": "NZDJPY=X",
    # Exotics
    "usdmxn_otc": "MXN=X",   "usdinr_otc": "INR=X",
    "usdbrl_otc": "BRL=X",   "usdcop_otc": "COP=X",
    "usdars_otc": "ARS=X",   "usdpkr_otc": "PKR=X",
    "usdzar_otc": "ZAR=X",   "usdidr_otc": "IDR=X",
    "usdphp_otc": "PHP=X",   "usdbdt_otc": "BDT=X",
    "usdegp_otc": "EGP=X",   "usddzd_otc": "DZD=X",
    "usdngn_otc": "NGN=X",
    # Metals
    "xauusd_otc": "GC=F",    "xagusd_otc": "SI=F",
    # Energy
    "ukbrent_otc": "BZ=F",   "brent_otc": "BZ=F",
    "uscrude_otc": "CL=F",   "usoil_otc": "CL=F",
    # Indices
    "nq_otc": "NQ=F",        "sp_otc": "ES=F",    "dji_otc": "YM=F",
    # Crypto
    "btcusd_otc": "BTC-USD",  "ethusd_otc": "ETH-USD",
    "etcusd_otc": "ETC-USD",  "ltcusd_otc": "LTC-USD",
    "bchusd_otc": "BCH-USD",  "bnbusd_otc": "BNB-USD",
    "solusd_otc": "SOL-USD",  "avaxusd_otc": "AVAX-USD",
    "dotusd_otc": "DOT-USD",  "linkusd_otc": "LINK-USD",
    "dashusd_otc": "DASH-USD","tonusd_otc": "TON11419-USD",
    "xrpusd_otc": "XRP-USD",  "adausd_otc": "ADA-USD",
    "maticusd_otc": "MATIC-USD",
    # Stocks
    "aapl_otc": "AAPL",  "amzn_otc": "AMZN", "tsla_otc": "TSLA",
    "googl_otc": "GOOGL","msft_otc": "MSFT",  "meta_otc": "META",
    "nflx_otc": "NFLX",  "nvda_otc": "NVDA",  "baba_otc": "BABA",
    "jnj_otc":  "JNJ",   "pfe_otc":  "PFE",   "ba_otc":   "BA",
    "mcd_otc":  "MCD",   "intc_otc": "INTC",  "amex_otc": "AXP",
    "csco_otc": "CSCO",  "v_otc":    "V",      "ma_otc":   "MA",
    "dis_otc":  "DIS",   "ibm_otc":  "IBM",   "fb_otc":   "META",
}

# yfinance OTC poll interval (seconds) — only runs to seed pairs with ZERO broker data ever
_YF_POLL_INTERVAL = 8
# yfinance is NEVER allowed to overwrite a broker price (source=qx/po), regardless of age.
# It may only seed a pair that has had NO broker tick at all (source absent or source=yf only).
# This constant is kept for reference but the loop now uses source-based gating, not age.
_YF_REPLACE_AGE   = 9999999   # effectively infinite — source check is the real gate


async def _yf_otc_poll_loop():
    """Poll yfinance for OTC pairs that have NEVER received a real broker tick.

    CRITICAL RULE: yfinance gives REAL-MARKET prices. Broker OTC prices are
    SYNTHETIC — completely broker-defined, disconnected from real markets.
    They can differ by 5-15%+ (e.g. yfinance=17.33, Quotex OTC=19.37).
    Using yfinance prices for OTC signals causes wrong entry points → losing trades.

    This loop ONLY writes yfinance data for pairs where source is absent or
    already "yf" — it NEVER overwrites a broker tick (source="qx" or "po"),
    regardless of how old that broker tick is.  A 10-minute-old broker price
    is infinitely more accurate for an OTC synthetic pair than a fresh
    yfinance real-market price.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[otc_svc:yf] yfinance not installed — yf OTC fallback disabled")
        return

    logger.info("[otc_svc:yf] yfinance OTC fallback poller started (broker-safe mode)")
    while True:
        try:
            # Only update pairs that have NO broker data at all (no entry, or existing source == "yf")
            # Never touch pairs that have a real qx/po broker tick.
            with _LOCK:
                need_update = [
                    k for k in _YF_OTC_MAP
                    if k not in _PRICES
                    or _PRICES[k].get("source") == "yf"
                ]

            # Process in batches of 20 to avoid Yahoo rate-limit
            for i in range(0, len(need_update), 20):
                batch_keys    = need_update[i: i + 20]
                batch_tickers = [_YF_OTC_MAP[k] for k in batch_keys]
                try:
                    raw = yf.download(
                        batch_tickers,
                        period="1d", interval="1m",
                        progress=False, auto_adjust=True,
                        threads=False,
                    )
                    if raw is not None and len(raw) > 0:
                        try:
                            closes = raw["Close"]
                        except KeyError:
                            closes = raw.get("close", raw)
                        if closes is not None and len(closes) > 0:
                            last_row = closes.iloc[-1]
                            for key, ticker in zip(batch_keys, batch_tickers):
                                # Double-check: still no broker tick before writing yf price
                                with _LOCK:
                                    cur = _PRICES.get(key)
                                if cur and cur.get("source") in ("qx", "po"):
                                    continue   # broker data arrived — skip yfinance write
                                try:
                                    px = float(last_row[ticker] if ticker in last_row.index
                                               else last_row.iloc[batch_tickers.index(ticker)])
                                    if px > 0:
                                        _write_price(key, px, "yf")
                                except Exception:
                                    pass
                except Exception:
                    # Fall back to individual Ticker calls on batch failure
                    for key, ticker in zip(batch_keys, batch_tickers):
                        with _LOCK:
                            cur = _PRICES.get(key)
                        if cur and cur.get("source") in ("qx", "po"):
                            continue   # broker data present — never overwrite
                        try:
                            t   = yf.Ticker(ticker)
                            fi  = t.fast_info
                            px  = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                            if px and float(px) > 0:
                                _write_price(key, float(px), "yf")
                        except Exception:
                            pass
                await asyncio.sleep(0.5)   # brief pause between batches
        except Exception as exc:
            logger.debug(f"[otc_svc:yf] poll error: {exc}")
        await asyncio.sleep(_YF_POLL_INTERVAL)


# ── Stooq real-time forex OTC bridge ─────────────────────────────────────────
# Maps OTC pair keys (normalised) to their Stooq forex symbols.
# Only forex-based OTC pairs — metals/crypto/stocks are handled by other feeds.
_STOOQ_OTC_MAP: Dict[str, str] = {
    # Major forex
    "eurusd_otc": "eurusd",  "gbpusd_otc": "gbpusd",
    "usdjpy_otc": "usdjpy",  "usdchf_otc": "usdchf",
    "usdcad_otc": "usdcad",  "audusd_otc": "audusd",
    "nzdusd_otc": "nzdusd",
    # Minor / cross
    "audcad_otc": "audcad",  "audchf_otc": "audchf",
    "audjpy_otc": "audjpy",  "audnzd_otc": "audnzd",
    "cadchf_otc": "cadchf",  "cadjpy_otc": "cadjpy",
    "chfjpy_otc": "chfjpy",  "euraud_otc": "euraud",
    "eurcad_otc": "eurcad",  "eurchf_otc": "eurchf",
    "eurgbp_otc": "eurgbp",  "eurjpy_otc": "eurjpy",
    "eurnzd_otc": "eurnzd",  "gbpaud_otc": "gbpaud",
    "gbpcad_otc": "gbpcad",  "gbpchf_otc": "gbpchf",
    "gbpjpy_otc": "gbpjpy",  "gbpnzd_otc": "gbpnzd",
    "nzdcad_otc": "nzdcad",  "nzdchf_otc": "nzdchf",
    "nzdjpy_otc": "nzdjpy",
    # Exotic (Stooq availability varies — guarded by try/except)
    "usdmxn_otc": "usdmxn",  "usdtry_otc": "usdtry",
    "usdzar_otc": "usdzar",  "usdinr_otc": "usdinr",
}

_STOOQ_OTC_POLL_INTERVAL = 3   # seconds — much faster than yfinance (8 s)


async def _stooq_otc_poll_loop():
    """Poll Stooq.com every 3 seconds for forex OTC pairs when broker WS is down.

    This is a real-time bridge:
    • Stooq provides genuine live mid-prices for all major/minor forex pairs.
    • Only writes for pairs whose source is absent, "stooq", or "yf"
      (never overwrites a fresh broker tick from QX or PO).
    • Broker tick (qx/po) younger than 60s → skip (broker is alive, don't touch it).
    • Tagged source = "stooq" so get_live_otc_price() accepts it with a 15 s TTL.
    """
    import urllib.request as _ur
    import json as _json

    def _stooq_fetch(sym: str) -> Optional[float]:
        try:
            url = f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlc&h&e=json"
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (SupremePro)"})
            with _ur.urlopen(req, timeout=3) as r:
                d = _json.loads(r.read().decode("utf-8", "ignore"))
            row = (d.get("symbols") or [{}])[0]
            px = float(row.get("close", 0))
            return px if px > 0 else None
        except Exception:
            return None

    logger.info("[otc_svc:stooq] Stooq OTC bridge started — polling every 3 s")
    while True:
        try:
            now = time.time()
            for key, stooq_sym in _STOOQ_OTC_MAP.items():
                # Skip if broker (qx/po) gave a fresh tick within 60 s
                with _LOCK:
                    existing = _PRICES.get(key)
                if existing:
                    src = existing.get("source", "")
                    age = now - existing.get("time", 0)
                    if src in ("qx", "po") and age < 60:
                        continue   # broker is alive — don't overwrite with Stooq

                # Fetch Stooq price in executor to avoid blocking the event loop
                px = await asyncio.get_event_loop().run_in_executor(
                    None, _stooq_fetch, stooq_sym
                )
                if px and px > 0:
                    _write_price(key, px, "stooq")
                # Small pause between requests to be polite to Stooq
                await asyncio.sleep(0.05)
        except Exception as exc:
            logger.debug(f"[otc_svc:stooq] poll error: {exc}")
        await asyncio.sleep(_STOOQ_OTC_POLL_INTERVAL)


async def run_otc_price_service():
    """Start both OTC price streams as concurrent tasks.
    Call once from bot.py:  asyncio.create_task(run_otc_price_service())

    Each stream runs in its own task so a QX crash never kills PO/yf.
    """
    logger.info("[otc_svc] Starting unified OTC price service …")
    logger.info(f"[otc_svc] QX pairs: {len(_QX_OTC_PAIRS)}  |  PO pairs: {len(_PO_OTC_PAIRS)}")

    # ── Isolated QX wrapper — never propagates exceptions upward ──────────────
    async def _qx_safe():
        while True:
            try:
                await _run_qx_loop()
            except Exception as exc:
                logger.error(f"[otc_svc:qx] Fatal error — restarting in 30s: {exc}")
                await asyncio.sleep(30)

    qx_task     = asyncio.create_task(_qx_safe(),              name="otc-qx-stream")
    po_task     = asyncio.create_task(_run_po_loop(),          name="otc-po-stream")
    sync_task   = asyncio.create_task(_sync_from_po_candles(), name="otc-po-candle-sync")
    yf_task     = asyncio.create_task(_yf_otc_poll_loop(),     name="otc-yf-fallback")
    stooq_task  = asyncio.create_task(_stooq_otc_poll_loop(),  name="otc-stooq-bridge")

    # Register tasks with Agent-3 (PriceFundAgent) so it can cancel/restart them
    try:
        from ai_guardian import _register_stream_task
        _register_stream_task("otc-qx-stream", qx_task)
        _register_stream_task("otc-po-stream", po_task)
    except Exception:
        pass

    # Status log every 5 minutes
    async def _status_loop():
        while True:
            await asyncio.sleep(300)
            s = get_otc_status()
            stooq_count = sum(
                1 for v in _PRICES.values()
                if v.get("source") == "stooq" and time.time() - v["time"] < 15
            )
            logger.info(
                f"[otc_svc] Live prices — QX: {s['qx']}  PO: {s['po']}  "
                f"Stooq bridge: {stooq_count}  Total: {s['qx'] + s['po'] + stooq_count}"
            )

    asyncio.create_task(_status_loop(), name="otc-status")

    # PO/yf/sync/stooq are the critical paths — QX is already isolated above
    await asyncio.gather(po_task, sync_task, yf_task, stooq_task)
