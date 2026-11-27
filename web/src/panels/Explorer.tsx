// Explorer tab: a linked scatter plot + table of any two metrics, for tiles or admin units, in
// Sri Lanka, the United States or both. Click a point / row to select it and fly the map there;
// drag on the plot to brush (the map dims everything else).
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { COUNTRY_META, DISTRICT_META, terrainLabel } from '../civ'
import { SourceChips } from '../components/SourceChip'
import { YieldRow } from '../components/YieldIcon'
import type { AppData } from '../data/useData'
import { computeLens, LENS_BY_ID, lensBasis } from '../lenses'
import { useStore } from '../store'
import type { CountryData, CountryId, IndexJson, Metric } from '../types'
import {
  axisInfo, brushFlags, brushSets, buildPlot, buildSeries, fmtShort, fmtVal, hasBrush, levelNoun, metricOptions,
  provenance, resolveKey, rowLonLat, rowName, segOf, selectionTargets, unitCaption, X_PREFS, Y_PREFS,
  type AxisInfo, type BrushMemo, type ColorBy, type ExLevel, type MetricOptions, type Norm, type PlotData, type Targets,
} from './explorerData'
import { ExplorerPlot } from './ExplorerPlot'
import './Explorer.css'

type ExState = ReturnType<typeof useStore.getState>['explorer']
/** Extra Explorer settings kept in store.explorer (not yet declared in store.ts; merged by setExplorer). */
interface ExExtra { adminLevel?: 'adm1' | 'adm2'; norm?: Norm; colorBy?: ColorBy }
type Ex = ExState & ExExtra
const setEx = (p: Partial<Ex>) => useStore.getState().setExplorer(p)

const TABLE_ROWS = 200
const FLY_ZOOM: Record<string, Record<CountryId, number | undefined>> = {
  hex: { lka: undefined, usa: undefined }, adm2: { lka: 8, usa: 6.4 }, adm1: { lka: 7.4, usa: 5 },
}

