// Pure helpers for the Tile inspector: percentile caches, point lookups and the yield derivation.
// Everything here is memoised per CountryData / AdminTable object (WeakMap), so a pipeline
// re-export (new objects) naturally invalidates the caches.
import { YIELDS, type AdminTable, type CountryData, type IndexJson, type Metric, type PointRow, type Wonder, type YieldKey } from '../types'
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

export const POINT_LAYER_META: Record<string, { label: string; icon: string; value?: string[]; unit?: string }> = {
  power_plants: { label: 'Power station', icon: '⚡', value: ['capacity_mw'], unit: 'MW' },
  ports: { label: 'Port', icon: '⚓', value: ['port_cargo_t', 'cargo_tonnes'], unit: 't cargo' },
  airports: { label: 'Airport', icon: '✈️', value: ['airport_pax', 'passengers'], unit: 'enplanements (boardings)' },
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
/** civ.py: holy_top = the 10 (LKA) / 25 (US) listed holy sites with the most Wikipedia editions. */
export const HOLY_DISTRICT_TOP: Record<string, number> = { lka: 10, usa: 25 }

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
  const bonusPart = (label: string, amt: ReturnType<typeof tileAmount>, unit: string, points: (v: number) => number, rule: string): YieldPart =>
    amt
      ? { label, amount: `${fmtNumber(amt.v)} ${amt.v === 1 ? unit.replace(/s$/, '') : unit}`.trim(), detail: `${rule} · from ${amt.from}`, points: points(amt.v), counted: true, sources: amt.sources }
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

  // Faith (+ listed holy sites). The bonus counts every exported site; the Holy Site district
  // only the country's top N by Wikipedia editions — say both so the two cards agree.
  const holyLayer = d.points?.holy_sites
  const holyN = holyLayer?.total ?? holyLayer?.rows.length ?? 0
  const topN = HOLY_DISTRICT_TOP[d.id] ?? 10
  const ranks = holySiteRanks(d)
  const here = pts.filter((p) => p.layer === 'holy_sites').map((p) => ({ name: p.row.name, rank: ranks.get(p.row.id) }))
  const holyRule = [
    `+${HOLY_PTS} for any of the ${fmtNumber(holyN)} listed holy sites (Wikidata, ranked by Wikipedia language editions)`,
    `the Holy Site district needs one of the top ${topN}`,
    ...here.filter((h) => h.rank != null).map((h) => `${h.name} ranks #${h.rank} of ${fmtNumber(holyN)}${h.rank! <= topN ? ' (qualifies for the district)' : ''}`),
  ].join(' · ')
  finish('faith', [
    basePart('faith'),
    bonusPart('Listed holy sites', tileAmount(null, ['holy_sites'], 'count'), 'sites',
      (v) => (v >= 1 ? HOLY_PTS : 0), holyRule),
  ])
  return out
}
