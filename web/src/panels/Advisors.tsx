// Advisors tab: five advisors comment on Sri Lanka vs the United States — nationally (World Bank
// indicators + the map's own metrics) or about the selected tile / admin unit / city, with the
// "twin across the ocean" finder. All sentences are generated from loaded data only.
import { useEffect, useMemo, useRef, useState } from 'react'
import { cellToLatLng } from 'h3-js'
import { COUNTRY_META } from '../civ'
import { SourceChips } from '../components/SourceChip'
import { YieldIcon } from '../components/YieldIcon'
import { adminUnitOfHex } from '../data/load'
import { useNational, useSources, type AppData } from '../data/useData'
import { useStore } from '../store'
import type { CountryData, CountryId, NationalIndicator, Selection, YieldKey } from '../types'
import {
  ADJ, ADVISORS, type AdvisorId, type Comment, counterpartLevel, fig, levelNoun, nm, ok, otherOf, type Place,
  resolvePlace, sameSelection, type Seg, SHORT,
} from './advisorsCore'
import {
  advisorRows, densityVerdict, fmtRow, indexRows, type NatCtx, nationalComments, rowVal, sparkRows, specFor,
} from './advisorsNational'
import { placeComments, twinName } from './advisorsPlace'
import { closeness, findTwins, fmtFeature, twinModel, type TwinResult } from './advisorsTwin'
import './Advisors.css'

type Countries = AppData['countries']
interface Target { country: CountryId; level: string; index: number }

const MAX_SHOWN = 4

const ZOOM: Record<CountryId, Record<string, number>> = {
  lka: { hex: 9.6, adm3: 10, adm2: 8.8, adm1: 8.1 },
  usa: { hex: 8, adm2: 7.2, adm1: 5.6 },
}

// ---------------------------------------------------------------------------------------------
// Navigation helpers
// ---------------------------------------------------------------------------------------------
function targetSelection(d: CountryData, t: Target): Selection {
  return t.level === 'hex'
    ? { country: t.country, kind: 'hex', id: d.hexes.h3[t.index] }
    : { country: t.country, kind: 'admin', id: `${t.level}:${t.index}` }
}

function targetLonLat(d: CountryData, t: Target): [number, number] | null {
  if (t.level === 'hex') {
    const h = d.hexes.h3[t.index]
    if (!h) return null
    const [lat, lon] = cellToLatLng(h)
    return [lon, lat]
  }
  const u = d.admin[t.level]?.units[t.index]
  return u ? [u.lon, u.lat] : null
}

/** Switch to split view, select the target and fly its map there. */
function goTo(countries: Countries, t: Target) {
  const d = countries[t.country]
  if (!d) return null
  const st = useStore.getState()
  const sel = targetSelection(d, t)
  const mustMount = st.mode !== 'split' && st.mode !== t.country
  st.set({ mode: 'split' })
  st.select(sel)
  const ll = targetLonLat(d, t)
  if (ll) {
    const fly = () => useStore.getState().fly(t.country, ll[0], ll[1], ZOOM[t.country][t.level])
    // a map pane that is only now being mounted needs a moment to measure itself before it can fly
    if (mustMount) window.setTimeout(fly, 450)
    else fly()
  }
  return sel
}

function computeTwin(place: Place, countries: Countries): TwinResult & { waiting?: boolean } {
  const oc = otherOf(place.country)
  const other = countries[oc]
  if (!other || !countries.lka || !countries.usa) return { ok: false, reason: `Waiting for ${nm(oc)} data to load…`, waiting: true }
  const ol = counterpartLevel(place.level, other)
  const [ll, ul] = place.country === 'lka' ? [place.level, ol] : [ol, place.level]
  const model = twinModel(countries.lka, countries.usa, ll, ul)
  if (typeof model === 'string') return { ok: false, reason: model }
  return findTwins(model, place.country, place.index, 3)
}

