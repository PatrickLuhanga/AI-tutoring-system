import { ChevronDown, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import type { TurnAudit } from '../types'

interface AuditPanelProps {
  audit: TurnAudit
}

function Pill({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    slate: 'bg-slate-100 text-slate-600 ring-slate-200',
    blue: 'bg-blue-50 text-blue-700 ring-blue-200',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
    rose: 'bg-rose-50 text-rose-700 ring-rose-200',
    emerald: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

/** Collapsible view of the agent workflow's audit trail for one tutor turn. */
export default function AuditPanel({ audit }: AuditPanelProps) {
  const [open, setOpen] = useState(false)
  const { intent, scaffolding, guardrail, retrieval, llm } = audit

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-medium text-slate-400 transition hover:text-slate-600"
        aria-expanded={open}
      >
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
        Agent audit
        <span className="text-slate-300">·</span>
        <span className="font-normal">{intent.label}</span>
        <span className="text-slate-300">·</span>
        <span className="font-normal">{llm.model}</span>
        <span className="text-slate-300">·</span>
        <span className="font-normal">{llm.latency_ms} ms</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50/80 p-3 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 font-semibold text-slate-500">Intent</span>
            <Pill tone="blue">{intent.label}</Pill>
            <Pill>confidence {intent.confidence}</Pill>
            <Pill>{intent.source}</Pill>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 font-semibold text-slate-500">Scaffolding</span>
            <Pill tone="blue">{scaffolding.stage}</Pill>
            <Pill>hint depth {scaffolding.hint_sequence_depth}</Pill>
            {scaffolding.strategy && <Pill>{scaffolding.strategy}</Pill>}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 font-semibold text-slate-500">Guardrail</span>
            {guardrail.flagged ? (
              <Pill tone="rose">
                <ShieldAlert className="h-3 w-3" /> flagged
              </Pill>
            ) : (
              <Pill tone="emerald">
                <ShieldCheck className="h-3 w-3" /> clean
              </Pill>
            )}
            <Pill tone={guardrail.action === 'pass' ? 'emerald' : 'amber'}>{guardrail.action}</Pill>
            {guardrail.flags.map((flag) => (
              <Pill key={flag} tone="rose">
                {flag}
              </Pill>
            ))}
          </div>

          <div className="flex gap-2">
            <span className="w-20 shrink-0 font-semibold text-slate-500">Retrieval</span>
            <div className="space-y-1">
              <div className="text-slate-600">
                {retrieval.chunks.length} curriculum chunk(s), {retrieval.patterns.length} code
                pattern(s) for <span className="font-mono">{retrieval.module_id}</span>
              </div>
              {retrieval.chunks.map((chunk) => (
                <div key={chunk.chunk_id} className="rounded-md border border-slate-200 bg-white p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-slate-700">
                      {chunk.section_title ?? chunk.source_name}
                    </span>
                    <span className="font-mono text-[10px] text-slate-400">
                      d={chunk.distance}
                    </span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-slate-500">{chunk.preview}</p>
                </div>
              ))}
              {retrieval.patterns.map((pattern) => (
                <div key={pattern.pattern_id} className="rounded-md border border-slate-200 bg-white p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-slate-700">{pattern.error_title}</span>
                    <span className="font-mono text-[10px] text-slate-400">
                      d={pattern.distance}
                    </span>
                  </div>
                  <p className="mt-0.5 text-slate-500">{pattern.hint}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 font-semibold text-slate-500">Inference</span>
            <Pill tone={llm.provider === 'local' ? 'blue' : 'amber'}>{llm.provider}</Pill>
            <Pill>{llm.backend}</Pill>
            <Pill>{llm.model}</Pill>
            <Pill>{llm.latency_ms} ms</Pill>
            {audit.telemetry_log_id != null && <Pill>log #{audit.telemetry_log_id}</Pill>}
          </div>
        </div>
      )}
    </div>
  )
}
