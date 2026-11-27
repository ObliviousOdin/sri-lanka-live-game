import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { COUNTRY_META } from './civ'
import { useAppData, useLens } from './data/useData'
import { Legend } from './components/Legend'
import { LensBar } from './components/LensBar'
import { MapPane } from './map/MapPane'
import { Advisors } from './panels/Advisors'
import { CityView } from './panels/CityView'
import { Explorer } from './panels/Explorer'
import { Sources } from './panels/Sources'
import { TilePanel } from './panels/TilePanel'
import { WondersPanel } from './panels/WondersPanel'
import { LENS_BY_ID, type Legend as LegendT } from './lenses'
import { useStore, type PanelTab, type ViewMode } from './store'
import type { CountryData, CountryId } from './types'

const TABS: { id: PanelTab; label: string; icon: string }[] = [
  { id: 'advisors', label: 'Advisors', icon: '🗣️' },
  { id: 'tile', label: 'Inspect', icon: '⬡' },
  { id: 'city', label: 'Cities', icon: '🏛️' },
  { id: 'explorer', label: 'Explorer', icon: '📈' },
  { id: 'wonders', label: 'Wonders', icon: '🏯' },
  { id: 'sources', label: 'Sources', icon: '📚' },
]

const HINTS = {
  shared: 'Colour both maps on one scale, so equal colours mean equal values.',
  same: 'Lock both maps to the same kilometres per pixel, so areas compare at true size.',
  extrude: 'Raise tiles by the lens value.',
}
/** Why an option is unavailable in the current layout (shown instead of its hint). */
const OFF_HINTS: Partial<Record<keyof typeof HINTS, string>> = {
  same: 'Only for Side by side: it locks the two maps to the same kilometres per pixel.',
}

/** Keys typed into a form field belong to that field, not to the app-wide shortcuts. */
function inField(t: EventTarget | null) {
  return t instanceof HTMLElement && !!t.closest('input, select, textarea, [contenteditable]:not([contenteditable="false"])')
}

