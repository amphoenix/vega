import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// API base URL is injected via VITE_API_BASE_URL (.env.local, written by start.sh).
// No proxy — browser talks directly to Hypercorn over HTTPS (HTTP/2 + HTTP/3).
export default defineConfig({
  plugins: [vue()],
})
