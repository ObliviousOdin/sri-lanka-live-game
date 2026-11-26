// Advisors panel — comments about a selected place (tile, admin unit or city): percentile within
// its own country, comparison with the other country's median unit, industry location quotients,
// yields, hazards, land cover. Only loaded values are used; missing inputs skip the comment.
import { adminUnitOfHex } from '../data/load'
import { terrainLabel } from '../civ'
import type { CountryData, CountryId, IndexJson, YieldKey } from '../types'
import {
  ADJ, type AdvisorId, type Comment, count, counterpartLevel, fig, getter, levelNoun, type Measure, measureValue,
  medianOf, metricMeasure, money, natAgg, nm, ok, ordinal, pct, percentileOf, perCapMeasure, type Place, poss, prettyLabel,
  type Seg, SHORT, sortedColumn, times, tonnes, unitName, yieldColumn,
} from './advisorsCore'

export interface PlaceCtx {
  place: Place
  other?: CountryData
}

const YLABEL: Record<YieldKey, string> = { gold: 'Gold', production: 'Production', food: 'Food', science: 'Science', culture: 'Culture', faith: 'Faith' }

// ---------------------------------------------------------------------------------------------
// Rank comments
// ---------------------------------------------------------------------------------------------
interface Rank { v: number; p: number; n: number }

function rankOf(place: Place, ms: Measure): Rank | null {
  const v = measureValue(place.d, place.level, place.index, ms)
  if (!ok(v)) return null
  const sorted = sortedColumn(place.d, place.level, ms)
  if (sorted.length < 3) return null
  return { v, p: percentileOf(sorted, v), n: sorted.length }
}

function otherMedian(ctx: PlaceCtx, ms: Measure | null) {
  if (!ctx.other || !ms) return null
  const lvl = counterpartLevel(ctx.place.level, ctx.other)
  const s = sortedColumn(ctx.other, lvl, ms)
  return s.length >= 3 ? { med: medianOf(s), n: s.length, lvl } : null
}

/** "<label> X — Nth percentile of M Sri Lankan districts; US median county: Y — k× higher here." */
function rankComment(ctx: PlaceCtx, id: string, icon: string, ms: Measure | null, msOther: Measure | null, opts: { lead?: string; suffix?: (r: Rank) => string; vlabel?: string } = {}): Comment | null {
  if (!ms) return null
  const { place } = ctx
  const r = rankOf(place, ms)
  if (!r) return null
  const home = place.country
  const segs: Seg[] = [`${opts.lead ?? ms.label} `, fig(ms.fmt(r.v), home),
    ` — ${ordinal(Math.min(99, Math.max(1, r.p)))} percentile of ${count(r.n)} ${ADJ[home]} ${levelNoun(place.d, place.level, r.n)}`]
  const sources = [...ms.sources]
  const om = otherMedian(ctx, msOther)
  if (om && ctx.other) {
    const oc = ctx.other.id
    segs.push(`; ${ADJ[oc]} median ${levelNoun(ctx.other, om.lvl, 1)}: `, fig(msOther!.fmt(om.med), oc))
    if (ms.ratio && r.v > 0 && om.med > 0) {
      const k = r.v / om.med
      if (k >= 1.5 || k <= 1 / 1.5) segs.push(` — ${times(k >= 1 ? k : 1 / k)} ${k >= 1 ? 'higher' : 'lower'} here`)
    }
    sources.push(...msOther!.sources)
  }
  if (opts.suffix) segs.push(opts.suffix(r))
  segs.push('.')
  const extreme = Math.abs(r.p - 50) / 50
  return {
    id, icon, segs, sources, pct: r.p, pctBetter: ms.better, meta: ms.vintage || undefined,
    weight: r.n >= 5 ? extreme : 0,
    verdict: [`${place.title} ranks in the `, fig(r.p >= 50 ? `top ${Math.max(1, Math.round(100 - r.p))}%` : `bottom ${Math.max(1, Math.round(r.p))}%`, home),
      ` of ${ADJ[home]} ${levelNoun(place.d, place.level, r.n)} for ${opts.vlabel ?? lc1(ms.label.replace(/[:\s]+$/, ''))} (`, fig(ms.fmt(r.v), home), ').'],
  }
}

/** Counts reconciled onto units can be fractional: show whole numbers, "≈" when rounded. */
const nCount = (v: number) => (Math.abs(v - Math.round(v)) > 0.05 && Math.abs(v) < 1000 ? `≈${count(Math.round(v))}` : count(v))

