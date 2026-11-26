// Pure helpers for the Tile inspector: percentile caches, point lookups and the yield derivation.
// Everything here is memoised per CountryData / AdminTable object (WeakMap), so a pipeline
// re-export (new objects) naturally invalidates the caches.
import type { AdminTable, CountryData, IndexJson, Metric, PointRow, YieldKey } from '../types'
import { fmtNumber, fmtUnit, fmtUsd } from '../format'

// ---------------------------------------------------------------------------------------------
// Categories (pipeline/civmap/schema.py CATEGORIES order) with Civ-flavoured labels
// ---------------------------------------------------------------------------------------------
export const CATEGORY_ORDER = [
  'population', 'economy', 'income', 'labor', 'production', 'energy', 'food', 'science', 'education',
  'culture', 'faith', 'society', 'hazard', 'trade', 'infrastructure', 'landcover', 'elevation',
  'environment', 'military',
]

export const CATEGORY_META: Record<string, { label: string; icon?: string; yield?: YieldKey }> = {
  population: { label: 'Population', icon: '👥' },
  economy: { label: 'Economy · Gold', yield: 'gold' },
  income: { label: 'Income & poverty', icon: '💵' },
  labor: { label: 'Workforce · Citizens', icon: '👷' },
  production: { label: 'Industry · Production', yield: 'production' },
  energy: { label: 'Energy', icon: '⚡' },
  food: { label: 'Agriculture · Food', yield: 'food' },
  science: { label: 'Research · Science', yield: 'science' },
  education: { label: 'Education', icon: '🎓' },
  culture: { label: 'Tourism & heritage · Culture', yield: 'culture' },
  faith: { label: 'Religion · Faith', yield: 'faith' },
  society: { label: 'Society & ethnicity', icon: '🧑‍🤝‍🧑' },
  hazard: { label: 'Disasters', icon: '🌪️' },
  trade: { label: 'Trade', icon: '⚓' },
  infrastructure: { label: 'Infrastructure', icon: '🛣️' },
  landcover: { label: 'Land cover', icon: '🌳' },
  elevation: { label: 'Elevation & climate', icon: '🏔️' },
  environment: { label: 'Environment', icon: '🌿' },
  military: { label: 'Military', icon: '🛡️' },
  other: { label: 'Other', icon: '📊' },
}

export function categoryRank(c: string) {
  const i = CATEGORY_ORDER.indexOf(c)
  return i < 0 ? CATEGORY_ORDER.length : i
}

// ---------------------------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------------------------
export const prettyUnit = (u: string) => u.replace(/km2/g, 'km²')
export const isAreaUnit = (u: string) => /^\s*km(2|²)\s*$/i.test(u)
export const isMoneyUnit = (u: string) => /^\s*(usd|us\$)/i.test(u)
const COUNTISH = new Set(['persons', 'people', 'count', '']);

/** Metric value in its unit (km2 → km²). */
export function fmtVal(v: number | null | undefined, unit: string) {
  return prettyUnit(fmtUnit(v, unit))
}

/** Density "x /km²" for an extensive metric value. */
export function fmtDensity(v: number, unit: string) {
  if (!Number.isFinite(v)) return '–'
  if (isMoneyUnit(unit)) return `${fmtUsd(v)}/km²`
  const u = unit.trim().toLowerCase()
  return `${fmtNumber(v)}${COUNTISH.has(u) ? '' : ' ' + prettyUnit(unit)}/km²`
}

export const hasDensity = (m: Pick<Metric, 'kind' | 'unit'>) => m.kind === 'extensive' && !isAreaUnit(m.unit)

export function fmtBytes(b: number) {
  if (!Number.isFinite(b) || b <= 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const e = Math.min(u.length - 1, Math.floor(Math.log10(b) / 3))
  return `${(b / Math.pow(1000, e)).toPrecision(e ? 3 : 1)} ${u[e]}`
}

// ---------------------------------------------------------------------------------------------
// Percentiles / ranks
// ---------------------------------------------------------------------------------------------
function lowerBound(a: ArrayLike<number>, v: number) {
  let lo = 0, hi = a.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m }
  return lo
}
function upperBound(a: ArrayLike<number>, v: number) {
  let lo = 0, hi = a.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= v) lo = m + 1; else hi = m }
  return lo
}

/** Mid-rank percentile (0-100) of v among sorted values. */
export function percentileOf(sorted: ArrayLike<number>, v: number): number | null {
  if (!Number.isFinite(v) || !sorted.length) return null
  const lo = lowerBound(sorted, v), hi = upperBound(sorted, v)
  return ((lo + (hi - lo) / 2) / sorted.length) * 100
}

