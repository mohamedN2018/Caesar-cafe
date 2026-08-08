import type { Config } from 'tailwindcss'

/**
 * Every colour resolves to a CSS variable declared in `src/assets/brand.css`.
 *
 * Tailwind holds the scale names, the stylesheet holds the values, and the
 * Desktop reads the same values from `ui/theme.py`. A hex literal in this file
 * would be a fourth place the brand lives, and the one that drifts.
 */
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
        // كافيه القيصر: burgundy and gold. Deep enough to stay legible on the
        // cheap, washed-out screens these dashboards actually run on.
        brand: {
          50: 'var(--brand-50)',
          100: 'var(--brand-100)',
          200: 'var(--brand-200)',
          300: 'var(--brand-300)',
          400: 'var(--brand-400)',
          500: 'var(--brand-500)',
          600: 'var(--brand-600)',
          700: 'var(--brand-700)',
          800: 'var(--brand-800)',
          900: 'var(--brand-900)',
        },
        gold: {
          100: 'var(--gold-100)',
          200: 'var(--gold-200)',
          300: 'var(--gold-300)',
          400: 'var(--gold-400)',
          500: 'var(--gold-500)',
          600: 'var(--gold-600)',
          700: 'var(--gold-700)',
        },
        surface: {
          DEFAULT: 'var(--surface)',
          muted: 'var(--surface-muted)',
          sunken: 'var(--surface-sunken)',
        },
        ink: {
          DEFAULT: 'var(--ink)',
          muted: 'var(--ink-muted)',
          faint: 'var(--ink-faint)',
        },
      },
      borderColor: {
        DEFAULT: 'var(--border)',
        strong: 'var(--border-strong)',
      },
    },
  },
  plugins: [],
} satisfies Config
