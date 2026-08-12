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
        // The state colours existed in `brand.css` and were never mapped here,
        // so every screen that needed a warning reached for `amber-50` /
        // `amber-900` straight out of Tailwind's default palette — which is not
        // the brand's amber and drifts a shade further with every screen that
        // guesses. Mapped once, they stay one colour.
        success: {
          DEFAULT: 'var(--success)',
          bg: 'var(--success-bg)',
        },
        warning: {
          DEFAULT: 'var(--warning)',
          bg: 'var(--warning-bg)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          bg: 'var(--danger-bg)',
        },
        info: {
          DEFAULT: 'var(--info)',
          bg: 'var(--info-bg)',
        },
        // A NAMED line colour, so it can be written as `border-line`,
        // `divide-line` and `ring-line`. `borderColor.DEFAULT` cannot serve
        // that job: the bare class `border` is Tailwind's border-WIDTH utility,
        // so mapping a colour onto it silently draws a 1px box around whatever
        // it lands on — and `divide` and `ring` have no bare form at all.
        line: {
          DEFAULT: 'var(--border)',
          strong: 'var(--border-strong)',
        },
        // The dim behind a modal. Written as `bg-scrim` so the one at each call
        // site cannot drift — the hand-written ones were `black/40` in some
        // dialogs and `black/50` in others, and a product that dims the room
        // differently per dialog reads as two products.
        scrim: 'var(--scrim)',
      },
      borderColor: {
        DEFAULT: 'var(--border)',
        strong: 'var(--border-strong)',
      },
      // Depth, from the same file as the colour. Tailwind's own shadows are
      // black-tinted, and pure black over a cream surface greys it — the card
      // stops looking like paper on a warm desk and starts looking like paper
      // behind glass. Overriding the scale rather than extending it means a
      // stray `shadow-md` picks up the warm one instead of the default.
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        DEFAULT: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
        brand: 'var(--shadow-brand)',
        none: 'none',
      },
      backgroundImage: {
        brand: 'var(--brand-gradient)',
        'brand-soft': 'var(--brand-gradient-soft)',
        gold: 'var(--gold-gradient)',
        'page-wash': 'var(--page-wash)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
      },
      transitionDuration: {
        fast: 'var(--duration-fast)',
        base: 'var(--duration-base)',
        slow: 'var(--duration-slow)',
      },
    },
  },
  plugins: [],
} satisfies Config
