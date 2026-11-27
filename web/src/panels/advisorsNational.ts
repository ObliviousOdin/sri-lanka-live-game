// Advisors panel — national view: World Bank indicators (national.json) compared country to
// country, plus summaries computed from the map's own metrics. Every comment is built only from
// loaded values; a comment whose inputs are missing is skipped.
import type { CountryData, CountryId, IndexJson, NationalIndicator, YieldKey } from '../types'
import {
  ADJ, type AdvisorId, type Comment, count, fig, fmtMetric, ISO, money, natAgg, nm, ok, pct, poss, type Seg,
  SHORT, times, tonnes,
} from './advisorsCore'
import { landYieldMeans } from './tilePanelStats'

/** usd = US$; intl = international $ (PPP); lcu = local currency per US$ / Int$ (exchange rates, PPP factors) */
type Fmt = 'usd' | 'intl' | 'pct' | 'num' | 'years' | 'lcu'

export interface NatSpec {
  key: string
  codes: string[]
  re?: RegExp
  advisor: AdvisorId | 'population'
  short: string
  fmt: Fmt
  unit?: string
  /** predicate with {v}: "spends {v} of GDP on R&D" */
  say?: string
  per?: { n: number; label: string; fmt: Fmt }
  better: 'higher' | 'lower' | 'neutral'
  /** per-capita / share indicator: eligible for the verdict */
  intensive: boolean
  icon: string
  /** sparkline priority within its advisor (lower first) */
  spark?: number
  /** headline indicator: eligible for the national verdict */
  headline?: boolean
  /** caveat appended to the comment's provenance line */
  note?: string
}

