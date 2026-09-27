import { Bot, CornerDownLeft, Loader2, Send, User } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, USE_MOCK } from '../api/client'
import { INITIAL_MESSAGES, MODULES } from '../api/mockData'
import AuditPanel from '../components/AuditPanel'
import FeedbackControls from '../components/FeedbackControls'
import MarkdownMessage from '../components/MarkdownMessage'
import Sources from '../components/Sources'
import type { ChatMessage, FeedbackState, Module } from '../types'

const newSessionId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2)

export default function StudentChat() {
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES)
  // The dropdown renders whatever the module registry actually contains, so a
  // module added in config.py appears here without a frontend change. MODULES is
  // only the offline fallback.
  const [modules, setModules] = useState<Module[]>(MODULES)
  const [moduleId, setModuleId] = useState(() => MODULES[1].module_id)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId] = useState(newSessionId)

  const scrollRef = useRef<HTMLDivElement>(null)
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

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, sending])

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
    }
  }

  function updateFeedback(messageId: string, next: FeedbackState | undefined) {
    setMessages((prev) =>
      prev.map((m) => (m.message_id === messageId ? { ...m, feedback: next } : m)),
    )
  }

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col px-4 py-6">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Socratic Tutor</h1>
          <p className="text-sm text-slate-500">
            Ask about theory or paste a coding error — hints, not answers.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {USE_MOCK && (
            <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
              Mock data
            </span>
          )}
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <span className="sr-only">Module</span>
            <select
              value={moduleId}
              onChange={(e) => setModuleId(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
            >
              {modules.map((module) => (
                <option key={module.module_id} value={module.module_id}>
                  {module.module_id} · {module.module_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <div
        ref={scrollRef}
        className="scrollbar-thin flex-1 space-y-4 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      >
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
        className="mt-4 flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100"
      >
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
          placeholder={`Ask a ${activeModule.module_name} question, or paste your error…`}
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
      </form>
      <p className="mt-2 text-center text-xs text-slate-400">
        Session <span className="font-mono">{sessionId.slice(0, 12)}</span> · module scope:{' '}
        <span className="font-mono">{moduleId}</span>
      </p>
    </div>
  )
}
