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
import time
from threading import Lock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Credentials ──────────────────────────────────────────────────────────────
QX_EMAIL    = os.environ.get("QUOTEX_EMAIL",    "hosnaranupur@gmail.com")
QX_PASSWORD = os.environ.get("QUOTEX_PASSWORD", "hosnaranupur@")

PO_SSID = os.environ.get(
    "PO_SSID",
    "g.a000-Ai13jQkQYD2vmUfrl1__ykff4-QQiOqs6vH3QY6NCDqJsjyyW5gw61CrJei5KRzT8h1rwACgYKAXESARQSFQHGX2MidDQ1Nyi9mQ0B9CaMyNyTdhoVAUF8yKqW_DYkz9SHvUGWIEH008bP0076"
)

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


def get_live_otc_price(pair: str) -> Optional[float]:
    """Return the freshest live OTC price for a bot pair label.

    Priority:
      1. Entry younger than _PRICE_MAX_AGE (3s)  → guaranteed fresh tick
      2. Entry younger than _PRICE_STALE_AGE (90s) → recent enough for signal accuracy
      3. None — no data at all (first start / both streams down)

    Returning a 10-60s old tick is far more accurate than falling back to
    yfinance which gives the REAL-market price, not the broker's synthetic OTC price.
    """
    key = _normalize_pair(pair)
    with _LOCK:
        entry = _PRICES.get(key)
    if not entry:
        return None
    age = time.time() - entry["time"]
    if age < _PRICE_STALE_AGE:
        return entry["price"]
    return None


def get_otc_status() -> Dict[str, int]:
    """Return {"qx": N, "po": N, "yf": N} count of live prices per source."""
    now = time.time()
    with _LOCK:
        qx = sum(1 for v in _PRICES.values()
                 if v.get("source") == "qx" and now - v["time"] < _PRICE_MAX_AGE)
        po = sum(1 for v in _PRICES.values()
                 if v.get("source") == "po" and now - v["time"] < _PRICE_MAX_AGE)
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


# ── Quotex pyquotex stream ────────────────────────────────────────────────────

async def _qx_stream_once():
    """Single Quotex WebSocket session — subscribes all OTC pairs, reads ticks."""
    try:
        from pyquotex.stable_api import Quotex
    except ImportError:
        try:
            from quotexapi.stable_api import Quotex
        except ImportError:
            logger.error("[otc_svc:qx] pyquotex not installed — QX stream disabled")
            await asyncio.sleep(3600)
            return

    client = Quotex(email=QX_EMAIL, password=QX_PASSWORD, lang="en")
    logger.info(f"[otc_svc:qx] Connecting as {QX_EMAIL} …")
    connected, reason = await client.connect()
    if not connected:
        raise ConnectionError(f"QX connect failed: {reason}")
    logger.info("[otc_svc:qx] Connected — subscribing pairs …")

    asset_map: Dict[str, str] = {}
    for i in range(0, len(_QX_OTC_PAIRS), _QX_BATCH):
        batch = _QX_OTC_PAIRS[i: i + _QX_BATCH]
        for pair in batch:
            try:
                asset, info = await client.get_available_asset(pair, force_open=True)
                if info and info[2]:
                    await client.start_candles_stream(asset, _CANDLE_PERIOD)
                    asset_map[pair] = asset
            except Exception:
                pass
        await asyncio.sleep(_QX_BATCH_DELAY)

    logger.info(f"[otc_svc:qx] Subscribed {len(asset_map)}/{len(_QX_OTC_PAIRS)} pairs — streaming")

    try:
        while True:
            await asyncio.sleep(0.25)   # poll 4× per second for real-time accuracy
            try:
                rt = client.api.realtime_price
            except Exception:
                break
            for pair, asset in asset_map.items():
                try:
                    ticks = rt.get(asset)
                    if not ticks:
                        continue
                    latest = ticks[-1]
                    for k in ("price", "close", "bid", "ask"):
                        v = latest.get(k)
                        if v is not None:
                            fv = float(v)
                            if fv > 0:
                                _write_price(asset, fv, "qx")
                                # Also write normalised key for cross-lookup
                                _write_price(_normalize_pair(asset), fv, "qx")
                                break
                except Exception:
                    pass
    finally:
        try:
            for asset in asset_map.values():
                await client.stop_candles_stream(asset)
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass


