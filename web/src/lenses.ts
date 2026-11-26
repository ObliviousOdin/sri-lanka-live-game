import { CITIZEN_META, DISTRICT_META, FEATURE_META, TERRAIN_META, YIELD_META, type RGB } from './civ'
import { CATEGORICAL, PALETTES, mix, ramp, robustDomain, scaleT, shade, yieldColor } from './color'
import { column } from './data/load'
import type { CountryData, IndexJson, YieldKey } from './types'

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
  log?: boolean
  description: string
  pointLayers?: string[]
}

export const LENSES: LensDef[] = [
  { id: 'terrain', label: 'Terrain', group: 'Map', icon: '⛰️', kind: 'terrain', description: 'Base terrain from ESA WorldCover land cover, Copernicus DEM relief and WorldClim climate.' },
  { id: 'districts', label: 'Districts', group: 'Map', icon: '🏛️', kind: 'district', description: 'Specialist districts assigned by published-data rules (see each district’s rule).' },
  { id: 'gold', label: 'Gold', group: 'Yields', icon: '◆', kind: 'yield', yield: 'gold', description: YIELD_META.gold.blurb },
  { id: 'production', label: 'Production', group: 'Yields', icon: '⚒', kind: 'yield', yield: 'production', description: YIELD_META.production.blurb },
  { id: 'food', label: 'Food', group: 'Yields', icon: '🌾', kind: 'yield', yield: 'food', description: YIELD_META.food.blurb },
  { id: 'science', label: 'Science', group: 'Yields', icon: '⚗', kind: 'yield', yield: 'science', description: YIELD_META.science.blurb },
  { id: 'culture', label: 'Culture', group: 'Yields', icon: '✦', kind: 'yield', yield: 'culture', description: YIELD_META.culture.blurb },
  { id: 'faith', label: 'Faith', group: 'Yields', icon: '☼', kind: 'yield', yield: 'faith', description: YIELD_META.faith.blurb },
  { id: 'population', label: 'Population density', group: 'People', icon: '👥', kind: 'metric', metrics: ['pop_density', 'pop'], palette: 'magma', log: true, description: 'Residents per km² (WorldPop distribution scaled to official 2023 totals).' },
  { id: 'income', label: 'Income', group: 'Economy', icon: '💵', kind: 'metric', metrics: ['hh_income_usd', 'gdp_per_capita_usd', 'poverty_rate', 'rwi'], palette: 'viridis', log: false, description: 'Household income, GDP per capita, poverty and (Sri Lanka) the Relative Wealth Index.' },
  { id: 'education', label: 'Education', group: 'People', icon: '🎓', kind: 'metric', metrics: ['pct_bachelors', 'pct_secondary', 'literacy_rate', 'schools'], palette: 'blues', log: false, description: 'Educational attainment of adults and schools.' },
  { id: 'religion', label: 'Religion', group: 'People', icon: '🪔', kind: 'dominant', dominant: { prefix: 'rel_', suffix: '_pct', exclude: ['rel_unclaimed_pct'], aggregates: { rel_christian_pct: ['rel_evangelical_pct', 'rel_catholic_pct', 'rel_mainline_pct', 'rel_black_protestant_pct', 'rel_orthodox_pct', 'rel_lds_pct'] } }, metrics: ['religious_diversity'], description: 'Largest religious tradition (shade = its share). Sri Lanka: Census; US: 2020 U.S. Religion Census adherents.' },
  { id: 'ethnicity', label: 'Ethnicity', group: 'People', icon: '🧑‍🤝‍🧑', kind: 'dominant', dominant: { prefix: 'eth_', suffix: '_pct' }, metrics: ['ethnic_diversity'], description: 'Largest ethnic group (shade = its share). Sri Lanka: Census; US: ACS race & Hispanic origin.' },
  { id: 'workforce', label: 'Workforce', group: 'People', icon: '👷', kind: 'dominant', dominant: { prefix: 'cit_', suffix: '_pct', byLQ: true }, description: 'Most over-represented citizen class (highest location quotient vs the national mix).' },
  { id: 'disasters', label: 'Disasters', group: 'Environment', icon: '🌪️', kind: 'metric', metrics: ['hazard_index', 'cyclone_tracks', 'quakes_m5', 'tsunami_runups', 'nri_risk_score', 'nri_eal_usd', 'disaster_deaths_per_100k', 'flood_events', 'landslide_events'], palette: 'hazard', log: false, description: 'Tropical cyclones (IBTrACS), earthquakes (USGS), tsunami run-ups (NOAA) and national loss databases.' },
  { id: 'energy', label: 'Energy', group: 'Infrastructure', icon: '⚡', kind: 'metric', metrics: ['power_capacity_mw', 'generation_gwh', 'renewable_capacity_mw'], palette: 'inferno', log: true, description: 'Installed generating capacity and generation per km².', pointLayers: ['power_plants'] },
  { id: 'trade', label: 'Trade', group: 'Infrastructure', icon: '⚓', kind: 'metric', metrics: ['port_cargo_t', 'airport_pax', 'emp_transport', 'emp_trade'], palette: 'blues', log: true, description: 'Port cargo, airport passengers and trade/transport employment per km².', pointLayers: ['ports', 'airports'] },
  { id: 'landcover', label: 'Land cover', group: 'Environment', icon: '🌳', kind: 'dominant', dominant: { prefix: 'lc_' }, metrics: ['lc_tree', 'lc_crop', 'lc_built', 'protected_pct'], description: 'Dominant ESA WorldCover 2021 class.' },
  { id: 'elevation', label: 'Elevation', group: 'Environment', icon: '🏔️', kind: 'metric', metrics: ['elev_mean_m', 'relief_m', 'slope_mean_deg', 'temp_mean_c', 'precip_mm'], palette: 'terrain', log: false, description: 'Copernicus DEM GLO-90 elevation and relief; WorldClim climate.' },
  { id: 'custom', label: 'Any metric', group: 'Economy', icon: '📊', kind: 'metric', metrics: [], palette: 'viridis', log: false, description: 'Any metric chosen in the Explorer.' },
]

