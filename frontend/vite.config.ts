import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_DEV_BACKEND_URL || 'http://localhost:8000'

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'autoUpdate',
        // Registered in main.tsx for the web only (the Android app ships its files in the APK).
        injectRegister: false,
        includeAssets: ['favicon.png', 'apple-touch-icon.png', 'loon-logo.png'],
        manifest: {
          name: 'LoonieToonie',
          short_name: 'LoonieToonie',
          description: 'Scan your receipts and see where your money goes.',
          theme_color: '#f7f4f6',
          background_color: '#f7f4f6',
          display: 'standalone',
          start_url: '/',
          icons: [
            { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
            { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
            { src: 'pwa-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          // API responses are never served from the service worker cache.
          navigateFallbackDenylist: [/^\/api\//],
          // pdf.js (1.2 MB) loads only when someone opens a PDF receipt; don't cache it for everyone.
          globIgnores: ['**/pdf.worker*', '**/pdf-*.js'],
        },
        devOptions: { enabled: false },
      }),
    ],
    server: {
      // In dev, /api/* is forwarded to FastAPI so the browser sees one origin.
      proxy: { '/api': { target: backendUrl, changeOrigin: true } },
    },
  }
})
