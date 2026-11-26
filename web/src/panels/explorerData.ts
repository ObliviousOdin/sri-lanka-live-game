// Explorer data preparation: metric availability, axis transforms (density / per capita),
// per-country series, plot arrays + OLS fits, brushing, selection mapping, names and formatting.
// Pure functions only (no React) so the panel can memoise each stage separately.
import { cellToLatLng } from 'h3-js'
import { adminUnitOfHex, column, hexesOfAdmin } from '../data/load'
import { fmtPct } from '../format'
import type { AdminUnit, CountryData, CountryId, Metric, Selection } from '../types'

export type ExLevel = 'hex' | 'adm1' | 'adm2'
export type Norm = 'total' | 'density' | 'capita'
export type ColorBy = 'country' | 'lens'

// Metric categories (pipeline/civmap/schema.py CATEGORIES) with their Civ reading.
export const CATEGORY_META: Record<string, { label: string; icon: string; civ?: string }> = {
  population: { label: 'Population', icon: '👥', civ: 'Citizens' },
  economy: { label: 'Economy', icon: '◆', civ: 'Gold' },
  income: { label: 'Income & poverty', icon: '💵', civ: 'Gold' },
  labor: { label: 'Workforce', icon: '👷', civ: 'Citizens' },
  production: { label: 'Industry', icon: '⚒', civ: 'Production' },
  energy: { label: 'Energy', icon: '⚡', civ: 'Production' },
  food: { label: 'Agriculture & fisheries', icon: '🌾', civ: 'Food' },
  science: { label: 'Research', icon: '⚗', civ: 'Science' },
  education: { label: 'Education', icon: '🎓', civ: 'Science' },
  culture: { label: 'Culture & tourism', icon: '✦', civ: 'Culture' },
  faith: { label: 'Religion', icon: '☼', civ: 'Faith' },
  society: { label: 'Society', icon: '🧑‍🤝‍🧑' },
  hazard: { label: 'Disasters', icon: '🌪️' },
  trade: { label: 'Trade', icon: '⚓', civ: 'Trade routes' },
  infrastructure: { label: 'Infrastructure', icon: '🛣️' },
  landcover: { label: 'Land cover', icon: '🌳', civ: 'Terrain' },
  elevation: { label: 'Elevation', icon: '🏔️', civ: 'Terrain' },
  environment: { label: 'Climate', icon: '🌦️', civ: 'Terrain' },
  military: { label: 'Military', icon: '🛡️', civ: 'Encampments' },
}
const CATEGORY_ORDER = Object.keys(CATEGORY_META)

export const X_PREFS = ['pop_density', 'pop', 'gdp_usd', 'built_km2']
export const Y_PREFS = ['gdp_per_capita_usd', 'hh_income_usd', 'gdp_usd', 'rwi', 'poverty_rate', 'pct_bachelors', 'elev_mean_m', 'precip_mm']

const LEVEL_RANK: Record<string, number> = { hex: 9, adm1: 1, adm2: 2, adm3: 3 }

// ---------------------------------------------------------------------------------------------
// Metric availability & options
// ---------------------------------------------------------------------------------------------

const availCache = new WeakMap<CountryData, Map<ExLevel, Set<string>>>()

/** Keys with at least one finite value for this country at this level (cached per data object). */
export function availableKeys(d: CountryData, level: ExLevel): Set<string> {
  let byLevel = availCache.get(d)
  if (!byLevel) { byLevel = new Map(); availCache.set(d, byLevel) }
  let s = byLevel.get(level)
  if (s) return s
  s = new Set()
  if (level === 'hex') {
    for (const m of d.meta.metrics) {
      const col = column(d, m.key)
      if (!col) continue
      for (let i = 0; i < col.length; i++) if (Number.isFinite(col[i])) { s.add(m.key); break }
    }
  } else {
    const t = d.admin[level]
    if (t) {
      for (const m of d.meta.metrics) {
        const a = t.metrics[m.key]
        if (a && a.some((v) => v != null && Number.isFinite(v))) s.add(m.key)
      }
    }
  }
  byLevel.set(level, s)
  return s
}

