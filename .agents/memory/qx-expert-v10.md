---
name: QX Expert V10 Supreme Engine
description: Why qx_expert.py was completely rewritten and what the new thresholds are
---

## The Critical Bug (Pre-V10)
The old `qx_analyze` fired with as few as **1 vote** when no opposing votes existed:
- `total = buy_votes + sell_votes`, if buy=1, sell=0 → ratio=1.0 → grade=100
- OTC gate was: `opposing > 0` → reject. With 1 vote and 0 opposing → ALWAYS passed.
- Result: single-indicator signals (RSI barely touching 28/72) fired with grade=100 "elite".

## New V10 Architecture (13 signals, max 32 votes)
- S01 RSI(3) ultra-fast: ≤10=+3, ≤20=+2, ≥80=-2, ≥90=-3
- S02 RSI(7): ≤20=+2, ≤30=+1, ≥70=-1, ≥80=-2
- S03 RSI(14): ≤28=+2, ≥72=-2
- S04 RSI Divergence: price new extreme + RSI diverges = ±3 (most powerful)
- S05 Stoch(3,1,1) ultra: crossover in <20/>80 = ±3
- S06 Stoch(5,3,3): crossover in <25/>75 = ±2
- S07 CCI(14): ≤-150 turning = +2, ≥150 turning = -2
- S08 Williams %R: ≤-88 turning = +2, ≥-12 turning = -2
- S09 BB(20, 2.5σ) pierce+bounce = ±3
- S10 Consecutive exhaustion (4+ candles) + reversal bar = ±4
- S11 Candlestick patterns (pin bar, engulfing) = ±2
- S12 HA reversal (flip after 3+ same-color bars) = ±2
- S13 MACD(5,13,3) histogram exhaustion = ±2

## New Thresholds
- **OTC**: ≥14 votes agree, opposing ≤1, grade ≥78
- **Live**: ≥11 votes agree, opposing ≤2, grade ≥70
- **Elite OTC**: ≥20 votes, opposing=0
- **Non-reprint**: bar[-3] must agree with bar[-2] for OTC
- **Cache TTL**: 18s (was 90s)

**Why:** The old engine's grade formula `60 + 40*(ratio-0.5)/0.5` gave grade=100 for ANY ratio=1.0 regardless of absolute vote count. With zero opposing, ratio is always 1.0, so even 1 vote → grade=100.
