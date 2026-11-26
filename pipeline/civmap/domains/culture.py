"""Culture domain: UNESCO World Heritage, national parks (visitors), museums, protected areas, tourist rooms.

Feeds the Culture yield (World Heritage sites, museums, park visitors), the Theater Square and
Preserve districts, and the 'Natural Wonder' / 'World Heritage' Wonders.

Outputs (data/interim/<country>/culture/):
  points_heritage.csv   id,name,lat,lon,kind(cultural|natural|mixed),whs_id,inscribed,criteria,sitelinks,
                        wikidata,components,heritage_sites(=1)
  points_parks.csv      id,name,lat,lon,kind,visitors,park_visitors,visitors_year,area_km2,wikidata[,local/foreign]
  points_museums.csv    id,name,lat,lon,kind(museum type),sitelinks,wikidata,count(=1),museums(=1)
  hex.csv               h3,protected_pct,protected_strict_pct,protected_km2   (every grid hex, land + water)
  admin_adm2.csv        admin_id,hotel_rooms                                    (Sri Lanka only)

Run:  python -m civmap.domains.culture      (builds lka + usa, then validates)
"""
from __future__ import annotations

import html
import json
from io import StringIO
import math
import re
import sys
import time
import unicodedata

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.strtree import STRtree

from .. import util
from ..config import CACHE, COUNTRIES, interim_dir
from ..core import load_admin, load_hexes
from ..fetch import USER_AGENT, fetch, fetch_json
from ..schema import Metric, Source, write_domain_meta

DOMAIN = "culture"
culture = DOMAIN

WD_COUNTRY = {"lka": "Q854", "usa": "Q30"}

# ---------------------------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------------------------

NPS_BASE = "https://irma.nps.gov"
NPS_REPORT = ("/Stats/SSRSReports/National%20Reports/"
              "Annual%20Park%20Ranking%20Report%20(1979%20-%20Last%20Calendar%20Year)")
SLTDA_YIR_2025 = ("https://www.sltda.gov.lk/storage/common_media/"
                  "Year_in_Review_2025_Final_updated_Report_2026_04_02-1.pdf")
HDX_WDPCA_LKA = "https://data.humdata.org/api/3/action/package_show?id=unep_wdpca_lka"
WDPCA_FS = ("https://services5.arcgis.com/Mj0hjvkNtV7NRhA7/arcgis/rest/services/WDPCA_v5/"
            "FeatureServer/1/query")
WDPCA_USA_WHERE = "iso3 LIKE '%USA%' AND desig_eng <> 'UNESCO-MAB Biosphere Reserve'"
WDPCA_PAGE = 2000

SOURCES = [
    Source("wikidata", "Wikidata (SPARQL query service)", "Wikimedia Foundation / Wikidata contributors",
           "https://query.wikidata.org/", "CC0 1.0", "accessed 2026-11", ["lka", "usa"],
           "UNESCO World Heritage Sites: items with heritage designation (P1435) = World Heritage Site "
           "(Q9259) in a non-deprecated statement without end time (P582), country (P17) Sri Lanka (Q854) / "
           "United States (Q30), with coordinates (P625), World Heritage Site ID (P757), selection criteria "
           "(P2614) and inscription year (P580 qualifier). Museums: instances of museum (Q33506) or any "
           "subclass, with P17 and P625, excluding items with dissolution (P576) or closure (P3999) dates. "
           "National-park coordinates and areas (P625, P2046): US items instance of 'National Park of the "
           "United States' (Q34918903); Sri Lankan park items listed in the module (LKA_PARKS)."),
    Source("unesco_whc_via_wikidata", "UNESCO World Heritage List (as recorded in Wikidata)",
           "UNESCO World Heritage Centre (via Wikidata)", "https://whc.unesco.org/en/list/",
           "CC BY-SA 3.0 IGO (UNESCO list) / CC0 (Wikidata)", "inscriptions 1978-2026", ["lka", "usa"],
           "whc.unesco.org is not reachable from the build environment, so the list is taken from Wikidata "
           "(P1435=Q9259, P757 site ids). Multi-component and transnational sites are collapsed to one row per "
           "World Heritage Site id."),
    Source("nps_irma_stats", "NPS Visitor Use Statistics - Annual Park Ranking Report (1979 - Last Calendar Year)",
           "U.S. National Park Service, Visitor Use Statistics Program (IRMA Stats)",
           "https://irma.nps.gov/Stats/Reports/National", "public domain", "2025", ["usa"],
           "SSRS report 'National Reports/Annual Park Ranking Report (1979 - Last Calendar Year)' run with "
           "parameters Year Type = Calendar, Field = Recreation Visits, Year = latest available (2025), Park "
           "Type = National Park, exported with the report viewer's CSV export (Format=CSV). One row per "
           "National Park unit (64 in 2025, incl. American Samoa and Virgin Islands)."),
    Source("sltda_year_in_review_2025", "Year in Review 2025 (tourism statistics report)",
           "Sri Lanka Tourism Development Authority (SLTDA); park data from the Department of Wildlife "
           "Conservation", "https://www.sltda.gov.lk/en/annual-statistical-report",
           "public document (SLTDA)", "2025", ["lka"],
           f"{SLTDA_YIR_2025}. Table 19 'Visitors to major national parks, 2025' (report p. 52; source: "
           "Department of Wildlife Conservation): local and foreign visitors and revenue by park. Table 12 "
           "'Total rooms by districts, 2025' (p. 36): rooms of all SLTDA-registered accommodation "
           "establishments by district (grand total 59,578). Both tables transcribed into this module."),
    Source("unep_wdpca", "World Database on Protected and Conserved Areas (WDPCA), polygons",
           "UNEP-WCMC and IUCN (Protected Planet)", "https://www.protectedplanet.net/",
           "UNEP-WCMC terms of use (non-commercial; attribution required)", "2026-11", ["lka", "usa"],
           "Sri Lanka: HDX dataset 'unep_wdpca_lka' (resource protected_conserved_areas_WDPCA_polygons.geojson, "
           "updated 2026-11-06). United States: ArcGIS feature service WDPCA_v5/FeatureServer/1 "
           f"({WDPCA_FS}) with where \"{WDPCA_USA_WHERE}\", outSR=4326, maxAllowableOffset=0.005 deg, paged "
           "2,000 records at a time. Cite as: UNEP-WCMC and IUCN (2026), Protected Planet: The World Database "
           "on Protected Areas (WDPA) and World Database on OECMs, Cambridge, UK."),
    Source("worldbank_wdi", "World Development Indicators", "World Bank", "https://data.worldbank.org/",
           "CC BY 4.0", "2025", ["lka", "usa"],
           "Used only for checks / conversion: ER.LND.PTLD.ZS terrestrial protected areas (% of land area), "
           "PA.NUS.FCRF official exchange rate (LKR per US$) for park revenue."),
]

# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

METRICS = [
    Metric("heritage_sites", "UNESCO World Heritage Sites", "count", "culture", "extensive", "none",
           "points_heritage", ["wikidata", "unesco_whc_via_wikidata"], "list as of 2026",
           "UNESCO World Heritage Sites (cultural, natural and mixed) located in the country.",
           "Wikidata items with P1435 = Q9259 (non-deprecated, no end date) and P17 = country, grouped by "
           "the numeric part of the World Heritage Site id (P757; '72bis'/'72ter' -> 72); component items "
           "without an id are merged into a site within 3 km. The representative item is the member with the "
           "most Wikipedia sitelinks that has a coordinate inside the country grid (coordinates are tried in "
           "turn: grid cell, snap within 6 H3 rings, else nearest land hex within 250 km). Sites whose "
           "coordinates are all outside the 50 states + DC (US territories, Mexico-only El Camino Real de "
           "Tierra Adentro, foreign components of transnational sites) are dropped. kind from the selection "
           "criteria (P2614): (i)-(vi) cultural, (vii)-(x) natural, both mixed. inscribed = earliest P580 "
           "start time of the designation (year).", "higher"),
    Metric("park_visitors", "National park visitors", "visits/yr", "culture", "extensive", "none",
           "points_parks", ["nps_irma_stats", "sltda_year_in_review_2025", "wikidata", "unep_wdpca"], "2025",
           "Annual visitors to national parks (USA: recreation visits; Sri Lanka: ticketed local + foreign "
           "visitors).",
           "USA: NPS IRMA Stats Annual Park Ranking Report, calendar year 2025, Recreation Visits, Park Type = "
           "National Park (64 units); National Park of American Samoa and Virgin Islands NP (territories) are "
           "dropped, leaving 62 parks in the 50 states. Coordinates and area (P2046) from the Wikidata item "
           "of each park (instance of Q34918903, matched on normalised names: 'NP' -> National Park, "
           "'NP & PRES' -> National Park and Preserve). Sri Lanka: SLTDA Year in Review 2025 Table 19 "
           "(Department of Wildlife Conservation), local + foreign visitors per park in 2025. Of its 23 rows, "
           "'Eth Athuru Sevana' (the Udawalawe Elephant Transit Home, a rehabilitation facility) and "
           "'Mirissa' (whale-watching tickets, not an area) are excluded, leaving 21 national parks, marine "
           "parks and sanctuaries (2,212,802 of the 2,604,243 visitors). 'Samanala Adaviya' is read as the "
           "Peak Wilderness Sanctuary and 'Bareef - Kalpitiya' as the Bar Reef Marine Sanctuary; Kalawewa and "
           "Girithale are located at the Kala Wewa and Giritale reservoirs (Wikidata). Coordinates from "
           "Wikidata; area_km2 from the WDPA polygon (GIS area) where the park is in WDPA, else Wikidata. "
           "Definitions differ: NPS counts recreation visits (person-entries, estimated from traffic counts "
           "for most parks); DWC counts tickets sold.", "higher"),
    Metric("museums", "Museums", "count", "culture", "extensive", "none", "points_museums", ["wikidata"],
           "Wikidata, accessed 2026-11",
           "Museums with coordinates documented in Wikidata (count of points).",
           "Wikidata items instance of museum (Q33506) or any subclass (art, history, science, house "
           "museums...), country P17 = Sri Lanka / United States, with coordinates; items with a dissolution "
           "(P576) or closure (P3999) date are dropped, one row per item (first coordinate). Items whose only "
           "museum-subclass types are visitor / nature / interpretation centres, heritage railways, film "
           "archives, cinematheques or virtual museums are dropped, as are points that do not snap to the "
           "country grid (US territories). kind = the most specific museum subclass of the item (P31). Coverage reflects Wikidata completeness, "
           "not an official museum census (the US IMLS universe lists far more institutions).", "higher"),
    Metric("protected_pct", "Protected area", "%", "environment", "intensive", "land", "hex",
           ["unep_wdpca"], "WDPCA 2026-11",
           "Share of the hex area covered by designated protected areas (any IUCN category).",
           "Union of WDPCA polygons with status Designated / Inscribed / Established, excluding UNESCO-MAB "
           "Biosphere Reserves (Protected Planet convention) and Proposed sites; all IUCN categories incl. "
           "'Not Reported'. Coverage = share of the hex's H3 child-cell centres (res+3 = 343 points per hex "
           "for Sri Lanka, res+2 = 49 for the USA) that fall inside any polygon (shapely STRtree). Computed "
           "for every grid hex (land and coastal water, 0 where none); marine protected areas count in water "
           "hexes. Sri Lanka's polygons are full resolution; US polygons were generalised server-side "
           "(maxAllowableOffset 0.005 deg ~ 500 m). WDPA point-only records (no boundary) are not counted.",
           "higher"),
    Metric("protected_strict_pct", "Strictly protected area (IUCN Ia-IV)", "%", "environment", "intensive",
           "land", "hex", ["unep_wdpca"], "WDPCA 2026-11",
           "Share of the hex area covered by protected areas in IUCN categories Ia, Ib, II, III or IV.",
           "As protected_pct, restricted to polygons whose iucn_cat is Ia, Ib, II, III or IV (strict "
           "reserves, wilderness, national parks, natural monuments, habitat/species management areas).",
           "higher"),
    Metric("protected_km2", "Protected area", "km2", "environment", "extensive", "none", "hex",
           ["unep_wdpca"], "WDPCA 2026-11", "Area of the hex (land or sea) inside designated protected areas.",
           "protected_pct / 100 x H3 cell area. Overlapping designations are counted once.", "higher"),
    Metric("hotel_rooms", "Tourist accommodation rooms", "rooms", "culture", "extensive", "built",
           "admin_adm2", ["sltda_year_in_review_2025"], "2025",
           "Rooms in SLTDA-registered tourist accommodation (hotels, boutique villas, guest houses, "
           "bungalows, homestays, rented apartments...).",
           "SLTDA Year in Review 2025 Table 12 'Total rooms by districts, 2025' (p. 36), transcribed; "
           "district names matched to COD-AB pcodes (Moneragala -> Monaragala, Rathnapura -> Ratnapura). "
           "Allocated to hexes by built-up land cover (fallback land area).", "higher", ["lka"]),
]