export const Explorer = memo(function Explorer({ app }: { app: AppData }) {
  const ex = useStore((s) => s.explorer) as Ex
  const selection = useStore((s) => s.selection)
  const lensId = useStore((s) => s.lensId)
  const lensMetric = useStore((s) => s.lensMetric[s.lensId])
  const sharedScale = useStore((s) => s.sharedScale)
  const { lka, usa } = app.countries
  const idx = app.index

  const scope = ex.scope
  const wanted: CountryId[] = scope === 'both' ? ['lka', 'usa'] : [scope]
  const ds = useMemo(
    () => (scope === 'both' ? [lka, usa] : [scope === 'lka' ? lka : usa]).filter(Boolean) as CountryData[],
    [scope, lka, usa],
  )
  const pending = wanted.filter((c) => !app.countries[c])
  const level: ExLevel = ex.level === 'hex' ? 'hex' : ex.adminLevel ?? 'adm2'
  const norm: Norm = ex.norm ?? 'total'
  const colorBy: ColorBy = level === 'hex' ? ex.colorBy ?? 'country' : 'country'

  // ---- metrics & axes
  const opts = useMemo(() => metricOptions(ds, level), [ds, level])
  const xKey = resolveKey(ex.x, opts.byKey, X_PREFS, null)
  const yKey = resolveKey(ex.y, opts.byKey, Y_PREFS, xKey)
  const xOpt = xKey ? opts.byKey.get(xKey) : undefined
  const yOpt = yKey ? opts.byKey.get(yKey) : undefined
  const xInfo = useMemo(() => (xOpt ? axisInfo(xOpt.metric, level, norm) : null), [xOpt, level, norm])
  const yInfo = useMemo(() => (yOpt ? axisInfo(yOpt.metric, level, norm) : null), [yOpt, level, norm])

  // ---- data stages (each memoised separately)
  const series = useMemo(
    () => (xInfo && yInfo ? ds.map((d) => buildSeries(d, level, xInfo, yInfo)) : []),
    [ds, level, xInfo, yInfo],
  )
  const plot = useMemo(() => buildPlot(series, ex.logX, ex.logY), [series, ex.logX, ex.logY])
  const brushMemo = useRef<BrushMemo | null>(null)
  const brush = useMemo(() => brushFlags(plot, ex.brushed, level, brushMemo.current), [plot, ex.brushed, level])
  const targets = useMemo(() => selectionTargets(plot, selection, level), [plot, selection, level])
  const lensColors = useMemo(() => {
    if (colorBy !== 'lens' || !idx) return null
    const lens = LENS_BY_ID.get(lensId) ?? LENS_BY_ID.get('terrain')!
    const pool = Object.values(app.countries) as CountryData[]
    const out: Partial<Record<CountryId, Uint8Array>> = {}
    for (const d of ds) out[d.id] = computeLens(lens, lensMetric ?? null, d, idx, sharedScale ? pool : [d]).colors
    return out
  }, [colorBy, idx, lensId, lensMetric, sharedScale, ds, app.countries])

  // ---- actions
  const pick = useCallback((p: number) => {
    const sg = segOf(plot, p)
    if (!sg) return
    const d = sg.s.d, row = plot.row[p], c = sg.country
    const st = useStore.getState()
    if (level === 'hex') st.select({ country: c, kind: 'hex', id: d.hexes.h3[row] }, 'explorer')
    else st.select({ country: c, kind: 'admin', id: `${level}:${row}` }, 'explorer')
    const [lon, lat] = rowLonLat(d, level, row)
    const switchMap = st.mode !== 'split' && st.mode !== c
    if (switchMap) st.set({ mode: c })
    const go = () => useStore.getState().fly(c, lon, lat, FLY_ZOOM[level]?.[c])
    if (switchMap) setTimeout(go, 400)
    else go()
  }, [plot, level])

  const onBrush = useCallback((pts: number[] | null) => {
    if (!pts || !pts.length) {
      if (hasBrush(useStore.getState().explorer.brushed)) { brushMemo.current = null; setEx({ brushed: {} }) }
      return
    }
    const m = brushSets(plot, pts, level)
    brushMemo.current = m
    setEx({ brushed: m.brushed })
  }, [plot, level])
  const clearBrush = () => { brushMemo.current = null; setEx({ brushed: {} }) }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && hasBrush(useStore.getState().explorer.brushed)) { brushMemo.current = null; setEx({ brushed: {} }) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ---- tooltip
  const renderTip = useCallback((p: number) => (xInfo && yInfo ? <Tip plot={plot} p={p} level={level} xInfo={xInfo} yInfo={yInfo} idx={idx} /> : null),
    [plot, level, xInfo, yInfo, idx])
  const tipText = useCallback((p: number) => {
    const sg = segOf(plot, p)
    if (!sg || !xInfo || !yInfo) return ''
    return `${rowName(sg.s.d, level, plot.row[p])}, ${COUNTRY_META[sg.country].name}. ${xInfo.label}: ${fmtVal(plot.x[p], xInfo.unit)}. ${yInfo.label}: ${fmtVal(plot.y[p], yInfo.unit)}.`
  }, [plot, level, xInfo, yInfo])

  if (!ds.length) return <div className="loading">Loading {wanted.map((c) => COUNTRY_META[c].name).join(' and ')}…</div>

  const nounAll = levelNoun(ds, level)
  const anyExtensive = xInfo?.metric.kind === 'extensive' || yInfo?.metric.kind === 'extensive'
  const missingNotes = series.filter((s) => s.missing).map((s) => {
    const nm = COUNTRY_META[s.country].name
    if (s.missing === 'level') return `${nm} has no ${level} table.`
    const which = s.missing === 'both' ? `${xOpt?.label} or ${yOpt?.label}` : s.missing === 'x' ? xOpt?.label : yOpt?.label
    return `${nm}: no data for ${which}${level === 'hex' ? '' : ' at this level'}.`
  })
  const hidden = plot.segs.reduce((a, s) => a + s.hidden, 0)

  return (
    <div className="explorer">
      <header className="explorer-head">
        <h2>Explorer</h2>
        <p>Plot any two statistics for every tile or district. Click a point to inspect it on the map; drag to brush.</p>
      </header>

      <div className="explorer-controls">
        <span className="explorer-lab" id="ex-scope">Civs</span>
        <Seg labelledBy="ex-scope" value={scope} onChange={(v) => setEx({ scope: v })}
          items={[{ v: 'lka', label: 'Sri Lanka' }, { v: 'both', label: 'Both' }, { v: 'usa', label: 'United States' }]} />

        <span className="explorer-lab" id="ex-level">Rows</span>
        <Seg labelledBy="ex-level" value={level}
          onChange={(v) => setEx(v === 'hex' ? { level: 'hex' } : { level: 'admin', adminLevel: v })}
          items={[
            { v: 'hex', label: 'Tiles' },
            { v: 'adm2', label: levelNoun(ds, 'adm2') },
            { v: 'adm1', label: levelNoun(ds, 'adm1') },
          ]} />

        <AxisRow axis="X" k={xKey} opts={opts} log={plot.logX} blocked={plot.logBlocked.x}
          onKey={(k) => setEx({ x: k })} onLog={(b) => setEx({ logX: b })} lensActive={lensId === 'custom' && lensMetric === xKey} />
        <AxisRow axis="Y" k={yKey} opts={opts} log={plot.logY} blocked={plot.logBlocked.y}
          onKey={(k) => setEx({ y: k })} onLog={(b) => setEx({ logY: b })} lensActive={lensId === 'custom' && lensMetric === yKey} />

        {level !== 'hex' && anyExtensive && (
          <>
            <span className="explorer-lab" id="ex-norm">Totals</span>
            <Seg labelledBy="ex-norm" value={norm} onChange={(v) => setEx({ norm: v })}
              items={[{ v: 'total', label: 'Total' }, { v: 'density', label: 'Per km² of land', title: 'Divided by each unit’s land area' }, { v: 'capita', label: 'Per capita' }]} />
          </>
        )}
        <span className="explorer-lab" id="ex-colour">Colour</span>
        <div className="explorer-inline">
          <Seg labelledBy="ex-colour" value={colorBy} onChange={(v) => setEx({ colorBy: v })}
            items={[{ v: 'country', label: 'By civ' }, { v: 'lens', label: 'Map lens', disabled: level !== 'hex', title: 'Tiles only: colours points like the map' }]} />
          {xKey && yKey && (
            <button className="btn explorer-swap" onClick={() => setEx({ x: yKey, y: xKey, logX: ex.logY, logY: ex.logX })}
              title="Swap X and Y" aria-label="Swap X and Y metrics">⇄ Swap</button>
          )}
        </div>
      </div>

      {pending.length > 0 && <p className="explorer-note">{pending.map((c) => COUNTRY_META[c].name).join(', ')} still loading…</p>}
      {!xInfo || !yInfo ? (
        <div className="card">No metrics are available for this scope yet. The data pipeline may still be running.</div>
      ) : (
        <>
          <div className="explorer-axis-title y" title={yInfo.metric.description}>
            <b>Y</b> {yInfo.label} <span className="muted">· {unitCaption(yInfo.unit)}{plot.logY ? ' · log' : ''}</span>
          </div>
          <ExplorerPlot
            plot={plot}
            xUnit={xInfo.unit}
            yUnit={yInfo.unit}
            colorBy={colorBy}
            lensColors={lensColors}
            brush={brush}
            targets={targets}
            onPick={pick}
            onBrush={onBrush}
            renderTip={renderTip}
            tipText={tipText}
            ariaLabel={`Scatter plot of ${yInfo.label} against ${xInfo.label}, ${plot.n} ${nounAll.toLowerCase()}. Arrow keys step through points, Enter selects, drag to brush, Escape clears the brush.`}
          />
          <div className="explorer-axis-title x">
            <b>X</b> {xInfo.label} <span className="muted">· {unitCaption(xInfo.unit)}{plot.logX ? ' · log' : ''}</span>
          </div>

          <div className="explorer-stats" aria-label="Correlation by country">
            {plot.segs.map((sg) => (
              <div key={sg.country} className="explorer-stat">
                <i className={`explorer-swatch ${sg.country}`} aria-hidden />
                <b>{COUNTRY_META[sg.country].name}</b>
                {sg.fit ? (
                  Number.isFinite(sg.fit.r)
                    ? <span>r = <b>{sg.fit.r.toFixed(2)}</b> · slope {fmtSlope(sg.fit.slope)}{plot.logX && plot.logY ? ' (log–log)' : ''}</span>
                    : <span className="muted">r n/a (Y is constant)</span>
                ) : <span className="muted">too few points for a fit</span>}
                <span className="muted">n = {(sg.end - sg.start).toLocaleString()}</span>
              </div>
            ))}
            {(hidden > 0 || missingNotes.length > 0) && (
              <div className="explorer-note">
                {hidden > 0 && <span>{hidden.toLocaleString()} {levelNoun(ds, level, hidden !== 1).toLowerCase()} with values ≤ 0 {hidden === 1 ? 'is' : 'are'} hidden on the log axis. </span>}
                {missingNotes.join(' ')}
              </div>
            )}
            <div className="explorer-note">Dashed lines: least-squares fit per civ{plot.logX || plot.logY ? ' on the log scale shown' : ''}.{level === 'hex' ? ` Totals are shown per km² of tile so ${ds.map((d) => `${Math.round(d.meta.avg_hex_km2)} km²`).join(' and ')} tiles compare.` : ''}</div>
          </div>

          <div className="explorer-brushbar row" aria-live="polite">
            {brush ? (
              <>
                <span><b>{brush.points.toLocaleString()}</b> brushed{level !== 'hex' ? ` · ${brush.tiles.toLocaleString()} map tiles` : ''}</span>
                <button className="btn" onClick={clearBrush}>Clear</button>
                <span className="muted">Esc also clears</span>
              </>
            ) : (
              <span className="muted">Drag across the plot to brush points and dim the rest of the map.</span>
            )}
          </div>

          <div className="explorer-defs">
            <MetricDef axis="X" info={xInfo} ds={ds} level={level} log={plot.logX} onMap={() => useStore.getState().setLens('custom', xInfo.key)} />
            <MetricDef axis="Y" info={yInfo} ds={ds} level={level} log={plot.logY} onMap={() => useStore.getState().setLens('custom', yInfo.key)} />
          </div>

          <ExplorerTable plot={plot} level={level} xInfo={xInfo} yInfo={yInfo} brush={brush} targets={targets}
            idx={idx} onPick={pick} noun={nounAll} />
        </>
      )}
    </div>
  )
})

