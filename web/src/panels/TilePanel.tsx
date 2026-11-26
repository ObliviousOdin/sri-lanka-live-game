// Inspect tab: tile / admin-unit inspector and, with nothing selected, a Civilization summary.
import { useMemo } from 'react'
import { cellToLatLng, getHexagonEdgeLengthAvg, UNITS } from 'h3-js'
import { COUNTRY_META, DISTRICT_META, TERRAIN_META, YIELD_META, terrainLabel } from '../civ'
import { rgbCss } from '../color'
import { SourceChips } from '../components/SourceChip'
import { YieldIcon, YieldRow } from '../components/YieldIcon'
import { adminUnitOfHex, hexesOfAdmin } from '../data/load'
import type { AppData } from '../data/useData'
import { useStore, type PickLevel } from '../store'
import { COUNTRIES, YIELDS, type CountryData, type CountryId, type IndexJson, type Selection, type YieldKey } from '../types'
import { fmtInt, fmtNumber, fmtUsd } from '../format'
import { MetricGroups, type MetricRowData } from './TilePanelMetrics'
import {
  adminParents, adminRank, explainYields, fmtDensity, fmtVal, hasDensity, hexPercentile,
  plural, POINT_LAYER_META, pointsInHex, pointValue, type YieldExplain,
} from './tilePanelStats'
import './TilePanel.css'