# ---------------------------------------------------------------------------------------------
# Transcribed tables (SLTDA Year in Review 2025)
# ---------------------------------------------------------------------------------------------

# Table 19 'Visitors to major national parks, 2025' (p. 52; source DWC):
# park -> (local visitors, foreign visitors, total visitors, total revenue incl. vehicle & boat income, Rs)
SLTDA_PARKS_2025 = {
    "Yala": (346229, 426535, 772764, 2755518830.48),
    "Horton Plains": (335313, 51770, 387083, 439026064.50),
    "Udawalawa": (170890, 197981, 368871, 1495100219.39),
    "Wasgomuwa": (12857, 3495, 16352, 11189534.04),
    "Minneriya": (78724, 76422, 155146, 616954177.20),
    "Bundala": (8706, 9203, 17909, 33829187.87),
    "Horagolla": (11823, 97, 11920, 586995.43),
    "Kaudulla": (42359, 38753, 81112, 292204164.40),
    "Galoya": (8615, 9593, 18208, 46211006.00),
    "Kumana": (30698, 14189, 44887, 55493172.00),
    "Angammedilla": (3948, 480, 4428, 3000261.18),
    "Galways Land": (10269, 693, 10962, 3116793.93),
    "Wilpattu": (88536, 76389, 164925, 443426416.89),
    "Maduruoya": (2365, 488, 2853, 1945772.80),
    "Lahugala": (416, 62, 478, 324874.20),
    "Pigeon Island": (73389, 20585, 93974, 161971850.00),
    "Hikkaduwa": (35042, 1613, 36655, 4414976.00),
    "Eth Athuru Sevana": (149785, 101119, 250904, 37210113.00),
    "Kalawewa": (1947, 446, 2393, 657738.81),
    "Bareef - Kalpitiya": (9082, 3732, 12814, 9001547.00),
    "Mirissa": (33213, 107324, 140537, 247070351.50),
    "Girithale": (8471, 153, 8624, 370302.20),
    "Samanala Adaviya": (432, 12, 444, 42214.79),
}
SLTDA_PARKS_TOTAL = (1463109, 1141134, 2604243)

# DWC table name -> (display name, Wikidata QID, WDPA site_id or None, kind); None = excluded (not a park)
LKA_PARKS = {
    "Yala": ("Yala National Park", "Q1530863", 899, "national park"),
    "Horton Plains": ("Horton Plains National Park", "Q1630245", 3281, "national park"),
    "Udawalawa": ("Udawalawe National Park", "Q131875", 901, "national park"),
    "Wasgomuwa": ("Wasgamuwa National Park", "Q7971410", 1364, "national park"),
    "Minneriya": ("Minneriya National Park", "Q15253488", 26087, "national park"),
    "Bundala": ("Bundala National Park", "Q2003663", 11582, "national park"),
    "Horagolla": ("Horagolla National Park", "Q5902508", 3302, "national park"),
    "Kaudulla": ("Kaudulla National Park", "Q6378661", 555592524, "national park"),
    "Galoya": ("Gal Oya National Park", "Q5517737", 900, "national park"),
    "Kumana": ("Kumana National Park", "Q6443439", 11142, "national park"),
    "Angammedilla": ("Angammedilla National Park", "Q4761937", None, "national park"),
    "Galways Land": ("Galway's Land National Park", "Q5519450", 555592529, "national park"),
    "Wilpattu": ("Wilpattu National Park", "Q3524793", 902, "national park"),
    "Maduruoya": ("Maduru Oya National Park", "Q6728863", 9007, "national park"),
    "Lahugala": ("Lahugala Kitulana National Park", "Q6473508", 2476, "national park"),
    "Pigeon Island": ("Pigeon Island National Park", "Q7193301", None, "marine national park"),
    "Hikkaduwa": ("Hikkaduwa National Park", "Q5760601", 555592536, "marine national park"),
    "Eth Athuru Sevana": None,   # Udawalawe Elephant Transit Home (Q7876950): rehabilitation centre
    "Kalawewa": ("Kalawewa (Kala Wewa)", "Q7354820", None, "wildlife area"),
    "Bareef - Kalpitiya": ("Bar Reef Marine Sanctuary (Kalpitiya)", "Q4857957", 62936, "marine sanctuary"),
    "Mirissa": None,             # whale-watching tickets sold at Mirissa: not a protected area
    "Girithale": ("Giritale", "Q25105275", None, "wildlife area"),
    "Samanala Adaviya": ("Peak Wilderness Sanctuary (Samanala Adaviya)", "Q3533131", 2469, "sanctuary"),
}

