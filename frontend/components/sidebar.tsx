// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { api } from '@/lib/api'
import { ThemeToggle } from './theme-toggle'

const nav = [
  { href: '/', label: 'Overview', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4' },
  { href: '/agents', label: 'Agents', icon: 'M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2z' },
  { href: '/events', label: 'Events', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
  { href: '/reviews', label: 'Reviews', icon: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z' },
  { href: '/mcp', label: 'MCP servers', icon: 'M4 6a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM4 14a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2z' },
  { href: '/settings', label: 'Settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z' },
]

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname()
  const [pendingReviews, setPendingReviews] = useState(0)

  // Cheap poll for the pending count - drives the badge on /reviews.
  // 30 s is well below the human-review wall-clock, low cost on the
  // backend (one COUNT against an indexed status).
  useEffect(() => {
    let cancelled = false
    function tick() {
      api('/api/reviews?status=pending&per_page=1')
        .then(r => { if (!cancelled) setPendingReviews(r.data?.total || 0) })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 30_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  return (
    <>
      {open && (
        <div
          className="md:hidden fixed inset-0 bg-black/60 z-30"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`w-56 border-r border-surface-border bg-surface h-screen fixed left-0 top-0 flex flex-col z-40 transition-transform md:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
      <div className="px-4 py-5 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-surface-overlay flex items-center justify-center">
            <span className="text-ink text-xs font-bold font-mono">A</span>
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-ink">Adrian</h1>
            <p className="text-[13px] font-medium text-ink-3">Security monitor</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {nav.map(item => {
          const active = item.href === '/'
            ? pathname === '/'
            : pathname.startsWith(item.href)
          const badge = item.href === '/reviews' && pendingReviews > 0 ? pendingReviews : null

          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onClose}
              className={`flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-colors ${
                active
                  ? 'bg-surface-raised text-ink font-medium shadow-sm'
                  : 'text-ink-2 hover:bg-surface-hover hover:text-ink'
              }`}
            >
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
              </svg>
              <span className="flex-1">{item.label}</span>
              {badge !== null && (
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-warn/20 text-warn tracking-wider">
                  {badge}
                </span>
              )}
            </Link>
          )
        })}
      </nav>

      <div className="px-4 py-3 border-t border-surface-border space-y-2">
        <ThemeToggle />
        <div className="flex items-center justify-between gap-2">
          <SocialLink
            href="https://discord.gg/Vq2VyYrw8Z"
            label="Join us on Discord"
            icon="M19.27 5.33C17.94 4.71 16.5 4.26 15 4a.09.09 0 00-.07.03c-.18.33-.39.76-.53 1.09a16.09 16.09 0 00-4.8 0c-.14-.34-.35-.76-.54-1.09-.01-.02-.04-.03-.07-.03-1.5.26-2.93.71-4.27 1.33a.06.06 0 00-.03.03C2.04 9.41 1.32 13.38 1.66 17.31a.07.07 0 00.03.05c1.81 1.33 3.56 2.13 5.27 2.66.03.01.06 0 .07-.02.41-.56.77-1.15 1.08-1.77.02-.04 0-.08-.04-.09-.58-.22-1.13-.49-1.66-.79a.07.07 0 01-.01-.11c.11-.08.22-.17.33-.25a.05.05 0 01.05-.01c3.49 1.59 7.27 1.59 10.72 0a.05.05 0 01.05.01c.11.09.22.17.33.26.05.04.04.1-.01.11-.53.31-1.08.57-1.66.79-.04.01-.06.06-.04.09.32.62.68 1.21 1.08 1.77.02.02.05.03.07.02 1.72-.53 3.46-1.33 5.28-2.66.02-.01.03-.03.03-.05.4-4.55-.69-8.49-2.91-11.95a.05.05 0 00-.03-.03zM8.52 14.91c-1.03 0-1.89-.95-1.89-2.12 0-1.17.84-2.12 1.89-2.12 1.06 0 1.91.96 1.89 2.12 0 1.17-.84 2.12-1.89 2.12zm6.97 0c-1.03 0-1.89-.95-1.89-2.12 0-1.17.84-2.12 1.89-2.12 1.06 0 1.91.96 1.89 2.12 0 1.17-.83 2.12-1.89 2.12z"
          />
          <SocialLink
            href="https://github.com/secureagentics/Adrian"
            label="Star us on GitHub"
            icon="M12 .3a12 12 0 00-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.6-1.4-1.4-1.8-1.4-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 016 0c2.3-1.5 3.3-1.2 3.3-1.2.7 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.5.4.9 1.2.9 2.4v3.6c0 .3.2.7.8.6A12 12 0 0012 .3"
          />
          <SocialLink
            href="https://www.linkedin.com/company/secure-agentics"
            label="Follow on LinkedIn"
            icon="M19 0h-14a5 5 0 00-5 5v14a5 5 0 005 5h14a5 5 0 005-5v-14a5 5 0 00-5-5zM8 19H5V8h3v11zM6.5 6.7a1.7 1.7 0 110-3.4 1.7 1.7 0 010 3.4zM20 19h-3v-5.6c0-1.4-.5-2.3-1.7-2.3-.9 0-1.5.6-1.7 1.2-.1.2-.1.5-.1.8V19h-3V8h3v1.3c.4-.6 1.1-1.5 2.7-1.5 2 0 3.5 1.3 3.5 4.1V19z"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-ink-3" />
          <span className="text-[13px] font-medium text-ink-3">System active</span>
        </div>
      </div>
      </aside>
    </>
  )
}

function SocialLink({ href, label, icon }: { href: string; label: string; icon: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={label}
      aria-label={label}
      className="text-ink-3 hover:text-ink transition-colors"
    >
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d={icon} />
      </svg>
    </a>
  )
}
