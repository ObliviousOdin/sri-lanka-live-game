import { CITIZEN_META, COUNTRY_META, DISTRICT_META, FEATURE_META, TERRAIN_META, YIELD_META, type RGB } from './civ'
import { CATEGORICAL, PALETTES, mix, ramp, scaleT, shade, sortedDomain, sortedQuantile, yieldColor } from './color'
import { column } from './data/load'
import { fmtNumber, fmtUsd, prettyUnit } from './format'
import { COUNTRIES, type CountryData, type CountryId, type IndexJson, type Metric, type YieldKey } from './types'

export type LensGroup = 'Map' | 'Yields' | 'People' | 'Economy' | 'Environment' | 'Infrastructure'

export interface LensDef {
  id: string
  label: string
  group: LensGroup
  icon: string
  kind: 'terrain' | 'district' | 'yield' | 'metric' | 'dominant'
  yield?: YieldKey
  /** candidate metric keys, first available is the default */
  metrics?: string[]
  /** for dominant lenses: metric key prefix & suffix filter, or explicit keys */
  dominant?: { prefix: string; suffix?: string; exclude?: string[]; byLQ?: boolean; aggregates?: Record<string, string[]> }
  palette?: keyof typeof PALETTES
  /** true: always log; false/undefined: linear (densities of extensive metrics are always log) */
  log?: boolean
  /** pick log automatically for skewed positive metrics (pooled p98/p02 ≥ 20; never for %, shares, indices) */
  autoLog?: boolean
  description: string
  pointLayers?: string[]
}

