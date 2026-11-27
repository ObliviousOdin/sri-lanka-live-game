// Sources tab: data pipeline overview + every source with what it feeds and how it was downloaded.
import { memo, useMemo, useState, type ReactNode } from 'react'
import { getHexagonEdgeLengthAvg, UNITS } from 'h3-js'
import { COUNTRY_META } from '../civ'
import type { AppData } from '../data/useData'
import { useNational, useSources } from '../data/useData'
import { fmtInt, fmtNumber } from '../format'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId, type Source } from '../types'
import { fmtBytes, plural } from './tilePanelStats'
import './Sources.css'

type Group = 'lka' | 'usa' | 'both' | 'national'
const GROUPS: { id: Group; label: string; blurb: string }[] = [
  { id: 'lka', label: 'Sri Lanka', blurb: 'Sri Lankan agencies and Sri Lanka-only datasets' },
  { id: 'usa', label: 'United States', blurb: 'U.S. federal statistics and U.S.-only datasets' },
  { id: 'both', label: 'Both countries', blurb: 'Global datasets applied identically to both maps' },
  { id: 'national', label: 'National', blurb: 'Country-level indicators (Advisors) and other references' },
]

const fmtDate = (s: string | null | undefined) => (s ? s.slice(0, 10) : '–')
const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u } }

// memo: App re-renders on every store change (e.g. map pan/zoom); skip unless the data changes
export const Sources = memo(function Sources({ app }: { app: AppData }) {
  const sources = useSources()
  const national = useNational()
  const [q, setQ] = useState('')
  const query = q.trim().toLowerCase()

  const nationalBySource = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const ind of national) {
      for (const id of String(ind.source ?? '').split(/[,;\s]+/).filter(Boolean)) {
        const a = m.get(id)
        if (a) a.push(ind.label); else m.set(id, [ind.label])
      }
    }
    return m
  }, [national])

  const wondersBySource = useMemo(() => {
    const m = new Map<string, Partial<Record<CountryId, number>>>()
    for (const c of COUNTRIES) {
      for (const w of app.countries[c]?.wonders ?? []) {
        for (const id of w.sources ?? []) {
          const e = m.get(id) ?? {}
          e[c] = (e[c] ?? 0) + 1
          m.set(id, e)
        }
      }
    }
    return m
  }, [app.countries])

  const labelOf = (c: CountryId, key: string) => app.countries[c]?.metricByKey.get(key)?.label ?? key

  const groupOf = (s: Source): Group => {
    if (nationalBySource.has(s.id)) return 'national'
    const cs = s.countries ?? []
    const l = cs.includes('lka') || !!s.metrics?.lka?.length
    const u = cs.includes('usa') || !!s.metrics?.usa?.length
    if (l && u) return 'both'
    if (l) return 'lka'
    if (u) return 'usa'
    return 'national'
  }

  const grouped = useMemo(() => {
    const out = new Map<Group, Source[]>(GROUPS.map((g) => [g.id, []]))
    for (const s of sources) {
      if (query) {
        const metricText = COUNTRIES.flatMap((c) => (s.metrics?.[c] ?? []).map((k) => `${k} ${labelOf(c, k)}`)).join(' ')
        const hay = `${s.id} ${s.title} ${s.publisher} ${s.notes} ${s.license} ${s.url} ${s.vintage} ${metricText}`.toLowerCase()
        if (!hay.includes(query)) continue
      }
      out.get(groupOf(s))!.push(s)
    }
    for (const a of out.values()) a.sort((x, y) => x.title.localeCompare(y.title))
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, query, nationalBySource, app.countries])

  const shown = [...grouped.values()].reduce((s, a) => s + a.length, 0)
  const totals = useMemo(() => {
    let files = 0, bytes = 0
    for (const s of sources) for (const d of s.downloads ?? []) { files++; bytes += d.bytes || 0 }
    return { files, bytes }
  }, [sources])

  return (
    <div className="sources">
      <PipelineCard app={app} nSources={sources.length} files={totals.files} bytes={totals.bytes} />

      <div className="sources-tools">
        <input
          className="input sources-search"
          type="search"
          placeholder="Search sources, publishers, metrics…"
          aria-label="Search sources"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="muted sources-count" aria-live="polite">{sources.length ? `${shown} of ${sources.length}` : ''}</span>
      </div>

      {!sources.length && <p className="muted">Loading sources.json…</p>}
      {sources.length > 0 && !shown && <p className="muted">No source matches “{q}”.</p>}

      {GROUPS.map((g) => {
        const list = grouped.get(g.id) ?? []
        if (!list.length) return null
        return (
          <section key={g.id} className="sources-group" aria-labelledby={`sources-g-${g.id}`}>
            <h3 id={`sources-g-${g.id}`} className="section-title sources-gtitle">
              {g.id === 'lka' || g.id === 'usa' ? <span className="sources-dot" style={{ background: COUNTRY_META[g.id].accent }} /> : null}
              {g.label} <span className="sources-gcount">{list.length}</span>
            </h3>
            <p className="muted sources-gblurb">{g.blurb}</p>
            {list.map((s) => (
              <SourceCard key={s.id} s={s} labelOf={labelOf} national={nationalBySource.get(s.id)} wonders={wondersBySource.get(s.id)} />
            ))}
          </section>
        )
      })}
    </div>
  )
})

