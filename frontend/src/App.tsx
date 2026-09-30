import {
  BookOpen,
  ClipboardList,
  GraduationCap,
  LayoutDashboard,
  Loader2,
  LogOut,
  PenLine,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import NotificationBell from './components/NotificationBell'
import { useSession } from './state/session'

const Login = lazy(() => import('./views/Login'))
const Home = lazy(() => import('./views/Home'))
const StudentChat = lazy(() => import('./views/StudentChat'))
const CourseMaterial = lazy(() => import('./views/CourseMaterial'))
const Practice = lazy(() => import('./views/Practice'))
const TutorDashboard = lazy(() => import('./views/TutorDashboard'))
const Content = lazy(() => import('./views/Content'))
const Accounts = lazy(() => import('./views/Accounts'))
const AdminDashboard = lazy(() => import('./views/AdminDashboard'))

type Route = 'home' | 'chat' | 'material' | 'practice' | 'tutor' | 'content' | 'accounts' | 'admin'

interface NavItem {
  id: Route
  label: string
  icon: ReactNode
  /** Returns whether the signed-in account may see this destination. */
  show: (role: string, isStaff: boolean) => boolean
}

const NAV: NavItem[] = [
  {
    id: 'home',
    label: 'Home',
    icon: <HomeIcon className="h-4 w-4" />,
    show: () => true,
  },
  {
    id: 'chat',
    label: 'Tutor Chat',
    icon: <GraduationCap className="h-4 w-4" />,
    show: () => true,
  },
  {
    id: 'material',
    label: 'Course Material',
    icon: <BookOpen className="h-4 w-4" />,
    show: () => true,
  },
  {
    id: 'practice',
    label: 'Practice',
    icon: <ClipboardList className="h-4 w-4" />,
    show: () => true,
  },
  {
    id: 'tutor',
    label: 'Help Queue',
    icon: <LayoutDashboard className="h-4 w-4" />,
    show: (role) => canUseTutorQueueRole(role),
  },
  {
    id: 'content',
    label: 'Authoring',
    icon: <PenLine className="h-4 w-4" />,
    show: (role) => role === 'tutor' || role === 'lecturer' || role === 'admin',
  },
  {
    id: 'accounts',
    label: 'Accounts',
    icon: <Users className="h-4 w-4" />,
    show: (role) => role === 'admin',
  },
  {
    id: 'admin',
    label: 'Model',
    icon: <ShieldCheck className="h-4 w-4" />,
    show: (r) => r === 'admin',
  },
]

function canUseTutorQueueRole(role: string): boolean {
  return role === 'tutor' || role === 'lecturer' || role === 'admin'
}

function HomeIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
      <path d="M3 10.5 12 3l9 7.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 9.5V21h14V9.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** Minimal hash router - deep-linkable and dependency-free. */
function useHashRoute(): [Route, (route: Route) => void] {
  const read = (): Route => {
    const raw = window.location.hash.replace(/^#\/?/, '')
    return NAV.some((n) => n.id === raw) ? (raw as Route) : 'home'
  }
  const [route, setRoute] = useState<Route>(read)

  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return [route, (next) => { window.location.hash = `/${next}` }]
}

const ROLE_LABEL: Record<string, string> = {
  student: 'Student',
  tutor: 'Tutor',
  lecturer: 'Lecturer',
  admin: 'System Admin',
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-400">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  )
}

export default function App() {
  const { user, loading, logout } = useSession()
  const [route, navigate] = useHashRoute()

  // A role change can invalidate the current page (a student opening /accounts),
  // so fall back to chat rather than render something they cannot use.
  useEffect(() => {
    if (!user) return
    const allowed = NAV.filter((n) => n.show(user.role, user.is_staff)).map((n) => n.id)
    if (!allowed.includes(route)) navigate('home')
  }, [user, route, navigate])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-100">
        <Spinner label="Checking your session…" />
      </div>
    )
  }

  if (!user) {
    return (
      <Suspense fallback={<Spinner label="Loading…" />}>
        <Login />
      </Suspense>
    )
  }

  const items = NAV.filter((n) => n.show(user.role, user.is_staff))

  return (
    <div className="flex min-h-screen flex-col bg-slate-100">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-slate-900">AI Tutoring System</div>
              <div className="text-[11px] text-slate-400">
                {ROLE_LABEL[user.role] ?? user.role}
                {user.modules?.length > 0 && (
                  <span className="ml-1.5 font-mono text-slate-400">· {user.modules.join(', ')}</span>
                )}
              </div>
            </div>
          </div>

          <nav className="order-3 -mx-1 flex w-full items-center gap-1 overflow-x-auto px-1 sm:order-none sm:mx-0 sm:w-auto sm:px-0">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => navigate(item.id)}
                className={`flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                  route === item.id
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'
                }`}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden max-w-[10rem] truncate text-xs text-slate-500 sm:inline">
              {user.full_name || user.email}
            </span>
            <NotificationBell />
            <button
              type="button"
              onClick={() => void logout()}
              title="Sign out"
              className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">
        <Suspense fallback={<Spinner label="Loading view…" />}>
          {route === 'home' && <Home />}
          {route === 'chat' && <StudentChat />}
          {route === 'material' && <CourseMaterial />}
          {route === 'practice' && <Practice />}
          {route === 'tutor' && <TutorDashboard />}
          {route === 'content' && <Content />}
          {route === 'accounts' && <Accounts />}
          {route === 'admin' && <AdminDashboard />}
        </Suspense>
      </main>
    </div>
  )
}
