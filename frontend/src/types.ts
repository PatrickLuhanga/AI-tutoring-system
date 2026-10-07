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
  /** `curriculum` (the module's own notes), `web` (web fallback), or `none`. */
  source_kind?: 'curriculum' | 'web' | 'none' | string
  /** Domains contributing to a `web` result. */
  web_domains?: string[]
  /** True when the web fallback ran (even if it found nothing). */
  web_attempted?: boolean
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
  /** Distinct source documents indexed for this module (from the chunk store). */
  document_count?: number
  /** Total chunks indexed for this module. */
  chunk_count?: number
}

// ---------------------------------------------------------------------------
// Ingested knowledge base (`GET /api/materials/<module_id>`)
// ---------------------------------------------------------------------------

/** One text chunk as stored in the vector store, for auditing. */
export interface MaterialChunk {
  chunk_id: number
  chunk_index: number
  section_title: string | null
  token_count: number | null
  is_answer: boolean
  text: string
}

/** One source document and a bounded sample of its ingested chunks. */
export interface MaterialDocument {
  source_file: string
  source_name: string
  source_type: string
  source_category: string
  topic: string | null
  section_title: string | null
  chunk_count: number
  returned: number
  chunks: MaterialChunk[]
}

/** Response of `GET /api/materials/<module_id>`. */
export interface ModuleMaterials {
  module_id: string
  module_name: string
  language: string | null
  total_chunks: number
  document_count: number
  returned_chunks: number
  limit_per_document: number
  documents: MaterialDocument[]
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

// ---------------------------------------------------------------------------
// Tutor telemetry — fallback queries and high hint-depth "struggles"
// ---------------------------------------------------------------------------

/** A queued question that missed the module's own material (GET /api/tutor/questions). */
export interface TutorFallbackQuestion {
  question_id: number
  module_id: string | null
  question_text: string
  intent: string | null
  reason: string
  best_distance: number | null
  occurrences: number
  status: string
  answer_text: string | null
  answered_at?: string | null
  promoted_chunk_id?: number | null
  created_at: string | null
}

export interface TutorScopeInfo {
  role: string
  is_admin: boolean
  modules: string[]
}

export interface TutorQuestionsResponse {
  scope: TutorScopeInfo
  status: string
  fallback?: boolean
  count: number
  questions: TutorFallbackQuestion[]
}

/** One module/intent bucket of turns that needed deep scaffolding. */
export interface TutorStruggle {
  module_id: string | null
  intent: string | null
  turns: number
  max_hint_depth: number
  avg_hint_depth: number
  latest_at: string | null
}

export interface TutorStrugglesResponse {
  scope: TutorScopeInfo
  module_id: string | null
  threshold: number
  total_turns: number
  struggles: TutorStruggle[]
}

/** Per-status queue counts from `GET /api/tutor/questions/summary`. */
export interface TutorQueueCount {
  questions: number
  occurrences: number
}

export interface TutorSummaryResponse {
  scope: TutorScopeInfo
  counts: Record<string, TutorQueueCount>
  open_total?: number
  open_occurrences?: number
  answered_total?: number
}

/** Human labels for why a question left the module's own material. */
export const FALLBACK_REASON_LABELS: Record<string, string> = {
  no_context: 'nothing in the module matched',
  below_threshold: 'only weak matches',
  third_party_only: 'answered from a textbook, not the notes',
  low_confidence: 'grounding looked weak',
  student_flagged: 'the student said it was wrong',
}