const lc1 = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s)

const mm = (d: CountryData | undefined, key: string, o?: Partial<Measure>) => (d ? metricMeasure(d, key, o) : null)
const firstKey = (d: CountryData, keys: string[]) => keys.find((k) => d.metricByKey.has(k))

// ---------------------------------------------------------------------------------------------
// Shares of national totals
// ---------------------------------------------------------------------------------------------
function localValue(place: Place, key: string) {
  return getter(place.d, place.level, place.index)(key)
}

function shareOfNation(place: Place, key: string) {
  const v = localValue(place, key)
  const tot = natAgg(place.d, key)
  if (!ok(v) || !ok(tot) || tot <= 0) return null
  return { v, tot, share: (100 * v) / tot }
}

const sortedYield = new WeakMap<Float32Array, Float32Array>()
function yieldRank(place: Place, y: YieldKey) {
  const col = yieldColumn(place.d, place.level, y)
  if (!col) return null
  let s = sortedYield.get(col)
  if (!s) {
    const tmp: number[] = []
    for (let i = 0; i < col.length; i++) if (Number.isFinite(col[i])) tmp.push(col[i])
    s = Float32Array.from(tmp).sort()
    sortedYield.set(col, s)
  }
  const v = col[place.index]
  const tot = place.d.meta.yield_totals?.[y] ?? 0
  if (!ok(v) || !(tot > 0)) return null
  return { v, p: percentileOf(s, v), n: s.length, share: (100 * v) / tot, tot }
}

function yieldSources(place: Place, y: YieldKey, extra: string[] = []) {
  const base: Record<YieldKey, string[]> = {
    gold: ['gdp_usd'], production: ['gdp_industry_usd', 'power_capacity_mw'], food: ['ag_gva_usd', 'gdp_agri_usd', 'fish_catch_t'],
    science: ['cit_engineers', 'research_works_5y'], culture: ['emp_arts_accommodation', 'museums', 'park_visitors'], faith: ['worship_sites'],
  }
  return [...base[y], ...extra].flatMap((k) => place.d.metricByKey.get(k)?.sources ?? [])
}

function yieldComment(place: Place, ys: YieldKey[]): Comment | null {
  const parts: Seg[] = []
  let p0: number | undefined
  const home = place.country
  for (const y of ys) {
    const r = yieldRank(place, y)
    if (!r) continue
    if (parts.length) parts.push('; ')
    parts.push(`${YLABEL[y]} `, fig(count(r.v), home), ` (${pct(r.share)} of ${poss(home)} ${count(r.tot)})`)
    p0 ??= r.p
  }
  if (!parts.length) return null
  const segs: Seg[] = [place.level === 'hex' ? 'Tile yields: ' : 'Yields of its tiles: ', ...parts, '.']
  return { id: `yield:${ys.join('+')}`, icon: `y:${ys[0]}`, segs, sources: ys.flatMap((y) => yieldSources(place, y)), pct: p0, pctBetter: 'higher', meta: 'Map yields on the shared scale (see Methodology)' }
}

