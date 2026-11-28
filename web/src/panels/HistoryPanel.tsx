import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { COUNTRY_META, EVENT_KIND_META } from '../civ'
import { SourceChips } from '../components/SourceChip'
import { useEvents, type AppData } from '../data/useData'
import { fmtNumber } from '../format'
import { DEFAULT_ERAS, LAST_YEAR, eraIndexOf, posToYear, resolveEras, yearLabel, yearToPos, type EraSpan } from '../history/eraScale'
import { prefersReducedMotion } from '../map/motion'
import { useStore } from '../store'
import type { CountryId, HistoryEvent } from '../types'
import './HistoryPanel.css'

type Ev = HistoryEvent & { country: CountryId }

const ERA_SECONDS = 6           // playback: each era takes this long
const FEED_MAX = 80
const HISTORY_SOURCES = ['wikidata', 'eia_860', 'wri_gppd', 'ceb_annual_report_2023', 'unesco_whc_via_wikidata']

function goTo(e: Ev) {
  const st = useStore.getState()
  const narrow = typeof window !== 'undefined' && window.innerWidth < 900
  if (st.mode !== 'split' && st.mode !== e.country) st.set({ mode: narrow ? e.country : 'split' })
  st.setHistory({ focus: e.id })
  if (e.h3) st.select({ country: e.country, kind: 'hex', id: e.h3 }, 'history')
  // give a newly shown map a moment to mount before flying
  setTimeout(() => useStore.getState().fly(e.country, e.lon, e.lat, e.country === 'lka' ? 9.2 : 7.4), 60)
}

function verbDate(e: Ev) {
  return `${e.event} ${e.date}`
}

