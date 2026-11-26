import { useSources } from '../data/useData'
import { useStore } from '../store'

/** Small citation chip: shows the source's short title and links to it; click opens the Sources tab. */
export function SourceChip({ id, compact }: { id: string; compact?: boolean }) {
  const sources = useSources()
  const s = sources.find((x) => x.id === id)
  const label = s ? (compact ? s.publisher.split(/[,(/]/)[0].trim() : s.title) : id
  return (
    <a
      className="chip source-chip"
      href={s?.url ?? '#'}
      target="_blank"
      rel="noreferrer"
      title={s ? `${s.title} — ${s.publisher} (${s.vintage}). ${s.license}` : id}
      onClick={(e) => {
        if (e.altKey || !s) { e.preventDefault(); useStore.getState().set({ panel: 'sources', panelOpen: true }) }
      }}
    >
      📎 {label.length > 42 ? label.slice(0, 40) + '…' : label}
    </a>
  )
}

export function SourceChips({ ids, compact = true }: { ids: string[]; compact?: boolean }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      {[...new Set(ids)].map((id) => <SourceChip key={id} id={id} compact={compact} />)}
    </span>
  )
}
