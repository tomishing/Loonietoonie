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
        includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
        manifest: {
          name: 'LoonieToonie',
          short_name: 'LoonieToonie',
          description: 'Scan receipts, track spending, and see how much you can save.',
          theme_color: '#ffb5c8',
          background_color: '#fff8fb',
          display: 'standalone',
          start_url: '/',
          icons: [
            { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
            { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
            { src: 'pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          // API responses are never served from the service worker cache.
          navigateFallbackDenylist: [/^\/api\//],
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
