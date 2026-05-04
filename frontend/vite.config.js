import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// All API/SSE traffic goes directly to nginx at https://localhost:47291
// (set in start.sh as VITE_API_BASE_URL). No proxy needed.
export default defineConfig({
  plugins: [vue()],
})
