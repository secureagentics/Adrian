// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

export function TelegramIcon({ size = 28 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <defs>
        <linearGradient id="tg-grad" x1="0.5" y1="0" x2="0.5" y2="1">
          <stop offset="0" stopColor="#37bbfe" />
          <stop offset="1" stopColor="#007dbb" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="12" r="12" fill="url(#tg-grad)" />
      <path fill="#FFF" d="M5.491 11.74L17.03 7.284c.535-.193 1.003.13.83.94l-1.965 9.263c-.145.655-.535.815-1.084.508l-3-2.212-1.446 1.394c-.16.16-.295.295-.604.295l.213-3.053 5.56-5.024c.243-.212-.054-.334-.373-.122l-6.872 4.327-2.963-.925c-.643-.204-.656-.644.137-.935z"/>
    </svg>
  )
}
