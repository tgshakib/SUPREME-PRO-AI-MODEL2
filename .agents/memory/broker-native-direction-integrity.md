---
name: Broker-native direction integrity
description: Durable rules preventing false OTC direction changes and correlated-engine consensus inflation.
---

Pocket Option OTC directions must never be blanket-inverted from public, Quotex, or underlying-market movement. A bullish streak is continuation until fresh opposite candle evidence confirms exhaustion; ties are WAIT, never SELL by default.

**Why:** Blanket inversion and tie defaults produced a high-confidence SELL while the observed market was still rising. Synthetic OTC relationships are broker- and moment-specific, not a universal inverse mapping.

**How to apply:** Only executable PO OTC analysis may use fresh PO-filtered ticks/candles. Public data can inform diagnostics but must not be promoted as PO-native execution evidence. Quotex remains authenticated-native-only.

Safety vetoes must count one vote per independent engine source. Internal grade, zone, or strategy weights may affect confidence, but must not turn one correlated analysis into several independent veto votes.

**Why:** Repeating elite engine directions in a flat vote list allowed one source to manufacture a decisive majority.

**How to apply:** Keep source provenance when constructing final direction gates. Prefer WAIT/no trade over random direction, minute parity, default SELL, or fabricated order-flow evidence.