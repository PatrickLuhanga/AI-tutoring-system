import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import CodeBlock from './CodeBlock'

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
  a({ children, ...props }) {
    return (
      <a target="_blank" rel="noreferrer noopener" {...props}>
        {children}
      </a>
    )
  },
}

interface MarkdownMessageProps {
  content: string
}

/** Renders the tutor's Socratic answer as styled prose + IDE-style code. */
export default function MarkdownMessage({ content }: MarkdownMessageProps) {
  return (
    <div className="prose prose-chat prose-slate max-w-none prose-headings:font-semibold prose-headings:text-slate-800 prose-p:text-slate-700 prose-strong:text-slate-900 prose-li:text-slate-700 prose-blockquote:border-l-blue-400 prose-blockquote:bg-blue-50/60 prose-blockquote:py-1 prose-blockquote:not-italic prose-blockquote:text-slate-600">
      <ReactMarkdown components={components} remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
