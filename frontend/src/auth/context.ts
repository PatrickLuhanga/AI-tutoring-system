import { createContext, useContext } from 'react'
import type { AuthUser, UserProfile } from '../types'

export interface AuthContextValue {
  /** The signed-in identity, or `null` when the login screen is showing. */
  user: AuthUser | null
  /**
   * Sign in with a DUT4life address. The profile is provisioned on first
   * contact; rejects when the address is not institutional.
   */
  login: (email: string) => Promise<AuthUser>
  /** Replace the in-memory identity after onboarding or a profile change. */
  applyProfile: (profile: UserProfile) => AuthUser
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an <AuthProvider>')
  }
  return ctx
}
