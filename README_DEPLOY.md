# SUPREME PRO AI BOT — Deployment Guide

A Telegram bot (Python + aiogram 3) that delivers binary & forex trading
signals with a paid-access model.

## Quick start (any host: Justrunmy.app, PythonAnywhere, Render, Railway, VPS)

1. **Unzip** this archive — keep all files in the same folder.

2. **Create your .env** by copying `.env.example` to `.env`:
   ```
   cp .env.example .env
   ```
   Then open `.env` and fill in:
   - `BOT_TOKEN` — get a fresh one from @BotFather (`/token`)
   - `ADMIN_ID` — your numeric Telegram id (get it from @userinfobot)

3. **Install dependencies**:
   ```
   pip install -r requirements.txt
   ```

4. **Run**:
   ```
   python bot.py
   ```

That's it — the bot will start polling Telegram immediately.

## Justrunmy.app specifics

- Upload the unzipped folder (all files at the root level).
- Set the **start command** to `python bot.py`.
- Add `BOT_TOKEN` and `ADMIN_ID` either in the host's environment-variables
  panel **or** in a `.env` file beside `bot.py` — both work.
- Persistent storage: the bot creates `trading_bot.db` (SQLite) on first
  run. If your host wipes the working directory between restarts, mount a
  persistent volume / use the host's data-directory feature so user data
  survives restarts.

## Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `BOT_TOKEN` | ✅ | — | Telegram bot token from @BotFather |
| `ADMIN_ID` | ✅ | `0` | Your numeric Telegram id (admin powers) |
| `SUPPORT_USERNAME` | – | `@JAYITAUTOBO` | Support chat link |
| `OWNER_USERNAME` | – | `@OAWHIDSHAKIB` | Owner credit |
| `COMMUNITY_BOT` | – | `@TRADERGUIDE_BOT` | Community bot link |
| `SVIP_BOT` | – | `@managementTG_bot` | Forex VIP onboarding bot |
| `REQUIRED_BOT` | – | `@traderguide_bot` | Required-to-start bot |
| `REQUIRED_BOT_ID` | – | `7116421438` | Numeric id of required bot |
| `DAILY_FREE_LIMIT` | – | `4` | Free analyses/day for trial users |

## Files in this bundle

| File | What it does |
|------|--------------|
| `bot.py` | Entry point — wires routers + background tasks |
| `config.py` | Constants, package pricing, pair lists, env reads |
| `database.py` | SQLite schema + every helper function |
| `signals.py` | Binary signal text/image builder |
| `forex_engine.py` | Forex auto-emitter + signal card + simulator |
| `live_prices.py` | Real-time price feed (yfinance) + pip helpers |
| `tz_utils.py` | Per-user timezone (GPS / IANA) helpers |
| `expiry_watcher.py` | Cleans up pinned cards on access expiry |
| `chat_clean.py` | Replaces previous bot screens for a clean chat |
| `keyboards.py` | All inline keyboards (home, TF, TZ picker…) |
| `handlers/` | One router per feature (main_menu, signal, purchase, admin, forex) |
| `assets/` | Green BUY / red SELL signal images |
| `requirements.txt` | Pinned Python deps |
| `.env.example` | Copy → `.env` and fill in real values |
| `Procfile` | `worker: python bot.py` for Heroku-style hosts |
| `runtime.txt` | `python-3.11` for hosts that read it |

## First-run checklist inside Telegram

1. Open your bot in Telegram, tap **/start**.
2. Tap the verify-join button so the welcome screen unlocks.
3. As the admin (the user id you set in `ADMIN_ID`) you'll see
   **🛡️ ADMINISTRATION ACCESS** — use it to grant a test user temporary or
   lifetime access without payment.
4. Tap **🌍 TIMEZONE CHANGE** and pick your zone — every signal time will
   then render in your local clock.

## Troubleshooting

- **`TelegramUnauthorizedError: Unauthorized`** → your `BOT_TOKEN` was
  revoked (BotFather auto-revokes tokens that get posted publicly).
  Get a new one with `/token` in @BotFather and update `.env`.
- **Bot starts but doesn't reply** → make sure the **REQUIRED_BOT** in
  config exists, or set `REQUIRED_BOT` to your own bot's username so the
  verify-gate passes for new users.
- **No signals firing in forex** → users must tap **START FOREX BOT**
  inside the forex menu after picking pairs and a timeframe.
