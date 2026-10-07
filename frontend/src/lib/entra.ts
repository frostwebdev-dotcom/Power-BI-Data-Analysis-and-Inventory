import type { IPublicClientApplication } from "@azure/msal-browser";

export interface AuthConfiguration {
  mode: "dev" | "entra" | "disabled";
  tenant_id: string | null;
  client_id: string | null;
  scope: string | null;
}

export async function initializeEntra(config: AuthConfiguration): Promise<IPublicClientApplication> {
  if (!config.tenant_id || !config.client_id || !config.scope) {
    throw new Error("Microsoft sign-in configuration is incomplete. Contact your administrator.");
  }
  const { PublicClientApplication } = await import("@azure/msal-browser");
  const client = new PublicClientApplication({
    auth: {
      clientId: config.client_id,
      authority: `https://login.microsoftonline.com/${config.tenant_id}`,
      redirectUri: `${window.location.origin}/auth/callback.html`,
      postLogoutRedirectUri: `${window.location.origin}/auth/callback.html`,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await client.initialize();
  return client;
}

export async function acquireApiToken(
  client: IPublicClientApplication,
  scope: string,
): Promise<string | null> {
  const accounts = client.getAllAccounts();
  const account = client.getActiveAccount() ?? (accounts.length === 1 ? accounts[0] : null);
  if (!account) return null;
  client.setActiveAccount(account);
  const result = await client.acquireTokenSilent({ account, scopes: [scope] });
  return result.accessToken;
}
