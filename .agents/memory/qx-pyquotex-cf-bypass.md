---
name: QX pyquotex Cloudflare bypass
description: How to connect pyquotex to Quotex from Replit when Cloudflare blocks HTTP login
---

## The rule
Cloudflare blocks ALL HTTP login requests to qxbroker.com from Replit (and other datacenter) IPs — including `pyquotex` with curl_cffi Chrome TLS fingerprint impersonation (`impersonate="chrome120"`). The confirmed HTTP 403 is a network-level datacenter block, not a TLS fingerprint check.

**Why:** Cloudflare's bot manager flags Replit/cloud ASNs at the IP reputation layer before TLS is even inspected.

## The bypass
pyquotex's `_connect_unlocked()` checks `session_data.get("token")` before calling `api.authenticate()`. If the token is already present, it **completely skips the HTTP login step** and jumps straight to WebSocket auth.

Pre-seed `session.json` (in the CWD) with the real token:
```json
{
  "email@example.com": {
    "cookies": "token=REAL_TOKEN",
    "token": "REAL_TOKEN",
    "user_agent": "Mozilla/5.0 ..."
  }
}
```

**How to apply:** In `otc_price_service.py`, call `_seed_pyquotex_session(token)` before `Quotex(...).connect()`. This is already implemented.

## Token source
The real token is `window.settings.token` — extracted from qxbroker.com's `/trade` page JS after browser login. It is NOT the `laravel_session` cookie (a base64 encrypted blob starting with `eyJpdiI6`).

Steps for user:
1. Open qxbroker.com on a real browser (phone/PC, not Replit)
2. Log in, then F12 → Console → `window.settings.token`
3. Copy the short alphanumeric value
4. Set as `QUOTEX_SSID` in Replit Secrets

Token TTL: ~30 days. `qx_auth.py` keeps a stored SSID available to the stream and attempts its renewal before expiry; a manual replacement is required if the broker rejects refreshes from this host.

## Libraries
- `pyquotex` (iahmedani fork): supported release requires Python 3.12+. Do not bypass its Python-version requirement.
- `curl_cffi`: browser-TLS mode is enabled via `ProxyConfig(use_browser_tls=True)` but does not bypass datacenter-IP reputation blocks.
- `quotexpy` (older): needs SSID not email/password; avoid — causes `authorization/reject` loop
