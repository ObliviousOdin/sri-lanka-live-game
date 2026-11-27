// Optional MapLibre basemap, loaded on demand: the maplibre-gl chunk (~780 KB) and its CSS are only
// fetched once a basemap is chosen, since the default is "No basemap".
// DeckGL recognises this element as a react-map-gl map by its `mapStyle` prop and clones it with
// the view props (viewState, width, height, style), which are forwarded to the real Map.
import { lazy, Suspense } from 'react'

const BaseMapImpl = lazy(() => import('./BaseMapImpl'))

export function BaseMap(props: { mapStyle: string; [k: string]: unknown }) {
  return (
    <Suspense fallback={null}>
      <BaseMapImpl {...props} />
    </Suspense>
  )
}
