// "True size" linking between the two map panes.
//
// While linking is on, exactly one pane leads: the one the user (or a fly-to) moved most recently,
// or Sri Lanka when neither has been moved yet. The leader publishes its metres per pixel; the
// follower adopts it at its own latitude. A follower never publishes, so the two panes cannot
// feed back into each other, and nothing is written while linking is off (no cross-pane renders).
import { create } from 'zustand'
import type { CountryId } from '../types'

const EARTH_MPP_Z0 = 78271.517 // metres per pixel at zoom 0 on the equator (512 px tiles)

/** Metres per screen pixel at the centre of a Web-Mercator view. */
export function metersPerPixel(zoom: number, lat: number) {
  return (EARTH_MPP_Z0 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom)
}

/** Zoom that gives `mpp` metres per pixel at latitude `lat`. */
export function zoomForMpp(mpp: number, lat: number) {
  return Math.log2((EARTH_MPP_Z0 * Math.cos((lat * Math.PI) / 180)) / mpp)
}

// Order in which the panes were last moved deliberately (drag, wheel, keys, fly-to). Not reactive.
const movedAt: Partial<Record<CountryId, number>> = {}
let moveTick = 0
export function markMoved(country: CountryId) {
  movedAt[country] = ++moveTick
}

/** The pane the other one follows while "True size" is on. */
export function scaleLeader(): CountryId {
  return (movedAt.usa ?? 0) > (movedAt.lka ?? 0) ? 'usa' : 'lka'
}

export interface ScaleLink { from: CountryId; mpp: number; seq: number }
export const useScaleLink = create<{ link: ScaleLink | null }>(() => ({ link: null }))

let seq = 0
export function publishScale(from: CountryId, mpp: number) {
  const cur = useScaleLink.getState().link
  if (cur && cur.from === from && Math.abs(cur.mpp - mpp) <= mpp * 1e-4) return
  useScaleLink.setState({ link: { from, mpp, seq: ++seq } })
}