const hexSortedCache = new WeakMap<CountryData, Map<string, Float32Array>>()

/** Value used for ranking a hex: density for extensive metrics (same as the map lens), raw otherwise. */
export function hexBasis(d: CountryData, m: Metric, i: number): number {
  const v = d.matrix[m.col * d.n + i]
  return hasDensity(m) ? Math.fround(v / d.hexArea[i]) : v
}

/** Sorted finite basis values of the country's land tiles (computed once per metric). */
export function hexSorted(d: CountryData, m: Metric): Float32Array {
  let byKey = hexSortedCache.get(d)
  if (!byKey) { byKey = new Map(); hexSortedCache.set(d, byKey) }
  let s = byKey.get(m.key)
  if (!s) {
    const base = m.col * d.n
    const dens = hasDensity(m)
    const ring = d.hexes.ring
    const tmp = new Float32Array(d.n)
    let k = 0
    for (let i = 0; i < d.n; i++) {
      if (ring[i] !== 0) continue
      const v = dens ? d.matrix[base + i] / d.hexArea[i] : d.matrix[base + i]
      if (Number.isFinite(v)) tmp[k++] = v
    }
    s = tmp.slice(0, k).sort()
    byKey.set(m.key, s)
  }
  return s
}

export function hexPercentile(d: CountryData, m: Metric, i: number): { pct: number | null; n: number } {
  const s = hexSorted(d, m)
  return { pct: percentileOf(s, hexBasis(d, m, i)), n: s.length }
}

const adminSortedCache = new WeakMap<AdminTable, Map<string, Float64Array>>()

export function adminRank(t: AdminTable, key: string, ui: number): { rank: number; n: number; ties: number; pct: number | null } | null {
  const col = t.metrics[key]
  const v = col?.[ui]
  if (!col || v == null || !Number.isFinite(v)) return null
  let byKey = adminSortedCache.get(t)
  if (!byKey) { byKey = new Map(); adminSortedCache.set(t, byKey) }
  let s = byKey.get(key)
  if (!s) {
    s = Float64Array.from(col.filter((x): x is number => x != null && Number.isFinite(x))).sort()
    byKey.set(key, s)
  }
  const hi = upperBound(s, v)
  return { rank: s.length - hi + 1, n: s.length, ties: hi - lowerBound(s, v), pct: percentileOf(s, v) }
}

// ---------------------------------------------------------------------------------------------
// Admin helpers
// ---------------------------------------------------------------------------------------------
const adminIdCache = new WeakMap<AdminTable, Map<string, number>>()
export function adminIndexById(t: AdminTable): Map<string, number> {
  let m = adminIdCache.get(t)
  if (!m) { m = new Map(t.units.map((u, i) => [u.id, i])); adminIdCache.set(t, m) }
  return m
}

/** Parent chain (outermost first) of an admin unit, excluding the unit itself. */
export function adminParents(d: CountryData, level: string, ui: number): { level: string; index: number; name: string }[] {
  const levels = Object.keys(d.meta.admin_levels)
  const out: { level: string; index: number; name: string }[] = []
  let li = levels.indexOf(level)
  let parentId = d.admin[level]?.units[ui]?.parent
  while (li > 0 && parentId) {
    const pl = levels[li - 1]
    const t = d.admin[pl]
    if (!t) break
    const pi = adminIndexById(t).get(parentId)
    if (pi == null) break
    out.unshift({ level: pl, index: pi, name: t.units[pi].name })
    parentId = t.units[pi].parent
    li--
  }
  return out
}

// ---------------------------------------------------------------------------------------------
// Points (facilities) by hex
// ---------------------------------------------------------------------------------------------
export interface TilePoint { layer: string; row: PointRow }
const pointsCache = new WeakMap<CountryData, Map<string, TilePoint[]>>()

export function pointsInHex(d: CountryData, h3: string): TilePoint[] {
  let m = pointsCache.get(d)
  if (!m) {
    m = new Map()
    for (const [layer, pl] of Object.entries(d.points ?? {})) {
      for (const row of pl?.rows ?? []) {
        if (!row.h3) continue
        const a = m.get(row.h3)
        if (a) a.push({ layer, row }); else m.set(row.h3, [{ layer, row }])
      }
    }
    pointsCache.set(d, m)
  }
  return m.get(h3) ?? []
}

