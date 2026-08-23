import os

BOT_TOKEN = "".join(os.environ.get("BOT_TOKEN", "").split()).strip().strip('"').strip("'")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "@JAYITAUTOBO")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "@OAWHIDSHAKIB")
COMMUNITY_BOT = os.environ.get("COMMUNITY_BOT", "@TRADERGUIDE_BOT")
SVIP_BOT = os.environ.get("SVIP_BOT", "@managementTG_bot")
REQUIRED_BOT = os.environ.get("REQUIRED_BOT", "@traderguide_bot")
REQUIRED_BOT_ID = int(os.environ.get("REQUIRED_BOT_ID", "7116421438"))

DAILY_FREE_LIMIT = int(os.environ.get("DAILY_FREE_LIMIT", "4"))
FREE_FOREX_DAILY_LIMIT = 1  # Free forex users: 1 signal per daily setup

BOT_NAME = "SUPREME PRO AI BOT"

# ── Binary OTC pairs ──────────────────────────────────────
OTC_PAIRS = [
    "AUD/CAD 〔OTC〕", "AUD/CHF 〔OTC〕", "AUD/JPY 〔OTC〕", "AUD/NZD 〔OTC〕",
    "AUD/USD 〔OTC〕", "UKBrent 〔OTC〕", "USCrude 〔OTC〕", "USD/ARS 〔OTC〕",
    "USD/BDT 〔OTC〕", "USD/CAD 〔OTC〕", "USD/CHF 〔OTC〕", "USD/COP 〔OTC〕",
    "USD/DZD 〔OTC〕", "USD/EGP 〔OTC〕", "USD/IDR 〔OTC〕", "USD/INR 〔OTC〕",
    "USD/MXN 〔OTC〕", "USD/NGN 〔OTC〕", "USD/PHP 〔OTC〕", "USD/PKR 〔OTC〕",
    "USD/ZAR 〔OTC〕", "Avalanche 〔OTC〕", "American Express 〔OTC〕",
    "Axie Infinity 〔OTC〕", "Boeing Company 〔OTC〕", "Bitcoin Cash 〔OTC〕",
    "Binance Coin 〔OTC〕", "USD/BRL 〔OTC〕", "Bitcoin 〔OTC〕", "CAD/CHF 〔OTC〕",
    "CAD/JPY 〔OTC〕", "CHF/JPY 〔OTC〕", "Dash 〔OTC〕", "Polkadot 〔OTC〕",
    "Ethereum Classic 〔OTC〕", "Ethereum 〔OTC〕", "EUR/AUD 〔OTC〕",
    "EUR/CAD 〔OTC〕", "EUR/CHF 〔OTC〕", "EUR/GBP 〔OTC〕", "EUR/JPY 〔OTC〕",
    "EUR/NZD 〔OTC〕", "EUR/USD 〔OTC〕", "FACEBOOK INC 〔OTC〕", "GBP/AUD 〔OTC〕",
    "GBP/CAD 〔OTC〕", "GBP/CHF 〔OTC〕", "GBP/JPY 〔OTC〕", "GBP/NZD 〔OTC〕",
    "GBP/USD 〔OTC〕", "Intel 〔OTC〕", "Johnson Johnson 〔OTC〕",
    "Chainlink 〔OTC〕", "Litecoin 〔OTC〕", "McDonald's 〔OTC〕", "NZD/CAD 〔OTC〕",
    "NZD/CHF 〔OTC〕", "NZD/JPY 〔OTC〕", "NZD/USD 〔OTC〕", "Pfizer Inc 〔OTC〕",
    "Solana 〔OTC〕", "Toncoin 〔OTC〕", "Trump 〔OTC〕", "Silver 〔OTC〕",
    "Gold 〔OTC〕",
]

# ── Binary LIVE pairs ─────────────────────────────────────
LIVE_PAIRS = [
    "AUD/CAD", "AUD/CHF", "AUD/JPY", "AUD/USD", "CAD/JPY", "CHF/JPY",
    "CAD/CHF", "EUR/AUD", "EUR/CAD", "EUR/CHF",
    "EUR/GBP", "EUR/JPY", "EUR/USD", "GBP/AUD", "GBP/CAD",
    "GBP/CHF", "GBP/JPY", "GBP/USD", "USD/CAD", "USD/CHF",
    "USD/JPY",
]

BINARY_TIMEFRAMES = [
    ("1 Minute", "1m"),
    ("2 Minutes", "2m"),
    ("3 Minutes", "3m"),
    ("5 Minutes", "5m"),
]

# ── Forex 24/7 Trading flow ───────────────────────────────
FOREX_TIMEFRAMES = [
    ("1 Minute", "1m"), ("3 Minutes", "3m"), ("5 Minutes", "5m"),
    ("15 Minutes", "15m"), ("30 Minutes", "30m"),
    ("1 HOUR", "1h"), ("4 HOUR", "4h"),
    ("1 DAY", "1d"), ("1 WEEK", "1w"),
]

