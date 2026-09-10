import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const echoTarget = process.env.ECHO_API_PROXY_TARGET ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      '/api': {
        target: echoTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      },
      '/events': {
        target: echoTarget,
        changeOrigin: true,
        ws: true
      }
    }
  }
});