export interface MetricOpt { key: string; label: string; category: string; metric: Metric; countries: CountryId[] }
export interface MetricGroup { category: string; label: string; opts: MetricOpt[] }
export interface MetricOptions { groups: MetricGroup[]; byKey: Map<string, MetricOpt> }

/** Union of metrics present in the given countries (marking country-only ones), grouped by category. */
export function metricOptions(ds: CountryData[], level: ExLevel): MetricOptions {
  const byKey = new Map<string, MetricOpt>()
  for (const d of ds) {
    const av = availableKeys(d, level)
    for (const m of d.meta.metrics) {
      if (!av.has(m.key)) continue
      const o = byKey.get(m.key)
      if (o) o.countries.push(d.id)
      else byKey.set(m.key, { key: m.key, label: m.label, category: m.category, metric: m, countries: [d.id] })
    }
  }
  const groups = new Map<string, MetricOpt[]>()
  for (const o of byKey.values()) {
    const g = groups.get(o.category) ?? []
    g.push(o)
    groups.set(o.category, g)
  }
  const cats = [...groups.keys()].sort((a, b) => rankOf(a) - rankOf(b) || a.localeCompare(b))
  return {
    byKey,
    groups: cats.map((c) => {
      const cm = CATEGORY_META[c]
      const label = cm ? `${cm.icon} ${cm.label}${cm.civ ? ` · ${cm.civ}` : ''}` : c
      return { category: c, label, opts: groups.get(c)! }
    }),
  }
}
const rankOf = (c: string) => { const i = CATEGORY_ORDER.indexOf(c); return i < 0 ? 99 : i }

/** The wanted key if available, else the first available preference (≠ avoid), else anything. */
export function resolveKey(want: string, byKey: Map<string, MetricOpt>, prefs: string[], avoid: string | null): string | null {
  if (byKey.has(want)) return want
  for (const p of prefs) if (p !== avoid && byKey.has(p)) return p
  for (const k of byKey.keys()) if (k !== avoid) return k
  return byKey.keys().next().value ?? null
}

// ---------------------------------------------------------------------------------------------
// Axes: transform rules and values
// ---------------------------------------------------------------------------------------------

export interface AxisInfo {
  key: string
  metric: Metric
  label: string
  unit: string
  tf: 'raw' | 'density' | 'capita'
}

/** Tiles: extensive → per km² of tile. Admin: extensive → total / per km² of land / per resident. */
export function axisInfo(m: Metric, level: ExLevel, norm: Norm): AxisInfo {
  let tf: AxisInfo['tf'] = 'raw'
  if (m.kind === 'extensive') {
    if (level === 'hex') tf = 'density'
    else if (norm === 'density' && m.key !== 'land_km2') tf = 'density'
    else if (norm === 'capita' && m.key !== 'pop') tf = 'capita'
  }
  const base = { key: m.key, metric: m, tf }
  const u = m.unit
  if (tf === 'density') {
    if (u === 'km2') return { ...base, label: `${m.label} (share of ${level === 'hex' ? 'tile' : 'land'})`, unit: 'share' }
    return { ...base, label: `${m.label} per km²`, unit: `${u}/km2` }
  }
  if (tf === 'capita') {
    if (u === 'persons') return { ...base, label: `${m.label} (share of residents)`, unit: 'share' }
    return { ...base, label: `${m.label} per capita`, unit: `${u}/person` }
  }
  return { ...base, label: m.label, unit: u }
}

/** Per-row values (row = hex index or admin unit index) for one axis, NaN where missing. */
export function axisValues(d: CountryData, level: ExLevel, ax: AxisInfo): ArrayLike<number> | null {
  if (level === 'hex') {
    const col = column(d, ax.key)
    if (!col) return null
    if (ax.tf !== 'density') return col
    const out = new Float32Array(d.n)
    for (let i = 0; i < d.n; i++) out[i] = col[i] / d.hexArea[i]
    return out
  }
  const t = d.admin[level]
  const a = t?.metrics[ax.key]
  if (!t || !a) return null
  const den = ax.tf === 'density' ? t.metrics.land_km2 : ax.tf === 'capita' ? t.metrics.pop : null
  if (ax.tf !== 'raw' && !den) return null
  const out = new Float64Array(a.length)
  for (let u = 0; u < a.length; u++) {
    const v = a[u]
    if (v == null) { out[u] = NaN; continue }
    if (den) { const q = den[u]; out[u] = q != null && q > 0 ? v / q : NaN } else out[u] = v
  }
  return out
}

