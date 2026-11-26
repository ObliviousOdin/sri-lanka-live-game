// "Twin across the ocean": nearest neighbour of a tile / admin unit in the OTHER country, on
// standardized features available in both countries. Feature matrices are built once per
// (Sri Lanka data, US data, level pair) and cached; a query is one linear scan (~40k × 9 floats).
import { column } from '../data/load'
import type { CountryData, CountryId, Metric } from '../types'
import { fmtMetric, prettyLabel } from './advisorsCore'

interface FeatureCandidate { keys: string[]; log: boolean; label?: string }

/** Twin features, in display priority. The first key of each group that exists in both countries is used. */
const CANDIDATES: FeatureCandidate[] = [
  { keys: ['pop_density'], log: true, label: 'Population density' },
  { keys: ['gdp_per_capita_usd', 'gdp_pc_usd', 'hh_income_usd'], log: true },
  { keys: ['pct_bachelors'], log: false, label: "Bachelor's degree or higher" },
  { keys: ['lc_tree'], log: false, label: 'Tree cover' },
  { keys: ['lc_crop'], log: false, label: 'Cropland' },
  { keys: ['elev_mean_m'], log: false, label: 'Mean elevation' },
  { keys: ['hazard_index'], log: false, label: 'Natural hazard index' },
  { keys: ['cit_engineers_pct'], log: false, label: 'Engineers (share of jobs)' },
  { keys: ['cit_farmers_pct'], log: false, label: 'Farmers (share of jobs)' },
]

export interface TwinFeature {
  key: string
  label: string
  log: boolean
  unit: string
  sources: string[]
}

interface Space {
  d: CountryData
  level: string
  /** candidate indices (hex index or unit index) */
  cand: Int32Array
  /** raw values per feature (full length) */
  raw: Float32Array[]
}

export interface TwinModel {
  feats: TwinFeature[]
  spaces: Record<CountryId, Space>
  mean: Float64Array
  sd: Float64Array
  /** candidates × features, row-major, z-scores (NaN = missing) */
  z: Record<CountryId, Float32Array>
}

export interface TwinFeatureMatch { f: TwinFeature; a: number; b: number; dz: number }
export interface TwinMatch { index: number; dist: number; used: number; feats: TwinFeatureMatch[] }
export type TwinResult =
  | { ok: true; model: TwinModel; country: CountryId; level: string; matches: TwinMatch[]; pool: number; queryFeatures: number }
  | { ok: false; reason: string }

function hasKey(d: CountryData, level: string, key: string) {
  return level === 'hex' ? d.metricByKey.has(key) : !!d.admin[level]?.metrics[key]
}

function rawColumn(d: CountryData, level: string, key: string): Float32Array | null {
  if (level === 'hex') return column(d, key)
  const arr = d.admin[level]?.metrics[key]
  if (!arr) return null
  const out = new Float32Array(arr.length)
  for (let i = 0; i < arr.length; i++) out[i] = arr[i] == null ? NaN : (arr[i] as number)
  return out
}

function candidates(d: CountryData, level: string): Int32Array {
  const out: number[] = []
  if (level === 'hex') {
    const pop = column(d, 'pop')
    for (let i = 0; i < d.n; i++) if (d.hexes.ring[i] === 0 && (!pop || pop[i] > 0)) out.push(i)
  } else {
    const t = d.admin[level]
    const pop = t?.metrics.pop
    const N = t?.units.length ?? 0
    for (let i = 0; i < N; i++) if (!pop || (pop[i] ?? 0) > 0) out.push(i)
  }
  return Int32Array.from(out)
}

const tr = (v: number, log: boolean) => (log ? (v > 0 ? Math.log10(v) : NaN) : v)

const modelCache = new WeakMap<CountryData, WeakMap<CountryData, Map<string, TwinModel | string>>>()

/** Build (or fetch) the pooled, standardized feature model for one level pair. Returns a reason string when impossible. */
export function twinModel(lka: CountryData, usa: CountryData, lkaLevel: string, usaLevel: string): TwinModel | string {
  let inner = modelCache.get(lka)
  if (!inner) { inner = new WeakMap(); modelCache.set(lka, inner) }
  let m = inner.get(usa)
  if (!m) { m = new Map(); inner.set(usa, m) }
  const key = `${lkaLevel}|${usaLevel}`
  const hit = m.get(key)
  if (hit) return hit
  const built = build(lka, usa, lkaLevel, usaLevel)
  m.set(key, built)
  return built
}

