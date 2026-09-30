/**
 * Single seam between the components and the API Gateway.
 *
 * Flip `MOCK` to `false` (or set `VITE_USE_MOCK=false`) and the exact same
 * component tree will start talking to Flask through the Vite `/api` proxy.
 */

import { readToken } from './auth'
import type {
  ChatRequestPayload,
  ChatResponse,
  FeedbackPayload,
  LLMConfig,
  LLMConfigUpdatePayload,
  Module,
  OllamaModelsResponse,
  TelemetryAnalytics,
} from '../types'
import {
  MOCK,
  MOCK_ANALYTICS,
  MOCK_LLM_CONFIG,
  MOCK_OLLAMA_MODELS,
  MODULES as MOCK_MODULES,
  mockChatResponse,
} from './mockData'

const USE_MOCK = MOCK && import.meta.env.VITE_USE_MOCK !== 'false'

const ADMIN_KEY = import.meta.env.VITE_ADMIN_KEY ?? 'dev-admin-key'

/** One entry in a module's corpus listing, as the resource routes report it. */
export interface ResourceDoc {
  path: string
  name: string
  /** First heading in the document; falls back to the filename. */
  title: string
  /** Folder the document sits in: `slides`, `lecture_notes`, `books`, ... */
  group: string
  source_category: string
  size_bytes: number
  url: string
}

function delay<T>(value: T, ms = 450): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  // Attach the session token when there is one. Routes fall back to the dev
  // headers while AUTH_MODE=dev, so this is additive rather than a switch.
  const token = readToken()
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Key': ADMIN_KEY,
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
      /* response was not JSON */
    }
    throw new Error(`${response.status} ${detail}`)
  }
  return (await response.json()) as T
}

export const api = {
  /** `GET /api/modules` - the module registry the dropdown should render. */
  async getModules(): Promise<Module[]> {
    if (USE_MOCK) {
      return delay(MOCK_MODULES)
    }
    const body = await http<{ modules: Module[] }>('/api/modules')
    return body.modules
  },

  /** `POST /api/chat` */
  async chat(payload: ChatRequestPayload): Promise<ChatResponse> {
    if (USE_MOCK) {
      return delay(mockChatResponse(payload.message, payload.session_id, payload.module_id))
    }
    return http<ChatResponse>('/api/chat', { method: 'POST', body: JSON.stringify(payload) })
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

  /** `GET /api/practice/questions` - a randomised test. Answers are withheld. */
  async drawPractice(
    moduleId: string,
    count: number,
  ): Promise<{
    module_id: string
    available: number
    questions: Array<{ question_id: number; prompt: string; difficulty: string }>
  }> {
    return http(`/api/practice/questions?module_id=${encodeURIComponent(moduleId)}&count=${count}`)
  },

  /** `POST /api/practice/submit` - score an attempt and reveal the marking notes. */
  async submitPractice(payload: {
    module_id: string
    question_ids: number[]
    answers: Record<string, string>
  }): Promise<{
    attempt_id: number
    score: number
    max_score: number
    unmarked: number
    breakdown: Array<{
      question_id: number
      correct: boolean
      markable: boolean
      answer_notes: string | null
    }>
  }> {
    return http('/api/practice/submit', { method: 'POST', body: JSON.stringify(payload) })
  },

  /** `GET /api/notifications` - the signed-in account's inbox. */
  async getNotifications(): Promise<{
    count: number
    unread: number
    notifications: Array<{
      notification_id: number
      title: string
      body: string
      audience: string
      module_id: string | null
      is_read: boolean
      created_at: string | null
    }>
  }> {
    return http('/api/notifications')
  },

  /** `POST /api/notifications/read` - idempotent read receipts. */
  async markNotificationsRead(ids: number[]): Promise<{ marked: number }> {
    return http('/api/notifications/read', {
      method: 'POST',
      body: JSON.stringify({ notification_ids: ids }),
    })
  },

  /** `GET /api/resources/<module>` - one module's document listing. */
  async getResources(moduleId: string): Promise<ResourceDoc[]> {
    if (USE_MOCK) return delay([])
    const body = await http<{ documents: ResourceDoc[] }>(
      `/api/resources/${moduleId}`,
    )
    return body.documents ?? []
  },

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

  /**
   * Telemetry aggregate. There is no dedicated backend route yet — this is the
   * shape the dashboard expects once one is added (aggregating `telemetry_logs`
   * and `hint_feedback`).
   */
  async getAnalytics(): Promise<TelemetryAnalytics> {
    if (USE_MOCK) {
      return delay(MOCK_ANALYTICS, 300)
    }
    return http<TelemetryAnalytics>('/api/admin/analytics')
  },
}

export { USE_MOCK }
