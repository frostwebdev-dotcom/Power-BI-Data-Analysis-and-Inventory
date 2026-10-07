"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "@/lib/auth";

/**
 * Sign-in mode comes from the API. Microsoft owns staging credentials; the
 * email-only form is available only when local development auth is enabled.
 */
export function SignIn() {
  const { signIn, authMode, configurationError } = useAuth();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(authMode === "dev" ? email.trim() : undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <form className="card signin__card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="muted">
          {authMode === "entra"
            ? "Use your approved Microsoft account to access PRMS."
            : authMode === "dev"
              ? "Development sign-in. Enter the email of a user in this environment."
              : "Sign-in is not configured. Contact your administrator."}
        </p>
        {authMode === "dev" ? <label className="field">
          <span className="field__label">Email</span>
          <input
            type="email"
            name="email"
            required
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="admin@example.test"
          />
        </label> : null}
        {error || configurationError ? (
          <div className="note note--danger" role="alert">
            {error || configurationError}
          </div>
        ) : null}
        <button type="submit" className="button button--primary" disabled={busy || authMode === "disabled"}>
          {busy ? "Signing in…" : authMode === "entra" ? "Sign in with Microsoft" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
