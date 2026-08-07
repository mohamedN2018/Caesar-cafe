import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        // Arabic-first stack. Cairo/Tajawal render Arabic far better than the
        // default sans stack, which falls back to fonts with poor shaping.
        sans: ['Cairo', 'Tajawal', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config
