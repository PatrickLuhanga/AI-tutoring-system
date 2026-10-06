import {
  BookOpen,
  ChevronRight,
  ClipboardList,
  Database,
  GraduationCap,
  Layers,
  Lightbulb,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Palette,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { ThemeSegmented } from '../components/ThemeControl'
import { useTheme } from '../state/theme'
import { useSession } from '../state/session'
import type { Module } from '../types'

/**
 * The student's landing page: what this tool is, how it finds material, how to
 * get a useful answer out of it, and what to do when it does not know.
 *
 * It answers the questions a student actually has on arrival - "is this just
 * ChatGPT?", "why is it quoting a Flask textbook in a web-development module?",
 * "what happens if I ask for the answer?" - because those are the questions that
 * decide whether the tutor is trusted or dismissed.
 */
export default function Home() {
  const { user } = useSession()
  const { choice, theme } = useTheme()
  const [modules, setModules] = useState<Module[]>([])

  // `GET /api/modules` already carries the ingested document/chunk counts, which
  // match the Course Material viewer. No per-module file fetch is needed.
  useEffect(() => {
    void api.getModules().then(setModules).catch(() => setModules([]))
  }, [])

  const first = user?.full_name?.split(' ')[0]

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
      {/* Hero */}
      <section className="mb-8">
        <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-blue-600">
          {user?.role === 'student' ? 'Student workspace' : 'Socratic tutor'}
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          {first ? `Welcome back, ${first}.` : 'Study with the tutor.'}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
          This is a Socratic tutor for your DUT modules. It answers from{' '}
          <strong>your lecture material</strong> and, by design, will not simply hand you the
          finished answer &mdash; it gives you the next question or hint that unsticks you. It
          shows you which notes it used, so you can check its work.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <a
            href="#/chat"
            className="flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-on-accent transition hover:bg-blue-700"
          >
            <MessageSquare className="h-4 w-4" />
            Ask the tutor
            <ChevronRight className="h-3.5 w-3.5" />
          </a>
          <a
            href="#/material"
            className="flex items-center gap-1.5 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <BookOpen className="h-4 w-4" />
            Browse the material
          </a>
          <a
            href="#/practice"
            className="flex items-center gap-1.5 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <ClipboardList className="h-4 w-4" />
            Take a practice test
          </a>
        </div>
      </section>

      {/* How retrieval works */}
      <Section
        icon={<Database className="h-4 w-4" />}
        title="How it finds your material"
        lede="So you know what it does and does not know."
      >
        <ol className="space-y-3">
          <Step
            n={1}
            title="Your question is turned into a vector"
            body="Your message is embedded into a list of 384 numbers that captures meaning rather than keywords. That is why asking &ldquo;how do I stop it being made again&rdquo; can match notes titled &ldquo;private constructor&rdquo;."
          />
          <Step
            n={2}
            title="It searches only your module, and only what is close enough"
            body="Every lecture note, slide deck, exercise and worked example for the module you picked is searched. Anything further than a strict similarity threshold is discarded, so a weakly-related passage is never presented as if it answered you."
          />
          <Step
            n={3}
            title="What it uses becomes your Sources list"
            body="The notes the reply was built from are listed underneath it, each linking to the exact section. If the badge says the material did not match, the answer is general knowledge, not your course's."
          />
          <Step
            n={4}
            title="If nothing matches, a human is asked instead"
            body="When no material is close enough, the question is queued for your tutor or lecturer, who writes a proper answer that later students are grounded in."
          />
        </ol>
      </Section>

      {/* How to use it well */}
      <Section
        icon={<Target className="h-4 w-4" />}
        title="How to get a useful answer"
        lede="The tutor is only as useful as the question, and it works very differently from a search box."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Tip
            icon={<MessageSquare className="h-3.5 w-3.5" />}
            good="Paste the whole error, not a summary"
            bad="My code does not work"
            why="The exception type and the line it names are what make retrieval precise. Those words are what match the exercise notes."
          />
          <Tip
            icon={<Layers className="h-3.5 w-3.5" />}
            good="Pick the module the topic belongs to"
            bad="Leaving it on last week's module"
            why="Retrieval is scoped to one module. A Research Skills question asked in the web module finds web notes, because that is all it is allowed to look at."
          />
          <Tip
            icon={<Lightbulb className="h-3.5 w-3.5" />}
            good="Answer the question it asks you back"
            bad="Asking for the finished code again"
            why="The progression is deliberate. Each reply earns the next level of help, so engaging with the hint is what unlocks the explanation."
          />
          <Tip
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            good="Keep one thread per topic"
            bad="Switching topics mid-conversation"
            why="It tracks how far it has taken you. A fresh thread for a fresh problem means it starts again at the gentlest level."
          />
        </div>
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-600 ring-1 ring-slate-200">
          <strong className="text-slate-700">If it refuses or deflects, that is working as
          intended.</strong> Asking for the complete solution is treated as a request to skip the
          part where you learn, and the tutor will keep you on the loop boundary instead.
        </p>
      </Section>

      {/* Benefits */}
      <Section
        icon={<Sparkles className="h-4 w-4" />}
        title="What you get from this"
        lede="Four things a general-purpose chatbot will not do."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Benefit
            icon={<BookOpen className="h-3.5 w-3.5" />}
            title="Answers tied to your notes"
            body="Citeable to a section you can open, instead of a plausible-sounding guess from anywhere on the internet."
          />
          <Benefit
            icon={<ShieldCheck className="h-3.5 w-3.5" />}
            title="No hallucinated citations"
            body="A source is only shown if it was genuinely retrieved. It cannot invent a reference to look authoritative."
          />
          <Benefit
            icon={<GraduationCap className="h-3.5 w-3.5" />}
            title="Practice on the real exam scope"
            body="Randomised tests drawn from your module's question bank, so you find the gaps before the assessment does."
          />
          <Benefit
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            title="Tutors see where you are stuck"
            body="Questions the material could not answer reach your tutor automatically, so the class gets better notes rather than one student quietly struggling."
          />
        </div>
      </Section>

      {/* Modules */}
      <section className="mt-8">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
          <BookOpen className="h-4 w-4 text-slate-400" />
          Your material
        </h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {modules.map((m) => (
            <a
              key={m.module_id}
              href="#/material"
              className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-sm transition hover:border-blue-300 hover:shadow"
            >
              <span className="min-w-0">
                <span className="block text-sm font-medium text-slate-800">
                  {m.module_id} · {m.module_name}
                </span>
                <span className="block text-[11px] text-slate-400">
                  {m.document_count != null
                    ? `${m.document_count} document${m.document_count === 1 ? '' : 's'}`
                    : m.course_code}
                  {m.chunk_count ? ` · ${m.chunk_count.toLocaleString()} chunks` : ''}
                </span>
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" />
            </a>
          ))}
        </div>
      </section>

      {/* Appearance */}
      <section className="mt-8">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Palette className="h-4 w-4 text-slate-400" />
          Appearance
        </h2>
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-800">Colour theme</p>
              <p className="mt-0.5 text-xs text-slate-500">
                {choice === 'system'
                  ? `Following your device, which is currently set to ${theme}.`
                  : `Always ${theme}, whatever your device prefers.`}
              </p>
            </div>
            <ThemeSegmented />
          </div>
        </div>
      </section>
    </div>
  )
}

