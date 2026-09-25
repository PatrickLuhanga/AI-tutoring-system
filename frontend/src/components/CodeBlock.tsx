import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import csharp from 'react-syntax-highlighter/dist/esm/languages/prism/csharp'
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

// Register only what a tutoring transcript realistically contains, so the
// PrismLight build stays a fraction of the full highlight-all-languages bundle.
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('csharp', csharp)
SyntaxHighlighter.registerLanguage('c#', csharp)
SyntaxHighlighter.registerLanguage('java', java)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('js', javascript)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('markup', markup)
SyntaxHighlighter.registerLanguage('html', markup)
SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('py', python)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('ts', typescript)

interface CodeBlockProps {
  language: string
  value: string
}

/** IDE-style code block: title bar, language chip, copy-to-clipboard. */
export default function CodeBlock({ language, value }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="not-prose my-4 overflow-hidden rounded-xl border border-slate-700/60 bg-[#282c34] shadow-lg">
      <div className="flex items-center justify-between border-b border-slate-700/60 bg-slate-800/80 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="flex gap-1.5">
            <span className="h-3 w-3 rounded-full bg-rose-400/80" />
            <span className="h-3 w-3 rounded-full bg-amber-400/80" />
            <span className="h-3 w-3 rounded-full bg-emerald-400/80" />
          </span>
          <span className="ml-2 font-mono text-xs uppercase tracking-wider text-slate-400">
            {language || 'text'}
          </span>
        </div>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-slate-300 transition hover:bg-slate-700 hover:text-white"
          aria-label="Copy code"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        customStyle={{
          margin: 0,
          padding: '1rem 1.25rem',
          background: 'transparent',
          fontSize: '0.85rem',
          lineHeight: 1.6,
        }}
        codeTagProps={{ style: { fontFamily: 'var(--font-mono)' } }}
        wrapLongLines
      >
        {value}
      </SyntaxHighlighter>
    </div>
  )
}
