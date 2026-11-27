import { useSources } from '../data/useData'
import { useStore } from '../store'
import type { Source } from '../types'

/** Publisher name as shown on compact chips ("Department of Census and Statistics"). */
function publisherLabel(s: Source) {
  return s.publisher.split(/[,(/]/)[0].trim().replace(/\s+(of\s+)?(Sri Lanka|the United States)$/i, '')
}

/** Short publisher form used when several chips share a publisher ("DCS", "Census Bureau", "BEA"). */
function publisherAbbrev(pub: string) {
  const p = pub.replace(/^U\.?S\.?\s+/, '')
  if (p.length <= 16) return p
  const words = p.split(/\s+/)
  if (/^[A-Z]{2,}$/.test(words[0])) return words[0] // "NOAA National Centers …" → "NOAA"
  const initials = words.filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).join('')
  return initials.length >= 2 ? initials : p
}

const TITLE_ABBR: [RegExp, string][] = [
  [/Household Income and Expenditure Survey/i, 'HIES'],
  [/Labou?r Force Survey/i, 'LFS'],
  [/American Community Survey/i, 'ACS'],
  [/Census of Population and Housing/i, 'Census'],
  [/Cartographic Boundary Files/i, 'Boundaries'],
  [/Statistical Abstract/i, 'Stat. Abstract'],
  [/\s+(Annual Report|Final Report|Final Results|detailed data|\(English\))\b/gi, ''],
]
const YEAR = /\b(1[89]|20)\d\d(\s?[-–/]\s?\d{2,4})?\b/

/** Dataset part of a chip label: "LFS 2024", "Stat. Abstract 2025 · Table 10.2". */
function datasetLabel(s: Source, pub: string, ab: string, segments: number) {
  const parts = s.title.split(/\s[-–—]\s|:\s|,\s|\s\(/).map((x) => {
    let t = x.trim()
    for (const p of [pub, ab, 'Sri Lanka', 'U.S.', 'United States']) if (t.startsWith(p + ' ')) t = t.slice(p.length + 1)
    for (const [re, rep] of TITLE_ABBR) t = t.replace(re, rep)
    return t.trim()
  }).filter(Boolean)
  let t = parts.slice(0, segments).join(' · ')
  if (!YEAR.test(t) && s.vintage && s.vintage.length <= 12) t += ` ${s.vintage}`
  const max = segments > 1 ? 34 : 27
  if (t.length > max) {
    const yr = t.match(YEAR)?.[0] ?? ''
    const base = yr ? t.replace(yr, '').replace(/\s+/g, ' ').trim() : t
    const room = max - (yr ? yr.length + 2 : 1)
    const cut = base.slice(0, room)
    t = (/\s/.test(base[room] ?? ' ') ? cut : cut.replace(/\s+\S*$/, '')).replace(/[\s·,]+$/, '') + '…' + (yr ? ` ${yr}` : '')
  }
  return t
}

/**
 * Compact labels for a set of chips. A publisher with a single dataset in the set shows its name;
 * publishers with several datasets get "DCS · LFS 2024"-style labels so the chips stay distinguishable
 * without a hover title (touch screens).
 */
function compactLabels(list: Source[]): Map<string, string> {
  const byPub = new Map<string, Source[]>()
  for (const s of list) {
    const p = publisherLabel(s)
    byPub.set(p, [...(byPub.get(p) ?? []), s])
  }
  const out = new Map<string, string>()
  for (const [pub, group] of byPub) {
    if (group.length === 1) { out.set(group[0].id, pub); continue }
    const ab = publisherAbbrev(pub)
    const l1 = group.map((s) => datasetLabel(s, pub, ab, 1))
    // only labels that still collide get the title's second segment ("Table 10.2", "Chapter 5")
    const labels = l1.map((l, i) => {
      if (l1.indexOf(l) === l1.lastIndexOf(l)) return l
      const l2 = datasetLabel(group[i], pub, ab, 2)
      return group.some((o, j) => j !== i && l1[j] === l && datasetLabel(o, pub, ab, 2) === l2) ? l : l2
    })
    group.forEach((s, i) => out.set(s.id, `${ab} · ${labels[i]}`))
  }
  return out
}

/** Small citation chip: shows the source's short title and links to it; click opens the Sources tab. */
export function SourceChip({ id, compact, label: given }: { id: string; compact?: boolean; label?: string }) {
  const sources = useSources()
  const s = sources.find((x) => x.id === id)
  const label = given ?? (s ? (compact ? publisherLabel(s) : s.title) : id)
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
      <span aria-hidden>📎</span> {label.length > 42 ? label.slice(0, 40) + '…' : label}
    </a>
  )
}

export function SourceChips({ ids, compact = true }: { ids: string[]; compact?: boolean }) {
  const sources = useSources()
  const uniq = [...new Set(ids)]
  const labels = compact ? compactLabels(uniq.map((id) => sources.find((x) => x.id === id)).filter((s): s is Source => !!s)) : null
  return (
    <span className="row" style={{ gap: 4 }}>
      {uniq.map((id) => <SourceChip key={id} id={id} compact={compact} label={labels?.get(id)} />)}
    </span>
  )
}