export const LENSES: LensDef[] = [
  { id: 'terrain', label: 'Terrain', group: 'Map', icon: '⛰️', kind: 'terrain', description: 'Base terrain from ESA WorldCover land cover, Copernicus DEM relief and WorldClim climate.' },
  // id stays 'districts'; the UI says "Specialist districts" so it is not read as an admin district
  { id: 'districts', label: 'Specialist districts', group: 'Map', icon: '🏛️', kind: 'district', description: 'Specialist districts assigned by published-data rules (see each district’s rule). Rings: US spaceport sites, filled when they host orbital launches.', pointLayers: ['spaceports'] },
  { id: 'gold', label: 'Gold', group: 'Yields', icon: YIELD_META.gold.glyph, kind: 'yield', yield: 'gold', description: YIELD_META.gold.blurb },
  { id: 'production', label: 'Production', group: 'Yields', icon: YIELD_META.production.glyph, kind: 'yield', yield: 'production', description: YIELD_META.production.blurb },
  { id: 'food', label: 'Food', group: 'Yields', icon: YIELD_META.food.glyph, kind: 'yield', yield: 'food', description: YIELD_META.food.blurb },
  { id: 'science', label: 'Science', group: 'Yields', icon: YIELD_META.science.glyph, kind: 'yield', yield: 'science', description: YIELD_META.science.blurb },
  { id: 'culture', label: 'Culture', group: 'Yields', icon: YIELD_META.culture.glyph, kind: 'yield', yield: 'culture', description: YIELD_META.culture.blurb },
  { id: 'faith', label: 'Faith', group: 'Yields', icon: YIELD_META.faith.glyph, kind: 'yield', yield: 'faith', description: YIELD_META.faith.blurb },
  { id: 'population', label: 'Population density', group: 'People', icon: '👥', kind: 'metric', metrics: ['pop_density'], palette: 'magma', log: true, description: 'Residents per km² of land (WorldPop 2020 distribution scaled to official 2023 totals).' },
  { id: 'income', label: 'Income', group: 'Economy', icon: '💵', kind: 'metric', metrics: ['hh_income_usd', 'gdp_per_capita_usd', 'poverty_rate', 'rwi'], palette: 'viridis', autoLog: true, description: 'Household income, GDP per capita and poverty (current US$ at market exchange rates), and the Relative Wealth Index (Sri Lanka only). Incomes span ~40x across the two countries, so money is shown on a log scale.' },
  { id: 'education', label: 'Education', group: 'People', icon: '🎓', kind: 'metric', metrics: ['pct_bachelors', 'pct_secondary', 'literacy_rate', 'schools'], palette: 'blues', log: false, description: 'Educational attainment of adults and schools.' },
  { id: 'religion', label: 'Religion', group: 'People', icon: '🪔', kind: 'dominant', dominant: { prefix: 'rel_', suffix: '_pct', exclude: ['rel_unclaimed_pct'], aggregates: { rel_christian_pct: ['rel_evangelical_pct', 'rel_catholic_pct', 'rel_mainline_pct', 'rel_black_protestant_pct', 'rel_orthodox_pct', 'rel_lds_pct'] } }, metrics: ['religious_diversity'], description: 'Largest religious tradition (shade = its share). Sri Lanka: Census; US: 2020 U.S. Religion Census adherents.' },
  { id: 'ethnicity', label: 'Ethnicity', group: 'People', icon: '🧑‍🤝‍🧑', kind: 'dominant', dominant: { prefix: 'eth_', suffix: '_pct' }, metrics: ['ethnic_diversity'], description: 'Largest ethnic group (shade = its share). Sri Lanka: Census; US: ACS race & Hispanic origin.' },
  { id: 'workforce', label: 'Workforce', group: 'People', icon: '👷', kind: 'dominant', dominant: { prefix: 'cit_', suffix: '_pct', byLQ: true }, description: 'Most over-represented citizen class (highest location quotient vs the national mix).' },
  { id: 'disasters', label: 'Disasters', group: 'Environment', icon: '🌪️', kind: 'metric', metrics: ['hazard_index', 'cyclone_tracks', 'quakes_m5', 'tsunami_runups', 'nri_risk_score', 'nri_eal_usd', 'disaster_deaths_per_100k', 'flood_events', 'landslide_events'], palette: 'hazard', log: false, description: 'Tropical cyclones (IBTrACS), earthquakes (USGS), tsunami run-ups (NOAA) and national loss databases.' },
  { id: 'energy', label: 'Energy', group: 'Infrastructure', icon: '⚡', kind: 'metric', metrics: ['power_capacity_mw', 'generation_gwh', 'renewable_capacity_mw'], palette: 'inferno', log: true, description: 'Installed generating capacity and generation per km² of tile.', pointLayers: ['power_plants'] },
  { id: 'trade', label: 'Trade', group: 'Infrastructure', icon: '⚓', kind: 'metric', metrics: ['port_cargo_t', 'airport_pax', 'emp_transport', 'emp_trade'], palette: 'blues', log: true, description: 'Port cargo, airport enplanements (US; FAA boardings — Sri Lanka publishes no airport-level passenger statistics) and trade/transport employment, per km² of tile.', pointLayers: ['ports', 'airports'] },
  { id: 'landcover', label: 'Land cover', group: 'Environment', icon: '🌳', kind: 'dominant', dominant: { prefix: 'lc_' }, metrics: ['lc_tree', 'lc_crop', 'lc_built', 'protected_pct'], description: 'Dominant ESA WorldCover 2021 class.' },
  { id: 'elevation', label: 'Elevation', group: 'Environment', icon: '🏔️', kind: 'metric', metrics: ['elev_mean_m', 'relief_m', 'slope_mean_deg', 'temp_mean_c', 'precip_mm'], palette: 'terrain', log: false, description: 'Copernicus DEM GLO-90 elevation and relief; WorldClim climate.' },
  { id: 'custom', label: 'Any metric', group: 'Economy', icon: '📊', kind: 'metric', metrics: [], palette: 'viridis', autoLog: true, description: 'Any metric chosen in the Explorer.' },
]

export const LENS_BY_ID = new Map(LENSES.map((l) => [l.id, l]))

// ---------------------------------------------------------------------------------------------
// Basis & units. Extensive metrics (sums: people, US$, MW…) are coloured per km² of TILE area
// (d.hexArea, the same basis yields use; tiles are ~36 km² in Sri Lanka and ~253 km² in the US).
// Areas (km²) become a share of the tile. Intensive metrics (rates, medians, indices) are shown as is;
// pop_density is per km² of LAND (water excluded) and is labelled so.
// ---------------------------------------------------------------------------------------------

export type LensBasis = 'raw' | 'density' | 'share'

const AREA_UNIT = /^\s*km(2|²)\s*$/i
const MONEY = /^\s*(usd|us\$)/i
const PCT_UNITS = new Set(['%', 'percent', 'pct'])
const SHARE_UNITS = new Set(['share', 'fraction', '0-1'])
const COUNT_UNITS = new Set(['count', ''])
const PEOPLE_UNITS = new Set(['persons', 'people'])
const LAND_DENSITY_UNITS = new Set(['persons/km2', 'people/km2', 'persons/km²', 'people/km²'])
/** Bounded or signed units that are never put on a log ramp automatically. */
const LINEAR_UNITS = new Set([...PCT_UNITS, ...SHARE_UNITS, 'index', 'score', 'magnitude', 'deg', 'degc', 'm', 'kt'])

