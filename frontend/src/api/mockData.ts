/**
 * Static mock payloads for the frontend-first prototype.
 *
 * Student chat history and profiles are no longer hard-coded here — they live
 * in `src/api/mockBackend.ts` (localStorage-backed) so the sidebar behaves like
 * the real API. This file only owns the reference/analytics payloads.
 */

import type {
  LLMConfig,
  Module,
  OllamaModelsResponse,
  SessionOpenPayload,
  SessionOpenResponse,
  StruggleTopic,
  TelemetryAnalytics,
  TutorAnalytics,
} from '../types'

export const MOCK = true

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

// ---------------------------------------------------------------------------
// Dynamic LLM Router mocks
// ---------------------------------------------------------------------------

export const MOCK_LLM_CONFIG: LLMConfig = {
  config_id: 1,
  name: 'default',
  is_active: true,
  provider: 'local',
  local: { base_url: 'http://localhost:11434', model: 'qwen3:4b' },
  cloud: {
    provider: 'openai',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    has_api_key: true,
    api_key_masked: '....4f2a',
  },
  generation: { temperature: 0.4, max_tokens: 900, top_p: 0.9 },
  effective: { target: 'ollama', model: 'qwen3:4b' },
  updated_by: 'admin@example.edu',
  updated_at: '2026-09-23T16:41:00Z',
}

export const MOCK_OLLAMA_MODELS: OllamaModelsResponse = {
  base_url: 'http://localhost:11434',
  count: 4,
  models: [
    {
      name: 'qwen3:4b',
      model: 'qwen3:4b',
      size_bytes: 2_500_000_000,
      size_gb: 2.5,
      modified_at: '2026-09-20T10:02:00Z',
      family: 'qwen3',
      parameter_size: '4B',
    },
    {
      name: 'qwen2.5-coder:7b',
      model: 'qwen2.5-coder:7b',
      size_bytes: 4_700_000_000,
      size_gb: 4.7,
      modified_at: '2026-09-18T08:11:00Z',
      family: 'qwen2',
      parameter_size: '7B',
    },
    {
      name: 'deepseek-r1:7b',
      model: 'deepseek-r1:7b',
      size_bytes: 4_900_000_000,
      size_gb: 4.9,
      modified_at: '2026-09-15T20:33:00Z',
      family: 'deepseek',
      parameter_size: '7B',
    },
    {
      name: 'llama3.2:3b',
      model: 'llama3.2:3b',
      size_bytes: 2_000_000_000,
      size_gb: 2.0,
      modified_at: '2026-09-12T13:05:00Z',
      family: 'llama',
      parameter_size: '3B',
    },
  ],
}

// ---------------------------------------------------------------------------
// Telemetry analytics mocks — the "LLM Health Index"
// ---------------------------------------------------------------------------

export const MOCK_ANALYTICS: TelemetryAnalytics = {
  total_sessions: 1284,
  total_hints: 6721,
  satisfaction: { thumbs_up: 5340, thumbs_down: 1381 },
  failure_categories: [
    { reason_tag: 'too_confusing', label: 'Still stuck', count: 512 },
    { reason_tag: 'too_long_or_too_short', label: 'Too long / too short', count: 388 },
    { reason_tag: 'gave_away_answer', label: 'Gave away answer', count: 241 },
    { reason_tag: 'incorrect_answer', label: 'Incorrect answer', count: 240 },
  ],
  by_module: [
    { module_id: 'IPRT301', module_name: 'Internet Programming', satisfaction_rate: 0.82, responses: 2180 },
    { module_id: 'PBDV301', module_name: 'Platform Based Development', satisfaction_rate: 0.77, responses: 1940 },
    { module_id: 'RESK301', module_name: 'Research Skills', satisfaction_rate: 0.88, responses: 1320 },
    { module_id: 'SPRI301', module_name: 'Social and Professional Issues', satisfaction_rate: 0.9, responses: 1281 },
  ],
  average_latency_ms: 1633,
}

// ---------------------------------------------------------------------------
// Session tracking (login + module-chat activity)
// ---------------------------------------------------------------------------

