import { Ban, CheckCircle2, Loader2, Search, ShieldAlert, Trash2, UserCog } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useSession } from '../state/session'
import type { AccountRole } from '../types'

interface AccountRow {
  user_id: number
  email: string
  full_name: string | null
  role: AccountRole
  status: 'active' | 'suspended'
  student_number: string | null
  modules: string[]
  last_login_at: string | null
  created_at: string | null
}

const ROLES: AccountRole[] = ['student', 'tutor', 'lecturer', 'admin']

/**
 * System Admin account management.
 *
 * Suspending an account also revokes its live sessions server-side, so the change
 * takes effect immediately rather than when a token happens to expire.
 */
export default function Accounts() {
  const { user: me } = useSession()
  const [rows, setRows] = useState<AccountRow[]>([])
  const [q, setQ] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const token = readToken()

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (q.trim()) params.set('q', q.trim())
    if (roleFilter) params.set('role', roleFilter)
    try {
      const res = await fetch(`/api/accounts?${params}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      setRows(body.accounts ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [q, roleFilter, token])

  useEffect(() => {
    void load()
  }, [load])

  async function patch(id: number, body: Record<string, string>) {
    setBusy(id)
    setError(null)
    try {
      const res = await fetch(`/api/accounts/${id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}))
        throw new Error(payload.error ?? `${res.status}`)
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function revoke(id: number) {
    setBusy(id)
    try {
      await fetch(`/api/accounts/${id}/sessions`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Accounts</h1>
        <p className="text-sm text-slate-500">
          Every account across the four roles. Suspending one signs it out everywhere at once.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5">
          <Search className="h-3.5 w-3.5 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Email, name or student number"
            className="w-56 bg-transparent text-sm outline-none placeholder:text-slate-400"
          />
        </div>
        <select
          value={roleFilter}
          onChange={(e) => setRoleFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm font-medium text-slate-700"
        >
          <option value="">All roles</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <span className="text-xs text-slate-400">{rows.length} shown</span>
      </div>

      {error && (
        <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
          {error}
        </p>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading accounts…
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Person</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Modules</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last seen</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const isSelf = r.user_id === me?.user_id
                return (
                  <tr key={r.user_id} className="hover:bg-slate-50">
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-800">
                        {r.full_name || r.email}
                        {isSelf && <span className="ml-1.5 text-[10px] text-slate-400">you</span>}
                      </div>
                      <div className="font-mono text-[11px] text-slate-400">
                        {r.email}
                        {r.student_number && ` · ${r.student_number}`}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={r.role}
                        disabled={isSelf || busy === r.user_id}
                        onChange={(e) => void patch(r.user_id, { role: e.target.value })}
                        className="rounded-md border border-slate-300 bg-white px-1.5 py-1 text-xs font-medium capitalize text-slate-700 disabled:opacity-50"
                      >
                        {ROLES.map((role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2 font-mono text-[11px] text-slate-500">
                      {r.modules.length ? r.modules.join(', ') : '-'}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${
                          r.status === 'active'
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-rose-100 text-rose-700'
                        }`}
                      >
                        {r.status === 'active' ? (
                          <CheckCircle2 className="h-3 w-3" />
                        ) : (
                          <Ban className="h-3 w-3" />
                        )}
                        {r.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-slate-500">
                      {r.last_login_at ? new Date(r.last_login_at).toLocaleString() : 'never'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button
                          type="button"
                          title="Sign out everywhere"
                          disabled={isSelf || busy === r.user_id}
                          onClick={() => void revoke(r.user_id)}
                          className="rounded-md border border-slate-300 px-1.5 py-1 text-[11px] text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
                        >
                          <Trash2 className="inline h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          disabled={isSelf || busy === r.user_id}
                          onClick={() =>
                            void patch(r.user_id, {
                              status: r.status === 'active' ? 'suspended' : 'active',
                            })
                          }
                          className={`rounded-md border px-1.5 py-1 text-[11px] font-medium transition disabled:opacity-40 ${
                            r.status === 'active'
                              ? 'border-rose-200 text-rose-600 hover:bg-rose-50'
                              : 'border-emerald-200 text-emerald-600 hover:bg-emerald-50'
                          }`}
                        >
                          {r.status === 'active' ? 'Suspend' : 'Restore'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-slate-500">
              No accounts match that search.
            </p>
          )}
        </div>
      )}

      <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-500">
        <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />
        You cannot change your own role or status. That guard exists because a single-admin
        deployment that demotes itself has nobody left to undo it.
        <UserCog className="mt-px h-3.5 w-3.5 shrink-0 text-slate-300" />
      </p>
    </div>
  )
}

function readToken(): string | null {
  try {
    return localStorage.getItem('ai_tutor.token')
  } catch {
    return null
  }
}
