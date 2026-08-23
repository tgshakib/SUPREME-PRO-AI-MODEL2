"""Quotex Auto-SSID Manager
============================
Mirrors the Pocket Option auth manager (po_auth.py) for Quotex.

AUTO-LOGIN STRATEGY (same as PO — three bypass layers):
  1.  pyquotex WebSocket login — email + password → bypasses Cloudflare
      Turnstile entirely (pyquotex uses WS auth, not HTTP).  This is the
      primary auto-login path (no manual token needed).
  2.  Digest endpoint — refresh via /api/v1/cabinets/digest when a token
      is already loaded (extends lifetime without re-login).
  3.  Manual fallback — QUOTEX_SSID env var (Replit Secrets).

TOKEN LIFECYCLE (identical to po_auth.py):
  • remember_me login → pyquotex issues a 30-day WS session token.
  • Keepalive ping every 90 min → resets server inactivity timer.
  • Proactive refresh at 85% of TTL → replaced before it can expire.
  • On WS rejection → immediate re-login attempted automatically.

Credentials:
   QUOTEX_EMAIL    environment variable
   QUOTEX_PASSWORD environment variable
   They may be loaded from the local .env or Replit Secrets. No credentials
   are stored as source-code defaults.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

QX_EMAIL    = os.environ.get("QUOTEX_EMAIL", "").strip()
QX_PASSWORD = os.environ.get("QUOTEX_PASSWORD", "").strip()

_SSID_FILE         = os.path.join(os.path.dirname(__file__), ".qx_ssid_cache")
_LOGIN_RETRY_DELAY = 60   # seconds between retries

# ── Token lifetime ────────────────────────────────────────────────────────────
# Quotex tokens issued from window.settings.token last approximately 30 days
# when remember_me is active.  We use a conservative 7-day default and probe
# the actual expiry via the digest endpoint when possible.
_SSID_TTL_DEFAULT  = 7  * 24 * 3600    # 7 days (safe conservative)
_SSID_TTL_30D      = 30 * 24 * 3600    # 30 days (extended)
_REFRESH_RATIO     = 0.85              # refresh at 85% of lifetime
_EARLY_REFRESH_BUFFER = 240            # also refresh if < 4 min remain

_SSID_STAMP: dict = {}   # {"ssid": str, "fetched_at": float, "ttl": int}
_LAST_LOGIN_ATTEMPT = 0.0
_LOGIN_ATTEMPT_COOLDOWN = 60.0

# Public flag — set True by the stream when WS sends "authorization/reject"
QX_SSID_REJECTED: bool = False

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Origin": "https://qxbroker.com",
    "Referer": "https://qxbroker.com/en/trade",
}

# ── Token validation ──────────────────────────────────────────────────────────
def _is_real_qx_token(token: str) -> bool:
    """Return True only if this looks like a real Quotex API token.

    Rejects:
      • Laravel encrypted session cookies — base64 JSON blobs starting with
        eyJpdiI6 ({"iv":"...}) or ending with %3D / %3D%3D (URL-encoded =)
      • Very short strings (< 20 chars)
      • Cloudflare tokens (__cf_bm, _cfuvid prefix patterns)

    Accepts:
      • Long alphanumeric tokens (the real window.settings.token format)
      • UUID-style tokens
    """
    if not token or len(token) < 20:
        return False
    # Laravel encrypted session always starts with eyJpdiI6 when base64-decoded
    # In raw form the cookie value starts with eyJpdiI6 (base64 of {"iv":)
    if token.startswith("eyJpdiI6"):
        return False
    # URL-encoded Laravel cookies end with %3D
    if "%3D" in token and len(token) > 100:
        return False
    # Cloudflare tokens
    if token.startswith("__cf") or token.startswith("_cf"):
        return False
    return True


# ── quotexpy sessions.pkl ─────────────────────────────────────────────────────
def _qxpy_sessions_path() -> Optional[str]:
    try:
        from quotexpy.utils import sessions_file_path
        return sessions_file_path
    except Exception:
        return None


def _seed_qxpy_session(ssid: str, cookies: str = ""):
    """Write the SSID into quotexpy's sessions.pkl so Chrome is never called."""
    path = _qxpy_sessions_path()
    if not path:
        return
    try:
        data: dict = {}
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
            except Exception:
                data = {}
        data[QX_EMAIL] = [{
            "ssid":       ssid,
            "cookies":    cookies or f"token={ssid}",
            "user_agent": _REQUEST_HEADERS["User-Agent"],
        }]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(data, f)
        logger.debug("[qx_auth] quotexpy sessions.pkl seeded — Chrome bypass active")
    except Exception as exc:
        logger.debug(f"[qx_auth] Could not seed sessions.pkl: {exc}")


# ── pyquotex WebSocket auto-login (bypasses Cloudflare entirely) ─────────────
def _do_pyquotex_login() -> Optional[str]:
    """Login to Quotex via pyquotex WebSocket — no Chrome, no Cloudflare block.

    Spawns an isolated daemon thread with its own asyncio event loop so this
    function is safe to call from both sync and async contexts (e.g. from a
    run_in_executor thread while the main event loop is running).

    Returns the raw session token string on success, None on failure.
    """
    import threading
    global _LAST_LOGIN_ATTEMPT

    if not QX_EMAIL or not QX_PASSWORD:
        logger.error("[qx_auth] QUOTEX_EMAIL and QUOTEX_PASSWORD are not configured")
        return None
    now = time.monotonic()
    if now - _LAST_LOGIN_ATTEMPT < _LOGIN_ATTEMPT_COOLDOWN:
        logger.debug("[qx_auth] Auto-login is already in progress or recently attempted")
        return None
    _LAST_LOGIN_ATTEMPT = now

    try:
        from pyquotex.stable_api import ProxyConfig, Quotex as _Quotex
    except ImportError:
        logger.debug("[qx_auth] pyquotex not installed — skipping WS login")
        return None

    logger.info("[qx_auth] 🔐 Attempting pyquotex auto-login …")

    _token_out: list = [None]

    async def _work():
        """Full connect → extract → close inside one async task."""
        import pickle as _pk
        from pathlib import Path as _Pt

        try:
            proxy_config = ProxyConfig(use_browser_tls=True)
        except Exception:
            proxy_config = None
        client = _Quotex(
            email=QX_EMAIL,
            password=QX_PASSWORD,
            lang="en",
            proxy_config=proxy_config,
        )
        try:
            check, reason = await asyncio.wait_for(client.connect(), timeout=35)
        except asyncio.TimeoutError:
            logger.warning("[qx_auth] pyquotex connection timed out after 35 seconds")
            try:
                await asyncio.wait_for(client.close(), timeout=4)
            except Exception:
                pass
            return
        except Exception as exc:
            detail = " ".join(str(exc).split())
            detail = detail.replace(QX_EMAIL, "[redacted email]")
            detail = detail.replace(QX_PASSWORD, "[redacted password]")
            detail = re.sub(
                r"(?i)\b(token|session|cookie)\s*[=:]\s*\S+",
                r"\1=[redacted]",
                detail,
            )[:160]
            logger.warning(
                "[qx_auth] pyquotex connection failed (%s): %s",
                type(exc).__name__,
                detail or "no broker detail",
            )
            try:
                await asyncio.wait_for(client.close(), timeout=4)
            except Exception:
                pass
            return

        if not check:
            safe_reason = str(reason).replace(QX_EMAIL, "[redacted email]")[:160]
            logger.warning("[qx_auth] pyquotex authentication was rejected: %s", safe_reason)
            try:
                await asyncio.wait_for(client.close(), timeout=4)
            except Exception:
                pass
            return

        logger.info("[qx_auth] ✅ pyquotex WS connected — extracting token …")

        # Give handshake a moment to persist the session file
        await asyncio.sleep(1.5)

        token: Optional[str] = None

        # ── 1. sessions.pkl (pyquotex persists the token here) ────────────
        def _read_pkl() -> Optional[str]:
            sp = _qxpy_sessions_path()
            if not sp:
                return None
            try:
                if _Pt(sp).exists():
                    with open(sp, "rb") as f:
                        d = _pk.load(f)
                    entries = d.get(QX_EMAIL, [])
                    if entries and isinstance(entries, list):
                        e = entries[0]
                        return e.get("ssid") or e.get("token")
            except Exception as exc:
                logger.debug(f"[qx_auth] sessions.pkl read: {exc}")
            return None

        # ── 1. session.json (current pyquotex persistence format) ─────────
        def _read_session_json() -> Optional[str]:
            try:
                path = _Pt("session.json")
                if not path.exists():
                    return None
                data = json.loads(path.read_text(encoding="utf-8"))
                record = data.get(QX_EMAIL, {}) if isinstance(data, dict) else {}
                if not isinstance(record, dict):
                    return None
                candidate = record.get("token") or record.get("ssid")
                return candidate if isinstance(candidate, str) else None
            except Exception as exc:
                logger.debug(f"[qx_auth] session.json read: {exc}")
                return None

        token = await asyncio.get_event_loop().run_in_executor(None, _read_session_json)

        # ── 2. Legacy sessions.pkl used by older Quotex clients ───────────
        if not token:
            token = await asyncio.get_event_loop().run_in_executor(None, _read_pkl)

        # ── 3. Client / api attribute walk ────────────────────────────────
        if not token:
            for obj in (client, getattr(client, "api", None)):
                if obj is None:
                    continue
                for attr in ("token", "ssid", "session_id", "auth_token", "_token"):
                    try:
                        v = getattr(obj, attr, None)
                        if v and isinstance(v, str) and _is_real_qx_token(v):
                            token = v
                            break
                    except Exception:
                        pass
                if token:
                    break

        # ── 4. Second persistence attempt after extra delay ───────────────
        if not token:
            await asyncio.sleep(2)
            token = await asyncio.get_event_loop().run_in_executor(None, _read_session_json)
        if not token:
            token = await asyncio.get_event_loop().run_in_executor(None, _read_pkl)

        try:
            await asyncio.wait_for(client.close(), timeout=4)
        except Exception:
            pass

        if token and _is_real_qx_token(token):
            logger.info("[qx_auth] ✅ Token extracted via pyquotex WS login")
            _token_out[0] = token
        else:
            logger.debug("[qx_auth] pyquotex connected but token not extractable")

    def _thread_main():
        try:
            asyncio.run(_work())
        except Exception as exc:
            logger.warning(
                "[qx_auth] pyquotex login worker stopped (%s)",
                type(exc).__name__,
            )

    t = threading.Thread(target=_thread_main, daemon=True, name="qx_pyquotex_login")
    t.start()
    t.join(timeout=50)   # 35 s connect + ~12 s extraction + margin

    return _token_out[0]


# ── Digest-based token refresh (works if session cookies are valid) ────────────
def _try_digest_refresh(current_ssid: str) -> Optional[str]:
    """Try GET /api/v1/cabinets/digest with the current SSID as cookie.

    Returns a fresh token string if the server accepts it, None otherwise.
    This endpoint works when the Cloudflare session is still valid.
    """
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()

        headers = {
            **_REQUEST_HEADERS,
            "Cookie": f"token={current_ssid}; session={current_ssid}",
            "Accept": "application/json",
        }
        for url in [
            "https://qxbroker.com/api/v1/cabinets/digest",
            "https://qxbroker.com/api/v1/cabinet/digest",
        ]:
            try:
                r = requests.get(url, headers=headers, timeout=12, verify=False)
                if r.status_code == 200:
                    try:
                        d = r.json()
                        # {"data": {"token": "NEW_TOKEN"}} or {"token": "..."}
                        tok = (
                            (d.get("data") or {}).get("token")
                            or d.get("token")
                        )
                        if tok and isinstance(tok, str) and len(tok) > 20:
                            logger.info(f"[qx_auth] ✅ Token refreshed via digest {url}")
                            return tok
                    except Exception:
                        pass
                elif r.status_code == 401:
                    logger.debug(f"[qx_auth] Digest {url} → 401 (token expired)")
                    return None
            except Exception as exc:
                logger.debug(f"[qx_auth] Digest {url} error: {exc}")
    except ImportError:
        pass
    return None


# ── Cache / persistence ───────────────────────────────────────────────────────
def _detect_token_ttl(token: str) -> int:
    """Heuristic TTL detection: long tokens → 30d, otherwise 7d."""
    if not token:
        return _SSID_TTL_DEFAULT
    if len(token.strip()) > 80:
        return _SSID_TTL_30D
    return _SSID_TTL_DEFAULT


def _save_ssid(ssid: str, ttl: Optional[int] = None, cookies: str = ""):
    """Persist SSID to cache file, env var, and quotexpy sessions.pkl."""
    os.environ["QUOTEX_SSID"] = ssid
    real_ttl = ttl if (ttl and 3600 < ttl <= _SSID_TTL_30D) else _detect_token_ttl(ssid)
    now = time.time()
    try:
        with open(_SSID_FILE, "w") as f:
            json.dump({"ssid": ssid, "fetched_at": now, "ttl": real_ttl, "cookies": cookies}, f)
    except Exception as exc:
        logger.debug(f"[qx_auth] Could not save cache: {exc}")
    _SSID_STAMP.update({"ssid": ssid, "fetched_at": now, "ttl": real_ttl})
    _seed_qxpy_session(ssid, cookies)
    _notify_services(ssid)
    token_type = "30d" if real_ttl >= _SSID_TTL_30D else f"{real_ttl//3600}h"
    logger.info(
        f"[qx_auth] ✅ QX SSID saved — type={token_type}  "
        f"auto-refresh in {real_ttl * _REFRESH_RATIO / 3600:.1f}h"
    )


def _load_cached_ssid() -> Optional[str]:
    """Load SSID from cache file or env var.

    Validates that the token is a real Quotex API token — rejects Laravel
    encrypted session cookies (eyJpdiI6...) that Cloudflare hands out.
    """
    # 1. Cache file
    try:
        if os.path.exists(_SSID_FILE):
            with open(_SSID_FILE, "r") as f:
                d = json.load(f)
            ssid       = d.get("ssid", "")
            fetched_at = d.get("fetched_at", 0)
            ttl        = int(d.get("ttl", _SSID_TTL_DEFAULT))
            age        = time.time() - fetched_at
            if ssid and _is_real_qx_token(ssid) and age < ttl * _REFRESH_RATIO:
                if not _SSID_STAMP.get("fetched_at"):
                    _SSID_STAMP.update({"ssid": ssid, "fetched_at": fetched_at, "ttl": ttl})
                return ssid
            elif ssid and not _is_real_qx_token(ssid):
                logger.warning(
                    "[qx_auth] ⚠️  Cached SSID looks like a Laravel session cookie — ignoring. "
                    "Set QUOTEX_SSID in Replit Secrets with the real token from window.settings.token"
                )
                try:
                    os.remove(_SSID_FILE)
                except Exception:
                    pass
    except Exception:
        pass
    # 2. Env var (manual secret)
    env_ssid = os.environ.get("QUOTEX_SSID", "").strip()
    if env_ssid:
        if not _is_real_qx_token(env_ssid):
            logger.warning(
                "[qx_auth] ⚠️  QUOTEX_SSID env var looks like a Laravel session cookie — ignoring. "
                "Copy the real token from window.settings.token in your browser's DevTools."
            )
            return None
        if not _SSID_STAMP.get("fetched_at"):
            ttl = _detect_token_ttl(env_ssid)
            _SSID_STAMP.update({"ssid": env_ssid, "fetched_at": 0, "ttl": ttl})
        return env_ssid
    return None


def _notify_services(ssid: str):
    """Push the new SSID to active QX stream so it reconnects immediately."""
    global QX_SSID_REJECTED
    try:
        import otc_price_service as _svc
        _svc.QX_SSID = ssid
        logger.info("[qx_auth] Updated otc_price_service.QX_SSID")
    except Exception:
        pass
    QX_SSID_REJECTED = False


# ── Keepalive ─────────────────────────────────────────────────────────────────
_KEEPALIVE_INTERVAL = 90 * 60   # 90 minutes

def _do_keepalive_ping(ssid: str) -> bool:
    """Ping qxbroker.com with the SSID cookie to reset the inactivity timer."""
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        headers = {
            **_REQUEST_HEADERS,
            "Cookie": f"token={ssid}; session={ssid}",
        }
        for url in [
            "https://qxbroker.com/api/v1/cabinets/digest",
            "https://qxbroker.com/",
        ]:
            try:
                r = requests.get(url, headers=headers, timeout=12, verify=False)
                if r.status_code < 500:
                    logger.debug(f"[qx_auth] QX keepalive {url} → {r.status_code}")
                    return True
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"[qx_auth] Keepalive error: {exc}")
    return False


async def _keepalive_loop():
    logger.info(f"[qx_auth] 🔄 QX SSID keepalive started — pinging every {_KEEPALIVE_INTERVAL//60}min")
    await asyncio.sleep(120)   # warm-up
    loop = asyncio.get_event_loop()
    while True:
        ssid = _SSID_STAMP.get("ssid") or os.environ.get("QUOTEX_SSID", "")
        if ssid:
            try:
                await loop.run_in_executor(None, _do_keepalive_ping, ssid)
            except Exception:
                pass
        await asyncio.sleep(_KEEPALIVE_INTERVAL)


# ── Renewal helpers ───────────────────────────────────────────────────────────
def refresh_ssid_now() -> bool:
    """Try to refresh the SSID via digest endpoint.

    Returns True if a new token was obtained, False otherwise.
    Tries pyquotex WS login first (bypasses Cloudflare), then digest refresh.
    """
    current = _SSID_STAMP.get("ssid") or os.environ.get("QUOTEX_SSID", "")

    # Layer 1: Try digest-based renewal if we have a valid current token
    if current and _is_real_qx_token(current):
        new_token = _try_digest_refresh(current)
        if new_token:
            _save_ssid(new_token)
            logger.info("[qx_auth] ✅ SSID renewed via digest endpoint")
            return True

    # Layer 2: pyquotex WebSocket auto-login (bypasses Cloudflare)
    logger.info("[qx_auth] Trying pyquotex WS auto-login …")
    ws_token = _do_pyquotex_login()
    if ws_token and _is_real_qx_token(ws_token):
        _save_ssid(ws_token)
        logger.info("[qx_auth] ✅ SSID obtained via pyquotex WS auto-login")
        return True

    # Layer 3: Nothing worked — log renewal instructions
    logger.warning(
        "[qx_auth] ⚠️ All auto-login methods failed.\n"
        "          Option A — set QUOTEX_SSID in Replit Secrets:\n"
        "            qxbroker.com → F12 → Console → window.settings.token\n"
        "          Option B — ensure QUOTEX_EMAIL / QUOTEX_PASSWORD are correct\n"
        "            and use a Python 3.12+ runtime for pyquotex.\n"
        "          The bot retries automatically every 60 seconds."
    )
    return False


# ── Public API ────────────────────────────────────────────────────────────────
def get_current_ssid() -> str:
    """Return the active QX SSID. Loads from cache/env, auto-logins via pyquotex if needed."""
    cached = _load_cached_ssid()
    if cached:
        _seed_qxpy_session(cached)
        os.environ["QUOTEX_SSID"] = cached
        return cached

    # No cached SSID — try pyquotex auto-login immediately
    logger.info("[qx_auth] No cached SSID — attempting pyquotex auto-login …")
    ws_token = _do_pyquotex_login()
    if ws_token and _is_real_qx_token(ws_token):
        _save_ssid(ws_token)
        logger.info("[qx_auth] ✅ pyquotex auto-login success on first call")
        return ws_token

    return ""


# ── Background manager ────────────────────────────────────────────────────────
async def run_qx_auth_manager():
    """Background task: keeps the QX SSID alive indefinitely.

    Strategy (identical to po_auth.py):
      1. Read SSID from cache / QUOTEX_SSID env var.
      2. If none found → pyquotex WS auto-login with email + password.
      3. Keepalive ping every 90 min (resets server inactivity timer).
      4. Proactive refresh at 85% of TTL via digest → pyquotex WS fallback.
      5. On WS rejection (QX_SSID_REJECTED=True) → immediate re-login.
      6. Tracks expiry countdown; logs time-to-refresh so admin can monitor.
    """
    global QX_SSID_REJECTED
    logger.info("[qx_auth] Starting Quotex auth manager …")
    if not QX_EMAIL or not QX_PASSWORD:
        logger.error(
            "[qx_auth] Quotex credentials are missing. Configure "
            "QUOTEX_EMAIL and QUOTEX_PASSWORD in .env or Replit Secrets."
        )

    asyncio.create_task(_keepalive_loop(), name="qx_ssid_keepalive")

    existing = get_current_ssid()
    if existing:
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _SSID_TTL_DEFAULT)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO

        if fetched_at == 0:
            # Came from env var (manual set) — save it properly now
            logger.info("[qx_auth] QX SSID loaded from QUOTEX_SSID env var — saving …")
            _save_ssid(existing)
        elif age >= refresh_after:
            logger.info(f"[qx_auth] QX SSID {age/3600:.1f}h old ≥ threshold — refreshing …")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        else:
            logger.info(
                f"[qx_auth] QX SSID loaded — valid  "
                f"(next refresh in {(refresh_after - age)/3600:.1f}h)"
            )
    else:
        # No SSID found anywhere — trigger pyquotex auto-login immediately
        logger.info("[qx_auth] No QUOTEX_SSID found — triggering pyquotex auto-login …")
        ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        if ok:
            logger.info("[qx_auth] ✅ Initial auto-login succeeded — QX OTC stream active")
        else:
            logger.warning(
                "[qx_auth] ⚠️ Auto-login failed on startup.\n"
                "          Will retry every 60 seconds automatically.\n"
                "          Credentials: QUOTEX_EMAIL / QUOTEX_PASSWORD env vars.\n"
                "          Or set QUOTEX_SSID manually in Replit Secrets."
            )

    # ── Maintenance loop (identical lifecycle to po_auth.py) ─────────────────
    _login_fail_count = 0   # consecutive failures — used for backoff
    while True:
        await asyncio.sleep(60)

        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _SSID_TTL_DEFAULT)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO
        hard_deadline = ttl - _EARLY_REFRESH_BUFFER

        # WS rejection detected by stream → immediate re-login
        if QX_SSID_REJECTED:
            logger.info("[qx_auth] QX SSID rejected by WS — attempting re-login …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if ok:
                QX_SSID_REJECTED = False
                _login_fail_count = 0
                logger.info("[qx_auth] ✅ Re-login after WS rejection succeeded")
            else:
                await asyncio.sleep(300)   # wait 5 min before next attempt
            continue

        # Check if env var was updated externally (admin set new QUOTEX_SSID)
        env_ssid = os.environ.get("QUOTEX_SSID", "").strip()
        current_ssid = _SSID_STAMP.get("ssid", "")
        if env_ssid and env_ssid != current_ssid and _is_real_qx_token(env_ssid):
            logger.info("[qx_auth] QUOTEX_SSID env var updated — applying new token …")
            _save_ssid(env_ssid)
            _login_fail_count = 0
            continue
        elif env_ssid and not _is_real_qx_token(env_ssid):
            logger.warning(
                "[qx_auth] ⚠️  QUOTEX_SSID looks like a Laravel/Cloudflare cookie — skipping. "
                "Use the real token from: qxbroker.com → F12 → Console → window.settings.token"
            )

        if not current_ssid:
            # Exponential backoff: 1m → 2m → 4m → 8m → 15m (cap)
            # Only log every N ticks to avoid spamming
            backoff_ticks = min(15, 2 ** _login_fail_count)   # minutes
            _login_fail_count += 1
            if _login_fail_count == 1 or (_login_fail_count % backoff_ticks == 0):
                logger.info(
                    f"[qx_auth] No active SSID — retrying auto-login "
                    f"(attempt #{_login_fail_count}, next in {backoff_ticks}m) …"
                )
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if ok:
                _login_fail_count = 0
            else:
                # Sleep extra time for backoff (already slept 60s above)
                extra = max(0, (backoff_ticks - 1) * 60)
                if extra:
                    await asyncio.sleep(extra)
            continue

        if age >= ttl:
            logger.info("[qx_auth] QX SSID fully expired — refreshing …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                await asyncio.sleep(_LOGIN_RETRY_DELAY)

        elif age >= refresh_after:
            remaining = ttl - age
            logger.info(
                f"[qx_auth] ⚡ QX SSID at {age/3600:.1f}h/{ttl/3600:.0f}h — "
                f"proactive refresh ({remaining/3600:.1f}h before expiry)"
            )
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)

        elif age >= hard_deadline:
            remaining = ttl - age
            logger.info(f"[qx_auth] ⚡ QX SSID expiring in {remaining:.0f}s — emergency refresh")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