export interface Series {
  country: CountryId
  d: CountryData
  /** row ids (hex or unit index) with finite X and Y */
  rows: Int32Array
  xv: Float64Array
  yv: Float64Array
  /** total rows at this level (for lookup tables) */
  size: number
  missing: null | 'x' | 'y' | 'both' | 'level'
}

export function buildSeries(d: CountryData, level: ExLevel, ax: AxisInfo, ay: AxisInfo): Series {
  const size = level === 'hex' ? d.n : d.admin[level]?.units.length ?? 0
  const empty = (missing: Series['missing']): Series =>
    ({ country: d.id, d, rows: new Int32Array(0), xv: new Float64Array(0), yv: new Float64Array(0), size, missing })
  if (level !== 'hex' && !d.admin[level]) return empty('level')
  const x = axisValues(d, level, ax)
  const y = axisValues(d, level, ay)
  if (!x || !y) return empty(!x && !y ? 'both' : !x ? 'x' : 'y')
  // Water tiles are only kept for metrics allocated to the coast (e.g. fish landings).
  const coast = level === 'hex' && (d.metricByKey.get(ax.key)?.weight === 'coast' || d.metricByKey.get(ay.key)?.weight === 'coast')
  const ring = d.hexes.ring
  const tmp = new Int32Array(size)
  let k = 0
  for (let i = 0; i < size; i++) {
    if (level === 'hex' && !coast && ring[i] !== 0) continue
    if (Number.isFinite(x[i]) && Number.isFinite(y[i])) tmp[k++] = i
  }
  const rows = tmp.slice(0, k)
  const xv = new Float64Array(k), yv = new Float64Array(k)
  for (let j = 0; j < k; j++) { xv[j] = x[rows[j]]; yv[j] = y[rows[j]] }
  return { country: d.id, d, rows, xv, yv, size, missing: null }
}

// ---------------------------------------------------------------------------------------------
// Plot arrays, log handling, fits
// ---------------------------------------------------------------------------------------------

export interface Fit { n: number; r: number; slope: number; intercept: number; t0: number; t1: number }
export interface Seg { country: CountryId; s: Series; start: number; end: number; hidden: number; fit: Fit | null }

export interface PlotData {
  n: number
  row: Int32Array
  x: Float64Array
  y: Float64Array
  segs: Seg[]
  logX: boolean
  logY: boolean
  /** why a requested log axis was switched off */
  logBlocked: { x: string | null; y: string | null }
  domX: [number, number]
  domY: [number, number]
  /** row id → plot point index, per country */
  pos: Partial<Record<CountryId, Int32Array>>
}

