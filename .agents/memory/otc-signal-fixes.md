---
name: OTC Signal Direction Fixes
description: Critical fixes to prevent trend-following engines from driving OTC direction
---

## Root Cause of All OTC Losses
OTC synthetic prices (Quotex/Pocket Option) are broker-generated and do NOT reliably
track the real underlying market trend in short timeframes (1-5 minutes). Using any
trend-following engine (EMA crossover, momentum continuation, MACD cross) on OTC causes
systematic losses because the trend visible in yfinance data ≠ the OTC price direction.

## Fixes Applied (signals.py)

### 1. Binary Sniper Blocked for OTC
`binary_sniper_analyze` is a 6-vote trend-following engine (EMA 9/21 cross, RSI zone).
Changed: `if direction is None and bin_sniper is not None and not is_otc:`
Was causing OTC signals to use trend direction when reversal engines didn't fire.

### 2. Vol Sniper Blocked for OTC
`quick_momentum_sniper` is a momentum-continuation engine.
Changed: `elif direction is None and vol_sniper is not None and not is_otc:`

### 3. Chart Conditions Blocked for OTC Fallback
Changed: `if direction is None and _cc_analyze is not None and not is_otc:`
Chart conditions can produce trend-following signals.

### 4. Random Time-Based Fallback De-ranked
The original code had `direction = "BUY" if minute % 2 == 0 else "SELL"` as last resort
for ALL pairs including OTC with `confidence=93`. For OTC this now caps at confidence=95,
elite=False, and `_otc_reversal_drove=False` is tracked.

## OTC Priority Chain (after fixes)
1. PO OTC Engine (reversal — Priority -3)
2. OTC God Engine (26-signal reversal — Priority -2)
3. 1-minute precision sniper (Priority -1)
4. Price Action V9 (candlestick/PA — Priority 0)
5. OTC Reversal Sniper V9 (5+ unanimous votes — Priority 0.5)
6. **QX Expert V10** (13-signal, ≥14 votes — Priority 2.5, now primary OTC driver)
7. Market bias only (no trend engines for OTC)

## OTC Reversal Sniper Threshold (strategy.py)
Raised from 4 votes to **5 votes** minimum (all unanimous, zero opposing).

**Why:** Binary options on OTC have a ~90% payout rate, meaning you need >52.6% win rate
just to break even. Accepting even 1 opposing vote on OTC doubles the false signal rate.
