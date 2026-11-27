// Data contract types — mirror pipeline/civmap/export.py (docs/PLAN.md §5)

export type CountryId = 'lka' | 'usa'
export const COUNTRIES: CountryId[] = ['lka', 'usa']
export const YIELDS = ['gold', 'production', 'food', 'science', 'culture', 'faith'] as const
export type YieldKey = (typeof YIELDS)[number]

export interface Metric {
  key: string
  label: string
  unit: string
  category: string
  kind: 'extensive' | 'intensive'
  weight: string
  table?: string
  sources: string[]
  vintage: string
  description: string
  method: string
  better: 'higher' | 'lower' | 'neutral'
  domain: string
  countries?: string[]
  col: number
  stats: { n: number; min?: number; max?: number; p05?: number; p50?: number; p95?: number; total?: number }
}

export interface Meta {
  country: CountryId
  name: string
  iso3: string
  h3_res: number
  hex_count: number
  land_hex_count: number
  avg_hex_km2: number
  bbox: [number, number, number, number]
  center: [number, number]
  admin_levels: Record<string, { label: string; count: number }>
  metrics: Metric[]
  totals: Record<string, number>
  yield_totals: Record<YieldKey, number>
  terrain_counts: Record<string, number>
  district_counts: Record<string, number>
  /** one row per wonder category: the ranking rule and whether this country has data to rank it */
  wonder_categories?: WonderCategory[]
}

/** meta.json `wonder_categories` (pipeline/civmap/civ.py): rule, coverage and provenance of a wonder category. */
export interface WonderCategory {
  category: string
  rule: string
  unit: string
  metric: string | null
  metric_vintage: string | null
  /** top-N kept per country; null = every qualifying site (World Heritage) */
  cap: number | null
  candidates: number
  with_value: number
  ranked: number
  /** 'ranked', or 'no_data' when no candidate has a value (missing data, not a zero) */
  status: 'ranked' | 'no_data' | string
  note: string | null
  sources: string[]
}

export interface HexesJson {
  h3: string[]
  ring: number[]
  terrain: number[]
  feature: number[]
  hills: number[]
  district: number[]
  districts_all: string[]
  yields: Record<YieldKey, number[]>
  adm1: number[]
  adm2: number[]
  adm3?: number[]
  /** sparse per-tile yield-bonus inputs: tile indices `i` with values `v` */
  bonus?: Partial<Record<'works_5y' | 'capacity_mw' | 'whs' | 'museums' | 'holy_sites' | 'park_visitors' | 'military_sites' | 'port_cargo_t' | 'airport_pax', { i: number[]; v: number[] }>>
}

export interface AdminUnit {
  id: string
  name: string
  parent: string
  lat: number
  lon: number
  abbr?: string
}

export interface AdminTable {
  level: string
  label: string
  units: AdminUnit[]
  metrics: Record<string, (number | null)[]>
}

export interface PointRow {
  id: string
  name: string
  lat: number
  lon: number
  kind: string
  h3: string | null
  [k: string]: unknown
}

export type PointLayers = Record<string, { domain: string; rows: PointRow[]; total: number }>

export interface Wonder {
  category: string
  rank: number
  name: string
  lat: number
  lon: number
  h3: string
  kind: string
  rule: string
  value: number | null
  unit: string
  domain: string
  sources: string[]
  [k: string]: unknown
}

export interface CityTile {
  h3: string
  d: number
  terrain: string
  feature: string
  hills: boolean
  district: string
  yields: number[] // order = YIELDS
  pop: number
}

export interface City {
  id: string
  name: string
  lat: number
  lon: number
  centre: string
  radius: number
  tiles: CityTile[]
  pop: number
  gdp_usd: number | null
  gdp_per_capita: number | null
  official_pop?: number | null
  official_pop_year?: number | null
  official_gdp_usd?: number | null
  official_gdp_year?: number | null
  admin_ids?: string[] | null
  wikidata?: string | null
  size: number
  citizens: Record<string, number>
  citizen_icons: Record<string, number>
  employed?: number | null
  yields: Record<YieldKey, number>
  industries: { key: string; jobs: number; lq: number }[]
  wonders: string[]
  districts: string[]
  /** ring tiles that went to a nearer City View centre (rings are de-duplicated in civ.py) */
  overlap?: { id: string; name: string; tiles: number; pop: number }[]
  /** tiles of the full radius-`radius` ring on the grid, before de-duplication (`tiles` holds this city's share) */
  ring_tiles?: number
  /** what `official_pop` covers, e.g. 'metro area (Census MSA)' / 'city proper (Wikidata)' */
  population_scope?: string
  population_note?: string
  population_sources?: string[]
  /** Sri Lanka: Census 2024 count of the DS division holding the centre (a like-for-like official figure) */
  census_ds_population?: number | null
  census_ds_urban_population?: number | null
  census_ds_id?: string | null
  census_ds_name?: string | null
  census_year?: number | null
  census_scope?: string | null
  census_sources?: string[] | null
}

export interface Source {
  id: string
  title: string
  publisher: string
  url: string
  license: string
  vintage: string
  countries: string[]
  notes: string
  metrics: Partial<Record<CountryId, string[]>>
  downloads: { url: string; bytes: number; fetched_at: string; sha256: string }[]
  accessed: string | null
}

export interface DistrictDef {
  code: string
  label: string
  rule: string
  /** optional: ranked-site cut-off per country when the rule names one (Holy Site: top N listed sites by Wikipedia editions) */
  top?: Partial<Record<CountryId, number>>
}

export interface IndexJson {
  built_at: string
  countries: { id: CountryId; name: string; h3_res: number; admin_levels: Record<string, string> }[]
  yields: YieldKey[]
  yield_scale: {
    cap: number
    bases: Record<string, { metric: string; base: number; unit: string }>
    power_bonus: [number, number][]
    research_bonus: [number, number][]
    culture_bonus?: { per_whs: number; max_whs: number; museums_min: number; museums_bonus: number; park_visitors_min: number; park_bonus: number }
    faith_bonus?: { per_holy_site: number; max_holy_sites: number }
  }
  terrain: Record<string, string>
  features: Record<string, string>
  districts: DistrictDef[]
  terrain_codes: string[]
  feature_codes: string[]
}

export interface NationalSeries {
  value: number | null
  year: number | null
  series?: [number, number][]
}

export interface NationalIndicator {
  code: string
  label: string
  name?: string
  description?: string
  method?: string
  source_org?: string
  derived?: boolean
  yield?: string
  better?: 'higher' | 'lower' | 'neutral' | string
  common?: { year: number | null; LKA: number | null; USA: number | null }
  unit: string
  advisor: 'economic' | 'military' | 'cultural' | 'scientific' | 'environmental' | string
  LKA?: NationalSeries
  USA?: NationalSeries
  source: string
}

/** Everything loaded for one country. */
export interface CountryData {
  id: CountryId
  meta: Meta
  hexes: HexesJson
  /** column-major Float32 matrix: value(hex i, metric col c) = matrix[c * n + i] */
  matrix: Float32Array
  /** false until metrics.bin has loaded (values are NaN before that) */
  matrixReady: boolean
  n: number
  hexIndex: Map<string, number>
  hexArea: Float32Array
  metricByKey: Map<string, Metric>
  points: PointLayers
  wonders: Wonder[]
  cities: City[]
  admin: Record<string, AdminTable>
}

export type SelectionKind = 'hex' | 'admin' | 'city' | 'wonder'
export interface Selection {
  country: CountryId
  kind: SelectionKind
  /** hex: h3 id; admin: `${level}:${id}`; city: city id; wonder: name */
  id: string
}
