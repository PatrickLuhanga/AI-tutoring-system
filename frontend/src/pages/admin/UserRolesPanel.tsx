import { AlertCircle, Check, Loader2, ShieldPlus, UserCog, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { SectionCard } from '../../components/DashboardKit'
import type { AdminOverview, Module, UserRecord, UserRole } from '../../types'

const ROLE_STYLE: Record<UserRole, string> = {
  student: 'bg-blue-50 text-blue-700 ring-blue-200',
  tutor: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  admin: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

/**
 * System-wide user / role management.
 *
 * An administrator grants tutor privileges (dual role) to any registered user
 * by student number, email or id; the user's student access is unaffected.
 */
export default function UserRolesPanel({
  overview,
  onRefresh,
}: {
  overview: AdminOverview | null
  onRefresh: () => Promise<void> | void
}) {
  const users = overview?.users ?? []
  const [modules, setModules] = useState<Module[]>([])
  const [identifier, setIdentifier] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const list = await api.listModules()
        if (!cancelled) setModules(list)
      } catch {
        /* the picker simply stays empty */
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

  async function grant() {
    if (!identifier.trim()) {
      setError('Enter a student number, email or user id.')
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const profile = await api.grantTutor({
        identifier: identifier.trim(),
        modules: Array.from(selected),
      })
      setNotice(`Tutor privileges granted to ${profile.email}.`)
      setIdentifier('')
      setSelected(new Set())
      await onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function revoke(user: UserRecord) {
    const target = user.student_number || user.email
    if (!window.confirm(`Revoke tutor privileges from ${user.full_name ?? user.email}?`)) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await api.revokeTutor(target)
      setNotice(`Tutor privileges revoked from ${user.email}.`)
      await onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SectionCard
      title="User & role management"
      description="DUT4life identities, their roles, and tutor privileges (dual roles)."
      icon={<UserCog className="h-4 w-4 text-indigo-600" />}
      actions={
        overview && (
          <div className="flex gap-2 text-[11px] text-slate-500">
            <span className="rounded-full bg-slate-100 px-2.5 py-1">
              {overview.total_students} students
            </span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1">
              {overview.total_tutors} tutors
            </span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1">{overview.total_admins} admins</span>
          </div>
        )
      }
    >
      {/* ---------------------------------------------------- Grant tutor */}
      <div className="mb-5 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
        <div className="mb-3 flex items-center gap-2">
          <ShieldPlus className="h-4 w-4 text-emerald-600" />
          <span className="text-sm font-semibold text-slate-700">Grant tutor privileges</span>
          <span className="text-xs text-slate-400">student number, DUT email or user id</span>
        </div>
        <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
          <input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="e.g. 22000000 or name@dut4life.ac.za"
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
          <div className="flex flex-wrap gap-1.5">
            {modules.map((module) => {
              const active = selected.has(module.module_id)
              return (
                <button
                  key={module.module_id}
                  type="button"
                  onClick={() => toggle(module.module_id)}
                  className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    active
                      ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                      : 'border-slate-300 bg-white text-slate-600 hover:border-emerald-300'
                  }`}
                >
                  {active && <Check className="h-3 w-3" />}
                  {module.module_id}
                </button>
              )
            })}
          </div>
        </div>
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="text-xs">
            {error && (
              <span className="flex items-center gap-1.5 text-rose-600">
                <AlertCircle className="h-3.5 w-3.5" />
                {error}
              </span>
            )}
            {!error && notice && <span className="text-emerald-600">{notice}</span>}
          </div>
          <button
            type="button"
            onClick={() => void grant()}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldPlus className="h-4 w-4" />}
            Grant tutor
          </button>
        </div>
      </div>

      {/* ------------------------------------------------------ User table */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="pb-2 font-semibold">Identity</th>
              <th className="pb-2 font-semibold">Roles</th>
              <th className="pb-2 font-semibold">Tutor modules</th>
              <th className="pb-2 font-semibold">Enrolled</th>
              <th className="pb-2 font-semibold">Last login</th>
              <th className="pb-2 text-right font-semibold">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((user) => (
              <tr key={user.email}>
                <td className="py-2.5 pr-3">
                  <div className="font-medium text-slate-700">{user.full_name ?? '—'}</div>
                  <div className="text-[11px] text-slate-400">
                    {user.email}
                    {user.student_number ? ` · ${user.student_number}` : ''}
                  </div>
                </td>
                <td className="py-2.5 pr-3">
                  <div className="flex flex-wrap gap-1">
                    {user.roles.map((role) => (
                      <span
                        key={role}
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${ROLE_STYLE[role]}`}
                      >
                        {role}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="py-2.5 pr-3 font-mono text-[11px] text-slate-500">
                  {user.tutor_modules.length ? user.tutor_modules.join(', ') : '—'}
                </td>
                <td className="py-2.5 pr-3 font-mono text-[11px] text-slate-500">
                  {user.enrolled_modules.length ? user.enrolled_modules.join(', ') : '—'}
                </td>
                <td className="py-2.5 pr-3 text-[11px] text-slate-500">
                  {formatDate(user.last_login_at)}
                </td>
                <td className="py-2.5 text-right">
                  {user.is_tutor ? (
                    <button
                      type="button"
                      onClick={() => void revoke(user)}
                      disabled={busy}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:border-rose-300 hover:text-rose-600 disabled:opacity-60"
                    >
                      <X className="h-3 w-3" />
                      Revoke
                    </button>
                  ) : (
                    <span className="text-[11px] text-slate-300">—</span>
                  )}
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={6} className="py-6 text-center text-sm text-slate-400">
                  No users found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </SectionCard>
  )
}
