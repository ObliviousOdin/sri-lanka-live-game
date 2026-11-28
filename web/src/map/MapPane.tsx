import { FlyToInterpolator, LinearInterpolator, WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { GeoJsonLayer, IconLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { cellToBoundary, cellToLatLng, cellToParent, getBaseCellNumber, latLngToCell } from 'h3-js'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { COUNTRY_META, DISTRICT_META, YIELD_META, terrainLabel } from '../civ'
import { ScaleBar } from '../components/ScaleBar'
import { SourceChips } from '../components/SourceChip'
import { adminUnitOfHex, finestLevel, hexesOfAdmin, loadAdminGeo, value } from '../data/load'
import { useEvents, useSources, type AppData } from '../data/useData'
import { DEFAULT_ERAS, eraIndexOf, resolveEras } from '../history/eraScale'
import { historyIcons, historyLayers, historyTooltip } from './historyLayer'
import { fmtInt, fmtNumber, fmtUnit } from '../format'
import { LENS_BY_ID, isDensity, landOnlyMetric, lensMetricKey, lensMetricKeys, metricLabel, type LensDef, type LensResult } from '../lenses'
import { CITY_ZOOM, openCity } from '../panels/cityViewNav'
import { plural } from '../panels/tilePanelStats'
import { useStore } from '../store'
import { YIELDS, type City, type CountryData, type CountryId, type HistoryEvent, type PointRow } from '../types'
import { adminFeature, adminIdAt, adminIndexOf, frameLon, geoForMap, mapLon } from './adminPick'
import { BaseMap } from './BaseMap'
import { emojiAtlas } from './emojiAtlas'
import { coarsePointer, prefersReducedMotion } from './motion'
import { escapeHtml, pointFlag, pointRadiusScale, pointStyle, pointTooltip, pointValue, splitRows } from './pointLayers'
import { markMoved, metersPerPixel, publishScale, resetMoved, scaleLeader, useScaleLink, zoomForMpp } from './scaleLink'
import { visibleHeightAboveSheet } from './sheet'
import './MapPane.css'

type Bounds = [[number, number], [number, number]]

const INITIAL_BOUNDS: Record<CountryId, Bounds> = {
  lka: [[79.5, 5.85], [82.0, 9.9]],
  usa: [[-125.0, 24.3], [-66.9, 49.4]],
}

/** US jump targets; Alaska includes the western Aleutians drawn west of 180° (longitudes < -180). */
const US_JUMPS: { name: string; short: string; bounds: Bounds }[] = [
  { name: 'Contiguous US', short: 'US', bounds: INITIAL_BOUNDS.usa },
  { name: 'Alaska', short: 'AK', bounds: [[-188.5, 51.0], [-129.5, 71.5]] },
  { name: 'Hawaii', short: 'HI', bounds: [[-160.6, 18.8], [-154.6, 22.4]] },
]

const BASEMAPS = {
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
}

export const WONDER_ICON: Record<string, string> = {
  'Great University': '📚', 'Great Port': '🚢', 'Great Power Station': '🔋', 'Natural Wonder': '🏞️',
  'Great Airport': '🛫', 'World Heritage': '🏯', 'Holy Wonder': '🛐',
}

// constant layer props, hoisted so re-renders do not hand deck.gl new objects
const HEX_MATERIAL = { ambient: 0.55, diffuse: 0.6, shininess: 20 }
const HEX_HIGHLIGHT: [number, number, number, number] = [255, 236, 170, 110]
/** Labels, icons, facilities and selections draw over extruded columns in 3D. */
const ON_TOP = { depthCompare: 'always', depthWriteEnabled: false } as const
const lonShift = (deg: number) => new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, deg, 0, 0, 1])
const SHIFT: Record<number, Float64Array | undefined> = { [-360]: lonShift(-360), 0: undefined, 360: lonShift(360) }
const MAX_PITCH_3D = 45

interface Props { country: CountryId; data: CountryData; app: AppData; lens: LensResult | null; compact?: boolean }
interface IconDatum { pos: [number, number]; icon: string; size: number; kind: 'district' | 'wonder'; label: string }

/** A view state without pending transition props (so spreading it never restarts an animation). */
function still(v: MapViewState): MapViewState {
  const out = { ...v } as Record<string, unknown>
  delete out.transitionDuration
  delete out.transitionInterpolator
  delete out.transitionEasing
  delete out.transitionInterruption
  return out as unknown as MapViewState
}

type Box = [number, number, number, number]

/**
 * Room kept free when fitting a region: the pane label (and US jump buttons) on top, with room for
 * the city banners drawn above the northernmost cities; the legend dock and scale bar at the bottom.
 * Capped on small panes, where fitBounds needs most of the pane.
 * `sheetTop` (phones): height of the pane above the open bottom sheet; the region is then fitted
 * into that strip, since the sheet hides the rest of the pane (and the legend and scale bar with it).
 */
function fitPadding(size: { w: number; h: number }, sheetTop: number | null = null) {
  const side = Math.min(40, size.w / 10)
  const top = Math.min(64, size.h / 6)
  if (sheetTop != null && sheetTop - top > 120) return { top, left: side, right: side, bottom: size.h - sheetTop + 12 }
  return {
    top,
    left: side,
    right: side,
    bottom: Math.min(40 + Math.min(100, size.h * 0.14), size.h / 4),
  }
}

/** Phones: the bottom sheet is drawn over the map (styles.css, max-width 900px) and is open. */
const sheetCovers = (panelOpen: boolean) => panelOpen && typeof window !== 'undefined' && window.innerWidth < 900

/** `lon` moved by whole turns to lie within 180° of `ref` (keeps fly-to paths short across 180°). */
const nearLon = (lon: number, ref: number) => lon + 360 * Math.round((ref - lon) / 360)

