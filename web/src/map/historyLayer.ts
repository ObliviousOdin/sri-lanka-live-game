import type { Layer, LayerProps } from '@deck.gl/core'
import { IconLayer, ScatterplotLayer } from '@deck.gl/layers'
import { EVENT_KIND_META } from '../civ'
import { escapeHtml } from './pointLayers'
import type { CountryId, HistoryEvent } from '../types'
import type { AtlasEntry } from './emojiAtlas'

export interface HistoryLayerOpts {
  country: CountryId
  events: HistoryEvent[] | null
  year: number
  /** first year of the era the timeline is in: events since then are drawn large */
  eraStart: number
  kinds: string[] | null
  focus: string | null
  atlas: { canvas: HTMLCanvasElement; mapping: Record<string, AtlasEntry> }
  lon: (country: CountryId, lon: number) => number
  modelMatrix: LayerProps['modelMatrix']
  onTop: Record<string, unknown>
  /** at most this many current-era moments drawn as badges (the rest as dots) */
  maxBadges: number
}

type Pt = HistoryEvent & { pos: [number, number]; rank: number }

/** Within-kind percentile of each event's value (wonders rank first), cached per events array. */
const rankCache = new WeakMap<HistoryEvent[], Map<string, number>>()
function ranks(events: HistoryEvent[]): Map<string, number> {
  let r = rankCache.get(events)
  if (r) return r
  r = new Map()
  const byKind = new Map<string, HistoryEvent[]>()
  for (const e of events) byKind.set(e.kind, [...(byKind.get(e.kind) ?? []), e])
  for (const list of byKind.values()) {
    const sorted = [...list].sort((a, b) => (a.value ?? -1) - (b.value ?? -1))
    sorted.forEach((e, i) => r!.set(e.id, (e.wonder ? 1 : 0) + (list.length > 1 ? i / (list.length - 1) : 0.5)))
  }
  rankCache.set(events, r)
  return r
}

const rgba = (e: HistoryEvent, a: number) => [...(EVENT_KIND_META[e.kind]?.rgb ?? [255, 255, 255]), a] as [number, number, number, number]

/**
 * Dated historic moments up to the timeline year. Earlier eras are small dots; the current era
 * is drawn as badges, capped to the most notable ones (wonders first, then by within-kind rank)
 * when zoomed out, with the rest as bright dots.
 */
export function historyLayers(o: HistoryLayerOpts): Layer[] {
  if (!o.events?.length) return []
  const rk = ranks(o.events)
  const old: Pt[] = [], now: Pt[] = []
  for (const e of o.events) {
    if (e.year > o.year) continue
    if (o.kinds && !o.kinds.includes(e.kind)) continue
    const pt = { ...e, pos: [o.lon(o.country, e.lon), e.lat] as [number, number], rank: rk.get(e.id) ?? 0 }
    ;(e.year < o.eraStart ? old : now).push(pt)
  }
  now.sort((a, b) => b.rank - a.rank)
  const badges = now.slice(0, o.maxBadges)
  const nowDots = now.slice(o.maxBadges)
  const icon = (e: HistoryEvent) => EVENT_KIND_META[e.kind]?.icon ?? '📍'
  const dot = { radiusUnits: 'pixels' as const, stroked: true, getLineColor: [10, 16, 26, 200] as [number, number, number, number],
    lineWidthMinPixels: 0.5, pickable: true, modelMatrix: o.modelMatrix, parameters: o.onTop }
  const focusPt = o.focus ? [...badges, ...nowDots, ...old].find((e) => e.id === o.focus) : undefined
  return [
    new ScatterplotLayer<Pt>({ id: `hist-old-${o.country}`, data: old, getPosition: (e) => e.pos, getRadius: 2.6,
      getFillColor: (e) => rgba(e, 130), ...dot }),
    new ScatterplotLayer<Pt>({ id: `hist-dot-${o.country}`, data: nowDots, getPosition: (e) => e.pos, getRadius: 4,
      getFillColor: (e) => rgba(e, 235), ...dot }),
    new ScatterplotLayer<Pt>({
      id: `hist-halo-${o.country}`, data: badges, getPosition: (e) => e.pos, radiusUnits: 'pixels', getRadius: 17,
      getFillColor: (e) => rgba(e, 55), stroked: false, pickable: false, modelMatrix: o.modelMatrix, parameters: o.onTop,
    }),
    new IconLayer<Pt>({
      id: `hist-now-${o.country}`, data: badges, getSize: 24, iconAtlas: o.atlas.canvas as unknown as string,
      iconMapping: o.atlas.mapping, getIcon: icon, getPosition: (e) => e.pos, sizeUnits: 'pixels',
      pickable: true, modelMatrix: o.modelMatrix, parameters: o.onTop,
    }),
    focusPt && new ScatterplotLayer<Pt>({
      id: `hist-focus-${o.country}`, data: [focusPt], getPosition: (e) => e.pos, radiusUnits: 'pixels', getRadius: 22,
      filled: false, stroked: true, getLineColor: [255, 244, 210, 255], lineWidthMinPixels: 2.5, pickable: false,
      modelMatrix: o.modelMatrix, parameters: o.onTop,
    }),
  ].filter(Boolean) as Layer[]
}

export function historyIcons(): string[] {
  return Object.values(EVENT_KIND_META).map((k) => k.icon)
}

/** Tooltip for one historic moment. */
export function historyTooltip(e: HistoryEvent): string {
  const k = EVENT_KIND_META[e.kind]
  const value = e.value != null && e.unit ? `<div><span class="k">${escapeHtml(e.unit)}</span> ${e.value.toLocaleString('en-US')}</div>` : ''
  const wonder = e.wonder ? `<div><span class="k">Wonder</span> ${escapeHtml(e.wonder)}</div>` : ''
  return `<div class="tt-title">${k?.icon ?? ''} ${escapeHtml(e.name)}</div>
    <div>${escapeHtml(e.event)} <b>${escapeHtml(e.date)}</b></div>${wonder}${value}
    <div class="k">${escapeHtml(e.basis || e.source)}</div>`
}
