import { cellArea } from 'h3-js'
import type {
  AdminTable, City, CountryData, CountryId, HexesJson, IndexJson, Meta, Metric, NationalIndicator,
  PointLayers, Source, Wonder,
} from '../types'

const BASE = `${import.meta.env.BASE_URL}data`

// Every data file is fetched as `?v=<index.built_at>`: index.json is written last by the exporter
// and always revalidated, so a re-export (or a stale browser cache) can never mix files from two
// exports — e.g. a cached metrics.bin with a newer meta.json, which garbles every column.
let dataVersion = ''
const url = (path: string) => `${BASE}/${path}${dataVersion ? `?v=${encodeURIComponent(dataVersion)}` : ''}`

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url(path), init)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.json() as Promise<T>
}

// ---------------------------------------------------------------------------------------------
// metrics.bin download progress (for the "Loading statistics… N MB" legend)
// ---------------------------------------------------------------------------------------------

export interface MatrixProgress { loaded: number; total: number; done: boolean }
const progress = new Map<CountryId, MatrixProgress>()
const progressListeners = new Set<() => void>()
const emitProgress = () => progressListeners.forEach((f) => f())
export const matrixProgress = (id: CountryId) => progress.get(id)
export function onMatrixProgress(f: () => void) {
  progressListeners.add(f)
  return () => { progressListeners.delete(f) }
}
function setProgress(id: CountryId, p: Partial<MatrixProgress>) {
  const cur = progress.get(id) ?? { loaded: 0, total: 0, done: false }
  progress.set(id, { ...cur, ...p })
  emitProgress()
}

/** Streams a binary file, reporting progress at most every ~200 ms. */
async function binary(path: string, onProgress?: (loaded: number, total: number) => void): Promise<ArrayBuffer> {
  // low priority: on HTTP/2 the admin outlines and other small JSON requested meanwhile go first
  const r = await fetch(url(path), { priority: 'low' } as RequestInit)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  if (!r.body || !onProgress) return r.arrayBuffer()
  // Content-Length is the encoded size when the server compresses; then the total stays unknown here
  const encoded = !!r.headers.get('content-encoding')
  const len = Number(r.headers.get('content-length')) || 0
  const total = encoded ? 0 : len
  let buf = new Uint8Array(total || 1 << 22)
  let off = 0, last = 0
  const reader = r.body.getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    if (off + value.length > buf.length) {
      const nb = new Uint8Array(Math.max(buf.length * 2, off + value.length))
      nb.set(buf.subarray(0, off))
      buf = nb
    }
    buf.set(value, off)
    off += value.length
    const now = performance.now()
    if (now - last > 200) { last = now; onProgress(off, total) }
  }
  onProgress(off, total)
  return off === buf.length ? buf.buffer : buf.slice(0, off).buffer
}

// ---------------------------------------------------------------------------------------------
// Countries
// ---------------------------------------------------------------------------------------------

type CountryBase = Omit<CountryData, 'matrix' | 'matrixReady'>

const countryCache = new Map<CountryId, Promise<CountryData>>()
const bases = new Map<CountryId, CountryBase>()
const matrixBuf = new Map<CountryId, Promise<ArrayBuffer>>()

/** Starts (once) the download of a country's metric matrix (~23 MB for the US). */
function fetchMatrix(id: CountryId): Promise<ArrayBuffer> {
  let p = matrixBuf.get(id)
  if (!p) {
    p = binary(`${id}/metrics.bin`, (loaded, total) => setProgress(id, { loaded, ...(total ? { total } : {}) }))
    p.catch(() => {}) // reported through loadMatrix; avoid an unhandled rejection meanwhile
    matrixBuf.set(id, p)
  }
  return p
}

