"""Live forex / crypto / metals / indices prices via yfinance.

Provides:
  • pip_size(pair)             → per-symbol pip size  (JPY 0.01, XAU 0.1,
                                 XAG 0.001, indices 1.0, crypto 1.0,
                                 everything else 0.0001)
  • decimals(pair)             → display decimals
  • get_live_price(pair)       → live mid price (float) or None on failure.
                                 Cached for 30 seconds per ticker so we
                                 don't hammer Yahoo.
  • format_price(pair, price)  → string formatted with the right decimals

Pair labels accepted are the same human strings used elsewhere in the bot,
e.g. 'EUR/USD', 'XAU/USD', 'GOLD', 'XAUUSD (Gold)', 'BTC/USDT', 'NAS100',
'EUR/USD (OTC)', etc. OTC suffix is stripped — Yahoo only carries the live
underlying ticker.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Optional

try:
    import yfinance as yf
    _YF_OK = True
except Exception as e:
    print(f"[live_prices] yfinance import failed: {e}")
    yf = None
    _YF_OK = False


# ── Real-time SPOT metals (matches MT5 / TradingView) ────────────────────
# yfinance / PAXG can drift $5–$10 from the true bullion spot reading
# that brokers and TradingView show. For Gold and Silver we therefore
# query a dedicated free spot-metal feed FIRST (gold-api.com), with a
# Stooq backup, and only fall through to the Yahoo ticker if both are
# down. This is what makes XAU/USD signals match MT5 to the dollar.
_SPOT_METAL_CACHE: dict[str, tuple[float, float]] = {}  # sym -> (ts, price)
_SPOT_METAL_TTL = 4.0  # seconds — keep it fresh, MT5 ticks every second


def _http_get(url: str, timeout: float = 4.0) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (SupremePro)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return None


def _fetch_spot_metal(sym: str) -> Optional[float]:
    """Fetch real-time spot metal price.

    `sym` is the ISO bullion code: 'XAU' (gold), 'XAG' (silver),
    'XPT' (platinum), 'XPD' (palladium). Returns USD/oz or None.
    Tries gold-api.com → stooq.com in order. Cached briefly so a burst
    of pair lookups doesn't hammer the upstream."""
    sym = sym.upper()
    now = time.time()
    cached = _SPOT_METAL_CACHE.get(sym)
    if cached and now - cached[0] < _SPOT_METAL_TTL:
        return cached[1]

    # Source #1 — gold-api.com (clean JSON, ~few-second freshness)
    body = _http_get(f"https://api.gold-api.com/price/{sym}")
    if body:
        try:
            d = json.loads(body)
            px = float(d.get("price", 0))
            if px > 0:
                _SPOT_METAL_CACHE[sym] = (now, px)
                return px
        except Exception:
            pass

    # Source #2 — stooq.com fallback (CSV/JSON of XAUUSD / XAGUSD)
    stooq_sym = {"XAU": "xauusd", "XAG": "xagusd",
                 "XPT": "xptusd", "XPD": "xpdusd"}.get(sym)
    if stooq_sym:
        body = _http_get(
            f"https://stooq.com/q/l/?s={stooq_sym}&i=d&f=sd2t2ohlc&h&e=json"
        )
        if body:
            try:
                d = json.loads(body)
                row = (d.get("symbols") or [{}])[0]
                px = float(row.get("close", 0))
                if px > 0:
                    _SPOT_METAL_CACHE[sym] = (now, px)
                    return px
            except Exception:
                pass

    return None


# ── Pair → Yahoo Finance ticker ──────────────────────────────────────────
# Forex pairs use the '<BASE><QUOTE>=X' convention.
# Crypto uses '<BASE>-USD'. Indices use Yahoo's index symbols.
# NOTE: For Gold (XAU/USD) we use `PAXG-USD` — PAX Gold, an asset-backed
# token where 1 PAXG = 1 troy oz of physical gold. It trades 24/7 on
# crypto exchanges, has minute-resolution Yahoo data, and tracks SPOT
# gold within ~$1. This matches what TradingView TVC:GOLD and MT5
# XAUUSD show. Yahoo's old `XAUUSD=X` symbol was delisted; the futures
# `GC=F` is kept ONLY as a last-resort fallback (drifts $20+ from spot
# and has session breaks that previously caused phantom SL hits).
_INDEX_MAP = {
    "NAS100":   "^NDX",
    "US100":    "^NDX",
    "US30":     "^DJI",
    "SPX500":   "^GSPC",
    "SP500":    "^GSPC",
    "DXY":      "DX-Y.NYB",
    "USOIL":    "CL=F",
    "USCRUDE":  "CL=F",
    "BRENT":    "BZ=F",
    "UKBRENT":  "BZ=F",
    "XAUUSD":   "PAXG-USD",   # spot Gold via PAX Gold (1:1 physical gold)
    "GOLD":     "PAXG-USD",
    "XAGUSD":   "SI=F",       # silver futures (no clean spot ticker on Yahoo)
    "SILVER":   "SI=F",
    "SP/ASX 200": "^AXJO",
}

