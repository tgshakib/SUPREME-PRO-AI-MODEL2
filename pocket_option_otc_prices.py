"""Pocket Option OTC — Real-Time WebSocket Price Stream
=========================================================
TRUE Socket.IO WebSocket streaming — server PUSHES every tick.

HOW IT WORKS
------------
  1. Auto-login via aiohttp → extracts SSID from session cookie
  2. Open one persistent WebSocket to wss://api-l.po.market/trade
  3. Send auth frame with SSID
  4. Subscribe ALL OTC pairs — one frame per pair, no polling
  5. Server pushes candle ticks → shared price buffer updated live
  6. Terminal redraws every second from the push buffer

CREDENTIALS
-----------
Set env vars  PO_EMAIL / PO_PASSWORD  or edit the constants below.
SSID is fetched automatically — no manual browser cookie needed.

SOCKET.IO PROTOCOL (EIO=3 / message prefix "42")
-------------------------------------------------
  "0{...}"        handshake from server
  "40"            namespace connect (client sends after "0")
  "42[event,data]" application message (bi-directional)
  "2" / "3"       ping / pong heartbeat
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Optional

import aiohttp
import websockets

# ── Credentials ───────────────────────────────────────────────────────────────
PO_EMAIL    = os.environ.get("PO_EMAIL",    "tgshakib012@gmail.com")
PO_PASSWORD = os.environ.get("PO_PASSWORD", "tgshakib012@g")
PO_SSID     = os.environ.get("PO_SSID",    "")   # override auto-login if set

# ── Stream settings ───────────────────────────────────────────────────────────
CANDLE_PERIOD     = 5       # seconds — subscribe granularity sent to server
REFRESH_INTERVAL  = 1.0     # seconds — terminal redraw rate
SUBSCRIBE_DELAY   = 0.08    # seconds between subscribe frames (rate-limit)
IS_DEMO           = 1       # 1 = demo account, 0 = real money account

# ── WebSocket endpoint ────────────────────────────────────────────────────────
PO_WS_URL   = "wss://api-l.po.market/trade"
PO_LOGIN_URL = "https://po.trade/api/v1/cabinet/login"

# ── ALL Pocket Option OTC asset codes ────────────────────────────────────────
# The server recognises these exact strings in subscribeSymbol frames.
ALL_OTC_ASSETS: list[str] = [
    # ── Major Forex OTC ───────────────────────────────────────────────────────
    "EURUSD_otc",
    "GBPUSD_otc",
    "USDJPY_otc",
    "USDCHF_otc",
    "USDCAD_otc",
    "AUDUSD_otc",
    "NZDUSD_otc",

    # ── Minor / Cross Forex OTC ───────────────────────────────────────────────
    "AUDCAD_otc",
    "AUDCHF_otc",
    "AUDJPY_otc",
    "AUDNZD_otc",
    "CADCHF_otc",
    "CADJPY_otc",
    "CHFJPY_otc",
    "EURAUD_otc",
    "EURCAD_otc",
    "EURCHF_otc",
    "EURGBP_otc",
    "EURJPY_otc",
    "EURNZD_otc",
    "GBPAUD_otc",
    "GBPCAD_otc",
    "GBPCHF_otc",
    "GBPJPY_otc",
    "GBPNZD_otc",
    "NZDCAD_otc",
    "NZDCHF_otc",
    "NZDJPY_otc",

    # ── Exotic / EM Forex OTC ─────────────────────────────────────────────────
    "USDMXN_otc",
    "USDINR_otc",
    "USDBRL_otc",
    "USDCOP_otc",
    "USDARS_otc",
    "USDPKR_otc",
    "USDNGN_otc",
    "USDEGP_otc",
    "USDIDR_otc",
    "USDPHP_otc",
    "USDZAR_otc",
    "USDBDT_otc",
    "USDDZD_otc",

    # ── Metals OTC ────────────────────────────────────────────────────────────
    "XAUUSD_otc",   # Gold
    "XAGUSD_otc",   # Silver

    # ── Energy OTC ────────────────────────────────────────────────────────────
    "USOIL_otc",    # US WTI Crude
    "BRENT_otc",    # UK Brent Crude

    # ── Indices OTC ───────────────────────────────────────────────────────────
    "NQ_otc",       # NASDAQ 100
    "SP_otc",       # S&P 500
    "DJI_otc",      # Dow Jones

    # ── Crypto OTC ────────────────────────────────────────────────────────────
    "BTCUSD_otc",   # Bitcoin
    "ETHUSD_otc",   # Ethereum
    "LTCUSD_otc",   # Litecoin
    "BCHUSD_otc",   # Bitcoin Cash
    "ETCUSD_otc",   # Ethereum Classic
    "BNBUSD_otc",   # Binance Coin
    "SOLUSD_otc",   # Solana
    "AVAXUSD_otc",  # Avalanche
    "DOTUSD_otc",   # Polkadot
    "LINKUSD_otc",  # Chainlink
    "DASHUSD_otc",  # Dash
    "AXSUSD_otc",   # Axie Infinity
    "TONUSD_otc",   # Toncoin
    "XRPUSD_otc",   # Ripple
    "ADAUSD_otc",   # Cardano
    "MATICUSD_otc", # Polygon

    # ── Stocks OTC ────────────────────────────────────────────────────────────
    "AAPL_otc",     # Apple
    "AMZN_otc",     # Amazon
    "TSLA_otc",     # Tesla
    "GOOGL_otc",    # Alphabet / Google
    "MSFT_otc",     # Microsoft
    "META_otc",     # Meta (Facebook)
    "NFLX_otc",     # Netflix
    "NVDA_otc",     # NVIDIA
    "BABA_otc",     # Alibaba
    "JNJ_otc",      # Johnson & Johnson
    "PFE_otc",      # Pfizer
    "BA_otc",       # Boeing
    "MCD_otc",      # McDonald's
    "INTC_otc",     # Intel
    "AMEX_otc",     # American Express
    "CSCO_otc",     # Cisco
    "V_otc",        # Visa
    "MA_otc",       # Mastercard
    "DIS_otc",      # Disney
    "IBM_otc",      # IBM
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(price: float, asset: str) -> str:
    a = asset.upper()
    if any(x in a for x in ("JPY", "IDR", "NGN", "PKR", "BDT", "EGP",
                              "DZD", "COP", "ARS", "INR", "PHP", "MXN",
                              "ZAR", "BRL")):
        return f"{price:.3f}"
    if any(x in a for x in ("BTC", "XAU")):
        return f"{price:.2f}"
    if any(x in a for x in ("NQ", "SP", "DJI")):
        return f"{price:.2f}"
    if any(x in a for x in ("ETH", "BCH", "BNB", "SOL", "AVAX", "XAG",
                              "AAPL", "AMZN", "TSLA", "GOOGL", "MSFT",
                              "META", "NFLX", "NVDA", "BABA")):
        return f"{price:.3f}"
    if any(x in a for x in ("LTC", "LINK", "DOT", "DASH", "AXS", "TON",
                              "XRP", "ADA", "MATIC")):
        return f"{price:.4f}"
    return f"{price:.5f}"


# ── Auto-login: get SSID from email + password ───────────────────────────────

async def fetch_ssid(email: str, password: str) -> Optional[str]:
    """
    POST credentials to Pocket Option's login API and extract the
    session token from the response cookies.
    Returns the SSID string, or None on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Origin": "https://po.trade",
        "Referer": "https://po.trade/",
    }
    payload = {"email": email, "password": password}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                PO_LOGIN_URL,
                json=payload,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                # Try cookie first
                for name in ("token", "PHPSESSID", "session", "auth"):
                    val = resp.cookies.get(name)
                    if val:
                        return str(val.value)

                # Try JSON body
                try:
                    body = await resp.json(content_type=None)
                    for key in ("token", "ssid", "session", "data"):
                        v = body.get(key)
                        if isinstance(v, str) and len(v) > 8:
                            return v
                        if isinstance(v, dict):
                            for sub in ("token", "ssid", "session"):
                                vv = v.get(sub)
                                if isinstance(vv, str) and len(vv) > 8:
                                    return vv
                except Exception:
                    pass

                # Try raw text for token pattern
                try:
                    text = await resp.text()
                    m = re.search(r'"(?:token|ssid|session)"\s*:\s*"([^"]{16,})"', text)
                    if m:
                        return m.group(1)
                except Exception:
                    pass

    except Exception as exc:
        print(f"⚠  Auto-login failed: {exc}")

    return None


