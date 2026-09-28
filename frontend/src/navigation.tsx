import { GraduationCap, LayoutDashboard, Users } from 'lucide-react'
import type { ReactNode } from 'react'
import type { AuthUser, UserRole } from './types'

export type Route = 'student' | 'tutor' | 'admin'

/**
 * A nav entry is visible when the user holds any of its capability roles.
 * `admin` is listed on every entry because administrators can preview all
 * workspaces; `tutor` is a capability a student may also hold (dual role).
 */
export interface NavItem {
  id: Route
  label: string
  icon: ReactNode
  roles: UserRole[]
}

export const NAV: NavItem[] = [
  {
    id: 'student',
    label: 'Student Chat',
    icon: <GraduationCap className="h-4 w-4" />,
    roles: ['student', 'admin'],
  },
  {
    id: 'tutor',
    label: 'Tutor Dashboard',
    icon: <Users className="h-4 w-4" />,
    roles: ['tutor', 'admin'],
  },
  {
    id: 'admin',
    label: 'Admin Dashboard',
    icon: <LayoutDashboard className="h-4 w-4" />,
    roles: ['admin'],
  },
]

export const ROLE_LABEL: Record<UserRole, string> = {
  student: 'Student',
  tutor: 'Tutor',
  admin: 'Administrator',
}

export const ROLE_BADGE: Record<UserRole, string> = {
  student: 'bg-blue-50 text-blue-700 ring-blue-200',
  tutor: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  admin: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
}

/** True when the user's capabilities allow the given workspace. */
export function canAccess(route: Route, user: AuthUser): boolean {
  const item = NAV.find((entry) => entry.id === route)
  if (!item) return false
  return item.roles.some((role) => user.roles.includes(role))
}

/** Routes the user's capabilities allow. */
export function allowedRoutesFor(user: AuthUser): Route[] {
  return NAV.filter((item) => item.roles.some((role) => user.roles.includes(role))).map(
    (item) => item.id,
  )
}

/**
 * The default landing route: administrators land on the admin dashboard, a
 * tutor account on the tutor dashboard, everyone else on the student chat. A
 * dual-role *student* keeps the student chat as home and reaches the tutor
 * dashboard from the nav.
 */
export function homeRouteFor(user: AuthUser): Route {
  const preferred: Route =
    user.role === 'admin' ? 'admin' : user.role === 'tutor' ? 'tutor' : 'student'
  if (canAccess(preferred, user)) return preferred
  return allowedRoutesFor(user)[0] ?? 'student'
}
