// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

export type TimeRange = '1h' | '24h' | '7d' | '30d' | 'custom'

const RANGE_LABELS: Record<TimeRange, string> = {
  '1h': 'Last 1 hour',
  '24h': 'Last 24 hours',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
  custom: 'Custom',
}

const RANGE_HEADER: Record<TimeRange, string> = {
  '1h': 'LAST 1 HOUR',
  '24h': 'LAST 24 HOURS',
  '7d': 'LAST 7 DAYS',
  '30d': 'LAST 30 DAYS',
  custom: 'CUSTOM RANGE',
}

const RANGE_SECONDS: Record<Exclude<TimeRange, 'custom'>, number> = {
  '1h': 3600,
  '24h': 86400,
  '7d': 7 * 86400,
  '30d': 30 * 86400,
}

// sinceForRange returns the RFC3339 lower bound that the dashboard API
// expects on `?since=...`. For preset ranges this is now() minus the
// fixed window. For custom ranges the caller provides a datetime-local
// string (YYYY-MM-DDTHH:mm) which we parse as local time.
export function sinceForRange(range: TimeRange, customSince: string): string {
  if (range === 'custom') {
    if (!customSince) return ''
    const t = new Date(customSince)
    return isNaN(t.getTime()) ? '' : t.toISOString()
  }
  const secs = RANGE_SECONDS[range]
  return new Date(Date.now() - secs * 1000).toISOString()
}

export function rangeHeaderLabel(range: TimeRange): string {
  return RANGE_HEADER[range]
}

export function TimeRangeSelect({
  value,
  customSince,
  onChange,
  className,
}: {
  value: TimeRange
  customSince: string
  onChange: (range: TimeRange, customSince?: string) => void
  className?: string
}) {
  return (
    <div className={`flex items-center gap-2 ${className || ''}`}>
      <select
        value={value}
        onChange={e => onChange(e.target.value as TimeRange)}
        className="px-3 py-1.5 border border-surface-border rounded text-sm bg-surface-overlay text-white"
      >
        {(Object.keys(RANGE_LABELS) as TimeRange[]).map(r => (
          <option key={r} value={r}>{RANGE_LABELS[r]}</option>
        ))}
      </select>
      {value === 'custom' && (
        <input
          type="datetime-local"
          value={customSince}
          onChange={e => onChange('custom', e.target.value)}
          className="px-3 py-1.5 border border-surface-border rounded text-sm bg-surface-overlay text-white"
        />
      )}
    </div>
  )
}