/** Full CountryData with the real matrix; throws when metrics.bin does not match meta.json. */
function withMatrix(base: CountryBase, buf: ArrayBuffer): CountryData {
  const want = base.n * base.meta.metrics.length * 4
  if (buf.byteLength !== want) {
    throw new Error(`${base.id}/metrics.bin has ${buf.byteLength} bytes but meta.json describes ${base.n} tiles × ${base.meta.metrics.length} metrics (${want} bytes). The data files come from different exports — reload the page.`)
  }
  setProgress(base.id, { loaded: buf.byteLength, total: buf.byteLength, done: true })
  return { ...base, matrix: new Float32Array(buf), matrixReady: true }
}

/**
 * Placeholder until metrics.bin lands: matrixReady is false, value() gives NaN and column() an all-NaN
 * column. The full-size NaN matrix is only allocated if some code reads d.matrix directly meanwhile,
 * and loadMatrix() replaces this object in the cache so it can be garbage-collected afterwards.
 */
function placeholder(base: CountryBase): CountryData {
  let m: Float32Array | null = null
  const len = base.n * base.meta.metrics.length
  return { ...base, matrixReady: false, get matrix() { return (m ??= new Float32Array(len).fill(NaN)) } }
}

const yieldToMain = () => new Promise<void>((r) => {
  const ch = new MessageChannel()
  ch.port1.onmessage = () => { ch.port1.close(); r() }
  ch.port2.postMessage(0)
})

/**
 * Tile areas in km² (the density basis). Uses hexes.json `area_km2` when the exporter provides it;
 * otherwise h3 cellArea in slices (~41k cells for the US), yielding between slices so the page stays
 * responsive while the US loads.
 */
async function tileAreas(hexes: HexesJson): Promise<Float32Array> {
  const n = hexes.h3.length
  const out = new Float32Array(n)
  const given = (hexes as HexesJson & { area_km2?: number[] }).area_km2
  if (given && given.length === n) { out.set(given); return out }
  const SLICE = 2048
  for (let s = 0; s < n; s += SLICE) {
    const e = Math.min(n, s + SLICE)
    for (let i = s; i < e; i++) out[i] = cellArea(hexes.h3[i], 'km2')
    if (e < n) await yieldToMain()
  }
  return out
}

/**
 * Loads one country. All map-critical JSON is requested at once — meta, hexes, points, wonders,
 * cities and the admin tables (levels from index.json) — instead of as a waterfall, and the metric
 * matrix right after it. Resolves as soon as the map data is ready; when metrics.bin is still
 * downloading the result is a placeholder (matrixReady false) and loadMatrix() resolves the full
 * object later.
 */
export function loadCountry(id: CountryId): Promise<CountryData> {
  let p = countryCache.get(id)
  if (!p) {
    p = (async () => {
      const index = await loadIndex().catch(() => null)
      const idxLevels = Object.keys(index?.countries.find((c) => c.id === id)?.admin_levels ?? {})
      const adminP = new Map(idxLevels.map((lvl) => [lvl, json<AdminTable>(`${id}/admin_${lvl}.json`)] as const))
      adminP.forEach((a) => a.catch(() => {}))
      const [meta, hexes, points, wonders, cities] = await Promise.all([
        json<Meta>(`${id}/meta.json`),
        json<HexesJson>(`${id}/hexes.json`),
        json<PointLayers>(`${id}/points.json`).catch(() => ({}) as PointLayers),
        json<Wonder[]>(`${id}/wonders.json`).catch(() => []),
        json<City[]>(`${id}/cities.json`).catch(() => []),
      ])
      const n = hexes.h3.length
      setProgress(id, { total: n * meta.metrics.length * 4 })
      const admin: Record<string, AdminTable> = {}
      await Promise.all(
        Object.keys(meta.admin_levels).map(async (lvl) => {
          admin[lvl] = await (adminP.get(lvl) ?? json<AdminTable>(`${id}/admin_${lvl}.json`))
        }),
      )
      // The map-critical JSON is in: start the matrix now, before the tile-area pass (and without
      // waiting for the caller). Starting it earlier would only split a slow link's bandwidth with
      // hexes/admin and delay the map, since browsers do not prioritise fetches over HTTP/1.1.
      let bin: ArrayBuffer | null = null
      fetchMatrix(id).then((b) => { bin = b }, () => {})
      const hexIndex = new Map<string, number>()
      hexes.h3.forEach((h, i) => hexIndex.set(h, i))
      const hexArea = await tileAreas(hexes)
      const metricByKey = new Map<string, Metric>()
      meta.metrics.forEach((m) => metricByKey.set(m.key, m))
      const base: CountryBase = { id, meta, hexes, n, hexIndex, hexArea, metricByKey, points, wonders, cities, admin }
      bases.set(id, base)
      // fast connection: the matrix is already here, so skip the placeholder entirely
      if (bin) { try { return withMatrix(base, bin) } catch { /* loadMatrix() reports the mismatch */ } }
      return placeholder(base)
    })()
    countryCache.set(id, p)
  }
  return p
}

