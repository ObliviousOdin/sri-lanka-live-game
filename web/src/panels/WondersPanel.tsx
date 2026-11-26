import { useEffect, useMemo, useRef, useState } from 'react'
import { COUNTRY_META } from '../civ'
import { SourceChips } from '../components/SourceChip'
import type { AppData } from '../data/useData'
import { fmtInt, fmtNumber, fmtUsd } from '../format'
import { WONDER_ICON } from '../map/MapPane'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId, type Wonder } from '../types'
import { SITE_ZOOM, flyToCountry } from './cityViewNav'
import './WondersPanel.css'

const CATEGORY_ORDER = Object.keys(WONDER_ICON)
const UNIT_SHORT: Record<string, string> = {
  tonnes: 't', passengers: 'pax', 'Wikipedia editions': 'editions', visitors: 'visitors', works: 'works', MW: 'MW',
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
  return {
    text: `${fmtNumber(w.value)}${w.unit ? ` ${UNIT_SHORT[w.unit] ?? w.unit}` : ''}`,
    title: `${fmtInt(w.value)} ${w.unit}${yr ? ` (${yr})` : ''}`,
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
  const { lka, usa } = app.countries

  const groups = useMemo(() => {
    const m = new Map<string, Group>()
    for (const cc of COUNTRIES) {
      const d = app.countries[cc]
      if (!d) continue
      for (const w of d.wonders) {
        let g = m.get(w.category)
        if (!g) { g = { cat: w.category, rules: [], by: {} }; m.set(w.category, g) }
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
            output, ports by cargo volume, power stations by capacity, airports by passengers, every UNESCO World Heritage
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
                {present.map((c) => {
                  const list = g.by[c] ?? []
                  const key = `${g.cat}:${c}`
                  const open = expanded.has(key) || selCat === key
                  const rows = open ? list : list.slice(0, COLLAPSED)
                  const srcs = [...new Set(list.flatMap((w) => w.sources ?? []))]
                  return (
                    <div key={c} className="wonders-country">
                      <div className="wonders-country-head">
                        <span className={`chip ${c}`}>{COUNTRY_META[c].short}</span>
                        <span className="muted">{list.length ? `${list.length} wonder${list.length === 1 ? '' : 's'}` : 'none qualify'}</span>
                      </div>
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

function WonderRace({ groups, lka, usa }: { groups: Group[]; lka?: CountryData; usa?: CountryData }) {
  const count = (g: Group, c: CountryId) => g.by[c]?.length ?? 0
  const top = (g: Group, c: CountryId) => g.by[c]?.find((w) => w.rank === 1 && w.value != null && Number.isFinite(w.value))?.value ?? null
  const tot = (c: CountryId) => groups.reduce((a, g) => a + count(g, c), 0)
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
            const a = lka ? count(g, 'lka') : null
            const b = hasUsa ? count(g, 'usa') : null
            const ta = top(g, 'lka'), tb = top(g, 'usa')
            let ratio: { c: CountryId; x: number } | null = null
            if (ta != null && tb != null && ta > 0 && tb > 0) ratio = ta >= tb ? { c: 'lka', x: ta / tb } : { c: 'usa', x: tb / ta }
            return (
              <tr key={g.cat}>
                <th scope="row"><span aria-hidden>{WONDER_ICON[g.cat] ?? '★'}</span> {g.cat}</th>
                <td className={`num ${lead(a, b, 'a')}`}>{a ?? '…'}</td>
                <td className={`num ${lead(a, b, 'b')}`}>{b ?? '…'}</td>
                <td className="num wonders-ratio">
                  {ratio ? <span className={ratio.c}>{COUNTRY_META[ratio.c].short} {ratio.x >= 10 ? ratio.x.toFixed(0) : ratio.x.toFixed(1)}×</span> : <span className="muted">–</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total wonders</th>
            <td className={`num ${lead(tot('lka'), hasUsa ? tot('usa') : null, 'a')}`}>{lka ? tot('lka') : '…'}</td>
            <td className={`num ${lead(tot('lka'), hasUsa ? tot('usa') : null, 'b')}`}>{hasUsa ? tot('usa') : '…'}</td>
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
        Population and land area: {lka ? `Sri Lanka ${fmtNumber(pop(lka))} people, ${fmtNumber(land(lka))} km²` : '…'}
        {usa ? `; United States ${fmtNumber(pop(usa))} people, ${fmtNumber(land(usa))} km²` : ''}.
      </p>
    </section>
  )
}
