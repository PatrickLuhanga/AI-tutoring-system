/**
 * Institutional (DUT4life) address helpers and demo sign-in shortcuts.
 *
 * There are no hard-coded profiles here: a demo shortcut is just a suggested
 * address that goes through the same dynamic `POST /api/auth/login` flow as a
 * typed address. Profiles are provisioned on first contact.
 */

import type { UserRole } from '../types'

export const DUT4LIFE_DOMAIN = 'dut4life.ac.za'

/** True when an address follows the institutional Microsoft/DUT4life pattern. */
export function isInstitutionalEmail(email: string): boolean {
  return /^[^\s@]+@dut4life\.ac\.za$/i.test(email.trim())
}

export interface DemoLogin {
  role: UserRole
  title: string
  email: string
  blurb: string
}

/** Suggested addresses for quickly previewing each capability. */
export const DEMO_LOGINS: DemoLogin[] = [
  {
    role: 'student',
    title: 'Student',
    email: 'student@dut4life.ac.za',
    blurb: 'Socratic chat with saved history',
  },
  {
    role: 'tutor',
    title: 'Tutor',
    email: 'tutor.dev@dut4life.ac.za',
    blurb: 'Scoped to Software Development (IPRT, PBDV)',
  },
  {
    role: 'admin',
    title: 'Administrator',
    email: 'admin.system@dut4life.ac.za',
    blurb: 'Global oversight, roles and tutor grants',
  },
]
