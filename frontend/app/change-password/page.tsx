// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { api, ApiError } from '@/lib/api'

export default function ChangePasswordPage() {
  const router = useRouter()
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match')
      return
    }
    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters')
      return
    }
    setBusy(true)
    try {
      await api('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
      })
      router.push('/')
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message || 'Update failed')
      } else {
        setError('Update failed')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-mono text-ink mb-1 text-center">Set a new password</h1>
        <p className="text-sm text-ink-3 text-center mb-8">
          You're using a temporary password from setup. Pick something only you know.
        </p>
        <form onSubmit={handleSubmit} className="space-y-4 bg-surface-raised border border-surface-border rounded-lg p-6">
          <div>
            <label className="block text-[13px] font-medium text-ink-3 mb-2">Current password</label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              required
              autoFocus
              className="w-full bg-surface border border-surface-border rounded px-3 py-2 text-sm text-ink focus:border-ink focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-[13px] font-medium text-ink-3 mb-2">New password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={8}
              className="w-full bg-surface border border-surface-border rounded px-3 py-2 text-sm text-ink focus:border-ink focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-[13px] font-medium text-ink-3 mb-2">Confirm new password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
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
            {busy ? 'Updating...' : 'Update password'}
          </button>
        </form>
      </div>
    </div>
  )
}
