// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import { Badge } from '@/components/badge'
import { Pagination } from '@/components/pagination'
import { madBadgeColor, timeAgo } from '@/lib/utils'

// One row from /api/agents, a runtime LangGraph node observed by the
// SDK. agent_profile_id + agent_profile_name carry the operator-
// configured agent profile this node most recently produced events
// under. Empty string for agents that haven't produced an event yet,
// or whose profile has been deleted, those group under "Unassigned".
type AgentRow = {
  id: string
  agent_id: string
  agent_profile_id: string
  agent_profile_name: string
  last_seen: string
  event_count: number
  worst_mad: string
}

type AgentGroup = {
  profile_id: string
  profile_name: string
  agents: AgentRow[]
}

const UNASSIGNED_LABEL = 'Unassigned'

// Group by profile_name preserving the backend's profile-name-then-
// last_seen ordering. Empty agent_profile_name groups under "Unassigned"
// at the bottom.
function group(rows: AgentRow[]): AgentGroup[] {
  const groups: AgentGroup[] = []
  const indexByProfile = new Map<string, number>()
  for (const r of rows) {
    const key = r.agent_profile_id || ''
    const name = r.agent_profile_name || UNASSIGNED_LABEL
    if (!indexByProfile.has(key)) {
      indexByProfile.set(key, groups.length)
      groups.push({ profile_id: key, profile_name: name, agents: [] })
    }
    groups[indexByProfile.get(key)!].agents.push(r)
  }
  // Pin "Unassigned" to the bottom regardless of where it landed in
  // the source ordering.
  groups.sort((a, b) => {
    if (a.profile_name === UNASSIGNED_LABEL) return 1
    if (b.profile_name === UNASSIGNED_LABEL) return -1
    return 0
  })
  return groups
}

export default function AgentsPage() {
  const [data, setData] = useState<{ agents: AgentRow[]; total: number }>({ agents: [], total: 0 })
  const [page, setPage] = useState(1)

  useEffect(() => {
    api(`/api/agents?page=${page}&per_page=20`)
      .then(r => setData(r.data || { agents: [], total: 0 }))
      .catch(() => {})
  }, [page])

  const isEmpty = !data.agents?.length
  const groups = group(data.agents || [])

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-1">Agents</h2>
      <p className="text-[12.5px] text-ink-3 mb-6">
        Runtime sub-agents observed by the SDK, grouped under the
        operator-configured Agent profile (Settings &rarr; Agents).
      </p>

      {isEmpty ? (
        <EmptyState
          title="No agents yet"
          hint="Sub-agents register the first time your SDK forwards an event under one of your API keys. Run an instrumented agent and it'll appear here."
        />
      ) : (
        <>
          <div className="space-y-6">
            {groups.map(g => (
              <AgentGroupCard key={g.profile_id || g.profile_name} group={g} />
            ))}
          </div>

          <Pagination page={page} perPage={20} total={data.total || 0} onChange={setPage} />
        </>
      )}
    </div>
  )
}

function AgentGroupCard({ group }: { group: AgentGroup }) {
  const isUnassigned = group.profile_name === UNASSIGNED_LABEL
  return (
    <div className="bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
      <div className="px-5 py-3 border-b border-surface-border bg-surface-overlay/50">
        <span className={`text-[13px] font-medium ${isUnassigned ? 'text-ink-3 italic' : 'text-ink'}`}>
          {group.profile_name}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-ink-3 border-b border-surface-border">
              <th className="px-4 py-2.5 text-[13px] font-medium">Agent</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Last seen</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Events</th>
              <th className="px-4 py-2.5 text-[13px] font-medium">Worst MAD</th>
            </tr>
          </thead>
          <tbody>
            {group.agents.map(a => (
              <tr key={a.id} className="border-b border-surface-border/50 table-row-hover">
                <td className="px-4 py-2.5 font-mono text-xs text-ink-2">
                  <Link href={`/agents/${a.agent_id}`} className="hover:underline">{a.agent_id}</Link>
                </td>
                <td className="px-4 py-2.5 text-ink-3 text-xs">{timeAgo(a.last_seen)}</td>
                <td className="px-4 py-2.5 text-ink">{a.event_count}</td>
                <td className="px-4 py-2.5">
                  {a.worst_mad && <Badge label={a.worst_mad} className={madBadgeColor(a.worst_mad)} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
      <p className="text-sm text-ink mb-1">{title}</p>
      <p className="text-xs text-ink-3 max-w-md mx-auto">{hint}</p>
    </div>
  )
}
