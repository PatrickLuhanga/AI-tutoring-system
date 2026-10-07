import { BookOpen, CheckCircle2, Circle, Info, Loader2, Send, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useSession } from '../state/session'
import type { Module } from '../types'

interface DrawnQuestion {
  question_id: number
  prompt: string
  difficulty: string
}

interface MarkedQuestion {
  question_id: number
  correct: boolean
  markable: boolean
  /** How this question was handled: auto-marked, reference-only, or no answer. */
  mode: 'auto' | 'reference' | 'none'
  answer_notes: string | null
}

const DIFFICULTY_TONE: Record<string, string> = {
  easy: 'bg-emerald-100 text-emerald-700',
  medium: 'bg-blue-100 text-blue-700',
  hard: 'bg-amber-100 text-amber-700',
}

/**
 * A randomised practice test drawn from the module's question bank.
 *
 * Answers are never sent to the browser until the attempt is submitted, so
 * marking notes cannot be read out of the devtools network tab.
 */
export default function Practice() {
  const { user } = useSession()
  const [modules, setModules] = useState<Module[]>([])
  const [moduleId, setModuleId] = useState<string>('')
  const [count, setCount] = useState(5)
  const [drawn, setDrawn] = useState<DrawnQuestion[] | null>(null)
  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [result, setResult] = useState<{
    score: number
    max_score: number
    unmarked: number
    /** Questions carrying an AI-written reference answer, shown but never marked. */
    reference_only: number
    /** Questions with no answer at all. */
    no_answer: number
    breakdown: MarkedQuestion[]
  } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bankSize = useRef<number | null>(null)

  const isStaff = Boolean(user?.is_staff)
  const isScoped = user?.role === 'tutor' || user?.role === 'lecturer'
  const assigned = user?.modules ?? []
  const assignedKey = assigned.join(',')

  useEffect(() => {
    void api
      .getModules()
      .then((live) => {
        const usable =
          isScoped && assigned.length
            ? live.filter((m) => assigned.includes(m.module_id))
            : live
        setModules(usable)
        if (usable.length) setModuleId(usable[0].module_id)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isScoped, assignedKey])

  async function draw() {
    setLoading(true)
    setError(null)
    setResult(null)
    setAnswers({})
    try {
      const res = await api.drawPractice(moduleId, count)
      setDrawn(res.questions)
      bankSize.current = res.available
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setDrawn(null)
    } finally {
      setLoading(false)
    }
  }

  async function submit() {
    if (!drawn) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.submitPractice({
        module_id: moduleId,
        question_ids: drawn.map((q) => q.question_id),
        answers: Object.fromEntries(
          drawn.map((q) => [String(q.question_id), answers[q.question_id] ?? '']),
        ),
      })
      setResult(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const answered = drawn ? drawn.filter((q) => (answers[q.question_id] ?? '').trim()).length : 0
  const allAnswered = Boolean(drawn?.length) && answered === drawn?.length

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Practice Tests</h1>
        <p className="text-sm text-slate-500">
          Randomised questions from your module's question bank. Answers are held back until you
          submit, and the marking notes come from your lecturer.
        </p>
      </header>

      {error && (
        <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
        <label className="text-xs font-medium text-slate-600">
          Module
          {isScoped ? (
            <span className="mt-1 block rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm font-medium text-slate-600">
              {modules[0] ? `${modules[0].module_id} · ${modules[0].module_name}` : '—'}
            </span>
          ) : (
            <select
              value={moduleId}
              onChange={(e) => setModuleId(e.target.value)}
              className="mt-1 block rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm font-medium text-slate-700"
            >
              {modules.map((m) => (
                <option key={m.module_id} value={m.module_id}>
                  {m.module_id} · {m.module_name}
                </option>
              ))}
            </select>
          )}
        </label>
        <label className="text-xs font-medium text-slate-600">
          Questions
          <input
            type="number"
            min={1}
            max={25}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(25, Number(e.target.value) || 1)))}
            className="mt-1 block w-20 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="button"
          onClick={() => void draw()}
          disabled={loading || !moduleId}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-on-accent transition hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {drawn ? 'New set' : 'Draw questions'}
        </button>
        {bankSize.current != null && (
          <span className="text-[11px] text-slate-400">
            {bankSize.current} in the bank
          </span>
        )}
      </div>

      {!drawn && !loading && (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">
          {isStaff
            ? 'Draw a test, or add questions to the bank first — a lecturer can do that from this module.'
            : 'No test drawn yet. Pick a module and press "Draw questions".'}
        </p>
      )}

      {drawn && drawn.length > 0 && (
        <>
          <ol className="space-y-3">
            {drawn.map((q, i) => {
              const marked = result?.breakdown.find((b) => b.question_id === q.question_id)
              return (
                <li
                  key={q.question_id}
                  className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                >
                  <div className="mb-2 flex items-start gap-2">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">
                      {i + 1}
                    </span>
                    <p className="flex-1 text-sm text-slate-800">{q.prompt}</p>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                        DIFFICULTY_TONE[q.difficulty] ?? DIFFICULTY_TONE.medium
                      }`}
                    >
                      {q.difficulty}
                    </span>
                  </div>

                  {!result && (
                    <textarea
                      value={answers[q.question_id] ?? ''}
                      onChange={(e) =>
                        setAnswers((prev) => ({ ...prev, [q.question_id]: e.target.value }))
                      }
                      rows={2}
                      placeholder="Your answer…"
                      className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    />
                  )}

                  {marked && (
                    <div
                      className={`mt-2 rounded-lg px-3 py-2 text-xs ${
                        marked.mode === 'auto'
                          ? marked.correct
                            ? 'bg-emerald-50 text-emerald-800'
                            : 'bg-rose-50 text-rose-800'
                          : 'bg-amber-50 text-amber-900'
                      }`}
                    >
                      <div className="flex items-start gap-2">
                        {marked.mode === 'auto' && marked.correct ? (
                          <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0" />
                        ) : marked.mode === 'auto' ? (
                          <Circle className="mt-px h-3.5 w-3.5 shrink-0" />
                        ) : (
                          <Info className="mt-px h-3.5 w-3.5 shrink-0" />
                        )}
                        <span className="font-medium">
                          {marked.mode === 'auto'
                            ? marked.correct
                              ? 'Correct.'
                              : 'Marked wrong.'
                            : marked.mode === 'reference'
                              ? 'Reference answer - not marked. This one was written by AI, so judge your own answer against it.'
                              : 'No reference answer. Your lecturer has not written one for this question.'}
                        </span>
                      </div>
                      {marked.answer_notes && (
                        <p className="mt-1.5 whitespace-pre-wrap border-l-2 border-current/25 pl-2 opacity-90">
                          {marked.answer_notes}
                        </p>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ol>

          <div className="sticky bottom-0 mt-4 flex items-center gap-3 rounded-xl border border-slate-200 bg-white/95 p-3 shadow-sm backdrop-blur">
            {!result ? (
              <>
                <span className="text-xs text-slate-500">
                  {answered} of {drawn.length} answered
                </span>
                <button
                  type="button"
                  onClick={() => void submit()}
                  disabled={loading || !allAnswered}
                  className="ml-auto flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-on-accent transition hover:bg-blue-700 disabled:opacity-50"
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Submit
                </button>
              </>
            ) : (
              <>
                <span className="text-sm font-medium text-slate-800">
                  {result.max_score > 0 ? (
                    `Score: ${result.score} / ${result.max_score}`
                  ) : (
                    'Not scored'
                  )}
                </span>
                {result.reference_only > 0 && (
                  <span className="text-xs text-amber-700">
                    {result.reference_only} AI reference
                    {result.reference_only === 1 ? '' : 's'} - compare your own answers
                  </span>
                )}
                {result.no_answer > 0 && (
                  <span className="text-xs text-slate-400">
                    {result.no_answer} with no reference answer
                  </span>
                )}
                {result.max_score === 0 && result.unmarked > 0 && (
                  <span className="text-xs text-slate-400">
                    (nothing could be marked yet)
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => void draw()}
                  className="ml-auto flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                >
                  <BookOpen className="h-3.5 w-3.5" />
                  Another set
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