/** How a metric is coloured on the map (same rule as the Tile inspector's hasDensity). */
export function lensBasis(m: Pick<Metric, 'kind' | 'unit'>): LensBasis {
  if (m.kind !== 'extensive') return 'raw'
  return AREA_UNIT.test(m.unit) ? 'share' : 'density'
}

/**
 * A statistic of the land and its residents (admin-unit tables and values derived from them): sea
 * tiles (ring > 0) must not show it, even when the export carries a value there (reconcile's sea
 * pieces inherit the nearest unit's rates). Sea tiles keep coastal-sea allocations (weight 'coast',
 * fish landings), per-hex rasters (hazards, land cover, protected areas, elevation) and facility
 * points snapped offshore.
 */
export function landOnlyMetric(m: Pick<Metric, 'weight' | 'table'>): boolean {
  if (m.weight === 'coast') return false
  const t = m.table ?? ''
  return !(t === 'hex' || t === 'hexes' || t.startsWith('points_'))
}

/** Metric keys that are coloured as densities (extensive metrics divided by tile area; areas excluded). */
export function isDensity(d: CountryData, key: string) {
  const m = d.metricByKey.get(key)
  return !!m && lensBasis(m) === 'density'
}

/** Unit caption shown once under a legend (ticks carry bare numbers, $ and % only). */
export function lensUnitCaption(unit: string, basis: LensBasis): string {
  const u = unit.trim(), lu = u.toLowerCase()
  if (basis === 'share') return '% of tile area'
  if (basis === 'density') {
    if (MONEY.test(u)) return `US$${prettyUnit(u.replace(MONEY, ''))} per km² of tile`
    if (PEOPLE_UNITS.has(lu)) return 'people per km² of tile'
    if (COUNT_UNITS.has(lu)) return 'per km² of tile'
    return `${prettyUnit(u)} per km² of tile`
  }
  if (LAND_DENSITY_UNITS.has(lu)) return 'people per km² of land'
  if (MONEY.test(u)) return `US$${prettyUnit(u.replace(MONEY, ''))}`
  if (PCT_UNITS.has(lu) || SHARE_UNITS.has(lu)) return '%'
  if (PEOPLE_UNITS.has(lu)) return 'people'
  // prettyUnit drops count-like codes (for "N per …" text); a legend caption keeps them
  if (lu === 'index' || lu === 'count') return lu
  return prettyUnit(u)
}

const money = (v: number) => (Math.abs(v) >= 1000 ? fmtUsd(v) : `$${fmtNumber(v)}`)

/** Short value for legend ticks: keeps $, % and degrees, drops other units (see lensUnitCaption). */
export function fmtLensTick(v: number, unit: string, basis: LensBasis): string {
  if (!Number.isFinite(v)) return '–'
  if (basis === 'share') return `${fmtNumber(v)}%`
  const lu = unit.trim().toLowerCase()
  if (MONEY.test(lu)) return money(v)
  if (PCT_UNITS.has(lu)) return `${fmtNumber(v)}%`
  if (SHARE_UNITS.has(lu)) return `${fmtNumber(v * 100)}%`
  if (lu === 'degc') return `${fmtNumber(v)}°C`
  if (lu === 'deg') return `${fmtNumber(v)}°`
  return fmtNumber(v)
}

const CAT_COLORS: Record<string, RGB> = {
  rel_buddhist_pct: [242, 169, 0], rel_hindu_pct: [233, 87, 45], rel_muslim_pct: [46, 160, 67],
  rel_christian_pct: [70, 120, 220], rel_catholic_pct: [70, 120, 220], rel_evangelical_pct: [150, 90, 210],
  rel_mainline_pct: [120, 180, 240], rel_black_protestant_pct: [0, 160, 160], rel_lds_pct: [230, 200, 60],
  rel_jewish_pct: [200, 220, 255], rel_orthodox_pct: [160, 110, 80], rel_other_pct: [150, 150, 150],
  rel_unclaimed_pct: [70, 70, 70],
  // ethnic groups: fixed per category, so 'Other' reads the same in both maps and no group of one
  // country borrows another country's colour by list position
  eth_sinhalese_pct: [242, 169, 0], eth_sl_tamil_pct: [229, 57, 53], eth_indian_tamil_pct: [214, 120, 200],
  eth_moor_pct: [76, 175, 80],
  eth_white_nh_pct: [91, 141, 239], eth_black_pct: [171, 71, 188], eth_hispanic_pct: [0, 172, 193],
  eth_asian_pct: [236, 64, 122], eth_native_pct: [176, 128, 64],
  eth_other_pct: [150, 150, 150],
  lc_tree: [0, 100, 0], lc_shrub: [255, 187, 34], lc_grass: [255, 255, 76], lc_crop: [240, 150, 255],
  lc_built: [250, 0, 0], lc_bare: [180, 180, 180], lc_snow: [240, 240, 240], lc_water: [0, 100, 200],
  lc_wetland: [0, 150, 160], lc_mangrove: [0, 207, 117], lc_moss: [250, 230, 160],
  cit_farmers_pct: [124, 194, 78], cit_workers_pct: [224, 128, 58], cit_merchants_pct: [240, 196, 58],
  cit_engineers_pct: [83, 179, 240], cit_scholars_pct: [120, 120, 240], cit_healers_pct: [240, 90, 110],
  cit_service_pct: [200, 160, 120], cit_administrators_pct: [190, 190, 190], cit_artists_pct: [192, 127, 240],
  cit_soldiers_pct: [160, 60, 60],
}

