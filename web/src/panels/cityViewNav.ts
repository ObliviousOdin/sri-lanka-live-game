// Navigation helpers shared by the City View and Wonders panels.
import { cellToLatLng } from 'h3-js'
import { useStore } from '../store'
import type { City, CountryId } from '../types'

export const CITY_ZOOM: Record<CountryId, number> = { lka: 9.4, usa: 7.6 }
export const SITE_ZOOM: Record<CountryId, number> = { lka: 10, usa: 8.2 }

/**
 * Fly a country's map to a location, first making sure that map is on screen. On desktop a hidden
 * map is revealed with the side-by-side layout; on phones (toggle layout) we switch to that country.
 */
export function flyToCountry(country: CountryId, lon: number, lat: number, zoom: number) {
  const st = useStore.getState()
  if (st.mode === 'split' || st.mode === country) {
    st.fly(country, lon, lat, zoom)
    return
  }
  const phone = typeof window !== 'undefined' && window.innerWidth < 900
  st.set({ mode: phone ? country : 'split' })
  // A freshly mounted map pane needs a moment to measure itself before it can accept a fly-to.
  window.setTimeout(() => useStore.getState().fly(country, lon, lat, zoom), 800)
}

/** Select a city (opens the City View) and centre its map on the city's centre tile. */
export function openCity(country: CountryId, city: City) {
  useStore.getState().select({ country, kind: 'city', id: city.id }, 'city')
  let lat = city.lat, lon = city.lon
  try { [lat, lon] = cellToLatLng(city.centre) } catch { /* keep the metro coordinates */ }
  flyToCountry(country, lon, lat, CITY_ZOOM[country])
}
