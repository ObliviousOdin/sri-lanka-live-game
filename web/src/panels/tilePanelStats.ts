// Pure helpers for the Tile inspector: percentile caches, point lookups and the yield derivation.
// Everything here is memoised per CountryData / AdminTable object (WeakMap), so a pipeline
// re-export (new objects) naturally invalidates the caches.
import { YIELDS, type AdminTable, type CountryData, type CountryId, type IndexJson, type Metric, type PointRow, type Wonder, type YieldKey } from '../types'
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

/**
 * Density of an extensive metric value. Without `basis`: "x/km²" (compact, used in the yield
 * breakdown). With a basis: "x per km² of tile" (H3 cell area, incl. water — the yield basis) or
 * "x per km² of land" (admin units). Never mix the two silently.
 */
export function fmtDensity(v: number, unit: string, basis?: 'tile' | 'land') {
  if (!Number.isFinite(v)) return '–'
  const u = unit.trim().toLowerCase()
  const core = isMoneyUnit(unit) ? fmtUsd(v) : `${fmtNumber(v)}${COUNTISH.has(u) ? '' : ' ' + prettyUnit(unit)}`
  return basis ? `${core} per km² of ${basis}` : `${core}/km²`
}

export const hasDensity = (m: Pick<Metric, 'kind' | 'unit'>) => m.kind === 'extensive' && !isAreaUnit(m.unit)

/** Intensive metrics that are themselves densities per km² of LAND (pop_density = pop ÷ land_km2). */
export const isLandDensity = (m: Pick<Metric, 'kind' | 'unit' | 'weight'>) =>
  m.kind === 'intensive' && /\/\s*km(2|²)\s*$/i.test(m.unit) && m.weight === 'land'

/** "10.7K people/km²" for a per-km²-of-land metric (the caller adds the "of land" note). */
export function fmtLandDensity(v: number, unit: string) {
  if (!Number.isFinite(v)) return '–'
  const what = unit.replace(/\/\s*km(2|²)\s*$/i, '').trim().toLowerCase()
  const noun = what === 'persons' || what === 'people' ? ' people' : what === '' || what === 'count' ? '' : ` ${prettyUnit(what)}`
  return `${fmtNumber(v)}${noun}/km²`
}

/** English plural of an admin-level label (County → Counties, DS Division → DS Divisions). */
export function plural(label: string, n = 2) {
  if (n === 1) return label
  return /[^aeiou]y$/i.test(label) ? label.slice(0, -1) + 'ies' : label + 's'
}

/** Fixed fly-to zooms per admin level (used when a unit has no land area). */
export const ADMIN_ZOOM: Record<CountryId, Record<string, number>> = { lka: { adm1: 8.4, adm2: 9, adm3: 10.5 }, usa: { adm1: 5.5, adm2: 8 } }
const MAX_FIT_ZOOM: Record<CountryId, number> = { lka: 10.5, usa: 8.5 }

/**
 * Fly-to zoom that shows an admin unit of `km2` (land area) at latitude `lat` about 320 px across
 * (deck.gl 512 px tiles). Clamped so small units still show whole tiles of the country's grid.
 */
export function adminZoom(country: CountryId, level: string, km2: number | null | undefined, lat: number): number {
  const fixed = ADMIN_ZOOM[country]?.[level] ?? 7
  if (km2 == null || !Number.isFinite(km2) || km2 <= 0) return fixed
  const mpp = (Math.sqrt(km2) * 1000) / 320
  const z = Math.log2((78271.517 * Math.cos((lat * Math.PI) / 180)) / mpp)
  return Number.isFinite(z) ? Math.max(3.5, Math.min(MAX_FIT_ZOOM[country] ?? 10, z)) : fixed
}

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

export interface HexRank {
  pct: number | null
  n: number
  /** tiles ranked strictly below this value / at or below it (tie block = hi - lo) */
  lo: number
  hi: number
}

export function hexPercentile(d: CountryData, m: Metric, i: number): HexRank {
  const s = hexSorted(d, m)
  const v = hexBasis(d, m, i)
  if (!Number.isFinite(v) || !s.length) return { pct: null, n: s.length, lo: 0, hi: 0 }
  return { pct: percentileOf(s, v), n: s.length, lo: lowerBound(s, v), hi: upperBound(s, v) }
}

