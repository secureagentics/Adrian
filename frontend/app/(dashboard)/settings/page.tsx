'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { JsonBlock } from '@/components/json-block'
import { Pagination } from '@/components/pagination'
import { timeAgo } from '@/lib/utils'
import {
  AGENT_PROFILE_LIMITS,
  AGENT_PROFILE_TEMPLATES,
  type AgentProfileTemplate,
} from '@/lib/agent-profile-templates'

type Tab = 'policy' | 'agents' | 'integrations' | 'activity'

const TAB_LABEL: Record<Tab, string> = {
  policy: 'Policy',
  agents: 'Agents',
  integrations: 'Integrations',
  activity: 'Activity',
}

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('policy')

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-6">Settings</h2>

      <div className="flex gap-1 mb-6 border-b border-surface-border overflow-x-auto">
        {(['policy', 'agents', 'integrations', 'activity'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-xs font-medium transition-colors whitespace-nowrap ${
              tab === t
                ? 'border-b-2 border-ink text-ink'
                : 'text-ink-3 hover:text-ink'
            }`}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {tab === 'policy' && <PolicyTab />}
      {tab === 'agents' && <AgentsTab />}
      {tab === 'integrations' && <IntegrationsTab />}
      {tab === 'activity' && <ActivityTab />}
    </div>
  )
}

function PolicyTab() {
  const [policy, setPolicy] = useState<any>(null)
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState<string>('')

  useEffect(() => {
    api('/api/settings/policy').then(r => setPolicy(r.data)).catch(() => {})
  }, [])

  if (!policy) return <div className="text-sm text-ink-3">Loading...</div>

  const showCodeToggles = policy.mode !== 'alert'
  const anyCodeOn = !!(policy.policy_m0 || policy.policy_m2 || policy.policy_m3 || policy.policy_m4)
  const saveDisabled = status === 'saving' || (showCodeToggles && !anyCodeOn)

  async function save() {
    setStatus('saving')
    setErrorMsg('')
    try {
      // Send only the new fields. Legacy block_behaviour / m3_action are
      // left untouched in DB by the server's COALESCE-based UPDATE.
      await api('/api/settings/policy', {
        method: 'PUT',
        body: JSON.stringify({
          mode: policy.mode,
          policy_m0: !!policy.policy_m0,
          policy_m2: !!policy.policy_m2,
          policy_m3: !!policy.policy_m3,
          policy_m4: !!policy.policy_m4,
          fail_closed_on_classifier_error: !!policy.fail_closed_on_classifier_error,
        }),
      })
      setStatus('saved')
      // Auto-revert to idle after 2.5s so the indicator doesn't linger
      // forever - re-firing setTimeout on each save resets the window.
      setTimeout(() => setStatus(s => (s === 'saved' ? 'idle' : s)), 2500)
    } catch (err: any) {
      setStatus('error')
      setErrorMsg(err?.message || 'Save failed - see logs')
    }
  }

  return (
    <div className="max-w-lg space-y-4">
      <SelectField
        label="Mode of execution"
        value={policy.mode || 'alert'}
        options={[
          ['alert', 'Alert - verdicts on dashboard only'],
          ['hitl',  'Human review - pause SDK until approved/rejected'],
          ['block', 'Block - halt SDK on flagged events'],
        ]}
        onChange={v => setPolicy({ ...policy, mode: v })}
      />

      {showCodeToggles && (
        <div className="border border-surface-border rounded p-3 space-y-3 bg-surface-overlay/30">
          <p className="text-[12.5px] text-ink-3">
            Codes to act on
          </p>
          <CodeToggle
            label="M0 - Benign"
            description="Mark benign tool calls - usually leave off"
            value={!!policy.policy_m0}
            onChange={v => setPolicy({ ...policy, policy_m0: v })}
          />
          <CodeToggle
            label="M2 - Likely misuse"
            value={!!policy.policy_m2}
            onChange={v => setPolicy({ ...policy, policy_m2: v })}
          />
          <CodeToggle
            label="M3 - High-risk"
            value={!!policy.policy_m3}
            onChange={v => setPolicy({ ...policy, policy_m3: v })}
          />
          <CodeToggle
            label="M4 - Malicious"
            value={!!policy.policy_m4}
            onChange={v => setPolicy({ ...policy, policy_m4: v })}
          />
          {!anyCodeOn && (
            <p className="text-xs text-warn">
              Enable at least one code or switch back to Alert mode.
            </p>
          )}
        </div>
      )}

      <div className="border border-surface-border rounded p-3 space-y-3 bg-surface-overlay/30">
        <p className="text-[12.5px] text-ink-3">
          Classifier failure handling
        </p>
        <CodeToggle
          label="Fail closed on classifier error"
          description="When enabled, classifier outages or unparseable classifier responses stop BLOCK-mode tool execution and send HITL-mode actions for review."
          value={!!policy.fail_closed_on_classifier_error}
          onChange={v => setPolicy({ ...policy, fail_closed_on_classifier_error: v })}
        />
        <p className="text-xs text-ink-3 leading-relaxed">
          Older SDK versions ignore this flag. Update agents to the SDK version
          shipped with this dashboard before relying on fail-closed enforcement.
        </p>
      </div>

      <div className="border border-surface-border rounded p-3 bg-surface-overlay/30 text-xs text-ink-3 leading-relaxed">
        <span className="font-mono text-warn tracking-wider">NOTE - </span>
        Mode changes apply when an SDK reconnects. The classifier-error
        fail-closed flag is included on future verdicts, so BLOCK-mode timeout
        decisions refresh after the next verdict snapshot reaches the SDK.
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saveDisabled}
          className="px-4 py-2 bg-ink text-surface-raised font-semibold text-sm rounded hover:bg-ink-2 disabled:opacity-50 transition-colors"
        >
          {status === 'saving' ? 'Saving...' : 'Save policy'}
        </button>
        {status === 'saved' && (
          <span className="text-xs font-mono text-ink tracking-wider">Saved</span>
        )}
        {status === 'error' && (
          <span className="text-xs font-mono text-ink tracking-wider">
            {errorMsg}
          </span>
        )}
      </div>
    </div>
  )
}

function SelectField({ label, value, options, onChange }: {
  label: string; value: string; options: string[][]; onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-[12.5px] text-ink-3 mb-1.5">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 border border-surface-border rounded text-sm bg-surface-overlay">
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  )
}

function CodeToggle({ label, description, value, onChange }: {
  label: string; description?: string; value: boolean; onChange: (v: boolean) => void
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <input
        type="checkbox"
        checked={value}
        onChange={e => onChange(e.target.checked)}
        className="mt-0.5 accent-accent"
      />
      <div>
        <div className="text-sm text-ink">{label}</div>
        {description && <div className="text-xs text-ink-3">{description}</div>}
      </div>
    </label>
  )
}

// ----------------------------------------------------------------------
// Agent Profile tab
// ----------------------------------------------------------------------
//
// Customer-editable custom MAD policy snippets the engine splices into
// the classifier system prompt:
//   - remit (single line, ≤ 500 chars)
//   - expected behaviours (≤ 10 entries, each ≤ 100 chars)
//   - known risks       (≤ 10 entries, each ≤ 100 chars)
//
// Limits + the angle-bracket rejection mirror the dashboard-api
// validator (see AGENT_PROFILE_LIMITS).  The server is authoritative;
// these checks exist for immediate UX feedback.

interface AgentProfileState {
  enabled: boolean
  remit: string
  expectedBehaviours: string[]
  knownRisks: string[]
}

const EMPTY_AGENT_PROFILE: AgentProfileState = {
  enabled: false,
  remit: '',
  expectedBehaviours: [],
  knownRisks: [],
}

function clampEntry(s: string): string {
  // utf-16 indexing is fine here - JS slice on a string is "characters"
  // for our purposes (the server enforces utf-8 rune counts; clamping
  // at 100 utf-16 code units may over-truncate emoji-heavy strings by
  // a few bytes, which is harmless: the user just sees the typed cap.)
  return s.slice(0, AGENT_PROFILE_LIMITS.entryMaxChars)
}

function clampRemit(s: string): string {
  return s.slice(0, AGENT_PROFILE_LIMITS.remitMaxChars)
}

function entryError(s: string): string | null {
  if (s.length > AGENT_PROFILE_LIMITS.entryMaxChars) return 'Too long'
  if (/[<>]/.test(s)) return "Cannot contain '<' or '>'"
  return null
}

function remitError(s: string): string | null {
  if (s.length > AGENT_PROFILE_LIMITS.remitMaxChars) return 'Too long'
  if (/[<>]/.test(s)) return "Cannot contain '<' or '>'"
  return null
}

// AgentProfileEditor renders the per-agent profile form (remit +
// expected behaviours + known risks) bound to a single agent_profile_id.
// Used inline inside each AgentRow so the customer can edit one
// agent's context without leaving the Agents tab.
function AgentProfileEditor({ agentId, initial, onSaved }: {
  agentId: string
  initial: { name: string; enabled: boolean; remit: string; m0_entries: string[]; m3_entries: string[] }
  onSaved?: () => void
}) {
  const [profile, setProfile] = useState<AgentProfileState>({
    enabled: !!initial.enabled,
    remit: initial.remit || '',
    expectedBehaviours: Array.isArray(initial.m0_entries) ? initial.m0_entries : [],
    knownRisks:         Array.isArray(initial.m3_entries) ? initial.m3_entries : [],
  })
  const [name, setName] = useState<string>(initial.name)
  const [templateId, setTemplateId] = useState<string>('')
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  function applyTemplate(id: string) {
    setTemplateId(id)
    if (!id) return
    const t = AGENT_PROFILE_TEMPLATES.find(x => x.id === id)
    if (!t) return
    setProfile({
      ...profile,
      remit: clampRemit(t.remit),
      expectedBehaviours: t.expectedBehaviours.slice(0, AGENT_PROFILE_LIMITS.entryMaxCount).map(clampEntry),
      knownRisks: t.knownRisks.slice(0, AGENT_PROFILE_LIMITS.entryMaxCount).map(clampEntry),
    })
  }

  function setEntry(list: 'expectedBehaviours' | 'knownRisks', i: number, v: string) {
    const next = [...profile[list]]
    next[i] = clampEntry(v)
    setProfile({ ...profile, [list]: next })
  }
  function addEntry(list: 'expectedBehaviours' | 'knownRisks') {
    if (profile[list].length >= AGENT_PROFILE_LIMITS.entryMaxCount) return
    setProfile({ ...profile, [list]: [...profile[list], ''] })
  }
  function removeEntry(list: 'expectedBehaviours' | 'knownRisks', i: number) {
    const next = [...profile[list]]
    next.splice(i, 1)
    setProfile({ ...profile, [list]: next })
  }

  const hasFieldError =
    !!remitError(profile.remit) ||
    profile.expectedBehaviours.some(e => entryError(e)) ||
    profile.knownRisks.some(e => entryError(e)) ||
    name.trim().length === 0

  const saveDisabled = status === 'saving' || hasFieldError

  async function save() {
    setStatus('saving')
    setErrorMsg('')
    try {
      await api(`/api/agent-profiles/${agentId}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: name.trim(),
          enabled: profile.enabled,
          remit: profile.remit,
          m0_entries: profile.expectedBehaviours.filter(s => s.length > 0),
          m3_entries: profile.knownRisks.filter(s => s.length > 0),
        }),
      })
      setStatus('saved')
      setTimeout(() => setStatus(s => (s === 'saved' ? 'idle' : s)), 2500)
      onSaved?.()
    } catch (err: unknown) {
      setStatus('error')
      setErrorMsg(err instanceof Error ? err.message : 'Save failed - see logs')
    }
  }

  return (
    <div className="space-y-5 pt-4 mt-4 border-t border-surface-border/60">
      <p className="text-xs text-ink-3 leading-relaxed">
        These don't need to be exhaustive - they help Adrian's detection model understand
        this agent's operating context. If you don't customise, a generic default policy is used.
      </p>

      <div>
        <label className="block text-[12.5px] text-ink-3 mb-1.5">
          Agent name
        </label>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          maxLength={80}
          className="w-full px-3 py-2 border border-surface-border rounded text-sm bg-surface-overlay"
        />
      </div>

      <label className="flex items-start gap-3 cursor-pointer bg-surface-raised border border-surface-border rounded-lg p-4">
        <input
          type="checkbox"
          checked={profile.enabled}
          onChange={e => setProfile({ ...profile, enabled: e.target.checked })}
          className="mt-0.5 accent-accent"
        />
        <div>
          <div className="text-sm text-ink">Use custom agent profile</div>
          <div className="text-xs text-ink-3 mt-0.5">
            When off, the default detection profile is used and the fields below are ignored.
          </div>
        </div>
      </label>

      {!profile.enabled && (
        <div className="border border-surface-border rounded p-3 bg-surface-overlay/30 text-xs text-ink-3 leading-relaxed">
          <span className="font-mono text-warn tracking-wider">NOTE - </span>
          Custom profile is OFF. The classifier is using the generic default policy. Toggle it on
          and fill in the fields below to give the model context for this agent.
        </div>
      )}

      <div>
        <label className="block text-[12.5px] text-ink-3 mb-1.5">
          Start from a template (optional)
        </label>
        <select
          value={templateId}
          onChange={e => applyTemplate(e.target.value)}
          className="w-full px-3 py-2 border border-surface-border rounded text-sm bg-surface-overlay"
        >
          <option value="">- pick to prefill the fields below -</option>
          {AGENT_PROFILE_TEMPLATES.map((t: AgentProfileTemplate) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <p className="text-xs text-ink-3 mt-1.5">
          Picking a template overwrites the current values. You can keep editing afterwards.
        </p>
      </div>

      <RemitField
        value={profile.remit}
        onChange={v => setProfile({ ...profile, remit: clampRemit(v) })}
      />

      <EntryListField
        label="Expected behaviours"
        helpText="Short statements describing what the agent should be doing - lets the classifier recognise routine activity as benign."
        entries={profile.expectedBehaviours}
        onChange={(i, v) => setEntry('expectedBehaviours', i, v)}
        onAdd={() => addEntry('expectedBehaviours')}
        onRemove={i => removeEntry('expectedBehaviours', i)}
      />

      <EntryListField
        label="Known risks"
        helpText="Short statements describing actions that should be flagged - anything you'd consider out-of-bounds for this agent."
        entries={profile.knownRisks}
        onChange={(i, v) => setEntry('knownRisks', i, v)}
        onAdd={() => addEntry('knownRisks')}
        onRemove={i => removeEntry('knownRisks', i)}
      />

      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={save}
          disabled={saveDisabled}
          className="px-4 py-2 bg-ink text-surface-raised font-semibold text-sm rounded hover:bg-ink-2 disabled:opacity-50 transition-colors"
        >
          {status === 'saving' ? 'Saving...' : 'Save agent'}
        </button>
        {status === 'saved' && (
          <span className="text-xs font-mono text-ink tracking-wider">Saved</span>
        )}
        {status === 'error' && (
          <span className="text-xs font-mono text-ink tracking-wider">
            {errorMsg}
          </span>
        )}
        {hasFieldError && status !== 'saving' && (
          <span className="text-xs font-mono text-warn tracking-wider">
            Fix field errors above
          </span>
        )}
      </div>
    </div>
  )
}

function RemitField({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const err = remitError(value)
  const remaining = AGENT_PROFILE_LIMITS.remitMaxChars - value.length
  return (
    <div>
      <label className="block text-[12.5px] text-ink-3 mb-1.5">
        Remit
      </label>
      <p className="text-xs text-ink-3 mb-1.5">
        One sentence describing what the agent is meant to do - its purpose and scope.
      </p>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={3}
        className={`w-full px-3 py-2 border rounded text-sm bg-surface-overlay font-mono ${
          err ? 'border-danger/60' : 'border-surface-border'
        }`}
        placeholder="e.g. Customer-service agent answering inbound queries via the helpdesk."
      />
      <div className="flex justify-between text-[11px] mt-1">
        <span className={err ? 'text-ink' : 'text-ink-3'}>{err || ''}</span>
        <span className="font-mono text-ink-3">
          {remaining < 0 ? 0 : remaining} chars left
        </span>
      </div>
    </div>
  )
}

function EntryListField({
  label,
  helpText,
  entries,
  onChange,
  onAdd,
  onRemove,
}: {
  label: string
  helpText: string
  entries: string[]
  onChange: (i: number, v: string) => void
  onAdd: () => void
  onRemove: (i: number) => void
}) {
  const atLimit = entries.length >= AGENT_PROFILE_LIMITS.entryMaxCount
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="text-[12.5px] text-ink-3">
          {label} ({entries.length}/{AGENT_PROFILE_LIMITS.entryMaxCount})
        </label>
        <button
          onClick={onAdd}
          disabled={atLimit}
          className="text-xs font-mono text-ink-2 hover:text-ink disabled:opacity-40 disabled:cursor-not-allowed tracking-wider"
        >
          + Add
        </button>
      </div>
      <p className="text-xs text-ink-3 mb-2">{helpText}</p>
      <div className="space-y-2">
        {entries.length === 0 && (
          <p className="text-xs text-ink-3 italic">No entries yet - click + Add to start.</p>
        )}
        {entries.map((e, i) => {
          const err = entryError(e)
          const remaining = AGENT_PROFILE_LIMITS.entryMaxChars - e.length
          return (
            <div key={i} className="flex gap-2 items-start">
              <div className="flex-1">
                <input
                  type="text"
                  value={e}
                  onChange={ev => onChange(i, ev.target.value)}
                  className={`w-full px-3 py-1.5 border rounded text-sm bg-surface-overlay font-mono ${
                    err ? 'border-danger/60' : 'border-surface-border'
                  }`}
                  placeholder="One sentence..."
                />
                <div className="flex justify-between text-[11px] mt-0.5">
                  <span className={err ? 'text-ink' : 'text-ink-3'}>{err || ''}</span>
                  <span className="font-mono text-ink-3">
                    {remaining < 0 ? 0 : remaining}
                  </span>
                </div>
              </div>
              <button
                onClick={() => onRemove(i)}
                className="px-2 py-1.5 text-xs font-mono text-ink-3 hover:text-ink tracking-wider"
                aria-label="Remove entry"
              >
                ×
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// AgentsTab - primary surface for managing agentic systems.
//
// Each row is one agent: name, current API key (with regenerate),
// expand-to-edit profile editor, delete. "+ New Agent" mints the
// agent + a fresh API key in one flow and auto-expands the profile
// editor so the customer sets context immediately. Keys are
// single-active per agent (regenerate revokes-old + mints-new).
interface AgentRow {
  id: string
  name: string
  enabled: boolean
  remit: string
  m0_entries: string[]
  m3_entries: string[]
  created_at?: string
  updated_at?: string
}

interface KeyRow {
  id: string
  prefix: string
  label: string
  agent_profile_id: string
  agent_name: string
  revoked: boolean
}

function AgentsTab() {
  const [agents, setAgents] = useState<AgentRow[]>([])
  const [keys, setKeys] = useState<KeyRow[]>([])
  const [newAgentName, setNewAgentName] = useState('')
  const [creating, setCreating] = useState(false)
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [newKey, setNewKey] = useState<{ key: string; agentName: string } | null>(null)
  const [confirmRegenId, setConfirmRegenId] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => { reload() }, [])

  async function reload() {
    try {
      const [a, k] = await Promise.all([
        api<{ data: AgentRow[] }>('/api/agent-profiles'),
        api<{ data: KeyRow[] }>('/api/keys'),
      ])
      setAgents(a.data || [])
      setKeys(k.data || [])
    } catch {
      // ignore - caller-visible state is the empty list.
    }
  }

  function activeKeyFor(agentId: string): KeyRow | undefined {
    return keys.find(k => k.agent_profile_id === agentId && !k.revoked)
  }

  async function createAgent() {
    const name = newAgentName.trim()
    if (!name) return
    setCreating(true)
    setErrorMsg('')
    try {
      const a = await api<{ data: AgentRow }>('/api/agent-profiles', {
        method: 'POST',
        body: JSON.stringify({
          name,
          enabled: false,
          remit: '',
          m0_entries: [],
          m3_entries: [],
        }),
      })
      const k = await api<{ data: { api_key: string } }>(
        `/api/agent-profiles/${a.data.id}/keys`,
        { method: 'POST', body: JSON.stringify({ label: '' }) },
      )
      setNewKey({ key: k.data.api_key, agentName: name })
      setNewAgentName('')
      setShowCreateForm(false)
      setExpandedId(a.data.id)  // auto-open profile editor for context entry
      await reload()
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Create failed')
    } finally {
      setCreating(false)
    }
  }

  async function regenerate(agentId: string) {
    setConfirmRegenId(null)
    try {
      const a = agents.find(x => x.id === agentId)
      const k = await api<{ data: { api_key: string } }>(
        `/api/agent-profiles/${agentId}/keys`,
        { method: 'POST', body: JSON.stringify({ label: '' }) },
      )
      setNewKey({ key: k.data.api_key, agentName: a?.name || 'agent' })
      await reload()
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Regenerate failed')
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-3 leading-relaxed">
        Each <span className="text-ink">agent</span> represents one of your
        agentic systems. It has its own API key for SDK authentication and its own
        classifier-context profile (remit, expected behaviours, known risks). One
        org can have many agents; each agent has one active key at a time.
      </p>

      {newKey && (
        <div className="p-4 bg-surface border border-surface-border rounded-lg">
          <p className="text-sm font-medium text-ink mb-2">
            New API key for <span className="font-semibold">{newKey.agentName}</span> - save it now
          </p>
          <code className="block bg-surface-overlay border border-surface-border rounded p-2 font-mono text-sm text-ink-2 break-all select-all">{newKey.key}</code>
          <button onClick={async () => {
              try {
                if (navigator.clipboard?.writeText) {
                  await navigator.clipboard.writeText(newKey.key)
                } else {
                  const ta = document.createElement('textarea')
                  ta.value = newKey.key
                  ta.style.position = 'fixed'
                  ta.style.left = '-9999px'
                  document.body.appendChild(ta)
                  ta.select()
                  document.execCommand('copy')
                  document.body.removeChild(ta)
                }
              } catch {
                // clipboard unavailable; the key is still visible above for manual copy.
              }
              setNewKey(null)
            }}
            className="mt-2 text-xs text-ink-3 hover:text-ink font-mono transition-colors">Copy and dismiss</button>
        </div>
      )}

      {errorMsg && (
        <div className="p-3 bg-surface border border-surface-border rounded text-xs font-mono text-ink">
          {errorMsg}
        </div>
      )}

      {!showCreateForm ? (
        <button
          onClick={() => setShowCreateForm(true)}
          className="px-4 py-2 bg-ink text-surface-raised font-semibold text-sm rounded hover:bg-ink-2 transition-colors"
        >
          + New agent
        </button>
      ) : (
        <div className="p-4 bg-surface-raised border border-surface-border rounded-lg space-y-3">
          <label className="block text-[12.5px] text-ink-3">
            Agent name
          </label>
          <input
            value={newAgentName}
            onChange={e => setNewAgentName(e.target.value)}
            placeholder="e.g. Customer Service Bot"
            maxLength={80}
            autoFocus
            className="w-full px-3 py-2 border border-surface-border rounded text-sm bg-surface-overlay"
          />
          <p className="text-xs text-ink-3">
            Creating an agent creates a fresh API key and opens its profile editor
            so you can give the classifier context for this agent.
          </p>
          <div className="flex gap-2">
            <button
              onClick={createAgent}
              disabled={creating || newAgentName.trim().length === 0}
              className="px-4 py-1.5 bg-ink text-surface-raised font-semibold text-sm rounded hover:bg-ink-2 disabled:opacity-50 transition-colors"
            >
              {creating ? 'Creating...' : 'Create agent'}
            </button>
            <button
              onClick={() => { setShowCreateForm(false); setNewAgentName('') }}
              className="px-4 py-1.5 border border-surface-border text-sm rounded text-ink-3 hover:text-ink transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {agents.length === 0 && !showCreateForm && (
        <div className="p-8 text-center text-sm text-ink-3 border border-dashed border-surface-border rounded-lg">
          No agents yet. Create one to get an API key and set its context.
        </div>
      )}

      <div className="space-y-3">
        {agents.map(a => {
          const ak = activeKeyFor(a.id)
          const isExpanded = expandedId === a.id
          return (
            <div key={a.id} className="bg-surface-raised border border-surface-border rounded-lg">
              <div className="flex items-center justify-between p-4">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-ink">{a.name}</div>
                  <div className="text-xs font-mono text-ink-3 mt-1">
                    {ak ? (
                      <>Key: <span className="text-ink-2">{ak.prefix}...</span></>
                    ) : (
                      <span className="text-warn">No active key</span>
                    )}
                    {a.enabled && <span className="ml-3 text-ink-2">Custom profile on</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : a.id)}
                    className="px-3 py-1.5 text-xs font-mono tracking-wider border border-surface-border rounded text-ink-3 hover:text-ink transition-colors"
                  >
                    {isExpanded ? 'Close' : 'Edit'}
                  </button>
                  <button
                    onClick={() => setConfirmRegenId(a.id)}
                    className="px-3 py-1.5 text-xs font-mono tracking-wider border border-warn/30 text-warn rounded hover:bg-warn/10 transition-colors"
                  >
                    Regen key
                  </button>
                </div>
              </div>

              {confirmRegenId === a.id && ak && (
                <div className="px-4 pb-4">
                  <div className="p-3 bg-surface border border-warn/30 rounded">
                    <p className="text-sm text-warn mb-2 font-semibold">Regenerate this agent's API key?</p>
                    <p className="text-xs text-ink-3 mb-2">
                      Revokes <code className="font-mono text-warn">{ak.prefix}...</code> immediately. Any SDK using
                      it will stop authenticating - make sure you can update it with the new key right away.
                    </p>
                    <div className="flex gap-2">
                      <button onClick={() => regenerate(a.id)}
                        className="px-3 py-1.5 bg-warn text-surface-raised font-semibold text-xs rounded hover:bg-warn/90 transition-colors">
                        Yes, regenerate
                      </button>
                      <button onClick={() => setConfirmRegenId(null)}
                        className="px-3 py-1.5 border border-surface-border text-xs rounded text-ink-3 hover:text-ink transition-colors">
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}



              {isExpanded && (
                <div className="px-4 pb-4">
                  <AgentProfileEditor
                    agentId={a.id}
                    initial={{
                      name: a.name,
                      enabled: a.enabled,
                      remit: a.remit,
                      m0_entries: a.m0_entries,
                      m3_entries: a.m3_entries,
                    }}
                    onSaved={reload}
                  />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

type AuditLogEntry = {
  id: string
  action: string
  target: string
  details: any
  user_id: string
  user_name: string
  user_email: string
  created_at: string
}

function ActivityTab() {
  const [data, setData] = useState<{ entries: AuditLogEntry[]; total: number }>({ entries: [], total: 0 })
  const [page, setPage] = useState(1)

  useEffect(() => {
    api(`/api/audit-log?page=${page}&per_page=20`)
      .then(r => setData(r.data || { entries: [], total: 0 }))
      .catch(() => {})
  }, [page])

  const isEmpty = !data.entries.length

  return (
    <div>
      <p className="text-xs text-ink-3 mb-4">
        Admin actions from the last 90 days: policy edits, agent-profile changes, key issuance and revocation.
      </p>

      {isEmpty ? (
        <div className="bg-surface-raised border border-surface-border rounded-lg p-8 text-center">
          <p className="text-sm text-ink mb-1">No activity yet</p>
          <p className="text-xs text-ink-3 max-w-md mx-auto">
            Policy changes and admin actions will be recorded here.
          </p>
        </div>
      ) : (
        <>
          <div className="hidden md:block bg-surface-raised border border-surface-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-ink-3 border-b border-surface-border bg-surface-overlay/50">
                  <th className="px-4 py-2.5 text-[13px] font-medium">Timestamp</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">User</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Action</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Target</th>
                  <th className="px-4 py-2.5 text-[13px] font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map(entry => (
                  <tr key={entry.id} className="border-b border-surface-border/50">
                    <td className="px-4 py-2.5 text-xs text-ink-3 font-mono">{timeAgo(entry.created_at)}</td>
                    <td className="px-4 py-2.5 text-xs">
                      {entry.user_name
                        ? <><span className="text-ink">{entry.user_name}</span><span className="text-ink-3 ml-1">{entry.user_email}</span></>
                        : <span className="text-ink-3">-</span>}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-2">{entry.action}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-3">{entry.target || '-'}</td>
                    <td className="px-4 py-2.5">
                      {entry.details && Object.keys(entry.details).length > 0 ? (
                        <details className="text-xs">
                          <summary className="cursor-pointer text-ink-3 hover:text-ink transition-colors font-mono">View</summary>
                          <div className="mt-1">
                            <JsonBlock value={entry.details} maxHeight="max-h-32" />
                          </div>
                        </details>
                      ) : (
                        <span className="text-ink-3 text-xs">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="md:hidden space-y-2">
            {data.entries.map(entry => {
              const hasDetails = entry.details && Object.keys(entry.details).length > 0
              return (
                <div key={entry.id} className="bg-surface-raised border border-surface-border rounded-lg p-3 space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[10px] text-ink-3">{timeAgo(entry.created_at)}</span>
                    {entry.user_name ? (
                      <span className="text-[11px] text-ink-3 font-mono truncate">{entry.user_name}</span>
                    ) : (
                      <span className="text-[11px] text-ink-3 font-mono">system</span>
                    )}
                  </div>
                  <p className="text-sm font-mono text-ink-2 break-all leading-snug">{entry.action}</p>
                  {entry.target && (
                    <p className="text-[11px] font-mono text-ink-3 break-all">{entry.target}</p>
                  )}
                  {hasDetails && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-ink-3 hover:text-ink transition-colors font-mono">Details</summary>
                      <div className="mt-1">
                        <JsonBlock value={entry.details} maxHeight="max-h-32" />
                      </div>
                    </details>
                  )}
                </div>
              )
            })}
          </div>

          <Pagination page={page} perPage={20} total={data.total} onChange={setPage} />
        </>
      )}
    </div>
  )
}

type WebhookEntry = {
  id: string
  platform: string
  webhook_url_masked: string
  alert_type: 'M3' | 'M4' | 'all'
  enabled: boolean
  created_at: string
}

type AlertType = 'M3' | 'M4' | 'all'

const ALERT_LABEL: Record<AlertType, string> = {
  M3: 'M3 only',
  M4: 'M4 only',
  all: 'All flagged',
}

function IntegrationsTab() {
  const [webhooks, setWebhooks] = useState<WebhookEntry[]>([])
  const [url, setUrl] = useState('')
  const [alertType, setAlertType] = useState<AlertType>('all')
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  function refresh() {
    api('/api/webhooks')
      .then(r => setWebhooks(r.data?.webhooks || []))
      .catch(() => {})
  }

  useEffect(refresh, [])

  async function add() {
    setStatus('saving')
    setErrorMsg('')
    try {
      await api('/api/webhooks', {
        method: 'POST',
        body: JSON.stringify({ webhook_url: url, alert_type: alertType }),
      })
      setUrl('')
      setAlertType('all')
      setStatus('idle')
      refresh()
    } catch (e: any) {
      setStatus('error')
      setErrorMsg(e?.message || 'Save failed')
    }
  }

  async function remove(id: string) {
    if (!confirm('Delete this webhook? Adrian will stop sending alerts to it.')) return
    try {
      await api(`/api/webhooks/${id}`, { method: 'DELETE' })
      refresh()
    } catch {
      // best-effort: leave the row visible
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div className="bg-surface-raised border border-surface-border rounded-lg p-4 space-y-3">
        <div>
          <h3 className="text-sm text-ink mb-1">Add a Discord webhook</h3>
          <p className="text-xs text-ink-3">
            Paste a Discord webhook URL (Server settings -&gt; Integrations -&gt; Webhooks). Adrian will POST a message there for every flagged verdict matching the alert type.
          </p>
        </div>
        <input
          placeholder="https://discord.com/api/webhooks/..."
          value={url}
          onChange={e => setUrl(e.target.value)}
          className="w-full px-3 py-2 border border-surface-border rounded text-sm bg-surface-overlay font-mono"
        />
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-ink-3">Alert on</span>
          {(Object.keys(ALERT_LABEL) as AlertType[]).map(a => (
            <label key={a} className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input
                type="radio"
                name="alert_type"
                checked={alertType === a}
                onChange={() => setAlertType(a)}
                className="accent-accent"
              />
              <span className="text-ink">{ALERT_LABEL[a]}</span>
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={add}
            disabled={!url || status === 'saving'}
            className="px-4 py-1.5 bg-surface-overlay border border-surface-border text-ink text-xs font-mono tracking-wider rounded hover:bg-ink/5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {status === 'saving' ? 'Saving...' : 'Add webhook'}
          </button>
          {status === 'error' && <span className="text-xs text-ink font-mono">{errorMsg}</span>}
        </div>
      </div>

      <div>
        <h3 className="text-[13px] font-medium text-ink-3 mb-3">Configured webhooks</h3>
        {webhooks.length === 0 ? (
          <p className="text-xs text-ink-3">None yet. Add one above.</p>
        ) : (
          <ul className="space-y-2">
            {webhooks.map(w => (
              <li key={w.id} className="bg-surface-raised border border-surface-border rounded-lg p-3 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-xs text-ink/80 truncate">{w.webhook_url_masked}</div>
                  <div className="text-[11px] text-ink-3 font-mono mt-1">
                    {w.platform.toUpperCase()} · {ALERT_LABEL[w.alert_type]} · {w.enabled ? 'enabled' : 'disabled'}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => remove(w.id)}
                  className="text-xs font-mono text-ink hover:underline flex-shrink-0"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
