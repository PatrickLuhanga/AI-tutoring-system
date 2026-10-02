/**
 * Account API: signup, login, logout, and "who am I".
 *
 * The session token is kept in localStorage and attached as a bearer header by
 * the shared `http` helper in ./client, so nothing else in the tree has to know
 * about authentication.
 *
 * Why localStorage rather than a cookie: the API is a bearer-token API and the
 * backend never reads cookies. The trade-off is that a token in localStorage is
 * readable by any script that gets injected into the page, so it is the right
 * choice for a teaching project and the wrong one for a real deployment - a
 * deployment should move to an HttpOnly, SameSite=Strict session cookie.
 */

import type { AuthResponse, SessionUser, SignupPayload, SignupPolicy } from '../types'

const TOKEN_KEY = 'ai_tutor.token'

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    // Private mode / storage disabled: treat as signed out rather than crashing.
    return null
  }
}

export function writeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable; the session simply will not persist */
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readToken()
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      // `/me` and `/logout` are session-authenticated; signup/login are not, and
      // send no token because there is not one yet.
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.error ?? detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export const authApi = {
  /** Public signup policy. Safe to call signed out. */
  policy(): Promise<SignupPolicy> {
    return json<SignupPolicy>('/api/auth/config')
  },

  async signup(payload: SignupPayload): Promise<SessionUser> {
    const res = await json<AuthResponse>('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    writeToken(res.token)
    return res.user
  },

  async login(email: string, password: string): Promise<SessionUser> {
    const res = await json<AuthResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    writeToken(res.token)
    return res.user
  },

  /**
   * `/api/auth/me` answers `{ user, active_sessions }`, not the bare user.
   * Unwrapped here so the session store never holds `{ user: {...} }` as if it
   * were the account itself.
   */
  async me(): Promise<SessionUser> {
    const body = await json<{ user: SessionUser }>('/api/auth/me')
    return body.user
  },

  async logout(): Promise<void> {
    try {
      await json('/api/auth/logout', { method: 'POST' })
    } finally {
      // Clear locally even if the call failed, so the UI can never get stuck
      // showing a signed-in shell for a token the server has already dropped.
      writeToken(null)
    }
  },
}
