import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync, cpSync, mkdirSync } from 'fs'
import { resolve } from 'path'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    // After build: copy mlb/predictions into site/ so Firebase always has fresh data
    {
      name: 'mlb-data-copy',
      closeBundle() {
        const src  = resolve(__dirname, '..', 'mlb', 'predictions')
        const dest = resolve(__dirname, '..', 'site', 'mlb', 'predictions')
        mkdirSync(dest, { recursive: true })
        cpSync(src, dest, { recursive: true })
      },
    },
    // Dev middleware: serves ../mlb/ as /mlb/ so fetch paths work identically in dev and prod
    {
      name: 'mlb-data-server',
      configureServer(server) {
        server.middlewares.use('/mlb', (req, res, next) => {
          const filePath = resolve(__dirname, '..', 'mlb', decodeURIComponent(req.url.replace(/^\//, '')))
          try {
            const data = readFileSync(filePath)
            const ext = filePath.split('.').pop().toLowerCase()
            const ct =
              ext === 'json' ? 'application/json' :
              ext === 'csv'  ? 'text/csv; charset=utf-8' :
              'text/plain'
            res.setHeader('Content-Type', ct)
            res.setHeader('Cache-Control', 'no-cache')
            res.end(data)
          } catch {
            res.statusCode = 404
            res.end()
          }
        })
      },
    },
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons/*.png'],
      manifest: false, // we use our own /manifest.json in public/
      workbox: {
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        globPatterns: ['**/*.{js,css,html,svg,woff2}'],
        runtimeCaching: [
          {
            urlPattern: /\/mlb\/predictions\/.+\.(json|csv)$/,
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  base: './',
  build: {
    // Output directly into E:\BettingModel\ so index.html sits next to mlb/
    // This means fetch('./mlb/predictions/...') resolves correctly when served from project root
    outDir: '../site',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          charts: ['chart.js', 'react-chartjs-2'],
          motion: ['framer-motion'],
        },
      },
    },
  },
  server: {
    port: 5173,
    fs: { allow: ['..'] },
  },
})