const fmtSlope = (v: number) => (Math.abs(v) >= 100 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(1) : v.toFixed(2))

// ---------------------------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------------------------

function Seg<T extends string>({ items, value, onChange, labelledBy }: {
  items: { v: T; label: string; disabled?: boolean; title?: string }[]
  value: T
  onChange: (v: T) => void
  labelledBy: string
}) {
  return (
    <div className="explorer-seg" role="group" aria-labelledby={labelledBy}>
      {items.map((it) => (
        <button key={it.v} type="button" aria-pressed={value === it.v} disabled={it.disabled} title={it.title}
          onClick={() => onChange(it.v)}>{it.label}</button>
      ))}
    </div>
  )
}

/**
 * Tooltip of a "Map X/Y" button. The map's "Any metric" lens keeps its own scale rule (lenses.ts:
 * per-km² densities always log, other skewed positive metrics auto-log when they span ≥ 20×);
 * the lens API takes no log flag, so the Explorer's switch cannot be handed over — say so.
 */
function mapScaleTitle(m: Metric | undefined, plotLog: boolean, axis: 'X' | 'Y'): string {
  if (!m) return `Colour the map by this ${axis} metric`
  const b = lensBasis(m)
  const rule = b === 'density' ? 'always on a log ramp (per km² of tile)'
    : b === 'share' ? 'on a linear ramp (share of the tile)'
      : 'on a log ramp only if its values span 20× or more, otherwise linear'
  return `Colour the map by ${m.label}. The map draws it ${rule}; the plot's log switch (now ${plotLog ? 'on' : 'off'}) does not carry over.`
}