// ---------------------------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------------------------
export function Advisors({ app }: { app: AppData }) {
  const selection = useStore((s) => s.selection)
  const mode = useStore((s) => s.mode)
  const rows = useNational()
  const [persp, setPersp] = useState<CountryId | null>(null)
  const [jump, setJump] = useState<{ from: Selection; to: Selection } | null>(null)
  const idx = app.index
  const d = selection ? app.countries[selection.country] : undefined
  const place = useMemo(() => (selection && d && idx ? resolvePlace(selection, d, idx) : null), [selection, d, idx])

  if (selection && !d) return <div className="loading">Loading {COUNTRY_META[selection.country].name}…</div>
  if (selection && !place) {
    return (
      <div className="advisors">
        <div className="card">
          <p>The council has no counsel on this selection ({selection.kind} {selection.id}) — it is not in the current data export.</p>
          <button className="btn" onClick={() => useStore.getState().select(null)}>Back to the national view</button>
        </div>
      </div>
    )
  }
  if (place) {
    const back = jump && sameSelection(selection, jump.to) ? jump.from : null
    return (
      <PlaceCouncil
        key={`${place.country}:${place.level}:${place.index}:${selection?.kind}`}
        app={app} place={place} back={back}
        onJump={(t) => { const from = selection!; const to = goTo(app.countries, t); if (to) setJump({ from, to }) }}
        onBack={() => {
          if (!back) return
          const bd = app.countries[back.country]
          const bp = bd && idx ? resolvePlace(back, bd, idx) : null
          useStore.getState().select(back)
          if (bp) useStore.getState().fly(bp.country, bp.lon, bp.lat, ZOOM[bp.country][bp.level])
          setJump(null)
        }}
      />
    )
  }
  const home: CountryId = persp ?? (mode === 'usa' ? 'usa' : 'lka')
  return <NationalCouncil app={app} rows={rows} home={home} setHome={setPersp} />
}

// ---------------------------------------------------------------------------------------------
// National view
// ---------------------------------------------------------------------------------------------
function NationalCouncil({ app, rows, home, setHome }: { app: AppData; rows: NationalIndicator[]; home: CountryId; setHome: (c: CountryId) => void }) {
  const { lka, usa } = app.countries
  const ctx = useMemo<NatCtx>(() => ({ rows, byKey: indexRows(rows), home, rival: otherOf(home), data: { lka, usa }, idx: app.index }), [rows, home, lka, usa, app.index])
  const groups = useMemo(() => ADVISORS.map((a) => ({
    a, comments: nationalComments(a.id, ctx), spark: sparkRows(rows, a.id), ledger: advisorRows(rows, a.id),
  })), [ctx, rows])
  const verdict = useMemo(() => {
    let best: Comment | null = null
    for (const g of groups) for (const c of g.comments) if ((c.weight ?? 0) > (best?.weight ?? 0)) best = c
    if (best) return { lead: "The widest gap on the council's ledger: ", c: best }
    const dv = densityVerdict(ctx)
    return dv ? { lead: '', c: dv } : null
  }, [groups, ctx])

  return (
    <div className="advisors">
      <header className="advisors-head">
        <div className="advisors-head-text">
          <h2>High Council</h2>
          <p>National comparison. Select a tile, district or county on a map for local counsel and its twin across the ocean.</p>
        </div>
        <div className="advisors-persp" role="group" aria-label="Advise which civilization">
          <span>Our civ</span>
          {(['lka', 'usa'] as CountryId[]).map((c) => (
            <button key={c} className={`advisors-persp-btn ${c} ${home === c ? 'active' : ''}`} aria-pressed={home === c} onClick={() => setHome(c)}>
              {COUNTRY_META[c].name}
            </button>
          ))}
        </div>
      </header>
      {verdict && <Verdict lead={verdict.lead} c={verdict.c} />}
      {!rows.length && (
        <p className="advisors-note">World Bank national indicators (national.json) are not built yet, so the council speaks from the map’s own metrics. Re-run the pipeline’s <code>national</code> stage to add them.</p>
      )}
      {groups.map((g) => (
        <AdvisorCard key={g.a.id} advisor={g.a} comments={g.comments} home={home}
          waiting={!rows.length && (!lka || !usa) ? `Waiting for ${!lka ? 'Sri Lanka' : 'United States'} data to load…` : undefined}
          extra={<>
            {g.spark.length > 0 && <div className="advisors-sparks">{g.spark.map((r) => <Sparkline key={r.code} row={r} />)}</div>}
            {g.ledger.length > 0 && <Ledger rows={g.ledger} home={home} />}
          </>}
        />
      ))}
      <FamousTwins app={app} />
    </div>
  )
}

