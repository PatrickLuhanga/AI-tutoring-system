import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { materialHref } from '../navigation'
import CodeBlock from './CodeBlock'
import type { Citation } from '../types'

/** Matches an inline citation label such as ``[C1]`` or a run of them ``[C1][C2]``. */
const CITE_RUN = /\[(C\d+)\](\[(?:C\d+\])*)/g

/** True when a citation points at the open web rather than course material. */
function isExternal(cite: Citation): boolean {
  return cite.source_category === 'web' || /^https?:\/\//i.test(cite.url ?? '')
}

/**
 * Turn the tutor's inline ``[C1]`` markers into links.
 *
 * A course-material citation deep-links into the Course Material viewer
 * (`#/material?module=...&doc=...`) so a student can verify the claim against the
 * exact source. A web-fallback citation links to the external URL and opens in a
 * new tab. Only labels actually offered for this turn become links; anything else
 * is left as plain text so a hallucinated label is visible as a mistake.
 */
function linkifyCitations(content: string, citations: Citation[]): string {
  if (!citations || citations.length === 0) return content
  const byKey = new Map(citations.map((c) => [c.cite_key, c]))
  if (byKey.size === 0) return content

  const hrefFor = (cite: Citation): string =>
    isExternal(cite)
      ? cite.url
      : materialHref({
          moduleId: cite.module_id,
          sourceFile: cite.source_file,
          section: cite.section_title,
        })

  return content.replace(CITE_RUN, (match, key: string, rest: string) => {
    const cite = byKey.get(key)
    if (!cite) return match
    const link = (k: string) => {
      const target = byKey.get(k)
      if (!target) return `[${k}]`
      return `[${k}](${hrefFor(target)})`
    }
    // Re-render the first label plus any immediately following run of them.
    const tail = rest.replace(/\[(C\d+)\]/g, (_, k: string) => link(k))
    return `${link(key)}${tail}`
  })
}

const components: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className ?? '')
    const text = String(children).replace(/\n$/, '')

    // v10 removed the `inline` flag: a fenced block either carries a
    // `language-*` class or spans multiple lines.
    const isBlock = Boolean(match) || text.includes('\n')

    if (isBlock) {
      return <CodeBlock language={match?.[1] ?? ''} value={text} />
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    )
  },
  a({ children, href, ...props }) {
    const isCitation =
      typeof href === 'string' &&
      (href.startsWith('/resources/') || href.startsWith('#/material'))
    // In-app citation links navigate the SPA (same tab); anything else opens in
    // a new tab.
    const isInternal = typeof href === 'string' && href.startsWith('#/material')
    return (
      <a
        href={href}
        {...(isInternal ? {} : { target: '_blank', rel: 'noreferrer noopener' })}
        className={
          isCitation
            ? 'mx-0.5 inline-flex items-center rounded bg-blue-50 px-1 py-px align-baseline text-[11px] font-semibold text-blue-700 no-underline ring-1 ring-inset ring-blue-200 hover:bg-blue-100'
            : undefined
        }
        {...props}
      >
        {children}
      </a>
    )
  },
}

interface MarkdownMessageProps {
  content: string
  citations?: Citation[]
}

/** Renders the tutor's Socratic answer as styled prose + IDE-style code. */
export default function MarkdownMessage({ content, citations }: MarkdownMessageProps) {
  return (
    <div className="prose prose-chat prose-slate max-w-none prose-headings:font-semibold prose-headings:text-slate-800 prose-p:text-slate-700 prose-strong:text-slate-900 prose-li:text-slate-700 prose-blockquote:border-l-blue-400 prose-blockquote:bg-blue-50/60 prose-blockquote:py-1 prose-blockquote:not-italic prose-blockquote:text-slate-600">
      <ReactMarkdown components={components} remarkPlugins={[remarkGfm]}>
        {linkifyCitations(content, citations ?? [])}
      </ReactMarkdown>
    </div>
  )
}
