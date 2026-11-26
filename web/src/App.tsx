import { useEffect } from 'react'
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
import { LENS_BY_ID } from './lenses'
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const s = useStore.getState()
      s.select(null)
      s.setExplorer({ brushed: {} })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const shown: CountryId[] = mode === 'split' ? ['lka', 'usa'] : [mode]
  const loaded = Object.values(app.countries) as CountryData[]

  return (
    <div className={`app ${panelOpen ? 'panel-open' : ''}`}>
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden><path d="M16 2 29 9.5v13L16 30 3 22.5v-13z" fill="#d9a441" stroke="#0d1726" strokeWidth="2" /><path d="M16 9l6 3.5v7L16 23l-6-3.5v-7z" fill="#0d1726" opacity=".55" /></svg>
          <div>
            <h1>Hex Atlas</h1>
            <p>Sri Lanka <span className="vs">vs</span> United States</p>
          </div>
        </div>
        <div className="mode-switch" role="tablist" aria-label="Map layout">
          {(['lka', 'split', 'usa'] as ViewMode[]).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? 'active' : ''} onClick={() => st.set({ mode: m })}>
              {m === 'split' ? 'Side by side' : COUNTRY_META[m].name}
            </button>
          ))}
        </div>
        <div className="toggles">
          <label title="Use one colour scale for both countries"><input type="checkbox" checked={st.sharedScale} onChange={(e) => st.set({ sharedScale: e.target.checked })} /> Shared scale</label>
          <label title="Lock both maps to the same km per pixel"><input type="checkbox" checked={st.sameScale} onChange={(e) => st.set({ sameScale: e.target.checked })} /> True size</label>
          <label><input type="checkbox" checked={st.extrude} onChange={(e) => st.set({ extrude: e.target.checked })} /> 3D</label>
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
        <button className="panel-toggle" onClick={() => st.set({ panelOpen: !panelOpen })} aria-expanded={panelOpen}>
          {panelOpen ? 'Hide panel' : 'Panel'}
        </button>
      </header>

      <LensBar countries={loaded} />

      <main className={`maps maps-${shown.length}`}>
        {app.error && <div className="error">Data failed to load: {app.error}. Run the pipeline (see README) to build web/public/data.</div>}
        {shown.map((c) => {
          const d = app.countries[c]
          const lr = c === 'lka' ? lensLka : lensUsa
          return (
            <section key={c} className="map-cell" aria-label={`${COUNTRY_META[c].name} map`}>
              {d && app.index ? <MapPane country={c} data={d} app={app} lens={lr} compact={shown.length > 1} /> : <div className="loading">Loading {COUNTRY_META[c].name}…</div>}
              {lr && <div className="legend-wrap"><Legend legend={lr.legend} note={shown.length > 1 && st.sharedScale && lens?.kind === 'metric' ? 'Shared scale across both countries' : undefined} /></div>}
            </section>
          )
        })}
      </main>

      <aside className="side" aria-label="Details panel">
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={panel === t.id} className={panel === t.id ? 'active' : ''} onClick={() => st.set({ panel: t.id, panelOpen: true })}>
              <span aria-hidden>{t.icon}</span> {t.label}
            </button>
          ))}
        </nav>
        <div className="panel-body">
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
