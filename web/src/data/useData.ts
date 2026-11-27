import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { computeLens, LENS_BY_ID, reliefElevation, type LensResult } from '../lenses'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId, type IndexJson, type NationalIndicator, type Source } from '../types'
import {
  loadCountry, loadIndex, loadMatrix, loadNational, loadSources, matrixProgress, onMatrixProgress, type MatrixProgress,
} from './load'

export interface AppData {
  index: IndexJson | null
  countries: Partial<Record<CountryId, CountryData>>
  error: string | null
}

/** Loads index + both countries (Sri Lanka first: it is small). */
export function useAppData(): AppData {
  const [state, setState] = useState<AppData>({ index: null, countries: {}, error: null })
  useEffect(() => {
    let alive = true
    loadIndex()
      .then((index) => alive && setState((s) => ({ ...s, index })))
      .catch((e) => alive && setState((s) => ({ ...s, error: String(e) })))
    ;(['lka', 'usa'] as CountryId[]).forEach((id) =>
      loadCountry(id)
        .then((d) => {
          if (!alive) return
          setState((s) => ({ ...s, countries: { ...s.countries, [id]: d } }))
          if (d.matrixReady) return
          return loadMatrix(d).then((full) => alive && setState((s) => ({ ...s, countries: { ...s.countries, [id]: full } })))
        })
        .catch((e) => alive && setState((s) => ({ ...s, error: `${id}: ${e}` }))),
    )
    return () => { alive = false }
  }, [])
  return state
}

/** metrics.bin download progress of one country (re-renders at most every ~200 ms while loading). */
export function useMatrixProgress(id: CountryId | undefined): MatrixProgress | undefined {
  return useSyncExternalStore(onMatrixProgress, () => (id ? matrixProgress(id) : undefined))
}

export function useSources() {
  const [s, set] = useState<Source[]>([])
  useEffect(() => { loadSources().then(set).catch(() => set([])) }, [])
  return s
}

export function useNational() {
  const [s, set] = useState<NationalIndicator[]>([])
  useEffect(() => { loadNational().then(set).catch(() => set([])) }, [])
  return s
}

/**
 * Lens colours for one country, recomputed when the lens / metric / pool changes.
 *
 * CountryData is replaced when metrics.bin arrives (its hexes table keeps its identity). Only metric
 * and dominant lenses read the metric matrix, so only they depend on the CountryData objects; with
 * the shared scale on they depend on every pooled one, so the colour domain is rebuilt when another
 * country's matrix arrives. Terrain, district and yield lenses depend on the hexes table alone; the
 * relief heights of terrain and district lenses (from the matrix) are refreshed separately, so the
 * colours are not recomputed when the matrix lands.
 */
export function useLens(d: CountryData | undefined, all: AppData): LensResult | null {
  const lensId = useStore((s) => s.lensId)
  const lensMetric = useStore((s) => s.lensMetric[s.lensId])
  const shared = useStore((s) => s.sharedScale)
  const lens = LENS_BY_ID.get(lensId) ?? LENS_BY_ID.get('terrain')!
  const readsMatrix = lens.kind === 'metric' || lens.kind === 'dominant'
  const reliefOnly = lens.kind === 'terrain' || lens.kind === 'district'
  const { lka, usa } = all.countries
  const loadedKey = COUNTRIES.filter((id) => all.countries[id]).join(',')
  const base = useMemo(() => {
    if (!d || !all.index) return null
    const loaded = COUNTRIES.map((id) => all.countries[id]).filter(Boolean) as CountryData[]
    const pool = shared ? loaded : [d]
    // countries still loading join the shared scale when they land; after a load error they never will
    const failed = COUNTRIES.filter((id) => all.error?.startsWith(`${id}:`))
    const pending = shared && !all.error ? COUNTRIES.filter((id) => !all.countries[id]) : []
    return computeLens(lens, lensMetric ?? null, d, all.index, pool, { shared, pending, failed, all: loaded })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    readsMatrix ? d : d?.hexes, all.index, lens,
    readsMatrix ? lensMetric : null, readsMatrix ? shared : null,
    readsMatrix && shared ? lka : null, readsMatrix && shared ? usa : null,
    readsMatrix ? loadedKey : '', readsMatrix ? all.error : null,
  ])
  // terrain / district columns: mean elevation, from the matrix once it has arrived
  const relief = useMemo(() => (reliefOnly && d?.matrixReady ? reliefElevation(d) : null), [reliefOnly, d])
  return useMemo(() => (base && relief ? { ...base, elevation: relief } : base), [base, relief])
}