function build(lka: CountryData, usa: CountryData, lkaLevel: string, usaLevel: string): TwinModel | string {
  if (!lka.admin[lkaLevel] && lkaLevel !== 'hex') return `No ${lkaLevel} table for Sri Lanka`
  if (!usa.admin[usaLevel] && usaLevel !== 'hex') return `No ${usaLevel} table for the United States`
  const cand: Record<CountryId, Int32Array> = { lka: candidates(lka, lkaLevel), usa: candidates(usa, usaLevel) }
  if (cand.lka.length < 2 || cand.usa.length < 2) return 'Too few populated units to compare'

  const feats: TwinFeature[] = []
  const raws: Record<CountryId, Float32Array[]> = { lka: [], usa: [] }
  const means: number[] = [], sds: number[] = []
  for (const fc of CANDIDATES) {
    const key = fc.keys.find((k) => hasKey(lka, lkaLevel, k) && hasKey(usa, usaLevel, k))
    if (!key) continue
    const cols = { lka: rawColumn(lka, lkaLevel, key), usa: rawColumn(usa, usaLevel, key) }
    if (!cols.lka || !cols.usa) continue
    // per-country moments over candidates, then an equal-weight pooled mean / variance
    const mom = (['lka', 'usa'] as CountryId[]).map((c) => {
      let s = 0, s2 = 0, n = 0
      const col = cols[c]!
      for (const i of cand[c]) {
        const v = tr(col[i], fc.log)
        if (Number.isFinite(v)) { s += v; s2 += v * v; n++ }
      }
      const mu = n ? s / n : NaN
      return { n, mu, v: n > 1 ? Math.max(0, s2 / n - mu * mu) : NaN }
    })
    if (mom.some((x) => x.n < 2)) continue
    const mu = (mom[0].mu + mom[1].mu) / 2
    const variance = (mom[0].v + mom[1].v) / 2 + ((mom[0].mu - mom[1].mu) / 2) ** 2
    const sd = Math.sqrt(variance)
    if (!(sd > 1e-9)) continue
    const m: Metric = (lka.metricByKey.get(key) ?? usa.metricByKey.get(key))!
    feats.push({
      key, log: fc.log, label: fc.label ?? prettyLabel(m?.label ?? key), unit: m?.unit ?? '',
      sources: [...new Set([...(lka.metricByKey.get(key)?.sources ?? []), ...(usa.metricByKey.get(key)?.sources ?? [])])],
    })
    raws.lka.push(cols.lka); raws.usa.push(cols.usa)
    means.push(mu); sds.push(sd)
  }
  if (feats.length < 3) {
    return `Only ${feats.length} comparable indicator${feats.length === 1 ? '' : 's'} exist in both countries at this level (need 3)`
  }
  const k = feats.length
  const mean = Float64Array.from(means), sd = Float64Array.from(sds)
  const z = {} as Record<CountryId, Float32Array>
  for (const c of ['lka', 'usa'] as CountryId[]) {
    const ids = cand[c]
    const out = new Float32Array(ids.length * k)
    for (let r = 0; r < ids.length; r++) {
      for (let f = 0; f < k; f++) out[r * k + f] = (tr(raws[c][f][ids[r]], feats[f].log) - mean[f]) / sd[f]
    }
    z[c] = out
  }
  return {
    feats, mean, sd, z,
    spaces: {
      lka: { d: lka, level: lkaLevel, cand: cand.lka, raw: raws.lka },
      usa: { d: usa, level: usaLevel, cand: cand.usa, raw: raws.usa },
    },
  }
}

/** Find the `top` most similar units in the other country to (country, index). */
export function findTwins(model: TwinModel, country: CountryId, index: number, top = 3): TwinResult {
  const k = model.feats.length
  const home = model.spaces[country]
  const rivalId: CountryId = country === 'lka' ? 'usa' : 'lka'
  const rival = model.spaces[rivalId]
  const zq = new Float64Array(k)
  let kq = 0
  for (let f = 0; f < k; f++) {
    zq[f] = (tr(home.raw[f][index], model.feats[f].log) - model.mean[f]) / model.sd[f]
    if (Number.isFinite(zq[f])) kq++
  }
  if (kq < 3) return { ok: false, reason: `This place has data for only ${kq} of the ${k} twin indicators` }
  const minUsed = Math.max(3, Math.ceil(kq * 0.75))
  const Z = model.z[rivalId]
  const ids = rival.cand
  const bestI: number[] = [], bestD: number[] = [], bestU: number[] = []
  for (let r = 0; r < ids.length; r++) {
    let s = 0, used = 0
    const o = r * k
    for (let f = 0; f < k; f++) {
      const a = zq[f], b = Z[o + f]
      if (a === a && b === b) { const dd = a - b; s += dd * dd; used++ }
    }
    if (used < minUsed) continue
    const dist = Math.sqrt(s / used) * (1 + 0.15 * (kq - used))
    if (bestD.length === top && dist >= bestD[top - 1]) continue
    let p = bestD.length
    while (p > 0 && bestD[p - 1] > dist) p--
    bestD.splice(p, 0, dist); bestI.splice(p, 0, ids[r]); bestU.splice(p, 0, used)
    if (bestD.length > top) { bestD.pop(); bestI.pop(); bestU.pop() }
  }
  if (!bestI.length) return { ok: false, reason: 'No unit in the other country has enough overlapping data' }
  const matches: TwinMatch[] = bestI.map((ti, j) => {
    const feats: TwinFeatureMatch[] = []
    for (let f = 0; f < k; f++) {
      const a = home.raw[f][index], b = rival.raw[f][ti]
      const za = zq[f], zb = (tr(b, model.feats[f].log) - model.mean[f]) / model.sd[f]
      if (Number.isFinite(za) && Number.isFinite(zb)) feats.push({ f: model.feats[f], a, b, dz: Math.abs(za - zb) })
    }
    feats.sort((x, y) => x.dz - y.dz)
    return { index: ti, dist: bestD[j], used: bestU[j], feats }
  })
  return { ok: true, model, country: rivalId, level: rival.level, matches, pool: ids.length, queryFeatures: kq }
}

export const fmtFeature = (f: TwinFeature, v: number) => fmtMetric(f.unit, v)

/** Qualitative closeness for a mean |Δz|. */
export function closeness(dist: number) {
  if (dist < 0.25) return 'a very close match'
  if (dist < 0.5) return 'a close match'
  if (dist < 0.9) return 'a loose match'
  return 'the nearest, though no close analogue exists'
}
