/**
 * Display formatting.
 *
 * Money arrives from the API as a STRING, deliberately: these are Decimals and
 * a JSON float would reintroduce the imprecision the whole system avoids. We
 * only convert to Number at the last moment, for display.
 *
 * Numerals default to Western digits (0-9) — that is what Egyptian receipts and
 * accounting use — with Eastern Arabic available as a display setting.
 */

const CURRENCY = 'ج.م'

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const amount = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(amount)) return '—'

  return `${amount.toLocaleString('en-EG', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${CURRENCY}`
}

export function quantity(value: string | number | null | undefined, unit = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  const amount = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(amount)) return '—'

  // Trim trailing zeros: 18.000 g reads as noise, 18 g reads as a quantity.
  const text = amount.toLocaleString('en-EG', { maximumFractionDigits: 3 })
  return unit ? `${text} ${unit}` : text
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ar-EG', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function time(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' })
}

export function relativeMinutes(value: string | null | undefined): string {
  if (!value) return '—'
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60_000)
  if (minutes < 1) return 'الآن'
  if (minutes < 60) return `منذ ${minutes} دقيقة`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `منذ ${hours} ساعة`
  return `منذ ${Math.floor(hours / 24)} يوم`
}

export function percent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('en-EG', { maximumFractionDigits: 2 })}%`
}
