<script setup lang="ts">
/**
 * The icon set.
 *
 * This replaced emoji. Emoji were quick and they were the single thing that
 * made the product look unserious: they render as a different picture on every
 * platform (a coffee cup on Windows is not the coffee cup on a phone), they are
 * colourful when nothing around them is, they cannot inherit the text colour of
 * the thing they sit next to, and they cannot be made to line up — an emoji's
 * baseline and weight are decided by a font nobody in this project controls.
 *
 * These are drawn instead: one 24×24 grid, one stroke width, `currentColor`
 * everywhere. That last part is what makes them look designed rather than
 * pasted — the icon in an active nav row is the colour of the active nav row,
 * automatically, and there is no palette to keep in sync.
 *
 * **Inline, not an icon library.** A published set would be a dependency and a
 * CDN request for two dozen glyphs, and the artifact CSP forbids the CDN
 * anyway. Two dozen paths cost less than the package that would supply them.
 *
 * `aria-hidden` is on every one of them. Each is decorative and sits beside a
 * real Arabic label; announcing "chart icon, Dashboard" to a screen reader says
 * the same thing twice.
 */
withDefaults(
  defineProps<{
    name: string
    /** Matches the surrounding text by default; `1em` scales with it. */
    size?: string | number
  }>(),
  { size: '1.25em' },
)

/**
 * Path data only. Every glyph is drawn on the same 24×24 box with round caps
 * and joins, which is what stops a set assembled over time from looking like a
 * set assembled over time.
 */
