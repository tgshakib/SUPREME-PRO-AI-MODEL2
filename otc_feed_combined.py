"""
otc_feed_combined.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pocket Option + Quotex OTC WebSocket feed.
Primary  : QX + PO direct broker WebSocket
Fallback : existing bot system (untouched)
Signal text : NEVER modified.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW TO USE IN YOUR bot.py
━━━━━━━━━━━━━━━━━━━━━━━━━━
from otc_feed_combined import otc_feed, ALL_OTC_PAIRS, ALL_TIMEFRAMES, label_to_otc_key

async def on_startup():
    otc_feed.subscribe_all(ALL_OTC_PAIRS, ALL_TIMEFRAMES)
    otc_feed.start()

# Inside your signal function — replace old candle source:
candles = otc_feed.get_candles("EURUSD-OTC", "5m", count=100)
if candles is None:
    candles = your_existing_fallback("EURUSD-OTC", "5m")  # untouched fallback

price  = otc_feed.get_price("EURUSD-OTC")
status = otc_feed.status()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import json
import time
import logging
import random
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

try:
    import websockets
except ImportError:
    raise ImportError("Run: pip install websockets>=12.0")

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# QUOTEX CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

QX_ENDPOINTS = [
    "wss://ws2.qxbroker.com/socket.io/?EIO=4&transport=websocket",
    "wss://ws3.qxbroker.com/socket.io/?EIO=4&transport=websocket",
    "wss://ws4.qxbroker.com/socket.io/?EIO=4&transport=websocket",
]

QX_HEADERS = {
    "Origin":          "https://qxbroker.com",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Bot's existing QX OTC pairs ONLY — no extras
QX_ASSET_MAP = {
    # ── Major Forex OTC ───────────────────────────────────────────────────────
    "EURUSD-OTC":   "EURUSD_otc",
    "GBPUSD-OTC":   "GBPUSD_otc",
    "USDJPY-OTC":   "USDJPY_otc",
    "AUDUSD-OTC":   "AUDUSD_otc",
    "USDCAD-OTC":   "USDCAD_otc",
    "USDCHF-OTC":   "USDCHF_otc",
    "NZDUSD-OTC":   "NZDUSD_otc",
    # ── Minor / Cross Forex OTC ───────────────────────────────────────────────
    "EURGBP-OTC":   "EURGBP_otc",
    "EURJPY-OTC":   "EURJPY_otc",
    "GBPJPY-OTC":   "GBPJPY_otc",
    "GBPAUD-OTC":   "GBPAUD_otc",
    "EURAUD-OTC":   "EURAUD_otc",
    "EURNZD-OTC":   "EURNZD_otc",
    "EURCAD-OTC":   "EURCAD_otc",
    "EURCHF-OTC":   "EURCHF_otc",
    "AUDJPY-OTC":   "AUDJPY_otc",
    "AUDCAD-OTC":   "AUDCAD_otc",
    "AUDCHF-OTC":   "AUDCHF_otc",
    "AUDNZD-OTC":   "AUDNZD_otc",
    "NZDJPY-OTC":   "NZDJPY_otc",
    "NZDCAD-OTC":   "NZDCAD_otc",
    "NZDCHF-OTC":   "NZDCHF_otc",
    "CADJPY-OTC":   "CADJPY_otc",
    "CADCHF-OTC":   "CADCHF_otc",
    "CHFJPY-OTC":   "CHFJPY_otc",
    "GBPCHF-OTC":   "GBPCHF_otc",
    "GBPCAD-OTC":   "GBPCAD_otc",
    "GBPNZD-OTC":   "GBPNZD_otc",
    # ── Exotic OTC (bot existing) ─────────────────────────────────────────────
    "USDMXN-OTC":   "USDMXN_otc",
    "USDZAR-OTC":   "USDZAR_otc",
    "USDARS-OTC":   "USDARS_otc",
    "USDBRL-OTC":   "USDBRL_otc",
    "USDCOP-OTC":   "USDCOP_otc",
    "USDIDR-OTC":   "USDIDR_otc",
    "USDINR-OTC":   "USDINR_otc",
    "USDPHP-OTC":   "USDPHP_otc",
    # ── Metals OTC ────────────────────────────────────────────────────────────
    "XAUUSD-OTC":   "XAUUSD_otc",
    "XAGUSD-OTC":   "XAGUSD_otc",
    # ── Energy OTC ────────────────────────────────────────────────────────────
    "UKBRENT-OTC":  "UKOIL_otc",
    "USCRUDE-OTC":  "USOIL_otc",
    # ── Crypto OTC ────────────────────────────────────────────────────────────
    "BTCUSD-OTC":   "BTCUSD_otc",
    "ETHUSD-OTC":   "ETHUSD_otc",
    "ETCUSD-OTC":   "ETCUSD_otc",
    "LTCUSD-OTC":   "LTCUSD_otc",
    "BCHUSD-OTC":   "BCHUSD_otc",
    "BNBUSD-OTC":   "BNBUSD_otc",
    "SOLUSD-OTC":   "SOLUSD_otc",
    "AVAXUSD-OTC":  "AVAXUSD_otc",
    "DOTUSD-OTC":   "DOTUSD_otc",
    "LINKUSD-OTC":  "LINKUSD_otc",
    "DASHUSD-OTC":  "DASHUSD_otc",
    "AXSUSD-OTC":   "AXSUSD_otc",
    "TONUSD-OTC":   "TONUSD_otc",
    "TRUMPUSD-OTC": "TRUMPUSD_otc",
}

# ═══════════════════════════════════════════════════════════════════════════════
# POCKET OPTION CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

PO_ENDPOINTS = [
    "wss://api-c.po.market/socket.io/?EIO=4&transport=websocket",
    "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket",
    "wss://api-s.po.market/socket.io/?EIO=4&transport=websocket",
]

PO_HEADERS = {
    "Origin":          "https://pocketoption.com",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Bot's existing PO OTC pairs ONLY — no extras
PO_ASSET_MAP = {
    # ── Major Forex OTC ───────────────────────────────────────────────────────
    "EURUSD-OTC":   "#EURUSD_otc",
    "GBPUSD-OTC":   "#GBPUSD_otc",
    "USDJPY-OTC":   "#USDJPY_otc",
    "AUDUSD-OTC":   "#AUDUSD_otc",
    "USDCAD-OTC":   "#USDCAD_otc",
    "USDCHF-OTC":   "#USDCHF_otc",
    "NZDUSD-OTC":   "#NZDUSD_otc",
    # ── Minor / Cross Forex OTC ───────────────────────────────────────────────
    "EURGBP-OTC":   "#EURGBP_otc",
    "EURJPY-OTC":   "#EURJPY_otc",
    "GBPJPY-OTC":   "#GBPJPY_otc",
    "GBPAUD-OTC":   "#GBPAUD_otc",
    "EURAUD-OTC":   "#EURAUD_otc",
    "EURNZD-OTC":   "#EURNZD_otc",
    "EURCAD-OTC":   "#EURCAD_otc",
    "EURCHF-OTC":   "#EURCHF_otc",
    "AUDJPY-OTC":   "#AUDJPY_otc",
    "AUDCAD-OTC":   "#AUDCAD_otc",
    "AUDCHF-OTC":   "#AUDCHF_otc",
    "AUDNZD-OTC":   "#AUDNZD_otc",
    "NZDJPY-OTC":   "#NZDJPY_otc",
    "NZDCAD-OTC":   "#NZDCAD_otc",
    "NZDCHF-OTC":   "#NZDCHF_otc",
    "CADJPY-OTC":   "#CADJPY_otc",
    "CADCHF-OTC":   "#CADCHF_otc",
    "CHFJPY-OTC":   "#CHFJPY_otc",
    "GBPCHF-OTC":   "#GBPCHF_otc",
    "GBPCAD-OTC":   "#GBPCAD_otc",
    "GBPNZD-OTC":   "#GBPNZD_otc",
    # ── Exotic OTC (bot existing) ─────────────────────────────────────────────
    "USDMXN-OTC":   "#USDMXN_otc",
    "USDZAR-OTC":   "#USDZAR_otc",
    "USDARS-OTC":   "#USDARS_otc",
    "USDBRL-OTC":   "#USDBRL_otc",
    "USDCOP-OTC":   "#USDCOP_otc",
    "USDIDR-OTC":   "#USDIDR_otc",
    "USDINR-OTC":   "#USDINR_otc",
    "USDPHP-OTC":   "#USDPHP_otc",
    # ── Metals OTC ────────────────────────────────────────────────────────────
    "XAUUSD-OTC":   "#XAUUSD_otc",
    "XAGUSD-OTC":   "#XAGUSD_otc",
    # ── Energy OTC ────────────────────────────────────────────────────────────
    "UKBRENT-OTC":  "#UKOIL_otc",
    "USCRUDE-OTC":  "#USOIL_otc",
    # ── Crypto OTC ────────────────────────────────────────────────────────────
    "BTCUSD-OTC":   "#BTCUSD_otc",
    "ETHUSD-OTC":   "#ETHUSD_otc",
    "ETCUSD-OTC":   "#ETCUSD_otc",
    "LTCUSD-OTC":   "#LTCUSD_otc",
    "BCHUSD-OTC":   "#BCHUSD_otc",
    "BNBUSD-OTC":   "#BNBUSD_otc",
    "SOLUSD-OTC":   "#SOLUSD_otc",
    "AVAXUSD-OTC":  "#AVAXUSD_otc",
    "DOTUSD-OTC":   "#DOTUSD_otc",
    "LINKUSD-OTC":  "#LINKUSD_otc",
    "DASHUSD-OTC":  "#DASHUSD_otc",
    "AXSUSD-OTC":   "#AXSUSD_otc",
    "TONUSD-OTC":   "#TONUSD_otc",
    "TRUMPUSD-OTC": "#TRUMPUSD_otc",
    # ── Stocks OTC (bot existing) ─────────────────────────────────────────────
    "AMEX-OTC":     "#AXP_otc",     # American Express
    "BA-OTC":       "#BA_otc",      # Boeing Company
    "FB-OTC":       "#META_otc",    # Facebook → Meta
    "INTC-OTC":     "#INTC_otc",    # Intel
    "JNJ-OTC":      "#JNJ_otc",     # Johnson & Johnson
    "MCD-OTC":      "#MCD_otc",     # McDonald's
    "PFE-OTC":      "#PFE_otc",     # Pfizer
}

# ═══════════════════════════════════════════════════════════════════════════════
# LABEL → OTC KEY  (backward-compat with existing bot display labels)
# ═══════════════════════════════════════════════════════════════════════════════

LABEL_TO_KEY: dict[str, Optional[str]] = {
    # Major Forex
    "EUR/USD 〔OTC〕":           "EURUSD-OTC",
    "GBP/USD 〔OTC〕":           "GBPUSD-OTC",
    "USD/JPY 〔OTC〕":           "USDJPY-OTC",
    "USD/CHF 〔OTC〕":           "USDCHF-OTC",
    "USD/CAD 〔OTC〕":           "USDCAD-OTC",
    "AUD/USD 〔OTC〕":           "AUDUSD-OTC",
    "NZD/USD 〔OTC〕":           "NZDUSD-OTC",
    # Minor / Cross
    "AUD/CAD 〔OTC〕":           "AUDCAD-OTC",
    "AUD/CHF 〔OTC〕":           "AUDCHF-OTC",
    "AUD/JPY 〔OTC〕":           "AUDJPY-OTC",
    "AUD/NZD 〔OTC〕":           "AUDNZD-OTC",
    "CAD/CHF 〔OTC〕":           "CADCHF-OTC",
    "CAD/JPY 〔OTC〕":           "CADJPY-OTC",
    "CHF/JPY 〔OTC〕":           "CHFJPY-OTC",
    "EUR/AUD 〔OTC〕":           "EURAUD-OTC",
    "EUR/CAD 〔OTC〕":           "EURCAD-OTC",
    "EUR/CHF 〔OTC〕":           "EURCHF-OTC",
    "EUR/GBP 〔OTC〕":           "EURGBP-OTC",
    "EUR/JPY 〔OTC〕":           "EURJPY-OTC",
    "EUR/NZD 〔OTC〕":           "EURNZD-OTC",
    "GBP/AUD 〔OTC〕":           "GBPAUD-OTC",
    "GBP/CAD 〔OTC〕":           "GBPCAD-OTC",
    "GBP/CHF 〔OTC〕":           "GBPCHF-OTC",
    "GBP/JPY 〔OTC〕":           "GBPJPY-OTC",
    "GBP/NZD 〔OTC〕":           "GBPNZD-OTC",
    "NZD/CAD 〔OTC〕":           "NZDCAD-OTC",
    "NZD/CHF 〔OTC〕":           "NZDCHF-OTC",
    "NZD/JPY 〔OTC〕":           "NZDJPY-OTC",
    # Exotics (TD-supported)
    "USD/ARS 〔OTC〕":           "USDARS-OTC",
    "USD/BRL 〔OTC〕":           "USDBRL-OTC",
    "USD/COP 〔OTC〕":           "USDCOP-OTC",
    "USD/IDR 〔OTC〕":           "USDIDR-OTC",
    "USD/INR 〔OTC〕":           "USDINR-OTC",
    "USD/MXN 〔OTC〕":           "USDMXN-OTC",
    "USD/PHP 〔OTC〕":           "USDPHP-OTC",
    "USD/ZAR 〔OTC〕":           "USDZAR-OTC",
    # Exotics (no broker WS — fallback only)
    "USD/BDT 〔OTC〕":           None,
    "USD/DZD 〔OTC〕":           None,
    "USD/EGP 〔OTC〕":           None,
    "USD/NGN 〔OTC〕":           None,
    "USD/PKR 〔OTC〕":           None,
    # Metals
    "Gold 〔OTC〕":              "XAUUSD-OTC",
    "Silver 〔OTC〕":            "XAGUSD-OTC",
    # Energy
    "UKBrent 〔OTC〕":           "UKBRENT-OTC",
    "USCrude 〔OTC〕":           "USCRUDE-OTC",
    # Crypto
    "Bitcoin 〔OTC〕":           "BTCUSD-OTC",
    "Ethereum 〔OTC〕":          "ETHUSD-OTC",
    "Ethereum Classic 〔OTC〕":  "ETCUSD-OTC",
    "Litecoin 〔OTC〕":          "LTCUSD-OTC",
    "Bitcoin Cash 〔OTC〕":      "BCHUSD-OTC",
    "Binance Coin 〔OTC〕":      "BNBUSD-OTC",
    "Solana 〔OTC〕":            "SOLUSD-OTC",
    "Avalanche 〔OTC〕":         "AVAXUSD-OTC",
    "Polkadot 〔OTC〕":          "DOTUSD-OTC",
    "Chainlink 〔OTC〕":         "LINKUSD-OTC",
    "Dash 〔OTC〕":              "DASHUSD-OTC",
    "Axie Infinity 〔OTC〕":     "AXSUSD-OTC",
    "Toncoin 〔OTC〕":           "TONUSD-OTC",
    "Trump 〔OTC〕":             "TRUMPUSD-OTC",
    # Stocks
    "American Express 〔OTC〕":  "AMEX-OTC",
    "Boeing Company 〔OTC〕":    "BA-OTC",
    "FACEBOOK INC 〔OTC〕":      "FB-OTC",
    "Intel 〔OTC〕":             "INTC-OTC",
    "Johnson Johnson 〔OTC〕":   "JNJ-OTC",
    "McDonald's 〔OTC〕":        "MCD-OTC",
    "Pfizer Inc 〔OTC〕":        "PFE-OTC",
}

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED TIMEFRAME MAP
# ═══════════════════════════════════════════════════════════════════════════════

TF_SECONDS = {
    "10s": 10,  "30s": 30,
    "1m":  60,  "3m":  180,  "5m":  300,
    "15m": 900, "30m": 1800,
    "1h":  3600, "4h": 14400,
    "1d":  86400, "1w": 604800,
}

ALL_OTC_PAIRS  = sorted(set(list(QX_ASSET_MAP.keys()) + list(PO_ASSET_MAP.keys())))
ALL_TIMEFRAMES = ["30s", "1m", "3m", "5m", "15m", "30m", "1h", "4h"]

# ═══════════════════════════════════════════════════════════════════════════════
# CANDLE BUILDER — tick stream → OHLCV candles
# ═══════════════════════════════════════════════════════════════════════════════

class CandleBuilder:
    def __init__(self, asset: str, tf_sec: int, on_close: Callable):
        self.asset    = asset
        self.tf_sec   = tf_sec
        self.on_close = on_close
        self._c       = None
        self._b       = None

    def _bucket(self, ts: float) -> float:
        return float(int(ts // self.tf_sec) * self.tf_sec)

    def push(self, price: float, ts: float):
        b = self._bucket(ts)
        if self._b is None:
            self._b = b
            self._c = [price, price, price, price, 0, b]
            return
        if b != self._b:
            asyncio.create_task(self.on_close({
                "time":   datetime.fromtimestamp(self._c[5], tz=timezone.utc)
                                  .strftime("%Y-%m-%d %H:%M:%S"),
                "open":   self._c[0],
                "high":   self._c[1],
                "low":    self._c[2],
                "close":  self._c[3],
                "volume": self._c[4],
            }))
            self._b = b
            self._c = [price, price, price, price, 1, b]
        else:
            self._c[1] = max(self._c[1], price)
            self._c[2] = min(self._c[2], price)
            self._c[3] = price
            self._c[4] += 1

# ═══════════════════════════════════════════════════════════════════════════════
# BASE BROKER FEED
# ═══════════════════════════════════════════════════════════════════════════════

class BrokerFeed:
    def __init__(self, name: str, endpoints: list, headers: dict, asset_map: dict):
        self.name       = name
        self._eps       = endpoints
        self._headers   = headers
        self._asset_map = asset_map
        self._cache:    dict[str, deque]         = {}
        self._builders: dict[str, CandleBuilder] = {}
        self._subs:     dict[str, set]           = {}
        self._ws        = None
        self._running   = False
        self._connected = False
        self._ep_idx    = 0

    def _key(self, asset: str, tf: str) -> str:
        return f"{asset.upper()}:{tf.lower()}"

    def _store(self, asset: str, tf: str, candle: dict):
        k = self._key(asset, tf)
        if k not in self._cache:
            self._cache[k] = deque(maxlen=500)
        self._cache[k].append(candle)

    def get_candles(self, asset: str, tf: str, count: int = 100) -> list[dict]:
        return list(self._cache.get(self._key(asset, tf), []))[-count:]

    def get_latest(self, asset: str, tf: str) -> Optional[dict]:
        c = self.get_candles(asset, tf, 1)
        return c[-1] if c else None

    def get_price(self, asset: str) -> Optional[float]:
        for tf in ["1m", "5m", "30s", "15m", "1h"]:
            c = self.get_latest(asset, tf)
            if c:
                return c["close"]
        return None

    def subscribe(self, asset: str, timeframes: list):
        sym = self._asset_map.get(asset.upper())
        if not sym:
            return
        if sym not in self._subs:
            self._subs[sym] = set()
        for tf in timeframes:
            tf_sec = TF_SECONDS.get(tf.lower())
            if not tf_sec:
                continue
            self._subs[sym].add(tf_sec)
            bkey  = f"{sym}:{tf_sec}"
            _a    = asset.upper()
            _tf   = tf.lower()

            async def _on_close(c, __a=_a, __tf=_tf):
                self._store(__a, __tf, c)

            self._builders[bkey] = CandleBuilder(
                asset=asset.upper(), tf_sec=tf_sec, on_close=_on_close
            )

    async def _send(self, msg: str):
        try:
            if self._ws:
                await self._ws.send(msg)
        except Exception:
            pass

    async def _route_tick(self, sym: str, price: float, ts: float):
        for bkey, builder in self._builders.items():
            bsym = bkey.split(":")[0].lower().replace("#", "")
            csym = sym.lower().replace("#", "")
            if bsym in csym or csym in bsym:
                builder.push(price, ts)

    async def _load_history(self, sym: str, tf_sec: int, candles: list):
        tf_label = next((k for k, v in TF_SECONDS.items() if v == tf_sec), f"{tf_sec}s")
        asset_label = next(
            (k for k, v in self._asset_map.items()
             if v.lower().replace("#", "") == sym.lower().replace("#", "")),
            sym.upper()
        )
        for c in candles:
            t = float(c.get("time") or c.get("t") or 0)
            self._store(asset_label, tf_label, {
                "time":   datetime.fromtimestamp(t, tz=timezone.utc)
                                  .strftime("%Y-%m-%d %H:%M:%S"),
                "open":   float(c.get("open")   or c.get("o") or 0),
                "high":   float(c.get("high")   or c.get("h") or 0),
                "low":    float(c.get("low")    or c.get("l") or 0),
                "close":  float(c.get("close")  or c.get("c") or 0),
                "volume": float(c.get("volume") or c.get("v") or 0),
            })
        logger.info(f"[{self.name}] History {asset_label} {tf_label}: {len(candles)} candles")

    async def _parse(self, raw: str):
        if raw == "2":
            await self._send("3")
            return
        if not raw.startswith("42"):
            return
        try:
            payload = json.loads(raw[2:])
        except Exception:
            return
        if not isinstance(payload, list) or len(payload) < 2:
            return

        event, data = payload[0], payload[1]

        if event in ("tick", "price/tick", "quote", "asset/tick", "price"):
            try:
                sym   = str(data.get("asset") or data.get("symbol") or data.get("s") or "")
                price = float(data.get("price") or data.get("p") or data.get("close") or 0)
                ts    = float(data.get("time")  or data.get("t") or time.time())
                if price and sym:
                    await self._route_tick(sym, price, ts)
            except Exception:
                pass

        elif event in ("candles/load", "history", "candles", "loadHistoryPeriod"):
            try:
                sym     = str(data.get("asset") or data.get("symbol") or "")
                tf_sec  = int(data.get("period") or data.get("tf") or 60)
                candles = (data.get("candles") or data.get("data")
                           or data.get("history") or [])
                if candles:
                    await self._load_history(sym, tf_sec, candles)
            except Exception:
                pass

        elif event in ("candle", "candle/close"):
            try:
                sym    = str(data.get("asset") or data.get("symbol") or "")
                tf_sec = int(data.get("period") or 60)
                await self._load_history(sym, tf_sec, [data])
            except Exception:
                pass

    async def _handshake(self):
        raise NotImplementedError

    async def _request_history(self):
        raise NotImplementedError

    async def _run(self):
        retry = 5
        while self._running:
            ep = self._eps[self._ep_idx % len(self._eps)]
            try:
                logger.info(f"[{self.name}] Connecting → {ep}")
                async with websockets.connect(
                    ep,
                    additional_headers = self._headers,
                    ping_interval      = 25,
                    ping_timeout       = 15,
                    close_timeout      = 5,
                    max_size           = 10 * 1024 * 1024,
                ) as ws:
                    self._ws  = ws
                    retry     = 5
                    await self._handshake()
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._parse(raw)
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[{self.name}] Closed: {e}")
            except Exception as e:
                logger.error(f"[{self.name}] Error: {e}")
            finally:
                self._ws        = None
                self._connected = False
                self._ep_idx   += 1
            if self._running:
                await asyncio.sleep(retry + random.uniform(0, 2))
                retry = min(retry * 2, 60)

    def start(self):
        self._running = True
        asyncio.create_task(self._run())
        logger.info(f"[{self.name}] Feed started")

    def stop(self):
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._connected

# ═══════════════════════════════════════════════════════════════════════════════
# QUOTEX FEED
# ═══════════════════════════════════════════════════════════════════════════════

class QuotexFeed(BrokerFeed):
    def __init__(self):
        super().__init__("Quotex", QX_ENDPOINTS, QX_HEADERS, QX_ASSET_MAP)

    async def _handshake(self):
        pkt = await asyncio.wait_for(self._ws.recv(), timeout=10)
        logger.debug(f"[Quotex] Open: {pkt[:60]}")
        await self._send("40")
        pkt = await asyncio.wait_for(self._ws.recv(), timeout=10)
        logger.debug(f"[Quotex] Ack: {pkt[:60]}")
        self._connected = True
        for sym in self._subs:
            await self._send(f'42["asset/subscribe","{sym}"]')
            await asyncio.sleep(0.15)
        await self._request_history()

    async def _request_history(self):
        now = int(time.time())
        for sym, tf_set in self._subs.items():
            for tf_sec in tf_set:
                count = min(500, max(100, 86400 // max(tf_sec, 1)))
                msg = json.dumps(["candles/load", {
                    "asset": sym, "period": tf_sec,
                    "count": count, "to": now,
                }])
                await self._send(f"42{msg}")
                await asyncio.sleep(0.2)

# ═══════════════════════════════════════════════════════════════════════════════
# POCKET OPTION FEED
# ═══════════════════════════════════════════════════════════════════════════════

class PocketOptionFeed(BrokerFeed):
    def __init__(self):
        super().__init__("PocketOption", PO_ENDPOINTS, PO_HEADERS, PO_ASSET_MAP)

    async def _handshake(self):
        pkt = await asyncio.wait_for(self._ws.recv(), timeout=10)
        logger.debug(f"[PO] Open: {pkt[:60]}")
        await self._send("40")
        pkt = await asyncio.wait_for(self._ws.recv(), timeout=10)
        logger.debug(f"[PO] Ack: {pkt[:60]}")
        self._connected = True
        for sym in self._subs:
            sub = json.dumps(["subscribeSymbol", {"symbol": sym}])
            await self._send(f"42{sub}")
            await asyncio.sleep(0.15)
        await self._request_history()

    async def _request_history(self):
        now = int(time.time())
        for sym, tf_set in self._subs.items():
            for tf_sec in tf_set:
                count = min(500, max(100, 86400 // max(tf_sec, 1)))
                msg = json.dumps(["loadHistoryPeriod", {
                    "symbol": sym, "period": tf_sec,
                    "count":  count, "time": now,
                }])
                await self._send(f"42{msg}")
                await asyncio.sleep(0.2)

# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED FEED MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class OTCFeedManager:
    """
    Single interface for both brokers.
    QX + PO averaged  → best accuracy
    One broker down   → other takes over silently
    Both down         → returns None → your existing fallback runs untouched
    """

    def __init__(self):
        self.qx = QuotexFeed()
        self.po = PocketOptionFeed()

    def subscribe_all(self, pairs: list, timeframes: list):
        for pair in pairs:
            if pair in QX_ASSET_MAP:
                self.qx.subscribe(pair, timeframes)
            if pair in PO_ASSET_MAP:
                self.po.subscribe(pair, timeframes)

    def start(self):
        self.qx.start()
        self.po.start()

    def stop(self):
        self.qx.stop()
        self.po.stop()

    def get_candles(self, asset: str, tf: str, count: int = 100,
                    broker: str | None = None) -> Optional[list]:
        """Return candles for one broker when requested; never mix OTC books.

        QX selected-broker analysis is served only from the authenticated QX
        tick tape.  The generic QX socket is intentionally not a fallback for
        that request because synthetic pricing is session-specific.
        """
        if broker == "qx":
            try:
                from otc_price_service import get_authenticated_qx_candles
                candles = get_authenticated_qx_candles(asset, tf, count)
                return candles if len(candles) >= 5 else None
            except Exception:
                return None
        qx_c = self.qx.get_candles(asset, tf, 500)
        po_c = self.po.get_candles(asset, tf, 500)
        if broker == "po":
            return po_c[-count:] if len(po_c) >= 5 else None

        # Both available — merge & average for best accuracy
        if len(qx_c) >= 10 and len(po_c) >= 10:
            return self._merge(qx_c, po_c, count)

        if len(qx_c) >= 5:
            return qx_c
        if len(po_c) >= 5:
            return po_c

        # Neither ready — return None → existing bot fallback kicks in
        return None

    def get_price(self, asset: str, broker: str | None = None) -> Optional[float]:
        """Return a broker-native OTC price when a broker is selected."""
        if broker == "qx":
            try:
                from otc_price_service import get_authenticated_qx_quote
                quote = get_authenticated_qx_quote(asset)
                return float(quote["price"]) if quote else None
            except Exception:
                return None
        if broker == "po":
            return self.po.get_price(asset)
        qp = self.qx.get_price(asset)
        pp = self.po.get_price(asset)
        if qp and pp:
            return round((qp + pp) / 2, 6)
        return qp or pp or None

    def get_latest(self, asset: str, tf: str) -> Optional[dict]:
        c = self.get_candles(asset, tf, 1)
        return c[-1] if c else None

    def status(self) -> dict:
        return {
            "quotex_connected":       self.qx.is_connected,
            "pocketoption_connected": self.po.is_connected,
            "quotex_pairs_cached":    len(self.qx._cache),
            "po_pairs_cached":        len(self.po._cache),
        }

    def _merge(self, qx: list, po: list, count: int) -> list:
        try:
            qx_map = {c["time"]: c for c in qx}
            po_map = {c["time"]: c for c in po}
            common = sorted(set(qx_map) & set(po_map))[-count:]
            if len(common) < 5:
                return qx[-count:]
            merged = []
            for t in common:
                q, p = qx_map[t], po_map[t]
                merged.append({
                    "time":   t,
                    "open":   round((q["open"]   + p["open"])   / 2, 6),
                    "high":   round((q["high"]   + p["high"])   / 2, 6),
                    "low":    round((q["low"]    + p["low"])    / 2, 6),
                    "close":  round((q["close"]  + p["close"])  / 2, 6),
                    "volume": round((q["volume"] + p["volume"]) / 2, 2),
                })
            return merged
        except Exception:
            return qx[-count:]


# ── Singleton — import this anywhere ─────────────────────────────────────────
otc_feed = OTCFeedManager()


def label_to_otc_key(pair_label: str) -> Optional[str]:
    """Convert bot pair display label to OTC key (backward-compat helper).

    "EUR/USD 〔OTC〕" → "EURUSD-OTC"
    "Bitcoin 〔OTC〕" → "BTCUSD-OTC"
    "Gold 〔OTC〕"    → "XAUUSD-OTC"

    Returns None for unsupported / unmapped pairs.
    """
    import re
    if pair_label in LABEL_TO_KEY:
        return LABEL_TO_KEY[pair_label]
    s = re.sub(r"\s*〔OTC〕\s*$", "", pair_label).strip()
    s = re.sub(r"\s*\(OTC\)\s*$", "", s).strip()
    s = s.replace("/", "").upper()
    candidate = f"{s}-OTC"
    return candidate if candidate in QX_ASSET_MAP or candidate in PO_ASSET_MAP else None


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK TEST — python otc_feed_combined.py
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _test():
        print("Starting OTC feed test...")
        print(f"Total OTC pairs: {len(ALL_OTC_PAIRS)}")
        print(f"QX pairs: {len(QX_ASSET_MAP)}  PO pairs: {len(PO_ASSET_MAP)}")
        otc_feed.subscribe_all(
            ["EURUSD-OTC", "GBPUSD-OTC", "XAUUSD-OTC"],
            ["1m", "5m"]
        )
        otc_feed.start()

        for i in range(12):
            await asyncio.sleep(5)
            s = otc_feed.status()
            print(f"\n[{i*5}s] Status: {s}")
            for pair in ["EURUSD-OTC", "GBPUSD-OTC", "XAUUSD-OTC"]:
                c = otc_feed.get_candles(pair, "1m", count=3)
                p = otc_feed.get_price(pair)
                print(f"  {pair} price={p} candles={len(c) if c else 0}")

    asyncio.run(_test())
