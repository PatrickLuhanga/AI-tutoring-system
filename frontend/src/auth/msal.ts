/**
 * Microsoft Entra ID (DUT4life) sign-in via MSAL.
 *
 * The SSO button is only active when both `VITE_AZURE_CLIENT_ID` and
 * `VITE_AZURE_TENANT_ID` are configured, so the app keeps running in dev mode
 * with no tenant. When enabled, `acquireMicrosoftToken` runs the popup flow and
 * returns a backend-callable access token that the API client forwards as
 * `Authorization: Bearer` (verified against the tenant JWKS in `src/auth.py`).
 */

import { InteractionRequiredAuthError, PublicClientApplication } from '@azure/msal-browser'
import type { AuthenticationResult, Configuration } from '@azure/msal-browser'

const clientId = import.meta.env.VITE_AZURE_CLIENT_ID?.trim() ?? ''
const tenantId = import.meta.env.VITE_AZURE_TENANT_ID?.trim() ?? ''

/** True when the tenant + client id are configured; gates the Microsoft button. */
export const msalEnabled = Boolean(clientId && tenantId)

/** A token the gateway can verify, plus the account's UPN when available. */
export interface MicrosoftSession {
  accessToken: string
  email: string | null
}

function requestedScopes(): string[] {
  const configured = (import.meta.env.VITE_AZURE_SCOPES ?? '')
    .split(/[\s,]+/)
    .map((scope) => scope.trim())
    .filter(Boolean)
  if (configured.length > 0) return configured
  // Default scope for a custom API registration exposing `access_as_user`.
  return clientId ? [`api://${clientId}/access_as_user`] : []
}

const loginRequest = { scopes: requestedScopes() }

let instance: PublicClientApplication | null = null
let initPromise: Promise<PublicClientApplication> | null = null

async function getInstance(): Promise<PublicClientApplication> {
  if (instance) return instance
  if (!initPromise) {
    const config: Configuration = {
      auth: {
        clientId,
        authority: `https://login.microsoftonline.com/${tenantId || 'common'}`,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: 'sessionStorage' },
    }
    const pca = new PublicClientApplication(config)
    initPromise = pca.initialize().then(() => {
      instance = pca
      return pca
    })
  }
  return initPromise
}

function toSession(result: AuthenticationResult): MicrosoftSession {
  const claims = result.idTokenClaims as Record<string, unknown> | undefined
  const claimed = claims?.preferred_username ?? claims?.email
  const email =
    result.account?.username ?? (typeof claimed === 'string' ? claimed : null)
  return { accessToken: result.accessToken, email: email ?? null }
}

/** Interactive fallback: open the Microsoft popup and return a fresh token. */
export async function acquireMicrosoftToken(): Promise<MicrosoftSession> {
  const pca = await getInstance()
  const result = await pca.loginPopup(loginRequest)
  return toSession(result)
}

/**
 * Silent token for an already-signed-in account; used on reload so a strict
 * gateway keeps accepting requests without a second interactive prompt.
 */
export async function acquireMicrosoftTokenSilent(): Promise<MicrosoftSession | null> {
  if (!msalEnabled) return null
  const pca = await getInstance()
  const account = pca.getAllAccounts()[0]
  if (!account) return null
  try {
    const result = await pca.acquireTokenSilent({ ...loginRequest, account })
    return toSession(result)
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      try {
        const result = await pca.acquireTokenPopup({ ...loginRequest, account })
        return toSession(result)
      } catch {
        return null
      }
    }
    return null
  }
}

/** Sign the Microsoft account out (best-effort; local state is cleared by caller). */
export async function signOutMicrosoft(): Promise<void> {
  if (!msalEnabled) return
  const pca = await getInstance()
  const account = pca.getAllAccounts()[0]
  if (!account) return
  try {
    await pca.logoutPopup({ account })
  } catch {
    /* the caller clears the local session regardless */
  }
}
