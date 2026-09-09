"use client";
import React, { useEffect, useState } from "react";
import { API_BASE_URL } from "../services/api";

/**
 * Google Sign-In gate. Wraps every page.
 * - Asks the backend (/auth/config) whether auth is enforced and for the OAuth client ID
 * - If enforced: shows a Google Sign-In screen until a valid ID token is stored
 * - Tokens live ~1h; any 401 from the API clears the token and re-shows this gate
 */

const TOKEN_KEY = "averroes_id_token";

export function tokenExpiry(token: string): number | null {
  try {
    if (token.startsWith("avr.")) {
      // Our 12h session token: avr.<b64(email|exp)>.<sig>
      const b64 = token.slice(4).split(".")[0];
      const payload = atob(b64.replace(/-/g, "+").replace(/_/g, "/"));
      return parseInt(payload.split("|").pop() || "0", 10) * 1000;
    }
    // Google ID token (JWT)
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp ? payload.exp * 1000 : null;
  } catch {
    return 0;
  }
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return null;
  const exp = tokenExpiry(token);
  if (exp !== null && exp < Date.now() + 30_000) {
    localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  return token;
}

export function clearToken() {
  if (typeof window !== "undefined") localStorage.removeItem(TOKEN_KEY);
}

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "open" | "locked">("loading");
  const [config, setConfig] = useState<{ client_id: string; allowed_domain: string } | null>(null);
  const [error, setError] = useState("");
  // How long we have been waiting, so a slow start SAYS something instead of
  // showing a blank screen. The backend scales to zero, so the first request
  // after a deploy or an idle period has to boot a container.
  const [waited, setWaited] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (state !== "loading") return;
    const t = setInterval(() => setWaited((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  useEffect(() => {
    // A hung request is NOT the same as a failed one. The old code caught
    // errors and fell through to "open", but had no timeout at all, so a cold
    // start left the whole app on a blank "Loading..." forever with nothing to
    // click (Ishu, 10 Sep 2026). Bound the wait, then retry, then say so.
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12_000);
    (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/auth/config`, { signal: ctrl.signal });
        const cfg = await res.json();
        if (!cfg.auth_enabled) {
          sessionStorage.removeItem("averroes_auth_on");
          setState("open");
          return;
        }
        // Remember auth is active so apiFetch can pre-check tokens client-side
        sessionStorage.setItem("averroes_auth_on", "1");
        const note = sessionStorage.getItem("averroes_session_note");
        if (note) { setError(note); sessionStorage.removeItem("averroes_session_note"); }
        setConfig(cfg);
        setState(getStoredToken() ? "open" : "locked");
      } catch {
        // A cold container usually answers on the second try, and by then it
        // is warm. Retry twice before giving up on it.
        if (attempt < 2) { setAttempt((n) => n + 1); return; }
        // Still nothing: the backend is genuinely unreachable. Never lock the
        // UI over that; let the app load and let each call report its own error.
        setState("open");
      } finally {
        clearTimeout(timer);
      }
    })();
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [attempt]);

  useEffect(() => {
    if (state !== "locked" || !config?.client_id) return;
    // Load Google Identity Services and render the button
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      const google = (window as any).google;
      if (!google?.accounts?.id) return;
      google.accounts.id.initialize({
        client_id: config.client_id,
        callback: async (resp: any) => {
          if (!resp.credential) { setError("Sign-in failed. Try again."); return; }
          // Exchange the 1-hour Google token for our 12-hour session token,
          // so long bulk runs are never interrupted by expiry.
          try {
            const r = await fetch(`${API_BASE_URL}/auth/session`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ credential: resp.credential }),
            });
            const d = await r.json();
            if (r.ok && d.session_token) {
              localStorage.setItem(TOKEN_KEY, d.session_token);
            } else {
              localStorage.setItem(TOKEN_KEY, resp.credential); // fallback: 1h Google token
            }
          } catch {
            localStorage.setItem(TOKEN_KEY, resp.credential);
          }
          window.location.reload();
        },
      });
      google.accounts.id.renderButton(document.getElementById("gsi-btn"), {
        theme: "filled_blue", size: "large", shape: "pill", text: "signin_with",
      });
    };
    document.head.appendChild(script);
    return () => { script.remove(); };
  }, [state, config]);

  if (state === "loading") {
    // After a few seconds, say what is happening. The server sleeps when nobody
    // is using it, so the first visit of the day waits for it to wake up. A
    // silent "Loading..." for 30 seconds is indistinguishable from a dead app.
    const slow = waited >= 4;
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "#f8fafc", fontFamily: "sans-serif", gap: "0.6rem" }}>
        <div style={{ fontWeight: 800, fontSize: 18, color: "#0f172a", letterSpacing: "0.02em" }}>AVERROES<span style={{ color: "#2563eb" }}>INTEL</span></div>
        <div style={{ color: "#94a3b8", fontSize: 14 }}>
          {slow ? "Waking the server up. This takes a moment after it has been idle." : "Loading…"}
        </div>
        {waited >= 12 && (
          <button onClick={() => { setWaited(0); setAttempt((n) => n + 1); }}
                  style={{ marginTop: "0.4rem", padding: "0.45rem 1.1rem", fontSize: 13, borderRadius: 999, border: "1px solid #cbd5e1", background: "#fff", color: "#0f172a", cursor: "pointer" }}>
            Try again
          </button>
        )}
      </div>
    );
  }

  if (state === "locked") {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "#f8fafc", fontFamily: "sans-serif" }}>
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 16, padding: "3rem 3.5rem", textAlign: "center", boxShadow: "0 10px 30px rgba(2,6,23,0.08)" }}>
          <div style={{ fontWeight: 800, fontSize: 20, color: "#0f172a", letterSpacing: "0.02em" }}>AVERROES<span style={{ color: "#2563eb" }}>INTEL</span></div>
          <p style={{ color: "#64748b", fontSize: 13, margin: "0.75rem 0 1.75rem" }}>
            Restricted to @{config?.allowed_domain} accounts.<br />Sign in with your company Google account.
          </p>
          <div id="gsi-btn" style={{ display: "flex", justifyContent: "center" }} />
          {error && <p style={{ color: "#dc2626", fontSize: 12, marginTop: "1rem" }}>{error}</p>}
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
