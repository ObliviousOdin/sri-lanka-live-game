// Admin-unit picking: which province / state / district / county / DS division polygon contains a
// map point. Clicks resolve to the polygon under the cursor, not to the tile's dominant unit, so
// small units (Manhattan, DC, Colombo's DS divisions) can be selected from the map.
import type { AdminTable, CountryData, CountryId } from '../types'

type Ring = number[][]
interface Feat { id: string; bbox: [number, number, number, number]; polys: Ring[][] }

// ---------------------------------------------------------------------------------------------
// Antimeridian: the western Aleutians (Attu, Kiska, Rat Islands) lie east of 180°. In the US map
// every longitude is kept in a frame centred on the US, [-270°, 90°), so Alaska is one piece.
// ---------------------------------------------------------------------------------------------

/** Shift a US data longitude into the US frame (east of 0° means west of 180°). */
export function usLon(lon: number) {
  return lon > 0 ? lon - 360 : lon
}

/** Longitude of a data point as drawn on `country`'s map. */
export function mapLon(country: CountryId, lon: number) {
  return country === 'usa' ? usLon(lon) : lon
}

/** Bring any view / click longitude into the country's frame ([-270, 90) for the US, [-180, 180) otherwise). */
export function frameLon(country: CountryId, lon: number) {
  const lo = country === 'usa' ? -270 : -180
  return ((((lon - lo) % 360) + 360) % 360) + lo
}

const shifted = new WeakMap<GeoJSON.FeatureCollection, GeoJSON.FeatureCollection>()
const shiftRing = (r: GeoJSON.Position[]) => r.map((p) => (p[0] > 0 ? [p[0] - 360, ...p.slice(1)] : p))
const hasEast = (r: GeoJSON.Position[]) => r.some((p) => p[0] > 0)

/** The admin boundaries as drawn on `country`'s map (US: western Aleutians moved west of -180°). */
export function geoForMap(country: CountryId, fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  if (country !== 'usa') return fc
  let out = shifted.get(fc)
  if (out) return out
  out = {
    ...fc,
    features: fc.features.map((f) => {
      const g = f.geometry
      if (!g) return f
      if (g.type === 'Polygon' && g.coordinates.some(hasEast)) return { ...f, geometry: { ...g, coordinates: g.coordinates.map(shiftRing) } }
      if (g.type === 'MultiPolygon' && g.coordinates.some((p) => p.some(hasEast)))
        return { ...f, geometry: { ...g, coordinates: g.coordinates.map((p) => p.map(shiftRing)) } }
      return f
    }),
  }
  shifted.set(fc, out)
  return out
}

// ---------------------------------------------------------------------------------------------
// Point in polygon
// ---------------------------------------------------------------------------------------------

const indexCache = new WeakMap<GeoJSON.FeatureCollection, Feat[]>()

function indexFeatures(fc: GeoJSON.FeatureCollection): Feat[] {
  let out = indexCache.get(fc)
  if (out) return out
  out = []
  for (const f of fc.features) {
    const g = f.geometry
    const id = f.properties?.id
    if (!g || id == null) continue
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : []
    if (!polys.length) continue
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const p of polys) for (const pt of p[0] ?? []) {
      if (pt[0] < x0) x0 = pt[0]
      if (pt[0] > x1) x1 = pt[0]
      if (pt[1] < y0) y0 = pt[1]
      if (pt[1] > y1) y1 = pt[1]
    }
    out.push({ id: String(id), bbox: [x0, y0, x1, y1], polys: polys as Ring[][] })
  }
  indexCache.set(fc, out)
  return out
}

function inRing(r: Ring, x: number, y: number) {
  let inside = false
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** Admin id (properties.id) of the polygon containing [lon, lat], in the same frame as `fc`. */
export function adminIdAt(fc: GeoJSON.FeatureCollection, lon: number, lat: number): string | null {
  for (const f of indexFeatures(fc)) {
    const [x0, y0, x1, y1] = f.bbox
    if (lon < x0 || lon > x1 || lat < y0 || lat > y1) continue
    for (const p of f.polys) {
      if (!p.length || !inRing(p[0], lon, lat)) continue
      let hole = false
      for (let k = 1; k < p.length && !hole; k++) hole = inRing(p[k], lon, lat)
      if (!hole) return f.id
    }
  }
  return null
}

const unitIndexCache = new WeakMap<AdminTable, Map<string, number>>()

/** Index of an admin unit in d.admin[level].units from its id (-1 when unknown). */
export function adminIndexOf(d: CountryData, level: string, id: string): number {
  const t = d.admin[level]
  if (!t) return -1
  let m = unitIndexCache.get(t)
  if (!m) {
    m = new Map(t.units.map((u, i) => [u.id, i]))
    unitIndexCache.set(t, m)
  }
  return m.get(id) ?? -1
}

/** The feature for admin unit `unitIndex` at `level` (for outlining a selection). */
export function adminFeature(d: CountryData, level: string, unitIndex: number, fc: GeoJSON.FeatureCollection) {
  const id = d.admin[level]?.units[unitIndex]?.id
  if (id == null) return null
  return fc.features.find((f) => String(f.properties?.id) === id) ?? null
}
