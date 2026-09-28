import {
  AlertTriangle,
  Gauge,
  Loader2,
  MessageSquare,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  Users,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, USE_MOCK } from '../../api/client'
import { useAuth } from '../../auth/context'
import { SectionCard, StatCard } from '../../components/DashboardKit'
import type { TutorAnalytics } from '../../types'

function relativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  return `${Math.floor(seconds / 3600)} h ago`
}

/**
 * Scoped Tutor Dashboard.
 *
 * Everything on this page is filtered to the modules the tutor is assigned to
 * (`tutor_assignments` on the backend, `user.modules` on the client). Admins
 * viewing the page fall back to the whole registry.
 */
export default function TutorDashboard() {
  const { user } = useAuth()
  const scope = useMemo(() => user?.modules ?? [], [user?.modules])
  const [analytics, setAnalytics] = useState<TutorAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError(null)
      try {
        const data = await api.getTutorAnalytics(scope)
        if (!cancelled) setAnalytics(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [scope, nonce])

  const struggleData = useMemo(
    () =>
      (analytics?.struggle_topics ?? []).slice(0, 8).map((topic) => ({
        name: topic.topic,
        Struggles: topic.struggles,
        Students: topic.students,
      })),
    [analytics],
  )

  if (loading && !analytics) {
    return (
      <div className="flex flex-1 items-center justify-center text-slate-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading tutor analytics…
      </div>
    )
  }

  return (
    <div className="mx-auto min-h-0 w-full max-w-6xl flex-1 overflow-y-auto px-4 py-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Tutor Dashboard</h1>
          <p className="text-sm text-slate-500">
            {user?.name} · activity strictly scoped to your assigned modules
          </p>
        </div>
        <div className="flex items-center gap-2">
          {USE_MOCK && (
            <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
              Mock data
            </span>
          )}
          <button
            type="button"
            onClick={() => setNonce((n) => n + 1)}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-60"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </button>
        </div>
      </header>

      {/* Module scope */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Module scope
        </span>
        {(analytics?.scope ?? []).map((module) => (
          <span
            key={module.module_id}
            className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200"
          >
            {module.module_id} · {module.module_name}
          </span>
        ))}
        {analytics && analytics.scope.length === 0 && (
          <span className="text-xs text-slate-400">
            No modules assigned yet — ask an administrator to grant access.
          </span>
        )}
      </div>

      {error && (
        <div className="mb-5 flex items-start gap-2 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-rose-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          icon={<Users className="h-4 w-4" />}
          label="Active students"
          value={(analytics?.active_students ?? 0).toLocaleString()}
          hint={`Last ${analytics?.window_minutes ?? 60} min`}
        />
        <StatCard
          icon={<MessageSquare className="h-4 w-4" />}
          label="Queries"
          value={(analytics?.total_queries ?? 0).toLocaleString()}
          hint={`${(analytics?.total_sessions ?? 0).toLocaleString()} sessions`}
        />
        <StatCard
          icon={<Gauge className="h-4 w-4" />}
          label="Avg hint depth"
          value={`${analytics?.avg_hint_depth ?? 0}`}
          hint="Scaffolding turns per session"
        />
        <StatCard
          icon={<ShieldAlert className="h-4 w-4" />}
          label="Guardrail flags"
          value={(analytics?.guardrail_flags ?? 0).toLocaleString()}
          hint="Leaks / out-of-scope in scope"
        />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-5">
        {/* -------------------------------------------- Struggle trend chart */}
        <SectionCard
          title="Struggle trends"
          description="Topics students needed the most help with, across your modules."
          className="lg:col-span-3"
          icon={<TrendingUp className="h-4 w-4 text-indigo-500" />}
        >
          <div className="h-80 w-full">
            {struggleData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-slate-400">
                No struggle data in scope yet.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={struggleData}
                  layout="vertical"
                  margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 12, fill: '#64748b' }} allowDecimals={false} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={190}
                    tick={{ fontSize: 11, fill: '#475569' }}
                  />
                  <Tooltip
                    cursor={{ fill: '#f1f5f9' }}
                    contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                  />
                  <Bar dataKey="Struggles" fill="#6366f1" radius={[0, 6, 6, 0]} maxBarSize={22} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </SectionCard>

        {/* --------------------------------------------- Repeat help requests */}
        <SectionCard
          title="Repeat help requests"
          description="Students with repeated sessions or down-voted hints."
          className="lg:col-span-2"
          icon={<AlertTriangle className="h-4 w-4 text-amber-500" />}
        >
          <div className="scrollbar-thin max-h-80 overflow-y-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-white text-[11px] uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="pb-2 font-semibold">Student</th>
                  <th className="pb-2 text-center font-semibold">Sessions</th>
                  <th className="pb-2 text-center font-semibold">Turns</th>
                  <th className="pb-2 text-center font-semibold">👎</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {(analytics?.repeat_help_students ?? []).map((row) => (
                  <tr key={`${row.student_id ?? row.email}-${row.module_id}`}>
                    <td className="py-2 pr-2">
                      <div className="truncate text-slate-700">{row.email ?? 'Unknown'}</div>
                      <div className="font-mono text-[10px] text-slate-400">{row.module_id}</div>
                    </td>
                    <td className="py-2 text-center text-slate-600">{row.sessions}</td>
                    <td className="py-2 text-center text-slate-600">{row.turns}</td>
                    <td className="py-2 text-center font-medium text-rose-600">{row.thumbs_down}</td>
                  </tr>
                ))}
                {(analytics?.repeat_help_students ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-6 text-center text-sm text-slate-400">
                      No repeat help requests in scope.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {analytics && (
            <p className="mt-3 border-t border-slate-100 pt-3 text-[11px] text-slate-400">
              Updated {relativeTime(analytics.generated_at)}
            </p>
          )}
        </SectionCard>
      </div>
    </div>
  )
}
