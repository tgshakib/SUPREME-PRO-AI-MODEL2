"""Regression checks for broker-safe Binary fallback behavior."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from handlers import signal as signal_handler
import otc_price_service as price_service
import signals


class BinaryChartFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        with price_service._LOCK:
            price_service._PRICES.clear()
            price_service._BROKER_PRICES.clear()
            price_service._BROKER_TICKS.clear()

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
        for price in (2.20020, 2.20010, 2.20000):
            price_service._write_price("audchf_otc", price, "qx")

        with patch.object(
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