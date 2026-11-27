import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev and preview servers listen on localhost only (Vite's default). To open the app from
// another device on the LAN (e.g. a phone), opt in per run: `npm run dev -- --host` or
// `npm run preview -- --host`.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: { port: 5173 },
  preview: { port: 4173 },
  build: { chunkSizeWarningLimit: 2500, sourcemap: false },
})
