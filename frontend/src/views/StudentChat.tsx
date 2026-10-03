import {
  Bot,
  CornerDownLeft,
  GraduationCap,
  Loader2,
  PanelLeft,
  Plus,
  Send,
  Square,
  Trash2,
  User,
} from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, USE_MOCK } from '../api/client'
import { MODULES } from '../api/mockData'
import AuditPanel from '../components/AuditPanel'
import FeedbackControls from '../components/FeedbackControls'
import MarkdownMessage from '../components/MarkdownMessage'
import Sources from '../components/Sources'
import type {
  ChatMessage,
  ChatSessionSummary,
  FeedbackState,
  Module,
} from '../types'

const newSessionId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2)

function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

/** Grows the composer with its content up to a ceiling, then scrolls internally. */
function useAutoGrow(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`
  }, [value])
  return ref
}

/**
 * Student workspace: the Socratic chat plus an LLM-style persistent history
 * sidebar. Sessions are saved server-side (``tutoring_sessions`` /
 * ``session_messages``) and can be reopened or deleted. The transcript itself
 * ships empty - no seeded demo turns.
 */
export default function StudentChat() {
  // The dropdown renders whatever the module registry actually contains, so a
  // module added in config.py appears here without a frontend change. MODULES is
  // only the offline fallback.
  const [modules, setModules] = useState<Module[]>(MODULES)
  const [moduleId, setModuleId] = useState(() => MODULES[1].module_id)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [atBottom, setAtBottom] = useState(true)

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [loadingSession, setLoadingSession] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const composerRef = useAutoGrow(input)
  const activeModule = useMemo(
    () => modules.find((m) => m.module_id === moduleId) ?? modules[0],
    [modules, moduleId],
  )

  // The model actually serving this session, taken from the most recent turn's
  // audit. Shown in the header so the badge reflects reality rather than a
  // hard-coded "Mock" label.
  const activeModel = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const llm = messages[i].audit?.llm
      if (llm) return llm
    }
    return null
  }, [messages])

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions())
    } catch {
      setSessions([])
    }
  }, [])

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

  // Only auto-scroll when the reader is already at the bottom, so scrolling back
  // through earlier turns is not yanked away by an incoming reply.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (atBottom) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, sending, atBottom])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    setAtBottom(distance < 80)
  }, [])

  // Sessions grouped by module for the sidebar.
  const grouped = useMemo(() => {
    const map = new Map<string, ChatSessionSummary[]>()
    for (const session of sessions) {
      const key = session.module_id ?? 'Other'
      const bucket = map.get(key)
      if (bucket) bucket.push(session)
      else map.set(key, [session])
    }
    return Array.from(map.entries())
  }, [sessions])

  function moduleName(moduleKey: string): string {
    return (
      modules.find((m) => m.module_id === moduleKey)?.module_name ??
      sessions.find((s) => s.module_id === moduleKey)?.module_name ??
      moduleKey
    )
  }

  function startNewChat() {
    setActiveSessionId(null)
    setMessages([])
    setInput('')
    setError(null)
    setAtBottom(true)
    setSidebarOpen(false)
    composerRef.current?.focus()
  }

  async function openSession(sessionId: string) {
    if (sessionId === activeSessionId) return
    setLoadingSession(true)
    setError(null)
    try {
      const detail = await api.getSession(sessionId)
      setActiveSessionId(sessionId)
      setMessages(detail.messages)
      if (detail.session.module_id) setModuleId(detail.session.module_id)
      setAtBottom(true)
      setSidebarOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoadingSession(false)
    }
  }

  async function removeSession(sessionId: string, event: React.MouseEvent) {
    event.stopPropagation()
    if (!window.confirm('Delete this chat and its history? This cannot be undone.')) return
    try {
      await api.deleteSession(sessionId)
      if (sessionId === activeSessionId) startNewChat()
      await refreshSessions()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function changeModule(nextModule: string) {
    setModuleId(nextModule)
    // A session is scoped to one module, so switching starts a fresh chat.
    if (activeSessionId) startNewChat()
  }

  async function send() {
    const text = input.trim()
    if (!text || sending) return

    const sessionId = activeSessionId ?? newSessionId()
    if (!activeSessionId) setActiveSessionId(sessionId)

    const userTurn: ChatMessage = {
      message_id: `local-${newSessionId()}`,
      role: 'user',
      content: text,
    }
    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, userTurn])
    setInput('')
    setSending(true)
    setAtBottom(true)
    setError(null)

    try {
      const response = await api.chat({
        message: text,
        module_id: moduleId,
        session_id: sessionId,
        history,
      })
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
      // exist in the list yet), then reconcile with the server so titles,
      // counts and ordering are authoritative.
      const nowIso = new Date().toISOString()
      setSessions((prev) => {
        if (prev.some((s) => s.session_id === sessionId)) return prev
        return [
          {
            session_id: sessionId,
            module_id: moduleId,
            module_name: activeModule?.module_name ?? null,
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
      composerRef.current?.focus()
    }
  }

  function updateFeedback(messageId: string, next: FeedbackState | undefined) {
    setMessages((prev) =>
      prev.map((m) => (m.message_id === messageId ? { ...m, feedback: next } : m)),
    )
  }

  const hasMessages = messages.length > 0

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden">
      {/* --------------------------------------------------------- Sidebar
          Anchored to the far left edge of the workspace (fixed width), exactly
          as in ChatGPT / Gemini. On small screens it overlays the chat. */}
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close history"
          onClick={() => setSidebarOpen(false)}
          className="absolute inset-0 z-10 bg-slate-900/30 md:hidden"
        />
      )}
      <aside
        className={`${
          sidebarOpen ? 'absolute inset-y-0 left-0 z-20 flex w-72 shadow-xl' : 'hidden'
        } shrink-0 flex-col border-r border-slate-200 bg-slate-50 md:static md:z-auto md:flex md:w-64 md:shadow-none lg:w-72`}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-3 py-3">
          <span className="text-sm font-semibold text-slate-800">Chats</span>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700 md:hidden"
            aria-label="Close history"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        </div>
        <div className="border-b border-slate-100 p-3">
          <button
            type="button"
            onClick={startNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-2.5 text-sm font-medium text-on-accent transition hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-3">
          {sessionsLoading && (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Loading history…
            </div>
          )}
          {!sessionsLoading && grouped.length === 0 && (
            <p className="px-2 py-3 text-xs text-slate-400">
              No past chats yet. Start one to see it saved here.
            </p>
          )}
          {grouped.map(([moduleKey, items]) => (
            <div key={moduleKey} className="mb-3">
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                {moduleName(moduleKey)}
              </div>
              <div className="space-y-0.5">
                {items.map((session) => {
                  const active = session.session_id === activeSessionId
                  return (
                    <div
                      key={session.session_id}
                      className={`group flex items-center rounded-lg transition ${
                        active ? 'bg-blue-50' : 'hover:bg-slate-100'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => void openSession(session.session_id)}
                        className="min-w-0 flex-1 px-2.5 py-2 text-left"
                      >
                        <span
                          className={`block truncate text-sm ${
                            active ? 'font-medium text-blue-700' : 'text-slate-700'
                          }`}
                        >
                          {session.title}
                        </span>
                        <span className="block text-[10px] text-slate-400">
                          {relativeTime(session.last_activity_at)} · {session.message_count} msgs
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => void removeSession(session.session_id, e)}
                        aria-label="Delete chat"
                        className="mr-1 hidden shrink-0 rounded-md p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 group-hover:block"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* ------------------------------------------------------------ Chat
          The transcript and composer share one centred column with a max width,
          so a reply never stretches across a wide monitor. */}
      <div className="flex min-h-0 flex-1 flex-col bg-white">
        {/* Compact bar: identity, module scope, and thread control on one line. */}
        <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3">
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            className="rounded-lg border border-slate-300 bg-white p-1.5 text-slate-500 md:hidden"
            aria-label="Toggle history"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
          <div className="mr-auto flex min-w-0 items-center gap-2">
            <GraduationCap className="h-4 w-4 shrink-0 text-blue-600" />
            <h1 className="truncate text-sm font-semibold text-slate-900">Socratic Tutor</h1>
            {USE_MOCK ? (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">
                Mock
              </span>
            ) : activeModel ? (
              <span
                title={`${activeModel.provider} · ${activeModel.backend}`}
                className="max-w-[12rem] truncate rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-emerald-200"
              >
                {activeModel.model}
              </span>
            ) : (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500 ring-1 ring-slate-200">
                live
              </span>
            )}
          </div>

          <select
            value={moduleId}
            onChange={(e) => changeModule(e.target.value)}
            aria-label="Module"
            className="max-w-[13rem] truncate rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          >
            {modules.map((module) => (
              <option key={module.module_id} value={module.module_id}>
                {module.module_id} · {module.module_name}
              </option>
            ))}
          </select>

          {hasMessages && (
            <button
              type="button"
              onClick={startNewChat}
              className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <Trash2 className="h-3.5 w-3.5" />
              New
            </button>
          )}
        </header>

        {error && (
          <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        {/* Transcript scrolls independently; the composer stays pinned. Both sit
            inside a centred, width-capped column. */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 flex-1 overflow-y-auto"
        >
          <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-6">
          {!hasMessages && !loadingSession && (
              <div className="flex h-full flex-col items-center justify-center px-2 text-center">
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 text-on-accent shadow-sm">
                <Bot className="h-6 w-6" />
              </div>
              <p className="text-base font-semibold text-slate-800">
                Ask about {activeModule.module_name}
              </p>
              <p className="mt-1.5 max-w-md text-sm leading-relaxed text-slate-500">
                Paste a stack trace or describe what you are stuck on. The tutor answers
                with the next question or hint rather than the finished solution, and
                cites the lecture material it used. Your chats are saved by module and
                can be resumed any time.
              </p>
            </div>
          )}

          {loadingSession && (
            <div className="flex items-center justify-center py-10 text-sm text-slate-400">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading conversation…
            </div>
          )}

          {messages.map((message) =>
            message.role === 'user' ? (
              <div key={message.message_id} className="flex justify-end gap-2.5">
                <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2 text-sm leading-relaxed text-on-accent shadow-sm">
                  {message.content}
                </div>
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-600">
                  <User className="h-3.5 w-3.5" />
                </div>
              </div>
            ) : (
              <div key={message.message_id} className="flex gap-2.5">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-on-accent shadow-sm">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0 max-w-[85%] flex-1">
                  <MarkdownMessage
                    content={message.content}
                    citations={message.audit?.retrieval.citations}
                  />
                  {message.audit?.retrieval.citations && (
                    <Sources
                      citations={message.audit.retrieval.citations}
                      thirdPartyFallback={message.audit.retrieval.third_party_fallback}
                    />
                  )}
                  {message.audit && <AuditPanel audit={message.audit} />}
                  {!message.message_id.startsWith('error-') && (
                    <FeedbackControls
                      sessionId={activeSessionId ?? ''}
                      messageId={message.message_id}
                      moduleId={moduleId}
                      value={message.feedback}
                      onChange={(next) => updateFeedback(message.message_id, next)}
                    />
                  )}
                </div>
              </div>
            ),
          )}

          {sending && (
            <div className="flex items-center gap-2.5 text-sm text-slate-400">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-on-accent shadow-sm">
                <Bot className="h-3.5 w-3.5" />
              </div>
              <span className="flex items-center gap-2 rounded-2xl rounded-tl-md border border-slate-200 bg-slate-50/70 px-3.5 py-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Thinking through the next hint…
              </span>
            </div>
          )}
          </div>
        </div>

        {/* Composer: Enter sends, Shift+Enter breaks the line. Centred in the
            same max-width column as the transcript, pinned to the bottom. */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
          className="shrink-0 border-t border-slate-200 bg-white/90 px-4 py-3 backdrop-blur"
        >
          <div className="mx-auto w-full max-w-3xl">
          <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-1.5 shadow-sm transition focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100">
            <textarea
              ref={composerRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              rows={1}
              placeholder={`Ask a ${activeModule.module_name} question, or paste your error…`}
              className="max-h-48 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm leading-relaxed text-slate-800 placeholder:text-slate-400 focus:outline-none"
            />
            {sending ? (
              <button
                type="button"
                disabled
                title="Waiting for the tutor"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-200 text-slate-500"
              >
                <Square className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                title="Send (Enter)"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-on-accent transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            )}
          </div>
          <p className="mt-1.5 flex items-center justify-center gap-1 text-[11px] text-slate-400">
            <CornerDownLeft className="h-3 w-3" />
            {activeSessionId ? (
              <>
                Enter to send · session{' '}
                <span className="font-mono">{activeSessionId.slice(0, 8)}</span>
              </>
            ) : (
              'New session — your first message starts a saved chat'
            )}
          </p>
          </div>
        </form>
      </div>
    </div>
  )
}
