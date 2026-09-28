/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_USE_MOCK?: string
  readonly VITE_ADMIN_KEY?: string
  /** Microsoft Entra ID application (client) id; enables the SSO button. */
  readonly VITE_AZURE_CLIENT_ID?: string
  /** Microsoft Entra ID tenant id used to build the authority URL. */
  readonly VITE_AZURE_TENANT_ID?: string
  /** Optional space/comma-separated scope override for the login request. */
  readonly VITE_AZURE_SCOPES?: string
}
