'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { api } from '@/lib/api'
import { Badge } from '@/components/badge'
import { JsonBlock } from '@/components/json-block'
import { verdictBadgeColor, verdictBadgeLabel, timeAgo } from '@/lib/utils'

type Verdict = {
  id: string
  mad_code: string
  classification: string
  verdict_status: string
}

type Entry = {
  id: string
  event_type: string
  run_id: string
  agent_id: string
  agent_name: string
  payload: any
  created_at: string
  verdict?: Verdict
}

type TimelinePayload = {
  session_id: string
  entries: Entry[]
}

export default function SessionTimelinePage() {
  const { session_id } = useParams<{ session_id: string }>()
  const [data, setData] = useState<TimelinePayload | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    if (!session_id) return
    api(`/api/sessions/${session_id}/timeline`)
      .then(r => setData(r.data || { session_id, entries: [] }))
      .catch(() => setData({ session_id, entries: [] }))
  }, [session_id])

  // When the URL carries #event-<id>, scroll the matching entry into
  // view and auto-expand it. Mirrors the deep-link pattern used by the
  // Discord notifications.
  useEffect(() => {
    if (!data?.entries.length) return
    const hash = typeof window !== 'undefined' ? window.location.hash : ''
    if (!hash.startsWith('#event-')) return
    const id = hash.slice('#event-'.length)
    if (data.entries.some(e => e.id === id)) {
      setExpanded(id)
      requestAnimationFrame(() => {
        const el = document.getElementById(`event-${id}`)
        el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
    }
  }, [data])

  if (!data) return <div className="text-sm text-ink-3">Loading...</div>

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-1">Session timeline</h2>
      <p className="font-mono text-xs text-ink-3 mb-6 break-all">{data.session_id}</p>

      {data.entries.length === 0 ? (
        <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
          <p className="text-sm text-ink mb-1">No events for this session</p>
          <p className="text-xs text-ink-3 max-w-md mx-auto">
            Either the session id is unknown or its events have aged out of the dashboard's view window.
          </p>
        </div>
      ) : (
        <ol className="space-y-2">
          {data.entries.map(entry => {
            const open = expanded === entry.id
            return (
              <li
                key={entry.id}
                id={`event-${entry.id}`}
                className="bg-surface-raised border border-surface-border rounded-lg overflow-hidden scroll-mt-16"
              >
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : entry.id)}
                  className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-surface-overlay/40 transition-colors"
                >
                  <span className="text-ink-3 text-xs font-mono select-none mt-0.5">{open ? '▾' : '▸'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <Badge label={entry.event_type.replace('EVENT_TYPE_', '').toUpperCase()} className="bg-surface-overlay text-ink-3" />
                      {entry.verdict && (
                        <Badge label={verdictBadgeLabel(entry.verdict)} className={verdictBadgeColor(entry.verdict)} />
                      )}
                      <span className="text-xs text-ink-3 font-mono ml-auto">{timeAgo(entry.created_at)}</span>
                    </div>
                    <div className="text-sm text-ink/80 truncate">
                      {entry.agent_name || entry.agent_id || <span className="text-ink-3">-</span>}
                    </div>
                    <div className="text-[11px] font-mono text-ink-3 truncate">
                      run {entry.run_id?.slice(0, 8) || '-'}
                    </div>
                  </div>
                </button>

                {open && (
                  <div className="border-t border-surface-border bg-surface-overlay/20 px-4 py-3 space-y-3">
                    {entry.verdict && (
                      <div>
                        <p className="text-[12.5px] text-ink-3 mb-1">Verdict</p>
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
                          <Badge label={verdictBadgeLabel(entry.verdict)} className={verdictBadgeColor(entry.verdict)} />
                        </div>
                      </div>
                    )}
                    <div>
                      <p className="text-[12.5px] text-ink-3 mb-1">Payload</p>
                      <JsonBlock value={entry.payload} />
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