export function buildPlot(series: Series[], wantLogX: boolean, wantLogY: boolean): PlotData {
  let total = 0, posX = 0, posY = 0
  for (const s of series) {
    total += s.rows.length
    for (let j = 0; j < s.rows.length; j++) { if (s.xv[j] > 0) posX++; if (s.yv[j] > 0) posY++ }
  }
  // auto-disable log when non-positive values dominate
  const okX = posX >= 2 && posX / Math.max(1, total) >= 0.5
  const okY = posY >= 2 && posY / Math.max(1, total) >= 0.5
  const logX = wantLogX && okX
  const logY = wantLogY && okY
  const why = (pos: number) => total ? `${Math.round(100 * (1 - pos / total))}% of values are ≤ 0` : 'no data'
  const logBlocked = { x: wantLogX && !okX ? why(posX) : null, y: wantLogY && !okY ? why(posY) : null }

  // USA first so the (fewer) Sri Lankan points are drawn on top
  const ordered = [...series].sort((a, b) => (a.country === 'usa' ? 0 : 1) - (b.country === 'usa' ? 0 : 1))
  let n = 0
  for (const s of ordered) n += s.rows.length
  const row = new Int32Array(n), x = new Float64Array(n), y = new Float64Array(n)
  const segs: Seg[] = []
  const pos: PlotData['pos'] = {}
  let k = 0
  for (const s of ordered) {
    const start = k
    const p = new Int32Array(s.size).fill(-1)
    for (let j = 0; j < s.rows.length; j++) {
      const a = s.xv[j], b = s.yv[j]
      if ((logX && a <= 0) || (logY && b <= 0)) continue
      row[k] = s.rows[j]; x[k] = a; y[k] = b; p[s.rows[j]] = k; k++
    }
    pos[s.country] = p
    segs.push({ country: s.country, s, start, end: k, hidden: s.rows.length - (k - start), fit: null })
  }
  const out: PlotData = {
    n: k, row: row.subarray(0, k), x: x.subarray(0, k), y: y.subarray(0, k), segs, logX, logY, logBlocked,
    domX: domain(x.subarray(0, k), logX), domY: domain(y.subarray(0, k), logY), pos,
  }
  for (const sg of segs) sg.fit = fitSeg(out, sg)
  return out
}

function domain(a: Float64Array, log: boolean): [number, number] {
  let lo = Infinity, hi = -Infinity
  for (let i = 0; i < a.length; i++) { const v = a[i]; if (v < lo) lo = v; if (v > hi) hi = v }
  if (!Number.isFinite(lo)) return log ? [1, 10] : [0, 1]
  if (log) {
    if (hi / lo < 1.0001) return [lo / 2, hi * 2]
    const pad = Math.pow(hi / lo, 0.045)
    return [lo / pad, hi * pad]
  }
  if (hi - lo < 1e-12) { const w = Math.abs(lo) || 1; return [lo - w * 0.5, hi + w * 0.5] }
  const pad = (hi - lo) * 0.045
  return [lo - pad, hi + pad]
}

/** Pearson r and OLS line on the displayed transform (log10 for log axes). */
function fitSeg(p: PlotData, sg: Seg): Fit | null {
  const n = sg.end - sg.start
  if (n < 3) return null
  let mx = 0, my = 0, t0 = Infinity, t1 = -Infinity
  const tx = (v: number) => (p.logX ? Math.log10(v) : v)
  const ty = (v: number) => (p.logY ? Math.log10(v) : v)
  for (let i = sg.start; i < sg.end; i++) {
    const a = tx(p.x[i]); mx += a; my += ty(p.y[i])
    if (a < t0) t0 = a; if (a > t1) t1 = a
  }
  mx /= n; my /= n
  let sxx = 0, syy = 0, sxy = 0
  for (let i = sg.start; i < sg.end; i++) {
    const a = tx(p.x[i]) - mx, b = ty(p.y[i]) - my
    sxx += a * a; syy += b * b; sxy += a * b
  }
  if (sxx <= 0) return null
  const slope = sxy / sxx
  return { n, r: syy > 0 ? sxy / Math.sqrt(sxx * syy) : NaN, slope, intercept: my - slope * mx, t0, t1 }
}

export function segOf(p: PlotData, i: number): Seg | undefined {
  for (const s of p.segs) if (i >= s.start && i < s.end) return s
  return undefined
}

// ---------------------------------------------------------------------------------------------
// Brushing
// ---------------------------------------------------------------------------------------------

export type Brushed = Partial<Record<CountryId, Set<number>>>
/** Remembers which admin units the last brush covered (units with no dominant tile have no hexes). */
export interface BrushMemo { brushed: Brushed; level: ExLevel; units: Partial<Record<CountryId, Set<number>>> }
export interface BrushInfo { flags: Uint8Array; points: number; tiles: number }

export function hasBrush(b: Brushed | undefined) {
  return !!b && Object.values(b).some((s) => s && s.size > 0)
}