# Fallback chain: if the primary spot ticker doesn't return data, walk
# down to alternatives so the simulator never has to use a fake price.
_TICKER_FALLBACKS: dict[str, list[str]] = {
    "PAXG-USD": ["GC=F"],   # PAX Gold → Gold futures
}

_CRYPTO_MAP = {
    "BTC":  "BTC-USD",
    "ETH":  "ETH-USD",
    "SOL":  "SOL-USD",
    "BCH":  "BCH-USD",
    "BNB":  "BNB-USD",
    "LTC":  "LTC-USD",
    "DOT":  "DOT-USD",
    "AVAX": "AVAX-USD",
    "LINK": "LINK-USD",
    "DASH": "DASH-USD",
    "ETC":  "ETC-USD",
    "TON":  "TON11419-USD",
    "AXS":  "AXS-USD",
    "TRUMP": None,
}


def _normalize(pair: str) -> str:
    """Strip ' 〔OTC〕' or ' (OTC)' suffix and uppercase for matching."""
    s = pair.upper().strip()
    s = re.sub(r"\s*〔OTC〕\s*$", "", s).strip()
    s = re.sub(r"\s*\(OTC\)\s*$", "", s).strip()
    return s


def yf_ticker(pair: str) -> Optional[str]:
    """Map a human pair label to a yfinance ticker, or None if unsupported."""
    s = _normalize(pair)
    s_nospace = s.replace(" ", "")
    s_compact = s_nospace.replace("/", "").replace("-", "")

    # ── 1. Indices / metals / oil — match either with or without slashes
    if s in _INDEX_MAP:
        return _INDEX_MAP[s]
    for k, v in _INDEX_MAP.items():
        k_compact = k.replace(" ", "").replace("/", "")
        if k in s or k_compact == s_compact or k_compact in s_compact:
            return v

    # ── 2. Crypto by full name
    if "BITCOIN CASH" in s: return "BCH-USD"
    if "BITCOIN" in s: return "BTC-USD"
    if "ETHEREUM CLASSIC" in s: return "ETC-USD"
    if "ETHEREUM" in s: return "ETH-USD"
    if "SOLANA" in s: return "SOL-USD"
    if "LITECOIN" in s: return "LTC-USD"
    if "CHAINLINK" in s: return "LINK-USD"
    if "AVALANCHE" in s: return "AVAX-USD"
    if "POLKADOT" in s: return "DOT-USD"
    if "BINANCE COIN" in s: return "BNB-USD"
    if "AXIE INFINITY" in s: return "AXS-USD"
    if "TONCOIN" in s: return "TON11419-USD"

    # ── 3. Crypto by symbol — only if base is in our known crypto list,
    # otherwise we'd misroute forex pairs like EUR/USD to a fake 'EUR-USD'.
    m = re.match(r"^([A-Z]{2,5})(USDT|USD)$", s_compact)
    if m and m.group(1) in _CRYPTO_MAP:
        mapped = _CRYPTO_MAP[m.group(1)]
        return mapped if mapped else None
    if s_compact in _CRYPTO_MAP:
        mapped = _CRYPTO_MAP[s_compact]
        return mapped if mapped else None

    # ── 4. Forex 3+3
    fx = re.match(r"^([A-Z]{3})([A-Z]{3})$", s_compact)
    if fx:
        return f"{fx.group(1)}{fx.group(2)}=X"
    return None


# ── Pip size ─────────────────────────────────────────────────────────────
def pip_size(pair: str) -> float:
    s = _normalize(pair).replace("/", "")
    if "JPY" in s:
        return 0.01
    if "XAUUSD" in s or s.startswith("XAU") or "GOLD" in s:
        return 0.1
    if "XAGUSD" in s or s.startswith("XAG") or "SILVER" in s:
        return 0.001
    if any(k in s for k in ("BTC", "ETH", "SOL", "BCH", "BNB", "LTC",
                            "DOT", "AVAX", "LINK", "DASH", "ETC")):
        return 1.0
    if any(k in s for k in ("NAS100", "US100", "US30", "SPX", "SP500", "ASX")):
        return 1.0
    if "USOIL" in s or "USCRUDE" in s or "BRENT" in s:
        return 0.01
    if "DXY" in s:
        return 0.01
    return 0.0001


