import type { YieldKey } from './types'

export type RGB = [number, number, number]

/**
 * `glyph` is the one text stand-in for the YieldIcon SVG (coin, gear, wheat, flask, star, flame),
 * for places that can only show text (lens buttons, <option>/<optgroup> labels, HTML tooltips).
 */
export const YIELD_META: Record<YieldKey, { label: string; color: string; rgb: RGB; blurb: string; glyph: string }> = {
  gold: { label: 'Gold', color: '#f0c43a', rgb: [240, 196, 58], glyph: '🪙', blurb: 'Economic output — GDP per km² of tile' },
  production: { label: 'Production', color: '#e0803a', rgb: [224, 128, 58], glyph: '⚙️', blurb: 'Industry (manufacturing, mining, construction, utilities) per km² of tile + power stations' },
  food: { label: 'Food', color: '#7cc24e', rgb: [124, 194, 78], glyph: '🌾', blurb: 'Agriculture, forestry & fishing value added per km² of tile; fish landings at sea' },
  science: { label: 'Science', color: '#53b3f0', rgb: [83, 179, 240], glyph: '🧪', blurb: 'STEM workers per km² of tile + research output of local institutions' },
  culture: { label: 'Culture', color: '#c07ff0', rgb: [192, 127, 240], glyph: '⭐', blurb: 'Arts, recreation & hospitality jobs per km² of tile; World Heritage, museums, national parks' },
  faith: { label: 'Faith', color: '#f2ead0', rgb: [242, 234, 208], glyph: '🕯️', blurb: 'Places of worship per km² of tile + the most notable religious sites' },
}

export const TERRAIN_META: Record<string, { label: string; rgb: RGB }> = {
  ocean: { label: 'Ocean', rgb: [22, 48, 78] },
  coast: { label: 'Coast', rgb: [38, 92, 122] },
  lake: { label: 'Lake', rgb: [52, 120, 150] },
  grassland: { label: 'Grassland', rgb: [104, 150, 62] },
  plains: { label: 'Plains', rgb: [170, 158, 86] },
  desert: { label: 'Desert', rgb: [214, 190, 132] },
  tundra: { label: 'Tundra', rgb: [128, 140, 126] },
  snow: { label: 'Snow', rgb: [226, 234, 240] },
  mountains: { label: 'Mountains', rgb: [122, 110, 100] },
}

export const FEATURE_META: Record<string, { label: string; rgb: RGB | null; icon: string }> = {
  '': { label: 'None', rgb: null, icon: '' },
  forest: { label: 'Forest', rgb: [52, 98, 50], icon: '🌲' },
  rainforest: { label: 'Rainforest', rgb: [30, 92, 62], icon: '🌴' },
  marsh: { label: 'Marsh', rgb: [84, 120, 98], icon: '🪷' },
  mangrove: { label: 'Mangrove', rgb: [56, 104, 84], icon: '🌿' },
  urban: { label: 'Urban', rgb: [146, 136, 128], icon: '🏙️' },
}

export const DISTRICT_META: Record<string, { label: string; icon: string; rgb: RGB }> = {
  '': { label: 'None', icon: '', rgb: [0, 0, 0] },
  city_center: { label: 'City Center', icon: '🏛️', rgb: [240, 214, 120] },
  harbor: { label: 'Harbor', icon: '⚓', rgb: [70, 160, 220] },
  aerodrome: { label: 'Aerodrome', icon: '✈️', rgb: [150, 200, 240] },
  spaceport: { label: 'Spaceport', icon: '🚀', rgb: [230, 230, 255] },
  campus: { label: 'Campus', icon: '🎓', rgb: [83, 179, 240] },
  commercial_hub: { label: 'Commercial Hub', icon: '💰', rgb: [240, 196, 58] },
  industrial_zone: { label: 'Industrial Zone', icon: '🏭', rgb: [224, 128, 58] },
  dam: { label: 'Dam', icon: '💧', rgb: [60, 140, 200] },
  power_plant: { label: 'Power Plant', icon: '⚡', rgb: [250, 220, 70] },
  encampment: { label: 'Encampment', icon: '🛡️', rgb: [200, 70, 70] },
  holy_site: { label: 'Holy Site', icon: '🪔', rgb: [242, 234, 208] },
  theater_square: { label: 'Theater Square', icon: '🎭', rgb: [192, 127, 240] },
  entertainment: { label: 'Entertainment Complex', icon: '🎡', rgb: [240, 120, 180] },
  preserve: { label: 'Preserve', icon: '🦌', rgb: [38, 104, 78] },
  plantation: { label: 'Plantation', icon: '🍃', rgb: [96, 214, 140] },
  farmland: { label: 'Farmland', icon: '🌾', rgb: [200, 200, 90] },
  fishery: { label: 'Fishery', icon: '🐟', rgb: [90, 200, 220] },
  neighborhood: { label: 'Neighborhood', icon: '🏘️', rgb: [200, 170, 150] },
}