FOREX_PAIRS = [
    # ── Majors ────────────────────────────────────────────────
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD",
    "USD/CHF", "NZD/USD",
    # ── Euro crosses ──────────────────────────────────────────
    "EUR/GBP", "EUR/JPY", "EUR/AUD", "EUR/CAD", "EUR/CHF", "EUR/NZD",
    # ── GBP crosses ───────────────────────────────────────────
    "GBP/JPY", "GBP/AUD", "GBP/CAD", "GBP/CHF", "GBP/NZD",
    # ── AUD / NZD crosses ─────────────────────────────────────
    "AUD/JPY", "AUD/CAD", "AUD/CHF", "AUD/NZD",
    "NZD/JPY", "NZD/CAD", "NZD/CHF",
    # ── CAD / CHF crosses ─────────────────────────────────────
    "CAD/JPY", "CAD/CHF", "CHF/JPY",
    # ── Metals ────────────────────────────────────────────────
    "XAU/USD", "XAG/USD", "GOLD", "SILVER",
    # ── Energy / DXY ──────────────────────────────────────────
    "USOIL", "DXY",
    # ── Indices ───────────────────────────────────────────────
    "NAS100", "US100", "DJ30", "SP500",
    # ── Crypto ────────────────────────────────────────────────
    "BTC/USD", "ETH/USD", "BNB/USD", "SOL/USD", "XRP/USD",
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSD", "ETHUSD", "BTC",
]

# Approximate price ranges for synthetic signal pricing per pair
def price_band(pair: str) -> tuple[float, float, int]:
    """Returns (mid_price, pip_size, decimals) per pair."""
    p = pair.upper()
    if "JPY" in p:
        return (150.0, 0.01, 3)
    if "GOLD" in p or "XAUUSD" in p:
        return (3300.0, 0.10, 2)
    if "SILVER" in p or "XAGUSD" in p:
        return (28.0, 0.01, 3)
    if "DXY" in p:
        return (104.0, 0.01, 3)
    if "USOIL" in p or "BRENT" in p:
        return (78.0, 0.01, 3)
    if "BTC" in p:
        return (62000.0, 1.0, 1)
    if "ETH" in p:
        return (3200.0, 0.10, 2)
    if "SOL" in p:
        return (150.0, 0.01, 3)
    if "NAS" in p or "US100" in p:
        return (18000.0, 1.0, 1)
    return (1.10, 0.0001, 5)


TP_LEVELS = [
    ("TP¹ 30+pips",   30),
    ("TP² 40+pips",   40),
    ("TP³ 60+pips",   60),
    ("TP⁴ 80+pips",   80),
    ("TP⁵ 100+pips", 100),
    ("TP⁶ 120pips",  120),
    ("TP⁷ 160+pips", 160),
    ("TP⁸ 220+pips", 220),
    ("🎯 300+ PIPS",  300),
    ("🎯 500+ PIPS",  500),
    ("🎯 900+ PIPS",  900),
]

# Legacy max_tp values (1-6) came from old TP-count format.
# New values (>=10) are pip targets. Use this helper everywhere.
def pip_target_from_max_tp(max_tp: int) -> int:
    """Convert stored max_tp to a pip target.
    Old format  : 1-6  → mapped to legacy pip steps [60,90,130,160,190,250]
    New format  : >=10 → already a pip target
    """
    if max_tp >= 10:
        return int(max_tp)
    legacy = [60, 90, 130, 160, 190, 250]
    idx = max(0, min(max_tp - 1, len(legacy) - 1))
    return legacy[idx]

# ── Binary pricing (MTG / NON-MTG) ────────────────────────
# `was` = 3× current price → shown struck-through so the live `price`
# reads as a luxury discount.
MTG_PACKAGES = [
    {"id": "mtg_6d",   "label": "6 DAYS",     "price": 10,  "was": 30,  "days": 6,    "type": "MTG"},
    {"id": "mtg_14d",  "label": "14 DAYS",    "price": 20,  "was": 60,  "days": 14,   "type": "MTG"},
    {"id": "mtg_30d",  "label": "30 DAYS",    "price": 40,  "was": 120, "days": 30,   "type": "MTG"},
    {"id": "mtg_60d",  "label": "60 DAYS",    "price": 66,  "was": 198, "days": 60,   "type": "MTG"},
    {"id": "mtg_3m",   "label": "3 MONTHS",   "price": 110, "was": 330, "days": 90,   "type": "MTG"},
    {"id": "mtg_6m",   "label": "6 MONTHS",   "price": 170, "was": 510, "days": 180,  "type": "MTG"},
    {"id": "mtg_life", "label": "LIFETIME",   "price": 220, "was": 660, "days": 0,    "type": "MTG"},
]

