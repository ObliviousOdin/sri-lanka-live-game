// Facility point layers drawn on top of the Energy and Trade lenses: styling, circle sizing and
// tooltips. Circle area is proportional to the facility's size measure (MW, tonnes, boardings) on
// one absolute scale for both countries, and grows more slowly than the map when zooming in, so
// thousands of US plants stay dots at country zoom instead of burying the hex lens.
import { fmtNumber, fmtUnit } from '../format'
import type { CountryData, CountryId, PointRow, Source } from '../types'

export type RGB = [number, number, number]

export interface PointStyle {
  label: string
  plural: string
  color: RGB
  /** row fields holding the size measure (points.json columns), first finite > 0 wins */
  value: string[]
  /** unit of the size measure, for fmtUnit */
  unit: string
  /** short words for the key: "circle area = {sizeLabel}" */
  sizeLabel: string
  /** value drawn at the reference radius (see pointRadiusScale) */
  ref: number
  /** metric whose sources / vintage describe the size measure */
  metric: string
  /** what to say when a country publishes no size measure for this layer (tooltip / map key) */
  noValue?: Partial<Record<CountryId, string>>
  noValueKey?: Partial<Record<CountryId, string>>
  /**
   * Unsized layers only: a boolean row field drawn as a filled dot when true and a ring when false
   * (spaceports: `orbital`), with the words for each in the map key and the tooltip.
   */
  flag?: { field: string; on: string; off: string; onTip: string; offTip: string }
}

export const POINT_STYLE: Record<string, PointStyle> = {
  power_plants: {
    label: 'Power station', plural: 'Power stations', color: [250, 210, 60], value: ['capacity_mw'], unit: 'MW',
    sizeLabel: 'capacity (MW)', ref: 1000, metric: 'power_capacity_mw',
  },
  ports: {
    label: 'Seaport', plural: 'Seaports', color: [90, 190, 255], value: ['port_cargo_t'], unit: 't',
    sizeLabel: 'cargo (t/yr)', ref: 30e6, metric: 'port_cargo_t',
  },
  airports: {
    label: 'Airport', plural: 'Airports', color: [205, 225, 255], value: ['airport_pax'], unit: 'enplanements',
    sizeLabel: 'enplanements (boardings)/yr', ref: 10e6, metric: 'airport_pax',
    noValue: { lka: 'Sri Lanka publishes no open airport-level passenger statistics: airports are shown as rings, without a size.' },
    noValueKey: { lka: 'rings, no size: Sri Lanka publishes no open airport-level passenger statistics' },
  },
  // no size measure: every site is drawn at one size; `orbital` (FAA list + launch history) decides
  // the Spaceport district (civ.py), so it also decides filled dot vs ring here
  spaceports: {
    label: 'Spaceport site', plural: 'Spaceport sites', color: [230, 230, 255], value: [], unit: '',
    sizeLabel: '', ref: 1, metric: 'spaceports',
    flag: {
      field: 'orbital', on: 'orbital launch site', off: 'runway, suborbital or reentry only',
      onTip: 'Hosts orbital launches: a Spaceport district', offTip: 'No orbital launches: not a Spaceport district',
    },
  },
}

/** Whether a row's flag field (e.g. spaceports' `orbital`) is set. */
export function pointFlag(name: string, r: PointRow): boolean {
  const f = pointStyle(name).flag
  if (!f) return false
  const v = r[f.field]
  return v === true || v === 1 || v === 'true' || v === 'True'
}

const FALLBACK_STYLE: PointStyle = {
  label: 'Facility', plural: 'Facilities', color: [255, 255, 255], value: [], unit: '', sizeLabel: '', ref: 1, metric: '',
}
export const pointStyle = (name: string) => POINT_STYLE[name] ?? FALLBACK_STYLE

/** Size measure of a row (NaN when the row has none). */
export function pointValue(name: string, r: PointRow): number {
  for (const k of pointStyle(name).value) {
    const v = Number(r[k])
    if (r[k] != null && Number.isFinite(v) && v > 0) return v
  }
  return NaN
}

