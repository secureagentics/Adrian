// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import { AlertExplanation } from '@/components/alert-explanation'
import { Badge } from '@/components/badge'
import { JsonBlock } from '@/components/json-block'
import { Pagination } from '@/components/pagination'
import { madBadgeColor, timeAgo } from '@/lib/utils'
import { TimeRange, sinceForRange, TimeRangeSelect } from '@/components/time-range'

type EventRow = {
  id: string
  session_id: string
  agent_id: string
  // agent_name is the customer-facing label (agent_profiles.name).
  // Empty string for events stamped before migration 015's backfill -
  // we render an em-dash in that case rather than a placeholder.
  agent_name: string
  event_type: string
  run_id: string
  created_at: string
  verdict?: EventVerdict
}

type EventVerdict = {
  id: string
  mad_code: string
  classification: string
  latency_ms?: number | null
}

type EventDetail = EventRow & {
  payload: any
  tokens_used: number
  verdict?: EventVerdict | null
}

export default function EventsPage() {
  const [data, setData] = useState<{ events: EventRow[]; total: number }>({ events: [], total: 0 })
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ event_type: '', session_id: '', min_mad: '' })
  const [range, setRange] = useState<TimeRange>('24h')
  const [customSince, setCustomSince] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const since = useMemo(() => sinceForRange(range, customSince), [range, customSince])

  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), per_page: '20' })
    if (filters.event_type) params.set('event_type', filters.event_type)
    if (filters.session_id) params.set('session_id', filters.session_id)
    if (filters.min_mad) params.set('min_mad', filters.min_mad)
    if (since) params.set('since', since)
    api(`/api/events?${params}`)
      .then(r => setData(r.data || { events: [], total: 0 }))
      .catch(() => {})
  }, [page, filters, since])

  const isEmpty = !data.events.length

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h2 className="text-lg font-semibold text-ink">Events</h2>
        <span className="text-[12.5px] text-ink-3">
          {(data.total || 0).toLocaleString()} total
        </span>
      </div>

      <div className="flex flex-wrap gap-3 mb-4 items-center">
        <input
          placeholder="Filter by session ID..."
          value={filters.session_id}
          onChange={e => { setFilters(f => ({ ...f, session_id: e.target.value })); setPage(1) }}
          className="px-3 py-1.5 border border-surface-border rounded text-sm w-full sm:w-64 bg-surface-overlay"
        />
        <select
          value={filters.event_type}
          onChange={e => { setFilters(f => ({ ...f, event_type: e.target.value })); setPage(1) }}
          className="px-3 py-1.5 border border-surface-border rounded text-sm bg-surface-overlay"
        >
          <option value="">All types</option>
          <option value="llm">LLM</option>
          <option value="tool">Tool</option>
        </select>
        <select
          value={filters.min_mad}
          onChange={e => { setFilters(f => ({ ...f, min_mad: e.target.value })); setPage(1) }}
          className="px-3 py-1.5 border border-surface-border rounded text-sm bg-surface-overlay"
          title="Restrict to events whose verdict was at least this severity. Useful for finding flagged events that didn't trigger an HITL hold (post-execution or non-tool-call)."
        >
          <option value="">All severities</option>
          <option value="M2">M2+</option>
          <option value="M3">M3+</option>
          <option value="M4">M4 only</option>
        </select>
        <TimeRangeSelect
          value={range}
          customSince={customSince}
          onChange={(r, c) => { setRange(r); if (c !== undefined) setCustomSince(c); setPage(1) }}
        />
      </div>

      {isEmpty ? (
        <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
          <p className="text-sm text-ink mb-1">No events in this window</p>
          <p className="text-xs text-ink-3 max-w-md mx-auto">
            Events arrive as your SDK forwards LLM and tool calls. Try a wider time range, or run an
            instrumented agent to see traffic appear here.
          </p>
        </div>
      ) : (
        <>
          <div className="hidden md:block bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-ink-3 border-b border-surface-border bg-surface-overlay/50">
                  <th className="px-4 py-2.5 w-6" />
                  <th className="px-4 py-2.5 text-[13px] font-medium">Timestamp</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Agent</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Session</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Type</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Severity</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Run ID</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map(e => (
                  <EventRow
                    key={e.id}
                    event={e}
                    open={expanded === e.id}
                    onToggle={() => setExpanded(expanded === e.id ? null : e.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="md:hidden space-y-2">
            {data.events.map(e => (
              <EventCard
                key={e.id}
                event={e}
                open={expanded === e.id}
                onToggle={() => setExpanded(expanded === e.id ? null : e.id)}
              />
            ))}
          </div>

          <Pagination page={page} perPage={20} total={data.total || 0} onChange={setPage} />
        </>
      )}
    </div>
  )
}

function EventRow({ event, open, onToggle }: { event: EventRow; open: boolean; onToggle: () => void }) {
  const [detail, setDetail] = useState<EventDetail | null | undefined>(undefined)

  useEffect(() => {
    if (!open || detail !== undefined) return
    api<{ data: EventDetail }>(`/api/events/${event.id}`)
      .then(r => setDetail(r.data ?? null))
      .catch(() => setDetail(null))
  }, [open, event.id, detail])

  return (
    <>
      <tr
        className="border-b border-surface-border/50 table-row-hover cursor-pointer"
        onClick={onToggle}
      >
        <td className="px-4 py-2.5 text-ink-3 text-xs font-mono select-none w-6">
          {open ? '▾' : '▸'}
        </td>
        <td className="px-4 py-2.5 text-ink-3 text-xs font-mono">{timeAgo(event.created_at)}</td>
        <td className="px-4 py-2.5 text-xs text-ink/80">
          {event.agent_name || <span className="text-ink-3">-</span>}
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-ink-2" onClick={e => e.stopPropagation()}>
          <Link href={`/sessions/${event.session_id}`} className="hover:underline">
            {event.session_id?.slice(0, 20)}
          </Link>
        </td>
        <td className="px-4 py-2.5">
          <Badge label={event.event_type?.replace('EVENT_TYPE_', '')} className="bg-surface-overlay text-ink-3" />
        </td>
        <td className="px-4 py-2.5">
          <SeverityBadge madCode={event.verdict?.mad_code} />
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-ink-3">{event.run_id?.slice(0, 8)}</td>
      </tr>

      {open && (
        <tr className="border-b border-surface-border/50 bg-surface-overlay/20">
          <td />
          <td colSpan={6} className="px-4 py-4">
            <ExpandedDetail event={event} detail={detail} />
          </td>
        </tr>
      )}
    </>
  )
}

function EventCard({ event, open, onToggle }: { event: EventRow; open: boolean; onToggle: () => void }) {
  const [detail, setDetail] = useState<EventDetail | null | undefined>(undefined)

  useEffect(() => {
    if (!open || detail !== undefined) return
    api<{ data: EventDetail }>(`/api/events/${event.id}`)
      .then(r => setDetail(r.data ?? null))
      .catch(() => setDetail(null))
  }, [open, event.id, detail])

  return (
    <div className="bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-surface-overlay/40 transition-colors"
      >
        <span className="text-ink-3 text-xs font-mono select-none mt-0.5">{open ? '▾' : '▸'}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Badge label={event.event_type?.replace('EVENT_TYPE_', '')} className="bg-surface-overlay text-ink-3" />
            <SeverityBadge madCode={event.verdict?.mad_code} />
            <span className="text-xs text-ink-3 font-mono ml-auto">{timeAgo(event.created_at)}</span>
          </div>
          <div className="text-sm text-ink/80 truncate">
            {event.agent_name || <span className="text-ink-3">-</span>}
          </div>
          <div className="text-[11px] font-mono text-ink-3 truncate">
            session {event.session_id?.slice(0, 20)} · run {event.run_id?.slice(0, 8)}
          </div>
        </div>
      </button>

      {open && (
        <div className="border-t border-surface-border bg-surface-overlay/20 px-4 py-3">
          <ExpandedDetail event={event} detail={detail} />
        </div>
      )}
    </div>
  )
}

function SeverityBadge({ madCode }: { madCode?: string }) {
  const label = severityLabel(madCode)
  return (
    <Badge label={label} className={`${madBadgeColor(madCode || '')}`} />
  )
}

function severityLabel(madCode?: string): string {
  if (!madCode) return 'Unknown'
  if (madCode.startsWith('M4')) return 'Critical'
  if (madCode.startsWith('M3')) return 'High'
  if (madCode.startsWith('M2')) return 'Medium'
  if (madCode.startsWith('M1')) return 'Low'
  if (madCode.startsWith('M0')) return 'Safe'
  return 'Unknown'
}

function ExpandedDetail({ event, detail }: { event: EventRow; detail: EventDetail | null | undefined }) {
  const verdict = detail?.verdict ?? event.verdict

  return (
    <div className="space-y-4">
      <DetailBlock label="Verdict">
        {!verdict ? (
          <p className="text-xs text-ink-3">No verdict recorded for this event yet.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            <Badge label={verdict.mad_code} className={madBadgeColor(verdict.mad_code)} />
            {detail?.verdict && typeof detail.verdict.latency_ms === 'number' && (
              <span className="font-mono text-ink-3">
                Latency: <span className="text-ink">{detail.verdict.latency_ms}ms</span>
              </span>
            )}
            <Link
              href={`/sessions/${event.session_id}`}
              className="text-xs text-ink-2 hover:underline font-mono ml-auto"
            >
              View in session &rarr;
            </Link>
          </div>
        )}
        {verdict && verdict.mad_code !== 'M0' && (
          <div className="mt-3">
            <AlertExplanation madCode={verdict.mad_code} />
          </div>
        )}
      </DetailBlock>

      <DetailBlock label="Payload">
        {detail === undefined ? (
          <p className="text-xs text-ink-3">Loading event details...</p>
        ) : detail === null ? (
          <p className="text-xs text-ink-3">Unable to load event details.</p>
        ) : (
          <JsonBlock value={detail.payload} />
        )}
      </DetailBlock>
    </div>
  )
}

function DetailBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[12.5px] text-ink-3 mb-2">{label}</p>
      {children}
    </div>
  )
}
