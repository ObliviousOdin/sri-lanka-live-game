import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { COUNTRY_META } from '../civ'
import { computeLens, LENS_BY_ID, reliefElevation, type LensResult } from '../lenses'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId, type IndexJson, type NationalIndicator, type Source } from '../types'
import {
  DataError, loadCountry, loadIndex, loadMatrix, loadNational, loadSources, matrixProgress, onMatrixProgress, resetCountry,
  type MatrixProgress,
} from './load'

/**
 * A failed load. stage 'index': index.json; 'map': a country's map data (no map at all);
 * 'statistics': its metric matrix (the map shows terrain, yields and districts, but no statistics).
 * `missing` = the server does not have the file (not built / not deployed), so a retry will not help.
 */
export interface LoadError { stage: 'index' | 'map' | 'statistics'; message: string; missing: boolean }

export interface AppData {
  index: IndexJson | null
  countries: Partial<Record<CountryId, CountryData>>
  /** failures per country (and 'index'); a country that failed is never "loading" */
  errors: Partial<Record<CountryId | 'index', LoadError>>
  /** all failures in one line (null when everything loaded) */
  error: string | null
  /** load again whatever failed */
  retry: () => void
}

const toLoadError = (stage: LoadError['stage'], e: unknown): LoadError => ({
  stage,
  message: e instanceof Error ? e.message : String(e),
  missing: e instanceof DataError && e.missing,
})

/** One sentence for the UI: what failed, and what to do about it. */
export function describeLoadError(id: CountryId | 'index', e: LoadError): string {
  const what = id === 'index'
    ? 'The data index (index.json)'
    : e.stage === 'statistics' ? `${COUNTRY_META[id].name} statistics` : `${COUNTRY_META[id].name} map data`
  if (e.missing) return `${what} not found on the server (${e.message}). Build web/public/data with the pipeline (see README).`
  return `${what} failed to load (${e.message}).`
}

type LoadState = Omit<AppData, 'error' | 'retry'>

/** Loads index + both countries (Sri Lanka first: it is small). Failures are per country and can be retried. */
export function useAppData(): AppData {
  const [state, setState] = useState<LoadState>({ index: null, countries: {}, errors: {} })
  const alive = useRef(true)
  const setError = (id: CountryId | 'index', e: LoadError) => {
    if (alive.current) setState((s) => ({ ...s, errors: { ...s.errors, [id]: e } }))
  }
  const loadIdx = useCallback(() => {
    loadIndex().then(
      (index) => alive.current && setState((s) => ({ ...s, index })),
      (e) => setError('index', toLoadError('index', e)),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const loadOne = useCallback((id: CountryId) => {
    loadCountry(id).then(
      (d) => {
        if (!alive.current) return
        setState((s) => ({ ...s, countries: { ...s.countries, [id]: d } }))
        if (d.matrixReady) return
        loadMatrix(d).then(
          (full) => alive.current && setState((s) => ({ ...s, countries: { ...s.countries, [id]: full } })),
          (e) => setError(id, toLoadError('statistics', e)),
        )
      },
      (e) => setError(id, toLoadError('map', e)),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    alive.current = true
    loadIdx()
    COUNTRIES.forEach(loadOne)
    return () => { alive.current = false }
  }, [loadIdx, loadOne])
  const errors = state.errors
  const retry = useCallback(() => {
    if (errors.index) loadIdx()
    for (const id of COUNTRIES) {
      const e = errors[id]
      if (!e) continue
      resetCountry(id, e.stage === 'statistics' ? 'statistics' : 'map')
      loadOne(id)
    }
    setState((s) => ({ ...s, errors: {} }))
  }, [errors, loadIdx, loadOne])
  const error = useMemo(() => {
    const ids = (['index', ...COUNTRIES] as const).filter((id) => errors[id])
    return ids.length ? ids.map((id) => describeLoadError(id, errors[id]!)).join(' ') : null
  }, [errors])
  return useMemo(() => ({ ...state, error, retry }), [state, error, retry])
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
  const failedKey = COUNTRIES.filter((id) => all.errors[id]).join(',')
  const base = useMemo(() => {
    if (!d || !all.index) return null
    const loaded = COUNTRIES.map((id) => all.countries[id]).filter(Boolean) as CountryData[]
    const pool = shared ? loaded : [d]
    // countries still loading join the shared scale when they land; after a load error they never will
    const failed = COUNTRIES.filter((id) => all.errors[id])
    const pending = shared ? COUNTRIES.filter((id) => !all.countries[id] && !all.errors[id]) : []
    return computeLens(lens, lensMetric ?? null, d, all.index, pool, { shared, pending, failed, all: loaded })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    readsMatrix ? d : d?.hexes, all.index, lens,
    readsMatrix ? lensMetric : null, readsMatrix ? shared : null,
    readsMatrix && shared ? lka : null, readsMatrix && shared ? usa : null,
    readsMatrix ? loadedKey : '', readsMatrix ? failedKey : '',
  ])
  // terrain / district columns: mean elevation, from the matrix once it has arrived
  const relief = useMemo(() => (reliefOnly && d?.matrixReady ? reliefElevation(d) : null), [reliefOnly, d])
  return useMemo(() => (base && relief ? { ...base, elevation: relief } : base), [base, relief])
}
