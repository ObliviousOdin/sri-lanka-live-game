import type { Era } from '../types'

/** An era with numeric bounds (inclusive years). */
export interface EraSpan { id: string; label: string; start: number; end: number }

export const LAST_YEAR = new Date().getFullYear()

export const DEFAULT_ERAS: Era[] = [
  { id: 'ancient', label: 'Ancient', start: null, end: 499 },
  { id: 'medieval', label: 'Medieval', start: 500, end: 1449 },
  { id: 'renaissance', label: 'Renaissance', start: 1450, end: 1749 },
  { id: 'industrial', label: 'Industrial', start: 1750, end: 1899 },
  { id: 'modern', label: 'Modern', start: 1900, end: 1944 },
  { id: 'atomic', label: 'Atomic', start: 1945, end: 1989 },
  { id: 'information', label: 'Information', start: 1990, end: null },
]

/** Resolve open-ended eras against the timeline's first and last year. */
export function resolveEras(eras: Era[], first: number, last = LAST_YEAR): EraSpan[] {
  return eras.map((e) => ({ id: e.id, label: e.label, start: e.start ?? first, end: e.end ?? last }))
}

/**
 * Era-paced scale: every era gets the same width on the slider, so the US's few centuries are
 * not squeezed against Sri Lanka's two and a half millennia. Position 0..1.
 */
export function yearToPos(y: number, spans: EraSpan[]): number {
  const n = spans.length
  if (y <= spans[0].start) return 0
  for (let i = 0; i < n; i++) {
    const s = spans[i]
    if (y <= s.end) return (i + (y - s.start) / Math.max(1, s.end - s.start + 1)) / n
  }
  return 1
}

export function posToYear(p: number, spans: EraSpan[]): number {
  const n = spans.length
  const x = Math.min(1, Math.max(0, p)) * n
  const i = Math.min(n - 1, Math.floor(x))
  const s = spans[i]
  return Math.min(s.end, Math.round(s.start + (x - i) * (s.end - s.start + 1)))
}

export function eraIndexOf(y: number, spans: EraSpan[]): number {
  const i = spans.findIndex((s) => y <= s.end)
  return i < 0 ? spans.length - 1 : i
}

/** Year label: 399 BCE / 1595. */
export function yearLabel(y: number): string {
  return y < 0 ? `${-y} BCE` : String(y)
}