function SourceCard({ s, labelOf, national, wonders }: {
  s: Source
  labelOf: (c: CountryId, k: string) => string
  national?: string[]
  wonders?: Partial<Record<CountryId, number>>
}) {
  const lensKey = useStore((st) => (st.lensId === 'custom' ? st.lensMetric.custom : undefined))
  const dls = s.downloads ?? []
  const bytes = dls.reduce((a, d) => a + (d.bytes || 0), 0)
  const first = dls[0]
  const urlCount = useMemo(() => {
    const m = new Map<string, number>()
    for (const d of dls) m.set(d.url, (m.get(d.url) ?? 0) + 1)
    return m
  }, [dls])
  const feeds = COUNTRIES.filter((c) => (s.metrics?.[c] ?? []).length > 0)
  return (
    <article className="card sources-item" id={`source-${s.id}`}>
      <header>
        <h4 className="sources-title">{s.title}</h4>
        <div className="sources-pub">{s.publisher}</div>
      </header>
      <dl className="sources-meta">
        <div><dt>Vintage</dt><dd>{s.vintage || '–'}</dd></div>
        <div><dt>License</dt><dd>{s.license || '–'}</dd></div>
        <div><dt>Accessed</dt><dd>{fmtDate(s.accessed)}</dd></div>
        <div><dt>ID</dt><dd><code>{s.id}</code></dd></div>
      </dl>
      {s.url && (
        <a className="sources-url" href={s.url} target="_blank" rel="noreferrer">
          {host(s.url)} <span aria-hidden>↗</span>
        </a>
      )}
      {s.notes && <Notes text={s.notes} />}

      {feeds.length > 0 && (
        <div className="sources-feeds">
          {feeds.map((c) => (
            <div key={c} className="sources-feedrow">
              <span className={`chip ${c} sources-cc`}>{COUNTRY_META[c].short}</span>
              <span className="sources-chips">
                {(s.metrics[c] ?? []).map((k) => (
                  <button
                    key={k}
                    className={`sources-mchip ${lensKey === k ? 'is-active' : ''}`}
                    onClick={() => useStore.getState().setLens('custom', k)}
                    aria-pressed={lensKey === k}
                    title={`Show “${labelOf(c, k)}” (${k}) on the map`}
                  >
                    {labelOf(c, k)}
                  </button>
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      {(national?.length || wonders) && (
        <div className="sources-also">
          {national?.length ? <span>National indicators: {national.slice(0, 6).join(', ')}{national.length > 6 ? ` +${national.length - 6}` : ''}</span> : null}
          {wonders ? <span>Wonders: {COUNTRIES.filter((c) => wonders[c]).map((c) => `${wonders[c]} ${COUNTRY_META[c].short}`).join(' · ')}</span> : null}
        </div>
      )}
      {!feeds.length && !national?.length && !wonders && <div className="sources-also muted">Not yet linked to a published metric.</div>}

      {first ? (
        <div className="sources-dl">
          <div className="sources-dlhead">⬇ {dls.length} download{dls.length > 1 ? 's' : ''} · {fmtBytes(bytes)}</div>
          <div className="sources-first">
            <a href={first.url} target="_blank" rel="noreferrer" className="sources-dlurl">{first.url.replace(/^https?:\/\//, '')}</a>
            {(urlCount.get(first.url) ?? 1) > 1 && <span className="sources-rep" title="Same URL, different request (e.g. a POST query) or a re-download"> #1 of {urlCount.get(first.url)}</span>}
            <span className="muted"> · sha256 <code>{first.sha256.slice(0, 12)}…</code> · {fmtDate(first.fetched_at)}</span>
          </div>
          {dls.length > 1 && (
            <details>
              <summary>All {dls.length} files</summary>
              <ul>
                {dls.slice(1).map((d, k) => {
                  // one URL can serve many files (POST queries) or be re-fetched: number the repeats
                  const n = urlCount.get(d.url) ?? 1
                  const nth = dls.slice(0, k + 2).filter((x) => x.url === d.url).length
                  return (
                    <li key={`${k}:${d.sha256}:${d.url}`}>
                      <a href={d.url} target="_blank" rel="noreferrer" className="sources-dlurl">{d.url.replace(/^https?:\/\//, '')}</a>
                      {n > 1 && <span className="sources-rep" title="Same URL, different request (e.g. a POST query) or a re-download"> #{nth} of {n}</span>}
                      <span className="muted"> · {fmtBytes(d.bytes)} · sha256 <code>{d.sha256.slice(0, 12)}…</code> · {fmtDate(d.fetched_at)}</span>
                    </li>
                  )
                })}
              </ul>
            </details>
          )}
        </div>
      ) : <div className="sources-dl muted">No cached download recorded (reference or manual source).</div>}
    </article>
  )
}

function Notes({ text }: { text: string }) {
  const [more, setMore] = useState(false)
  const long = text.length > 220
  return (
    <p className="sources-notes">
      {long && !more ? `${text.slice(0, 200).replace(/\s+\S*$/, '')}… ` : text}
      {long && (
        <button className="sources-more" onClick={() => setMore(!more)} aria-expanded={more}>{more ? ' less' : 'more'}</button>
      )}
    </p>
  )
}

// expanded on desktop, collapsed in the phone bottom sheet; remembers the user's toggle
let pipelineOpen: boolean | null = null

function PipelineCard({ app, nSources, files, bytes }: { app: AppData; nSources: number; files: number; bytes: number }) {
  const idx = app.index
  const ds = COUNTRIES.map((c) => app.countries[c])
  const cell = (fn: (d: CountryData) => ReactNode) => ds.map((d, k) => <td key={k} className="num">{d ? fn(d) : '…'}</td>)
  const open = pipelineOpen ?? (typeof window === 'undefined' || window.innerWidth >= 900)
  return (
    <details className="card sources-pipeline" open={open} onToggle={(e) => { pipelineOpen = (e.currentTarget as HTMLDetailsElement).open }}>
      <summary>
        <h3>Data pipeline</h3>
        <span className="sources-psum">6 stages{nSources > 0 ? ` · ${nSources} sources · ${fmtInt(files)} files · ${fmtBytes(bytes)}` : ''}</span>
      </summary>
      <p className="sources-lede">
        Every number on the maps is traceable: open data is downloaded once, reconciled onto H3 hexagons and translated into Civilization terms by published rules.
        Run it with <code>python -m civmap.run</code> (see README).
      </p>
      <ol className="sources-steps">
        <li>
          <b>Fetch</b> — each file is downloaded once into <code>data/cache/</code> with a <code>.meta.json</code> sidecar (URL, bytes, sha256, time); re-runs are offline and reproducible.
          {nSources > 0 && <span className="sources-fact"> {nSources} sources · {fmtInt(files)} files · {fmtBytes(bytes)} cached</span>}
        </li>
        <li>
          <b>Core crosswalk</b> — every WorldPop population pixel (100 m Sri Lanka, 1 km U.S.) is assigned to a (hex × finest admin unit) <i>piece</i>; populations are rescaled to official 2023 totals (Sri Lanka districts, U.S. counties).
        </li>
        <li>
          <b>Domain tables</b> — each data domain (economy, labour, food, science, culture, hazards, energy, terrain…) parses its sources into tidy admin, point and raster tables with unit, vintage and method.
        </li>
        <li>
          <b>Reconcile</b> — admin values move onto hexes through the pieces. <i>Extensive</i> sums (GDP, jobs) use a mass-preserving weighted allocation, value × w(hex∩unit) / w(unit); <i>intensive</i> rates (income, shares) take the weighted mean, Σ value × w(hex∩unit) / w(hex). Weights are population, land, cropland, tree or built-up area. Facilities snap to their H3 cell.
        </li>
        <li>
          <b>Civ rules</b> — terrain from land cover + DEM, yields from per-km² densities on one shared log scale, districts from location quotients and facility thresholds, wonders from ranking rules, citizens from workforce composition.
        </li>
        <li>
          <b>Export</b> — static JSON plus a column-major Float32 metric matrix in <code>web/public/data/</code>; this app reads nothing else.
        </li>
      </ol>
      <table className="data sources-grid">
        <caption className="sources-sr">Hex grid facts</caption>
        <thead>
          <tr><th scope="col">Grid</th>{COUNTRIES.map((c) => <th key={c} scope="col" className="num"><span className="sources-dot" style={{ background: COUNTRY_META[c].accent }} />{COUNTRY_META[c].name}</th>)}</tr>
        </thead>
        <tbody>
          <tr><th scope="row">H3 resolution</th>{cell((d) => `r${d.meta.h3_res}`)}</tr>
          <tr><th scope="row">Avg tile area</th>{cell((d) => `${fmtNumber(d.meta.avg_hex_km2)} km²`)}</tr>
          <tr><th scope="row">Tile width</th>{cell((d) => `~${fmtNumber(2 * getHexagonEdgeLengthAvg(d.meta.h3_res, UNITS.km))} km`)}</tr>
          <tr><th scope="row">Tiles (all)</th>{cell((d) => fmtInt(d.meta.hex_count))}</tr>
          <tr><th scope="row">Land tiles</th>{cell((d) => fmtInt(d.meta.land_hex_count))}</tr>
          <tr><th scope="row">Sea tiles</th>{cell((d) => fmtInt(d.meta.hex_count - d.meta.land_hex_count))}</tr>
          <tr><th scope="row">Admin levels</th>{cell((d) => Object.values(d.meta.admin_levels).map((a) => <div key={a.label}>{fmtInt(a.count)} {plural(a.label, a.count)}</div>))}</tr>
          <tr><th scope="row">Metrics</th>{cell((d) => fmtInt(d.meta.metrics.length))}</tr>
        </tbody>
      </table>
      <p className="muted sources-built">
        {idx ? <>Built {idx.built_at.slice(0, 16).replace('T', ' ')} UTC.</> : null} Re-running the pipeline rebuilds every file counted here from the cached downloads.
      </p>
    </details>
  )
}
