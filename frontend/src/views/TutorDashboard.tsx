import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useSession } from '../state/session'
import { FALLBACK_REASON_LABELS, type TutorFallbackQuestion } from '../types'

/**
 * The "Fallback Queries" view.
 *
 * Every student question that missed the module's own material and fell through
 * to the web fallback, for the tutor's assigned modules. It is deliberately a
 * record rather than a worklist for now: the point is to surface what the notes
 * could not answer, not to capture an answer here.
 */
export default function TutorDashboard() {
  const { user } = useSession()
  // A tutor or lecturer is sandboxed to the module they teach; the API applies
  // its own scope too, but naming it here keeps the view strictly on it.
  const moduleId = user?.modules?.[0]
  const [questions, setQuestions] = useState<TutorFallbackQuestion[]>([])
  const [scope, setScope] = useState<{ role: string; modules: string[] } | null>(null)
  const [status, setStatus] = useState('open')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const body = await api.getTutorQuestions({ status, moduleId })
      setQuestions(body.questions)
      setScope({ role: body.scope.role, modules: body.scope.modules })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [status, moduleId])

  useEffect(() => {
    void load()
  }, [load])

  const scopeLabel = scope
    ? `${scope.role}: ${scope.modules.join(', ') || 'all modules'}`
    : null

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-4 py-6">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Fallback Queries</h1>
          <p className="text-sm text-slate-500">
            Questions the tutor could not ground in your module's notes, so it fell back to
            external web content. Use these to decide what the material is missing.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {scopeLabel && (
            <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-[11px] text-slate-600">
              {scopeLabel}
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
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            title="Refresh"
            className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}

      {loading && questions.length === 0 && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading fallback queries…
        </div>
      )}

      {!loading && questions.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">
          Nothing {status === 'open' ? 'waiting' : `marked ${status}`} for your modules. Every
          question was answered from the module's own notes.
        </p>
      )}

      {questions.length > 0 && (
        <ul className="space-y-1.5">
          {questions.map((q) => (
            <li
              key={q.question_id}
              className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"
            >
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-800">{q.question_text}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono">{q.module_id}</span>
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 font-medium text-amber-700">
                      {FALLBACK_REASON_LABELS[q.reason] ?? q.reason}
                    </span>
                    {q.best_distance != null && (
                      <span className="font-mono">d={q.best_distance.toFixed(3)}</span>
                    )}
                    <span>{q.occurrences}× asked</span>
                    <span className="text-slate-400">·</span>
                    <span>{when(q.created_at)}</span>
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function when(iso: string | null): string {
  if (!iso) return 'unknown time'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'unknown time'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