def decimals(pair: str) -> int:
    """Reasonable display decimals for a pair price."""
    s = _normalize(pair).replace("/", "")
    if "JPY" in s: return 3
    if "XAUUSD" in s or "GOLD" in s: return 2
    if "XAGUSD" in s or "SILVER" in s: return 3
    if "BTC" in s: return 1
    if "ETH" in s: return 2
    if "SOL" in s: return 3
    if any(k in s for k in ("NAS100", "US100", "US30", "SPX", "SP500", "ASX")):
        return 1
    if "USOIL" in s or "USCRUDE" in s or "BRENT" in s: return 2
    if "DXY" in s: return 3
    return 5


def format_price(pair: str, price: float) -> str:
    return f"{price:.{decimals(pair)}f}"


# ── Live price fetch with 2-second TTL cache ────────────────────────────
# Binary options expire every 1 minute — even a 5s stale price is too
# old on fast-moving markets. Dropped to 2s so every analysis run
# starts from a tick no more than 2 seconds old.
_CACHE: dict[str, tuple[float, float]] = {}      # ticker -> (ts, price)
_TTL = 2.0
_BIAS_CACHE: dict[str, tuple[float, str, float]] = {}  # ticker -> (ts, dir, strength)
_BIAS_TTL = 15.0


def _fetch_yf_one(ticker: str) -> Optional[float]:
    """Single yfinance fetch for ONE ticker. Returns the actual live price.

    Priority order:
      1. fast_info.last_price        — real last-trade tick (<5 s latency)
      2. fast_info.regularMarketPrice — market mid from info endpoint
      3. history 1m iloc[-1]         — last completed 1-min candle close
                                       (up to 60 s stale — last resort only)

    Previously the code used the 1-min history close as the PRIMARY source
    and fast_info only as a fallback, which meant the displayed price was
    always the CLOSE of the last completed candle — up to 60 seconds old.
    That stale price then fed every signal engine, causing bad entries.
    """
    if not _YF_OK:
        return None
    try:
        t = yf.Ticker(ticker)

        # ── Priority 1 & 2: fast_info — actual live tick, not candle close ──
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for key in ("last_price", "lastPrice",
                        "regularMarketPrice", "regular_market_price"):
                try:
                    v = (fi.get(key) if hasattr(fi, "get")
                         else getattr(fi, key, None))
                    if v is not None:
                        fv = float(v)
                        if fv > 0:
                            return fv
                except Exception:
                    pass

        # ── Priority 3: 1-min history — last completed bar (fallback only) ──
        hist = t.history(period="1d", interval="1m", auto_adjust=False)
        if hist is not None and len(hist) > 0:
            return float(hist["Close"].iloc[-1])

    except Exception as e:
        print(f"[live_prices] yfinance error for {ticker}: {e}")
    return None


def _fetch_yf(ticker: str) -> Optional[float]:
    """Fetch the live price for a ticker, walking the fallback chain.

    For Gold/Silver this means: try spot `XAUUSD=X` first (matches
    TradingView TVC:GOLD), fall back to futures `GC=F` only if Yahoo
    can't quote spot. This is what stops the engine from grading
    SL/TP against stale futures data."""
    price = _fetch_yf_one(ticker)
    if price is not None and price > 0:
        return price
    for fb in _TICKER_FALLBACKS.get(ticker, []):
        price = _fetch_yf_one(fb)
        if price is not None and price > 0:
            return price
    return None


# ── Stooq.com — real-time forex / metals (no API key required) ───────────
# Stooq provides free real-time quotes for all major forex pairs, metals
# and some indices via a public JSON endpoint. It is already used above
# for the metals backup feed; here we extend it to cover ALL forex pairs
# so that if yfinance is slow or returns a stale close, Stooq can serve
# a fresher mid-price instantly.
#
# Ticker format: lowercase base+quote with no separator → e.g. "eurusd"
# Indices: some available as "^ndx" but yfinance is preferred for those.

_STOOQ_FX_CACHE: dict[str, tuple[float, float]] = {}  # stooq_sym → (ts, px)
_STOOQ_FX_TTL = 5.0  # seconds — matches main TTL