export function catColor(key: string, i: number): RGB {
  return CAT_COLORS[key] ?? CATEGORICAL[i % CATEGORICAL.length]
}

/**
 * Column heights of the terrain-style lenses (terrain, districts, dominant): mean elevation in metres,
 * from the metric matrix (all zero until metrics.bin arrives).
 */
export function reliefElevation(d: CountryData): Float32Array {
  const out = new Float32Array(d.n)
  const elev = column(d, 'elev_mean_m')
  if (elev) for (let i = 0; i < d.n; i++) out[i] = Math.max(0, elev[i] || 0)
  return out
}

export function terrainRGB(d: CountryData, idx: IndexJson, i: number): RGB {
  const t = idx.terrain_codes[d.hexes.terrain[i]] ?? 'plains'
  const f = idx.feature_codes[d.hexes.feature[i]] ?? ''
  let c = TERRAIN_META[t]?.rgb ?? [120, 120, 120]
  const fm = FEATURE_META[f]
  if (fm?.rgb) c = mix(c, fm.rgb, f === 'urban' ? 0.55 : 0.6)
  if (d.hexes.hills[i]) c = shade(c, 0.86)
  return c
}

/** Vintage + sources of the data behind a legend, per country. */
export interface Provenance { country: CountryId; vintage: string; sources: string[] }

/** Which countries a continuous legend's colour domain was built from. */
export interface ScaleInfo {
  /** Shared scale was requested (pool of more than one country) */
  shared: boolean
  /** countries whose values form the domain */
  pooled: CountryId[]
  /** requested countries whose statistics are still loading (excluded for now) */
  pending: CountryId[]
  /** requested countries that do not publish this metric */
  missing: CountryId[]
}

export type Legend =
  | { kind: 'categorical'; title: string; items: { label: string; rgb: RGB; count?: number }[]; prov?: Provenance[] }
  | {
    kind: 'continuous'; title: string; hint: string; palette: RGB[]; domain: [number, number]; log: boolean
    unit: string; basis: LensBasis; density: boolean; country: CountryId; scale: ScaleInfo; prov: Provenance[]
  }
  | { kind: 'yield'; title: string; yieldKey: YieldKey; cap: number }
  /**
   * the chosen metric is not published for this country (the map is dimmed, never another metric),
   * or (failed) this country's statistics could not be loaded
   */
  | { kind: 'nodata'; title: string; detail: string; country: CountryId; failed?: boolean }
  /** the metric matrix (metrics.bin) of this country has not arrived yet */
  | { kind: 'loading'; title: string; country: CountryId }

export interface LensResult {
  colors: Uint8Array
  elevation: Float32Array
  legend: Legend
}

export function lensMetricKeys(lens: LensDef, d: CountryData[]): string[] {
  if (!lens.metrics) return []
  return lens.metrics.filter((k) => d.some((c) => c.metricByKey.has(k)))
}

/** Countries (in COUNTRIES order) whose data publish a metric key. */
export function metricCoverage(key: string, countries: CountryData[]): CountryId[] {
  return COUNTRIES.filter((id) => countries.some((c) => c.id === id && c.metricByKey.has(key)))
}

/** Label of a metric from whichever country publishes it. */
export function metricLabel(key: string, countries: CountryData[]): string {
  for (const c of countries) { const m = c.metricByKey.get(key); if (m) return m.label }
  return key
}

