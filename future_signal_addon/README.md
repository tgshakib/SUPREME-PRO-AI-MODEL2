# TG Advance Signal Generator — Bot Add-on

This source is integrated with the existing Python bot through a private local
update relay. Python remains the only Telegram polling process; Telegraf owns
the Future Signal menus and handlers.

## Included

- User onboarding and settings
- Real-market and OTC market selection
- Quotex, Pocket Option, IQ Option, Olymp Trade, and real-market adapters
- Signal count options and 1-hour block mode
- CALL, PUT, and MIX direction flows
- Strategy selection and timeframe/timezone settings
- Package/paywall flow
- Binance Pay, USDT TRC20, BTC, BNB BEP20, ETH ERC20, and Solana payment instructions
- Payment screenshot submission and admin approve/reject controls
- Admin assessment and user-management controls
- Compiled `dist/index.mjs` plus the complete TypeScript source

## Integrated runtime

The project workflow runs `relay_launcher.py`, which reuses the current bot
token and admin ID and forces `INTEGRATED_UPDATE_RELAY=1`. In this mode:

- Telegraf never calls `bot.launch()` or Telegram `getUpdates`.
- The local authenticated endpoint receives updates from aiogram.
- The add-on continues to send its own menus, payment flow, administration,
  access screens, settings, and generated signals through the Bot API.
- `/start` remains owned by the Python bot.
- `/TgFuturesignal` and the Home button enter this add-on.

## Important

- Never enable Telegraf polling while the Python workflow is running.
- Do not commit `.env` or paste the token into chat.
- User session state is held in memory, so active in-progress flows reset when this process restarts.
- The included adapters use algorithmic fallback data where official broker APIs are unavailable. This bot cannot guarantee wins, 95–99% accuracy, or risk-free profit. Always validate signals before trading.

## Health check

After startup, open:

```text
http://127.0.0.1:<PORT>/api/healthz
```

A working server returns `{"status":"ok"}`.
