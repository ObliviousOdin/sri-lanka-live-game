import type { RGB } from './civ'

// Colour ramps (stops sampled from well-known perceptual colormaps).
export const PALETTES: Record<string, RGB[]> = {
  viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [109, 205, 89], [180, 222, 44], [253, 231, 37]],
  magma: [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]],
  inferno: [[0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85], [227, 89, 51], [249, 140, 10], [249, 201, 50], [252, 255, 164]],
  cividis: [[0, 34, 78], [37, 60, 110], [76, 84, 108], [108, 108, 113], [136, 133, 120], [167, 160, 117], [200, 189, 104], [234, 220, 80], [255, 234, 70]],
  blues: [[12, 40, 80], [20, 70, 130], [40, 110, 180], [80, 150, 210], [130, 190, 230], [190, 225, 245]],
  greens: [[0, 60, 30], [20, 100, 50], [50, 140, 70], [100, 180, 90], [160, 210, 120], [220, 240, 170]],
  reds: [[60, 10, 20], [120, 20, 30], [180, 40, 40], [220, 90, 60], [245, 150, 100], [255, 210, 170]],
  hazard: [[40, 50, 60], [90, 80, 60], [150, 100, 50], [200, 110, 40], [235, 80, 40], [255, 40, 60]],
  terrain: [[40, 90, 60], [90, 140, 70], [170, 170, 90], [190, 150, 90], [160, 120, 90], [140, 120, 110], [230, 230, 235]],
  rdbu: [[178, 24, 43], [214, 96, 77], [244, 165, 130], [247, 247, 247], [146, 197, 222], [67, 147, 195], [33, 102, 172]],
}

export function ramp(stops: RGB[], t: number): RGB {
  if (!Number.isFinite(t)) return [80, 80, 80]
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(x))
  const f = x - i
  const a = stops[i], b = stops[i + 1]
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]
}

export function rgbCss(c: RGB, a = 1) {
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a})`
}

export function mix(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

export function shade(c: RGB, k: number): RGB {
  return [c[0] * k, c[1] * k, c[2] * k]
}

/** Yield ramp: from a dark neutral to the yield colour. */
export function yieldColor(base: RGB, y: number, cap: number): RGB {
  if (y <= 0) return [34, 40, 48]
  const t = Math.min(1, y / cap)
  return mix([48, 52, 58], base, 0.25 + 0.75 * Math.pow(t, 0.8))
}

export const CATEGORICAL: RGB[] = [
  [242, 169, 0], [91, 141, 239], [76, 175, 80], [229, 57, 53], [171, 71, 188], [0, 172, 193],
  [255, 112, 67], [141, 110, 99], [189, 189, 189], [255, 238, 88], [38, 166, 154], [236, 64, 122],
]

/** Robust domain (quantiles) of positive / finite values across arrays. */
export function robustDomain(arrays: ArrayLike<number>[], log: boolean, qlo = 0.02, qhi = 0.98): [number, number] {
  const vals: number[] = []
  for (const arr of arrays) {
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i]
      if (Number.isFinite(v) && (!log || v > 0)) vals.push(v)
    }
  }
  if (!vals.length) return [0, 1]
  vals.sort((a, b) => a - b)
  const q = (p: number) => vals[Math.min(vals.length - 1, Math.max(0, Math.floor(p * (vals.length - 1))))]
  let lo = q(qlo), hi = q(qhi)
  if (hi <= lo) hi = lo + (log ? lo : 1)
  return [lo, hi]
}

export function scaleT(v: number, dom: [number, number], log: boolean): number {
  if (!Number.isFinite(v)) return NaN
  if (log) {
    if (v <= 0) return -1
    return (Math.log10(v) - Math.log10(dom[0])) / (Math.log10(dom[1]) - Math.log10(dom[0]))
  }
  return (v - dom[0]) / (dom[1] - dom[0])
}

export function invScale(t: number, dom: [number, number], log: boolean): number {
  if (log) return Math.pow(10, Math.log10(dom[0]) + t * (Math.log10(dom[1]) - Math.log10(dom[0])))
  return dom[0] + t * (dom[1] - dom[0])
}
