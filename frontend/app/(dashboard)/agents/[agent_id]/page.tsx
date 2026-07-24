// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/api'
import { timeAgo } from '@/lib/utils'

export default function AgentDetailPage() {
  const { agent_id } = useParams<{ agent_id: string }>()
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    api(`/api/agents/${agent_id}`).then(r => setData(r.data)).catch(() => {})
  }, [agent_id])

  if (!data) return <div className="text-sm text-ink-3">Loading...</div>

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-1 font-mono">{data.agent_id}</h2>
      <p className="text-xs text-ink-3 font-mono mb-6">
        First seen {timeAgo(data.first_seen)} &middot; Last seen {timeAgo(data.last_seen)}
      </p>

      <h3 className="text-[13px] font-medium text-ink-3 mb-3">Recent sessions</h3>
      <div className="bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-ink-3 border-b border-surface-border bg-surface-overlay/50">
              <th className="px-4 py-2.5 text-[13px] font-medium">Session</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Started</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Ended</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Events</th>
            </tr>
          </thead>
          <tbody>
            {data.sessions?.map((s: any) => (
              <tr key={s.session_id} className="border-b border-surface-border/50 table-row-hover">
                <td className="px-4 py-2.5 font-mono text-xs text-ink-2">
                  <Link href={`/sessions/${s.session_id}`} className="hover:underline">{s.session_id}</Link>
                </td>
                <td className="px-4 py-2.5 text-ink-3 text-xs">{timeAgo(s.started_at)}</td>
                <td className="px-4 py-2.5 text-ink-3 text-xs">{timeAgo(s.ended_at)}</td>
                <td className="px-4 py-2.5 text-ink">{s.event_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
