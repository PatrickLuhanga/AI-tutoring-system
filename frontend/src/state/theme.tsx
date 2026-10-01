import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * Light / dark / "follow the system" theme, applied as `data-theme` on <html>.
 *
 * The choice is stored in localStorage and re-applied before React mounts, via
 * the inline script in index.html. Doing it only in an effect would mean a
 * flash of the light theme on every load for anyone who chose dark - which is
 * the single most common way a dark mode gets abandoned.
 *
 * The resolved theme is also written to `color-scheme`, so the browser's own
 * form controls, scrollbars and the canvas behind the page follow suit.
 */

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'dut-theme'

interface ThemeContextValue {
  /** What the user picked, which may be 'system'. */
  choice: ThemeChoice
  /** What is actually on screen. Never 'system'. */
  theme: ResolvedTheme
  setChoice: (choice: ThemeChoice) => void
  /** Flips between light and dark, resolving 'system' first. */
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStoredChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // Private browsing, or storage disabled. Fall through to the default.
  }
  return 'system'
}

function prefersDark(): boolean {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false
}

function resolve(choice: ThemeChoice): ResolvedTheme {
  return choice === 'system' ? (prefersDark() ? 'dark' : 'light') : choice
}

function apply(theme: ResolvedTheme): void {
  const root = document.documentElement
  root.dataset.theme = theme
  // Keeps the browser's own UI - scrollbars, form controls, the page canvas -
  // in step. Without this a dark app sits on a white scrollbar.
  root.style.colorScheme = theme
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readStoredChoice)
  const [theme, setTheme] = useState<ResolvedTheme>(() => resolve(readStoredChoice()))

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // A theme that cannot be remembered is still applied for this session.
    }
    const resolved = resolve(next)
    setTheme(resolved)
    apply(resolved)
  }, [])

  const toggle = useCallback(() => {
    // Toggle away from what is on screen, not from what was last clicked: with
    // 'system' picked, the first press should go to the opposite of the
    // system appearance, not get stuck re-deriving it.
    setChoice(theme === 'dark' ? 'light' : 'dark')
  }, [theme, setChoice])

  // Track the system preference, but only while 'system' is the active choice -
  // otherwise a desktop switching to night mode would override a deliberate pick.
  useEffect(() => {
    if (choice !== 'system' || typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      const next = query.matches ? 'dark' : 'light'
      setTheme(next)
      apply(next)
    }
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [choice])

  // Re-apply on mount in case the inline script in index.html did not run
  // (client-side navigation into a cached document, or storage cleared).
  useEffect(() => {
    apply(theme)
  }, [theme])

  const value = useMemo<ThemeContextValue>(
    () => ({ choice, theme, setChoice, toggle }),
    [choice, theme, setChoice, toggle],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used inside a <ThemeProvider>.')
  }
  return context
}