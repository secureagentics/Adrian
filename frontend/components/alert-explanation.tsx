// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { resolveAlert, useMadAlerts, type MadAlert } from '@/lib/mad-alerts'
import { madBadgeColor } from '@/lib/utils'

const ACTION_TONE: Record<string, string> = {
  NOTIFY: 'bg-accent/20 text-accent',
  BLOCK: 'bg-warn/20 text-warn',
  ESCALATE: 'bg-danger/20 text-danger',
}

// AlertExplanation renders the curated description for a verdict's
// MAD code: severity_label + subcategory + description + example +
// references. Falls back to a minimal severity-only badge when the
// code is unrecognised (e.g. a bare M3 with no subcode).
//
// The model's raw reasoning is never rendered. The only user-facing
// text is what ships in the alerts bundle.
export function AlertExplanation({ madCode }: { madCode: string }) {
  const bundle = useMadAlerts()
  const alert = resolveAlert(bundle, madCode)

  if (!madCode || madCode.startsWith('M0')) return null

  if (!alert) {
    const baseAction = bundle?.default_action?.[madCode.slice(0, 2)] || ''
    return (
      <div className="border border-surface-border rounded-lg p-3 bg-surface-overlay/30">
        <p className="text-xs text-muted">
          Code <code className="text-accent">{madCode}</code>
          {baseAction && <> · default action <span className="text-white">{baseAction}</span></>}
        </p>
      </div>
    )
  }

  return (
    <div className="border border-surface-border rounded-lg p-4 bg-surface-overlay/30 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-[10px] font-mono tracking-wider px-1.5 py-0.5 rounded ${madBadgeColor(alert.code)}`}>
          {alert.code}
        </span>
        <span className="text-xs font-mono text-muted tracking-wider">{alert.severity_label.toUpperCase()}</span>
        <span className={`text-[10px] font-mono tracking-wider px-1.5 py-0.5 rounded ml-auto ${ACTION_TONE[alert.default_action] || 'bg-surface text-muted'}`}>
          {alert.default_action}
        </span>
      </div>

      <h4 className="text-sm font-semibold text-white">{alert.subcategory}</h4>
      <p className="text-sm text-white/80">{alert.description}</p>

      {alert.example && (
        <p className="text-xs text-muted italic border-l-2 border-surface-border pl-3">
          <span className="not-italic font-mono text-[10px] tracking-wider text-muted/80 mr-1">FOR EXAMPLE:</span>
          {alert.example}
        </p>
      )}

      {alert.references.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {alert.references.map((ref, i) => (
            <a
              key={i}
              href={ref.url}
              target="_blank"
              rel="noopener noreferrer"
              title={ref.framework}
              className="text-[10px] font-mono tracking-wider px-1.5 py-0.5 rounded bg-surface-overlay text-muted hover:text-accent transition-colors"
            >
              {ref.identifier}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

// AlertExplanationFromAlert is a small variant for callers that
// already hold the resolved entry (e.g. testing / Storybook). Same
// rendering, no hook lookup.
export function AlertExplanationFromAlert({ alert }: { alert: MadAlert }) {
  return (
    <div className="border border-surface-border rounded-lg p-4 bg-surface-overlay/30 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-[10px] font-mono tracking-wider px-1.5 py-0.5 rounded ${madBadgeColor(alert.code)}`}>
          {alert.code}
        </span>
        <span className="text-xs font-mono text-muted tracking-wider">{alert.severity_label.toUpperCase()}</span>
        <span className={`text-[10px] font-mono tracking-wider px-1.5 py-0.5 rounded ml-auto ${ACTION_TONE[alert.default_action] || 'bg-surface text-muted'}`}>
          {alert.default_action}
        </span>
      </div>
      <h4 className="text-sm font-semibold text-white">{alert.subcategory}</h4>
      <p className="text-sm text-white/80">{alert.description}</p>
    </div>
  )
}
