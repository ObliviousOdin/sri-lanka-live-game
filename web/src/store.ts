import { create } from 'zustand'
import type { CountryId, Selection } from './types'

export type ViewMode = 'split' | 'lka' | 'usa'
export type PanelTab = 'tile' | 'city' | 'advisors' | 'explorer' | 'wonders' | 'sources'
export type PickLevel = 'hex' | 'adm1' | 'adm2' | 'adm3'

export interface FlyTo { country: CountryId; lon: number; lat: number; zoom?: number; ts: number }

interface State {
  mode: ViewMode
  lensId: string
  lensMetric: Record<string, string>
  sharedScale: boolean
  extrude: boolean
  basemap: 'none' | 'dark' | 'light'
  showAdmin: boolean
  showIcons: boolean
  sameScale: boolean
  pickLevel: PickLevel
  selection: Selection | null
  hover: { country: CountryId; index: number } | null
  panel: PanelTab
  panelOpen: boolean
  explorer: {
    scope: CountryId | 'both'
    level: 'hex' | 'admin'
    x: string
    y: string
    logX: boolean
    logY: boolean
    brushed: Partial<Record<CountryId, Set<number>>>
    adminLevel?: 'adm1' | 'adm2'
    norm?: 'total' | 'density' | 'capita'
    colorBy?: 'country' | 'lens'
  }
  flyTo: FlyTo | null
  zoomRef: Partial<Record<CountryId, { zoom: number; lat: number }>>
  set: (p: Partial<State>) => void
  setLens: (id: string, metric?: string) => void
  select: (s: Selection | null, openPanel?: PanelTab) => void
  setExplorer: (p: Partial<State['explorer']>) => void
  fly: (country: CountryId, lon: number, lat: number, zoom?: number) => void
}

const initialMode: ViewMode = typeof window !== 'undefined' && window.innerWidth < 900 ? 'lka' : 'split'

export const useStore = create<State>((set) => ({
  mode: initialMode,
  lensId: 'terrain',
  lensMetric: {},
  sharedScale: true,
  extrude: false,
  basemap: 'none',
  showAdmin: true,
  showIcons: true,
  sameScale: false,
  pickLevel: 'hex',
  selection: null,
  hover: null,
  panel: 'advisors',
  panelOpen: typeof window !== 'undefined' ? window.innerWidth >= 900 : true,
  explorer: { scope: 'both', level: 'admin', x: 'pop_density', y: 'gdp_per_capita_usd', logX: true, logY: true, brushed: {} },
  flyTo: null,
  zoomRef: {},
  set: (p) => set(p),
  setLens: (id, metric) =>
    set((s) => ({ lensId: id, lensMetric: metric ? { ...s.lensMetric, [id]: metric } : s.lensMetric })),
  select: (sel, openPanel) =>
    set((s) => ({ selection: sel, panel: openPanel ?? s.panel, panelOpen: sel ? true : s.panelOpen })),
  setExplorer: (p) => set((s) => ({ explorer: { ...s.explorer, ...p } })),
  fly: (country, lon, lat, zoom) => set({ flyTo: { country, lon, lat, zoom, ts: Date.now() } }),
}))