# Table 12 'Total rooms by districts, 2025' (p. 36)
SLTDA_ROOMS_2025 = {
    "Ampara": 846, "Anuradhapura": 1830, "Badulla": 2208, "Batticaloa": 1004, "Colombo": 11398,
    "Galle": 10021, "Gampaha": 4865, "Hambantota": 2620, "Jaffna": 757, "Kalutara": 4170, "Kandy": 4657,
    "Kegalle": 693, "Kilinochchi": 159, "Kurunegala": 645, "Mannar": 54, "Matale": 2561, "Matara": 3081,
    "Moneragala": 951, "Mullaitivu": 52, "Nuwara Eliya": 2699, "Polonnaruwa": 779, "Puttalam": 1669,
    "Rathnapura": 835, "Trincomalee": 877, "Vavuniya": 147,
}
SLTDA_ROOMS_TOTAL = 59578
LKA_DISTRICT_ALIASES = {"moneragala": "monaragala", "rathnapura": "ratnapura", "kegalla": "kegalle",
                        "mullaittivu": "mullaitivu", "puttalama": "puttalam", "digamadulla": "ampara"}

# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _qid(uri) -> str | None:
    return uri.rsplit("/", 1)[-1] if isinstance(uri, str) else None


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _year(s) -> float:
    if isinstance(s, str) and re.match(r"^-?\d{4}", s):
        return float(s[:4])
    return np.nan


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("ʻ", "").replace("'", "").replace("’", "").replace("&", " and ")
    s = re.sub(r"[‐-―-]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class _Locator:
    """Place points on the country grid: exact cell, snap within 6 rings, or nearest land hex (<= max_km)."""

    def __init__(self, country: str):
        self.country = country
        self.res = COUNTRIES[country]["h3_res"]
        hx = load_hexes(country)
        self.grid = set(hx.h3)
        land = hx[hx.ring == 0]
        self.land_h3 = land.h3.to_numpy()
        self.land_lat = land.lat.to_numpy(float)
        self.land_lon = land.lon.to_numpy(float)

    def locate(self, lat: float, lon: float, max_km: float = 0.0):
        """-> (lat, lon, how) or None.  how: 'grid' | 'snapped' | 'nearest land hex (<d> km)'."""
        if not (np.isfinite(lat) and np.isfinite(lon)):
            return None
        c = h3.latlng_to_cell(lat, lon, self.res)
        if c in self.grid:
            return lat, lon, "grid"
        if util.snap_points([lat], [lon], self.country)[0] is not None:
            return lat, lon, "snapped"
        if max_km > 0:
            d = _haversine_km(lat, lon, self.land_lat, self.land_lon)
            i = int(np.argmin(d))
            if d[i] <= max_km:
                la, lo = h3.cell_to_latlng(self.land_h3[i])
                return la, lo, f"moved to nearest land hex ({d[i]:.0f} km)"
        return None


# ---------------------------------------------------------------------------------------------
# UNESCO World Heritage Sites
# ---------------------------------------------------------------------------------------------

CULTURAL_CRIT = {"i", "ii", "iii", "iv", "v", "vi"}
NATURAL_CRIT = {"vii", "viii", "ix", "x"}


def heritage(country: str, loc: _Locator) -> pd.DataFrame:
    q = """SELECT ?site ?siteLabel ?sitelinks
      (GROUP_CONCAT(DISTINCT STR(?coord); separator="|") AS ?coords)
      (GROUP_CONCAT(DISTINCT ?whsid; separator=" ") AS ?whsids)
      (GROUP_CONCAT(DISTINCT ?critLabel; separator=" ") AS ?crits)
      (MIN(?start) AS ?start1)
      (GROUP_CONCAT(DISTINCT ?p31l; separator="; ") AS ?types) WHERE {
      ?site p:P1435 ?st . ?st ps:P1435 wd:Q9259 ; wikibase:rank ?rank .
      FILTER(?rank != wikibase:DeprecatedRank)
      FILTER NOT EXISTS { ?st pq:P582 [] }
      OPTIONAL { ?st pq:P580 ?start }
      ?site wdt:P17 wd:%s ; wikibase:sitelinks ?sitelinks .
      OPTIONAL { ?site wdt:P625 ?coord }
      OPTIONAL { ?site wdt:P757 ?whsid }
      OPTIONAL { ?site wdt:P2614 ?crit . ?crit rdfs:label ?critLabel . FILTER(lang(?critLabel)='en') }
      OPTIONAL { ?site wdt:P31 ?p31 . ?p31 rdfs:label ?p31l . FILTER(lang(?p31l)='en') }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". }
    } GROUP BY ?site ?siteLabel ?sitelinks""" % WD_COUNTRY[country]
    d = util.sparql(q, f"culture_whs_{country}")
    for c in ("coords", "whsids", "crits", "start1", "types"):
        if c not in d.columns:
            d[c] = ""
    d = d.fillna("")
    items = []
    for r in d.itertuples():
        coords = [util.parse_wkt_point(s) for s in r.coords.split("|") if s.startswith("Point")]
        ids = sorted({re.match(r"\d+", x).group(0) for x in r.whsids.split() if re.match(r"\d+", x)})
        items.append(dict(qid=_qid(r.site), label=r.siteLabel, sitelinks=int(r.sitelinks), coords=coords,
                          ids=ids, crits=set(r.crits.replace("(", " ").replace(")", " ").split()),
                          start=_year(r.start1), types=r.types))
    # group by World Heritage Site id; id-less items join a site within 3 km, else stand alone
    groups: dict[str, list[dict]] = {}
    for it in items:
        if it["ids"]:
            groups.setdefault(it["ids"][0], []).append(it)
    for it in items:
        if it["ids"]:
            continue
        key = None
        for gid, mem in groups.items():
            for m in mem:
                for (a, b) in m["coords"]:
                    for (c, e) in it["coords"]:
                        if _haversine_km(a, b, c, e) <= 3.0:
                            key = gid
        groups.setdefault(key or f"wd:{it['qid']}", []).append(it)

    rows, dropped = [], []
    for gid, mem in groups.items():
        mem = sorted(mem, key=lambda m: -m["sitelinks"])
        placed = None
        for max_km in (0.0, 250.0):   # any coordinate on the grid first, then the nearest-land fallback
            for m in mem:
                for (la, lo) in m["coords"]:
                    p = loc.locate(la, lo, max_km=max_km)
                    if p is not None:
                        placed = (m, p, (la, lo))
                        break
                if placed:
                    break
            if placed:
                break
        if placed is None:
            dropped.append(f"{mem[0]['label']} ({gid})")
            continue
        rep, (la, lo, how), orig = placed
        crits = set().union(*[m["crits"] for m in mem])
        has_c, has_n = bool(crits & CULTURAL_CRIT), bool(crits & NATURAL_CRIT)
        if has_c and has_n:
            kind = "mixed"
        elif has_n:
            kind = "natural"
        elif has_c:
            kind = "cultural"
        else:
            kind = "natural" if re.search(r"park|protected|forest|reserve", " ".join(m["types"] for m in mem)) \
                else "cultural"
        roman = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
        starts = [m["start"] for m in mem if np.isfinite(m["start"])]
        rows.append(dict(
            id=rep["qid"], name=rep["label"], lat=round(la, 6), lon=round(lo, 6), kind=kind,
            whs_id=gid if not gid.startswith("wd:") else "",
            inscribed=int(min(starts)) if starts else np.nan,
            criteria=" ".join(f"({c})" for c in roman if c in crits),
            sitelinks=max(m["sitelinks"] for m in mem), wikidata=rep["qid"],
            components=len(mem), location=how if how != "grid" else "",
            orig_lat=orig[0] if how.startswith("moved") else np.nan,
            orig_lon=orig[1] if how.startswith("moved") else np.nan,
            heritage_sites=1))
    df = pd.DataFrame(rows).sort_values(["inscribed", "name"]).reset_index(drop=True)
    print(f"  heritage: {len(df)} World Heritage Sites ({df.kind.value_counts().to_dict()}); "
          f"dropped outside grid: {dropped}")
    moved = df[df.location != ""]
    if len(moved):
        print("    placement notes:", dict(zip(moved.name, moved.location)))
    return df


# ---------------------------------------------------------------------------------------------
# National parks
# ---------------------------------------------------------------------------------------------


def _nps_ranking_csv() -> tuple:
    """NPS 'Annual Park Ranking Report' (Recreation Visits, National Parks), latest calendar year, as CSV.

    The report lives in an SSRS ReportViewer: the parameters are cascading drop-downs, so the export URL
    is obtained by replaying the viewer's postbacks in a session; the CSV is then fetched (and cached)
    through civmap.fetch with the session cookie.
    """
    sub = "usa/nps"
    cached = sorted((CACHE / sub).glob("nps_park_ranking_*_recreation_visits_national_park.csv"))
    if cached:
        p = cached[-1]
        return p, int(re.search(r"_(\d{4})_", p.name).group(1))

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    r = s.get(NPS_BASE + NPS_REPORT, timeout=120)
    r.raise_for_status()
    url = NPS_BASE + html.unescape(re.search(r'<iframe[^>]*src="([^"]+)"', r.text).group(1))
    t = s.get(url, timeout=180).text

    def hidden(t):
        f = {}
        for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', t):
            n = re.search(r'name="([^"]+)"', m.group(0))
            v = re.search(r'value="([^"]*)"', m.group(0))
            if n:
                f[html.unescape(n.group(1))] = html.unescape(v.group(1)) if v else ""
        return f

    def options(t, name):
        m = re.search(r'<select name="%s"[^>]*>(.*?)</select>' % re.escape(name), t, re.S)
        return [(v, html.unescape(lab).replace("\xa0", " "))
                for v, lab in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1))]

    names = [f"ReportViewer$ctl04$ctl{i:02d}$ddValue" for i in (3, 5, 7, 9)]
    year_opts = [lab for _, lab in options(t, names[2]) if re.fullmatch(r"\d{4}", lab)]
    year = max(int(y) for y in year_opts)
    want = ["Calendar", "Recreation Visits", str(year), "National Park"]
    chosen = {}
    for nm, w in zip(names, want):
        val = next(v for v, lab in options(t, nm) if lab == w)
        chosen[nm] = val
        f = hidden(t)
        f.update(chosen)
        f.update({"__EVENTTARGET": nm, "__EVENTARGUMENT": ""})
        t = s.post(url, data=f, timeout=180).text
    f = hidden(t)
    f.update(chosen)
    f.update({"ReportViewer$ctl04$ctl00": "View Report", "__EVENTTARGET": "", "__EVENTARGUMENT": ""})
    t = s.post(url, data=f, timeout=300).text
    exp = re.search(r'"ExportUrlBase":"([^"]+)"', t).group(1).encode().decode("unicode_escape")
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    p = fetch(NPS_BASE + exp + "CSV", "nps_irma_stats", sub,
              f"nps_park_ranking_{year}_recreation_visits_national_park.csv", headers={"Cookie": cookie})
    return p, year