export default function App() {
  const app = useAppData()
  const mode = useStore((s) => s.mode)
  const panel = useStore((s) => s.panel)
  const panelOpen = useStore((s) => s.panelOpen)
  const sharedScale = useStore((s) => s.sharedScale)
  const sameScale = useStore((s) => s.sameScale)
  const extrude = useStore((s) => s.extrude)
  const pickLevel = useStore((s) => s.pickLevel)
  const basemap = useStore((s) => s.basemap)
  const lensId = useStore((s) => s.lensId)
  const set = useStore((s) => s.set)
  const st = { sharedScale, sameScale, extrude, pickLevel, basemap, set, lensId }
  const lka = app.countries.lka
  const usa = app.countries.usa
  const lensLka = useLens(lka, app)
  const lensUsa = useLens(usa, app)
  const lens = LENS_BY_ID.get(lensId)
  const [optsOpen, setOptsOpen] = useState(false)
  const [sheetFull, setSheetFull] = useState(false)
  const optsRef = useRef<HTMLDivElement>(null)
  const sideRef = useRef<HTMLElement>(null)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || inField(e.target)) return
      const s = useStore.getState()
      s.select(null)
      s.setExplorer({ brushed: {} })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // a hidden panel (desktop "Hide panel", or the closed phone sheet) must leave the Tab order
  useEffect(() => {
    if (sideRef.current) sideRef.current.inert = !panelOpen
    if (!panelOpen) setSheetFull(false)
  }, [panelOpen])

  // phone options popover: close on an outside press
  useEffect(() => {
    if (!optsOpen) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!optsRef.current?.contains(t) && !(t instanceof Element && t.closest('.options-toggle'))) setOptsOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [optsOpen])

  useTooltipFlip()

  const shown: CountryId[] = mode === 'split' ? ['lka', 'usa'] : [mode]
  const loaded = Object.values(app.countries) as CountryData[]

  const onTabKey = (e: ReactKeyboardEvent, i: number) => {
    const n = TABS.length
    const j = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i + n - 1) % n : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1
    if (j < 0) return
    e.preventDefault()
    st.set({ panel: TABS[j].id, panelOpen: true })
    tabRefs.current[j]?.focus()
  }

  return (
    <div className={`app ${panelOpen ? 'panel-open' : ''} ${sheetFull ? 'sheet-full' : ''}`}>
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden><path d="M16 2 29 9.5v13L16 30 3 22.5v-13z" fill="#d9a441" stroke="#0d1726" strokeWidth="2" /><path d="M16 9l6 3.5v7L16 23l-6-3.5v-7z" fill="#0d1726" opacity=".55" /></svg>
          <div>
            <h1>Hex Atlas</h1>
            <p>Sri Lanka <span className="vs">vs</span> United States</p>
          </div>
        </div>
        <div className="mode-switch" role="group" aria-label="Map layout">
          {(['lka', 'split', 'usa'] as ViewMode[]).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'active' : ''} onClick={() => st.set({ mode: m })}>
              {m === 'split' ? 'Side by side' : COUNTRY_META[m].name}
            </button>
          ))}
        </div>
        <div
          ref={optsRef}
          id="map-options"
          className={`toggles ${optsOpen ? 'open' : ''}`}
          onKeyDown={(e) => { if (e.key === 'Escape' && optsOpen) { e.preventDefault(); setOptsOpen(false) } }}
        >
          <ToggleItem id="shared" label="Shared scale" checked={st.sharedScale} onChange={(v) => st.set({ sharedScale: v })} />
          <ToggleItem id="same" label="True size" checked={st.sameScale} disabled={mode !== 'split'} onChange={(v) => st.set({ sameScale: v })} />
          <ToggleItem id="extrude" label="3D" checked={st.extrude} onChange={(v) => st.set({ extrude: v })} />
          <select value={st.pickLevel} onChange={(e) => st.set({ pickLevel: e.target.value as never })} aria-label="Click selects">
            <option value="hex">Click: tile</option>
            <option value="adm1">Click: province / state</option>
            <option value="adm2">Click: district / county</option>
            <option value="adm3">Click: DS division (LK)</option>
          </select>
          <select value={st.basemap} onChange={(e) => st.set({ basemap: e.target.value as never })} aria-label="Basemap">
            <option value="none">No basemap</option>
            <option value="dark">Dark basemap</option>
            <option value="light">Light basemap</option>
          </select>
        </div>
        <div className="topbar-actions">
          <button type="button" className="options-toggle" aria-expanded={optsOpen} aria-controls="map-options" onClick={() => setOptsOpen((o) => !o)}>
            <span aria-hidden>⚙</span> Options
          </button>
          <button type="button" className="panel-toggle" onClick={() => st.set({ panelOpen: !panelOpen })} aria-expanded={panelOpen} aria-controls="side-panel">
            {panelOpen ? 'Hide panel' : 'Panel'}
          </button>
        </div>
      </header>

      <LensBar countries={loaded} />

      <main className={`maps maps-${shown.length}`}>
        {app.error && <div className="error">Data failed to load: {app.error}. Run the pipeline (see README) to build web/public/data.</div>}
        {shown.map((c) => {
          const d = app.countries[c]
          const lr = c === 'lka' ? lensLka : lensUsa
          // busy until this country's statistics (metrics.bin) have arrived, unless its load failed
          const busy = !app.error?.startsWith(`${c}:`) && !d?.matrixReady
          return (
            <section key={c} className="map-cell" aria-label={`${COUNTRY_META[c].name} map`} aria-busy={busy}>
              {d && app.index ? <MapPane country={c} data={d} app={app} lens={lr} compact={shown.length > 1} /> : <div className="loading">Loading {COUNTRY_META[c].name}…</div>}
              {lr && (
                <LegendDock
                  legend={lr.legend}
                  note={shown.length > 1 && st.sharedScale && lens?.kind === 'metric' ? 'Shared scale across both countries' : undefined}
                  desc={lens?.description}
                />
              )}
            </section>
          )
        })}
      </main>

      <aside className="side" id="side-panel" aria-label="Details panel" ref={sideRef}>
        <SheetGrip full={sheetFull} setFull={setSheetFull} />
        <div className="tabs" role="tablist" aria-label="Panels">
          {TABS.map((t, i) => (
            <button
              key={t.id}
              ref={(el) => { tabRefs.current[i] = el }}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={panel === t.id}
              aria-controls="side-tabpanel"
              tabIndex={panel === t.id ? 0 : -1}
              className={panel === t.id ? 'active' : ''}
              onClick={() => st.set({ panel: t.id, panelOpen: true })}
              onKeyDown={(e) => onTabKey(e, i)}
            >
              <span className="tab-icon" aria-hidden>{t.icon}</span>
              <span className="tab-label">{t.label}</span>
            </button>
          ))}
        </div>
        <div className="panel-body" role="tabpanel" id="side-tabpanel" aria-labelledby={`tab-${panel}`} tabIndex={0}>
          {!app.index || !lka ? <div className="loading">Loading data…</div> : (
            <>
              {panel === 'tile' && <TilePanel app={app} />}
              {panel === 'city' && <CityView app={app} />}
              {panel === 'advisors' && <Advisors app={app} />}
              {panel === 'explorer' && <Explorer app={app} />}
              {panel === 'wonders' && <WondersPanel app={app} />}
              {panel === 'sources' && <Sources app={app} />}
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

/**
 * Map option checkbox; its explanation is a hover title on desktop and visible text in the phone
 * popover. A disabled option keeps its stored value and says why it does not apply.
 */
function ToggleItem({ id, label, checked, disabled, onChange }: { id: keyof typeof HINTS; label: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  const hint = disabled ? OFF_HINTS[id] ?? HINTS[id] : HINTS[id]
  return (
    <div className={`toggle-item ${disabled ? 'disabled' : ''}`}>
      <label title={hint}>
        <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} aria-describedby={`hint-${id}`} /> {label}
      </label>
      <small className="toggle-hint" id={`hint-${id}`}>{hint}</small>
    </div>
  )
}

/**
 * Collapsible legend card. Starts open unless the open legend would cover a large share of its map
 * pane (many categories, or a narrow pane); the viewer's own choice then sticks across lenses.
 */
function LegendDock({ legend, note, desc }: { legend: LegendT; note?: string; desc?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pref, setPref] = useState<boolean | null>(null)
  const [auto, setAuto] = useState(true)
  const [cell, setCell] = useState('')
  useEffect(() => {
    const p = ref.current?.parentElement
    if (!p) return
    const ro = new ResizeObserver(() => setCell(`${p.clientWidth}x${p.clientHeight}`))
    ro.observe(p)
    return () => ro.disconnect()
  }, [])
  useLayoutEffect(() => {
    const el = ref.current
    const p = el?.parentElement
    if (!el || !p || !p.clientWidth || !p.clientHeight) return
    // measure the open card (before paint, so there is no flicker)
    const was = el.classList.contains('collapsed')
    el.classList.remove('collapsed')
    const r = el.getBoundingClientRect()
    if (was) el.classList.add('collapsed')
    const frac = (r.width * r.height) / (p.clientWidth * p.clientHeight)
    setAuto(frac <= 0.1 && r.height <= p.clientHeight * 0.25)
  }, [legend, note, desc, cell])
  const open = pref ?? auto
  const name = legend.title.replace(/:.*$/, '')
  return (
    <div ref={ref} className={`legend-wrap ${open ? '' : 'collapsed'}`}>
      <div className="legend-card">
        <Legend legend={legend} note={note} />
        {desc && <p className="legend-desc">{desc}</p>}
        <button
          type="button"
          className="legend-toggle"
          aria-expanded={open}
          aria-label={open ? `Collapse ${name} legend` : `Expand ${name} legend`}
          title={open ? 'Collapse legend' : 'Expand legend'}
          onClick={() => setPref(!open)}
        >
          <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden><path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
    </div>
  )
}

/** Phone bottom sheet handle: tap toggles half / full height; drag up to expand, down to shrink or close. */
function SheetGrip({ full, setFull }: { full: boolean; setFull: (f: boolean) => void }) {
  const start = useRef<number | null>(null)
  const dragged = useRef(false)
  const onDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    start.current = e.clientY
    dragged.current = false
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }
  const onUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (start.current == null) return
    const dy = e.clientY - start.current
    start.current = null
    if (Math.abs(dy) < 24) return
    dragged.current = true
    if (dy < 0) setFull(true)
    else if (full) setFull(false)
    else useStore.getState().set({ panelOpen: false })
  }
  return (
    <button
      type="button"
      className="sheet-grip"
      aria-label={full ? 'Shrink panel to half height' : 'Expand panel to full height'}
      aria-expanded={full}
      onPointerDown={onDown}
      onPointerUp={onUp}
      onPointerCancel={() => { start.current = null }}
      onClick={() => { if (dragged.current) { dragged.current = false; return } setFull(!full) }}
    >
      <span aria-hidden />
    </button>
  )
}

