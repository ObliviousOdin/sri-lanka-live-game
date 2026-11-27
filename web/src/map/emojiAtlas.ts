// Builds a colour icon atlas (canvas) from emoji so deck.gl's IconLayer can draw district and
// wonder markers in full colour.  Each icon gets a round dark badge behind it.

export interface AtlasEntry { x: number; y: number; width: number; height: number; mask: boolean; anchorY: number }

const SIZE = 72
let atlas: { canvas: HTMLCanvasElement; mapping: Record<string, AtlasEntry> } | null = null
const ALL = new Set<string>()

export function emojiAtlas(icons: string[]) {
  const missing = icons.filter((i) => i && !ALL.has(i))
  if (atlas && !missing.length) return atlas
  missing.forEach((i) => ALL.add(i))
  const list = [...ALL]
  const cols = 8
  const rows = Math.ceil(list.length / cols)
  const canvas = document.createElement('canvas')
  canvas.width = cols * SIZE
  canvas.height = Math.max(1, rows) * SIZE
  const ctx = canvas.getContext('2d')!
  const mapping: Record<string, AtlasEntry> = {}
  list.forEach((icon, k) => {
    const x = (k % cols) * SIZE, y = Math.floor(k / cols) * SIZE
    ctx.save()
    ctx.beginPath()
    ctx.arc(x + SIZE / 2, y + SIZE / 2, SIZE / 2 - 3, 0, Math.PI * 2)
    ctx.fillStyle = 'rgba(12,20,32,0.82)'
    ctx.fill()
    ctx.lineWidth = 3
    ctx.strokeStyle = 'rgba(217,164,65,0.95)'
    ctx.stroke()
    ctx.font = `${Math.round(SIZE * 0.52)}px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(icon, x + SIZE / 2, y + SIZE / 2 + 2)
    ctx.restore()
    mapping[icon] = { x, y, width: SIZE, height: SIZE, mask: false, anchorY: SIZE / 2 }
  })
  atlas = { canvas, mapping }
  return atlas
}
