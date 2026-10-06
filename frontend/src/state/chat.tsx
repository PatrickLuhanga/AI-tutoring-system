/**
 * Chat state, lifted above the router.
 *
 * The app renders only the active view (`App.tsx`), so navigating away from the
 * chat used to unmount `StudentChat` and destroy its transcript, module choice
 * and in-flight request. This provider lives above the router, so the chat
 * survives navigation: the pending `api.chat` promise writes its reply into this
 * store even while the user is on another tab.
 *
 * A small slice (`activeSessionId`, `moduleId`, `messages`, draft `input`) is
 * mirrored to `sessionStorage` so a full page reload also restores the thread.
 * When an `activeSessionId` is restored but the transcript is empty (e.g. the
 * storage write failed, or only the id was kept), the thread is rehydrated from
 * the server via `GET /api/sessions/<id>`.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api } from '../api/client'
import { MODULES } from '../api/mockData'
import type {
  ChatMessage,
  ChatSessionSummary,
  FeedbackState,
  LLMResult,
  Module,
} from '../types'

const STORAGE_KEY = 'ai-tutor.chat'

const newSessionId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2)

interface PersistedChat {
  activeSessionId: string | null
  moduleId: string
  messages: ChatMessage[]
  input: string
}

function readStored(): Partial<PersistedChat> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Partial<PersistedChat>
    return {
      activeSessionId: typeof parsed.activeSessionId === 'string' ? parsed.activeSessionId : null,
      moduleId: typeof parsed.moduleId === 'string' ? parsed.moduleId : undefined,
      messages: Array.isArray(parsed.messages) ? (parsed.messages as ChatMessage[]) : undefined,
      input: typeof parsed.input === 'string' ? parsed.input : undefined,
    }
  } catch {
    return {}
  }
}

interface ChatValue {
  modules: Module[]
  moduleId: string
  activeModule: Module
  messages: ChatMessage[]
  input: string
  sending: boolean
  sessions: ChatSessionSummary[]
  sessionsLoading: boolean
  activeSessionId: string | null
  loadingSession: boolean
  error: string | null
  activeModel: LLMResult | null

  setInput: (value: string) => void
  setError: (value: string | null) => void
  send: () => Promise<void>
  startNewChat: () => void
  openSession: (sessionId: string) => Promise<void>
  removeSession: (sessionId: string) => Promise<void>
  changeModule: (moduleId: string) => void
  refreshSessions: () => Promise<void>
  updateFeedback: (messageId: string, next: FeedbackState | undefined) => void
}

const ChatContext = createContext<ChatValue | null>(null)

export function ChatProvider({ children }: { children: ReactNode }) {
  const stored = useMemo(() => readStored(), [])

  const [modules, setModules] = useState<Module[]>(MODULES)
  const [moduleId, setModuleId] = useState<string>(
    () => stored.moduleId ?? MODULES[1].module_id,
  )
  const [messages, setMessages] = useState<ChatMessage[]>(() => stored.messages ?? [])
  const [input, setInput] = useState<string>(() => stored.input ?? '')
  const [sending, setSending] = useState(false)

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(
    () => stored.activeSessionId ?? null,
  )
  const [loadingSession, setLoadingSession] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Keep the latest messages reachable from effects without re-subscribing.
  // Synced in an effect (not during render) so the rehydrate check below sees
  // the committed transcript.
  const messagesRef = useRef(messages)
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  // --- Persist a small slice so a reload restores the thread --------------
  useEffect(() => {
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeSessionId, moduleId, messages, input }),
      )
    } catch {
      // Quota exceeded or storage unavailable - the in-memory state still works.
    }
  }, [activeSessionId, moduleId, messages, input])

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions())
    } catch {
      setSessions([])
    }
  }, [])

  // --- Load the module registry once --------------------------------------
  useEffect(() => {
    let cancelled = false
    void api
      .getModules()
      .then((live) => {
        if (cancelled || !live.length) return
        setModules(live)
        setModuleId((current) =>
          live.some((m) => m.module_id === current) ? current : live[0].module_id,
        )
      })
      .catch((err) => console.error('could not load modules', err))
    return () => {
      cancelled = true
    }
  }, [])

  // --- Load the session list once -----------------------------------------
  useEffect(() => {
    let cancelled = false
    async function run() {
      setSessionsLoading(true)
      try {
        const data = await api.listSessions()
        if (!cancelled) setSessions(data)
      } catch {
        if (!cancelled) setSessions([])
      } finally {
        if (!cancelled) setSessionsLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [])

  // --- Rehydrate a restored session from the server -----------------------
  // Only when we have an id but no transcript in memory (a reload where the
  // message slice was not persisted, or storage was cleared).
  useEffect(() => {
    if (!activeSessionId) return
    if (messagesRef.current.length > 0) return
    let cancelled = false
    setLoadingSession(true)
    api
      .getSession(activeSessionId)
      .then((detail) => {
        if (cancelled) return
        setMessages(detail.messages)
        if (detail.session.module_id) setModuleId(detail.session.module_id)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoadingSession(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeSessionId])

  const startNewChat = useCallback(() => {
    setActiveSessionId(null)
    setMessages([])
    setInput('')
    setError(null)
  }, [])

  const openSession = useCallback(
    async (sessionId: string) => {
      if (sessionId === activeSessionId) return
      setLoadingSession(true)
      setError(null)
      try {
        const detail = await api.getSession(sessionId)
        setActiveSessionId(sessionId)
        setMessages(detail.messages)
        if (detail.session.module_id) setModuleId(detail.session.module_id)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoadingSession(false)
      }
    },
    [activeSessionId],
  )

  const removeSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.deleteSession(sessionId)
        if (sessionId === activeSessionId) startNewChat()
        await refreshSessions()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [activeSessionId, refreshSessions, startNewChat],
  )

  const changeModule = useCallback(
    (nextModule: string) => {
      setModuleId(nextModule)
      // A session is scoped to one module, so switching starts a fresh chat.
      if (activeSessionId) startNewChat()
    },
    [activeSessionId, startNewChat],
  )

  const updateFeedback = useCallback((messageId: string, next: FeedbackState | undefined) => {
    setMessages((prev) =>
      prev.map((m) => (m.message_id === messageId ? { ...m, feedback: next } : m)),
    )
  }, [])

  const send = useCallback(async () => {
    // Read fresh values: `send` outlives the component, so it must not capture a
    // stale transcript or draft from an earlier render.
    const text = input.trim()
    if (!text || sending) return

    const sessionId = activeSessionId ?? newSessionId()
    if (!activeSessionId) setActiveSessionId(sessionId)

    const moduleForTurn = modules.find((m) => m.module_id === moduleId) ?? modules[0]
    const userTurn: ChatMessage = {
      message_id: `local-${newSessionId()}`,
      role: 'user',
      content: text,
    }
    const history = messagesRef.current.map((m) => ({ role: m.role, content: m.content }))

    setMessages((prev) => [...prev, userTurn])
    setInput('')
    setSending(true)
    setError(null)

    try {
      const response = await api.chat({
        message: text,
        module_id: moduleId,
        session_id: sessionId,
        history,
      })
      // This runs in the provider, so the reply lands in the store even if the
      // user navigated away from the chat while it was generating.
      setMessages((prev) => [
        ...prev,
        {
          message_id: response.message_id,
          role: 'assistant',
          content: response.reply,
          audit: {
            intent: response.intent,
            scaffolding: response.scaffolding,
            guardrail: response.guardrail,
            retrieval: response.retrieval,
            llm: response.llm,
            telemetry_log_id: response.telemetry_log_id,
          },
        },
      ])

      // Show the chat in the sidebar immediately (a brand-new session does not
      // exist in the list yet), then reconcile with the server.
      const nowIso = new Date().toISOString()
      setSessions((prev) => {
        if (prev.some((s) => s.session_id === sessionId)) return prev
        return [
          {
            session_id: sessionId,
            module_id: moduleId,
            module_name: moduleForTurn?.module_name ?? null,
            title: text.length > 80 ? `${text.slice(0, 80)}…` : text,
            started_at: nowIso,
            last_activity_at: nowIso,
            turn_count: 1,
            message_count: 2,
          },
          ...prev,
        ]
      })
      await refreshSessions()
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          message_id: `error-${newSessionId()}`,
          role: 'assistant',
          content: `The tutoring model is unavailable.\n\n\`\`\`text\n${
            err instanceof Error ? err.message : String(err)
          }\n\`\`\``,
        },
      ])
    } finally {
      setSending(false)
    }
  }, [input, sending, activeSessionId, moduleId, modules, refreshSessions])

  const activeModule = useMemo(
    () => modules.find((m) => m.module_id === moduleId) ?? modules[0],
    [modules, moduleId],
  )

  const activeModel = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const llm = messages[i].audit?.llm
      if (llm) return llm
    }
    return null
  }, [messages])

  const value = useMemo<ChatValue>(
    () => ({
      modules,
      moduleId,
      activeModule,
      messages,
      input,
      sending,
      sessions,
      sessionsLoading,
      activeSessionId,
      loadingSession,
      error,
      activeModel,
      setInput,
      setError,
      send,
      startNewChat,
      openSession,
      removeSession,
      changeModule,
      refreshSessions,
      updateFeedback,
    }),
    [
      modules,
      moduleId,
      activeModule,
      messages,
      input,
      sending,
      sessions,
      sessionsLoading,
      activeSessionId,
      loadingSession,
      error,
      activeModel,
      send,
      startNewChat,
      openSession,
      removeSession,
      changeModule,
      refreshSessions,
      updateFeedback,
    ],
  )

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export function useChat(): ChatValue {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChat must be used inside <ChatProvider>')
  return ctx
}