const ordinal = (p: number) => {
  const n = Math.round(p)
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${s}`
}

export function TilePanel({ app }: { app: AppData }) {
  const selection = useStore((s) => s.selection)
  const idx = app.index
  if (!idx) return <div className="loading">Loading…</div>
  if (!selection) return <Overview app={app} idx={idx} />
  const d = app.countries[selection.country]
  if (!d) return <div className="loading">Loading {COUNTRY_META[selection.country].name}…</div>
  if (selection.kind === 'hex') {
    const i = d.hexIndex.get(selection.id)
    if (i == null) return <NotFound what={`Tile ${selection.id}`} />
    return <HexInspector key={`${d.id}:${i}`} d={d} idx={idx} i={i} />
  }
  if (selection.kind === 'admin') {
    const [level, s] = selection.id.split(':')
    const ui = Number(s)
    if (!d.admin[level]?.units[ui]) return <NotFound what={`Unit ${selection.id}`} />
    return <AdminInspector key={`${d.id}:${selection.id}`} d={d} idx={idx} level={level} ui={ui} />
  }
  return <OtherSelection d={d} sel={selection} />
}

function NotFound({ what }: { what: string }) {
  return (
    <div className="tilepanel">
      <div className="card"><p>{what} is not in the current data export (the pipeline may have regenerated the grid).</p>
        <button className="btn" onClick={() => useStore.getState().select(null)}>Clear selection</button></div>
    </div>
  )
}

function CountryChip({ id }: { id: CountryId }) {
  return <span className={`chip ${id}`}>{COUNTRY_META[id].name}</span>
}

function CloseButton() {
  return (
    <button className="tilepanel-close" onClick={() => useStore.getState().select(null)} aria-label="Clear selection" title="Clear selection (Esc)">✕</button>
  )
}

// ---------------------------------------------------------------------------------------------
// Hex
// ---------------------------------------------------------------------------------------------
function HexInspector({ d, idx, i }: { d: CountryData; idx: IndexJson; i: number }) {
  const h3 = d.hexes.h3[i]
  const country = d.id
  const t = idx.terrain_codes[d.hexes.terrain[i]] ?? 'plains'
  const f = idx.feature_codes[d.hexes.feature[i]] ?? ''
  const hills = !!d.hexes.hills[i]
  const ring = d.hexes.ring[i]
  const dist = idx.districts[d.hexes.district[i]] ?? idx.districts[0]
  const allDistricts = (d.hexes.districts_all?.[i] ?? '').split(',').filter(Boolean)
  const [lat, lon] = useMemo(() => cellToLatLng(h3), [h3])
  const yields = useMemo(() => Object.fromEntries(YIELDS.map((y) => [y, d.hexes.yields?.[y]?.[i] ?? 0])) as Record<YieldKey, number>, [d, i])
  const explain = useMemo(() => explainYields(d, idx, i), [d, idx, i])
  const facilities = useMemo(() => pointsInHex(d, h3), [d, h3])
  const wonders = useMemo(() => d.wonders.filter((w) => w.h3 === h3), [d, h3])
  const crumbs = useMemo(() => Object.keys(d.meta.admin_levels).map((lvl) => ({ lvl, u: adminUnitOfHex(d, lvl, i) })).filter((c) => c.u), [d, i])
  const terrainRgb = TERRAIN_META[t]?.rgb ?? [120, 120, 120]
  const scope = `Percentiles rank this tile among ${d.meta.name}'s ${fmtInt(d.meta.land_hex_count)} land tiles (extensive metrics per km², like the map lens).`

  const rows = useMemo<MetricRowData[]>(() => d.meta.metrics.map((m) => {
    const raw = d.matrix[m.col * d.n + i]
    return {
      key: m.key, metric: m, label: m.label, category: m.category || 'other', raw,
      valueText: fmtVal(raw, m.unit),
      densityText: hasDensity(m) && Number.isFinite(raw) ? fmtDensity(raw / d.hexArea[i], m.unit) : undefined,
      rank: () => {
        const { pct, n } = hexPercentile(d, m, i)
        if (pct == null) return null
        return { pct, text: `P${Math.round(pct)}`, title: `${ordinal(pct)} percentile of ${fmtInt(n)} ${d.meta.name} land tiles${hasDensity(m) ? ' (per km²)' : ''}` }
      },
    }
  }), [d, i])

  return (
    <div className="tilepanel">
      <header className="tilepanel-head">
        <div className="tilepanel-title">
          <span className="tilepanel-hex" style={{ background: rgbCss(terrainRgb) }} aria-hidden />
          <h2>{terrainLabel(t, f, hills)}</h2>
          <CloseButton />
        </div>
        <div className="row tilepanel-sub">
          <CountryChip id={country} />
          <span className="chip">{ring === 0 ? 'Land tile' : ring === 1 ? 'Coastal waters' : `Open sea (ring ${ring})`}</span>
          <span className="chip">H3 r{d.meta.h3_res} · {fmtNumber(d.hexArea[i])} km²</span>
        </div>
        {crumbs.length > 0 && (
          <nav className="tilepanel-crumbs" aria-label="Administrative units">
            {crumbs.map(({ lvl, u }, k) => (
              <span key={lvl}>
                {k > 0 && <span className="tilepanel-sep" aria-hidden>›</span>}
                <button onClick={() => useStore.getState().select({ country, kind: 'admin', id: `${lvl}:${u!.index}` }, 'tile')}
                  title={`Inspect ${d.meta.admin_levels[lvl]?.label ?? lvl} ${u!.unit.name}`}>
                  {u!.unit.name}
                </button>
                <span className="tilepanel-lvl">{d.meta.admin_levels[lvl]?.label}</span>
              </span>
            ))}
          </nav>
        )}
        <div className="tilepanel-ids">
          <code title="H3 cell index">{h3}</code>
          <span>{lat.toFixed(4)}°, {lon.toFixed(4)}°</span>
          <button className="tilepanel-link" onClick={() => useStore.getState().fly(country, lon, lat)}>Zoom to</button>
        </div>
      </header>

      <div className="card tilepanel-yieldcard">
        <YieldRow values={yields} size={20} />
      </div>

      <div className="card tilepanel-district">
        {dist.code ? (
          <>
            <div className="tilepanel-dname">
              <span aria-hidden className="tilepanel-dicon">{DISTRICT_META[dist.code]?.icon}</span>
              <strong>{dist.label || DISTRICT_META[dist.code]?.label}</strong>
              <span className="muted">district</span>
            </div>
            <p className="tilepanel-rule">{dist.rule}</p>
          </>
        ) : (
          <div className="tilepanel-dname muted">No specialist district on this tile</div>
        )}
        {allDistricts.length > 1 && (
          <div className="tilepanel-dall">
            <span className="muted">Qualifies for:</span>
            {allDistricts.map((c) => {
              const def = idx.districts.find((x) => x.code === c)
              return <span key={c} className="chip" title={def?.rule}>{DISTRICT_META[c]?.icon} {def?.label || DISTRICT_META[c]?.label || c}</span>
            })}
          </div>
        )}
      </div>

      <YieldBreakdown explain={explain} idx={idx} />

      {(facilities.length > 0 || wonders.length > 0) && (
        <div className="card">
          <h3>In this tile</h3>
          <ul className="tilepanel-facilities">
            {wonders.map((w) => (
              <li key={`w-${w.category}-${w.name}`}>
                <button className="tilepanel-link" onClick={() => useStore.getState().select({ country, kind: 'wonder', id: w.name }, 'wonders')}>
                  🏯 {w.name}
                </button>
                <span className="muted"> · {w.category} #{w.rank}{w.value != null ? ` · ${fmtNumber(w.value)} ${w.unit}` : ''}</span>
              </li>
            ))}
            {facilities.slice(0, 12).map((p) => {
              const meta = POINT_LAYER_META[p.layer] ?? { label: p.layer, icon: '•' }
              const v = pointValue(p)
              return (
                <li key={`${p.layer}-${p.row.id}`}>
                  <span aria-hidden>{meta.icon}</span> {p.row.name}
                  <span className="muted"> · {meta.label}{Number.isFinite(v) ? ` · ${fmtNumber(v)} ${meta.unit ?? ''}` : ''}</span>
                </li>
              )
            })}
            {facilities.length > 12 && <li className="muted">+ {facilities.length - 12} more facilities</li>}
          </ul>
        </div>
      )}

      <h3 className="section-title">All metrics</h3>
      <MetricGroups rows={rows} scopeLabel={scope} />
    </div>
  )
}

