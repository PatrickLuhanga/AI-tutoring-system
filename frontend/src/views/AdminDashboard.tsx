import {
  Activity,
  CheckCircle2,
  Cloud,
  Cpu,
  Gauge,
  RefreshCw,
  Save,
  Server,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, USE_MOCK } from '../api/client'
import type { LLMConfig, LLMProvider, OllamaModel, TelemetryAnalytics } from '../types'

const CATEGORY_COLORS = ['#6366f1', '#f59e0b', '#ec4899', '#14b8a6']

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: ReactNode
  label: string
  value: string
  hint?: string
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-slate-500">
        {icon}
        <span className="text-xs font-semibold uppercase tracking-wide">{label}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-400">{hint}</div>}
    </div>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100'

export default function AdminDashboard() {
  const [config, setConfig] = useState<LLMConfig | null>(null)
  const [models, setModels] = useState<OllamaModel[]>([])
  const [analytics, setAnalytics] = useState<TelemetryAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  // Draft config the admin edits before saving.
  const [provider, setProvider] = useState<LLMProvider>('local')
  const [localModel, setLocalModel] = useState('')
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState('http://localhost:11434')
  const [cloudProvider, setCloudProvider] = useState('openai')
  const [cloudBaseUrl, setCloudBaseUrl] = useState('https://api.openai.com/v1')
  const [cloudModel, setCloudModel] = useState('gpt-4o-mini')
  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      // Settle each call independently. A single rejected request must not leave
      // the whole panel stuck on the spinner: the model list and the analytics
      // panel fail independently in practice.
      const [cfg, ollama, telemetry] = await Promise.all([
        api.getLLMConfig().catch((e) => {
          console.error('llm-config failed', e)
          return null
        }),
        api.listOllamaModels().catch((e) => {
          console.error('ollama-models failed', e)
          return null
        }),
        api.getAnalytics().catch((e) => {
          console.error('analytics failed', e)
          return null
        }),
      ])
      if (cancelled) return
      if (cfg) {
        setConfig(cfg)
        setProvider(cfg.provider)
        setLocalModel(cfg.local.model)
        setOllamaBaseUrl(cfg.local.base_url)
        setCloudProvider(cfg.cloud.provider)
        setCloudBaseUrl(cfg.cloud.base_url)
        setCloudModel(cfg.cloud.model)
      }
      if (ollama) setModels(ollama.models)
      setAnalytics(telemetry)
      setLoading(false)
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const satisfactionRate = useMemo(() => {
    if (!analytics) return 0
    const { thumbs_up, thumbs_down } = analytics.satisfaction
    return thumbs_up / Math.max(1, thumbs_up + thumbs_down)
  }, [analytics])

  const failureData = useMemo(
    () =>
      (analytics?.failure_categories ?? []).map((c) => ({
        name: c.label,
        value: c.count,
      })),
    [analytics],
  )

  const moduleData = useMemo(
    () =>
      (analytics?.by_module ?? []).map((m) => ({
        name: m.module_id,
        Satisfaction: Math.round(m.satisfaction_rate * 100),
        Responses: m.responses,
      })),
    [analytics],
  )

  async function save() {
    setSaving(true)
    try {
      const updated = await api.updateLLMConfig({
        provider,
        local_model: localModel,
        ollama_base_url: ollamaBaseUrl,
        cloud_provider: cloudProvider as LLMConfig['cloud']['provider'],
        cloud_base_url: cloudBaseUrl,
        cloud_model: cloudModel,
        ...(apiKey ? { api_key: apiKey } : {}),
        updated_by: 'admin@example.edu',
      })
      setConfig(updated)
      setApiKey('')
      setSavedAt(new Date().toLocaleTimeString())
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-slate-400">
        <RefreshCw className="mr-2 h-5 w-5 animate-spin" />
        Loading dashboard…
      </div>
    )
  }

  return (
    <div className="mx-auto min-h-0 w-full max-w-6xl flex-1 overflow-y-auto px-4 py-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Admin · Tutor Dashboard</h1>
          <p className="text-sm text-slate-500">
            Dynamic LLM Router control and telemetry health for the tutoring agents.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {USE_MOCK && (
            <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
              Mock data
            </span>
          )}
          {config && (
            <span className="flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Active: {config.effective.model}
            </span>
          )}
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* ---------------------------- LLM Router ---------------------------- */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-base font-semibold text-slate-800">
              <Server className="h-4 w-4 text-blue-600" />
              LLM Router
            </h2>
            {savedAt && <span className="text-xs text-slate-400">Saved {savedAt}</span>}
          </div>

          <div className="mb-4 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => setProvider('local')}
              className={`flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition ${
                provider === 'local'
                  ? 'bg-white text-blue-700 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Cpu className="h-4 w-4" />
              Local Ollama
            </button>
            <button
              type="button"
              onClick={() => setProvider('cloud')}
              className={`flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition ${
                provider === 'cloud'
                  ? 'bg-white text-blue-700 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Cloud className="h-4 w-4" />
              Cloud API
            </button>
          </div>

          {provider === 'local' ? (
            <div className="space-y-3">
              <Field label="Ollama base URL">
                <input
                  className={inputClass}
                  value={ollamaBaseUrl}
                  onChange={(e) => setOllamaBaseUrl(e.target.value)}
                />
              </Field>
              <Field label="Local model">
                <select
                  className={inputClass}
                  value={localModel}
                  onChange={(e) => setLocalModel(e.target.value)}
                >
                  {models.map((model) => (
                    <option key={model.model} value={model.model}>
                      {model.name}
                      {model.parameter_size ? ` · ${model.parameter_size}` : ''}
                      {model.size_gb ? ` · ${model.size_gb} GB` : ''}
                    </option>
                  ))}
                  {!models.some((m) => m.model === localModel) && localModel && (
                    <option value={localModel}>{localModel} (not detected)</option>
                  )}
                </select>
              </Field>
              <p className="text-xs text-slate-400">
                Populated from <span className="font-mono">GET /api/admin/ollama-models</span>.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <Field label="Cloud provider">
                <select
                  className={inputClass}
                  value={cloudProvider}
                  onChange={(e) => setCloudProvider(e.target.value)}
                >
                  <option value="openai">OpenAI</option>
                  <option value="openai_compatible">OpenAI-compatible</option>
                  <option value="azure_openai">Azure OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </Field>
              <Field label="Base URL">
                <input
                  className={inputClass}
                  value={cloudBaseUrl}
                  onChange={(e) => setCloudBaseUrl(e.target.value)}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Model">
                  <input
                    className={inputClass}
                    value={cloudModel}
                    onChange={(e) => setCloudModel(e.target.value)}
                  />
                </Field>
                <Field label="API key">
                  <input
                    type="password"
                    className={inputClass}
                    placeholder={config?.cloud.api_key_masked ?? 'sk-…'}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                  />
                </Field>
              </div>
              <p className="text-xs text-slate-400">
                Keys are encrypted in PostgreSQL and never echoed back by the API.
              </p>
            </div>
          )}

          <div className="mt-5 flex items-center justify-between border-t border-slate-100 pt-4">
            <div className="text-xs text-slate-400">
              {config && (
                <>
                  Effective target{' '}
                  <span className="font-mono text-slate-600">{config.effective.target}</span> ·
                  updated by {config.updated_by}
                </>
              )}
            </div>
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
            >
              {saving ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {saving ? 'Saving…' : 'Apply configuration'}
            </button>
          </div>
        </section>

        {/* ------------------------ Telemetry stat cards ---------------------- */}
        <section className="grid grid-cols-2 gap-4">
          <StatCard
            icon={<Gauge className="h-4 w-4" />}
            label="LLM Health Index"
            value={`${Math.round(satisfactionRate * 100)}%`}
            hint="Thumbs-up share of all rated hints"
          />
          <StatCard
            icon={<Activity className="h-4 w-4" />}
            label="Avg latency"
            value={`${analytics?.average_latency_ms ?? 0} ms`}
            hint="Local + cloud, last 24h"
          />
          <StatCard
            icon={<ThumbsUp className="h-4 w-4" />}
            label="Helpful hints"
            value={(analytics?.satisfaction.thumbs_up ?? 0).toLocaleString()}
            hint={`of ${(analytics?.total_hints ?? 0).toLocaleString()} hints`}
          />
          <StatCard
            icon={<ThumbsDown className="h-4 w-4" />}
            label="Flagged hints"
            value={(analytics?.satisfaction.thumbs_down ?? 0).toLocaleString()}
            hint={`${(analytics?.failure_categories ?? []).reduce(
              (sum, c) => sum + c.count,
              0,
            )} tagged reasons`}
          />
        </section>
      </div>

      {/* --------------------------- Charts ---------------------------------- */}
      <div className="mt-5 grid gap-5 lg:grid-cols-5">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-3">
          <h2 className="mb-1 text-base font-semibold text-slate-800">
            Satisfaction by module
          </h2>
          <p className="mb-4 text-xs text-slate-400">
            Percentage of rated hints with a thumbs-up, per enrolled module.
          </p>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={moduleData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#64748b' }} />
                <YAxis
                  domain={[0, 100]}
                  unit="%"
                  tick={{ fontSize: 12, fill: '#64748b' }}
                />
                <Tooltip
                  cursor={{ fill: '#f1f5f9' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                  formatter={(value) => [`${value}%`, 'Satisfaction']}
                />
                <Bar dataKey="Satisfaction" fill="#3b82f6" radius={[6, 6, 0, 0]} maxBarSize={56} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-1 text-base font-semibold text-slate-800">Failure categories</h2>
          <p className="mb-4 text-xs text-slate-400">
            Thumbs-down reason tags collected from the student micro-feedback.
          </p>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={failureData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={52}
                  outerRadius={92}
                  paddingAngle={2}
                  label={({ value }) => value}
                  labelLine={false}
                >
                  {failureData.map((entry, index) => (
                    <Cell key={entry.name} fill={CATEGORY_COLORS[index % CATEGORY_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                />
                <Legend
                  verticalAlign="bottom"
                  height={36}
                  formatter={(value) => <span className="text-xs text-slate-600">{value}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
    </div>
  )
}
