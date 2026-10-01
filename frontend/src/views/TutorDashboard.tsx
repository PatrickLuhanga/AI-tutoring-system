import { AlertTriangle, Check, ChevronDown, Loader2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useSession } from '../state/session'
import type { AccountRole } from '../types'

/** A row from GET /api/tutor/questions. */
interface QueuedQuestion {
  question_id: number
  module_id: string
  question_text: string
  intent: string | null
  reason: string
  best_distance: number | null
  occurrences: number
  status: string
  answer_text: string | null
  created_at: string
}

const REASON_COPY: Record<string, string> = {
  no_context: 'nothing in the module matched',
  below_threshold: 'only weak matches',
  third_party_only: 'answered from a textbook, not the notes',
  low_confidence: 'grounding looked weak',
  student_flagged: 'the student said it was wrong',
}

/**
 * The ungrounded-question queue, scoped to the signed-in tutor/lecturer's
 * modules by the API (never here).
 *
 * Answering can `promote` the reply into the corpus, so the next student asking
 * something similar is grounded in it rather than falling back to a textbook.
 */
export default function TutorDashboard() {
  const { user } = useSession()
  const [questions, setQuestions] = useState<QueuedQuestion[]>([])
  const [scope, setScope] = useState<{ role: string; modules: string[] } | null>(null)
  const [status, setStatus] = useState('open')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [answer, setAnswer] = useState('')
  const [promote, setPromote] = useState(true)
  const [busy, setBusy] = useState(false)

  const token = readToken()

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/tutor/questions?status=${status}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      setQuestions(body.questions ?? [])
      setScope(body.scope ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [status, token])

  useEffect(() => {
    void load()
  }, [load])

  async function act(questionId: number, kind: 'answer' | 'dismiss') {
    setBusy(true)
    try {
      const res = await fetch(`/api/tutor/questions/${questionId}/${kind}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: kind === 'answer' ? JSON.stringify({ answer_text: answer, promote }) : undefined,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? `${res.status}`)
      }
      setAnswer('')
      setOpen(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const roleLabel: Record<AccountRole, string> = {
    student: 'Student',
    tutor: 'Tutor',
    lecturer: 'Lecturer',
    admin: 'System Admin',
  }

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-4 py-6">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Help Queue</h1>
          <p className="text-sm text-slate-500">
            Questions the tutor could not ground in your module's material, and answers that
            looked weak. Answering can publish the reply into the corpus.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {scope && (
            <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-[11px] text-slate-600">
              {roleLabel[scope.role as AccountRole] ?? scope.role}: {scope.modules.join(', ') || 'all'}
            </span>
          )}
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700"
          >
            <option value="open">Open</option>
            <option value="answered">Answered</option>
            <option value="dismissed">Dismissed</option>
          </select>
        </div>
      </header>

      {user?.role === 'lecturer' && (
        <p className="mb-4 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800 ring-1 ring-blue-200">
          As a lecturer you see the same queue as a tutor, for the modules you teach.
        </p>
      )}

      {error && (
        <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading the queue…
        </div>
      )}

      {!loading && questions.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">
          Nothing {status === 'open' ? 'waiting' : `marked ${status}`} for your modules.
        </p>
      )}

      <ul className="space-y-2">
        {questions.map((q) => (
          <li key={q.question_id} className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <button
              type="button"
              onClick={() => setOpen(open === q.question_id ? null : q.question_id)}
              className="flex w-full items-start gap-3 px-4 py-3 text-left"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-slate-800">{q.question_text}</span>
                <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono">{q.module_id}</span>
                  <span>{REASON_COPY[q.reason] ?? q.reason}</span>
                  {q.best_distance != null && (
                    <span className="font-mono">d={q.best_distance.toFixed(3)}</span>
                  )}
                  {q.occurrences > 1 && <span>asked {q.occurrences}×</span>}
                </span>
              </span>
              <ChevronDown
                className={`mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition ${
                  open === q.question_id ? 'rotate-180' : ''
                }`}
              />
            </button>

            {open === q.question_id && (
              <div className="border-t border-slate-100 px-4 py-3">
                {q.answer_text ? (
                  <p className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                    {q.answer_text}
                  </p>
                ) : (
                  <>
                    <textarea
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      rows={4}
                      placeholder="Write the answer a student should have received…"
                      className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    />
                    <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                      <input
                        type="checkbox"
                        checked={promote}
                        onChange={(e) => setPromote(e.target.checked)}
                        className="h-3.5 w-3.5 rounded border-slate-300"
                      />
                      Publish into the corpus so later students are grounded in this answer
                    </label>
                  </>
                )}

                <div className="mt-3 flex gap-2">
                  {!q.answer_text && (
                    <button
                      type="button"
                      disabled={busy || !answer.trim()}
                      onClick={() => void act(q.question_id, 'answer')}
                      className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-on-accent transition hover:bg-blue-700 disabled:opacity-50"
                    >
                      <Check className="h-3.5 w-3.5" />
                      Save answer
                    </button>
                  )}
                  {q.status === 'open' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void act(q.question_id, 'dismiss')}
                      className="flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                    >
                      <X className="h-3.5 w-3.5" />
                      Dismiss
                    </button>
                  )}
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function readToken(): string | null {
  try {
    return localStorage.getItem('ai_tutor.token')
  } catch {
    return null
  }
}
