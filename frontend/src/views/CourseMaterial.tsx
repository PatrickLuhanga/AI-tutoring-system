import { BookOpen, ExternalLink, Loader2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, type ResourceDoc } from '../api/client'
import type { Module } from '../types'

/** Folder order for reading, and a readable heading for each. */
const GROUP_ORDER = ['slides', 'lecture_notes', 'notes', 'examples', 'exercises', 'books']
const GROUP_LABEL: Record<string, string> = {
  slides: 'Lecture slides',
  lecture_notes: 'Lecture notes',
  notes: 'Notes',
  examples: 'Worked examples',
  exercises: 'Exercises',
  books: 'Textbook (third-party)',
}

/**
 * The course material library.
 *
 * These are plain links into the rendered corpus rather than an in-app reader,
 * because that is exactly what the tutor's inline citations point at - following
 * a source from an answer should land on the same page it always has.
 */
export default function CourseMaterial() {
  const [modules, setModules] = useState<Module[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [docs, setDocs] = useState<ResourceDoc[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void api
      .getModules()
      .then((live) => {
        setModules(live)
        if (live.length) setActive(live[0].module_id)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!active) return
    setDocs([])
    void api
      .getResources(active)
      .then(setDocs)
      .catch(() => setDocs([]))
  }, [active])

  // Grouped for reading order. An unrecognised folder is kept at the end rather
  // than dropped, so an unfamiliar upload still shows up.
  const grouped = useMemo(() => {
    const buckets = new Map<string, ResourceDoc[]>()
    for (const doc of docs) {
      const key = doc.group || doc.source_category || 'notes'
      const list = buckets.get(key)
      if (list) list.push(doc)
      else buckets.set(key, [doc])
    }
    return [...buckets.entries()].sort(([a], [b]) => {
      const ia = GROUP_ORDER.indexOf(a)
      const ib = GROUP_ORDER.indexOf(b)
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
    })
  }, [docs])

  const current = modules.find((m) => m.module_id === active)

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Course Material</h1>
        <p className="text-sm text-slate-500">
          Every module's ingested notes, slides and exercises. The tutor cites these, and a
          citation link opens the same page.
        </p>
      </header>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading modules…
        </div>
      )}

      {error && (
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}

      {!loading && modules.length > 0 && (
        <>
          <div className="mb-4 flex flex-wrap gap-1.5">
            {modules.map((m) => (
              <button
                key={m.module_id}
                type="button"
                onClick={() => setActive(m.module_id)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                  active === m.module_id
                    ? 'border-blue-400 bg-blue-50 text-blue-800'
                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                }`}
              >
                {m.module_id}
              </button>
            ))}
          </div>

          <h2 className="mb-2 text-sm font-medium text-slate-700">
            {current ? `${current.module_id} · ${current.module_name}` : ''}
          </h2>

          {docs.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-500">
              No material has been ingested for this module yet.
            </p>
          ) : (
            <div className="space-y-5">
              {grouped.map(([group, items]) => (
                <section key={group}>
                  <h3 className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {GROUP_LABEL[group] ?? group.replace(/_/g, ' ')}
                    <span className="rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                      {items.length}
                    </span>
                  </h3>
                  <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                    {items.map((doc) => (
                      <li key={doc.path}>
                        <a
                          href={`/resources/${active}/${doc.path}`}
                          target="_blank"
                          rel="noreferrer"
                          className="group flex items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                        >
                          <BookOpen className="h-4 w-4 shrink-0 text-slate-400 group-hover:text-blue-500" />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-slate-800">
                              {doc.title || doc.path}
                            </span>
                            <span className="block truncate font-mono text-[11px] text-slate-400">
                              {doc.path}
                            </span>
                          </span>
                          <span className="shrink-0 text-[11px] text-slate-400">
                            {Math.max(1, Math.round(doc.size_bytes / 1024))} KB
                          </span>
                          <ExternalLink className="h-3.5 w-3.5 shrink-0 text-slate-300 group-hover:text-blue-500" />
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
