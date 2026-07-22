// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

export function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 text-[13px] font-medium rounded ${className}`}>
      {label}
    </span>
  )
}
