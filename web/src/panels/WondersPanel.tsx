import { useEffect, useMemo, useRef, useState } from 'react'
import { COUNTRY_META } from '../civ'
import { SourceChips } from '../components/SourceChip'
import { useNational, type AppData } from '../data/useData'
import { fmtInt, fmtNumber, fmtUsd } from '../format'
import { WONDER_ICON } from '../map/MapPane'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId, type NationalIndicator, type Wonder, type WonderCategory } from '../types'
import { SITE_ZOOM, flyToCountry } from './cityViewNav'
import { countryVintage } from './explorerData'
import { wonderUnit } from './tilePanelStats'
import './WondersPanel.css'

const CATEGORY_ORDER = Object.keys(WONDER_ICON)
const UNIT_SHORT: Record<string, string> = {
  tonnes: 't', passengers: 'pax', enplanements: 'enpl.', 'Wikipedia editions': 'editions', visitors: 'visitors', works: 'works', MW: 'MW',
}
const UNIT_LONG: Record<string, string> = {
  enplanements: 'enplanements (passenger boardings only — about half of all arriving + departing passengers)',
}

/**
 * Where each ranked category's value lives in points.json. A country with no wonders in a category
 * AND no value for any of its facilities has no data — that is not the same as "none qualify".
 */
const CATEGORY_DATA: Record<string, { layer: string; fields: string[]; what: string }> = {
  'Great Airport': { layer: 'airports', fields: ['airport_pax', 'passengers'], what: 'open airport-level passenger statistics' },
  'Great Port': { layer: 'ports', fields: ['port_cargo_t', 'cargo_tonnes'], what: 'port cargo statistics' },
  'Great Power Station': { layer: 'power_plants', fields: ['capacity_mw'], what: 'power-station capacities' },
  'Natural Wonder': { layer: 'parks', fields: ['park_visitors', 'visitors'], what: 'national-park visitor counts' },
  'Great University': { layer: 'universities', fields: ['works_5y'], what: 'research-output counts' },
  'Holy Wonder': { layer: 'holy_sites', fields: ['sitelinks'], what: 'holy-site documentation counts' },
}

/** The pipeline's record of a category for one country (meta.json `wonder_categories`). */
function catMeta(d: CountryData | undefined, cat: string): WonderCategory | undefined {
  return d?.meta.wonder_categories?.find((x) => x.category === cat)
}

function noData(d: CountryData | undefined, cat: string, n: number): boolean {
  if (!d || n > 0) return false
  const cm = catMeta(d, cat)
  if (cm) return cm.status === 'no_data'
  // older exports without wonder_categories: infer from the facilities' value columns
  const cd = CATEGORY_DATA[cat]
  if (!cd) return false
  const rows = d.points?.[cd.layer]?.rows ?? []
  return !rows.some((r) => cd.fields.some((f) => r[f] != null && Number.isFinite(Number(r[f]))))
}

/** One map site can hold several titles (Yellowstone: Natural Wonder + World Heritage): count it once. */
const siteKey = (w: Wonder) => (w.wikidata ? `wd:${String(w.wikidata)}` : `${w.h3}|${w.name}`)

function NoDataNote({ c, d, cat, national }: { c: CountryId; d?: CountryData; cat: string; national: NationalIndicator[] }) {
  const cm = catMeta(d, cat)
  const what = CATEGORY_DATA[cat]?.what ?? 'ranking data'
  // national context for the one known gap: passengers carried by the country's airlines (World Bank)
  const air = cat === 'Great Airport' ? national.find((r) => r.code === 'IS.AIR.PSGR')?.[c === 'lka' ? 'LKA' : 'USA'] : undefined
  return (
    <div className="wonders-nodata">
      <p>
        {cm?.note ?? `No ${what} are published for ${COUNTRY_META[c].name}, so no ${cat === 'Great Airport' ? 'airport' : 'site'} can be ranked.`}
        {' '}This is missing data, not a zero{cm && cm.candidates > 0 ? `: ${fmtInt(cm.candidates)} candidates are on the map, none with a${/^[aeiou]/i.test(cm.unit) ? 'n' : ''} ${cm.unit || 'value'} figure` : ''}.
        {air?.value != null && air.year != null && (
          <> For scale: {COUNTRY_META[c].name}-registered airlines carried {fmtNumber(air.value)} passengers in {air.year} (World Bank). <SourceChips ids={['worldbank_wdi']} /></>
        )}
      </p>
      {cm && cm.sources.length > 0 && <div className="wonders-src"><span className="muted">Candidates:</span> <SourceChips ids={cm.sources} /></div>}
    </div>
  )
}