export const NAT_SPECS: NatSpec[] = [
  // ---- economic
  { key: 'gdp_pc', codes: ['NY.GDP.PCAP.CD'], re: /^GDP per capita \(current/i, advisor: 'economic', short: 'GDP per capita', fmt: 'usd', say: 'earns {v} per citizen (GDP per capita)', better: 'higher', intensive: true, icon: 'y:gold', spark: 1, headline: true },
  { key: 'gdp_pc_ppp', codes: ['NY.GDP.PCAP.PP.CD'], re: /^GDP per capita, PPP/i, advisor: 'economic', short: 'GDP per capita, PPP', fmt: 'intl', say: 'earns {v} per citizen at purchasing-power parity', better: 'higher', intensive: true, icon: 'y:gold' },
  { key: 'gdp', codes: ['NY.GDP.MKTP.CD'], re: /^GDP \(current US/i, advisor: 'economic', short: 'GDP', fmt: 'usd', say: 'commands an economy of {v} (GDP)', better: 'higher', intensive: false, icon: '🏦', spark: 2 },
  { key: 'gni_pc', codes: ['NY.GNP.PCAP.CD'], re: /^GNI per capita, Atlas/i, advisor: 'economic', short: 'GNI per capita (Atlas)', fmt: 'usd', say: 'has a gross national income of {v} per citizen (Atlas method)', better: 'higher', intensive: true, icon: '💵' },
  { key: 'gni_pc_ppp', codes: ['NY.GNP.PCAP.PP.CD'], re: /^GNI per capita, PPP/i, advisor: 'economic', short: 'GNI per capita, PPP', fmt: 'intl', say: 'has a gross national income of {v} per citizen at PPP', better: 'higher', intensive: true, icon: '💵' },
  { key: 'agri', codes: ['NV.AGR.TOTL.ZS'], re: /^Agriculture.*% of GDP/i, advisor: 'economic', short: 'Agriculture, % of GDP', fmt: 'pct', say: 'draws {v} of GDP from farms, forests and fisheries', better: 'neutral', intensive: true, icon: 'y:food' },
  { key: 'industry', codes: ['NV.IND.TOTL.ZS'], re: /^Industry.*% of GDP/i, advisor: 'economic', short: 'Industry, % of GDP', fmt: 'pct', say: 'draws {v} of GDP from industry and construction', better: 'neutral', intensive: true, icon: 'y:production' },
  { key: 'manuf', codes: ['NV.IND.MANF.ZS'], re: /^Manufacturing.*% of GDP/i, advisor: 'economic', short: 'Manufacturing, % of GDP', fmt: 'pct', say: 'draws {v} of GDP from manufacturing', better: 'neutral', intensive: true, icon: 'y:production' },
  { key: 'services', codes: ['NV.SRV.TOTL.ZS'], re: /^Services.*% of GDP/i, advisor: 'economic', short: 'Services, % of GDP', fmt: 'pct', say: 'draws {v} of GDP from services', better: 'neutral', intensive: true, icon: '🧑‍💼' },
  { key: 'exports', codes: ['NE.EXP.GNFS.ZS'], re: /^Exports of goods and services \(% of GDP/i, advisor: 'economic', short: 'Exports, % of GDP', fmt: 'pct', say: 'sells {v} of its GDP abroad as exports', better: 'neutral', intensive: true, icon: '🚢' },
  { key: 'unemp', codes: ['SL.UEM.TOTL.ZS', 'SL.UEM.TOTL.NE.ZS'], re: /^Unemployment, total/i, advisor: 'economic', short: 'Unemployment', fmt: 'pct', say: 'has {v} of its labour force out of work', better: 'lower', intensive: true, icon: '🪑' },
  { key: 'gini', codes: ['SI.POV.GINI'], re: /^Gini/i, advisor: 'economic', short: 'Gini index', fmt: 'num', say: 'scores {v} on the Gini inequality index', better: 'lower', intensive: true, icon: '⚖️' },
  { key: 'pop', codes: ['SP.POP.TOTL'], re: /^Population, total/i, advisor: 'population', short: 'Population', fmt: 'num', say: 'counts {v} citizens', better: 'neutral', intensive: false, icon: '👥' },
  { key: 'gdp_growth', codes: ['NY.GDP.MKTP.KD.ZG'], advisor: 'economic', short: 'GDP growth', fmt: 'pct', say: 'grew its real GDP by {v} in the latest year', better: 'higher', intensive: true, icon: '📈' },
  { key: 'inflation', codes: ['FP.CPI.TOTL.ZG'], advisor: 'economic', short: 'Consumer-price inflation', fmt: 'pct', say: 'saw consumer prices change by {v}', better: 'neutral', intensive: true, icon: '🏷️' },
  { key: 'emp_agri', codes: ['SL.AGR.EMPL.ZS'], advisor: 'economic', short: 'Employment in agriculture', fmt: 'pct', say: 'keeps {v} of its workers on farms, in forests and fisheries', better: 'neutral', intensive: true, icon: '🧑‍🌾', headline: true },
  { key: 'remit', codes: ['BX.TRF.PWKR.DT.GD.ZS'], advisor: 'economic', short: 'Remittances received, % of GDP', fmt: 'pct', say: 'receives {v} of GDP as remittances from citizens abroad', better: 'neutral', intensive: true, icon: '✉️' },
  { key: 'lfp_f', codes: ['SL.TLF.CACT.FE.ZS'], advisor: 'economic', short: 'Female labour-force participation', fmt: 'pct', say: 'has {v} of women aged 15+ in the labour force', better: 'higher', intensive: true, icon: '👩‍💼' },
  { key: 'debt', codes: ['GC.DOD.TOTL.GD.ZS'], advisor: 'economic', short: 'Central government debt, % of GDP', fmt: 'pct', say: 'owes central-government debt worth {v} of GDP', better: 'lower', intensive: true, icon: '📜' },
  // ---- military
  { key: 'mil_gdp', codes: ['MS.MIL.XPND.GD.ZS'], re: /^Military expenditure \(% of GDP/i, advisor: 'military', short: 'Military spending, % of GDP', fmt: 'pct', say: 'spends {v} of GDP on its armed forces', better: 'neutral', intensive: true, icon: '🛡️', spark: 1, headline: true },
  { key: 'mil_usd', codes: ['MS.MIL.XPND.CD'], re: /^Military expenditure \(current/i, advisor: 'military', short: 'Military spending', fmt: 'usd', say: 'spends {v} a year on its armed forces', per: { n: 1, label: 'citizen', fmt: 'usd' }, better: 'neutral', intensive: false, icon: '⚔️', spark: 2 },
  { key: 'mil_gov', codes: ['MS.MIL.XPND.ZS'], re: /^Military expenditure \(% of general government/i, advisor: 'military', short: 'Military, % of government spending', fmt: 'pct', say: 'devotes {v} of government spending to the military', better: 'neutral', intensive: true, icon: '🛡️' },
  { key: 'mil_pers', codes: ['MS.MIL.TOTL.P1'], re: /^Armed forces personnel, total/i, advisor: 'military', short: 'Armed forces personnel', fmt: 'num', say: 'fields {v} armed forces personnel', per: { n: 1000, label: '1,000 people', fmt: 'num' }, better: 'neutral', intensive: false, icon: '💂' },
  { key: 'mil_lab', codes: ['MS.MIL.TOTL.TF.ZS'], re: /^Armed forces personnel \(% of total labor/i, advisor: 'military', short: 'Armed forces, % of labour force', fmt: 'pct', say: 'keeps {v} of its labour force under arms', better: 'neutral', intensive: true, icon: '💂', headline: true },
  { key: 'arms_imp', codes: ['MS.MIL.MPRT.KD'], advisor: 'military', short: 'Arms imports (SIPRI TIV)', fmt: 'num', say: 'imported arms worth {v} SIPRI trend-indicator units', better: 'neutral', intensive: false, icon: '📦' },
  { key: 'homicide', codes: ['VC.IHR.PSRC.P5'], advisor: 'military', short: 'Intentional homicides per 100,000', fmt: 'num', say: 'records {v} intentional homicides per 100,000 people', better: 'lower', intensive: true, icon: '🚨' },
  // ---- cultural
  { key: 'tour_arr', codes: ['ST.INT.ARVL'], re: /^International tourism, number of arrivals/i, advisor: 'cultural', short: 'International tourist arrivals', fmt: 'num', say: 'welcomes {v} international tourists a year', per: { n: 100, label: '100 residents', fmt: 'num' }, better: 'higher', intensive: false, icon: '🧳', spark: 1 },
  { key: 'tour_rcpt', codes: ['ST.INT.RCPT.CD'], re: /^International tourism, receipts \(current/i, advisor: 'cultural', short: 'International tourism receipts', fmt: 'usd', say: 'earns {v} a year from international tourism', per: { n: 1, label: 'citizen', fmt: 'usd' }, better: 'higher', intensive: false, icon: '🎟️' },
  { key: 'urban', codes: ['SP.URB.TOTL.IN.ZS'], re: /^Urban population \(%/i, advisor: 'cultural', short: 'Urban population', fmt: 'pct', say: 'houses {v} of its people in cities', better: 'neutral', intensive: true, icon: '🏙️', headline: true },
  { key: 'internet', codes: ['IT.NET.USER.ZS'], re: /using the Internet/i, advisor: 'cultural', short: 'Internet users', fmt: 'pct', say: 'has {v} of its people online', better: 'higher', intensive: true, icon: '📡', spark: 2, headline: true },
  { key: 'life', codes: ['SP.DYN.LE00.IN'], re: /^Life expectancy at birth, total/i, advisor: 'cultural', short: 'Life expectancy', fmt: 'years', say: 'has a life expectancy at birth of {v}', better: 'higher', intensive: true, icon: '🧑‍⚕️', headline: true },
  { key: 'tour_share', codes: ['ST.INT.RCPT.XP.ZS'], advisor: 'cultural', short: 'Tourism receipts, % of exports', fmt: 'pct', say: 'earns {v} of its export income from international tourism', better: 'neutral', intensive: true, icon: '🏖️' },
  { key: 'health_pc', codes: ['SH.XPD.CHEX.PC.CD'], advisor: 'cultural', short: 'Health spending per capita', fmt: 'usd', say: 'spends {v} per citizen on health care', better: 'higher', intensive: true, icon: '🧑‍⚕️' },
  { key: 'u5mort', codes: ['SH.DYN.MORT'], advisor: 'cultural', short: 'Under-5 mortality per 1,000 births', fmt: 'num', say: 'loses {v} of every 1,000 children before age five', better: 'lower', intensive: true, icon: '👶' },
  // ---- scientific
  { key: 'rd', codes: ['GB.XPD.RSDV.GD.ZS'], re: /^Research and development expenditure/i, advisor: 'scientific', short: 'R&D spending, % of GDP', fmt: 'pct', say: 'spends {v} of GDP on R&D', better: 'higher', intensive: true, icon: 'y:science', spark: 1, headline: true },
  { key: 'researchers', codes: ['SP.POP.SCIE.RD.P6'], re: /^Researchers in R&D/i, advisor: 'scientific', short: 'Researchers per million people', fmt: 'num', say: 'employs {v} researchers per million people', better: 'higher', intensive: true, icon: '🧑‍🔬', headline: true },
  { key: 'articles', codes: ['IP.JRN.ARTC.SC'], re: /journal articles/i, advisor: 'scientific', short: 'Scientific & technical journal articles', fmt: 'num', say: 'publishes {v} scientific and technical articles a year', per: { n: 1e6, label: 'million people', fmt: 'num' }, better: 'higher', intensive: false, icon: '📄', spark: 2 },
  { key: 'patents', codes: ['IP.PAT.RESD'], re: /^Patent applications, residents/i, advisor: 'scientific', short: 'Resident patent applications', fmt: 'num', say: 'files {v} resident patent applications a year', per: { n: 1e6, label: 'million people', fmt: 'num' }, better: 'higher', intensive: false, icon: '💡' },
  { key: 'hitech', codes: ['TX.VAL.TECH.MF.ZS'], re: /^High-technology exports \(%/i, advisor: 'scientific', short: 'High-tech exports, % of manufactured exports', fmt: 'pct', say: 'ships {v} of its manufactured exports as high-tech goods', better: 'higher', intensive: true, icon: '🖥️', headline: true },
  { key: 'tertiary', codes: ['SE.TER.ENRR'], re: /^School enrollment, tertiary/i, advisor: 'scientific', short: 'Tertiary enrolment (gross)', fmt: 'pct', say: 'enrols {v} of the university-age population in tertiary education (gross ratio)', better: 'higher', intensive: true, icon: '🎓', headline: true },
  { key: 'ba_attain', codes: ['SE.TER.CUAT.BA.ZS'], advisor: 'scientific', short: "Adults 25+ with a bachelor's degree", fmt: 'pct', say: "counts {v} of adults (25+) with at least a bachelor's degree", better: 'higher', intensive: true, icon: '🎓', headline: true },
  { key: 'edu_spend', codes: ['SE.XPD.TOTL.GD.ZS'], advisor: 'scientific', short: 'Public education spending, % of GDP', fmt: 'pct', say: 'spends {v} of GDP on public education', better: 'higher', intensive: true, icon: '🏫' },
  { key: 'mht_manuf', codes: ['NV.MNF.TECH.ZS.UN'], advisor: 'scientific', short: 'Medium & high-tech share of manufacturing', fmt: 'pct', say: 'makes {v} of its manufacturing value in medium- and high-tech industries', better: 'higher', intensive: true, icon: '⚙️' },
  // ---- environmental
  { key: 'forest', codes: ['AG.LND.FRST.ZS'], re: /^Forest area \(%/i, advisor: 'environmental', short: 'Forest area', fmt: 'pct', say: 'keeps {v} of its land under forest', better: 'higher', intensive: true, icon: '🌲', spark: 1, headline: true },
  // terrestrial only (% of land area) is the like-for-like series; the marine-inclusive one divides by land + territorial waters
  { key: 'protected', codes: ['ER.LND.PTLD.ZS'], re: /^Terrestrial protected areas/i, advisor: 'environmental', short: 'Protected land', fmt: 'pct', say: 'protects {v} of its land area', better: 'higher', intensive: true, icon: '🦌', headline: true },
  { key: 'protected_all', codes: ['ER.PTD.TOTL.ZS'], re: /^Terrestrial and marine protected/i, advisor: 'environmental', short: 'Protected areas, land + sea', fmt: 'pct', say: 'protects {v} of its land and territorial waters combined', better: 'higher', intensive: true, icon: '🌊', note: 'Share of land plus territorial waters, so a small island with wide waters scores low; see protected land above' },
  { key: 'co2', codes: ['EN.GHG.CO2.PC.CE.AR5', 'EN.ATM.CO2E.PC'], re: /CO2\) emissions.*per capita/i, advisor: 'environmental', short: 'CO₂ emissions per capita', fmt: 'num', unit: 't', say: 'emits {v} of CO₂ per citizen', better: 'lower', intensive: true, icon: '🏭', spark: 2, headline: true },
  { key: 'renew_elec', codes: ['EG.ELC.RNEW.ZS'], re: /^Renewable electricity output/i, advisor: 'environmental', short: 'Renewable electricity', fmt: 'pct', say: 'generates {v} of its electricity from renewables', better: 'higher', intensive: true, icon: '⚡', spark: 3, headline: true },
  { key: 'renew_energy', codes: ['EG.FEC.RNEW.ZS'], re: /^Renewable energy consumption/i, advisor: 'environmental', short: 'Renewable energy', fmt: 'pct', say: 'meets {v} of final energy use with renewables', better: 'higher', intensive: true, icon: '⚡' },
  { key: 'pm25', codes: ['EN.ATM.PM25.MC.M3'], advisor: 'environmental', short: 'PM2.5 air pollution', fmt: 'num', unit: 'µg/m³', say: 'breathes air with {v} of fine particulates (PM2.5, annual mean)', better: 'lower', intensive: true, icon: '🌫️', headline: true },
  { key: 'water_stress', codes: ['ER.H2O.FWST.ZS'], advisor: 'environmental', short: 'Water stress', fmt: 'pct', say: 'withdraws {v} of its available freshwater (water stress)', better: 'lower', intensive: true, icon: '💧' },
  { key: 'energy_pc', codes: ['EG.USE.PCAP.KG.OE'], advisor: 'environmental', short: 'Energy use per capita', fmt: 'num', unit: 'kgoe', say: 'uses {v} of oil-equivalent energy per citizen', better: 'neutral', intensive: true, icon: '🛢️' },
  { key: 'hydro', codes: ['EG.ELC.HYRO.ZS'], advisor: 'environmental', short: 'Hydroelectric share of power', fmt: 'pct', say: 'generates {v} of its electricity from hydropower', better: 'neutral', intensive: true, icon: '🌊' },
  { key: 'water', codes: ['ER.H2O.FWTL.ZS'], re: /^Annual freshwater withdrawals, total \(%/i, advisor: 'environmental', short: 'Freshwater withdrawals', fmt: 'pct', say: 'withdraws {v} of its internal freshwater resources each year', better: 'lower', intensive: true, icon: '💧' },
]

export function specFor(row: NationalIndicator): NatSpec | undefined {
  return NAT_SPECS.find((s) => s.codes.includes(row.code)) ?? NAT_SPECS.find((s) => s.re?.test(row.label))
}

/** Rows keyed by spec key (first matching row wins). */
export function indexRows(rows: NationalIndicator[]): Map<string, NationalIndicator> {
  const m = new Map<string, NationalIndicator>()
  for (const r of rows) {
    const s = specFor(r)
    if (s && !m.has(s.key)) m.set(s.key, r)
  }
  return m
}

/** Exchange rates and PPP conversion factors: local currency units per dollar, not dollars. */
const isLcu = (row: NationalIndicator) => /^LCU per\b/i.test(row.unit ?? '')

function inferFmt(row: NationalIndicator): Fmt {
  if (isLcu(row)) return 'lcu'
  const t = `${row.label} ${row.unit}`
  if (/international \$/i.test(t)) return 'intl'
  if (/US\$|USD/i.test(t)) return 'usd'
  if (/%|percent/i.test(t)) return 'pct'
  if (/\byears\b/i.test(t)) return 'years'
  return 'num'
}

/** International dollars (PPP) are written Int$ so they never read as market-rate US$. */
export const intl = (v: number) => (ok(v) ? money(v).replace('$', 'Int$') : '–')

export function fmtV(f: Fmt, v: number, unit?: string) {
  if (!ok(v)) return '–'
  if (f === 'usd') return money(v)
  if (f === 'intl') return intl(v)
  if (f === 'pct') return pct(v)
  if (f === 'years') return `${v.toFixed(1)} years`
  if (unit === 't') return tonnes(v)
  return `${count(v)}${unit ? ' ' + unit : ''}`
}

/** Compact display unit for a World Bank unit string ('' = the indicator name already carries it). */
const UNIT_SHORT: [RegExp, string, number?][] = [
  [/^metric tons$/i, 't'], [/^million ton-km$/i, 'ton-km', 1e6], [/route-km/i, 'route-km'], [/^TEU/i, 'TEU'],
  [/^kg per hectare$/i, 'kg/ha'], [/^sq\. km$/i, 'km²'], [/^kWh per capita$/i, 'kWh/person'], [/^kWh$/i, 'kWh'],
  [/^Mt CO2e$/i, 'Mt CO₂e'], [/^t CO2e\/capita$/i, 't CO₂e/person'], [/^billion cubic meters$/i, 'bn m³'], [/^cubic meters$/i, 'm³'],
  [/^micrograms per cubic meter$/i, 'µg/m³'], [/^kg of oil equivalent per capita$/i, 'kgoe/person'], [/^SIPRI trend indicator/i, 'TIV'],
]
export function shortUnit(unit: string | undefined): { u: string; k: number } {
  if (unit) for (const [re, u, k] of UNIT_SHORT) if (re.test(unit.trim())) return { u, k: k ?? 1 }
  return { u: '', k: 1 }
}

/** A national value with its unit; `c` names the country (needed for local-currency rows). */
export function fmtRow(row: NationalIndicator, v: number, c?: CountryId) {
  const s = specFor(row)
  const f = s?.fmt ?? inferFmt(row)
  if (f === 'lcu') {
    if (!ok(v)) return '–'
    const cur = c === 'lka' ? 'Rs ' : c === 'usa' ? '$' : ''
    const per = /international/i.test(row.unit) ? 'Int$' : 'US$'
    return `${cur}${Math.abs(v) >= 10 ? count(v) : v.toFixed(2)}${cur ? '' : ' LCU'} per ${per}`
  }
  if (s || f !== 'num') return fmtV(f, v, s?.unit)
  const su = shortUnit(row.unit)
  return fmtV(f, v * su.k, su.u)
}

/** COVID-19 collapsed international travel: tourism values for these years are not typical. */
export const isTourismRow = (row: NationalIndicator) => row.code.startsWith('ST.INT.')
export const PANDEMIC_YEARS = new Set([2020, 2021])

/** Extra fields the national stage writes (not yet in types.ts). */
type Row = NationalIndicator & {
  name?: string
  yield?: string | null
  better?: 'higher' | 'lower' | 'neutral'
  common?: { year: number; LKA: number | null; USA: number | null } | null
  source_org?: string
}

/** Values for both countries, using the latest common year when the latest years differ. */
export function pairVals(row: NationalIndicator | undefined, home: CountryId, rival: CountryId) {
  const a = rowVal(row, home), b = rowVal(row, rival)
  if (!a || !b) return null
  const cm = (row as Row).common
  if (a.y !== b.y && cm && ok(cm[ISO[home]]) && ok(cm[ISO[rival]])) {
    return { a: { v: cm[ISO[home]]!, y: cm.year }, b: { v: cm[ISO[rival]]!, y: cm.year }, shared: true }
  }
  return { a, b, shared: false }
}

const firstOrg = (o?: string) => (o ? o.split(/[;\n]/)[0].split(', uri:')[0].trim() : '')

export function rowVal(row: NationalIndicator | undefined, c: CountryId): { v: number; y: number | null } | null {
  const s = row?.[ISO[c]]
  if (!s || !ok(s.value)) return null
  return { v: s.value, y: s.year ?? null }
}

export function yearsText(home: CountryId, ya: number | null, rival: CountryId, yb: number | null) {
  if (ya == null && yb == null) return ''
  if (ya === yb) return `${ya}`
  return `${SHORT[home]} ${ya ?? 'n/a'}, ${SHORT[rival]} ${yb ?? 'n/a'}`
}

function statusOf(better: 'higher' | 'lower' | 'neutral', va: number, vb: number, fmt?: Fmt): Comment['status'] {
  if (better === 'neutral') return undefined
  const m = Math.max(Math.abs(va), Math.abs(vb))
  if (fmt === 'years' ? Math.abs(va - vb) < 0.3 : !m || Math.abs(va - vb) / m < 0.03) return 'even'
  return (va > vb) === (better === 'higher') ? 'ahead' : 'behind'
}

const sayParts = (tpl: string, v: Seg): Seg[] => {
  const [a, b] = tpl.split('{v}')
  return [a, v, b ?? '']
}

/** One-sentence comparison, larger value first: "The United States spends 3.4% of GDP on R&D — about 33× Sri Lanka's 0.11%". */
export function compareSentence(o: { say?: string; short: string; fmt: Fmt; unit?: string }, home: CountryId, rival: CountryId, va: number, vb: number): Seg[] {
  const F = (v: number) => fmtV(o.fmt, v, o.unit)
  if (!o.say) {
    const segs: Seg[] = [`${o.short}: ${nm(home, true)} `, fig(F(va), home), `, ${nm(rival)} `, fig(F(vb), rival)]
    if (va > 0 && vb > 0) {
      const k = Math.max(va, vb) / Math.min(va, vb)
      if (k >= 2) segs.push(` — a ${times(k)} gap`)
    }
    return segs
  }
  const bigIsHome = va >= vb
  const big = bigIsHome ? home : rival, small = bigIsHome ? rival : home
  const vBig = bigIsHome ? va : vb, vSmall = bigIsHome ? vb : va
  const head: Seg[] = [nm(big, true) + ' ', ...sayParts(o.say, fig(F(vBig), big))]
  const possV: Seg[] = [poss(small) + ' ', fig(F(vSmall), small)]
  if (!(vBig > 0 && vSmall > 0)) return [...head, ', against ', ...possV]
  const k = vBig / vSmall
  if (o.fmt === 'years') {
    return vBig - vSmall < 0.3 ? [...head, ', level with ', ...possV] : [...head, `, ${(vBig - vSmall).toFixed(1)} years more than `, ...possV]
  }
  if ((vBig - vSmall) / vBig < 0.03) return [...head, ', level with ', ...possV]
  if (o.fmt === 'pct' && k < 2.5) return [...head, `, ${(vBig - vSmall).toFixed(1)} points above `, ...possV]
  if (k < 2) return [...head, `, ${Math.round((k - 1) * 100)}% higher than `, ...possV]
  return [...head, ` — about ${times(k)} `, ...possV]
}

// ---------------------------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------------------------
export interface NatCtx {
  rows: NationalIndicator[]
  byKey: Map<string, NationalIndicator>
  home: CountryId
  rival: CountryId
  data: Partial<Record<CountryId, CountryData>>
  idx: IndexJson | null
}

function popAt(ctx: NatCtx, c: CountryId, year: number | null): { pop: number; src: string[] } | null {
  const row = ctx.byKey.get('pop')
  const s = row?.[ISO[c]]
  if (s?.series?.length) {
    let best: [number, number] | null = null
    for (const p of s.series) {
      if (!ok(p[1])) continue
      if (!best || (year != null && Math.abs(p[0] - year) < Math.abs(best[0] - year)) || (year == null && p[0] > best[0])) best = p
    }
    if (best) return { pop: best[1], src: [row!.source] }
  }
  if (s && ok(s.value)) return { pop: s.value, src: [row!.source] }
  const d = ctx.data[c]
  if (d && ok(d.meta.totals?.pop)) return { pop: d.meta.totals.pop, src: d.metricByKey.get('pop')?.sources ?? [] }
  return null
}

/** Comment for one national row (spec-driven or generic). */
/** Latest year before `before` with values for both countries. */
function commonBefore(row: NationalIndicator, home: CountryId, rival: CountryId, before: number) {
  const sa = new Map((row[ISO[home]]?.series ?? []).filter((p) => ok(p[1]) && p[0] < before).map((p) => [p[0], p[1]]))
  let best: { y: number; a: number; b: number } | null = null
  for (const [y, v] of row[ISO[rival]]?.series ?? []) {
    if (!ok(v) || y >= before || !sa.has(y)) continue
    if (!best || y > best.y) best = { y, a: sa.get(y)!, b: v }
  }
  return best
}

export function rowComment(row: NationalIndicator, ctx: NatCtx): Comment | null {
  const r = row as Row
  const spec = specFor(row)
  const { home, rival } = ctx
  // exchange rates / PPP factors are definitions of currency units, not a contest ("a 301× gap")
  if (!spec && isLcu(row)) return null
  let pv = pairVals(row, home, rival)
  if (!pv) return null
  // tourism series end in 2020: lead with the last pre-pandemic year and report the collapse separately
  let pandemic: { a: { v: number; y: number | null }; b: { v: number; y: number | null } } | null = null
  if (isTourismRow(row) && (PANDEMIC_YEARS.has(pv.a.y ?? 0) || PANDEMIC_YEARS.has(pv.b.y ?? 0))) {
    const pre = commonBefore(row, home, rival, 2020)
    if (pre) {
      pandemic = { a: pv.a, b: pv.b }
      pv = { a: { v: pre.a, y: pre.y }, b: { v: pre.b, y: pre.y }, shared: false }
    }
  }
  const { a, b } = pv
  const better = r.better ?? spec?.better ?? 'neutral'
  const o: { short: string; fmt: Fmt; icon: string; say?: string; unit?: string } =
    spec ?? { short: `${row.label}${row.unit ? ` (${row.unit})` : ''}`, fmt: inferFmt(row), icon: r.yield ? `y:${r.yield}` : '📊' }
  const segs = compareSentence(o, home, rival, a.v, b.v)
  const sources = [row.source].filter(Boolean)
  if (spec?.per) {
    const pa = popAt(ctx, home, a.y), pb = popAt(ctx, rival, b.y)
    if (pa && pb && pa.pop > 0 && pb.pop > 0) {
      const ca = (a.v / pa.pop) * spec.per.n, cb = (b.v / pb.pop) * spec.per.n
      const bigIsHome = a.v >= b.v
      const [c1, v1, c2, v2] = bigIsHome ? [home, ca, rival, cb] : [rival, cb, home, ca]
      segs.push(`; per ${spec.per.label}, ${SHORT[c1]} `, fig(fmtV(spec.per.fmt, v1), c1), ` vs ${SHORT[c2]} `, fig(fmtV(spec.per.fmt, v2), c2))
      sources.push(...pa.src, ...pb.src)
    }
  }
  const yt = yearsText(home, a.y, rival, b.y)
  if (pandemic) {
    const F = (v: number) => fmtV(o.fmt, v, o.unit)
    const yp = yearsText(home, pandemic.a.y, rival, pandemic.b.y)
    segs.push(` (${yt}, the last year before the COVID-19 pandemic; the World Bank series ends in ${yp}, when travel collapsed: ${SHORT[home]} `,
      fig(F(pandemic.a.v), home), ` vs ${SHORT[rival]} `, fig(F(pandemic.b.v), rival), ').')
  } else segs.push(yt ? ` (${yt}${pv.shared ? ', latest year with data for both' : ''}).` : '.')
  const k = a.v > 0 && b.v > 0 ? Math.abs(Math.log(a.v / b.v)) : 0
  const sameEra = a.y == null || b.y == null || Math.abs(a.y - b.y) <= 3
  const org = firstOrg(r.source_org)
  return {
    id: spec?.key ?? row.code, icon: o.icon, segs, sources,
    meta: [r.name ?? row.label, row.code, org && org.length < 70 ? `via ${org}` : '', spec?.note ?? ''].filter(Boolean).join(' · '),
    status: statusOf(better, a.v, b.v, o.fmt),
    weight: spec?.headline && sameEra && !pandemic ? k : 0,
  }
}

// ---------------------------------------------------------------------------------------------
// Combined / metric-derived comments
// ---------------------------------------------------------------------------------------------
type Gen = (ctx: NatCtx) => Comment | null

const both = (ctx: NatCtx) => (ctx.data.lka && ctx.data.usa ? ctx.data : null)
const msrc = (ctx: NatCtx, ...keys: string[]) => {
  const out: string[] = []
  for (const c of ['lka', 'usa'] as CountryId[]) for (const k of keys) out.push(...(ctx.data[c]?.metricByKey.get(k)?.sources ?? []))
  return out
}
const mvint = (ctx: NatCtx, key: string) => {
  const v = new Set((['lka', 'usa'] as CountryId[]).map((c) => ctx.data[c]?.metricByKey.get(key)?.vintage).filter(Boolean))
  return [...v].join(' / ')
}

/** "label: home X vs rival Y (k× gap)." for two national aggregates. */
function pairComment(ctx: NatCtx, id: string, icon: string, lead: string, va: number, vb: number, F: (v: number) => string,
  sources: string[], meta: string, better: 'higher' | 'lower' | 'neutral' = 'neutral', tail = ''): Comment | null {
  if (!ok(va) || !ok(vb)) return null
  const { home, rival } = ctx
  const segs: Seg[] = [lead, `${SHORT[home]} `, fig(F(va), home), ` vs ${SHORT[rival]} `, fig(F(vb), rival)]
  if (va > 0 && vb > 0) {
    const k = Math.max(va, vb) / Math.min(va, vb)
    if (k >= 2) segs.push(` (${times(k)})`)
  }
  segs.push(tail + '.')
  return { id, icon, segs, sources, meta, status: statusOf(better, va, vb) }
}

/** Mean yield per land tile (from hexes.json, land tiles only): the per-tile intensity index. */
const yieldPerTile = (d: CountryData, y: YieldKey) => {
  const r = landYieldMeans(d)
  return r.n > 0 ? r.mean[y] : NaN
}

const landAreaCache = new WeakMap<CountryData, number>()
/** Total area of the land tiles (H3 cell area incl. water, km²): the basis of the yield densities. */
function landTileArea(d: CountryData): number {
  let a = landAreaCache.get(d)
  if (a === undefined) {
    a = 0
    const ring = d.hexes.ring
    for (let i = 0; i < d.n; i++) if (ring[i] === 0) a += d.hexArea[i]
    landAreaCache.set(d, a)
  }
  return a
}

const zeroCache = new WeakMap<CountryData, Partial<Record<YieldKey, number>>>()
/** Share (0-1) of land tiles scoring 0 of a yield. */
function zeroShare(d: CountryData, y: YieldKey): number {
  let c = zeroCache.get(d)
  if (!c) { c = {}; zeroCache.set(d, c) }
  if (c[y] === undefined) {
    const a = d.hexes.yields?.[y], ring = d.hexes.ring
    let n = 0, z = 0
    if (a) for (let i = 0; i < d.n; i++) if (ring[i] === 0) { n++; if (!(a[i] > 0)) z++ }
    c[y] = n ? z / n : NaN
  }
  return c[y]!
}

/**
 * National density of the quantity a yield is scored from: its national total ÷ the land tiles' area
 * (per km² of tile, the yield basis). GDP uses the unrounded national total from meta.json.
 */
function yieldDensity(ctx: NatCtx, d: CountryData, y: YieldKey): { v: number; metric: string; unit: string } | null {
  const b = ctx.idx?.yield_scale.bases[y]
  const m = b ? d.metricByKey.get(b.metric) : undefined
  if (!b || !m || m.kind !== 'extensive') return null
  const tot = b.metric === 'gdp_usd' && ok(d.meta.totals?.gdp_usd) ? d.meta.totals.gdp_usd! : natAgg(d, b.metric)
  const area = landTileArea(d)
  return ok(tot) && area > 0 ? { v: tot / area, metric: b.metric, unit: m.unit } : null
}

/**
 * One per-tile yield remark. Yields are per-tile intensity indices on one log scale, so the country
 * comparison (and the Ahead / Behind badge) follows the national density they are scored from; the
 * mean yield per land tile is quoted beside it. A mean of a floored log index rewards activity spread
 * over many tiles (a tile below the base scores 0 however close it is), so when the mean points the
 * other way the remark says so instead of ranking the countries by it.
 */
function yieldTileComment(ctx: NatCtx, y: YieldKey, o: { id: string; icon: string; name: string; what: string; fmt: (v: number) => string; meta: string; bonus?: string }): Comment | null {
  const d = both(ctx); if (!d) return null
  const { home, rival } = ctx
  const ma = yieldPerTile(d[home]!, y), mb = yieldPerTile(d[rival]!, y)
  const da = yieldDensity(ctx, d[home]!, y), db = yieldDensity(ctx, d[rival]!, y)
  if (!ok(ma) || !ok(mb)) return null
  const sources = yieldSources(ctx, y)
  const meanSegs: Seg[] = [`${SHORT[home]} `, fig(ma.toFixed(2), home), ` vs ${SHORT[rival]} `, fig(mb.toFixed(2), rival)]
  if (!da || !db || !(da.v > 0 && db.v > 0)) {
    // no density to compare: quote the means without ranking the countries on them
    return { id: o.id, icon: o.icon, segs: [`Mean ${o.name} per land tile on the shared scale: `, ...meanSegs, '.'], sources, meta: o.meta }
  }
  const k = Math.max(da.v, db.v) / Math.min(da.v, db.v)
  const segs: Seg[] = [`${o.name} is scored from ${o.what} per km² of tile${o.bonus ? ` (${o.bonus})` : ''}: ${SHORT[home]} `,
    fig(o.fmt(da.v), home), ` vs ${SHORT[rival]} `, fig(o.fmt(db.v), rival), k >= 1.5 ? ` (${times(k)})` : '', '. ']
  const densHi = da.v >= db.v ? home : rival
  const meanHi = ma >= mb ? home : rival
  const meanGap = Math.abs(ma - mb) / Math.max(ma, mb, 1e-9)
  if (meanHi !== densHi && meanGap >= 0.03) {
    // the denser country has the lower mean: many of its tiles fall below the base and score 0
    const lo = densHi, hi = meanHi
    const mean = (c: CountryId) => (c === home ? ma : mb)
    const zLo = zeroShare(d[lo]!, y), zHi = zeroShare(d[hi]!, y)
    segs.push(`The mean ${o.name} per land tile is still higher in ${nm(hi)} (`, fig(mean(hi).toFixed(2), hi), ' vs ', fig(mean(lo).toFixed(2), lo),
      `) only because the log scale rewards activity spread over more of the land`)
    segs.push(ok(zLo) && ok(zHi) ? `: ${pct(zLo * 100)} of ${ADJ[lo]} land tiles score 0 ${o.name}, against ${pct(zHi * 100)} in ${SHORT[hi]}.` : '.')
  } else segs.push(`Mean ${o.name} per land tile: `, ...meanSegs, '.')
  return { id: o.id, icon: o.icon, segs, sources, meta: o.meta, status: statusOf('higher', da.v, db.v) }
}

function yieldSources(ctx: NatCtx, ...ys: string[]) {
  const ks = ys.map((y) => ctx.idx?.yield_scale.bases[y]?.metric).filter(Boolean) as string[]
  return msrc(ctx, ...ks)
}

const GENERATORS: Record<string, Gen> = {
  ppp_gap: (ctx) => {
    const n = ctx.byKey.get('gdp_pc'), p = ctx.byKey.get('gdp_pc_ppp')
    const nv = pairVals(n, ctx.home, ctx.rival), pv = pairVals(p, ctx.home, ctx.rival)
    if (!nv || !pv) return null
    const na = nv.a, nb = nv.b, pa = pv.a, pb = pv.b
    if (!(na.v > 0 && nb.v > 0 && pa.v > 0 && pb.v > 0)) return null
    if ((na.v > nb.v) !== (pa.v > pb.v)) return null
    const k1 = Math.max(na.v, nb.v) / Math.min(na.v, nb.v), k2 = Math.max(pa.v, pb.v) / Math.min(pa.v, pb.v)
    if (Math.abs(k1 - k2) / k1 < 0.05) return null
    const { home, rival } = ctx
    const yt = yearsText(home, pa.y, rival, pb.y)
    return {
      id: 'ppp_gap', icon: '🪙', sources: [p!.source, n!.source],
      segs: ['Adjusted for prices (PPP), the income gap ', k2 < k1 ? 'narrows' : 'widens', ' from ', fig(times(k1)), ' to ', fig(times(k2)), `: ${SHORT[home]} `,
        fig(intl(pa.v), home), ` vs ${SHORT[rival]} `, fig(intl(pb.v), rival), ` per citizen in international dollars${yt ? ` (${yt})` : ''}.`],
      meta: `${p!.label} · ${p!.code}`,
      status: statusOf('higher', pa.v, pb.v),
    }
  },
  sector_mix: (ctx) => {
    const keys = ['agri', 'industry', 'services']
    const rows = keys.map((k) => ctx.byKey.get(k))
    const pvs = rows.map((r) => pairVals(r, ctx.home, ctx.rival))
    const a = pvs.map((x) => x?.a ?? null), b = pvs.map((x) => x?.b ?? null)
    const have = keys.filter((_, i) => a[i] && b[i])
    if (have.length < 2) return null
    const names = { agri: 'agriculture', industry: 'industry', services: 'services' } as Record<string, string>
    const pick = (v: typeof a) => keys.map((_, i) => v[i]).filter((_, i) => a[i] && b[i])
    const txt = (v: typeof a) => pick(v).map((x) => x!.v.toFixed(1)).join(' / ')
    const yr = (v: typeof a) => [...new Set(pick(v).map((x) => x!.y))].join('–')
    const { home, rival } = ctx
    return {
      id: 'sector_mix', icon: 'y:production', sources: rows.filter(Boolean).map((r) => r!.source),
      segs: [`Economy mix (${have.map((k) => names[k]).join(' / ')}, % of GDP): ${SHORT[home]} `, fig(txt(a), home), ` (${yr(a)}) vs ${SHORT[rival]} `, fig(txt(b), rival), ` (${yr(b)}).`],
      meta: rows.filter(Boolean).map((r) => r!.code).join(', '),
    }
  },
  gold_tile: (ctx) => yieldTileComment(ctx, 'gold', {
    id: 'gold_tile', icon: 'y:gold', name: 'Gold', what: 'GDP', fmt: (v) => `${money(v)}/km²`,
    meta: `GDP (current US$, ${mvint(ctx, 'gdp_usd') || 'latest'}) ÷ land-tile area; tile yields on one log scale for both countries (see Methodology)`,
  }),
  gdp_pipeline: (ctx) => {
    if (ctx.byKey.get('gdp_pc')) return null
    const d = both(ctx); if (!d) return null
    const g = (c: CountryId) => (ok(d[c]!.meta.totals?.gdp_usd) && d[c]!.meta.totals.pop > 0 ? d[c]!.meta.totals.gdp_usd! / d[c]!.meta.totals.pop : NaN)
    return pairComment(ctx, 'gdp_pipeline', 'y:gold', 'GDP per citizen from the map’s own accounts: ', g(ctx.home), g(ctx.rival), money, msrc(ctx, 'gdp_usd', 'pop'), mvint(ctx, 'gdp_usd'), 'higher')
  },
  power: (ctx) => {
    const d = both(ctx); if (!d) return null
    const a = natAgg(d[ctx.home]!, 'power_capacity_mw'), b = natAgg(d[ctx.rival]!, 'power_capacity_mw')
    if (!(a > 0 && b > 0)) return null
    const pa = d[ctx.home]!.meta.totals.pop, pb = d[ctx.rival]!.meta.totals.pop
    // MW ÷ people × 1,000 kW/MW × 1,000 people = kW per 1,000 people (numerically equal to W per person)
    const kwPer1000 = (mw: number, people: number) => (mw * 1e3 / people) * 1e3
    return pairComment(ctx, 'power', 'y:production', 'Production capacity — installed power plants: ', a, b, (v) => `${count(v)} MW`, msrc(ctx, 'power_capacity_mw'), mvint(ctx, 'power_capacity_mw'), 'higher',
      pa > 0 && pb > 0 ? `; ${count(kwPer1000(a, pa))} vs ${count(kwPer1000(b, pb))} kW per 1,000 people` : '')
  },
  manpower: (ctx) => {
    const d = both(ctx); if (!d) return null
    const a = d[ctx.home]!.meta.totals.pop, b = d[ctx.rival]!.meta.totals.pop
    const la = d[ctx.home]!.meta.totals.land_km2, lb = d[ctx.rival]!.meta.totals.land_km2
    return pairComment(ctx, 'manpower', '👥', 'Manpower — residents: ', a, b, count, msrc(ctx, 'pop'), mvint(ctx, 'pop'), 'neutral',
      la > 0 && lb > 0 ? `; land to defend: ${count(la)} vs ${count(lb)} km²` : '')
  },
  mil_sites: (ctx) => {
    const d = both(ctx); if (!d) return null
    const a = natAgg(d[ctx.home]!, 'military_sites'), b = natAgg(d[ctx.rival]!, 'military_sites')
    if (!(a > 0 || b > 0)) return null
    const pa = d[ctx.home]!.meta.totals.pop, pb = d[ctx.rival]!.meta.totals.pop
    return pairComment(ctx, 'mil_sites', '🏰', 'Garrisons mapped (military bases & installations): ', a, b, count, msrc(ctx, 'military_sites'), mvint(ctx, 'military_sites'), 'neutral',
      pa > 0 && pb > 0 ? `; ${((a / pa) * 1e6).toFixed(1)} vs ${((b / pb) * 1e6).toFixed(1)} per million people` : '')
  },
  supply: (ctx) => {
    const d = both(ctx); if (!d) return null
    const a = natAgg(d[ctx.home]!, 'port_cargo_t'), b = natAgg(d[ctx.rival]!, 'port_cargo_t')
    if (!(a > 0 && b > 0)) return null
    return pairComment(ctx, 'supply', '⚓', 'Supply lines — port cargo handled: ', a, b, tonnes, msrc(ctx, 'port_cargo_t'), mvint(ctx, 'port_cargo_t'), 'higher')
  },
  culture_yield: (ctx) => {
    const c = yieldTileComment(ctx, 'culture', {
      id: 'culture_yield', icon: 'y:culture', name: 'Culture', what: 'arts, recreation & hospitality workers', bonus: 'plus heritage, museum and park bonuses',
      fmt: (v) => `${count(v)}/km²`,
      meta: 'Arts, recreation & hospitality workers ÷ land-tile area; Faith counts places of worship from different sources (OpenStreetMap in Sri Lanka, the U.S. Religion Census in the US), so it is quoted but not ranked (see Methodology)',
    })
    const d = both(ctx)
    if (!c || !d) return c
    // Faith: quoted, never ranked (the two countries' worship-site counts are not the same kind of count)
    const { home, rival } = ctx
    const fa = yieldPerTile(d[home]!, 'faith'), fb = yieldPerTile(d[rival]!, 'faith')
    if (ok(fa) && ok(fb)) c.segs.push(' Mean Faith per land tile: ', `${SHORT[home]} `, fig(fa.toFixed(2), home), ` vs ${SHORT[rival]} `, fig(fb.toFixed(2), rival), ' (not compared: different sources).')
    c.sources = yieldSources(ctx, 'culture', 'faith')
    return c
  },
  wonders: (ctx) => {
    const d = both(ctx); if (!d) return null
    const wa = d[ctx.home]!.wonders, wb = d[ctx.rival]!.wonders
    if (!wa.length && !wb.length) return null
    // a site can hold several titles (Yellowstone is a Natural Wonder and World Heritage): count sites once
    const sites = (ws: typeof wa) => new Set(ws.map((w) => (typeof w.wikidata === 'string' && w.wikidata ? w.wikidata : `${w.h3}|${w.name}`))).size
    const cats = (ws: typeof wa) => {
      const m = new Map<string, number>()
      ws.forEach((w) => m.set(w.category, (m.get(w.category) ?? 0) + 1))
      return [...m.entries()].sort((x, y) => y[1] - x[1]).slice(0, 2).map(([k, v]) => `${v} ${k}`).join(', ')
    }
    const { home, rival } = ctx
    const src = [...wa, ...wb].flatMap((w) => w.sources ?? [])
    const part = (ws: typeof wa, c: CountryId): Seg[] => {
      const n = sites(ws)
      const extra = [n !== ws.length ? `${ws.length} titles` : '', ws.length ? cats(ws) : ''].filter(Boolean).join('; ')
      return [fig(String(n), c), ` site${n === 1 ? '' : 's'}`, extra ? ` (${extra})` : '']
    }
    return {
      id: 'wonders', icon: '🏯', sources: src,
      segs: [`Wonders on the map: ${SHORT[home]} `, ...part(wa, home), ` vs ${SHORT[rival]} `, ...part(wb, rival), '.'],
      meta: 'Wonders are chosen by published ranking rules (Wonders tab); a site with two titles counts once',
    }
  },
  heritage: (ctx) => {
    const d = both(ctx); if (!d) return null
    const { home, rival } = ctx
    const parts: Seg[][] = []
    const keys: string[] = []
    const add = (key: string, f: (a: number, b: number) => Seg[] | null) => {
      const a = natAgg(d[home]!, key), b = natAgg(d[rival]!, key)
      if (!ok(a) || !ok(b) || (a <= 0 && b <= 0)) return
      const s = f(a, b)
      if (s) { parts.push(s); keys.push(key) }
    }
    add('museums', (a, b) => [fig(count(a), home), ' vs ', fig(count(b), rival), ' museums'])
    add('park_visitors', (a, b) => ['parks drew ', fig(count(a), home), ' vs ', fig(count(b), rival), ' visitors'])
    if (!parts.length) return null
    const segs: Seg[] = [`Heritage (${SHORT[home]} vs ${SHORT[rival]}): `]
    parts.forEach((p, i) => segs.push(...(i ? ['; ', ...p] : p)))
    segs.push('.')
    // places of worship come from different sources (OSM-mapped sites vs a census of congregations): not compared
    if (d[home]!.metricByKey.has('worship_sites') && d[rival]!.metricByKey.has('worship_sites')) {
      segs.push(' Places of worship are not compared: Sri Lanka’s are OpenStreetMap-mapped sites, the US figure is the Religion Census count of congregations.')
      keys.push('worship_sites')
    }
    return { id: 'heritage', icon: '🏛️', segs, sources: msrc(ctx, ...keys), meta: keys.map((k) => mvint(ctx, k)).filter(Boolean).join(' · ') }
  },
  science_yield: (ctx) => yieldTileComment(ctx, 'science', {
    id: 'science_yield', icon: 'y:science', name: 'Science', what: 'STEM workers', bonus: 'plus research-output bonuses', fmt: (v) => `${count(v)}/km²`,
    meta: 'STEM workers ÷ land-tile area; tile yields on one log scale plus research-output bonuses (see Methodology)',
  }),
  research_works: (ctx) => {
    const d = both(ctx); if (!d) return null
    const r = (c: CountryId) => { const w = natAgg(d[c]!, 'research_works_5y'); const p = d[c]!.meta.totals.pop; return w >= 0 && p > 0 ? (w / p) * 1e6 : NaN }
    return pairComment(ctx, 'research_works', '📚', 'Research output of local institutions, works per million people over 5 years: ', r(ctx.home), r(ctx.rival), count, msrc(ctx, 'research_works_5y', 'pop'), mvint(ctx, 'research_works_5y'), 'higher')
  },
  bachelors: (ctx) => {
    const d = both(ctx); if (!d) return null
    return pairComment(ctx, 'bachelors', '🎓', "Adults with a bachelor's degree or higher: ", natAgg(d[ctx.home]!, 'pct_bachelors'), natAgg(d[ctx.rival]!, 'pct_bachelors'), pct, msrc(ctx, 'pct_bachelors'), mvint(ctx, 'pct_bachelors'), 'higher')
  },
  engineers: (ctx) => {
    const d = both(ctx); if (!d) return null
    return pairComment(ctx, 'engineers', '🧑‍🔬', 'Engineers (STEM occupations) as a share of workers: ', natAgg(d[ctx.home]!, 'cit_engineers_pct'), natAgg(d[ctx.rival]!, 'cit_engineers_pct'), pct, msrc(ctx, 'cit_engineers_pct'), mvint(ctx, 'cit_engineers_pct'), 'higher')
  },
  protected_land: (ctx) => {
    // the WDI land-only series (ER.LND.PTLD.ZS) says the same thing when it is present
    if (ctx.byKey.get('protected')) return null
    const d = both(ctx); if (!d) return null
    return pairComment(ctx, 'protected_land', '🦌', 'Protected land on the map (WDPA designated areas, share of land area): ',
      natAgg(d[ctx.home]!, 'protected_pct'), natAgg(d[ctx.rival]!, 'protected_pct'), pct, msrc(ctx, 'protected_pct'),
      `${mvint(ctx, 'protected_pct')} · land area-weighted over land tiles; overlapping designations counted once`, 'higher')
  },
  landcover: (ctx) => {
    const d = both(ctx); if (!d) return null
    const { home, rival } = ctx
    const segs: Seg[] = ['Land cover (ESA WorldCover): ']
    const used: string[] = []
    for (const [k, l] of [['lc_tree', 'trees'], ['lc_crop', 'cropland'], ['lc_built', 'built-up']] as const) {
      const a = natAgg(d[home]!, k), b = natAgg(d[rival]!, k)
      if (!ok(a) || !ok(b)) continue
      if (used.length) segs.push('; ')
      segs.push(`${l} `, fig(pct(a * 100), home), ' vs ', fig(pct(b * 100), rival))
      used.push(k)
    }
    if (!used.length) return null
    segs.push(` of land (${SHORT[home]} vs ${SHORT[rival]}).`)
    return { id: 'landcover', icon: '🌳', segs, sources: msrc(ctx, ...used), meta: mvint(ctx, 'lc_tree') }
  },
  hazard: (ctx) => {
    const d = both(ctx); if (!d) return null
    const a = d[ctx.home]!.metricByKey.get('hazard_index')?.stats.p50, b = d[ctx.rival]!.metricByKey.get('hazard_index')?.stats.p50
    if (!ok(a) || !ok(b)) return null
    return pairComment(ctx, 'hazard', '🌪️', 'Natural hazard index (cyclones, M5+ quakes, tsunamis; 0–100, one scale), median land tile: ', a, b, (v) => v.toFixed(1), msrc(ctx, 'hazard_index'), mvint(ctx, 'hazard_index'), 'lower')
  },
  elevation: (ctx) => {
    const d = both(ctx); if (!d) return null
    const { home, rival } = ctx
    const ma = natAgg(d[home]!, 'elev_mean_m'), mb = natAgg(d[rival]!, 'elev_mean_m')
    const xa = d[home]!.metricByKey.get('elev_max_m')?.stats.max, xb = d[rival]!.metricByKey.get('elev_max_m')?.stats.max
    if (!ok(ma) || !ok(mb)) return null
    const segs: Seg[] = ['Terrain: mean land elevation ', fig(fmtMetric('m', ma), home), ' vs ', fig(fmtMetric('m', mb), rival)]
    if (ok(xa) && ok(xb)) segs.push('; highest tile summit ', fig(fmtMetric('m', xa), home), ' vs ', fig(fmtMetric('m', xb), rival))
    segs.push(` (${SHORT[home]} vs ${SHORT[rival]}).`)
    return { id: 'elevation', icon: '🏔️', segs, sources: msrc(ctx, 'elev_mean_m'), meta: `Copernicus DEM GLO-90 · ${mvint(ctx, 'elev_mean_m')}` }
  },
  climate: (ctx) => {
    const d = both(ctx); if (!d) return null
    const { home, rival } = ctx
    const ta = natAgg(d[home]!, 'temp_mean_c'), tb = natAgg(d[rival]!, 'temp_mean_c')
    const pa = natAgg(d[home]!, 'precip_mm'), pb = natAgg(d[rival]!, 'precip_mm')
    if (!ok(ta) || !ok(tb)) return null
    const segs: Seg[] = ['Climate (area-weighted): ', fig(fmtMetric('degC', ta), home), ' vs ', fig(fmtMetric('degC', tb), rival), ' mean temperature']
    if (ok(pa) && ok(pb)) segs.push('; ', fig(fmtMetric('mm/yr', pa), home), ' vs ', fig(fmtMetric('mm/yr', pb), rival), ' of rain')
    segs.push(` (${SHORT[home]} vs ${SHORT[rival]}).`)
    return { id: 'climate', icon: '🌦️', segs, sources: msrc(ctx, 'temp_mean_c', 'precip_mm'), meta: `WorldClim 2.1 · ${mvint(ctx, 'temp_mean_c')}` }
  },
}

/** Display order per advisor: national spec keys and generator ids. */
const ORDER: Record<AdvisorId, string[]> = {
  economic: ['gdp_pc', 'gdp_pipeline', 'ppp_gap', 'gdp_pc_ppp', 'sector_mix', 'agri', 'industry', 'services', 'emp_agri', 'unemp', 'gold_tile', 'remit',
    'gdp_growth', 'gini', 'gdp', 'power', 'exports', 'lfp_f', 'debt', 'inflation', 'gni_pc', 'gni_pc_ppp', 'manuf'],
  military: ['mil_gdp', 'mil_usd', 'mil_pers', 'mil_lab', 'mil_gov', 'manpower', 'mil_sites', 'supply', 'arms_imp', 'homicide'],
  cultural: ['tour_arr', 'tour_share', 'culture_yield', 'wonders', 'tour_rcpt', 'life', 'urban', 'internet', 'heritage', 'health_pc', 'u5mort'],
  scientific: ['rd', 'researchers', 'articles', 'ba_attain', 'patents', 'hitech', 'tertiary', 'internet', 'science_yield', 'research_works', 'bachelors', 'engineers', 'edu_spend', 'mht_manuf'],
  environmental: ['forest', 'co2', 'renew_elec', 'protected', 'protected_land', 'protected_all', 'pm25', 'water_stress', 'landcover', 'hazard', 'elevation', 'climate', 'renew_energy', 'hydro', 'energy_pc', 'water'],
}
export function advisorRows(rows: NationalIndicator[], advisor: AdvisorId): NationalIndicator[] {
  const order = ORDER[advisor]
  const rank = (r: NationalIndicator) => {
    const s = specFor(r)
    const i = s ? order.indexOf(s.key) : -1
    return i < 0 ? 100 + (s ? NAT_SPECS.indexOf(s) : 0) : i
  }
  return rows.filter((r) => (r.advisor || specFor(r)?.advisor) === advisor).sort((a, b) => rank(a) - rank(b))
}

const MAX_COMMENTS = 10

const FOLDS: Record<string, string[]> = { ppp_gap: ['gdp_pc_ppp'], sector_mix: ['agri', 'industry', 'services'] }

/** All comments for one advisor in the national view, in display order. Grouping follows row.advisor. */
export function nationalComments(advisor: AdvisorId, ctx: NatCtx): Comment[] {
  const out: Comment[] = []
  const folded = new Set<string>()
  const used = new Set<NationalIndicator>()
  for (const id of ORDER[advisor]) {
    if (out.length >= MAX_COMMENTS) break
    const gen = GENERATORS[id]
    if (gen) {
      const c = gen(ctx)
      if (c) { out.push(c); FOLDS[id]?.forEach((k) => folded.add(k)) }
      continue
    }
    if (folded.has(id)) continue
    const row = ctx.byKey.get(id)
    if (!row || (row.advisor && row.advisor !== advisor)) continue
    used.add(row)
    const c = rowComment(row, ctx)
    if (c) out.push(c)
  }
  // rows grouped under this advisor that the order list does not place (the ledger lists them all)
  for (const r of ctx.rows) {
    if (out.length >= MAX_COMMENTS) break
    if (r.advisor !== advisor || used.has(r)) continue
    const s = specFor(r)
    if (s && (folded.has(s.key) || s.key === 'pop')) continue
    const c = rowComment(r, ctx)
    if (c) out.push(c)
  }
  return out
}

/** Rows to draw as sparklines for an advisor (series in both countries, ≥ 3 points). */
export function sparkRows(rows: NationalIndicator[], advisor: AdvisorId, max = 2): NationalIndicator[] {
  const good = (r: NationalIndicator) => ['LKA', 'USA'].every((k) => ((r as unknown as Record<string, { series?: [number, number][] }>)[k]?.series ?? []).filter((p) => ok(p[1])).length >= 3)
  return advisorRows(rows, advisor)
    .filter((r) => specFor(r)?.spark != null && good(r))
    .sort((a, b) => (specFor(a)!.spark! - specFor(b)!.spark!))
    .slice(0, max)
}

/** Fallback verdict from the map itself: population density. */
export function densityVerdict(ctx: NatCtx): Comment | null {
  const d = both(ctx); if (!d) return null
  const { home, rival } = ctx
  const da = d[home]!.meta.totals.pop / d[home]!.meta.totals.land_km2, db = d[rival]!.meta.totals.pop / d[rival]!.meta.totals.land_km2
  if (!(da > 0 && db > 0)) return null
  const big = da >= db ? home : rival, small = da >= db ? rival : home
  const bd = Math.max(da, db), sd = Math.min(da, db)
  return {
    id: 'density', icon: '👥', sources: msrc(ctx, 'pop', 'land_km2'),
    segs: [`${nm(big, true)} packs `, fig(count(d[big]!.meta.totals.pop), big), ' people into ', fig(`${count(d[big]!.meta.totals.land_km2)} km²`, big),
      ` — ${times(bd / sd)} ${poss(small)} density (`, fig(`${count(bd)}`, big), ' vs ', fig(`${count(sd)}`, small), ` people per km² of land, ${mvint(ctx, 'pop') || '2023'}).`],
  }
}

