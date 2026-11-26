import { FlyToInterpolator, WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { GeoJsonLayer, IconLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Map as BaseMap } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
import { COUNTRY_META, DISTRICT_META, YIELD_META, terrainLabel } from '../civ'
import { adminUnitOfHex, hexesOfAdmin, loadAdminGeo, value } from '../data/load'
import type { AppData } from '../data/useData'
import { fmtUnit } from '../format'
import { LENS_BY_ID, isDensity, type LensResult } from '../lenses'
import { useStore } from '../store'
import { YIELDS, type CountryData, type CountryId } from '../types'
import { emojiAtlas } from './emojiAtlas'
import { ScaleBar } from '../components/ScaleBar'

const INITIAL_BOUNDS: Record<CountryId, [[number, number], [number, number]]> = {
  lka: [[79.5, 5.85], [82.0, 9.9]],
  usa: [[-125.0, 24.3], [-66.9, 49.4]],
}

const BASEMAPS = {
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
}

export const WONDER_ICON: Record<string, string> = {
  'Great University': '📚', 'Great Port': '🚢', 'Great Power Station': '🔋', 'Natural Wonder': '🏞️',
  'Great Airport': '🛫', 'World Heritage': '🏯', 'Holy Wonder': '🕌',
}

const POINT_COLORS: Record<string, [number, number, number]> = {
  power_plants: [250, 210, 60], ports: [90, 190, 255], airports: [200, 220, 255],
}
const POINT_VALUE: Record<string, string[]> = {
  power_plants: ['capacity_mw'], ports: ['port_cargo_t', 'cargo_tonnes'], airports: ['airport_pax', 'passengers'],
}

function metersPerPixel(zoom: number, lat: number) {
  return (78271.517 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom)
}

interface Props { country: CountryId; data: CountryData; app: AppData; lens: LensResult | null; compact?: boolean }

export function MapPane({ country, data, app, lens, compact }: Props) {
  const idx = app.index!
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [view, setView] = useState<MapViewState | null>(null)
  const st = useStore()
  const { lensId, extrude, basemap, showAdmin, showIcons, sameScale, pickLevel, selection, explorer, flyTo, mode } = st
  const lensDef = LENS_BY_ID.get(lensId)!

  // ---- sizing / initial view
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])
  // fit the country on first render, and again on resize until the user moves the map
  const userMoved = useRef(false)
  useEffect(() => {
    if (!size || size.w < 10 || (view && userMoved.current)) return
    const vp = new WebMercatorViewport({ width: size.w, height: size.h })
    const { longitude, latitude, zoom } = vp.fitBounds(INITIAL_BOUNDS[country], { padding: Math.min(40, size.w / 10) })
    setView((v) => ({ ...(v ?? { pitch: 0, bearing: 0 }), longitude, latitude, zoom }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, country])

  // ---- fly-to requests
  useEffect(() => {
    if (!flyTo || flyTo.country !== country || !view) return
    userMoved.current = true
    setView({ ...view, longitude: flyTo.lon, latitude: flyTo.lat, zoom: flyTo.zoom ?? Math.max(view.zoom, country === 'lka' ? 9 : 7.5),
      transitionDuration: 900, transitionInterpolator: new FlyToInterpolator() } as MapViewState)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyTo?.ts])

  // ---- same-scale linking between the two panes
  const other: CountryId = country === 'lka' ? 'usa' : 'lka'
  const otherRef = useStore((s) => s.zoomRef[other])
  useEffect(() => {
    if (!sameScale || mode !== 'split' || !otherRef || !view) return
    const mpp = metersPerPixel(otherRef.zoom, otherRef.lat)
    const z = Math.log2((78271.517 * Math.cos((view.latitude * Math.PI) / 180)) / mpp)
    if (Math.abs(z - view.zoom) > 0.02) setView({ ...view, zoom: z })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [otherRef?.zoom, otherRef?.lat, sameScale, mode])

  const onViewStateChange = ({ viewState, interactionState }: { viewState: MapViewState; interactionState?: { isDragging?: boolean; isZooming?: boolean; isPanning?: boolean } }) => {
    setView(viewState)
    if (interactionState && (interactionState.isZooming || interactionState.isDragging || interactionState.isPanning)) {
      userMoved.current = true
      useStore.setState((s) => ({ zoomRef: { ...s.zoomRef, [country]: { zoom: viewState.zoom, lat: viewState.latitude } } }))
    }
  }

  // ---- admin outlines
  const [geo, setGeo] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  useEffect(() => {
    const lv = country === 'lka' ? ['adm1', 'adm2'] : ['adm1']
    lv.forEach((l) => loadAdminGeo(country, l).then((g) => setGeo((s) => ({ ...s, [l]: g }))).catch(() => {}))
  }, [country])
  useEffect(() => {
    if (pickLevel !== 'hex' && !geo[pickLevel] && data.meta.admin_levels[pickLevel])
      loadAdminGeo(country, pickLevel).then((g) => setGeo((s) => ({ ...s, [pickLevel]: g }))).catch(() => {})
  }, [pickLevel, country, geo, data])

  // ---- colours with brushing / admin-selection emphasis
  const indices = useMemo(() => Array.from({ length: data.n }, (_, i) => i), [data])
  const brushed = explorer.brushed[country]
  const colors = useMemo(() => {
    if (!lens) return null
    if (!brushed || !brushed.size) return lens.colors
    const c = lens.colors.slice()
    for (let i = 0; i < data.n; i++) {
      if (!brushed.has(i)) { c[i * 4] *= 0.25; c[i * 4 + 1] *= 0.25; c[i * 4 + 2] *= 0.25 }
    }
    return c
  }, [lens, brushed, data])

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
    return []
  }, [selection, country, data])

  const zoom = view?.zoom ?? 4
  const iconMinZoom = country === 'lka' ? 7.6 : 5.6

  // ---- icons (districts + wonders)
  const iconData = useMemo(() => {
    const out: { pos: [number, number]; icon: string; size: number; kind: 'district' | 'wonder'; label: string }[] = []
    const codes = idx.districts.map((x) => x.code)
    const minor = new Set(['neighborhood', 'farmland', 'preserve', 'fishery', 'plantation', 'encampment'])
    for (let i = 0; i < data.n; i++) {
      const code = codes[data.hexes.district[i]]
      if (!code) continue
      if (minor.has(code) && zoom < iconMinZoom + 1.2) continue
      const h = data.hexes.h3[i]
      const ll = hexCenter(data, i)
      out.push({ pos: ll, icon: DISTRICT_META[code]?.icon ?? '•', size: code === 'city_center' ? 30 : 22, kind: 'district', label: `${DISTRICT_META[code]?.label} · ${h}` })
    }
    for (const w of data.wonders) out.push({ pos: [w.lon, w.lat], icon: WONDER_ICON[w.category] ?? '★', size: 30, kind: 'wonder', label: `${w.category}: ${w.name}` })
    return out
  }, [data, idx, zoom >= iconMinZoom + 1.2])
  const atlas = useMemo(() => emojiAtlas([...new Set(iconData.map((d) => d.icon))]), [iconData])

  const pointLayers = (lensDef.pointLayers ?? []).flatMap((name) => {
    const layer = data.points[name]
    if (!layer) return []
    const vk = POINT_VALUE[name] ?? []
    const rows = layer.rows.filter((r) => vk.some((k) => Number(r[k]) > 0))
    const val = (r: Record<string, unknown>) => Number(vk.map((k) => r[k]).find((v) => Number(v) > 0) ?? 0)
    const maxV = Math.max(1, ...rows.map(val))
    return [new ScatterplotLayer({
      id: `pts-${name}`, data: rows, pickable: true, stroked: true, filled: true,
      getPosition: (r: Record<string, unknown>) => [Number(r.lon), Number(r.lat)],
      getRadius: (r: Record<string, unknown>) => 3 + 22 * Math.sqrt(val(r) / maxV),
      radiusUnits: 'pixels', getFillColor: [...(POINT_COLORS[name] ?? [255, 255, 255]), 170] as [number, number, number, number],
      getLineColor: [10, 15, 25, 220], lineWidthMinPixels: 1,
    })]
  })

  const layers = [
    colors && new H3HexagonLayer<number>({
      id: `hex-${country}`,
      data: indices,
      getHexagon: (i) => data.hexes.h3[i],
      getFillColor: (i) => [colors[i * 4], colors[i * 4 + 1], colors[i * 4 + 2], colors[i * 4 + 3]],
      getElevation: (i) => (lens ? lens.elevation[i] : 0),
      elevationScale: lensDef.kind === 'terrain' || lensId === 'elevation' ? 6 : 1,
      extruded: extrude,
      coverage: extrude ? 0.92 : 0.955,
      highPrecision: false,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 236, 170, 110],
      updateTriggers: { getFillColor: [colors], getElevation: [lens] },
      material: { ambient: 0.55, diffuse: 0.6, shininess: 20 },
    }),
    showAdmin && geo.adm2 && country === 'lka' && new GeoJsonLayer({
      id: `adm2-${country}`, data: geo.adm2, stroked: true, filled: false,
      getLineColor: [230, 220, 190, 70], lineWidthMinPixels: 0.6, pickable: false,
    }),
    showAdmin && geo.adm1 && new GeoJsonLayer({
      id: `adm1-${country}`, data: geo.adm1, stroked: true, filled: false,
      getLineColor: [240, 226, 180, 150], lineWidthMinPixels: country === 'usa' ? 0.9 : 1.4, pickable: false,
    }),
    pickLevel !== 'hex' && geo[pickLevel] && new GeoJsonLayer({
      id: `pick-${country}-${pickLevel}`, data: geo[pickLevel], stroked: true, filled: false,
      getLineColor: [255, 255, 255, 60], lineWidthMinPixels: 0.5,
    }),
    selectedHexes.length > 0 && new H3HexagonLayer<string>({
      id: `sel-${country}`, data: selectedHexes, getHexagon: (h) => h, filled: true, stroked: true, extruded: false,
      getFillColor: [255, 240, 190, 40], getLineColor: [255, 244, 210, 255], lineWidthMinPixels: selectedHexes.length > 1 ? 1 : 2.5,
      highPrecision: true,
    }),
    ...pointLayers,
    showIcons && zoom >= iconMinZoom && new IconLayer({
      id: `icons-${country}`, data: iconData, iconAtlas: atlas.canvas as unknown as string, iconMapping: atlas.mapping,
      getIcon: (d) => d.icon, getPosition: (d) => d.pos, getSize: (d) => d.size, sizeUnits: 'pixels',
      pickable: true, updateTriggers: { getIcon: [atlas] },
    }),
    showIcons && new TextLayer({
      id: `cities-${country}`, data: data.cities, getPosition: (c) => [c.lon, c.lat], getText: (c) => `${c.size}  ${c.name}`,
      getSize: compact ? 11 : 13, getColor: [255, 246, 220, 255], background: true, getBackgroundColor: [12, 22, 38, 225],
      backgroundPadding: [6, 3, 6, 3], getBorderColor: [217, 164, 65, 255], getBorderWidth: 1,
      fontFamily: 'system-ui, -apple-system, Segoe UI, sans-serif', fontWeight: 700, getPixelOffset: [0, -26],
      pickable: true, characterSet: 'auto',
    }),
  ].filter(Boolean)

  const onClick = (info: PickingInfo) => {
    if (!info.layer) return
    const id = info.layer.id
    if (id.startsWith('cities-')) {
      const c = info.object as CountryData['cities'][number]
      useStore.getState().select({ country, kind: 'city', id: c.id }, 'city')
      return
    }
    if (id.startsWith('hex-') && typeof info.index === 'number' && info.index >= 0) {
      const i = info.index
      if (pickLevel === 'hex') useStore.getState().select({ country, kind: 'hex', id: data.hexes.h3[i] }, 'tile')
      else {
        const u = adminUnitOfHex(data, pickLevel, i)
        if (u) useStore.getState().select({ country, kind: 'admin', id: `${pickLevel}:${u.index}` }, 'tile')
      }
    }
  }

  const getTooltip = (info: PickingInfo) => {
    if (!info.layer) return null
    if (info.layer.id.startsWith('hex-') && typeof info.index === 'number' && info.index >= 0) {
      return { html: hexTooltip(data, app, info.index, lensId, st.lensMetric[lensId]), className: 'deck-tip' }
    }
    if (info.layer.id.startsWith('icons-')) return { html: `<b>${(info.object as { label: string }).label}</b>`, className: 'deck-tip' }
    if (info.layer.id.startsWith('pts-')) {
      const r = info.object as Record<string, unknown>
      const extra = Object.entries(r).filter(([k]) => !['id', 'lat', 'lon', 'h3', 'name'].includes(k)).slice(0, 5)
        .map(([k, v]) => `<div><span class="k">${k}</span> ${typeof v === 'number' ? v.toLocaleString() : v}</div>`).join('')
      return { html: `<b>${r.name}</b>${extra}`, className: 'deck-tip' }
    }
    if (info.layer.id.startsWith('cities-')) {
      const c = info.object as CountryData['cities'][number]
      return { html: `<b>${c.name}</b><div>City size ${c.size} · ${fmtUnit(c.pop, 'persons')} people in ${c.tiles.length} tiles</div><div class="k">Click for City View</div>`, className: 'deck-tip' }
    }
    return null
  }

  return (
    <div className="map-pane" ref={ref}>
      {view && (
        <DeckGL
          viewState={view}
          onViewStateChange={onViewStateChange as never}
          controller={{ doubleClickZoom: true, touchRotate: true, dragRotate: extrude }}
          layers={layers}
          onClick={onClick}
          getTooltip={getTooltip as never}
          getCursor={({ isHovering }) => (isHovering ? 'pointer' : 'grab')}
        >
          {basemap !== 'none' && <BaseMap mapStyle={BASEMAPS[basemap]} reuseMaps attributionControl={false} />}
        </DeckGL>
      )}
      <div className="pane-label">
        <span className="dot" style={{ background: COUNTRY_META[country].accent }} />
        <strong>{data.meta.name}</strong>
        <span className="muted"> · H3 r{data.meta.h3_res} · ~{Math.round(data.meta.avg_hex_km2)} km² tiles</span>
      </div>
      {view && <ScaleBar zoom={view.zoom} lat={view.latitude} />}
      {country === 'usa' && !compact && (
        <div className="jump">
          {([['CONUS', -98.5, 39.5, 3.4], ['Alaska', -152, 63.5, 3.2], ['Hawaii', -157.5, 20.6, 5.8]] as const).map(([n, lo, la, z]) => (
            <button key={n} onClick={() => useStore.getState().fly('usa', lo, la, z)}>{n}</button>
          ))}
        </div>
      )}
    </div>
  )
}

