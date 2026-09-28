/**
 * Mock backend for `VITE_USE_MOCK=true`.
 *
 * The student chat no longer depends on a hard-coded transcript: history is
 * read from this localStorage-backed store, so create / load / delete behave
 * exactly like the Flask endpoints. Profiles are provisioned dynamically on
 * first login, mirroring `POST /api/auth/login`.
 */

import type {
  ChatRequestPayload,
  ChatResponse,
  ChatSessionSummary,
  SessionDetail,
  SessionMessage,
  UserProfile,
  UserRole,
} from '../types'

const PROFILES_KEY = 'ai-tutor.mock.profiles'
const SESSIONS_KEY = 'ai-tutor.mock.sessions'

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write<T>(key: string, value: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable — mock state stays in-memory for this session */
  }
}

function rolesFor(profile: Pick<UserProfile, 'role' | 'is_tutor'>): UserRole[] {
  if (profile.role === 'admin') return ['admin']
  const roles: UserRole[] = ['student']
  if (profile.is_tutor) roles.push('tutor')
  return roles
}

const SEED_PROFILES: UserProfile[] = [
  {
    student_id: 1042,
    email: 'student@dut4life.ac.za',
    full_name: 'Sanele Ndlovu',
    student_number: '22000000',
    role: 'student',
    roles: ['student'],
    is_tutor: false,
    is_admin: false,
    modules: [],
    enrolled_modules: ['IPRT301', 'PBDV301'],
    needs_onboarding: false,
  },
  {
    student_id: 2001,
    email: 'tutor.dev@dut4life.ac.za',
    full_name: 'Dr. Thabo Mokoena',
    student_number: null,
    role: 'tutor',
    roles: ['student', 'tutor'],
    is_tutor: true,
    is_admin: false,
    modules: ['IPRT301', 'PBDV301'],
    enrolled_modules: [],
    needs_onboarding: false,
  },
  {
    student_id: 2002,
    email: 'tutor.research@dut4life.ac.za',
    full_name: 'Prof. Naledi Khumalo',
    student_number: null,
    role: 'tutor',
    roles: ['student', 'tutor'],
    is_tutor: true,
    is_admin: false,
    modules: ['RESK301', 'SPRI301'],
    enrolled_modules: [],
    needs_onboarding: false,
  },
  {
    student_id: 3001,
    email: 'admin.system@dut4life.ac.za',
    full_name: 'System Administrator',
    student_number: null,
    role: 'admin',
    roles: ['admin'],
    is_tutor: false,
    is_admin: true,
    modules: [],
    enrolled_modules: [],
    needs_onboarding: false,
  },
]

interface MockSession {
  session_id: string
  student_email: string
  module_id: string
  title: string
  started_at: string
  last_activity_at: string
  turn_count: number
  message_count: number
  messages: SessionMessage[]
}

function loadProfiles(): UserProfile[] {
  const stored = read<UserProfile[]>(PROFILES_KEY, [])
  if (stored.length === 0) {
    write(PROFILES_KEY, SEED_PROFILES)
    return [...SEED_PROFILES]
  }
  return stored
}

function saveProfiles(profiles: UserProfile[]): void {
  write(PROFILES_KEY, profiles)
}

function loadSessions(): MockSession[] {
  return read<MockSession[]>(SESSIONS_KEY, [])
}

function saveSessions(sessions: MockSession[]): void {
  write(SESSIONS_KEY, sessions)
}

function nextStudentId(profiles: UserProfile[]): number {
  return profiles.reduce((max, p) => Math.max(max, p.student_id ?? 0), 3000) + 1
}

function isAdminEmail(email: string): boolean {
  return email === 'admin.system@dut4life.ac.za' || email.startsWith('admin.')
}

function summarize(session: MockSession): ChatSessionSummary {
  return {
    session_id: session.session_id,
    module_id: session.module_id,
    module_name: null,
    title: session.title,
    started_at: session.started_at,
    last_activity_at: session.last_activity_at,
    turn_count: session.turn_count,
    message_count: session.message_count,
  }
}

