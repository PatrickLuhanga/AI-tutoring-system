import { Monitor, Moon, Sun } from 'lucide-react'
import type { ThemeChoice } from '../state/theme'
import { useTheme } from '../state/theme'

const OPTIONS: Array<{ value: ThemeChoice; label: string; Icon: typeof Sun }> = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
]

/**
 * A three-way theme control: light, dark, or follow the system.
 *
 * A plain sun/moon toggle is the obvious thing to reach for, but it cannot
 * express "follow the system", which is what someone who has never thought about
 * themes actually wants and what a laptop user needs after moving it between a
 * lit desk and a dim room. The segmented control makes the third option
 * discoverable and keeps the current choice visible, rather than hidden in a
 * toggle's opposite.
 */
export function ThemeSegmented({ className = '' }: { className?: string }) {
  const { choice, setChoice, theme } = useTheme()

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className={`inline-flex gap-1 rounded-xl border border-line bg-surface-sunken p-1 ${className}`}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = choice === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setChoice(value)}
            title={
              value === 'system'
                ? `Follow the system${theme ? ` (currently ${theme})` : ''}`
                : `${label} theme`
            }
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
              active
                ? 'bg-accent text-on-accent shadow-sm'
                : 'text-ink-muted hover:bg-surface hover:text-ink'
            }`}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * The compact header affordance: one click to flip, with the current choice
 * reachable by clicking again through to "system".
 */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggle, choice } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <button
      type="button"
      onClick={toggle}
      title={`Switch to ${next} theme${choice === 'system' ? ' (following the system)' : ''}`}
      aria-label={`Switch to ${next} theme`}
      className={`flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-surface text-ink-muted transition hover:bg-surface-raised hover:text-ink ${className}`}
    >
      {theme === 'dark' ? (
        <Moon className="h-4 w-4" aria-hidden />
      ) : (
        <Sun className="h-4 w-4" aria-hidden />
      )}
    </button>
  )
}