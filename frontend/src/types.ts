/**
 * Shared types for the Client Tier.
 *
 * These mirror the JSON contracts emitted by the Flask API Gateway
 * (`src/api/chat_routes.py`, `src/api/admin_routes.py`) one-for-one, so the
 * mock layer in `src/api/mockData.ts` can be deleted and replaced with real
 * `fetch` calls without touching a single component.
 */

/** Roles accepted by the chat transcript. */
/** Roles that can hold a login account. `lecturer` is a superset of `tutor`. */
export type AccountRole = 'student' | 'tutor' | 'lecturer' | 'admin'

/** The signed-in account, as returned by `/api/auth/me` and the login routes. */
export interface SessionUser {
  user_id: number
  email: string
  full_name: string | null
  role: AccountRole
  status: 'active' | 'suspended'
  student_number: string | null
  student_id: number | null
  is_staff: boolean
  /** Modules this account may act on. Empty for students and admins. */
  modules: string[]
}

export interface AuthResponse {
  user: SessionUser
  token: string
}

/** Public signup policy, so the form can render only the fields that apply. */
export interface SignupPolicy {
  allow_self_signup: boolean
  roles: AccountRole[]
  student_email_domain: string
  lecturer_email_domain: string
  min_password_length: number
  session_ttl_hours: number
}

export interface SignupPayload {
  email: string
  password: string
  role: AccountRole
  full_name?: string
  /** Students only, and required for them. */
  student_number?: string
  /** Tutors and lecturers only. */
  module_ids?: string[]
}

/** Roles that can reach the help queue and content tooling. */
export function isStaff(user: SessionUser | null): boolean {
  return Boolean(user?.is_staff)
}

/** Roles that may work the ungrounded-question queue. */
export function canUseTutorQueue(user: SessionUser | null): boolean {
  return user?.role === 'tutor' || user?.role === 'lecturer' || user?.role === 'admin'
}

export type ChatRole = 'user' | 'assistant'

/** A single message in a tutoring session. */
export interface ChatMessage {
  /** Server-issued id (`WorkflowResult.message_id`). Used to key feedback. */
  message_id: string
  role: ChatRole
  content: string
  created_at?: string
  /** Attached only to assistant turns; the audit trail from the agent workflow. */
  audit?: TurnAudit
  /** Client-side feedback state for this turn. */
  feedback?: FeedbackState
}

/** One row in the student's chat-history sidebar (`GET /api/sessions`). */
export interface ChatSessionSummary {
  session_id: string
  module_id: string | null
  module_name: string | null
  title: string
  started_at: string | null
  last_activity_at: string | null
  turn_count: number
  message_count: number
}

/** Response of `GET /api/sessions/<session_id>`. */
export interface ChatSessionDetail {
  session: ChatSessionSummary
  messages: ChatMessage[]
}

/** Everything the orchestration tier reports back for one student turn. */
export interface TurnAudit {
  intent: IntentResult
  scaffolding: ScaffoldingResult
  guardrail: GuardrailResult
  retrieval: RetrievalResult
  llm: LLMResult
  telemetry_log_id: number | null
}

export interface IntentResult {
  label: 'factual' | 'conceptual' | 'debugging' | 'problem_solving' | 'bypass' | string
  confidence: number
  source: 'heuristic' | 'llm' | string
  route?: 'direct' | 'scaffold' | string
}

export interface ScaffoldingResult {
  stage: string
  hint_depth: number
  strategy?: string
}

export interface GuardrailResult {
  flagged: boolean
  flags: string[]
  action: 'pass' | 'blocked' | 'truncated' | string
}

export interface RetrievedChunk {
  chunk_id: number
  cite_key: string
  module_id: string
  source_name: string
  source_file: string
  section_title: string | null
  distance: number
  source_category: string
  is_answer: boolean
  url: string
  anchor: string
  preview: string
}

/** One retrievable source, with a deep link into the rendered corpus page. */
export interface Citation {
  cite_key: string
  module_id: string
  source_file: string
  section_title: string | null
  url: string
  anchor: string
  distance: number
  source_category: string
  is_answer: boolean
}

export interface RetrievedPattern {
  pattern_id: number
  module_id: string | null
  error_title: string
  exception_thrown: string | null
  distance: number
  hint: string
}

export interface RetrievalResult {
  query: string
  module_id: string
  chunks: RetrievedChunk[]
  patterns: RetrievedPattern[]
  citations?: Citation[]
  grounding_categories?: string[]
  third_party_fallback?: boolean
  below_threshold?: number
  context_empty?: boolean
}

