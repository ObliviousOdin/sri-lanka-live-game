import { metersPerPixel } from '../map/scaleLink'

export function ScaleBar({ zoom, lat, className }: { zoom: number; lat: number; className?: string }) {
  const mpp = metersPerPixel(zoom, lat)
  const target = mpp * 110
  const pow = Math.pow(10, Math.floor(Math.log10(target)))
  const nice = [1, 2, 5, 10].map((k) => k * pow).filter((v) => v <= target).pop() ?? pow
  const px = nice / mpp
  const label = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`
  return (
    <div className={`scalebar ${className ?? ''}`} role="img" aria-label={`Scale: ${label}`}>
      <div className="bar" style={{ width: px }} />
      <span>{label}</span>
    </div>
  )
}
