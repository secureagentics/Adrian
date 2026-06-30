import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Manrope', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'Menlo', 'Monaco', 'monospace'],
      },
      colors: {
        ink: {
          DEFAULT: 'rgb(var(--ink) / <alpha-value>)',
          2: 'rgb(var(--ink-2) / <alpha-value>)',
          3: 'rgb(var(--ink-3) / <alpha-value>)',
          4: 'rgb(var(--ink-4) / <alpha-value>)',
          5: 'rgb(var(--ink-5) / <alpha-value>)',
        },
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          raised: 'rgb(var(--surface-raised) / <alpha-value>)',
          overlay: 'rgb(var(--surface-overlay) / <alpha-value>)',
          border: 'rgb(var(--surface-border) / <alpha-value>)',
          hover: 'rgb(var(--surface-hover) / <alpha-value>)',
        },
        // Semantic aliases — map to monochrome tokens so existing
        // class names (text-accent, bg-danger, etc.) keep working.
        accent: {
          DEFAULT: 'rgb(var(--ink) / <alpha-value>)',
          dim: 'rgb(var(--surface-overlay) / <alpha-value>)',
          glow: 'transparent',
        },
        danger: {
          DEFAULT: 'rgb(var(--ink) / <alpha-value>)',
          dim: 'rgb(var(--ink) / <alpha-value>)',
        },
        warn: {
          DEFAULT: 'rgb(var(--ink-2) / <alpha-value>)',
          dim: 'rgb(var(--surface-overlay) / <alpha-value>)',
        },
        muted: 'rgb(var(--ink-3) / <alpha-value>)',
        white: 'rgb(var(--ink) / <alpha-value>)',
      },
      borderRadius: {
        DEFAULT: '0.5rem',
        lg: '0.75rem',
      },
      boxShadow: {
        sm: '0 1px 2px rgb(15 15 15 / 0.04), 0 0 0 1px rgb(15 15 15 / 0.04)',
        DEFAULT: '0 4px 14px rgb(15 15 15 / 0.06), 0 0 0 1px rgb(15 15 15 / 0.04)',
        glow: 'none',
        'glow-sm': 'none',
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}

export default config
