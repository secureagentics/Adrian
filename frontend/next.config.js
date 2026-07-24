// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // The dashboard fetches /api/* relatively. In compose the rewrite
  // proxies to the backend service over the docker network. For host
  // dev (`npm run dev` outside docker) set ADRIAN_API_URL=http://localhost:8080
  // in the shell or .env.local.
  async rewrites() {
    const apiTarget = process.env.ADRIAN_API_URL || 'http://backend:8080'
    return [
      { source: '/api/:path*', destination: `${apiTarget}/api/:path*` },
    ]
  },
}

module.exports = nextConfig
