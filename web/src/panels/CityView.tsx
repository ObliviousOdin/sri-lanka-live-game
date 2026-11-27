import { cellArea } from 'h3-js'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { CITIZEN_META, COUNTRY_META, DISTRICT_META, FEATURE_META, TERRAIN_META, YIELD_META, terrainLabel, type RGB } from '../civ'
import { mix, rgbCss, shade } from '../color'
import { SourceChips } from '../components/SourceChip'
import { YieldIcon, YieldRow } from '../components/YieldIcon'
import { column } from '../data/load'
import { useNational, type AppData } from '../data/useData'
import { fmtArea, fmtNumber, fmtPct, fmtUsd } from '../format'
import { WONDER_ICON } from '../map/MapPane'
import { useStore } from '../store'
import { COUNTRIES, YIELDS, type City, type CityTile, type CountryData, type CountryId, type IndexJson, type NationalIndicator, type Selection, type Wonder, type YieldKey } from '../types'
import { citiesByPop } from './advisorsCore'
import { countryVintage } from './explorerData'
import { HEX_R, hexPoints, layoutRing } from './cityViewLayout'
import { openCity } from './cityViewNav'
import './CityView.css'

// ---------------------------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------------------------

const INDUSTRY: Record<string, { label: string; icon: string }> = {
  emp_agri: { label: 'Agriculture, forestry & fishing', icon: '🌾' },
  emp_mining: { label: 'Mining & quarrying', icon: '⛏️' },
  emp_manufacturing: { label: 'Manufacturing', icon: '🏭' },
  emp_construction: { label: 'Construction', icon: '🏗️' },
  emp_utilities: { label: 'Utilities (power, water)', icon: '⚡' },
  emp_trade: { label: 'Wholesale & retail trade', icon: '🛒' },
  emp_transport: { label: 'Transport & storage', icon: '🚚' },
  emp_ict: { label: 'Information & communication', icon: '💻' },
  emp_finance: { label: 'Finance & insurance', icon: '💰' },
  emp_professional: { label: 'Professional & business services', icon: '📐' },
  emp_education: { label: 'Education', icon: '🎓' },
  emp_health: { label: 'Health & social care', icon: '🩺' },
  emp_arts_accommodation: { label: 'Arts, recreation & hospitality', icon: '🎭' },
  emp_public_admin: { label: 'Public administration & defence', icon: '🏛️' },
  emp_other: { label: 'Other services', icon: '🧰' },
}

const CIT_KEYS = Object.keys(CITIZEN_META)
const YIELD_SLOTS: { y: YieldKey; dx: number; dy: number }[] = [
  { y: 'gold', dx: -14, dy: 4 }, { y: 'production', dx: 0, dy: 4 }, { y: 'food', dx: 14, dy: 4 },
  { y: 'science', dx: -14, dy: 16 }, { y: 'culture', dx: 0, dy: 16 }, { y: 'faith', dx: 14, dy: 16 },
]

// ---------------------------------------------------------------------------------------------
// Data helpers (memoised per CountryData)
// ---------------------------------------------------------------------------------------------

/**
 * Citizen classes whose figure means something different in one country, so the class is not
 * compared across countries (the city-vs-national bar stays: both sides use the same survey).
 * Sri Lanka's LFS covers the household population only, so soldiers living in barracks are
 * missing (≈41K vs ≈317K personnel, World Bank); the US ACS count includes group quarters.
 */
const NOT_COMPARABLE: Partial<Record<CountryId, Record<string, string>>> = {
  lka: { cit_soldiers: 'Household-resident armed forces only (LFS); not comparable with the US' },
}
const CROSS_COUNTRY_SKIP = new Set(['cit_soldiers'])

/** h3 → city whose ring contains it (closest centre wins where rings overlap). */
const ringCache = new WeakMap<CountryData, Map<string, { city: City; dist: number }>>()
function ringIndex(d: CountryData) {
  let m = ringCache.get(d)
  if (!m) {
    m = new Map()
    for (const c of d.cities) {
      for (const t of c.tiles) {
        const prev = m.get(t.h3)
        if (!prev || t.d < prev.dist) m.set(t.h3, { city: c, dist: t.d })
      }
    }
    ringCache.set(d, m)
  }
  return m
}

/**
 * National citizen-class mix: Σ tiles cit_k / Σ tiles Σ_k cit_k (the same basis as a city's
 * `citizens`, so the two are directly comparable). null while metrics.bin is loading or without labour data.
 */
const natCache = new WeakMap<CountryData, { shares: Record<string, number>; workers: number; employed: number | null } | null>()
function nationalCitizenMix(d: CountryData) {
  if (natCache.has(d)) return natCache.get(d)!
  const keys = CIT_KEYS.filter((k) => d.metricByKey.has(k))
  let res: { shares: Record<string, number>; workers: number; employed: number | null } | null = null
  if (keys.length) {
    const sums: Record<string, number> = {}
    let tot = 0
    for (const k of keys) {
      const col = column(d, k)!
      let s = 0
      for (let i = 0; i < col.length; i++) { const v = col[i]; if (v > 0) s += v }
      sums[k] = s
      tot += s
    }
    let employed: number | null = null
    const emp = column(d, 'employed')
    if (emp) { employed = 0; for (let i = 0; i < emp.length; i++) if (emp[i] > 0) employed += emp[i] }
    if (tot > 0) res = { shares: Object.fromEntries(keys.map((k) => [k, sums[k] / tot])), workers: tot, employed }
  }
  natCache.set(d, res)
  return res
}

