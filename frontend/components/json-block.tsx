// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// JsonBlock renders a value as a pretty-printed, syntax-highlighted
// <pre>. Tokenises the JSON.stringify output with a single regex
// pass and wraps each token in a span with a Tailwind colour class:
//
//   keys    -> warn  (amber)    so they read as labels
//   strings -> accent (green)   distinct from keys
//   numbers -> white            quietly readable
//   bool    -> accent           true / false stand out
//   null    -> muted            de-emphasised
//
// Input is our own server-controlled JSON. Stringify guarantees no
// raw HTML, but we still HTML-escape before highlighting to protect
// against any future caller that hands us already-escaped content.

const TOKEN = /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

function escapeHTML(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function classify(token: string): string {
  if (token[0] === '"') {
    return token.endsWith(':') || /"\s*:$/.test(token)
      ? 'text-warn'
      : 'text-accent'
  }
  if (token === 'true' || token === 'false') return 'text-accent font-semibold'
  if (token === 'null') return 'text-muted'
  return 'text-white'
}

function highlight(json: string): string {
  return escapeHTML(json).replace(TOKEN, (match) =>
    `<span class="${classify(match)}">${match}</span>`,
  )
}

export function JsonBlock({
  value,
  className = '',
  maxHeight = 'max-h-72',
}: {
  value: unknown
  className?: string
  maxHeight?: string
}) {
  const json = (() => {
    try {
      return JSON.stringify(value ?? null, null, 2)
    } catch {
      return String(value)
    }
  })()
  return (
    <pre
      className={`bg-surface border border-surface-border rounded p-3 text-[11px] font-mono overflow-x-auto whitespace-pre-wrap break-all ${maxHeight} ${className}`}
      // Highlighter only touches its own JSON output (escaped above);
      // no caller-controlled HTML lands here.
      dangerouslySetInnerHTML={{ __html: highlight(json) }}
    />
  )
}
