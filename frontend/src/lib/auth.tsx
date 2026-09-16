"use client";

/**
 * Who is signed in.
 *
 * Development sign-in: the API's `/auth/dev-token` issues a token for an
 * existing user by email — no password, by design (docs/security.md). The
 * provider keeps the token, resolves the principal through `/auth/me`, and
 * exposes `hasRole` so screens can hide actions the API would refuse anyway.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, ApiError } from "./api";
import { clearToken, readToken, writeToken } from "./auth-storage";
import type { Principal } from "./types";

interface AuthState {
  status: "loading" | "anonymous" | "signed-in";
  principal: Principal | null;
  signIn: (email: string) => Promise<void>;
  signOut: () => void;
  hasRole: (...roles: string[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [principal, setPrincipal] = useState<Principal | null>(null);

  const resolve = useCallback(async () => {
    if (!readToken()) {
      setPrincipal(null);
      setStatus("anonymous");
      return;
    }
    try {
      const me = await api.get<Principal>("/auth/me");
      setPrincipal(me);
      setStatus("signed-in");
    } catch {
      clearToken();
      setPrincipal(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    void resolve();
  }, [resolve]);

  const signIn = useCallback(
    async (email: string) => {
      try {
        const token = await api.post<{ access_token: string }>("/auth/dev-token", { email });
        writeToken(token.access_token);
      } catch (error) {
        if (error instanceof ApiError && error.statusCode === 404) {
          throw new Error("No active user with that email, or development sign-in is disabled.");
        }
        throw error;
      }
      await resolve();
    },
    [resolve],
  );

  const signOut = useCallback(() => {
    clearToken();
    setPrincipal(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      status,
      principal,
      signIn,
      signOut,
      hasRole: (...roles: string[]) =>
        principal !== null &&
        (principal.roles.includes("ADMIN") || roles.some((r) => principal.roles.includes(r))),
    }),
    [status, principal, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