export const POINT_LAYER_META: Record<string, { label: string; icon: string; value?: string[]; unit?: string }> = {
  power_plants: { label: 'Power station', icon: '⚡', value: ['capacity_mw'], unit: 'MW' },
  ports: { label: 'Port', icon: '⚓', value: ['port_cargo_t', 'cargo_tonnes'], unit: 't cargo' },
  airports: { label: 'Airport', icon: '✈️', value: ['airport_pax', 'passengers'], unit: 'passengers' },
  universities: { label: 'University', icon: '🎓', value: ['works_5y'], unit: 'works (5 y)' },
  institutions: { label: 'Research institution', icon: '🔬', value: ['works_5y'], unit: 'works (5 y)' },
  heritage: { label: 'World Heritage', icon: '🏛️' },
  parks: { label: 'National park', icon: '🏞️', value: ['park_visitors', 'visitors'], unit: 'visitors' },
  museums: { label: 'Museum', icon: '🖼️' },
  holy_sites: { label: 'Holy site', icon: '🪔' },
  military: { label: 'Military site', icon: '🛡️' },
  spaceports: { label: 'Spaceport', icon: '🚀' },
  industrial_zones: { label: 'Industrial zone', icon: '🏭' },
}

export function pointValue(p: TilePoint): number {
  for (const c of POINT_LAYER_META[p.layer]?.value ?? []) {
    const v = Number(p.row[c])
    if (p.row[c] != null && Number.isFinite(v)) return v
  }
  return NaN
}

// ---------------------------------------------------------------------------------------------
// "Why these yields" — mirrors pipeline/civmap/civ.py build_tiles()
// ---------------------------------------------------------------------------------------------
export interface YieldPart {
  label: string
  amount: string        // what was measured in the tile
  detail: string        // how it turns into points
  points: number
  counted: boolean
  missing?: boolean
  metric?: string
  sources?: string[]
}
export interface YieldExplain { key: YieldKey; actual: number; computed: number; parts: YieldPart[]; note?: string }

const BASE_LABELS: Record<string, string> = {
  gold: 'GDP', production: 'Industrial GVA', food: 'Agricultural GVA', food_sea: 'Fish landings',
  science: 'STEM workers', culture: 'Arts, recreation & hospitality jobs', faith: 'Places of worship',
}

// culture / faith bonuses are constants in civ.py (not in index.json)
const WHS_PTS = 2, WHS_MAX = 2, MUSEUM_MIN = 3, PARK_VIS_MIN = 1_000_000, HOLY_PTS = 2

function logYield(dens: number, base: number, cap: number) {
  if (!(dens >= base) || !Number.isFinite(dens)) return 0
  return Math.max(0, Math.min(cap, Math.floor(2 * Math.log10(dens / base)) + 1))
}
function stepBonus(v: number, steps: [number, number][]) {
  let b = 0
  for (const [thr, pts] of steps) if (v >= thr) b = pts
  return b
}
const fmtThreshold = (v: number, unit: string) => (isMoneyUnit(unit) ? fmtUsd(v) : fmtNumber(v))