function FamousTwins({ app }: { app: AppData }) {
  const { lka, usa } = app.countries
  const res = useMemo(() => {
    if (!lka || !usa || !lka.admin.adm2 || !usa.admin.adm2) return null
    const model = twinModel(lka, usa, 'adm2', 'adm2')
    if (typeof model === 'string') return { reason: model, rows: [] }
    const rows: { city: string; from: Target; to: Target; dist: number }[] = []
    const add = (d: CountryData, n: number) => {
      for (const c of d.cities.slice(0, n)) {
        const ci = d.hexIndex.get(c.centre)
        const u = ci != null ? adminUnitOfHex(d, 'adm2', ci) : null
        if (!u) continue
        const r = findTwins(model, d.id, u.index, 1)
        if (r.ok) rows.push({ city: c.name, from: { country: d.id, level: 'adm2', index: u.index }, to: { country: r.country, level: 'adm2', index: r.matches[0].index }, dist: r.matches[0].dist })
      }
    }
    add(lka, 6)
    add(usa, 4)
    return { reason: '', rows, feats: model.feats }
  }, [lka, usa])
  if (!res) return null
  if (!res.rows.length && !res.reason) return null
  return (
    <section className="card advisors-twins" aria-labelledby="adv-twins-h">
      <h3 id="adv-twins-h"><span aria-hidden>🌊</span> Twins across the ocean</h3>
      {res.reason ? <p className="advisors-empty">{endDot(res.reason)}</p> : (
        <>
          <ul className="advisors-twinlist">
            {res.rows.map((r) => {
              const a = twinName(app.countries[r.from.country]!, 'adm2', r.from.index)
              const b = twinName(app.countries[r.to.country]!, 'adm2', r.to.index)
              return (
                <li key={`${r.from.country}:${r.from.index}:${r.city}`}>
                  <button className={`advisors-twinlink ${r.from.country}`} onClick={() => goTo(app.countries, r.from)} title={`Select ${a.title}`}>
                    <b>{r.city}</b> <small>{a.title}</small>
                  </button>
                  <span className="advisors-twinarrow" aria-label="twin of">⇄</span>
                  <button className={`advisors-twinlink ${r.to.country}`} onClick={() => goTo(app.countries, r.to)} title={`Show ${b.title}`}>
                    <b>{b.title}</b> <small>Δ {r.dist.toFixed(2)} SD</small>
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="advisors-small" title={`Indicators: ${res.feats!.map((f) => f.label).join(', ')}`}>
            The district or county containing each City View centre, matched on {res.feats!.length} shared indicators (z-scores pooled across both countries). Δ = mean difference in standard deviations.
          </p>
        </>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Place view
// ---------------------------------------------------------------------------------------------
function PlaceCouncil({ app, place, back, onJump, onBack }: { app: AppData; place: Place; back: Selection | null; onJump: (t: Target) => void; onBack: () => void }) {
  const other = app.countries[otherOf(place.country)]
  const { lka, usa } = app.countries
  const groups = useMemo(() => ADVISORS.map((a) => ({ a, comments: placeComments(a.id, { place, other }) })), [place, other])
  const twin = useMemo(() => computeTwin(place, { lka, usa }), [place, lka, usa])
  const verdict = useMemo(() => {
    let best: Comment | null = null
    for (const g of groups) for (const c of g.comments) if ((c.weight ?? 0) > (best?.weight ?? 0)) best = c
    const segs: Seg[] = best ? [...(best.verdict ?? best.segs)] : []
    if (twin.ok) {
      const tn = twinName(app.countries[twin.country]!, twin.level, twin.matches[0].index, app.index)
      segs.push(`${segs.length ? ' ' : ''}Its closest twin across the ocean is `, fig(tn.title, twin.country), ` — ${closeness(twin.matches[0].dist)}.`)
    }
    if (!segs.length) return null
    return { segs, sources: best?.sources ?? [] }
  }, [groups, twin, app.countries])
  const otherName = otherOf(place.country)
  const root = useRef<HTMLDivElement>(null)
  // a new place: bring the panel back to the top (the panel body is the scroll container)
  useEffect(() => { root.current?.closest('.panel-body')?.scrollTo({ top: 0 }) }, [])

  return (
    <div className="advisors" ref={root}>
      <header className="advisors-head">
        <div className="advisors-head-text">
          <div className="row advisors-chips">
            <span className={`chip ${place.country}`}>{COUNTRY_META[place.country].name}</span>
            <span className="chip">{place.kind}</span>
          </div>
          <h2>{place.title}</h2>
          {place.subtitle && <p>{place.subtitle}</p>}
          {place.city && <p className="advisors-small">Ranked as the {levelNoun(place.d, 'adm2', 1)} containing the city centre.</p>}
        </div>
        <button className="advisors-close" onClick={() => useStore.getState().select(null)} aria-label="Clear selection and return to the national view" title="National view (Esc)">✕</button>
      </header>
      {back && (
        <button className="btn advisors-back" onClick={onBack}>← Back to {describe(app, back)}</button>
      )}
      {verdict && <Verdict lead="" c={{ id: 'v', icon: '⚖️', segs: verdict.segs, sources: verdict.sources }} />}
      <TwinCard app={app} place={place} twin={twin} onJump={onJump} />
      {!other && <p className="advisors-note">{COUNTRY_META[otherName].name} data is still loading — comparisons with its median will appear when it arrives.</p>}
      {groups.map((g) => <AdvisorCard key={g.a.id} advisor={g.a} comments={g.comments} home={place.country} local />)}
    </div>
  )
}

const endDot = (s: string) => (/[.…!?]$/.test(s) ? s : `${s}.`)

function describe(app: AppData, sel: Selection) {
  const d = app.countries[sel.country]
  const p = d && app.index ? resolvePlace(sel, d, app.index) : null
  return p?.title ?? sel.id
}

function TwinCard({ app, place, twin, onJump }: { app: AppData; place: Place; twin: ReturnType<typeof computeTwin>; onJump: (t: Target) => void }) {
  const [showAll, setShowAll] = useState(false)
  if (!twin.ok) {
    return (
      <section className="card advisors-twin" aria-labelledby="adv-twin-h">
        <h3 id="adv-twin-h"><span aria-hidden>🌊</span> Twin across the ocean</h3>
        <p className="advisors-empty">{endDot(twin.reason)}</p>
      </section>
    )
  }
  const od = app.countries[twin.country]!
  const best = twin.matches[0]
  const name = twinName(od, twin.level, best.index, app.index)
  const feats = showAll ? best.feats : best.feats.slice(0, 3)
  const worst = best.feats.length > 3 ? best.feats[best.feats.length - 1] : null
  const target: Target = { country: twin.country, level: twin.level, index: best.index }
  const srcs = best.feats.flatMap((f) => f.f.sources)
  return (
    <section className="card advisors-twin" aria-labelledby="adv-twin-h">
      <h3 id="adv-twin-h"><span aria-hidden>🌊</span> Twin across the ocean</h3>
      <div className="advisors-twin-head">
        <div className="advisors-twin-name">
          <span className={`chip ${twin.country}`}>{COUNTRY_META[twin.country].name}</span>
          <b>{name.title}</b>
          {name.subtitle && <small>{name.subtitle}</small>}
        </div>
        <button className="btn primary" onClick={() => onJump(target)}>Show twin</button>
      </div>
      <p className="advisors-twin-score">
        Closest of {twin.pool.toLocaleString('en-US')} {ADJ[twin.country]} {levelNoun(od, twin.level, twin.pool)} — {closeness(best.dist)}: on average {best.dist.toFixed(2)} SD apart across {best.used} indicators.
      </p>
      <table className="data advisors-twin-table">
        <caption className="advisors-sr">Closest-matching indicators</caption>
        <thead>
          <tr><th scope="col">{showAll ? 'All indicators' : 'Best-matching indicators'}</th><th scope="col" className="num">{SHORT[place.country]}</th><th scope="col" className="num">{SHORT[twin.country]}</th></tr>
        </thead>
        <tbody>
          {feats.map((m) => (
            <tr key={m.f.key}>
              <td>{m.f.label}</td>
              <td className={`num ${place.country}`}>{fmtFeature(m.f, m.a)}</td>
              <td className={`num ${twin.country}`}>{fmtFeature(m.f, m.b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {best.feats.length > 3 && (
        <button className="advisors-more" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>{showAll ? 'Show best matches only' : `All ${best.feats.length} indicators`}</button>
      )}
      {worst && !showAll && (
        <p className="advisors-small">Biggest difference: {worst.f.label} — {fmtFeature(worst.f, worst.a)} here vs {fmtFeature(worst.f, worst.b)} there.</p>
      )}
      {twin.matches.length > 1 && (
        <div className="row advisors-runners">
          <span className="advisors-small">Runners-up:</span>
          {twin.matches.slice(1).map((m, j) => {
            const n = twinName(od, twin.level, m.index, app.index)
            const label = twin.level === 'hex' ? `${n.title.replace(/^Tile in /, '')} · ${n.subtitle.split(' · ')[0].replace(/ tile$/, '')} (#${j + 2})` : n.title
            return (
              <button key={m.index} className={`chip advisors-chipbtn ${twin.country}`} onClick={() => onJump({ country: twin.country, level: twin.level, index: m.index })} title={`Show ${n.title} — ${n.subtitle} (Δ ${m.dist.toFixed(2)} SD)`}>
                {label}
              </button>
            )
          })}
        </div>
      )}
      <div className="advisors-cmeta">
        <span className="advisors-vint">Nearest neighbour on z-scores pooled across both countries (each weighted equally){twin.level === 'hex' ? '; populated land tiles only' : ''}.</span>
        <Sources ids={srcs} />
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Cards & comments
// ---------------------------------------------------------------------------------------------
function Verdict({ lead, c }: { lead: string; c: Comment }) {
  return (
    <div className="advisors-verdict" role="note" aria-label="Advisor's verdict">
      <span className="advisors-verdict-icon" aria-hidden>⚖️</span>
      <div>
        <div className="advisors-verdict-title">Advisor’s verdict</div>
        <p>{lead}<Segs segs={c.segs} /></p>
        <Sources ids={c.sources} />
      </div>
    </div>
  )
}

/** Source chips, capped: one chip per publisher first, the rest behind a "+N" toggle. */
function Sources({ ids, max = 3 }: { ids: string[]; max?: number }) {
  const [all, setAll] = useState(false)
  const catalog = useSources()
  const ordered = useMemo(() => {
    const uniq = [...new Set(ids.filter(Boolean))]
    const pub = (id: string) => catalog.find((x) => x.id === id)?.publisher ?? id
    const seen = new Set<string>()
    const first: string[] = [], rest: string[] = []
    for (const id of uniq) {
      const p = pub(id)
      if (seen.has(p)) rest.push(id)
      else { seen.add(p); first.push(id) }
    }
    return [...first, ...rest]
  }, [ids, catalog])
  if (!ordered.length) return null
  const shown = all ? ordered : ordered.slice(0, max)
  return (
    <span className="advisors-sources">
      <SourceChips ids={shown} />
      {ordered.length > max && (
        <button className="chip advisors-chipbtn" aria-expanded={all} onClick={() => setAll(!all)} title={all ? 'Show fewer sources' : `${ordered.length - max} more sources`}>
          {all ? '−' : `+${ordered.length - max}`}
        </button>
      )}
    </span>
  )
}

function Segs({ segs }: { segs: Seg[] }) {
  return <>{segs.map((s, i) => (typeof s === 'string' ? <span key={i}>{s}</span> : <b key={i} className={`advisors-fig ${s.c ?? ''}`}>{s.t}</b>))}</>
}

function AdvisorCard({ advisor, comments, home, extra, local, waiting }: {
  advisor: (typeof ADVISORS)[number]; comments: Comment[]; home: CountryId; extra?: React.ReactNode; local?: boolean; waiting?: string
}) {
  const [more, setMore] = useState(false)
  const shown = more ? comments : comments.slice(0, MAX_SHOWN)
  const ahead = comments.filter((c) => c.status === 'ahead').length
  const behind = comments.filter((c) => c.status === 'behind').length
  const hid = `adv-${advisor.id}`
  return (
    <section className="card advisors-card" aria-labelledby={hid} style={{ '--adv': advisor.color } as React.CSSProperties}>
      <header className="advisors-card-head">
        <span className="advisors-avatar" aria-hidden>{advisor.emoji}</span>
        <div className="advisors-card-title">
          <h3 id={hid}>{advisor.title}</h3>
          <span>{advisor.role}</span>
        </div>
        {(ahead > 0 || behind > 0) && (
          <span className="advisors-tally" title={`${nm(home, true)} leads on ${ahead} and trails on ${behind} of this advisor's indicators`}>
            <span className="good">▲{ahead}</span> <span className="bad">▼{behind}</span>
          </span>
        )}
      </header>
      {comments.length === 0 ? (
        <p className="advisors-empty">{waiting ?? EMPTY[advisor.id](local)}</p>
      ) : (
        <ul className="advisors-comments">
          {shown.map((c) => <CommentItem key={c.id} c={c} home={home} />)}
        </ul>
      )}
      {comments.length > MAX_SHOWN && (
        <button className="advisors-more" aria-expanded={more} onClick={() => setMore(!more)}>
          {more ? 'Fewer remarks' : `${comments.length - MAX_SHOWN} more remark${comments.length - MAX_SHOWN === 1 ? '' : 's'}`}
        </button>
      )}
      {extra}
    </section>
  )
}

const EMPTY: Record<AdvisorId, (local?: boolean) => string> = {
  economic: (l) => `No economic data ${l ? 'for this place ' : ''}yet — GDP, income and jobs arrive when the pipeline's economy & labour domains finish.`,
  military: (l) => `Nothing to report ${l ? 'here ' : ''}yet — military, population and terrain data are still being built.`,
  cultural: (l) => `No cultural data ${l ? 'for this place ' : ''}yet — tourism, heritage and religion domains are still being built.`,
  scientific: (l) => `No research or education data ${l ? 'for this place ' : ''}yet — the science domain is still being built.`,
  environmental: (l) => `No environmental data ${l ? 'for this place ' : ''}yet.`,
}

function CommentItem({ c, home }: { c: Comment; home: CountryId }) {
  const icon = c.icon.startsWith('y:') ? <YieldIcon y={c.icon.slice(2) as YieldKey} size={15} /> : <span>{c.icon}</span>
  return (
    <li className="advisors-comment">
      <span className="advisors-cicon" aria-hidden>{icon}</span>
      <div className="advisors-cbody">
        <p><Segs segs={c.segs} /></p>
        <div className="advisors-cmeta">
          {c.status && (
            <span className={`advisors-status ${c.status}`} title={`${nm(home, true)} is ${c.status === 'even' ? 'level with' : c.status} the rival on this indicator`}>
              {c.status === 'ahead' ? '▲ Ahead' : c.status === 'behind' ? '▼ Behind' : '= Level'}
            </span>
          )}
          {ok(c.pct) && <PctMeter p={c.pct} better={c.pctBetter ?? 'neutral'} />}
          {c.meta && <span className="advisors-vint">{c.meta}</span>}
          <Sources ids={c.sources} />
        </div>
      </div>
    </li>
  )
}

function PctMeter({ p, better }: { p: number; better: 'higher' | 'lower' | 'neutral' }) {
  const good = better === 'neutral' ? null : better === 'higher' ? p >= 50 : p <= 50
  const cls = good == null ? 'neutral' : good ? 'good' : 'bad'
  const pp = Math.min(100, Math.max(0, p))
  return (
    <span className={`advisors-meter ${cls}`} role="img" aria-label={`Percentile ${Math.round(pp)} of 100 within the country`} title={`Percentile ${Math.round(pp)} within the country`}>
      <span className="advisors-meter-bar"><i style={{ left: `${pp}%` }} /></span>
      <span className="advisors-meter-n">P{Math.round(Math.min(99, pp))}</span>
    </span>
  )
}

// ---------------------------------------------------------------------------------------------
// Sparklines & ledger
// ---------------------------------------------------------------------------------------------
function cleanSeries(row: NationalIndicator, iso: 'LKA' | 'USA') {
  const s = (row[iso]?.series ?? []).filter((p) => ok(p[0]) && ok(p[1])).sort((a, b) => a[0] - b[0])
  if (!s.length) return s
  const last = s[s.length - 1][0]
  return s.filter((p) => p[0] >= last - 30)
}

function Sparkline({ row }: { row: NationalIndicator }) {
  const spec = specFor(row)
  const model = useMemo(() => {
    const L = cleanSeries(row, 'LKA'), U = cleanSeries(row, 'USA')
    if (L.length < 3 || U.length < 3) return null
    const y0 = Math.min(L[0][0], U[0][0]), y1 = Math.max(L[L.length - 1][0], U[U.length - 1][0])
    const lastL = L[L.length - 1][1], lastU = U[U.length - 1][1]
    const allPos = [...L, ...U].every((p) => p[1] > 0)
    const isPct = spec?.fmt === 'pct' || spec?.fmt === 'years' || /%/.test(row.label)
    const ratio = allPos ? Math.max(lastL, lastU) / Math.min(lastL, lastU) : Infinity
    let mode: 'shared' | 'index' | 'own' = 'own'
    let base = 0
    if (isPct && ratio < 3) mode = 'shared'
    else if (allPos) {
      const ul = new Map(U.map((p) => [p[0], p[1]]))
      const common = L.find((p) => ul.has(p[0]))
      if (common) { mode = 'index'; base = common[0] }
    }
    const tf = (S: [number, number][]) => {
      if (mode === 'index') { const b = S.find((p) => p[0] === base)![1]; return S.map((p) => [p[0], (100 * p[1]) / b] as [number, number]) }
      return S
    }
    const A = tf(L), B = tf(U)
    const ext = (S: [number, number][]) => [Math.min(...S.map((p) => p[1])), Math.max(...S.map((p) => p[1]))]
    const path = (S: [number, number][], lo: number, hi: number) => S.map((p, i) => {
      const x = y1 > y0 ? ((p[0] - y0) / (y1 - y0)) * 100 : 50
      const y = 26 - (hi > lo ? ((p[1] - lo) / (hi - lo)) * 24 : 12)
      return `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`
    }).join('')
    let pa: string, pb: string
    if (mode === 'own') {
      const [la, ha] = ext(A), [lb, hb] = ext(B)
      pa = path(A, la, ha); pb = path(B, lb, hb)
    } else {
      const [la, ha] = ext(A), [lb, hb] = ext(B)
      const lo = Math.min(la, lb), hi = Math.max(ha, hb)
      pa = path(A, lo, hi); pb = path(B, lo, hi)
    }
    const caption = mode === 'shared' ? 'same scale' : mode === 'index' ? `indexed, ${base} = 100` : 'each on its own scale'
    const growth = mode === 'index' ? { l: A[A.length - 1][1], u: B[B.length - 1][1] } : null
    return { pa, pb, y0, y1, lastL, lastU, yl: L[L.length - 1][0], yu: U[U.length - 1][0], caption, growth }
  }, [row, spec])
  if (!model) return null
  const label = spec?.short ?? row.label
  const aria = `${label}, ${model.y0}–${model.y1}: Sri Lanka latest ${fmtRow(row, model.lastL)} (${model.yl}), United States latest ${fmtRow(row, model.lastU)} (${model.yu}); ${model.caption}.`
  return (
    <figure className="advisors-spark">
      <figcaption><span title={row.label}>{label}</span><small>{model.caption}</small></figcaption>
      <svg viewBox="0 0 100 28" preserveAspectRatio="none" role="img" aria-label={aria}>
        <line x1="0" x2="100" y1="27.5" y2="27.5" className="advisors-spark-base" vectorEffect="non-scaling-stroke" />
        <path d={model.pb} className="advisors-spark-usa" vectorEffect="non-scaling-stroke" />
        <path d={model.pa} className="advisors-spark-lka" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="advisors-spark-axis" aria-hidden>
        <span>{model.y0}</span>
        <span>{model.y1}</span>
      </div>
      <div className="advisors-spark-legend">
        <span className="lka" title={`Sri Lanka, ${model.yl}${model.growth ? ` — index ${Math.round(model.growth.l)}` : ''}`}><i />LK {fmtRow(row, model.lastL)}</span>
        <span className="usa" title={`United States, ${model.yu}${model.growth ? ` — index ${Math.round(model.growth.u)}` : ''}`}><i />US {fmtRow(row, model.lastU)}</span>
      </div>
    </figure>
  )
}

function Ledger({ rows, home }: { rows: NationalIndicator[]; home: CountryId }) {
  const rival = otherOf(home)
  const order: CountryId[] = [home, rival]
  return (
    <details className="advisors-ledger">
      <summary>Ledger · {rows.length} World Bank indicator{rows.length === 1 ? '' : 's'}</summary>
      <div className="advisors-ledger-wrap">
        <table className="data">
          <thead>
            <tr><th scope="col">Indicator</th>{order.map((c) => <th key={c} scope="col" className={`num ${c}`}>{SHORT[c]}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td title={`${r.label} · ${r.code}`}>{specFor(r)?.short ?? r.label}</td>
                {order.map((c) => {
                  const v = rowVal(r, c)
                  return <td key={c} className="num">{v ? <>{fmtRow(r, v.v)}<small> {v.y}</small></> : '–'}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Sources ids={rows.map((r) => r.source)} />
    </details>
  )
}