const centreCache = new WeakMap<CountryData, Float64Array>()
import { cellToLatLng } from 'h3-js'
function hexCenter(d: CountryData, i: number): [number, number] {
  let c = centreCache.get(d)
  if (!c) {
    c = new Float64Array(d.n * 2).fill(NaN)
    centreCache.set(d, c)
  }
  if (Number.isNaN(c[i * 2])) {
    const [la, lo] = cellToLatLng(d.hexes.h3[i])
    c[i * 2] = lo; c[i * 2 + 1] = la
  }
  return [c[i * 2], c[i * 2 + 1]]
}

function hexTooltip(d: CountryData, app: AppData, i: number, lensId: string, lensMetric?: string) {
  const idx = app.index!
  const t = idx.terrain_codes[d.hexes.terrain[i]]
  const f = idx.feature_codes[d.hexes.feature[i]]
  const dist = idx.districts[d.hexes.district[i]]?.code ?? ''
  const unit = adminUnitOfHex(d, 'adm2', i)
  const y = YIELDS.map((k) => `<span class="y" style="color:${YIELD_META[k].color}">${d.hexes.yields[k][i]}</span>`).join(' ')
  const lens = LENS_BY_ID.get(lensId)
  let lensLine = ''
  if (lens?.kind === 'metric') {
    const key = lensMetric && d.metricByKey.has(lensMetric) ? lensMetric : (lens.metrics ?? []).find((k) => d.metricByKey.has(k))
    if (key) {
      const m = d.metricByKey.get(key)!
      const v = value(d, key, i)
      lensLine = `<div><span class="k">${m.label}</span> ${fmtUnit(v, m.unit)}${isDensity(d, key) ? ` <span class="k">(${fmtUnit(v / d.hexArea[i], m.unit)}/km²)</span>` : ''}</div>`
    }
  }
  const pop = value(d, 'pop', i)
  return `<div class="tt-title">${terrainLabel(t, f, !!d.hexes.hills[i])}${dist ? ` · ${DISTRICT_META[dist]?.icon ?? ''} ${DISTRICT_META[dist]?.label ?? ''}` : ''}</div>
    <div class="tt-yields">${y}</div>
    ${unit ? `<div><span class="k">${d.admin.adm2.label}</span> ${unit.unit.name}</div>` : ''}
    <div><span class="k">Population</span> ${fmtUnit(pop, 'persons')}</div>${lensLine}`
}
