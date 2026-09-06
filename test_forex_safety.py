"""Focused regression tests for forex signal safety gates."""
import unittest
from unittest.mock import AsyncMock, patch

import forex_engine
import forex_quick_engine as quick
import liquidity_zones as zones


class ForexSafetyTests(unittest.TestCase):
    def test_strong_finorix_opposition_needs_stronger_sniper_and_institutional_confirmation(self):
        self.assertFalse(
            forex_engine._can_override_finorix_opposition(
                {"score": 95}, 90, False
            )
        )
        self.assertFalse(
            forex_engine._can_override_finorix_opposition(
                {"score": 90}, 90, True
            )
        )
        self.assertTrue(
            forex_engine._can_override_finorix_opposition(
                {"score": 95}, 90, True
            )
        )

    def test_quick_engine_rejects_split_consensus(self):
        quick._cache.clear()
        smart = {"direction": "BUY", "grade": 90, "ms_shift": True}
        expert = {"direction": "SELL", "confidence": 90}
        with (
            patch.object(quick, "_PRICES_OK", True),
            patch.object(quick, "_SMART_OK", True),
            patch.object(quick, "_smart_analyze", return_value=smart),
            patch.object(quick, "_smart_valid", return_value=True),
            patch.object(quick, "_FX_OK", True),
            patch.object(quick, "_fx_analyze", return_value=expert),
            patch.object(quick, "_SNIPER_OK", False),
            patch.object(quick, "_ELITE_OK", False),
            patch.object(quick, "_get_bias", return_value=None),
        ):
            self.assertIsNone(quick.forex_quick_sniper("EUR/USD"))

    def test_ema_squeeze_alone_is_not_a_fakeout(self):
        zones._CACHE.clear()
        feed = {
            "5m": {"ok": True, "rsi": 50, "buy_v": 0, "sell_v": 0},
            "15m": {
                "ok": True, "rsi": 50, "close": 1.0001,
                "ema20": 1.0000, "ema50": 1.0001,
                "buy_v": 10, "sell_v": 0,
            },
            "1h": {"ok": True, "rsi": 50, "buy_v": 10, "sell_v": 0},
        }
        with patch.object(zones, "_tv", side_effect=lambda _, tf: feed[tf]):
            result = zones.analyze_liquidity_zones("EUR/USD", "BUY")
        self.assertEqual(result["zone_type"], "neutral")
        self.assertNotEqual(result["zone_type"], "fakeout")

    def test_near_zone_requires_measured_price_and_atr(self):
        self.assertFalse(zones._near_measured_zone(1.10, None, None))
        self.assertFalse(zones._near_measured_zone(1.10, 1.10, None))
        self.assertTrue(zones._near_measured_zone(1.10, 1.105, 0.01))


class ForexBackgroundScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_scan_uses_full_depth_engine(self):
        setup = {"user_id": 7, "status": "active"}
        send_signal = AsyncMock(return_value=True)
        with (
            patch.object(forex_engine.db, "get_forex_setup", return_value=setup),
            patch.object(
                forex_engine.db, "list_open_forex_signals", return_value=[]
            ),
            patch.object(forex_engine, "_send_signal", send_signal),
        ):
            sent = await forex_engine.trigger_immediate_scan(object(), 7)

        self.assertTrue(sent)
        send_signal.assert_awaited_once()
        self.assertFalse(send_signal.await_args.kwargs["force_signal"])


if __name__ == "__main__":
    unittest.main()