/** ' (LK only)' / ' (US only)' for single-country metrics when both countries are loaded, else ''. */
export function coverageTag(key: string, countries: CountryData[]): string {
  if (new Set(countries.map((c) => c.id)).size < 2) return ''
  const cov = metricCoverage(key, countries)
  return cov.length === 1 ? ` (${cov[0] === 'lka' ? 'LK' : 'US'} only)` : ''
}

/** Menu options of a metric or dominant lens: every candidate key with its label and coverage tag. */
export function lensMetricOptions(lens: LensDef, countries: CountryData[]) {
  const keys = lens.id === 'custom'
    ? [...new Set(countries.flatMap((c) => c.meta.metrics.map((m) => m.key)))]
    : lensMetricKeys(lens, countries)
  return keys.map((key) => ({ key, label: metricLabel(key, countries), coverage: metricCoverage(key, countries), tag: coverageTag(key, countries) }))
}

/**
 * The metric a metric lens shows for country d: the chosen key (null when d does not publish it —
 * the map then shows "no data", never a different metric), else the lens default, which is the first
 * candidate published by any of `all` (the same default the lens menu shows).
 */
export function lensMetricKey(lens: LensDef, metricKey: string | null | undefined, d: CountryData, all: CountryData[] = [d]): string | null {
  const k = metricKey || lensMetricKeys(lens, all.length ? all : [d])[0]
  return k && d.metricByKey.has(k) ? k : null
}

function dominantKeys(lens: LensDef, d: CountryData) {
  const dm = lens.dominant!
  const keys = d.meta.metrics
    .map((m) => m.key)
    .filter((k) => k.startsWith(dm.prefix) && (!dm.suffix || k.endsWith(dm.suffix)) && !(dm.exclude ?? []).includes(k))
  // Aggregate categories (e.g. all-Christian): when ≥ 2 components are published use the
  // components (US traditions), otherwise keep the aggregate and drop the lone subset (LKA Catholics).
  const drop = new Set<string>()
  for (const [agg, comps] of Object.entries(dm.aggregates ?? {})) {
    if (!keys.includes(agg)) continue
    const present = comps.filter((c) => keys.includes(c))
    if (present.length >= 2) drop.add(agg)
    else present.forEach((c) => drop.add(c))
  }
  return keys.filter((k) => !drop.has(k))
}

/** Values used for colouring a metric lens: per km² of tile for extensive metrics, % of tile for areas. */
export function lensValues(d: CountryData, key: string): Float32Array | null {
  const m = d.metricByKey.get(key)
  const col = m ? column(d, key) : null
  if (!m || !col) return null
  const basis = lensBasis(m)
  if (basis === 'raw') return col
  const k = basis === 'share' ? 100 : 1
  const out = new Float32Array(d.n)
  for (let i = 0; i < d.n; i++) out[i] = (col[i] / d.hexArea[i]) * k
  return out
}

// ---------------------------------------------------------------------------------------------
// Colour domain: computed once per (lens, metric, pool of CountryData objects) and shared by both
// panes and the Explorer's lens colouring. A new CountryData object (e.g. when metrics.bin lands)
// gets a new id, so the cache refreshes by itself.
// ---------------------------------------------------------------------------------------------

interface DomainInfo { domain: [number, number]; log: boolean; pooled: CountryId[]; pending: CountryId[]; missing: CountryId[] }

const objIds = new WeakMap<object, number>()
let objSeq = 0
const objId = (o: object) => {
  let v = objIds.get(o)
  if (v == null) { v = ++objSeq; objIds.set(o, v) }
  return v
}
const domainCache = new Map<string, DomainInfo>()
const DOMAIN_CACHE_MAX = 48
const byCountryOrder = (a: CountryId, b: CountryId) => COUNTRIES.indexOf(a) - COUNTRIES.indexOf(b)

/** The strictly positive tail of an ascending-sorted array. */
function positivePart(sorted: Float64Array): Float64Array {
  let lo = 0, hi = sorted.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] > 0) hi = mid; else lo = mid + 1 }
  return sorted.subarray(lo)
}

/**
 * Should a skewed positive metric go on a log ramp? (only for lenses with autoLog): no negatives,
 * not a bounded unit (%, share, index…), and the union of the countries' positive 2-98% ranges
 * spans at least 20x (e.g. household income: ~$2.3k in Sri Lanka to ~$110k in the US).
 */
function autoLog(sortedBy: Float64Array[], unit: string): boolean {
  if (LINEAR_UNITS.has(unit.trim().toLowerCase()) || !sortedBy.length) return false
  let lo = Infinity, hi = -Infinity
  for (const sorted of sortedBy) {
    if (sorted[0] < 0) return false
    const pos = positivePart(sorted)
    if (pos.length < 10) continue
    lo = Math.min(lo, sortedQuantile(pos, 0.02)); hi = Math.max(hi, sortedQuantile(pos, 0.98))
  }
  return Number.isFinite(lo) && hi / lo >= 20
}