/** Per-plot-point brushed flags derived from the store's brushed hex sets. */
export function brushFlags(p: PlotData, brushed: Brushed, level: ExLevel, memo: BrushMemo | null): BrushInfo | null {
  if (!hasBrush(brushed)) return null
  const flags = new Uint8Array(p.n)
  let points = 0, tiles = 0
  for (const s of Object.values(brushed)) if (s) tiles += s.has(-1) ? s.size - 1 : s.size
  for (const sg of p.segs) {
    const set = brushed[sg.country]
    if (!set || !set.size) continue
    let isOn: (row: number) => boolean
    if (level === 'hex') isOn = (r) => set.has(r)
    else {
      const units = memo && memo.brushed === brushed && memo.level === level ? memo.units[sg.country] : undefined
      if (units) isOn = (r) => units.has(r)
      else {
        const arr = (sg.s.d.hexes as unknown as Record<string, number[]>)[level]
        const mark = new Uint8Array(sg.s.size)
        if (arr) for (const i of set) { const u = arr[i]; if (u >= 0) mark[u] = 1 }
        isOn = (r) => mark[r] === 1
      }
    }
    for (let i = sg.start; i < sg.end; i++) if (isOn(p.row[i])) { flags[i] = 1; points++ }
  }
  return { flags, points, tiles }
}

/**
 * Plot points → store brushed hex sets. Countries in the plot with no brushed point get a set
 * holding only -1 so MapPane dims that whole map too (an empty set means "no brush" there).
 */
export function brushSets(p: PlotData, pts: number[], level: ExLevel): BrushMemo {
  const rows: Partial<Record<CountryId, Set<number>>> = {}
  for (const i of pts) {
    const sg = segOf(p, i)
    if (!sg) continue
    ;(rows[sg.country] ??= new Set()).add(p.row[i])
  }
  const brushed: Brushed = {}
  for (const sg of p.segs) {
    const r = rows[sg.country]
    if (!r || !r.size) { brushed[sg.country] = new Set([-1]); continue }
    if (level === 'hex') { brushed[sg.country] = r; continue }
    const d = sg.s.d
    const arr = (d.hexes as unknown as Record<string, number[]>)[level]
    const out = new Set<number>()
    if (arr) for (let i = 0; i < d.n; i++) if (d.hexes.ring[i] === 0 && r.has(arr[i])) out.add(i)
    brushed[sg.country] = out.size ? out : new Set([-1])
  }
  return { brushed, level, units: rows }
}

// ---------------------------------------------------------------------------------------------
// Selection ↔ plot points
// ---------------------------------------------------------------------------------------------

export interface Targets { primary: number; secondary: Int32Array | null }
const NONE: Targets = { primary: -1, secondary: null }

/** Map the app selection (tile / admin unit / city / wonder) onto plot points. */
export function selectionTargets(p: PlotData, sel: Selection | null, level: ExLevel): Targets {
  if (!sel) return NONE
  const sg = p.segs.find((s) => s.country === sel.country)
  const pos = p.pos[sel.country]
  if (!sg || !pos) return NONE
  const d = sg.s.d
  const at = (r: number) => (r >= 0 && r < pos.length ? pos[r] : -1)
  const many = (rows: number[]) => { const out = rows.map(at).filter((x) => x >= 0); return out.length ? Int32Array.from(out) : null }
  if (sel.kind === 'admin') {
    const [lvl, s] = sel.id.split(':')
    const ui = Number(s)
    if (!d.admin[lvl]?.units[ui]) return NONE
    if (level === 'hex') return { primary: -1, secondary: many(hexesOfAdmin(d, lvl, ui)) }
    if (lvl === level) return { primary: at(ui), secondary: null }
    if ((LEVEL_RANK[lvl] ?? 0) > LEVEL_RANK[level]) {
      // finer unit (e.g. DS division) → the district that contains it
      const hs = hexesOfAdmin(d, lvl, ui)
      const u = hs.length ? adminUnitOfHex(d, level, hs[0]) : null
      return { primary: u ? at(u.index) : -1, secondary: null }
    }
    // coarser unit (province / state) → all of its districts / counties
    const pid = d.admin[lvl].units[ui].id
    const kids: number[] = []
    d.admin[level]?.units.forEach((u, j) => { if (u.parent === pid) kids.push(j) })
    return { primary: -1, secondary: many(kids) }
  }
  let hex: number | undefined
  let cityTiles: string[] | null = null
  if (sel.kind === 'hex') hex = d.hexIndex.get(sel.id)
  else if (sel.kind === 'city') {
    const c = d.cities.find((x) => x.id === sel.id)
    if (c) { hex = d.hexIndex.get(c.centre); cityTiles = c.tiles.map((t) => t.h3) }
  } else if (sel.kind === 'wonder') {
    const w = d.wonders.find((x) => x.name === sel.id)
    if (w) hex = d.hexIndex.get(w.h3)
  }
  if (hex == null) return NONE
  if (level === 'hex') {
    const sec = cityTiles ? many(cityTiles.map((h) => d.hexIndex.get(h) ?? -1)) : null
    return { primary: at(hex), secondary: sec }
  }
  const u = adminUnitOfHex(d, level, hex)
  return { primary: u ? at(u.index) : -1, secondary: null }
}