const matrixCache = new Map<CountryId, Promise<CountryData>>()

/** Resolve a new CountryData object carrying the real metric matrix (new identity → caches refresh). */
export function loadMatrix(d: CountryData): Promise<CountryData> {
  if (d.matrixReady) return Promise.resolve(d)
  let p = matrixCache.get(d.id)
  if (!p) {
    p = fetchMatrix(d.id).then((buf) => {
      const base = bases.get(d.id)
      if (!base) throw new Error(`${d.id}: loadMatrix() before loadCountry()`)
      const full = withMatrix(base, buf)
      // drop the placeholder (and any NaN matrix it allocated) from the cache
      countryCache.set(d.id, Promise.resolve(full))
      return full
    })
    matrixCache.set(d.id, p)
  }
  return p
}

let indexP: Promise<IndexJson> | null = null
export const loadIndex = () => (indexP ??= (async () => {
  // always revalidate: it carries the build stamp used to version every other file
  const index = await json<IndexJson>('index.json', { cache: 'no-cache' })
  if (index.built_at) dataVersion = index.built_at
  return index
})())

let sourcesP: Promise<Source[]> | null = null
export const loadSources = () => (sourcesP ??= loadIndex().catch(() => null).then(() => json<Source[]>('sources.json')))

let nationalP: Promise<NationalIndicator[]> | null = null
export const loadNational = () => (nationalP ??= loadIndex().catch(() => null).then(() => json<NationalIndicator[]>('national.json')).catch(() => []))

const geoCache = new Map<string, Promise<GeoJSON.FeatureCollection>>()
export function loadAdminGeo(country: CountryId, level: string) {
  const k = `${country}/${level}`
  let p = geoCache.get(k)
  if (!p) {
    p = loadIndex().catch(() => null).then(() => json<GeoJSON.FeatureCollection>(`${country}/admin_${level}.geojson`))
    geoCache.set(k, p)
  }
  return p
}

// ---------------------------------------------------------------------------------------------
// Accessors
// ---------------------------------------------------------------------------------------------

const nanColumns = new Map<number, Float32Array>()
/** Shared read-only all-NaN column of length n (used while the matrix is loading). */
function nanColumn(n: number) {
  let c = nanColumns.get(n)
  if (!c) { c = new Float32Array(n).fill(NaN); nanColumns.set(n, c) }
  return c
}

/** Metric value for hex i (NaN when missing, metric unknown, or the matrix is still loading). */
export function value(d: CountryData, key: string, i: number): number {
  const m = d.metricByKey.get(key)
  if (!m || !d.matrixReady) return NaN
  return d.matrix[m.col * d.n + i]
}

/** Whole column as a Float32Array view (no copy; all NaN while the matrix is loading). Do not write to it. */
export function column(d: CountryData, key: string): Float32Array | null {
  const m = d.metricByKey.get(key)
  if (!m) return null
  if (!d.matrixReady) return nanColumn(d.n)
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