def _wd_area_clause(var: str = "?p") -> str:
    return ("OPTIONAL { %s p:P2046 ?ast . ?ast psv:P2046 ?av . ?av wikibase:quantityAmount ?a ; "
            "wikibase:quantityUnit ?u . ?u p:P2370/psv:P2370 [ wikibase:quantityAmount ?conv ] . "
            "BIND(?a * ?conv / 1000000 AS ?area) }" % var)


def parks_usa(loc: _Locator) -> pd.DataFrame:
    p, year = _nps_ranking_csv()
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    hdr = next(i for i, l in enumerate(lines) if l.startswith("ParkName,"))
    nps = pd.read_csv(StringIO("\n".join(lines[hdr:])), dtype=str)
    nps = nps[nps.ParkName.notna() & nps.Value.notna()]
    nps["visitors"] = nps.Value.str.replace(",", "").astype(float)
    assert f"Recreation Visits in: {year}" in "\n".join(lines[:hdr]) and "National Park" in "\n".join(lines[:hdr])

    q = """SELECT ?p ?pLabel ?sitelinks (SAMPLE(?coord) AS ?coord1) (MAX(?area) AS ?area_km2)
      (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases) WHERE {
      ?p wdt:P31 wd:Q34918903 ; wikibase:sitelinks ?sitelinks .
      OPTIONAL { ?p wdt:P625 ?coord }
      OPTIONAL { ?p skos:altLabel ?alias . FILTER(lang(?alias)='en') }
      %s
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?p ?pLabel ?sitelinks""" % _wd_area_clause()
    wd = util.sparql(q, "culture_us_national_parks")
    wd = wd[wd.coord1.notna()].copy()
    wd["sitelinks"] = wd.sitelinks.astype(int)
    keys = {}
    for r in wd.sort_values("sitelinks").itertuples():   # higher sitelinks win
        for nm in [r.pLabel] + [a for a in str(r.aliases or "").split("|") if a and a != "nan"]:
            keys[_norm(nm)] = r

    def cands(n: str) -> list[str]:
        base = re.sub(r"\s+NP\s*&\s*PRES$", " National Park and Preserve", n)
        base = re.sub(r"\s+NP$", " National Park", base)
        out = [base]
        if base.endswith(" and Preserve"):
            out.append(base[: -len(" and Preserve")])
        return out

    rows, unmatched = [], []
    for r in nps.itertuples():
        hit = next((keys[_norm(c)] for c in cands(r.ParkName) if _norm(c) in keys), None)
        rows.append((r.ParkName, r.visitors, hit, cands(r.ParkName)))
        if hit is None:
            unmatched.append(r.ParkName)
    # fallback: exact English label lookup without the class restriction
    fb = {}
    if unmatched:
        labels = sorted({c for n in unmatched for c in cands(n)})
        vals = " ".join('"%s"@en' % c.replace('"', "") for c in labels)
        q2 = """SELECT ?p ?l ?sitelinks (SAMPLE(?coord) AS ?coord1) (MAX(?area) AS ?area_km2) WHERE {
          VALUES ?l { %s } ?p rdfs:label ?l ; wdt:P17 wd:Q30 ; wdt:P625 ?coord ; wikibase:sitelinks ?sitelinks .
          %s
        } GROUP BY ?p ?l ?sitelinks""" % (vals, _wd_area_clause())
        w2 = util.sparql(q2, "culture_us_parks_fallback")
        if len(w2):
            w2["sitelinks"] = w2.sitelinks.astype(int)
            for r in w2.sort_values("sitelinks").itertuples():
                fb[_norm(r.l)] = r
    out, dropped = [], []
    for name, vis, hit, cs in rows:
        if hit is None:
            hit = next((fb[_norm(c)] for c in cs if _norm(c) in fb), None)
        if hit is None:
            dropped.append(f"{name} (no Wikidata match)")
            continue
        la, lo = util.parse_wkt_point(hit.coord1)
        pl = loc.locate(la, lo)
        if pl is None:
            dropped.append(f"{name} (outside 50 states + DC)")
            continue
        qid = _qid(hit.p)
        area = pd.to_numeric(getattr(hit, "area_km2", np.nan), errors="coerce")
        out.append(dict(id=f"nps:{qid}", name=getattr(hit, "pLabel", None) or hit.l, lat=round(la, 6),
                        lon=round(lo, 6), kind="national park and preserve" if "PRES" in name else "national park",
                        nps_name=name, visitors=vis, park_visitors=vis, visitors_year=year,
                        area_km2=round(float(area), 2) if np.isfinite(area) else np.nan,
                        area_source="Wikidata" if np.isfinite(area) else "", wikidata=qid))
    df = pd.DataFrame(out).sort_values("park_visitors", ascending=False).reset_index(drop=True)
    print(f"  parks: {len(df)} US national parks, {df.park_visitors.sum():,.0f} recreation visits ({year}); "
          f"report total {nps.visitors.sum():,.0f} for {len(nps)} units; dropped: {dropped}")
    return df


