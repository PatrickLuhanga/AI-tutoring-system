import { GraduationCap, Loader2, LogIn, ShieldCheck, UserPlus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { authApi } from '../api/auth'
import { api } from '../api/client'
import { ThemeSegmented } from '../components/ThemeControl'
import { useSession } from '../state/session'
import type { AccountRole, Module, SignupPolicy } from '../types'

type Mode = 'login' | 'signup'

const ROLE_COPY: Record<string, { label: string; blurb: string }> = {
  student: { label: 'Student', blurb: 'Study with the tutor, read the notes, take practice tests.' },
  tutor: { label: 'Tutor', blurb: 'Work the help queue for the modules you teach.' },
  lecturer: { label: 'Lecturer', blurb: 'Everything a tutor can do, plus notes, questions and announcements.' },
  admin: { label: 'System Admin', blurb: 'Manage accounts and the whole system.' },
}

export default function Login() {
  const { login, signup } = useSession()
  const [mode, setMode] = useState<Mode>('login')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [policy, setPolicy] = useState<SignupPolicy | null>(null)
  const [modules, setModules] = useState<Module[]>([])

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState<AccountRole>('student')
  const [studentNumber, setStudentNumber] = useState('')
  const [chosen, setChosen] = useState<string[]>([])

  useEffect(() => {
    void authApi.policy().then(setPolicy).catch(() => setPolicy(null))
    void api.getModules().then(setModules).catch(() => setModules([]))
  }, [])

  // The email domain is enforced server-side; mirroring it here turns a
  // guaranteed 403 into an inline hint instead of a round trip.
  const domainHint = useMemo(() => {
    if (!policy || mode !== 'signup') return null
    if (role === 'student') return `Must be an @${policy.student_email_domain} address.`
    if (role === 'lecturer') return `Must be an @${policy.lecturer_email_domain} address.`
    return null
  }, [policy, mode, role])

  const needsStudentNumber = mode === 'signup' && role === 'student'
  const needsModules = mode === 'signup' && (role === 'tutor' || role === 'lecturer')

  function toggleModule(id: string) {
    setChosen((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]))
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      if (mode === 'login') {
        await login(email.trim(), password)
      } else {
        await signup({
          email: email.trim(),
          password,
          role,
          full_name: fullName.trim() || undefined,
          student_number: needsStudentNumber ? studentNumber.trim() : undefined,
          module_ids: needsModules ? chosen : undefined,
        })
        setNotice('Account created. You are signed in.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-full items-center justify-center bg-slate-100 px-4 py-10">
      {/* Available before sign-in on purpose: someone who lands on this page in
          a bright room and prefers dark should not have to create an account
          first to say so. */}
      <div className="absolute right-4 top-4">
        <ThemeSegmented />
      </div>
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-on-accent shadow-sm">
            <GraduationCap className="h-5 w-5" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-slate-900">AI Tutoring System</div>
            <div className="text-[11px] text-slate-500">Socratic tutor for DUT modules</div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1">
            {(['login', 'signup'] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m)
                  setError(null)
                  setNotice(null)
                }}
                className={`flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-sm font-medium transition ${
                  mode === m ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {m === 'login' ? <LogIn className="h-3.5 w-3.5" /> : <UserPlus className="h-3.5 w-3.5" />}
                {m === 'login' ? 'Sign in' : 'Register'}
              </button>
            ))}
          </div>

          {policy && !policy.allow_self_signup && mode === 'signup' && (
            <p className="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200">
              Registration is closed on this deployment. Existing accounts can still sign in.
            </p>
          )}

          <form onSubmit={submit} className="space-y-3">
            {mode === 'signup' && (
              <div>
                <span className="mb-1.5 block text-xs font-medium text-slate-600">I am a</span>
                <div className="grid grid-cols-2 gap-1.5">
                  {(Object.keys(ROLE_COPY) as AccountRole[]).map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setRole(r)}
                      className={`rounded-lg border px-2.5 py-2 text-left text-xs transition ${
                        role === r
                          ? 'border-blue-400 bg-blue-50 text-blue-800 ring-1 ring-blue-200'
                          : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                      }`}
                    >
                      {ROLE_COPY[r].label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
                  {ROLE_COPY[role].blurb}
                </p>
              </div>
            )}

            {mode === 'signup' && (
              <Field label="Full name">
                <input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                  className={inputClass}
                />
              </Field>
            )}

            <Field label="Email" hint={domainHint ?? undefined}>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder={mode === 'login' ? 'you@dut4life.ac.za' : undefined}
                className={inputClass}
              />
            </Field>

            {needsStudentNumber && (
              <Field label="Student number" hint="8 digits, e.g. 22000000">
                <input
                  value={studentNumber}
                  onChange={(e) => setStudentNumber(e.target.value)}
                  inputMode="numeric"
                  pattern="\d{8}"
                  maxLength={8}
                  placeholder="22000000"
                  className={inputClass}
                />
              </Field>
            )}

            {needsModules && (
              <Field label="Modules you teach" hint="Pick every module you are assigned to.">
                <div className="flex flex-wrap gap-1.5">
                  {modules.map((m) => {
                    const on = chosen.includes(m.module_id)
                    return (
                      <button
                        key={m.module_id}
                        type="button"
                        onClick={() => toggleModule(m.module_id)}
                        className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
                          on
                            ? 'border-blue-400 bg-blue-50 text-blue-800'
                            : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                        }`}
                      >
                        {m.module_id}
                      </button>
                    )
                  })}
                </div>
              </Field>
            )}

            <Field
              label="Password"
              hint={
                mode === 'signup' && role === 'admin'
                  ? 'The System Admin registration code.'
                  : policy && mode === 'signup'
                    ? `At least ${policy.min_password_length} characters.`
                    : undefined
              }
            >
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                className={inputClass}
              />
            </Field>

            {error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 ring-1 ring-rose-200">
                {error}
              </p>
            )}
            {notice && (
              <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700 ring-1 ring-emerald-200">
                {notice}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || (mode === 'signup' && policy !== null && !policy.allow_self_signup)}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 py-2.5 text-sm font-medium text-on-accent transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </button>
          </form>
        </div>

        <p className="mt-4 flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-500">
          <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />
          Sessions are held server-side, so signing out or suspending an account takes effect
          immediately. Email domains are enforced per role: students on the DUT4life student
          domain, lecturers on the academic staff domain, tutors unrestricted.
        </p>
      </div>
    </div>
  )
}

const inputClass =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100'

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  )
}
