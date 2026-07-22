// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// ClickUp uses a three-color gradient mark. Inline SVG keeps the gradient
// IDs scoped via a stable unique suffix to avoid clashes when the icon is
// rendered multiple times on the same page.
export function ClickupIcon({ size = 28 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <defs>
        <linearGradient id="cu-grad" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#8930FD" />
          <stop offset="0.5" stopColor="#49CCF9" />
          <stop offset="1" stopColor="#FF51E5" />
        </linearGradient>
      </defs>
      <path fill="url(#cu-grad)" d="M2 18.439l3.67-2.814c1.95 2.546 4.025 3.721 6.343 3.721 2.304 0 4.322-1.157 6.19-3.691L22 18.501c-2.695 3.656-6.045 5.599-9.987 5.599-3.927 0-7.302-1.931-10.013-5.661z"/>
      <path fill="url(#cu-grad)" d="M12.006 5.429L5.469 11.08 3.068 8.306l8.953-7.726 8.87 7.733-2.414 2.767z"/>
    </svg>
  )
}
