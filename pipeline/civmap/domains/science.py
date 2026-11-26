"""Science domain (Science yield, Education lens, university Wonders).

Outputs (data/interim/<country>/science/):
  points_institutions.csv  both: OpenAlex research institutions (universities, hospitals, labs, firms...)
                           with 5-year research output -> metric research_works_5y (summed per hex)
  points_universities.csv  both: kind=education subset (+ US HERD-matched medical institutions), ranked
                           by research output; US rows carry NSF HERD FY2024 R&D (rd_usd)
  admin_adm2.csv           USA: ACS 2020-2024 educational attainment (25+), NCES public schools
                           LKA: Census 2024 district tables (O/L+ share, literacy, computer/digital
                           literacy) + DCS Statistical Abstract 2025 pupils / teachers
  admin_adm3.csv           LKA: Census 2024 DS-division degree holders (Census Data Portal) and Giga
                           school counts
  points_schools.csv       LKA: Giga school geolocations (10,146 government schools)

Research output (OpenAlex): ``works_5y`` is the institution's ``counts_by_year`` total for 2021-2025
(all work types, as published).  Because OpenAlex counts ~1.8 million US *datasets* in 2021-2025
(1.17 M for Pacific Northwest National Laboratory, ~0.45 M non-research items for the University of
Michigan), ``research_works_5y`` is an exact count of research-type works (articles, reviews,
books/chapters, conference papers, preprints, reports, dissertations, letters, data/software papers)
per institution: one cached ``works?filter=authorships.institutions.id:..,type:..`` count query each
(OpenAlex now requires an API key for cursor paging and caps group_by at 200 groups, so per-institution
counts are the only exact key-less route; ~7,000 queries, 8 threads, a few minutes).

    python -m civmap.domains.science        # builds lka + usa, then validates
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import config, schema, util
from ..core import load_admin
from ..fetch import fetch, unzip
from ..schema import Metric, Source

DOMAIN = "science"
science = DOMAIN  # module-shape alias

YEARS = (2021, 2025)                     # last five complete publication years
RESEARCH_TYPES = ("article", "review", "book", "book-chapter", "conference-paper", "preprint",
                  "report", "dissertation", "letter", "data-paper", "software-paper")
US_MIN_WORKS = 200                       # OpenAlex works_count filter for the US institution list

OPENALEX_SELECT = "id,display_name,type,works_count,cited_by_count,ror,geo,counts_by_year"
HERD_TABLE5 = "https://ncses.nsf.gov/pubs/nsf26304/assets/data-tables/tables/nsf26304-tab005.xlsx"
GIGA_CSV = ("https://data.humdata.org/dataset/4f6bd828-21de-403d-80e5-0c18b2d1ac46/resource/"
            "481d95c9-29fb-4432-bd02-a083fc527193/download/sri-lanka-school-geolocation-connectivity-data.csv")
NCES_ZIP = "https://nces.ed.gov/programs/edge/data/EDGE_GEOCODE_PUBLICSCH_2425.zip"
CPH2024_REPORT = "https://www.statistics.gov.lk/Resource/en/Population/CPH_2024/CPH2024_Final_Eng.pdf"
PORTAL = "https://www.statistics.gov.lk/DashBoard/censusdataportal/"
ABSTRACT_14_11 = "https://www.statistics.gov.lk/abstract2025/CHAP14/14.11.pdf"
ACS_YEAR = 2024

# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------

SOURCES = [
    Source("openalex", "OpenAlex: institutions and works (API)", "OurResearch / OpenAlex",
           "https://openalex.org", "CC0", "2021-2025 publications (queried 2026-11)", ["lka", "usa"],
           "Institutions endpoint filtered country_code:LK (all) and country_code:US,works_count:>200 "
           "(US list is truncated at 200 lifetime works), paged with page=N. Coordinates from the "
           "institution's ROR/GeoNames geo record. Research works per institution from "
           "works?filter=authorships.institutions.id:<id>,publication_year:2021-2025,type:"
           + "|".join(RESEARCH_TYPES) + " (meta.count)."),
    Source("nsf_herd_fy2024", "Higher Education Research and Development (HERD) Survey FY 2024, Table 5: "
           "R&D expenditures by state, institutional control, system and institution", "NSF NCSES",
           "https://ncses.nsf.gov/surveys/higher-education-research-development/2024", "public domain",
           "FY2024", ["usa"],
           "Data tables nsf26304 (tab005.xlsx): all R&D expenditures (dollars in thousands) of the standard "
           "and short-form populations. System and control subtotal rows are skipped (spreadsheet "
           "indentation levels); leaf institutions are matched to OpenAlex by name within state."),
    Source("census_acs5_2024", "American Community Survey 5-year estimates 2020-2024, table B15003 "
           "(Educational attainment for the population 25 years and over)", "U.S. Census Bureau",
           "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/",
           "public domain", "2020-2024", ["usa"], "Table-based Summary File, county rows (cb_2023 GEOIDs, "
           "Connecticut planning regions)."),
    Source("nces_edge_publicsch", "EDGE Public School Locations 2024-25 (geocode file)",
           "National Center for Education Statistics (NCES), U.S. Department of Education",
           "https://nces.ed.gov/programs/edge/Geographic/SchoolLocations", "public domain", "2024-25",
           ["usa"], "EDGE_GEOCODE_PUBLICSCH_2425.zip: one record per public school in the CCD universe "
           "with latitude/longitude and 5-digit county (CNTY). Private schools are not included."),
    Source("dcs_cph2024", "Census of Population and Housing 2024 - Final Report (English)",
           "Department of Census and Statistics Sri Lanka",
           "https://www.statistics.gov.lk/Population/StaticalInformation/CPH2024", "open (DCS)", "2024", ["lka"],
           "CPH2024_Final_Eng.pdf: Table 7.6 (educational level of population 25+, national), Table 7.7 "
           "(percentage distribution of population 25+ by educational level and district), Table 7.10 "
           "(language literacy by district, population 10+), Table 7.13 (computer and digital literacy by "
           "district, population 5+). Parsed from the PDF text layer."),
    Source("dcs_census_portal", "Census Data Portal - Census of Population and Housing 2024",
           "Department of Census and Statistics Sri Lanka",
           "https://www.statistics.gov.lk/DashBoard/censusdataportal/", "open (DCS)", "2024", ["lka"],
           "DS-division (ds_table_2024) values of tot_EA (persons 5+), tot_EA_Degreeandabove "
           "('Degree and above', persons 5+), tot_Ag and tot_Ag_0_4..tot_Ag_20_24 (5-year age groups) "
           "returned by the portal's fetch_data.php endpoint; DS codes and names from 2024-dsdivisions-topo.js."),
    Source("dcs_abstract_2025", "Statistical Abstract 2025, Table 14.11: Number of government schools, pupils "
           "and teachers by District 2019-2024", "Department of Census and Statistics Sri Lanka "
           "(data: Ministry of Education school census)",
           "https://www.statistics.gov.lk/abstract2025/CHAP14/14.11.pdf", "open (DCS)", "2024", ["lka"],
           "Trilingual PDF; the English 'Schools / Pupils / Teachers' rows are parsed for 2024."),
    Source("giga_schools_lka", "Sri Lanka: School Geolocation and Connectivity Status", "Giga (UNICEF / ITU) via HDX",
           "https://data.humdata.org/dataset/sri-lanka-school-geolocation-and-connectivity-status",
           "CC BY 4.0", "2024 release", ["lka"],
           "10,146 schools with name, coordinates, education level (Primary / Secondary / Primary and "
           "Secondary) and connectivity (Yes/No). The count equals the Ministry of Education 2021 "
           "government-school total in DCS Statistical Abstract 2025 Table 14.11."),
    Source("worldbank_wdi", "World Development Indicators", "World Bank", "https://data.worldbank.org",
           "CC BY 4.0", "latest", ["lka", "usa"],
           "Used only for the national sanity checks printed by build() (SE.TER.CUAT.BA.ZS, SE.SEC.CUAT.UP.ZS, "
           "SE.SEC.CUAT.LO.ZS, SE.ADT.LITR.ZS, IP.JRN.ARTC.SC); not written to any table."),
]

_RW_METHOD = (
    "OpenAlex institution points. research_works_5y = number of works published 2021-2025 of type "
    + ", ".join(RESEARCH_TYPES) + " with an authorship affiliated to the institution "
    "(works?filter=authorships.institutions.id:<id>,publication_year:2021-2025,type:...; meta.count). "
    "Datasets, peer reviews, errata, editorials, conference abstracts, book reviews, paratext, software and "
    "'other' are excluded: in 2021-2025 OpenAlex lists 1.8 M US datasets, most deposited by a few "
    "institutions (PNNL 1.17 M). Column works_5y keeps the all-types counts_by_year total for reference. "
    "A co-authored work counts once for every participating institution, so sums over hexes or the nation "
    "exceed the number of distinct works; preprints and their published versions can both count. Parent "
    "organisations do not roll up their children in OpenAlex (works are counted on the institution id, "
    "not its lineage), so there is no hierarchy double counting. Reconcile sums the points per hex.")

METRICS = [
    # --- science (both) -----------------------------------------------------------------------
    Metric("research_works_5y", "Research works 2021-2025", "works", "science", "extensive", "none",
           "points_institutions", ["openalex"], "2021-2025",
           "Scholarly works (articles, reviews, books, conference papers, preprints, reports...) published "
           "2021-2025 with an author affiliated to research institutions located in the hex.",
           _RW_METHOD + " Sri Lanka: all 141 OpenAlex institutions with country_code LK. United States: "
           f"institutions with more than {US_MIN_WORKS} lifetime works (6,834 of 35,768), which excludes "
           "only the long tail of small organisations.", "higher"),
    Metric("university_rd_usd", "University R&D expenditure (HERD FY2024)", "USD", "science", "extensive", "none",
           "points_universities", ["nsf_herd_fy2024", "openalex"], "FY2024",
           "Total R&D expenditures of the higher-education institution (all sources of funds).",
           "NSF HERD FY2024 Table 5 'All R&D expenditures' (dollars in thousands x 1000) for leaf "
           "institutions (system and public/private subtotal rows removed by indentation level), matched "
           "to OpenAlex education/healthcare institutions by normalised name within the same state "
           "(abbreviations U./C./Inst./Sch./Med./St. expanded; exact token match first, then "
           "subset-of-name matches without institutional-type words, then fuzzy >= 0.86; one-to-one, "
           "greedy by score then R&D; a few manual overrides listed in science.HERD_OVERRIDES). "
           "Unmatched HERD institutions are not mapped; coverage is printed by build().", "higher",
           ["usa"]),
    # --- education: attainment ------------------------------------------------------------------
    Metric("pct_bachelors", "Adults 25+ with a bachelor's degree or higher", "%", "education", "intensive", "pop",
           "admin_adm2", [f"census_acs5_{ACS_YEAR}"], "2020-2024",
           "Share of the population aged 25 and over whose highest attainment is a bachelor's, master's, "
           "professional or doctorate degree.",
           "ACS 5-year 2020-2024 B15003: (E022+E023+E024+E025) / E001 x 100, county level.", "higher", ["usa"]),
    Metric("pct_bachelors", "Adults 25+ with a degree or higher", "%", "education", "intensive", "pop",
           "admin_adm3", ["dcs_census_portal", "dcs_cph2024"], "2024",
           "Share of the population aged 25 and over holding a university degree or higher (degree, "
           "postgraduate diploma, postgraduate degree, PhD).",
           "Census 2024. The DCS Census Data Portal publishes 'Degree and above' by DS division only for "
           "persons aged 5+. Degree holders aged 25+ per DS division are estimated as the 5+ count x the "
           "national ratio 958,440 / 1,022,588 (= degree+postgraduate diploma+postgraduate degree+PhD aged 25+ "
           "from Final Report Table 7.6, over the national 5+ 'Degree and above' total, i.e. 93.7% of degree "
           "holders are 25+), then divided by the DS population aged 25+ (portal tot_Ag minus the 0-24 "
           "five-year groups). Assumes the 25+ share of degree holders is the same in every DS division. "
           "Portal DS divisions are matched to COD-AB pcodes by name within district (DCS renumbered "
           "several divisions in Nuwara Eliya, Galle, Batticaloa, Ampara and Ratnapura); 'Kalmunai North "
           "Sub' and 'Kalmunai' are summed into COD-AB Kalmunai (LK5221). National result 6.9% equals Table 7.6.",
           "higher", ["lka"]),
    Metric("degree_holders", "Degree holders (25+)", "persons", "education", "extensive", "pop", "admin_adm2",
           [f"census_acs5_{ACS_YEAR}"], "2020-2024", "Persons aged 25+ with a bachelor's degree or higher.",
           "ACS 5-year 2020-2024 B15003 E022+E023+E024+E025, county level; allocated to hexes by population.",
           "higher", ["usa"]),
    Metric("degree_holders", "Degree holders (25+, estimated)", "persons", "education", "extensive", "pop",
           "admin_adm3", ["dcs_census_portal", "dcs_cph2024"], "2024",
           "Persons aged 25+ with a university degree or higher (estimated).",
           "Census 2024 Data Portal 'Degree and above' (persons 5+) per DS division x 0.9373 (national share "
           "aged 25+, Final Report Table 7.6); national total 958,440. The unscaled 5+ count is kept in "
           "column degree_holders_5plus.", "higher", ["lka"]),
    Metric("pct_secondary", "Adults 25+ with high school or higher", "%", "education", "intensive", "pop",
           "admin_adm2", [f"census_acs5_{ACS_YEAR}"], "2020-2024",
           "Share of the population aged 25+ that graduated from high school (diploma or GED) or higher.",
           "ACS 5-year 2020-2024 B15003: sum(E017..E025) / E001 x 100, county level. Definition differs "
           "from Sri Lanka's (G.C.E. O/L is ~grade 11, below a US high-school diploma, roughly ISCED 2-3).",
           "higher", ["usa"]),
    Metric("pct_secondary", "Adults 25+ with G.C.E. O/L or higher", "%", "education", "intensive", "pop",
           "admin_adm2", ["dcs_cph2024"], "2024",
           "Share of the population aged 25+ whose highest educational level is G.C.E. Ordinary Level "
           "(or equivalent) or higher (A/L, degree, postgraduate).",
           "Census 2024 Final Report Table 7.7, 2024 columns 'G.C.E. O/L or equivalent' + 'G.C.E. A/L or "
           "over' (percent of population aged 25+), district level. National 48.2% (22.0 + 26.2). O/L is "
           "taken at ~grade 11, so this is closer to completed lower/upper-secondary than to a US high-school "
           "diploma (12 grades).", "higher", ["lka"]),
    Metric("literacy_rate", "Literacy rate (10+)", "%", "education", "intensive", "pop", "admin_adm2",
           ["dcs_cph2024"], "2024",
           "Share of persons aged 10+ able to read and write (with understanding) in at least one of "
           "Sinhala, Tamil or English.", "Census 2024 Final Report Table 7.10 'At least one language', district.",
           "higher", ["lka"]),
    Metric("english_literacy_rate", "English literacy rate (10+)", "%", "education", "intensive", "pop",
           "admin_adm2", ["dcs_cph2024"], "2024", "Share of persons aged 10+ literate in English.",
           "Census 2024 Final Report Table 7.10 'English' column, district.", "higher", ["lka"]),
    Metric("computer_literacy_rate", "Computer literacy rate (5+)", "%", "education", "intensive", "pop",
           "admin_adm2", ["dcs_cph2024"], "2024",
           "Share of persons aged 5+ who can use a computer on their own (DCS definition).",
           "Census 2024 Final Report Table 7.13, district.", "higher", ["lka"]),
    Metric("digital_literacy_rate", "Digital literacy rate (5+)", "%", "education", "intensive", "pop",
           "admin_adm2", ["dcs_cph2024"], "2024",
           "Share of persons aged 5+ who can use a computer, laptop, tablet or smartphone on their own "
           "(DCS definition).", "Census 2024 Final Report Table 7.13, district.", "higher", ["lka"]),
    # --- education: schools ---------------------------------------------------------------------
    Metric("schools", "Public schools", "count", "education", "extensive", "pop", "admin_adm2",
           ["nces_edge_publicsch"], "2024-25", "Public elementary and secondary schools (NCES CCD universe).",
           "NCES EDGE public school geocodes 2024-25 counted by the file's 5-digit county code (CNTY), "
           "which uses the current county set including the Connecticut planning regions; 50 states + DC "
           "(101,183 schools). Counties with no school get 0. Private schools excluded.", "higher", ["usa"]),
    Metric("schools", "Government schools", "count", "education", "extensive", "pop", "admin_adm3",
           ["giga_schools_lka"], "2024 release",
           "Schools in the Giga school-location register (government schools).",
           "Giga school points assigned to COD-AB DS divisions by point-in-polygon (nearest division for "
           "the few coastal points outside every polygon) and counted. Total 10,146 equals the 2021 MoE "
           "government-school count; DCS Statistical Abstract Table 14.11 reports 10,076 for 2024 "
           "(district totals compared in the build log).", "higher", ["lka"]),
    Metric("schools_online", "Schools with internet connectivity", "count", "education", "extensive", "pop",
           "admin_adm3", ["giga_schools_lka"], "2024 release",
           "Schools reported as connected to the internet by Giga.",
           "Giga connectivity == 'Yes', counted per DS division as for schools.", "higher", ["lka"]),
    Metric("pupils", "Pupils in government schools", "persons", "education", "extensive", "pop", "admin_adm2",
           ["dcs_abstract_2025"], "2024", "Pupils enrolled in government schools.",
           "DCS Statistical Abstract 2025 Table 14.11, 2024 column, district level.", "neutral", ["lka"]),
    Metric("teachers", "Teachers in government schools", "persons", "education", "extensive", "pop",
           "admin_adm2", ["dcs_abstract_2025"], "2024", "Teachers in government schools.",
           "DCS Statistical Abstract 2025 Table 14.11, 2024 column, district level.", "higher", ["lka"]),
]

# ---------------------------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------------------------


def _short_id(u: str | None) -> str | None:
    return u.rsplit("/", 1)[-1] if isinstance(u, str) else None


def _openalex_institutions(country: str) -> list[dict]:
    """All institutions of the country (page-numbered paging: OpenAlex now needs an API key for
    cursor paging, which civmap.util.openalex uses, but page=N works up to 10,000 results)."""
    if country == "lka":
        flt, name = "country_code:LK", "science_inst_lk"
    else:
        flt, name = f"country_code:US,works_count:>{US_MIN_WORKS}", f"science_inst_us_wc{US_MIN_WORKS}"
    out: list[dict] = []
    for page in range(1, 51):
        p = fetch("https://api.openalex.org/institutions", "openalex", "openalex", f"{name}_page{page:02d}.json",
                  params={"filter": flt, "select": OPENALEX_SELECT, "per-page": 200, "page": page})
        js = json.loads(p.read_text())
        res = js.get("results") or []
        out.extend(res)
        if len(res) < 200 or len(out) >= (js.get("meta") or {}).get("count", 0):
            break
    return out


def _research_counts(ids: list[str], cc: str) -> pd.Series:
    """Exact research-type works 2021-2025 per institution (one count query each, cached, 8 threads)."""
    from concurrent.futures import ThreadPoolExecutor
    types = "|".join(RESEARCH_TYPES)

    def one(i: str) -> tuple[str, float]:
        p = fetch("https://api.openalex.org/works", "openalex", "openalex/science_research_counts",
                  f"{cc.lower()}_{YEARS[0]}_{YEARS[1]}_{i}.json",
                  params={"filter": f"authorships.institutions.id:{i},publication_year:{YEARS[0]}-{YEARS[1]},"
                                    f"type:{types}", "per-page": 1, "select": "id"})
        return i, float(json.loads(p.read_text())["meta"]["count"])

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(ex.map(one, ids))
    return pd.Series(res)


def institutions(country: str) -> pd.DataFrame:
    raw = _openalex_institutions(country)
    rows = []
    for r in raw:
        geo = r.get("geo") or {}
        cby = {c["year"]: c.get("works_count") or 0 for c in (r.get("counts_by_year") or [])}
        w5 = sum(v for y, v in cby.items() if YEARS[0] <= y <= YEARS[1])
        rows.append({
            "id": _short_id(r["id"]), "name": r.get("display_name"),
            "lat": geo.get("latitude"), "lon": geo.get("longitude"),
            "kind": r.get("type") or "other", "works_count": r.get("works_count"), "works_5y": w5,
            "cited_by_count": r.get("cited_by_count"), "ror": r.get("ror"),
            "city": geo.get("city"), "region": geo.get("region"),
        })
    df = pd.DataFrame(rows).drop_duplicates("id")
    n0 = len(df)
    df = df[df.lat.notna() & df.lon.notna()]
    lon0, lat0, lon1, lat1 = config.COUNTRIES[country]["bbox"]
    inside = df.lon.between(lon0, lon1) & df.lat.between(lat0, lat1)
    if country == "usa":
        inside |= (df.lon > 170) & (df.lat > 50)   # Aleutians west of the antimeridian
    df = df[inside].copy()
    print(f"  OpenAlex: counting research works for {len(df):,} institutions ...", flush=True)
    df["research_works_5y"] = df.id.map(_research_counts(df.id.tolist(), config.COUNTRIES[country]["iso2"]))
    print(f"  OpenAlex institutions: {n0} fetched, {len(df)} with coordinates in the country; "
          f"works_5y {df.works_5y.sum():,.0f}, research_works_5y {df.research_works_5y.sum():,.0f}", flush=True)
    return df.sort_values("research_works_5y", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# NSF HERD (US university R&D) and name matching
# ---------------------------------------------------------------------------------------------

# HERD leaf name -> OpenAlex id, for pairs the automatic matcher cannot resolve (checked by hand
# against both lists).  None = known to have no counterpart in the OpenAlex list.
HERD_OVERRIDES: dict[tuple[str, str], str | None] = {}

_ABBR = [
    (r"\bU\.", "University"), (r"\bC\.", "College"), (r"\bInst\.", "Institute"), (r"\bSch\.", "School"),
    (r"\bMed\.", "Medicine"), (r"\bCtr\.", "Center"), (r"\bHlth\.", "Health"), (r"\bSci\.", "Science"),
    (r"\bSt\.", "Saint"), (r"\bMt\.", "Mount"), (r"\bTech\.", "Technology"), (r"\bNatl\.", "National"),
]
_STOP = {"the", "of", "at", "in", "and", "for", "a", "an", "main", "campus", "inc"}
_SYN = {"st": "saint", "mt": "mount", "ft": "fort", "univ": "university", "centre": "center",
        "&": "and", "suny": "state university new york", "cuny": "city university new york"}
# words that make two institutions different when only one of the names has them
_TYPE_WORDS = {
    "state", "medical", "medicine", "health", "technology", "technological", "polytechnic", "science",
    "sciences", "center", "college", "hospital", "school", "institute", "community", "research",
    "laboratory", "system", "clinic", "foundation", "seminary", "law", "osteopathic", "nursing", "pharmacy",
    "dental", "art", "arts", "design", "music", "theological", "agricultural", "mechanical", "engineering",
    "cancer", "children", "childrens", "veterans", "graduate", "christian", "baptist", "methodist",
    "lutheran", "catholic", "international", "national", "global", "online", "upstate", "downstate",
    "northern", "southern", "eastern", "western", "central", "north", "south", "east", "west",
    "northeastern", "northwestern", "southeastern", "southwestern", "mid", "a", "m", "am",
}


def _tokens(s: str, herd: bool = False) -> list[str]:
    if herd:
        for pat, rep in _ABBR:
            s = re.sub(pat, rep, s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("a&m", "a and m")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    out = []
    for t in s.split():
        t = _SYN.get(t, t)
        for u in t.split():
            if u not in _STOP:
                out.append(u)
    return out


def _name_score(h: list[str], o: list[str]) -> float:
    A, B = set(h), set(o)
    if not A or not B:
        return 0.0
    if A == B:
        return 1.0
    if B < A and len(B) >= 2 and not ((A - B) & _TYPE_WORDS):
        return 0.92       # HERD adds a place qualifier (e.g. "U. Michigan, Ann Arbor")
    if A < B and len(A) >= 2 and not ((B - A) & _TYPE_WORDS):
        return 0.90       # OpenAlex adds words (e.g. "Rutgers, The State University of New Jersey")
    seq = difflib.SequenceMatcher(None, " ".join(h), " ".join(o)).ratio()
    jac = len(A & B) / len(A | B)
    if (A ^ B) & _TYPE_WORDS:
        seq *= 0.9
    return 0.8 * seq + 0.2 * jac


def herd_institutions() -> pd.DataFrame:
    """Leaf institutions of HERD FY2024 Table 5 with state and total R&D (USD)."""
    import openpyxl
    p = fetch(HERD_TABLE5, "nsf_herd_fy2024", "usa/nsf_herd", "nsf26304-tab005.xlsx")
    ws = openpyxl.load_workbook(p).active
    rows = []
    for r in range(5, ws.max_row + 1):
        c = ws.cell(r, 1)
        v = ws.cell(r, 2).value
        if c.value is None or not isinstance(v, (int, float)):
            continue
        rows.append((str(c.value).strip(), int(c.alignment.indent or 0), float(v)))
    out, state = [], None
    for i, (name, ind, v) in enumerate(rows):
        if ind == 0:
            state = name
            continue
        nxt = rows[i + 1][1] if i + 1 < len(rows) else -1
        if ind >= 2 and nxt <= ind:
            clean = re.sub(r"(?<=[.)])[a-z]{1,2}$", "", name)          # footnote letter after "U."
            clean = re.sub(r"(System|Systems|Center|Campus)[a-d]$", r"\1", clean)
            out.append({"herd_name": clean, "state": state, "rd_usd": v * 1000.0})
    df = pd.DataFrame(out)
    tot = sum(v for n, ind, v in rows if ind == 0)
    print(f"  HERD FY2024 Table 5: {len(df)} leaf institutions, ${df.rd_usd.sum() / 1e9:,.2f} bn "
          f"(state rows sum ${tot * 1000 / 1e9:,.2f} bn)", flush=True)
    return df.sort_values("rd_usd", ascending=False).reset_index(drop=True)


def match_herd(inst: pd.DataFrame, herd: pd.DataFrame) -> pd.DataFrame:
    """Return herd with columns oa_id, match_score (one-to-one, greedy by score then R&D)."""
    cand = inst[inst.kind.isin(["education", "healthcare"])].copy()
    cand["tok"] = [_tokens(n) for n in cand.name]
    by_state = {s: g for s, g in cand.groupby(cand.region.fillna(""))}
    herd = herd.copy()
    herd["tok"] = [_tokens(n, herd=True) for n in herd.herd_name]
    pairs = []
    for i, h in herd.iterrows():
        key = (h.herd_name, h.state)
        if key in HERD_OVERRIDES:
            if HERD_OVERRIDES[key]:
                pairs.append((2.0, h.rd_usd, i, HERD_OVERRIDES[key]))
            continue
        pool = pd.concat([by_state.get(h.state, cand.iloc[:0]), by_state.get("", cand.iloc[:0])])
        for _, o in pool.iterrows():
            s = _name_score(h.tok, o.tok)
            if s >= 0.86:
                pairs.append((s, h.rd_usd, i, o.id))
    pairs.sort(key=lambda t: (-t[0], -t[1]))
    used_h, used_o, res = set(), set(), {}
    for s, _, i, oid in pairs:
        if i in used_h or oid in used_o:
            continue
        used_h.add(i)
        used_o.add(oid)
        res[i] = (oid, min(s, 1.0))
    herd["oa_id"] = [res.get(i, (None, np.nan))[0] for i in herd.index]
    herd["match_score"] = [res.get(i, (None, np.nan))[1] for i in herd.index]
    return herd.drop(columns="tok")


def universities(country: str, inst: pd.DataFrame) -> pd.DataFrame:
    df = inst.copy()
    df["rd_usd"] = np.nan
    df["herd_name"] = None
    df["herd_match_score"] = np.nan
    if country == "usa":
        herd = match_herd(inst, herd_institutions())
        m = herd.dropna(subset=["oa_id"]).set_index("oa_id")
        df["rd_usd"] = df.id.map(m.rd_usd)
        df["herd_name"] = df.id.map(m.herd_name)
        df["herd_match_score"] = df.id.map(m.match_score)
        top = herd.head(100)
        cov = herd.rd_usd[herd.oa_id.notna()].sum() / herd.rd_usd.sum()
        print(f"  HERD matched: {herd.oa_id.notna().sum()} of {len(herd)} institutions, {cov:.1%} of HERD R&D; "
              f"top-100 matched {top.oa_id.notna().sum()}", flush=True)
        miss = top[top.oa_id.isna()]
        if len(miss):
            print("  HERD top-100 unmatched:", "; ".join(f"{r.herd_name} ({r.state})" for r in miss.itertuples()))
        uni = df[(df.kind == "education") | df.rd_usd.notna()].copy()
    else:
        uni = df[df.kind == "education"].copy()
    uni = uni.sort_values(["research_works_5y", "works_5y"], ascending=False).reset_index(drop=True)
    uni["rank"] = np.arange(1, len(uni) + 1)
    uni["university_rd_usd"] = uni.rd_usd
    return uni


# ---------------------------------------------------------------------------------------------
# USA: ACS attainment, NCES schools
# ---------------------------------------------------------------------------------------------


def _usa_admin() -> pd.DataFrame:
    a2 = load_admin("usa", "adm2")[["admin_id", "name"]]
    t = util.acs_table("B15003", ACS_YEAR)
    e = lambda k: t[f"B15003_E{k:03d}"]  # noqa: E731
    pop25 = e(1)
    hs = sum(e(k) for k in range(17, 26))
    ba = sum(e(k) for k in range(22, 26))
    edu = pd.DataFrame({"pop_25plus": pop25, "degree_holders": ba,
                        "pct_bachelors": 100 * ba / pop25, "pct_secondary": 100 * hs / pop25})
    df = a2.merge(edu, left_on="admin_id", right_index=True, how="left")
    miss = df.pct_bachelors.isna().sum()
    if miss:
        print(f"  ACS B15003: {miss} counties without estimates: {df[df.pct_bachelors.isna()].admin_id.tolist()[:10]}")
    print(f"  ACS {ACS_YEAR} 5-yr B15003: pop25+ {df.pop_25plus.sum():,.0f}; bachelor's+ "
          f"{100 * df.degree_holders.sum() / df.pop_25plus.sum():.1f}%; HS+ "
          f"{100 * (df.pct_secondary * df.pop_25plus).sum() / df.pop_25plus.sum() / 100:.1f}%", flush=True)

    z = fetch(NCES_ZIP, "nces_edge_publicsch", "usa/nces", "EDGE_GEOCODE_PUBLICSCH_2425.zip")
    txt = unzip(z, ".TXT")
    cols = ["NCESSCH", "LEAID", "NAME", "OPSTFIPS", "STREET", "CITY", "STATE", "ZIP", "STFIP", "CNTY", "NMCNTY",
            "LOCALE", "LAT", "LON", "CBSA", "NMCBSA", "CBSATYPE", "CSA", "NMCSA", "CD", "SLDL", "SLDU", "SCHOOLYEAR"]
    s = pd.read_csv(txt, sep="|", header=None, names=cols, dtype=str, encoding="latin-1")
    if s.iloc[0].NCESSCH == "NCESSCH":   # header row present in some releases
        s = s.iloc[1:]
    s = s[s.STFIP.isin(config.US_STATE_FIPS)]
    unknown = set(s.CNTY) - set(a2.admin_id)
    if unknown:
        print(f"  NCES: {len(unknown)} county codes not in cb_2023: {sorted(unknown)[:10]} (dropped)")
    df["schools"] = df.admin_id.map(s.CNTY.value_counts()).fillna(0).astype(int)
    print(f"  NCES EDGE 2024-25 public schools: {df.schools.sum():,} in {(df.schools > 0).sum()} counties", flush=True)
    return df.drop(columns="name")


# ---------------------------------------------------------------------------------------------
# Sri Lanka: census report tables, census data portal, abstract, Giga
# ---------------------------------------------------------------------------------------------

_LKA_DISTRICT_ALIASES = {
    "moneragala": "monaragala", "rathnapura": "ratnapura", "mullaittivu": "mullaitivu", "kegalla": "kegalle",
    "nuwaraeliya": "nuwaraeliya", "hambanthota": "hambantota", "digamadulla": "ampara", "puttalama": "puttalam",
    "mulativu": "mullaitivu", "trincomale": "trincomalee",
}


def _dkey(s: str) -> str:
    k = re.sub(r"[^a-z]", "", s.lower())
    return _LKA_DISTRICT_ALIASES.get(k, k)


@lru_cache(maxsize=None)
def _district_ids() -> dict[str, str]:
    a2 = load_admin("lka", "adm2")
    return {_dkey(n): i for i, n in zip(a2.admin_id, a2.name)}


def _num(tok: str) -> float:
    return float(tok.replace(",", "").replace("'", ""))


@lru_cache(maxsize=None)
def _report_pages() -> dict[str, str]:
    """Text of the CPH 2024 report pages holding the tables we need (located with pdfium, read with pdfplumber)."""
    import pdfplumber
    import pypdfium2 as pdfium
    p = fetch(CPH2024_REPORT, "dcs_cph2024", "lka/census2024", "CPH2024_Final_Eng.pdf", timeout=900)
    keys = {"7.6": "Table 7.6 : The Educational Level of the Population",
            "7.7": "Table 7.7 : Percentage Distribution of Population Aged 25",
            "7.10": "Table 7.10 : Language Literacy Rate by Language and District",
            "7.13": "Table 7.13 : Computer and Digital Literacy Rate by District"}
    doc = pdfium.PdfDocument(str(p))
    found: dict[str, int] = {}
    for i in range(len(doc)):
        tx = doc[i].get_textpage().get_text_range()
        for k, title in keys.items():
            j = tx.find(title)
            if k not in found and j >= 0 and "...." not in tx[j: j + 250]:   # skip the list of tables
                found[k] = i
    doc.close()
    missing = set(keys) - set(found)
    if missing:
        raise RuntimeError(f"CPH2024 report: tables not found {missing}")
    out = {}
    with pdfplumber.open(p) as pdf:
        for k, i in found.items():
            out[k] = pdf.pages[i].extract_text() or ""
    return out


def _district_rows(text: str, start: str, ncol: int) -> dict[str, list[float]]:
    """Parse '<District name> n1 n2 ...' lines after the table title; keys are LK adm2 ids."""
    ids = _district_ids()
    seg = text[text.index(start):]
    rows: dict[str, list[float]] = {}
    for line in seg.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z .\-]*?)\s+((?:[\d,]+(?:\.\d+)?\s*){%d})$" % ncol, line.strip())
        if not m:
            continue
        k = _dkey(m.group(1))
        vals = [_num(x) for x in m.group(2).split()]
        if k == "srilanka":
            rows["LK"] = vals
        elif k in ids and ids[k] not in rows:
            rows[ids[k]] = vals
    return rows


def _lka_report_tables() -> tuple[pd.DataFrame, dict]:
    pages = _report_pages()
    t77 = _district_rows(pages["7.7"], "Table 7.7", 14)
    t710 = _district_rows(pages["7.10"], "Table 7.10", 5)
    t713 = _district_rows(pages["7.13"], "Table 7.13", 3)
    for nm, t in (("7.7", t77), ("7.10", t710), ("7.13", t713)):
        if len(t) != 26:
            raise RuntimeError(f"CPH2024 Table {nm}: parsed {len(t)} rows (expected 25 districts + Sri Lanka)")
    df = pd.DataFrame({"admin_id": sorted(k for k in t77 if k != "LK")})
    # Table 7.7 columns: pop25 2012, 2024, total% 2012, 2024, no schooling, grades 1-5, grades 6-10,
    # O/L or equivalent, A/L or over (each 2012, 2024)
    df["pop_25plus_census"] = [t77[i][1] for i in df.admin_id]
    df["pct_secondary"] = [t77[i][11] + t77[i][13] for i in df.admin_id]
    df["pct_al_or_over"] = [t77[i][13] for i in df.admin_id]
    # Table 7.10: pop 10+, at least one language, Sinhala, Tamil, English
    df["literacy_rate"] = [t710[i][1] for i in df.admin_id]
    df["english_literacy_rate"] = [t710[i][4] for i in df.admin_id]
    # Table 7.13: pop 5+, computer literacy, digital literacy
    df["computer_literacy_rate"] = [t713[i][1] for i in df.admin_id]
    df["digital_literacy_rate"] = [t713[i][2] for i in df.admin_id]

    # Table 7.6 (national, 25+): degree + postgraduate diploma + postgraduate degree + PhD, 2024 column
    t76 = pages["7.6"]
    nat = {}
    for lab, key in (("Population 25 years and over", "pop25"), ("Degree", "degree"),
                     ("Post graduate Diploma", "pgdip"), ("Post graduate Degree", "pgdeg"), ("PhD", "phd")):
        m = re.search(r"^\s*" + re.escape(lab) + r"\s+([\d,]+)\s+[\d.]+\s+([\d,]+)\s", t76, re.M)
        if not m:
            raise RuntimeError(f"CPH2024 Table 7.6: row {lab!r} not found")
        nat[key] = _num(m.group(2))
    nat["degree_plus_25"] = nat["degree"] + nat["pgdip"] + nat["pgdeg"] + nat["phd"]
    nat["pct_secondary_national"] = t77["LK"][11] + t77["LK"][13]
    nat["literacy_national"] = t710["LK"][1]
    print(f"  CPH2024 report: 25+ {nat['pop25']:,.0f}, degree+ 25+ {nat['degree_plus_25']:,.0f} "
          f"({100 * nat['degree_plus_25'] / nat['pop25']:.2f}%), O/L+ {nat['pct_secondary_national']:.1f}%, "
          f"literacy {nat['literacy_national']:.1f}%", flush=True)
    return df, nat


# --- Census Data Portal (DS divisions) ------------------------------------------------------

_DS_ALIASES = {   # portal name -> COD-AB name (keys/values before phonetic folding)
    "kalmunainorthsub": "kalmunai",                     # Kalmunai North Sub + Kalmunai = COD-AB Kalmunai
    "kandy4gravetsgangawata": "gangawata",              # COD-AB 'Gangawata Korale' is the same unit
}


def _ds_norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s.lower())
    s = s.replace("&", " and ").replace("four", "4")
    s = re.sub(r"\b(korale|koralaya|and)\b", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    s = _DS_ALIASES.get(s, s)
    for a, b in (("th", "t"), ("dh", "d"), ("ee", "i"), ("oo", "u"), ("w", "v"), ("aa", "a")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


def _portal_ds(indicators: list[str]) -> pd.DataFrame:
    topo = fetch(PORTAL + "2024-dsdivisions-topo.js", "dcs_census_portal", "lka/census_portal",
                 "2024-dsdivisions-topo.json")
    t = json.loads(topo.read_text())
    geoms = t["objects"][list(t["objects"])[0]]["geometries"]
    props = pd.DataFrame([g["properties"] for g in geoms])[["ADM_ID", "ADM_NAME", "BELONGS_TO"]]
    props = props.astype(str)
    ids = props.ADM_ID.tolist()
    for ind in indicators:
        p = fetch(PORTAL + "fetch_data.php", "dcs_census_portal", "lka/census_portal", f"ds_table_2024_{ind}.json",
                  method="POST", data=json.dumps({"year": "2024", "indicator": ind, "ids": ids,
                                                  "table": "ds_table_2024"}),
                  headers={"Content-Type": "application/json"})
        vals = {str(r["ADM_ID"]): r["value"] for r in json.loads(p.read_text())}   # ids come as str or int
        props[ind] = pd.to_numeric(props.ADM_ID.map(vals), errors="coerce")
        if props[ind].isna().any():
            raise RuntimeError(f"census portal {ind}: {int(props[ind].isna().sum())} DS divisions without a value")
    return props


def _match_ds(portal: pd.DataFrame) -> pd.Series:
    """Portal ADM_ID -> COD-AB adm3 pcode, by name within district (one-to-one except aliases)."""
    from scipy.optimize import linear_sum_assignment
    a3 = load_admin("lka", "adm3")[["admin_id", "name", "parent_id"]]
    out: dict[str, str] = {}
    report = []
    for d, pg in portal.groupby("BELONGS_TO"):
        cg = a3[a3.parent_id == "LK" + d]
        cn = {r.admin_id: _ds_norm(r.name) for r in cg.itertuples()}
        left = []
        for r in pg.itertuples():
            n = _ds_norm(r.ADM_NAME)
            hit = [k for k, v in cn.items() if v == n]
            if hit:
                out[r.ADM_ID] = hit[0]
            else:
                left.append(r)
        free = [k for k in cn if k not in set(out.values())]
        if left and free:
            S = np.array([[difflib.SequenceMatcher(None, _ds_norm(r.ADM_NAME), cn[k]).ratio() for k in free]
                          for r in left])
            ri, ci = linear_sum_assignment(-S)
            for a, b in zip(ri, ci):
                out[left[a].ADM_ID] = free[b]
                report.append((left[a].ADM_NAME, cg.set_index("admin_id").name[free[b]], round(S[a, b], 2)))
    unmatched = set(portal.ADM_ID) - set(out)
    if unmatched:
        raise RuntimeError(f"census portal DS divisions unmatched: {sorted(unmatched)}")
    lost = set(a3.admin_id) - set(out.values())
    if lost:
        raise RuntimeError(f"COD-AB DS divisions without portal data: {sorted(lost)}")
    weak = [x for x in report if x[2] < 0.75]
    print(f"  census portal: {len(portal)} DS divisions -> {len(set(out.values()))} COD-AB units; "
          f"{len(report)} fuzzy matches" + (f", weakest: {weak}" if weak else ""), flush=True)
    return pd.Series(out)


def _lka_adm3_education(nat: dict) -> pd.DataFrame:
    young = ["tot_Ag_0_4", "tot_Ag_5_9", "tot_Ag_10_14", "tot_Ag_15_19", "tot_Ag_20_24"]
    p = _portal_ds(["tot_EA", "tot_EA_Degreeandabove", "tot_Ag"] + young)
    p["admin_id"] = p.ADM_ID.map(_match_ds(p))
    p["pop_25plus"] = p.tot_Ag - p[young].sum(axis=1)
    g = p.groupby("admin_id").agg(degree_holders_5plus=("tot_EA_Degreeandabove", "sum"),
                                  pop_5plus=("tot_EA", "sum"), pop_census=("tot_Ag", "sum"),
                                  pop_25plus=("pop_25plus", "sum")).reset_index()
    if abs(g.pop_25plus.sum() - nat["pop25"]) > 0.001 * nat["pop25"]:
        raise RuntimeError(f"census portal 25+ {g.pop_25plus.sum():,.0f} != Table 7.6 {nat['pop25']:,.0f}")
    k = nat["degree_plus_25"] / g.degree_holders_5plus.sum()
    g["degree_holders"] = g.degree_holders_5plus * k
    g["pct_bachelors"] = 100 * g.degree_holders / g.pop_25plus
    print(f"  census portal: pop {g.pop_census.sum():,.0f}, 25+ {g.pop_25plus.sum():,.0f} (Table 7.6: "
          f"{nat['pop25']:,.0f}); degree+ (5+) {g.degree_holders_5plus.sum():,.0f}; 25+ share k={k:.4f}; "
          f"national degree+ 25+ {100 * g.degree_holders.sum() / g.pop_25plus.sum():.2f}%", flush=True)
    return g


def _lka_abstract() -> pd.DataFrame:
    import pdfplumber
    p = fetch(ABSTRACT_14_11, "dcs_abstract_2025", "lka/dcs_abstract2025", "abstract2025_14.11.pdf")
    ids = _district_ids()
    rows: dict[str, dict] = {}
    cur = None
    with pdfplumber.open(p) as pdf:
        lines = [ln for pg in pdf.pages for ln in (pg.extract_text() or "").splitlines()]
    for ln in lines:
        if re.search(r"Grand Total\s*$", ln):
            cur = "LK"
            continue
        m = re.search(r"\b(Schools|Pupils|Teachers)\b(.*)$", ln)
        if m and cur:
            nums = re.findall(r"\d[\d,']*", re.sub(r"\(\d\)", "", m.group(2)))
            if len(nums) >= 6:
                rows.setdefault(cur, {})[m.group(1).lower()] = _num(nums[-1])   # last column = 2024
            continue
        # district header rows end with the English name after the Sinhala and Tamil ones
        words = ln.split()
        for cand in (" ".join(words[-2:]), " ".join(words[-1:])):
            if _dkey(cand) in ids:
                cur = ids[_dkey(cand)]
                break
    df = pd.DataFrame.from_dict(rows, orient="index")
    nat = df.loc["LK"] if "LK" in df.index else None
    df = df.drop(index="LK", errors="ignore")
    if len(df) != 25 or df.isna().any().any():
        raise RuntimeError(f"Abstract Table 14.11: parsed {len(df)} districts:\n{df}")
    if nat is not None:
        for c in ("schools", "pupils", "teachers"):
            if abs(df[c].sum() - nat[c]) > 0.002 * nat[c]:
                print(f"  WARNING Table 14.11 {c}: districts sum {df[c].sum():,.0f} != total {nat[c]:,.0f}")
    print(f"  Abstract 14.11 (2024): schools {df.schools.sum():,.0f}, pupils {df.pupils.sum():,.0f}, "
          f"teachers {df.teachers.sum():,.0f}", flush=True)
    df.index.name = "admin_id"
    return df.rename(columns={"schools": "gov_schools_moe_2024"}).reset_index()


def _lka_schools() -> tuple[pd.DataFrame, pd.DataFrame]:
    import geopandas as gpd
    p = fetch(GIGA_CSV, "giga_schools_lka", "lka/schools", "sri-lanka-school-geolocation-connectivity-data.csv")
    s = pd.read_csv(p)
    s = s.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    pts = pd.DataFrame({
        "id": [f"giga_lka_{i:05d}" for i in range(len(s))],
        "name": s.school_name.str.strip().str.title(),
        "lat": s.latitude, "lon": s.longitude,
        "kind": s.education_level.fillna("Unknown"),
        "connectivity": s.connectivity,
    })
    a3 = load_admin("lka", "adm3")[["admin_id", "geometry"]]
    g = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.lon, pts.lat), crs=4326)
    j = gpd.sjoin(g, a3, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    out = j.admin_id.isna()
    if out.any():
        near = gpd.sjoin_nearest(g[out].to_crs(5235), a3.to_crs(5235), how="left")
        near = near[~near.index.duplicated()]
        j.loc[out, "admin_id"] = near.admin_id
    pts["adm3"] = j.admin_id.values
    cnt = pts.groupby("adm3").agg(schools=("id", "size"),
                                  schools_online=("connectivity", lambda x: int((x == "Yes").sum())))
    adm = load_admin("lka", "adm3")[["admin_id"]].copy()
    adm["schools"] = adm.admin_id.map(cnt.schools).fillna(0).astype(int)
    adm["schools_online"] = adm.admin_id.map(cnt.schools_online).fillna(0).astype(int)
    print(f"  Giga: {len(pts):,} schools ({int(out.sum())} snapped to nearest DS division), "
          f"{adm.schools_online.sum():,} connected", flush=True)
    return pts, adm


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def _sanity(country: str, inst: pd.DataFrame, adm: pd.DataFrame | None, pct_b: float, pct_s: float) -> None:
    iso = config.COUNTRIES[country]["iso3"]
    for ind, label, mine in (("SE.TER.CUAT.BA.ZS", "bachelor's+ 25+ (WB)", pct_b),
                             ("SE.SEC.CUAT.UP.ZS", "upper secondary+ 25+ (WB)", pct_s),
                             ("SE.SEC.CUAT.LO.ZS", "lower secondary+ 25+ (WB)", pct_s)):
        try:
            v, y = util.wb_value(ind, iso)
        except Exception as e:  # noqa: BLE001 - sanity check only
            v, y = None, str(e)[:60]
        print(f"  check {label}: WB {v if v is None else round(v, 1)} ({y}) vs ours {mine:.1f}")
    try:
        v, y = util.wb_value("IP.JRN.ARTC.SC", iso)
        print(f"  check S&E journal articles/yr (WB IP.JRN.ARTC.SC {y}): {v:,.0f} vs ours research_works_5y/5 "
              f"{inst.research_works_5y.sum() / 5:,.0f} (ours counts co-authored works once per institution "
              f"and includes conference papers, books, preprints)")
    except Exception:  # noqa: BLE001
        pass


def build(country: str) -> None:
    print(f"[{DOMAIN}] building {country}", flush=True)
    inst = institutions(country)
    uni = universities(country, inst)
    cols = ["id", "name", "lat", "lon", "kind", "works_count", "works_5y", "research_works_5y",
            "cited_by_count", "ror", "city", "region"]
    util.write_points(country, DOMAIN, "institutions", inst[cols])
    util.write_points(country, DOMAIN, "universities",
                      uni[cols + ["rank", "rd_usd", "university_rd_usd", "herd_name", "herd_match_score"]])
    print("  top universities: " + "; ".join(f"{r.rank}. {r.name} ({r.research_works_5y:,.0f})"
                                             for r in uni.head(5).itertuples()), flush=True)

    if country == "usa":
        adm = _usa_admin()
        util.write_admin(country, DOMAIN, "adm2", adm)
        pct_b = 100 * adm.degree_holders.sum() / adm.pop_25plus.sum()
        pct_s = (adm.pct_secondary * adm.pop_25plus).sum() / adm.pop_25plus.sum()
        _sanity(country, inst, adm, pct_b, pct_s)
    else:
        d2, nat = _lka_report_tables()
        ab = _lka_abstract()
        d2 = d2.merge(ab, on="admin_id", how="left")
        pts, sch = _lka_schools()
        d3 = _lka_adm3_education(nat).merge(sch, on="admin_id", how="outer")
        # district check: Giga vs MoE 2024 school counts
        giga_d = pts.groupby(pts.adm3.str[:4]).size()
        cmp = d2.set_index("admin_id").gov_schools_moe_2024
        diff = (giga_d.reindex(cmp.index) - cmp)
        print(f"  Giga vs MoE 2024 schools by district: max |diff| {diff.abs().max():.0f} "
              f"({diff.abs().idxmax()}), total {giga_d.sum():,} vs {cmp.sum():,.0f}", flush=True)
        # district check: estimated degree share must stay below the census A/L-or-over share
        g3 = d3.groupby(d3.admin_id.str[:4])[["degree_holders", "pop_25plus"]].sum()
        deg_d = 100 * g3.degree_holders / g3.pop_25plus
        al = d2.set_index("admin_id").pct_al_or_over
        bad = deg_d[deg_d > al.reindex(deg_d.index)]
        if len(bad):
            print(f"  WARNING degree share exceeds A/L-or-over share in {bad.index.tolist()}")
        print(f"  district degree+ share {deg_d.min():.1f}% ({deg_d.idxmin()}) .. {deg_d.max():.1f}% "
              f"({deg_d.idxmax()}); A/L-or-over {al.min():.1f}..{al.max():.1f}%", flush=True)
        util.write_admin(country, DOMAIN, "adm2", d2)
        util.write_admin(country, DOMAIN, "adm3", d3)
        util.write_points(country, DOMAIN, "schools", pts)
        pct_b = 100 * d3.degree_holders.sum() / d3.pop_25plus.sum()
        _sanity(country, inst, d3, pct_b, nat["pct_secondary_national"])
        try:
            v, y = util.wb_value("SE.ADT.LITR.ZS", "LKA")
            print(f"  check adult literacy 15+ (WB {y}): {v:.1f} vs census 10+ {nat['literacy_national']:.1f}")
        except Exception:  # noqa: BLE001
            pass
    schema.write_domain_meta(config.interim_dir(country, DOMAIN), METRICS, SOURCES, country)


if __name__ == "__main__":
    import sys

    from .. import validate

    for cc in ("lka", "usa"):
        build(cc)
    sys.exit(validate.main([DOMAIN, "lka", "usa"]))
