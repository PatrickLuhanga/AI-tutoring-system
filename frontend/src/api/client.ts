/**
 * Single seam between the components and the API Gateway.
 *
 * Flip `MOCK` to `false` (or set `VITE_USE_MOCK=false`) and the exact same
 * component tree will start talking to Flask through the Vite `/api` proxy.
 */

import type {
  ChatRequestPayload,
  ChatResponse,
  FeedbackPayload,
  LLMConfig,
  LLMConfigUpdatePayload,
  OllamaModelsResponse,
  TelemetryAnalytics,
} from '../types'
import {
  MOCK,
  MOCK_ANALYTICS,
  MOCK_LLM_CONFIG,
  MOCK_OLLAMA_MODELS,
  mockChatResponse,
} from './mockData'

const USE_MOCK = MOCK && import.meta.env.VITE_USE_MOCK !== 'false'

const ADMIN_KEY = import.meta.env.VITE_ADMIN_KEY ?? 'change-me-admin-key'

function delay<T>(value: T, ms = 450): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', 'X-Admin-Key': ADMIN_KEY },
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
