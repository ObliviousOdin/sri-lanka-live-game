import { YIELD_META } from '../civ'
import type { YieldKey } from '../types'

// Original, minimal glyphs for the six yields.
const PATHS: Record<YieldKey, JSX.Element> = {
  gold: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5.2" fill="none" stroke="rgba(0,0,0,.35)" strokeWidth="1.6" />
    </>
  ),
  production: (
    <path d="M12 2.5l1.6 2.7 3.1-.6.6 3.1 2.7 1.6-1.4 2.7 1.4 2.7-2.7 1.6-.6 3.1-3.1-.6L12 21.5l-1.6-2.7-3.1.6-.6-3.1-2.7-1.6L5.4 12 4 9.3l2.7-1.6.6-3.1 3.1.6zM12 8.6a3.4 3.4 0 100 6.8 3.4 3.4 0 000-6.8z" fillRule="evenodd" />
  ),
  food: (
    <path d="M12 22V9M12 9c-3.2-.3-4.6-2.5-4.8-6 3.1.4 4.6 2.4 4.8 6zm0 0c3.2-.3 4.6-2.5 4.8-6-3.1.4-4.6 2.4-4.8 6zm0 5c-3.2-.3-4.6-2.5-4.8-6 3.1.4 4.6 2.4 4.8 6zm0 0c3.2-.3 4.6-2.5 4.8-6-3.1.4-4.6 2.4-4.8 6z" strokeWidth="1.6" stroke="currentColor" />
  ),
  science: (
    <path d="M9 2.5h6v1.8h-1v5.2l5.6 9.4A1.8 1.8 0 0118 21.5H6a1.8 1.8 0 01-1.6-2.6L10 9.5V4.3H9zM7.6 16h8.8l-2.6-4.4h-3.6z" fillRule="evenodd" />
  ),
  culture: (
    <path d="M12 2l2.6 6.2 6.7.6-5.1 4.4 1.6 6.6L12 16.3l-5.8 3.5 1.6-6.6-5.1-4.4 6.7-.6z" />
  ),
  faith: (
    <path d="M12 2.2c2.6 3.3 4.6 5.9 4.6 9.1a4.6 4.6 0 01-9.2 0c0-1.8.8-3.3 1.9-4.6.2 1.6.9 2.6 2 3 .3-2.6-.2-4.9.7-7.5zM6 19h12v2.6H6z" />
  ),
}

export function YieldIcon({ y, size = 16, title }: { y: YieldKey; size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={YIELD_META[y].color} style={{ color: YIELD_META[y].color, flex: 'none' }} role="img" aria-label={title ?? YIELD_META[y].label}>
      <title>{title ?? YIELD_META[y].label}</title>
      {PATHS[y]}
    </svg>
  )
}

export function YieldRow({ values, size = 14 }: { values: Record<YieldKey, number> | number[]; size?: number }) {
  const keys: YieldKey[] = ['gold', 'production', 'food', 'science', 'culture', 'faith']
  return (
    <span className="yield-row">
      {keys.map((k, j) => {
        const v = Array.isArray(values) ? values[j] : values[k]
        return (
          <span key={k} className={`yield ${v ? '' : 'zero'}`} title={`${YIELD_META[k].label}: ${v}`}>
            <YieldIcon y={k} size={size} />
            <b>{v}</b>
          </span>
        )
      })}
    </span>
  )
}