_STOOQ_INDEX_MAP = {
    "^NDX":   "^ndx",
    "^DJI":   "^dji",
    "^GSPC":  "^spx",
    "^AXJO":  "^axjo",
}


def _stooq_sym_for_ticker(yf_ticker_str: str) -> Optional[str]:
    """Convert a Yahoo ticker to a stooq symbol, or None if unsupported."""
    t = yf_ticker_str.upper()
    # Forex: EURUSD=X → eurusd
    if t.endswith("=X") and len(t) == 8:
        return t[:-2].lower()
    # Metals already handled by _fetch_spot_metal; skip duplication
    if t in ("PAXG-USD", "SI=F", "GC=F"):
        return None
    # Indices
    if t in _STOOQ_INDEX_MAP:
        return _STOOQ_INDEX_MAP[t]
    return None


def _fetch_stooq(stooq_sym: str) -> Optional[float]:
    """Single stooq.com fetch for a forex/index symbol."""
    now = time.time()
    cached = _STOOQ_FX_CACHE.get(stooq_sym)
    if cached and (now - cached[0]) < _STOOQ_FX_TTL:
        return cached[1]
    body = _http_get(
        f"https://stooq.com/q/l/?s={stooq_sym}&f=sd2t2ohlc&h&e=json",
        timeout=3.5,
    )
    if body:
        try:
            d = json.loads(body)
            row = (d.get("symbols") or [{}])[0]
            # Stooq returns "close" as the last traded price
            px = float(row.get("close", 0))
            if px > 0:
                _STOOQ_FX_CACHE[stooq_sym] = (now, px)
                return px
        except Exception:
            pass
    return None


# ── Stooq micro-momentum — compare two sequential price reads ────────────────
# Used by the 1m binary signal engine as a live-tape direction check when
# all other engines (yfinance, tradingview-ta) are unavailable.

_STOOQ_MOMENTUM_CACHE: dict[str, tuple[float, str, float]] = {}
_STOOQ_MOMENTUM_TTL = 12.0  # seconds — 1m candles need fresh reads


def get_stooq_momentum(pair: str) -> Optional[tuple[str, float]]:
    """Return ('BUY'/'SELL', strength 0-1) from live Stooq price change.

    Compares the currently-cached Stooq price (older read) with a forced-fresh
    fetch to detect real-time micro-movement.  No sleep required — the cache
    already holds a stale price from the last candle cycle.
    Returns None when no Stooq symbol exists or the move is too small to
    determine direction (noise / spread)."""
    yf_tk = yf_ticker(pair)
    stooq_sym: Optional[str] = None
    if yf_tk:
        stooq_sym = _stooq_sym_for_ticker(yf_tk)
    if not stooq_sym:
        # Try to derive from pair name directly (OTC pairs, exotic pairs)
        clean = re.sub(r"[\s\(\)〔〕/]", "", pair.upper())
        clean = re.sub(r"OTC$", "", clean)
        if len(clean) == 6 and clean.isalpha():
            stooq_sym = clean.lower()
    if not stooq_sym:
        return None

    now = time.time()
    cached_mom = _STOOQ_MOMENTUM_CACHE.get(stooq_sym)
    if cached_mom and (now - cached_mom[0]) < _STOOQ_MOMENTUM_TTL:
        return (cached_mom[1], cached_mom[2])

    # p_old = last price already in cache (may be a few seconds old)
    old_entry = _STOOQ_FX_CACHE.get(stooq_sym)
    p_old = old_entry[1] if old_entry else None

    # p_new = forced-fresh Stooq fetch
    _STOOQ_FX_CACHE.pop(stooq_sym, None)
    p_new = _fetch_stooq(stooq_sym)

    if p_old is None or p_new is None or p_old <= 0 or p_new <= 0:
        return None

    pip = pip_size(pair)
    diff = p_new - p_old
    if abs(diff) < pip * 0.5:   # below half a pip — noise / spread
        return None

    direction = "BUY" if diff > 0 else "SELL"
    strength  = min(1.0, abs(diff) / max(pip * 0.0001, pip * 4))
    strength  = max(0.30, strength)
    _STOOQ_MOMENTUM_CACHE[stooq_sym] = (now, direction, strength)
    return (direction, strength)