const CANNED_REPLIES = [
  `That's a useful observation — let's test it rather than trust it.

> What output do you *expect* from the code as it stands, and what does it actually print?

Write down both, then run it. The gap between them is the clue we care about.`,
  `You're close. Before we move on, try to explain *why* the rule exists, not just what it is.

1. What assumption does your code make here?
2. What is the smallest input that breaks that assumption?

Answer those in your own words and I'll verify your reasoning rather than your syntax.`,
]

const BYPASS_REPLY = `I can't hand over a finished solution — that would skip the part where you learn.

Let's meet in the middle: describe the **exact error message** and the line it points to, and I'll ask you one guiding question at a time.`

let cursor = 0

function buildChatResponse(payload: ChatRequestPayload, session: MockSession): ChatResponse {
  const bypass =
    /\b(give me (the )?(answer|code|solution)|just give me|final code|solve it for me)\b/i.test(
      payload.message,
    )
  const reply = bypass ? BYPASS_REPLY : CANNED_REPLIES[cursor++ % CANNED_REPLIES.length]
  const moduleId = payload.module_id
  return {
    session_id: session.session_id,
    message_id: session.messages[session.messages.length - 1]?.message_id ?? `mock-${Date.now()}`,
    reply,
    intent: {
      label: bypass ? 'bypass' : 'debugging',
      confidence: bypass ? 0.88 : 0.9,
      source: 'heuristic',
    },
    scaffolding: { stage: 'guiding', hint_sequence_depth: 1, strategy: 'questioning' },
    guardrail: {
      flagged: bypass,
      flags: bypass ? ['bypass_attempt'] : [],
      action: bypass ? 'blocked' : 'pass',
    },
    retrieval: {
      query: payload.message.slice(0, 120),
      module_id: moduleId,
      chunks: [],
      patterns: [],
    },
    llm: { provider: 'local', model: 'qwen3:4b', backend: 'ollama', latency_ms: 1520 },
    telemetry_log_id: Math.floor(Math.random() * 100000),
    identity: { student_id: null, email: null, role: 'student' },
    module_access: { allowed: true, exists: true },
  }
}

function uid(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2)
}

