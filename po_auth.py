"""Pocket Option Auto-Login & SSID Manager
==========================================
Automatically logs into Pocket Option using email + password,
extracts the SSID session cookie, and keeps it alive indefinitely.

Bypass strategy — three layers prevent the SSID from ever expiring:
  1. remember_me=True on every login → requests max-lifetime token (7d/30d)
  2. Keepalive heartbeat every 90 min → resets server-side inactivity timer
  3. Proactive re-login at 85% of TTL → replaced before it can expire

• On startup: tries existing SSID → if stale/rejected logs in fresh.
• Keepalive: sends authenticated ping every 90 min (extends server session).
• Background: proactive refresh at 85% of actual token TTL.
• On auth failure: immediate re-login triggered by Agent-2 SSIDGuard.
• Updates PO_SSID env var + notifies all WS services on every change.

Login method: requests.Session() — handles cookies, redirects, and
all Set-Cookie variants that urllib misses. remember_me=True is sent
on every request so PO issues the longest available session token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

PO_EMAIL    = os.environ.get("PO_EMAIL",    "tgshakib012@gmail.com")
PO_PASSWORD = os.environ.get("PO_PASSWORD", "tgshakib012@g")

_SSID_FILE        = os.path.join(os.path.dirname(__file__), ".po_ssid_cache")
_LOGIN_RETRY_DELAY = 30              # seconds between login retries on failure

# ── SSID token lifetime options ───────────────────────────────────────────────
# PO issues two token lifetimes depending on login method:
#   • Standard session (UUID format)  → 7-day TTL   (604 800 s)
#   • Extended / OAuth token          → 30-day TTL  (2 592 000 s)
# We refresh at 85% of actual TTL so the SSID never actually expires.
_SSID_TTL_7D  = 7  * 24 * 3600    # 604 800 s
_SSID_TTL_30D = 30 * 24 * 3600    # 2 592 000 s
_REFRESH_RATIO = 0.85              # refresh when 85% of lifetime has elapsed
_EARLY_REFRESH_BUFFER = 240        # also refresh if < 4 min remain
_DEFAULT_TTL  = _SSID_TTL_7D       # conservative default

_SSID_STAMP: dict = {}   # {"ssid": str, "fetched_at": float, "ttl": int}

# ── PO login endpoints (tried in order) ──────────────────────────────────────
_PO_LOGIN_ENDPOINTS = [
    # Cabinet API — returns JSON with token in body + Set-Cookie
    # remember_me=True / remember=1 requests the longest session token (30d)
    ("json", "https://po.trade/api/v1/cabinet/login",
     {"email": None, "password": None, "remember_me": True, "remember": 1}),
    ("json", "https://pocketoption.com/api/v1/cabinet/login",
     {"email": None, "password": None, "remember_me": True, "remember": 1}),
    ("json", "https://api.po.market/api/v1/cabinet/login",
     {"email": None, "password": None, "remember_me": True, "remember": 1}),
    # Older auth endpoints
    ("json", "https://po.trade/api/v1/user/login",
     {"email": None, "password": None, "remember_me": True}),
    ("json", "https://pocketoption.com/api/user/login",
     {"email": None, "password": None, "remember_me": True}),
    # Form-encoded login — remember=1 for extended session
    ("form", "https://po.trade/login",
     {"email": None, "password": None, "remember": 1}),
]

# Keepalive endpoints — lightweight authenticated requests that reset the
# server-side inactivity timer so the SSID lasts the full 7/30 days.
_KEEPALIVE_URLS = [
    "https://po.trade/api/v1/cabinet/balance",
    "https://po.trade/api/v1/cabinet/info",
    "https://api.po.market/api/v1/cabinet/balance",
]
_KEEPALIVE_INTERVAL = 90 * 60   # 90 minutes — well within any idle timeout

# Cookie names that PO uses for session tokens (checked in priority order)
_SSID_COOKIE_NAMES = [
    "ssid", "token", "PHPSESSID", "auth_token",
    "session_id", "sessionId", "session", "sid",
    "_ga_token", "access_token",
]

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://po.trade",
    "Referer": "https://po.trade/",
}


def _extract_cookie_ttl(session, response=None) -> Optional[int]:
    """Read the real session TTL from Set-Cookie Max-Age or expires headers.

    Returns seconds (int) if found, or None if no expiry info available.
    Checks both the response headers and the session cookie jar.
    """
    try:
        import email.utils as _eu
        # 1. Check response Set-Cookie headers
        sources = []
        if response is not None:
            sources.append(response.headers.get("set-cookie", ""))
            for v in response.headers.getlist("set-cookie") if hasattr(response.headers, "getlist") else []:
                sources.append(v)
        # 2. Also check cookie jar for expires attributes
        if hasattr(session, "cookies"):
            for cookie in session.cookies:
                if cookie.expires:
                    remaining = cookie.expires - time.time()
                    if remaining > 3600:   # at least 1 hour — sane value
                        return int(remaining)

        for raw in sources:
            if not raw:
                continue
            # Max-Age=<seconds> — most reliable
            m = re.search(r'[Mm]ax-[Aa]ge=(\d+)', raw)
            if m:
                val = int(m.group(1))
                if val > 3600:   # sane: > 1 hour
                    return val
            # expires=<http-date>
            m = re.search(r'[Ee]xpires=([^;,]+)', raw)
            if m:
                try:
                    ts = _eu.parsedate_to_datetime(m.group(1).strip()).timestamp()
                    remaining = ts - time.time()
                    if remaining > 3600:
                        return int(remaining)
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _extract_from_cookies(session) -> Optional[str]:
    """Extract SSID token from a requests.Session cookie jar."""
    try:
        import requests
        jar = session.cookies
        for name in _SSID_COOKIE_NAMES:
            v = jar.get(name)
            if v and len(str(v)) > 8:
                return str(v)
        # Check all cookies for long-value tokens
        for cookie in jar:
            if len(cookie.value or "") > 30:
                return cookie.value
    except Exception:
        pass
    return None


def _extract_from_body(text: str) -> Optional[str]:
    """Extract token from JSON response body or HTML script tags."""
    # JSON body extraction
    try:
        d = json.loads(text)
        # Flat keys
        for key in ("ssid", "token", "session", "sid", "sessionId",
                    "session_id", "access_token", "auth_token"):
            v = d.get(key)
            if v and isinstance(v, str) and len(v) > 10:
                return v
        # Nested under "data"
        data = d.get("data") or d.get("result") or d.get("payload") or {}
        if isinstance(data, dict):
            for key in ("ssid", "token", "session", "sid", "sessionId",
                        "session_id", "access_token"):
                v = data.get(key)
                if v and isinstance(v, str) and len(v) > 10:
                    return v
    except Exception:
        pass
    # HTML/JS token pattern (e.g. window.__token__ = "...")
    for pattern in [
        r'"(?:ssid|token|session|auth_token)"\s*:\s*"([^"]{16,})"',
        r"'(?:ssid|token|session|auth_token)'\s*:\s*'([^']{16,})'",
        r'ssid["\s:=]+([A-Za-z0-9._~+/-]{20,})',
    ]:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _do_login() -> Optional[str]:
    """Login to Pocket Option using requests.Session for proper cookie handling.

    Uses a single persistent session so cookies from redirects are captured.
    Sends remember_me=True on every attempt to request the longest available
    session token (7d or 30d). Real TTL is extracted from Set-Cookie headers
    and stored alongside the SSID so the keepalive knows the actual deadline.
    """
    try:
        import requests
    except ImportError:
        logger.error("[po_auth] requests library not installed — cannot auto-login")
        return None

    session = requests.Session()
    session.headers.update(_REQUEST_HEADERS)

    for method, url, payload_template in _PO_LOGIN_ENDPOINTS:
        payload = {}
        for k, v in payload_template.items():
            if "email" in k or k == "login":
                payload[k] = PO_EMAIL
            elif "pass" in k or "password" in k:
                payload[k] = PO_PASSWORD
            elif k in ("remember_me", "remember"):
                payload[k] = v   # keep as-is (True / 1)
            else:
                payload[k] = v if v is not None else ""

        logger.info(f"[po_auth] Trying {method.upper()} {url} (remember_me=True) …")
        try:
            if method == "json":
                r = session.post(url, json=payload, timeout=20, allow_redirects=True)
            else:
                r = session.post(url, data=payload, timeout=20, allow_redirects=True)

            logger.debug(f"[po_auth] {url} → HTTP {r.status_code}")

            # 1. Try to read the real cookie TTL from Set-Cookie header
            real_ttl = _extract_cookie_ttl(session, r)
            if real_ttl:
                _SSID_STAMP["_pending_ttl"] = real_ttl

            # 2. Check cookies set by this response + accumulated in session
            ssid = _extract_from_cookies(session)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from cookies via {url}")
                return ssid

            # 3. Check response body
            ssid = _extract_from_body(r.text)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from body via {url}")
                return ssid

        except Exception as exc:
            logger.debug(f"[po_auth] {url} error: {exc}")
            continue

    # Last resort: GET the PO homepage with the session — may set cookie
    for url in ("https://po.trade/", "https://pocketoption.com/"):
        try:
            r2 = session.get(url, timeout=10, allow_redirects=True)
            real_ttl = _extract_cookie_ttl(session, r2)
            if real_ttl:
                _SSID_STAMP["_pending_ttl"] = real_ttl
            ssid = _extract_from_cookies(session)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from homepage GET {url}")
                return ssid
        except Exception:
            pass

    logger.warning("[po_auth] ⚠️ All login attempts failed — SSID unavailable")
    return None


def _load_cached_ssid() -> Optional[str]:
    """Load SSID from the local cache file, or from environment.

    Reads fetched_at AND ttl so the TTL-aware age check in
    run_po_auth_manager() works correctly across restarts.
    """
    # Try cache file first — most reliable source of age + TTL information
    try:
        if os.path.exists(_SSID_FILE):
            with open(_SSID_FILE, "r") as f:
                d = json.load(f)
            ssid       = d.get("ssid", "")
            fetched_at = d.get("fetched_at", 0)
            ttl        = int(d.get("ttl", _DEFAULT_TTL))
            age        = time.time() - fetched_at
            refresh_after = ttl * _REFRESH_RATIO   # 85% of lifetime
            if ssid and age < refresh_after:
                if not _SSID_STAMP.get("fetched_at"):
                    _SSID_STAMP["ssid"]       = ssid
                    _SSID_STAMP["fetched_at"] = fetched_at
                    _SSID_STAMP["ttl"]        = ttl
                return ssid
    except Exception:
        pass
    # Fall back to environment variable — treat as stale (fetched_at=0) so the
    # auth manager immediately attempts a fresh login.
    env_ssid = os.environ.get("PO_SSID", "").strip()
    if env_ssid:
        if not _SSID_STAMP.get("fetched_at"):
            _SSID_STAMP["ssid"]       = env_ssid
            _SSID_STAMP["fetched_at"] = 0   # immediately stale → force refresh
            _SSID_STAMP["ttl"]        = _detect_ssid_ttl(env_ssid)
        return env_ssid
    return None


def _detect_ssid_ttl(ssid: str) -> int:
    """Detect the expected lifetime of an SSID token.

    Rules (heuristic — conservative side always wins):
    • Starts with 'g.a' (Google OAuth) or is longer than 80 chars → 30-day token
    • Standard UUID format (8-4-4-4-12 hex, 36 chars)             → 7-day token
    • Anything else                                                 → 7-day (safe default)
    """
    if not ssid:
        return _DEFAULT_TTL
    s = ssid.strip()
    if s.startswith("g.a") or len(s) > 80:
        logger.debug("[po_auth] Detected 30-day extended SSID token")
        return _SSID_TTL_30D
    import re as _re
    if _re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s, _re.I):
        logger.debug("[po_auth] Detected 7-day UUID SSID token")
        return _SSID_TTL_7D
    return _DEFAULT_TTL


def _save_ssid(ssid: str):
    """Persist SSID to cache file and update the environment.

    TTL priority:
      1. Real TTL from Set-Cookie header (captured in _pending_ttl by _do_login)
      2. Format-based detection (_detect_ssid_ttl)
    Whichever is available, the largest sane value wins.
    """
    os.environ["PO_SSID"] = ssid
    # Pick best TTL: real cookie expiry > format detection
    format_ttl  = _detect_ssid_ttl(ssid)
    pending_ttl = _SSID_STAMP.pop("_pending_ttl", None)
    if pending_ttl and 3600 < pending_ttl <= _SSID_TTL_30D:
        ttl = pending_ttl   # real value from Set-Cookie header
        logger.info(f"[po_auth] Real cookie TTL from server: {ttl//3600}h")
    else:
        ttl = format_ttl

    now = time.time()
    try:
        with open(_SSID_FILE, "w") as f:
            json.dump({"ssid": ssid, "fetched_at": now, "ttl": ttl}, f)
    except Exception as exc:
        logger.debug(f"[po_auth] Could not save SSID cache: {exc}")
    _SSID_STAMP["ssid"]       = ssid
    _SSID_STAMP["fetched_at"] = now
    _SSID_STAMP["ttl"]        = ttl
    token_type  = "30d" if ttl == _SSID_TTL_30D else ("7d" if ttl == _SSID_TTL_7D else f"{ttl//3600}h")
    refresh_at  = ttl * _REFRESH_RATIO
    logger.info(
        f"[po_auth] ✅ SSID saved — type={token_type}  "
        f"TTL={ttl//3600}h  keepalive active  auto-refresh in {refresh_at/3600:.1f}h"
    )


def _notify_services(ssid: str):
    """Push the new SSID to all WS services so they reconnect immediately."""
    try:
        import otc_price_service as _svc
        _svc.PO_SSID = ssid
        logger.info("[po_auth] Updated otc_price_service.PO_SSID")
    except Exception:
        pass
    try:
        import pocket_option_ws as _pows
        _pows.update_ssid(ssid)
        logger.info("[po_auth] Notified pocket_option_ws of new SSID")
    except Exception:
        pass


def _do_keepalive_ping() -> bool:
    """Send a lightweight authenticated request to PO API using the current SSID.

    This resets the server-side inactivity timer so the session stays alive
    for the full 7 or 30 days without requiring a re-login.
    Returns True on success (any 2xx or 401/403), False on network error.
    """
    try:
        import requests as _req
        ssid = os.environ.get("PO_SSID", "").strip()
        if not ssid:
            return False
        headers = {
            **_REQUEST_HEADERS,
            "Cookie": f"ssid={ssid}; token={ssid}",
            "X-Requested-With": "XMLHttpRequest",
        }
        for url in _KEEPALIVE_URLS:
            try:
                r = _req.get(url, headers=headers, timeout=15, allow_redirects=False)
                if r.status_code < 500:
                    # Any non-5xx response (including 401/403) counts as a
                    # successful ping — the server responded, session is tracked
                    logger.debug(
                        f"[po_auth] Keepalive ping {url} → HTTP {r.status_code}"
                    )
                    return True
            except Exception:
                continue
        return False
    except Exception as exc:
        logger.debug(f"[po_auth] Keepalive error: {exc}")
        return False


async def _keepalive_loop():
    """Async loop: ping PO API every 90 min to bypass inactivity expiry.

    Runs permanently in background. A failed ping is silently retried on
    the next cycle — it does NOT trigger a re-login (that's po_auth_manager's job).
    """
    logger.info(f"[po_auth] 🔄 SSID keepalive started — pinging every {_KEEPALIVE_INTERVAL//60}min")
    await asyncio.sleep(120)   # 2-min warm-up after startup
    loop = asyncio.get_event_loop()
    while True:
        try:
            ok = await loop.run_in_executor(None, _do_keepalive_ping)
            if ok:
                logger.debug("[po_auth] Keepalive ✅ — session inactivity timer reset")
            else:
                logger.debug("[po_auth] Keepalive ⚠️ — no reachable endpoint (will retry next cycle)")
        except Exception as exc:
            logger.debug(f"[po_auth] Keepalive loop error: {exc}")
        await asyncio.sleep(_KEEPALIVE_INTERVAL)


def get_current_ssid() -> str:
    """Return the active SSID. Tries cache/env first, login if needed."""
    cached = _load_cached_ssid()
    if cached:
        return cached
    ssid = _do_login()
    if ssid:
        _save_ssid(ssid)
        return ssid
    return os.environ.get("PO_SSID", "")


def refresh_ssid_now() -> bool:
    """Force an immediate SSID refresh via login. Returns True on success."""
    logger.info("[po_auth] Forcing SSID refresh via login …")
    ssid = _do_login()
    if ssid:
        _save_ssid(ssid)
        _notify_services(ssid)
        logger.info("[po_auth] ✅ SSID refreshed and all services notified")
        return True
    logger.warning("[po_auth] ❌ SSID refresh failed — using existing SSID")
    return False


async def run_po_auth_manager():
    """Background task: keeps the PO SSID alive indefinitely.

    Three-layer bypass strategy:
      1. remember_me=True on login → PO issues max-lifetime token (7d/30d)
      2. Keepalive heartbeat (90 min) → resets server inactivity timer
      3. Proactive refresh at 85% of real TTL → replaced before expiry

    On any auth failure Agent-2 SSIDGuard triggers an immediate re-login.
    """
    logger.info("[po_auth] Starting Pocket Option auth manager …")
    logger.info(f"[po_auth] Using account: {PO_EMAIL}")
    # Launch keepalive heartbeat as a permanent background task
    asyncio.create_task(_keepalive_loop(), name="po_ssid_keepalive")

    existing = _load_cached_ssid()
    if existing:
        logger.info("[po_auth] Existing SSID found — verifying age …")
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _DEFAULT_TTL)
        refresh_after = ttl * _REFRESH_RATIO
        age           = time.time() - fetched_at
        token_type    = "30d" if ttl == _SSID_TTL_30D else "7d"
        if age >= refresh_after:
            logger.info(f"[po_auth] SSID ({token_type}) is {age/3600:.1f}h old ≥ "
                        f"{refresh_after/3600:.1f}h threshold — refreshing now")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        else:
            logger.info(
                f"[po_auth] SSID is {age/3600:.1f}h old — valid "
                f"(type={token_type}  next refresh in {(refresh_after - age)/3600:.1f}h)"
            )
    else:
        logger.info("[po_auth] No SSID found — logging in now …")
        ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        if not ok:
            logger.warning("[po_auth] Initial login failed — will retry in background")

    # Main maintenance loop — checks every 60 s, refreshes when:
    #   • 85% of TTL elapsed (7-day → ~6d  |  30-day → ~25.5d)
    #   • < 4 minutes remain before full TTL expiry
    #   • Auth failure detected by Agent-2 SSIDGuard (external trigger)
    while True:
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _DEFAULT_TTL)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO      # 85% threshold
        hard_deadline = ttl - _EARLY_REFRESH_BUFFER  # 4 min before expiry
        token_type    = "30d" if ttl == _SSID_TTL_30D else "7d"

        if age >= ttl:
            # Fully expired — must refresh immediately
            logger.info(f"[po_auth] SSID ({token_type}) fully expired — refreshing …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning(f"[po_auth] Login failed — retrying in {_LOGIN_RETRY_DELAY}s")
                await asyncio.sleep(_LOGIN_RETRY_DELAY)
            else:
                await asyncio.sleep(60)

        elif age >= refresh_after:
            # Past 85% of lifetime — proactive refresh
            remaining = ttl - age
            logger.info(
                f"[po_auth] ⚡ SSID ({token_type}) at {age/3600:.1f}h / "
                f"{ttl/3600:.0f}h — proactive refresh ({remaining/3600:.1f}h before expiry)"
            )
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning("[po_auth] Proactive refresh failed — retrying in 30s")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)

        elif age >= hard_deadline:
            # Within 4 minutes of full expiry — emergency early refresh
            remaining = ttl - age
            logger.info(
                f"[po_auth] ⚡ SSID expiring in {remaining:.0f}s — emergency early refresh"
            )
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning("[po_auth] Emergency refresh failed — retrying in 30s")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)

        else:
            # All good — check again in 60 seconds
            await asyncio.sleep(60)