// ---------------------------------------------------------------------------------------------
// Industry mix: location quotients
// ---------------------------------------------------------------------------------------------
const cleanEmp = (l: string) => prettyLabel(l).replace(/^(employment|jobs|employed)\s*(in|:)?\s*/i, '').replace(/\s+(jobs|employment|workers)(?=\s*(\(|$))/i, '')

function lqComment(place: Place): Comment | null {
  const d = place.d
  const keys = [...d.metricByKey.keys()].filter((k) => k.startsWith('emp_') && !k.endsWith('_pct') && d.metricByKey.get(k)!.kind === 'extensive')
  const g = getter(d, place.level, place.index)
  if (keys.length >= 3) {
    const useEmployed = d.metricByKey.has('employed')
    const loc = useEmployed ? g('employed') : keys.reduce((s, k) => s + (ok(g(k)) ? g(k) : 0), 0)
    const nat = useEmployed ? natAgg(d, 'employed') : keys.reduce((s, k) => s + (ok(natAgg(d, k)) ? natAgg(d, k) : 0), 0)
    if (loc > 0 && nat > 0) {
      let best: { k: string; s: number; ns: number; lq: number } | null = null
      let biggest: { k: string; s: number } | null = null
      for (const k of keys) {
        const v = g(k), nv = natAgg(d, k)
        if (!ok(v) || !ok(nv) || nv <= 0) continue
        const s = v / loc, ns = nv / nat
        if (!biggest || s > biggest.s) biggest = { k, s }
        if (s < 0.02) continue
        const lq = s / ns
        if (!best || lq > best.lq) best = { k, s, ns, lq }
      }
      const home = place.country
      const src = [...keys, useEmployed ? 'employed' : ''].filter(Boolean).flatMap((k) => d.metricByKey.get(k)?.sources ?? [])
      const vint = d.metricByKey.get(keys[0])?.vintage
      if (best && best.lq >= 1.2) {
        return {
          id: 'lq', icon: '🏭', sources: src, meta: `Location quotient = local share ÷ national share of jobs${vint ? ` · ${vint}` : ''}`,
          segs: ['Specialisation: ', cleanEmp(d.metricByKey.get(best.k)!.label).toLowerCase(), ' employs ', fig(pct(best.s * 100), home),
            ' of workers here — ', fig(times(best.lq), home), ` the national share (${pct(best.ns * 100)}).`],
          weight: 0,
        }
      }
      if (biggest) {
        return {
          id: 'lq', icon: '🏭', sources: src, meta: vint,
          segs: ['A balanced economy: no sector reaches 1.2× its national share of jobs; the largest employer is ', cleanEmp(d.metricByKey.get(biggest.k)!.label).toLowerCase(), ' (', fig(pct(biggest.s * 100), home), ').'],
        }
      }
    }
  }
  // fallback: GDP by sector
  const gdp = g('gdp_usd'), ngdp = natAgg(d, 'gdp_usd')
  if (!(gdp > 0) || !(ngdp > 0)) return null
  const sectors: [string, string][] = [['gdp_agri_usd', 'agriculture'], ['gdp_industry_usd', 'industry'], ['gdp_services_usd', 'services'], ['gdp_manufacturing_usd', 'manufacturing'], ['gdp_finance_usd', 'finance']]
  let best: { l: string; s: number; ns: number; k: string } | null = null
  for (const [k, l] of sectors) {
    const v = g(k), nv = natAgg(d, k)
    if (!ok(v) || !(nv > 0)) continue
    const s = v / gdp, ns = nv / ngdp
    if (s < 0.02) continue
    if (!best || s / ns > best.s / best.ns) best = { l, s, ns, k }
  }
  if (!best) return null
  return {
    id: 'lq', icon: 'y:production', sources: d.metricByKey.get(best.k)?.sources ?? [], meta: d.metricByKey.get(best.k)?.vintage,
    segs: [`Economic mix: ${best.l} generates `, fig(pct(best.s * 100), place.country), ` of local GDP vs ${pct(best.ns * 100)} nationally.`],
  }
}

// ---------------------------------------------------------------------------------------------
// Dominant category (religion, ethnicity, land cover)
// ---------------------------------------------------------------------------------------------
function dominant(place: Place, prefix: string, suffix: string, exclude: string[] = []) {
  const d = place.d
  const g = getter(d, place.level, place.index)
  let best: { k: string; v: number } | null = null
  for (const k of d.metricByKey.keys()) {
    if (!k.startsWith(prefix) || !k.endsWith(suffix) || exclude.includes(k)) continue
    const v = g(k)
    if (ok(v) && (!best || v > best.v)) best = { k, v }
  }
  return best && best.v > 0 ? best : null
}

const catName = (d: CountryData, k: string) => prettyLabel(d.metricByKey.get(k)?.label ?? k).replace(/\s*\((share|%)\)$/i, '').replace(/^(Religion|Ethnicity|Adherents)\s*:\s*/i, '')

function dominantComment(place: Place, other: CountryData | undefined, id: string, icon: string, lead: string, prefix: string, suffix: string, scale: number, exclude: string[] = []): Comment | null {
  const b = dominant(place, prefix, suffix, exclude)
  if (!b) return null
  const d = place.d
  const home = place.country
  const nat = natAgg(d, b.k)
  const segs: Seg[] = [lead, catName(d, b.k), ' ', fig(pct(b.v * scale), home)]
  if (ok(nat)) segs.push(` (${nm(home)}: ${pct(nat * scale)}`)
  if (other?.metricByKey.has(b.k)) {
    const on = natAgg(other, b.k)
    if (ok(on)) segs.push(`${ok(nat) ? '; ' : ' ('}${SHORT[other.id]}: `, fig(pct(on * scale), other.id))
  }
  if (ok(nat) || other?.metricByKey.has(b.k)) segs.push(')')
  segs.push('.')
  return { id, icon, segs, sources: d.metricByKey.get(b.k)?.sources ?? [], meta: d.metricByKey.get(b.k)?.vintage }
}

// ---------------------------------------------------------------------------------------------
// Advisors
// ---------------------------------------------------------------------------------------------
function wondersIn(place: Place) {
  const d = place.d
  return d.wonders.filter((w) => {
    const i = w.h3 ? d.hexIndex.get(w.h3) : undefined
    if (i == null) return false
    if (place.level === 'hex') return i === place.index
    return adminUnitOfHex(d, place.level, i)?.index === place.index
  })
}

function cityComment(place: Place): Comment | null {
  const c = place.city
  if (!c) return null
  const home = place.country
  const segs: Seg[] = [`City View: ${c.name} is a size-`, fig(String(c.size), home), ' city of ', fig(count(c.pop), home), ` residents across its ${c.tiles.length} tiles`]
  if (ok(c.official_pop)) segs.push(` (official figure ${count(c.official_pop)}${c.official_pop_year ? `, ${c.official_pop_year}` : ''})`)
  if (ok(c.gdp_per_capita)) segs.push('; GDP per resident ', fig(money(c.gdp_per_capita), home))
  segs.push('.')
  return { id: 'city', icon: '🏛️', segs, sources: [...(place.d.metricByKey.get('pop')?.sources ?? []), ...(ok(c.gdp_per_capita) ? place.d.metricByKey.get('gdp_usd')?.sources ?? [] : [])], meta: 'City population = sum of its City View tiles' }
}

function economic(ctx: PlaceCtx): Comment[] {
  const { place, other } = ctx
  const d = place.d
  const out: (Comment | null)[] = [cityComment(place)]
  const gk = firstKey(d, ['gdp_per_capita_usd', 'gdp_pc_usd'])
  const ogk = other && firstKey(other, ['gdp_per_capita_usd', 'gdp_pc_usd'])
  if (gk) out.push(rankComment(ctx, 'gdp_pc', 'y:gold', mm(d, gk, { label: 'GDP per capita' }), ogk ? mm(other, ogk, { label: 'GDP per capita' }) : null))
  out.push(rankComment(ctx, 'hh_income', '💵', mm(d, 'hh_income_usd'), mm(other, 'hh_income_usd')))
  out.push(rankComment(ctx, 'poverty', '🥣', mm(d, 'poverty_rate'), mm(other, 'poverty_rate')))
  out.push(lqComment(place))
  out.push(rankComment(ctx, 'rwi', '📈', mm(d, 'rwi'), mm(other, 'rwi')))
  out.push(yieldComment(place, ['gold', 'production']))
  return out.filter(Boolean) as Comment[]
}

function military(ctx: PlaceCtx): Comment[] {
  const { place, other } = ctx
  const d = place.d
  const home = place.country
  const out: (Comment | null)[] = []
  const pop = shareOfNation(place, 'pop')
  if (pop) {
    out.push({
      id: 'manpower', icon: '👥', sources: d.metricByKey.get('pop')?.sources ?? [], meta: d.metricByKey.get('pop')?.vintage,
      segs: ['Manpower: ', fig(count(pop.v), home), ` residents — ${pct(pop.share)} of ${poss(home)} ${count(pop.tot)} people.`],
    })
  }
  const ms = shareOfNation(place, 'military_sites')
  if (ms) {
    out.push({
      id: 'garrison', icon: '🏰', sources: d.metricByKey.get('military_sites')?.sources ?? [], meta: d.metricByKey.get('military_sites')?.vintage,
      segs: ms.v > 0
        ? ['Garrisons: ', fig(nCount(ms.v), home), ` mapped military installation${Math.round(ms.v) === 1 ? '' : 's'} — ${pct(ms.share)} of ${poss(home)} ${nCount(ms.tot)}.`]
        : [`No mapped military installations here (${nm(home)} has `, fig(nCount(ms.tot), home), ').'],
    })
  }
  out.push(rankComment(ctx, 'soldiers', '💂', mm(d, 'cit_soldiers_pct', { label: 'Soldiers (share of workers)' }), mm(other, 'cit_soldiers_pct', { label: 'Soldiers (share of workers)' })))
  out.push(rankComment(ctx, 'slope', '⛰️', mm(d, 'slope_mean_deg', { label: 'Defensive ground — mean slope', ratio: true }), mm(other, 'slope_mean_deg', { ratio: true }), {
    suffix: (r) => (r.p >= 75 ? ' (rough terrain favours defenders)' : r.p <= 25 ? ' (open ground, hard to hold)' : ''),
    vlabel: 'rugged terrain (mean slope)',
  }))
  // strategic assets
  const assets: Seg[] = []
  const srcs: string[] = []
  const add = (key: string, f: (v: number) => string, label: string) => {
    const s = shareOfNation(place, key)
    if (!s || !(s.v > 0)) return
    if (assets.length) assets.push('; ')
    assets.push(label, fig(f(s.v), home), ` (${pct(s.share)} of the national total)`)
    srcs.push(...(d.metricByKey.get(key)?.sources ?? []))
  }
  add('port_cargo_t', tonnes, 'port cargo ')
  add('airport_pax', count, 'air passengers ')
  add('power_capacity_mw', (v) => `${count(v)} MW`, 'power plants ')
  if (assets.length) out.push({ id: 'assets', icon: '⚓', segs: ['Strategic assets: ', ...assets, '.'], sources: srcs })
  return out.filter(Boolean) as Comment[]
}


function cultural(ctx: PlaceCtx): Comment[] {
  const { place, other } = ctx
  const d = place.d
  const home = place.country
  const out: (Comment | null)[] = []
  out.push(yieldComment(place, ['culture', 'faith']))
  out.push(dominantComment(place, other, 'religion', '🪔', 'Largest faith: ', 'rel_', '_pct', 1, ['rel_unclaimed_pct']))
  out.push(dominantComment(place, other, 'ethnicity', '🧑‍🤝‍🧑', 'Largest group: ', 'eth_', '_pct', 1))
  const w = wondersIn(place)
  const heritage: Seg[] = []
  const srcs: string[] = []
  if (w.length) {
    heritage.push(`${w.length === 1 ? 'Wonder' : 'Wonders'} here: `, fig(w.slice(0, 3).map((x) => x.name).join(', ') + (w.length > 3 ? ` +${w.length - 3}` : ''), home))
    srcs.push(...w.flatMap((x) => x.sources ?? []))
  }
  for (const [k, one, many] of [['museums', 'museum', 'museums'], ['worship_sites', 'place of worship', 'places of worship'], ['park_visitors', 'park visitor', 'park visitors a year']] as const) {
    const s = shareOfNation(place, k)
    if (!s || !(s.v > 0)) continue
    heritage.push(heritage.length ? '; ' : '', fig(nCount(s.v), home), ` ${Math.round(s.v) === 1 ? one : many} (${pct(s.share)} of national)`)
    srcs.push(...(d.metricByKey.get(k)?.sources ?? []))
  }
  if (heritage.length) out.push({ id: 'heritage', icon: '🏛️', segs: ['Heritage: ', ...heritage, '.'], sources: srcs })
  out.push(rankComment(ctx, 'rel_div', '☯️', mm(d, 'religious_diversity'), mm(other, 'religious_diversity')))
  return out.filter(Boolean) as Comment[]
}

function scientific(ctx: PlaceCtx): Comment[] {
  const { place, other } = ctx
  const d = place.d
  const home = place.country
  const out: (Comment | null)[] = []
  const ek = firstKey(d, ['pct_bachelors', 'pct_secondary', 'literacy_rate'])
  if (ek) out.push(rankComment(ctx, 'edu', '🎓', mm(d, ek), mm(other, ek)))
  const share = shareOfNation(place, 'research_works_5y')
  if (share && share.v > 0) {
    const lab = 'Research works per million residents (5 yrs):'
    const rw = perCapMeasure(d, 'research_works_5y', 1e6, lab, '')
    const orw = other ? perCapMeasure(other, 'research_works_5y', 1e6, lab, '') : null
    out.push(rankComment(ctx, 'research', '📚', rw, orw, {
      suffix: () => `; ${count(share.v)} works in all, ${pct(share.share)} of ${poss(home)} output`,
    }))
  } else if (share) {
    out.push({
      id: 'research', icon: '📚', sources: d.metricByKey.get('research_works_5y')?.sources ?? [], meta: d.metricByKey.get('research_works_5y')?.vintage,
      segs: [`No research output is attributed here; ${poss(home)} `, fig(count(share.tot), home), ' research works (5 yrs) come from other places.'],
    })
  }
  out.push(rankComment(ctx, 'engineers', '🧑‍🔬', mm(d, 'cit_engineers_pct', { label: 'Engineers (STEM share of workers)' }), mm(other, 'cit_engineers_pct', { label: 'Engineers' })))
  out.push(yieldComment(place, ['science']))
  out.push(rankComment(ctx, 'schools', '🏫', perCapMeasure(d, 'schools', 1e4, 'Schools per 10,000 residents:', ''), other ? perCapMeasure(other, 'schools', 1e4, 'Schools per 10,000 residents:', '') : null))
  return out.filter(Boolean) as Comment[]
}

function environmental(ctx: PlaceCtx): Comment[] {
  const { place, other } = ctx
  const d = place.d
  const home = place.country
  const out: (Comment | null)[] = []
  const lc = dominant(place, 'lc_', '', [])
  out.push(rankComment(ctx, 'tree', '🌳', mm(d, 'lc_tree', { label: 'Tree cover' }), mm(other, 'lc_tree', { label: 'Tree cover' }), {
    lead: lc && lc.k !== 'lc_tree' ? `Mostly ${catName(d, lc.k).toLowerCase()} (${pct(lc.v * 100)}); tree cover` : 'Tree cover',
  }))
  const g = getter(d, place.level, place.index)
  out.push(rankComment(ctx, 'elev', '🏔️', mm(d, 'elev_mean_m', { label: 'Mean elevation' }), mm(other, 'elev_mean_m'), {
    suffix: () => (ok(g('elev_min_m')) && ok(g('elev_max_m')) ? `; range ${Math.round(g('elev_min_m'))}–${Math.round(g('elev_max_m'))} m` : ''),
  }))
  out.push(rankComment(ctx, 'hazard', '🌪️', mm(d, 'hazard_index', { label: 'Natural hazard index', ratio: true }), mm(other, 'hazard_index', { ratio: true })))
  if (home === 'lka') out.push(rankComment(ctx, 'deaths', '🕯️', mm(d, 'disaster_deaths_per_100k', { label: 'Disaster deaths', ratio: true }), null))
  else out.push(rankComment(ctx, 'nri', '🧯', mm(d, 'nri_risk_score', { label: 'FEMA risk score', ratio: true }), null))
  out.push(rankComment(ctx, 'protected', '🦌', mm(d, 'protected_pct', { label: 'Protected land' }), mm(other, 'protected_pct')))
  out.push(rankComment(ctx, 'rain', '🌧️', mm(d, 'precip_mm', { label: 'Rainfall' }), mm(other, 'precip_mm'), {
    suffix: () => (ok(g('temp_mean_c')) ? `; mean temperature ${g('temp_mean_c').toFixed(1)} °C` : ''),
  }))
  return out.filter(Boolean) as Comment[]
}

export function placeComments(advisor: AdvisorId, ctx: PlaceCtx): Comment[] {
  switch (advisor) {
    case 'economic': return economic(ctx)
    case 'military': return military(ctx)
    case 'cultural': return cultural(ctx)
    case 'scientific': return scientific(ctx)
    case 'environmental': return environmental(ctx)
  }
  return []
}

/** Display name of a twin unit / tile. */
export function twinName(d: CountryData, level: string, index: number, idx?: IndexJson | null): { title: string; subtitle: string } {
  if (level === 'hex') {
    const u2 = adminUnitOfHex(d, 'adm2', index)
    const u1 = adminUnitOfHex(d, 'adm1', index)
    const terr = idx ? `${terrainLabel(idx.terrain_codes[d.hexes.terrain[index]] ?? 'plains', idx.feature_codes[d.hexes.feature[index]] ?? '', !!d.hexes.hills[index])} tile` : ''
    return {
      title: u2 ? `Tile in ${unitName(d, 'adm2', u2.unit)}` : `Tile ${d.hexes.h3[index]}`,
      subtitle: [terr, d.id === 'lka' && u1 ? unitName(d, 'adm1', u1.unit) : ''].filter(Boolean).join(' · '),
    }
  }
  const u = d.admin[level]?.units[index]
  if (!u) return { title: '?', subtitle: '' }
  const parent = level === 'adm2' ? d.admin.adm1?.units.find((x) => x.id === u.parent) : undefined
  const sub = d.id === 'lka' ? (parent ? unitName(d, 'adm1', parent) : '') : level === 'adm2' ? (d.admin.adm1?.units.find((x) => x.id === u.parent)?.name ?? '') : ''
  return { title: unitName(d, level, u), subtitle: sub || (d.meta.admin_levels[level]?.label ?? '') }
}

