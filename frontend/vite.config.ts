import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  build: {
    outDir: '../app/static/ui',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          antd: ['antd', '@ant-design/icons'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8420',
        changeOrigin: true,
        headers: { Origin: 'http://127.0.0.1:8420' },
      },
    },
  },
})
