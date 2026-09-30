import { defineConfig } from 'vite';

export default defineConfig({
  base: '/llm-tasks/',
  // Vue needs its full build (in-browser template compiler) because Chat Center
  // declares its markup as a `template` string. The default `vue` entry is
  // runtime-only -> renders an empty placeholder.
  resolve: {
    alias: {
      vue: 'vue/dist/vue.esm-bundler.js',
    },
  },
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
