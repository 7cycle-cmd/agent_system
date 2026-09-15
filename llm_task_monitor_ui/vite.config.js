import { defineConfig } from 'vite';

export default defineConfig({
  base: '/llm-tasks/',
  server: {
    port: 5175,
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:18765',
    },
  },
  preview: {
    port: 5175,
    host: '127.0.0.1',
  },
});
