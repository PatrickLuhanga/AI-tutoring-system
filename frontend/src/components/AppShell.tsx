import { LogOut, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/context'
import { canAccess, NAV, ROLE_BADGE, ROLE_LABEL, type Route } from '../navigation'

interface AppShellProps {
  route: Route
  onNavigate: (route: Route) => void
  children: ReactNode
}

/**
 * Shared chrome for every signed-in view: brand header, role-filtered
 * navigation, the current identity and a sign-out control.
 */
export default function AppShell({ route, onNavigate, children }: AppShellProps) {
  const { user, logout } = useAuth()
  if (!user) return null

  const nav = NAV.filter((item) => canAccess(item.id, user))

  return (
    <div className="flex min-h-screen flex-col bg-slate-100">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-slate-900">AI Tutoring System</div>
              <div className="text-[11px] text-slate-400">Client Tier</div>
            </div>
          </div>

          <nav className="order-3 flex w-full items-center gap-1 rounded-xl bg-slate-100 p-1 sm:order-2 sm:w-auto">
            {nav.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onNavigate(item.id)}
                className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition sm:flex-none ${
                  route === item.id
                    ? 'bg-white text-blue-700 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </nav>

          <div className="order-2 flex items-center gap-3 sm:order-3">
            <div className="hidden text-right leading-tight sm:block">
              <div className="flex items-center justify-end gap-1.5">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${ROLE_BADGE[user.role]}`}
                >
                  {ROLE_LABEL[user.role]}
                </span>
                {user.is_tutor && user.role === 'student' && (
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 ring-1 ring-inset ring-emerald-200">
                    Tutor
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-[11px] text-slate-500">{user.email}</div>
            </div>
            <button
              type="button"
              onClick={logout}
              className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-600"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Sign out</span>
            </button>
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">{children}</main>
    </div>
  )
}
