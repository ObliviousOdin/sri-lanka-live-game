// "Find a place" (Inspect tab): search the provinces / states, districts / counties, DS divisions and
// City View cities of both countries. WAI-ARIA 1.2 combobox: the input owns a listbox popup,
// ArrowUp/Down move the active option, Enter chooses it, Escape closes the list (then clears).
import { memo, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { COUNTRY_META } from '../civ'
import type { AppData } from '../data/useData'
import { fmtNumber } from '../format'
import { useStore } from '../store'
import { COUNTRIES, type CountryData, type CountryId } from '../types'
import { flyToCountry, openCity } from './cityViewNav'
import { adminZoom } from './tilePanelStats'

interface Place {
  key: string
  country: CountryId
  kind: 'admin' | 'city'
  /** admin level ('adm1'…) or 'city' */
  level: string
  /** admin unit index, or the city id */
  ref: number | string
  name: string
  kindLabel: string
  context: string
  icon: string
  pop: number
  lat: number
  lon: number
  zoom: number
  /** tie-break order of kinds: provinces / states, cities, districts / counties, DS divisions */
  order: number
  norm: string
  words: string[]
  /** words of the name and its context (parent units, kind) for multi-word queries */
  hay: string[]
}

const LIMIT = 12
const LEVEL_ORDER: Record<string, number> = { adm1: 0, city: 1, adm2: 2, adm3: 3 }
const LEVEL_ICON: Record<string, string> = { adm1: '🗺️', adm2: '📍', adm3: '•', city: '🏛️' }

/** Lower-case, accents and punctuation removed ("St. Mary's" → "st mary s"). */
const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

function buildPlaces(d: CountryData): Place[] {
  const out: Place[] = []
  const levels = Object.keys(d.meta.admin_levels)
  const byId = new Map<string, { name: string; parent: string; abbr?: string }>()
  for (const l of levels) for (const u of d.admin[l]?.units ?? []) byId.set(`${l}:${u.id}`, u)
  const country = COUNTRY_META[d.id].name
  const mk = (p: Omit<Place, 'norm' | 'words' | 'hay' | 'key'>): Place => {
    const n = norm(p.name)
    const words = n.split(' ')
    return { ...p, key: `${p.country}:${p.level}:${p.ref}`, norm: n, words, hay: [...words, ...norm(`${p.context} ${p.kindLabel}`).split(' ')] }
  }
  levels.forEach((l, li) => {
    const t = d.admin[l]
    if (!t) return
    const label = d.meta.admin_levels[l]?.label ?? t.label ?? l
    const pops = t.metrics.pop
    const land = t.metrics.land_km2
    t.units.forEach((u, k) => {
      // parent chain, nearest first: DS Division "Colombo, Western"; County "California (CA)"
      const chain: string[] = []
      let pid = u.parent
      for (let pl = li - 1; pl >= 0 && pid; pl--) {
        const p = byId.get(`${levels[pl]}:${pid}`)
        if (!p) break
        chain.push(p.abbr ? `${p.name} (${p.abbr})` : p.name)
        pid = p.parent
      }
      out.push(mk({
        country: d.id, kind: 'admin', level: l, ref: k, name: u.name, kindLabel: label,
        context: chain.length ? chain.join(', ') : country, icon: LEVEL_ICON[l] ?? '·',
        pop: pops?.[k] ?? 0, lat: u.lat, lon: u.lon, zoom: adminZoom(d.id, l, land?.[k], u.lat), order: LEVEL_ORDER[l] ?? 4,
      }))
    })
  })
  for (const c of d.cities) {
    out.push(mk({
      country: d.id, kind: 'city', level: 'city', ref: c.id, name: c.name, kindLabel: 'City View city',
      context: `size ${c.size}`, icon: LEVEL_ICON.city, pop: c.pop || 0, lat: c.lat, lon: c.lon, zoom: 0, order: LEVEL_ORDER.city,
    }))
  }
  return out
}

/** Ranked matches: exact name, name prefix, word prefixes (name + parents), then substring. */
function search(places: Place[], q: string): { hits: Place[]; total: number } {
  const nq = norm(q)
  if (!nq) return { hits: [], total: 0 }
  const qt = nq.split(' ')
  const scored: { p: Place; s: number }[] = []
  for (const p of places) {
    let s = -1
    if (p.norm === nq) s = 0
    else if (p.norm.startsWith(nq)) s = 1
    else if (p.words.some((w) => w.startsWith(qt[0])) && qt.every((t) => p.hay.some((w) => w.startsWith(t)))) s = 2
    else if (nq.length >= 3 && p.norm.includes(nq)) s = 3
    if (s >= 0) scored.push({ p, s })
  }
  scored.sort((a, b) => a.s - b.s || a.p.order - b.p.order || b.p.pop - a.p.pop || a.p.name.localeCompare(b.p.name))
  return { hits: scored.slice(0, LIMIT).map((x) => x.p), total: scored.length }
}

export const PlaceSearch = memo(function PlaceSearch({ app }: { app: AppData }) {
  const { lka, usa } = app.countries
  const places = useMemo(
    () => COUNTRIES.flatMap((c) => (app.countries[c] ? buildPlaces(app.countries[c]!) : [])),
    // admin tables and cities do not change when the metric matrix arrives
    [lka?.admin, lka?.cities, usa?.admin, usa?.cities], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const id = useId()
  const listId = `${id}-list`
  const optId = (k: number) => `${id}-opt-${k}`
  const { hits, total } = useMemo(() => search(places, q), [places, q])
  const shown = open && q.trim().length > 0
  const loading = COUNTRIES.filter((c) => !app.countries[c]).map((c) => COUNTRY_META[c].name)

  const moveTo = (k: number) => {
    setActive(k)
    listRef.current?.querySelector(`#${CSS.escape(optId(k))}`)?.scrollIntoView({ block: 'nearest' })
  }

  const choose = (p: Place, viaPointer: boolean) => {
    if (p.kind === 'city') {
      const city = app.countries[p.country]?.cities.find((c) => c.id === p.ref)
      if (city) openCity(p.country, city)
    } else {
      useStore.getState().select({ country: p.country, kind: 'admin', id: `${p.level}:${p.ref}` }, 'tile')
      flyToCountry(p.country, p.lon, p.lat, p.zoom)
    }
    setQ('')
    setOpen(false)
    setActive(-1)
    // touch: drop focus so the on-screen keyboard closes and the map is visible
    if (viaPointer && typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches) inputRef.current?.blur()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = hits.length
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!n) return
      if (!shown) { setOpen(true); moveTo(e.key === 'ArrowDown' ? 0 : n - 1); return }
      moveTo(e.key === 'ArrowDown' ? (active + 1) % n : active <= 0 ? n - 1 : active - 1)
    } else if (e.key === 'Enter') {
      if (!shown || !n) return
      e.preventDefault()
      choose(hits[active >= 0 && active < n ? active : 0], false)
    } else if (e.key === 'Escape') {
      if (shown) { e.preventDefault(); setOpen(false); setActive(-1) }
      else if (q) { e.preventDefault(); setQ('') }
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <div className="tilepanel-find" role="search">
      <label className="tilepanel-sr" htmlFor={`${id}-input`}>Find a place</label>
      <span className="tilepanel-find-icon" aria-hidden>⌕</span>
      <input
        ref={inputRef}
        id={`${id}-input`}
        className="input tilepanel-find-input"
        type="text"
        role="combobox"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 && active < hits.length ? optId(active) : undefined}
        aria-describedby={`${id}-hint`}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="go"
        placeholder="Find a place: province, county, city…"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(-1) }}
        onFocus={() => { if (q) setOpen(true) }}
        onBlur={() => { setOpen(false); setActive(-1) }}
        onKeyDown={onKeyDown}
      />
      {q && (
        <button type="button" className="tilepanel-find-clear" aria-label="Clear search" title="Clear"
          onMouseDown={(e) => e.preventDefault()} onClick={() => { setQ(''); setActive(-1); inputRef.current?.focus() }}>✕</button>
      )}
      <span id={`${id}-hint`} className="tilepanel-sr">
        Type a name, then use the up and down arrow keys and Enter to select it and fly the map there.
      </span>
      <ul ref={listRef} id={listId} role="listbox" aria-label="Places" className="tilepanel-find-list" hidden={!shown}>
        {shown && hits.map((p, k) => (
          <li
            key={p.key}
            id={optId(k)}
            role="option"
            aria-selected={k === active}
            className="tilepanel-find-opt"
            onMouseDown={(e) => e.preventDefault()}
            onMouseMove={() => { if (k !== active) setActive(k) }}
            onClick={(e) => choose(p, (e.nativeEvent as PointerEvent).pointerType !== 'mouse')}
          >
            <span className="tilepanel-find-ico" aria-hidden>{p.icon}</span>
            <span className="tilepanel-find-name">{p.name}</span>
            <span className={`chip ${p.country}`}>{COUNTRY_META[p.country].short}</span>
            <span className="tilepanel-find-ctx">
              {p.kindLabel} · {p.context}
              {p.pop > 0 && <> · {fmtNumber(p.pop)} people</>}
            </span>
          </li>
        ))}
        {shown && hits.length === 0 && (
          <li className="tilepanel-find-none" role="presentation">No province, district, county or city matches “{q.trim()}”.</li>
        )}
        {shown && total > hits.length && (
          <li className="tilepanel-find-none" role="presentation">{total - hits.length} more matches: keep typing to narrow.</li>
        )}
        {shown && loading.length > 0 && (
          <li className="tilepanel-find-none" role="presentation">Still loading {loading.join(' and ')}…</li>
        )}
      </ul>
      <span className="tilepanel-sr" aria-live="polite">
        {shown ? (hits.length ? `${total} match${total === 1 ? '' : 'es'}${total > hits.length ? `, first ${hits.length} shown` : ''}` : 'No matches') : ''}
      </span>
    </div>
  )
})
