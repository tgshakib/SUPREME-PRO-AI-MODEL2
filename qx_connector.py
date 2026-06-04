# ================================================================
# qx_connector.py — Quotex WebSocket connector
# No Chrome needed — pure WebSocket auth
# Auto-reconnects forever — lifetime stable
# ================================================================

import os
import asyncio
import logging

logger = logging.getLogger(__name__)

QX_EMAIL    = os.environ.get("QX_EMAIL",    "falgunijakiyakhanom@gmail.com")
QX_PASSWORD = os.environ.get("QX_PASSWORD", "falgunijakiyakhanom@")

try:
    from pyquotex.stable_api import Quotex
    PYQUOTEX_OK = True
    logger.info("✅ pyquotex loaded successfully.")
except ImportError:
    PYQUOTEX_OK = False
    logger.warning("⚠️  pyquotex not installed. Bot runs on synthetic data.")
    logger.warning("    Fix: pip install git+https://github.com/iahmedani/pyquotex.git")


class QXStream:
    """
    Lifetime-stable Quotex OTC stream.
    Pure WebSocket — no Chrome, works on Replit.
    Auto-reconnects on any auth or socket failure.
    """

    def __init__(self):
        self.client      = None
        self.ready       = False
        self._retries    = 0
        self._max_wait   = 60
        self._connecting = False

    async def connect(self):
        if not PYQUOTEX_OK:
            logger.error("pyquotex missing.")
            return False
        if not QX_EMAIL or not QX_PASSWORD:
            logger.error("QX_EMAIL / QX_PASSWORD not set in Replit Secrets.")
            return False
        if self._connecting:
            return False

        self._connecting = True
        while True:
            try:
                logger.info(f"QX connecting (attempt {self._retries + 1})...")
                self.client = Quotex(
                    email=QX_EMAIL,
                    password=QX_PASSWORD,
                    lang="en"
                )
                check, reason = await self.client.connect()
                if check:
                    self.ready       = True
                    self._retries    = 0
                    self._connecting = False
                    logger.info("✅ QX connected and ready.")
                    return True
                else:
                    logger.warning(f"QX auth failed: {reason}")
                    await self._backoff()
            except Exception as e:
                logger.error(f"QX connect error: {e}")
                await self._backoff()

    async def _backoff(self):
        wait = min(5 * (2 ** self._retries), self._max_wait)
        self._retries += 1
        logger.info(f"QX retry in {wait}s...")
        await asyncio.sleep(wait)

    async def ensure_connected(self):
        """Call before every data fetch — auto-heals dropped connections."""
        if not PYQUOTEX_OK: return
        if not self.ready or self.client is None:
            if not self._connecting:
                asyncio.create_task(self.connect())
            await asyncio.sleep(2)
            return
        try:
            if not self.client.check_connect():
                logger.warning("QX socket dropped — reconnecting...")
                self.ready = False
                asyncio.create_task(self.connect())
                await asyncio.sleep(2)
        except Exception:
            self.ready = False
            asyncio.create_task(self.connect())
            await asyncio.sleep(2)

    async def get_candles(self, asset: str, duration: int = 60, count: int = 50):
        """
        Fetch OTC candles.
        asset    : e.g. "EURUSD_OTC"
        duration : candle size seconds (60=M1, 300=M5)
        count    : number of candles
        Returns  : list of OHLCV dicts or []
        """
        await self.ensure_connected()
        if not self.ready: return []
        try:
            candles = await self.client.get_candles(asset, duration, count)
            return self._normalize(candles) if candles else []
        except Exception as e:
            logger.error(f"get_candles ({asset}): {e}")
            self.ready = False
            return []

    async def get_realtime_price(self, asset: str):
        """Latest tick price."""
        await self.ensure_connected()
        if not self.ready: return None
        try:
            return await self.client.get_realtime_price(asset)
        except Exception as e:
            logger.error(f"realtime_price ({asset}): {e}")
            self.ready = False
            return None

    def _normalize(self, raw):
        """Convert pyquotex format → standard OHLCV."""
        out = []
        for c in raw:
            try:
                out.append({
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": int(c.get("volume",  c.get("v", 0))),
                })
            except Exception:
                continue
        return out

    async def disconnect(self):
        if self.client:
            try: self.client.close()
            except Exception: pass
        self.ready = False

    # ── Timeframe map ─────────────────────────────────────────
    TF_SECONDS = {
        "M1": 60, "M5": 300, "M15": 900,
        "M30": 1800, "H1": 3600, "H4": 14400,
    }

    # ── All QX OTC pairs ──────────────────────────────────────
    OTC_PAIRS = [
        "EURUSD_OTC","GBPUSD_OTC","USDJPY_OTC","USDCHF_OTC",
        "AUDUSD_OTC","NZDUSD_OTC","USDCAD_OTC","EURGBP_OTC",
        "EURJPY_OTC","GBPJPY_OTC","AUDJPY_OTC","EURAUD_OTC",
        "EURCHF_OTC","GBPCHF_OTC","CADCHF_OTC","CADJPY_OTC",
        "NZDJPY_OTC","AUDNZD_OTC","AUDCAD_OTC","AUDCHF_OTC",
        "GBPAUD_OTC","GBPCAD_OTC","GBPNZD_OTC","EURCAD_OTC",
        "EURNZD_OTC","NZDCAD_OTC","NZDCHF_OTC","CHFJPY_OTC",
        "USDBDT_OTC","USDMXN_OTC","USDZAR_OTC","USDSGD_OTC",
        "USDINR_OTC","USDTRY_OTC","USDRUB_OTC","USDNOK_OTC",
        "USDSEK_OTC","USDDKK_OTC","USDPLN_OTC","USDCZK_OTC",
        "USDHUF_OTC","USDRON_OTC","BTCUSD_OTC","ETHUSD_OTC",
        "LTCUSD_OTC","XRPUSD_OTC","BCHUSD_OTC","XAUUSD_OTC",
        "XAGUSD_OTC","AAPL_OTC","AMZN_OTC","GOOGL_OTC",
        "MSFT_OTC","TSLA_OTC","META_OTC","NVDA_OTC",
        "SPX500_OTC","NAS100_OTC",
    ]


# ── Singleton — import in main.py ─────────────────────────────
qx = QXStream()
