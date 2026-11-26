import { YIELD_META } from '../civ'
import { invScale, rgbCss, yieldColor } from '../color'
import { fmtUnit } from '../format'
import type { Legend as L } from '../lenses'
import { YieldIcon } from './YieldIcon'

export function Legend({ legend, note }: { legend: L | null; note?: string }) {
  if (!legend) return null
  if (legend.kind === 'categorical') {
    return (
      <div className="legend">
        <div className="legend-title">{legend.title}</div>
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
  const unit = legend.density ? `${legend.unit}/km²` : legend.unit
  return (
    <div className="legend">
      <div className="legend-title">{legend.title}{legend.density ? ' per km²' : ''}{legend.log ? ' (log scale)' : ''}</div>
      <div className="legend-ramp" style={{ background: `linear-gradient(90deg,${stops})` }} />
      <div className="legend-ticks">
        {ticks.map((t, i) => <span key={i}>{fmtUnit(t, unit)}</span>)}
      </div>
      {note && <div className="legend-note">{note}</div>}
    </div>
  )
}
