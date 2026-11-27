import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { LENSES, lensMetricOptions, type LensGroup } from '../lenses'
import { useStore } from '../store'
import type { CountryData } from '../types'

const GROUPS: LensGroup[] = ['Map', 'Yields', 'People', 'Economy', 'Environment', 'Infrastructure']

export function LensBar({ countries }: { countries: CountryData[] }) {
  const lensId = useStore((s) => s.lensId)
  const lensMetric = useStore((s) => s.lensMetric)
  const setLens = useStore((s) => s.setLens)
  const lens = LENSES.find((l) => l.id === lensId)!
  // each option carries " (LK only)" / " (US only)" when just one loaded country publishes it
  const metricOptions = lens.kind === 'metric' || lens.kind === 'dominant' ? lensMetricOptions(lens, countries) : []
  const showSelect = lens.kind === 'metric' && metricOptions.length > 0

  // ---- overflow affordance: edge fades + chevrons while more lenses are off-screen
  const scroller = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  const measure = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const left = el.scrollLeft > 2
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
    setEdges((e) => (e.left === left && e.right === right ? e : { left, right }))
  }, [])
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure])
  // keep the active lens visible, also after the metric dropdown appears and narrows the strip
  useLayoutEffect(() => {
    const el = scroller.current
    const b = el?.querySelector<HTMLElement>('.lens-btn.active')
    if (!el) return
    // roving tabindex: the active lens is the toolbar's Tab stop
    el.querySelectorAll<HTMLButtonElement>('.lens-btn').forEach((x, k) => { x.tabIndex = (b ? x === b : k === 0) ? 0 : -1 })
    if (!b) return
    const br = b.getBoundingClientRect(), sr = el.getBoundingClientRect(), pad = 30
    if (br.left < sr.left + pad) el.scrollLeft -= sr.left + pad - br.left
    else if (br.right > sr.right - pad) el.scrollLeft += br.right - (sr.right - pad)
    measure()
  }, [lensId, showSelect, measure])
  const page = (dir: 1 | -1) => {
    const el = scroller.current
    const calm = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.7, behavior: calm ? 'auto' : 'smooth' })
  }

  // ---- toolbar keyboard model: one Tab stop, arrow keys move between lenses
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const btns = [...(scroller.current?.querySelectorAll<HTMLButtonElement>('.lens-btn') ?? [])]
    const i = btns.indexOf(document.activeElement as HTMLButtonElement)
    if (i < 0) return
    const j = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? Math.min(btns.length - 1, i + 1)
      : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? Math.max(0, i - 1)
        : e.key === 'Home' ? 0 : e.key === 'End' ? btns.length - 1 : -1
    if (j < 0) return
    e.preventDefault()
    btns.forEach((b, k) => { b.tabIndex = k === j ? 0 : -1 })
    btns[j].focus()
  }

  return (
    <div className={`lensbar ${showSelect ? 'has-metric' : ''}`}>
      <div className={`lens-scroll-wrap ${edges.left ? 'can-left' : ''} ${edges.right ? 'can-right' : ''}`}>
        <button type="button" className="lens-more left" tabIndex={-1} aria-hidden onClick={() => page(-1)}>‹</button>
        <div className="lens-scroll" ref={scroller} role="toolbar" aria-label="Map lenses" onScroll={measure} onKeyDown={onKey}>
          {GROUPS.map((g) => (
            <div className="lens-group" key={g} role="group" aria-label={g}>
              <span className="lens-group-label" aria-hidden>{g}</span>
              {LENSES.filter((l) => l.group === g && l.id !== 'custom').map((l) => (
                <button
                  key={l.id}
                  type="button"
                  className={`lens-btn ${l.id === lensId ? 'active' : ''}`}
                  onClick={() => setLens(l.id)}
                  title={l.description}
                  aria-label={l.label}
                  aria-pressed={l.id === lensId}
                  tabIndex={l.id === lensId ? 0 : -1}
                >
                  <span className="lens-icon" aria-hidden>{l.icon}</span>
                  <span className="lens-label" aria-hidden>{l.label}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
        <button type="button" className="lens-more right" tabIndex={-1} aria-hidden onClick={() => page(1)}>›</button>
      </div>
      {showSelect && (
        <select
          className="lens-metric"
          value={lensMetric[lensId] ?? metricOptions[0].key}
          onChange={(e) => setLens(lensId, e.target.value)}
          aria-label={`${lens.label} lens metric`}
        >
          {metricOptions.map((o) => <option key={o.key} value={o.key}>{o.label}{o.tag}</option>)}
        </select>
      )}
    </div>
  )
}