/** "top 10 of 917 candidates" — how the category was ranked for this country. */
function coverage(cm: WonderCategory | undefined): { text: string; title: string } | null {
  if (!cm || cm.status === 'no_data') return null
  const vint = cm.metric_vintage ? ` (${cm.metric_vintage})` : ''
  if (cm.cap == null) return { text: 'every listed site', title: `All ${fmtInt(cm.candidates)} qualifying sites are wonders${vint}` }
  return {
    text: `top ${cm.cap} of ${fmtInt(cm.candidates)}`,
    title: `Top ${cm.cap} of ${fmtInt(cm.candidates)} candidates; ${fmtInt(cm.with_value)} have a ${cm.unit || 'ranking'} value${vint}`,
  }
}
const SHOW_KIND = new Set(['World Heritage', 'Holy Wonder'])
const COLLAPSED = 5

interface Group { cat: string; rules: string[]; by: Partial<Record<CountryId, Wonder[]>> }

function cap(s: string) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s }

function extrasOf(w: Wonder): string[] {
  const out: string[] = []
  if (w.primary_fuel) out.push(cap(String(w.primary_fuel)))
  if (w.iata) out.push(`IATA ${w.iata}`)
  if (w.inscribed) out.push(`Inscribed ${w.inscribed}`)
  if (w.rd_usd != null && Number(w.rd_usd) > 0) out.push(`R&D ${fmtUsd(Number(w.rd_usd))}/yr`)
  if (w.kind && SHOW_KIND.has(w.category) && !/^(heritage|holy_sites?|site)$/i.test(w.kind)) out.push(cap(w.kind.replace(/_/g, ' ')))
  return out
}

function valueOf(w: Wonder) {
  if (w.value == null || !Number.isFinite(w.value)) return null
  const yr = (w.visitors_year ?? w.year) as number | string | undefined
  const unit = wonderUnit(w)
  return {
    text: `${fmtNumber(w.value)}${unit ? ` ${UNIT_SHORT[unit] ?? unit}` : ''}`,
    title: `${fmtInt(w.value)} ${UNIT_LONG[unit] ?? unit}${yr ? ` (${yr})` : ''}`,
    year: yr != null && yr !== '' ? String(yr) : null,
  }
}

function go(country: CountryId, w: Wonder) {
  if (w.h3) useStore.getState().select({ country, kind: 'hex', id: w.h3 }, 'wonders')
  if (Number.isFinite(w.lon) && Number.isFinite(w.lat)) flyToCountry(country, w.lon, w.lat, SITE_ZOOM[country])
}

