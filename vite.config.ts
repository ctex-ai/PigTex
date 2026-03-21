import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

// Keep the renderer override env in sync with the packaging guard env.
const localhostApiOverride =
    process.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE
    ?? process.env.PIGTEX_ALLOW_LOCALHOST_API_BASE

if (
    typeof localhostApiOverride === 'string'
    && localhostApiOverride.trim()
    && process.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE === undefined
) {
    process.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE = localhostApiOverride
}

export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@': resolve(__dirname, 'src'),
            'lucide-react': resolve(__dirname, 'src/icons/lucide-react.tsx'),
        },
    },
    base: './',
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        rollupOptions: {
            output: {
                manualChunks(id) {
                    if (!id.includes('node_modules')) return
                    if (id.includes('react-dom') || id.includes('react/')) return 'vendor-react'
                    if (id.includes('framer-motion')) return 'vendor-motion'
                    if (id.includes('react-markdown') || id.includes('remark-') || id.includes('rehype-') || id.includes('katex') || id.includes('highlight.js')) {
                        return 'vendor-markdown'
                    }
                    if (id.includes('@phosphor-icons/react')) return 'vendor-icons'
                }
            }
        },
        chunkSizeWarningLimit: 700,
    },
    server: {
        port: 5173,
        strictPort: true,
    },
})
