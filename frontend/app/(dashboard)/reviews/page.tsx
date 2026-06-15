'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import { AlertExplanation } from '@/components/alert-explanation'
import { Badge } from '@/components/badge'
import { JsonBlock } from '@/components/json-block'
import { isClassifierErrorVerdict, timeAgo, verdictBadgeColor, verdictBadgeLabel } from '@/lib/utils'

type ReviewSummary = {
  id: string
  event_id: string
  verdict_id: string
  session_id: string
  mad_code: string
  verdict_status: string
  status: string
  created_at: string
}

type ReviewDetail = ReviewSummary & {
  event_payload?: any
  classification?: string
  reasoning?: string
}

export default function ReviewsPage() {
  const [pending, setPending] = useState<ReviewSummary[]>([])
  const [selectedID, setSelectedID] = useState<string | null>(null)
  const [detail, setDetail] = useState<ReviewDetail | null>(null)
  const [actionStatus, setActionStatus] = useState<'idle' | 'submitting' | 'error'>('idle')
  const [error, setError] = useState('')

  // Inline derivation: when nothing is explicitly chosen (or the
  // chosen review has just been resolved away), fall through to the
  // first pending row. Removes the "Select a review on the left"
  // dead-card and means a fresh load lands straight on the event.
  const activeID =
    selectedID && pending.some(r => r.id === selectedID)
      ? selectedID
      : pending[0]?.id ?? null

  function refresh() {
    api('/api/reviews?status=pending')
      .then(r => setPending(r.data?.reviews || []))
      .catch(() => setPending([]))
  }

  // Poll every 4s so new pending reviews appear and resolved ones
  // disappear without a manual refresh. /api/reviews?status=pending is
  // paginated and cheap, no joins. activeID is derived inline so the
  // selection survives each tick when the chosen review still exists.
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (!activeID) {
      setDetail(null)
      return
    }
    api(`/api/reviews/${activeID}`)
      .then(r => setDetail(r.data || null))
      .catch(() => setDetail(null))
  }, [activeID])

  async function resolve(action: 'approve' | 'reject') {
    if (!activeID) return
    setActionStatus('submitting')
    setError('')
    try {
      await api(`/api/reviews/${activeID}/${action}`, { method: 'POST' })
      setSelectedID(null)
      setActionStatus('idle')
      refresh()
    } catch (e: any) {
      setActionStatus('error')
      setError(e?.message || 'Action failed')
    }
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h2 className="text-lg font-semibold text-ink">Reviews</h2>
        <span className="text-[12.5px] text-ink-3">
          {pending.length} pending
        </span>
      </div>

      {pending.length === 0 ? (
        <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
          <p className="text-sm text-ink mb-1">Nothing waiting on you</p>
          <p className="text-xs text-ink-3 max-w-md mx-auto">
            When policy mode is HITL and a flagged verdict or fail-closed classifier error lands in scope, the SDK pauses and the event appears here.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[20rem,1fr] gap-4">
          <ul className="space-y-2">
            {pending.map(r => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setSelectedID(r.id)}
                  className={`w-full text-left bg-surface-raised border rounded-lg p-3 transition-colors ${
                    activeID === r.id ? 'border-ink/30' : 'border-surface-border hover:border-ink/20'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Badge label={verdictBadgeLabel(r)} className={verdictBadgeColor(r)} />
                    <span className="text-xs text-ink-3 font-mono ml-auto">{timeAgo(r.created_at)}</span>
                  </div>
                  <div className="font-mono text-[11px] text-ink-3 truncate">
                    session {r.session_id.slice(0, 24)}
                  </div>
                </button>
              </li>
            ))}
          </ul>

          <div>
            {!detail ? (
              <div className="text-sm text-ink-3">Loading...</div>
            ) : (
              <div className="bg-surface-raised border border-surface-border rounded-lg p-5 space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Badge label={verdictBadgeLabel(detail)} className={verdictBadgeColor(detail)} />
                  <Link
                    href={`/sessions/${detail.session_id}#event-${detail.event_id}`}
                    className="text-xs text-ink-2 hover:underline font-mono ml-auto"
                  >
                    Open in timeline &rarr;
                  </Link>
                </div>

                {isClassifierErrorVerdict(detail) ? (
                  <div className="border border-surface-border rounded-lg p-3 bg-surface-overlay/30">
                    <p className="text-sm text-ink">Classifier error</p>
                    <p className="text-xs text-ink-3 mt-1">
                      The classifier did not return a MAD code. Approving resumes the paused SDK action; rejecting returns a blocked tool response.
                    </p>
                    {detail.reasoning && (
                      <p className="text-xs text-ink-3 font-mono break-words mt-3">
                        {detail.reasoning}
                      </p>
                    )}
                  </div>
                ) : (
                  <AlertExplanation madCode={detail.mad_code} />
                )}

                <div>
                  <p className="text-[12.5px] text-ink-3 mb-1">Event payload</p>
                  <JsonBlock value={detail.event_payload} />
                </div>

                <div className="flex flex-wrap items-center gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => resolve('approve')}
                    disabled={actionStatus === 'submitting'}
                    className="px-5 py-2 bg-surface-overlay border border-surface-border text-ink text-sm font-mono tracking-wider rounded hover:bg-ink/5 disabled:opacity-50"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => resolve('reject')}
                    disabled={actionStatus === 'submitting'}
                    className="px-5 py-2 bg-danger/20 border border-danger text-ink text-sm font-mono tracking-wider rounded hover:bg-danger/30 disabled:opacity-50"
                  >
                    Reject
                  </button>
                  {actionStatus === 'error' && (
                    <span className="text-xs text-ink font-mono">{error}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