function Section({
  icon,
  title,
  lede,
  children,
}: {
  icon: React.ReactNode
  title: string
  lede: string
  children: React.ReactNode
}) {
  return (
    <section className="mb-8">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
          {icon}
        </span>
        {title}
      </h2>
      <p className="mb-3 pl-8 text-xs text-slate-500">{lede}</p>
      <div className="pl-8">{children}</div>
    </section>
  )
}

function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">
        {n}
      </span>
      <span>
        <span className="block text-sm font-medium text-slate-800">{title}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-slate-600">{body}</span>
      </span>
    </li>
  )
}

function Tip({
  icon,
  good,
  bad,
  why,
}: {
  icon: React.ReactNode
  good: string
  bad: string
  why: string
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <p className="flex items-start gap-1.5 text-xs font-medium text-emerald-700">
        <span className="mt-px text-emerald-500">{icon}</span>
        {good}
      </p>
      <p className="mt-1.5 pl-5 text-xs text-rose-600 line-through decoration-rose-300">{bad}</p>
      <p className="mt-1.5 pl-5 text-[11px] leading-relaxed text-slate-500">{why}</p>
    </div>
  )
}

function Benefit({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode
  title: string
  body: string
}) {
  return (
    <div className="flex gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
        {icon}
      </span>
      <span>
        <span className="block text-sm font-medium text-slate-800">{title}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-slate-600">{body}</span>
      </span>
    </div>
  )
}
