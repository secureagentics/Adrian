// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

import { useState } from 'react'
import { Sidebar } from '@/components/sidebar'
import { Topbar } from '@/components/topbar'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  return (
    <div className="flex">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <div className="md:ml-56 flex-1 min-h-screen bg-surface">
        <Topbar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-4 md:p-6">{children}</main>
      </div>
    </div>
  )
}