// open by default on desktop, collapsed in the phone bottom sheet; remembers the user's last toggle
let whyOpen: boolean | null = null

function YieldBreakdown({ explain, idx }: { explain: YieldExplain[]; idx: IndexJson }) {
  const open = whyOpen ?? (typeof window === 'undefined' || window.innerWidth >= 900)
  return (
    <details className="card tilepanel-why" open={open} onToggle={(e) => { whyOpen = (e.currentTarget as HTMLDetailsElement).open }}>
      <summary><h3>Why these yields</h3></summary>
      <p className="tilepanel-formula">
        Yield = ⌊2·log<sub>10</sub>(density ÷ base)⌋ + 1 when density ≥ base — one point per ×3.16 — capped at {idx.yield_scale.cap}, plus facility bonuses. The same scale is used in both countries.
      </p>
      {explain.map((e) => (
        <div key={e.key} className="tilepanel-ybox">
          <div className="tilepanel-yhead">
            <YieldIcon y={e.key} size={16} />
            <strong>{YIELD_META[e.key].label}</strong>
            <span className="tilepanel-ytotal" style={{ color: YIELD_META[e.key].color }}>{e.actual}</span>
            {e.computed !== e.actual && (
              <span className="tilepanel-ydiff" title="Recomputed in the browser from the published metrics and point layers. Differences come from facilities not shipped to the browser or data that changed since the yields were built.">
                recomputed {e.computed}
              </span>
            )}
          </div>
          <ul className="tilepanel-parts">
            {e.parts.map((p) => (
              <li key={p.label} className={`${p.counted ? '' : 'is-uncounted'} ${p.missing ? 'is-missing' : ''}`}>
                <div className="tilepanel-part-main">
                  <span className="tilepanel-part-label">{p.label}</span>
                  <span className="tilepanel-part-amt">{p.amount}</span>
                  <span className="tilepanel-part-pts">{p.counted ? `+${p.points}` : `(${p.points})`}</span>
                </div>
                <div className="tilepanel-part-detail">
                  {p.detail}
                  {p.sources && p.sources.length > 0 && <> <SourceChips ids={p.sources} /></>}
                </div>
              </li>
            ))}
          </ul>
          {e.note && <div className="tilepanel-ynote">{e.note}</div>}
        </div>
      ))}
    </details>
  )
}

