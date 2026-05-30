"""Quotex OTC Real-Time Price Stream
======================================
TRUE WebSocket streaming — the server PUSHES every tick into a shared
buffer; we never send repeated REST-like requests.

Architecture
------------
  1. Connect once  →  one persistent WebSocket to qxbroker.com
  2. Subscribe all pairs via  start_candles_stream(asset)
     └─ sends ONE "subscribe" frame per pair; server starts pushing ticks
  3. Read from  api.realtime_price[asset]  (shared state, updated by push)
  4. Refresh the terminal display every REFRESH_INTERVAL seconds

Credentials
-----------
Set environment variables QUOTEX_EMAIL and QUOTEX_PASSWORD, or they fall
back to the inline values below.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from pyquotex.stable_api import Quotex

# ── Credentials ──────────────────────────────────────────────────────────────
QUOTEX_EMAIL    = os.environ.get("QUOTEX_EMAIL",    "hosnaranupur@gmail.com")
QUOTEX_PASSWORD = os.environ.get("QUOTEX_PASSWORD", "hosnaranupur@")

# ── Stream settings ───────────────────────────────────────────────────────────
CANDLE_PERIOD     = 5    # seconds — granularity fed to the subscribe frame
REFRESH_INTERVAL  = 1.0  # seconds — how often to redraw the price table
SUBSCRIBE_BATCH   = 5    # pairs subscribed per batch (avoids WS flood)
SUBSCRIBE_DELAY   = 0.3  # seconds between batches
FIRST_TICK_TIMEOUT = 15  # seconds to wait for first push tick per pair

# ── All Quotex OTC pairs ──────────────────────────────────────────────────────
# The Quotex WebSocket uses lowercase <BASE><QUOTE>_otc notation.
ALL_OTC_PAIRS: list[str] = [
    # Major Forex
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc",
    "USDCAD_otc", "AUDUSD_otc", "NZDUSD_otc",
    # Minor / Cross Forex
    "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "AUDNZD_otc",
    "CADCHF_otc", "CADJPY_otc", "CHFJPY_otc",
    "EURAUD_otc", "EURCAD_otc", "EURCHF_otc", "EURGBP_otc",
    "EURJPY_otc", "EURNZD_otc",
    "GBPAUD_otc", "GBPCAD_otc", "GBPCHF_otc", "GBPJPY_otc", "GBPNZD_otc",
    "NZDCAD_otc", "NZDCHF_otc", "NZDJPY_otc",
    # Exotic / EM Forex
    "USDARS_otc", "USDBDT_otc", "USDBRL_otc", "USDCOP_otc",
    "USDDZD_otc", "USDEGP_otc", "USDIDR_otc", "USDINR_otc",
    "USDMXN_otc", "USDNGN_otc", "USDPHP_otc", "USDPKR_otc",
    "USDZAR_otc",
    # Metals
    "XAUUSD_otc",   # Gold
    "XAGUSD_otc",   # Silver
    # Energy
    "UKBRENT_otc",  # UK Brent crude
    "USCRUDE_otc",  # US WTI crude
    # Crypto
    "BTCUSD_otc",  "ETHUSD_otc",  "ETCUSD_otc",  "LTCUSD_otc",
    "BCHUSD_otc",  "BNBUSD_otc",  "SOLUSD_otc",  "AVAXUSD_otc",
    "DOTUSD_otc",  "LINKUSD_otc", "DASHUSD_otc", "AXSUSD_otc",
    "TONUSD_otc",  "TRUMPUSD_otc",
    # Stocks
    "AMEX_otc",    # American Express
    "BA_otc",      # Boeing
    "FB_otc",      # Facebook / Meta
    "INTC_otc",    # Intel
    "JNJ_otc",     # Johnson & Johnson
    "MCD_otc",     # McDonald's
    "PFE_otc",     # Pfizer
]


# ── Price formatting ──────────────────────────────────────────────────────────

def _fmt(price: float, pair: str) -> str:
    p = pair.upper()
    if any(x in p for x in ("JPY", "IDR", "NGN", "PKR", "BDT", "EGP",
                              "DZD", "COP", "ARS", "INR", "PHP", "MXN",
                              "ZAR", "BRL")):
        return f"{price:.3f}"
    if any(x in p for x in ("BTC", "XAU")):
        return f"{price:.2f}"
    if any(x in p for x in ("ETH", "BCH", "BNB", "SOL", "AVAX", "XAG")):
        return f"{price:.3f}"
    if any(x in p for x in ("LTC", "LINK", "DOT", "DASH", "AXS", "TON")):
        return f"{price:.4f}"
    return f"{price:.5f}"


# ── Subscription helpers ──────────────────────────────────────────────────────

async def _subscribe_pair(
    client: Quotex,
    pair: str,
    asset_map: dict[str, str],
    unavailable: set[str],
) -> None:
    """
    Resolve the pair to its internal asset name, then send ONE WebSocket
    subscribe frame.  Results written into asset_map / unavailable.
    """
    try:
        asset, info = await client.get_available_asset(pair, force_open=True)
        if not info or not info[2]:
            unavailable.add(pair)
            return
        asset_map[pair] = asset
        # ✅ WebSocket SUBSCRIBE — one frame, server now PUSHes ticks
        await client.start_candles_stream(asset, CANDLE_PERIOD)
    except Exception as exc:
        unavailable.add(pair)
        print(f"  ⚠  {pair}: subscribe failed — {exc}")


async def _subscribe_all(
    client: Quotex,
) -> tuple[dict[str, str], set[str]]:
    """
    Subscribe every OTC pair in small batches to avoid flooding the socket.
    Returns (asset_map, unavailable_set).
    """
    asset_map: dict[str, str] = {}    # pair → internal asset name
    unavailable: set[str]     = set()

    print(f"\n📡  Subscribing {len(ALL_OTC_PAIRS)} OTC pairs via WebSocket …")

    for i in range(0, len(ALL_OTC_PAIRS), SUBSCRIBE_BATCH):
        batch = ALL_OTC_PAIRS[i : i + SUBSCRIBE_BATCH]
        await asyncio.gather(*[
            _subscribe_pair(client, p, asset_map, unavailable)
            for p in batch
        ])
        # Brief pause between batches so we don't flood the WS channel
        await asyncio.sleep(SUBSCRIBE_DELAY)

    print(f"✅  Subscribed: {len(asset_map)} active  |  "
          f"⚠  Unavailable: {len(unavailable)}\n")
    return asset_map, unavailable


# ── Live price reader ─────────────────────────────────────────────────────────

def _latest_price(client: Quotex, asset: str) -> Optional[float]:
    """
    Read the most recent tick price from the shared push buffer.

    api.realtime_price[asset] is a deque/list of dicts populated by the
    WebSocket message handler every time the server sends a tick — no
    request is made here.
    """
    try:
        ticks = client.api.realtime_price.get(asset)
        if not ticks:
            return None
        latest = ticks[-1]
        # The tick dict may use 'price', 'close', or 'bid' depending on
        # the message type.  Try each key in priority order.
        for key in ("price", "close", "bid", "ask"):
            val = latest.get(key)
            if val is not None:
                fv = float(val)
                if fv > 0:
                    return fv
    except Exception:
        pass
    return None


# ── Terminal display ──────────────────────────────────────────────────────────

def _print_table(
    asset_map: dict[str, str],
    unavailable: set[str],
    client: Quotex,
    tick_counts: dict[str, int],
) -> None:
    """Redraw the full price table in-place."""
    now = time.strftime("%H:%M:%S")
    print(f"\033[H\033[J", end="")   # clear terminal
    print("=" * 66)
    print(f"  QUOTEX OTC — LIVE WEBSOCKET PRICE STREAM      {now}")
    print(f"  {len(asset_map)} active pairs  •  {CANDLE_PERIOD}s ticks  •  push-only")
    print("=" * 66)
    print(f"\n  {'PAIR':<22}  {'LIVE PRICE':>14}  {'TICKS':>6}  STATUS")
    print("  " + "-" * 52)

    for pair in sorted(ALL_OTC_PAIRS):
        if pair in unavailable:
            print(f"  {pair:<22}  {'—':>14}  {'—':>6}  ⚠  market closed")
            continue

        asset = asset_map.get(pair)
        if asset is None:
            print(f"  {pair:<22}  {'—':>14}  {'—':>6}  ⏳ resolving…")
            continue

        price = _latest_price(client, asset)
        ticks = tick_counts.get(pair, 0)

        if price is not None:
            print(f"  {pair:<22}  {_fmt(price, pair):>14}  {ticks:>6}  ✅ live")
        else:
            print(f"  {pair:<22}  {'—':>14}  {ticks:>6}  ⏳ waiting for tick…")

    print("  " + "-" * 52)
    print(f"\n  Ctrl+C to stop")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 66)
    print("  QUOTEX OTC REAL-TIME WEBSOCKET PRICE STREAM")
    print("=" * 66)

    client = Quotex(
        email=QUOTEX_EMAIL,
        password=QUOTEX_PASSWORD,
        lang="en",
    )

    print(f"\n🔌  Connecting to Quotex as {QUOTEX_EMAIL} …")
    connected, reason = await client.connect()
    if not connected:
        print(f"\n❌  Connection failed: {reason}")
        return
    print("✅  WebSocket connected!\n")

    # ── Step 1: subscribe all pairs (one WS frame each, server pushes ticks)
    asset_map, unavailable = await _subscribe_all(client)

    if not asset_map:
        print("❌  No pairs could be subscribed. Check your credentials.")
        await client.close()
        return

    # ── Step 2: wait for first push ticks to arrive
    print(f"⏳  Waiting for server to push first ticks "
          f"(up to {FIRST_TICK_TIMEOUT}s) …")

    deadline = time.time() + FIRST_TICK_TIMEOUT
    while time.time() < deadline:
        received = sum(
            1 for pair, asset in asset_map.items()
            if _latest_price(client, asset) is not None
        )
        print(f"\r  {received}/{len(asset_map)} pairs have data…", end="", flush=True)
        if received == len(asset_map):
            break
        await asyncio.sleep(0.4)
    print()

    # ── Step 3: live display loop — read from push buffer, never request
    tick_counts: dict[str, int] = {p: 0 for p in ALL_OTC_PAIRS}
    last_prices: dict[str, Optional[float]] = {}

    print("\n🟢  Streaming live prices — Ctrl+C to stop\n")
    await asyncio.sleep(0.5)

    try:
        while True:
            # Update tick counters for pairs where the price changed
            for pair, asset in asset_map.items():
                new_price = _latest_price(client, asset)
                old_price = last_prices.get(pair)
                if new_price is not None and new_price != old_price:
                    tick_counts[pair] += 1
                    last_prices[pair] = new_price

            _print_table(asset_map, unavailable, client, tick_counts)
            await asyncio.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n🔴  Stopped by user.")
    finally:
        # ── Unsubscribe all streams cleanly
        for pair, asset in asset_map.items():
            try:
                await client.stop_candles_stream(asset)
            except Exception:
                pass
        await client.close()
        print("🔌  WebSocket closed.\n")


if __name__ == "__main__":
    asyncio.run(main())