export const LENS_BY_ID = new Map(LENSES.map((l) => [l.id, l]))

/** Metric keys that are colored as densities (extensive metrics are divided by tile area). */
export function isDensity(d: CountryData, key: string) {
  const m = d.metricByKey.get(key)
  return !!m && m.kind === 'extensive' && key !== 'pop'
}

const CAT_COLORS: Record<string, RGB> = {
  rel_buddhist_pct: [242, 169, 0], rel_hindu_pct: [233, 87, 45], rel_muslim_pct: [46, 160, 67],
  rel_christian_pct: [70, 120, 220], rel_catholic_pct: [70, 120, 220], rel_evangelical_pct: [150, 90, 210],
  rel_mainline_pct: [120, 180, 240], rel_black_protestant_pct: [0, 160, 160], rel_lds_pct: [230, 200, 60],
  rel_jewish_pct: [200, 220, 255], rel_orthodox_pct: [160, 110, 80], rel_other_pct: [150, 150, 150],
  rel_unclaimed_pct: [70, 70, 70],
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

export function terrainRGB(d: CountryData, idx: IndexJson, i: number): RGB {
  const t = idx.terrain_codes[d.hexes.terrain[i]] ?? 'plains'
  const f = idx.feature_codes[d.hexes.feature[i]] ?? ''
  let c = TERRAIN_META[t]?.rgb ?? [120, 120, 120]
  const fm = FEATURE_META[f]
  if (fm?.rgb) c = mix(c, fm.rgb, f === 'urban' ? 0.55 : 0.6)
  if (d.hexes.hills[i]) c = shade(c, 0.86)
  return c
}

export type Legend =
  | { kind: 'categorical'; title: string; items: { label: string; rgb: RGB; count?: number }[] }
  | { kind: 'continuous'; title: string; palette: RGB[]; domain: [number, number]; log: boolean; unit: string; density: boolean }
  | { kind: 'yield'; title: string; yieldKey: YieldKey; cap: number }

export interface LensResult {
  colors: Uint8Array
  elevation: Float32Array
  legend: Legend
}

export function lensMetricKeys(lens: LensDef, d: CountryData[]): string[] {
  if (!lens.metrics) return []
  return lens.metrics.filter((k) => d.some((c) => c.metricByKey.has(k)))
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

/** Values used for colouring a metric lens (densities for extensive metrics). */
export function lensValues(d: CountryData, key: string): Float32Array | null {
  const col = column(d, key)
  if (!col) return null
  if (!isDensity(d, key)) return col
  const out = new Float32Array(d.n)
  for (let i = 0; i < d.n; i++) out[i] = col[i] / d.hexArea[i]
  return out
}

export function computeLens(
  lens: LensDef, metricKey: string | null, d: CountryData, idx: IndexJson, pool: CountryData[],
): LensResult {
  const n = d.n
  const colors = new Uint8Array(n * 4)
  const elevation = new Float32Array(n)
  const elev = column(d, 'elev_mean_m')
  const set = (i: number, c: RGB, a = 255) => {
    colors[i * 4] = c[0]; colors[i * 4 + 1] = c[1]; colors[i * 4 + 2] = c[2]; colors[i * 4 + 3] = a
  }
  const terrainAll = () => { for (let i = 0; i < n; i++) set(i, terrainRGB(d, idx, i)) }
  const dimTerrain = (i: number) => set(i, shade(terrainRGB(d, idx, i), 0.32))
  if (elev) for (let i = 0; i < n; i++) elevation[i] = Math.max(0, elev[i] || 0)

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
      legend: { kind: 'categorical', title: 'Districts', items: codes.filter((c) => counts[c]).map((c) => ({ label: `${DISTRICT_META[c]?.icon ?? ''} ${DISTRICT_META[c]?.label ?? c}`, rgb: DISTRICT_META[c]?.rgb ?? [200, 200, 200], count: counts[c] })) },
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
      return (d.metricByKey.get(k)?.label ?? k).replace(/\s*\(share\)|\s*share|\s*\(%\)/i, '')
    }
    return {
      colors, elevation,
      legend: { kind: 'categorical', title: lens.label, items: keys.map((k, j) => ({ label: label(k), rgb: catColor(k, j), count: counts[j] })).filter((x) => x.count) },
    }
  }

  // metric lens
  const key = metricKey && d.metricByKey.has(metricKey) ? metricKey : lensMetricKeys(lens, [d])[0]
  const vals = key ? lensValues(d, key) : null
  if (!key || !vals) {
    for (let i = 0; i < n; i++) dimTerrain(i)
    return { colors, elevation, legend: { kind: 'categorical', title: `${lens.label}: no data for this country`, items: [] } }
  }
  const m = d.metricByKey.get(key)!
  const log = lens.log ?? false
  const useLog = log || (m.kind === 'extensive')
  const arrays = pool.map((c) => lensValues(c, key)).filter(Boolean) as Float32Array[]
  const landOnly = (c: CountryData, a: Float32Array) => {
    const out = new Float32Array(c.n)
    for (let i = 0; i < c.n; i++) out[i] = c.hexes.ring[i] === 0 || m.weight === 'coast' ? a[i] : NaN
    return out
  }
  const domain = robustDomain(arrays.map((a, j) => landOnly(pool[j], a)), useLog)
  const pal = PALETTES[lens.palette ?? 'viridis']
  for (let i = 0; i < n; i++) {
    const v = vals[i]
    if (!Number.isFinite(v)) { set(i, shade(terrainRGB(d, idx, i), 0.22)); continue }
    if (useLog && v <= 0) { dimTerrain(i); continue }
    if (d.hexes.ring[i] > 0 && m.weight !== 'coast' && v === 0) { dimTerrain(i); continue }
    const t = scaleT(v, domain, useLog)
    set(i, ramp(pal, t))
    elevation[i] = Math.max(0, Math.min(1, t)) * 60000
  }
  return {
    colors, elevation,
    legend: { kind: 'continuous', title: m.label, palette: pal, domain, log: useLog, unit: m.unit, density: isDensity(d, key) },
  }
}
