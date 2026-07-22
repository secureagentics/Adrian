// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import type { Metadata } from 'next'
import './globals.css'
import { ThemeProvider, themeInitScript } from '@/components/theme-provider'

export const metadata: Metadata = {
  title: 'Adrian',
  description: 'AI agent security monitoring and control',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  )
}
