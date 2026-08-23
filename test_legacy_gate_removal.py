"""Regression checks for the retired legacy signal-gate layer."""

import importlib
import unittest


class LegacyGateRemovalTests(unittest.TestCase):
    def test_binary_path_has_no_legacy_gate_dependencies(self):
        signals = importlib.import_module("signals")
        source = open(signals.__file__, encoding="utf-8").read()
        retired = (
            "binary_master_filter",
            "supreme_quick_engine",
            "thirty_second_engine",
            "ultra_supreme_engine",
            "supreme_binary_gate",
            "binary_volatility_gate",
        )
        for name in retired:
            self.assertNotIn(name, source)

    def test_forex_path_has_no_supreme_forex_gate_dependency(self):
        forex = importlib.import_module("forex_engine")
        source = open(forex.__file__, encoding="utf-8").read()
        self.assertNotIn("supreme_forex_gate", source)


if __name__ == "__main__":
    unittest.main()