const splitCache = new WeakMap<PointRow[], { sized: PointRow[]; unsized: PointRow[] }>()
/** Rows with and without a size measure (memoised on the rows array). */
export function splitRows(name: string, rows: PointRow[]) {
  let s = splitCache.get(rows)
  if (!s) {
    s = { sized: [], unsized: [] }
    for (const r of rows) (Number.isFinite(pointValue(name, r)) ? s.sized : s.unsized).push(r)
    // big circles first so small ones stay on top and pickable
    s.sized.sort((a, b) => pointValue(name, b) - pointValue(name, a))
    splitCache.set(rows, s)
  }
  return s
}

/** Radius in px for a facility of size `ref` at `mpp` metres per pixel (√2 per zoom level). */
export function pointRadiusScale(mpp: number) {
  return 8 * Math.sqrt(1000 / mpp)
}

// ---------------------------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------------------------

export function escapeHtml(v: unknown): string {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

const FIELDS: Record<string, { label: string; unit?: string; year?: boolean }> = {
  capacity_mw: { label: 'Capacity', unit: 'MW' },
  generation_gwh: { label: 'Generation', unit: 'GWh/yr' },
  commissioning_year: { label: 'Commissioned', year: true },
  owner: { label: 'Owner' },
  port_cargo_t: { label: 'Cargo (imports + exports)', unit: 't/yr' },
  port_calls: { label: 'Port calls', unit: '/yr' },
  airport_pax: { label: 'Enplanements (boardings)', unit: '/yr' },
  iata: { label: 'IATA code' },
  launch_type: { label: 'Launch type' },
  faa_license: { label: 'Licence' },
}
const KIND_LABEL: Record<string, string> = {
  large_airport: 'large airport', medium_airport: 'medium airport', small_airport: 'small airport', seaport: 'seaport',
}

/** Short publisher form for tooltips: the acronym in brackets, else the first clause. */
function shortPublisher(s: Source) {
  return s.publisher.match(/\(([A-Z][A-Za-z]{1,12})\)/)?.[1] ?? s.publisher.split(/[,(/]/)[0].trim()
}

export function pointTooltip(d: CountryData, name: string, r: PointRow, sources: Source[]): string {
  const st = pointStyle(name)
  const kindRaw = typeof r.kind === 'string' && r.kind ? (KIND_LABEL[r.kind] ?? r.kind) : ''
  const kind = kindRaw.toLowerCase() === st.label.toLowerCase() ? '' : kindRaw
  const fuel = typeof r.primary_fuel === 'string' && r.primary_fuel && r.primary_fuel !== r.kind ? ` (${r.primary_fuel})` : ''
  const lines: string[] = []
  for (const [k, f] of Object.entries(FIELDS)) {
    const v = r[k]
    if (v == null || v === '') continue
    const n = Number(v)
    const shown = f.year ? (Number.isFinite(n) ? String(Math.round(n)) : escapeHtml(v)) : typeof v === 'number' || (f.unit && Number.isFinite(n))
      ? (f.unit?.startsWith('/') ? `${fmtNumber(n)}${f.unit}` : fmtUnit(n, f.unit ?? ''))
      : escapeHtml(v)
    lines.push(`<div><span class="k">${f.label}</span> ${shown}</div>`)
  }
  const note = !Number.isFinite(pointValue(name, r)) && st.noValue?.[d.id]
    ? `<div class="k">${escapeHtml(st.noValue[d.id])}</div>`
    : st.flag ? `<div>${escapeHtml(pointFlag(name, r) ? st.flag.onTip : st.flag.offTip)}</div>` : ''
  const m = d.metricByKey.get(st.metric)
  const src = m?.sources?.length
    ? m.sources.map((id) => { const s = sources.find((x) => x.id === id); return s ? `${shortPublisher(s)} ${s.vintage.split(/[;(]/)[0].trim()}`.trim() : id }).join(' · ')
    : ''
  return `<b>${escapeHtml(r.name)}</b><div class="k">${st.label}${kind ? ` · ${escapeHtml(kind)}` : ''}${escapeHtml(fuel)}</div>${lines.join('')}${note}${src ? `<div class="k mp-tip-src">Source: ${escapeHtml(src)}</div>` : ''}`
}
