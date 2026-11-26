// Grouped, filterable "All metrics" list used by the Tile inspector for tiles and admin units.
import { useMemo, useState } from 'react'
import { SourceChip, SourceChips } from '../components/SourceChip'
import { YieldIcon } from '../components/YieldIcon'
import { useStore } from '../store'
import type { Metric } from '../types'
import { CATEGORY_META, categoryRank, prettyUnit } from './tilePanelStats'

export interface RankInfo { pct: number | null; text: string; title: string }

export interface MetricRowData {
  key: string
  metric?: Metric
  label: string
  category: string
  raw: number
  valueText: string
  densityText?: string
  /** lazy: percentile / rank is only computed for rows that are actually rendered */
  rank: () => RankInfo | null
}

// group open state survives selection changes and tab switches
const openGroups = new Map<string, boolean>([['population', true], ['economy', true]])

export function MetricGroups({ rows, scopeLabel }: { rows: MetricRowData[]; scopeLabel: string }) {
  const [q, setQ] = useState('')
  const [hideMissing, setHideMissing] = useState(true)
  const [, force] = useState(0)
  const query = q.trim().toLowerCase()

  const missing = useMemo(() => rows.filter((r) => !Number.isFinite(r.raw)).length, [rows])
  const groups = useMemo(() => {
    const g = new Map<string, MetricRowData[]>()
    for (const r of rows) {
      if (hideMissing && !Number.isFinite(r.raw)) continue
      if (query) {
        const hay = `${r.label} ${r.key} ${r.category} ${r.metric?.description ?? ''}`.toLowerCase()
        if (!hay.includes(query)) continue
      }
      const a = g.get(r.category)
      if (a) a.push(r); else g.set(r.category, [r])
    }
    return [...g.entries()].sort((a, b) => categoryRank(a[0]) - categoryRank(b[0]))
  }, [rows, hideMissing, query])

  const toggle = (c: string) => { openGroups.set(c, !openGroups.get(c)); force((x) => x + 1) }
  const setAll = (v: boolean) => { groups.forEach(([c]) => openGroups.set(c, v)); force((x) => x + 1) }
  const shown = groups.reduce((s, [, r]) => s + r.length, 0)

  return (
    <div className="tilepanel-metrics">
      <div className="tilepanel-mtools">
        <input
          className="input tilepanel-filter"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter metrics by name or key…"
          aria-label="Filter metrics"
        />
        <div className="row tilepanel-mtools-row">
          <label className="tilepanel-check">
            <input type="checkbox" checked={hideMissing} onChange={(e) => setHideMissing(e.target.checked)} />
            Hide no-data ({missing})
          </label>
          <span className="tilepanel-spacer" />
          <button className="tilepanel-link" onClick={() => setAll(true)}>Expand all</button>
          <button className="tilepanel-link" onClick={() => setAll(false)}>Collapse</button>
        </div>
      </div>
      {!shown && <div className="muted tilepanel-empty">{rows.length ? 'No metric matches the filter.' : 'No metrics published yet — the data pipeline is still running.'}</div>}
      {groups.map(([cat, rs]) => {
        const meta = CATEGORY_META[cat] ?? { label: cat, icon: '📊' }
        const open = !!query || !!openGroups.get(cat)
        return (
          <section key={cat} className="tilepanel-group">
            <button className="tilepanel-group-head" aria-expanded={open} onClick={() => toggle(cat)} disabled={!!query}>
              <span className="tilepanel-caret" aria-hidden>{open ? '▾' : '▸'}</span>
              {meta.yield ? <YieldIcon y={meta.yield} size={14} /> : <span aria-hidden className="tilepanel-gicon">{meta.icon}</span>}
              <span className="tilepanel-group-label">{meta.label}</span>
              <span className="tilepanel-count">{rs.length}</span>
            </button>
            {open && (
              <div role="list">
                {rs.map((r) => <MetricRow key={r.key} r={r} scopeLabel={scopeLabel} />)}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}

function MetricRow({ r, scopeLabel }: { r: MetricRowData; scopeLabel: string }) {
  const [open, setOpen] = useState(false)
  const rank = useMemo(() => (Number.isFinite(r.raw) ? r.rank() : null), [r])
  const m = r.metric
  const lensActive = useStore((s) => s.lensId === 'custom' && s.lensMetric.custom === r.key)
  const hover = m ? [m.description, m.method && `Method: ${m.method}`].filter(Boolean).join('\n') : r.key
  const missing = !Number.isFinite(r.raw)
  return (
    <div role="listitem" className={`tilepanel-mrow ${missing ? 'is-missing' : ''} ${open ? 'is-open' : ''}`}>
      <div className="tilepanel-mrow-main">
        <button className="tilepanel-mlabel" onClick={() => setOpen(!open)} aria-expanded={open} title={hover}>
          {r.label}
        </button>
        <span className="tilepanel-mval">{r.valueText}</span>
        <span className="tilepanel-pct" title={rank?.title ?? 'No data'}>
          <span className="tilepanel-pct-text">{rank ? rank.text : '–'}</span>
          <span className="tilepanel-pct-bar" aria-hidden>
            {rank?.pct != null && <i style={{ width: `${Math.max(2, rank.pct)}%` }} />}
          </span>
        </span>
      </div>
      <div className="tilepanel-msub">
        {r.densityText && <span className="tilepanel-dens">{r.densityText}</span>}
        {m?.vintage && <span>{m.vintage}</span>}
        {m && m.sources.length > 0 && <RowSources ids={m.sources} />}
        {m && !m.sources.length && m.domain === 'derived' && <span>derived</span>}
      </div>
      {open && (
        <div className="tilepanel-mdetail">
          {m?.description && <p>{m.description}</p>}
          {m?.method && <p><span className="muted">Method:</span> {m.method}</p>}
          <p className="muted">
            <code>{r.key}</code>
            {m && <> · {prettyUnit(m.unit) || 'unitless'} · {m.kind}{m.kind === 'extensive' ? ` (allocated by ${m.weight})` : ` (${m.weight}-weighted mean)`}</>}
            {rank && <> · {rank.title}</>}
          </p>
          {m && m.sources.length > 0 && <div className="row" style={{ gap: 4 }}><SourceChips ids={m.sources} compact={false} /></div>}
          <div className="row tilepanel-mactions">
            <button className={`btn ${lensActive ? 'primary' : ''}`} onClick={() => useStore.getState().setLens('custom', r.key)} aria-pressed={lensActive}>
              🗺 {lensActive ? 'On the map' : 'Show on map'}
            </button>
            <button className="btn" onClick={() => { const st = useStore.getState(); st.setExplorer({ y: r.key }); st.set({ panel: 'explorer' }) }}>
              📈 Plot in Explorer
            </button>
          </div>
          <p className="muted tilepanel-scope">{scopeLabel}</p>
        </div>
      )}
    </div>
  )
}

/** At most two compact source chips per row (all of them are listed when the row is expanded). */
function RowSources({ ids }: { ids: string[] }) {
  const u = [...new Set(ids)]
  return (
    <>
      {u.slice(0, 2).map((id) => <SourceChip key={id} id={id} compact />)}
      {u.length > 2 && <span title={u.slice(2).join(', ')}>+{u.length - 2}</span>}
    </>
  )
}
