export function ScaleBar({ zoom, lat }: { zoom: number; lat: number }) {
  const mpp = (78271.517 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom)
  const target = mpp * 110
  const pow = Math.pow(10, Math.floor(Math.log10(target)))
  const nice = [1, 2, 5, 10].map((k) => k * pow).filter((v) => v <= target).pop() ?? pow
  const px = nice / mpp
  const label = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`
  return (
    <div className="scalebar" aria-label={`Scale: ${label}`}>
      <div className="bar" style={{ width: px }} />
      <span>{label}</span>
    </div>
  )
}
