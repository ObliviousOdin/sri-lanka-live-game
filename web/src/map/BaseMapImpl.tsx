import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

export default function BaseMapImpl(props: { mapStyle: string; [k: string]: unknown }) {
  return <Map reuseMaps attributionControl={false} {...props} />
}
