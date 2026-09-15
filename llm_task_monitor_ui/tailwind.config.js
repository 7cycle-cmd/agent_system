/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,html}'],
  theme: {
    extend: {
      colors: {
        canvas: '#f7f8fa',
        panel: '#ffffff',
        ink: '#1f2937',
        muted: '#6b7280',
        line: '#e5e7eb',
        soft: '#f3f4f6',
        accent: {
          DEFAULT: '#3b82f6',
          soft: '#eff6ff',
          mid: '#bfdbfe',
        },
      },
      boxShadow: {
        panel: '0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.04)',
      },
    },
  },
  plugins: [],
};
