"""National domain: World Bank indicators for the Advisors + the metro list for City View.

This domain does not use the admin/hex contract (METRICS is empty).  It writes two global files
that the civ and export stages read directly:

Outputs (data/interim/national/):
  indicators.json  list of {code, label, name, unit, advisor, yield, better, description,
                   source_org, source, derived, method, LKA: {value, year, series}, USA: {...},
                   common: {year, LKA, USA, ratio_usa_lka} | null}
                   -- World Bank WDI, latest non-null value per country, series = the last 15 years
                   of the indicator (window ending at the latest year either country reports).
                   ratio_usa_lka is null unless both values are > 0. requested_code differs from
                   code when an archived code was replaced (RENAMED).
  metros.json      City View list: 10 Sri Lankan cities (Wikidata centre + population as stated on
                   Wikidata, with its reference and a reference-kind flag; district / DS division by
                   point-in-polygon; official Census 2024 total and urban-sector population of the DS
                   division holding the centre) and the 20 most populous US Metropolitan Statistical Areas
                   (Census Vintage 2025 CBSA estimates, OMB July 2023 delineation counties,
                   principal-city centre from Wikidata, metro GDP = sum of BEA county GDP 2024).
  sources.json     Source records of this domain.
  report.json      sanity checks (WDI vs BEA / COD-PS totals, member-county consistency, ...).
Also data/interim/<cc>/national/{metrics.json, sources.json} so `civmap.validate national` works.

Run:  python -m civmap.domains.national        (builds lka + usa, then validates)
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import util
from ..config import COUNTRIES, INTERIM, US_STATE_FIPS, interim_dir
from ..core import SOURCES as CORE_SOURCES
from ..core import load_admin
from ..fetch import fetch, unzip
from ..schema import Metric, Source, write_domain_meta

DOMAIN = "national"
national = DOMAIN

NAT_DIR = INTERIM / "national"
SERIES_YEARS = 15
ISO = {"lka": "LKA", "usa": "USA"}

# ---------------------------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------------------------

WDI_CATALOG = "https://api.worldbank.org/v2/source/2/indicator"
CBSA_POP = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/"
            "cbsa-est2025-alldata.csv")
DELIN = "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/"
DELIN_LIST1 = DELIN + "list1_2023.xlsx"
DELIN_LIST2 = DELIN + "list2_2023.xlsx"
GAZ_PLACES = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/"
              "2025_Gaz_place_national.zip")
BEA_CAGDP1 = "https://apps.bea.gov/regional/zip/CAGDP1.zip"
N_US_METROS = 20
WDI_ARCHIVE_META = "https://api.worldbank.org/v2/indicator/{code}"   # resolves archived codes (source 57)
PORTAL = "https://www.statistics.gov.lk/DashBoard/censusdataportal/"
DCS_ABSTRACT_10_2 = "https://www.statistics.gov.lk/abstract2025/CHAP10/10.2.pdf"
CEB_AR_2023 = ("https://www.dropbox.com/scl/fi/xzi7k1ntby3y96ip4nimc/3.-CEB-Annual-Report-2023-English.pdf"
               "?rlkey=kbjxu6hj4pwdz2it3d7vfuz03&st=48utl0ii&dl=1")

SOURCES = [
    Source("worldbank_wdi", "World Development Indicators (WDI)", "The World Bank",
           "https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators",
           "CC BY 4.0", "1960-2025 (latest year varies by indicator)", ["lka", "usa"],
           "Read through the Indicators API v2 (api.worldbank.org/v2/country/LKA;USA/indicator/<code>, "
           "all years) and the WDI indicator catalogue (api.worldbank.org/v2/source/2/indicator) for "
           "names, definitions (sourceNote) and original publishers (sourceOrganization). Several series "
           "are produced by other agencies and redistributed in WDI: ILO modelled estimates (labour), "
           "SIPRI (military expenditure, arms transfers), IISS (armed forces), UNESCO UIS (education, "
           "R&D), UN Tourism (tourism), IEA (energy use and electricity mix), ESMAP (electricity access), "
           "EDGAR (emissions), FAO (food, fisheries, land), IDMC (displacement); each record keeps WDI's own "
           "sourceOrganization text. EG.ELC.PROD.KH (total electricity production) is archived in WDI "
           "(WDI Database Archives, source 57: metadata only, no data served) and is derived here as "
           "EG.ELC.RNWX.KH / (EG.ELC.RNWX.ZS / 100); its definition is the archive's sourceNote."),
    Source("wikidata", "Wikidata (coordinate location P625, population P1082 with point in time P585, "
           "GNIS ID P590)", "Wikimedia Foundation / Wikidata contributors", "https://www.wikidata.org",
           "CC0 1.0", "accessed 2026-11", ["lka", "usa"],
           "SPARQL endpoint query.wikidata.org. Sri Lankan city items were resolved once by English "
           "label within country P17 = Sri Lanka (ISO P298 'LKA'); US principal cities are resolved "
           "from the Census GNIS code (Gazetteer ANSICODE) via P590. Population statement = preferred "
           "rank, else the most recent point in time; its reference URL / 'stated in' is kept."),
    Source("census_pep_cbsa_2025", "Annual Resident Population Estimates for Metropolitan and "
           "Micropolitan Statistical Areas and Their Component Counties: April 1, 2020 to July 1, 2025 "
           "(CBSA-EST2025-ALLDATA, Vintage 2025)", "U.S. Census Bureau, Population Estimates Program",
           "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/",
           "public domain", "2025 (July 1)", ["usa"],
           "Metropolitan Statistical Area rows (LSAD = 'Metropolitan Statistical Area'); "
           "POPESTIMATE2025 used for ranking, POPESTIMATE2024 for GDP per capita."),
    Source("census_delineation_2023", "Core Based Statistical Areas (CBSAs), Metropolitan Divisions, "
           "and Combined Statistical Areas (CSAs), July 2023 - delineation List 1 (counties) and "
           "List 2 (principal cities)", "U.S. Census Bureau / Office of Management and Budget (OMB "
           "Bulletin No. 23-01)",
           "https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html",
           "public domain", "2023-07", ["usa"],
           "List 1 gives the member counties (FIPS state + county; Connecticut planning regions) of "
           "each CBSA; List 2 gives principal cities with FIPS place codes."),
    Source("census_gazetteer_2025", "2025 Gazetteer Files - Places (national)", "U.S. Census Bureau",
           "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
           "public domain", "2025", ["usa"],
           "Used only to translate principal-city FIPS place codes to GNIS feature ids (ANSICODE), "
           "which identify the Wikidata items."),
    Source("bea_cagdp1", "CAGDP1: County gross domestic product (GDP) summary, 2001-2024",
           "U.S. Bureau of Economic Analysis",
           "https://apps.bea.gov/regional/downloadzip.htm", "public domain",
           "2024 (released 2026-02-05)", ["usa"],
           "LineCode 3 'Current-dollar GDP (thousands of current dollars)'. Counties are BEA units: "
           "Virginia independent cities are combined with surrounding counties (e.g. 51919 'Fairfax, "
           "Fairfax City + Falls Church'); Hawaii 15901 = Maui + Kalawao; Connecticut is published by "
           "planning region from 2024 data. The ALL_AREAS file has no MSA rows, so metro GDP is the "
           "sum of member-county GDP (BEA's own MSA definition)."),
    Source("dcs_census_portal", "Census Data Portal - Census of Population and Housing 2024 (DS-division tables)",
           "Department of Census and Statistics Sri Lanka", PORTAL, "open (DCS)", "2024", ["lka"],
           "fetch_data.php, table ds_table_2024, indicators tot_Ag (total population) and tot_Sec_Ur (urban "
           "sector population: CPH 2024 defines the urban sector as areas under a Municipal Council or an Urban "
           "Council that are not classified as Estate Urban) for the DS division whose 2024 polygon "
           "(2024-dsdivisions-topo.js) contains each city centre. Portal sums over the 340 DS divisions equal the "
           "CPH 2024 Final Report Table 3.6 (total 21,781,800; urban 3,807,135); build() re-checks the total."),
    Source("dcs_abstract2025", "Statistical Abstract 2025, Table 10.2: Gross Domestic Product by major economic "
           "activity at current market prices 2019-2024 (Rs. million)",
           "Department of Census and Statistics Sri Lanka", DCS_ABSTRACT_10_2, "open (DCS)", "2019-2024",
           ["lka"], "Row 'Equals GDP at market price', used only to cross-check WDI NY.GDP.MKTP.CD x PA.NUS.FCRF."),
    Source("ceb_annual_report_2023", "Ceylon Electricity Board Annual Report 2023 (English)",
           "Ceylon Electricity Board", CEB_AR_2023, "CEB publication", "2019-2023", ["lka"],
           "Figure FR4 'Generation and Sales (GWh)' gross generation labels 2019-2023, used only to cross-check "
           "the derived EG.ELC.PROD.KH for Sri Lanka."),
] + [s for s in CORE_SOURCES if s.id in ("ocha_codab_lka", "unfpa_codps_lka", "census_cb_2023")]

METRICS: list[Metric] = []   # national-level indicators live in indicators.json, not in admin/hex tables

# ---------------------------------------------------------------------------------------------
# Indicator list: code, advisor, civ yield it relates to (or None), better
# ---------------------------------------------------------------------------------------------

E, M, C, S, V = "economic", "military", "cultural", "scientific", "environmental"
INDICATORS: list[tuple[str, str, str | None, str]] = [
    # economy & incomes (Gold)
    ("NY.GDP.MKTP.CD", E, "gold", "higher"),
    ("NY.GDP.PCAP.CD", E, "gold", "higher"),
    ("NY.GDP.MKTP.PP.CD", E, "gold", "higher"),
    ("NY.GDP.PCAP.PP.CD", E, "gold", "higher"),
    ("NY.GNP.MKTP.CD", E, "gold", "higher"),
    ("NY.GNP.PCAP.CD", E, "gold", "higher"),
    ("NY.GNP.PCAP.PP.CD", E, "gold", "higher"),
    ("NY.GDP.MKTP.KD.ZG", E, "gold", "higher"),
    ("FP.CPI.TOTL.ZG", E, None, "neutral"),
    ("PA.NUS.FCRF", E, None, "neutral"),
    ("PA.NUS.PPP", E, None, "neutral"),
    # structure of the economy (Production / Food)
    ("NV.AGR.TOTL.ZS", E, "food", "neutral"),
    ("NV.IND.TOTL.ZS", E, "production", "neutral"),
    ("NV.IND.MANF.ZS", E, "production", "neutral"),
    ("NV.SRV.TOTL.ZS", E, "gold", "neutral"),
    ("NY.GDP.TOTL.RT.ZS", E, None, "neutral"),
    # trade & external
    ("NE.EXP.GNFS.ZS", E, "gold", "neutral"),
    ("NE.IMP.GNFS.ZS", E, "gold", "neutral"),
    ("NE.EXP.GNFS.CD", E, "gold", "higher"),
    ("NE.IMP.GNFS.CD", E, "gold", "neutral"),
    ("BN.CAB.XOKA.GD.ZS", E, "gold", "neutral"),
    ("BX.KLT.DINV.WD.GD.ZS", E, "gold", "higher"),
    ("BX.TRF.PWKR.CD.DT", E, "gold", "neutral"),
    ("BX.TRF.PWKR.DT.GD.ZS", E, "gold", "neutral"),
    ("GC.DOD.TOTL.GD.ZS", E, "gold", "lower"),
    ("IS.SHP.GOOD.TU", E, "gold", "higher"),
    ("IS.AIR.PSGR", E, None, "higher"),
    ("IS.AIR.GOOD.MT.K1", E, None, "higher"),
    ("IS.RRS.TOTL.KM", E, None, "higher"),
    ("LP.LPI.OVRL.XQ", E, None, "higher"),
    # labour (Citizens)
    ("SL.TLF.TOTL.IN", E, None, "neutral"),
    ("SL.TLF.CACT.ZS", E, None, "higher"),
    ("SL.TLF.CACT.FE.ZS", E, None, "higher"),
    ("SL.UEM.TOTL.ZS", E, None, "lower"),
    ("SL.AGR.EMPL.ZS", E, "food", "neutral"),
    ("SL.IND.EMPL.ZS", E, "production", "neutral"),
    ("SL.SRV.EMPL.ZS", E, "gold", "neutral"),
    # inequality & poverty
    ("SI.POV.GINI", E, None, "lower"),
    ("SI.POV.DDAY", E, None, "lower"),
    ("SI.POV.NAHC", E, None, "lower"),
    # food & fisheries (Food)
    ("AG.PRD.FOOD.XD", E, "food", "higher"),
    ("AG.PRD.CREL.MT", E, "food", "higher"),
    ("AG.YLD.CREL.KG", E, "food", "higher"),
    ("ER.FSH.PROD.MT", E, "food", "higher"),
    ("ER.FSH.CAPT.MT", E, "food", "higher"),
    ("ER.FSH.AQUA.MT", E, "food", "higher"),
    # military
    ("MS.MIL.XPND.CD", M, None, "neutral"),
    ("MS.MIL.XPND.GD.ZS", M, None, "neutral"),
    ("MS.MIL.XPND.ZS", M, None, "neutral"),
    ("MS.MIL.TOTL.P1", M, None, "neutral"),
    ("MS.MIL.TOTL.TF.ZS", M, None, "neutral"),
    ("MS.MIL.MPRT.KD", M, None, "neutral"),
    ("MS.MIL.XPRT.KD", M, None, "neutral"),
    ("VC.IHR.PSRC.P5", M, None, "lower"),
    # science, technology & education (Science)
    ("GB.XPD.RSDV.GD.ZS", S, "science", "higher"),
    ("SP.POP.SCIE.RD.P6", S, "science", "higher"),
    ("IP.JRN.ARTC.SC", S, "science", "higher"),
    ("IP.PAT.RESD", S, "science", "higher"),
    ("IP.PAT.NRES", S, "science", "neutral"),
    ("IP.TMK.RSCT", S, None, "higher"),
    ("TX.VAL.TECH.MF.ZS", S, "science", "higher"),
    ("TX.VAL.TECH.CD", S, "science", "higher"),
    ("NV.MNF.TECH.ZS.UN", S, "production", "higher"),
    ("BX.GSR.CCIS.CD", S, "science", "higher"),
    ("BX.GSR.ROYL.CD", S, "science", "higher"),
    ("SE.TER.ENRR", S, "science", "higher"),
    ("SE.TER.CUAT.ST.ZS", S, "science", "higher"),
    ("SE.TER.CUAT.BA.ZS", S, "science", "higher"),
    ("SE.ADT.LITR.ZS", S, "science", "higher"),
    ("SE.XPD.TOTL.GD.ZS", S, "science", "higher"),
    ("IT.NET.USER.ZS", S, "science", "higher"),
    ("IT.NET.BBND.P2", S, None, "higher"),
    ("IT.CEL.SETS.P2", S, None, "higher"),
    # tourism, people & society (Culture)
    ("ST.INT.ARVL", C, "culture", "higher"),
    ("ST.INT.RCPT.CD", C, "culture", "higher"),
    ("ST.INT.RCPT.XP.ZS", C, "culture", "neutral"),
    ("ST.INT.DPRT", C, None, "neutral"),
    ("ST.INT.XPND.CD", C, None, "neutral"),
    ("SP.POP.TOTL", C, None, "neutral"),
    ("SP.POP.GROW", C, None, "neutral"),
    ("EN.POP.DNST", C, None, "neutral"),
    ("SP.URB.TOTL.IN.ZS", C, None, "neutral"),
    ("EN.URB.LCTY", C, None, "neutral"),
    ("SP.DYN.LE00.IN", C, None, "higher"),
    ("SP.DYN.TFRT.IN", C, None, "neutral"),
    ("SP.POP.65UP.TO.ZS", C, None, "neutral"),
    ("SH.DYN.MORT", C, None, "lower"),
    ("SH.XPD.CHEX.GD.ZS", C, None, "neutral"),
    ("SH.XPD.CHEX.PC.CD", C, None, "higher"),
    # land, energy, emissions, water & disasters
    ("AG.SRF.TOTL.K2", V, None, "neutral"),
    ("AG.LND.FRST.ZS", V, None, "higher"),
    ("AG.LND.ARBL.ZS", V, "food", "neutral"),
    ("AG.LND.AGRI.ZS", V, "food", "neutral"),
    ("ER.PTD.TOTL.ZS", V, None, "higher"),
    ("EG.ELC.ACCS.ZS", V, None, "higher"),
    ("EG.ELC.PROD.KH", V, "production", "higher"),      # derived (archived in WDI)
    ("EG.USE.PCAP.KG.OE", V, "production", "neutral"),
    ("EG.USE.ELEC.KH.PC", V, "production", "neutral"),
    ("EG.FEC.RNEW.ZS", V, None, "higher"),
    ("EG.ELC.RNEW.ZS", V, None, "higher"),
    ("EG.ELC.FOSL.ZS", V, None, "lower"),
    ("EG.ELC.COAL.ZS", V, None, "lower"),
    ("EG.ELC.HYRO.ZS", V, None, "neutral"),
    ("EG.ELC.NUCL.ZS", V, None, "neutral"),
    ("EN.ATM.CO2E.PC", V, None, "lower"),               # archived -> EN.GHG.CO2.PC.CE.AR5 (RENAMED)
    ("EN.GHG.CO2.MT.CE.AR5", V, None, "lower"),
    ("EN.GHG.ALL.PC.CE.AR5", V, None, "lower"),
    ("EN.GHG.ALL.MT.CE.AR5", V, None, "lower"),
    ("EN.ATM.PM25.MC.M3", V, None, "lower"),
    ("ER.H2O.FWTL.K3", V, None, "neutral"),
    ("ER.H2O.FWST.ZS", V, None, "lower"),
    ("ER.H2O.INTR.PC", V, None, "higher"),
    ("EN.CLC.MDAT.ZS", V, None, "lower"),
    ("VC.IDP.NWDS", V, None, "lower"),
    ("EN.CLC.DRSK.XQ", V, None, "higher"),
]

# Archived / renamed codes: requested code -> current WDI code (checked against the catalogue)
RENAMED = {"EN.ATM.CO2E.PC": "EN.GHG.CO2.PC.CE.AR5"}

# Units for names without a parenthetical unit (WDI metadata has an empty `unit` field)
UNIT_OVERRIDE = {
    "SP.POP.TOTL": "persons", "SL.TLF.TOTL.IN": "persons", "MS.MIL.TOTL.P1": "persons",
    "EN.URB.LCTY": "persons", "ST.INT.ARVL": "arrivals", "ST.INT.DPRT": "departures",
    "IS.AIR.PSGR": "passengers", "IP.JRN.ARTC.SC": "articles", "IP.PAT.RESD": "applications",
    "IP.PAT.NRES": "applications", "SI.POV.GINI": "index (0-100)", "IP.TMK.RSCT": "applications",
    "EG.ELC.PROD.KH": "kWh", "ER.H2O.FWST.ZS": "%", "AG.PRD.FOOD.XD": "index (2014-2016 = 100)",
    "LP.LPI.OVRL.XQ": "index (1=low to 5=high)", "EN.CLC.DRSK.XQ": "score (1-5; 5=best)",
    "IS.RRS.TOTL.KM": "route-km",
}
# parenthetical groups that qualify the concept rather than give the unit
QUALIFIER_RE = re.compile(r"^(modeled ILO estimate|DOD|national estimate|cumulative|including construction|"
                          r"total|CO2|CH4|N2O|\d{4} PPP)$")

# name / description / original publisher of a derived code come from the WDI archive metadata
# (api.worldbank.org/v2/indicator/<code>, source 57 "WDI Database Archives"), fetched at build time.
DERIVED = {
    "EG.ELC.PROD.KH": {
        "label": "Electricity production",
        "from": ("EG.ELC.RNWX.KH", "EG.ELC.RNWX.ZS"),
        "method": ("EG.ELC.PROD.KH is archived in WDI (source 57, metadata only, no data served). Derived as "
                   "EG.ELC.RNWX.KH (electricity production from renewables excl. hydro, kWh) / "
                   "(EG.ELC.RNWX.ZS / 100) (the same quantity as a % of total electricity production), for "
                   "years with a share >= 1% so rounding of the share stays negligible. Both inputs are IEA "
                   "series (WDI sourceOrganization), as was the archived series."),
    },
}


# ---------------------------------------------------------------------------------------------
# World Bank indicators
# ---------------------------------------------------------------------------------------------

def _wdi_catalog() -> dict[str, dict]:
    p = fetch(WDI_CATALOG, "worldbank_wdi", "worldbank", "wdi_indicator_catalog_source2.json",
              params={"format": "json", "per_page": 20000})
    js = json.loads(p.read_text())
    return {r["id"]: r for r in js[1]}


def _wdi_archive_meta(code: str) -> dict:
    """Metadata record of an archived WDI code (name, sourceNote, sourceOrganization, source)."""
    p = fetch(WDI_ARCHIVE_META.format(code=code), "worldbank_wdi", "worldbank", f"{code}_archive_meta.json",
              params={"format": "json"})
    js = json.loads(p.read_text())
    if not isinstance(js, list) or len(js) < 2 or not js[1]:
        raise RuntimeError(f"WDI archive metadata for {code} not found")
    return js[1][0]


def _split_unit(code: str, name: str) -> tuple[str, str]:
    """('GDP (current US$)') -> ('GDP', 'current US$'); unit = first non-qualifier parenthesis."""
    groups = re.findall(r"\(([^()]*)\)", name)
    unit = next((g for g in groups if not QUALIFIER_RE.match(g.strip())), "")
    label = name
    if unit:
        label = name.replace(f"({unit})", "").strip()
    label = re.sub(r"\s+", " ", label).strip(" ,")
    if code in UNIT_OVERRIDE:
        unit = UNIT_OVERRIDE[code]
    return label, unit


def _clean(v: float) -> float:
    return float(f"{float(v):.6g}")


def _series(code: str) -> pd.DataFrame:
    if code in DERIVED:
        a, b = DERIVED[code]["from"]
        x = util.wb_series(a).merge(util.wb_series(b), on=["iso3", "year"], suffixes=("_kh", "_zs"))
        x = x[x.value_zs >= 1.0]
        x["value"] = x.value_kh / (x.value_zs / 100.0)
        return x[["iso3", "year", "value"]]
    return util.wb_series(code)


def build_indicators() -> tuple[list[dict], dict]:
    cat = _wdi_catalog()
    out, notes = [], {"renamed": {}, "missing_in_catalog": [], "empty": [], "one_country_only": []}
    for code0, advisor, yld, better in INDICATORS:
        code = RENAMED.get(code0, code0)
        if code != code0:
            notes["renamed"][code0] = code
        meta = cat.get(code)
        if code in DERIVED:
            am = _wdi_archive_meta(code)
            src_orgs = sorted({cat[c]["sourceOrganization"].strip() for c in DERIVED[code]["from"] if c in cat})
            name = am["name"]
            desc = (f"{am['sourceNote'].strip()} [Definition of the archived series, WDI "
                    f"{am['source']['value']} (source {am['source']['id']}).]")
            org = ("Derived in this pipeline from WDI " + " and ".join(DERIVED[code]["from"]) + ": "
                   + "; ".join(src_orgs) + f". Archived series: {am['sourceOrganization'].strip()}")
        elif meta is None:
            notes["missing_in_catalog"].append(code)
            print(f"  ! {code}: not in the WDI catalogue, skipped", flush=True)
            continue
        else:
            name, desc, org = meta["name"], meta.get("sourceNote", ""), meta.get("sourceOrganization", "")
        label, unit = _split_unit(code, name)
        if code in DERIVED:
            label = DERIVED[code]["label"]
        d = _series(code)
        if d.empty:
            notes["empty"].append(code)
            print(f"  ! {code}: empty series, skipped", flush=True)
            continue
        last_year = int(d.year.max())
        y0 = last_year - SERIES_YEARS + 1
        rec = {"code": code, "label": label, "name": name, "unit": unit, "advisor": advisor,
               "yield": yld, "better": better, "description": desc.strip(), "source_org": org.strip(),
               "source": "worldbank_wdi", "derived": code in DERIVED,
               "method": DERIVED.get(code, {}).get("method", ""), "requested_code": code0}
        have = []
        for iso in ("LKA", "USA"):
            s = d[d.iso3 == iso].sort_values("year")
            if s.empty:
                rec[iso] = {"value": None, "year": None, "series": []}
                continue
            have.append(iso)
            win = s[s.year >= min(y0, int(s.year.iloc[-1]))]
            rec[iso] = {"value": _clean(s.value.iloc[-1]), "year": int(s.year.iloc[-1]),
                        "series": [[int(y), _clean(v)] for y, v in zip(win.year, win.value)]}
        if len(have) < 2:
            notes["one_country_only"].append(code)
        # latest year both countries report (for like-for-like Advisor comparisons)
        both = d.pivot_table(index="year", columns="iso3", values="value").dropna()
        if {"LKA", "USA"} <= set(both.columns) and len(both):
            yc = int(both.index.max())
            lv, uv = float(both.at[yc, "LKA"]), float(both.at[yc, "USA"])
            # a ratio is only meaningful for two positive quantities (not for growth rates, balances
            # or inflation that can be zero or negative)
            rec["common"] = {"year": yc, "LKA": _clean(lv), "USA": _clean(uv),
                             "ratio_usa_lka": _clean(uv / lv) if (lv > 0 and uv > 0) else None}
        else:
            rec["common"] = None
        out.append(rec)
    return out, notes


# ---------------------------------------------------------------------------------------------
# Wikidata helpers
# ---------------------------------------------------------------------------------------------

def _qid(uri: str | None) -> str | None:
    return uri.rsplit("/", 1)[-1] if isinstance(uri, str) else None


def _wd_items(qids: list[str], name: str) -> dict[str, dict]:
    """Label, coordinate and best population statement for each Wikidata item."""
    vals = " ".join(f"wd:{q}" for q in qids)
    q = f"""SELECT ?item ?itemLabel ?coord ?pop ?t ?rank ?refurl ?stated ?statedLabel WHERE {{
  VALUES ?item {{ {vals} }}
  OPTIONAL {{ ?item wdt:P625 ?coord }}
  OPTIONAL {{ ?item p:P1082 ?st . ?st ps:P1082 ?pop ; wikibase:rank ?rank .
             OPTIONAL {{ ?st pq:P585 ?t }}
             OPTIONAL {{ ?st prov:wasDerivedFrom ?r .
                        OPTIONAL {{ ?r pr:P854 ?refurl }} OPTIONAL {{ ?r pr:P248 ?stated }} }} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}"""
    df = util.sparql(q, name)
    out: dict[str, dict] = {}
    for qid, g in df.groupby(df["item"].map(_qid)):
        coords = sorted(set(g["coord"].dropna())) if "coord" in g else []
        lat = lon = None
        if coords:
            lat, lon = util.parse_wkt_point(coords[0])
        pop = pop_year = ref = None
        if "pop" in g and g["pop"].notna().any():
            s = g[g["pop"].notna()].copy()
            s["rank"] = s["rank"].fillna("").str.rsplit("#", n=1).str[-1]
            s = s[s["rank"] != "DeprecatedRank"]
            s["pref"] = (s["rank"] == "PreferredRank").astype(int)
            s["yr"] = pd.to_numeric(s.get("t", pd.Series(index=s.index, dtype=str)).str[:4], errors="coerce")
            s = s.sort_values(["pref", "yr"], ascending=[False, False], na_position="last")
            if len(s):
                b = s.iloc[0]
                pop = int(float(b["pop"]))
                pop_year = int(b["yr"]) if pd.notna(b["yr"]) else None
                refs = s[(s["pop"] == b["pop"]) & (s["yr"].fillna(-1) == (b["yr"] if pd.notna(b["yr"]) else -1))]
                urls = sorted(set(refs["refurl"].dropna())) if "refurl" in refs else []
                stated = sorted(set(refs["statedLabel"].dropna())) if "statedLabel" in refs else []
                ref = "; ".join(stated + urls) or None
        out[qid] = {"label": g["itemLabel"].iloc[0], "lat": lat, "lon": lon, "population": pop,
                    "population_year": pop_year, "population_ref": ref}
    return out


# ---------------------------------------------------------------------------------------------
# Sri Lanka cities
# ---------------------------------------------------------------------------------------------

# Wikidata items resolved once with: ?item rdfs:label "<name>"@en ; wdt:P17 ?lk . ?lk wdt:P298 "LKA"
# (the city item with coordinates; e.g. Anuradhapura also matches Q19699991, a settlement item
# without coordinates).  The label is re-checked at build time.
LKA_CITIES = [
    ("colombo", "Colombo", "Q35381"),
    ("kandy", "Kandy", "Q203197"),
    ("galle", "Galle", "Q319366"),
    ("jaffna", "Jaffna", "Q215277"),
    ("trincomalee", "Trincomalee", "Q323873"),
    ("anuradhapura", "Anuradhapura", "Q5724"),
    ("batticaloa", "Batticaloa", "Q810963"),
    ("kurunegala", "Kurunegala", "Q776887"),
    ("ratnapura", "Ratnapura", "Q508753"),
    ("negombo", "Negombo", "Q668616"),
]


def _locate(lat: float, lon: float, adm: gpd.GeoDataFrame) -> tuple[str, str]:
    """(admin_id, name) of the polygon containing the point, else the nearest polygon (coastal points)."""
    pt = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs=4326).iloc[0]
    hit = adm[adm.contains(pt)]
    if len(hit):
        return hit.admin_id.iloc[0], hit.name.iloc[0]
    d = adm.to_crs(3857).distance(gpd.GeoSeries([pt], crs=4326).to_crs(3857).iloc[0])
    i = int(np.argmin(d.to_numpy()))
    return adm.admin_id.iloc[i], adm.name.iloc[i]


CENSUS_YEAR = 2024
OFFICIAL_REF_SUFFIX = ".gov.lk"       # e.g. www.statistics.gov.lk (DCS census reports)


def _census_ds() -> gpd.GeoDataFrame:
    """Census 2024 DS divisions (portal polygons) with tot_Ag (total) and tot_Sec_Ur (urban sector)."""
    topo = fetch(PORTAL + "2024-dsdivisions-topo.js", "dcs_census_portal", "lka/census_portal",
                 "2024-dsdivisions-topo.json")
    g = gpd.read_file(topo)
    if g.crs is None:
        g = g.set_crs(4326)
    g["ADM_ID"] = g.ADM_ID.astype(str)
    ids = g.ADM_ID.tolist()
    for ind in ("tot_Ag", "tot_Sec_Ur"):
        p = fetch(PORTAL + "fetch_data.php", "dcs_census_portal", "lka/census_portal", f"ds_table_2024_{ind}.json",
                  method="POST", data=json.dumps({"year": str(CENSUS_YEAR), "indicator": ind, "ids": ids,
                                                  "table": "ds_table_2024"}),
                  headers={"Content-Type": "application/json"}, timeout=120)
        vals = {str(r["ADM_ID"]): r["value"] for r in json.loads(p.read_text())}
        g[ind] = pd.to_numeric(g.ADM_ID.map(vals), errors="coerce")
        if g[ind].isna().any():
            raise RuntimeError(f"census portal {ind}: {int(g[ind].isna().sum())} DS divisions without a value")
    return g


def _ref_kind(ref: str | None) -> str:
    """Classify a Wikidata population reference: official (a .gov.lk document), other website, or none."""
    if not ref:
        return "unreferenced"
    urls = re.findall(r"https?://([^/;\s]+)", ref)
    if any(h.lower().endswith(OFFICIAL_REF_SUFFIX) for h in urls):
        return "official (DCS / government of Sri Lanka document)"
    if urls:
        return "third-party website (not an official statistics source)"
    return "stated in: " + ref


def metros_lka(inds: list[dict] | None = None) -> tuple[list[dict], dict]:
    wd = _wd_items([q for _, _, q in LKA_CITIES], "national_lka_cities")
    a1, a2, a3 = (load_admin("lka", lv) for lv in ("adm1", "adm2", "adm3"))
    pop2 = a2.set_index("admin_id")["pop"]
    cds = _census_ds()
    # UN World Urbanization Prospects largest-city series (WDI EN.URB.LCTY) to label matching statements
    lcty = {}
    for r in inds or []:
        if r["code"] == "EN.URB.LCTY":
            lcty = {int(y): v for y, v in r["LKA"]["series"]}
    out, checks = [], {}
    for rank, (slug, name, q) in enumerate(LKA_CITIES, 1):
        w = wd.get(q)
        if not w or w["lat"] is None:
            raise RuntimeError(f"Wikidata item {q} ({name}) has no coordinates")
        if w["label"] != name:
            raise RuntimeError(f"Wikidata item {q}: label {w['label']!r} != {name!r}")
        d2, d2n = _locate(w["lat"], w["lon"], a2)
        d3, d3n = _locate(w["lat"], w["lon"], a3)
        p1 = a2.set_index("admin_id").at[d2, "parent_id"]
        p1n = a1.set_index("admin_id").at[p1, "name"]
        # Census 2024 DS division holding the centre (portal polygons; ids can differ from COD-AB pcodes)
        cid, _ = _locate(w["lat"], w["lon"], cds.rename(columns={"ADM_ID": "admin_id", "ADM_NAME": "name"}))
        c = cds.set_index("ADM_ID").loc[cid]
        pop, py = w["population"], w["population_year"]
        kind = _ref_kind(w["population_ref"]) if pop is not None else "no population (P1082) statement"
        note = None
        if pop is not None and py in lcty and abs(pop - lcty[py]) <= 0.005 * lcty[py]:
            note = (f"Equals WDI EN.URB.LCTY for {py} ({lcty[py]:,.0f}), the UN World Urbanization Prospects "
                    f"2018 estimate/projection for Sri Lanka's largest city, not a census count.")
        srcs = ["wikidata", "ocha_codab_lka", "unfpa_codps_lka", "dcs_census_portal"]
        out.append({
            "id": slug, "name": name, "country": "lka", "lat": round(w["lat"], 6), "lon": round(w["lon"], 6),
            "population": pop, "population_year": py,
            "population_scope": ("as stated on Wikidata (P1082, preferred rank else latest point in time) for "
                                 "the city item; the statement does not record its area, which varies by city "
                                 "(municipal / urban council, DS division, or a UN urban-agglomeration "
                                 "estimate). For a like-for-like official figure use census_ds_population."),
            "population_ref": w["population_ref"], "population_ref_kind": kind, "population_note": note,
            "census_ds_id": cid, "census_ds_name": str(c["ADM_NAME"]),
            "census_ds_population": int(c["tot_Ag"]), "census_ds_urban_population": int(c["tot_Sec_Ur"]),
            "census_year": CENSUS_YEAR,
            "census_scope": ("Census of Population and Housing 2024 (DCS): total and urban-sector population "
                             "(Municipal / Urban Council areas, excluding Estate Urban) of the DS division "
                             "containing the city centre. A council area that spans several DS divisions "
                             "(e.g. Colombo, Jaffna) is only partly covered."),
            "admin_level": "adm2", "admin_ids": [d2],
            "district_id": d2, "district_name": d2n, "province_id": p1, "province_name": p1n,
            "ds_division_id": d3, "ds_division_name": d3n,
            "district_population": int(round(float(pop2[d2]))), "district_population_year": 2023,
            "rank": rank, "rank_basis": "curated City View order (not a population rank)",
            "gdp_usd": None, "gdp_year": None, "wikidata": q, "sources": srcs,
        })
        checks[slug] = {"district": f"{d2} {d2n}", "ds": f"{d3} {d3n}", "census_ds": f"{cid} {c['ADM_NAME']}",
                        "census_ds_matches_codab": cid == d3.removeprefix("LK"),
                        "pop": pop, "pop_ref_kind": kind,
                        "census_ds_pop": int(c["tot_Ag"]), "census_ds_urban_pop": int(c["tot_Sec_Ur"])}
    checks["census_total"] = int(cds["tot_Ag"].sum())
    checks["census_urban_total"] = int(cds["tot_Sec_Ur"].sum())
    return out, checks


# ---------------------------------------------------------------------------------------------
# US metros
# ---------------------------------------------------------------------------------------------

def _cbsa_pop() -> tuple[pd.DataFrame, int]:
    p = fetch(CBSA_POP, "census_pep_cbsa_2025", "usa/metro")
    d = pd.read_csv(p, encoding="latin-1", dtype=str)
    years = sorted(int(c[-4:]) for c in d.columns if re.fullmatch(r"POPESTIMATE\d{4}", c))
    for y in years:
        d[f"POPESTIMATE{y}"] = pd.to_numeric(d[f"POPESTIMATE{y}"])
    return d, years[-1]


def _delineation() -> tuple[pd.DataFrame, pd.DataFrame]:
    p1 = fetch(DELIN_LIST1, "census_delineation_2023", "usa/metro")
    p2 = fetch(DELIN_LIST2, "census_delineation_2023", "usa/metro")
    l1 = pd.read_excel(p1, header=2, dtype=str)
    l1 = l1[l1["CBSA Code"].notna() & l1["FIPS County Code"].notna()].copy()
    l1["geoid"] = l1["FIPS State Code"].str.zfill(2) + l1["FIPS County Code"].str.zfill(3)
    l2 = pd.read_excel(p2, header=2, dtype=str)
    l2 = l2[l2["CBSA Code"].notna() & l2["FIPS Place Code"].notna()].copy()
    l2["place_geoid"] = l2["FIPS State Code"].str.zfill(2) + l2["FIPS Place Code"].str.zfill(5)
    return l1, l2


def _gazetteer() -> pd.DataFrame:
    z = fetch(GAZ_PLACES, "census_gazetteer_2025", "usa/metro")
    f = unzip(z, ".txt")
    sep = "|" if "|" in f.open(encoding="latin-1").readline() else "\t"
    g = pd.read_csv(f, sep=sep, dtype=str, encoding="latin-1")
    g.columns = [c.strip() for c in g.columns]
    return g.set_index("GEOID")


def _bea_county_gdp() -> tuple[pd.Series, pd.Series, int, float]:
    """BEA current-dollar GDP (USD) by BEA county unit for the latest year, names, year, US total."""
    z = fetch(BEA_CAGDP1, "bea_cagdp1", "usa/bea")
    f = next(p for p in unzip(z).iterdir() if p.name.startswith("CAGDP1__ALL_AREAS"))
    d = pd.read_csv(f, dtype=str, encoding="latin-1")
    d = d[d.LineCode.notna()].copy()
    d["fips"] = d.GeoFIPS.str.strip().str.strip('"').str.strip()
    d = d[d.LineCode.str.strip() == "3"]
    year = max(int(c) for c in d.columns if re.fullmatch(r"\d{4}", str(c)))
    d["val"] = pd.to_numeric(d[str(year)].str.strip(), errors="coerce") * 1000.0
    us_total = float(d.loc[d.fips == "00000", "val"].iloc[0])
    d = d[d.fips.str[:2].isin(US_STATE_FIPS) & (d.fips.str[2:] != "000") & d.val.notna()]
    return d.set_index("fips")["val"], d.set_index("fips")["GeoName"].str.strip(), year, us_total


def _bea_units_of(counties: set[str], bea: pd.Series, names: pd.Series) -> dict[str, list[str]]:
    """BEA unit -> list of Census 2023 county GEOIDs it covers (combination areas matched by name)."""
    adm = load_admin("usa", "adm2")
    by_name = {(r.admin_id[:2], r.name.lower()): r.admin_id for r in adm.itertuples()}
    units: dict[str, list[str]] = {}
    for u in bea.index:
        if u in counties:
            units[u] = [u]
            continue
        st = u[:2]
        nm = re.sub(r",\s*[A-Z]{2}\*?$", "", names[u]).strip()
        parts = [p.strip() for p in re.split(r"\+|,", nm) if p.strip()]
        ids = []
        for i, p in enumerate(parts):
            p0 = re.sub(r"\s+City$", "", p)
            # first part is a county (VA "James City" -> "James City County"; HI counties), rest are cities
            cands = [f"{p} county", f"{p0} county"] if (st == "15" or i == 0) else [f"{p0} city", f"{p} city"]
            hit = next((by_name[(st, c.lower())] for c in cands if (st, c.lower()) in by_name), None)
            if hit is None:
                raise ValueError(f"cannot match BEA unit {u} part {p!r}")
            ids.append(hit)
        units[u] = ids
    covered = {c for v in units.values() for c in v}
    missing = sorted(counties - covered)
    if missing:
        raise ValueError(f"Census counties without a BEA unit: {missing[:10]}")
    return units


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def metros_usa() -> tuple[list[dict], dict]:
    cb, py = _cbsa_pop()
    l1, l2 = _delineation()
    gaz = _gazetteer()
    bea, bea_names, gdp_year, bea_us = _bea_county_gdp()
    adm2 = load_admin("usa", "adm2")
    counties = set(adm2.admin_id)
    units = _bea_units_of(counties, bea, bea_names)
    unit_of = {c: u for u, cs in units.items() for c in cs}
    cpop = adm2.set_index("admin_id")["pop"]       # 2023 PEP county population (core) for splitting

    msa = cb[(cb.LSAD == "Metropolitan Statistical Area")].copy()
    members = l1[l1["Metropolitan/Micropolitan Statistical Area"] == "Metropolitan Statistical Area"]
    members = members.groupby("CBSA Code")["geoid"].apply(lambda s: sorted(set(s)))
    msa = msa[msa.CBSA.isin(members.index)]
    msa = msa[msa.CBSA.map(lambda c: all(g[:2] in US_STATE_FIPS for g in members[c]))]   # drop Puerto Rico
    top = msa.sort_values(f"POPESTIMATE{py}", ascending=False).head(N_US_METROS)

    # principal city = first city named in the CBSA title, matched in List 2
    rows = []
    for r in top.itertuples():
        first = r.NAME.split(",")[0].split("-")[0].strip()
        pc = l2[(l2["CBSA Code"] == r.CBSA) & (l2["Principal City Name"] == first)]
        if pc.empty:
            raise RuntimeError(f"principal city {first!r} of {r.NAME} not in delineation List 2")
        pg = pc.place_geoid.iloc[0]
        rows.append((r, first, pg, gaz.at[pg, "ANSICODE"].strip()))
    gnis = {g.lstrip("0"): pg for _, _, pg, g in rows}
    vals = " ".join(f'"{g}"' for g in gnis)
    wd_ids = util.sparql(f"SELECT ?item ?g WHERE {{ VALUES ?g {{ {vals} }} ?item wdt:P590 ?g . }}",
                         "national_usa_gnis")
    wd_ids["qid"] = wd_ids["item"].map(_qid)
    by_gnis = wd_ids.groupby("g")["qid"].apply(list).to_dict()
    qids = sorted({q for v in by_gnis.values() for q in v})
    wd = _wd_items(qids, "national_usa_cities")

    out, checks = [], {"gdp_split_units": {}, "county_list_mismatch": {}}
    sub_pop = cb[cb.LSAD == "County or equivalent"]
    for rank, (r, first, pg, g) in enumerate(rows, 1):
        cands = [q for q in by_gnis.get(g.lstrip("0"), []) if wd.get(q, {}).get("lat") is not None]
        if not cands:
            raise RuntimeError(f"no Wikidata item with coordinates for GNIS {g} ({first})")
        cands.sort(key=lambda q: (wd[q]["population"] is None, q))
        q = cands[0]
        w = wd[q]
        mem = members[r.CBSA]
        bad = [c for c in mem if c not in counties]
        if bad:
            raise RuntimeError(f"{r.NAME}: member counties not in Census cb_2023: {bad}")
        # Census CBSA file lists the same component counties?
        cs = sorted(sub_pop[sub_pop.CBSA == r.CBSA].STCOU.dropna())
        if cs and cs != mem:
            checks["county_list_mismatch"][r.CBSA] = {"delineation_only": sorted(set(mem) - set(cs)),
                                                      "pep_only": sorted(set(cs) - set(mem))}
        # metro GDP = Σ BEA units; a BEA combination area only partly inside the metro is split by pop
        gdp, split = 0.0, {}
        for u in sorted({unit_of[c] for c in mem}):
            inside = [c for c in units[u] if c in mem]
            share = 1.0
            if len(inside) < len(units[u]):
                share = float(cpop.reindex(inside).sum() / cpop.reindex(units[u]).sum())
                split[u] = round(share, 4)
            gdp += float(bea[u]) * share
        if split:
            checks["gdp_split_units"][r.CBSA] = split
        # principal-city point should fall inside a member county
        cid, cname = _locate(w["lat"], w["lon"], adm2)
        pcol = f"POPESTIMATE{gdp_year}"
        pop_prev = int(getattr(r, pcol)) if pcol in cb.columns else None
        title = r.NAME
        out.append({
            "id": _slug(first), "name": first, "country": "usa",
            "lat": round(w["lat"], 6), "lon": round(w["lon"], 6),
            "population": int(getattr(r, f"POPESTIMATE{py}")), "population_year": py,
            "population_scope": "Metropolitan Statistical Area (OMB 2023 delineation)",
            "admin_level": "adm2", "admin_ids": mem,
            "cbsa": r.CBSA, "title": title, "rank": rank,
            "rank_basis": f"MSA population {py} (Puerto Rico excluded)", "states": sorted({c[:2] for c in mem}),
            "principal_city": first, "principal_city_fips": pg, "principal_city_gnis": g,
            "principal_city_population": w["population"], "principal_city_population_year": w["population_year"],
            "centre_county": cid, "centre_in_members": cid in mem,
            "gdp_usd": round(gdp), "gdp_year": gdp_year,
            "gdp_per_capita_usd": round(gdp / pop_prev) if pop_prev else None,
            "gdp_per_capita_pop_year": gdp_year if pop_prev else None,
            "wikidata": q,
            "sources": ["census_pep_cbsa_2025", "census_delineation_2023", "bea_cagdp1", "wikidata",
                        "census_gazetteer_2025", "census_cb_2023"],
        })
        if cid not in mem:
            print(f"  ! {first}: Wikidata centre falls in {cid} {cname}, not a member county", flush=True)
    checks["bea_year"] = gdp_year
    checks["bea_us_total_usd"] = bea_us
    checks["bea_sum_counties_usd"] = float(bea.sum())
    checks["pop_year"] = py
    return out, checks


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------

def _dump(path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=lambda o: None if o is pd.NA else str(o)))


def _summary(inds: list[dict], metros: list[dict]) -> None:
    def fmt(x):
        if x is None:
            return "-"
        a = abs(x)
        return f"{x / 1e12:.3f}T" if a >= 1e12 else f"{x / 1e9:.2f}B" if a >= 1e9 else \
               f"{x / 1e6:.2f}M" if a >= 1e6 else f"{x:,.4g}"
    print(f"\n{'code':<22}{'advisor':<14}{'Sri Lanka':>16}{'':>6}{'United States':>16}{'':>6}  unit")
    for r in inds:
        L, U = r["LKA"], r["USA"]
        print(f"{r['code']:<22}{r['advisor']:<14}{fmt(L['value']):>16} {L['year'] or '':<5}"
              f"{fmt(U['value']):>16} {U['year'] or '':<5}  {r['unit']}")
    print(f"\n{'metro':<16}{'cc':<5}{'lat':>9}{'lon':>11}{'population':>13}{'yr':>6}{'GDP':>11}{'yr':>6}  admin_ids")
    for m in metros:
        ids = m["admin_ids"]
        print(f"{m['name']:<16}{m['country']:<5}{m['lat']:>9.4f}{m['lon']:>11.4f}"
              f"{fmt(m['population']):>13}{m['population_year'] or '':>6}{fmt(m['gdp_usd']):>11}"
              f"{m['gdp_year'] or '':>6}  {','.join(ids[:4])}{'…+' + str(len(ids) - 4) if len(ids) > 4 else ''}")


def _sanity(inds: list[dict], checks: dict) -> dict:
    by = {r["code"]: r for r in inds}
    gdp = by["NY.GDP.MKTP.CD"]
    s = {"gdp_usd": {iso: {"value": gdp[iso]["value"], "year": gdp[iso]["year"]} for iso in ("LKA", "USA")}}
    usv, usy = gdp["USA"]["value"], gdp["USA"]["year"]
    lkv, lky = gdp["LKA"]["value"], gdp["LKA"]["year"]
    s["usa_gdp_in_27_31T"] = bool(27e12 <= usv <= 31e12)
    s["lka_gdp_in_75_110B"] = bool(75e9 <= lkv <= 110e9)
    print(f"\nsanity: US GDP {usy} = ${usv / 1e12:.2f}T (expect 27-31T: {s['usa_gdp_in_27_31T']}); "
          f"Sri Lanka GDP {lky} = ${lkv / 1e9:.1f}B (expect 75-110B: {s['lka_gdp_in_75_110B']})")
    u = checks.get("usa")
    if u:
        by_year = dict(map(tuple, [(y, v) for y, v in gdp["USA"]["series"]]))
        wb = by_year.get(u["bea_year"])
        s["bea_vs_wdi"] = {"year": u["bea_year"], "bea_us_total": u["bea_us_total_usd"],
                           "bea_sum_counties": u["bea_sum_counties_usd"], "wdi": wb,
                           "ratio_bea_wdi": (u["bea_us_total_usd"] / wb) if wb else None}
        print(f"sanity: BEA US GDP {u['bea_year']} = ${u['bea_us_total_usd'] / 1e12:.3f}T, sum of BEA "
              f"counties (50 states + DC) = ${u['bea_sum_counties_usd'] / 1e12:.3f}T, WDI = "
              f"${(wb or float('nan')) / 1e12:.3f}T")
    pop = by["SP.POP.TOTL"]
    lps = dict(map(tuple, pop["LKA"]["series"]))
    lp = lps.get(2023)
    a2 = load_admin("lka", "adm2")
    s["lka_pop_2023"] = {"wdi": lp, "codps_districts": float(a2["pop"].sum()),
                         "note": "both are DCS mid-year estimates made before the 2024 census"}
    print(f"sanity: Sri Lanka population 2023 WDI = {lp:,.0f}, COD-PS district sum = {a2['pop'].sum():,.0f}")
    lk = checks.get("lka", {})
    if lk.get("census_total"):
        w24 = lps.get(CENSUS_YEAR)
        s["lka_pop_census_2024"] = {"census_portal_sum": lk["census_total"], "wdi_2024": w24,
                                    "ratio_wdi_census": (w24 / lk["census_total"]) if w24 else None}
        print(f"sanity: Sri Lanka Census {CENSUS_YEAR} (portal sum of DS divisions) = {lk['census_total']:,}, "
              f"WDI {CENSUS_YEAR} = {w24:,.0f}")
    s["lka_gdp_dcs_vs_wdi"] = _lka_gdp_check(by)
    s["lka_electricity_ceb_vs_derived"] = _lka_elec_check(by)
    return s


def _lka_gdp_check(by: dict) -> dict:
    """DCS Statistical Abstract Table 10.2 GDP (Rs. million) / WDI exchange rate vs WDI GDP in US$."""
    import pdfplumber
    p = fetch(DCS_ABSTRACT_10_2, "dcs_abstract2025", "lka/economy", "abstract2025_10.2.pdf")
    with pdfplumber.open(p) as pdf:
        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    row = next((ln for ln in text.splitlines() if "GDP at market price" in ln), None)
    if row is None:
        raise RuntimeError("DCS Table 10.2: row 'GDP at market price' not found")
    vals = [float(v.replace(",", "")) for v in re.findall(r"\d{1,3}(?:,\d{3})+", row)]
    years = list(range(2019, 2025))                    # table title: '... 2019 - 2024', one column per year
    if "2019 - 2024" not in text or len(vals) != len(years):
        raise RuntimeError(f"DCS Table 10.2: unexpected layout ({len(vals)} values in {row!r})")
    fx = dict(map(tuple, by["PA.NUS.FCRF"]["LKA"]["series"]))
    wd = dict(map(tuple, by["NY.GDP.MKTP.CD"]["LKA"]["series"]))
    out = {}
    for y, v in zip(years, vals):
        if y in fx and y in wd:
            usd = v * 1e6 / fx[y]
            out[str(y)] = {"dcs_rs_million": v, "lkr_per_usd": fx[y], "dcs_usd": usd, "wdi_usd": wd[y],
                           "ratio_wdi_dcs": wd[y] / usd}
    r = out.get("2024", {})
    if r:
        print(f"sanity: Sri Lanka GDP 2024 DCS Rs {r['dcs_rs_million'] / 1e6:.3f} trillion / {r['lkr_per_usd']:.2f} "
              f"= ${r['dcs_usd'] / 1e9:.2f}B vs WDI ${r['wdi_usd'] / 1e9:.2f}B (ratio {r['ratio_wdi_dcs']:.4f})")
    return out


def _lka_elec_check(by: dict) -> dict:
    """CEB Annual Report 2023 Figure FR4 gross generation (GWh, 2019-2023) vs derived EG.ELC.PROD.KH."""
    import pypdfium2 as pdfium
    p = fetch(CEB_AR_2023, "ceb_annual_report_2023", "lka/ceb", "ceb_annual_report_2023_en.pdf")
    doc = pdfium.PdfDocument(str(p))
    seg, page = None, None
    legend = "Sales Unit Gross Generation Unit"            # FR4 legend; the chart labels follow it
    for i in range(len(doc)):
        t = doc[i].get_textpage().get_text_range()
        if "Figure FR4: Generation and Sales (GWh)" in t and legend in t:
            seg = t[t.index(legend) + len(legend):]
            seg, page = seg[: seg.index("%")], i + 1       # stop at the next chart (FR6 percentages)
            break
    doc.close()
    if seg is None:
        raise RuntimeError("CEB AR 2023: Figure FR4 not found")
    years = sorted({int(y) for y in re.findall(r"\b(20[12]\d)\b", seg)})
    nums = [float(v.replace(",", "")) for v in re.findall(r"\b\d{1,2},\d{3}\b", seg)]
    nums = [v for v in nums if v % 5000]                  # drop the axis ticks 5,000 ... 20,000
    if years != list(range(2019, 2024)) or len(nums) != 10:
        raise RuntimeError(f"CEB AR 2023 FR4: unexpected layout years={years} values={nums}")
    a, b = nums[:5], nums[5:]
    gross = a if all(x > y for x, y in zip(a, b)) else b  # gross generation exceeds sales every year
    der = dict(map(tuple, by["EG.ELC.PROD.KH"]["LKA"]["series"]))
    out = {"page": page, "gross_generation_gwh": dict(zip(map(str, years), gross)), "derived_gwh": {},
           "ratio_derived_ceb": {}}
    for y, g in zip(years, gross):
        if y in der:
            out["derived_gwh"][str(y)] = der[y] / 1e6
            out["ratio_derived_ceb"][str(y)] = der[y] / 1e6 / g
    print(f"sanity: Sri Lanka electricity, derived WDI vs CEB gross generation: "
          + ", ".join(f"{y} {r:.3f}" for y, r in out["ratio_derived_ceb"].items()))
    return out


def build(country: str | None = None) -> None:
    """Build indicators.json + metros.json (both countries' indicators always; metros for `country`,
    or both when None) and the per-country domain meta used by civmap.validate."""
    t0 = time.time()
    countries = [country] if country else list(COUNTRIES)
    NAT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"== national / {','.join(countries)}", flush=True)

    inds, notes = build_indicators()
    print(f"  indicators: {len(inds)} (renamed {notes['renamed']}, one country only "
          f"{notes['one_country_only']})", flush=True)

    metros_f = NAT_DIR / "metros.json"
    prev = json.loads(metros_f.read_text()) if metros_f.exists() else []
    metros = [m for m in prev if m.get("country") not in countries]
    checks: dict = {}
    for cc in countries:
        ms, ck = metros_lka(inds) if cc == "lka" else metros_usa()
        metros += ms
        checks[cc] = ck
        print(f"  metros {cc}: {len(ms)}", flush=True)
    metros.sort(key=lambda m: (m["country"], m.get("rank") or 0))

    _dump(NAT_DIR / "indicators.json", inds)
    _dump(metros_f, metros)
    (NAT_DIR / "sources.json").write_text(json.dumps([asdict(s) for s in SOURCES], indent=2))
    report_f = NAT_DIR / "report.json"
    report = json.loads(report_f.read_text()) if report_f.exists() else {}
    report.update({"indicator_notes": notes, "n_indicators": len(inds),
                   "metros": {cc: len([m for m in metros if m["country"] == cc]) for cc in COUNTRIES},
                   "checks": {**report.get("checks", {}), **checks}})
    report["sanity"] = _sanity(inds, report["checks"])
    _dump(report_f, report)
    for cc in countries:
        write_domain_meta(interim_dir(cc, DOMAIN), METRICS, SOURCES, cc)
    _summary(inds, metros)
    print(f"  done in {time.time() - t0:.0f}s -> {NAT_DIR}", flush=True)


if __name__ == "__main__":
    from .. import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    if set(ccs) == set(COUNTRIES):
        build()
    else:
        for cc in ccs:
            build(cc)
    sys.exit(validate.main([DOMAIN] + ccs))
