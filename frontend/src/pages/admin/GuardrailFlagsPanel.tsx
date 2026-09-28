import { ShieldAlert } from 'lucide-react'
import { SectionCard } from '../../components/DashboardKit'
import type { GuardrailFlag } from '../../types'

const FLAG_LABEL: Record<string, string> = {
  solution_leak: 'Solution leak',
  out_of_scope: 'Out of scope',
  too_long: 'Response too long',
  bypass_attempt: 'Bypass attempt',
}

/** Guardrail flag totals from the Guardrail Agent's telemetry (section 11). */
export default function GuardrailFlagsPanel({ flags }: { flags: GuardrailFlag[] }) {
  const max = Math.max(1, ...flags.map((f) => f.count))

  return (
    <SectionCard
      title="Guardrail flags"
      description="Content-audit failures recorded across every module."
      icon={<ShieldAlert className="h-4 w-4 text-rose-500" />}
    >
      <div className="space-y-3">
        {flags.map((flag) => (
          <div key={flag.flag}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="font-medium text-slate-600">
                {FLAG_LABEL[flag.flag] ?? flag.flag}
              </span>
              <span className="font-mono text-slate-400">{flag.count.toLocaleString()}</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-rose-400 to-rose-500"
                style={{ width: `${(flag.count / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
        {flags.length === 0 && (
          <p className="py-4 text-center text-sm text-slate-400">No guardrail flags recorded.</p>
        )}
      </div>
    </SectionCard>
  )
}
