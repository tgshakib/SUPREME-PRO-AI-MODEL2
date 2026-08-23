"""Regression checks for broker-safe Binary fallback behavior."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from handlers import signal as signal_handler
import otc_price_service as price_service
import self_improve
import signals
import tz_utils


class BinaryChartFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        with price_service._LOCK:
            price_service._PRICES.clear()
            price_service._BROKER_PRICES.clear()
            price_service._BROKER_TICKS.clear()

    @staticmethod
    def _candles(prices: tuple[float, ...], age_sec: int = 30) -> list[dict]:
        stamp = (
            datetime.now(timezone.utc) - timedelta(seconds=age_sec)
        ).strftime("%Y-%m-%d %H:%M:%S")
        return [
            {
                "time": stamp,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 10,
            }
            for price in prices
        ]

    def test_binary_execute_time_uses_utc_offset_label(self) -> None:
        fixed = datetime(2026, 8, 23, 20, 30, 42)
        with patch.object(tz_utils, "get_user_tz", return_value="Asia/Dhaka"), patch.object(
            tz_utils, "now_for_user", return_value=fixed
        ), patch.object(tz_utils, "_utc_offset_str", return_value="UTC+6"):
            self.assertEqual(tz_utils.next_candle_time_for_user(1), "20:31 UTC+6")

    def test_selected_pocket_option_tape_is_not_mirrored(self) -> None:
        for price in (1.10000, 1.10010, 1.10020):
            price_service._write_price("audchf_otc", price, "po")

        with patch.object(
            signals,
            "_chart_view_direction",
            side_effect=AssertionError("selected PO tape should be used first"),
        ), patch.object(
            signals, "next_candle_time_for_user", return_value="15:30 +06"
        ):
            payload = signals.generate_chart_view_binary_fallback(
                "AUD/CHF 〔OTC〕", "PO OTC", "1 MIN", 1, "po"
            )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["direction"], "BUY")

    def test_selected_quotex_tape_never_uses_pocket_option_ticks(self) -> None:
        for price in (1.10000, 1.10010, 1.10020):
            price_service._write_price("audchf_otc", price, "po")
        for price in (2.20050, 2.20040, 2.20030, 2.20020, 2.20010, 2.20000):
            price_service._write_price("audchf_otc", price, "qx")

        with patch(
            "otc_feed_combined.otc_feed.get_candles", return_value=[]
        ), patch.object(
            signals,
            "_chart_view_direction",
            side_effect=AssertionError("selected QX tape should be used first"),
        ), patch.object(
            signals, "next_candle_time_for_user", return_value="15:30 +06"
        ):
            payload = signals.generate_chart_view_binary_fallback(
                "AUD/CHF 〔OTC〕", "QX OTC", "1 MIN", 1, "qx"
            )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["direction"], "SELL")
        self.assertEqual(payload["entry_price"], 2.2)

    def test_quotex_signal_uses_its_own_candles_and_ticks(self) -> None:
        for price in (2.2, 2.2001, 2.2002, 2.2003, 2.2004, 2.2005):
            price_service._write_price("audchf_otc", price, "qx")
        for price in (9.0, 8.9, 8.8, 8.7, 8.6, 8.5):
            price_service._write_price("audchf_otc", price, "po")

        with patch(
            "otc_feed_combined.otc_feed.get_candles",
            return_value=self._candles((2.1, 2.11, 2.12, 2.13, 2.14, 2.15)),
        ) as get_candles, patch.object(
            signals, "next_candle_time_for_user", return_value="20:31 UTC+6"
        ), patch.object(
            signals,
            "_chart_view_direction",
            side_effect=AssertionError("QX must not use public chart direction"),
        ):
            payload = signals.generate_signal(
                "AUD/CHF 〔OTC〕", "QX OTC", "1 MIN", 1, "qx"
            )

        self.assertTrue(payload["is_trade"])
        self.assertEqual(payload["direction"], "BUY")
        self.assertEqual(payload["entry_price"], 2.2005)
        self.assertIn("20:31 UTC+6", payload["text"])
        self.assertEqual(get_candles.call_args.kwargs["broker"], "qx")

    def test_stale_quotex_data_defers_without_public_or_po_fallback(self) -> None:
        for price in (9.0, 8.9, 8.8, 8.7, 8.6, 8.5):
            price_service._write_price("audchf_otc", price, "po")

        with patch(
            "otc_feed_combined.otc_feed.get_candles",
            return_value=self._candles((2.1, 2.11, 2.12, 2.13, 2.14), age_sec=180),
        ), patch.object(
            signals,
            "_chart_view_direction",
            side_effect=AssertionError("QX must not fall back to public chart data"),
        ):
            payload = signals.generate_signal(
                "AUD/CHF 〔OTC〕", "QX OTC", "1 MIN", 1, "qx"
            )

        self.assertFalse(payload["is_trade"])
        self.assertIsNone(payload["direction"])
        self.assertIn("Quotex", payload["text"])

    def test_quotex_outcome_check_uses_quotex_quote_only(self) -> None:
        async def run_check() -> None:
            with patch("asyncio.sleep", new=AsyncMock()), patch(
                "live_prices.get_live_price", return_value=2.3
            ) as get_price, patch.object(
                self_improve.db, "mark_signal_outcome"
            ), patch.object(
                self_improve, "_update_learning"
            ):
                await self_improve._check_and_record_outcome(
                    signal_id=1,
                    pair="AUD/CHF 〔OTC〕",
                    market="QX OTC",
                    direction="BUY",
                    entry_price=2.2,
                    expiry_minutes=1,
                    engine="quotex_native_match",
                    broker="qx",
                )
            get_price.assert_called_once_with("AUD/CHF 〔OTC〕", broker="qx")

        asyncio.run(run_check())

    def test_only_a_delivered_payload_can_be_recorded(self) -> None:
        payload = {"signal_record": {"user_id": 1}}
        with patch.object(signal_handler, "_SI_OK", True), patch.object(
            signal_handler, "_si_record", return_value=42
        ) as record:
            self.assertEqual(signal_handler._record_delivered_signal(payload), 42)
            self.assertEqual(signal_handler._record_delivered_signal({}), -1)
        record.assert_called_once_with(user_id=1)


if __name__ == "__main__":
    unittest.main()