// ---------------------------------------------------------------------------------------------
// Names & positions
// ---------------------------------------------------------------------------------------------

const adm1Cache = new WeakMap<CountryData, Map<string, AdminUnit>>()
function adm1ById(d: CountryData) {
  let m = adm1Cache.get(d)
  if (!m) {
    m = new Map((d.admin.adm1?.units ?? []).map((u) => [u.id, u]))
    adm1Cache.set(d, m)
  }
  return m
}

export function unitName(d: CountryData, level: string, ui: number): string {
  const u = d.admin[level]?.units[ui]
  if (!u) return `Unit ${ui}`
  if (level === 'adm1') return u.name
  const p = adm1ById(d).get(u.parent)
  return p ? `${u.name}, ${p.abbr ?? p.name}` : u.name
}

/** Short tile id: H3 index without its trailing 'f' padding, last 6 hex digits. */
export const hexShort = (h3: string) => h3.replace(/f+$/, '').slice(-6)

export function rowName(d: CountryData, level: ExLevel, row: number): string {
  if (level !== 'hex') return unitName(d, level, row)
  const a = d.hexes.adm2?.[row] ?? -1
  return `${a >= 0 ? unitName(d, 'adm2', a) : 'Open water'} · ${hexShort(d.hexes.h3[row])}`
}

/** [lon, lat] of a row. */
export function rowLonLat(d: CountryData, level: ExLevel, row: number): [number, number] {
  if (level === 'hex') { const [la, lo] = cellToLatLng(d.hexes.h3[row]); return [lo, la] }
  const u = d.admin[level].units[row]
  return [u.lon, u.lat]
}

export function levelNoun(ds: CountryData[], level: ExLevel, plural = true): string {
  if (level === 'hex') return plural ? 'Tiles' : 'Tile'
  const names = [...new Set(ds.map((d) => d.meta.admin_levels[level]?.label).filter(Boolean) as string[])]
  const pl = (s: string) => (!plural ? s : /[^aeiou]y$/.test(s) ? s.slice(0, -1) + 'ies' : s + 's')
  return names.length ? names.map(pl).join(' / ') : level
}

// ---------------------------------------------------------------------------------------------
// Formatting (unit aware, compact)
// ---------------------------------------------------------------------------------------------

const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumSignificantDigits: 3 })
const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const one = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const two = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

export function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return '–'
  const a = Math.abs(v)
  if (a === 0) return '0'
  if (a >= 1e4) return compact.format(v)
  if (a >= 100) return int.format(v)
  if (a >= 10) return one.format(v)
  if (a >= 1) return two.format(v)
  return String(Number(v.toPrecision(2)))
}

