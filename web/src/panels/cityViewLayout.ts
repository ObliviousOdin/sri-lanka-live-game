// Hex-ring layout for the City View: places a city's H3 tiles on a pointy-top hex lattice.
//
// H3 local IJ coordinates give exact lattice positions (x = i - j/2, y = j·√3/2, neighbours at
// distance 1). Their orientation is arbitrary (it depends on the icosahedron face and on the
// resolution class), so we pick the one of the 12 lattice symmetries (6 rotations × mirror) that best
// matches the tiles' real geographic layout — north stays up and the sea stays on the correct side,
// while the tiles still tessellate perfectly. Tiles that cellToLocalIj cannot reach (across an
// icosahedron edge) fall back to an equirectangular lat/lon projection in the same frame.
import { cellToLatLng, cellToLocalIj, gridDisk } from 'h3-js'

/** SVG units between neighbouring tile centres. */
export const UNIT = 60
/** Circumradius of a pointy-top hex whose neighbours are UNIT apart. */
export const HEX_R = UNIT / Math.sqrt(3)

export interface RingCell { h3: string; x: number; y: number; ghost: boolean }
export interface RingLayout {
  cells: RingCell[]
  pos: Map<string, { x: number; y: number }>
  viewBox: [number, number, number, number]
  method: 'h3-ij' | 'lat/lon' | 'mixed'
}

const S3 = Math.sqrt(3) / 2
type M2 = [number, number, number, number] // [a, b, c, d] → (a·x + b·y, c·x + d·y)
const ORIENTS: M2[] = []
for (let k = 0; k < 6; k++) {
  const a = (k * Math.PI) / 3
  const c = Math.cos(a), s = Math.sin(a)
  ORIENTS.push([c, -s, s, c]) // rotation
  ORIENTS.push([c, s, s, -c]) // rotation ∘ mirror(y)
}

/** Pointy-top hexagon polygon points around (cx, cy). */
export function hexPoints(cx: number, cy: number, r = HEX_R): string {
  const pts: string[] = []
  for (let k = 0; k < 6; k++) {
    const a = ((-90 + 60 * k) * Math.PI) / 180
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
}

export function layoutRing(centre: string, cells: string[], radius: number): RingLayout {
  const present = new Set(cells)
  let disk: string[] = []
  try { disk = gridDisk(centre, radius) } catch { disk = [] }
  const all = [...new Set([centre, ...cells, ...disk])]

  // geographic frame: equirectangular around the centre, screen y pointing south
  const [lat0, lon0] = cellToLatLng(centre)
  const kx = Math.cos((lat0 * Math.PI) / 180)
  const geo = new Map<string, [number, number]>()
  for (const h of all) {
    const [la, lo] = cellToLatLng(h)
    let dl = lo - lon0
    if (dl > 180) dl -= 360
    if (dl < -180) dl += 360
    geo.set(h, [dl * kx, -(la - lat0)])
  }

  // lattice positions from local IJ
  const lat = new Map<string, [number, number]>()
  let c0: [number, number] | null = null
  try {
    const { i, j } = cellToLocalIj(centre, centre)
    c0 = [i - 0.5 * j, j * S3]
  } catch { c0 = null }
  if (c0) {
    for (const h of all) {
      try {
        const { i, j } = cellToLocalIj(centre, h)
        lat.set(h, [i - 0.5 * j - c0[0], j * S3 - c0[1]])
      } catch { /* falls back to lat/lon below */ }
    }
  }

  // choose the lattice symmetry that best matches geography (least squares, free scale)
  let best: M2 = ORIENTS[0]
  let bestScale = 0
  let bestErr = Infinity
  const fitPairs = [...lat.entries()].filter(([h]) => h !== centre)
  if (fitPairs.length >= 2) {
    for (const o of ORIENTS) {
      let qg = 0, qq = 0
      const qs: [number, number, number, number][] = []
      for (const [h, p] of fitPairs) {
        const q: [number, number] = [o[0] * p[0] + o[1] * p[1], o[2] * p[0] + o[3] * p[1]]
        const g = geo.get(h)!
        qg += q[0] * g[0] + q[1] * g[1]
        qq += q[0] * q[0] + q[1] * q[1]
        qs.push([q[0], q[1], g[0], g[1]])
      }
      const s = qq > 0 ? qg / qq : 0
      if (s <= 0) continue
      let err = 0
      for (const [qx, qy, gx, gy] of qs) err += (gx - s * qx) ** 2 + (gy - s * qy) ** 2
      if (err < bestErr) { bestErr = err; best = o; bestScale = s }
    }
  }

  // fallback scale (degrees per lattice step) from nearest-neighbour spacing
  if (!(bestScale > 0)) {
    const g = [...geo.values()]
    const nn: number[] = []
    for (let a = 0; a < g.length; a++) {
      let m = Infinity
      for (let b = 0; b < g.length; b++) if (a !== b) m = Math.min(m, Math.hypot(g[a][0] - g[b][0], g[a][1] - g[b][1]))
      if (Number.isFinite(m)) nn.push(m)
    }
    nn.sort((a, b) => a - b)
    bestScale = nn.length ? nn[Math.floor(nn.length / 2)] : 1
  }

  const useLattice = bestErr < Infinity
  let nIj = 0, nGeo = 0
  const out: RingCell[] = []
  const pos = new Map<string, { x: number; y: number }>()
  for (const h of all) {
    const p = lat.get(h)
    let x: number, y: number
    if (useLattice && p) {
      x = best[0] * p[0] + best[1] * p[1]
      y = best[2] * p[0] + best[3] * p[1]
      nIj++
    } else {
      const g = geo.get(h)!
      x = g[0] / bestScale
      y = g[1] / bestScale
      nGeo++
    }
    const cell = { h3: h, x: x * UNIT, y: y * UNIT, ghost: !present.has(h) }
    out.push(cell)
    pos.set(h, { x: cell.x, y: cell.y })
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const c of out) {
    minX = Math.min(minX, c.x); maxX = Math.max(maxX, c.x)
    minY = Math.min(minY, c.y); maxY = Math.max(maxY, c.y)
  }
  const pad = HEX_R + 4
  return {
    cells: out,
    pos,
    viewBox: [minX - pad, minY - pad, maxX - minX + 2 * pad, maxY - minY + 2 * pad],
    method: nGeo === 0 ? 'h3-ij' : nIj === 0 ? 'lat/lon' : 'mixed',
  }
}
