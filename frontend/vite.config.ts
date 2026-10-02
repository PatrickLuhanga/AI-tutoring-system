import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Wire straight into the Flask API Gateway once the backend is running.
      // The mock layer ignores this; it exists so swapping mock -> real is a
      // one-line change in src/api/client.ts.
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
      // The corpus pages are served by Flask too, not by the SPA. Without this
      // a citation link such as /resources/IPRT301/slides/01_Inheritance.md
      // falls through to Vite's SPA fallback and silently renders the React app
      // shell instead of the notes - a 200 that shows the wrong thing.
      '/resources': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
})