# ── Price buffer — populated by WebSocket push ────────────────────────────────

class PriceBuffer:
    """Thread-safe buffer of latest prices per asset, filled by WS push."""

    def __init__(self):
        self._data: dict[str, dict] = {}   # asset → {price, time, ticks}

    def update(self, asset: str, price: float):
        prev = self._data.get(asset, {}).get("price")
        ticks = self._data.get(asset, {}).get("ticks", 0)
        if price != prev:
            ticks += 1
        self._data[asset] = {"price": price, "time": time.time(), "ticks": ticks}

    def get(self, asset: str) -> Optional[dict]:
        return self._data.get(asset)

    def count_live(self) -> int:
        return sum(1 for v in self._data.values() if v.get("price", 0) > 0)


# ── Terminal display ──────────────────────────────────────────────────────────

def _print_table(buf: PriceBuffer, subscribed: set[str], failed: set[str]) -> None:
    now = time.strftime("%H:%M:%S")
    live = buf.count_live()
    print("\033[H\033[J", end="")
    print("=" * 70)
    print(f"  POCKET OPTION OTC — LIVE WEBSOCKET STREAM          {now}")
    print(f"  {len(subscribed)} subscribed  •  {live} live  •  {CANDLE_PERIOD}s ticks  •  push-only")
    print("=" * 70)
    print(f"\n  {'ASSET':<18}  {'LIVE PRICE':>14}  {'TICKS':>6}  {'AGE':>6}  STATUS")
    print("  " + "─" * 58)

    for asset in sorted(ALL_OTC_ASSETS):
        if asset in failed:
            print(f"  {asset:<18}  {'—':>14}  {'—':>6}  {'—':>6}  ⚠ sub failed")
            continue
        if asset not in subscribed:
            print(f"  {asset:<18}  {'—':>14}  {'—':>6}  {'—':>6}  ⏳ pending…")
            continue

        d = buf.get(asset)
        if d and d.get("price", 0) > 0:
            age = time.time() - d["time"]
            age_s = f"{age:.0f}s"
            stale = "⚠ STALE" if age > 10 else "✅ live"
            print(f"  {asset:<18}  {_fmt(d['price'], asset):>14}  {d['ticks']:>6}  {age_s:>6}  {stale}")
        else:
            print(f"  {asset:<18}  {'—':>14}  {'0':>6}  {'—':>6}  ⏳ waiting…")

    print("  " + "─" * 58)
    print(f"\n  Ctrl+C to stop  |  Prices stale >10s are flagged ⚠")


