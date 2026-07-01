export function timeAgo(date: string | Date): string {
  const seconds = Math.floor((Date.now() - new Date(date).getTime()) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

const PILL_NEUTRAL = 'bg-surface-raised text-ink-3 border-surface-border'
const PILL_SOFT    = 'bg-surface-overlay text-ink-2 border-surface-border'
const PILL_MID     = 'bg-ink-2 text-surface-raised border-ink-2'
const PILL_STRONG  = 'bg-ink text-surface-raised border-ink'
const PILL_ERROR   = 'bg-danger/20 text-danger border-danger/40'

export type VerdictDisplayInput = {
  mad_code?: string
  classification?: string
  verdict_status?: string
} | null | undefined

export function madBadgeColor(code: string): string {
  if (!code) return PILL_NEUTRAL
  if (code.startsWith('M4')) return PILL_STRONG
  if (code.startsWith('M3')) return PILL_MID
  if (code.startsWith('M2')) return PILL_SOFT
  return PILL_NEUTRAL
}

export function isClassifierErrorVerdict(verdict: VerdictDisplayInput): boolean {
  if (!verdict) return false
  return verdict.verdict_status === 'error' || (verdict.classification === 'error' && !verdict.mad_code)
}

export function verdictBadgeLabel(verdict: VerdictDisplayInput): string {
  if (isClassifierErrorVerdict(verdict)) return 'Classifier error'
  return verdict?.mad_code || 'No MAD code'
}

export function verdictBadgeColor(verdict: VerdictDisplayInput): string {
  if (isClassifierErrorVerdict(verdict)) return PILL_ERROR
  return madBadgeColor(verdict?.mad_code || '')
}

export function classificationBadgeColor(cls: string): string {
  switch (cls) {
    case 'BLOCK': return PILL_STRONG
    case 'NOTIFY': return PILL_MID
    default: return PILL_NEUTRAL
  }
}

export function statusBadgeColor(status: string): string {
  switch (status) {
    case 'pending': return PILL_SOFT
    case 'approved': return PILL_NEUTRAL
    case 'rejected': return PILL_MID
    default: return PILL_NEUTRAL
  }
}

export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '...' : s
}
