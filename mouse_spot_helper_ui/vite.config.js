import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:18765',
      '/target-image': 'http://127.0.0.1:18765',
      '/screenshot.png': 'http://127.0.0.1:18765',
      '/static': 'http://127.0.0.1:18765',
    },
  },
});