function largestRemainder(shares: Record<string, number>, total: number): Record<string, number> {
  const s = Object.values(shares).reduce((a, v) => a + Math.max(0, v), 0)
  const out: Record<string, number> = {}
  if (s <= 0 || total <= 0) return out
  const raw = Object.entries(shares).map(([k, v]) => [k, (Math.max(0, v) / s) * total] as const)
  raw.forEach(([k, v]) => { out[k] = Math.floor(v) })
  let rest = total - Object.values(out).reduce((a, v) => a + v, 0)
  for (const [k] of [...raw].sort((a, b) => (b[1] - Math.floor(b[1])) - (a[1] - Math.floor(a[1])))) {
    if (rest-- <= 0) break
    out[k] += 1
  }
  return out
}

function sumValues(r: Record<string, number> | null | undefined) {
  return r ? Object.values(r).reduce((a, v) => a + (Number.isFinite(v) && v > 0 ? v : 0), 0) : 0
}

/** Largest citizen class, ignoring classes that are not comparable across the two countries. */
function topCitizen(city: City) {
  const tot = sumValues(city.citizens)
  if (!tot) return null
  const top = Object.entries(city.citizens).filter(([k]) => !CROSS_COUNTRY_SKIP.has(k)).sort((a, b) => b[1] - a[1])[0]
  return top ? { key: top[0], share: top[1] / tot } : null
}

function tileRGB(t: CityTile): RGB {
  let c: RGB = TERRAIN_META[t.terrain]?.rgb ?? [120, 120, 120]
  const fm = FEATURE_META[t.feature ?? '']
  if (fm?.rgb) c = mix(c, fm.rgb, t.feature === 'urban' ? 0.55 : 0.6)
  if (t.hills) c = shade(c, 0.86)
  return c
}

function metricSources(d: CountryData, keys: string[]): string[] {
  const out: string[] = []
  for (const k of keys) for (const s of d.metricByKey.get(k)?.sources ?? []) if (!out.includes(s)) out.push(s)
  return out
}

/** Further provenance fields a cities.json may carry (pipeline: domains/national.py metros_*). */
interface CityProvenance {
  official_pop_scope?: string | null
  official_pop_note?: string | null
  official_pop_sources?: string[] | null
  official_gdp_sources?: string[] | null
}

interface OfficialPop {
  /** row label, e.g. "Metro area (Census MSA, 2025)" */
  label: string
  /** list badge prefix, e.g. "metro" / "city proper" */
  short: string
  /** what the number covers (tooltip) */
  scope: string
  note: string | null
  sources: string[]
}

/**
 * The "official" population differs in kind between the countries: US cities carry Census MSA
 * (metro) estimates; Sri Lankan cities only a Wikidata P1082 statement for the city proper
 * (municipal / urban council area, various years), which for Colombo is the UN WUP projection.
 */
/**
 * Row label and list badge from the exported `population_scope` ("metro area (Census MSA)" →
 * "Metro area (Census MSA, 2025)" / "metro area"), or null when the export has none.
 */
function scopeLabels(scope: string | null | undefined, yr: number | null | undefined): { label: string; short: string } | null {
  const t = scope?.trim()
  if (!t) return null
  const m = t.match(/^(.*?)\s*\((.*)\)\s*$/)
  const head = (m ? m[1] : t).trim()
  const cap = head.charAt(0).toUpperCase() + head.slice(1)
  return { label: m ? `${cap} (${m[2]}${yr ? `, ${yr}` : ''})` : `${cap}${yr ? ` (${yr})` : ''}`, short: head.toLowerCase() }
}

function officialPop(d: CountryData, city: City, national: NationalIndicator[]): OfficialPop | null {
  if (!city.official_pop) return null
  const x = city as City & CityProvenance
  const yr = city.official_pop_year
  const exported = scopeLabels(city.population_scope, yr)
  const exportedSources = x.official_pop_sources ?? city.population_sources ?? null
  // the exported population_note is a full description of the figure: it replaces the built-in one
  const exportedNote = city.population_note?.trim() || null
  if (d.id === 'usa') {
    return {
      label: exported?.label ?? `Metro area (Census MSA${yr ? `, ${yr}` : ''})`,
      short: exported?.short ?? 'metro',
      scope: x.official_pop_scope ?? exportedNote ?? 'Metropolitan Statistical Area population, Census Bureau population estimates (OMB 2023 delineation).',
      note: x.official_pop_note ?? null,
      sources: exportedSources?.length ? exportedSources : ['census_pep_cbsa_2025', 'census_delineation_2023'],
    }
  }
  if (exportedNote) {
    return {
      label: exported?.label ?? `City proper (Wikidata${yr ? `, ${yr}` : ''})`,
      short: exported?.short ?? 'city proper',
      scope: x.official_pop_scope ?? exportedNote,
      note: x.official_pop_note ?? null,
      sources: exportedSources?.length ? [...exportedSources] : ['wikidata'],
    }
  }
  let note = x.official_pop_note ?? null
  const sources = exportedSources?.length ? [...exportedSources] : ['wikidata']
  if (!note && yr != null) {
    // Wikidata often repeats the UN World Urbanization Prospects projection for the largest city
    const s = national.find((r) => r.code === 'EN.URB.LCTY')?.LKA?.series
    const un = s?.find(([y]) => y === yr)?.[1]
    if (un && Math.abs(city.official_pop - un) <= 0.005 * un) {
      note = `Equals the UN World Urbanization Prospects 2018 projection for ${yr} (World Bank EN.URB.LCTY), not a census count.`
      if (!sources.includes('worldbank_wdi')) sources.push('worldbank_wdi')
    }
  }
  return {
    label: exported?.label ?? `City proper (Wikidata${yr ? `, ${yr}` : ''})`,
    short: exported?.short ?? 'city proper',
    scope: x.official_pop_scope ?? 'Population statement on the city’s Wikidata item (P1082), usually the municipal / urban council area (Wikidata does not record which); not a metro area, and years differ by city. Not comparable with US metro figures — compare the tile-ring populations instead.',
    note,
    sources,
  }
}

interface Resolved { d: CountryData; city: City; hex: string | null }

