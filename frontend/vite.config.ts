/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The backend's CORS_ORIGINS default is http://localhost:5173, so this port
    // is not arbitrary — changing it means changing the backend too.
    strictPort: true,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          firebase: ['firebase/app', 'firebase/auth'],
          leaflet: ['leaflet'],
          react: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // The reporting wizard tests drive a seven-step form through real user
    // events; each keystroke re-renders it. 5s is too tight for that under load.
    testTimeout: 20_000,
  },
})