function AxisRow({ axis, k, opts, log, blocked, onKey, onLog, lensActive }: {
  axis: 'X' | 'Y'
  k: string | null
  opts: MetricOptions
  log: boolean
  blocked: string | null
  onKey: (k: string) => void
  onLog: (b: boolean) => void
  lensActive: boolean
}) {
  const both = new Set(opts.groups.flatMap((g) => g.opts.flatMap((o) => o.countries))).size > 1
  return (
    <>
      <label className="explorer-lab" htmlFor={`ex-${axis}`}>{axis}</label>
      <div className="explorer-axis">
        <select id={`ex-${axis}`} className="input" aria-label={`${axis} metric`} value={k ?? ''} onChange={(e) => onKey(e.target.value)}>
          {opts.groups.map((g) => (
            <optgroup key={g.category} label={g.label}>
              {g.opts.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}{both && o.countries.length === 1 ? ` (${COUNTRY_META[o.countries[0]].short} only)` : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <label className={`explorer-log ${blocked ? 'blocked' : ''}`} title={blocked ? `Log scale off: ${blocked}` : `Logarithmic ${axis} axis`}>
          <input type="checkbox" checked={log} disabled={!!blocked} aria-label={`Log ${axis} axis`}
            onChange={(e) => onLog(e.target.checked)} />
          log
        </label>
        <button type="button" className={`btn explorer-map ${lensActive ? 'on' : ''}`} disabled={!k} aria-pressed={lensActive}
          title={mapScaleTitle(k ? opts.byKey.get(k)?.metric : undefined, log, axis)} onClick={() => k && useStore.getState().setLens('custom', k)}>
          Map {axis}
        </button>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------------------------

function Tip({ plot, p, level, xInfo, yInfo, idx }: {
  plot: PlotData; p: number; level: ExLevel; xInfo: AxisInfo; yInfo: AxisInfo; idx: IndexJson | null
}) {
  const sg = segOf(plot, p)
  if (!sg) return null
  const d = sg.s.d, row = plot.row[p], c = sg.country
  let sub: string | null = null
  let yields: number[] | null = null
  if (level === 'hex' && idx) {
    const t = idx.terrain_codes[d.hexes.terrain[row]] ?? ''
    const f = idx.feature_codes[d.hexes.feature[row]] ?? ''
    const dist = idx.districts[d.hexes.district[row]]?.code ?? ''
    sub = `${terrainLabel(t, f, !!d.hexes.hills[row])}${dist ? ` · ${DISTRICT_META[dist]?.icon ?? ''} ${DISTRICT_META[dist]?.label ?? dist}` : ''}`
    yields = (['gold', 'production', 'food', 'science', 'culture', 'faith'] as const).map((y) => d.hexes.yields?.[y]?.[row] ?? 0)
  } else if (level !== 'hex') {
    const pop = d.admin[level]?.metrics.pop?.[row]
    sub = `${d.admin[level]?.label ?? level}${pop != null ? ` · ${fmtVal(pop, 'persons')} people` : ''}`
  }
  return (
    <>
      <div className="explorer-tip-title">{rowName(d, level, row)}</div>
      <div className="explorer-tip-sub"><span className={`chip ${c}`}>{COUNTRY_META[c].name}</span> {sub}</div>
      {yields && <div className="explorer-tip-yields"><YieldRow values={yields} size={12} /></div>}
      <div className="explorer-tip-kv"><span>X · {xInfo.label}</span><b>{fmtVal(plot.x[p], xInfo.unit)}</b></div>
      <div className="explorer-tip-kv"><span>Y · {yInfo.label}</span><b>{fmtVal(plot.y[p], yInfo.unit)}</b></div>
      <div className="explorer-tip-hint">Click to select &amp; fly to</div>
    </>
  )
}

// ---------------------------------------------------------------------------------------------
// Definitions & provenance
// ---------------------------------------------------------------------------------------------

function MetricDef({ axis, info, ds, level, log, onMap }: { axis: 'X' | 'Y'; info: AxisInfo; ds: CountryData[]; level: ExLevel; log: boolean; onMap: () => void }) {
  const per = ds.flatMap((d) => {
    const m = d.metricByKey.get(info.key)
    return m ? [{ c: d.id, m, pv: provenance(d, info.key) }] : []
  })
  const m = info.metric
  const sameMethod = per.every((x) => x.m.method === per[0].m.method)
  const sameSources = per.every((x) => x.pv.sources.join() === per[0].pv.sources.join())
  const sameVintage = per.every((x) => x.pv.vintage === per[0].pv.vintage)
  const vintages = sameVintage ? [per[0]?.pv.vintage || 'n/a'] : per.map((x) => `${COUNTRY_META[x.c].short} ${x.pv.vintage || 'n/a'}`)
  const via = [...new Set(per.flatMap((x) => x.pv.via))]
  const tfNote = info.tf === 'density'
    ? (level === 'hex' ? 'Shown per km² of tile (value ÷ H3 tile area, including any water — the basis of yields and map lenses).' : 'Shown per km² of land (value ÷ the unit’s land area).')
    : info.tf === 'capita' ? 'Shown per resident (value ÷ population).'
      : m.kind === 'intensive' && m.weight === 'land' && /\/km2$/.test(m.unit) ? 'Measured per km² of land (excluding water), not per km² of tile.' : ''
  return (
    <section className="card explorer-def" aria-label={`${axis} metric definition`}>
      <div className="explorer-def-head">
        <span className="explorer-def-axis" aria-hidden>{axis}</span>
        <h3>{m.label}</h3>
        <button className="btn explorer-map" onClick={onMap} title={mapScaleTitle(m, log, axis)} aria-label={`Map ${axis}: colour the map by ${m.label}`}>Map {axis}</button>
      </div>
      <p className="explorer-def-desc">{m.description || 'No description.'} {tfNote && <span className="muted">{tfNote}</span>}</p>
      <div className="explorer-def-meta">
        <span className="chip">{m.unit ? unitCaption(m.unit) : 'unitless'}</span>
        <span className="chip">{m.kind === 'extensive' ? 'Total (additive)' : 'Rate / average'}</span>
        <span className="chip">Vintage {vintages.join(' · ')}</span>
        {via.length > 0 && <span className="chip" title="Derived metric: sources are those of its inputs">Derived from {via.join(', ')}</span>}
      </div>
      {per.length > 0 && (
        <details className="explorer-def-method">
          <summary>Method</summary>
          {sameMethod ? <p>{per[0].m.method || '—'}</p> : per.map((x) => <p key={x.c}><b>{COUNTRY_META[x.c].name}:</b> {x.m.method || '—'}</p>)}
        </details>
      )}
      <div className="explorer-def-src">
        {sameSources ? (
          per[0]?.pv.sources.length ? <SourceChips ids={per[0].pv.sources} /> : <span className="muted">No source listed.</span>
        ) : per.map((x) => (
          <div key={x.c} className="explorer-def-srcrow">
            <span className={`chip ${x.c}`}>{COUNTRY_META[x.c].short}</span>
            {x.pv.sources.length ? <SourceChips ids={x.pv.sources} /> : <span className="muted">No source listed.</span>}
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------------------------

type SortKey = 'x' | 'y' | 'name'
const headUnit = (u: string) => unitCaption(u).replace(/^(persons|people)\//, 'per ')

const ExplorerTable = memo(function ExplorerTable({ plot, level, xInfo, yInfo, brush, targets, idx, onPick, noun }: {
  plot: PlotData
  level: ExLevel
  xInfo: AxisInfo
  yInfo: AxisInfo
  brush: ReturnType<typeof brushFlags>
  targets: Targets
  idx: IndexJson | null
  onPick: (p: number) => void
  noun: string
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'y', dir: -1 })
  const wrap = useRef<HTMLDivElement>(null)
  const name = useCallback((p: number) => {
    const sg = segOf(plot, p)
    return sg ? rowName(sg.s.d, level, plot.row[p]) : ''
  }, [plot, level])

  const { rows, total } = useMemo(() => {
    const flags = brush?.flags
    let cand: Int32Array
    if (flags) {
      const tmp: number[] = []
      for (let i = 0; i < plot.n; i++) if (flags[i]) tmp.push(i)
      cand = Int32Array.from(tmp)
    } else cand = Int32Array.from({ length: plot.n }, (_, i) => i)
    const dir = sort.dir
    if (sort.key === 'name') {
      const keys = new Array<string>(plot.n)
      for (const p of cand) keys[p] = name(p).toLowerCase()
      const arr = Array.from(cand).sort((a, b) => (keys[a] < keys[b] ? -dir : keys[a] > keys[b] ? dir : 0))
      return { rows: arr.slice(0, TABLE_ROWS), total: cand.length }
    }
    const v = sort.key === 'x' ? plot.x : plot.y
    cand.sort((a, b) => (v[a] - v[b]) * dir)
    return { rows: Array.from(cand.subarray(0, TABLE_ROWS)), total: cand.length }
  }, [plot, brush, sort, name])

  const secondary = useMemo(() => new Set(targets.secondary ?? []), [targets])
  const pinned = targets.primary >= 0 && !rows.includes(targets.primary) ? targets.primary : -1

  // keep the selected row visible inside the table's own scroller (never scroll the panel)
  useEffect(() => {
    const el = wrap.current
    if (!el || targets.primary < 0) return
    const tr = el.querySelector<HTMLTableRowElement>(`tr[data-p="${targets.primary}"]`)
    if (!tr) return
    const head = el.querySelector('thead')?.clientHeight ?? 0
    const top = tr.offsetTop - head, bot = tr.offsetTop + tr.offsetHeight
    if (top < el.scrollTop || bot > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, top - el.clientHeight / 3)
  }, [targets, rows])

  const th = (key: SortKey, label: string, cls = '', sub = '') => {
    const active = sort.key === key
    return (
      <th className={cls} aria-sort={active ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
        <button type="button" className={`explorer-sort ${active ? 'on' : ''}`} title={sub ? `Sort by ${label} (${sub})` : `Sort by ${label}`}
          onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === 'name' ? 1 : -1 }))}>
          {label}{active ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
          {sub && <small>{sub}</small>}
        </button>
      </th>
    )
  }

  const renderRow = (p: number, rank: number | null) => {
    const sg = segOf(plot, p)
    if (!sg) return null
    const d = sg.s.d, row = plot.row[p]
    const sel = p === targets.primary || secondary.has(p)
    const dist = level === 'hex' && idx ? idx.districts[d.hexes.district[row]]?.code ?? '' : ''
    const icon = dist ? DISTRICT_META[dist]?.icon : ''
    return (
      <tr key={`${rank == null ? 'pin' : 'r'}${p}`} data-p={p} className={`${sel ? 'explorer-sel' : ''} ${rank == null ? 'explorer-pinned' : ''}`}
        tabIndex={0} aria-selected={sel} onClick={() => onPick(p)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(p) } }}>
        <td className="num muted">{rank == null ? '★' : rank}</td>
        <td className="explorer-name" title={rowName(d, level, row)}>
          <span className={`chip ${sg.country}`}>{COUNTRY_META[sg.country].short}</span>{' '}
          {icon && <span aria-hidden title={DISTRICT_META[dist]?.label}>{icon} </span>}
          {rowName(d, level, row)}
        </td>
        <td className="num">{fmtShort(plot.x[p], xInfo.unit)}</td>
        <td className="num">{fmtShort(plot.y[p], yInfo.unit)}</td>
      </tr>
    )
  }

  return (
    <section className="explorer-tablesec" aria-label="Explorer table">
      <div className="explorer-table-cap">
        <h3 className="section-title">{brush ? 'Brushed ' : ''}{noun}</h3>
        <span className="muted">
          {total > TABLE_ROWS ? `Top ${TABLE_ROWS} of ${total.toLocaleString()}` : `${total.toLocaleString()} rows`} · click a row to fly there
        </span>
      </div>
      <div className="explorer-table-wrap" ref={wrap}>
        <table className="data explorer-table">
          <colgroup><col className="c-rank" /><col /><col className="c-val" /><col className="c-val" /></colgroup>
          <thead>
            <tr>
              <th className="num">#</th>
              {th('name', 'Name')}
              {th('x', 'X', 'num', headUnit(xInfo.unit))}
              {th('y', 'Y', 'num', headUnit(yInfo.unit))}
            </tr>
          </thead>
          <tbody>
            {pinned >= 0 && renderRow(pinned, null)}
            {rows.map((p, i) => renderRow(p, i + 1))}
          </tbody>
        </table>
        {!total && <div className="explorer-empty-row muted">No rows.</div>}
      </div>
    </section>
  )
})
