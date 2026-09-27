import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: the FastAPI backend runs on :8099. /api and /ws are forwarded
// so the React app can be developed against the real backend.
// Production: `npm run build` emits web/dist, which FastAPI serves statically.
export default defineConfig({
  plugins: [react()],
  server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8099',
          changeOrigin: true,
        },
        '/ws': {
          target: 'ws://127.0.0.1:8099',
          ws: true,
          changeOrigin: true,
        },
      },
    },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
