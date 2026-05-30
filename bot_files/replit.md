# SUPREME PRO AI BOT — Trading Signal Bot

## 📦 Distribution
* **`SUPREME_PRO_AI_BOT.zip`** — password-protected (AES-256) source bundle
  for handing the bot to other hosts. Password: `tgoawhidshakib`. Rebuild
  any time with `python build_locked_zip.py`. The Replit workspace itself
  stays unencrypted so editing here keeps working as normal.
* **Portability**: ships with `Dockerfile`, `Procfile`, `runtime.txt`,
  `start.sh`, `.env.example`, GitHub Actions workflow, and full README —
  runs on JustRunMy, Render, Railway, Fly.io, Heroku, GitHub, any VPS.

## 🎯 PRO V4 Win-Rate Filters (`strategy.py`)
* `MIN_SCORE = 82` — ELITE-only sniper setups
* `RSI_BUY_MIN/MAX = 52..72`, `RSI_SELL_MIN/MAX = 28..48` (tight zones)
* `MIN_BODY_RATIO = 0.58` — trigger candle must be a conviction body
* `MIN_ATR_PCT = 0.0010` — skip dead-chop (ATR < 0.10% of price)
* MTF agreement floor `0.55`
* Binary loss probability `0.03` → ~97% wins

## 🧠 SMART AI ENTRY ENGINE (`trade_entry.py` — NEW in V5)
Direct Python port of the user-supplied Pine v6 indicator
"Trade Entry Signals" (sweep ▸ BoS ▸ MS) with three production-grade
filters layered on top:
* **Sweep detection** — current bar closed beyond prior bar's range
* **BoS confirmation** — next-bar close past the swept level
* **MS shift** — close beyond the prior opposing swing flips direction
* **TRUE BREAKOUT** filter — close cleared by ≥ 0.10 × ATR AND body ≥ 50 %
* **WICK CONFIRMATION** — opposing rejection wick ≥ 35 % of bar range
* **TRENDLINE THEORY** — must break the active descending/ascending
  line through the last 4 pivots
* Returns a 0-100 confluence grade — engine accepts ≥ 80
* Active for **LIVE forex signals** AND **funded-pass live signals**
  (funded-pass LIMIT signals + binary signals are unchanged)

## 🛑 Ghost-SL fix (V5)
* **2-tick confirmation** — SL only marked HIT after two consecutive
  polls show price beyond the level (kills single bad-tick spikes
  from the Yahoo feed)
* **No live feed → wait** — when the price feed is briefly dark we
  do NOT advance with a synthetic random drift (was the original
  Gold ghost-SL source)
* **Timeout never grades as SL** — if the 4 h tracking window
  elapses with no real touch the trade closes as `partial`
  (some TPs hit) or `expired` (none hit), never SL

## 💧 Liquidity / SMC Engine (`liquidity.py` — NEW in V4)
Every forex signal is now anchored to real Smart-Money structure:
* **Swing pivots** = liquidity pools where stops cluster
* **BOS / CHoCH** detection (continuation vs reversal cue)
* **Liquidity sweeps** (stop hunts that flush retail traders)
* **Order blocks** (last opposing candle before BOS — institutional zone)
* **Fair Value Gaps** (3-bar imbalance the market revisits)
* **Liquidity ladder** — TP1…TPn placed at sequential pools
  ("liquidity to liquidity, target to target")

## 🛡️ SL placement rules (PRO V4)
* **SL is placed BEYOND the nearest opposing liquidity pool**
  with a `0.30 × ATR` buffer so wicks don't pick it off
* **SL distance clamps:**
  * Standard forex → **25–60 pips** (per user spec)
  * Metals (XAU/XAG) → ATR-scaled, typically 50-150 pips ($5-$15 on Gold)
    so we don't get killed by gold's normal noise
  * Crypto → ATR-scaled, typically $30-$200 SL
  * Indices → ATR-scaled
* If no clean liquidity is available the signal falls back to a
  default ladder, still SL-clamped — but high-confluence pairs
  with real structure are preferred.


A Telegram bot that delivers binary & forex trading signals with a paid-access
business model. Built with Python + aiogram 3.

## Features

- **Two trading modes**
  - 📊 **BINARY TRADING** → OTC Market (Pocket Option / Quotex) and LIVE Market
  - 💹 **FOREX TRADING** → 21 live currency pairs
- **Pair → Timeframe → Analyze → Signal** flow
  - Timeframes: 1m / 2m / 3m / 5m / "Chart Conditions" (auto)
  - 3–6 sec analysis simulation, then a fully formatted SUPREME PRO signal
