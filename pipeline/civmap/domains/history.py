"""History domain: dated "historic moments" for the timeline.

Every event is a located facility or site from another domain's point table plus a year taken
from a fetched source — never typed in:

* power stations — US: Operating Year of the earliest generator still listed as operating or
  standby in EIA-860 (a plant's first *surviving* unit; retired original units are not in the
  file). Sri Lanka: the CEB / GPPD commissioning year, else the service-entry / opening /
  inception date of the nearest Wikidata power station or dam within 5 km.
* World Heritage Sites — UNESCO inscription year (Wikidata P1435 qualifier, culture domain),
  plus the site's own founding date (Wikidata P571) when Wikidata records one.
* universities (OpenAlex, matched to Wikidata through their ROR id), museums, national parks,
  holy sites, airports (by IATA code) and spaceports — Wikidata inception (P571), date of official
  opening (P1619), service entry (P729) or start time (P580), in a per-kind order of preference.

Dates keep their Wikidata precision (year, decade, century, millennium) so a "5th century" date
is shown as such, never as an exact year.

Output (data/interim/<cc>/history/): points_events.csv + metrics.json / sources.json.
"""
from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd

from .. import config
from ..config import INTERIM
from ..fetch import fetch, unzip
from ..schema import Metric, Source, write_domain_meta
from ..util import sparql, write_points

DOMAIN = "history"

# Source records are copied from the domains that produced the underlying facility tables.
SOURCE_IDS = ["wikidata", "eia_860", "wri_gppd", "ceb_annual_report_2023", "unesco_whc_via_wikidata",
              "openalex", "ourairports", "faa_spaceports", "nps_irma_stats", "faa_enplanements_cy2025"]
SOURCES: list[Source] = []   # filled lazily from the other domains' sources.json

METRICS = [
    Metric("historic_events", "Dated historic moments", "count", "culture", "extensive", "none",
           "points_events", ["wikidata", "eia_860", "wri_gppd", "ceb_annual_report_2023", "unesco_whc_via_wikidata"],
           "", "Number of dated historic moments (founding, opening, commissioning or World Heritage "
           "inscription of the tile's facilities and sites).",
           "One row per dated event (see METHODOLOGY 'Historic moments'); summed per tile."),
]

COUNTRY_QID = {"lka": "Q854", "usa": "Q30"}
US_PLANT_MIN_MW = 250        # US plants on the timeline (≥ 250 MW keeps ~1,700 plants)
LKA_PLANT_MIN_MW = 1
US_UNIVERSITIES = 300        # top US universities by research works
US_MUSEUMS = 200             # top US museums by Wikipedia language editions
US_AIRPORT_MIN_ENPL = 1_000_000

# Wikidata date properties, in order of preference per kind
PROPS = {"inception": "P571", "opening": "P1619", "service": "P729", "start": "P580"}
PREF = {
    "university": ["inception"],
    "museum": ["opening", "inception"],
    "park": ["inception", "start", "opening"],
    "holy_site": ["inception"],
    "airport": ["opening", "inception", "service"],
    "spaceport": ["inception", "opening", "service"],
    "heritage": ["inception"],
    "power": ["service", "opening", "inception"],
}
EVENT_VERB = {
    "heritage": "inscribed as a UNESCO World Heritage Site", "power": "first unit in service",
}
# Wikidata-dated events are worded after the property that dates them, not the kind of place
# (a park's P571 can be the date its ancient reservoir was built)
PROP_VERB = {"inception": "founded", "opening": "opened", "service": "entered service", "start": "began"}
PRECISION_NAME = {11: "day", 10: "month", 9: "year", 8: "decade", 7: "century", 6: "millennium"}

# Timeline eras (conventional periods of world history, used to pace the slider; inclusive years)
ERAS = [
    {"id": "ancient", "label": "Ancient", "start": None, "end": 499},
    {"id": "medieval", "label": "Medieval", "start": 500, "end": 1449},
    {"id": "renaissance", "label": "Renaissance", "start": 1450, "end": 1749},
    {"id": "industrial", "label": "Industrial", "start": 1750, "end": 1899},
    {"id": "modern", "label": "Modern", "start": 1900, "end": 1944},
    {"id": "atomic", "label": "Atomic", "start": 1945, "end": 1989},
    {"id": "information", "label": "Information", "start": 1990, "end": None},
]


