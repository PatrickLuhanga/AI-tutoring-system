/**
 * Session state: who is signed in, and the helpers to change that.
 *
 * The whole tree reads the session from here rather than re-fetching, so a role
 * check is a field read instead of a request. On load the token is validated
 * once against `/api/auth/me`; an expired or revoked token is discarded rather
 * than left to fail later on the first API call.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { authApi, readToken, writeToken } from '../api/auth'
import type { SessionUser, SignupPayload } from '../types'

interface SessionValue {
  user: SessionUser | null
  /** True until the initial `/me` check finishes, so the app can hold still. */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  signup: (payload: SignupPayload) => Promise<void>
  logout: () => Promise<void>
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const token = readToken()
    if (!token) {
      setLoading(false)
      return
    }
    authApi
      .me()
      .then((u) => {
        if (!cancelled) setUser(u)
      })
      .catch(() => {
        // Expired, revoked, or the server was unreachable. Either way the token
        // in storage is no longer good for anything.
        writeToken(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setUser(await authApi.login(email, password))
  }, [])

  const signup = useCallback(async (payload: SignupPayload) => {
    setUser(await authApi.signup(payload))
  }, [])

  const logout = useCallback(async () => {
    await authApi.logout()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, signup, logout }),
    [user, loading, login, signup, logout],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used inside <SessionProvider>')
  return ctx
}
