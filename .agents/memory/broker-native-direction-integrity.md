---
name: Broker-native direction integrity
description: Durable rules preventing false OTC direction changes and correlated-engine consensus inflation.
---

Pocket Option OTC directions must never be blanket-inverted from public, Quotex, or underlying-market movement. A bullish streak is continuation until fresh opposite candle evidence confirms exhaustion; ties are WAIT, never SELL by default.

**Why:** Blanket inversion and tie defaults produced a high-confidence SELL while the observed market was still rising. Synthetic OTC relationships are broker- and moment-specific, not a universal inverse mapping.

**How to apply:** Prefer fresh selected-broker ticks when available, but keep the original shared OTC/chart feed as the user-requested fallback for both PO and Quotex. Never claim fallback candles are broker-native.

Safety vetoes must count one vote per independent engine source. Internal grade, zone, or strategy weights may affect confidence, but must not turn one correlated analysis into several independent veto votes.

**Why:** Repeating elite engine directions in a flat vote list allowed one source to manufacture a decisive majority.

**How to apply:** Keep source provenance when constructing final direction gates. Prefer WAIT/no trade over random direction, minute parity, default SELL, or fabricated order-flow evidence.

Explicit broker secrets must override local session caches. Pocket Option's current Socket.IO authentication requires the complete browser `42["auth", {...}]` frame, including `uid`; a token/cookie alone is not sufficient.

**Why:** Old local cache files silently displaced newly configured sessions, and PO accepted the WebSocket upgrade but closed the namespace when sent a token-only auth object. Email/password automation is gated by interactive reCAPTCHA.

**How to apply:** Preserve complete PO auth frames unchanged when sending them. Use the working broker region discovered by a live handshake, require an explicit auth-success event, and never mark a namespace-close response as authenticated.

The user explicitly chose uninterrupted legacy OTC feed fallback for both Pocket Option and Quotex instead of authenticated-only blocking.

**Why:** Authenticated broker sessions can be rejected or unavailable, and the user does not want the bot replaced by selected-broker refresh screens in that state.

**How to apply:** Both broker buttons should continue through the shared first-bot OTC engines and legacy chart recovery when native ticks are missing. Keep card text/layout unchanged and retain no inversion, no tie-to-SELL, and no random direction.