# ── Binance — real-time crypto prices (no API key, sub-second latency) ──────
# Binance is the world's largest crypto exchange. Their public REST API
# requires no authentication and returns the live last traded price for
# any spot or perpetual-futures symbol with <200ms latency.
#
# Source priority per symbol:
#   A. Binance Futures perp (fapi) → matches TradingView "BTCUSD.P" Binance chart
#   B. Binance Spot               → matches TradingView "BTCUSDT" Binance chart
# Used BEFORE yfinance and CoinGecko so prices are never stale.

_BINANCE_SPOT_MAP: dict[str, str] = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "BNB":  "BNBUSDT",
    "LTC":  "LTCUSDT",
    "BCH":  "BCHUSDT",
    "DOT":  "DOTUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "DASH": "DASHUSDT",
    "ETC":  "ETCUSDT",
    "AXS":  "AXSUSDT",
}

_BINANCE_CACHE: dict[str, tuple[float, float]] = {}  # symbol → (ts, price)
_BINANCE_TTL = 1.0  # seconds — Binance updates ~100ms; 1 s keeps it fresh without hammering


def _fetch_binance(binance_symbol: str) -> Optional[float]:
    """Fetch real-time price from Binance public REST API (no key required).

    Tries Binance Futures perpetual endpoint first (matches TradingView
    BTCUSD.P Binance chart), then falls back to Binance Spot.
    Result cached for 1 second."""
    now = time.time()
    cached = _BINANCE_CACHE.get(binance_symbol)
    if cached and (now - cached[0]) < _BINANCE_TTL:
        return cached[1]

    # Source A — Binance Futures perpetual (TradingView BTCUSD.P)
    body = _http_get(
        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={binance_symbol}",
        timeout=3.0,
    )
    if body:
        try:
            d = json.loads(body)
            px = float(d.get("price", 0))
            if px > 0:
                _BINANCE_CACHE[binance_symbol] = (now, px)
                return px
        except Exception:
            pass

    # Source B — Binance Spot
    body = _http_get(
        f"https://api.binance.com/api/v3/ticker/price?symbol={binance_symbol}",
        timeout=3.0,
    )
    if body:
        try:
            d = json.loads(body)
            px = float(d.get("price", 0))
            if px > 0:
                _BINANCE_CACHE[binance_symbol] = (now, px)
                return px
        except Exception:
            pass

    return None


# ── CoinGecko — real-time crypto prices (no API key required) ────────────
# CoinGecko's public /simple/price endpoint is free, requires no key, and
# returns live prices updated every ~30 s. Used as a fallback for all
# crypto pairs when Binance is unavailable.

_CG_ID_MAP = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "SOL":   "solana",
    "BNB":   "binancecoin",
    "LTC":   "litecoin",
    "BCH":   "bitcoin-cash",
    "DOT":   "polkadot",
    "AVAX":  "avalanche-2",
    "LINK":  "chainlink",
    "DASH":  "dash",
    "ETC":   "ethereum-classic",
    "TON":   "the-open-network",
    "AXS":   "axie-infinity",
}

_CG_CACHE: dict[str, tuple[float, float]] = {}  # cg_id → (ts, px)
_CG_TTL = 8.0  # seconds


def _fetch_coingecko(cg_id: str) -> Optional[float]:
    """Fetch a crypto price from CoinGecko public API (no key needed)."""
    now = time.time()
    cached = _CG_CACHE.get(cg_id)
    if cached and (now - cached[0]) < _CG_TTL:
        return cached[1]
    body = _http_get(
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={cg_id}&vs_currencies=usd",
        timeout=4.0,
    )
    if body:
        try:
            d = json.loads(body)
            px = float(d.get(cg_id, {}).get("usd", 0))
            if px > 0:
                _CG_CACHE[cg_id] = (now, px)
                return px
        except Exception:
            pass
    return None


def _crypto_base(pair: str) -> Optional[str]:
    """Extract the crypto base symbol from a pair string, or None."""
    s = _normalize(pair).replace("/", "").replace("-", "")
    for base in _CG_ID_MAP:
        if s.startswith(base):
            return base
    return None


_GOLD_PAIR_RE = re.compile(r"(XAU|GOLD)", re.IGNORECASE)
_SILVER_PAIR_RE = re.compile(r"(XAG|SILVER)", re.IGNORECASE)


def _spot_metal_for_pair(pair: str) -> Optional[float]:
    """Return real-time spot metal price if `pair` is gold/silver."""
    p = (pair or "").upper()
    if _GOLD_PAIR_RE.search(p):
        return _fetch_spot_metal("XAU")
    if _SILVER_PAIR_RE.search(p):
        return _fetch_spot_metal("XAG")
    return None