/** Mirrors the row `POST /api/session` upserts into `tutoring_sessions`. */
export function mockSessionOpen(payload: SessionOpenPayload): SessionOpenResponse {
  const now = new Date().toISOString()
  return {
    session_id: payload.session_id,
    student_id: 1042,
    module_id: payload.module_id,
    started_at: now,
    last_activity_at: now,
    turn_count: 0,
  }
}

// ---------------------------------------------------------------------------
// Tutor dashboard — scoped analytics
// ---------------------------------------------------------------------------

/** Every struggle topic, keyed by module, so a scope filter can slice it. */
const MOCK_STRUGGLE_TOPICS: StruggleTopic[] = [
  { module_id: 'IPRT301', topic: 'NullPointerException handling', struggles: 42, students: 18 },
  { module_id: 'IPRT301', topic: 'JDBC connection pooling', struggles: 27, students: 11 },
  { module_id: 'IPRT301', topic: 'Off-by-one loop boundaries', struggles: 23, students: 15 },
  { module_id: 'IPRT301', topic: 'Servlet request lifecycle', struggles: 14, students: 9 },
  { module_id: 'PBDV301', topic: 'Mutable default arguments', struggles: 31, students: 14 },
  { module_id: 'PBDV301', topic: 'List vs. generator iteration', struggles: 22, students: 12 },
  { module_id: 'PBDV301', topic: 'Exception scope in try/except', struggles: 19, students: 10 },
  { module_id: 'RESK301', topic: 'Referencing academic sources', struggles: 12, students: 8 },
  { module_id: 'SPRI301', topic: 'Ethical case analysis', struggles: 9, students: 6 },
]

const MOCK_REPEAT_HELP = [
  { student_id: 1042, email: 'student@dut4life.ac.za', module_id: 'IPRT301', sessions: 4, turns: 17, thumbs_down: 3 },
  { student_id: 1055, email: '22000123@dut4life.ac.za', module_id: 'IPRT301', sessions: 3, turns: 12, thumbs_down: 2 },
  { student_id: 1078, email: '22000488@dut4life.ac.za', module_id: 'PBDV301', sessions: 5, turns: 21, thumbs_down: 4 },
  { student_id: 1090, email: '22000601@dut4life.ac.za', module_id: 'PBDV301', sessions: 3, turns: 11, thumbs_down: 2 },
  { student_id: 1112, email: '22000730@dut4life.ac.za', module_id: 'RESK301', sessions: 2, turns: 8, thumbs_down: 1 },
]

/**
 * Build the tutor analytics payload, filtered to `modules` when supplied.
 * An empty scope (admin viewer) returns every module.
 */
export function mockTutorAnalytics(modules: string[] = []): TutorAnalytics {
  const inScope = (moduleId: string) => modules.length === 0 || modules.includes(moduleId)
  const scope = MODULES.filter((m) => inScope(m.module_id)).map((m) => ({
    module_id: m.module_id,
    module_name: m.module_name,
  }))
  const struggle = MOCK_STRUGGLE_TOPICS.filter((t) => inScope(t.module_id)).sort(
    (a, b) => b.struggles - a.struggles,
  )
  const repeat = MOCK_REPEAT_HELP.filter((r) => inScope(r.module_id))

  return {
    scope,
    window_minutes: 60,
    active_students: modules.length === 2 ? 37 : 94,
    total_sessions: modules.length === 2 ? 68 : 176,
    total_queries: modules.length === 2 ? 289 : 741,
    avg_hint_depth: modules.length === 2 ? 2.4 : 2.1,
    guardrail_flags: modules.length === 2 ? 16 : 43,
    struggle_topics: struggle,
    repeat_help_students: repeat,
    generated_at: new Date().toISOString(),
  }
}

/** Guardrail flag totals shown on the admin dashboard (mock mode). */
export const MOCK_GUARDRAIL_FLAGS = [
  { flag: 'solution_leak', count: 128 },
  { flag: 'out_of_scope', count: 74 },
  { flag: 'too_long', count: 51 },
  { flag: 'bypass_attempt', count: 33 },
]
