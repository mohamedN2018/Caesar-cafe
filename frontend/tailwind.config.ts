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
      colors: {
        // Deep blue, not the usual SaaS indigo: it has to stay legible on the
        // cheap, washed-out screens these dashboards actually run on.
        brand: {
          50: '#eef4fb',
          100: '#d6e4f5',
          200: '#adc9ea',
          300: '#7ea9dc',
          400: '#4f88cd',
          500: '#2e6ab4',
          600: '#245699',
          700: '#1d4e89',
          800: '#17406f',
          900: '#123456',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