function lensDomain(lens: LensDef, key: string, poolIn: CountryData[], pendingIds: CountryId[]): DomainInfo {
  // order-independent key: both panes and the Explorer share one entry
  const pool = [...poolIn].sort((a, b) => byCountryOrder(a.id, b.id))
  const ck = `${lens.id}|${key}|${pool.map((c) => `${c.id}:${objId(c)}`).join(',')}|${[...pendingIds].sort(byCountryOrder).join(',')}`
  const hit = domainCache.get(ck)
  if (hit) return hit
  const pooled: CountryId[] = [], pending = [...pendingIds], missing: CountryId[] = []
  const parts: { c: CountryData; v: Float32Array; m: Metric }[] = []
  for (const c of pool) {
    const m = c.metricByKey.get(key)
    if (!m) { missing.push(c.id); continue }
    if (!c.matrixReady) { pending.push(c.id); continue }
    parts.push({ c, v: lensValues(c, key)!, m })
  }
  // Land tiles only (sea rings carry zeros for most metrics), except coastal-sea metrics. Each
  // country's values are sorted on their own: the shared domain is the union of the countries' own
  // robust (2-98%) ranges, so Sri Lanka's ~2k tiles are not clipped by the US's ~38k.
  const sortedBy: Float64Array[] = []
  for (const { c, v, m } of parts) {
    const coast = m.weight === 'coast', ring = c.hexes.ring
    const buf = new Float64Array(c.n)
    let k = 0
    for (let i = 0; i < c.n; i++) {
      const x = v[i]
      if (Number.isFinite(x) && (coast || ring[i] === 0)) buf[k++] = x
    }
    if (k) { pooled.push(c.id); sortedBy.push(buf.subarray(0, k).sort()) } else missing.push(c.id)
  }
  const m0 = parts[0]?.m
  const basis = m0 ? lensBasis(m0) : 'raw'
  const log = basis === 'density' || lens.log === true || (!!lens.autoLog && basis === 'raw' && !!m0 && autoLog(sortedBy, m0.unit))
  let lo = Infinity, hi = -Infinity
  for (const sorted of sortedBy) {
    const vals = log ? positivePart(sorted) : sorted
    if (!vals.length) continue
    const [a, b] = sortedDomain(vals, log)
    lo = Math.min(lo, a); hi = Math.max(hi, b)
  }
  const domain: [number, number] = Number.isFinite(lo) ? [lo, hi] : [0, 1]
  const info: DomainInfo = {
    domain, log,
    pooled: pooled.sort(byCountryOrder), pending: [...new Set(pending)].sort(byCountryOrder), missing: missing.sort(byCountryOrder),
  }
  if (domainCache.size >= DOMAIN_CACHE_MAX) domainCache.delete(domainCache.keys().next().value!)
  domainCache.set(ck, info)
  return info
}

function lensTitle(m: Metric, basis: LensBasis): { title: string; hint: string } {
  if (basis === 'density') {
    // the unit caption ("… per km² of tile") is shown next to the title
    return {
      title: m.label,
      hint: `${m.label}, divided by each tile’s total area (per km² of tile), the basis the yields use. Tiles are ~36 km² in Sri Lanka and ~253 km² in the US.`,
    }
  }
  if (basis === 'share') return { title: m.label, hint: `${m.label} as a share of each tile’s total area.` }
  if (LAND_DENSITY_UNITS.has(m.unit.trim().toLowerCase())) {
    return { title: m.label, hint: 'Residents per km² of land in the tile (water excluded).' }
  }
  return { title: m.label, hint: m.description || m.label }
}

const provOf = (c: CountryData, keys: string[]): Provenance => {
  const ms = keys.map((k) => c.metricByKey.get(k)).filter(Boolean) as Metric[]
  return {
    country: c.id,
    vintage: [...new Set(ms.map((m) => m.vintage).filter(Boolean))].join(' / '),
    sources: [...new Set(ms.flatMap((m) => m.sources))],
  }
}

export interface LensContext {
  /** Shared scale requested (defaults to pool.length > 1) */
  shared?: boolean
  /** countries expected in the shared pool whose data has not loaded at all yet */
  pending?: CountryId[]
  /** every loaded country (labels and the default metric); defaults to the pool */
  all?: CountryData[]
  /** countries whose data failed to load (never "loading"; left out of the shared scale) */
  failed?: CountryId[]
}

