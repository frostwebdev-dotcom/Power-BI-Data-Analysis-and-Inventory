/**
 * Where the development token lives in the browser.
 *
 * `localStorage`, deliberately: the token is a development credential issued
 * to a known user, and the production identity provider (Entra ID, B2) will
 * replace this module wholesale rather than extend it.
 */

const KEY = "prms.token";

// Entra tokens remain in MSAL's session cache, never our development storage.
let tokenProvider: (() => Promise<string | null>) | null = null;

export function setTokenProvider(provider: (() => Promise<string | null>) | null): void {
  tokenProvider = provider;
}

export async function getToken(): Promise<string | null> {
  return tokenProvider ? tokenProvider() : readToken();
}

export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string): void {
  try {
    window.localStorage.setItem(KEY, token);
  } catch {
    // storage unavailable: the session lasts as long as the page
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // nothing to clear
  }
}
