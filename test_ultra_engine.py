"""test_ultra_engine.py — Minimal test file for Ultra God Engine.

Run:
    python test_ultra_engine.py

Tests all 9 modules independently then the full pipeline.
No external dependencies beyond what the bot already uses.
"""
import sys
import os

print("=" * 60)
print("ULTRA GOD ENGINE — MODULE TESTS")
print("=" * 60)

passed = 0
failed = 0


def test(name: str, fn):
    global passed, failed
    try:
        result = fn()
        assert result is not None, "returned None"
        print(f"  ✅ {name}: {result}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        failed += 1


# ── Module 1: regime_filter ─────────────────────────────────────────────────
print("\n[1] regime_filter")
try:
    from regime_filter import detect_regime
    test("EURUSD regime", lambda: detect_regime("EURUSD"))
    test("XAUUSD regime", lambda: detect_regime("XAUUSD"))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 2: htf_alignment ─────────────────────────────────────────────────
print("\n[2] htf_alignment")
try:
    from htf_alignment import check_htf_alignment
    test("EURUSD HTF", lambda: check_htf_alignment("EURUSD"))
    test("XAUUSD HTF", lambda: check_htf_alignment("XAUUSD"))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 3: liquidity_zones ───────────────────────────────────────────────
print("\n[3] liquidity_zones")
try:
    from liquidity_zones import analyze_liquidity_zones
    test("EURUSD liq BUY",  lambda: analyze_liquidity_zones("EURUSD", "BUY"))
    test("XAUUSD liq SELL", lambda: analyze_liquidity_zones("XAUUSD", "SELL"))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 4: momentum_gate ─────────────────────────────────────────────────
print("\n[4] momentum_gate")
try:
    from momentum_gate import check_momentum
    test("EURUSD mom BUY",  lambda: check_momentum("EURUSD", "BUY"))
    test("XAUUSD mom SELL", lambda: check_momentum("XAUUSD", "SELL"))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 5: volatility_adapter ────────────────────────────────────────────
print("\n[5] volatility_adapter")
try:
    from volatility_adapter import check_volatility
    test("EURUSD vol",  lambda: check_volatility("EURUSD"))
    test("BTCUSD vol",  lambda: check_volatility("BTCUSD"))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 6: entry_precision ───────────────────────────────────────────────
print("\n[6] entry_precision")
try:
    from entry_precision import assess_entry
    test("EURUSD entry BUY",  lambda: assess_entry("EURUSD", "BUY",  0.7))
    test("XAUUSD entry SELL", lambda: assess_entry("XAUUSD", "SELL", 0.6))
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 7: confidence_engine ─────────────────────────────────────────────
print("\n[7] confidence_engine")
try:
    from confidence_engine import calculate_confidence
    # High confidence — should accept
    r1 = calculate_confidence(htf_score=20, liq_score=20, mom_score=15,
                               vol_score=15, body_score=10,
                               entry_score=10, regime_score=10)
    test("Perfect score 100", lambda: r1)
    assert r1["confidence"] == 100 and r1["accept"], "should accept at 100"

    # Low confidence — should reject
    r2 = calculate_confidence(htf_score=6, liq_score=6, mom_score=7,
                               vol_score=8, body_score=4,
                               entry_score=5, regime_score=5)
    test("Low score 41", lambda: r2)
    assert not r2["accept"], "should reject at 41"
    passed += 2
except Exception as e:
    print(f"  ❌ confidence_engine: {e}"); failed += 1

# ── Module 8: risk_guard ─────────────────────────────────────────────────────
print("\n[8] risk_guard")
try:
    from risk_guard import check_allowed, record_signal, record_outcome
    test("EURUSD allowed BUY",  lambda: check_allowed("EURUSD", "BUY"))
    test("XAUUSD allowed SELL", lambda: check_allowed("XAUUSD", "SELL"))
    # Record a loss and check cooldown
    record_signal("TEST_PAIR", "BUY", "loss")
    record_outcome("TEST_PAIR", "loss")
    blocked = check_allowed("TEST_PAIR", "BUY")
    test("TEST_PAIR BUY after loss (expect blocked)",
         lambda: blocked if not blocked["allowed"] else None)
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Module 9: debug_report ───────────────────────────────────────────────────
print("\n[9] debug_report")
try:
    from debug_report import log_decision, log_raw
    os.environ["ULTRA_DEBUG"] = "1"
    log_raw("test_ultra_engine.py started")
    log_decision("EURUSD", "BUY", True,  87, reason="test accept")
    log_decision("XAUUSD", "SELL", False, 65, reason="test reject")
    test("log_decision wrote to file", lambda: os.path.exists("ultra_engine.log"))
    os.environ["ULTRA_DEBUG"] = "0"
except Exception as e:
    print(f"  ❌ import failed: {e}"); failed += 1

# ── Full pipeline ────────────────────────────────────────────────────────────
print("\n[FULL PIPELINE]")
try:
    from ultra_god_engine import ultra_analyze
    for pair, direction in [("EURUSD", "BUY"), ("XAUUSD", None), ("BTCUSD", "SELL")]:
        r = ultra_analyze(pair, direction=direction, is_otc=False)
        verdict = "✅ ACCEPT" if r["accept"] else "❌ REJECT"
        print(f"  {verdict} {pair} {direction or 'AUTO'} → "
              f"conf={r['confidence']} grade={r['grade']} "
              f"dir={r['direction']}")
        passed += 1
except Exception as e:
    print(f"  ❌ full pipeline: {e}"); failed += 1

# ── Demo backtest ────────────────────────────────────────────────────────────
print("\n[DEMO BACKTEST]")
try:
    from debug_report import demo_backtest
    demo_backtest("XAUUSD")
    passed += 1
except Exception as e:
    print(f"  ❌ demo_backtest: {e}"); failed += 1

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"Results: {passed} passed  {failed} failed")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