export function computeLens(
  lens: LensDef, metricKey: string | null, d: CountryData, idx: IndexJson, pool: CountryData[], ctx: LensContext = {},
): LensResult {
  const n = d.n
  const colors = new Uint8Array(n * 4)
  // yield and metric lenses overwrite these heights below
  const elevation = reliefElevation(d)
  const set = (i: number, c: RGB, a = 255) => {
    colors[i * 4] = c[0]; colors[i * 4 + 1] = c[1]; colors[i * 4 + 2] = c[2]; colors[i * 4 + 3] = a
  }
  const terrainAll = () => { for (let i = 0; i < n; i++) set(i, terrainRGB(d, idx, i)) }
  const dimTerrain = (i: number) => set(i, shade(terrainRGB(d, idx, i), 0.32))
  const dimAll = () => { for (let i = 0; i < n; i++) dimTerrain(i) }
  const name = COUNTRY_META[d.id].name

  if (lens.kind === 'terrain') {
    terrainAll()
    const counts: Record<string, number> = {}
    d.hexes.terrain.forEach((t) => { const k = idx.terrain_codes[t]; counts[k] = (counts[k] ?? 0) + 1 })
    return {
      colors, elevation,
      legend: { kind: 'categorical', title: 'Terrain', items: Object.keys(TERRAIN_META).filter((k) => counts[k]).map((k) => ({ label: TERRAIN_META[k].label, rgb: TERRAIN_META[k].rgb, count: counts[k] as number | undefined })).concat(
        Object.entries(FEATURE_META).filter(([k, v]) => k && v.rgb && d.hexes.feature.some((f) => idx.feature_codes[f] === k)).map(([, v]) => ({ label: v.label, rgb: v.rgb!, count: undefined })),
      ) },
    }
  }

  if (lens.kind === 'district') {
    const codes = idx.districts.map((x) => x.code)
    const counts: Record<string, number> = {}
    for (let i = 0; i < n; i++) {
      const code = codes[d.hexes.district[i]] ?? ''
      if (code) { set(i, DISTRICT_META[code]?.rgb ?? [200, 200, 200]); counts[code] = (counts[code] ?? 0) + 1 } else dimTerrain(i)
    }
    return {
      colors, elevation,
      legend: { kind: 'categorical', title: 'Specialist districts', items: codes.filter((c) => counts[c]).map((c) => ({ label: `${DISTRICT_META[c]?.icon ?? ''} ${DISTRICT_META[c]?.label ?? c}`, rgb: DISTRICT_META[c]?.rgb ?? [200, 200, 200], count: counts[c] })) },
    }
  }

  if (lens.kind === 'yield') {
    const y = lens.yield!
    const arr = d.hexes.yields[y]
    const cap = idx.yield_scale.cap
    for (let i = 0; i < n; i++) {
      if (arr[i] <= 0) dimTerrain(i)
      else set(i, yieldColor(YIELD_META[y].rgb, arr[i], cap))
      elevation[i] = arr[i] * 1500
    }
    return { colors, elevation, legend: { kind: 'yield', title: `${YIELD_META[y].label} yield per tile`, yieldKey: y, cap } }
  }

  if (lens.kind === 'dominant') {
    const keys = dominantKeys(lens, d)
    if (!keys.length) {
      dimAll()
      return { colors, elevation, legend: { kind: 'nodata', title: lens.label, detail: `Not published for ${name}`, country: d.id } }
    }
    if (!d.matrixReady) {
      dimAll()
      if (ctx.failed?.includes(d.id)) return { colors, elevation, legend: { kind: 'nodata', title: lens.label, detail: `${name} statistics failed to load`, country: d.id, failed: true } }
      return { colors, elevation, legend: { kind: 'loading', title: lens.label, country: d.id } }
    }
    const cols = keys.map((k) => column(d, k)!)
    let nat: number[] | null = null
    if (lens.dominant?.byLQ) {
      // national share of each class (pop-weighted mean of tile shares ≈ national share)
      const emp = column(d, 'employed')
      nat = cols.map((c) => {
        let num = 0, den = 0
        for (let i = 0; i < n; i++) { const e = emp ? emp[i] : 1; if (Number.isFinite(c[i]) && e > 0) { num += c[i] * e; den += e } }
        return den ? num / den : NaN
      })
    }
    const counts = new Array(keys.length).fill(0)
    const isFrac = lens.dominant?.prefix === 'lc_'
    for (let i = 0; i < n; i++) {
      let best = -1, bv = -Infinity, share = 0
      for (let k = 0; k < cols.length; k++) {
        const v = cols[k][i]
        if (!Number.isFinite(v)) continue
        const score = nat ? v / (nat[k] || 1) : v
        if (score > bv) { bv = score; best = k; share = isFrac ? v : v / 100 }
      }
      if (best < 0 || (d.hexes.ring[i] > 0 && !isFrac)) { dimTerrain(i); continue }
      counts[best]++
      const c = catColor(keys[best], best)
      set(i, nat ? c : mix([60, 64, 70], c, 0.35 + 0.65 * Math.min(1, share * 1.4)))
    }
    const label = (k: string) => {
      if (k.startsWith('cit_')) return `${CITIZEN_META[k.replace(/_pct$/, '')]?.icon ?? ''} ${CITIZEN_META[k.replace(/_pct$/, '')]?.label ?? k}`
      return (d.metricByKey.get(k)?.label ?? k).replace(/\s*\((share|%)( of [^)]*)?\)|\s*share/i, '')
    }
    return {
      colors, elevation,
      legend: {
        kind: 'categorical', title: lens.label,
        items: keys.map((k, j) => ({ label: label(k), rgb: catColor(k, j), count: counts[j] })).filter((x) => x.count),
        prov: [provOf(d, keys)],
      },
    }
  }

  // ---- metric lens
  const all = ctx.all?.length ? ctx.all : pool
  const wanted = metricKey || lensMetricKeys(lens, all.length ? all : [d])[0] || null
  const key = lensMetricKey(lens, metricKey, d, all)
  if (!key) {
    // never fall back to another metric: that would put two different metrics under one legend
    dimAll()
    const cov = wanted ? metricCoverage(wanted, all) : []
    const only = cov.length ? ` — ${cov.map((c) => COUNTRY_META[c].name).join(' and ')} only` : ''
    return {
      colors, elevation,
      legend: { kind: 'nodata', title: wanted ? metricLabel(wanted, all) : lens.label, detail: `Not published for ${name}${only}`, country: d.id },
    }
  }
  const m = d.metricByKey.get(key)!
  const failed = new Set(ctx.failed ?? [])
  if (!d.matrixReady) {
    dimAll()
    if (failed.has(d.id)) return { colors, elevation, legend: { kind: 'nodata', title: m.label, detail: `${name} statistics failed to load`, country: d.id, failed: true } }
    return { colors, elevation, legend: { kind: 'loading', title: m.label, country: d.id } }
  }
  const shared = ctx.shared ?? pool.length > 1
  const domPool = shared ? [d, ...pool.filter((c) => c.id !== d.id && !(failed.has(c.id) && !c.matrixReady))] : [d]
  const info = lensDomain(lens, key, domPool, shared ? (ctx.pending ?? []).filter((c) => c !== d.id) : [])
  if (!info.pooled.includes(d.id)) {
    dimAll()
    return { colors, elevation, legend: { kind: 'nodata', title: m.label, detail: `No values for ${name}`, country: d.id } }
  }
  const { domain, log } = info
  const vals = lensValues(d, key)!
  const pal = PALETTES[lens.palette ?? 'viridis']
  const landOnly = landOnlyMetric(m), ring = d.hexes.ring
  for (let i = 0; i < n; i++) {
    const v = vals[i]
    // sea tiles: never painted with a land statistic (see landOnlyMetric), whatever the export holds
    if (ring[i] > 0 && landOnly) { dimTerrain(i); continue }
    if (!Number.isFinite(v)) { set(i, shade(terrainRGB(d, idx, i), 0.22)); continue }
    if (log && v <= 0) { dimTerrain(i); continue }
    if (ring[i] > 0 && m.weight !== 'coast' && v === 0) { dimTerrain(i); continue }
    const t = scaleT(v, domain, log)
    set(i, ramp(pal, t))
    elevation[i] = Math.max(0, Math.min(1, t)) * 60000
  }
  const basis = lensBasis(m)
  const { title, hint } = lensTitle(m, basis)
  const prov = [provOf(d, [key]), ...domPool.filter((c) => c.id !== d.id && info.pooled.includes(c.id)).map((c) => provOf(c, [key]))]
  return {
    colors, elevation,
    legend: {
      kind: 'continuous', title, hint, palette: pal, domain, log, unit: m.unit, basis, density: basis === 'density', country: d.id,
      scale: { shared, pooled: info.pooled, pending: info.pending, missing: info.missing }, prov,
    },
  }
}