def _wdpa_lka() -> gpd.GeoDataFrame:
    meta = fetch_json(HDX_WDPCA_LKA, "unep_wdpca", "lka/wdpa", "hdx_unep_wdpca_lka_package.json")
    res = next(r for r in meta["result"]["resources"]
               if r["name"] == "protected_conserved_areas_WDPCA_polygons.geojson")
    p = fetch(res["url"], "unep_wdpca", "lka/wdpa", "lka_wdpca_polygons.geojson")
    return gpd.read_file(p)


def parks_lka(loc: _Locator, wdpa: gpd.GeoDataFrame) -> pd.DataFrame:
    fetch(SLTDA_YIR_2025, "sltda_year_in_review_2025", "lka/sltda", "sltda_year_in_review_2025.pdf")
    tot = np.array([v[:3] for v in SLTDA_PARKS_2025.values()]).sum(axis=0)
    assert tuple(int(x) for x in tot) == SLTDA_PARKS_TOTAL, (tot, SLTDA_PARKS_TOTAL)
    for k, (l, f, t, _) in SLTDA_PARKS_2025.items():
        assert l + f == t, k
    qids = [v[1] for v in LKA_PARKS.values() if v]
    q = """SELECT ?p ?pLabel ?sitelinks (SAMPLE(?coord) AS ?coord1) (MAX(?area) AS ?area_km2) WHERE {
      VALUES ?p { %s } ?p wikibase:sitelinks ?sitelinks . OPTIONAL { ?p wdt:P625 ?coord }
      %s
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?p ?pLabel ?sitelinks""" % (" ".join(f"wd:{q}" for q in qids), _wd_area_clause())
    wd = util.sparql(q, "culture_lka_parks").set_index("p")
    wd.index = [_qid(i) for i in wd.index]
    wdpa_area = wdpa.set_index("site_id")["gis_area"]
    rate = util.lkr_per_usd(2025)
    rows, excluded = [], []
    for key, (local, foreign, total, rev) in SLTDA_PARKS_2025.items():
        m = LKA_PARKS[key]
        if m is None:
            excluded.append((key, total))
            continue
        name, qid, site, kind = m
        w = wd.loc[qid]
        la, lo = util.parse_wkt_point(w.coord1)
        assert loc.locate(la, lo) is not None, key
        if site is not None and site in wdpa_area.index:
            area, area_src = float(wdpa_area[site]), "WDPA"
        elif kind == "wildlife area":   # located via a reservoir / town item: its area is not the park's
            area, area_src = np.nan, ""
        else:
            a = pd.to_numeric(w.get("area_km2"), errors="coerce")
            area, area_src = (float(a), "Wikidata") if np.isfinite(a) else (np.nan, "")
        rows.append(dict(id=f"dwc:{qid}", name=name, lat=round(la, 6), lon=round(lo, 6), kind=kind,
                         dwc_name=key, visitors=float(total), park_visitors=float(total),
                         local_visitors=local, foreign_visitors=foreign, visitors_year=2025,
                         revenue_lkr=rev, revenue_usd=round(rev / rate, 0),
                         area_km2=round(area, 2) if np.isfinite(area) else np.nan, area_source=area_src,
                         location=("Wikidata item of the nearby reservoir (park not in Wikidata/WDPA)"
                                   if kind == "wildlife area" else ""),
                         wdpa_id=site if site is not None else np.nan, wikidata=qid))
    df = pd.DataFrame(rows).sort_values("park_visitors", ascending=False).reset_index(drop=True)
    print(f"  parks: {len(df)} Sri Lankan parks, {df.park_visitors.sum():,.0f} visitors (2025); "
          f"excluded (not parks): {excluded}; table total {SLTDA_PARKS_TOTAL[2]:,}")
    return df


