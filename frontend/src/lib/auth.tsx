"use client";

import type { IPublicClientApplication } from "@azure/msal-browser";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { api, ApiError } from "./api";
import { clearToken, getToken, readToken, setTokenProvider, writeToken } from "./auth-storage";
import { acquireApiToken, initializeEntra } from "./entra";
import type { AuthConfiguration } from "./entra";
import type { Principal } from "./types";

interface AuthState {
  status: "loading" | "anonymous" | "signed-in";
  principal: Principal | null;
  authMode: AuthConfiguration["mode"];
  configurationError: string | null;
  signIn: (email?: string) => Promise<void>;
  signOut: () => void;
  hasRole: (...roles: string[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [authMode, setAuthMode] = useState<AuthConfiguration["mode"]>("disabled");
  const [configurationError, setConfigurationError] = useState<string | null>(null);
  const configRef = useRef<AuthConfiguration | null>(null);
  const clientRef = useRef<IPublicClientApplication | null>(null);
  const sessionEnabled = useRef(true);

  const resolve = useCallback(async () => {
    if (!(await getToken())) {
      setPrincipal(null);
      setStatus("anonymous");
      return;
    }
    const me = await api.get<Principal>("/auth/me");
    setPrincipal(me);
    setStatus("signed-in");
  }, []);

  useEffect(() => {
    let cancelled = false;
    setTokenProvider(async () => null);
    async function initialize() {
      try {
        const config = await api.get<AuthConfiguration>("/auth/config");
        const client = config.mode === "entra" ? await initializeEntra(config) : null;
        if (cancelled) return;
        configRef.current = config;
        clientRef.current = client;
        setAuthMode(config.mode);
        if (config.mode !== "dev") clearToken();
        setTokenProvider(async () => {
          if (!sessionEnabled.current) return null;
          if (client && config.scope) return acquireApiToken(client, config.scope);
          return config.mode === "dev" ? readToken() : null;
        });
        await resolve();
      } catch (error) {
        if (cancelled) return;
        clearToken();
        setPrincipal(null);
        setStatus("anonymous");
        setConfigurationError(
          error instanceof Error ? error.message : "Unable to initialize sign-in.",
        );
      }
    }
    void initialize();
    return () => {
      cancelled = true;
      setTokenProvider(async () => null);
    };
  }, [resolve]);

  const signIn = useCallback(async (email?: string) => {
    const config = configRef.current;
    setConfigurationError(null);
    queryClient.clear();
    sessionEnabled.current = true;
    try {
      if (config?.mode === "entra" && clientRef.current && config.scope) {
        const result = await clientRef.current.loginPopup({
          scopes: [config.scope],
          prompt: "select_account",
        });
        clientRef.current.setActiveAccount(result.account);
      } else if (config?.mode === "dev") {
        const token = await api.post<{ access_token: string }>("/auth/dev-token", {
          email: email?.trim().toLowerCase(),
        });
        writeToken(token.access_token);
      } else {
        throw new Error("Sign-in is not configured. Contact your administrator.");
      }
      await resolve();
    } catch (error) {
      clearToken();
      setPrincipal(null);
      setStatus("anonymous");
      if (error instanceof ApiError && error.statusCode === 404 && config?.mode === "dev") {
        throw new Error("No active user with that email, or development sign-in is disabled.");
      }
      throw error;
    }
  }, [queryClient, resolve]);

  const signOut = useCallback(() => {
    sessionEnabled.current = false;
    clearToken();
    queryClient.clear();
    setPrincipal(null);
    setStatus("anonymous");
    const client = clientRef.current;
    if (client) {
      void client.logoutPopup({ account: client.getActiveAccount() }).catch(() => {
        // A blocked popup can fail before MSAL clears its cache. Still remove
        // this browser's credentials, so reloading cannot restore the session.
        void client.clearCache();
        setConfigurationError("Signed out of PRMS. Microsoft sign-out did not finish.");
      });
    }
  }, [queryClient]);

  const value = useMemo<AuthState>(() => ({
    status, principal, authMode, configurationError, signIn, signOut,
    hasRole: (...roles: string[]) => principal !== null &&
      (principal.roles.includes("ADMIN") || roles.some((role) => principal.roles.includes(role))),
  }), [status, principal, authMode, configurationError, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
