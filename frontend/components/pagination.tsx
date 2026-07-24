// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

export function Pagination({
  page, perPage, total, onChange,
}: {
  page: number; perPage: number; total: number; onChange: (p: number) => void
}) {
  const totalPages = Math.ceil(total / perPage)
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-between mt-4 text-xs text-ink-3 font-medium">
      <span>{total} total</span>
      <div className="flex gap-2">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="px-3 py-1 border border-surface-border rounded disabled:opacity-30 hover:bg-surface-hover hover:text-ink transition-colors"
        >
          Prev
        </button>
        <span className="px-3 py-1 text-ink">
          {page}/{totalPages}
        </span>
        <button
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
          className="px-3 py-1 border border-surface-border rounded disabled:opacity-30 hover:bg-surface-hover hover:text-ink transition-colors"
        >
          Next
        </button>
      </div>
    </div>
  )
}
