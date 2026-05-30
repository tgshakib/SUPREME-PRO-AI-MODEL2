"""Pocket Option WebSocket Client
================================
Connects to Pocket Option's live Socket.IO API to stream real OTC candle data.
Uses EIO=4 Socket.IO protocol over WebSocket — no login form required, only SSID.

HOW TO GET YOUR SSID:
  1. Log into Pocket Option in your browser
  2. Press F12 → Application → Cookies → find the cookie named "token" or "_ga" session
  3. Or: F12 → Network → any request → Headers → find "Cookie:" value
  4. Set it as environment variable:  PO_SSID=your_ssid_value
  5. Restart the bot

When SSID is set: streams real PO OTC candles → highest accuracy (no mirror needed)
When SSID not set: module stays silent, engine falls back to yfinance + PO mirror
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PO_WS_URL = "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket"
_CANDLE_BUFFER = 200  # candles stored per (asset, period)
_RECONNECT_DELAY = 30  # seconds between reconnect attempts
_PERIODS = [60, 300, 900]  # 1m, 5m, 15m — fetched on connect

_PAIR_TO_ASSET: Dict[str, str] = {
    # ── Major Forex OTC ───────────────────────────────────────────────────────
    "eurusd":       "EURUSD_otc",
    "gbpusd":       "GBPUSD_otc",
    "usdjpy":       "USDJPY_otc",
    "usdchf":       "USDCHF_otc",
    "usdcad":       "USDCAD_otc",
    "audusd":       "AUDUSD_otc",
    "nzdusd":       "NZDUSD_otc",
    # ── Minor / Cross Forex OTC ───────────────────────────────────────────────
    "audcad":       "AUDCAD_otc",
    "audchf":       "AUDCHF_otc",
    "audjpy":       "AUDJPY_otc",
    "audnzd":       "AUDNZD_otc",
    "cadchf":       "CADCHF_otc",
    "cadjpy":       "CADJPY_otc",
    "chfjpy":       "CHFJPY_otc",
    "euraud":       "EURAUD_otc",
    "eurcad":       "EURCAD_otc",
    "eurchf":       "EURCHF_otc",
    "eurgbp":       "EURGBP_otc",
    "eurjpy":       "EURJPY_otc",
    "eurnzd":       "EURNZD_otc",
    "gbpaud":       "GBPAUD_otc",
    "gbpcad":       "GBPCAD_otc",
    "gbpchf":       "GBPCHF_otc",
    "gbpjpy":       "GBPJPY_otc",
    "gbpnzd":       "GBPNZD_otc",
    "nzdcad":       "NZDCAD_otc",
    "nzdchf":       "NZDCHF_otc",
    "nzdjpy":       "NZDJPY_otc",
    # ── Exotic / EM Forex OTC ─────────────────────────────────────────────────
    "usdmxn":       "USDMXN_otc",
    "usdinr":       "USDINR_otc",
    "usdbrl":       "USDBRL_otc",
    "usdcop":       "USDCOP_otc",
    "usdars":       "USDARS_otc",
    "usdpkr":       "USDPKR_otc",
    "usdngn":       "USDNGN_otc",
    "usdegp":       "USDEGP_otc",
    "usdidr":       "USDIDR_otc",
    "usdphp":       "USDPHP_otc",
    "usdzar":       "USDZAR_otc",
    "usdbdt":       "USDBDT_otc",
    "usddzd":       "USDDZD_otc",
    # ── Metals OTC ────────────────────────────────────────────────────────────
    "xauusd":       "XAUUSD_otc",
    "gold":         "XAUUSD_otc",
    "xagusd":       "XAGUSD_otc",
    "silver":       "XAGUSD_otc",
    # ── Energy OTC ────────────────────────────────────────────────────────────
    "usoil":        "USOIL_otc",
    "uscrude":      "USOIL_otc",
    "brent":        "BRENT_otc",
    "ukbrent":      "BRENT_otc",
    # ── Indices OTC ───────────────────────────────────────────────────────────
    "nas100":       "NQ_otc",
    "us100":        "NQ_otc",
    "spx500":       "SP_otc",
    "sp500":        "SP_otc",
    "dji30":        "DJI_otc",
    "us30":         "DJI_otc",
    # ── Crypto OTC ────────────────────────────────────────────────────────────
    "bitcoin":      "BTCUSD_otc",
    "btcusd":       "BTCUSD_otc",
    "btcusdt":      "BTCUSD_otc",
    "ethereum":     "ETHUSD_otc",
    "ethusd":       "ETHUSD_otc",
    "ethusdt":      "ETHUSD_otc",
    "litecoin":     "LTCUSD_otc",
    "ltcusd":       "LTCUSD_otc",
    "bitcoincash":  "BCHUSD_otc",
    "bchusd":       "BCHUSD_otc",
    "ethereumclassic": "ETCUSD_otc",
    "etcusd":       "ETCUSD_otc",
    "binancecoin":  "BNBUSD_otc",
    "bnbusd":       "BNBUSD_otc",
    "solana":       "SOLUSD_otc",
    "solusd":       "SOLUSD_otc",
    "avalanche":    "AVAXUSD_otc",
    "avaxusd":      "AVAXUSD_otc",
    "polkadot":     "DOTUSD_otc",
    "dotusd":       "DOTUSD_otc",
    "chainlink":    "LINKUSD_otc",
    "linkusd":      "LINKUSD_otc",
    "dash":         "DASHUSD_otc",
    "dashusd":      "DASHUSD_otc",
    "axieinfinity": "AXSUSD_otc",
    "axsusd":       "AXSUSD_otc",
    "toncoin":      "TONUSD_otc",
    "tonusd":       "TONUSD_otc",
    "ripple":       "XRPUSD_otc",
    "xrpusd":       "XRPUSD_otc",
    "cardano":      "ADAUSD_otc",
    "adausd":       "ADAUSD_otc",
    "polygon":      "MATICUSD_otc",
    "maticusd":     "MATICUSD_otc",
    "trump":        "TRUMPUSD_otc",
    "trumpusd":     "TRUMPUSD_otc",
    # ── Stocks OTC ────────────────────────────────────────────────────────────
    "apple":        "AAPL_otc",
    "aapl":         "AAPL_otc",
    "amazon":       "AMZN_otc",
    "amzn":         "AMZN_otc",
    "tesla":        "TSLA_otc",
    "tsla":         "TSLA_otc",
    "google":       "GOOGL_otc",
    "googl":        "GOOGL_otc",
    "microsoft":    "MSFT_otc",
    "msft":         "MSFT_otc",
    "facebook":     "META_otc",
    "meta":         "META_otc",
    "netflix":      "NFLX_otc",
    "nflx":         "NFLX_otc",
    "nvidia":       "NVDA_otc",
    "nvda":         "NVDA_otc",
    "alibaba":      "BABA_otc",
    "baba":         "BABA_otc",
    "johnson":      "JNJ_otc",
    "jnj":          "JNJ_otc",
    "pfizer":       "PFE_otc",
    "pfe":          "PFE_otc",
    "boeing":       "BA_otc",
    "ba":           "BA_otc",
    "mcdonalds":    "MCD_otc",
    "mcd":          "MCD_otc",
    "intel":        "INTC_otc",
    "intc":         "INTC_otc",
    "americanexpress": "AMEX_otc",
    "amex":         "AMEX_otc",
    "cisco":        "CSCO_otc",
    "csco":         "CSCO_otc",
    "visa":         "V_otc",
    "mastercard":   "MA_otc",
    "disney":       "DIS_otc",
    "dis":          "DIS_otc",
    "ibm":          "IBM_otc",
}


def pair_to_po_asset(pair: str) -> Optional[str]:
    """Map a bot pair name like 'EUR/USD 〔OTC〕' to a PO asset name."""
    clean = re.sub(r"[^a-zA-Z0-9]", "", pair).lower()
    clean = re.sub(r"otc$", "", clean)
    for key, asset in _PAIR_TO_ASSET.items():
        if key in clean:
            return asset
    return None


class PocketOptionWS:
    """Thread-safe Pocket Option WebSocket client."""

    def __init__(self):
        self._ssid: str = os.environ.get("PO_SSID", "").strip()
        self._candles: Dict[Tuple[str, int], deque] = defaultdict(
            lambda: deque(maxlen=_CANDLE_BUFFER)
        )
        self._connected = False
        self._authenticated = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        if not self._ssid:
            logger.info("[po_ws] PO_SSID not set — running without live PO data")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="PO-WS")
        self._thread.start()
        logger.info("[po_ws] WebSocket thread started")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._reconnect_loop())

    async def _reconnect_loop(self):
        while True:
            try:
                await self._connect()
            except Exception as exc:
                logger.warning(f"[po_ws] Connection lost: {exc} — retrying in {_RECONNECT_DELAY}s")
            finally:
                self._connected = False
                self._authenticated = False
            await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect(self):
        try:
            import websockets as _ws
        except ImportError:
            logger.error("[po_ws] websockets library not installed — cannot connect")
            await asyncio.sleep(3600)
            return

        logger.info(f"[po_ws] Connecting to {_PO_WS_URL}")
        _hdrs = {
            "Origin": "https://po.trade",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Cookie": f"ssid={self._ssid}",
        }
        async with _ws.connect(
            _PO_WS_URL,
            additional_headers=_hdrs,
            ping_interval=None,
            close_timeout=10,
        ) as ws:
            self._ws = ws
            await self._handshake(ws)

    async def _handshake(self, ws):
        ping_task = None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            raw = str(raw)
            if raw.startswith("0"):
                data = json.loads(raw[1:])
                ping_interval = data.get("pingInterval", 25000) / 1000
                ping_task = asyncio.create_task(self._ping_loop(ws, ping_interval))
                await ws.send("40")
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                if str(resp).startswith("40"):
                    await self._authenticate(ws)
                    await self._message_loop(ws)
        finally:
            if ping_task:
                ping_task.cancel()

    async def _authenticate(self, ws):
        auth_msg = json.dumps(["auth", {"session": self._ssid, "isDemo": 0}])
        await ws.send(f"42{auth_msg}")
        resp = await asyncio.wait_for(ws.recv(), timeout=15)
        resp_str = str(resp)
        if "successauth" in resp_str or "authSuccess" in resp_str:
            self._connected = True
            self._authenticated = True
            logger.info("[po_ws] Authenticated successfully")
            await self._subscribe_all(ws)
        elif "failauth" in resp_str or "error" in resp_str.lower():
            logger.error(f"[po_ws] Auth failed: {resp_str[:200]}")
            raise ConnectionError("PO auth failed — check PO_SSID")

    async def _subscribe_all(self, ws):
        assets = list(set(_PAIR_TO_ASSET.values()))
        for asset in assets:
            for period in _PERIODS:
                sub = json.dumps(["subscribeSymbol", {"asset": asset, "period": period}])
                await ws.send(f"42{sub}")
                await asyncio.sleep(0.08)   # small delay to avoid WS flood
        hist_time = int(time.time())
        for asset in assets:
            hist = json.dumps(["loadHistoryPeriod", {
                "asset": asset, "period": 60,
                "time": hist_time, "index": 0, "count": 200
            }])
            await ws.send(f"42{hist}")
            await asyncio.sleep(0.1)

    async def _message_loop(self, ws):
        async for raw in ws:
            raw = str(raw)
            if raw == "2":
                await ws.send("3")
                continue
            if not raw.startswith("42"):
                continue
            try:
                payload = json.loads(raw[2:])
                if not isinstance(payload, list) or len(payload) < 2:
                    continue
                event, data = payload[0], payload[1]
                if event in ("newcandle", "candle"):
                    self._on_candle(data)
                elif event == "candles":
                    self._on_candles_batch(data)
            except Exception:
                pass

    def _on_candle(self, data: dict):
        asset = data.get("asset", "")
        period = int(data.get("period", 60))
        candle = {
            "time":   int(data.get("time", 0)),
            "open":   float(data.get("open", 0)),
            "high":   float(data.get("high", 0)),
            "low":    float(data.get("low", 0)),
            "close":  float(data.get("close", 0)),
            "volume": float(data.get("volume", 0)),
        }
        with self._lock:
            self._candles[(asset, period)].append(candle)

    def _on_candles_batch(self, data: dict):
        asset = data.get("asset", "")
        period = int(data.get("period", 60))
        candle_list = data.get("candles", [])
        with self._lock:
            buf = self._candles[(asset, period)]
            for c in candle_list:
                buf.append({
                    "time":   int(c.get("time", 0)),
                    "open":   float(c.get("open", 0)),
                    "high":   float(c.get("high", 0)),
                    "low":    float(c.get("low", 0)),
                    "close":  float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                })

    async def _ping_loop(self, ws, interval: float):
        while True:
            await asyncio.sleep(interval)
            try:
                await ws.send("2")
            except Exception:
                break

    def get_candles(self, pair: str, period: int = 60) -> List[dict]:
        asset = pair_to_po_asset(pair)
        if not asset:
            return []
        with self._lock:
            return list(self._candles.get((asset, period), []))

    def is_connected(self) -> bool:
        return self._connected and self._authenticated

    def has_ssid(self) -> bool:
        return bool(self._ssid)


_client = PocketOptionWS()


def start_ws():
    _client.start()


def get_candles(pair: str, period: int = 60) -> List[dict]:
    return _client.get_candles(pair, period)


def is_connected() -> bool:
    return _client.is_connected()


def has_ssid() -> bool:
    return _client.has_ssid()


def update_ssid(new_ssid: str):
    """Hot-swap the SSID and reconnect to Pocket Option with the fresh session.

    Called by po_auth.py whenever the auto-login manager obtains a new SSID.
    The current WebSocket connection is closed gracefully; the reconnect loop
    then re-authenticates with the new session cookie automatically.
    """
    if not new_ssid or new_ssid == _client._ssid:
        return
    logger.info("[po_ws] SSID updated by po_auth — reconnecting …")
    _client._ssid = new_ssid
    _client._connected = False
    _client._authenticated = False
    # Close the current WebSocket so the reconnect loop fires immediately
    try:
        ws = getattr(_client, "_ws", None)
        if ws is not None:
            loop = _client._loop
            if loop and loop.is_running():
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(ws.close()))
    except Exception as exc:
        logger.debug(f"[po_ws] Force-close on SSID update: {exc}")
    # Restart the thread if it died
    if not (_client._thread and _client._thread.is_alive()):
        start_ws()


start_ws()
