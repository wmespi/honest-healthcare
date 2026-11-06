import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // `npm run dev` on the host is for working on the screen: it reloads on every
  // edit and sends /api to this checkout's running app, on the port its .env
  // gives (infra/README.md).
  const { APP_PORT = '8000' } = loadEnv(mode, '../..', 'APP_')
  return {
    plugins: [
      react(),
      tailwindcss(),
    ],
    server: {
      proxy: { '/api': `http://localhost:${APP_PORT}` },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.js'],
      css: false,
    },
  }
})
