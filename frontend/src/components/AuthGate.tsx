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

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/auth/config`);
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
        // Backend unreachable — don't lock the UI over it
        setState("open");
      }
    })();
  }, []);

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
    return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", color: "#94a3b8", fontFamily: "sans-serif", fontSize: 14 }}>Loading…</div>;
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
