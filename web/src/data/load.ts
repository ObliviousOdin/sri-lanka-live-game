import { cellArea } from 'h3-js'
import type {
  AdminTable, City, CountryData, CountryId, HexesJson, IndexJson, Meta, Metric, NationalIndicator,
  PointLayers, Source, Wonder,
} from '../types'

const BASE = `${import.meta.env.BASE_URL}data`

async function json<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}/${path}`)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.json() as Promise<T>
}

async function binary(path: string): Promise<ArrayBuffer> {
  const r = await fetch(`${BASE}/${path}`)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.arrayBuffer()
}

const countryCache = new Map<CountryId, Promise<CountryData>>()

export function loadCountry(id: CountryId): Promise<CountryData> {
  let p = countryCache.get(id)
  if (!p) {
    p = (async () => {
      const [meta, hexes, buf, points, wonders, cities] = await Promise.all([
        json<Meta>(`${id}/meta.json`),
        json<HexesJson>(`${id}/hexes.json`),
        binary(`${id}/metrics.bin`),
        json<PointLayers>(`${id}/points.json`).catch(() => ({}) as PointLayers),
        json<Wonder[]>(`${id}/wonders.json`).catch(() => []),
        json<City[]>(`${id}/cities.json`).catch(() => []),
      ])
      const n = hexes.h3.length
      const admin: Record<string, AdminTable> = {}
      await Promise.all(
        Object.keys(meta.admin_levels).map(async (lvl) => {
          admin[lvl] = await json<AdminTable>(`${id}/admin_${lvl}.json`)
        }),
      )
      const hexIndex = new Map<string, number>()
      hexes.h3.forEach((h, i) => hexIndex.set(h, i))
      const hexArea = new Float32Array(n)
      for (let i = 0; i < n; i++) hexArea[i] = cellArea(hexes.h3[i], 'km2')
      const metricByKey = new Map<string, Metric>()
      meta.metrics.forEach((m) => metricByKey.set(m.key, m))
      return {
        id, meta, hexes, matrix: new Float32Array(buf), n, hexIndex, hexArea, metricByKey,
        points, wonders, cities, admin,
      }
    })()
    countryCache.set(id, p)
  }
  return p
}

let indexP: Promise<IndexJson> | null = null
export const loadIndex = () => (indexP ??= json<IndexJson>('index.json'))

let sourcesP: Promise<Source[]> | null = null
export const loadSources = () => (sourcesP ??= json<Source[]>('sources.json'))

let nationalP: Promise<NationalIndicator[]> | null = null
export const loadNational = () => (nationalP ??= json<NationalIndicator[]>('national.json').catch(() => []))

const geoCache = new Map<string, Promise<GeoJSON.FeatureCollection>>()
export function loadAdminGeo(country: CountryId, level: string) {
  const k = `${country}/${level}`
  let p = geoCache.get(k)
  if (!p) {
    p = json<GeoJSON.FeatureCollection>(`${country}/admin_${level}.geojson`)
    geoCache.set(k, p)
  }
  return p
}

// ---------------------------------------------------------------------------------------------
// Accessors
// ---------------------------------------------------------------------------------------------

/** Metric value for hex i (NaN when missing or metric unknown). */
export function value(d: CountryData, key: string, i: number): number {
  const m = d.metricByKey.get(key)
  if (!m) return NaN
  return d.matrix[m.col * d.n + i]
}

/** Whole column as a Float32Array view (no copy). */
export function column(d: CountryData, key: string): Float32Array | null {
  const m = d.metricByKey.get(key)
  if (!m) return null
  return d.matrix.subarray(m.col * d.n, (m.col + 1) * d.n)
}

export function hasMetric(d: CountryData, key: string) {
  return d.metricByKey.has(key)
}

/** Admin metric value for a unit. */
export function adminValue(d: CountryData, level: string, unitIndex: number, key: string): number {
  const v = d.admin[level]?.metrics[key]?.[unitIndex]
  return v == null ? NaN : v
}

/** Finest admin level for a country (adm3 for LKA, adm2 for USA). */
export function finestLevel(d: CountryData): string {
  const lv = Object.keys(d.meta.admin_levels)
  return lv[lv.length - 1]
}

/** Display level used for "district/county" selection. */
export function mainLevel(d: CountryData): string {
  return 'adm2'
}

export function adminUnitOfHex(d: CountryData, level: string, i: number) {
  const idx = (d.hexes as unknown as Record<string, number[]>)[level]?.[i] ?? -1
  return idx >= 0 ? { index: idx, unit: d.admin[level].units[idx] } : null
}

/** Indices of all hexes whose dominant unit at `level` is `unitIndex`. */
export function hexesOfAdmin(d: CountryData, level: string, unitIndex: number): number[] {
  const arr = (d.hexes as unknown as Record<string, number[]>)[level]
  const out: number[] = []
  if (!arr) return out
  for (let i = 0; i < arr.length; i++) if (arr[i] === unitIndex && d.hexes.ring[i] === 0) out.push(i)
  return out
}
