import {
  Bot,
  CornerDownLeft,
  Loader2,
  PanelLeft,
  Plus,
  Send,
  Trash2,
  User,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../auth/context'
import AuditPanel from '../../components/AuditPanel'
import FeedbackControls from '../../components/FeedbackControls'
import MarkdownMessage from '../../components/MarkdownMessage'
import type { ChatMessage, ChatSessionSummary, FeedbackState, Module } from '../../types'

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

/**
 * Student workspace: an LLM-style persistent history sidebar plus the Socratic
 * chat. History is loaded from the gateway (no mock transcript); sessions can
 * be created, reopened and deleted.
 */
export default function StudentChat() {
  const { user } = useAuth()

  const preferredModules = useMemo(
    () => Array.from(new Set([...(user?.enrolled_modules ?? []), ...(user?.modules ?? [])])),
    [user?.enrolled_modules, user?.modules],
  )

  const [moduleOptions, setModuleOptions] = useState<Module[]>([])
  const [moduleId, setModuleId] = useState('')
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loadingSession, setLoadingSession] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)

  const selectOptions = useMemo(() => {
    const options = [...moduleOptions]
    const seen = new Set(options.map((o) => o.module_id))
    for (const id of preferredModules) {
      if (!seen.has(id)) {
        options.push({ module_id: id, module_code: id, module_name: id, course_code: '', language: '' })
        seen.add(id)
      }
    }
    return options
  }, [moduleOptions, preferredModules])

  const activeModuleId = moduleId || selectOptions[0]?.module_id || ''
  const activeModule = useMemo(
    () => selectOptions.find((m) => m.module_id === activeModuleId),
    [selectOptions, activeModuleId],
  )

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions())
    } catch {
      setSessions([])
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

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const list = await api.listModules()
        if (!cancelled) setModuleOptions(list)
      } catch {
        /* the module dropdown falls back to the user's own modules */
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, sending])

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
    return selectOptions.find((m) => m.module_id === moduleKey)?.module_name ?? moduleKey
  }

  function startNewChat() {
    setActiveSessionId(null)
    setMessages([])
    setInput('')
    setError(null)
    setSidebarOpen(false)
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
    if (!text || sending || !activeModuleId) return

    const sessionId = activeSessionId ?? newSessionId()
    if (!activeSessionId) setActiveSessionId(sessionId)

    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    const userTurn: ChatMessage = {
      message_id: `local-${newSessionId()}`,
      role: 'user',
      content: text,
    }
    setMessages((prev) => [...prev, userTurn])
    setInput('')
    setSending(true)
    setError(null)

    try {
      const response = await api.chat({
        message: text,
        module_id: activeModuleId,
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
      void refreshSessions()
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
  }

  function updateFeedback(messageId: string, next: FeedbackState | undefined) {
    setMessages((prev) =>
      prev.map((m) => (m.message_id === messageId ? { ...m, feedback: next } : m)),
    )
  }

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      {/* --------------------------------------------------------- Sidebar */}
      <aside
        className={`${
          sidebarOpen ? 'flex absolute inset-y-0 left-0 z-20 w-72 shadow-xl' : 'hidden'
        } shrink-0 flex-col border-r border-slate-200 bg-white md:static md:z-auto md:flex md:shadow-none`}
      >
        <div className="border-b border-slate-100 p-3">
          <button
            type="button"
            onClick={startNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            New chat
          </button>
        </div>
        <div className="scrollbar-thin flex-1 overflow-y-auto px-2 py-3">
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

      {/* ------------------------------------------------------------ Chat */}
      <div className="flex min-h-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSidebarOpen((v) => !v)}
              className="rounded-lg border border-slate-300 bg-white p-1.5 text-slate-500 md:hidden"
              aria-label="Toggle history"
            >
              <PanelLeft className="h-4 w-4" />
            </button>
            <div>
              <h1 className="text-base font-semibold text-slate-900">Socratic Tutor</h1>
              <p className="text-xs text-slate-500">
                {user?.name ? `${user.name} · ` : ''}hints, not answers
              </p>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <span className="sr-only">Module</span>
            <select
              value={activeModuleId}
              onChange={(e) => changeModule(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
            >
              {selectOptions.map((module) => (
                <option key={module.module_id} value={module.module_id}>
                  {module.module_id} · {module.module_name}
                </option>
              ))}
            </select>
          </label>
        </header>

        {error && (
          <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        <div
          ref={scrollRef}
          className="scrollbar-thin flex-1 space-y-4 overflow-y-auto bg-white p-4 sm:p-6"
        >
          {messages.length === 0 && !loadingSession && (
            <div className="flex h-full flex-col items-center justify-center text-center text-slate-400">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
                <Bot className="h-6 w-6" />
              </div>
              <p className="mt-3 max-w-sm text-sm">
                Ask a {activeModule?.module_name ?? 'module'} question, or paste a coding error.
                Your chats are saved by module and can be resumed any time.
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
              <div key={message.message_id} className="flex justify-end gap-3">
                <div className="max-w-[80%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-2.5 text-sm text-white shadow-sm">
                  {message.content}
                </div>
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-600">
                  <User className="h-4 w-4" />
                </div>
              </div>
            ) : (
              <div key={message.message_id} className="flex gap-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
                  <Bot className="h-4 w-4" />
                </div>
                <div className="min-w-0 max-w-[85%] rounded-2xl rounded-tl-md border border-slate-200 bg-slate-50/70 px-4 py-3 shadow-sm">
                  <MarkdownMessage content={message.content} />
                  {message.audit && <AuditPanel audit={message.audit} />}
                  {!message.message_id.startsWith('error-') && (
                    <FeedbackControls
                      sessionId={activeSessionId ?? ''}
                      messageId={message.message_id}
                      moduleId={activeModuleId}
                      value={message.feedback}
                      onChange={(next) => updateFeedback(message.message_id, next)}
                    />
                  )}
                </div>
              </div>
            ),
          )}

          {sending && (
            <div className="flex items-center gap-3 text-sm text-slate-400">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
                <Bot className="h-4 w-4" />
              </div>
              <span className="flex items-center gap-2 rounded-2xl rounded-tl-md border border-slate-200 bg-slate-50/70 px-4 py-2.5">
                <Loader2 className="h-4 w-4 animate-spin" />
                Thinking through the next hint…
              </span>
            </div>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
          className="border-t border-slate-200 bg-slate-50 p-4"
        >
          <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              rows={1}
              placeholder={`Ask a ${activeModule?.module_name ?? 'module'} question, or paste your error…`}
              className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none"
            />
            <button
              type="submit"
              disabled={!input.trim() || sending}
              className="flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
              Send
              <span className="hidden items-center gap-0.5 rounded bg-white/15 px-1.5 py-0.5 text-[10px] font-semibold sm:inline-flex">
                <CornerDownLeft className="h-2.5 w-2.5" />
              </span>
            </button>
          </div>
          <p className="mt-2 text-center text-xs text-slate-400">
            {activeSessionId ? (
              <>
                Session <span className="font-mono">{activeSessionId.slice(0, 12)}</span> · module{' '}
                <span className="font-mono">{activeModuleId}</span>
              </>
            ) : (
              'New session — your first message starts a saved chat'
            )}
          </p>
        </form>
      </div>
    </div>
  )
}
