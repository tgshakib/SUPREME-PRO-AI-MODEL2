import asyncio
import logging
import os

# Load a local .env file if present (so users can drop a .env beside bot.py
# on hosts like Justrunmy.app and have it picked up automatically).
def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        logging.warning(f"Could not load .env: {e}")


_load_dotenv()

from keep_alive import keep_alive
keep_alive()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
import database as db
from handlers import main_menu, signal, purchase, admin, forex, funded_pass, ai_scoreboard, auto_trading, referral, futures_signal
from expiry_watcher import run_expiry_watcher
from forex_engine import run_signal_loop
from handlers.funded_pass import run_funded_pass_loop
from handlers.admin import run_mailing_purge_loop
from middleware import AntiSpamMiddleware, UpdateDedupMiddleware, start_cleanup
try:
    from self_improve import recover_pending_outcomes as _si_recover
    _SI_OK = True
except Exception:
    _SI_OK = False
    _si_recover = None  # type: ignore

try:
    from winrate_guardian import run_winrate_guardian as _run_guardian
    _GUARDIAN_OK = True
except Exception:
    _GUARDIAN_OK = False
    _run_guardian = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Silence verbose DEBUG loggers that flood the console
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in env vars.")

    db.init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # ── Anti-spam / dedup middleware ──────────────────────────────────────
    # UpdateDedupMiddleware catches re-delivered updates at the outer level.
    # AntiSpamMiddleware handles per-user cooldown + per-callback dedup +
    # per-user async lock so only one request runs at a time per user.
    dp.update.outer_middleware(UpdateDedupMiddleware())
    dp.callback_query.middleware(AntiSpamMiddleware())
    start_cleanup()   # schedules background set-flush every 60 s

    # Register routers
    dp.include_router(admin.router)        # admin first (FSM input precedence)
    dp.include_router(purchase.router)
    dp.include_router(forex.router)        # forex before main_menu (FSM input)
    dp.include_router(funded_pass.router)  # funded-pass FSM input precedence
    dp.include_router(futures_signal.router)  # isolated Future Signal • TG flow
    dp.include_router(signal.router)
    dp.include_router(ai_scoreboard.router)
    dp.include_router(auto_trading.router)
    dp.include_router(referral.router)
    dp.include_router(main_menu.router)

    # Background tasks
    asyncio.create_task(run_expiry_watcher(bot))
    asyncio.create_task(run_signal_loop(bot))
    # Pocket Option auto-login — gets/refreshes SSID from email+password
    try:
        from po_auth import run_po_auth_manager, get_current_ssid as _po_get_ssid
        _active_ssid = _po_get_ssid()
        if _active_ssid:
            os.environ["PO_SSID"] = _active_ssid
        asyncio.create_task(run_po_auth_manager())
        logger.info("[bot] PO auth manager started")
    except Exception as _po_err:
        logger.warning(f"PO auth manager failed to start: {_po_err}")

    # Quotex auto-login — Chrome-free SSID manager (mirrors PO auth manager)
    try:
        from qx_auth import run_qx_auth_manager, get_current_ssid as _qx_get_ssid
        _qx_ssid = _qx_get_ssid()
        if _qx_ssid:
            os.environ["QUOTEX_SSID"] = _qx_ssid
        asyncio.create_task(run_qx_auth_manager())
        logger.info("[bot] QX auth manager started")
    except Exception as _qx_err:
        logger.warning(f"QX auth manager failed to start: {_qx_err}")

    # OTC live price service — streams QX + PO WebSocket ticks for all OTC pairs
    try:
        from otc_price_service import run_otc_price_service
        asyncio.create_task(run_otc_price_service())
    except Exception as _otc_err:
        logger.warning(f"OTC price service failed to start: {_otc_err}")

    # OTC Combined WebSocket Feed (QX + PO dual-broker live candle stream)
    # Provides real OHLCV candles for all OTC pairs — falls back to existing
    # system silently when not connected.
    try:
        from otc_feed_combined import (
            otc_feed      as _otc_combined_feed,
            ALL_OTC_PAIRS as _OTC_ALL_PAIRS,
            ALL_TIMEFRAMES as _OTC_ALL_TFS,
        )
        _otc_combined_feed.subscribe_all(_OTC_ALL_PAIRS, _OTC_ALL_TFS)
        _otc_combined_feed.start()
        logger.info("[bot] OTC combined WS feed (QX + PO) started — %d pairs",
                    len(_OTC_ALL_PAIRS))
    except Exception as _cfe:
        logger.warning(f"OTC combined feed failed to start: {_cfe}")
    asyncio.create_task(run_funded_pass_loop(bot))
    asyncio.create_task(run_mailing_purge_loop(bot))
    asyncio.create_task(auto_trading.run_midnight_archive())

    # Win Rate Guardian — monitors 2-day win rate, auto-boosts AI thresholds
    if _GUARDIAN_OK and _run_guardian is not None:
        asyncio.create_task(_run_guardian(bot))

    # Self-improve: pick up any outcome checks that were pending before restart
    if _SI_OK and _si_recover is not None:
        asyncio.create_task(_si_recover())

    # AI Guardian — 3 silent agents: WinRateAgent + SSIDGuard + ClaudeAdvisor
    # These agents run completely silently. They NEVER add text to signal cards.
    # Only the admin receives private status pings when the AI acts.
    try:
        from ai_guardian import run_ai_guardian as _run_ai_guardian
        asyncio.create_task(_run_ai_guardian(bot))
        logger.info("[bot] AI Guardian (Agent-1 + Agent-2 + Claude) started silently")
    except Exception as _ag_err:
        logger.warning(f"AI Guardian failed to start: {_ag_err}")

    # Register bot command menu (shown in Telegram "/" list)
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start",  description="▶️ Start bot & open menu"),
        BotCommand(command="futuresignal", description="🔮 Open Future Signal • TG"),
        BotCommand(command="admin",  description="📩 Contact admin for help"),
    ])

    # Clear any pre-existing webhook so polling can run cleanly
    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logger.info(f"🤖 SUPREME PRO AI BOT starting as @{me.username}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
