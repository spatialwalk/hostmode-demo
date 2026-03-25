import { defineConfig } from 'vite'
import { avatarkitVitePlugin } from '@spatialwalk/avatarkit/vite'

const backendOrigin = process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [avatarkitVitePlugin()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
      },
      '/healthz': {
        target: backendOrigin,
        changeOrigin: true,
      },
      '/ws': {
        target: backendOrigin.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