/**
 * A mid-rank percentile is misleading when a large block of tiles shares the value (98% of tiles
 * have 0 World Heritage Sites → "P49"). Returns how to show such ties instead, or null for a
 * normal percentile.
 */
export function tieInfo(r: HexRank): { atMin: boolean; share: number; loPct: number; hiPct: number } | null {
  if (r.pct == null || !r.n) return null
  const share = (r.hi - r.lo) / r.n
  if (share <= 0.2) return null
  return { atMin: r.lo === 0, share, loPct: (r.lo / r.n) * 100, hiPct: (r.hi / r.n) * 100 }
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

export interface PointLayerMeta {
  label: string
  icon: string
  /** columns holding the facility's size value, first finite one wins */
  value?: string[]
  unit?: string
  /** shown instead of a value when a country publishes no figure for this layer (missing data, not zero) */
  noValue?: Partial<Record<CountryId, string>>
}

export const POINT_LAYER_META: Record<string, PointLayerMeta> = {
  power_plants: { label: 'Power station', icon: '⚡', value: ['capacity_mw'], unit: 'MW' },
  ports: { label: 'Port', icon: '⚓', value: ['port_cargo_t', 'cargo_tonnes'], unit: 't cargo' },
  // US values are FAA enplanements (each boarding passenger once, about half of all passengers)
  airports: {
    label: 'Airport', icon: '✈️', value: ['airport_pax', 'passengers'], unit: 'enplanements (boardings)',
    noValue: { lka: 'no open airport-level statistics' },
  },
  universities: { label: 'University', icon: '🎓', value: ['works_5y'], unit: 'works (5 y)' },
  institutions: { label: 'Research institution', icon: '🔬', value: ['works_5y'], unit: 'works (5 y)' },
  heritage: { label: 'World Heritage', icon: '🏯' },
  parks: { label: 'National park', icon: '🏞️', value: ['park_visitors', 'visitors'], unit: 'visitors' },
  museums: { label: 'Museum', icon: '🖼️' },
  holy_sites: { label: 'Holy site', icon: '🪔' },
  military: { label: 'Military site', icon: '🛡️' },
  spaceports: { label: 'Spaceport', icon: '🚀' },
  industrial_zones: { label: 'Industrial zone', icon: '🏭' },
}

/** Display unit of a wonder value: FAA airport counts are enplanements (boardings), not passengers. */
export function wonderUnit(w: Pick<Wonder, 'category' | 'unit'>): string {
  if (w.category === 'Great Airport' && /passeng|enplan/i.test(w.unit ?? '')) return 'enplanements'
  return w.unit
}

// ---------------------------------------------------------------------------------------------
// Civilization summary: mean yield per land tile (comparable across the two grids)
// ---------------------------------------------------------------------------------------------
const landMeanCache = new WeakMap<CountryData, { mean: Record<YieldKey, number>; n: number }>()

/**
 * Mean yield per land tile. Each tile gets a yield from its density whatever its size, so a sum of
 * yields counts tiles (Sri Lanka 36 km² tiles vs US 253 km²) — only the mean compares countries.
 */
export function landYieldMeans(d: CountryData): { mean: Record<YieldKey, number>; n: number } {
  let r = landMeanCache.get(d)
  if (!r) {
    const ring = d.hexes.ring
    let n = 0
    for (let i = 0; i < d.n; i++) if (ring[i] === 0) n++
    const mean = Object.fromEntries(YIELDS.map((y) => {
      const a = d.hexes.yields?.[y]
      let s = 0
      if (a) for (let i = 0; i < d.n; i++) if (ring[i] === 0) s += a[i] ?? 0
      return [y, n ? s / n : NaN]
    })) as Record<YieldKey, number>
    r = { mean, n }
    landMeanCache.set(d, r)
  }
  return r
}

// ---------------------------------------------------------------------------------------------
// Holy sites: the Faith bonus counts every listed site, the Holy Site district only the top N
// ---------------------------------------------------------------------------------------------
/** Used only when index.json states no cut-off at all (civ.py: holy_top, 10 in Sri Lanka / 25 in the US). */
const HOLY_DISTRICT_TOP_FALLBACK: Record<string, number> = { lka: 10, usa: 25 }

/**
 * How many of a country's listed holy sites qualify a tile for the Holy Site district. Read from
 * index.json: the district's `top` field when exported, else the cut-off its rule text states
 * ("top 10 in Sri Lanka, top 25 in the US"), so the panel never disagrees with the published rule.
 */
export function holyDistrictTop(idx: IndexJson, country: CountryId): number {
  const def = idx.districts.find((x) => x.code === 'holy_site')
  const given = def?.top?.[country]
  if (given != null && Number.isFinite(given)) return given
  const re = country === 'lka' ? /top\s+(\d+)\s+in\s+Sri Lanka/i : /top\s+(\d+)\s+in\s+the\s+(US|United States)\b/i
  const m = def?.rule ? re.exec(def.rule) : null
  return m ? Number(m[1]) : HOLY_DISTRICT_TOP_FALLBACK[country] ?? 10
}

const holyRankCache = new WeakMap<CountryData, Map<string, number>>()
/** Rank (1 = most Wikipedia editions) of each listed holy site, by point id. */
export function holySiteRanks(d: CountryData): Map<string, number> {
  let m = holyRankCache.get(d)
  if (!m) {
    const rows = (d.points?.holy_sites?.rows ?? []).filter((r) => r.h3)
    const sl = (r: PointRow) => { const v = Number(r.sitelinks); return Number.isFinite(v) ? v : -1 }
    m = new Map([...rows].sort((a, b) => sl(b) - sl(a)).map((r, k) => [r.id, k + 1]))
    holyRankCache.set(d, m)
  }
  return m
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
  /** the tile statistics (metrics.bin) are still downloading: no value yet, not missing data */
  loading?: boolean
  metric?: string
  sources?: string[]
}
export interface YieldExplain { key: YieldKey; actual: number; computed: number; parts: YieldPart[]; note?: string; loading?: boolean }

export const LOADING_STATS = 'loading statistics…'
export const STATS_FAILED = 'statistics did not load'

/** State of a country's tile statistics (metrics.bin): downloading, failed, or loaded. */
export type StatsState = 'ready' | 'loading' | 'failed'
export function statsState(d: CountryData | undefined, errors?: Partial<Record<string, { stage: string }>>): StatsState {
  if (d?.matrixReady) return 'ready'
  const e = d ? errors?.[d.id] : undefined
  return e && (e.stage === 'statistics' || e.stage === 'map') ? 'failed' : 'loading'
}

const BASE_LABELS: Record<string, string> = {
  gold: 'GDP', production: 'Industrial GVA', food: 'Agricultural GVA', food_sea: 'Fish landings',
  science: 'STEM workers', culture: 'Arts, recreation & hospitality jobs', faith: 'Places of worship',
}

type BonusKey = keyof NonNullable<CountryData['hexes']['bonus']>
const bonusCache = new WeakMap<object, Map<string, Map<number, number>>>()
/**
 * Per-tile yield-bonus input exported by the pipeline (hexes.json `bonus`, the exact values civ.py
 * scored), or undefined when this export has no such input. Tiles not listed hold 0.
 */
function bonusValue(d: CountryData, key: BonusKey, i: number): number | undefined {
  const b = d.hexes.bonus?.[key]
  if (!b) return undefined
  let byKey = bonusCache.get(d.hexes)
  if (!byKey) { byKey = new Map(); bonusCache.set(d.hexes, byKey) }
  let m = byKey.get(key)
  if (!m) { m = new Map(b.i.map((t, k) => [t, b.v[k]])); byKey.set(key, m) }
  return m.get(i) ?? 0
}

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

/**
 * Culture and Faith bonus rules from index.json `yield_scale` (civ.py CULTURE_BONUS / FAITH_BONUS).
 * The defaults only stand in for an older export that did not ship them.
 */
function bonusRules(idx: IndexJson) {
  const cb = idx.yield_scale.culture_bonus
  const fb = idx.yield_scale.faith_bonus
  return {
    culture: {
      per_whs: cb?.per_whs ?? 2, max_whs: cb?.max_whs ?? 2, museums_min: cb?.museums_min ?? 3, museums_bonus: cb?.museums_bonus ?? 1,
      park_visitors_min: cb?.park_visitors_min ?? 1_000_000, park_bonus: cb?.park_bonus ?? 1,
    },
    faith: { per_holy_site: fb?.per_holy_site ?? 2, max_holy_sites: fb?.max_holy_sites ?? 1 },
  }
}

/** `failed`: the tile statistics did not load (shown instead of "loading statistics…"). */
export function explainYields(d: CountryData, idx: IndexJson, i: number, failed = false): YieldExplain[] {
  const ys = idx.yield_scale
  const cap = ys.cap
  const rules = bonusRules(idx)
  const area = d.hexArea[i]
  const sea = d.hexes.ring[i] > 0
  const pts = pointsInHex(d, d.hexes.h3[i])
  const loading = !d.matrixReady
  const waitText = failed ? STATS_FAILED : LOADING_STATS

  const basePart = (bkey: string, label?: string): YieldPart => {
    const b = ys.bases[bkey]
    if (!b) return { label: label ?? BASE_LABELS[bkey] ?? bkey, amount: '–', detail: 'no yield base defined', points: 0, counted: true, missing: true }
    const m = d.metricByKey.get(b.metric)
    const name = label ?? m?.label ?? BASE_LABELS[bkey] ?? b.metric
    const baseTxt = `${fmtThreshold(b.base, isMoneyUnit(m?.unit ?? '') || b.unit.startsWith('US$') ? 'usd' : '')}/km²`
    if (!m) {
      return { label: name, amount: 'not published', detail: `1 point at ${baseTxt} (${b.unit}) · ${b.metric} is not in this export`, points: 0, counted: true, missing: true, metric: b.metric }
    }
    if (loading) {
      return { label: name, amount: waitText, detail: `1 point at ${baseTxt} (${b.unit})`, points: 0, counted: true, loading: true, metric: m.key, sources: m.sources }
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

  type Amount = { v: number; from: string; sources?: string[]; loading?: boolean }
  /**
   * Tile total of a bonus input: the pipeline's own per-tile input (hexes.json `bonus`) when exported,
   * else the published metric, else the exported point layer(s).
   */
  const tileAmount = (bonusKey: BonusKey | null, metricKey: string | null, layers: string[], col: 'value' | 'count'): Amount | null => {
    const m = metricKey ? d.metricByKey.get(metricKey) : undefined
    const b = bonusKey ? bonusValue(d, bonusKey, i) : undefined
    if (b !== undefined) return { v: b, from: 'the facilities the yields were built from', sources: m?.sources }
    if (m) {
      if (loading) return { v: NaN, from: `metric ${m.key}`, sources: m.sources, loading: true }
      const v = d.matrix[m.col * d.n + i]
      return { v: Number.isFinite(v) ? v : 0, from: `metric ${m.key}`, sources: m.sources }
    }
    // first available layer only (the pipeline reads one table per bonus; summing layers could double count)
    const layer = layers.find((l) => d.points?.[l])
    if (!layer) return null
    const pl = d.points[layer]
    let v = 0
    for (const p of pts) {
      if (p.layer !== layer) continue
      if (col === 'count') v += 1
      else { const x = pointValue(p); if (Number.isFinite(x)) v += x }
    }
    const partial = pl.total > pl.rows.length ? `, top ${fmtNumber(pl.rows.length)} of ${fmtNumber(pl.total)} shipped` : ''
    return { v, from: `${layer.replace(/_/g, ' ')} points${partial}` }
  }
  const bonusPart = (label: string, amt: Amount | null, unit: string, points: (v: number) => number, rule: string): YieldPart => {
    if (!amt) return { label, amount: 'not published', detail: rule, points: 0, counted: true, missing: true }
    if (amt.loading) return { label, amount: waitText, detail: rule, points: 0, counted: true, loading: true, sources: amt.sources }
    return {
      label, amount: `${fmtNumber(amt.v)} ${amt.v === 1 ? unit.replace(/s$/, '') : unit}`.trim(), detail: `${rule} · from ${amt.from}`,
      points: points(amt.v), counted: true, sources: amt.sources,
    }
  }

  const stepsText = (steps: [number, number][], unit: string) => steps.map(([t, b]) => `≥ ${fmtNumber(t)} ${unit} +${b}`).join(' · ')

  const out: YieldExplain[] = []
  const actual = (k: YieldKey) => d.hexes.yields?.[k]?.[i] ?? 0
  const finish = (key: YieldKey, parts: YieldPart[], computed?: number, note?: string) => {
    let c = computed ?? parts.filter((p) => p.counted).reduce((s, p) => s + p.points, 0)
    c = Math.max(0, Math.min(cap + 4, c))
    const busy = parts.some((p) => p.loading)
    if (sea && (key === 'science' || key === 'culture' || key === 'faith')) {
      c = 0
      note = 'Sea tiles yield no Science, Culture or Faith.'
      out.push({ key, actual: actual(key), computed: c, parts, note })
      return
    }
    out.push({ key, actual: actual(key), computed: c, parts, note, loading: busy || undefined })
  }

  // Gold
  finish('gold', [basePart('gold')])

  // Production (+ power stations)
  finish('production', [
    basePart('production'),
    bonusPart('Power stations', tileAmount('capacity_mw', 'power_capacity_mw', ['power_plants'], 'value'), 'MW',
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
    if (!land.loading && !fish.loading) { if (land.points >= fish.points) fish.counted = false; else land.counted = false }
    finish('food', [land, fish], best, 'Land tiles take the larger of farm and fishery yield.')
  }

  // Science (+ research output)
  finish('science', [
    basePart('science'),
    bonusPart('Research output', tileAmount('works_5y', 'research_works_5y', ['institutions', 'universities'], 'value'), 'works / 5 y',
      (v) => stepBonus(v, ys.research_bonus), stepsText(ys.research_bonus, 'works')),
  ])

  // Culture (+ World Heritage, museums, park visitors) — rules from index.json yield_scale.culture_bonus
  const cr = rules.culture
  finish('culture', [
    basePart('culture'),
    bonusPart('World Heritage sites', tileAmount('whs', null, ['heritage'], 'count'), 'sites',
      (v) => cr.per_whs * Math.min(v, cr.max_whs), `+${cr.per_whs} per site (max ${cr.max_whs})`),
    bonusPart('Museums', tileAmount('museums', 'museums', ['museums'], 'count'), '',
      (v) => (v >= cr.museums_min ? cr.museums_bonus : 0), `≥ ${fmtNumber(cr.museums_min)} museums +${cr.museums_bonus}`),
    bonusPart('National park visitors', tileAmount('park_visitors', 'park_visitors', ['parks'], 'value'), 'visitors / yr',
      (v) => (v >= cr.park_visitors_min ? cr.park_bonus : 0), `≥ ${fmtNumber(cr.park_visitors_min)} visitors +${cr.park_bonus}`),
  ])

  // Faith (+ listed holy sites) — rules from index.json yield_scale.faith_bonus. The bonus counts
  // every exported site; the Holy Site district only the country's top N by Wikipedia editions —
  // say both so the two cards agree.
  const fr = rules.faith
  const holyLayer = d.points?.holy_sites
  const holyN = holyLayer?.total ?? holyLayer?.rows.length ?? 0
  const topN = holyDistrictTop(idx, d.id)
  const ranks = holySiteRanks(d)
  const here = pts.filter((p) => p.layer === 'holy_sites').map((p) => ({ name: p.row.name, rank: ranks.get(p.row.id) }))
  const perSite = fr.max_holy_sites > 1 ? `+${fr.per_holy_site} per listed holy site (max ${fr.max_holy_sites})` : `+${fr.per_holy_site} for any`
  const holyRule = [
    `${perSite} of the ${fmtNumber(holyN)} listed holy sites (Wikidata, ranked by Wikipedia language editions)`,
    `the Holy Site district needs one of the top ${topN}`,
    ...here.filter((h) => h.rank != null).map((h) => `${h.name} ranks #${h.rank} of ${fmtNumber(holyN)}${h.rank! <= topN ? ' (qualifies for the district)' : ''}`),
  ].join(' · ')
  finish('faith', [
    basePart('faith'),
    bonusPart('Listed holy sites', tileAmount('holy_sites', null, ['holy_sites'], 'count'), 'sites',
      (v) => fr.per_holy_site * Math.min(v, fr.max_holy_sites), holyRule),
  ])
  return out
}
