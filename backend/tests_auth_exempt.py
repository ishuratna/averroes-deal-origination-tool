#!/usr/bin/env python3
"""
Every sign-in-exempt endpoint must guard itself with WATCH_TOKEN.

WHY THIS TEST EXISTS. auth.py's EXEMPT_PATHS skips Google sign-in for a path,
because Cloud Scheduler and a terminal cannot hold a browser session. The token
check then lives INSIDE the handler. The two are a matched pair, and nothing in
the language enforces it:

    in EXEMPT_PATHS, no token check  -> open to the internet
    token check, not in EXEMPT_PATHS -> unreachable from a terminal

The first half happened for real: /delivery/verify was added to EXEMPT_PATHS
while its handler checked nothing, leaving an endpoint that rewrites company
stages callable by anyone. The second half happened repeatedly, and cost a
round trip each time. This test makes both halves impossible to land.

It reads the live route table and the real handler source, so a new exempt
endpoint is covered the moment it is written.
"""
import inspect
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

import main  # noqa: E402
from auth import EXEMPT_PATHS, EXEMPT_PREFIXES  # noqa: E402

# Paths that are PUBLIC BY DESIGN, each for a stated reason. Anything not on this
# list and not token-guarded is a hole. Adding to this list is a deliberate
# security decision, which is exactly why it must be written down here.
PUBLIC_BY_DESIGN = {
    "/":              "health check. Reports no company data.",
    "/auth/config":   "the OAuth client id, which ships in the browser bundle anyway.",
    "/auth/session":  "the sign-in endpoint itself. It cannot require a session.",
}

# Evidence that a handler guards itself. Either the shared helper or the inline
# pattern that predates it.
TOKEN_MARKERS = ("_require_token", 'os.getenv("WATCH_TOKEN"')

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


# path -> handler source, for every registered route
handlers = {}
for r in main.app.routes:
    fn = getattr(r, "endpoint", None)
    path = getattr(r, "path", "")
    if not fn or not path:
        continue
    try:
        handlers.setdefault(path, inspect.getsource(fn))
    except (OSError, TypeError):
        pass

print("── Every exempt path guards itself with a token ──")
for path in sorted(EXEMPT_PATHS):
    if path in PUBLIC_BY_DESIGN:
        continue
    src = handlers.get(path)
    if src is None:
        chk(f"{path} is a real registered route", False, True)
        continue
    chk(f"{path} checks WATCH_TOKEN", any(m in src for m in TOKEN_MARKERS), True)

print()
print("── Exempt PREFIXES too ──")
for prefix in EXEMPT_PREFIXES:
    matching = [p for p in handlers if p.startswith(prefix)]
    chk(f"{prefix} matches at least one route", bool(matching), True)
    for p in matching:
        chk(f"{p} checks WATCH_TOKEN", any(m in handlers[p] for m in TOKEN_MARKERS), True)

print()
print("── The reverse mistake: guarded but unreachable ──")
# A handler that demands a token while still behind sign-in can never be called
# from a terminal, and no error says why. It looks exactly like an auth failure.
for path, src in sorted(handlers.items()):
    if not any(m in src for m in TOKEN_MARKERS):
        continue
    exempt = path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES)
    # The reply rule is dual-path on purpose: the browser path is session-based
    # and only the /admin/ alias is exempt, so the token check is conditional.
    if not exempt and "request.url.path.startswith" in src:
        continue
    chk(f"{path} demands a token, so it must be sign-in exempt", exempt, True)

print()
print("── The specific hole that prompted this test ──")
chk("/delivery/verify is exempt", "/delivery/verify" in EXEMPT_PATHS, True)
chk("...and guarded",
    any(m in handlers.get("/delivery/verify", "") for m in TOKEN_MARKERS), True)
chk("the reply rule is runnable from a terminal",
    "/admin/reply-rule/reconcile" in EXEMPT_PATHS, True)
chk("...and its browser path is NOT exempt, so the UI keeps needing sign-in",
    "/reply-rule/reconcile" in EXEMPT_PATHS, False)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
