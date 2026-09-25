import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import {
  FEEDBACK_REASON_LABELS,
  FEEDBACK_REASON_TAGS,
  type FeedbackReasonTag,
  type FeedbackState,
} from '../types'

interface FeedbackControlsProps {
  sessionId: string
  messageId: string
  moduleId: string
  value?: FeedbackState
  onChange: (next: FeedbackState | undefined) => void
}

const chipBase =
  'rounded-full border px-3 py-1 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-60'

export default function FeedbackControls({
  sessionId,
  messageId,
  moduleId,
  value,
  onChange,
}: FeedbackControlsProps) {
  const [open, setOpen] = useState(value?.rating === -1 && !value?.submitted)

  async function send(rating: 1 | -1, reason_tag?: FeedbackReasonTag) {
    onChange({ rating, reason_tag })
    try {
      await api.feedback({ session_id: sessionId, message_id: messageId, module_id: moduleId, rating, reason_tag })
      onChange({ rating, reason_tag, submitted: true })
    } catch {
      // Keep the optimistic state; a real build would surface a toast here.
      onChange({ rating, reason_tag, submitted: true })
    }
  }

  function thumbsUp() {
    setOpen(false)
    void send(1)
  }

  function thumbsDown() {
    setOpen(true)
    if (!value?.reason_tag) {
      onChange({ rating: -1 })
    }
  }

  return (
    <div className="mt-3 border-t border-slate-200/80 pt-2.5">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Was this helpful?
        </span>
        <button
          type="button"
          onClick={thumbsUp}
          aria-pressed={value?.rating === 1}
          aria-label="Thumbs up"
          className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition ${
            value?.rating === 1
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-transparent text-slate-500 hover:border-slate-300 hover:bg-white hover:text-emerald-600'
          }`}
        >
          <ThumbsUp className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Helpful</span>
        </button>
        <button
          type="button"
          onClick={thumbsDown}
          aria-pressed={value?.rating === -1}
          aria-label="Thumbs down"
          className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition ${
            value?.rating === -1
              ? 'border-rose-300 bg-rose-50 text-rose-700'
              : 'border-transparent text-slate-500 hover:border-slate-300 hover:bg-white hover:text-rose-600'
          }`}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Not helpful</span>
        </button>
        {value?.submitted && (
          <span className="text-xs font-medium text-slate-400">Feedback recorded</span>
        )}
      </div>

      {open && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-slate-500">What went wrong?</span>
          {FEEDBACK_REASON_TAGS.map((tag) => {
            const active = value?.reason_tag === tag
            return (
              <button
                key={tag}
                type="button"
                onClick={() => void send(-1, tag)}
                className={`${chipBase} ${
                  active
                    ? 'border-rose-300 bg-rose-100 text-rose-700'
                    : 'border-slate-300 bg-white text-slate-600 hover:border-rose-300 hover:text-rose-600'
                }`}
              >
                {FEEDBACK_REASON_LABELS[tag]}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
