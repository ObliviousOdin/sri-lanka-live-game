// Navigation helpers shared by the City View and Wonders panels.
import { cellToLatLng } from 'h3-js'
import { COUNTRY_META } from '../civ'
import { visibleHeightAboveSheet } from '../map/sheet'
import { useStore } from '../store'
import type { City, CountryId } from '../types'

export const CITY_ZOOM: Record<CountryId, number> = { lka: 9.4, usa: 7.6 }
export const SITE_ZOOM: Record<CountryId, number> = { lka: 9.8, usa: 7.8 }

/**
 * On phones the panel is a bottom sheet drawn over the map. Shift the fly-to centre south so the
 * target lands in the middle of the part of the map that is still visible above the sheet.
 * Uses the sheet's resting (open) position (map/sheet.ts: layout offsetTop, which ignores the
 * slide-in transform), so a fly-to issued while the sheet is still closed or animating aims at
 * where the sheet will be, not where it is on screen.
 */
function sheetAdjustedLat(country: CountryId, lat: number, zoom: number): number {
  // the callers open the panel (select) before flying; a sheet that stays closed covers nothing
  if (typeof document === 'undefined' || !useStore.getState().panelOpen) return lat
  const cell = document.querySelector<HTMLElement>(`.map-cell[aria-label="${COUNTRY_META[country].name} map"]`)
  if (!cell) return lat
  const visible = visibleHeightAboveSheet(cell)
  if (visible == null) return lat
  const offsetPx = (cell.getBoundingClientRect().height - visible) / 2
  const mpp = (78271.517 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom)
  return lat - (offsetPx * mpp) / 111320
}

/**
 * Fly a country's map to a location, first making sure that map is on screen. On desktop a hidden
 * map is revealed with the side-by-side layout; on phones (toggle layout) we switch to that country.
 * NB: MapPane ignores fly-to requests until its view is initialised, hence the delay after a switch.
 */
export function flyToCountry(country: CountryId, lon: number, lat: number, zoom: number) {
  const st = useStore.getState()
  const go = () => useStore.getState().fly(country, lon, sheetAdjustedLat(country, lat, zoom), zoom)
  // phones use the toggle layout (the second map of a split view sits under the bottom sheet)
  const phone = typeof window !== 'undefined' && window.innerWidth < 900
  const target = phone ? country : st.mode === country ? country : 'split'
  if (target === st.mode) {
    go()
    return
  }
  st.set({ mode: target })
  // A freshly mounted / resized map pane needs a moment to measure itself before it can fly.
  window.setTimeout(go, 800)
}

/** Select a city (opens the City View) and centre its map on the city's centre tile. */
export function openCity(country: CountryId, city: City) {
  useStore.getState().select({ country, kind: 'city', id: city.id }, 'city')
  let lat = city.lat, lon = city.lon
  try { [lat, lon] = cellToLatLng(city.centre) } catch { /* keep the metro coordinates */ }
  flyToCountry(country, lon, lat, CITY_ZOOM[country])
}