export const HistoryPanel = memo(function HistoryPanel({ app }: { app: AppData }) {
  const lka = useEvents('lka')
  const usa = useEvents('usa')
  const { year, playing, kinds, focus } = useStore((s) => s.history)
  const setHistory = useStore((s) => s.setHistory)
  const lensId = useStore((s) => s.lensId)
  const [order, setOrder] = useState<'newest' | 'notable'>('newest')

  // opening the timeline switches the map to the Historic moments lens
  useEffect(() => {
    if (useStore.getState().lensId !== 'history') useStore.getState().setLens('history')
  }, [])

  const all: Ev[] = useMemo(() => [
    ...(lka ?? []).map((e) => ({ ...e, country: 'lka' as const })),
    ...(usa ?? []).map((e) => ({ ...e, country: 'usa' as const })),
  ].sort((a, b) => a.year - b.year || a.name.localeCompare(b.name)), [lka, usa])
  const first = useMemo(() => Math.floor(Math.min(-300, ...all.map((e) => e.year)) / 100) * 100, [all])
  const spans: EraSpan[] = useMemo(() => resolveEras(app.index?.eras ?? DEFAULT_ERAS, first), [app.index, first])
  const eraI = eraIndexOf(year, spans)
  const era = spans[eraI]
  const filtered = useMemo(() => (kinds ? all.filter((e) => kinds.includes(e.kind)) : all), [all, kinds])
  // notability: wonders first, then the within-kind percentile of the event's value
  const notability = useMemo(() => {
    const out = new Map<string, number>()
    const byKind = new Map<string, Ev[]>()
    for (const e of all) byKind.set(`${e.country}:${e.kind}`, [...(byKind.get(`${e.country}:${e.kind}`) ?? []), e])
    for (const list of byKind.values()) {
      const sorted = [...list].sort((a, b) => (a.value ?? -1) - (b.value ?? -1))
      sorted.forEach((e, i) => out.set(`${e.country}:${e.id}`, (e.wonder ? 1 : 0) + (list.length > 1 ? i / (list.length - 1) : 0.5)))
    }
    return out
  }, [all])
  const eventYears = useMemo(() => [...new Set(filtered.map((e) => e.year))].sort((a, b) => a - b), [filtered])

  // ---- playback: era-paced animation (or one moment per step with reduced motion)
  const raf = useRef<number | null>(null)
  useEffect(() => {
    if (!playing) return
    if (prefersReducedMotion()) {
      const t = setInterval(() => {
        const s = useStore.getState().history
        const next = eventYears.find((y) => y > s.year)
        if (next == null) { setHistory({ playing: false, year: LAST_YEAR }); return }
        setHistory({ year: next })
      }, 900)
      return () => clearInterval(t)
    }
    let last = performance.now()
    let pos = yearToPos(useStore.getState().history.year, spans)
    if (pos >= 0.999) pos = 0
    const step = (t: number) => {
      const dt = (t - last) / 1000
      last = t
      pos = Math.min(1, pos + dt / (ERA_SECONDS * spans.length))
      setHistory({ year: posToYear(pos, spans) })
      if (pos >= 1) { setHistory({ playing: false, year: LAST_YEAR }); return }
      raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
    return () => { if (raf.current != null) cancelAnimationFrame(raf.current) }
  }, [playing, spans, eventYears, setHistory])

  const prevYear = [...eventYears].reverse().find((y) => y < year)
  const nextYear = eventYears.find((y) => y > year)
  const shown = filtered.filter((e) => e.year <= year)
  const inEra = shown.filter((e) => e.year >= era.start)
  const count = (c: CountryId, list: Ev[]) => list.reduce((n, e) => n + (e.country === c ? 1 : 0), 0)

  // firsts: earliest moment of each kind, per country
  const firsts = useMemo(() => {
    const out: Record<string, Partial<Record<CountryId, Ev>>> = {}
    for (const e of all) {
      const k = (out[e.kind] ??= {})
      if (!k[e.country]) k[e.country] = e
    }
    return out
  }, [all])
  const perEra = useMemo(() => spans.map((s) => ({
    span: s,
    lka: filtered.filter((e) => e.country === 'lka' && e.year >= s.start && e.year <= s.end).length,
    usa: filtered.filter((e) => e.country === 'usa' && e.year >= s.start && e.year <= s.end).length,
  })), [spans, filtered])
  const eraMax = Math.max(1, ...perEra.map((r) => Math.max(r.lka, r.usa)))

  const sliderPos = Math.round(yearToPos(year, spans) * 1000)
  const onSliderKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // PageUp/PageDown jump between moments; Home/End to the ends
    if (e.key === 'PageUp' && nextYear != null) { e.preventDefault(); setHistory({ year: nextYear, playing: false }) }
    if (e.key === 'PageDown' && prevYear != null) { e.preventDefault(); setHistory({ year: prevYear, playing: false }) }
  }
  const toggleKind = (k: string) => {
    const cur = kinds ?? Object.keys(EVENT_KIND_META)
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]
    setHistory({ kinds: next.length === Object.keys(EVENT_KIND_META).length ? null : next })
  }
  const loading = lka == null || usa == null

  return (
    <div className="history">
      <div className="card history-head">
        <div className="history-year" aria-live="polite">
          <span className="history-year-num">{yearLabel(year)}</span>
          <span className="history-era">{era.label} era <span className="muted">· {yearLabel(era.start)}–{era.end >= LAST_YEAR ? 'today' : yearLabel(era.end)}</span></span>
        </div>
        <div className="history-controls" role="group" aria-label="Timeline playback">
          <button type="button" className="btn" onClick={() => setHistory({ year: spans[0].start, playing: false })} aria-label="Go to the earliest moment" title="Earliest">⏮</button>
          <button type="button" className="btn" disabled={prevYear == null} onClick={() => prevYear != null && setHistory({ year: prevYear, playing: false })} aria-label="Previous moment" title="Previous moment">◀</button>
          <button type="button" className="btn primary" onClick={() => setHistory({ playing: !playing })} aria-pressed={playing} aria-label={playing ? 'Pause' : 'Play the timeline'}>
            {playing ? '⏸ Pause' : '▶ Play'}
          </button>
          <button type="button" className="btn" disabled={nextYear == null} onClick={() => nextYear != null && setHistory({ year: nextYear, playing: false })} aria-label="Next moment" title="Next moment">▶</button>
          <button type="button" className="btn" onClick={() => setHistory({ year: LAST_YEAR, playing: false })} aria-label="Go to today" title="Today">⏭</button>
        </div>
        <div className="history-track">
          <div className="history-bands" aria-hidden>
            {spans.map((s, i) => <i key={s.id} className={i === eraI ? 'on' : ''} />)}
          </div>
          <input
            type="range" min={0} max={1000} step={1} value={sliderPos}
            onChange={(e) => setHistory({ year: posToYear(Number(e.target.value) / 1000, spans), playing: false })}
            onKeyDown={onSliderKey}
            aria-label="Year" aria-valuetext={`${yearLabel(year)}, ${era.label} era`}
          />
          <div className="history-eras">
            {spans.map((s, i) => (
              <button key={s.id} type="button" className={i === eraI ? 'on' : ''}
                onClick={() => setHistory({ year: s.end, playing: false })} title={`Show the ${s.label} era (to ${yearLabel(Math.min(s.end, LAST_YEAR))})`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
        <div className="history-tally">
          {(['lka', 'usa'] as CountryId[]).map((c) => (
            <span key={c} className={`chip ${c}`}>{COUNTRY_META[c].short} · {fmtNumber(count(c, shown))} moments so far · {fmtNumber(count(c, inEra))} this era</span>
          ))}
        </div>
        {lensId !== 'history' && (
          <button type="button" className="btn history-lens" onClick={() => useStore.getState().setLens('history')}>Show moments on the map</button>
        )}
      </div>

      <div className="history-kinds" role="group" aria-label="Kinds of moments">
        {Object.entries(EVENT_KIND_META).map(([k, m]) => {
          const on = !kinds || kinds.includes(k)
          return (
            <button key={k} type="button" className={`chip history-kind ${on ? 'on' : ''}`} aria-pressed={on} onClick={() => toggleKind(k)}>
              <span aria-hidden>{m.icon}</span> {m.label}
            </button>
          )
        })}
      </div>

      <div className="history-feedhead">
        <h3 className="section-title">Moments of the {era.label} era{year < era.end ? ` to ${yearLabel(year)}` : ''}</h3>
        <div className="history-order" role="group" aria-label="Order moments by">
          {(['newest', 'notable'] as const).map((o) => (
            <button key={o} type="button" className={`chip ${order === o ? 'on' : ''}`} aria-pressed={order === o} onClick={() => setOrder(o)}>
              {o === 'newest' ? 'Newest' : 'Most notable'}
            </button>
          ))}
        </div>
      </div>
      {loading ? <div className="muted">Loading historic moments…</div> : inEra.length === 0 ? (
        <div className="muted history-empty">No dated moments in this era yet for either country{kinds ? ' (with these kinds)' : ''}. Press ▶ or pick a later era.</div>
      ) : (
        <ol className="history-feed">
          {(order === 'newest' ? [...inEra].reverse()
            : [...inEra].sort((a, b) => (notability.get(`${b.country}:${b.id}`) ?? 0) - (notability.get(`${a.country}:${a.id}`) ?? 0)))
            .slice(0, FEED_MAX).map((e) => (
            <li key={`${e.country}:${e.id}`} className={focus === e.id ? 'focus' : ''}>
              <button type="button" onClick={() => goTo(e)} title={e.basis}>
                <span className="history-ico" aria-hidden>{EVENT_KIND_META[e.kind]?.icon ?? '📍'}</span>
                <span className="history-what">
                  <b>{e.name}</b>
                  <span className="muted"> {verbDate(e)}</span>
                  {e.wonder && <span className="history-wonder"> · {e.wonder}</span>}
                </span>
                <span className={`chip ${e.country}`}>{COUNTRY_META[e.country].short}</span>
              </button>
            </li>
          ))}
          {inEra.length > FEED_MAX && <li className="muted history-more">+{inEra.length - FEED_MAX} earlier in this era</li>}
        </ol>
      )}

      <h3 className="section-title">Firsts</h3>
      <table className="data history-firsts">
        <thead><tr><th>Kind</th><th>{COUNTRY_META.lka.short}</th><th>{COUNTRY_META.usa.short}</th></tr></thead>
        <tbody>
          {Object.entries(EVENT_KIND_META).filter(([k]) => firsts[k]).map(([k, m]) => (
            <tr key={k}>
              <td><span aria-hidden>{m.icon}</span> {m.label}</td>
              {(['lka', 'usa'] as CountryId[]).map((c) => {
                const e = firsts[k]?.[c]
                if (!e) return <td key={c} className="muted">–</td>
                const reached = e.year <= year
                return (
                  <td key={c} className={reached ? '' : 'history-future'}>
                    <button type="button" className="history-link" onClick={() => { setHistory({ year: Math.max(year, e.year) }); goTo({ ...e, country: c }) }} title={`${e.name}: ${verbDate({ ...e, country: c })}`}>
                      <b>{e.date}</b> <span className="muted">{e.name}</span>
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="section-title">Moments per era</h3>
      <div className="history-eras-chart" role="table" aria-label="Dated moments per era">
        {perEra.map((r, i) => (
          <div key={r.span.id} role="row" className={`history-era-row ${i === eraI ? 'on' : ''}`}>
            <span role="cell" className="history-era-name">{r.span.label}</span>
            <span role="cell" className="history-bars">
              <i className="lka" style={{ width: `${(r.lka / eraMax) * 100}%` }} title={`${COUNTRY_META.lka.name}: ${r.lka}`} />
              <i className="usa" style={{ width: `${(r.usa / eraMax) * 100}%` }} title={`${COUNTRY_META.usa.name}: ${r.usa}`} />
            </span>
            <span role="cell" className="history-era-n"><span className="lka">{r.lka}</span> · <span className="usa">{r.usa}</span></span>
          </div>
        ))}
      </div>

      <div className="card history-about">
        <h3>About these dates</h3>
        <ul>
          <li>Every moment is a place already on the map, dated by a fetched source: Wikidata founding / opening dates (P571, P1619, P729, P580), the earliest power-generating unit still in service (EIA-860, US), CEB / Global Power Plant Database commissioning years (Sri Lanka) and UNESCO inscription years.</li>
          <li>Dates keep their precision: “5th century” is a century, not an exact year. Wikidata writes BCE years with a minus sign and no year zero.</li>
          <li>Coverage follows the sources: Wikidata dates Sri Lanka’s ancient monuments unevenly, US power stations are shown from 250 MW and US universities / museums are the most-researched / most-documented 300 / 200.</li>
        </ul>
        <SourceChips ids={HISTORY_SOURCES} />
      </div>
    </div>
  )
})
