// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

// Minimal accessible-ish modal primitive. We intentionally skip Radix - the
// dashboard has no other dialogs yet and pulling in the package just for one
// modal adds bundle weight without a proportional payoff. If we grow more
// dialogs with complex focus/escape behaviour we can swap to Radix later.

import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: React.ReactNode
  size?: 'sm' | 'md' | 'lg'
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    // Prevent background scroll while open; restore previous value on close.
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  if (!open) return null
  if (typeof document === 'undefined') return null

  const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-2xl' }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className={`relative w-full ${widths[size]} bg-surface-overlay border border-surface-border rounded-lg shadow-xl`}>
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-surface-border">
          <div>
            <h3 className="text-sm font-semibold text-white">{title}</h3>
            {description && <p className="text-xs text-muted mt-0.5">{description}</p>}
          </div>
          <button onClick={onClose}
                  className="text-muted hover:text-white transition-colors"
                  aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="px-5 py-4 max-h-[70vh] overflow-y-auto">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