# ── WebSocket stream ──────────────────────────────────────────────────────────

async def run_stream(ssid: str, buf: PriceBuffer) -> None:
    """
    Open one persistent WebSocket, authenticate, subscribe ALL OTC pairs,
    then listen for server-pushed candle/price ticks indefinitely.
    """
    subscribed: set[str] = set()
    failed:     set[str] = set()

    headers = {
        "Origin": "https://po.trade",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    print(f"\n🔌  Opening WebSocket → {PO_WS_URL}")

    async with websockets.connect(
        PO_WS_URL,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=15,
        close_timeout=10,
    ) as ws:

        # ── Socket.IO handshake ───────────────────────────────────────────────
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        raw = str(raw)
        ping_interval = 25.0
        if raw.startswith("0"):
            try:
                hs = json.loads(raw[1:])
                ping_interval = hs.get("pingInterval", 25000) / 1000
            except Exception:
                pass

        # Namespace connect
        await ws.send("40")
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        if not str(resp).startswith("40"):
            raise ConnectionError(f"Namespace connect rejected: {resp!r}")

        # ── Authenticate ──────────────────────────────────────────────────────
        auth = json.dumps(["auth", {"session": ssid, "isDemo": IS_DEMO}])
        await ws.send(f"42{auth}")
        print("🔑  Auth frame sent — waiting for confirmation …")

        auth_resp = await asyncio.wait_for(ws.recv(), timeout=15)
        auth_str = str(auth_resp)
        if "failauth" in auth_str.lower() or "error" in auth_str.lower():
            raise ConnectionError(f"Auth rejected: {auth_str[:200]}")
        print("✅  Authenticated!\n")

        # ── Subscribe all OTC pairs ───────────────────────────────────────────
        print(f"📡  Subscribing {len(ALL_OTC_ASSETS)} OTC pairs …")
        for asset in ALL_OTC_ASSETS:
            sub = json.dumps(["subscribeSymbol", {"asset": asset, "period": CANDLE_PERIOD}])
            await ws.send(f"42{sub}")
            subscribed.add(asset)
            await asyncio.sleep(SUBSCRIBE_DELAY)

        print(f"✅  All pairs subscribed — server is now pushing ticks\n")
        print("🟢  Entering live display …\n")
        await asyncio.sleep(0.5)

        # ── Start background heartbeat ────────────────────────────────────────
        async def _heartbeat():
            while True:
                await asyncio.sleep(ping_interval)
                try:
                    await ws.send("2")   # Socket.IO ping
                except Exception:
                    break

        hb_task = asyncio.create_task(_heartbeat())

        # ── Start background display refresh ──────────────────────────────────
        async def _display():
            while True:
                _print_table(buf, subscribed, failed)
                await asyncio.sleep(REFRESH_INTERVAL)

        disp_task = asyncio.create_task(_display())

        try:
            # ── Listen for server-pushed messages ─────────────────────────────
            async for raw_msg in ws:
                msg = str(raw_msg)

                # Heartbeat pong
                if msg == "2":
                    await ws.send("3")
                    continue
                if msg == "3":
                    continue

                # Application message
                if not msg.startswith("42"):
                    continue

                try:
                    payload = json.loads(msg[2:])
                    if not isinstance(payload, list) or len(payload) < 2:
                        continue

                    event, data = payload[0], payload[1]

                    # ── Single candle tick (most common push) ─────────────────
                    if event in ("newcandle", "candle", "tick"):
                        asset = data.get("asset", data.get("symbol", ""))
                        price = (
                            data.get("close")
                            or data.get("price")
                            or data.get("bid")
                            or data.get("ask")
                        )
                        if asset and price:
                            buf.update(asset, float(price))

                    # ── Batch candle history ──────────────────────────────────
                    elif event in ("candles", "history"):
                        asset   = data.get("asset", data.get("symbol", ""))
                        candles = data.get("candles", data.get("data", []))
                        if asset and candles:
                            last = candles[-1]
                            price = (
                                last.get("close")
                                or last.get("price")
                                or last.get("bid")
                            )
                            if price:
                                buf.update(asset, float(price))

                    # ── Server may send prices as plain dict ──────────────────
                    elif event == "price":
                        asset = data.get("asset", data.get("symbol", ""))
                        price = data.get("price") or data.get("close")
                        if asset and price:
                            buf.update(asset, float(price))

                except Exception:
                    pass

        except asyncio.CancelledError:
            pass
        finally:
            hb_task.cancel()
            disp_task.cancel()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 70)
    print("  POCKET OPTION OTC — REAL-TIME WEBSOCKET PRICE STREAM")
    print(f"  {len(ALL_OTC_ASSETS)} pairs  •  {CANDLE_PERIOD}s candles  •  Socket.IO push")
    print("=" * 70)

    # ── Step 1: get SSID ──────────────────────────────────────────────────────
    ssid = PO_SSID
    if ssid:
        print(f"\n✅  Using SSID from environment (PO_SSID)")
    else:
        print(f"\n🔑  Auto-login as {PO_EMAIL} …")
        ssid = await fetch_ssid(PO_EMAIL, PO_PASSWORD)
        if ssid:
            print(f"✅  SSID obtained: {ssid[:8]}…{ssid[-4:]}")
        else:
            print(
                "\n⚠  Auto-login could not extract SSID from the API response.\n"
                "   This usually means the endpoint requires browser cookies.\n\n"
                "   Manual method:\n"
                "   1. Log into https://po.trade in Chrome\n"
                "   2. Press F12 → Application → Cookies → po.trade\n"
                "   3. Copy the value of the 'token' or 'PHPSESSID' cookie\n"
                "   4. Set it:  export PO_SSID='<paste-here>'  then re-run\n"
            )
            return

    # ── Step 2: run stream (auto-reconnect on disconnect) ─────────────────────
    buf = PriceBuffer()
    reconnect_delay = 5

    while True:
        try:
            await run_stream(ssid, buf)
        except KeyboardInterrupt:
            print("\n\n🔴  Stopped by user.\n")
            break
        except ConnectionError as exc:
            print(f"\n❌  {exc}")
            print("   Check your SSID or credentials and try again.\n")
            break
        except Exception as exc:
            print(f"\n⚠  Stream error: {exc} — reconnecting in {reconnect_delay}s …")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🔴  Stopped.\n")