function fmtMoney(v: number): string {
  const a = Math.abs(v)
  const s = v < 0 ? '-' : ''
  if (a >= 1e12) return `${s}$${fmtNum(a / 1e12)}T`
  if (a >= 1e9) return `${s}$${fmtNum(a / 1e9)}B`
  if (a >= 1e6) return `${s}$${fmtNum(a / 1e6)}M`
  if (a >= 1e4) return `${s}$${fmtNum(a / 1e3)}k`
  return `${s}$${fmtNum(a)}`
}

/** Pretty unit text: km2 → km², degC → °C, count/km2 → per km². */
export function prettyUnit(u: string): string {
  return u.replace(/km2/g, 'km²').replace(/degC/g, '°C').replace(/^deg$/, '°').replace(/^(count|events)\//, 'per ')
}

const isUsd = (u: string) => /^(usd|us\$)/i.test(u)
const isShare = (u: string) => ['share', 'fraction', '0-1'].includes(u.toLowerCase())
const isPct = (u: string) => ['%', 'percent', 'pct'].includes(u.toLowerCase())

/** Full value with unit (tooltips). */
export function fmtVal(v: number, unit: string): string {
  if (!Number.isFinite(v)) return '–'
  if (isShare(unit)) return fmtPct(v * 100, Math.abs(v) < 0.01 && v !== 0 ? 2 : 1)
  if (isPct(unit)) return fmtPct(v, Math.abs(v) < 1 && v !== 0 ? 2 : 1)
  if (isUsd(unit)) { const rest = unit.replace(/^(usd|us\$)/i, ''); return fmtMoney(v) + prettyUnit(rest) }
  const u = unit.toLowerCase()
  if (u === 'persons' || u === 'count' || u === 'people' || u === '') return fmtNum(v)
  return `${fmtNum(v)} ${prettyUnit(unit)}`
}

/** Short value (ticks & table cells): keeps $ and %, drops other units (shown in headers). */
export function fmtShort(v: number, unit: string): string {
  if (!Number.isFinite(v)) return '–'
  if (isShare(unit)) return `${fmtNum(v * 100)}%`
  if (isPct(unit)) return `${fmtNum(v)}%`
  if (isUsd(unit)) return fmtMoney(v)
  return fmtNum(v)
}

/** Unit caption for headers/axis titles. */
export function unitCaption(unit: string): string {
  if (isShare(unit) || isPct(unit)) return '%'
  if (isUsd(unit)) return 'US$' + prettyUnit(unit.replace(/^(usd|us\$)/i, ''))
  return prettyUnit(unit)
}

// ---------------------------------------------------------------------------------------------
// Provenance (derived metrics such as GDP per capita list no sources of their own)
// ---------------------------------------------------------------------------------------------

export interface Provenance { sources: string[]; vintage: string; via: string[] }

/** Sources & vintage of a metric; for derived metrics, those of the metric keys named in its method. */
export function provenance(d: CountryData, key: string): Provenance {
  const m = d.metricByKey.get(key)
  if (!m) return { sources: [], vintage: '', via: [] }
  if (m.sources.length && m.vintage) return { sources: m.sources, vintage: countryVintage(m.vintage, d.id), via: [] }
  const via = [...new Set((m.method.match(/[a-z][a-z0-9_]+/g) ?? []).filter((t) => t !== key && d.metricByKey.has(t)))]
  const parts = via.map((k) => d.metricByKey.get(k)!)
  const sources = m.sources.length ? m.sources : [...new Set(parts.flatMap((p) => p.sources))]
  const vintage = m.vintage
    ? countryVintage(m.vintage, d.id)
    : [...new Set(parts.map((p) => countryVintage(p.vintage, d.id)).filter(Boolean))].join(', ')
  return { sources, vintage, via }
}

/** "USA 2024; Sri Lanka 2023" → the part for this country ("2023"); other strings unchanged. */
export function countryVintage(v: string, c: CountryId): string {
  if (!v || !v.includes(';')) return v
  const names = c === 'lka' ? /^(sri lanka|lka|lk)\b[:\s]*/i : /^(usa|united states|us)\b[:\s]*/i
  const part = v.split(';').map((x) => x.trim()).find((x) => names.test(x))
  return part ? part.replace(names, '').trim() || v : v
}
