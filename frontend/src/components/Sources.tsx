import { BookOpen, ExternalLink } from 'lucide-react'
import type { Citation } from '../types'

interface SourcesProps {
  citations?: Citation[]
  /** True when the tutor fell back to third-party material (commercial books). */
  thirdPartyFallback?: boolean
}

const CATEGORY_LABEL: Record<string, string> = {
  slides: 'Lecture slides',
  notes: 'Lecture notes',
  lecture_notes: 'Lecture notes',
  exercises: 'Exercises',
  examples: 'Worked examples',
  tutorials: 'Tutorials',
  books: 'Textbook (third-party)',
  tutor_answers: 'Tutor answer',
}

/**
 * The notes a reply was built from, each linking to the exact section of the
 * rendered corpus page.
 *
 * This is rendered from the retrieval record rather than from whatever labels the
 * model happened to write, so the sources are shown even when the model forgets
 * to tag individual sentences. Inline [C1] markers are progressive enhancement on
 * top of this, not the mechanism.
 */
export default function Sources({ citations, thirdPartyFallback }: SourcesProps) {
  if (!citations || citations.length === 0) return null

  return (
    <div className="mt-2.5 rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <BookOpen className="h-3.5 w-3.5" />
        <span>Sources</span>
        {thirdPartyFallback && (
          <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium normal-case text-amber-800">
            no lecture material matched, so this answer leans on third-party text
          </span>
        )}
      </div>
      <ul className="space-y-1">
        {citations.map((cite) => {
          const label =
            CATEGORY_LABEL[cite.source_category] ?? cite.source_category
          const isThirdParty = cite.source_category === 'books'
          return (
            <li key={cite.cite_key}>
              <a
                href={cite.url}
                target="_blank"
                rel="noreferrer"
                title={`${cite.source_file}${cite.anchor ? ` :: ${cite.anchor}` : ''}`}
                className="group flex items-start gap-2 rounded px-1.5 py-1 transition hover:bg-white"
              >
                <span
                  className={`mt-px shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                    isThirdParty
                      ? 'bg-amber-100 text-amber-800'
                      : 'bg-blue-100 text-blue-800'
                  }`}
                >
                  {cite.cite_key}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-slate-700 group-hover:text-blue-700">
                    {cite.section_title || cite.source_file}
                  </span>
                  <span className="block truncate text-[11px] text-slate-400">
                    {label} · {cite.source_file}
                  </span>
                </span>
                <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-slate-300 group-hover:text-blue-500" />
              </a>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
