import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, setClientIdentity } from '../api/client'
import type { AuthUser, UserProfile } from '../types'
import { AuthContext, type AuthContextValue } from './context'

const STORAGE_KEY = 'ai-tutor.auth'

function toAuthUser(profile: UserProfile): AuthUser {
  return {
    email: profile.email,
    name: profile.full_name,
    role: profile.role,
    roles: profile.roles,
    student_id: profile.student_id,
    is_tutor: profile.is_tutor,
    modules: profile.modules,
    enrolled_modules: profile.enrolled_modules,
    needs_onboarding: profile.needs_onboarding,
    source: 'local',
  }
}

function readStored(): AuthUser | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as AuthUser
    if (!parsed?.email || !parsed?.role) return null
    return parsed
  } catch {
    return null
  }
}

/**
 * Holds the signed-in identity, persists it across reloads, and mirrors it into
 * the API client so every request carries `X-User-Email` / `X-User-Role`.
 *
 * There are no hard-coded profiles: the identity always comes from
 * `POST /api/auth/login`. On mount the cached identity is re-fetched so a role
 * change (e.g. an admin granting tutor privileges) is picked up.
 */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(readStored)

  useEffect(() => {
    setClientIdentity(user)
    try {
      if (user) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(user))
      } else {
        localStorage.removeItem(STORAGE_KEY)
      }
    } catch {
      /* storage unavailable (private mode) — the session still works in-memory */
    }
  }, [user])

  useEffect(() => {
    let cancelled = false
    async function bootstrap() {
      if (!readStored()) return
      try {
        const profile = await api.getProfile()
        if (!cancelled) setUser(toAuthUser(profile))
      } catch {
        /* keep the cached identity if the gateway is unreachable */
      }
    }
    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string) => {
    const profile = await api.login(email)
    const next = toAuthUser(profile)
    setUser(next)
    return next
  }, [])

  const applyProfile = useCallback((profile: UserProfile) => {
    const next = toAuthUser(profile)
    setUser(next)
    return next
  }, [])

  const logout = useCallback(() => setUser(null), [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, login, applyProfile, logout }),
    [user, login, applyProfile, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