NONMTG_PACKAGES = [
    {"id": "nmg_6d",   "label": "6 DAYS",     "price": 15,  "was": 45,  "days": 6,   "type": "NON-MTG"},
    {"id": "nmg_30d",  "label": "1 MONTH",    "price": 48,  "was": 144, "days": 30,  "type": "NON-MTG"},
    {"id": "nmg_3m",   "label": "3 MONTHS",   "price": 99,  "was": 297, "days": 90,  "type": "NON-MTG"},
    {"id": "nmg_6m",   "label": "6 MONTHS",   "price": 170, "was": 510, "days": 180, "type": "NON-MTG"},
    {"id": "nmg_life", "label": "LIFETIME",   "price": 270, "was": 810, "days": 0,   "type": "NON-MTG"},
]

# ── Forex VIP (GOLDZILA SVIP) packages ────────────────────
GOLDZILA_PACKAGES = [
    {"id": "gz_7d",   "label": "7 DAYS",      "price": 20,  "days": 7,    "type": "GOLDZILA", "was": 120},
    {"id": "gz_15d",  "label": "15 DAYS",     "price": 30,  "days": 15,   "type": "GOLDZILA", "was": 192},
    {"id": "gz_1m",   "label": "1 MONTH",     "price": 52,  "days": 30,   "type": "GOLDZILA", "was": 476},
    {"id": "gz_3m",   "label": "3 MONTHS",    "price": 70,  "days": 90,   "type": "GOLDZILA", "was": 680},
    {"id": "gz_12m",  "label": "12 MONTHS",   "price": 220, "days": 365,  "type": "GOLDZILA", "was": 2396},
    {"id": "gz_unl",  "label": "UNLIMITED",   "price": 470, "days": 3650, "type": "GOLDZILA", "was": 3596},
]

ALL_PACKAGES = MTG_PACKAGES + NONMTG_PACKAGES + GOLDZILA_PACKAGES


def get_package(pkg_id: str):
    for p in ALL_PACKAGES:
        if p["id"] == pkg_id:
            return p
    return None


# ── FUNDED PASS — prop-firm challenge mode ────────────────
# Account sizes (label shown on button, USD value).
FP_ACCOUNT_SIZES = [
    ("$1.2K",   1200),
    ("$5K",     5000),
    ("$6K",     6000),
    ("$10K",    10000),
    ("$15K",    15000),
    ("$20K",    20000),
    ("$25K",    25000),
    ("$50K",    50000),
    ("$100K",   100000),
    ("$200K",   200000),
]

# Profit targets (% of account)
FP_PROFIT_TARGETS = [3, 5, 6, 10]

# Max DAILY loss (% of account)
FP_DAILY_LOSSES = [3, 5, 6, 10]

# Max OVERALL drawdown (% of account)
FP_MAX_DRAWDOWNS = [1, 3, 5, 6, 10]


PAYMENT_INFO = (
    "💸 <b>Payment Method</b> 💳\n\n"
    "🔸 <b>Binance Pay (Business Official):</b>\n"
    "<code>582355370</code>\n\n"
    "🔸 <b>Crypto (USDT — TRC20):</b>\n"
    "<code>TYudgrH88fCWzNqthy6tXQAieeNcCBYmER</code>\n\n"
    "📌 <i>No hassle of opening a new ID. Just pay and get access — "
    "enjoy SVIP. You will get this bot 100% FREE FULL ACCESS.</i>"
)

# Payment display is split into two pages so wallet addresses remain easy to
# copy on Telegram.  Binance Pay and the legacy USDT method stay unchanged.
PAYMENT_INFO_PAGE_1 = (
    "💸 <b>PAYMENT METHODS · PAGE 1/2</b> 💳\n\n"
    "🔸 <b>Binance Pay:</b>\n<code>582355370</code>\n\n"
    "🔸 <b>USDT — TRC20:</b>\n"
    "<code>TYudgrH88fCWzNqthy6tXQAieeNcCBYmER</code>\n\n"
    "🔸 <b>BTC · Bitcoin:</b>\n"
    "<code>1KgTBewwyvg6wd1F5jy9PKMy3mkvajbaCf</code>\n\n"
    "🔸 <b>BNB Smart Chain · BEP20:</b>\n"
    "<code>0x3dc13af0ff1a7f4585360ab416d35d335afe68e3</code>"
)

PAYMENT_INFO_PAGE_2 = (
    "💸 <b>PAYMENT METHODS · PAGE 2/2</b> 💳\n\n"
    "🔸 <b>Ethereum · ERC20:</b>\n"
    "<code>0x3dc13af0ff1a7f4585360ab416d35d335afe68e3</code>\n\n"
    "🔸 <b>Solana:</b>\n"
    "<code>CuG5iW99W8fKCPyT34Zkgyox2aa7hzyK8eRL3CXBvjXC</code>\n\n"
    "📌 <i>Send only on the selected network. After paying, submit your "
    "screenshot for admin review.</i>"
)
