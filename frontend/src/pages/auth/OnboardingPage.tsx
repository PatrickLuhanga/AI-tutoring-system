import { AlertCircle, Check, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../auth/context'
import type { Module } from '../../types'

/**
 * First-login onboarding: capture the user's name and enrolled modules.
 *
 * The profile was provisioned on sign-in; this fills in the details that the
 * directory does not supply. Saving clears `needs_onboarding`, after which the
 * workspace is shown.
 */
export default function OnboardingPage() {
  const { user, applyProfile, logout } = useAuth()
  const [modules, setModules] = useState<Module[]>([])
  const [fullName, setFullName] = useState(user?.name ?? '')
  const [studentNumber, setStudentNumber] = useState('')
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(user?.enrolled_modules ?? []),
  )
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const list = await api.listModules()
        if (!cancelled) setModules(list)
      } catch {
        if (!cancelled) setError('Could not load the module registry.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  function toggle(moduleId: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(moduleId)) next.delete(moduleId)
      else next.add(moduleId)
      return next
    })
  }

  const requiresModules = user?.role === 'student'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (fullName.trim().length < 2) {
      setError('Enter your full name.')
      return
    }
    if (requiresModules && selected.size === 0) {
      setError('Select at least one enrolled module.')
      return
    }
    setError(null)
    setSaving(true)
    try {
      const profile = await api.updateProfile({
        full_name: fullName.trim(),
        ...(studentNumber.trim() ? { student_number: studentNumber.trim() } : {}),
        modules: Array.from(selected),
      })
      applyProfile(profile)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-100 via-slate-100 to-blue-100 px-4 py-10">
      <div className="w-full max-w-2xl rounded-3xl border border-slate-200 bg-white p-8 shadow-xl">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Complete your profile</h1>
            <p className="text-sm text-slate-500">{user?.email}</p>
          </div>
        </div>

        <form onSubmit={submit} className="mt-6 space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Full name
              </span>
              <input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="e.g. Sanele Ndlovu"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Student number <span className="font-normal normal-case text-slate-400">(optional)</span>
              </span>
              <input
                value={studentNumber}
                onChange={(e) => setStudentNumber(e.target.value)}
                placeholder="e.g. 22000000"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
              />
            </label>
          </div>

          <div>
            <span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-500">
              {requiresModules ? 'Enrolled modules' : 'Modules (optional)'}
            </span>
            {loading ? (
              <div className="flex items-center gap-2 py-4 text-sm text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading modules…
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {modules.map((module) => {
                  const active = selected.has(module.module_id)
                  return (
                    <button
                      key={module.module_id}
                      type="button"
                      onClick={() => toggle(module.module_id)}
                      aria-pressed={active}
                      className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                        active
                          ? 'border-blue-400 bg-blue-50/70 ring-1 ring-blue-200'
                          : 'border-slate-200 bg-white hover:border-blue-300'
                      }`}
                    >
                      <span
                        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                          active
                            ? 'border-blue-500 bg-blue-600 text-white'
                            : 'border-slate-300 bg-white text-transparent'
                        }`}
                      >
                        <Check className="h-3.5 w-3.5" />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-medium text-slate-800">
                          {module.module_id} · {module.module_name}
                        </span>
                        <span className="block text-[11px] text-slate-400">
                          {module.language && module.language !== 'N/A'
                            ? `${module.language} · ${module.course_code}`
                            : module.course_code}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 ring-1 ring-rose-200">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {error}
            </div>
          )}

          <div className="flex items-center justify-between border-t border-slate-100 pt-4">
            <button
              type="button"
              onClick={logout}
              className="text-sm font-medium text-slate-400 transition hover:text-rose-600"
            >
              Sign out
            </button>
            <button
              type="submit"
              disabled={saving || loading}
              className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              Save & continue
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
