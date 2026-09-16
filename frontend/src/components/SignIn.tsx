"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "@/lib/auth";

/**
 * Development sign-in: an email the API already knows. There is no password
 * because the development backend is not a credential system; the production
 * identity provider replaces this screen (phase1-status B2).
 */
export function SignIn() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim());
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
          Development sign-in. Enter the email of a user in this environment; the API issues a
          short-lived token.
        </p>
        <label className="field">
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
        </label>
        {error ? (
          <div className="note note--danger" role="alert">
            {error}
          </div>
        ) : null}
        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
