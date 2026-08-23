---
name: Future Signal source constraint
description: The imported future-signal project's broker adapters are simulated, not live API clients.
---

The locally preserved Future Signal source must not be described as a working
Pocket Option, Quotex, IQ Option, or Olymp Trade API client. Its adapters
generate seeded algorithmic candles when asked for data.

**Why:** The upstream code exposes market choices and adapter classes, but each
adapter's candle path falls back to generated OHLCV rather than making a broker
HTTP or WebSocket request. Treating it as broker-live would mislead users.

**How to apply:** Keep the upstream source isolated from the primary polling
bot. Prefer the project's existing verified local broker feed when fresh data
exists; otherwise label the generated-data fallback clearly. A true broker
integration needs independent authentication and data-feed verification.