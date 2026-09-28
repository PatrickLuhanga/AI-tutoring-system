import {
  AlertCircle,
  GraduationCap,
  LayoutDashboard,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { setAccessToken } from '../../api/client'
import { useAuth } from '../../auth/context'
import { DEMO_LOGINS, isInstitutionalEmail } from '../../auth/institutional'
import { acquireMicrosoftToken, msalEnabled } from '../../auth/msal'
import type { UserRole } from '../../types'

const ROLE_ICON: Record<UserRole, ReactNode> = {
  student: <GraduationCap className="h-4 w-4" />,
  tutor: <Users className="h-4 w-4" />,
  admin: <LayoutDashboard className="h-4 w-4" />,
}

/**
 * Unified Login / Authentication view.
 *
 * Any valid `*@dut4life.ac.za` address signs in; the gateway provisions a
 * profile on first contact and the app routes the user to onboarding. The demo
 * shortcuts are just suggested addresses — they use the same dynamic flow, not
 * hard-coded profiles. When the Microsoft tenant is configured, the SSO button
 * runs an MSAL popup flow and forwards the verified token to the gateway.
 */
export default function LoginPage() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)

  async function signIn(address: string) {
    if (!isInstitutionalEmail(address)) {
      setError('Enter a valid DUT4life address, e.g. 22000000@dut4life.ac.za')
      return
    }
    setError(null)
    setPending(address)
    try {
      await login(address)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setPending(null)
    }
  }

  async function signInWithMicrosoft() {
    setError(null)
    setPending('microsoft')
    try {
      const session = await acquireMicrosoftToken()
      setAccessToken(session.accessToken)
      // Strict mode derives the address from the token; dev mode still needs one.
      const address = session.email ?? email
      if (!address) {
        setError('Microsoft sign-in did not return an e-mail address.')
        setPending(null)
        return
      }
      await login(address)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setPending(null)
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    void signIn(email)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-100 via-slate-100 to-blue-100 px-4 py-10">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl md:grid-cols-2">
        {/* ---------------------------------------------------------- Branding */}
        <div className="relative hidden flex-col justify-between bg-gradient-to-br from-blue-600 to-indigo-700 p-8 text-white md:flex">
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/30">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <span className="text-sm font-semibold">AI Tutoring System</span>
          </div>
          <div>
            <h1 className="text-2xl font-semibold leading-snug">Socratic hints, not answers.</h1>
            <p className="mt-3 text-sm text-blue-100">
              A hybrid RAG + multi-agent tutor for IPRT, PBDV, RESK and SPRI. Sign in with your
              DUT4life account to continue.
            </p>
          </div>
          <p className="text-xs text-blue-200">Identity federates with the DUT Microsoft tenant.</p>
        </div>

        {/* -------------------------------------------------------------- Form */}
        <div className="p-8">
          <h2 className="text-lg font-semibold text-slate-900">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">
            New here? We&apos;ll set up your profile on first sign-in.
          </p>

          <button
            type="button"
            onClick={() => void signInWithMicrosoft()}
            disabled={!msalEnabled || pending !== null}
            title={
              msalEnabled
                ? 'Sign in with your DUT Microsoft account'
                : 'Set VITE_AZURE_CLIENT_ID and VITE_AZURE_TENANT_ID to enable Microsoft SSO.'
            }
            className={`mt-5 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium transition disabled:cursor-not-allowed ${
              msalEnabled
                ? 'text-slate-700 hover:border-blue-300 hover:bg-blue-50/40'
                : 'text-slate-400'
            }`}
          >
            {pending === 'microsoft' ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <MicrosoftLogo />
            )}
            Sign in with Microsoft
          </button>

          <div className="my-5 flex items-center gap-3 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            <span className="h-px flex-1 bg-slate-200" />
            or
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          <form onSubmit={submit} className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                DUT4life email
              </span>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="22000000@dut4life.ac.za"
                  autoComplete="email"
                  className="w-full rounded-lg border border-slate-300 bg-white py-2.5 pl-9 pr-3 text-sm text-slate-800 shadow-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>
            </label>

            {error && (
              <div className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 ring-1 ring-rose-200">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={pending !== null}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
            >
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
              Continue
            </button>
          </form>

          {/* ------------------------------------------------ Demo shortcuts */}
          <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-4">
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 ring-1 ring-amber-200">
                Demo accounts
              </span>
              <span className="text-xs text-slate-400">Preview each capability</span>
            </div>
            <div className="mt-3 space-y-2">
              {DEMO_LOGINS.map((account) => (
                <button
                  key={account.role}
                  type="button"
                  disabled={pending !== null}
                  onClick={() => void signIn(account.email)}
                  className="flex w-full items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left transition hover:border-blue-300 hover:bg-blue-50/40 disabled:opacity-60"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                    {ROLE_ICON[account.role]}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-slate-800">
                      {account.title}
                    </span>
                    <span className="block truncate text-[11px] text-slate-400">
                      {account.email}
                    </span>
                  </span>
                  <span className="hidden text-[11px] text-slate-400 lg:block">
                    {account.blurb}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function MicrosoftLogo() {
  return (
    <svg viewBox="0 0 21 21" className="h-4 w-4" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}