export interface LLMResult {
  text?: string
  provider: 'local' | 'cloud' | string
  model: string
  backend: string
  latency_ms: number
}

/** Feedback reason tags — must match `FEEDBACK_REASON_TAGS` in `src/models.py`. */
export const FEEDBACK_REASON_TAGS = [
  'too_confusing',
  'too_long_or_too_short',
  'gave_away_answer',
  'incorrect_answer',
] as const

export type FeedbackReasonTag = (typeof FEEDBACK_REASON_TAGS)[number]

/** Human-facing labels for the micro-feedback tags. */
export const FEEDBACK_REASON_LABELS: Record<FeedbackReasonTag, string> = {
  too_confusing: 'Still stuck',
  too_long_or_too_short: 'Too long / too short',
  gave_away_answer: 'Gave away answer',
  incorrect_answer: 'Incorrect answer',
}

export interface FeedbackState {
  rating: 1 | -1
  reason_tag?: FeedbackReasonTag
  comment?: string
  submitted?: boolean
}

/** Body of `POST /api/feedback`. */
export interface FeedbackPayload {
  session_id: string
  message_id: string
  module_id: string
  rating: 1 | -1
  reason_tag?: FeedbackReasonTag
  comment?: string
}

/** Body of `POST /api/chat`. */
export interface ChatRequestPayload {
  message: string
  module_id: string
  session_id: string
  history: Array<{ role: ChatRole; content: string }>
}

/** Response of `POST /api/chat` (`WorkflowResult.to_dict()` + identity/access). */
export interface ChatResponse {
  session_id: string
  message_id: string
  reply: string
  intent: IntentResult
  scaffolding: ScaffoldingResult
  guardrail: GuardrailResult
  retrieval: RetrievalResult
  llm: LLMResult
  telemetry_log_id: number | null
  identity?: Identity
  module_access?: ModuleAccess
}

export interface Identity {
  student_id: number | null
  email: string | null
  role: 'student' | 'tutor' | 'admin' | string
  modules?: string[]
}

export interface ModuleAccess {
  allowed: boolean
  exists: boolean
  reason?: string
}

/** A module from the data-tier registry (`src/config.py` MODULE_REGISTRY). */
export interface Module {
  module_id: string
  module_code: string
  module_name: string
  course_code: string
  language: string
}

// ---------------------------------------------------------------------------
// Dynamic LLM Router (section 10.2) — shapes from `LLMConfigService.describe`
// ---------------------------------------------------------------------------

export type LLMProvider = 'local' | 'cloud'
export type CloudProvider =
  | 'openai'
  | 'openai_compatible'
  | 'azure_openai'
  | 'anthropic'
  | 'groq'

export interface LLMConfig {
  config_id: number
  name: string
  is_active: boolean
  provider: LLMProvider
  local: {
    base_url: string
    model: string
  }
  cloud: {
    provider: CloudProvider
    base_url: string
    model: string
    has_api_key: boolean
    api_key_masked: string | null
  }
  generation: {
    temperature: number
    max_tokens: number
    top_p: number
  }
  effective: {
    target: string
    model: string
  }
  updated_by: string | null
  updated_at: string | null
}

/** A row from `GET /api/admin/ollama-models`. */
export interface OllamaModel {
  name: string
  model: string
  size_bytes: number | null
  size_gb: number | null
  modified_at: string | null
  family: string | null
  parameter_size: string | null
}

export interface OllamaModelsResponse {
  base_url: string
  count: number
  models: OllamaModel[]
}

/** Body of `POST /api/admin/llm-config`. */
export interface LLMConfigUpdatePayload {
  provider: LLMProvider
  local_model?: string
  ollama_base_url?: string
  cloud_provider?: CloudProvider
  cloud_base_url?: string
  cloud_model?: string
  api_key?: string
  clear_api_key?: boolean
  temperature?: number
  max_tokens?: number
  top_p?: number
  updated_by?: string
}

// ---------------------------------------------------------------------------
// Telemetry analytics — aggregate of `telemetry_logs` + `hint_feedback`
// ---------------------------------------------------------------------------

export interface FeedbackSummary {
  thumbs_up: number
  thumbs_down: number
}

export interface FailureCategory {
  reason_tag: FeedbackReasonTag
  label: string
  count: number
}

export interface SatisfactionPoint {
  module_id: string
  module_name: string
  satisfaction_rate: number
  responses: number
}

export interface TelemetryAnalytics {
  total_sessions: number
  total_hints: number
  satisfaction: FeedbackSummary
  failure_categories: FailureCategory[]
  by_module: SatisfactionPoint[]
  average_latency_ms: number
}
