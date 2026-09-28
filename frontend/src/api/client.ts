/**
 * Single seam between the components and the API Gateway.
 *
 * Flip `MOCK` to `false` (or set `VITE_USE_MOCK=false`) and the exact same
 * component tree will start talking to Flask through the Vite `/api` proxy.
 */

import type {
  AdminOverview,
  AuthUser,
  ChatRequestPayload,
  ChatResponse,
  ChatSessionSummary,
  FeedbackPayload,
  GrantTutorPayload,
  LLMConfig,
  LLMConfigUpdatePayload,
  Module,
  OllamaModelsResponse,
  ProfileUpdatePayload,
  SessionDetail,
  SessionOpenPayload,
  SessionOpenResponse,
  TelemetryAnalytics,
  TutorAnalytics,
  UserProfile,
  UserRecord,
} from '../types'
import {
  MOCK,
  MOCK_ANALYTICS,
  MOCK_GUARDRAIL_FLAGS,
  MOCK_LLM_CONFIG,
  MOCK_OLLAMA_MODELS,
  MODULES,
  mockSessionOpen,
  mockTutorAnalytics,
} from './mockData'
import { mockBackend } from './mockBackend'

const USE_MOCK = MOCK && import.meta.env.VITE_USE_MOCK !== 'false'

const ADMIN_KEY = import.meta.env.VITE_ADMIN_KEY ?? 'change-me-admin-key'

/**
 * Identity mirrored from the auth context so every request carries the
 * dev-mode `X-User-Email` / `X-User-Role` headers the gateway reads. When MSAL
 * is configured, the verified Microsoft access token is added as well so a
 * strict-mode gateway can cryptographically authenticate the caller.
 */
let clientIdentity: AuthUser | null = null
let accessToken: string | null = null

export function setClientIdentity(user: AuthUser | null): void {
  clientIdentity = user
}

/**
 * Store (or clear) the Microsoft Entra ID access token acquired by MSAL.
 * Sent on every request as `Authorization: Bearer`; the dev headers are
 * ignored by the gateway when `AUTH_MODE=strict`.
 */
export function setAccessToken(token: string | null): void {
  accessToken = token
}

function identityHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  if (clientIdentity?.email) headers['X-User-Email'] = clientIdentity.email
  if (clientIdentity?.role) headers['X-User-Role'] = clientIdentity.role
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`
    headers['X-DUT4life-Token'] = accessToken
  }
  return headers
}

function delay<T>(value: T, ms = 450): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

function currentEmail(): string {
  return clientIdentity?.email ?? 'student@dut4life.ac.za'
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Key': ADMIN_KEY,
      ...identityHeaders(),
    },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.error ?? detail
    } catch {
      /* response was not JSON */
    }
    throw new Error(`${response.status} ${detail}`)
  }
  return (await response.json()) as T
}

export const api = {
  // -------------------------------------------------------------------------
  // Authentication & profile
  // -------------------------------------------------------------------------

  /** `POST /api/auth/login` — provision/resolve a DUT4life profile. */
  async login(email: string): Promise<UserProfile> {
    if (USE_MOCK) {
      return delay(mockBackend.login(email), 300)
    }
    return http<UserProfile>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  },

  /** `GET /api/profile` */
  async getProfile(): Promise<UserProfile> {
    if (USE_MOCK) {
      const profile = mockBackend.getProfile(currentEmail())
      if (!profile) throw new Error('404 Profile not found.')
      return delay(profile, 150)
    }
    return http<UserProfile>('/api/profile')
  },

  /** `PUT /api/profile` — save name, student number and modules. */
  async updateProfile(payload: ProfileUpdatePayload): Promise<UserProfile> {
    if (USE_MOCK) {
      return delay(mockBackend.updateProfile(currentEmail(), payload), 300)
    }
    return http<UserProfile>('/api/profile', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  /** `GET /api/modules` — module registry for onboarding and grant pickers. */
  async listModules(): Promise<Module[]> {
    if (USE_MOCK) {
      return delay(MODULES, 100)
    }
    const body = await http<{ modules: Module[] }>('/api/modules')
    return body.modules
  },

  /** `POST /api/admin/grant-tutor` — grant tutor privileges (dual role). */
  async grantTutor(payload: GrantTutorPayload): Promise<UserProfile> {
    if (USE_MOCK) {
      return delay(mockBackend.grantTutor(payload.identifier, payload.modules), 300)
    }
    return http<UserProfile>('/api/admin/grant-tutor', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /** `POST /api/admin/revoke-tutor` */
  async revokeTutor(identifier: string): Promise<UserProfile> {
    if (USE_MOCK) {
      return delay(mockBackend.revokeTutor(identifier), 300)
    }
    return http<UserProfile>('/api/admin/revoke-tutor', {
      method: 'POST',
      body: JSON.stringify({ identifier }),
    })
  },

  // -------------------------------------------------------------------------
  // Chat + history
  // -------------------------------------------------------------------------

  /** `POST /api/chat` */
  async chat(payload: ChatRequestPayload): Promise<ChatResponse> {
    if (USE_MOCK) {
      return delay(mockBackend.chat(currentEmail(), payload), 500)
    }
    return http<ChatResponse>('/api/chat', { method: 'POST', body: JSON.stringify(payload) })
  },

  /** `GET /api/sessions` — persisted chat sessions for the sidebar. */
  async listSessions(moduleId?: string): Promise<ChatSessionSummary[]> {
    if (USE_MOCK) {
      return delay(mockBackend.listSessions(currentEmail(), moduleId), 200)
    }
    const query = moduleId ? `?module_id=${encodeURIComponent(moduleId)}` : ''
    const body = await http<{ sessions: ChatSessionSummary[] }>(`/api/sessions${query}`)
    return body.sessions
  },

  /** `GET /api/sessions/<id>` — one session with its full message history. */
  async getSession(sessionId: string): Promise<SessionDetail> {
    if (USE_MOCK) {
      const detail = mockBackend.getSession(currentEmail(), sessionId)
      if (!detail) throw new Error('404 Session not found.')
      return delay(detail, 200)
    }
    return http<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`)
  },

  /** `DELETE /api/sessions/<id>` — delete a session and its associated data. */
  async deleteSession(sessionId: string): Promise<{ status: string }> {
    if (USE_MOCK) {
      const removed = mockBackend.deleteSession(currentEmail(), sessionId)
      if (!removed) throw new Error('404 Session not found.')
      return delay({ status: 'deleted' }, 200)
    }
    return http<{ status: string }>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    })
  },

  /** `POST /api/feedback` */
  async feedback(payload: FeedbackPayload): Promise<{ status: string }> {
    if (USE_MOCK) {
      return delay({ status: 'recorded' }, 200)
    }
    return http<{ status: string }>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  // -------------------------------------------------------------------------
  // Dashboards / router
  // -------------------------------------------------------------------------

  /** `GET /api/admin/llm-config` */
  async getLLMConfig(): Promise<LLMConfig> {
    if (USE_MOCK) {
      return delay(MOCK_LLM_CONFIG)
    }
    return http<LLMConfig>('/api/admin/llm-config')
  },

  /** `POST /api/admin/llm-config` */
  async updateLLMConfig(payload: LLMConfigUpdatePayload): Promise<LLMConfig> {
    if (USE_MOCK) {
      const provider = payload.provider
      const localModel = payload.local_model ?? MOCK_LLM_CONFIG.local.model
      return delay({
        ...MOCK_LLM_CONFIG,
        provider,
        local: { ...MOCK_LLM_CONFIG.local, model: localModel },
        cloud: {
          ...MOCK_LLM_CONFIG.cloud,
          ...(payload.cloud_model ? { model: payload.cloud_model } : {}),
          ...(payload.api_key
            ? { has_api_key: true, api_key_masked: `....${payload.api_key.slice(-4)}` }
            : {}),
        },
        effective: {
          target: provider === 'local' ? 'ollama' : MOCK_LLM_CONFIG.cloud.provider,
          model: provider === 'local' ? localModel : MOCK_LLM_CONFIG.cloud.model,
        },
        updated_by: 'admin@example.edu',
        updated_at: new Date().toISOString(),
      })
    }
    return http<LLMConfig>('/api/admin/llm-config', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /** `GET /api/admin/ollama-models` */
  async listOllamaModels(): Promise<OllamaModelsResponse> {
    if (USE_MOCK) {
      return delay(MOCK_OLLAMA_MODELS, 700)
    }
    return http<OllamaModelsResponse>('/api/admin/ollama-models')
  },

  /** `GET /api/admin/analytics` */
  async getAnalytics(): Promise<TelemetryAnalytics> {
    if (USE_MOCK) {
      return delay(MOCK_ANALYTICS, 300)
    }
    return http<TelemetryAnalytics>('/api/admin/analytics')
  },

  /** `POST /api/session` — record a login + module-chat open. */
  async openSession(payload: SessionOpenPayload): Promise<SessionOpenResponse> {
    if (USE_MOCK) {
      return delay(mockSessionOpen(payload), 220)
    }
    return http<SessionOpenResponse>('/api/session', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /**
   * `GET /api/tutor/analytics` — analytics scoped to the tutor's assigned
   * modules (falls back to all modules for an admin viewer).
   */
  async getTutorAnalytics(modules: string[] = []): Promise<TutorAnalytics> {
    if (USE_MOCK) {
      return delay(mockTutorAnalytics(modules), 340)
    }
    const query = modules.length ? `?modules=${encodeURIComponent(modules.join(','))}` : ''
    return http<TutorAnalytics>(`/api/tutor/analytics${query}`)
  },

  /** `GET /api/admin/overview` — users, roles and guardrail flag totals. */
  async getAdminOverview(): Promise<AdminOverview> {
    if (USE_MOCK) {
      const users: UserRecord[] = mockBackend.listProfiles().map((profile) => ({
        student_id: profile.student_id,
        email: profile.email,
        full_name: profile.full_name,
        student_number: profile.student_number,
        role: profile.role,
        roles: profile.roles,
        is_tutor: profile.is_tutor,
        is_active: true,
        modules: Array.from(new Set([...profile.modules, ...profile.enrolled_modules])),
        tutor_modules: profile.modules,
        enrolled_modules: profile.enrolled_modules,
        last_login_at: null,
      }))
      return delay(
        {
          users,
          guardrail_flags: MOCK_GUARDRAIL_FLAGS,
          total_students: users.filter((u) => u.role === 'student').length,
          total_tutors: users.filter((u) => u.is_tutor).length,
          total_admins: users.filter((u) => u.role === 'admin').length,
          active_sessions: 37,
        },
        300,
      )
    }
    return http<AdminOverview>('/api/admin/overview')
  },
}

export { USE_MOCK }