# ---------------------------------------------------------------------------------------------
# Museums
# ---------------------------------------------------------------------------------------------


NON_MUSEUM_TYPES = {"visitor center", "National Park Service visitor center", "nature center",
                    "interpretation centre", "heritage railway", "virtual museum", "film archive", "cinematheque"}


def museums(country: str, loc: _Locator) -> pd.DataFrame:
    q = """SELECT ?m ?mLabel ?sitelinks (SAMPLE(STR(?coord)) AS ?coord1)
      (GROUP_CONCAT(DISTINCT ?tl; separator="; ") AS ?types) WHERE {
      ?m wdt:P31/wdt:P279* wd:Q33506 ; wdt:P17 wd:%s ; wdt:P625 ?coord ; wikibase:sitelinks ?sitelinks .
      FILTER NOT EXISTS { ?m wdt:P576 [] }
      FILTER NOT EXISTS { ?m wdt:P3999 [] }
      OPTIONAL { ?m wdt:P31 ?t . ?t wdt:P279* wd:Q33506 . ?t rdfs:label ?tl . FILTER(lang(?tl)='en') }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul,si,ta". }
    } GROUP BY ?m ?mLabel ?sitelinks""" % WD_COUNTRY[country]
    d = util.sparql(q, f"culture_museums_{country}")
    d = d.drop_duplicates("m")
    ll = np.array([util.parse_wkt_point(s) if isinstance(s, str) and s.startswith("Point") else (np.nan, np.nan)
                   for s in d.coord1])
    d["lat"], d["lon"] = ll[:, 0], ll[:, 1]
    cells = util.snap_points(d.lat.to_numpy(float), d.lon.to_numpy(float), country)
    ok = np.array([c is not None for c in cells])
    n_out = int((~ok).sum())
    d = d[ok].copy()

    def types_of(types) -> list[str]:
        return [t for t in str(types or "").split("; ") if t and t != "nan"]

    def kind(types) -> str:
        spec = [t for t in types_of(types) if t != "museum"]
        return sorted(spec, key=len)[0] if spec else "museum"

    # Wikidata files visitor centres, nature centres, heritage railways, archives and virtual museums under
    # museum; drop items whose only museum-type classes are such non-museum facilities
    not_museum = d.types.map(lambda t: bool(types_of(t)) and all(x in NON_MUSEUM_TYPES for x in types_of(t)))
    n_nm = int(not_museum.sum())
    d = d[~not_museum].copy()

    df = pd.DataFrame({
        "id": d.m.map(_qid), "name": d.mLabel, "lat": d.lat.round(6), "lon": d.lon.round(6),
        "kind": d.types.map(kind), "sitelinks": d.sitelinks.astype(int), "wikidata": d.m.map(_qid),
        "count": 1, "museums": 1,
    }).sort_values(["sitelinks", "name"], ascending=[False, True]).reset_index(drop=True)
    print(f"  museums: {len(df)} (dropped {n_out} outside the grid, {n_nm} non-museum facility types); top kinds "
          f"{df.kind.value_counts().head(5).to_dict()}")
    return df


# ---------------------------------------------------------------------------------------------
# Protected areas (WDPCA) -> hex coverage
# ---------------------------------------------------------------------------------------------

KEEP_STATUS = {"Designated", "Inscribed", "Established"}
STRICT_IUCN = {"Ia", "Ib", "II", "III", "IV"}


