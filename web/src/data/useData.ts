import { useEffect, useMemo, useState } from 'react'
import { computeLens, LENS_BY_ID, type LensResult } from '../lenses'
import { useStore } from '../store'
import type { CountryData, CountryId, IndexJson, NationalIndicator, Source } from '../types'
import { loadCountry, loadIndex, loadNational, loadSources } from './load'

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
        .then((d) => alive && setState((s) => ({ ...s, countries: { ...s.countries, [id]: d } })))
        .catch((e) => alive && setState((s) => ({ ...s, error: `${id}: ${e}` }))),
    )
    return () => { alive = false }
  }, [])
  return state
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

/** Lens colours for one country, recomputed when the lens / metric / pool changes. */
export function useLens(d: CountryData | undefined, all: AppData): LensResult | null {
  const lensId = useStore((s) => s.lensId)
  const lensMetric = useStore((s) => s.lensMetric[s.lensId])
  const shared = useStore((s) => s.sharedScale)
  const poolKey = shared ? Object.keys(all.countries).sort().join(',') : d?.id
  return useMemo(() => {
    if (!d || !all.index) return null
    const lens = LENS_BY_ID.get(lensId) ?? LENS_BY_ID.get('terrain')!
    const pool = shared ? (Object.values(all.countries) as CountryData[]) : [d]
    return computeLens(lens, lensMetric ?? null, d, all.index, pool)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, all.index, lensId, lensMetric, poolKey])
}