- **Daily limits**
  - Free users: 4 signals/day → after that, an upsell screen appears
  - Paid temporary access: unlimited until expiry
  - Lifetime access: unlimited forever
- **Vanish-on-navigate**: every menu click replaces the previous bot message
  (no chat clutter)
- **Buy access**
  - MTG packages: 6d–Lifetime (10$ – 220$)
  - NON-MTG packages: 6d–Lifetime (15$ – 270$)
  - Payment via Binance Pay or USDT-TRC20, screenshot upload, admin review
- **Required-bot gate**: users must "join" `@traderguide_bot` and verify before
  the bot becomes usable
- **Admin panel** (`@admin` chat ID 5087570194)
  - Live stats (users, active, lifetime, pending)
  - List members with access + remaining time
  - List pending payments
  - Remove user access (with auto-notify)
  - Ownership transfer (chat_id + @username)
  - Approve / reject incoming payment screenshots
- **Auto-expiry** background task notifies users when temporary access ends

- **Live prices (yfinance)**
  - Forex pairs auto-mapped to Yahoo tickers (`EURUSD=X`, `USDJPY=X`, …)
  - **Gold (XAU/USD) → `PAXG-USD`** — PAX Gold token, 1:1 backed by physical
    gold, trades 24/7, tracks SPOT gold within ~$1 (matches TradingView
    `TVC:GOLD` and MT5 XAUUSD). Fallback to `GC=F` futures if outage.
    The old `XAUUSD=X` Yahoo symbol was delisted, and the futures contract
    drifts $20+ from spot AND has session breaks that previously caused
    phantom SL hits.
  - Silver → `SI=F`. Indices → `^NDX`, `^DJI`, etc. Crypto → `BTC-USD`, …
  - 30-second per-ticker price cache; trade tracker NEVER advances on
    fake/synthetic prices — if the live feed is dark it waits, and if it
    stays dark for the full window the trade closes as `expired` (not SL).
- **Correct pip math** per symbol class
  - JPY pairs → 0.01
  - XAU / Gold → 0.1
  - XAG / Silver → 0.001
  - Indices / crypto → 1.0
  - Everything else → 0.0001
- **Per-user timezone**
  - `/timezone` command shows a "📍 Share My Location" reply keyboard
  - GPS coordinates → `TimezoneFinder` → IANA name → stored on `users.tz`
  - `/timezone Asia/Dhaka` also works for manual setting
  - All signal timestamps are formatted in the user's local TZ via pytz
  - Default fallback = UTC

## Architecture

```
bot.py                — Entry point. Initializes bot, dispatcher, routers.
config.py             — Constants, pair lists, packages, payment info.
database.py           — SQLite (trading_bot.db). Users, access, payments,
                        signal_log, settings.
signals.py            — Signal-text generator (per-minute deterministic).
keyboards.py          — All inline keyboards (pagination, packages, admin).
chat_clean.py         — show_screen() helper that edits/replaces the active
                        bot message to keep the chat clean.
expiry_watcher.py     — Background loop: pings users when access expires.
handlers/
  main_menu.py        — /start, join-required gate, home/menu navigation.
  signal.py           — Pair → timeframe → analyse → signal flow.
  purchase.py         — Buy access flow + screenshot upload + admin review
                        approve/reject buttons.
  admin.py            — Admin panel: stats, list access, pending payments,
                        remove access, ownership transfer.
```

## Environment Variables

| Key | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token |
| `ADMIN_ID` | Initial admin chat ID |
| `SUPPORT_USERNAME` | Support contact (e.g. `@Jayitautobo`) |
| `OWNER_USERNAME` | Bot owner display username (e.g. `@OAWHIDSHAKIB`) |
| `COMMUNITY_BOT` | Community bot link target |
| `SVIP_BOT` | SVIP auto-join bot link target |
| `REQUIRED_BOT` | Bot users must join before use |
| `DAILY_FREE_LIMIT` | Free signals per day (default 4) |

## Run

```bash
python bot.py
```
The "Start Bot" workflow runs this automatically.

## Database

SQLite file `trading_bot.db` is auto-created. Tables:
- `users` — user records, with `verified` flag
- `access` — granted access (temporary or lifetime, with expiry)
- `payments` — payment submissions w/ screenshot file_id and review status
- `signal_log` — per-day signal usage for daily-limit enforcement
- `settings` — admin_id and per-user "active message id" for vanish-on-navigate

## Notes

- Signals are simulated (per-minute deterministic) — this is a UX/UI bot. No
  real exchange/broker API is connected.
- The "join @traderguide_bot" check is a soft verification — Telegram does not
  expose other-bot membership, so users self-confirm via a button.
