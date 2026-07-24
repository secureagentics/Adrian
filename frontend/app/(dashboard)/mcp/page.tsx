// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import { Badge } from '@/components/badge'
import { timeAgo } from '@/lib/utils'

type McpServer = {
  session_id: string
  name: string
  transport: string
  endpoint: string
  received_at: string
}

export default function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([])

  useEffect(() => {
    api('/api/mcp/servers')
      .then(r => setServers(r.data?.servers || []))
      .catch(() => {})
  }, [])

  const isEmpty = servers.length === 0

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h2 className="text-lg font-semibold text-ink">MCP servers</h2>
        <span className="text-[12.5px] text-ink-3">
          {servers.length.toLocaleString()} reported
        </span>
      </div>

      {isEmpty ? (
        <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
          <p className="text-sm text-ink mb-1">No MCP servers reported yet</p>
          <p className="text-xs text-ink-3 max-w-md mx-auto">
            The SDK reports its MCP server inventory at session login. Run an instrumented agent that uses MCP and entries appear here.
          </p>
        </div>
      ) : (
        <>
          <div className="hidden md:block bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-ink-3 border-b border-surface-border bg-surface-overlay/50">
                  <th className="px-4 py-2.5 text-[13px] font-medium">Name</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Transport</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Endpoint</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Session</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Reported</th>
                </tr>
              </thead>
              <tbody>
                {servers.map(s => (
                  <tr key={`${s.session_id}-${s.name}`} className="border-b border-surface-border/50">
                    <td className="px-4 py-2.5 text-sm text-ink">{s.name}</td>
                    <td className="px-4 py-2.5">
                      <Badge label={s.transport.toUpperCase()} className="bg-surface-overlay text-ink-3" />
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-3 truncate max-w-xs">
                      {s.endpoint || <span>-</span>}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-2">
                      <Link href={`/sessions/${s.session_id}`} className="hover:underline">
                        {s.session_id.slice(0, 20)}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-ink-3 text-xs">{timeAgo(s.received_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="md:hidden space-y-2">
            {servers.map(s => (
              <div key={`${s.session_id}-${s.name}`} className="bg-surface-raised border border-surface-border rounded-lg p-3">
                <div className="flex items-baseline justify-between mb-1">
                  <span className="text-sm text-ink truncate">{s.name}</span>
                  <span className="text-[11px] text-ink-3 font-mono ml-2 flex-shrink-0">{timeAgo(s.received_at)}</span>
                </div>
                <div className="flex items-center gap-2 mb-1">
                  <Badge label={s.transport.toUpperCase()} className="bg-surface-overlay text-ink-3" />
                  {s.endpoint && <span className="font-mono text-[11px] text-ink-3 truncate">{s.endpoint}</span>}
                </div>
                <Link href={`/sessions/${s.session_id}`} className="font-mono text-[11px] text-ink-2 hover:underline">
                  session {s.session_id.slice(0, 20)}
                </Link>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