def get_live_price(
    pair: str,
    force_fresh: bool = False,
    broker: Optional[str] = None,
) -> Optional[float]:
    """Live mid price for a pair, or None if all sources fail.

    Source priority (each tried in order, first valid price wins):
      0. OTC WebSocket stream         — QX / PO live tick (OTC pairs only)
      1. gold-api.com / stooq metals  — XAU, XAG spot (dedicated feed)
      2. yfinance fast_info.last_price — live tick, <5 s latency
      3. Stooq.com forex/index feed   — real-time, no key required
      4. CoinGecko                    — crypto only, no key required
      5. yfinance history 1m close    — last completed candle (fallback)
      6. Stale in-memory cache        — last known value, prevents None drop

    ``force_fresh=True`` bypasses the in-memory cache so the signal
    engine always starts from a brand-new network fetch."""

    # ── 0. OTC pairs — broker-synthetic prices, NEVER yfinance ────────
    # Broker OTC feeds (Quotex / Pocket Option) are completely synthetic
    # and disconnected from any public exchange. yfinance would return
    # the real-market price for the underlying (or mismap to an entirely
    # different instrument), producing signals based on completely wrong
    # entry prices.  We therefore try broker-native sources ONLY and
    # return None if none are available — never fall to yfinance.
    _is_otc = ("〔OTC〕" in pair or "(OTC)" in pair.upper()
               or "_otc" in pair.lower())
    if _is_otc:
        # Source 0a: live WebSocket tick buffer (QX + PO streams).
        # get_live_otc_price() enforces a 90-second freshness window internally.
        try:
            from otc_price_service import get_live_otc_price
            otc_px = get_live_otc_price(pair, broker=broker)
            if otc_px and otc_px > 0:
                return otc_px
        except Exception:
            pass

        # Source 0b: Pocket Option candle stream (already-authenticated thread).
        if broker in (None, "", "po"):
            try:
                from pocket_option_ws import get_candles as _po_get_candles
                _po_bars = _po_get_candles(pair, 60)
                if _po_bars:
                    _last_close = float(_po_bars[-1].get("close", 0))
                    if _last_close > 0:
                        return _last_close
            except Exception:
                pass

        # A broker-specific OTC request must never fall through to the shared
        # last-writer buffer or public-market bridge. Those sources can belong
        # to the other broker and are not valid as its synthetic OTC price.
        if broker in {"po", "qx"}:
            return None

        # Source 0c: last known broker price — WITH strict age gate.
        #
        # *** BUG FIX: previously returned ANY broker price regardless of age,
        #     so a 10-minute-old price was presented as "live current price".
        #     Fixed: hard 60-second limit, then real-time Stooq bridge, then
        #     5-minute stale fallback as absolute last resort.
        try:
            from otc_price_service import _PRICES, _normalize_pair as _norm, _LOCK
            _key = _norm(pair)
            with _LOCK:
                _entry = dict(_PRICES.get(_key) or {})
            if _entry and _entry.get("price", 0) > 0 and _entry.get("source") != "yf":
                _broker_age = time.time() - _entry.get("time", 0)

                if _broker_age <= 60:
                    # ✅ Fresh broker price — always trust it
                    return float(_entry["price"])

                # Broker price is stale (>60 s). Try Stooq as real-time bridge.
                # OTC forex prices track real-market rates within a tiny spread.
                _otc_sym = _normalize(pair).replace("/", "")
                _is_fx_otc = (len(_otc_sym) == 6 and _otc_sym.isalpha()
                              and not any(c in _otc_sym for c in
                                          ("XAU", "XAG", "XPT", "XPD")))
                if _is_fx_otc:
                    _bridge = _fetch_stooq(_otc_sym.lower())
                    if _bridge and _bridge > 0:
                        return _bridge

                # Metal OTC (XAU/XAG) — use dedicated spot feed as bridge
                _metal_bridge = _spot_metal_for_pair(pair)
                if _metal_bridge and _metal_bridge > 0:
                    return _metal_bridge

                # Last resort: stale broker price up to 5 min
                # (still far more accurate than yfinance for OTC synthetic feeds)
                if _broker_age <= 300:
                    return float(_entry["price"])
        except Exception:
            pass

        # Source 0d: Stooq real-time bridge for OTC forex pairs that NEVER
        # received a broker tick (WS was always down). Stooq's mid-price for
        # major/minor forex is genuine real-time — accurate enough for display
        # and signal direction when the broker stream is unavailable.
        try:
            _otc_sym = _normalize(pair).replace("/", "")
            if len(_otc_sym) == 6 and _otc_sym.isalpha() and not any(
                c in _otc_sym for c in ("XAU", "XAG", "BTC", "ETH", "SOL",
                                        "BNB", "LTC", "DOT", "BCH", "XRP")
            ):
                _stooq_fresh = _fetch_stooq(_otc_sym.lower())
                if _stooq_fresh and _stooq_fresh > 0:
                    return _stooq_fresh
        except Exception:
            pass

        # Source 0e: dedicated spot-metal feed for XAU/XAG OTC
        _metal_otc = _spot_metal_for_pair(pair)
        if _metal_otc and _metal_otc > 0:
            return _metal_otc

        # No usable price at all — do NOT fall through to yfinance for OTC.
        return None

    # ── 1. Spot metals: gold-api.com → stooq metals ────────────────
    spot = _spot_metal_for_pair(pair)
    if spot is not None and spot > 0:
        ticker_key = yf_ticker(pair) or pair.upper()
        _CACHE[ticker_key] = (time.time(), spot)
        return spot

    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    if not force_fresh:
        cached = _CACHE.get(ticker)
        if cached and (now - cached[0]) < _TTL:
            return cached[1]

    # ── 2. Binance — Priority 1 for crypto (sub-second, no key, matches TradingView) ──
    # For BTC/ETH/SOL etc. Binance is the canonical source — it's what
    # TradingView shows as "BTCUSD.P" (Binance perp). Try it BEFORE
    # Stooq / yfinance so crypto prices are never stale.
    crypto_base_early = _crypto_base(pair)
    if crypto_base_early and crypto_base_early in _BINANCE_SPOT_MAP:
        bn_sym = _BINANCE_SPOT_MAP[crypto_base_early]
        bn_px = _fetch_binance(bn_sym)
        if bn_px is not None and bn_px > 0:
            _CACHE[ticker] = (now, bn_px)
            return bn_px

    # ── 3. Stooq.com — genuinely REAL-TIME forex & index (NO delay, NO key) ──
    #
    # *** BUG FIX: previously yfinance was tried FIRST, but Yahoo Finance's
    #     free forex API has a known 15-minute delay — "fast_info.last_price"
    #     often returns the previous candle close, not the live tick.
    #     Stooq.com provides genuinely real-time mid-prices for all major forex
    #     pairs with zero delay and no API key. Now tried BEFORE yfinance.
    stooq_sym = _stooq_sym_for_ticker(ticker)
    if stooq_sym:
        stooq_px = _fetch_stooq(stooq_sym)
        if stooq_px is not None and stooq_px > 0:
            _CACHE[ticker] = (now, stooq_px)
            return stooq_px

    # ── 4. yfinance fast_info.last_price (fallback — may be delayed for forex)
    price = _fetch_yf(ticker)
    if price is not None and price > 0:
        _CACHE[ticker] = (now, price)
        return price

    # ── 5. CoinGecko — crypto fallback (Binance was tried above; this catches outages)
    crypto_base = _crypto_base(pair)
    if crypto_base:
        cg_id = _CG_ID_MAP.get(crypto_base)
        if cg_id:
            cg_px = _fetch_coingecko(cg_id)
            if cg_px is not None and cg_px > 0:
                _CACHE[ticker] = (now, cg_px)
                return cg_px

    # A forced refresh is used for executable entries.  Do not silently
    # relabel an old in-memory quote as a fresh market observation.
    if force_fresh:
        return None

    # ── 6. Stale cache — display-only continuity, never forced entries ──
    cached = _CACHE.get(ticker)
    return cached[1] if cached else None


