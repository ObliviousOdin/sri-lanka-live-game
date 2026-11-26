// Advisors panel — shared helpers: wording, number formatting, national aggregates, percentile
// ranks over tiles / admin units, and "places" (the selected tile, admin unit or city).
// Every cache is a WeakMap keyed by the CountryData object, so a pipeline re-export (which creates
// new objects) invalidates it automatically.
import { cellToLatLng } from 'h3-js'
import { terrainLabel } from '../civ'
import { adminUnitOfHex, column } from '../data/load'
import { fmtUsd } from '../format'
import type { AdminUnit, City, CountryData, CountryId, IndexJson, Metric, Selection, YieldKey } from '../types'

// ---------------------------------------------------------------------------------------------
// Advisors
// ---------------------------------------------------------------------------------------------
export type AdvisorId = 'economic' | 'military' | 'cultural' | 'scientific' | 'environmental'

export const ADVISORS: { id: AdvisorId; title: string; role: string; emoji: string; color: string }[] = [
  { id: 'economic', title: 'Treasurer', role: 'Economic advisor', emoji: '💰', color: '#f0c43a' },
  { id: 'military', title: 'General', role: 'Military advisor', emoji: '🛡️', color: '#e5604c' },
  { id: 'cultural', title: 'Curator', role: 'Cultural advisor', emoji: '🎭', color: '#c07ff0' },
  { id: 'scientific', title: 'Chief Scientist', role: 'Scientific advisor', emoji: '🔬', color: '#53b3f0' },
  { id: 'environmental', title: 'Steward', role: 'Environmental advisor', emoji: '🌿', color: '#7cc24e' },
]

// ---------------------------------------------------------------------------------------------
// Comments
// ---------------------------------------------------------------------------------------------
/** A text segment; objects are figures, highlighted in their country's colour. */
export type Seg = string | { t: string; c?: CountryId }
export const fig = (t: string, c?: CountryId): Seg => ({ t, c })

export interface Comment {
  id: string
  /** emoji, or `y:<yield>` for a yield icon */
  icon: string
  segs: Seg[]
  sources: string[]
  /** vintage / method note shown under the sentence */
  meta?: string
  /** relative to the "home" civilization */
  status?: 'ahead' | 'behind' | 'even'
  /** percentile of the place within its own country (0-100) */
  pct?: number
  pctBetter?: 'higher' | 'lower' | 'neutral'
  /** strength of the contrast, used to pick the verdict */
  weight?: number
  /** verdict phrasing (defaults to segs) */
  verdict?: Seg[]
}

// ---------------------------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------------------------
export const ISO: Record<CountryId, 'LKA' | 'USA'> = { lka: 'LKA', usa: 'USA' }
export const otherOf = (c: CountryId): CountryId => (c === 'lka' ? 'usa' : 'lka')
const NAMES: Record<CountryId, string> = { lka: 'Sri Lanka', usa: 'the United States' }
const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
/** "Sri Lanka" / "the United States" (capitalised at sentence start). */
export const nm = (c: CountryId, cap = false) => (cap ? cap1(NAMES[c]) : NAMES[c])
/** "Sri Lanka's" / "the United States'". */
export const poss = (c: CountryId, cap = false) => (c === 'usa' ? `${nm(c, cap)}'` : `${nm(c, cap)}'s`)
export const ADJ: Record<CountryId, string> = { lka: 'Sri Lankan', usa: 'US' }
export const SHORT: Record<CountryId, string> = { lka: 'Sri Lanka', usa: 'US' }