def _sources() -> list[Source]:
    if SOURCES:
        return SOURCES
    seen: dict[str, dict] = {}
    for cc in config.COUNTRIES:
        for f in (INTERIM / cc).glob("*/sources.json"):
            if f.parent.name in (DOMAIN, "reconciled", "civ"):
                continue
            for s in json.loads(f.read_text()):
                if s["id"] in SOURCE_IDS and s["id"] not in seen:
                    seen[s["id"]] = s
    fields = Source.__dataclass_fields__
    SOURCES.extend(Source(**{k: v for k, v in s.items() if k in fields}) for s in seen.values())
    return SOURCES


def _pts(country: str, rel: str) -> pd.DataFrame:
    f = INTERIM / country / f"{rel}.csv"
    return pd.read_csv(f, low_memory=False) if f.exists() else pd.DataFrame()


# ---------------------------------------------------------------------------------------------
# Wikidata helpers
# ---------------------------------------------------------------------------------------------

def parse_wd_time(t: str) -> int | None:
    """'+1942-01-01T00:00:00Z' -> 1942; '-0300-01-01T00:00:00Z' -> -300 (astronomical sign kept)."""
    m = re.match(r"^([+-]?)(\d+)-", t or "")
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) == "-" else y


def date_text(year: int, precision: int) -> str:
    """Human-readable date at the stated precision ('5th century', '3rd century BCE', '1940s').

    Wikidata writes BCE years with a minus sign and no year zero (44 BCE = -0044), so -399 is 399 BCE.
    """
    bce = year < 0
    if precision >= 9:
        return f"{abs(year)} BCE" if bce else str(year)
    if precision == 8:
        d = (abs(year) // 10) * 10
        return f"{d}s BCE" if bce else f"{d}s"
    if precision == 7:
        c = (abs(year) - 1) // 100 + 1 if year != 0 else 1
        suf = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
        return f"{c}{suf} century" + (" BCE" if bce else "")
    m = (abs(year) - 1) // 1000 + 1 if year != 0 else 1
    suf = {1: "st", 2: "nd", 3: "rd"}.get(m, "th")
    return f"{m}{suf} millennium" + (" BCE" if bce else "")


def wd_dates(qids: list[str], tag: str) -> pd.DataFrame:
    """All P571/P1619/P729/P580 time values (with precision) for the given items."""
    qids = sorted({q for q in qids if isinstance(q, str) and re.match(r"^Q\d+$", q)})
    rows = []
    # one simple query per property and batch: a UNION over four properties with a VALUES block
    # makes the Wikidata planner time out
    for i in range(0, len(qids), 100):
        chunk = qids[i:i + 100]
        values = " ".join(f"wd:{q}" for q in chunk)
        for name, p in PROPS.items():
            q = (f"SELECT ?item ?t ?prec WHERE {{ VALUES ?item {{ {values} }} ?item p:{p} ?st . "
                 f"?st psv:{p} ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec . }}")
            d = sparql(q, f"history_dates_{tag}_{name}_{i // 100:03d}")
            if len(d):
                rows.append(d.assign(prop=name))
    if not rows:
        return pd.DataFrame(columns=["qid", "prop", "year", "precision"])
    d = pd.concat(rows, ignore_index=True)
    d["qid"] = d["item"].str.rsplit("/", n=1).str[-1]
    d["year"] = d["t"].map(parse_wd_time)
    d["precision"] = pd.to_numeric(d["prec"], errors="coerce").fillna(9).astype(int)
    d = d.dropna(subset=["year"])
    d["year"] = d["year"].astype(int)
    return d[(d.year <= 2026) & (d.year >= -3000)][["qid", "prop", "year", "precision"]]


def pick_date(dates: pd.DataFrame, qid: str, kind: str):
    """Earliest value of the first preferred property that has one -> (year, precision, prop)."""
    sub = dates[dates.qid == qid]
    for prop in PREF[kind]:
        s = sub[sub.prop == prop]
        if len(s):
            r = s.sort_values(["year", "precision"], ascending=[True, False]).iloc[0]
            return int(r.year), int(r.precision), prop
    return None


def ror_to_qid(rors: list[str], tag: str) -> dict[str, str]:
    ids = sorted({str(r).rstrip("/").rsplit("/", 1)[-1] for r in rors if isinstance(r, str) and r})
    out = {}
    for i in range(0, len(ids), 150):
        values = " ".join(f'"{x}"' for x in ids[i:i + 150])
        d = sparql(f"SELECT ?item ?ror WHERE {{ VALUES ?ror {{ {values} }} ?item wdt:P6782 ?ror . }}",
                   f"history_ror_{tag}_{i // 150:03d}")
        for _, r in d.iterrows():
            out.setdefault(r["ror"], r["item"].rsplit("/", 1)[-1])
    return out


def iata_to_qid(codes: list[str], country: str) -> dict[str, str]:
    codes = sorted({c for c in codes if isinstance(c, str) and re.match(r"^[A-Z0-9]{3}$", c)})
    out = {}
    for i in range(0, len(codes), 150):
        values = " ".join(f'"{x}"' for x in codes[i:i + 150])
        d = sparql(f"SELECT ?item ?iata WHERE {{ VALUES ?iata {{ {values} }} ?item wdt:P238 ?iata ; "
                   f"wdt:P17 wd:{COUNTRY_QID[country]} . }}", f"history_iata_{country}_{i // 150:03d}")
        for _, r in d.iterrows():
            out.setdefault(r["iata"], r["item"].rsplit("/", 1)[-1])
    return out


def lka_power_wikidata() -> pd.DataFrame:
    """Power stations and dams in Sri Lanka with coordinates (for date matching)."""
    q = ("SELECT DISTINCT ?item ?itemLabel ?coord WHERE { "
         "VALUES ?cls { wd:Q159719 wd:Q12323 } ?item wdt:P31/wdt:P279* ?cls ; wdt:P17 wd:Q854 ; wdt:P625 ?coord . "
         'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }')
    d = sparql(q, "history_lka_power")
    if not len(d):
        return d
    ll = d["coord"].str.extract(r"Point\(([-\d.]+) ([-\d.]+)\)").astype(float)
    d["lon"], d["lat"] = ll[0], ll[1]
    d["qid"] = d["item"].str.rsplit("/", n=1).str[-1]
    return d.dropna(subset=["lat", "lon"])


def haversine_km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------------------------

def _row(r, kind, event_kind, year, precision, source, value=np.nan, unit="", qid=None, basis="", verb=None):
    return {
        "id": f"{event_kind}:{r['id']}", "name": r["name"], "lat": float(r["lat"]), "lon": float(r["lon"]),
        "kind": kind, "event": verb or EVENT_VERB[event_kind], "year": int(year), "precision": int(precision),
        "precision_name": PRECISION_NAME.get(int(precision), "year"),
        "date_text": date_text(int(year), int(precision)), "value": value, "value_unit": unit,
        "wikidata": qid or (r.get("wikidata") if isinstance(r.get("wikidata"), str) else None),
        "source": source, "date_basis": basis,
    }


def power_events(country: str) -> list[dict]:
    p = _pts(country, "infra/points_power_plants")
    if p.empty:
        return []
    out = []
    if country == "usa":
        z = fetch("https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip", "eia_860", "usa/eia860")
        d = unzip(z)
        gen = pd.read_excel(d / "3_1_Generator_Y2025.xlsx", sheet_name="Operable", header=1)
        gen = gen[gen.Status.isin(["OP", "SB"])]
        first = pd.to_numeric(gen["Operating Year"], errors="coerce").groupby(gen["Plant Code"]).min()
        p = p[p.capacity_mw >= US_PLANT_MIN_MW].copy()
        p["first_year"] = p["id"].str.replace("eia_", "", regex=False).astype(int).map(first)
        for _, r in p.dropna(subset=["first_year"]).iterrows():
            out.append(_row(r, "power", "power", r.first_year, 9, "eia_860", r.capacity_mw, "MW",
                            basis="Operating Year of the earliest generator still operating or on standby (EIA-860 2025)"))
        return out
    # Sri Lanka
    p = p[p.capacity_mw >= LKA_PLANT_MIN_MW].copy()
    wd = lka_power_wikidata()
    dates = wd_dates(wd.qid.tolist(), "lka_power") if len(wd) else pd.DataFrame()
    for _, r in p.iterrows():
        if pd.notna(r.commissioning_year):
            src = "ceb_annual_report_2023" if str(r.get("capacity_source", "")).startswith("ceb") else "wri_gppd"
            out.append(_row(r, "power", "power", round(r.commissioning_year), 9, src, r.capacity_mw, "MW",
                            basis="commissioning year (CEB / Global Power Plant Database)"))
            continue
        if not len(wd):
            continue
        dist = wd.apply(lambda w: haversine_km(r.lat, r.lon, w.lat, w.lon), axis=1)
        cand = wd.assign(dist=dist).sort_values("dist")
        cand = cand[cand.dist <= 5]
        for _, w in cand.iterrows():
            got = pick_date(dates, w.qid, "power")
            if got:
                out.append(_row(r, "power", "power", got[0], got[1], "wikidata", r.capacity_mw, "MW", qid=w.qid,
                                basis=f"Wikidata {got[2]} date of '{w.itemLabel}' ({w.dist:.1f} km from the plant)",
                                verb=PROP_VERB[got[2]] if got[2] != "service" else "first unit in service"))
                break
    return out


def wikidata_events(country: str, rel: str, kind: str, source: str, value_col: str | None, unit: str,
                    top: int | None = None, sort_col: str | None = None, qid_map: dict | None = None,
                    key_col: str = "wikidata") -> list[dict]:
    d = _pts(country, rel)
    if d.empty:
        return []
    if top and sort_col and sort_col in d.columns:
        d = d.sort_values(sort_col, ascending=False).head(top)
    d = d.copy()
    d["qid"] = d[key_col].map(qid_map) if qid_map is not None else d.get(key_col)
    d = d.dropna(subset=["qid"])
    dates = wd_dates(d.qid.tolist(), f"{country}_{kind}")
    out = []
    for _, r in d.iterrows():
        got = pick_date(dates, r.qid, kind)
        if not got:
            continue
        val = float(r[value_col]) if value_col and value_col in d.columns and pd.notna(r[value_col]) else np.nan
        out.append(_row(r, kind, kind, got[0], got[1], "wikidata", val, unit, qid=r.qid,
                        basis=f"Wikidata {got[2]} ({PROPS[got[2]]})", verb=PROP_VERB[got[2]]))
    return out


def heritage_events(country: str) -> list[dict]:
    h = _pts(country, "culture/points_heritage")
    if h.empty:
        return []
    out = []
    for _, r in h.iterrows():
        if pd.notna(r.get("inscribed")):
            out.append(_row(r, "heritage", "heritage", int(r.inscribed), 9, "unesco_whc_via_wikidata",
                            r.get("sitelinks", np.nan), "Wikipedia editions",
                            basis="UNESCO inscription year (Wikidata P1435 qualifier P580)"))
    dates = wd_dates(h.wikidata.dropna().tolist(), f"{country}_heritage")
    for _, r in h.dropna(subset=["wikidata"]).iterrows():
        got = pick_date(dates, r.wikidata, "heritage")
        if got and (pd.isna(r.get("inscribed")) or got[0] < int(r.inscribed)):
            out.append(_row(r, "heritage", "heritage_founded", got[0], got[1], "wikidata",
                            r.get("sitelinks", np.nan), "Wikipedia editions",
                            basis=f"Wikidata {got[2]} ({PROPS[got[2]]}) of the site", verb=PROP_VERB[got[2]]))
    return out


# Items that are whole countries / states are not places on a tile
EXCLUDE_CLASSES = ["Q6256", "Q3624078", "Q35657", "Q7275", "Q107390", "Q1352230", "Q15624"]
# administrative divisions are not places that were "founded" on a tile
ADMIN_WORDS = ("province", "district of sri lanka", "divisional secretariat", "county of", "county in",
               "u.s. state", "state of the united states", "administrative territorial", "census area",
               "borough of alaska", "territory", "region of")
SETTLEMENT_WORDS = ("city", "town", "village", "settlement", "municipality", "borough", "capital",
                    "county seat", "census-designated", "hamlet", "port city")
LANDMARK_QUERIES = {
    # (latest year, minimum Wikipedia language editions, max rows)
    "lka": [(1799, 3, 200), (1899, 5, 120)],
    "usa": [(1799, 12, 300), (1899, 40, 200)],
}


def landmark_events(country: str, known_qids: set[str]) -> list[dict]:
    """Dated settlements and monuments from Wikidata (inception before 1800 / 1900)."""
    rows = []
    lo = -10_000
    for hi, minlinks, limit in LANDMARK_QUERIES[country]:
        excl = " ".join(f"wd:{c}" for c in EXCLUDE_CLASSES)
        q = (f"SELECT ?item ?itemLabel ?coord ?t ?prec ?links WHERE {{ ?item wdt:P17 wd:{COUNTRY_QID[country]} ; "
             f"wdt:P625 ?coord ; wikibase:sitelinks ?links ; p:P571/psv:P571 ?v . ?v wikibase:timeValue ?t ; "
             f"wikibase:timePrecision ?prec . FILTER(?links >= {minlinks}) FILTER(YEAR(?t) <= {hi} && YEAR(?t) > {lo}) "
             f"FILTER NOT EXISTS {{ VALUES ?ex {{ {excl} }} ?item wdt:P31 ?ex }} "
             f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} ORDER BY DESC(?links) LIMIT {limit}')
        d = sparql(q, f"history_landmarks_{country}_{hi}")
        lo = hi
        if len(d):
            rows.append(d)
    if not rows:
        return []
    d = pd.concat(rows, ignore_index=True)
    d["qid"] = d["item"].str.rsplit("/", n=1).str[-1]
    d = d[~d.qid.isin(known_qids)]
    d["year"] = d["t"].map(parse_wd_time)
    d["precision"] = pd.to_numeric(d["prec"], errors="coerce").fillna(9).astype(int)
    d = d.dropna(subset=["year"]).sort_values(["qid", "year"]).drop_duplicates("qid")
    ll = d["coord"].str.extract(r"Point\(([-\d.]+) ([-\d.]+)\)").astype(float)
    d["lon"], d["lat"] = ll[0], ll[1]
    d = d.dropna(subset=["lat", "lon"])
    # classify with the items' P31 labels (one extra query per 100 items)
    cls: dict[str, list[str]] = {}
    qs = d.qid.tolist()
    for i in range(0, len(qs), 100):
        values = " ".join(f"wd:{x}" for x in qs[i:i + 100])
        c = sparql(f"SELECT ?item ?clsLabel WHERE {{ VALUES ?item {{ {values} }} ?item wdt:P31 ?cls . "
                   f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}',
                   f"history_landmark_classes_{country}_{i // 100:03d}")
        for _, r in c.iterrows():
            cls.setdefault(r["item"].rsplit("/", 1)[-1], []).append(str(r.get("clsLabel", "")).lower())
    out = []
    for _, r in d.iterrows():
        labels = cls.get(r.qid, [])
        kind = "settlement" if any(w in l for l in labels for w in SETTLEMENT_WORDS) else "landmark"
        if kind == "landmark" and any(w in l for l in labels for w in ADMIN_WORDS):
            continue
        rr = {"id": r.qid, "name": r.itemLabel, "lat": r.lat, "lon": r.lon}
        out.append(_row(rr, kind, kind, int(r.year), int(r.precision), "wikidata", float(r.links),
                        "Wikipedia editions", qid=r.qid, verb="founded",
                        basis=f"Wikidata inception (P571); class: {', '.join(labels[:2]) or 'unknown'}"))
    return out


