/**
 * Shared types for the Client Tier.
 *
 * These mirror the JSON contracts emitted by the Flask API Gateway
 * (`src/api/chat_routes.py`, `src/api/admin_routes.py`) one-for-one, so the
 * mock layer in `src/api/mockData.ts` can be deleted and replaced with real
 * `fetch` calls without touching a single component.
 */

/** Roles accepted by the chat transcript. */
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
  module_id: string
  source_name: string
  section_title: string | null
  distance: number
  preview: string
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
  /** Server-generated id for the student's persisted turn. */
  user_message_id?: string
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
  roles?: UserRole[]
  is_tutor?: boolean
  modules?: string[]
}

export interface ModuleAccess {
  allowed: boolean
  exists: boolean
  reason?: string
}

// ---------------------------------------------------------------------------
// Authentication / role-based access (Client Tier)
// ---------------------------------------------------------------------------

export type UserRole = 'student' | 'tutor' | 'admin'

/**
 * The signed-in identity held by the client.
 *
 * Produced by `POST /api/auth/login`, which provisions any valid DUT4life
 * address on first contact. `roles` is the capability set: a student who has
 * been granted tutor privileges holds `['student', 'tutor']` and can reach both
 * workspaces (dual role). `role` remains the primary/base role used to pick the
 * landing route.
 */
export interface AuthUser {
  email: string
  name: string | null
  /** Primary role used for the default landing route. */
  role: UserRole
  /** Capability roles: `student` and/or `tutor`, or `admin`. */
  roles: UserRole[]
  student_id: number | null
  is_tutor: boolean
  /** Modules a tutor is scoped to; empty for students and admins. */
  modules: string[]
  /** Modules the user is enrolled in as a student. */
  enrolled_modules: string[]
  /** True until the user saves their name + modules on first login. */
  needs_onboarding: boolean
  /** Where the identity came from — `local` today, `sso` once the IDP is wired. */
  source: 'local' | 'sso'
}

/** Server profile shape (`POST /api/auth/login`, `/api/profile`). */
export interface UserProfile {
  student_id: number | null
  email: string
  full_name: string | null
  student_number: string | null
  role: UserRole
  roles: UserRole[]
  is_tutor: boolean
  is_admin: boolean
  /** Tutor scope (assigned modules). */
  modules: string[]
  /** Student enrollments. */
  enrolled_modules: string[]
  needs_onboarding: boolean
}

/** Body of `PUT /api/profile`. */
export interface ProfileUpdatePayload {
  full_name?: string
  student_number?: string
  modules?: string[]
}

/** Body of `POST /api/admin/grant-tutor`. */
export interface GrantTutorPayload {
  identifier: string
  modules: string[]
}

// ---------------------------------------------------------------------------
// Chat history (LLM-style sidebar)
// ---------------------------------------------------------------------------

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

/** A persisted turn; the same shape the live chat renders. */
export interface SessionMessage extends ChatMessage {
  session_id: string
  module_id?: string | null
}

export interface SessionDetail {
  session: ChatSessionSummary
  messages: SessionMessage[]
}

/** Body of `POST /api/session` — a student opens a module chat. */
export interface SessionOpenPayload {
  session_id: string
  module_id: string
  email?: string
  role?: UserRole
}

export interface SessionOpenResponse {
  session_id: string
  student_id: number | null
  module_id: string
  started_at: string
  last_activity_at: string
  turn_count: number
}

// ---------------------------------------------------------------------------
// Tutor dashboard — scoped analytics (assigned modules only)
// ---------------------------------------------------------------------------

export interface ScopeModule {
  module_id: string
  module_name: string
}

export interface StruggleTopic {
  module_id: string
  /** Section/topic the student was working on when they struggled. */
  topic: string
  struggles: number
  students: number
}

export interface RepeatHelpStudent {
  student_id: number | null
  email: string | null
  module_id: string
  sessions: number
  turns: number
  thumbs_down: number
}

export interface TutorAnalytics {
  scope: ScopeModule[]
  window_minutes: number
  active_students: number
  total_sessions: number
  total_queries: number
  avg_hint_depth: number
  guardrail_flags: number
  struggle_topics: StruggleTopic[]
  repeat_help_students: RepeatHelpStudent[]
  generated_at: string
}

// ---------------------------------------------------------------------------
// Admin dashboard — system-wide oversight
// ---------------------------------------------------------------------------

export interface UserRecord {
  student_id: number | null
  email: string
  full_name: string | null
  student_number: string | null
  role: UserRole
  /** Capability roles (a student may also hold `tutor`). */
  roles: UserRole[]
  is_tutor: boolean
  is_active: boolean
  /** Union of enrolled + tutor-assigned modules. */
  modules: string[]
  tutor_modules: string[]
  enrolled_modules: string[]
  last_login_at: string | null
}

export interface GuardrailFlag {
  flag: string
  count: number
}

export interface AdminOverview {
  users: UserRecord[]
  guardrail_flags: GuardrailFlag[]
  total_students: number
  total_tutors: number
  total_admins: number
  active_sessions: number
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
export type CloudProvider = 'openai' | 'openai_compatible' | 'azure_openai' | 'anthropic'

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