// ---------------------------------------------------------------------------------------------
// Admin unit
// ---------------------------------------------------------------------------------------------
function AdminInspector({ d, idx, level, ui }: { d: CountryData; idx: IndexJson; level: string; ui: number }) {
  const table = d.admin[level]
  const unit = table.units[ui]
  const country = d.id
  const levels = Object.keys(d.meta.admin_levels)
  const levelLabel = d.meta.admin_levels[level]?.label ?? table.label ?? level
  const parents = useMemo(() => adminParents(d, level, ui), [d, level, ui])
  const tiles = useMemo(() => hexesOfAdmin(d, level, ui), [d, level, ui])
  const agg = useMemo(() => {
    const y = Object.fromEntries(YIELDS.map((k) => [k, 0])) as Record<YieldKey, number>
    const dc = new Map<string, number>()
    for (const i of tiles) {
      for (const k of YIELDS) y[k] += d.hexes.yields?.[k]?.[i] ?? 0
      const code = idx.districts[d.hexes.district[i]]?.code
      if (code) dc.set(code, (dc.get(code) ?? 0) + 1)
    }
    return { yields: y, districts: [...dc.entries()].sort((a, b) => b[1] - a[1]) }
  }, [d, idx, tiles])
  const children = useMemo(() => {
    const cl = levels[levels.indexOf(level) + 1]
    const ct = cl ? d.admin[cl] : undefined
    if (!ct) return null
    const pop = ct.metrics.pop
    const list = ct.units.map((u, k) => ({ u, k, pop: pop?.[k] ?? 0 })).filter((x) => x.u.parent === unit.id)
    list.sort((a, b) => (b.pop ?? 0) - (a.pop ?? 0))
    return { level: cl, label: d.meta.admin_levels[cl]?.label ?? cl, list }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, level, unit])
  const landKm2 = table.metrics.land_km2?.[ui] ?? null
  const pop = table.metrics.pop?.[ui] ?? null
  const gdp = table.metrics.gdp_usd?.[ui] ?? null
  const scope = `Ranks compare this ${levelLabel.toLowerCase()} with all ${fmtInt(table.units.length)} ${levelLabel.toLowerCase()} units of ${d.meta.name} (#1 = highest value).`

  const rows = useMemo<MetricRowData[]>(() => Object.keys(table.metrics).map((key) => {
    const m = d.metricByKey.get(key)
    const raw = table.metrics[key][ui] ?? NaN
    const unitStr = m?.unit ?? ''
    const dens = m && hasDensity(m) && landKm2 && landKm2 > 0 && Number.isFinite(raw) ? raw / landKm2 : NaN
    return {
      key, metric: m, label: m?.label ?? key, category: m?.category || 'other', raw,
      valueText: fmtVal(raw, unitStr),
      densityText: Number.isFinite(dens) ? fmtDensity(dens, unitStr) : undefined,
      rank: () => {
        const r = adminRank(table, key, ui)
        if (!r) return null
        const tie = r.ties > 1
        return {
          pct: r.pct, text: `#${fmtInt(r.rank)}${tie ? '=' : ''}/${fmtInt(r.n)}`,
          title: `#${fmtInt(r.rank)} of ${fmtInt(r.n)} ${levelLabel.toLowerCase()} units with data (highest first)${tie ? `, tied with ${fmtInt(r.ties - 1)} other${r.ties > 2 ? 's' : ''}` : ''}`,
        }
      },
    }
  }), [d, table, ui, landKm2, levelLabel])

  const zoom = ((country === 'lka' ? { adm1: 8.4, adm2: 9, adm3: 10.5 } : { adm1: 5.5, adm2: 8 }) as Record<string, number>)[level]

  return (
    <div className="tilepanel">
      <header className="tilepanel-head">
        <div className="tilepanel-title">
          <span className="tilepanel-hex is-admin" aria-hidden />
          <h2>{unit.name}</h2>
          <CloseButton />
        </div>
        <div className="row tilepanel-sub">
          <CountryChip id={country} />
          <span className="chip">{levelLabel}</span>
          <code className="tilepanel-code" title="Admin code">{unit.id}</code>
        </div>
        <nav className="tilepanel-crumbs" aria-label="Parent units">
          <span><span className="tilepanel-plain">{d.meta.name}</span></span>
          {parents.map((p) => (
            <span key={p.level}>
              <span className="tilepanel-sep" aria-hidden>›</span>
              <button onClick={() => useStore.getState().select({ country, kind: 'admin', id: `${p.level}:${p.index}` }, 'tile')}
                title={`Inspect ${d.meta.admin_levels[p.level]?.label ?? p.level} ${p.name}`}>{p.name}</button>
              <span className="tilepanel-lvl">{d.meta.admin_levels[p.level]?.label}</span>
            </span>
          ))}
          <span><span className="tilepanel-sep" aria-hidden>›</span><span className="tilepanel-plain">{unit.name}</span></span>
        </nav>
        <div className="tilepanel-ids">
          <span>{unit.lat.toFixed(3)}°, {unit.lon.toFixed(3)}°</span>
          <button className="tilepanel-link" onClick={() => useStore.getState().fly(country, unit.lon, unit.lat, zoom)}>Zoom to</button>
        </div>
      </header>

      <div className="card">
        <div className="tilepanel-stats">
          <div><span className="k">Population</span><b>{pop != null ? fmtNumber(pop) : '–'}</b></div>
          <div><span className="k">Land</span><b>{landKm2 != null ? `${fmtNumber(landKm2)} km²` : '–'}</b></div>
          <div><span className="k">GDP</span><b>{gdp != null ? fmtUsd(gdp) : '–'}</b></div>
          <div><span className="k">Tiles</span><b>{fmtInt(tiles.length)}</b></div>
        </div>
        <div className="tilepanel-agg">
          <div className="muted tilepanel-small">Summed yields of its {fmtInt(tiles.length)} tiles</div>
          <YieldRow values={agg.yields} size={18} />
        </div>
        {agg.districts.length > 0 && (
          <div className="tilepanel-dall">
            <span className="muted">Districts:</span>
            {agg.districts.map(([c, n]) => (
              <span key={c} className="chip" title={idx.districts.find((x) => x.code === c)?.rule}>{DISTRICT_META[c]?.icon} {DISTRICT_META[c]?.label ?? c} × {n}</span>
            ))}
          </div>
        )}
        <p className="muted tilepanel-small">Tiles are those whose dominant {levelLabel.toLowerCase()} (by population) is {unit.name}.</p>
      </div>

      {children && children.list.length > 0 && (
        <div className="card">
          <h3>{children.list.length} {plural(children.label, children.list.length)}</h3>
          <div className="tilepanel-children">
            {children.list.slice(0, 16).map(({ u, k, pop: p }) => (
              <button key={u.id} className="chip tilepanel-childchip" onClick={() => useStore.getState().select({ country, kind: 'admin', id: `${children.level}:${k}` }, 'tile')}
                title={p ? `${fmtNumber(p)} people` : undefined}>
                {u.name}
              </button>
            ))}
            {children.list.length > 16 && <span className="muted tilepanel-small">+ {children.list.length - 16} more</span>}
          </div>
        </div>
      )}

      <h3 className="section-title">All {levelLabel.toLowerCase()} metrics</h3>
      <MetricGroups rows={rows} scopeLabel={scope} />
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// City / wonder selections
// ---------------------------------------------------------------------------------------------
function OtherSelection({ d, sel }: { d: CountryData; sel: Selection }) {
  const isCity = sel.kind === 'city'
  const city = isCity ? d.cities.find((c) => c.id === sel.id) : undefined
  const wonder = !isCity ? d.wonders.find((w) => w.name === sel.id) : undefined
  const name = city?.name ?? wonder?.name ?? sel.id
  const tileH3 = city?.centre ?? wonder?.h3
  return (
    <div className="tilepanel">
      <header className="tilepanel-head">
        <div className="tilepanel-title">
          <span aria-hidden className="tilepanel-dicon">{isCity ? '🏛️' : '🏯'}</span>
          <h2>{name}</h2>
          <CloseButton />
        </div>
        <div className="row tilepanel-sub"><CountryChip id={d.id} /><span className="chip">{isCity ? 'City' : wonder?.category ?? 'Wonder'}</span></div>
      </header>
      <div className="card">
        <p>{isCity ? 'This is a metro selection. Its tiles, citizens, districts and yields are shown in City View.' : 'This is a wonder. Its ranking rule, value and source are shown in the Wonders tab.'}</p>
        <div className="row">
          <button className="btn primary" onClick={() => useStore.getState().set({ panel: isCity ? 'city' : 'wonders', panelOpen: true })}>
            Open {isCity ? 'City View' : 'Wonders'}
          </button>
          {tileH3 && d.hexIndex.has(tileH3) && (
            <button className="btn" onClick={() => useStore.getState().select({ country: d.id, kind: 'hex', id: tileH3 }, 'tile')}>
              Inspect its {isCity ? 'centre ' : ''}tile
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// No selection: how-to + Civilization summary
// ---------------------------------------------------------------------------------------------
const PICKS: { v: PickLevel; label: string }[] = [
  { v: 'hex', label: 'Tile' }, { v: 'adm1', label: 'Province / state' }, { v: 'adm2', label: 'District / county' },
]

function Overview({ app, idx }: { app: AppData; idx: IndexJson }) {
  const pickLevel = useStore((s) => s.pickLevel)
  return (
    <div className="tilepanel">
      <div className="card tilepanel-howto">
        <h3>Inspect a tile</h3>
        <ul>
          <li><b>Click any hex</b> on either map to see its terrain, district, yields — and exactly which statistics produced them — plus every metric with its percentile in the country.</li>
          <li>Change <b>Click:</b> in the top bar (or here) to select whole provinces / states, districts / counties or DS divisions instead.</li>
          <li><b>Lenses</b> above the maps recolour both countries; “Shared scale” puts them on one colour ramp. Esc clears the selection.</li>
        </ul>
        <div className="tilepanel-pick" role="group" aria-label="Click selects">
          <span className="muted">Click selects:</span>
          {PICKS.map((p) => (
            <button key={p.v} className={`btn ${pickLevel === p.v ? 'primary' : ''}`} aria-pressed={pickLevel === p.v} onClick={() => useStore.getState().set({ pickLevel: p.v })}>{p.label}</button>
          ))}
        </div>
      </div>
      <h3 className="section-title">Civilization summary</h3>
      <div className="tilepanel-civgrid">
        {COUNTRIES.map((c) => <CivSummary key={c} id={c} d={app.countries[c]} idx={idx} />)}
      </div>
      <p className="muted tilepanel-small">
        Yields are computed per tile from per-km² densities on a shared log scale, so totals grow with both land area and intensity.
        Data built {idx.built_at.slice(0, 16).replace('T', ' ')} UTC. <SourceChips ids={['worldpop_2020', 'uber_h3']} />
      </p>
    </div>
  )
}

function CivSummary({ id, d, idx }: { id: CountryId; d?: CountryData; idx: IndexJson }) {
  const cm = COUNTRY_META[id]
  if (!d) {
    return (
      <section className={`card tilepanel-civ ${id}`} aria-label={`${cm.name} summary`}>
        <h3><span className="tilepanel-dot" style={{ background: cm.accent }} />{cm.name}</h3>
        <p className="muted">Loading tiles…</p>
      </section>
    )
  }
  const m = d.meta
  const pop = m.totals.pop ?? NaN
  const land = m.totals.land_km2 ?? NaN
  const gdp = m.totals.gdp_usd
  const sea = m.hex_count - m.land_hex_count
  const edge = getHexagonEdgeLengthAvg(m.h3_res, UNITS.km)
  const terr = Object.entries(m.terrain_counts ?? {}).sort((a, b) => b[1] - a[1])
  const terrTotal = terr.reduce((s, [, n]) => s + n, 0) || 1
  const dists = Object.entries(m.district_counts ?? {}).filter(([k]) => k).sort((a, b) => b[1] - a[1]).slice(0, 8)
  const per = (v: number, denom: number) => (denom > 0 ? v / denom : NaN)
  return (
    <section className={`card tilepanel-civ ${id}`} aria-label={`${cm.name} summary`}>
      <h3><span className="tilepanel-dot" style={{ background: cm.accent }} />{cm.name}</h3>
      <dl className="tilepanel-civstats">
        <div><dt>Population</dt><dd>{fmtNumber(pop)}</dd></div>
        <div><dt>Land area</dt><dd>{fmtNumber(land)} km²</dd></div>
        <div><dt>GDP</dt><dd>{gdp != null ? fmtUsd(gdp) : <span className="muted">pending</span>}</dd></div>
        <div><dt>Tiles</dt><dd>{fmtInt(m.land_hex_count)} land · {fmtInt(sea)} sea</dd></div>
        <div><dt>Grid</dt><dd>H3 r{m.h3_res} · {fmtNumber(m.avg_hex_km2)} km² · ~{fmtNumber(edge * 2)} km across</dd></div>
      </dl>
      <table className="tilepanel-ytable">
        <caption className="tilepanel-sr">Yield totals for {cm.name}</caption>
        <thead><tr><th scope="col"><span className="tilepanel-sr">Yield</span></th><th scope="col">Total</th><th scope="col" title="per 1,000 km² of land">/1k km²</th><th scope="col" title="per million residents">/1M ppl</th></tr></thead>
        <tbody>
          {YIELDS.map((y) => {
            const v = m.yield_totals?.[y] ?? 0
            return (
              <tr key={y} className={v ? '' : 'is-zero'}>
                <th scope="row"><YieldIcon y={y} size={13} /></th>
                <td>{fmtNumber(v)}</td>
                <td>{fmtNumber(per(v, land / 1000))}</td>
                <td>{fmtNumber(per(v, pop / 1e6))}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="tilepanel-minihead">Terrain</div>
      <div className="tilepanel-tbar" aria-hidden>
        {terr.map(([k, n]) => <i key={k} style={{ width: `${(n / terrTotal) * 100}%`, background: rgbCss(TERRAIN_META[k]?.rgb ?? [120, 120, 120]) }} />)}
      </div>
      <ul className="tilepanel-mini">
        {terr.slice(0, 5).map(([k, n]) => (
          <li key={k}>
            <i className="tilepanel-sw" style={{ background: rgbCss(TERRAIN_META[k]?.rgb ?? [120, 120, 120]) }} />
            <span className="tilepanel-mini-label">{TERRAIN_META[k]?.label ?? idx.terrain[k] ?? k}</span>
            <span className="tilepanel-mini-val">{((n / terrTotal) * 100).toFixed(n / terrTotal < 0.1 ? 1 : 0)}%</span>
          </li>
        ))}
      </ul>
      <div className="tilepanel-minihead">Districts</div>
      {dists.length ? (
        <ul className="tilepanel-mini">
          {dists.map(([k, n]) => (
            <li key={k} title={idx.districts.find((x) => x.code === k)?.rule}>
              <span className="tilepanel-sw-icon" aria-hidden>{DISTRICT_META[k]?.icon}</span>
              <span className="tilepanel-mini-label">{DISTRICT_META[k]?.label ?? k}</span>
              <span className="tilepanel-mini-val">{fmtInt(n)}</span>
            </li>
          ))}
        </ul>
      ) : <p className="muted tilepanel-small">None yet</p>}
    </section>
  )
}