/**
 * Keep deck.gl hover tooltips inside their map pane: the tooltip is placed at the pointer, so near
 * the right or bottom edge it is flipped to the other side of the pointer (the pane clips overflow).
 * Works on the element deck.gl positions with `transform: translate(x, y)`, using the independent
 * CSS `translate` property for the offset.
 */
function useTooltipFlip() {
  useEffect(() => {
    const GAP = 14
    const adjust = (el: HTMLElement) => {
      if (el.style.display === 'none') return
      const cell = el.closest('.map-cell') as HTMLElement | null
      const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(el.style.transform)
      if (!cell || !m) return
      const x = +m[1], y = +m[2]
      const w = el.offsetWidth, h = el.offsetHeight
      const W = cell.clientWidth, H = cell.clientHeight
      let dx = GAP, dy = GAP
      if (x + GAP + w > W - 4) dx = x - GAP - w >= 4 ? -GAP - w : Math.max(4 - x, W - 4 - w - x)
      if (y + GAP + h > H - 4) dy = y - GAP - h >= 4 ? -GAP - h : Math.max(4 - y, H - 4 - h - y)
      const t = `${Math.round(dx)}px ${Math.round(dy)}px`
      if (el.style.translate !== t) el.style.translate = t
    }
    const mo = new MutationObserver((recs) => {
      for (const r of recs) {
        const t = r.target
        if (t instanceof HTMLElement && t.classList.contains('deck-tip')) adjust(t)
      }
    })
    mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['style', 'class'] })
    return () => mo.disconnect()
  }, [])
}
