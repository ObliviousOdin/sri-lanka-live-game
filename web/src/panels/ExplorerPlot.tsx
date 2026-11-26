// Explorer scatter plot: points on <canvas> (USA has ~41k tiles), axes / fits / rings / brush in SVG.
// Hover uses a screen-space grid index rebuilt when data, axes or size change.
import { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { scaleLinear, scaleLog } from 'd3-scale'
import type { CountryId } from '../types'
import { fmtShort, type BrushInfo, type ColorBy, type PlotData, type Targets } from './explorerData'

const RGB: Record<CountryId, [number, number, number]> = { lka: [242, 169, 0], usa: [91, 141, 239] }
const M = { l: 50, r: 10, t: 8, b: 22 }
const CELL = 16

interface Scale { (v: number): number; ticks(n?: number): number[] }

function markStyle(count: number) {
  if (count <= 60) return { r: 4.6, a: 0.92, round: true, stroke: true }
  if (count <= 400) return { r: 3.3, a: 0.8, round: true, stroke: true }
  if (count <= 3000) return { r: 2.4, a: 0.62, round: true, stroke: false }
  if (count <= 12000) return { r: 2.6, a: 0.42, round: false, stroke: false }
  return { r: 2.2, a: 0.3, round: false, stroke: false }
}

const mantissa = (v: number) => Math.round(v / Math.pow(10, Math.floor(Math.log10(v) + 1e-9)))

function axisTicks(sc: Scale, log: boolean, dom: [number, number], px: number) {
  const target = Math.max(2, Math.min(7, Math.floor(px / 64)))
  if (!log) return { major: sc.ticks(target), minor: [] as number[] }
  const all = sc.ticks().filter((v) => v > 0)
  // richest mantissa set that still fits: 1 → 1,3 → 1,2,5 → every tick
  const sets = [[1], [1, 3], [1, 2, 5], [1, 2, 3, 4, 5, 6, 7, 8, 9]]
  let major = all.filter((v) => mantissa(v) === 1)
  for (const set of sets.slice(1)) {
    const c = all.filter((v) => set.includes(mantissa(v)))
    if (c.length <= target + 1) major = c
    else break
  }
  while (major.length > target + 1) major = major.filter((_, i) => i % 2 === 0)
  if (major.length < 2) major = all.slice(0, Math.max(2, target))
  const ms = new Set(major)
  return { major, minor: all.filter((v) => !ms.has(v)) }
}

export interface PlotProps {
  plot: PlotData
  xUnit: string
  yUnit: string
  colorBy: ColorBy
  lensColors: Partial<Record<CountryId, Uint8Array>> | null
  brush: BrushInfo | null
  targets: Targets
  onPick: (p: number) => void
  onBrush: (pts: number[] | null) => void
  renderTip: (p: number) => ReactNode
  tipText: (p: number) => string
  ariaLabel: string
}

export const ExplorerPlot = memo(function ExplorerPlot(props: PlotProps) {
  const { plot, xUnit, yUnit, colorBy, lensColors, brush, targets } = props
  const wrap = useRef<HTMLDivElement>(null)
  const cvs = useRef<HTMLCanvasElement>(null)
  const clipId = `exclip${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const [W, setW] = useState(0)
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(Math.floor(el.clientWidth)))
    ro.observe(el)
    setW(Math.floor(el.clientWidth))
    return () => ro.disconnect()
  }, [])
  const H = W ? Math.round(Math.min(320, Math.max(220, W * 0.68))) : 260
  const iw = Math.max(10, W - M.l - M.r), ih = Math.max(10, H - M.t - M.b)

  const sc = useMemo(() => {
    const mk = (dom: [number, number], log: boolean, range: [number, number]) =>
      ((log ? scaleLog() : scaleLinear()).domain(dom).range(range)) as unknown as Scale
    const x = mk(plot.domX, plot.logX, [M.l, M.l + iw])
    const y = mk(plot.domY, plot.logY, [M.t + ih, M.t])
    return { x, y, tx: axisTicks(x, plot.logX, plot.domX, iw), ty: axisTicks(y, plot.logY, plot.domY, ih * 1.3) }
  }, [plot, iw, ih])

  // screen positions + grid index for hover
  const screen = useMemo(() => {
    const n = plot.n
    const px = new Float32Array(n), py = new Float32Array(n)
    for (let i = 0; i < n; i++) { px[i] = sc.x(plot.x[i]); py[i] = sc.y(plot.y[i]) }
    const cols = Math.max(1, Math.ceil((W || 1) / CELL)), rows = Math.max(1, Math.ceil(H / CELL))
    const cell = new Int32Array(n)
    const start = new Int32Array(cols * rows + 1)
    for (let i = 0; i < n; i++) {
      const cx = Math.min(cols - 1, Math.max(0, Math.floor(px[i] / CELL)))
      const cy = Math.min(rows - 1, Math.max(0, Math.floor(py[i] / CELL)))
      cell[i] = cy * cols + cx
      start[cell[i] + 1]++
    }
    for (let c = 0; c < cols * rows; c++) start[c + 1] += start[c]
    const cur = start.slice(0, cols * rows)
    const items = new Int32Array(n)
    for (let i = 0; i < n; i++) items[cur[cell[i]]++] = i
    const nearest = (x: number, y: number, rad: number) => {
      let best = -1, bd = rad * rad
      const c0 = Math.max(0, Math.floor((x - rad) / CELL)), c1 = Math.min(cols - 1, Math.floor((x + rad) / CELL))
      const r0 = Math.max(0, Math.floor((y - rad) / CELL)), r1 = Math.min(rows - 1, Math.floor((y + rad) / CELL))
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const k = r * cols + c
        for (let j = start[k]; j < start[k + 1]; j++) {
          const i = items[j]
          const dx = px[i] - x, dy = py[i] - y, dd = dx * dx + dy * dy
          if (dd <= bd) { bd = dd; best = i } // later (on-top) points win ties
        }
      }
      return best
    }
    return { px, py, nearest }
  }, [plot, sc, W, H])

  // lens colour groups (one fillStyle per group keeps the canvas fast)
  const lensGroups = useMemo(() => {
    if (colorBy !== 'lens' || !lensColors) return null
    const out = new Map<CountryId, Map<string, number[]>>()
    for (const sg of plot.segs) {
      const c = lensColors[sg.country]
      if (!c) continue
      const g = new Map<string, number[]>()
      for (let i = sg.start; i < sg.end; i++) {
        const r = plot.row[i] * 4
        const k = `rgb(${c[r] & 0xf8},${c[r + 1] & 0xf8},${c[r + 2] & 0xf8})`
        let a = g.get(k)
        if (!a) { a = []; g.set(k, a) }
        a.push(i)
      }
      out.set(sg.country, g)
    }
    return out
  }, [plot, colorBy, lensColors])

  // ---- canvas: grid + points (redrawn on data / brush / selection change, not on hover)
  useEffect(() => {
    const c = cvs.current
    if (!c || !W) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr)
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)
    // grid
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(154,167,184,0.13)'
    ctx.beginPath()
    for (const v of sc.tx.major) { const x = Math.round(sc.x(v)) + 0.5; ctx.moveTo(x, M.t); ctx.lineTo(x, M.t + ih) }
    for (const v of sc.ty.major) { const y = Math.round(sc.y(v)) + 0.5; ctx.moveTo(M.l, y); ctx.lineTo(M.l + iw, y) }
    ctx.stroke()
    ctx.save()
    ctx.beginPath(); ctx.rect(M.l, M.t, iw, ih); ctx.clip()
    const { px, py } = screen
    const flags = brush?.flags ?? null
    const dot = (i: number, st: ReturnType<typeof markStyle>, pass: 'dim' | 'main') => {
      if (flags && (pass === 'dim') !== (flags[i] === 0)) return
      if (st.round) {
        ctx.beginPath(); ctx.arc(px[i], py[i], st.r, 0, Math.PI * 2); ctx.fill()
        if (st.stroke && pass === 'main') ctx.stroke()
      } else ctx.fillRect(px[i] - st.r / 2, py[i] - st.r / 2, st.r, st.r)
    }
    for (const pass of (flags ? ['dim', 'main'] : ['main']) as ('dim' | 'main')[]) {
      for (const sg of plot.segs) {
        const st = markStyle(sg.end - sg.start)
        ctx.globalAlpha = pass === 'dim' ? st.a * 0.2 : Math.min(1, st.a * (flags ? 1.25 : 1))
        ctx.strokeStyle = 'rgba(8,14,24,0.85)'
        ctx.lineWidth = 0.8
        const groups = lensGroups?.get(sg.country)
        if (groups) {
          for (const [fill, idx] of groups) { ctx.fillStyle = fill; for (let j = 0; j < idx.length; j++) dot(idx[j], st, pass) }
        } else {
          ctx.fillStyle = `rgb(${RGB[sg.country].join(',')})`
          for (let i = sg.start; i < sg.end; i++) dot(i, st, pass)
        }
      }
    }
    // secondary highlight (e.g. all tiles of a selected county, all counties of a state)
    if (targets.secondary?.length) {
      ctx.globalAlpha = 0.95
      ctx.strokeStyle = '#fff4d2'
      ctx.lineWidth = 1.3
      const r = targets.secondary.length > 400 ? 2.2 : 3.6
      for (const i of targets.secondary) { ctx.beginPath(); ctx.arc(px[i], py[i], r, 0, Math.PI * 2); ctx.stroke() }
    }
    ctx.restore()
  }, [plot, screen, sc, brush, targets, lensGroups, W, H, iw, ih])

  // ---- interaction
  const [hover, setHover] = useState(-1)
  const [kb, setKb] = useState(false)
  const [rect, setRect] = useState<[number, number, number, number] | null>(null)
  const drag = useRef<{ x0: number; y0: number; on: boolean } | null>(null)
  useEffect(() => { setHover(-1); setRect(null); drag.current = null }, [plot])

  const local = (e: React.PointerEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top] as const
  }
  const clampX = (x: number) => Math.min(M.l + iw, Math.max(M.l, x))
  const clampY = (y: number) => Math.min(M.t + ih, Math.max(M.t, y))

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    const [x, y] = local(e)
    drag.current = { x0: clampX(x), y0: clampY(y), on: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const [x, y] = local(e)
    const g = drag.current
    if (g) {
      if (!g.on && Math.hypot(x - g.x0, y - g.y0) > 5) { g.on = true; setHover(-1) }
      if (g.on) setRect([g.x0, g.y0, clampX(x), clampY(y)])
      return
    }
    if (e.pointerType === 'mouse') { setKb(false); setHover(screen.nearest(x, y, 12)) }
  }
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const g = drag.current
    drag.current = null
    if (!g) return
    const [x, y] = local(e)
    if (g.on) {
      setRect(null)
      const x0 = Math.min(g.x0, clampX(x)), x1 = Math.max(g.x0, clampX(x))
      const y0 = Math.min(g.y0, clampY(y)), y1 = Math.max(g.y0, clampY(y))
      const pts: number[] = []
      const { px, py } = screen
      for (let i = 0; i < plot.n; i++) if (px[i] >= x0 && px[i] <= x1 && py[i] >= y0 && py[i] <= y1) pts.push(i)
      props.onBrush(pts)
      return
    }
    const hit = screen.nearest(x, y, e.pointerType === 'mouse' ? 12 : 22)
    if (hit >= 0) { setHover(hit); props.onPick(hit) } else props.onBrush(null)
  }
  const onPointerCancel = () => { drag.current = null; setRect(null) }

  // keyboard: arrows step through points in X (←/→) or Y (↑/↓) order, Enter selects
  const order = useRef<{ plot: PlotData; byX: Int32Array; byY: Int32Array; rx: Int32Array; ry: Int32Array } | null>(null)
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!plot.n) return
    if (e.key === 'Enter' || e.key === ' ') {
      if (hover >= 0) { e.preventDefault(); props.onPick(hover) }
      return
    }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return
    e.preventDefault()
    let o = order.current
    if (!o || o.plot !== plot) {
      const ids = () => Int32Array.from({ length: plot.n }, (_, i) => i)
      const byX = ids().sort((a, b) => plot.x[a] - plot.x[b])
      const byY = ids().sort((a, b) => plot.y[a] - plot.y[b])
      const rx = new Int32Array(plot.n), ry = new Int32Array(plot.n)
      byX.forEach((p, r) => { rx[p] = r })
      byY.forEach((p, r) => { ry[p] = r })
      o = order.current = { plot, byX, byY, rx, ry }
    }
    const horiz = e.key !== 'ArrowUp' && e.key !== 'ArrowDown'
    const ord = horiz ? o.byX : o.byY
    const rank = horiz ? o.rx : o.ry
    const step = e.shiftKey ? 10 : 1
    let r = hover >= 0 ? rank[hover] : -1
    if (e.key === 'Home') r = 0
    else if (e.key === 'End') r = plot.n - 1
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') r = r < 0 ? 0 : Math.min(plot.n - 1, r + step)
    else r = r < 0 ? plot.n - 1 : Math.max(0, r - step)
    setKb(true)
    setHover(ord[r])
  }

  // ---- overlay geometry
  const fits = plot.segs.flatMap((sg) => {
    const f = sg.fit
    if (!f || f.n < 3 || !Number.isFinite(f.slope)) return []
    const X = (t: number) => (plot.logX ? Math.pow(10, t) : t)
    const Y = (t: number) => (plot.logY ? Math.pow(10, f.intercept + f.slope * t) : f.intercept + f.slope * t)
    const x1 = sc.x(X(f.t0)), x2 = sc.x(X(f.t1)), y1 = sc.y(Y(f.t0)), y2 = sc.y(Y(f.t1))
    if (![x1, x2, y1, y2].every(Number.isFinite)) return []
    return [{ c: sg.country, x1, x2, y1, y2 }]
  })
  const prim = targets.primary
  const { px, py } = screen
  const tipLeft = hover >= 0 && px[hover] > W * 0.55
  const tipUp = hover >= 0 && py[hover] > H * 0.5

  return (
    <div
      className="explorer-plot"
      ref={wrap}
      style={{ height: H }}
      tabIndex={0}
      role="group"
      aria-label={props.ariaLabel}
      aria-roledescription="scatter plot"
      onKeyDown={onKeyDown}
      onBlur={() => kb && setHover(-1)}
    >
      <canvas ref={cvs} style={{ width: W, height: H }} aria-hidden />
      <svg
        className="explorer-svg"
        width={W}
        height={H}
        aria-hidden
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onPointerLeave={() => { if (!drag.current && !kb) setHover(-1) }}
      >
        <defs><clipPath id={clipId}><rect x={M.l} y={M.t} width={iw} height={ih} /></clipPath></defs>
        <rect className="explorer-frame" x={M.l + 0.5} y={M.t + 0.5} width={iw - 1} height={ih - 1} />
        <g className="explorer-ticks">
          {sc.tx.minor.map((v) => <line key={`mx${v}`} x1={sc.x(v)} x2={sc.x(v)} y1={M.t + ih} y2={M.t + ih + 3} />)}
          {sc.ty.minor.map((v) => <line key={`my${v}`} x1={M.l - 3} x2={M.l} y1={sc.y(v)} y2={sc.y(v)} />)}
          {sc.tx.major.map((v) => (
            <g key={`x${v}`}>
              <line x1={sc.x(v)} x2={sc.x(v)} y1={M.t + ih} y2={M.t + ih + 5} />
              <text x={sc.x(v)} y={M.t + ih + 15} textAnchor="middle">{fmtShort(v, xUnit)}</text>
            </g>
          ))}
          {sc.ty.major.map((v) => (
            <g key={`y${v}`}>
              <line x1={M.l - 5} x2={M.l} y1={sc.y(v)} y2={sc.y(v)} />
              <text x={M.l - 7} y={sc.y(v) + 3.5} textAnchor="end">{fmtShort(v, yUnit)}</text>
            </g>
          ))}
        </g>
        <g clipPath={`url(#${clipId})`}>
          {fits.map((f) => (
            <g key={f.c}>
              <line x1={f.x1} y1={f.y1} x2={f.x2} y2={f.y2} stroke="#060b14" strokeWidth={3.4} strokeOpacity={0.7} />
              <line x1={f.x1} y1={f.y1} x2={f.x2} y2={f.y2} stroke={`rgb(${RGB[f.c].join(',')})`} strokeWidth={1.7} strokeDasharray="6 4" />
            </g>
          ))}
          {prim >= 0 && prim < plot.n && (
            <g className="explorer-selring">
              <circle cx={px[prim]} cy={py[prim]} r={10} />
              <circle cx={px[prim]} cy={py[prim]} r={5} className="inner" />
            </g>
          )}
          {hover >= 0 && hover < plot.n && <circle className="explorer-hoverring" cx={px[hover]} cy={py[hover]} r={7} />}
        </g>
        {rect && (
          <rect className="explorer-brush" x={Math.min(rect[0], rect[2])} y={Math.min(rect[1], rect[3])}
            width={Math.abs(rect[2] - rect[0])} height={Math.abs(rect[3] - rect[1])} />
        )}
      </svg>
      {!plot.n && <div className="explorer-empty">No points to plot for this combination.</div>}
      {hover >= 0 && hover < plot.n && (
        <div
          className="explorer-tip"
          style={{
            left: tipLeft ? px[hover] - 12 : px[hover] + 12,
            top: tipUp ? py[hover] - 10 : py[hover] + 10,
            transform: `translate(${tipLeft ? '-100%' : '0'}, ${tipUp ? '-100%' : '0'})`,
          }}
        >
          {props.renderTip(hover)}
        </div>
      )}
      <div className="explorer-sr" aria-live="polite">{kb && hover >= 0 && hover < plot.n ? props.tipText(hover) : ''}</div>
    </div>
  )
})
