import { LENSES, lensMetricKeys, type LensGroup } from '../lenses'
import { useStore } from '../store'
import type { CountryData } from '../types'

const GROUPS: LensGroup[] = ['Map', 'Yields', 'People', 'Economy', 'Environment', 'Infrastructure']

export function LensBar({ countries }: { countries: CountryData[] }) {
  const lensId = useStore((s) => s.lensId)
  const lensMetric = useStore((s) => s.lensMetric)
  const setLens = useStore((s) => s.setLens)
  const lens = LENSES.find((l) => l.id === lensId)!
  const metricOptions = lens.kind === 'metric' || lens.kind === 'dominant'
    ? (lens.id === 'custom'
      ? [...new Map(countries.flatMap((c) => c.meta.metrics).map((m) => [m.key, m])).values()].map((m) => m.key)
      : lensMetricKeys(lens, countries))
    : []
  const labelOf = (k: string) => countries.map((c) => c.metricByKey.get(k)?.label).find(Boolean) ?? k
  return (
    <div className="lensbar" role="toolbar" aria-label="Map lenses">
      <div className="lens-scroll">
        {GROUPS.map((g) => (
          <div className="lens-group" key={g}>
            <span className="lens-group-label">{g}</span>
            {LENSES.filter((l) => l.group === g && l.id !== 'custom').map((l) => (
              <button
                key={l.id}
                className={`lens-btn ${l.id === lensId ? 'active' : ''}`}
                onClick={() => setLens(l.id)}
                title={l.description}
                aria-pressed={l.id === lensId}
              >
                <span className="lens-icon" aria-hidden>{l.icon}</span>
                <span className="lens-label">{l.label}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      {lens.kind === 'metric' && metricOptions.length > 0 && (
        <select
          className="lens-metric"
          value={lensMetric[lensId] ?? metricOptions[0]}
          onChange={(e) => setLens(lensId, e.target.value)}
          aria-label="Lens metric"
        >
          {metricOptions.map((k) => <option key={k} value={k}>{labelOf(k)}</option>)}
        </select>
      )}
    </div>
  )
}