def get_qualified_market_quote(pair: str) -> Optional[dict]:
    """Return a direct, fresh real-market quote plus auditable provenance.

    OTC synthetic symbols are deliberately rejected here: they require a
    selected-broker quote through ``get_qualified_otc_quote`` instead.
    Delayed yfinance and stale cache fallbacks are intentionally excluded.
    """
    if "〔OTC〕" in pair or "(OTC)" in pair.upper() or "_otc" in pair.lower():
        return None
    ticker = yf_ticker(pair)
    if not ticker:
        return None
    base = _crypto_base(pair)
    if base and base in _BINANCE_SPOT_MAP:
        price = _fetch_binance(_BINANCE_SPOT_MAP[base])
        if price and price > 0:
            return {
                "price": float(price), "source": "Binance spot",
                "source_ts": time.time(), "freshness_sec": 0.0,
            }
    stooq_sym = _stooq_sym_for_ticker(ticker)
    if stooq_sym:
        price = _fetch_stooq(stooq_sym)
        if price and price > 0:
            return {
                "price": float(price), "source": f"Stooq ({stooq_sym})",
                "source_ts": time.time(), "freshness_sec": 0.0,
            }
    return None


def get_chart_view_quote(pair: str, broker: Optional[str] = None) -> Optional[dict]:
    """Return a named chart reference for manual analysis screens.

    Prefer a direct executable quote. When that is unavailable (for example,
    during a weekend session or a temporarily unavailable provider), retain a
    real chart reference as a display-only fallback. Callers must label this
    path clearly and require the user to confirm the terminal price before
    placing an order.
    """
    qualified = get_qualified_market_quote(pair)
    if qualified is not None:
        return {**qualified, "reference_only": False}

    reference_price = get_live_price(pair, broker=broker)
    if reference_price is None or reference_price <= 0:
        return None
    return {
        "price": float(reference_price),
        "source": "Chart-view reference — verify terminal price",
        "source_ts": time.time(),
        "freshness_sec": 60.0,
        "reference_only": True,
    }


