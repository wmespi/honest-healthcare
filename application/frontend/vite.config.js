import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  // The dev server is only reachable via loopback (compose binds 127.0.0.1) and
  // `tailscale serve` (authenticated tailnet). Vite's host check is DNS-rebinding
  // protection for *exposed* dev servers — moot here, and `['.ts.net']` rejected
  // the short MagicDNS name (`http://<machine>:5173`, no domain suffix). Allow all.
  server: {
    allowedHosts: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    css: false,
  },
})
