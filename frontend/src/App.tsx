import { Loader2 } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import AuthProvider from './auth/AuthProvider'
import { useAuth } from './auth/context'
import AppShell from './components/AppShell'
import { allowedRoutesFor, homeRouteFor, type Route } from './navigation'
import LoginPage from './pages/auth/LoginPage'
import OnboardingPage from './pages/auth/OnboardingPage'

// One lazy chunk per role so a student never downloads recharts and an admin
// never downloads the syntax highlighter.
const StudentChat = lazy(() => import('./pages/student/StudentChat'))
const TutorDashboard = lazy(() => import('./pages/tutor/TutorDashboard'))
const AdminDashboard = lazy(() => import('./pages/admin/AdminDashboard'))

/**
 * Routes the signed-in user through onboarding, then to their role's workspace.
 *
 * Unauthenticated visitors get the unified login screen. A freshly provisioned
 * profile is sent to onboarding to capture a name + enrolled modules. The
 * requested route is clamped to the capability set on every render, so a role
 * change can never leave a stale (unauthorized) view on screen.
 */
function Workspace() {
  const { user } = useAuth()
  const [route, setRoute] = useState<Route>(() => (user ? homeRouteFor(user) : 'student'))

  if (!user) {
    return <LoginPage />
  }

  if (user.needs_onboarding) {
    return <OnboardingPage />
  }

  const allowed = allowedRoutesFor(user)
  const active = allowed.includes(route) ? route : homeRouteFor(user)

  return (
    <AppShell route={active} onNavigate={setRoute}>
      <Suspense
        fallback={
          <div className="flex flex-1 items-center justify-center text-slate-400">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading view…
          </div>
        }
      >
        {active === 'student' && <StudentChat />}
        {active === 'tutor' && <TutorDashboard />}
        {active === 'admin' && <AdminDashboard />}
      </Suspense>
    </AppShell>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Workspace />
    </AuthProvider>
  )
}
