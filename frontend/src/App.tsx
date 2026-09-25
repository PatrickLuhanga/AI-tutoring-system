import { GraduationCap, LayoutDashboard, Loader2, ShieldCheck } from 'lucide-react'
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'

// Split the two tiers so the student bundle never downloads recharts, and the
// admin bundle never downloads the syntax highlighter.
const StudentChat = lazy(() => import('./views/StudentChat'))
const AdminDashboard = lazy(() => import('./views/AdminDashboard'))

type Route = 'student' | 'admin'

/** Minimal hash router — deep-linkable and dependency-free. */
function useHashRoute(): [Route, (route: Route) => void] {
  const read = (): Route =>
    window.location.hash.replace(/^#\/?/, '') === 'admin' ? 'admin' : 'student'

  const [route, setRoute] = useState<Route>(read)

  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return [route, (next) => { window.location.hash = `/${next}` }]
}

const NAV: Array<{ id: Route; label: string; icon: ReactNode }> = [
  { id: 'student', label: 'Student Chat', icon: <GraduationCap className="h-4 w-4" /> },
  { id: 'admin', label: 'Admin / Tutor', icon: <LayoutDashboard className="h-4 w-4" /> },
]

export default function App() {
  const [route, navigate] = useHashRoute()

  return (
    <div className="flex min-h-screen flex-col bg-slate-100">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-slate-900">AI Tutoring System</div>
              <div className="text-[11px] text-slate-400">Client Tier prototype</div>
            </div>
          </div>

          <nav className="flex items-center gap-1 rounded-xl bg-slate-100 p-1">
            {NAV.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => navigate(item.id)}
                className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
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
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">
        <Suspense
          fallback={
            <div className="flex flex-1 items-center justify-center text-slate-400">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading view…
            </div>
          }
        >
          {route === 'student' ? <StudentChat /> : <AdminDashboard />}
        </Suspense>
      </main>
    </div>
  )
}
