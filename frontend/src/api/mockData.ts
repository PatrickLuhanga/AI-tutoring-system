/**
 * Minimal offline fixtures for the frontend-first prototype.
 *
 * Mock mode is OPT-IN (`VITE_USE_MOCK=true`). What used to live here - a cycled
 * transcript of canned C#/debugging replies and an Ollama `qwen3:4b` model list -
 * has been deleted. Those strings could shadow a real backend failure by
 * returning plausible-looking content while the live API was actually down.
 *
 * What remains is only data that must exist for the UI to render offline: the
 * module registry, and empty/minimal shapes for the mock endpoints so a component
 * never crashes. Nothing here fabricates a tutoring answer.
 */

import type { ChatResponse, LLMConfig, Module } from '../types'

export const MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** Mirrors MODULE_REGISTRY in src/config.py. */
export const MODULES: Module[] = [
  {
    module_id: 'IPRT301',
    module_code: 'IPRT301',
    module_name: 'Internet Programming',
    course_code: 'DIP3',
    language: 'Java',
  },
  {
    module_id: 'PBDV301',
    module_code: 'PBDV301',
    module_name: 'Platform Based Development',
    course_code: 'DIP3',
    language: 'Python',
  },
  {
    module_id: 'RESK301',
    module_code: 'RESK301',
    module_name: 'Research Skills',
    course_code: 'DIP3',
    language: 'N/A',
  },
  {
    module_id: 'SPRI301',
    module_code: 'SPRI301',
    module_name: 'Social and Professional Issues',
    course_code: 'DIP3',
    language: 'N/A',
  },
]

/**
 * A mock `POST /api/chat` response that explicitly says it is offline.
 *
 * It deliberately does NOT produce a fake tutoring answer: mock mode exists to
 * exercise the UI shell, not to pretend a model replied. The message tells the
 * user to disable mock mode to reach the real backend, which is the honest
 * behaviour when there is no provider behind the request.
 */
export function mockChatResponse(message: string, sessionId: string, moduleId: string): ChatResponse {
  return {
    session_id: sessionId,
    message_id: `mock-${Date.now().toString(36)}`,
    reply:
      '**Mock mode is on** — no model was called.\n\n' +
      'Set `VITE_USE_MOCK=false` in `frontend/.env` and restart the dev server ' +
      'to talk to the real tutoring backend.',
    intent: { label: 'other', confidence: 0.0, source: 'heuristic', route: 'direct' },
    scaffolding: { stage: 'direct_answer', hint_depth: 0 },
    guardrail: { flagged: false, flags: [], action: 'pass' },
    retrieval: {
      query: message.slice(0, 120),
      module_id: moduleId,
      chunks: [],
      patterns: [],
      citations: [],
    },
    llm: { provider: 'mock', model: 'mock', backend: 'mock', latency_ms: 0 },
    telemetry_log_id: null,
    identity: { student_id: null, email: null, role: 'student' },
    module_access: { allowed: true, exists: true },
  }
}

/** A shape-correct stand-in for the active LLM config when offline. */
export const MOCK_LLM_CONFIG: LLMConfig = {
  config_id: 0,
  name: 'mock',
  is_active: true,
  provider: 'cloud',
  local: { base_url: 'http://localhost:11434', model: '' },
  cloud: {
    provider: 'groq',
    base_url: 'https://api.groq.com/openai/v1',
    model: 'openai/gpt-oss-120b',
    has_api_key: false,
    api_key_masked: null,
  },
  generation: { temperature: 0.4, max_tokens: 512, top_p: 0.9 },
  effective: { target: 'mock', model: 'mock' },
  updated_by: null,
  updated_at: null,
}