export function MapPane({ country, data, app, lens, compact }: Props) {
  const idx = app.index!
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [view, setView] = useState<MapViewState | null>(null)
  const lensId = useStore((s) => s.lensId)
  const lensMetricSel = useStore((s) => s.lensMetric[s.lensId])
  const extrude = useStore((s) => s.extrude)
  const basemap = useStore((s) => s.basemap)
  const showAdmin = useStore((s) => s.showAdmin)
  const showIcons = useStore((s) => s.showIcons)
  const sameScale = useStore((s) => s.sameScale)
  const pickLevel = useStore((s) => s.pickLevel)
  const selection = useStore((s) => s.selection)
  const brushedAll = useStore((s) => s.explorer.brushed)
  const flyTo = useStore((s) => s.flyTo)
  const mode = useStore((s) => s.mode)
  const panelOpen = useStore((s) => s.panelOpen)
  const sources = useSources()
  const lensDef = LENS_BY_ID.get(lensId) ?? LENS_BY_ID.get('terrain')!
  // ---- timeline (Historic moments lens)
  const histOn = lensDef.kind === 'history'
  const events = useEvents(country, histOn)
  const histYear = useStore((s) => s.history.year)
  const histKinds = useStore((s) => s.history.kinds)
  const histFocus = useStore((s) => s.history.focus)
  const histSpans = useMemo(() => resolveEras(app.index?.eras ?? DEFAULT_ERAS, -10_000), [app.index])
  const histEraStart = histSpans[eraIndexOf(histYear, histSpans)].start
  /** admin level clicks resolve to here: DS divisions fall back to US counties on the US map */
  const pickLv = pickLevel === 'hex' ? 'hex' : data.meta.admin_levels[pickLevel] ? pickLevel : finestLevel(data)
  const pickFallback = pickLevel !== 'hex' && pickLv !== pickLevel

  // ---- sizing / initial view
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])
  /** fitBounds padding for this pane now: on phones with the sheet open, only the strip above it */
  const padNow = (sz: { w: number; h: number }) => {
    const el = ref.current
    return fitPadding(sz, el && sheetCovers(useStore.getState().panelOpen) ? visibleHeightAboveSheet(el) : null)
  }
  // fit the country on first render, and again on resize, or when the phone sheet opens or closes,
  // until the user moves the map
  const userMoved = useRef(false)
  const appliedFly = useRef<number | null>(null)
  const sheetOver = sheetCovers(panelOpen)
  const fittedSheet = useRef(sheetOver)
  useEffect(() => {
    if (!size || size.w < 10 || (view && userMoved.current)) return
    const sheetToggled = fittedSheet.current !== sheetOver
    fittedSheet.current = sheetOver
    const vp = new WebMercatorViewport({ width: size.w, height: size.h })
    const { longitude, latitude, zoom } = vp.fitBounds(INITIAL_BOUNDS[country], { padding: padNow(size) })
    // the sheet slides in or out: follow it; a first fit or a resize jumps
    const anim = view && sheetToggled && !prefersReducedMotion()
      ? { transitionDuration: 300, transitionInterpolator: new LinearInterpolator(['longitude', 'latitude', 'zoom']) }
      : {}
    setView((v) => ({ ...(v ? still(v) : { pitch: extrude ? MAX_PITCH_3D : 0, bearing: 0 }), longitude, latitude, zoom, ...anim }) as MapViewState)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, country, sheetOver])

  // ---- fly-to requests (store.fly from panels, jump buttons, city banners)
  useEffect(() => {
    if (!flyTo || flyTo.country !== country || !view || flyTo.ts === appliedFly.current) return
    appliedFly.current = flyTo.ts
    userMoved.current = true
    markMoved(country)
    const target = {
      ...still(view),
      longitude: nearLon(mapLon(country, flyTo.lon), view.longitude),
      latitude: flyTo.lat,
      zoom: flyTo.zoom ?? Math.max(view.zoom, country === 'lka' ? 9 : 7.5),
    }
    setView(prefersReducedMotion() ? target : ({ ...target, transitionDuration: 900, transitionInterpolator: new FlyToInterpolator() } as MapViewState))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyTo?.ts, view === null])

  // ---- 3D: tilt the camera when columns are extruded, flatten it again when they are not
  const prevExtrude = useRef(extrude)
  useEffect(() => {
    if (prevExtrude.current === extrude) return
    prevExtrude.current = extrude
    const anim = prefersReducedMotion() ? {} : { transitionDuration: 500, transitionInterpolator: new LinearInterpolator(['pitch', 'bearing']) }
    setView((v) => (v ? ({ ...still(v), pitch: extrude ? MAX_PITCH_3D : 0, bearing: extrude ? v.bearing ?? 0 : 0, ...anim } as MapViewState) : v))
  }, [extrude])

  // ---- "True size": the most recently moved pane leads, the other adopts its metres per pixel
  const linking = sameScale && mode === 'split'
  const link = useScaleLink((s) => (linking && s.link && s.link.from !== country ? s.link : null))
  useEffect(() => resetMoved(country), [country])
  useEffect(() => {
    if (!linking) useScaleLink.setState({ link: null })
  }, [linking])
  useEffect(() => {
    if (!linking || !view || scaleLeader() !== country) return
    publishScale(country, metersPerPixel(view.zoom, view.latitude))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linking, view?.zoom, view?.latitude, country])
  useEffect(() => {
    if (!link || !view || scaleLeader() === country) return
    const z = Math.max(0, Math.min(20, zoomForMpp(link.mpp, view.latitude)))
    if (Math.abs(z - view.zoom) < 0.005) return
    userMoved.current = true
    setView({ ...still(view), zoom: z })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link, view === null])

  const inTransition = useRef(false)
  const onViewStateChange = ({ viewState, interactionState }: { viewState: MapViewState; interactionState?: { inTransition?: boolean; isDragging?: boolean; isZooming?: boolean; isPanning?: boolean; isRotating?: boolean } }) => {
    inTransition.current = !!interactionState?.inTransition
    setView(viewState)
    if (interactionState && (interactionState.isZooming || interactionState.isDragging || interactionState.isPanning || interactionState.isRotating)) {
      userMoved.current = true
      markMoved(country)
    }
  }

  // ---- admin boundaries (outlines, click picking, selection outline)
  const [geo, setGeo] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  const needGeo = (l: string | undefined) => {
    if (!l || geo[l] || !data.meta.admin_levels[l]) return
    loadAdminGeo(country, l).then((g) => setGeo((s) => (s[l] ? s : { ...s, [l]: g }))).catch(() => {})
  }
  useEffect(() => {
    ;(country === 'lka' ? ['adm1', 'adm2'] : ['adm1']).forEach(needGeo)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country])
  const selAdmin = selection && selection.country === country && selection.kind === 'admin' ? selection.id.split(':') : null
  const selLevel = selAdmin?.[0]
  const selUnit = selAdmin ? Number(selAdmin[1]) : -1
  useEffect(() => {
    if (pickLv !== 'hex') needGeo(pickLv)
    needGeo(selLevel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickLv, selLevel, country, geo, data.meta])
  const mapGeo = (l: string) => (geo[l] ? geoForMap(country, geo[l]) : null)

  // ---- tiles: drawn in place, plus (US) the western Aleutians east of 180° drawn 360° west
  const hexSets = useMemo(() => {
    const all = Array.from({ length: data.n }, (_, i) => i)
    if (country !== 'usa') return { main: all, east: [] as number[] }
    const main: number[] = [], east: number[] = []
    // base cells are ~2,000 km across: only those centred near 180° need a per-tile test
    const nearAntimeridian = new Map<number, boolean>()
    for (let i = 0; i < data.n; i++) {
      const h = data.hexes.h3[i]
      const bc = getBaseCellNumber(h)
      let near = nearAntimeridian.get(bc)
      if (near === undefined) {
        near = Math.abs(cellToLatLng(cellToParent(h, 0))[1]) > 140
        nearAntimeridian.set(bc, near)
      }
      ;(near && cellToLatLng(h)[1] > 0 ? east : main).push(i)
    }
    return { main, east }
  }, [data.hexes, data.n, country])

  // US view centred east of 50°E (after panning across 180°): draw the US data one turn east too
  const lonOffset = country === 'usa' && view && view.longitude > 50 ? 360 : 0
  const shiftBy = (deg: number) => SHIFT[lonOffset + deg]

  // ---- colours with brushing emphasis
  const brushed = brushedAll[country]
  const anyBrush = useMemo(() => Object.values(brushedAll).some((b) => {
    if (!b) return false
    for (const i of b) if (i >= 0) return true
    return false
  }), [brushedAll])
  const colors = useMemo(() => {
    if (!lens) return null
    if (!anyBrush || !brushed) return lens.colors
    const c = lens.colors.slice()
    for (let i = 0; i < data.n; i++) {
      if (!brushed.has(i)) { c[i * 4] *= 0.25; c[i * 4 + 1] *= 0.25; c[i * 4 + 2] *= 0.25 }
    }
    return c
  }, [lens, brushed, anyBrush, data.n])

  // ---- selection geometry
  const selectedHexes = useMemo(() => {
    if (!selection || selection.country !== country) return [] as string[]
    if (selection.kind === 'hex') return [selection.id]
    if (selection.kind === 'admin') {
      const [lvl, ui] = selection.id.split(':')
      return hexesOfAdmin(data, lvl, Number(ui)).map((i) => data.hexes.h3[i])
    }
    if (selection.kind === 'city') {
      const c = data.cities.find((x) => x.id === selection.id)
      return c ? c.tiles.map((t) => t.h3) : []
    }
    if (selection.kind === 'wonder') {
      const w = data.wonders.find((x) => x.name === selection.id)
      return w?.h3 ? [w.h3] : []
    }
    return []
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, country, data.hexes, data.cities, data.wonders, data.admin])
  const selPolys = useMemo(
    () => selectedHexes.map((h) => cellToBoundary(h, true).map(([lo, la]) => [mapLon(country, lo), la])),
    [selectedHexes, country],
  )
  const selFeature = useMemo(() => {
    if (!selLevel || selUnit < 0 || !geo[selLevel]) return null
    return adminFeature(data, selLevel, selUnit, geoForMap(country, geo[selLevel]))
  }, [selLevel, selUnit, geo, data, country])

  // Once the user has moved the map, a resize (panel shown or hidden; on phones, Side by side shrinking
  // both maps above the opened sheet) keeps the view centre, which can push the selection out of the
  // pane: bring it back to the centre then. Before that, the fit above shows the whole country.
  const prevSize = useRef(size)
  useEffect(() => {
    const was = prevSize.current
    prevSize.current = size
    // a fly-to in progress lands on its target (the selection) at the new size by itself
    if (!size || !was || !view || !userMoved.current || inTransition.current || (was.w === size.w && was.h === size.h)) return
    const p = selectionPoint(data, selection, country)
    if (!p) return
    const lon = nearLon(mapLon(country, p[0]), view.longitude)
    const vp = new WebMercatorViewport({ width: size.w, height: size.h, longitude: view.longitude, latitude: view.latitude, zoom: view.zoom, pitch: view.pitch ?? 0, bearing: view.bearing ?? 0 })
    const [x, y] = vp.project([lon, p[1]])
    const m = 24
    if (x > m && x < size.w - m && y > m && y < size.h - m) return
    setView({ ...still(view), longitude: lon, latitude: p[1] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size])

  const zoom = view?.zoom ?? 4
  const iconMinZoom = country === 'lka' ? 7.6 : 5.6

  // ---- icons (districts + wonders)
  const iconData = useMemo(() => {
    const out: IconDatum[] = []
    const codes = idx.districts.map((x) => x.code)
    // a Spaceport district needs an orbital launch site in the tile: name those sites on its icon
    const orbital = new Map<string, string[]>()
    for (const r of data.points.spaceports?.rows ?? []) {
      if (typeof r.h3 === 'string' && pointFlag('spaceports', r)) orbital.set(r.h3, [...(orbital.get(r.h3) ?? []), String(r.name)])
    }
    const minor = new Set(['neighborhood', 'farmland', 'preserve', 'fishery', 'plantation', 'encampment'])
    for (let i = 0; i < data.n; i++) {
      const code = codes[data.hexes.district[i]]
      if (!code) continue
      if (minor.has(code) && zoom < iconMinZoom + 1.2) continue
      const [lo, la] = hexCenter(data, i)
      const sites = code === 'spaceport' ? orbital.get(data.hexes.h3[i]) : undefined
      const label = `Specialist district: ${DISTRICT_META[code]?.label ?? code}${sites?.length ? ` (orbital launch site${sites.length > 1 ? 's' : ''}: ${sites.join(', ')})` : ''}`
      out.push({ pos: [mapLon(country, lo), la], icon: DISTRICT_META[code]?.icon ?? '•', size: code === 'city_center' ? 30 : 22, kind: 'district', label })
    }
    for (const w of data.wonders) out.push({ pos: [mapLon(country, w.lon), w.lat], icon: WONDER_ICON[w.category] ?? '★', size: 30, kind: 'wonder', label: `${w.category}: ${w.name}` })
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.hexes, data.wonders, data.points, data.n, idx, country, zoom >= iconMinZoom + 1.2])
  const atlas = useMemo(() => emojiAtlas([...new Set(iconData.map((d) => d.icon))]), [iconData])
  const histAtlas = useMemo(() => (histOn ? emojiAtlas(historyIcons()) : null), [histOn])

  // pane overlays in pane pixels (measured below): city banners are not drawn under them
  const [overlays, setOverlays] = useState<Box[]>([])

  // declutter city banners: keep the biggest cities, drop labels whose boxes would overlap each other
  // or a pane overlay
  const zKey = view ? Math.round(view.zoom * 4) / 4 : 0
  const visibleCities = useMemo(() => {
    if (!view || !size) return data.cities
    const vp = new WebMercatorViewport({ width: size.w, height: size.h, longitude: view.longitude, latitude: view.latitude, zoom: view.zoom })
    const boxes: Box[] = [...overlays]
    const fs = compact ? 11 : 13
    return [...data.cities].sort((a, b) => b.size - a.size).filter((c) => {
      const [x, y] = vp.project([mapLon(country, c.lon) + lonOffset, c.lat])
      const w = (c.name.length + 4) * fs * 0.62 + 12, h = fs + 8
      const b: Box = [x - w / 2, y - 26 - h / 2, x + w / 2, y - 26 + h / 2]
      // cut by the top or bottom edge of the pane (labels running off the sides stay readable)
      if (b[1] < 0 || b[3] > size.h) return false
      if (boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1])) return false
      boxes.push(b)
      return true
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.cities, zKey, size?.w, size?.h, compact, lonOffset, overlays, Math.round((view?.longitude ?? 0) * 10), Math.round((view?.latitude ?? 0) * 10)])

  // ---- facility points (Energy / Trade lenses)
  const pointSets = useMemo(() => (lensDef.pointLayers ?? []).flatMap((name) => {
    const layer = data.points[name]
    if (!layer) return []
    return [{ name, ...splitRows(name, layer.rows), total: layer.total }]
  }), [data.points, lensDef])
  const radiusScale = pointRadiusScale(view ? metersPerPixel(view.zoom, view.latitude) : 1000)
  const pointLayers = pointSets.flatMap(({ name, sized, unsized }) => {
    const st = pointStyle(name)
    const pos = (r: PointRow): [number, number] => [mapLon(country, Number(r.lon)), Number(r.lat)]
    return [
      sized.length > 0 && new ScatterplotLayer<PointRow>({
        id: `pts-${name}`, data: sized, pickable: true, stroked: true, filled: true,
        getPosition: pos, getRadius: (r) => Math.sqrt(pointValue(name, r) / st.ref),
        radiusUnits: 'pixels', radiusScale, radiusMinPixels: 1.6, radiusMaxPixels: 30,
        getFillColor: [...st.color, 120] as [number, number, number, number], getLineColor: [10, 15, 25, 210], lineWidthUnits: 'pixels', getLineWidth: 0.8,
        modelMatrix: shiftBy(0), parameters: ON_TOP,
      }),
      unsized.length > 0 && new ScatterplotLayer<PointRow>({
        id: `pts-${name}-ring`, data: unsized, pickable: true, stroked: true, filled: true,
        getPosition: pos, getRadius: st.flag ? (r) => (pointFlag(name, r) ? 5.5 : 4.5) : 4.5, radiusUnits: 'pixels',
        // flagged rows (orbital spaceports) are filled dots, the others rings
        getFillColor: st.flag
          ? (r) => (pointFlag(name, r) ? [...st.color, 230] as [number, number, number, number] : [10, 15, 25, 90])
          : [10, 15, 25, 90],
        getLineColor: st.flag
          ? (r) => (pointFlag(name, r) ? [10, 15, 25, 230] : [...st.color, 235]) as [number, number, number, number]
          : [...st.color, 235] as [number, number, number, number],
        lineWidthUnits: 'pixels', getLineWidth: 1.5,
        modelMatrix: shiftBy(0), parameters: ON_TOP,
      }),
    ]
  })

  const hexProps = {
    getHexagon: (i: number) => data.hexes.h3[i],
    getFillColor: (i: number): [number, number, number, number] => [colors![i * 4], colors![i * 4 + 1], colors![i * 4 + 2], colors![i * 4 + 3]],
    getElevation: (i: number) => (lens ? lens.elevation[i] : 0),
    // raw metres of relief (terrain, district and dominant lenses) are exaggerated ×6; metric and
    // yield lenses already scale their columns (normalised value × 60 km, yield × 1.5 km)
    elevationScale: lensDef.kind === 'terrain' || lensDef.kind === 'district' || lensDef.kind === 'dominant' || lensDef.kind === 'history' ? 6 : 1,
    extruded: extrude,
    coverage: extrude ? 0.92 : 0.955,
    highPrecision: false,
    pickable: true,
    autoHighlight: true,
    highlightColor: HEX_HIGHLIGHT,
    updateTriggers: { getFillColor: [colors], getElevation: [lens] },
    material: HEX_MATERIAL,
  }
  const pickGeo = pickLv !== 'hex' ? mapGeo(pickLv) : null
  const adm1Geo = mapGeo('adm1'), adm2Geo = country === 'lka' ? mapGeo('adm2') : null

  const layers = [
    colors && new H3HexagonLayer<number>({ id: `hex-${country}`, data: hexSets.main, modelMatrix: shiftBy(0), ...hexProps }),
    colors && hexSets.east.length > 0 && new H3HexagonLayer<number>({ id: `hex-${country}-east`, data: hexSets.east, modelMatrix: shiftBy(-360), ...hexProps }),
    showAdmin && adm2Geo && new GeoJsonLayer({
      id: `adm2-${country}`, data: adm2Geo, stroked: true, filled: false,
      getLineColor: [230, 220, 190, 70], lineWidthMinPixels: 0.6, pickable: false, modelMatrix: shiftBy(0),
    }),
    showAdmin && adm1Geo && new GeoJsonLayer({
      id: `adm1-${country}`, data: adm1Geo, stroked: true, filled: false,
      getLineColor: [240, 226, 180, 150], lineWidthMinPixels: country === 'usa' ? 0.9 : 1.4, pickable: false, modelMatrix: shiftBy(0),
    }),
    pickGeo && new GeoJsonLayer({
      id: `pick-${country}-${pickLv}`, data: pickGeo, stroked: true, filled: false,
      getLineColor: [255, 255, 255, 60], lineWidthMinPixels: 0.5, pickable: false, modelMatrix: shiftBy(0),
    }),
    selPolys.length > 0 && new PolygonLayer<number[][]>({
      id: `sel-${country}`, data: selPolys, getPolygon: (p) => p, filled: true, stroked: true,
      getFillColor: [255, 240, 190, selFeature ? 22 : 40], getLineColor: [255, 244, 210, selFeature ? 150 : 255],
      lineWidthMinPixels: selPolys.length > 1 ? 1 : 2.5, modelMatrix: shiftBy(0), parameters: ON_TOP,
    }),
    selFeature && new GeoJsonLayer({
      id: `selunit-${country}`, data: [selFeature], stroked: true, filled: true,
      getFillColor: [255, 240, 190, 30], getLineColor: [255, 244, 210, 255], lineWidthMinPixels: 2.5,
      pickable: false, modelMatrix: shiftBy(0), parameters: ON_TOP,
    }),
    ...pointLayers,
    ...(histOn && histAtlas ? historyLayers({
      country, events, year: histYear, eraStart: histEraStart, kinds: histKinds, focus: histFocus, atlas: histAtlas,
      lon: mapLon, modelMatrix: shiftBy(0), onTop: ON_TOP as unknown as Record<string, unknown>,
      maxBadges: zoom < (country === 'usa' ? 5.5 : 8.2) ? (country === 'usa' ? 90 : 45) : 600,
    }) : []),
    showIcons && !histOn && zoom >= iconMinZoom && new IconLayer<IconDatum>({
      id: `icons-${country}`, data: iconData, iconAtlas: atlas.canvas as unknown as string, iconMapping: atlas.mapping,
      getIcon: (d) => d.icon, getPosition: (d) => d.pos, getSize: (d) => d.size, sizeUnits: 'pixels',
      pickable: true, updateTriggers: { getIcon: [atlas] }, modelMatrix: shiftBy(0), parameters: ON_TOP,
    }),
    showIcons && new TextLayer<City>({
      id: `cities-${country}`, data: visibleCities, getPosition: (c) => [mapLon(country, c.lon), c.lat], getText: (c) => `${c.size}  ${c.name}`,
      getSize: compact ? 11 : 13, getColor: [255, 246, 220, 255], background: true, getBackgroundColor: [12, 22, 38, 225],
      backgroundPadding: [6, 3, 6, 3], getBorderColor: [217, 164, 65, 255], getBorderWidth: 1,
      fontFamily: 'system-ui, -apple-system, Segoe UI, sans-serif', fontWeight: 700, getPixelOffset: [0, -26],
      pickable: true, characterSet: 'auto', modelMatrix: shiftBy(0), parameters: ON_TOP,
    }),
  ].filter(Boolean)

  // ---- picking: tiles, or the admin polygon that contains the point
  const hexAt = (lon: number, lat: number): number | null => {
    try { return data.hexIndex.get(latLngToCell(lat, lon, data.meta.h3_res)) ?? null } catch { return null }
  }
  /** Select what lies at a map point (longitude in this map's data frame). Returns a short description. */
  const selectAt = (lon: number, lat: number, hexIdx: number | null): string | null => {
    const st = useStore.getState()
    const tab = st.panel === 'advisors' || st.panel === 'explorer' ? st.panel : 'tile'
    const i = hexIdx ?? hexAt(lon, lat)
    if (pickLv === 'hex') {
      if (i == null) return null
      st.select({ country, kind: 'hex', id: data.hexes.h3[i] }, tab)
      const t = idx.terrain_codes[data.hexes.terrain[i]], f = idx.feature_codes[data.hexes.feature[i]]
      const u = adminUnitOfHex(data, 'adm2', i)
      return `Tile ${terrainLabel(t, f, !!data.hexes.hills[i])}${u ? `, ${u.unit.name}` : ''}`
    }
    let ui = -1
    const fc = mapGeo(pickLv)
    if (fc) {
      const id = adminIdAt(fc, lon, lat)
      if (id != null) ui = adminIndexOf(data, pickLv, id)
    }
    // boundaries not loaded yet, or a click in a gap of the simplified outlines: use the tile's main unit
    if (ui < 0 && i != null) ui = adminUnitOfHex(data, pickLv, i)?.index ?? -1
    if (ui < 0) return null
    st.select({ country, kind: 'admin', id: `${pickLv}:${ui}` }, tab)
    return `${data.admin[pickLv].label} ${data.admin[pickLv].units[ui]?.name ?? ''}`
  }

  /** Phones: pan so a tapped point is not hidden under the bottom sheet that the selection opens. */
  const revealAboveSheet = (lonView: number, lat: number) => {
    const el = ref.current
    // Side by side: both maps shrink to the strip above the sheet (styles.css), and the resize
    // effect above keeps the selection in view
    if (!el || !view || !size || mode === 'split') return
    const visH = visibleHeightAboveSheet(el)
    if (visH == null) return
    const vp = new WebMercatorViewport({ width: size.w, height: size.h, longitude: view.longitude, latitude: view.latitude, zoom: view.zoom, pitch: view.pitch ?? 0, bearing: view.bearing ?? 0 })
    const [x, y] = vp.project([lonView, lat])
    const m = 44
    if (y > m && y < visH - m && x > m && x < size.w - m) return
    const tx = x > m && x < size.w - m ? x : size.w / 2
    const [cLon, cLat] = vp.unproject([size.w / 2 + (x - tx), size.h / 2 + (y - visH / 2)])
    userMoved.current = true
    markMoved(country)
    const anim = prefersReducedMotion() ? {} : { transitionDuration: 450, transitionInterpolator: new LinearInterpolator(['longitude', 'latitude']) }
    setView({ ...still(view), longitude: cLon, latitude: cLat, ...anim } as MapViewState)
  }

  const onClick = (info: PickingInfo) => {
    const id = info.layer?.id ?? ''
    if (id.startsWith('cities-') && info.object) {
      const c = info.object as City
      const el = ref.current
      const visH = el ? visibleHeightAboveSheet(el) : null
      // Side by side on phones: the maps themselves shrink to the strip above the sheet (styles.css)
      if (visH == null || !size || mode === 'split') {
        openCity(country, c) // selects, opens the City View and flies there
        return
      }
      // phones: the City View opens in the bottom sheet, which is still closed at this moment, so
      // aim the fly-to at the middle of the map area that stays visible above the open sheet
      useStore.getState().select({ country, kind: 'city', id: c.id }, 'city')
      let lat = c.lat, lon = c.lon
      try { [lat, lon] = cellToLatLng(c.centre) } catch { /* keep the metro coordinates */ }
      const z = CITY_ZOOM[country]
      const [cLon, cLat] = new WebMercatorViewport({ width: size.w, height: size.h, longitude: lon, latitude: lat, zoom: z })
        .unproject([size.w / 2, size.h / 2 + (size.h - visH) / 2])
      useStore.getState().fly(country, cLon, cLat, z)
      return
    }
    if (id.startsWith('hist-') && info.object) {
      const e = info.object as HistoryEvent
      useStore.getState().setHistory({ focus: e.id })
      if (e.h3) useStore.getState().select({ country, kind: 'hex', id: e.h3 }, 'history')
      return
    }
    // facilities and icons select the tile they sit in; anything else the point under the cursor
    let lonView: number, lat: number
    if (id.startsWith('icons-') && info.object) [lonView, lat] = [(info.object as IconDatum).pos[0] + lonOffset, (info.object as IconDatum).pos[1]]
    else if (id.startsWith('pts-') && info.object) [lonView, lat] = [mapLon(country, Number((info.object as PointRow).lon)) + lonOffset, Number((info.object as PointRow).lat)]
    else if (info.coordinate) [lonView, lat] = info.coordinate as [number, number]
    else return
    const hexIdx = id.startsWith('hex-') && typeof info.object === 'number' ? info.object : null
    if (selectAt(frameLon(country, lonView), lat, hexIdx)) revealAboveSheet(lonView, lat)
  }

  const getTooltip = (info: PickingInfo) => {
    const id = info.layer?.id ?? ''
    if (id.startsWith('hex-') && typeof info.object === 'number') {
      return { html: hexTooltip(data, app, info.object, lensDef, lensMetricSel), className: 'deck-tip' }
    }
    if (id.startsWith('icons-')) return { html: `<b>${escapeHtml((info.object as IconDatum).label)}</b>`, className: 'deck-tip' }
    if (id.startsWith('hist-') && info.object) return { html: historyTooltip(info.object as HistoryEvent), className: 'deck-tip' }
    if (id.startsWith('pts-') && info.object) {
      const name = id.slice(4).replace(/-ring$/, '')
      return { html: pointTooltip(data, name, info.object as PointRow, sources), className: 'deck-tip' }
    }
    if (id.startsWith('cities-') && info.object) {
      const c = info.object as City
      return { html: `<b>${escapeHtml(c.name)}</b><div>City size ${c.size} · ${fmtUnit(c.pop, 'persons')} people in ${c.tiles.length} tiles</div><div class="k">Click for City View</div>`, className: 'deck-tip' }
    }
    return null
  }

  // ---- keyboard access: a labelled, focusable map; Enter selects what is under the centre mark
  const [kbFocus, setKbFocus] = useState(false)
  const [announce, setAnnounce] = useState('')
  const keySelect = useRef<() => void>(() => {})
  keySelect.current = () => {
    if (!view) return
    const lon = frameLon(country, view.longitude)
    const what = selectAt(lon, view.latitude, null)
    setAnnounce(what ? `Selected ${what}. Details are in the Inspect panel.` : 'Nothing to select at the centre of the map.')
  }
  const mounted = view !== null
  useEffect(() => {
    const w = ref.current?.querySelector<HTMLElement>(`#deck-${country}-wrapper`)
    if (!w) return
    w.setAttribute('role', 'application')
    w.setAttribute('aria-roledescription', 'map')
    w.setAttribute('aria-label', `${COUNTRY_META[country].name} map. Arrow keys pan, plus and minus zoom, Enter selects the tile or area under the centre mark.`)
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' && e.key !== ' ') return
      e.preventDefault()
      keySelect.current()
    }
    const onFocus = () => setKbFocus(w.matches(':focus-visible'))
    const onBlur = () => setKbFocus(false)
    w.addEventListener('keydown', onKey)
    w.addEventListener('focus', onFocus)
    w.addEventListener('blur', onBlur)
    return () => { w.removeEventListener('keydown', onKey); w.removeEventListener('focus', onFocus); w.removeEventListener('blur', onBlur) }
  }, [mounted, country])

  const notes: string[] = []
  if (extrude) notes.push(coarsePointer() ? '3D: drag with two fingers to tilt or turn.' : '3D: right-drag, or Ctrl + drag, to tilt or turn.')
  if (pickFallback) notes.push(`No ${pickLevel === 'adm3' ? 'DS divisions' : 'units of that level'} here: clicks select ${plural((data.admin[pickLv]?.label ?? 'unit').toLowerCase())}.`)
  const showKey = pointSets.length > 0
  const lensKey = lensDef.kind === 'metric' ? lensMetricKey(lensDef, lensMetricSel, data, loadedCountries(app)) : null
  const shortJump = compact || (size?.w ?? 1000) < 600

  // Pane overlays (label chip, US jump buttons, notes / facility key, legend card) in pane pixels: a
  // city banner under one of them would be unreadable, so the city declutter treats them as taken.
  const overlayKey = [compact, shortJump, panelOpen, mode, showKey, basemap, lensId, notes.join('|'), view !== null].join(';')
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const targets = () => [
      ...el.querySelectorAll<HTMLElement>('.pane-label, .jump, .mp-tr > *'),
      ...(el.closest('.map-cell')?.querySelectorAll<HTMLElement>('.legend-card') ?? []),
    ]
    const measure = () => {
      const pr = el.getBoundingClientRect()
      const out: Box[] = []
      for (const t of targets()) {
        const r = t.getBoundingClientRect()
        if (r.width > 0 && r.height > 0) out.push([r.left - pr.left - 2, r.top - pr.top - 2, r.right - pr.left + 2, r.bottom - pr.top + 2])
      }
      const key = (b: Box[]) => b.map((x) => x.map(Math.round).join(',')).join(';')
      setOverlays((prev) => (key(prev) === key(out) ? prev : out))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    targets().forEach((t) => ro.observe(t))
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayKey, lens?.legend])

  return (
    <div className={`map-pane ${compact ? 'mp-compact' : ''}`} ref={ref}>
      {view && (
        <DeckGL
          id={`deck-${country}`}
          viewState={view}
          onViewStateChange={onViewStateChange as never}
          controller={{ doubleClickZoom: true, touchRotate: true, dragRotate: extrude }}
          layers={layers}
          onClick={onClick}
          getTooltip={getTooltip as never}
          getCursor={({ isHovering }) => (isHovering ? 'pointer' : 'grab')}
        >
          {basemap !== 'none' && <BaseMap mapStyle={BASEMAPS[basemap]} />}
        </DeckGL>
      )}
      {kbFocus && <div className="mp-crosshair" aria-hidden />}
      <div className="mp-sr" role="status" aria-live="polite">{announce}</div>
      <div className="pane-label">
        <span className="dot" style={{ background: COUNTRY_META[country].accent }} />
        <strong>{data.meta.name}</strong>
        <span className="muted"> · H3 r{data.meta.h3_res} · ~{Math.round(data.meta.avg_hex_km2)} km² tiles</span>
      </div>
      {view && <ScaleBar zoom={view.zoom} lat={view.latitude} />}
      {country === 'usa' && (
        <div className={`jump ${shortJump ? 'mp-jump-compact' : ''}`} role="group" aria-label="Show part of the United States">
          {US_JUMPS.map((j) => (
            <button
              key={j.name}
              title={`Show ${j.name}`}
              aria-label={`Show ${j.name}`}
              onClick={() => {
                if (!size) return
                const vp = new WebMercatorViewport({ width: size.w, height: size.h })
                const f = vp.fitBounds(j.bounds, { padding: padNow(size) })
                useStore.getState().fly('usa', f.longitude, f.latitude, f.zoom)
              }}
            >
              {shortJump ? j.short : j.name}
            </button>
          ))}
        </div>
      )}
      {(notes.length > 0 || showKey || basemap !== 'none') && (
        <div className="mp-tr">
          {notes.map((n) => <div key={n} className="mp-note">{n}</div>)}
          {showKey && (
            <div className="mp-key" role="note" aria-label="Facility circles" title="Same size rule on both maps: circle area is proportional to the facility's size. Circles grow more slowly than the map as you zoom in.">
              {pointSets.map(({ name, sized, unsized, total }) => {
                const st = pointStyle(name)
                const m = data.metricByKey.get(st.metric)
                const shown = sized.length + unsized.length
                const minV = sized.length ? pointValue(name, sized[sized.length - 1]) : NaN
                const noValue = sized.length === 0 && (st.noValueKey?.[country] ?? st.noValue?.[country])
                const dotStyle = { '--c': `rgb(${st.color.join(',')})` } as CSSProperties
                if (st.flag) {
                  return (
                    <div key={name} className="mp-key-row" title={`${st.flag.onTip}. ${st.flag.offTip}.`}>
                      <span className="mp-key-dot solid" style={dotStyle} aria-hidden />
                      <span>
                        <b>{st.plural}</b>: {st.flag.on} · <span className="mp-key-dot ring inline" style={dotStyle} aria-hidden /> {st.flag.off}
                        {/* the FAA list decides orbital vs not; the tooltip names every source */}
                        {m?.sources?.length ? <> <SourceChips ids={m.sources.slice(0, 1)} /></> : null}
                      </span>
                    </div>
                  )
                }
                return (
                  <div key={name} className="mp-key-row">
                    <span className={`mp-key-dot ${noValue ? 'ring' : ''}`} style={dotStyle} aria-hidden />
                    <span>
                      <b>{st.plural}</b>
                      {noValue ? <>: {noValue}</> : <>: circle area = {st.sizeLabel}</>}
                      {!noValue && shown < total && <span className="mp-key-sub"> · {fmtInt(shown)} of {fmtInt(total)} shown, all ≥ {fmtUnit(minV, st.unit)}</span>}
                      {/* the legend already cites the lens metric's sources; cite the others here */}
                      {!noValue && m?.sources?.length && st.metric !== lensKey ? <> <SourceChips ids={m.sources} /></> : null}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
          {basemap !== 'none' && (
            <div className="mp-attrib">
              © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions" target="_blank" rel="noreferrer">CARTO</a>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Where the current selection sits in this country ([lon, lat], data frame), or null. */
function selectionPoint(d: CountryData, sel: ReturnType<typeof useStore.getState>['selection'], country: CountryId): [number, number] | null {
  if (!sel || sel.country !== country) return null
  try {
    if (sel.kind === 'hex') { const [la, lo] = cellToLatLng(sel.id); return [lo, la] }
    if (sel.kind === 'city') {
      const c = d.cities.find((x) => x.id === sel.id)
      if (!c) return null
      const [la, lo] = cellToLatLng(c.centre)
      return [lo, la]
    }
    if (sel.kind === 'wonder') { const w = d.wonders.find((x) => x.name === sel.id); return w ? [w.lon, w.lat] : null }
    if (sel.kind === 'admin') {
      const [lvl, ui] = sel.id.split(':')
      const u = d.admin[lvl]?.units[Number(ui)]
      return u && Number.isFinite(u.lon) && Number.isFinite(u.lat) ? [u.lon, u.lat] : null
    }
  } catch { /* malformed id */ }
  return null
}

// tile centres, cached per hexes table (kept across the metric-matrix arrival, which swaps CountryData)
const centreCache = new WeakMap<CountryData['hexes'], Float64Array>()
function hexCenter(d: CountryData, i: number): [number, number] {
  let c = centreCache.get(d.hexes)
  if (!c) {
    c = new Float64Array(d.n * 2).fill(NaN)
    centreCache.set(d.hexes, c)
  }
  if (Number.isNaN(c[i * 2])) {
    const [la, lo] = cellToLatLng(d.hexes.h3[i])
    c[i * 2] = lo; c[i * 2 + 1] = la
  }
  return [c[i * 2], c[i * 2 + 1]]
}

const loadedCountries = (app: AppData) => Object.values(app.countries).filter(Boolean) as CountryData[]

function hexTooltip(d: CountryData, app: AppData, i: number, lens: LensDef, lensMetric?: string) {
  const idx = app.index!
  const t = idx.terrain_codes[d.hexes.terrain[i]]
  const f = idx.feature_codes[d.hexes.feature[i]]
  const dist = idx.districts[d.hexes.district[i]]?.code ?? ''
  const unit = adminUnitOfHex(d, 'adm2', i)
  const y = YIELDS.map((k) => {
    const m = YIELD_META[k]
    return `<span class="y" style="color:${m.color}" title="${m.label}"><span aria-hidden="true">${m.glyph}</span>${d.hexes.yields[k][i]}</span>`
  }).join('')
  let lensLine = ''
  if (lens.kind === 'metric') {
    // the metric the map shows for this country; never another metric when it is not published here
    const all = loadedCountries(app)
    const key = lensMetricKey(lens, lensMetric, d, all)
    const wanted = lensMetric || lensMetricKeys(lens, all.length ? all : [d])[0]
    if (key) {
      const m = d.metricByKey.get(key)!
      // a land statistic has no value on a sea tile (the map dims it too)
      const sea = d.hexes.ring[i] > 0 && landOnlyMetric(m)
      const v = sea ? NaN : value(d, key, i)
      const shown = !d.matrixReady ? 'loading…' : sea ? 'none at sea' : key === 'pop_density' ? `${fmtNumber(v)} people per km² of land` : fmtUnit(v, m.unit)
      const dens = isDensity(d, key) && Number.isFinite(v) ? ` <span class="k">(${fmtUnit(v / d.hexArea[i], m.unit)} per km² of tile)</span>` : ''
      lensLine = `<div><span class="k">${escapeHtml(m.label)}</span> ${shown}${dens}</div>`
    } else if (wanted) {
      lensLine = `<div><span class="k">${escapeHtml(metricLabel(wanted, all))}</span> not published for ${COUNTRY_META[d.id].name}</div>`
    }
  }
  const pop = value(d, 'pop', i)
  // "district" is also a game concept (Specialist district line above), so name the admin unit explicitly
  const unitLabel = d.admin.adm2.label === 'District' ? 'Admin district' : d.admin.adm2.label
  return `<div class="tt-title">${terrainLabel(t, f, !!d.hexes.hills[i])}</div>
    <div class="tt-yields">${y}</div>
    ${dist ? `<div><span class="k">Specialist district</span> ${DISTRICT_META[dist]?.icon ?? ''} ${DISTRICT_META[dist]?.label ?? dist}</div>` : ''}
    ${unit ? `<div><span class="k">${unitLabel}</span> ${escapeHtml(unit.unit.name)}</div>` : ''}
    <div><span class="k">Population</span> ${d.matrixReady ? fmtUnit(pop, 'persons') : 'loading…'}</div>${lensLine}`
}
