import { Bot, CornerDownLeft, GraduationCap, Loader2, Send, Square, Trash2, User } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, USE_MOCK } from '../api/client'
import { MODULES } from '../api/mockData'
import AuditPanel from '../components/AuditPanel'
import FeedbackControls from '../components/FeedbackControls'
import MarkdownMessage from '../components/MarkdownMessage'
import Sources from '../components/Sources'
import type { ChatMessage, FeedbackState, Module } from '../types'

const newSessionId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2)

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

export default function StudentChat() {
  // No seeded conversation: the transcript starts empty so a real session is
  // never confused with canned demo turns.
  const [messages, setMessages] = useState<ChatMessage[]>([])
  // The dropdown renders whatever the module registry actually contains, so a
  // module added in config.py appears here without a frontend change. MODULES is
  // only the offline fallback.
  const [modules, setModules] = useState<Module[]>(MODULES)
  const [moduleId, setModuleId] = useState(() => MODULES[1].module_id)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionId] = useState(newSessionId)
  const [atBottom, setAtBottom] = useState(true)

  const scrollRef = useRef<HTMLDivElement>(null)
  const composerRef = useAutoGrow(input)
  const activeModule = useMemo(
    () => modules.find((m) => m.module_id === moduleId) ?? modules[0],
    [modules, moduleId],
  )

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

  const clearThread = useCallback(() => {
    setMessages([])
    setSessionId(newSessionId())
    setAtBottom(true)
    composerRef.current?.focus()
  }, [composerRef])

  async function send() {
    const text = input.trim()
    if (!text || sending) return

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
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          message_id: `error-${newSessionId()}`,
          role: 'assistant',
          content: `The tutoring model is unavailable.\n\n\`\`\`text\n${
            error instanceof Error ? error.message : String(error)
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
    <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col px-4">
      {/* Compact bar: identity, module scope, and thread control on one line. */}
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 py-3">
        <div className="mr-auto flex min-w-0 items-center gap-2">
          <GraduationCap className="h-4 w-4 shrink-0 text-blue-600" />
          <h1 className="truncate text-sm font-semibold text-slate-900">Socratic Tutor</h1>
          {USE_MOCK && (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">
              Mock
            </span>
          )}
        </div>

        <select
          value={moduleId}
          onChange={(e) => setModuleId(e.target.value)}
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
            onClick={clearThread}
            className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
          >
            <Trash2 className="h-3.5 w-3.5" />
            New
          </button>
        )}
      </header>

      {/* Transcript scrolls independently; the composer stays pinned. */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-h-0 flex-1 space-y-5 overflow-y-auto py-5"
      >
        {!hasMessages && (
          <div className="flex h-full flex-col items-center justify-center px-2 text-center">
            <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <Bot className="h-5 w-5" />
            </div>
            <p className="text-sm font-medium text-slate-700">
              Ask about {activeModule.module_name}
            </p>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
              Paste a stack trace or describe what you are stuck on. The tutor answers
              with the next question or hint rather than the finished solution, and
              cites the lecture material it used.
            </p>
          </div>
        )}

        {messages.map((message) =>
          message.role === 'user' ? (
            <div key={message.message_id} className="flex justify-end gap-2.5">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2 text-sm leading-relaxed text-white shadow-sm">
                {message.content}
              </div>
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-600">
                <User className="h-3.5 w-3.5" />
              </div>
            </div>
          ) : (
            <div key={message.message_id} className="flex gap-2.5">
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
                <Bot className="h-3.5 w-3.5" />
              </div>
              <div className="min-w-0 max-w-[90%] flex-1">
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
                <FeedbackControls
                  sessionId={sessionId}
                  messageId={message.message_id}
                  moduleId={moduleId}
                  value={message.feedback}
                  onChange={(next) => updateFeedback(message.message_id, next)}
                />
              </div>
            </div>
          ),
        )}

        {sending && (
          <div className="flex items-center gap-2.5 text-sm text-slate-400">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <Bot className="h-3.5 w-3.5" />
            </div>
            <span className="flex items-center gap-2 rounded-2xl rounded-tl-md border border-slate-200 bg-slate-50/70 px-3.5 py-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Thinking through the next hint…
            </span>
          </div>
        )}
      </div>

      {/* Composer: Enter sends, Shift+Enter breaks the line. */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
        className="sticky bottom-0 shrink-0 border-t border-slate-200 bg-slate-100/80 py-3 backdrop-blur"
      >
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
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
        <p className="mt-1.5 flex items-center justify-center gap-1 text-[11px] text-slate-400">
          <CornerDownLeft className="h-3 w-3" />
          Enter to send · Shift+Enter for a new line · session{' '}
          <span className="font-mono">{sessionId.slice(0, 8)}</span>
        </p>
      </form>
    </div>
  )
}
