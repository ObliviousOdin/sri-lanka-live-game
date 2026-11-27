// Number formatting helpers

const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumSignificantDigits: 3 })
const plain = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export function fmtNumber(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return '–'
  const a = Math.abs(v)
  if (a >= 10000) return compact.format(v)
  if (a >= 100) return int.format(v)
  if (a >= 1) return plain.format(v)
  if (a === 0) return '0'
  return v.toPrecision(Math.max(1, digits - 1))
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '–'
  const a = Math.abs(v)
  if (a >= 1e12) return `$${(v / 1e12).toPrecision(3)}T`
  if (a >= 1e9) return `$${(v / 1e9).toPrecision(3)}B`
  if (a >= 1e6) return `$${(v / 1e6).toPrecision(3)}M`
  if (a >= 1e3) return `$${(v / 1e3).toPrecision(3)}k`
  return `$${v.toFixed(0)}`
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '–'
  return `${v.toFixed(digits)}%`
}

/**
 * Display form of a unit code (the one implementation for the map and its legends): km2 → km²,
 * degC → °C, deg → °, a trailing /year → /yr (as in 'USD/yr'), persons → people. Count-like and
 * unitless codes ('count', 'index') become empty, so 'count/km2' reads '/km²'.
 */
export function prettyUnit(unit: string): string {
  return unit
    .trim()
    .replace(/km2\b/g, 'km²')
    .replace(/\bdegC\b/g, '°C')
    .replace(/^deg$/, '°')
    .replace(/\/year$/i, '/yr')
    .replace(/^persons?\b/, 'people')
    .replace(/^(count|index)\b/, '')
}

/** Format a metric value according to its unit string. */
export function fmtUnit(v: number | null | undefined, unit: string): string {
  if (v == null || !Number.isFinite(v)) return '–'
  const u = unit.trim().toLowerCase()
  // money: '$2.92k/yr', '$1.2M/km²' (no space before the per-unit)
  if (u === 'usd' || u.startsWith('usd') || u.startsWith('us$')) {
    const slash = unit.indexOf('/')
    return fmtUsd(v) + (slash >= 0 ? prettyUnit(unit.slice(slash)) : '')
  }
  if (u === '%' || u === 'percent' || u === 'pct') return fmtPct(v)
  if (u === 'fraction' || u === '0-1' || u === 'share') return fmtPct(v * 100)
  // area per area (e.g. km² of cropland per km² of tile) is a share
  if (/^km(2|²)\/km(2|²)$/.test(u)) return fmtPct(v * 100)
  if (u === 'persons' || u === 'count' || u === 'people' || u === 'index' || u === '') return fmtNumber(v)
  // 0 here means "no M5+ earthquake recorded", not a magnitude-0 event
  if (u === 'magnitude') return v > 0 ? `M${v.toFixed(1)}` : 'none ≥ M5'
  const p = prettyUnit(unit)
  if (!p) return fmtNumber(v)
  if (p === '°' || p === '°C') return `${fmtNumber(v)}${p}`
  return p.startsWith('/') ? `${fmtNumber(v)}${p}` : `${fmtNumber(v)} ${p}`
}

export function fmtInt(v: number | null | undefined) {
  if (v == null || !Number.isFinite(v)) return '–'
  return int.format(v)
}

export function fmtArea(km2: number) {
  return `${fmtNumber(km2)} km²`
}
