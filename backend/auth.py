"""
Google Sign-In authentication for the Averroes tool. Zero-cost design:
frontend obtains a Google ID token (free Google Identity Services);
this middleware verifies its signature and enforces the allowlist.

Activation: auth is ENFORCED only when GOOGLE_OAUTH_CLIENT_ID is set.
Unset → everything passes with a startup warning (safe rollout).

Allowlist:
  ALLOWED_DOMAIN  — e.g. "averroescapital.com" (default) — any verified
                    Google account on that domain gets in
  ALLOWED_EMAILS  — optional comma-separated extras (external advisers etc.)

Exempt paths: "/" (Cloud Run health), /auth/config (frontend bootstrap),
/ch-pdf/* (links open in new tabs without headers; the documents are
public Companies House filings anyway), and CORS preflights.
"""
import os
import hmac
import time
import base64
import hashlib
import logging
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# OAuth client IDs are public identifiers (they ship in the browser bundle),
# so a baked-in default is safe. Env var still overrides for rotation.
_DEFAULT_CLIENT_ID = "890361705054-c5glgcq0029d5o447t114kl19hmvc8lo.apps.googleusercontent.com"
AUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", _DEFAULT_CLIENT_ID).strip()
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "averroescapital.com").strip().lower()
ALLOWED_EMAILS = {e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()}

# /ch-watch/run is exempt from Google auth but guarded by its own shared
# token (WATCH_TOKEN) — Cloud Scheduler cannot present a user session.
EXEMPT_PATHS = {"/", "/auth/config", "/auth/session", "/ch-watch/run", "/enrich-oneoff/run", "/prequalify/run", "/email/deep-sync/run"}
EXEMPT_PREFIXES = ("/ch-pdf/",)

# ── 12-hour session tokens ────────────────────────────────────────────────────
# Google ID tokens expire after 1 hour, which interrupted long bulk runs.
# At sign-in the frontend exchanges the Google token for our own HMAC-signed
# session token (12h default), so one sign-in covers a full workday.
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))


def _session_secret() -> bytes:
    """Stable signing key across instances: env override, else derived from existing secrets."""
    seed = os.getenv("SESSION_SECRET") or (os.getenv("GEMINI_API_KEY", "") + AUTH_CLIENT_ID)
    return hashlib.sha256(("averroes-session:" + seed).encode()).digest()


def issue_session_token(google_credential: str):
    """Verify a Google ID token (incl. allowlist) and mint a 12h session token."""
    email, _ = _verify_token_cached(google_credential)
    exp = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{email}|{exp}"
    sig = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"avr.{b64}.{sig}", exp


def verify_session_token(token: str):
    """Validate our session token: signature, expiry, and (re-)check the allowlist."""
    try:
        b64, sig = token[4:].rsplit(".", 1)
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
        expected = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        email, exp = payload.rsplit("|", 1)
    except Exception:
        raise ValueError("Invalid session token")
    if int(exp) < time.time():
        raise TimeoutError("Session expired")
    domain_ok = ALLOWED_DOMAIN and email.endswith("@" + ALLOWED_DOMAIN)
    if not (domain_ok or email in ALLOWED_EMAILS):
        raise PermissionError(f"{email} is not authorised for this tool")
    return email, int(exp)


def auth_enabled() -> bool:
    return bool(AUTH_CLIENT_ID)


@lru_cache(maxsize=512)
def _verify_token_cached(token: str):
    """
    Verify a Google ID token (signature, expiry, audience) and return
    (email, exp) or raise. Cached: the same token is reused for ~1h of
    requests, so we only do crypto + cert fetch once per token.
    """
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    info = google_id_token.verify_oauth2_token(token, google_requests.Request(), AUTH_CLIENT_ID)
    email = (info.get("email") or "").lower()
    if not info.get("email_verified"):
        raise PermissionError("Email not verified by Google")

    domain_ok = ALLOWED_DOMAIN and email.endswith("@" + ALLOWED_DOMAIN)
    if not (domain_ok or email in ALLOWED_EMAILS):
        raise PermissionError(f"{email} is not authorised for this tool")

    return email, int(info.get("exp", 0))


async def auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Sign in required"})

    token = auth_header[7:].strip()
    try:
        if token.startswith("avr."):
            email, exp = verify_session_token(token)
        else:
            email, exp = _verify_token_cached(token)
            if exp and exp < time.time():
                _verify_token_cached.cache_clear()
                return JSONResponse(status_code=401, content={"detail": "Session expired — sign in again"})
    except TimeoutError:
        return JSONResponse(status_code=401, content={"detail": "Session expired — sign in again"})
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"detail": str(e)})
    except Exception as e:
        logger.warning(f"Auth token rejected: {e}")
        return JSONResponse(status_code=401, content={"detail": "Invalid sign-in token — sign in again"})

    request.state.user_email = email
    return await call_next(request)