function resolveCity(app: AppData, sel: Selection | null): Resolved | null {
  if (!sel) return null
  const d = app.countries[sel.country]
  if (!d) return null
  if (sel.kind === 'city') {
    const city = d.cities.find((c) => c.id === sel.id)
    return city ? { d, city, hex: null } : null
  }
  if (sel.kind === 'hex') {
    const hit = ringIndex(d).get(sel.id)
    return hit ? { d, city: hit.city, hex: sel.id } : null
  }
  return null
}

// ---------------------------------------------------------------------------------------------
// Small atoms
// ---------------------------------------------------------------------------------------------

function SizeBadge({ size, country, big }: { size: number; country: CountryId; big?: boolean }) {
  return (
    <span className={`cityview-size ${country} ${big ? 'big' : ''}`} title={`City size ${size}`} aria-label={`City size ${size}`}>
      {size}
    </span>
  )
}

function CountryChip({ c }: { c: CountryId }) {
  return <span className={`chip ${c}`}>{COUNTRY_META[c].short}</span>
}

// ---------------------------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------------------------

export function CityView({ app }: { app: AppData }) {
  const selection = useStore((s) => s.selection)
  const [listFor, setListFor] = useState<Selection | null | undefined>(undefined)
  const { lka, usa } = app.countries
  const current = useMemo(() => resolveCity(app, selection), [lka, usa, selection]) // eslint-disable-line react-hooks/exhaustive-deps
  const rootRef = useRef<HTMLDivElement>(null)
  const showList = !current || listFor === selection
  const key = showList ? 'list' : `${current!.d.id}:${current!.city.id}`

  useEffect(() => {
    rootRef.current?.closest('.panel-body')?.scrollTo({ top: 0 })
  }, [key])

  return (
    <div className="cityview" ref={rootRef}>
      {showList || !current
        ? <CityList app={app} />
        : <CityScreen app={app} d={current.d} city={current.city} hex={current.hex} onBack={() => setListFor(selection)} />}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// City list
// ---------------------------------------------------------------------------------------------

function CityList({ app }: { app: AppData }) {
  const [filter, setFilter] = useState<'all' | CountryId>('all')
  const national = useNational()
  const { lka, usa } = app.countries
  const rows = useMemo(() => {
    const out: { d: CountryData; c: City; rank: number; n: number }[] = []
    for (const cc of COUNTRIES) {
      const d = app.countries[cc]
      if (!d) continue
      const s = citiesByPop(d)
      s.forEach((c, i) => out.push({ d, c, rank: i + 1, n: s.length }))
    }
    return out.sort((a, b) => (b.c.pop || 0) - (a.c.pop || 0))
  }, [lka, usa]) // eslint-disable-line react-hooks/exhaustive-deps
  const shown = rows.filter((r) => filter === 'all' || r.d.id === filter)
  const radius = rows[0]?.c.radius ?? 3
  const nTiles = 3 * radius * (radius + 1) + 1

  return (
    <>
      <h2 className="section-title cityview-title">Cities</h2>
      <p className="cityview-lede">
        Each metro is a Civ city: its most populous tile plus a ring of radius {radius} ({nTiles} tiles). Where two rings
        overlap, each shared tile belongs only to the nearer centre, so nothing is counted in two cities.
        City size grows with population<sup>0.4</sup> ÷ 20, so a city 10× larger is about 2.5× the size.
        The big number is the tile-ring population; the small one is the official figure — a US metro area, but only the
        city proper for Sri Lanka.
      </p>
      <div className="cityview-seg" role="group" aria-label="Filter cities by country">
        {(['all', 'lka', 'usa'] as const).map((f) => (
          <button key={f} className={filter === f ? 'active' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === 'all' ? 'Both' : COUNTRY_META[f].name}
          </button>
        ))}
      </div>
      {!usa && filter !== 'lka' && <p className="muted cityview-note">Loading United States…</p>}
      {shown.length === 0 && (usa || filter === 'lka') && (
        <p className="muted cityview-note">No City View cities in this data export (cities.json is empty).</p>
      )}
      <ul className="cityview-list">
        {shown.map(({ d, c, rank, n }) => {
          const off = officialPop(d, c, national)
          return (
          <li key={`${d.id}:${c.id}`}>
            <button className="cityview-li" onClick={() => openCity(d.id, c)} aria-label={`${c.name}, ${COUNTRY_META[d.id].name}: size ${c.size}, ${fmtNumber(c.pop)} people`}>
              <SizeBadge size={c.size} country={d.id} />
              <span className="cityview-li-main">
                <span className="cityview-li-name">
                  {c.name} <CountryChip c={d.id} />
                  <small className="muted">#{rank} of {n}</small>
                </span>
                <YieldRow values={c.yields} size={11} />
              </span>
              <span className="cityview-li-pop">
                <b>{fmtNumber(c.pop)}</b>
                <small title={off ? `${off.label}: ${off.scope}` : undefined}>{off ? `${off.short} ${fmtNumber(c.official_pop)}` : 'tile-ring pop.'}</small>
              </span>
            </button>
          </li>
          )
        })}
      </ul>
    </>
  )
}

// ---------------------------------------------------------------------------------------------
// City screen
// ---------------------------------------------------------------------------------------------

function CityScreen({ app, d, city, hex, onBack }: { app: AppData; d: CountryData; city: City; hex: string | null; onBack: () => void }) {
  const idx = app.index!
  const { lka, usa } = app.countries
  const all = useMemo(() => {
    const out: { d: CountryData; c: City }[] = []
    for (const cc of COUNTRIES) { const x = app.countries[cc]; if (x) citiesByPop(x).forEach((c) => out.push({ d: x, c })) }
    return out
  }, [lka, usa]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="cityview-nav">
        <button className="btn" onClick={onBack}>← All cities</button>
        <select
          className="input cityview-jump"
          aria-label="Jump to city"
          value={`${d.id}:${city.id}`}
          onChange={(e) => {
            const hit = all.find((x) => `${x.d.id}:${x.c.id}` === e.target.value)
            if (hit) openCity(hit.d.id, hit.c)
          }}
        >
          {COUNTRIES.filter((cc) => app.countries[cc]).map((cc) => (
            <optgroup key={cc} label={COUNTRY_META[cc].name}>
              {all.filter((x) => x.d.id === cc).map((x) => (
                <option key={x.c.id} value={`${cc}:${x.c.id}`}>{x.c.name} (size {x.c.size})</option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      <CityBanner d={d} city={city} />
      <CityRing d={d} city={city} idx={idx} selectedHex={hex} />
      <CityCitizens d={d} city={city} />
      <CityIndustries d={d} city={city} />
      <CityDistricts d={d} city={city} idx={idx} />
      <SisterCity app={app} d={d} city={city} />
    </>
  )
}

function CityBanner({ d, city }: { d: CountryData; city: City }) {
  const ranked = citiesByPop(d)
  const rank = ranked.findIndex((c) => c.id === city.id) + 1
  const area = useMemo(() => city.tiles.reduce((a, t) => {
    const i = d.hexIndex.get(t.h3)
    let km2 = i != null ? d.hexArea[i] : NaN
    if (!Number.isFinite(km2)) { try { km2 = cellArea(t.h3, 'km2') } catch { km2 = 0 } }
    return a + km2
  }, 0), [d, city])
  const national = useNational()
  const off = officialPop(d, city, national)
  const srcKeys = ['pop', 'gdp_usd']
  const src = metricSources(d, srcKeys)
  // metric vintages can name both countries ("USA 2024; Sri Lanka 2024"): show this city's part only
  const vint = (k: string) => { const v = d.metricByKey.get(k)?.vintage; return v ? countryVintage(v, d.id) : '' }
  const gdpSrc = (city as City & CityProvenance).official_gdp_sources ?? ['bea_cagdp1']
  const ringFull = city.ring_tiles ?? city.tiles.length
  // Sri Lanka: the Census count of the DS division holding the centre (an official, dated count)
  const census = city.census_ds_population ? {
    pop: city.census_ds_population,
    label: `${city.census_ds_name ? `${city.census_ds_name} DS division` : 'DS division'} (Census${city.census_year ? ` ${city.census_year}` : ''})`,
    scope: city.census_scope ?? 'Census of Population and Housing (DCS): population of the DS division containing the city centre.',
    sources: city.census_sources?.length ? city.census_sources : ['dcs_census_portal'],
  } : null
  const overlap = (city.overlap ?? []).filter((o) => o.tiles > 0)
  return (
    <section className="card cityview-banner" aria-label={`${city.name} overview`}>
      <div className="cityview-banner-head">
        <SizeBadge size={city.size} country={d.id} big />
        <div className="cityview-banner-name">
          <h2>{city.name}</h2>
          <div className="row">
            <CountryChip c={d.id} />
            <span className="muted">{COUNTRY_META[d.id].name} · #{rank} of {ranked.length} City View metros</span>
          </div>
        </div>
      </div>
      <div className="cityview-banner-yields" aria-label="City yields (sum of tile yields)">
        <YieldRow values={city.yields} size={16} />
      </div>
      <div className="kv cityview-kv">
        <span className="k">Population · tile ring{vint('pop') ? ` (${vint('pop')})` : ''}</span>
        <span>{fmtNumber(city.pop)}</span>
        <span className="k">Ring</span>
        <span title={ringFull > city.tiles.length ? `${ringFull} tiles lie within ${city.radius} steps of the centre; ${ringFull - city.tiles.length} of them belong to a nearer city` : undefined}>
          {ringFull > city.tiles.length ? `${city.tiles.length} of ${ringFull} tiles` : `${city.tiles.length} tiles`} · {fmtArea(area)}
        </span>
        {off ? (
          <>
            <span className="k" title={off.scope}>{off.label}</span>
            <span title={off.scope}>{fmtNumber(city.official_pop)}</span>
          </>
        ) : null}
        {census ? (
          <>
            <span className="k" title={census.scope}>{census.label}</span>
            <span title={census.scope}>{fmtNumber(census.pop)}</span>
          </>
        ) : null}
        <span className="k">GDP · tile ring{vint('gdp_usd') ? ` (${vint('gdp_usd')})` : ''}</span>
        <span>{city.gdp_usd != null ? fmtUsd(city.gdp_usd) : <span className="muted">building…</span>}</span>
        <span className="k">GDP per capita</span>
        <span>{city.gdp_per_capita != null ? fmtUsd(city.gdp_per_capita) : <span className="muted">–</span>}</span>
        {city.official_gdp_usd ? (
          <>
            <span className="k" title="Sum of BEA county GDP over the metro area's member counties">Metro GDP (BEA{city.official_gdp_year ? `, ${city.official_gdp_year}` : ''})</span>
            <span>{fmtUsd(city.official_gdp_usd)}</span>
          </>
        ) : null}
      </div>
      {overlap.length > 0 && (
        <p className="cityview-overlap">
          {overlap.map((o, k) => {
            const other = d.cities.find((c) => c.id === o.id)
            return (
              <span key={o.id}>
                {k > 0 && '; '}
                {o.tiles} ring tile{o.tiles === 1 ? '' : 's'} ({fmtNumber(o.pop)} people) belong{o.tiles === 1 ? 's' : ''} to nearer{' '}
                {other ? <button className="cityview-link" onClick={() => openCity(d.id, other)}>{o.name}</button> : o.name}
              </span>
            )
          })}
          , so {overlap.length === 1 && overlap[0].tiles === 1 ? 'it counts' : 'they count'} there and not in {city.name}’s population, GDP, yields or citizens.
        </p>
      )}
      {src.length > 0 && <div className="cityview-src"><span className="cityview-srclab">Tile ring</span><SourceChips ids={src} /></div>}
      {(off || census) && (
        <div className="cityview-official">
          {off && <p><b>{off.label}:</b> {off.scope}{off.note ? ` ${off.note}` : ''}</p>}
          {census && <p><b>{census.label}:</b> {census.scope}</p>}
          <div className="cityview-src">
            <span className="cityview-srclab">Official</span>
            <SourceChips ids={[...(off?.sources ?? []), ...(census?.sources ?? []), ...(city.official_gdp_usd ? gdpSrc : [])]} />
          </div>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Hex ring
// ---------------------------------------------------------------------------------------------

function CityRing({ d, city, idx, selectedHex }: { d: CountryData; city: City; idx: IndexJson; selectedHex: string | null }) {
  const layout = useMemo(() => layoutRing(city.centre, city.tiles.map((t) => t.h3), city.radius), [city])
  const tileBy = useMemo(() => new Map(city.tiles.map((t) => [t.h3, t])), [city])
  const wondersBy = useMemo(() => {
    const m = new Map<string, Wonder[]>()
    for (const w of d.wonders) if (w.h3) m.set(w.h3, [...(m.get(w.h3) ?? []), w])
    return m
  }, [d])
  const [active, setActive] = useState<string | null>(null)
  const pointer = useRef<string>('mouse')
  // touch: first tap previews a tile (tooltip), a second tap on the same tile inspects it
  const armed = useRef(false)
  useEffect(() => setActive(null), [city])

  const choose = (h: string) => useStore.getState().select({ country: d.id, kind: 'hex', id: h }, 'tile')
  const onClick = (h: string) => {
    if (pointer.current === 'touch' && !armed.current) { setActive(h); return }
    choose(h)
  }
  const onKey = (e: KeyboardEvent, h: string) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(h) }
    if (e.key === 'Escape') setActive(null)
  }

  const [vx, vy, vw, vh] = layout.viewBox
  const tiles = layout.cells.filter((c) => !c.ghost)
  const ghosts = layout.cells.filter((c) => c.ghost)
  // stable DOM / keyboard order: centre, then ring by ring, clockwise from north
  const order = useMemo(() => [...layout.cells.filter((c) => !c.ghost)].sort((a, b) =>
    (tileBy.get(a.h3)?.d ?? 0) - (tileBy.get(b.h3)?.d ?? 0) ||
    ((Math.atan2(a.x, -a.y) + 2 * Math.PI) % (2 * Math.PI)) - ((Math.atan2(b.x, -b.y) + 2 * Math.PI) % (2 * Math.PI))), [layout, tileBy])
  // highlight outlines are drawn in an overlay after all tiles so they are never covered
  const rings = [
    { h: city.centre, cls: 'centre', r: HEX_R - 3 },
    ...(active && active !== selectedHex ? [{ h: active, cls: 'hot', r: HEX_R - 2.5 }] : []),
    ...(selectedHex ? [{ h: selectedHex, cls: 'sel', r: HEX_R - 2.5 }] : []),
  ].filter((x) => layout.pos.has(x.h) && tileBy.has(x.h))

  const at = active ? tileBy.get(active) : null
  const ap = active ? layout.pos.get(active) : null
  const above = ap ? (ap.y - vy) / vh > 0.42 : true
  const tipStyle = ap ? {
    left: `clamp(108px, ${(((ap.x - vx) / vw) * 100).toFixed(2)}%, calc(100% - 108px))`,
    top: `${((((above ? ap.y - HEX_R * 0.9 : ap.y + HEX_R * 0.9) - vy) / vh) * 100).toFixed(2)}%`,
  } : undefined

  return (
    <section className="card cityview-ringcard" aria-label="City tiles">
      <h3>City tiles</h3>
      <div className="cityview-ring" onPointerLeave={() => { if (pointer.current !== 'touch') setActive(null) }}>
        <svg viewBox={`${vx} ${vy} ${vw} ${vh}`} role="group" aria-label={`${city.name}: ${tiles.length} tiles, radius ${city.radius}`}>
          {ghosts.map((g) => (
            <polygon key={g.h3} points={hexPoints(g.x, g.y, HEX_R - 1.5)} className="cityview-ghost" aria-hidden />
          ))}
          {order.map((c) => {
            const t = tileBy.get(c.h3)!
            const isCentre = c.h3 === city.centre
            const isSel = c.h3 === selectedHex
            const dm = DISTRICT_META[t.district ?? '']
            const fm = FEATURE_META[t.feature ?? '']
            const ws = wondersBy.get(c.h3)
            const anyYield = (t.yields ?? []).some((v) => v > 0)
            const label = `${isCentre ? 'City centre: ' : `Ring ${t.d}: `}${terrainLabel(t.terrain, t.feature, t.hills)}${dm?.label && t.district ? `, ${dm.label}` : ''}; yields ${YIELDS.map((y, j) => `${YIELD_META[y].label} ${t.yields?.[j] ?? 0}`).join(', ')}; population ${fmtNumber(t.pop)}`
            return (
              <g
                key={c.h3}
                className={`cityview-tile ${isCentre ? 'centre' : ''} ${isSel ? 'sel' : ''} ${active === c.h3 ? 'hot' : ''}`}
                role="button"
                tabIndex={0}
                aria-label={label}
                aria-pressed={isSel}
                onPointerDown={(e) => { pointer.current = e.pointerType; armed.current = active === c.h3 }}
                onPointerEnter={(e) => { pointer.current = e.pointerType; if (e.pointerType !== 'touch') setActive(c.h3) }}
                onFocus={() => setActive(c.h3)}
                onBlur={() => setActive((a) => (a === c.h3 ? null : a))}
                onClick={() => onClick(c.h3)}
                onKeyDown={(e) => onKey(e, c.h3)}
              >
                <polygon points={hexPoints(c.x, c.y)} fill={rgbCss(tileRGB(t))} className="cityview-hex" />
                {t.district && dm?.icon ? (
                  <text x={c.x} y={c.y - 13} className="cityview-emoji">{dm.icon}</text>
                ) : fm?.icon ? (
                  <text x={c.x} y={c.y - 13} className="cityview-emoji faint">{fm.icon}</text>
                ) : null}
                {ws && <text x={c.x + 17} y={c.y - 19} className="cityview-emoji small">{WONDER_ICON[ws[0].category] ?? '★'}</text>}
                {anyYield && (
                  <>
                    <rect x={c.x - 22} y={c.y - 2.5} width={44} height={25} rx={6} className="cityview-plate" />
                    {YIELD_SLOTS.map((s) => {
                      const v = t.yields[YIELDS.indexOf(s.y)] ?? 0
                      return v > 0 ? (
                        <text key={s.y} x={c.x + s.dx} y={c.y + s.dy} className="cityview-yv" fill={YIELD_META[s.y].color}>{v}</text>
                      ) : null
                    })}
                  </>
                )}
              </g>
            )
          })}
          <g aria-hidden className="cityview-overlay">
            {rings.map((x) => {
              const p = layout.pos.get(x.h)!
              return <polygon key={`${x.cls}:${x.h}`} points={hexPoints(p.x, p.y, x.r)} className={`cityview-hexring ${x.cls}`} />
            })}
          </g>
        </svg>
        {at && tipStyle && (
          <div className={`cityview-tip ${above ? 'above' : 'below'}`} style={tipStyle} aria-hidden>
            <div className="cityview-tip-title">
              {t_district(at) ? <>{DISTRICT_META[at.district].icon} {DISTRICT_META[at.district].label}</> : terrainLabel(at.terrain, at.feature, at.hills)}
            </div>
            {t_district(at) && <div className="muted">{terrainLabel(at.terrain, at.feature, at.hills)}</div>}
            <YieldRow values={at.yields ?? []} size={12} />
            <div className="cityview-tip-meta">
              <span>{fmtNumber(at.pop)} people</span>
              <span className="muted">{at.d === 0 ? 'centre tile' : `ring ${at.d}`}</span>
            </div>
            {(wondersBy.get(at.h3) ?? []).map((w) => (
              <div key={w.name} className="cityview-tip-wonder">{WONDER_ICON[w.category] ?? '★'} {w.name}</div>
            ))}
            <div className="cityview-tip-hint">{pointer.current === 'touch' ? 'Tap again to inspect' : 'Click to inspect tile'}</div>
          </div>
        )}
      </div>
      <div className="cityview-ringlegend">
        <span className="cityview-slots" aria-label="Yield number positions: gold, production, food on the top row; science, culture, faith below">
          {YIELD_SLOTS.map((s) => <YieldIcon key={s.y} y={s.y} size={11} />)}
        </span>
        <span>yield slots in each tile</span>
        <span className="cityview-legitem"><span className="cityview-key centre" aria-hidden /> city centre</span>
        {layout.cells.some((c) => c.ghost) && <span className="cityview-legitem"><span className="cityview-key ghost" aria-hidden /> outside map grid</span>}
      </div>
      {layout.method !== 'h3-ij' && (
        <p className="cityview-foot">Layout: {layout.method === 'mixed' ? 'H3 local IJ with lat/lon fallback for some tiles' : 'lat/lon projection'}.</p>
      )}
    </section>
  )
}

function t_district(t: CityTile) {
  return !!t.district && !!DISTRICT_META[t.district]?.icon
}

// ---------------------------------------------------------------------------------------------
// Citizens
// ---------------------------------------------------------------------------------------------

function CityCitizens({ d, city }: { d: CountryData; city: City }) {
  const nat = nationalCitizenMix(d)
  const total = sumValues(city.citizens)
  const src = metricSources(d, CIT_KEYS)
  if (!total) {
    return (
      <section className="card" aria-label="Citizens">
        <h3>Citizens</h3>
        <p className="muted cityview-note">No workforce composition is published for this city’s tiles, so it has no citizens.</p>
      </section>
    )
  }
  const icons = sumValues(city.citizen_icons) > 0 ? city.citizen_icons : largestRemainder(city.citizens, city.size)
  const perCitizen = total / Math.max(1, city.size)
  const groups = CIT_KEYS.filter((k) => (icons[k] ?? 0) > 0)
  const bars = CIT_KEYS
    .map((k) => ({ k, city: (city.citizens[k] ?? 0) / total, nat: nat?.shares[k] }))
    .filter((r) => r.city > 0 || (r.nat ?? 0) > 0)
    .sort((a, b) => b.city - a.city)
  const max = Math.max(...bars.map((b) => Math.max(b.city, b.nat ?? 0)), 0.01)

  return (
    <section className="card" aria-label="Citizens">
      <h3>Citizens</h3>
      <p className="cityview-note">
        <b>{city.size}</b> citizens · 1 citizen ≈ <b>{fmtNumber(perCitizen)}</b> workers ({fmtNumber(total)} classified workers in the ring)
      </p>
      <div className="cityview-cits">
        {groups.map((k) => (
          <div key={k} className="cityview-citgroup" title={`${CITIZEN_META[k].label}: ${CITIZEN_META[k].desc} — ${icons[k]} citizen${icons[k] === 1 ? '' : 's'} ≈ ${fmtNumber((city.citizens[k] ?? 0))} workers`}>
            <span className="cityview-citicons" aria-hidden>{Array.from({ length: icons[k] }, () => CITIZEN_META[k].icon).join('')}</span>
            <span className="cityview-citlabel"><b>{icons[k]}</b> {CITIZEN_META[k].label}</span>
          </div>
        ))}
      </div>
      <div className="cityview-barhead">
        <span>Share of workers</span>
        <span className="cityview-legend-sw"><i className={`sw ${d.id}`} /> {city.name}</span>
        {nat && <span className="cityview-legend-sw"><i className="sw nat" /> {COUNTRY_META[d.id].name}</span>}
      </div>
      <div className="cityview-bars" role="table" aria-label="Citizen class shares, city vs national">
        {bars.map((b) => {
          const lq = b.nat ? b.city / b.nat : NaN
          const caveat = NOT_COMPARABLE[d.id]?.[b.k]
          return (
            <div key={b.k} className={`cityview-bar ${caveat ? 'has-note' : ''}`} role="row" title={caveat ? `${CITIZEN_META[b.k].desc}. ${caveat}.` : CITIZEN_META[b.k].desc}>
              <span className="cityview-bar-label" role="rowheader"><span aria-hidden>{CITIZEN_META[b.k].icon}</span> {CITIZEN_META[b.k].label}{caveat && <sup aria-hidden>†</sup>}</span>
              <span className="cityview-bar-track" role="cell" aria-label={`${fmtPct(b.city * 100)} in ${city.name}${b.nat != null ? `, ${fmtPct(b.nat * 100)} nationally` : ''}${caveat ? `. ${caveat}` : ''}`}>
                <i className={`cityview-bar-city ${d.id}`} style={{ width: `${(b.city / max) * 100}%` }} />
                {b.nat != null && <i className="cityview-bar-nat" style={{ width: `${(b.nat / max) * 100}%` }} />}
              </span>
              <span className="cityview-bar-val" role="cell">
                {fmtPct(b.city * 100)}
                {Number.isFinite(lq) && <small className={lq >= 1.15 ? 'up' : lq <= 0.87 ? 'down' : ''}>{lq.toFixed(1)}×</small>}
              </span>
              {caveat && <span className="cityview-bar-note" aria-hidden>† {caveat}.</span>}
            </div>
          )
        })}
      </div>
      <p className="cityview-foot">
        {nat ? `× = city share ÷ national share (location quotient). National mix = all tiles' citizen classes${nat.employed ? `; classes cover ${fmtPct(Math.min(100, (nat.workers / nat.employed) * 100), 0)} of the employed` : ''}.` : !d.matrixReady ? 'National mix: loading statistics…' : 'No national mix: the citizen classes are not published by tile.'}
      </p>
      {src.length > 0 && <SourceChips ids={src} />}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Industries
// ---------------------------------------------------------------------------------------------

function CityIndustries({ d, city }: { d: CountryData; city: City }) {
  const inds = (city.industries ?? []).filter((r) => Number.isFinite(r.lq))
  const src = metricSources(d, inds.map((r) => r.key))
  if (!inds.length) {
    return (
      <section className="card" aria-label="Top industries">
        <h3>Top industries</h3>
        <p className="muted cityview-note">No industry employment is published for this city’s tiles.</p>
      </section>
    )
  }
  const max = Math.max(2, ...inds.map((r) => r.lq)) * 1.05
  return (
    <section className="card" aria-label="Top industries">
      <h3>Top industries</h3>
      <p className="cityview-note muted">Most over-represented industries: share of jobs in the ring ÷ national share (location quotient).</p>
      <ul className="cityview-inds">
        {inds.map((r) => {
          const meta = INDUSTRY[r.key] ?? { label: d.metricByKey.get(r.key)?.label ?? r.key.replace(/^emp_/, ''), icon: '•' }
          return (
            <li key={r.key} className="cityview-ind">
              <div className="cityview-ind-top">
                <span><span aria-hidden>{meta.icon}</span> {meta.label}</span>
                <span className="cityview-ind-val"><b>{r.lq.toFixed(2)}×</b> <small className="muted">{fmtNumber(r.jobs)} jobs</small></span>
              </div>
              <div className="cityview-ind-track" aria-hidden>
                <i style={{ width: `${(r.lq / max) * 100}%` }} className={r.lq >= 1 ? 'over' : 'under'} />
                <b className="cityview-ind-one" style={{ left: `${(1 / max) * 100}%` }} />
              </div>
            </li>
          )
        })}
      </ul>
      <p className="cityview-foot">Dashed line = national average (1.0×).</p>
      {src.length > 0 && <SourceChips ids={src} />}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Districts & wonders
// ---------------------------------------------------------------------------------------------

function CityDistricts({ d, city, idx }: { d: CountryData; city: City; idx: IndexJson }) {
  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const t of city.tiles) if (t.district) m.set(t.district, (m.get(t.district) ?? 0) + 1)
    for (const k of city.districts ?? []) if (k && !m.has(k)) m.set(k, 0)
    return m
  }, [city])
  const codes = idx.districts.map((x) => x.code).filter((c) => counts.has(c))
  for (const k of counts.keys()) if (!codes.includes(k)) codes.push(k)
  const rules = new Map(idx.districts.map((x) => [x.code, x]))
  const wonders = (city.wonders ?? []).map((name) => ({ name, w: d.wonders.find((w) => w.name === name) }))
  const ring = new Set(city.tiles.map((t) => t.h3))

  return (
    <section className="card" aria-label="Districts and wonders">
      <h3 title="Civ specialist districts (Campus, Harbor, Neighborhood…), not administrative districts">Specialist districts</h3>
      {codes.length === 0 ? <p className="muted cityview-note">No specialist districts in this ring yet.</p> : (
        <ul className="cityview-dists">
          {codes.map((c) => (
            <li key={c}>
              <details>
                <summary title={rules.get(c)?.rule}>
                  <span aria-hidden>{DISTRICT_META[c]?.icon ?? '•'}</span> {DISTRICT_META[c]?.label ?? rules.get(c)?.label ?? c}
                  {(counts.get(c) ?? 0) > 1 && <small className="muted"> ×{counts.get(c)}</small>}
                </summary>
                <p>{rules.get(c)?.rule || 'Rule not documented.'}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
      <h3 className="cityview-h3b">Wonders in the city</h3>
      {wonders.length === 0 ? <p className="muted cityview-note">No wonders within the ring{d.wonders.length ? '' : ` (no wonders in ${COUNTRY_META[d.id].name}’s data export)`}.</p> : (
        <ul className="cityview-wonders">
          {wonders.map(({ name, w }) => (
            <li key={name}>
              <button
                className="cityview-wonder"
                disabled={!w?.h3 || !ring.has(w.h3)}
                onClick={() => w && useStore.getState().select({ country: d.id, kind: 'hex', id: w.h3 })}
                title={w ? `${w.category} #${w.rank}: ${w.rule}` : name}
              >
                <span aria-hidden>{w ? WONDER_ICON[w.category] ?? '★' : '★'}</span>
                <span>{name}</span>
                {w && <small className="muted">{w.category} #{w.rank}</small>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Sister city
// ---------------------------------------------------------------------------------------------

function SisterCity({ app, d, city }: { app: AppData; d: CountryData; city: City }) {
  const [by, setBy] = useState<'rank' | 'pop'>('rank')
  const other: CountryId = d.id === 'lka' ? 'usa' : 'lka'
  const od = app.countries[other]
  const mine = citiesByPop(d)
  const rank = mine.findIndex((c) => c.id === city.id)
  const theirs = od ? citiesByPop(od) : []
  let sister: City | null = null
  if (theirs.length) {
    if (by === 'rank') sister = theirs[Math.min(Math.max(rank, 0), theirs.length - 1)]
    else {
      const lp = Math.log(Math.max(1, city.pop))
      sister = theirs.reduce((b, c) => (Math.abs(Math.log(Math.max(1, c.pop)) - lp) < Math.abs(Math.log(Math.max(1, b.pop)) - lp) ? c : b), theirs[0])
    }
  }

  return (
    <section className="card" aria-label="Sister city">
      <div className="cityview-sister-head">
        <h3>Sister city</h3>
        <div className="cityview-seg small" role="group" aria-label="Match sister city by">
          <button className={by === 'rank' ? 'active' : ''} aria-pressed={by === 'rank'} onClick={() => setBy('rank')}>Same rank</button>
          <button className={by === 'pop' ? 'active' : ''} aria-pressed={by === 'pop'} onClick={() => setBy('pop')}>Closest population</button>
        </div>
      </div>
      {!od ? <p className="muted cityview-note">Loading {COUNTRY_META[other].name}…</p> : !sister ? (
        <p className="muted cityview-note">No cities for {COUNTRY_META[other].name} yet.</p>
      ) : (
        <SisterTable a={{ d, c: city }} b={{ d: od, c: sister }} />
      )}
      {sister && od && (
        <button className="btn primary cityview-open" onClick={() => openCity(other, sister!)}>
          Open {sister.name} →
        </button>
      )}
    </section>
  )
}

function SisterTable({ a, b }: { a: { d: CountryData; c: City }; b: { d: CountryData; c: City } }) {
  const rankOf = (x: { d: CountryData; c: City }) => {
    const s = citiesByPop(x.d)
    return `#${s.findIndex((c) => c.id === x.c.id) + 1} of ${s.length}`
  }
  const cit = (x: City) => {
    const t = topCitizen(x)
    return t ? <><span aria-hidden>{CITIZEN_META[t.key]?.icon}</span> {CITIZEN_META[t.key]?.label ?? t.key} <small className="muted">{fmtPct(t.share * 100, 0)}</small></> : <span className="muted">–</span>
  }
  const ind = (x: City) => {
    const t = (x.industries ?? [])[0]
    return t ? <>{INDUSTRY[t.key]?.label ?? t.key} <small className="muted">{t.lq.toFixed(1)}×</small></> : <span className="muted">–</span>
  }
  const win = (va: number | null | undefined, vb: number | null | undefined, side: 'a' | 'b') => {
    if (va == null || vb == null || !Number.isFinite(va) || !Number.isFinite(vb) || va === vb) return ''
    return (side === 'a' ? va > vb : vb > va) ? 'lead' : ''
  }
  const rows: { k: string; label: ReactNode; a: ReactNode; b: ReactNode; va?: number | null; vb?: number | null }[] = [
    { k: 'rank', label: 'Rank in country', a: rankOf(a), b: rankOf(b) },
    { k: 'size', label: 'City size', a: <SizeBadge size={a.c.size} country={a.d.id} />, b: <SizeBadge size={b.c.size} country={b.d.id} />, va: a.c.size, vb: b.c.size },
    { k: 'pop', label: 'Population (ring)', a: fmtNumber(a.c.pop), b: fmtNumber(b.c.pop), va: a.c.pop, vb: b.c.pop },
    { k: 'gdppc', label: 'GDP per capita', a: fmtUsd(a.c.gdp_per_capita), b: fmtUsd(b.c.gdp_per_capita), va: a.c.gdp_per_capita, vb: b.c.gdp_per_capita },
    ...YIELDS.map((y) => ({
      k: y, label: <><YieldIcon y={y} size={13} /> {YIELD_META[y].label}</>, a: a.c.yields?.[y] ?? 0, b: b.c.yields?.[y] ?? 0, va: a.c.yields?.[y], vb: b.c.yields?.[y],
    })),
    { k: 'cit', label: 'Top citizen class', a: cit(a.c), b: cit(b.c) },
    { k: 'ind', label: 'Top industry (LQ)', a: ind(a.c), b: ind(b.c) },
  ]
  return (
    <table className="data cityview-sister">
      <thead>
        <tr>
          <th scope="col"><span className="sr-only">Measure</span></th>
          <th scope="col" className="num">{a.c.name} <CountryChip c={a.d.id} /></th>
          <th scope="col" className="num">{b.c.name} <CountryChip c={b.d.id} /></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.k}>
            <th scope="row">{r.label}</th>
            <td className={`num ${win(r.va, r.vb, 'a')}`}>{r.a}</td>
            <td className={`num ${win(r.va, r.vb, 'b')}`}>{r.b}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
