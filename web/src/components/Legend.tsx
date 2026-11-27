import { COUNTRY_META, YIELD_META } from '../civ'
import { invScale, rgbCss, yieldColor } from '../color'
import { useMatrixProgress } from '../data/useData'
import { fmtLensTick, lensUnitCaption, type Legend as L, type Provenance, type ScaleInfo } from '../lenses'
import { useStore } from '../store'
import { COUNTRIES, type CountryId } from '../types'
import { SourceChips } from './SourceChip'
import { YieldIcon } from './YieldIcon'
import './Legend.css'

const SHORT: Record<CountryId, string> = { lka: 'LK', usa: 'US' }
const MAX_CHIPS = 2

/** Which countries the colour domain was built from (replaces a generic "shared scale" note). */
function scaleNote(s: ScaleInfo, country: CountryId): { short: string; long: string } {
  const names = (ids: CountryId[]) => ids.map((c) => COUNTRY_META[c].name).join(' and ')
  if (!s.shared) return { short: `Own scale (${SHORT[country]})`, long: `Colours use ${COUNTRY_META[country].name}'s values only (Shared scale is off).` }
  if (s.pooled.length > 1) {
    return { short: `Shared scale (${s.pooled.map((c) => SHORT[c]).join(' + ')})`, long: `One colour scale built from both ${names(s.pooled)}: equal colours mean equal values.` }
  }
  if (s.pending.length) {
    return { short: `Own scale until ${s.pending.map((c) => SHORT[c]).join(', ')} loads`, long: `${names(s.pending)} statistics are still loading; the scale becomes shared when they arrive.` }
  }
  if (s.missing.length) {
    return { short: `Own scale (no ${s.missing.map((c) => SHORT[c]).join(', ')} data)`, long: `${names(s.missing)} does not publish this metric, so the scale uses ${COUNTRY_META[country].name} only.` }
  }
  return { short: 'Own scale', long: 'Colours use this country’s values only.' }
}

/** One vintage when the pooled countries agree, else "LK 2024 · US 2020-2024". */
function vintageText(prov: Provenance[]): string {
  const uniq = [...new Set(prov.map((p) => p.vintage).filter(Boolean))]
  if (uniq.length <= 1) return uniq[0] ?? ''
  return COUNTRIES.flatMap((c) => prov.filter((p) => p.country === c && p.vintage)).map((p) => `${SHORT[p.country]} ${p.vintage}`).join(' · ')
}

/** Up to MAX_CHIPS source chips of this pane's own country, then "+N" (opens the Sources tab). */
function Chips({ ids }: { ids: string[] }) {
  if (!ids.length) return null
  const more = ids.length - MAX_CHIPS
  return (
    <span className="lgx-chips">
      <SourceChips ids={ids.slice(0, MAX_CHIPS)} />
      {more > 0 && (
        <button type="button" className="chip lgx-more" title={`${more} more source${more > 1 ? 's' : ''}: open the Sources tab`}
          onClick={() => useStore.getState().set({ panel: 'sources', panelOpen: true })}>+{more}</button>
      )}
    </span>
  )
}

function LoadingLegend({ title, country }: { title: string; country: CountryId }) {
  const p = useMatrixProgress(country)
  const mb = (b: number) => (b / 1e6).toFixed(1)
  const pct = p && p.total ? Math.min(100, (p.loaded / p.total) * 100) : 0
  return (
    <div className="legend lgx-loading" role="status" aria-live="polite">
      <div className="legend-title">{title}</div>
      <div className="legend-note">Loading {COUNTRY_META[country].name} statistics…
        {p && p.loaded > 0 && <span aria-hidden> {mb(p.loaded)}{p.total ? ` / ${mb(p.total)}` : ''} MB</span>}
      </div>
      <div className="lgx-bar" aria-hidden><i style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

/**
 * Map legend. `note` is an extra caption from the caller; continuous, no-data and loading legends
 * carry their own scale note (which countries the colour domain pools), so `note` is ignored there.
 * Kept compact (title row + ramp + ticks + one footer line) so it fits a split-screen map pane.
 */
export function Legend({ legend, note }: { legend: L | null; note?: string }) {
  if (!legend) return null
  if (legend.kind === 'loading') return <LoadingLegend title={legend.title} country={legend.country} />
  if (legend.kind === 'nodata') {
    return (
      <div className="legend lgx-nodata" role="status">
        <div className="legend-title">{legend.title}</div>
        <div className="legend-note">{legend.detail}. Pick another metric in the lens menu.</div>
      </div>
    )
  }
  if (legend.kind === 'categorical') {
    const own = legend.prov?.[0]
    const vt = legend.prov ? vintageText(legend.prov) : ''
    return (
      <div className="legend">
        <div className="legend-title lgx-title">
          <span>{legend.title}</span>
          {vt && <span className="lgx-unit" title={`Data vintage: ${vt}`}>{vt}</span>}
          {own && <Chips ids={own.sources} />}
        </div>
        <div className="legend-cats">
          {legend.items.slice(0, 18).map((it) => (
            <span key={it.label} className="cat">
              <i style={{ background: rgbCss(it.rgb) }} />
              {it.label}
            </span>
          ))}
        </div>
        {note && <div className="legend-note">{note}</div>}
      </div>
    )
  }
  if (legend.kind === 'yield') {
    const cells = Array.from({ length: legend.cap + 1 }, (_, i) => i)
    return (
      <div className="legend">
        <div className="legend-title"><YieldIcon y={legend.yieldKey} size={14} /> {legend.title}</div>
        <div className="legend-steps">
          {cells.map((i) => (
            <span key={i} style={{ background: rgbCss(yieldColor(YIELD_META[legend.yieldKey].rgb, i, legend.cap)) }}>{i}</span>
          ))}
        </div>
        {note && <div className="legend-note">{note}</div>}
      </div>
    )
  }
  const stops = legend.palette.map((c, i) => `${rgbCss(c)} ${(i / (legend.palette.length - 1)) * 100}%`).join(',')
  const ticks = [0, 0.5, 1].map((t) => invScale(t, legend.domain, legend.log))
  const caption = `${lensUnitCaption(legend.unit, legend.basis)}${legend.log ? ' · log scale' : ''}`
  const sc = scaleNote(legend.scale, legend.country)
  const vt = vintageText(legend.prov)
  const own = legend.prov.find((p) => p.country === legend.country) ?? legend.prov[0]
  return (
    <div className="legend lgx-cont">
      <div className="legend-title lgx-title" title={legend.hint}>
        <span>{legend.title}</span>
        <span className="lgx-unit">{caption}</span>
      </div>
      <div className="legend-ramp" style={{ background: `linear-gradient(90deg,${stops})` }} />
      <div className="legend-ticks">
        {ticks.map((t, i) => <span key={i}>{fmtLensTick(t, legend.unit, legend.basis)}</span>)}
      </div>
      <div className="legend-note lgx-foot">
        <span className="lgx-meta" title={`${sc.long}${vt ? ` Data vintage: ${vt}.` : ''}`}>
          {sc.short}{vt ? ` · ${vt}` : ''}
        </span>
        {own && <Chips ids={own.sources} />}
      </div>
    </div>
  )
}
