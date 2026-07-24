// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

// Three-way segmented toggle: light / system / dark.
// The current selection has shadow + bg; the others are flat.

import { Monitor, Moon, Sun } from 'lucide-react'
import { Theme, useTheme } from './theme-provider'

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'system', label: 'System', Icon: Monitor },
  { value: 'dark', label: 'Dark', Icon: Moon },
]

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useTheme()
  return (
    <div className="inline-flex items-center bg-surface-overlay border border-surface-border rounded-lg p-0.5">
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = theme === value
        return (
          <button
            key={value}
            type="button"
            onClick={() => setTheme(value)}
            aria-label={`Theme: ${label}`}
            aria-pressed={active}
            className={`inline-flex items-center justify-center gap-1.5 rounded-md transition-colors ${
              compact ? 'h-6 w-6' : 'h-7 px-2'
            } ${
              active
                ? 'bg-surface-raised text-ink shadow-sm'
                : 'text-ink-3 hover:text-ink'
            }`}
          >
            <Icon size={compact ? 13 : 13} />
            {!compact && <span className="text-[12px] font-medium">{label}</span>}
          </button>
        )
      })}
    </div>
  )
}
