# SUPREME PRO AI BOT

A high-conviction Telegram trading-signal bot (binary + forex / metals /
crypto / indices) built with **Python 3.11 + aiogram 3**.

---

## ✨ What's inside

* **Sniper PRO V3 engine** — multi-timeframe (4H → 1M) consensus,
  EMA9/21 cross with EMA50 trend filter, body-strength + ATR
  volatility floor, RSI sweet-spot bands, sniper-score ≥ 78.
* **Pattern engine** — Head-and-Shoulders, inverse H&S, Quasimodo and
  inverse-Quasimodo with measured-move targets and quality scoring.
* **Real-time SPOT Gold / Silver** via `gold-api.com` (matches MT5 /
  TradingView TVC:GOLD to the dollar), with `stooq.com` and Yahoo
  Finance as fallbacks. No more phantom SL hits on stale data.
* **Smooth TP ladder** weighted 0.30 / 0.50 / 0.70 / 0.85 / 0.95 / 1.00
  with `PIPS_COMMAND_MIN_RR = 1.8` floor on every signal.
* Paid-access business logic, admin panel, payment review, daily
  free-tier limits, auto-expiry notifications, vanish-on-navigate UX.

---

## 🚀 Deploy anywhere

The bot is a single long-running Python process. It works on **any**
Python 3.11 host — JustRunMy, Render, Railway, Fly.io, Heroku, any
Linux VPS, GitHub Codespaces, Replit. Required env var: `BOT_TOKEN`.

### 1. JustRunMy / Replit / GitHub Codespaces

```bash
bash start.sh
```

(`start.sh` auto-installs `requirements.txt` if needed.)

### 2. Render / Railway / Fly.io / Heroku

These read **`Procfile`**:

```
worker: python bot.py
```

Set `BOT_TOKEN` in the host's environment / secrets panel and deploy.
Python version is pinned via `runtime.txt` (`python-3.11.10`).

### 3. Docker (any cloud, VPS, Kubernetes)

```bash
docker build -t supreme-pro-ai-bot .
docker run -e BOT_TOKEN=123:abc... -v $PWD/data:/app/data supreme-pro-ai-bot
```

### 4. Plain VPS (Ubuntu / Debian)

```bash
git clone <your-repo> supreme-bot && cd supreme-bot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN=123:abc...
python bot.py
```

Run it under **systemd** or **screen/tmux** for 24/7 uptime.

### 5. GitHub Actions (free hosting via scheduled workflow)

Use `.github/workflows/run-bot.yml` (sample provided in
`README_DEPLOY.md`) and store `BOT_TOKEN` in repository secrets.

---

## 🔐 Environment variables

Copy `.env.example` to `.env` and fill in:

| Var               | Required | Notes                             |
| ----------------- | -------- | --------------------------------- |
| `BOT_TOKEN`       | yes      | From `@BotFather`                 |
| `ADMIN_ID`        | yes      | Your numeric Telegram user id     |
| `DAILY_FREE_LIMIT`| no       | Free analyses/user/day (default 4)|

---

## 🧪 Quick sanity check

```bash
python -c "from live_prices import get_live_price as p; print('XAU/USD =', p('XAU/USD', force_fresh=True))"
```

Should print spot Gold within ~$1 of MT5.

---

## 📦 File layout

```
bot.py            # entry point
config.py         # pairs, timeframes, env loading
strategy.py       # PRO V3 sniper engine + MTF + pattern confluence
patterns.py       # H&S, iH&S, QM, iQM detectors
signals.py        # binary signal generator
forex_engine.py   # forex 24/7 engine + smooth TP ladder
live_prices.py    # spot gold/silver + yfinance everything else
database.py       # SQLite wrapper (aiosqlite)
keyboards.py      # all aiogram inline keyboards
expiry_watcher.py # background expiry notifier
chat_clean.py     # vanish-on-navigate helper
tz_utils.py       # timezone helpers
```