def get_qualified_otc_quote(pair: str, broker: str) -> Optional[dict]:
    """Return only a current tick from the user-selected OTC broker."""
    if broker not in {"po", "qx"}:
        return None
    try:
        from otc_price_service import _BROKER_PRICES, _LOCK, _normalize_pair
        key = _normalize_pair(pair)
        with _LOCK:
            item = dict(_BROKER_PRICES.get(key, {}).get(broker) or {})
        timestamp = float(item.get("time") or 0)
        age = time.time() - timestamp
        if item.get("price", 0) <= 0 or not 0 <= age <= 5:
            return None
        return {
            "price": float(item["price"]),
            "source": "Pocket Option broker tick" if broker == "po" else "Quotex broker tick",
            "source_ts": timestamp,
            "freshness_sec": age,
        }
    except Exception:
        return None


def get_market_bias(pair: str, lookback_bars: int = 15
                    ) -> Optional[tuple[str, float]]:
    """Scan the chart and decide the dominant short-term direction.

    Primary  : candle_feed.get_mtf_bias() — TradingView real-time data
               across 1m / 5m / 15m / 1h / 4h / 1d / 1W with weighted
               consensus (longer TF = higher weight).
    Fallback : yfinance 1-minute candle momentum (original logic).

    Returns ('BUY', strength) or ('SELL', strength) where strength is
    0..1, or None if the pair is not supported / no data available.
    Cached per ticker for ~45 seconds."""
    ticker = yf_ticker(pair)
    now = time.time()
    cached = _BIAS_CACHE.get(ticker or pair)
    if cached and (now - cached[0]) < _BIAS_TTL:
        return (cached[1], cached[2])

    # ── Primary: multi-timeframe feed (TradingView → yfinance per TF) ──
    try:
        from candle_feed import get_mtf_bias as _mtf
        mtf = _mtf(pair)
        if mtf and mtf.get("bias") in ("BUY", "SELL") and mtf.get("strength", 0) > 0.1:
            direction = mtf["bias"]
            strength  = float(mtf["strength"])
            _BIAS_CACHE[ticker or pair] = (now, direction, strength)
            return (direction, strength)
    except Exception as _ce:
        print(f"[live_prices] candle_feed error for {pair}: {_ce}")

    # ── Fallback: original yfinance 1-minute momentum ──────────────────
    if not _YF_OK or not ticker:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="1m", auto_adjust=False)
        if hist is None or len(hist) < max(5, lookback_bars // 2):
            return None
        closes = hist["Close"].iloc[-lookback_bars:].tolist()
        first = float(closes[0])
        last  = float(closes[-1])
        if first <= 0:
            return None
        change_pct = (last - first) / first
        if change_pct >= 0:
            confirm   = sum(1 for c in closes[1:] if float(c) >= first)
            direction = "BUY"
        else:
            confirm   = sum(1 for c in closes[1:] if float(c) <= first)
            direction = "SELL"
        move_score  = min(1.0, abs(change_pct) * 800.0)
        ratio_score = confirm / max(1, len(closes) - 1)
        strength    = max(0.15, min(1.0, 0.6 * move_score + 0.4 * ratio_score))
        _BIAS_CACHE[ticker] = (now, direction, strength)
        return (direction, strength)
    except Exception as e:
        print(f"[live_prices] bias error for {ticker}: {e}")
        return None