export function WondersPanel({ app }: { app: AppData }) {
  const selection = useStore((s) => s.selection)
  const [filter, setFilter] = useState<'all' | CountryId>('all')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const rootRef = useRef<HTMLDivElement>(null)
  const national = useNational()
  const { lka, usa } = app.countries

  const groups = useMemo(() => {
    const m = new Map<string, Group>()
    const group = (cat: string) => {
      let g = m.get(cat)
      if (!g) { g = { cat, rules: [], by: {} }; m.set(cat, g) }
      return g
    }
    for (const cc of COUNTRIES) {
      const d = app.countries[cc]
      if (!d) continue
      // the pipeline's category list carries the rule even where a country has no wonder in it
      for (const cm of d.meta.wonder_categories ?? []) {
        const g = group(cm.category)
        if (cm.rule && !g.rules.includes(cm.rule)) g.rules.push(cm.rule)
      }
      for (const w of d.wonders) {
        const g = group(w.category)
        if (w.rule && !g.rules.includes(w.rule)) g.rules.push(w.rule)
        ;(g.by[cc] ??= []).push(w)
      }
    }
    for (const g of m.values()) for (const k of Object.keys(g.by) as CountryId[]) g.by[k]!.sort((a, b) => a.rank - b.rank)
    const ord = (c: string) => { const i = CATEGORY_ORDER.indexOf(c); return i < 0 ? 99 : i }
    return [...m.values()].sort((a, b) => ord(a.cat) - ord(b.cat) || a.cat.localeCompare(b.cat))
  }, [lka, usa]) // eslint-disable-line react-hooks/exhaustive-deps

  const isSel = (c: CountryId, w: Wonder) =>
    !!selection && selection.country === c &&
    ((selection.kind === 'hex' && selection.id === w.h3) || (selection.kind === 'wonder' && selection.id === w.name))

  // the category holding the selected wonder is always expanded, and its row scrolled into view
  const selCat = useMemo(() => {
    if (!selection) return null
    for (const g of groups) for (const cc of COUNTRIES) if (g.by[cc]?.some((w) => isSel(cc, w))) return `${g.cat}:${cc}`
    return null
  }, [groups, selection]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    rootRef.current?.querySelector('.wonders-row.sel')?.scrollIntoView({ block: 'nearest' })
  }, [selection, selCat])

  const total = groups.reduce((a, g) => a + (g.by.lka?.length ?? 0) + (g.by.usa?.length ?? 0), 0)
  const shownCountries = COUNTRIES.filter((c) => filter === 'all' || filter === c)

  return (
    <div className="wonders" ref={rootRef}>
      <h2 className="section-title wonders-title">Wonders</h2>
      <p className="wonders-lede">
        Real landmarks promoted to Civ wonders by published ranking rules — every list below states its rule and source.
      </p>

      {total === 0 ? (
        <section className="card wonders-empty" aria-label="No wonders yet">
          <h3>No wonders yet — data still building</h3>
          <p className="muted">
            Wonders are picked by rules once their data domains finish: national parks by visitors, universities by research
            output, ports by cargo volume, power stations by capacity, airports by passenger boardings, every UNESCO World Heritage
            site, and the most widely documented holy sites.
          </p>
          <div className="wonders-await">
            {CATEGORY_ORDER.map((c) => (
              <span key={c} className="chip"><span aria-hidden>{WONDER_ICON[c]}</span> {c}</span>
            ))}
          </div>
          {!usa && <p className="muted">Loading United States…</p>}
        </section>
      ) : (
        <>
          <WonderRace groups={groups} lka={lka} usa={usa} />
          <div className="wonders-seg" role="group" aria-label="Show wonders for">
            {(['all', 'lka', 'usa'] as const).map((f) => (
              <button key={f} className={filter === f ? 'active' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f === 'all' ? 'Both' : COUNTRY_META[f].name}
              </button>
            ))}
          </div>
          {!usa && filter !== 'lka' && <p className="muted wonders-note">Loading United States…</p>}
          {groups.map((g) => {
            const present = shownCountries.filter((c) => app.countries[c])
            if (!present.some((c) => g.by[c]?.length) && filter !== 'all') return null
            return (
              <section key={g.cat} className="card wonders-cat" aria-label={g.cat}>
                <h3>
                  <span className="wonders-icon" aria-hidden>{WONDER_ICON[g.cat] ?? '★'}</span>
                  <span>{g.cat}</span>
                </h3>
                {g.rules.map((r) => <p key={r} className="wonders-rule"><b>Rule:</b> {r}</p>)}
                {g.cat === 'Great Airport' && !g.rules.some((r) => /enplane|boarding/i.test(r)) && (
                  <p className="wonders-rule">US values are FAA enplanements: passengers boarding only, about half of all passenger movements.</p>
                )}
                {present.map((c) => {
                  const list = g.by[c] ?? []
                  const key = `${g.cat}:${c}`
                  const open = expanded.has(key) || selCat === key
                  const rows = open ? list : list.slice(0, COLLAPSED)
                  const srcs = [...new Set(list.flatMap((w) => w.sources ?? []))]
                  const missing = noData(app.countries[c], g.cat, list.length)
                  const cov = coverage(catMeta(app.countries[c], g.cat))
                  return (
                    <div key={c} className="wonders-country">
                      <div className="wonders-country-head">
                        <span className={`chip ${c}`}>{COUNTRY_META[c].short}</span>
                        <span className="muted">{list.length ? `${list.length} wonder${list.length === 1 ? '' : 's'}` : missing ? 'no data' : 'none qualify'}</span>
                        {cov && <span className="muted wonders-cov" title={cov.title}>· {cov.text}</span>}
                      </div>
                      {missing && <NoDataNote c={c} d={app.countries[c]} cat={g.cat} national={national} />}
                      {list.length > 0 && (
                        <ol className="wonders-rows">
                          {rows.map((w) => {
                            const v = valueOf(w)
                            const ex = extrasOf(w)
                            const sel = isSel(c, w)
                            return (
                              <li key={`${w.rank}:${w.name}`}>
                                <button
                                  className={`wonders-row ${sel ? 'sel' : ''}`}
                                  onClick={() => go(c, w)}
                                  aria-current={sel ? 'true' : undefined}
                                  title={`${w.name}${v ? ` — ${v.title}` : ''}. Click to fly there.`}
                                >
                                  <span className={`wonders-rank r${Math.min(w.rank, 4)}`}>{w.rank}</span>
                                  <span className="wonders-name">
                                    <span>{w.name}</span>
                                    {ex.length > 0 && <small>{ex.join(' · ')}</small>}
                                  </span>
                                  {v && (
                                    <span className="wonders-val">
                                      <b>{v.text}</b>
                                      {v.year && <small>{v.year}</small>}
                                    </span>
                                  )}
                                </button>
                              </li>
                            )
                          })}
                        </ol>
                      )}
                      {list.length > COLLAPSED && selCat !== key && (
                        <button
                          className="btn wonders-more"
                          aria-expanded={open}
                          onClick={() => setExpanded((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n })}
                        >
                          {open ? 'Show top 5' : `Show all ${list.length}`}
                        </button>
                      )}
                      {srcs.length > 0 && <div className="wonders-src"><SourceChips ids={srcs} /></div>}
                    </div>
                  )
                })}
              </section>
            )
          })}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// Wonder race scoreboard
// ---------------------------------------------------------------------------------------------

function SiteCount({ sites, titles }: { sites: number; titles: number }) {
  return (
    <span className="wonders-sites" title={`${sites} distinct site${sites === 1 ? '' : 's'} holding ${titles} wonder title${titles === 1 ? '' : 's'}`}>
      <span>{sites} site{sites === 1 ? '' : 's'}</span>
      {titles !== sites && <small>({titles} titles)</small>}
    </span>
  )
}

function WonderRace({ groups, lka, usa }: { groups: Group[]; lka?: CountryData; usa?: CountryData }) {
  const count = (g: Group, c: CountryId) => g.by[c]?.length ?? 0
  const dataOf = (c: CountryId) => (c === 'lka' ? lka : usa)
  const gap = (g: Group, c: CountryId) => noData(dataOf(c), g.cat, count(g, c))
  const gaps = COUNTRIES.flatMap((c) => groups.filter((g) => gap(g, c)).map((g) => `${g.cat} (${COUNTRY_META[c].name})`))
  const popYear = (d?: CountryData) => { const v = d?.metricByKey.get('pop')?.vintage; return v && d ? countryVintage(v, d.id) : '' }
  const top = (g: Group, c: CountryId) => g.by[c]?.find((w) => w.rank === 1 && w.value != null && Number.isFinite(w.value))?.value ?? null
  const titles = (c: CountryId) => groups.reduce((a, g) => a + count(g, c), 0)
  const siteSet = (c: CountryId) => new Set(groups.flatMap((g) => (g.by[c] ?? []).map(siteKey)))
  const tot = (c: CountryId) => siteSet(c).size
  // sites holding more than one title, for the footnote
  const multi = COUNTRIES.flatMap((c) => {
    const n = new Map<string, { name: string; cats: string[] }>()
    for (const g of groups) for (const w of g.by[c] ?? []) {
      const k = siteKey(w)
      const e = n.get(k) ?? { name: w.name, cats: [] }
      e.cats.push(g.cat)
      n.set(k, e)
    }
    return [...n.values()].filter((e) => e.cats.length > 1).map((e) => ({ c, ...e }))
  })
  const pop = (d?: CountryData) => d?.meta.totals?.pop
  const land = (d?: CountryData) => d?.meta.totals?.land_km2
  const perM = (c: CountryId, d?: CountryData) => (d && pop(d) ? tot(c) / (pop(d)! / 1e6) : null)
  const perA = (c: CountryId, d?: CountryData) => (d && land(d) ? tot(c) / (land(d)! / 1e4) : null)
  const lead = (a: number | null, b: number | null, side: 'a' | 'b') =>
    a == null || b == null || a === b ? '' : (side === 'a' ? a > b : b > a) ? 'lead' : ''
  const fmtRate = (v: number | null) => (v == null ? '…' : v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toPrecision(2))
  const hasUsa = !!usa

  return (
    <section className="card wonders-race" aria-label="Wonder race scoreboard">
      <h3><span aria-hidden>🏆</span> Wonder race</h3>
      <table className="data wonders-race-table">
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col" className="num"><span className="chip lka">LKA</span></th>
            <th scope="col" className="num"><span className="chip usa">USA</span></th>
            <th scope="col" className="num" title="Value of each country's #1 wonder, larger ÷ smaller">#1 vs #1</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const ga = gap(g, 'lka'), gb = gap(g, 'usa')
            const a = lka && !ga ? count(g, 'lka') : null
            const b = hasUsa && !gb ? count(g, 'usa') : null
            const cell = (v: number | null, d: CountryData | undefined, miss: boolean) =>
              miss ? <span className="muted" title={catMeta(d, g.cat)?.note ?? `No ${CATEGORY_DATA[g.cat]?.what ?? 'data'} for this country`}>–</span> : d ? v : '…'
            const ta = top(g, 'lka'), tb = top(g, 'usa')
            let ratio: { c: CountryId; x: number } | null = null
            if (ta != null && tb != null && ta > 0 && tb > 0) ratio = ta >= tb ? { c: 'lka', x: ta / tb } : { c: 'usa', x: tb / ta }
            return (
              <tr key={g.cat}>
                <th scope="row"><span aria-hidden>{WONDER_ICON[g.cat] ?? '★'}</span> {g.cat}</th>
                <td className={`num ${lead(a, b, 'a')}`}>{cell(a, lka, ga)}</td>
                <td className={`num ${lead(a, b, 'b')}`}>{cell(b, usa, gb)}</td>
                <td className="num wonders-ratio">
                  {ratio ? <span className={ratio.c}>{COUNTRY_META[ratio.c].short} {ratio.x >= 10 ? ratio.x.toFixed(0) : ratio.x.toFixed(1)}×</span> : <span className="muted">–</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" title="Distinct sites: a site that holds two titles (e.g. a national park that is also a World Heritage Site) counts once">Total wonders</th>
            <td className={`num wonders-tot ${lead(tot('lka'), hasUsa ? tot('usa') : null, 'a')}`}>{lka ? <SiteCount sites={tot('lka')} titles={titles('lka')} /> : '…'}</td>
            <td className={`num wonders-tot ${lead(tot('lka'), hasUsa ? tot('usa') : null, 'b')}`}>{hasUsa ? <SiteCount sites={tot('usa')} titles={titles('usa')} /> : '…'}</td>
            <td />
          </tr>
          <tr>
            <th scope="row">per million people</th>
            <td className={`num ${lead(perM('lka', lka), perM('usa', usa), 'a')}`}>{fmtRate(perM('lka', lka))}</td>
            <td className={`num ${lead(perM('lka', lka), perM('usa', usa), 'b')}`}>{fmtRate(perM('usa', usa))}</td>
            <td />
          </tr>
          <tr>
            <th scope="row">per 10,000 km²</th>
            <td className={`num ${lead(perA('lka', lka), perA('usa', usa), 'a')}`}>{fmtRate(perA('lka', lka))}</td>
            <td className={`num ${lead(perA('lka', lka), perA('usa', usa), 'b')}`}>{fmtRate(perA('usa', usa))}</td>
            <td />
          </tr>
        </tfoot>
      </table>
      <p className="wonders-foot">
        Most rules keep a fixed top-N list per country, so raw counts are capped by design; only categories that list every
        qualifying site (such as World Heritage) compare like for like. With capped lists, rates per person and per area
        naturally favour the smaller country — the “#1 vs #1” column compares the champions’ real values instead.
        {' '}Totals and rates count distinct sites, so a site with two titles counts once
        {multi.length > 0 ? (
          <span title={multi.map((e) => `${e.name}: ${e.cats.join(' + ')}`).join('\n')}>
            {' '}({COUNTRIES.map((c) => {
              const m = multi.filter((e) => e.c === c)
              return m.length ? `${COUNTRY_META[c].name} ${m.length}: ${m.length === 1 ? m[0].name : `e.g. ${m[0].name}`}` : ''
            }).filter(Boolean).join('; ')})
          </span>
        ) : null}.
        {gaps.length > 0 && <> “–” = no data, not zero: {gaps.join(', ')}; those categories add nothing to the total.</>}
        {' '}Population (map tiles, {[popYear(lka), popYear(usa)].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(' / ') || 'n/a'} estimates) and land area: {lka ? `Sri Lanka ${fmtNumber(pop(lka))} people, ${fmtNumber(land(lka))} km²` : '…'}
        {usa ? `; United States ${fmtNumber(pop(usa))} people, ${fmtNumber(land(usa))} km²` : ''}.
        {' '}<SourceChips ids={[...new Set([...(lka?.metricByKey.get('pop')?.sources ?? []), ...(usa?.metricByKey.get('pop')?.sources ?? [])])]} />
      </p>
    </section>
  )
}