const PATHS: Record<string, string[]> = {
  // ── navigation ──────────────────────────────────────────────────────────
  dashboard: ['M3 13h4v8H3zM10 3h4v18h-4zM17 9h4v12h-4z'],
  pos: ['M4 4h16v16H4z', 'M4 9h16', 'M8 13h2M8 17h2M14 13h2M14 17h2'],
  receipt: ['M6 3h12v18l-3-2-3 2-3-2-3 2z', 'M9 8h6M9 12h6'],
  cash: ['M2 6h20v12H2z', 'M12 12m-2.5 0a2.5 2.5 0 105 0a2.5 2.5 0 10-5 0', 'M5 9v6M19 9v6'],
  chart: ['M4 20V10M10 20V4M16 20v-7M22 20H2'],
  table: ['M4 10h16', 'M6 10v10M18 10v10', 'M8 6h8a2 2 0 012 2v2H6V8a2 2 0 012-2z'],
  kitchen: ['M4 12h16', 'M6 12a6 6 0 0112 0', 'M4 16h16v3H4z', 'M9 6V3M15 6V3'],
  station: ['M12 3c2 3 4 4 4 7a4 4 0 11-8 0c0-3 2-4 4-7z', 'M12 21v-3'],
  kids: ['M12 12m-4 0a4 4 0 108 0a4 4 0 10-8 0', 'M7 7L5 4M17 7l2-3', 'M6 21a6 6 0 0112 0'],
  clock: ['M12 12m-9 0a9 9 0 1018 0a9 9 0 10-18 0', 'M12 7v5l3 2'],
  guardians: ['M8 11m-3 0a3 3 0 106 0a3 3 0 10-6 0', 'M2 21a6 6 0 0112 0', 'M17 8a3 3 0 010 6', 'M18 21a6 6 0 00-2-4.5'],
  clipboard: ['M9 4h6v3H9z', 'M9 5H6v16h12V5h-3', 'M9 11h6M9 15h4'],
  ticket: ['M3 8a2 2 0 002-2h14a2 2 0 002 2v2a2 2 0 000 4v2a2 2 0 00-2 2H5a2 2 0 00-2-2v-2a2 2 0 000-4z', 'M12 8v8'],
  cup: ['M5 8h11v6a5 5 0 01-10 0z', 'M16 9h2a2 2 0 010 4h-2', 'M6 21h11'],
  folders: ['M3 7a1 1 0 011-1h4l2 2h7a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1z', 'M7 6V4h4l2 2'],
  box: ['M12 3l8 4v10l-8 4-8-4V7z', 'M4 7l8 4 8-4M12 11v10'],
  history: ['M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8', 'M3 3v5h5', 'M12 8v4l3 2'],
  truck: ['M2 7h11v9H2z', 'M13 10h4l3 3v3h-7z', 'M6 19m-1.5 0a1.5 1.5 0 103 0a1.5 1.5 0 10-3 0', 'M17 19m-1.5 0a1.5 1.5 0 103 0a1.5 1.5 0 10-3 0'],
  inbox: ['M4 4h16v10h-5a3 3 0 01-6 0H4z', 'M4 14v6h16v-6'],
  // A bin with a lid and two staves. Drawn rather than an emoji, like the rest —
  // an emoji renders in whatever font the machine happens to have.
  trash: ['M4 7h16', 'M9 7V4h6v3', 'M6 7l1 13h10l1-13', 'M10 11v6M14 11v6'],
  bell: ['M18 15V10a6 6 0 10-12 0v5l-2 3h16z', 'M10 21h4'],
  users: ['M9 10m-4 0a4 4 0 108 0a4 4 0 10-8 0', 'M2 21a7 7 0 0114 0', 'M17 7a4 4 0 010 6', 'M19 21a7 7 0 00-3-5.7'],
  key: ['M15 9m-4 0a4 4 0 108 0a4 4 0 10-8 0', 'M11.5 11.5L3 20v2h3v-2h2v-2h2z'],
  monitor: ['M3 4h18v12H3z', 'M9 20h6M12 16v4'],
  sync: ['M20 12a8 8 0 01-13.6 5.7L4 16', 'M4 12a8 8 0 0113.6-5.7L20 8', 'M4 21v-5h5M20 3v5h-5'],
  shield: ['M12 3l8 3v6c0 5-3.4 8.4-8 9-4.6-.6-8-4-8-9V6z', 'M9 12l2 2 4-4'],
  save: ['M5 4h11l3 3v13H5z', 'M8 4v5h7V4M8 20v-6h8v6'],
  printer: ['M7 9V4h10v5', 'M5 9h14a2 2 0 012 2v5h-4', 'M7 16H3v-5a2 2 0 012-2', 'M7 14h10v7H7z'],
  settings: ['M12 12m-3 0a3 3 0 106 0a3 3 0 10-6 0', 'M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.2 2.2M16.9 16.9l2.2 2.2M19.1 4.9l-2.2 2.2M7.1 16.9l-2.2 2.2'],

  camera: ['M3 8h4l2-2h6l2 2h4v12H3z', 'M12 13m-3.5 0a3.5 3.5 0 107 0a3.5 3.5 0 10-7 0'],

  // ── states ──────────────────────────────────────────────────────────────
  empty: ['M4 7h16v13H4z', 'M4 12h5a3 3 0 006 0h5', 'M8 4h8'],
  check: ['M4 13l5 5L20 7'],
  close: ['M6 6l12 12M18 6L6 18'],
  alert: ['M12 3.5L22 20H2z', 'M12 10v4', 'M12 17.2v.1'],
  info: ['M12 12m-9 0a9 9 0 1018 0a9 9 0 10-18 0', 'M12 11v5', 'M12 7.8v.1'],
  // The delta arrows. Drawn rather than the solid up/down triangles, which are
  // font-dependent: the two glyphs are different sizes in most Arabic-capable
  // faces, so a rising figure and a falling one were not the same weight next
  // to each other.
  'arrow-up': ['M12 20V5', 'M6 11l6-6 6 6'],
  'arrow-down': ['M12 4v15', 'M6 13l6 6 6-6'],
  silent: ['M18 15V10a6 6 0 00-9.3-5', 'M6 9v6l-2 3h13', 'M3 3l18 18'],
  balloon: ['M12 14c3 0 5-2.7 5-6a5 5 0 10-10 0c0 3.3 2 6 5 6z', 'M12 14v2', 'M10 20c1-1 3-1 4 0'],
  child: ['M12 8m-3 0a3 3 0 106 0a3 3 0 10-6 0', 'M7 21a5 5 0 0110 0', 'M12 3v1'],
  note: ['M5 3h9l5 5v13H5z', 'M14 3v5h5', 'M8 13h7M8 17h5'],
  lost: ['M12 12m-9 0a9 9 0 1018 0a9 9 0 10-18 0', 'M15 9l-2.2 4.8L8 16l2.2-4.8z'],
}

function paths(name: string): string[] {
  // An unknown name renders the neutral box rather than nothing: a missing
  // icon that leaves a hole in a row is harder to spot in review than one that
  // is visibly the wrong picture.
  return PATHS[name] ?? PATHS.empty
}
</script>

<template>
  <svg
    class="ui-icon"
    :style="{ width: typeof size === 'number' ? `${size}px` : size, height: typeof size === 'number' ? `${size}px` : size }"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in paths(name)" :key="index" :d="d" />
  </svg>
</template>

<style scoped>
.ui-icon {
  display: inline-block;
  flex: 0 0 auto;
  /* Pulls the glyph onto the text baseline. Without it an inline SVG sits on
     the line box bottom and every label it accompanies looks a pixel high. */
  vertical-align: -0.15em;
}
</style>