export const mockBackend = {
  login(email: string): UserProfile {
    const normalized = email.trim().toLowerCase()
    const profiles = loadProfiles()
    let profile = profiles.find((p) => p.email === normalized)
    if (!profile) {
      const admin = isAdminEmail(normalized)
      profile = {
        student_id: nextStudentId(profiles),
        email: normalized,
        full_name: null,
        student_number: null,
        role: admin ? 'admin' : 'student',
        roles: admin ? ['admin'] : ['student'],
        is_tutor: false,
        is_admin: admin,
        modules: [],
        enrolled_modules: [],
        needs_onboarding: !admin,
      }
      profiles.push(profile)
      saveProfiles(profiles)
    }
    return { ...profile }
  },

  getProfile(email: string): UserProfile | null {
    const profile = loadProfiles().find((p) => p.email === email.toLowerCase())
    return profile ? { ...profile } : null
  },

  listProfiles(): UserProfile[] {
    return loadProfiles().map((p) => ({ ...p }))
  },

  updateProfile(
    email: string,
    payload: { full_name?: string; student_number?: string; modules?: string[] },
  ): UserProfile {
    const profiles = loadProfiles()
    const profile = profiles.find((p) => p.email === email.toLowerCase())
    if (!profile) throw new Error('Profile not found.')
    if (payload.full_name !== undefined) profile.full_name = payload.full_name
    if (payload.student_number !== undefined) profile.student_number = payload.student_number
    if (payload.modules !== undefined) {
      profile.enrolled_modules =
        profile.role === 'student' ? payload.modules : profile.enrolled_modules
    }
    profile.needs_onboarding =
      !profile.full_name || (profile.role === 'student' && profile.enrolled_modules.length === 0)
    saveProfiles(profiles)
    return { ...profile }
  },

  grantTutor(identifier: string, modules: string[]): UserProfile {
    const profiles = loadProfiles()
    const profile = profiles.find(
      (p) => p.email === identifier.toLowerCase() || p.student_number === identifier,
    )
    if (!profile) throw new Error(`No registered user matches '${identifier}'.`)
    profile.is_tutor = true
    profile.modules = Array.from(new Set([...profile.modules, ...modules]))
    profile.roles = rolesFor(profile)
    saveProfiles(profiles)
    return { ...profile }
  },

  revokeTutor(identifier: string): UserProfile {
    const profiles = loadProfiles()
    const profile = profiles.find(
      (p) => p.email === identifier.toLowerCase() || p.student_number === identifier,
    )
    if (!profile) throw new Error(`No registered user matches '${identifier}'.`)
    profile.is_tutor = false
    profile.modules = []
    profile.roles = rolesFor(profile)
    saveProfiles(profiles)
    return { ...profile }
  },

  listSessions(email: string, moduleId?: string): ChatSessionSummary[] {
    return loadSessions()
      .filter((s) => s.student_email === email.toLowerCase())
      .filter((s) => !moduleId || s.module_id === moduleId)
      .sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))
      .map(summarize)
  },

  getSession(email: string, sessionId: string): SessionDetail | null {
    const session = loadSessions().find(
      (s) => s.session_id === sessionId && s.student_email === email.toLowerCase(),
    )
    if (!session) return null
    return { session: summarize(session), messages: session.messages }
  },

  deleteSession(email: string, sessionId: string): boolean {
    const sessions = loadSessions()
    const next = sessions.filter(
      (s) => !(s.session_id === sessionId && s.student_email === email.toLowerCase()),
    )
    const removed = next.length !== sessions.length
    saveSessions(next)
    return removed
  },

  /** Persist a student turn + a canned tutor reply, mirroring `POST /api/chat`. */
  chat(email: string, payload: ChatRequestPayload): ChatResponse {
    const sessions = loadSessions()
    const now = new Date().toISOString()
    let session = sessions.find((s) => s.session_id === payload.session_id)
    if (!session) {
      session = {
        session_id: payload.session_id,
        student_email: email.toLowerCase(),
        module_id: payload.module_id,
        title: 'New chat',
        started_at: now,
        last_activity_at: now,
        turn_count: 0,
        message_count: 0,
        messages: [],
      }
      sessions.push(session)
    }

    if (session.title === 'New chat') {
      const collapsed = payload.message.replace(/\s+/g, ' ').trim()
      session.title = collapsed.length > 80 ? `${collapsed.slice(0, 80)}…` : collapsed
    }

    const userMessage: SessionMessage = {
      message_id: uid(),
      session_id: session.session_id,
      role: 'user',
      content: payload.message,
      module_id: payload.module_id,
      created_at: new Date().toISOString(),
    }
    session.messages.push(userMessage)

    const response = buildChatResponse(payload, session)
    const assistantMessage: SessionMessage = {
      message_id: uid(),
      session_id: session.session_id,
      role: 'assistant',
      content: response.reply,
      module_id: payload.module_id,
      created_at: new Date().toISOString(),
      audit: {
        intent: response.intent,
        scaffolding: response.scaffolding,
        guardrail: response.guardrail,
        retrieval: response.retrieval,
        llm: response.llm,
        telemetry_log_id: response.telemetry_log_id,
      },
    }
    session.messages.push(assistantMessage)
    session.message_count = session.messages.length
    session.turn_count += 1
    session.last_activity_at = new Date().toISOString()
    saveSessions(sessions)

    return {
      ...response,
      message_id: assistantMessage.message_id,
      user_message_id: userMessage.message_id,
    }
  },
}