def build(country: str) -> None:
    ev: list[dict] = []
    ev += power_events(country)
    ev += heritage_events(country)
    u = _pts(country, "science/points_universities")
    if not u.empty:
        rmap = ror_to_qid(u.ror.dropna().tolist(), country)
        u_rel = "science/points_universities"
        ev += wikidata_events(country, u_rel, "university", "openalex", "research_works_5y", "research works (5 y)",
                              top=US_UNIVERSITIES if country == "usa" else None, sort_col="research_works_5y",
                              qid_map={r: rmap.get(str(r).rstrip("/").rsplit("/", 1)[-1]) for r in u.ror.dropna()},
                              key_col="ror")
    ev += wikidata_events(country, "culture/points_museums", "museum", "wikidata", "sitelinks", "Wikipedia editions",
                          top=US_MUSEUMS if country == "usa" else None, sort_col="sitelinks")
    ev += wikidata_events(country, "culture/points_parks", "park", "wikidata", "park_visitors", "visits/yr")
    ev += wikidata_events(country, "society/points_holy_sites", "holy_site", "wikidata", "sitelinks", "Wikipedia editions")
    a = _pts(country, "infra/points_airports")
    if not a.empty:
        if country == "usa":
            a = a[pd.to_numeric(a.get("airport_pax"), errors="coerce") >= US_AIRPORT_MIN_ENPL]
        imap = iata_to_qid(a.iata.dropna().tolist(), country)
        tmp = INTERIM / country / DOMAIN
        tmp.mkdir(parents=True, exist_ok=True)
        a.to_csv(tmp / "_airports_subset.csv", index=False)
        ev += wikidata_events(country, f"{DOMAIN}/_airports_subset", "airport", "wikidata",
                              "airport_pax" if country == "usa" else None,
                              "enplanements" if country == "usa" else "", qid_map=imap, key_col="iata")
    ev += wikidata_events(country, "infra/points_spaceports", "spaceport", "wikidata", None, "")
    known = {e["wikidata"] for e in ev if isinstance(e.get("wikidata"), str)}
    ev += landmark_events(country, known)

    df = pd.DataFrame(ev)
    if df.empty:
        df = pd.DataFrame(columns=["id", "name", "lat", "lon", "kind", "event", "year", "precision"])
    df = df.drop_duplicates("id").sort_values(["year", "name"]).reset_index(drop=True)
    # Wikidata 'country' includes territories (Puerto Rico, Pacific atolls): keep only events on this
    # country's grid (the 50 states + DC for the US)
    from ..util import snap_points
    on_grid = pd.Series(snap_points(df.lat.to_numpy(float), df.lon.to_numpy(float), country)).notna().to_numpy()
    lon0, lat0, lon1, lat1 = config.COUNTRIES[country]["bbox"]
    in_box = ((df.lon >= lon0) & (df.lon <= lon1) & (df.lat >= lat0) & (df.lat <= lat1)).to_numpy()
    if country == "usa":
        in_box = in_box | ((df.lon > 170) & (df.lat > 50)).to_numpy()   # Aleutians west of 180°
    keep = on_grid & in_box
    if (~keep).any():
        print(f"  {country}: dropped {int((~keep).sum())} events outside the country grid: "
              f"{df.loc[~keep, 'name'].head(6).tolist()}", flush=True)
    df = df[keep].reset_index(drop=True)
    df["historic_events"] = 1
    write_points(country, DOMAIN, "events", df)
    write_domain_meta(config.interim_dir(country, DOMAIN), METRICS, _sources(), country)
    print(f"  {country}: {len(df)} events, years {df.year.min()}..{df.year.max()}, by kind "
          f"{df.kind.value_counts().to_dict()}", flush=True)


if __name__ == "__main__":
    from ..validate import main as validate
    for cc in ("lka", "usa"):
        build(cc)
    validate([DOMAIN, "lka", "usa"])
