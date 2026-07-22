// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'

export function Topbar({ onMenuClick }: { onMenuClick?: () => void }) {
  const router = useRouter()

  async function handleLogout() {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {})
    document.cookie = 'adrian_token=; path=/; max-age=0'
    router.push('/login')
  }

  return (
    <header className="h-12 border-b border-surface-border bg-surface flex items-center justify-between px-4 md:px-6">
      <div className="flex items-center gap-3">
        {onMenuClick && (
          <button
            type="button"
            onClick={onMenuClick}
            aria-label="Open navigation"
            className="md:hidden -ml-1 p-2 text-ink-3 hover:text-ink transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        )}
        <span className="text-[13px] font-medium text-ink-3">Dashboard</span>
      </div>
      <button
        onClick={handleLogout}
        className="text-xs text-ink-3 hover:text-ink transition-colors font-medium"
      >
        Logout
      </button>
    </header>
  )
}
