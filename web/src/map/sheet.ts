// On phones the details panel is a bottom sheet drawn over the map (styles.css, max-width 900px).

/**
 * Height in px, from the top of `pane`, of the map area that stays visible above the bottom sheet
 * once it is open, or null when no sheet covers the pane (desktop layouts).
 * The sheet slides in with a CSS transform, so its layout box (offsetTop, which ignores transforms)
 * gives the open position even while it is still closed or animating.
 */
export function visibleHeightAboveSheet(pane: HTMLElement): number | null {
  if (typeof window === 'undefined' || window.innerWidth >= 900) return null
  const side = document.querySelector<HTMLElement>('.side')
  if (!side) return null
  const fixed = getComputedStyle(side).position === 'fixed'
  if (!fixed) return null
  const m = pane.getBoundingClientRect()
  const top = side.offsetTop
  if (!(top > m.top + 40 && top < m.bottom)) return null
  return top - m.top
}
