/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,html}'],
  theme: {
    extend: {
      colors: {
        canvas: '#f7f8fa',
        panel: '#ffffff',
        ink: '#1f2937',
        // muted: #6b7280 measured 4.39:1 on soft / 4.42:1 on #f4f5f7 /
        // 4.48:1 on #f5f6f8 — all below the 4.5:1 AA floor. #5b6472 measures
        // 5.89:1 on white, 5.45:1 on #f5f6f8, 5.35:1 on soft. `muted` is never
        // used as a background (0 matches for `bg-muted`), so darkening it
        // cannot break a fill it never sets.
        muted: '#5b6472',
        line: '#e5e7eb',
        soft: '#f3f4f6',
        accent: {
          // #3b82f6 measured 3.68:1 in BOTH directions (white on it, and it on
          // white) — the ratio is symmetric, so one value failed as a fill AND
          // as text. #2563eb (= Tailwind blue-600) measures 5.17:1 both ways.
          // The repo already used blue-600 as the HOVER, so the correct value
          // was already present; it is now the base, and the hovers moved to
          // blue-700 (#1d4ed8, 6.71:1) to keep hover feedback visible.
          DEFAULT: '#2563eb',
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
