// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

'use client'

// Minimal theme provider — no deps. Persists choice to localStorage and
// adds/removes `dark` on <html>. Pre-mount script in <head> avoids FOUC
// by setting the class before the first paint.

import { createContext, useContext, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'adrian-theme'

type ThemeContextValue = {
  theme: Theme
  resolved: ResolvedTheme
  setTheme: (t: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('system')
  const [resolved, setResolved] = useState<ResolvedTheme>('light')

  // Read initial state from <html> (set by the pre-mount script in layout).
  useEffect(() => {
    const saved = (typeof window !== 'undefined'
      && (window.localStorage.getItem(STORAGE_KEY) as Theme | null)) || 'system'
    setThemeState(saved)
    document.documentElement.classList.add('theme-ready')
  }, [])

  // React to theme + system changes.
  useEffect(() => {
    function apply(t: Theme) {
      const isDark = t === 'dark'
        || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.documentElement.classList.toggle('dark', isDark)
      setResolved(isDark ? 'dark' : 'light')
    }
    apply(theme)
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => apply('system')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [theme])

  function setTheme(t: Theme) {
    setThemeState(t)
    try { window.localStorage.setItem(STORAGE_KEY, t) } catch {}
  }

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}

// Inline script: runs before React mounts so the first paint already has
// the right class on <html>. Returned as a string to inject via
// dangerouslySetInnerHTML in the root layout's <head>.
export const themeInitScript = `
(function(){try{
  var t=localStorage.getItem('${STORAGE_KEY}')||'system';
  var d=t==='dark'||(t==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);
  if(d)document.documentElement.classList.add('dark');
}catch(e){}})();
`.trim()