export function ordinal(p: number) {
  const n = Math.round(p)
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${s}`
}

export function pluralize(label: string, n = 2) {
  if (n === 1) return label
  if (/[^aeiou]y$/i.test(label)) return label.slice(0, -1) + 'ies'
  return label + 's'
}

// ---------------------------------------------------------------------------------------------
// Number formatting
// ---------------------------------------------------------------------------------------------
const compactFmt = new Intl.NumberFormat('en-US', { notation: 'compact', maximumSignificantDigits: 3 })
const intFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export function money(v: number) {
  if (!ok(v)) return '–'
  const a = Math.abs(v)
  if (a >= 1e6) return fmtUsd(v)
  if (a >= 100) return `$${intFmt.format(v)}`
  return `$${v.toFixed(a >= 10 ? 0 : 2)}`
}

export function pct(v: number) {
  if (!ok(v)) return '–'
  if (v === 0) return '0%'
  const a = Math.abs(v)
  return `${v.toFixed(a >= 1 ? 1 : a >= 0.1 ? 2 : 3)}%`
}

export function count(v: number) {
  if (!ok(v)) return '–'
  const a = Math.abs(v)
  if (a >= 1e4) return compactFmt.format(v)
  if (a >= 100) return intFmt.format(v)
  if (a >= 10) return v.toFixed(1).replace(/\.0$/, '')
  if (a === 0) return '0'
  return v.toPrecision(2).replace(/\.0+$/, '')
}

/** "about 30×" style ratio text. */
export function times(k: number) {
  if (!ok(k)) return '–'
  if (k >= 100) return `${intFmt.format(k)}×`
  if (k >= 10) return `${Math.round(k)}×`
  return `${k.toFixed(1)}×`
}

export const prettyLabel = (l: string) => l.replace(/^Land cover:\s*/i, '').replace(/km2/g, 'km²')

/** Format a value of a metric according to its unit. */
export function fmtMetric(unit: string, v: number): string {
  if (!ok(v)) return '–'
  const u = unit.trim().toLowerCase()
  if (u === 'share' || u === 'fraction' || u === '0-1') return pct(v * 100)
  if (u === '%' || u === 'percent' || u === 'pct') return pct(v)
  if (u.startsWith('usd') || u.startsWith('us$')) return money(v) + (u.includes('/yr') ? '/yr' : '')
  if (u === 'persons/km2') return `${count(v)} /km²`
  if (u === 'm') return `${intFmt.format(v)} m`
  if (u === 'deg') return `${v.toFixed(1)}°`
  if (u === 'degc') return `${v.toFixed(1)} °C`
  if (u === 'mm/yr') return `${intFmt.format(v)} mm/yr`
  if (u === 'index') return v.toFixed(1)
  if (u === 'persons' || u === 'people' || u === 'count' || u === '') return count(v)
  if (u === 't') return tonnes(v)
  if (u === 'km2') return `${count(v)} km²`
  return `${count(v)} ${unit.replace(/km2/g, 'km²')}`
}

export function tonnes(v: number) {
  if (!ok(v)) return '–'
  if (Math.abs(v) >= 1e9) return `${count(v / 1e6)} Mt`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toPrecision(3)} Mt`
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toPrecision(3)} kt`
  return `${count(v)} t`
}

// ---------------------------------------------------------------------------------------------
// National aggregates from the tile matrix
// ---------------------------------------------------------------------------------------------
const aggCache = new WeakMap<CountryData, Map<string, number>>()

function cacheFor<T>(wm: WeakMap<object, Map<string, T>>, o: object) {
  let m = wm.get(o)
  if (!m) { m = new Map(); wm.set(o, m) }
  return m
}

/** National value of a metric: total for extensive metrics, weighted mean (by the metric's weight) for intensive ones, over land tiles. */
export function natAgg(d: CountryData, key: string): number {
  const m = d.metricByKey.get(key)
  if (!m) return NaN
  const cache = cacheFor(aggCache, d)
  const hit = cache.get(key)
  if (hit !== undefined) return hit
  const col = column(d, key)!
  const ring = d.hexes.ring
  let out = NaN
  if (m.kind === 'extensive') {
    if (ok(m.stats?.total)) out = m.stats.total!
    else {
      let s = 0, n = 0
      for (let i = 0; i < d.n; i++) if (ring[i] === 0 && Number.isFinite(col[i])) { s += col[i]; n++ }
      out = n ? s : NaN
    }
  } else {
    const wk = m.weight === 'pop' ? 'pop' : m.weight === 'cropland' ? 'cropland_km2' : 'land_km2'
    const w = column(d, wk)
    let s = 0, ws = 0
    for (let i = 0; i < d.n; i++) {
      if (ring[i] !== 0) continue
      const v = col[i]
      if (!Number.isFinite(v)) continue
      const wi = w ? w[i] : d.hexArea[i]
      if (!(wi > 0)) continue
      s += v * wi; ws += wi
    }
    out = ws > 0 ? s / ws : NaN
  }
  cache.set(key, out)
  return out
}

// ---------------------------------------------------------------------------------------------
// Measures: a value per tile or admin unit, derived from one or more metrics
// ---------------------------------------------------------------------------------------------
export interface Measure {
  id: string
  label: string
  fmt: (v: number) => string
  sources: string[]
  vintage: string
  better: 'higher' | 'lower' | 'neutral'
  /** raw accessor for a metric key, the unit's land area (km²) */
  calc: (get: (k: string) => number, area: number) => number
  /** ratio-scale (positive) measure: "×" comparisons are meaningful */
  ratio: boolean
}

const metricOf = (d: CountryData, k: string): Metric | undefined => d.metricByKey.get(k)

export function metricMeasure(d: CountryData, key: string, opts: Partial<Measure> = {}): Measure | null {
  const m = metricOf(d, key)
  if (!m) return null
  const u = m.unit.toLowerCase()
  return {
    id: key, label: prettyLabel(m.label), fmt: (v) => fmtMetric(m.unit, v), sources: m.sources, vintage: m.vintage,
    better: m.better ?? 'neutral', calc: (get) => get(key),
    ratio: !['m', 'degc', 'index'].includes(u) && !key.startsWith('elev_'), ...opts,
  }
}

/** value / pop × per (e.g. research works per million residents). */
export function perCapMeasure(d: CountryData, key: string, per: number, label: string, unitText: string): Measure | null {
  const m = metricOf(d, key)
  if (!m || !metricOf(d, 'pop')) return null
  const pm = metricOf(d, 'pop')!
  return {
    id: `${key}/pop*${per}`, label, fmt: (v) => (unitText ? `${count(v)} ${unitText}` : count(v)), sources: [...m.sources, ...pm.sources],
    vintage: m.vintage, better: m.better ?? 'neutral', ratio: true,
    calc: (get) => { const p = get('pop'); const v = get(key); return p > 0 && Number.isFinite(v) ? (v / p) * per : NaN },
  }
}

export type Level = string // 'hex' | 'adm1' | 'adm2' | 'adm3'

export function levelSize(d: CountryData, level: Level) {
  return level === 'hex' ? d.n : d.admin[level]?.units.length ?? 0
}

/** Raw metric accessor for one tile / unit. */
export function getter(d: CountryData, level: Level, idx: number): (k: string) => number {
  if (level === 'hex') {
    return (k) => {
      const m = d.metricByKey.get(k)
      return m ? d.matrix[m.col * d.n + idx] : NaN
    }
  }
  const t = d.admin[level]
  return (k) => {
    const v = t?.metrics[k]?.[idx]
    return v == null ? NaN : v
  }
}

export function areaOf(d: CountryData, level: Level, idx: number) {
  const a = getter(d, level, idx)('land_km2')
  if (a > 0) return a
  return level === 'hex' ? d.hexArea[idx] : NaN
}

export function measureValue(d: CountryData, level: Level, idx: number, ms: Measure) {
  return ms.calc(getter(d, level, idx), areaOf(d, level, idx))
}

const colCache = new WeakMap<CountryData, Map<string, Float32Array>>()
const sortCache = new WeakMap<CountryData, Map<string, Float32Array>>()

/** The measure for every tile (land tiles only; sea = NaN) or every unit of a level. Cached. */
export function measureColumn(d: CountryData, level: Level, ms: Measure): Float32Array {
  const cache = cacheFor(colCache, d)
  const key = `${level}|${ms.id}`
  let c = cache.get(key)
  if (c) return c
  const N = levelSize(d, level)
  c = new Float32Array(N)
  const ring = d.hexes.ring
  for (let i = 0; i < N; i++) c[i] = level === 'hex' && ring[i] !== 0 ? NaN : measureValue(d, level, i, ms)
  cache.set(key, c)
  return c
}

/** Sorted finite values of a measure column (for percentiles / medians). */
export function sortedColumn(d: CountryData, level: Level, ms: Measure): Float32Array {
  const cache = cacheFor(sortCache, d)
  const key = `${level}|${ms.id}`
  let s = cache.get(key)
  if (s) return s
  const c = measureColumn(d, level, ms)
  const tmp: number[] = []
  for (let i = 0; i < c.length; i++) if (Number.isFinite(c[i])) tmp.push(c[i])
  s = Float32Array.from(tmp).sort()
  cache.set(key, s)
  return s
}

function lowerBound(a: Float32Array, v: number) {
  let lo = 0, hi = a.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] < v) lo = mid + 1; else hi = mid }
  return lo
}
function upperBound(a: Float32Array, v: number) {
  let lo = 0, hi = a.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] <= v) lo = mid + 1; else hi = mid }
  return lo
}

/** Mid-rank percentile (0-100) of v within sorted values. */
export function percentileOf(sorted: Float32Array, v: number) {
  if (!sorted.length || !Number.isFinite(v)) return NaN
  const f = Math.fround(v)
  return (100 * (lowerBound(sorted, f) + upperBound(sorted, f))) / 2 / sorted.length
}

export function medianOf(sorted: Float32Array) {
  const n = sorted.length
  if (!n) return NaN
  return n % 2 ? sorted[(n - 1) >> 1] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2
}

// ---------------------------------------------------------------------------------------------
// Admin helpers & yields per unit
// ---------------------------------------------------------------------------------------------
const unitMapCache = new WeakMap<CountryData, Map<string, Map<string, AdminUnit>>>()
export function unitById(d: CountryData, level: string): Map<string, AdminUnit> {
  const cache = cacheFor(unitMapCache, d)
  let m = cache.get(level)
  if (!m) {
    m = new Map((d.admin[level]?.units ?? []).map((u) => [u.id, u]))
    cache.set(level, m)
  }
  return m
}

/** "Colombo District", "Western Province", "Travis County, TX", "California". */
export function unitName(d: CountryData, level: string, u: AdminUnit): string {
  const label = d.meta.admin_levels[level]?.label ?? d.admin[level]?.label ?? ''
  if (d.id === 'usa') {
    if (level === 'adm1') return u.name
    const st = unitById(d, 'adm1').get(u.parent)
    return st ? `${u.name}, ${st.abbr ?? st.name}` : u.name
  }
  if (!label || u.name.toLowerCase().includes(label.toLowerCase())) return u.name
  return `${u.name} ${label}`
}

/** Plural noun for ranking pools: "land tiles", "districts", "counties", "DS divisions". */
export function levelNoun(d: CountryData, level: Level, n = 2) {
  if (level === 'hex') return n === 1 ? 'land tile' : 'land tiles'
  const label = d.meta.admin_levels[level]?.label ?? level
  const lower = label.replace(/^([A-Z])([a-z])/, (_m, a: string, b: string) => a.toLowerCase() + b)
  return pluralize(lower, n)
}

const yieldSumCache = new WeakMap<CountryData, Map<string, Record<YieldKey, Float32Array>>>()
const YK: YieldKey[] = ['gold', 'production', 'food', 'science', 'culture', 'faith']
/** Sum of tile yields per admin unit (land tiles), one pass over the grid. */
export function yieldSums(d: CountryData, level: string): Record<YieldKey, Float32Array> | null {
  const arr = (d.hexes as unknown as Record<string, number[]>)[level]
  if (!arr || !d.hexes.yields) return null
  const cache = cacheFor(yieldSumCache, d)
  let r = cache.get(level)
  if (r) return r
  const N = d.admin[level]?.units.length ?? 0
  r = Object.fromEntries(YK.map((y) => [y, new Float32Array(N)])) as Record<YieldKey, Float32Array>
  for (let i = 0; i < d.n; i++) {
    const u = arr[i]
    if (u < 0 || u >= N || d.hexes.ring[i] !== 0) continue
    for (const y of YK) r[y][u] += d.hexes.yields[y]?.[i] ?? 0
  }
  cache.set(level, r)
  return r
}

/** Yield of a place (tile value or unit sum) and the rank pool for it. */
export function yieldColumn(d: CountryData, level: Level, y: YieldKey): Float32Array | null {
  if (level === 'hex') {
    const ys = d.hexes.yields?.[y]
    if (!ys) return null
    const cache = cacheFor(colCache, d)
    const key = `hex|yield:${y}`
    let c = cache.get(key)
    if (!c) {
      c = new Float32Array(d.n)
      for (let i = 0; i < d.n; i++) c[i] = d.hexes.ring[i] === 0 ? ys[i] : NaN
      cache.set(key, c)
    }
    return c
  }
  return yieldSums(d, level)?.[y] ?? null
}

// ---------------------------------------------------------------------------------------------
// Places
// ---------------------------------------------------------------------------------------------
export interface Place {
  d: CountryData
  country: CountryId
  /** ranking / twin level: 'hex' or an admin level */
  level: Level
  index: number
  title: string
  subtitle: string
  /** "tile", "District", "County", "City" */
  kind: string
  lon: number
  lat: number
  city?: City
  wonder?: string
}

export function hexTrail(d: CountryData, i: number) {
  const u2 = adminUnitOfHex(d, 'adm2', i)
  const u1 = adminUnitOfHex(d, 'adm1', i)
  if (d.id === 'usa') return u2 ? unitName(d, 'adm2', u2.unit) : u1 ? u1.unit.name : ''
  return [u2 && unitName(d, 'adm2', u2.unit), u1 && unitName(d, 'adm1', u1.unit)].filter(Boolean).join(' · ')
}

export function resolvePlace(sel: Selection, d: CountryData, idx: IndexJson): Place | null {
  const hexPlace = (h3: string, wonder?: string): Place | null => {
    const i = d.hexIndex.get(h3)
    if (i == null) return null
    const t = idx.terrain_codes[d.hexes.terrain[i]] ?? 'plains'
    const f = idx.feature_codes[d.hexes.feature[i]] ?? ''
    const [lat, lon] = cellToLatLng(h3)
    const ring = d.hexes.ring[i]
    return {
      d, country: d.id, level: 'hex', index: i, kind: 'tile', lon, lat, wonder,
      title: wonder ?? `${terrainLabel(t, f, !!d.hexes.hills[i])} ${ring > 0 ? 'sea tile' : 'tile'}`,
      subtitle: hexTrail(d, i),
    }
  }
  if (sel.kind === 'hex') return hexPlace(sel.id)
  if (sel.kind === 'wonder') {
    const w = d.wonders.find((x) => x.name === sel.id)
    return w?.h3 ? hexPlace(w.h3, w.name) : null
  }
  if (sel.kind === 'admin') {
    const [level, s] = sel.id.split(':')
    const ui = Number(s)
    const u = d.admin[level]?.units[ui]
    if (!u) return null
    const parentLevel = level === 'adm3' ? 'adm2' : level === 'adm2' ? 'adm1' : null
    const parent = parentLevel ? unitById(d, parentLevel).get(u.parent) : undefined
    const grand = parentLevel === 'adm2' && parent ? unitById(d, 'adm1').get(parent.parent) : undefined
    const subtitle = d.id === 'usa'
      ? (level === 'adm2' ? (parent?.name ?? '') : '')
      : [parent && unitName(d, parentLevel!, parent), grand && unitName(d, 'adm1', grand)].filter(Boolean).join(' · ')
    return {
      d, country: d.id, level, index: ui, kind: d.meta.admin_levels[level]?.label ?? level,
      title: unitName(d, level, u), subtitle, lon: u.lon, lat: u.lat,
    }
  }
  if (sel.kind === 'city') {
    const c = d.cities.find((x) => x.id === sel.id)
    if (!c) return null
    const ci = d.hexIndex.get(c.centre)
    const u = ci != null ? adminUnitOfHex(d, 'adm2', ci) : null
    if (!u) return null
    return {
      d, country: d.id, level: 'adm2', index: u.index, kind: 'City', city: c,
      title: c.name, subtitle: hexTrail(d, ci!),
      lon: c.lon, lat: c.lat,
    }
  }
  return null
}

/** The level used in the other country for comparisons and twins. */
export function counterpartLevel(level: Level, other: CountryData): Level {
  if (level === 'hex') return 'hex'
  if ((level === 'adm1' || level === 'adm2') && other.admin[level]) return level
  return other.admin.adm2 ? 'adm2' : Object.keys(other.admin)[0]
}

export function sameSelection(a: Selection | null, b: Selection | null) {
  return !!a && !!b && a.country === b.country && a.kind === b.kind && a.id === b.id
}