export const CITIZEN_META: Record<string, { label: string; icon: string; desc: string }> = {
  cit_farmers: { label: 'Farmers', icon: '🧑‍🌾', desc: 'Agriculture, forestry & fishing' },
  cit_workers: { label: 'Workers', icon: '👷', desc: 'Trades, construction, production, transport' },
  cit_merchants: { label: 'Merchants', icon: '🧑‍💼', desc: 'Sales & office / clerical' },
  cit_engineers: { label: 'Engineers', icon: '🧑‍🔬', desc: 'Computer, engineering & science (STEM)' },
  cit_scholars: { label: 'Scholars', icon: '🧑‍🏫', desc: 'Education, legal, community service' },
  cit_healers: { label: 'Healers', icon: '🧑‍⚕️', desc: 'Healthcare practitioners & support' },
  cit_service: { label: 'Service', icon: '🧑‍🍳', desc: 'Food, personal care, protective, cleaning' },
  cit_administrators: { label: 'Administrators', icon: '🧑‍⚖️', desc: 'Managers, business & finance' },
  cit_artists: { label: 'Artists', icon: '🧑‍🎨', desc: 'Arts, design, media, sports' },
  cit_soldiers: { label: 'Soldiers', icon: '💂', desc: 'Armed forces' },
}

/** Kinds of historic moments on the timeline (History lens). */
export const EVENT_KIND_META: Record<string, { label: string; icon: string; rgb: RGB }> = {
  settlement: { label: 'Cities founded', icon: '🏘️', rgb: [240, 214, 120] },
  landmark: { label: 'Monuments', icon: '🗿', rgb: [200, 170, 130] },
  heritage: { label: 'World Heritage', icon: '🏯', rgb: [192, 127, 240] },
  holy_site: { label: 'Holy sites', icon: '🛐', rgb: [242, 234, 208] },
  university: { label: 'Universities', icon: '🎓', rgb: [83, 179, 240] },
  museum: { label: 'Museums', icon: '🖼️', rgb: [230, 140, 190] },
  park: { label: 'Parks', icon: '🏞️', rgb: [96, 200, 120] },
  power: { label: 'Power stations', icon: '⚡', rgb: [250, 210, 60] },
  airport: { label: 'Airports', icon: '✈️', rgb: [150, 200, 240] },
  spaceport: { label: 'Spaceports', icon: '🚀', rgb: [230, 230, 255] },
}

export const COUNTRY_META = {
  lka: { name: 'Sri Lanka', short: 'LKA', flagColor: '#f2a900', accent: '#f2a900' },
  usa: { name: 'United States', short: 'USA', flagColor: '#5b8def', accent: '#5b8def' },
} as const

export function terrainLabel(t: string, f: string, hills: boolean) {
  const base = TERRAIN_META[t]?.label ?? t
  const parts = [hills && t !== 'mountains' ? `${base} (Hills)` : base]
  if (f) parts.push(FEATURE_META[f]?.label ?? f)
  return parts.join(' · ')
}
