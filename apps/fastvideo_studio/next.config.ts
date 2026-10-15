import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { NextConfig } from 'next';

const configDir = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  // Point tracing at the monorepo root (apps/fastvideo_studio -> apps -> repo
  // root) so Next stops warning about multiple lockfiles in the workspace.
  outputFileTracingRoot: path.join(configDir, '..', '..'),
  // With NEXT_PUBLIC_API_BASE_URL=/api, remote users need only forward the web port.
  async rewrites() {
    const target = (process.env.STUDIO_API_PROXY_TARGET || 'http://127.0.0.1:8189').replace(/\/$/, '');
    return [{source: '/api/:path*', destination: `${target}/api/:path*`}];
  },
};

export default nextConfig;