def _wdpa_usa() -> gpd.GeoDataFrame:
    base = {"where": WDPCA_USA_WHERE, "f": "json"}
    n = fetch_json(WDPCA_FS, "unep_wdpca", "usa/wdpa", "usa_wdpca_count.json",
                   params={**base, "returnCountOnly": "true"})["count"]
    pages = math.ceil(n / WDPCA_PAGE)
    print(f"  WDPCA USA: {n} polygons in {pages} pages", flush=True)
    frames = []
    for i in range(pages):
        p = fetch(WDPCA_FS, "unep_wdpca", "usa/wdpa", f"usa_wdpca_poly_p{i:03d}.geojson", timeout=600, params={
            "where": WDPCA_USA_WHERE, "outFields": "site_id,site_type,name_eng,desig_eng,iucn_cat,status,realm,"
            "gis_area,iso3", "outSR": "4326", "maxAllowableOffset": "0.005", "geometryPrecision": "5",
            "orderByFields": "OBJECTID", "resultOffset": str(i * WDPCA_PAGE),
            "resultRecordCount": str(WDPCA_PAGE), "f": "geojson"})
        g = gpd.read_file(p)
        frames.append(g)
        if (i + 1) % 5 == 0:
            print(f"    page {i + 1}/{pages}", flush=True)
    g = pd.concat(frames, ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
    assert len(g) == n, (len(g), n)
    return g


def _prep_polys(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    g = g[g.status.isin(KEEP_STATUS) & (g.desig_eng != "UNESCO-MAB Biosphere Reserve")].copy()
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    bad = ~g.geometry.is_valid
    if bad.any():
        g.loc[bad, "geometry"] = shapely.make_valid(g.loc[bad, "geometry"].to_numpy())
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon", "GeometryCollection"])]
    g["strict"] = g.iucn_cat.isin(STRICT_IUCN)
    return g.reset_index(drop=True)


def protected_hex(country: str, polys: gpd.GeoDataFrame) -> pd.DataFrame:
    hx = load_hexes(country)
    cells = hx.h3.tolist()
    child = COUNTRIES[country]["h3_res"] + (3 if country == "lka" else 2)
    t0 = time.time()
    pts = util.hex_children_points(cells, child)
    print(f"  protected areas: {len(polys)} polygons ({int(polys.strict.sum())} IUCN Ia-IV); "
          f"{len(pts):,} sample points (res {child})", flush=True)
    # the tree indexes the sample points and the polygons are the query geometries, so shapely
    # prepares each polygon once (much faster than testing unprepared polygons per point)
    P = shapely.points(pts.lon.to_numpy(float), pts.lat.to_numpy(float))
    tree = STRtree(P)
    hit_any = np.zeros(len(P), bool)
    hit_strict = np.zeros(len(P), bool)
    geoms = polys.geometry.to_numpy()
    strict = polys.strict.to_numpy()
    step = 5_000
    for s in range(0, len(geoms), step):
        ig, ip = tree.query(geoms[s:s + step], predicate="intersects")
        hit_any[ip] = True
        hit_strict[ip[strict[s + ig]]] = True
    print(f"    point-in-polygon done ({time.time() - t0:.0f}s)", flush=True)
    g = pd.DataFrame({"h3": pts.h3.to_numpy(), "a": hit_any, "s": hit_strict}).groupby("h3", sort=False).mean()
    out = pd.DataFrame({"h3": cells})
    out["protected_pct"] = (out.h3.map(g.a).fillna(0.0) * 100).round(2)
    out["protected_strict_pct"] = (out.h3.map(g.s).fillna(0.0) * 100).round(2)
    out["protected_km2"] = (out.protected_pct / 100 * hx.hex_km2.to_numpy(float)).round(3)
    return out


# ---------------------------------------------------------------------------------------------
# Tourist accommodation (Sri Lanka)
# ---------------------------------------------------------------------------------------------


def hotel_rooms_lka() -> pd.DataFrame:
    assert sum(SLTDA_ROOMS_2025.values()) == SLTDA_ROOMS_TOTAL
    adm = load_admin("lka", "adm2")
    ids = {_norm(n): a for n, a in zip(adm.name, adm.admin_id)}
    rows = []
    for name, v in SLTDA_ROOMS_2025.items():
        k = _norm(name)
        k = LKA_DISTRICT_ALIASES.get(k, k)
        rows.append((ids[k], v))
    df = pd.DataFrame(rows, columns=["admin_id", "hotel_rooms"])
    assert df.admin_id.nunique() == 25
    return df


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def _report(country: str, res: dict, hx: pd.DataFrame) -> None:
    iso3 = COUNTRIES[country]["iso3"]
    ph = res["protected"].merge(hx[["h3", "ring", "land_km2", "hex_km2"]], on="h3")
    land = ph[ph.ring == 0]
    land_share = float((land.protected_pct * land.land_km2).sum() / land.land_km2.sum())
    strict_share = float((land.protected_strict_pct * land.land_km2).sum() / land.land_km2.sum())
    wb, wb_year = util.wb_value("ER.LND.PTLD.ZS", iso3)
    rep = {
        "heritage_sites": int(len(res["heritage"])),
        "heritage_by_kind": res["heritage"].kind.value_counts().to_dict(),
        "parks": int(len(res["parks"])),
        "park_visitors": float(res["parks"].park_visitors.sum()),
        "top_parks": res["parks"].head(5)[["name", "park_visitors"]].to_dict("records"),
        "museums": int(len(res["museums"])),
        "protected_polygons": int(res["n_polys"]),
        "protected_land_share_pct (land-weighted over land hexes)": round(land_share, 2),
        "protected_strict_land_share_pct": round(strict_share, 2),
        "protected_km2_all_hexes": round(float(ph.protected_km2.sum()), 0),
        "wb_check ER.LND.PTLD.ZS": {"value": wb, "year": wb_year},
    }
    if "rooms" in res:
        rep["hotel_rooms"] = int(res["rooms"].hotel_rooms.sum())
    (interim_dir(country, DOMAIN) / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print("  report:", json.dumps(rep, default=str))


def build(country: str) -> None:
    t0 = time.time()
    print(f"== culture / {country}", flush=True)
    out = interim_dir(country, DOMAIN)
    loc = _Locator(country)
    hx = load_hexes(country)
    res: dict = {}

    res["heritage"] = heritage(country, loc)
    util.write_points(country, DOMAIN, "heritage", res["heritage"])

    wdpa = _wdpa_lka() if country == "lka" else _wdpa_usa()
    res["parks"] = parks_lka(loc, wdpa) if country == "lka" else parks_usa(loc)
    util.write_points(country, DOMAIN, "parks", res["parks"])

    res["museums"] = museums(country, loc)
    util.write_points(country, DOMAIN, "museums", res["museums"])

    polys = _prep_polys(wdpa)
    res["n_polys"] = len(polys)
    res["protected"] = protected_hex(country, polys)
    util.write_hex(country, DOMAIN, res["protected"])

    if country == "lka":
        res["rooms"] = hotel_rooms_lka()
        util.write_admin(country, DOMAIN, "adm2", res["rooms"])

    _report(country, res, hx)
    write_domain_meta(out, METRICS, SOURCES, country)
    print(f"  done ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    from .. import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    for cc in ccs:
        build(cc)
    sys.exit(validate.main([DOMAIN] + ccs))
