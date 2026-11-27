/** True when the user asked the OS / browser for reduced motion: map moves then jump instead of fly. */
export function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** True on touch-first devices (used for gesture hints). */
export function coarsePointer(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches
  } catch {
    return false
  }
}
