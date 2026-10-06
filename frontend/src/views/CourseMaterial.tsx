import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Search,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useHashParams } from '../navigation'
import type { MaterialDocument, Module, ModuleMaterials } from '../types'

/** Reading order and human labels for a document's provenance category. */
const GROUP_ORDER = ['slides', 'lecture_notes', 'notes', 'examples', 'exercises', 'books']
const GROUP_LABEL: Record<string, string> = {
  slides: 'Lecture slides',
  lecture_notes: 'Lecture notes',
  notes: 'Notes',
  examples: 'Worked examples',
  exercises: 'Exercises',
  books: 'Textbook (third-party)',
}

function categoryRank(category: string): number {
  const i = GROUP_ORDER.indexOf(category)
  return i === -1 ? 99 : i
}

/**
 * The ingested knowledge base, read straight from the vector store.
 *
 * This is what the RAG retriever actually searches, so a student or lecturer can
 * audit coverage and wording rather than trusting an empty file listing. Each
 * card is one source document; expanding it reveals the real text chunks with
 * their section headings and token counts.
 */
export default function CourseMaterial() {
  const [modules, setModules] = useState<Module[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [materials, setMaterials] = useState<ModuleMaterials | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMaterials, setLoadingMaterials] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [perDoc, setPerDoc] = useState(5)
  // Deep-link highlight: the document (and optionally section) a citation sent
  // us to. Pulsed briefly so the eye lands on the right card.
  const [highlightDoc, setHighlightDoc] = useState<string | null>(null)

  // Deep-link parameters from `#/material?module=...&doc=...&section=...`.
  const { params } = useHashParams()
  const linkModule = params.get('module')
  const linkDoc = params.get('doc')
  const linkSection = params.get('section')

  const docRefs = useRef<Map<string, HTMLLIElement>>(new Map())
  // Guards the scroll/expand/pulse so it fires once per distinct citation target.
  const handledTarget = useRef<string | null>(null)

  useEffect(() => {
    void api
      .getModules()
      .then((live) => {
        setModules(live)
        // Prefer a deep-linked module; fall back to the first.
        const wanted =
          linkModule && live.some((m) => m.module_id === linkModule)
            ? linkModule
            : live[0]?.module_id
        if (wanted) setActive(wanted)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
    // Run once on mount; the module effect below handles later deep-link changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A deep-link can arrive while the view is already mounted (a second citation
  // click): switch to the requested module.
  useEffect(() => {
    if (!linkModule || !modules.length) return
    if (modules.some((m) => m.module_id === linkModule)) {
      setActive((current) => (current === linkModule ? current : linkModule))
    }
  }, [linkModule, modules])

  const loadMaterials = useCallback(
    async (moduleId: string, limit: number) => {
      setLoadingMaterials(true)
      setError(null)
      try {
        setMaterials(await api.getMaterials(moduleId, { limit }))
      } catch (err) {
        setMaterials(null)
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoadingMaterials(false)
      }
    },
    [],
  )

  useEffect(() => {
    if (!active) return
    setExpanded(new Set())
    void loadMaterials(active, perDoc)
  }, [active, perDoc, loadMaterials])

  // A citation targets one document. Fetch that document's full chunk list (up
  // to the server cap) and merge it in, so a section beyond the default
  // per-document limit is still present when we jump to it.
  useEffect(() => {
    if (!active || !linkDoc) return
    let cancelled = false
    api
      .getMaterials(active, { doc: linkDoc, limit: 200 })
      .then((res) => {
        if (cancelled) return
        const target = res.documents[0]
        if (!target) return
        setMaterials((prev) => {
          if (!prev) return prev
          const others = prev.documents.filter((d) => d.source_file !== linkDoc)
          return { ...prev, documents: [target, ...others] }
        })
      })
      .catch(() => {
        // Deep-link enrichment is best-effort; the module list still renders.
      })
    return () => {
      cancelled = true
    }
  }, [active, linkDoc])

  // Expand, scroll to, and briefly pulse the deep-linked document card.
  useEffect(() => {
    if (!linkDoc) return
    if (!materials?.documents.some((d) => d.source_file === linkDoc)) return
    const targetKey = `${linkModule ?? active}|${linkDoc}|${linkSection ?? ''}`
    if (handledTarget.current === targetKey) return
    handledTarget.current = targetKey

    setExpanded((prev) => new Set(prev).add(linkDoc))
    // Let the expansion render before scrolling to it.
    const timer = window.setTimeout(() => {
      docRefs.current.get(linkDoc)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setHighlightDoc(linkDoc)
      window.setTimeout(() => setHighlightDoc((h) => (h === linkDoc ? null : h)), 2600)
    }, 180)
    return () => window.clearTimeout(timer)
  }, [linkDoc, linkModule, linkSection, active, materials])

  // Group documents by provenance category for a readable, ordered list.
  const grouped = useMemo(() => {
    const buckets = new Map<string, MaterialDocument[]>()
    for (const doc of materials?.documents ?? []) {
      const key = doc.source_category || 'notes'
      const list = buckets.get(key)
      if (list) list.push(doc)
      else buckets.set(key, [doc])
    }
    return [...buckets.entries()].sort(
      ([a], [b]) => categoryRank(a) - categoryRank(b),
    )
  }, [materials])

  function toggleDoc(sourceFile: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(sourceFile)) next.delete(sourceFile)
      else next.add(sourceFile)
      return next
    })
  }

  const current = modules.find((m) => m.module_id === active)

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Course Material</h1>
        <p className="text-sm text-slate-500">
          The ingested knowledge base the tutor retrieves from — real text chunks,
          their sources and topics. Expand a document to audit exactly what the RAG
          system knows.
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
          {/* Module tabs */}
          <div className="mb-3 flex flex-wrap gap-1.5">
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

          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-slate-700">
              {current ? `${current.module_id} · ${current.module_name}` : ''}
            </h2>
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <Search className="h-3.5 w-3.5" />
              <span className="sr-only">Chunks per document</span>
              <select
                value={perDoc}
                onChange={(e) => setPerDoc(Number(e.target.value))}
                className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 shadow-sm focus:border-blue-400 focus:outline-none"
              >
                <option value={3}>3 chunks / doc</option>
                <option value={5}>5 chunks / doc</option>
                <option value={10}>10 chunks / doc</option>
                <option value={25}>25 chunks / doc</option>
              </select>
            </label>
          </div>

          {materials && (
            <div className="mb-3 flex flex-wrap gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-600 shadow-sm">
              <span>
                <strong className="text-slate-900">{materials.total_chunks.toLocaleString()}</strong>{' '}
                chunk(s) indexed
              </span>
              <span>
                <strong className="text-slate-900">{materials.document_count}</strong> document(s)
              </span>
              <span className="text-slate-400">
                showing {materials.returned_chunks.toLocaleString()} chunk(s)
              </span>
            </div>
          )}

          {loadingMaterials ? (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading ingested material…
            </div>
          ) : (materials?.documents.length ?? 0) === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-500">
              No material has been ingested for this module yet.
            </p>
          ) : (
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pb-4">
              {grouped.map(([group, items]) => (
                <section key={group}>
                  <h3 className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {GROUP_LABEL[group] ?? group.replace(/_/g, ' ')}
                    <span className="rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                      {items.length}
                    </span>
                  </h3>
                  <ul className="space-y-2">
                    {items.map((doc) => {
                      const open = expanded.has(doc.source_file)
                      const isTarget = highlightDoc === doc.source_file
                      return (
                        <li
                          key={doc.source_file}
                          ref={(el) => {
                            if (el) docRefs.current.set(doc.source_file, el)
                            else docRefs.current.delete(doc.source_file)
                          }}
                          className={`overflow-hidden rounded-xl border bg-white shadow-sm transition ${
                            isTarget
                              ? 'border-amber-400 ring-2 ring-amber-300 animate-pulse'
                              : 'border-slate-200'
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => toggleDoc(doc.source_file)}
                            className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                          >
                            {open ? (
                              <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                            ) : (
                              <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                            )}
                            <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-slate-800">
                                {doc.source_name}
                              </span>
                              <span className="block truncate font-mono text-[11px] text-slate-400">
                                {doc.source_file}
                              </span>
                            </span>
                            <span className="flex shrink-0 items-center gap-2 text-[11px] text-slate-400">
                              {isTarget && (
                                <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800">
                                  Cited source
                                </span>
                              )}
                              <span className="rounded bg-slate-100 px-1.5 py-0.5 font-semibold uppercase text-slate-500">
                                {doc.source_type}
                              </span>
                              {doc.topic && (
                                <span className="hidden max-w-[10rem] truncate sm:inline">
                                  {doc.topic}
                                </span>
                              )}
                              <span>{doc.chunk_count} chunk(s)</span>
                            </span>
                          </button>

                          {open && (
                            <div className="border-t border-slate-100 bg-slate-50/60 px-4 py-3">
                              <div className="space-y-2">
                                {doc.chunks.map((chunk) => {
                                  const isCitedChunk =
                                    isTarget &&
                                    Boolean(linkSection) &&
                                    chunk.section_title === linkSection
                                  return (
                                  <div
                                    key={chunk.chunk_id}
                                    className={`rounded-lg border p-3 ${
                                      isCitedChunk
                                        ? 'border-amber-400 bg-amber-50 ring-1 ring-amber-300'
                                        : 'border-slate-200 bg-white'
                                    }`}
                                  >
                                    <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-slate-400">
                                      <span className="truncate font-medium text-slate-600">
                                        {chunk.section_title || `Chunk ${chunk.chunk_index + 1}`}
                                      </span>
                                      <span className="shrink-0 font-mono">
                                        #{chunk.chunk_id}
                                        {chunk.token_count != null && ` · ${chunk.token_count} tok`}
                                        {chunk.is_answer && ' · answer'}
                                      </span>
                                    </div>
                                    <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">
                                      {chunk.text}
                                    </p>
                                  </div>
                                  )
                                })}
                              </div>
                              {doc.chunk_count > doc.returned && (
                                <p className="mt-2 text-[11px] text-slate-400">
                                  Showing {doc.returned} of {doc.chunk_count} chunks — raise the
                                  “chunks / doc” limit above to see more.
                                </p>
                              )}
                            </div>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}

      {!loading && modules.length === 0 && !error && (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <BookOpen className="h-4 w-4" /> No modules configured.
        </p>
      )}
    </div>
  )
}