export function explainYields(d: CountryData, idx: IndexJson, i: number): YieldExplain[] {
  const ys = idx.yield_scale
  const cap = ys.cap
  const area = d.hexArea[i]
  const sea = d.hexes.ring[i] > 0
  const pts = pointsInHex(d, d.hexes.h3[i])

  const basePart = (bkey: string, label?: string): YieldPart => {
    const b = ys.bases[bkey]
    if (!b) return { label: label ?? BASE_LABELS[bkey] ?? bkey, amount: '–', detail: 'no yield base defined', points: 0, counted: true, missing: true }
    const m = d.metricByKey.get(b.metric)
    const name = label ?? m?.label ?? BASE_LABELS[bkey] ?? b.metric
    const baseTxt = `${fmtThreshold(b.base, isMoneyUnit(m?.unit ?? '') || b.unit.startsWith('US$') ? 'usd' : '')}/km²`
    if (!m) {
      return { label: name, amount: 'awaiting data', detail: `1 point at ${baseTxt} (${b.unit}) · ${b.metric} not published yet`, points: 0, counted: true, missing: true, metric: b.metric }
    }
    const v = d.matrix[m.col * d.n + i]
    if (!Number.isFinite(v)) {
      return { label: name, amount: 'no data', detail: `no value for this tile · 1 point at ${baseTxt}`, points: 0, counted: true, missing: true, metric: m.key, sources: m.sources }
    }
    const dens = v / area
    const p = logYield(dens, b.base, cap)
    const next = b.base * Math.pow(10, p / 2)
    const nextTxt = p >= cap ? 'capped' : `next point at ${fmtThreshold(next, m.unit)}/km²`
    return {
      label: name, amount: `${fmtVal(v, m.unit)} → ${fmtDensity(dens, m.unit)}`,
      detail: `base ${fmtThreshold(b.base, m.unit)}/km² (${b.unit}) · ${nextTxt}`,
      points: p, counted: true, metric: m.key, sources: m.sources,
    }
  }

  /** tile total from a metric if published, else from the exported point layer(s). */
  const tileAmount = (metricKey: string | null, layers: string[], col: 'value' | 'count'): { v: number; from: string; sources?: string[] } | null => {
    const m = metricKey ? d.metricByKey.get(metricKey) : undefined
    if (m) {
      const v = d.matrix[m.col * d.n + i]
      return { v: Number.isFinite(v) ? v : 0, from: `metric ${m.key}`, sources: m.sources }
    }
    const avail = layers.filter((l) => d.points?.[l])
    if (!avail.length) return null
    let v = 0
    for (const p of pts) {
      if (!avail.includes(p.layer)) continue
      if (col === 'count') v += 1
      else { const x = pointValue(p); if (Number.isFinite(x)) v += x }
    }
    return { v, from: `${avail.join(' + ')} points` }
  }
  const bonusPart = (label: string, amt: ReturnType<typeof tileAmount>, unit: string, points: (v: number) => number, rule: string): YieldPart =>
    amt
      ? { label, amount: `${fmtNumber(amt.v)} ${unit}`.trim(), detail: `${rule} · from ${amt.from}`, points: points(amt.v), counted: true, sources: amt.sources }
      : { label, amount: 'awaiting data', detail: rule, points: 0, counted: true, missing: true }

  const stepsText = (steps: [number, number][], unit: string) => steps.map(([t, b]) => `≥ ${fmtNumber(t)} ${unit} +${b}`).join(' · ')

  const out: YieldExplain[] = []
  const actual = (k: YieldKey) => d.hexes.yields?.[k]?.[i] ?? 0
  const finish = (key: YieldKey, parts: YieldPart[], computed?: number, note?: string) => {
    let c = computed ?? parts.filter((p) => p.counted).reduce((s, p) => s + p.points, 0)
    c = Math.max(0, Math.min(cap + 4, c))
    if (sea && (key === 'science' || key === 'culture' || key === 'faith')) {
      c = 0
      note = 'Sea tiles yield no Science, Culture or Faith.'
    }
    out.push({ key, actual: actual(key), computed: c, parts, note })
  }

  // Gold
  finish('gold', [basePart('gold')])

  // Production (+ power stations)
  finish('production', [
    basePart('production'),
    bonusPart('Power stations', tileAmount('power_capacity_mw', ['power_plants'], 'value'), 'MW',
      (v) => stepBonus(v, ys.power_bonus), stepsText(ys.power_bonus, 'MW')),
  ])

  // Food: land = max(agriculture, fish); sea = fish only
  const land = basePart('food')
  const fish = basePart('food_sea', 'Fish landings')
  if (sea) {
    land.counted = false
    land.detail = 'not counted on sea tiles'
    finish('food', [fish, land], fish.points)
  } else {
    const best = Math.max(land.points, fish.points)
    if (land.points >= fish.points) fish.counted = false; else land.counted = false
    finish('food', [land, fish], best, 'Land tiles take the larger of farm and fishery yield.')
  }

  // Science (+ research output)
  finish('science', [
    basePart('science'),
    bonusPart('Research output', tileAmount('research_works_5y', ['institutions', 'universities'], 'value'), 'works / 5 y',
      (v) => stepBonus(v, ys.research_bonus), stepsText(ys.research_bonus, 'works')),
  ])

  // Culture (+ World Heritage, museums, park visitors)
  finish('culture', [
    basePart('culture'),
    bonusPart('World Heritage sites', tileAmount(null, ['heritage'], 'count'), 'sites',
      (v) => WHS_PTS * Math.min(v, WHS_MAX), `+${WHS_PTS} per site (max ${WHS_MAX})`),
    bonusPart('Museums', tileAmount('museums', ['museums'], 'count'), '',
      (v) => (v >= MUSEUM_MIN ? 1 : 0), `≥ ${MUSEUM_MIN} museums +1`),
    bonusPart('National park visitors', tileAmount('park_visitors', ['parks'], 'value'), 'visitors / yr',
      (v) => (v >= PARK_VIS_MIN ? 1 : 0), `≥ ${fmtNumber(PARK_VIS_MIN)} visitors +1`),
  ])

  // Faith (+ notable holy sites)
  finish('faith', [
    basePart('faith'),
    bonusPart('Notable holy sites', tileAmount(null, ['holy_sites'], 'count'), 'sites',
      (v) => (v >= 1 ? HOLY_PTS : 0), `any notable religious site +${HOLY_PTS}`),
  ])
  return out
}
