import { Bell, CheckCheck, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'

interface Inbox {
  count: number
  unread: number
  notifications: Array<{
    notification_id: number
    title: string
    body: string
    audience: string
    module_id: string | null
    is_read: boolean
    created_at: string | null
  }>
}

/** The header bell: unread count plus a dropdown of the signed-in account's inbox. */
export default function NotificationBell() {
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      setInbox(await api.getNotifications())
    } catch {
      // A signed-out or failing inbox must not break the header.
      setInbox(null)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  async function markAll() {
    const ids = (inbox?.notifications ?? []).filter((n) => !n.is_read).map((n) => n.notification_id)
    if (!ids.length) return
    try {
      await api.markNotificationsRead(ids)
      await load()
    } catch {
      /* leave the badge as-is rather than pretending it worked */
    }
  }

  const unread = inbox?.unread ?? 0

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Notifications"
        className="relative flex h-8 w-8 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 transition hover:bg-slate-50"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-80 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Notifications
            </span>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => void markAll()}
                className="flex items-center gap-1 text-[11px] font-medium text-blue-600 hover:text-blue-700"
              >
                <CheckCheck className="h-3 w-3" />
                Mark all read
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {!inbox || inbox.count === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-slate-500">
                Nothing yet. Announcements from your lecturer appear here.
              </p>
            ) : (
              inbox.notifications.map((n) => (
                <div
                  key={n.notification_id}
                  className={`border-b border-slate-50 px-3 py-2.5 ${
                    n.is_read ? '' : 'bg-blue-50/50'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <div className="flex-1">
                      <p className="text-sm font-medium text-slate-800">{n.title}</p>
                      <p className="mt-0.5 text-xs leading-relaxed text-slate-600">{n.body}</p>
                      <p className="mt-1 text-[10px] text-slate-400">
                        {n.module_id ? `${n.module_id} · ` : ''}
                        {n.created_at ? new Date(n.created_at).toLocaleString() : ''}
                      </p>
                    </div>
                    {!n.is_read && (
                      <button
                        type="button"
                        onClick={async () => {
                          await api.markNotificationsRead([n.notification_id])
                          await load()
                        }}
                        title="Mark as read"
                        className="shrink-0 rounded p-0.5 text-slate-300 transition hover:text-slate-500"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
