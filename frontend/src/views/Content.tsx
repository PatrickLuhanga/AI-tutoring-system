import { Bell, Loader2, Plus, Sparkles, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useSession } from '../state/session'
import type { Module } from '../types'

interface BankQuestion {
  question_id: number
  prompt: string
  difficulty: string
  origin: string
  /** Who wrote the answer: 'authored' | 'generated' | null. Drives grading. */
  answer_source: 'authored' | 'generated' | null
  answer_notes: string | null
  source_label: string | null
}

interface UploadedDoc {
  document_id: number
  module_id: string
  category: string
  original_name: string
  stored_path: string
  size_bytes: number
  ingest_status: string
}

/**
 * The lecturer's authoring console: the practice bank, staff-triggered question
 * generation, uploads, and announcements.
 *
 * Scoped to the modules the signed-in account teaches. The API enforces that, so
 * this only decides what to *offer*, never what is permitted.
 */
export default function Content() {
  const { user } = useSession()
  const [modules, setModules] = useState<Module[]>([])
  const [moduleId, setModuleId] = useState('')
  const [tab, setTab] = useState<'bank' | 'generate' | 'upload' | 'announce'>('bank')
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  const scoped = user?.role === 'admin' ? undefined : user?.modules
  useEffect(() => {
    void api
      .getModules()
      .then((live) => {
        const usable = scoped?.length ? live.filter((m) => scoped.includes(m.module_id)) : live
        setModules(usable)
        if (usable.length) setModuleId(usable[0].module_id)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [scoped])

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Authoring</h1>
        <p className="text-sm text-slate-500">
          Build the practice bank, add course material, and announce changes to your students.
          {scoped?.length ? ` Scoped to ${scoped.join(', ')}.` : ' You can see every module.'}
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
          {(
            [
              ['bank', 'Question bank'],
              ['generate', 'Generate'],
              ['upload', 'Uploads'],
              ['announce', 'Announce'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                tab === id ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          value={moduleId}
          onChange={(e) => setModuleId(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm font-medium text-slate-700"
        >
          {modules.map((m) => (
            <option key={m.module_id} value={m.module_id}>
              {m.module_id} · {m.module_name}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}
      {ok && (
        <p className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700 ring-1 ring-emerald-200">
          {ok}
        </p>
      )}

      {tab === 'bank' && <BankTab moduleId={moduleId} onError={setError} onOk={setOk} />}
      {tab === 'generate' && <GenerateTab moduleId={moduleId} onError={setError} onOk={setOk} />}
      {tab === 'upload' && <UploadTab moduleId={moduleId} modules={modules} onError={setError} onOk={setOk} />}
      {tab === 'announce' && <AnnounceTab moduleId={moduleId} modules={modules} onError={setError} onOk={setOk} />}
    </div>
  )
}

const token = () => {
  try {
    return localStorage.getItem('ai_tutor.token')
  } catch {
    return null
  }
}

function BankTab({ moduleId, onError, onOk }: { moduleId: string; onError: (s: string) => void; onOk: (s: string) => void }) {
  const [rows, setRows] = useState<BankQuestion[]>([])
  const [loading, setLoading] = useState(true)
  const [prompt, setPrompt] = useState('')
  const [answer, setAnswer] = useState('')
  const [difficulty, setDifficulty] = useState('medium')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!moduleId) return
    setLoading(true)
    try {
      const res = await fetch(`/api/practice/bank?module_id=${moduleId}`, {
        headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      setRows(body.questions ?? [])
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [moduleId, onError])

  useEffect(() => {
    void load()
  }, [load])

  async function add() {
    setBusy(true)
    onError('')
    onOk('')
    try {
      const res = await fetch('/api/practice/bank', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        },
        body: JSON.stringify({ module_id: moduleId, prompt, answer_notes: answer, difficulty }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.error ?? `${res.status}`)
      setPrompt('')
      setAnswer('')
      onOk('Question added to the bank.')
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function retire(id: number) {
    try {
      await fetch(`/api/practice/bank/${id}`, {
        method: 'DELETE',
        headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      })
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    }
  }

  /**
   * Approve a drafted answer, making it a marking key.
   *
   * A `generated` answer is shown to students as a reference but never scored,
   * because the model wrote it and exact-match marking against a wrong draft
   * would penalise a student who answered correctly. So a seeded bank scores
   * nothing until a lecturer reads a question, agrees the answer is right, and
   * says so here. Editing the text alone deliberately does *not* do this.
   */
  async function approve(row: BankQuestion) {
    setBusy(true)
    onError('')
    onOk('')
    try {
      const res = await fetch(`/api/practice/bank/${row.question_id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        },
        body: JSON.stringify({ answer_source: 'authored' }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.error ?? `${res.status}`)
      onOk('Approved. This answer is now marked automatically.')
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <label className="text-xs font-medium text-slate-600">
          Question
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            placeholder="Explain why a Singleton class keeps its constructor private."
            className="mt-1 w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="flex-1 text-xs font-medium text-slate-600">
            Expected answer (used for marking; never shown to students)
            <input
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
          </label>
          <select
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
          >
            {['easy', 'medium', 'hard'].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void add()}
            disabled={busy || prompt.trim().length < 8}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Add
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-slate-400">Loading the bank…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-500">
          No questions in {moduleId} yet. Add one above, or generate some from your notes.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((q) => (
            <li
              key={q.question_id}
              className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"
            >
              <span className="flex-1 text-sm text-slate-800">
                {q.prompt}
                {q.answer_notes && q.answer_source === 'generated' && (
                  <span className="mt-1 block rounded bg-slate-50 px-2 py-1 text-xs text-slate-600">
                    <span className="font-medium text-slate-500">Draft answer:</span>{' '}
                    {q.answer_notes}
                  </span>
                )}
              </span>
              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500">
                {q.origin === 'generated' ? 'AI' : q.difficulty}
              </span>
              {q.answer_source === 'generated' ? (
                <button
                  type="button"
                  onClick={() => void approve(q)}
                  disabled={busy}
                  title="Check the answer is right, then approve it for automatic marking"
                  className="shrink-0 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-800 transition hover:bg-amber-100 disabled:opacity-50"
                >
                  Approve answer
                </button>
              ) : q.answer_source === 'authored' ? (
                <span
                  title="Marked automatically against this answer"
                  className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-700"
                >
                  Marked
                </span>
              ) : (
                <span
                  title="No answer recorded, so this question is left out of the score"
                  className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500"
                >
                  No answer
                </span>
              )}
              <button
                type="button"
                onClick={() => void retire(q.question_id)}
                title="Retire this question"
                className="shrink-0 rounded-md border border-slate-300 px-1.5 py-1 text-slate-500 transition hover:bg-slate-50"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {rows.length > 0 && (
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
          A drafted answer is shown to students as a reference but is{' '}
          <strong>never scored</strong> — marking is exact match, so a draft that is
          subtly wrong would mark a correct student answer wrong. Approving an
          answer is what makes a question count towards the score.
        </p>
      )}
    </div>
  )
}

function GenerateTab({ moduleId, onError, onOk }: { moduleId: string; onError: (s: string) => void; onOk: (s: string) => void }) {
  const [source, setSource] = useState<'corpus' | 'paste'>('corpus')
  const [text, setText] = useState('')
  const [count, setCount] = useState(3)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    onError('')
    onOk('')
    try {
      const url = source === 'corpus' ? '/api/practice/generate-from-corpus' : '/api/practice/generate'
      const body =
        source === 'corpus'
          ? { module_id: moduleId, count }
          : { module_id: moduleId, text, count }
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        },
        body: JSON.stringify(body),
      })
      const payload = await res.json()
      if (!res.ok) throw new Error(payload.error ?? `${res.status}`)
      onOk(
        payload.count === 0
          ? 'The model returned nothing usable this time. Try again, or add a question by hand.'
          : `${payload.count} question(s) generated and added to the bank, marked as AI-made. Review them before students see them.`,
      )
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const ready = source === 'corpus' || text.trim().length >= 120

  return (
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
        {(
          [
            ['corpus', 'From the module notes'],
            ['paste', 'From text I paste'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setSource(id)}
            className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition ${
              source === id ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <p className="text-xs leading-relaxed text-slate-500">
        {source === 'corpus'
          ? `Draws on the lecture material already ingested for ${moduleId}. Commercial textbooks are excluded, so questions come from your own notes.`
          : 'Paste a section of material and the model will draft questions from exactly that.'}{' '}
        This runs on the local model, which generates only a few tokens a second on this
        machine, so expect it to take a while. Every question lands in the bank flagged as
        AI-made, for you to review before a student can be issued it.
      </p>

      {source === 'paste' && (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={10}
          placeholder="Paste lecture notes, a slide deck transcript, or a textbook section…"
          className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
        />
      )}

      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-slate-600">
          How many
          <input
            type="number"
            min={1}
            max={10}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
            className="ml-2 w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || !ready}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {source === 'corpus' ? 'Generate from notes' : 'Generate questions'}
        </button>
      </div>
      {source === 'paste' && text.trim().length < 120 && (
        <p className="text-[11px] text-slate-400">
          Needs at least a paragraph of material ({120 - text.trim().length} more characters).
        </p>
      )}
    </div>
  )
}

function UploadTab({
  moduleId,
  modules,
  onError,
  onOk,
}: {
  moduleId: string
  modules: Module[]
  onError: (s: string) => void
  onOk: (s: string) => void
}) {
  const [category, setCategory] = useState('notes')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [rows, setRows] = useState<UploadedDoc[]>([])

  const load = useCallback(async () => {
    if (!moduleId) return
    try {
      const res = await fetch(`/api/content/uploads?module_id=${moduleId}`, {
        headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      })
      if (res.ok) setRows((await res.json()).uploads ?? [])
    } catch {
      /* list failure is not worth blocking the form for */
    }
  }, [moduleId])

  useEffect(() => {
    void load()
  }, [load])

  async function send() {
    if (!file) return
    setBusy(true)
    onError('')
    onOk('')
    const form = new FormData()
    form.append('module_id', moduleId)
    form.append('category', category)
    form.append('file', file)
    try {
      const res = await fetch('/api/content/upload', {
        method: 'POST',
        headers: token() ? { Authorization: `Bearer ${token()}` } : {},
        body: form,
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.error ?? `${res.status}`)
      onOk(
        category === 'notes'
          ? 'Uploaded. Run `python -m src.ingest_curriculum --module ' +
              moduleId +
              '` to make it retrievable by the tutor.'
          : 'Uploaded.',
      )
      setFile(null)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs font-medium text-slate-600">
            What is it?
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1 block rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
            >
              <option value="notes">Lecture notes (become course material)</option>
              <option value="past_paper">Past paper / exam</option>
              <option value="exercises">Exercise sheet</option>
            </select>
          </label>
          <label className="flex-1 text-xs font-medium text-slate-600">
            File
            <input
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-xs text-slate-600 file:mr-2 file:rounded-lg file:border-0 file:bg-slate-100 file:px-2.5 file:py-1.5 file:text-xs file:font-medium file:text-slate-700"
            />
          </label>
          <button
            type="button"
            onClick={() => void send()}
            disabled={busy || !file}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            Upload
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-400">
          PDF, DOCX, PPTX, TXT, MD and CSV up to the configured limit. Files land in{' '}
          <span className="font-mono">{modules.find((m) => m.module_id === moduleId)?.module_id}</span>
          's folder in the content root.
        </p>
      </div>

      {rows.length > 0 && (
        <ul className="space-y-1.5">
          {rows.map((d) => (
            <li
              key={d.document_id}
              className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm"
            >
              <Upload className="h-3.5 w-3.5 shrink-0 text-slate-400" />
              <span className="flex-1 truncate text-slate-700">{d.original_name}</span>
              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-500">
                {d.category.replace('_', ' ')}
              </span>
              <span className="shrink-0 text-[11px] text-slate-400">
                {(d.size_bytes / 1024).toFixed(0)} KB
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AnnounceTab({
  moduleId,
  modules,
  onError,
  onOk,
}: {
  moduleId: string
  modules: Module[]
  onError: (s: string) => void
  onOk: (s: string) => void
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [audience, setAudience] = useState('all')
  const [busy, setBusy] = useState(false)

  async function send() {
    setBusy(true)
    onError('')
    onOk('')
    try {
      const res = await fetch('/api/notifications', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        },
        body: JSON.stringify({ title, body, audience, module_id: moduleId }),
      })
      const payload = await res.json()
      if (!res.ok) throw new Error(payload.error ?? `${res.status}`)
      setTitle('')
      setBody('')
      onOk('Announcement sent.')
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <label className="block text-xs font-medium text-slate-600">
        Send to
        <select
          value={audience}
          onChange={(e) => setAudience(e.target.value)}
          className="mt-1 block rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
        >
          <option value="all">Everyone</option>
          <option value="module">{modules.find((m) => m.module_id === moduleId)?.module_id} only</option>
          <option value="role">Students only</option>
        </select>
      </label>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Title"
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={4}
        placeholder="What do your students need to know?"
        className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
      />
      <button
        type="button"
        onClick={() => void send()}
        disabled={busy || !title.trim() || !body.trim()}
        className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
        Send announcement
      </button>
      <p className="text-[11px] leading-relaxed text-slate-400">
        Module announcements reach the students enrolled in that module and the staff assigned
        to it. Students see unread items behind the bell in the header.
      </p>
    </div>
  )
}
