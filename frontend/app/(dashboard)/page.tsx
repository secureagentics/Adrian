'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import { madBadgeColor } from '@/lib/utils'

type Overview = {
  total_events: number
  flagged_verdicts: number
  classifier_errors: number
  pending_reviews: number
  active_agents: number
  verdicts_by_mad: Record<string, number>
  window: string
}

type ActivityBucket = { time: string; count: number }
type ActivityPayload = { range: '24h' | '7d'; buckets: ActivityBucket[] }

const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS

export default function OverviewPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [activity, setActivity] = useState<ActivityPayload | null>(null)
  const [range, setRange] = useState<'24h' | '7d'>('24h')

  useEffect(() => {
    api('/api/stats/overview')
      .then(r => setOverview(r.data || null))
      .catch(() => setOverview(null))
  }, [])

  useEffect(() => {
    api(`/api/stats/activity?range=${range}`)
      .then(r => setActivity(r.data || null))
      .catch(() => setActivity(null))
  }, [range])

  const series = useMemo(() => fillBuckets(activity), [activity])
  const peak = Math.max(1, ...series.map(s => s.count))

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-ink mb-1">Overview</h2>
          <p className="text-[12.5px] text-ink-3">Last 24 hours</p>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        <StatCard label="Events" value={overview?.total_events} href="/events" />
        <StatCard label="Flagged verdicts" value={overview?.flagged_verdicts} href="/events" tone="warn" />
        <StatCard label="Classifier errors" value={overview?.classifier_errors} href="/events?verdict_status=error" tone="danger" />
        <StatCard label="Pending reviews" value={overview?.pending_reviews} href="/reviews" tone="danger" />
        <StatCard label="Active agents" value={overview?.active_agents} href="/agents" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <div className="lg:col-span-2 bg-surface-raised border border-surface-border rounded-lg p-4">
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-[13px] font-medium text-ink-3">Event activity</h3>
            <div className="flex gap-1 text-[10px] font-mono">
              {(['24h', '7d'] as const).map(r => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRange(r)}
                  className={`px-2 py-1 rounded ${
                    range === r ? 'bg-surface-overlay text-ink' : 'text-ink-3 hover:text-ink'
                  }`}
                >
                  {r.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          {series.length === 0 ? (
            <p className="text-xs text-ink-3 py-8 text-center">No events in this window yet.</p>
          ) : (
            <div className="flex items-end gap-0.5 h-24">
              {series.map((s, i) => (
                <div
                  key={i}
                  className="flex-1 bg-surface-overlay hover:bg-ink-2/30 transition-colors rounded-t"
                  style={{ height: `${(s.count / peak) * 100}%`, minHeight: s.count > 0 ? '2px' : '0' }}
                  title={`${s.count} events @ ${formatTick(s.time, range)}`}
                />
              ))}
            </div>
          )}
          <div className="flex justify-between text-[10px] font-mono text-ink-3 mt-2">
            <span>{series[0] ? formatTick(series[0].time, range) : ''}</span>
            <span>{series[series.length - 1] ? formatTick(series[series.length - 1].time, range) : ''}</span>
          </div>
        </div>

        <div className="bg-surface-raised border border-surface-border rounded-lg p-4">
          <h3 className="text-[13px] font-medium text-ink-3 mb-3">Verdict mix</h3>
          {overview && Object.values(overview.verdicts_by_mad).some(v => v > 0) ? (
            <ul className="space-y-2">
              {(['M0', 'M2', 'M3', 'M4', 'error'] as const).map(family => {
                const count = overview.verdicts_by_mad[family] || 0
                const total = Object.values(overview.verdicts_by_mad).reduce((a, b) => a + b, 0)
                const pct = total ? (count / total) * 100 : 0
                return (
                  <li key={family} className="text-xs">
                    <div className="flex items-baseline justify-between mb-1">
                      <span className={`font-mono ${family === 'error' ? 'bg-danger/20 text-danger border-danger/40' : madBadgeColor(family)} px-1.5 rounded`}>
                        {family === 'error' ? 'Classifier error' : family}
                      </span>
                      <span className="text-ink-3 font-mono">{count}</span>
                    </div>
                    <div className="h-1 bg-surface rounded-full overflow-hidden">
                      <div
                        className={family === 'M0' ? 'h-full bg-ink/20' : family === 'M4' || family === 'error' ? 'h-full bg-danger/60' : family === 'M3' ? 'h-full bg-warn/60' : 'h-full bg-ink-3/40'}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="text-xs text-ink-3">No verdicts in this window yet.</p>
          )}
        </div>
      </div>

      <div className="bg-surface-raised border border-surface-border rounded-lg p-5">
        <h3 className="text-[13px] font-medium text-ink-3 mb-3">First-time setup</h3>
        <ol className="text-sm text-ink-3 space-y-2 list-decimal list-inside">
          <li>Open <Link href="/settings" className="text-ink-2 hover:underline">Settings</Link>, set your policy mode, create an agent profile, create an API key.</li>
          <li>Install the Python SDK (<code className="text-ink-2">pip install -e ./sdk/</code> from the repo root).</li>
          <li>In your agent code: <code className="text-ink-2">adrian.init(api_key=&quot;...&quot;, ws_url=&quot;ws://localhost:8080/ws&quot;)</code>.</li>
          <li>Run your agent. Events appear under <Link href="/events" className="text-ink-2 hover:underline">Events</Link>; verdicts show beside each event.</li>
        </ol>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  href,
  tone,
}: {
  label: string
  value: number | undefined
  href: string
  tone?: 'warn' | 'danger'
}) {
  const valueClass = tone === 'danger' ? 'text-ink' : tone === 'warn' ? 'text-warn' : 'text-ink'
  return (
    <Link
      href={href}
      className="block bg-surface-raised border border-surface-border rounded-lg p-4 hover:border-ink/20 transition-colors"
    >
      <p className="text-[12.5px] text-ink-3 mb-1">{label}</p>
      <p className={`text-2xl font-semibold tabular-nums ${valueClass}`}>{value ?? '-'}</p>
    </Link>
  )
}

// fillBuckets pads the sparse series the API returns (no row -> no
// bucket) into a contiguous array so the chart renders gaps as empty
// bars rather than just compressing the visible buckets together.
function fillBuckets(payload: ActivityPayload | null): ActivityBucket[] {
  if (!payload) return []
  const stepMs = payload.range === '7d' ? DAY_MS : HOUR_MS
  const total = payload.range === '7d' ? 7 : 24
  const now = Date.now()
  const startMs = floor(now - total * stepMs, stepMs)

  const counts = new Map<number, number>()
  for (const b of payload.buckets) {
    counts.set(new Date(b.time).getTime(), b.count)
  }
  const out: ActivityBucket[] = []
  for (let i = 0; i < total; i++) {
    const tMs = startMs + i * stepMs
    const t = new Date(tMs).toISOString().replace(/\.\d{3}Z$/, 'Z')
    out.push({ time: t, count: counts.get(tMs) || 0 })
  }
  return out
}

function floor(t: number, step: number): number {
  return Math.floor(t / step) * step
}

function formatTick(iso: string, range: '24h' | '7d'): string {
  const d = new Date(iso)
  if (range === '7d') {
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }
  const h = d.getHours().toString().padStart(2, '0')
  return `${h}:00`
}
