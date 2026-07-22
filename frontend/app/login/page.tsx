// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { api, ApiError } from '@/lib/api'

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('admin@localhost')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const res = await api<{ data: { must_change_password: boolean } }>(
        '/api/auth/login',
        {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        },
      )
      if (res.data.must_change_password) {
        router.push('/change-password')
      } else {
        router.push('/')
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message || 'Sign-in failed')
      } else {
        setError('Sign-in failed')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-mono text-ink mb-1 text-center">Adrian</h1>
        <p className="text-sm text-ink-3 text-center mb-8">Sign in to your dashboard</p>
        <form onSubmit={handleSubmit} className="space-y-4 bg-surface-raised border border-surface-border rounded-lg p-6">
          <div>
            <label className="block text-[13px] font-medium text-ink-3 mb-2">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-surface border border-surface-border rounded px-3 py-2 text-sm text-ink focus:border-ink focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-[13px] font-medium text-ink-3 mb-2">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
              className="w-full bg-surface border border-surface-border rounded px-3 py-2 text-sm text-ink focus:border-ink focus:outline-none"
            />
          </div>
          {error && (
            <div className="text-sm text-ink bg-danger/10 border border-danger/30 rounded px-3 py-2">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full bg-ink text-surface-raised font-mono font-semibold py-2 rounded hover:bg-ink-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {busy ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
        <p className="text-xs text-ink-3 text-center mt-6">
          The admin password is shown once during <code className="text-ink-2">setup bootstrap</code>. If you lost it, see the <a href="https://docs.adrian.secureagentics.ai/reference/backend#reset-the-admin-password" className="text-ink-2 underline hover:text-ink-1">docs</a> to reset it.
        </p>
      </div>
    </div>
  )
}