async def _run_qx_loop():
    """Auto-reconnecting Quotex price stream."""
    delay = _QX_RECONNECT
    while True:
        try:
            await _qx_stream_once()
        except ConnectionError as exc:
            logger.error(f"[otc_svc:qx] Connection error: {exc}")
            logger.info(f"[otc_svc:qx] Retrying in {delay}s …")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
        except Exception as exc:
            logger.warning(f"[otc_svc:qx] Stream error: {exc} — reconnecting in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
        else:
            delay = _QX_RECONNECT


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
    "maticusd_otc": "POL-USD",
    # Stocks
    "aapl_otc": "AAPL",  "amzn_otc": "AMZN", "tsla_otc": "TSLA",
    "googl_otc": "GOOGL","msft_otc": "MSFT",  "meta_otc": "META",
    "nflx_otc": "NFLX",  "nvda_otc": "NVDA",  "baba_otc": "BABA",
    "jnj_otc":  "JNJ",   "pfe_otc":  "PFE",   "ba_otc":   "BA",
    "mcd_otc":  "MCD",   "intc_otc": "INTC",  "amex_otc": "AXP",
    "csco_otc": "CSCO",  "v_otc":    "V",      "ma_otc":   "MA",
    "dis_otc":  "DIS",   "ibm_otc":  "IBM",   "fb_otc":   "META",
}

# yfinance OTC poll interval (seconds) — keeps prices live when broker streams down
_YF_POLL_INTERVAL = 10
# How stale a price must be before yf replaces it (don't overwrite fresh broker ticks)
_YF_REPLACE_AGE   = 30


async def _yf_otc_poll_loop():
    """Poll yfinance for OTC pair prices every 10s as a real-time fallback.

    Fills _PRICES for any pair that is missing or older than 30s so that
    get_live_otc_price() always returns a valid price even when both QX and
    PO WebSocket streams are down.  Fresh broker ticks (< 30s) are never
    overwritten so broker data always takes priority.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[otc_svc:yf] yfinance not installed — yf OTC fallback disabled")
        return

    logger.info("[otc_svc:yf] yfinance OTC fallback poller started")
    while True:
        try:
            now = time.time()
            # Collect pairs that need a yfinance update
            with _LOCK:
                need_update = [
                    k for k in _YF_OTC_MAP
                    if k not in _PRICES
                    or (now - _PRICES[k].get("time", 0)) > _YF_REPLACE_AGE
                ]

            # Process in batches of 20 to avoid Yahoo rate-limit
            for i in range(0, len(need_update), 20):
                batch_keys   = need_update[i: i + 20]
                batch_tickers = [_YF_OTC_MAP[k] for k in batch_keys]
                try:
                    raw = yf.download(
                        batch_tickers,
                        period="1d", interval="1m",
                        progress=False, auto_adjust=True,
                        threads=False,
                    )
                    if raw is not None and len(raw) > 0:
                        # Multi-ticker download returns MultiIndex columns
                        try:
                            closes = raw["Close"]
                        except KeyError:
                            closes = raw.get("close", raw)
                        if closes is not None and len(closes) > 0:
                            last_row = closes.iloc[-1]
                            for key, ticker in zip(batch_keys, batch_tickers):
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


async def run_otc_price_service():
    """Start both OTC price streams as concurrent tasks.
    Call once from bot.py:  asyncio.create_task(run_otc_price_service())
    """
    logger.info("[otc_svc] Starting unified OTC price service …")
    logger.info(f"[otc_svc] QX pairs: {len(_QX_OTC_PAIRS)}  |  PO pairs: {len(_PO_OTC_PAIRS)}")

    qx_task   = asyncio.create_task(_run_qx_loop(),          name="otc-qx-stream")
    po_task   = asyncio.create_task(_run_po_loop(),          name="otc-po-stream")
    sync_task = asyncio.create_task(_sync_from_po_candles(), name="otc-po-candle-sync")
    yf_task   = asyncio.create_task(_yf_otc_poll_loop(),     name="otc-yf-fallback")

    # Status log every 5 minutes
    async def _status_loop():
        while True:
            await asyncio.sleep(300)
            s = get_otc_status()
            logger.info(
                f"[otc_svc] Live prices — QX: {s['qx']}  PO: {s['po']}  "
                f"Total: {s['qx'] + s['po']}"
            )

    asyncio.create_task(_status_loop(), name="otc-status")

    # Wait for all (they run forever / auto-reconnect)
    await asyncio.gather(qx_task, po_task, sync_task, yf_task)
