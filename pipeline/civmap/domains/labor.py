"""Labor domain: workforce size, unemployment, Civ "Citizens" (occupation classes) and industry employment.

Feeds the Citizens of every tile and City View (``cit_*``), the Science yield (``cit_engineers``) and
the industry location quotients behind Districts (``emp_*``).

Outputs (data/interim/<country>/labor/):
  LKA  admin_adm3.csv   339 DS divisions: every metric (district LFS estimates distributed inside
                        each district with Census 2024 DS-division counts)
       admin_adm2.csv   25 districts: the district-level LFS figures and estimates (audit table,
                        not referenced by metrics.json)
       admin_adm1.csv   9 provinces: LFS 2024 occupation (ISCO-08) and industry (ISIC Rev.4) tables
                        as published (audit table, not referenced by metrics.json)
  USA  admin_adm2.csv   3,144 counties: ACS 2020-2024 5-year B23025, C24010, C24030

Sri Lanka method (all figures DCS Labour Force Survey 2024 unless stated):
  1. district labour force / employed / unemployed / household population 15+ (appendix Table 12)
     and district employment by sector (agriculture / industry / services, Table 13);
  2. province employment by ISIC Rev.4 group (Table 15) is split to districts inside each sector
     block in proportion to district sector employment (exact for both margins);
  3. province employment by ISCO-08 major group (Table 14) is split to district x industry with
     iterative proportional fitting: seed = district x industry employment (step 2) x the national
     occupation mix of that industry (ILOSTAT 2024 cross-tab of the same LFS), raked to the province
     occupation totals and the district x industry totals;
  4. major groups are cut into Civ classes with the national ISCO-08 sub-major-group (2-digit) shares
     of LFS 2024 (ILOSTAT); elementary workers in agriculture are ISCO 92 (agricultural labourers);
  5. district values are distributed to DS divisions with Census of Population and Housing 2024
     DS-division counts of employed / unemployed / persons 15+ (DCS census data portal), mapped from
     the 2024 DS geography (340 units) to the COD-AB DS geography (339 units) with a population-
     weighted overlay on the WorldPop 100 m grid.

Run:  python -m civmap.domains.labor      (builds lka + usa, then validates)
"""
from __future__ import annotations

import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import util
from ..config import CACHE, COUNTRIES, interim_dir
from ..core import POP_RASTER, load_admin
from ..fetch import fetch
from ..schema import Metric, Source, write_domain_meta

DOMAIN = "labor"
labor = DOMAIN

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

LFS_URL = "https://www.statistics.gov.lk/Resource/en/LabourForce/Annual_Reports/LFS2024.pdf"
ILO_URL = "https://rplumber.ilo.org/data/indicator/"
PORTAL_BASE = "https://www.statistics.gov.lk/DashBoard/censusdataportal/"
PORTAL_DS_TOPO = PORTAL_BASE + "2024-dsdivisions-topo.js"
PORTAL_FETCH = PORTAL_BASE + "fetch_data.php"
ACS_YEAR = 2024
ACS_SHELLS = ("https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/"
              "documentation/ACS20245YR_Table_Shells.txt")

SOURCES = [
    Source("dcs_lfs2024", "Sri Lanka Labour Force Survey Annual Report - 2024 (with provincial and district level data)",
           "Department of Census and Statistics, Sri Lanka (DCS)",
           "https://www.statistics.gov.lk/LabourForce/StaticalInformation/AnnualReports", "public document (DCS)",
           "2024", ["lka"],
           "PDF " + LFS_URL + ". Parsed from the Statistical Appendix: Table 12 (p. 72, PDF p. 92) labour force "
           "status of the household population 15+ by district; Table 13 (p. 75, PDF p. 95) employment by "
           "major industry group (agriculture/industry/services) by district; Table 14 (p. 77, PDF p. 97) "
           "employment by ISCO-08 major group by province; Table 15 (p. 78, PDF p. 98) employment by ISIC "
           "Rev.4 industry group by province; Tables 6 and 7 (pp. 64, 67) national totals used as checks. "
           "Annual sample of 25,750 housing units; covers the household (non-institutional) population only, "
           "so armed-forces personnel living in barracks are not represented. DCS flags district/province "
           "cells with high coefficients of variation."),
    Source("ilostat_lfs_lka", "ILOSTAT: Employment by sex and occupation - ISCO level 2; Employment by economic "
           "activity and occupation; Employment by sex and economic activity (Sri Lanka, 2024)",
           "International Labour Organization (ILOSTAT)", "https://ilostat.ilo.org/data/", "CC BY 4.0", "2024",
           ["lka"],
           "Bulk API " + ILO_URL + " ids EMP_TEMP_SEX_OC2_NB_A, EMP_TEMP_ECO_OCU_NB_A, EMP_TEMP_SEX_ECO_NB_A, "
           "ref_area=LKA. ILO tabulation of the same Sri Lanka LFS 2024 microdata (source BA:1359); totals "
           "equal the DCS report (7,949.751 thousand employed). Supplies ISCO-08 sub-major-group (2-digit) "
           "shares, the national industry x occupation cross-tab and ISIC section detail (D, E, F, L, R, U) "
           "that the DCS report publishes only in combined groups. Cells ILO suppresses as unreliable are "
           "treated as zero in seeds."),
    Source("dcs_census_portal", "Census of Population and Housing 2024 - Census Data Portal (DS-division tables: "
           "economic activity of persons 15 years and over)",
           "Department of Census and Statistics, Sri Lanka", PORTAL_BASE, "public data (DCS)", "2024", ["lka"],
           "POST " + PORTAL_FETCH + " with table ds_table_2024 and indicators tot_EcoAct (persons 15+), "
           "tot_EcoAct_Emp (employed), tot_EcoAct_Unemp (unemployed) for all 340 DS divisions of the 2024 "
           "census geography; DS boundaries from 2024-dsdivisions-topo.js on the same portal. Used only as "
           "the within-district distribution of the LFS figures."),
    Source("worldpop_2020", "WorldPop Global Project Population Data 2020 (unconstrained, people per pixel)",
           "WorldPop, University of Southampton", "https://hub.worldpop.org/project/categories?id=3",
           "CC BY 4.0", "2020", ["lka"],
           "100 m grid used to weight the overlay of 2024 census DS divisions onto COD-AB DS divisions."),
    Source("census_acs5_2024", "American Community Survey 2020-2024 5-year estimates, table-based Summary File "
           "(tables B23025, C24010, C24030)",
           "U.S. Census Bureau", "https://www.census.gov/programs-surveys/acs/data/summary-file.html",
           "public domain", "2020-2024", ["usa"],
           "County rows of acsdt5y2024-b23025.dat (employment status, population 16+), acsdt5y2024-c24010.dat "
           "(sex by occupation, civilian employed 16+), acsdt5y2024-c24030.dat (sex by industry, civilian "
           "employed 16+) from https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/"
           "data/5YRData/; line labels from ACS20245YR_Table_Shells.txt. Counties are the 2023 Census "
           "geography (Connecticut planning regions), so no splitting was needed."),
    Source("worldbank_wdi", "World Development Indicators", "The World Bank", "https://data.worldbank.org",
           "CC BY 4.0", "2024", ["lka", "usa"],
           "Used only for sanity checks of national totals (SL.TLF.TOTL.IN, SL.UEM.TOTL.NE.ZS, "
           "SL.TLF.CACT.NE.ZS, MS.MIL.TOTL.P1)."),
]

# ---------------------------------------------------------------------------------------------
# Class and industry definitions
# ---------------------------------------------------------------------------------------------

CLASSES = ["cit_farmers", "cit_workers", "cit_merchants", "cit_engineers", "cit_scholars", "cit_healers",
           "cit_service", "cit_administrators", "cit_artists", "cit_soldiers"]
INDUSTRIES = ["emp_agri", "emp_mining", "emp_manufacturing", "emp_construction", "emp_utilities", "emp_trade",
              "emp_transport", "emp_ict", "emp_finance", "emp_professional", "emp_education", "emp_health",
              "emp_arts_accommodation", "emp_public_admin", "emp_other"]

# --- USA: ACS C24010 detailed lines (male line n, female line n + 36) -> class
US_OCC = {
    "cit_administrators": [5, 6],              # SOC 11 management, 13 business & financial operations
    "cit_engineers": [8, 9, 10],               # SOC 15 computer & math, 17 architecture & engineering, 19 science
    "cit_scholars": [12, 13, 14],              # SOC 21 community & social service, 23 legal, 25 education
    "cit_artists": [15],                       # SOC 27 arts, design, entertainment, sports & media
    "cit_healers": [17, 18, 20],               # SOC 29 healthcare practitioners & technical, 31 support
    "cit_service": [22, 23, 24, 25, 26],       # SOC 33 protective, 35 food, 37 building & grounds, 39 personal care
    "cit_merchants": [28, 29],                 # SOC 41 sales, 43 office & administrative support
    "cit_farmers": [31],                       # SOC 45 farming, fishing & forestry
    "cit_workers": [32, 33, 35, 36, 37],       # SOC 47, 49, 51, 53 (transportation + material moving)
}
# --- USA: ACS C24030 lines (male n, female n + 27) -> industry
US_IND = {
    "emp_agri": [4], "emp_mining": [5], "emp_construction": [6], "emp_manufacturing": [7],
    "emp_trade": [8, 9], "emp_transport": [11], "emp_utilities": [12], "emp_ict": [13],
    "emp_finance": [15, 16], "emp_professional": [18, 19, 20], "emp_education": [22], "emp_health": [23],
    "emp_arts_accommodation": [25, 26], "emp_other": [27], "emp_public_admin": [28],
}

# --- LKA: LFS industry groups (codes as printed in LFS Tables 6/15) -> ISIC Rev.4 sections, sector
LFS_GROUPS = ["1", "2", "3", "6", "7", "8", "9", "10", "11", "13", "14", "15", "16", "17", "19", "20", "Other"]
GROUP_SECTIONS = {"1": "A", "2": "B", "3": "C", "6": "DEF", "7": "G", "8": "H", "9": "I", "10": "J", "11": "K",
                  "13": "M", "14": "N", "15": "O", "16": "P", "17": "Q", "19": "S", "20": "T", "Other": "LRU"}
GROUP_SECTOR = {g: ("agri" if g == "1" else "industry" if g in ("2", "3", "6") else "services")
                for g in LFS_GROUPS}
SECTION_INDUSTRY = {"A": "emp_agri", "B": "emp_mining", "C": "emp_manufacturing", "D": "emp_utilities",
                    "E": "emp_utilities", "F": "emp_construction", "G": "emp_trade", "H": "emp_transport",
                    "I": "emp_arts_accommodation", "J": "emp_ict", "K": "emp_finance", "L": "emp_finance",
                    "M": "emp_professional", "N": "emp_professional", "O": "emp_public_admin",
                    "P": "emp_education", "Q": "emp_health", "R": "emp_arts_accommodation", "S": "emp_other",
                    "T": "emp_other", "U": "emp_other"}
# --- LKA: ISCO-08 sub-major groups -> class ("X" = ISCO-6 not further classified in ILOSTAT)
ISCO2_CLASS = {
    "01": "cit_soldiers", "02": "cit_soldiers", "03": "cit_soldiers",
    "11": "cit_administrators", "12": "cit_administrators", "13": "cit_administrators", "14": "cit_administrators",
    "21": "cit_engineers", "22": "cit_healers", "23": "cit_scholars", "24": "cit_administrators",
    "25": "cit_engineers", "26": "cit_scholars",
    "31": "cit_engineers", "32": "cit_healers", "33": "cit_administrators", "34": "cit_artists",
    "35": "cit_engineers",
    "41": "cit_merchants", "42": "cit_merchants", "43": "cit_merchants", "44": "cit_merchants",
    "51": "cit_service", "52": "cit_merchants", "53": "cit_healers", "54": "cit_service",
    "61": "cit_farmers", "62": "cit_farmers", "63": "cit_farmers", "X": "cit_farmers",
    "71": "cit_workers", "72": "cit_workers", "73": "cit_workers", "74": "cit_workers", "75": "cit_workers",
    "81": "cit_workers", "82": "cit_workers", "83": "cit_workers",
    "91": "cit_service", "92": "cit_farmers", "93": "cit_workers", "94": "cit_service", "95": "cit_merchants",
    "96": "cit_service",
}
ISCO1 = [str(i) for i in range(10)]   # "0" = armed forces occupations (& unidentified)

PROVINCES = {"western": "LK1", "central": "LK2", "southern": "LK3", "northern": "LK4", "eastern": "LK5",
             "northwestern": "LK6", "northcentral": "LK7", "uva": "LK8", "sabaragamuwa": "LK9"}
DISTRICT_ALIASES = {"vavunia": "vavuniya", "mullativu": "mullaitivu", "mullaittivu": "mullaitivu",
                    "kilinochchci": "kilinochchi", "moneragala": "monaragala", "rathnapura": "ratnapura",
                    "kegalla": "kegalle", "digamadulla": "ampara", "puttalama": "puttalam",
                    "nuwaraeliya": "nuwaraeliya"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def _num(s: str) -> float:
    s = s.strip()
    if s in ("..", "-", "–", ""):
        return 0.0
    return float(s.replace(",", ""))


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

LKA_BASE = ("Sri Lanka LFS 2024 (DCS), household population aged 15+. District figures are LFS estimates "
            "(appendix Table 12/13) or estimates derived from province tables (14/15); each district value "
            "is distributed to its DS divisions in proportion to Census 2024 DS-division counts (DCS census "
            "data portal) overlaid onto the COD-AB DS geography with WorldPop 100 m population weights.")
US_BASE = ("ACS 2020-2024 5-year estimates, county level (U.S. Census Bureau table-based Summary File), "
           "population aged 16+.")


def _m(key, label, unit, kind, better, desc, lka_method, us_method, lka_src, us_src):
    """One Metric per country (the tables and methods differ)."""
    out = []
    if lka_method:
        out.append(Metric(key, label, unit, "labor", kind, "pop", "admin_adm3", lka_src, "2024", desc,
                          lka_method, better, ["lka"]))
    if us_method:
        out.append(Metric(key, label, unit, "labor", kind, "pop", "admin_adm2", us_src, "2020-2024", desc,
                          us_method, better, ["usa"]))
    return out


_LK = ["dcs_lfs2024", "dcs_census_portal", "worldpop_2020"]
_LKO = _LK + ["ilostat_lfs_lka"]
_US = ["census_acs5_2024"]

CLASS_META = {
    "cit_farmers": ("Farmers (agriculture, forestry & fishery workers)",
                    "Skilled agricultural, forestry & fishery workers and agricultural labourers.",
                    "ISCO-08 major group 6 (61-63 incl. ILO 'not classified' X, which belongs to group 6) + "
                    "elementary workers (group 9) employed in agriculture, taken as ISCO 92 agricultural, "
                    "forestry & fishery labourers, + the remaining ISCO 92 share of non-agricultural elementary "
                    "workers (ILOSTAT 2024: 92 = 888.2k vs 843.0k elementary workers in agriculture).",
                    "C24010 line 031/067 (SOC 45 farming, fishing & forestry)."),
    "cit_workers": ("Workers (trades, operators, labourers)",
                    "Craft & trades workers, plant & machine operators, drivers and labourers in mining, "
                    "construction, manufacturing & transport.",
                    "ISCO-08 groups 7 (71-75) and 8 (81-83) + ISCO 93 (labourers in mining, construction, "
                    "manufacturing & transport; national 2-digit share of non-agricultural elementary workers).",
                    "C24010 lines 032, 033, 035, 036, 037 (+ female 068, 069, 071-073): SOC 47 construction & "
                    "extraction, 49 installation/maintenance/repair, 51 production, 53 transportation & "
                    "material moving."),
    "cit_merchants": ("Merchants (sales & clerical)",
                      "Sales workers and clerical / office support workers.",
                      "ISCO-08 group 4 (41-44 clerical support) + 52 sales workers (national 2-digit share of "
                      "group 5) + 95 street & related sales and service workers (share of non-agricultural "
                      "elementary workers).",
                      "C24010 lines 028, 029 (+064, 065): SOC 41 sales & related, 43 office & administrative support."),
    "cit_engineers": ("Engineers (STEM workforce)",
                      "Science, engineering and ICT professionals and technicians.",
                      "ISCO-08 21 science & engineering professionals + 25 ICT professionals (national 2-digit "
                      "shares of group 2) + 31 science & engineering associate professionals + 35 ICT "
                      "technicians (shares of group 3). ICT groups are included to match US SOC 15.",
                      "C24010 lines 008, 009, 010 (+044-046): SOC 15 computer & mathematical, 17 architecture & "
                      "engineering, 19 life, physical & social science."),
    "cit_scholars": ("Scholars (education, legal & social professions)",
                     "Teachers, lawyers, social, religious and cultural professionals.",
                     "ISCO-08 23 teaching professionals + 26 legal, social & cultural professionals (national "
                     "2-digit shares of group 2). ISCO 26 also contains authors, journalists and creative "
                     "artists (264-265), which cannot be separated at the 2-digit level.",
                     "C24010 lines 012, 013, 014 (+048-050): SOC 21 community & social service, 23 legal, 25 "
                     "educational instruction & library."),
    "cit_healers": ("Healers (health workforce)",
                    "Health professionals, health associate professionals and personal care workers.",
                    "ISCO-08 22 health professionals (share of group 2) + 32 health associate professionals "
                    "(share of group 3) + 53 personal care workers (share of group 5; includes 531 child-care "
                    "workers and teachers' aides, not separable at the 2-digit level).",
                    "C24010 lines 016 (=017+018), 020 (+052, 056): SOC 29 healthcare practitioners & technical, "
                    "31 healthcare support."),
    "cit_service": ("Service workers",
                    "Personal and protective service workers, cleaners, helpers and other elementary service work.",
                    "ISCO-08 51 personal service workers + 54 protective services workers (shares of group 5) + "
                    "91 cleaners & helpers, 94 food preparation assistants, 96 refuse & other elementary workers "
                    "(shares of non-agricultural group 9).",
                    "C24010 lines 021 (=022+023), 024, 025, 026 (+057, 060-062): SOC 33 protective service, 35 "
                    "food preparation & serving, 37 building & grounds cleaning, 39 personal care & service."),
    "cit_administrators": ("Administrators (managers & business professionals)",
                           "Managers, business & administration professionals and associate professionals.",
                           "ISCO-08 group 1 managers (11-14) + 24 business & administration professionals "
                           "(share of group 2) + 33 business & administration associate professionals (share "
                           "of group 3; includes 334 administrative secretaries).",
                           "C24010 lines 005, 006 (+041, 042): SOC 11 management, 13 business & financial operations."),
    "cit_artists": ("Artists (arts, culture, sports & media)",
                    "Arts, design, entertainment, sports and media occupations.",
                    "Proxy: ISCO-08 34 legal, social, cultural & related associate professionals (share of group "
                    "3: 342 sports & fitness, 343 artistic, cultural & culinary associates, but also 341 legal, "
                    "social & religious associates). Creative professionals (ISCO 264-265) are inside ISCO 26 "
                    "(counted as scholars) because ILOSTAT/DCS publish no 3-digit split.",
                    "C24010 line 015 (+051): SOC 27 arts, design, entertainment, sports & media."),
    "cit_soldiers": ("Soldiers (armed forces)",
                     "Members of the armed forces.",
                     "ISCO-08 group 0 armed forces occupations (LFS Table 14; ILOSTAT 01+02 = 41.1k, so the "
                     "'unidentified' part is nil). The LFS covers only the household population, so personnel "
                     "living in barracks are missing: 41k vs 317k armed forces personnel (World Bank "
                     "MS.MIL.TOTL.P1, 2020). Northern province value is not reported ('..') and is 0 here.",
                     "B23025_E006 'In labor force: Armed Forces' (population 16+, includes group quarters)."),
}

IND_META = {
    "emp_agri": ("Agriculture, forestry & fishing jobs", "ISIC A (LFS group 1; exact district values from Table 13)",
                 "C24030 line 004 (+031)"),
    "emp_mining": ("Mining & quarrying jobs", "ISIC B (LFS group 2)", "C24030 line 005 (+032)"),
    "emp_manufacturing": ("Manufacturing jobs", "ISIC C (LFS group 3)", "C24030 line 007 (+034)"),
    "emp_construction": ("Construction jobs",
                         "ISIC F: LFS group 6 (D+E+F) x national F share of D+E+F (ILOSTAT 2024: 544.8k of 604.5k)",
                         "C24030 line 006 (+033)"),
    "emp_utilities": ("Utilities jobs (power, water, waste)",
                      "ISIC D+E: LFS group 6 x national (D+E) share (ILOSTAT 2024: 59.7k of 604.5k)",
                      "C24030 line 012 (+039) utilities (NAICS 22; waste management is in emp_professional)"),
    "emp_trade": ("Wholesale & retail trade jobs", "ISIC G (LFS group 7)", "C24030 lines 008, 009 (+035, 036)"),
    "emp_transport": ("Transport & storage jobs", "ISIC H (LFS group 8)", "C24030 line 011 (+038)"),
    "emp_ict": ("Information & communication jobs", "ISIC J (LFS group 10)", "C24030 line 013 (+040) information"),
    "emp_finance": ("Finance, insurance & real estate jobs",
                    "ISIC K (group 11) + L: LFS 'Other' (L+R+U) x national L share (ILOSTAT 2024: 13.8k of 62.0k)",
                    "C24030 lines 015, 016 (+042, 043)"),
    "emp_professional": ("Professional, scientific, technical & administrative jobs",
                         "ISIC M + N (LFS groups 13, 14)", "C24030 lines 018, 019, 020 (+045-047)"),
    "emp_education": ("Education jobs", "ISIC P (LFS group 16)", "C24030 line 022 (+049)"),
    "emp_health": ("Health & social work jobs", "ISIC Q (LFS group 17)", "C24030 line 023 (+050)"),
    "emp_arts_accommodation": ("Arts, recreation, accommodation & food jobs",
                               "ISIC I (group 9) + R: LFS 'Other' x national R share (ILOSTAT 2024: 44.5k of 62.0k)",
                               "C24030 lines 025, 026 (+052, 053)"),
    "emp_public_admin": ("Public administration & defence jobs",
                         "ISIC O public administration & defence (LFS group 15; includes household-resident "
                         "armed forces)",
                         "C24030 line 028 (+055) public administration + B23025_E006 Armed Forces, so the US value "
                         "matches ISIC section O, which includes defence"),
    "emp_other": ("Other services jobs",
                  "ISIC S + T (groups 19, 20: other services, households as employers) + U: LFS 'Other' x "
                  "national U share (ILOSTAT 2024: 3.7k of 62.0k)",
                  "C24030 line 027 (+054) other services except public administration (includes private "
                  "households)"),
}

METRICS: list[Metric] = []
METRICS += _m("employed", "Employed persons", "persons", "extensive", "neutral",
              "Persons in employment (main job), including members of the armed forces.",
              LKA_BASE + " Table 12 'Employed' (7,949,751 nationally); includes ISCO group 0 armed forces "
              "living in households.",
              US_BASE + " B23025_E004 civilian employed + B23025_E006 Armed Forces, so that, as in the ILO "
              "definition used by Sri Lanka, members of the armed forces are counted as employed; the ten "
              "citizen classes and the fifteen emp_* industries both sum to this value.",
              _LK, _US)
METRICS += _m("labor_force", "Labour force", "persons", "extensive", "neutral",
              "Employed plus unemployed persons (economically active population).",
              LKA_BASE + " Table 12 'Total labour force' (employed + unemployed); DS divisions: employed + "
              "unemployed as distributed below.",
              US_BASE + " B23025_E002 'In labor force' (civilian labour force + Armed Forces).", _LK, _US)
METRICS += _m("unemployed", "Unemployed persons", "persons", "extensive", "lower",
              "Persons without work, available and actively seeking work.",
              LKA_BASE + " Table 12 'Unemployed' by district; distributed to DS divisions with Census 2024 "
              "DS counts of unemployed persons.",
              US_BASE + " B23025_E005 civilian unemployed.", _LK, _US)
METRICS += _m("unemployment_rate", "Unemployment rate", "%", "intensive", "lower",
              "Unemployed as a share of the labour force.",
              LKA_BASE + " unemployed / labour force x 100 (district values equal LFS Table 12/5.3; inside a "
              "district the DS-division rate follows the Census 2024 pattern scaled to the LFS district rate).",
              US_BASE + " B23025_E005 / B23025_E003 x 100 (civilian unemployment rate, the official US "
              "definition; Armed Forces excluded from the denominator).", _LK, _US)
METRICS += _m("lfpr", "Labour force participation rate", "%", "intensive", "higher",
              "Labour force as a share of the working-age population (LKA 15+, USA 16+).",
              LKA_BASE + " labour force / household population aged 15+ x 100 (Table 12; DS-division "
              "population 15+ distributed with Census 2024 'tot_EcoAct').",
              US_BASE + " B23025_E002 / B23025_E001 x 100 (population 16+).", _LK, _US)

_LKA_CLS_BASE = (LKA_BASE + " Occupation: province employment by ISCO-08 major group (LFS Table 14) split to "
                 "district x industry by iterative proportional fitting (seed = district x ISIC-group employment "
                 "x national occupation mix of each industry from ILOSTAT's 2024 LFS cross-tab; margins = "
                 "Table 14 province totals and district x industry totals), then cut into classes with national "
                 "ISCO-08 2-digit shares of LFS 2024 (ILOSTAT). Class mapping: ")
_US_CLS_BASE = US_BASE + " C24010 (civilian employed 16+, male + female lines). Class mapping: "
for k in CLASSES:
    label, desc, lka_m, us_m = CLASS_META[k]
    if k == "cit_soldiers":
        METRICS += _m(k, label, "persons", "extensive", "neutral", desc, LKA_BASE + " " + lka_m,
                      US_BASE + " " + us_m, _LK + ["worldbank_wdi"], _US)
    else:
        METRICS += _m(k, label, "persons", "extensive", "neutral", desc, _LKA_CLS_BASE + lka_m,
                      _US_CLS_BASE + us_m, _LKO, _US)

_LKA_IND_BASE = (LKA_BASE + " Industry: province employment by ISIC Rev.4 group (LFS Table 15) split to districts "
                 "inside each sector block (agriculture / industry / services) in proportion to the district "
                 "sector employment of Table 13; combined LFS groups are split with national ILOSTAT 2024 "
                 "section shares. Definition: ")
_US_IND_BASE = (US_BASE + " C24030 sex by industry for the civilian employed population 16+ (NAICS-based "
                "census industry codes; male + female lines). Definition: ")
for k in INDUSTRIES:
    label, lka_m, us_m = IND_META[k]
    METRICS += _m(k, label, "persons", "extensive", "neutral",
                  f"Employed persons by industry of main job: {label}.",
                  _LKA_IND_BASE + lka_m + ".", _US_IND_BASE + us_m + ".", _LKO, _US)


# ---------------------------------------------------------------------------------------------
# Sri Lanka: LFS 2024 report tables
# ---------------------------------------------------------------------------------------------

def _lfs_pages() -> list[str]:
    pdf = fetch(LFS_URL, "dcs_lfs2024", "lka/labor", "LFS2024.pdf")
    cache = pdf.with_name("LFS2024.pages.json")
    if cache.exists():
        return json.loads(cache.read_text())
    import pdfplumber
    print("  extracting LFS 2024 text ...")
    with pdfplumber.open(pdf) as doc:
        pages = [(p.extract_text() or "") for p in doc.pages]
    cache.write_text(json.dumps(pages))
    return pages


def _table_lines(pages: list[str], title: str, stop: str | None = None) -> list[str]:
    """Lines following `title` on the (non-contents) page that carries the table."""
    for txt in pages:
        if title in txt and "....." not in txt:
            lines = txt.splitlines()
            i = next(j for j, ln in enumerate(lines) if title in ln)
            out = []
            for ln in lines[i + 1:]:
                if stop and stop in ln:
                    break
                out.append(ln.strip())
            return out
    raise ValueError(f"LFS table not found: {title}")


def _district_ids() -> dict[str, str]:
    a = load_admin("lka", "adm2")
    ids = {_norm(n): i for i, n in zip(a.admin_id, a.name)}
    for alias, canon in DISTRICT_ALIASES.items():
        if canon in ids:
            ids[alias] = ids[canon]
    return ids


_NUM = r"([\d,]+|\.\.)"
_PCT = r"([\d.]+|\.\.)"


def lfs_district_status(pages) -> pd.DataFrame:
    """Table 12: labour force status of the household population 15+ by district (both sexes)."""
    lines = _table_lines(pages, "Table 12 - Labour force status", "These figures")
    pat = re.compile(rf"^([A-Za-z][A-Za-z ]*?)\s+{_NUM}\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}\s+{_NUM}$")
    ids = _district_ids()
    rows, total = [], None
    for ln in lines:
        m = pat.match(ln)
        if not m:
            continue
        name = m.group(1)
        vals = [_num(x) for x in m.groups()[1:]]
        rec = dict(zip(["pop15", "labor_force", "lfpr_pub", "employed", "emp_rate", "unemployed",
                        "unemployment_rate_pub", "not_in_lf"], vals))
        if _norm(name) == "total":
            total = rec
            continue
        rec["admin_id"] = ids[_norm(name)]
        rows.append(rec)
    df = pd.DataFrame(rows).set_index("admin_id")
    assert len(df) == 25, f"Table 12: parsed {len(df)} districts"
    for c in ("pop15", "labor_force", "employed", "unemployed"):
        assert abs(df[c].sum() - total[c]) <= 25, f"Table 12 {c}: {df[c].sum()} vs total {total[c]}"
    return df


def lfs_district_sector(pages) -> pd.DataFrame:
    """Table 13: employment by major industry group (agriculture / industry / services) by district."""
    lines = _table_lines(pages, "Table 13 - Employment by major industry group by districts - Both sexes",
                         "Table 13A")
    pat = re.compile(rf"^([A-Za-z][A-Za-z ]*?)\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}$")
    ids = _district_ids()
    rows, total = [], None
    for ln in lines:
        m = pat.match(ln)
        if not m:
            continue
        g = m.groups()
        rec = {"total": _num(g[1]), "agri": _num(g[3]), "industry": _num(g[5]), "services": _num(g[7])}
        if _norm(g[0]) == "total":
            total = rec
            continue
        rec["admin_id"] = ids[_norm(g[0])]
        rows.append(rec)
    df = pd.DataFrame(rows).set_index("admin_id")
    assert len(df) == 25, f"Table 13: parsed {len(df)} districts"
    for c in ("total", "agri", "industry", "services"):
        assert abs(df[c].sum() - total[c]) <= 25, f"Table 13 {c}"
    assert (abs(df[["agri", "industry", "services"]].sum(axis=1) - df.total) <= 3).all()
    return df


def _province_table(pages, title: str, cols: list[str]) -> pd.DataFrame:
    lines = _table_lines(pages, title, "These")
    n = len(cols) + 1
    pat = re.compile(rf"^([A-Za-z][A-Za-z ]*?)" + rf"\s+{_NUM}" * n + "$")
    rows = {}
    for ln in lines:
        m = pat.match(ln)
        if not m:
            continue
        key = _norm(m.group(1))
        vals = [_num(x) for x in m.groups()[1:]]
        pid = "LK" if key == "total" else PROVINCES[key]
        rows[pid] = dict(zip(["total"] + cols, vals))
    df = pd.DataFrame.from_dict(rows, orient="index")
    assert len(df) == 10, f"{title}: parsed {len(df)} rows"
    prov = df.drop(index="LK")
    for c in ["total"] + cols:
        assert abs(prov[c].sum() - df.at["LK", c]) <= 25, f"{title}: column {c} does not add up"
    assert (abs(prov[cols].sum(axis=1) - prov.total) <= 25).all(), f"{title}: rows do not add up"
    return prov


def lfs_province_occupation(pages) -> pd.DataFrame:
    """Table 14: employed by ISCO-08 major group by province (columns '1'..'9', '0')."""
    cols = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    return _province_table(pages, "Table 14 - Currently employed person by occupation group", cols)[["total"] + ISCO1]


def lfs_province_industry(pages) -> pd.DataFrame:
    """Table 15: employed by ISIC Rev.4 industry group by province."""
    return _province_table(pages, "Table 15 - Currently employed person by industry group", LFS_GROUPS)


def lfs_national_2024(pages) -> tuple[pd.Series, pd.Series]:
    """Tables 6 and 7, 2024 rows (national industry groups and ISCO major groups) for cross-checks."""
    t6 = _table_lines(pages, "Table 6 - currently employed person by industry group", "Note")
    t7 = _table_lines(pages, "Table 7 - Currently employed person by occupation group", "Occupation group 1")
    r6 = next(ln for ln in t6 if ln.startswith("2024 "))
    r7 = next(ln for ln in t7 if ln.startswith("2024 "))
    v6 = [_num(x) for x in r6.split()[1:]]
    v7 = [_num(x) for x in r7.split()[1:]]
    return (pd.Series(v6, index=["total"] + LFS_GROUPS), pd.Series(v7, index=["total"] + ["1", "2", "3", "4", "5",
                                                                                         "6", "7", "8", "9", "0"]))


# ---------------------------------------------------------------------------------------------
# Sri Lanka: ILOSTAT tabulations of the same LFS 2024
# ---------------------------------------------------------------------------------------------

def _ilo(indicator: str, year: int = 2024) -> pd.DataFrame:
    p = fetch(ILO_URL, "ilostat_lfs_lka", "lka/labor", f"ilostat_{indicator}_LKA.csv",
              params={"id": indicator, "ref_area": "LKA", "format": ".csv"})
    d = pd.read_csv(p)
    d = d[d.time == year]
    if "sex" in d.columns:
        d = d[d.sex == "SEX_T"]
    return d


def ilo_isco2(year: int = 2024) -> pd.Series:
    d = _ilo("EMP_TEMP_SEX_OC2_NB_A", year)
    s = d.set_index("classif1").obs_value * 1000.0
    s.index = s.index.str.replace("OC2_ISCO08_", "", regex=False)
    return s


def ilo_sections(year: int = 2024) -> pd.Series:
    d = _ilo("EMP_TEMP_SEX_ECO_NB_A", year)
    d = d[d.classif1.str.startswith("ECO_ISIC4_")]
    s = d.set_index("classif1").obs_value * 1000.0
    s.index = s.index.str.replace("ECO_ISIC4_", "", regex=False)
    return s


def ilo_industry_occupation(year: int = 2024) -> pd.DataFrame:
    """ISIC section x ISCO-08 major group (persons); suppressed cells -> 0; X merged into group 6."""
    d = _ilo("EMP_TEMP_ECO_OCU_NB_A", year)
    d = d[d.classif1.str.startswith("ECO_ISIC4_") & d.classif2.str.startswith("OCU_ISCO08_")]
    t = d.pivot_table(index="classif1", columns="classif2", values="obs_value", aggfunc="sum") * 1000.0
    t.index = t.index.str.replace("ECO_ISIC4_", "", regex=False)
    t.columns = t.columns.str.replace("OCU_ISCO08_", "", regex=False)
    t = t.fillna(0.0)
    if "X" in t.columns:
        t["6"] = t.get("6", 0.0) + t["X"]
    return t[ISCO1]


# ---------------------------------------------------------------------------------------------
# Sri Lanka: Census 2024 DS-division counts and the 2024-DS -> COD-AB DS crosswalk
# ---------------------------------------------------------------------------------------------

def _portal_ds_geometry() -> gpd.GeoDataFrame:
    p = fetch(PORTAL_DS_TOPO, "dcs_census_portal", "lka/census_portal", "2024-dsdivisions-topo.json")
    g = gpd.read_file(p)
    if g.crs is None:
        g = g.set_crs(4326)
    g["ADM_ID"] = g["ADM_ID"].astype(str)
    g["geometry"] = g.geometry.make_valid()
    return g[["ADM_ID", "ADM_NAME", "BELONGS_TO", "geometry"]]


def portal_ds_values(indicators: list[str]) -> pd.DataFrame:
    g = _portal_ds_geometry()
    ids = sorted(g.ADM_ID)
    out = {}
    for ind in indicators:
        body = json.dumps({"year": 2024, "indicator": ind, "ids": ids, "table": "ds_table_2024"})
        p = fetch(PORTAL_FETCH, "dcs_census_portal", "lka/census_portal", f"ds_table_2024_{ind}.json",
                  method="POST", data=body, headers={"Content-Type": "application/json"}, timeout=120)
        rows = json.loads(p.read_text())
        out[ind] = pd.Series({str(r["ADM_ID"]): float(r["value"]) for r in rows if r.get("value") not in (None, "")})
    df = pd.DataFrame(out)
    df.index.name = "ds24"
    missing = set(ids) - set(df.index)
    assert not missing, f"census portal: no values for {sorted(missing)[:5]}"
    return df


def ds24_to_cod() -> pd.DataFrame:
    """Population-weighted shares (ds24 -> COD adm3) from rasterizing both geographies on WorldPop 100 m."""
    import rasterio
    from rasterio import features

    url, sub, name = POP_RASTER["lka"]
    tif = fetch(url, "worldpop_2020", sub, name)
    cod = load_admin("lka", "adm3")
    g24 = _portal_ds_geometry()
    with rasterio.open(tif) as src:
        pop = src.read(1, masked=True)
        transform, shape = src.transform, src.shape
    popv = np.where(pop.mask, 0.0, pop.filled(0)).astype(np.float64)
    popv[~np.isfinite(popv) | (popv < 0)] = 0

    def labels(gdf, idcol):
        codes = {v: i + 1 for i, v in enumerate(gdf[idcol])}
        lab = features.rasterize(((geom, codes[v]) for geom, v in zip(gdf.geometry, gdf[idcol])),
                                 out_shape=shape, transform=transform, fill=0, dtype="int32")
        miss = (lab == 0) & (popv > 0)
        if miss.any():
            lab2 = features.rasterize(((geom, codes[v]) for geom, v in zip(gdf.geometry, gdf[idcol])),
                                      out_shape=shape, transform=transform, fill=0, dtype="int32",
                                      all_touched=True)
            lab = np.where(miss, lab2, lab)
        return lab, {i: v for v, i in codes.items()}

    lab_c, inv_c = labels(cod, "admin_id")
    lab_2, inv_2 = labels(g24, "ADM_ID")
    m = (lab_c > 0) & (lab_2 > 0) & (popv > 0)
    df = pd.DataFrame({"ds24": lab_2[m], "adm3": lab_c[m], "pop": popv[m]})
    df = df.groupby(["ds24", "adm3"], as_index=False)["pop"].sum()
    df["ds24"] = df.ds24.map(inv_2)
    df["adm3"] = df.adm3.map(inv_c)
    df["share"] = df["pop"] / df.groupby("ds24")["pop"].transform("sum")
    # drop boundary slivers (generalised portal outlines vs COD-AB lines), keep each unit's main link
    main = df.sort_values("share", ascending=False).drop_duplicates("ds24")
    keep = df[df.share >= 0.05]
    # a COD unit must receive something: keep its largest incoming link
    inc = df.sort_values("pop", ascending=False).drop_duplicates("adm3")
    keep = pd.concat([keep, main, inc]).drop_duplicates(["ds24", "adm3"])
    keep = keep.copy()
    keep["share"] = keep["pop"] / keep.groupby("ds24")["pop"].transform("sum")
    missing_cod = set(cod.admin_id) - set(keep.adm3)
    missing_24 = set(g24.ADM_ID) - set(keep.ds24)
    assert not missing_cod and not missing_24, (sorted(missing_cod)[:5], sorted(missing_24)[:5])
    return keep[["ds24", "adm3", "share"]]


# ---------------------------------------------------------------------------------------------
# Sri Lanka: estimation
# ---------------------------------------------------------------------------------------------

def ipf3(seed: np.ndarray, m_dg: np.ndarray, m_o: np.ndarray, iters: int = 2000, tol: float = 1e-7):
    """Rake a (district, industry, occupation) array to margins over occupation (d,g) and over (d,g) (o)."""
    X = seed.astype(float).copy()
    err = np.inf
    for _ in range(iters):
        s = X.sum(axis=(0, 1))
        f = np.divide(m_o, s, out=np.zeros_like(m_o, dtype=float), where=s > 0)
        X *= f[None, None, :]
        s = X.sum(axis=2)
        f = np.divide(m_dg, s, out=np.zeros_like(m_dg, dtype=float), where=s > 0)
        X *= f[:, :, None]
        err = max(np.abs(X.sum(axis=(0, 1)) - m_o).max(), np.abs(X.sum(axis=2) - m_dg).max())
        if err <= tol * max(m_o.sum(), 1.0):
            break
    return X, err


def lka_district_estimates() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    pages = _lfs_pages()
    st = lfs_district_status(pages)
    sec = lfs_district_sector(pages)
    occ = lfs_province_occupation(pages)
    ind = lfs_province_industry(pages)
    nat_ind, nat_occ = lfs_national_2024(pages)
    adm2 = load_admin("lka", "adm2").set_index("admin_id")
    parent = adm2.parent_id

    assert (abs(st.employed - sec.total.reindex(st.index)) <= 3).all(), "Table 12 vs 13 employed differ"
    # province sector totals agree between Table 13 (districts) and Table 15 (province groups)
    sec_p = sec.groupby(parent.reindex(sec.index)).sum()
    for s in ("agri", "industry", "services"):
        gp = ind[[g for g in LFS_GROUPS if GROUP_SECTOR[g] == s]].sum(axis=1)
        diff = (sec_p[s] - gp.reindex(sec_p.index)).abs().max()
        assert diff <= 30, f"sector {s}: Table 13 vs Table 15 differ by {diff}"

    # ---- ILOSTAT national tabulations
    isco2 = ilo_isco2()
    sections = ilo_sections()
    io = ilo_industry_occupation()
    assert abs(isco2["TOTAL"] - nat_occ["total"]) < 2000, "ILOSTAT total differs from LFS report"
    # P(occupation | LFS industry group)
    p_og = {}
    for g, secs in GROUP_SECTIONS.items():
        row = io.loc[[s for s in secs if s in io.index]].sum()
        if row.sum() <= 0:
            row = io.sum()
        p_og[g] = (row / row.sum()).to_numpy()

    # ---- industry groups -> districts (sector blocks)
    emp_dg = pd.DataFrame(0.0, index=st.index, columns=LFS_GROUPS)
    for d in st.index:
        p = parent[d]
        for g in LFS_GROUPS:
            s = GROUP_SECTOR[g]
            block = ind.loc[p, [h for h in LFS_GROUPS if GROUP_SECTOR[h] == s]].sum()
            emp_dg.at[d, g] = sec.at[d, s] * ind.at[p, g] / block if block > 0 else 0.0

    # ---- occupation x industry per district (IPF inside each province)
    X_all = {}
    max_err = 0.0
    for p in sorted(occ.index):
        ds = [d for d in st.index if parent[d] == p]
        m_dg = emp_dg.loc[ds].to_numpy()
        m_o = occ.loc[p, ISCO1].to_numpy(float)
        m_o = m_o * m_dg.sum() / m_o.sum()          # remove rounding differences between tables
        seed = m_dg[:, :, None] * np.stack([p_og[g] for g in LFS_GROUPS])[None, :, :]
        X, err = ipf3(seed, m_dg, m_o)
        max_err = max(max_err, err)
        for i, d in enumerate(ds):
            X_all[d] = X[i]                          # (group, occupation)

    # ---- 2-digit shares and class assignment
    def shares(major: str) -> dict[str, float]:
        sub = isco2[[k for k in isco2.index if len(k) == 2 and k[0] == major]]
        if major == "6" and "X" in isco2.index:
            sub = pd.concat([sub, isco2[["X"]]])
        return (sub / sub.sum()).to_dict()

    major_class = {}
    for major in ISCO1:
        if major == "9":
            continue
        cls = {}
        for k, v in shares(major).items():
            c = ISCO2_CLASS[k]
            cls[c] = cls.get(c, 0.0) + v
        major_class[major] = cls
    # elementary occupations: agriculture -> ISCO 92; non-agriculture -> national residual 2-digit shares
    e_agri = float(io.at["A", "9"])
    nonag = {k: isco2[k] for k in ("91", "92", "93", "94", "95", "96")}
    nonag["92"] = max(nonag["92"] - e_agri, 0.0)
    tot = sum(nonag.values())
    cls9_nonag = {}
    for k, v in nonag.items():
        c = ISCO2_CLASS[k]
        cls9_nonag[c] = cls9_nonag.get(c, 0.0) + v / tot

    gi = {g: i for i, g in enumerate(LFS_GROUPS)}
    rows = []
    for d in st.index:
        X = X_all[d]
        rec = {c: 0.0 for c in CLASSES}
        for j, major in enumerate(ISCO1):
            col = X[:, j]
            if major == "9":
                rec["cit_farmers"] += col[gi["1"]]
                rest = col.sum() - col[gi["1"]]
                for c, s in cls9_nonag.items():
                    rec[c] += rest * s
            else:
                for c, s in major_class[major].items():
                    rec[c] += col.sum() * s
        for j, major in enumerate(ISCO1):
            rec[f"isco_{major}"] = X[:, j].sum()
        # industries: split combined LFS groups with national ILOSTAT section shares
        indrec = {k: 0.0 for k in INDUSTRIES}
        for g in LFS_GROUPS:
            secs = [s for s in GROUP_SECTIONS[g] if s in sections.index]
            w = sections[secs]
            for s in secs:
                indrec[SECTION_INDUSTRY[s]] += emp_dg.at[d, g] * (w[s] / w.sum())
        rec.update(indrec)
        rec["admin_id"] = d
        rows.append(rec)
    est = pd.DataFrame(rows).set_index("admin_id")

    out = pd.DataFrame(index=st.index)
    out["employed"] = st.employed
    out["labor_force"] = st.labor_force
    out["unemployed"] = st.unemployed
    out["pop15"] = st.pop15
    out["unemployment_rate"] = 100 * st.unemployed / st.labor_force
    out["lfpr"] = 100 * st.labor_force / st.pop15
    for c in CLASSES + INDUSTRIES:
        out[c] = est[c]
    out["sector_agri"] = sec.agri
    out["sector_industry"] = sec.industry
    out["sector_services"] = sec.services
    for major in ISCO1:
        out[f"isco_{major}"] = est[f"isco_{major}"]

    prov = pd.concat([occ.add_prefix("isco_"), ind.drop(columns="total").add_prefix("isic_group_")], axis=1)
    diag = {"ipf_max_abs_error": float(max_err), "nat_ind": nat_ind, "nat_occ": nat_occ,
            "isco2": isco2, "sections": sections}
    return out, prov, diag


def build_lka() -> None:
    cc = "lka"
    dist, prov, diag = lka_district_estimates()
    print(f"  LFS 2024 parsed: employed {dist.employed.sum():,.0f}, labour force {dist.labor_force.sum():,.0f}, "
          f"unemployment {100 * dist.unemployed.sum() / dist.labor_force.sum():.2f}%  (IPF max error "
          f"{diag['ipf_max_abs_error']:.3g} persons)")

    # ---- Census 2024 DS-division distribution
    cen = portal_ds_values(["tot_EcoAct", "tot_EcoAct_Emp", "tot_EcoAct_Unemp"])
    print(f"  Census 2024 DS counts: employed {cen.tot_EcoAct_Emp.sum():,.0f}, unemployed "
          f"{cen.tot_EcoAct_Unemp.sum():,.0f}, persons 15+ {cen.tot_EcoAct.sum():,.0f} ({len(cen)} DS divisions)")
    xw = ds24_to_cod()
    n_multi = int((xw.groupby("ds24").size() > 1).sum())
    print(f"  2024 DS -> COD-AB DS crosswalk: {len(xw)} links ({n_multi} census DS split over >1 COD DS)")
    c = xw.merge(cen, left_on="ds24", right_index=True)
    vals = ["tot_EcoAct", "tot_EcoAct_Emp", "tot_EcoAct_Unemp"]
    for v in vals:
        c[v] = c[v] * c.share
    cod = c.groupby("adm3")[vals].sum()
    adm3 = load_admin(cc, "adm3").set_index("admin_id")
    cod = cod.reindex(adm3.index).fillna(0.0)
    cod["district"] = adm3.parent_id

    def distribute(dist_val: pd.Series, weight: pd.Series) -> pd.Series:
        wsum = weight.groupby(cod.district).transform("sum")
        share = np.where(wsum > 0, weight / wsum.replace(0, np.nan), 0.0)
        return cod.district.map(dist_val) * share

    out = pd.DataFrame(index=adm3.index)
    out["employed"] = distribute(dist.employed, cod.tot_EcoAct_Emp)
    out["unemployed"] = distribute(dist.unemployed, cod.tot_EcoAct_Unemp)
    out["labor_force"] = out.employed + out.unemployed
    pop15 = distribute(dist.pop15, cod.tot_EcoAct)
    out["unemployment_rate"] = 100 * out.unemployed / out.labor_force.replace(0, np.nan)
    out["lfpr"] = 100 * out.labor_force / pop15.replace(0, np.nan)
    emp_share = out.employed / cod.district.map(dist.employed)
    for k in CLASSES + INDUSTRIES:
        out[k] = cod.district.map(dist[k]) * emp_share
    out.index.name = "admin_id"
    out = out.reset_index()
    util.write_admin(cc, DOMAIN, "adm3", out)

    d2 = dist.copy()
    d2.index.name = "admin_id"
    util.write_admin(cc, DOMAIN, "adm2", d2.reset_index())
    p1 = prov.copy()
    p1.index.name = "admin_id"
    util.write_admin(cc, DOMAIN, "adm1", p1.reset_index())

    # ---- checks against the published national tables
    tot = out[["employed", "labor_force", "unemployed"] + CLASSES + INDUSTRIES].sum()
    nat_occ, nat_ind = diag["nat_occ"], diag["nat_ind"]
    assert abs(tot[CLASSES].sum() - tot.employed) < 5, "classes do not sum to employed"
    assert abs(tot[INDUSTRIES].sum() - tot.employed) < 5, "industries do not sum to employed"
    assert abs(tot.employed - nat_occ["total"]) < 30, "employed differs from LFS national total"
    assert abs(dist[[f"isco_{m}" for m in ISCO1]].sum().to_numpy() - nat_occ[ISCO1].to_numpy()).max() < 50
    assert abs(tot.emp_agri - nat_ind["1"]) < 30 and abs(tot.emp_manufacturing - nat_ind["3"]) < 30
    assert abs(tot.emp_construction + tot.emp_utilities - nat_ind["6"]) < 30
    print("  national totals: " + ", ".join(f"{k}={tot[k]:,.0f}" for k in ["employed"] + CLASSES))
    print("  industries:      " + ", ".join(f"{k[4:]}={tot[k]:,.0f}" for k in INDUSTRIES))
    lf_wb, y = util.wb_value("SL.TLF.TOTL.IN", "LKA", 2024)
    ur_wb, _ = util.wb_value("SL.UEM.TOTL.NE.ZS", "LKA", 2024)
    pr_wb, _ = util.wb_value("SL.TLF.CACT.NE.ZS", "LKA", 2024)
    mil, ym = util.wb_value("MS.MIL.TOTL.P1", "LKA")
    print(f"  World Bank {y}: labour force {lf_wb:,.0f} (ours {tot.labor_force:,.0f}); unemployment "
          f"{ur_wb:.2f}% (ours {100 * tot.unemployed / tot.labor_force:.2f}%); LFPR {pr_wb:.1f}% (ours "
          f"{100 * tot.labor_force / dist.pop15.sum():.1f}%); armed forces {mil:,.0f} ({ym}) vs LFS household "
          f"armed forces {tot.cit_soldiers:,.0f}")
    write_domain_meta(interim_dir(cc, DOMAIN), METRICS, SOURCES, cc)


# ---------------------------------------------------------------------------------------------
# United States: ACS 2020-2024 5-year
# ---------------------------------------------------------------------------------------------

def _acs_labels(table: str) -> dict[int, str]:
    p = fetch(ACS_SHELLS, f"census_acs5_{ACS_YEAR}", f"usa/acs{ACS_YEAR}")
    d = pd.read_csv(p, sep="|", dtype=str)
    d = d[d["Table ID"] == table]
    return {int(u.split("_")[1]): lab.strip() for u, lab in zip(d["Unique ID"], d["Label"])}


def build_usa() -> None:
    cc = "usa"
    ids = load_admin(cc, "adm2").admin_id
    b = util.acs_table("B23025", ACS_YEAR).reindex(ids)
    occ = util.acs_table("C24010", ACS_YEAR).reindex(ids)
    ind = util.acs_table("C24030", ACS_YEAR).reindex(ids)
    for name, t in (("B23025", b), ("C24010", occ), ("C24030", ind)):
        n_bad = int(t.isna().any(axis=1).sum())
        assert n_bad == 0, f"{name}: {n_bad} counties without values"

    # check the line labels the mapping relies on (table shells of the same release)
    lab_o, lab_i, lab_b = _acs_labels("C24010"), _acs_labels("C24030"), _acs_labels("B23025")
    assert lab_o[2].startswith("Male") and lab_o[38].startswith("Female"), "C24010 layout changed"
    assert lab_i[2].startswith("Male") and lab_i[29].startswith("Female"), "C24030 layout changed"
    assert lab_b[6].startswith("Armed Forces") and lab_b[4].startswith("Employed"), "B23025 layout changed"
    for k, lines in US_OCC.items():
        for n in lines:
            assert lab_o[n] == lab_o[n + 36], f"C24010 male/female line mismatch {n}"
    for k, lines in US_IND.items():
        for n in lines:
            assert lab_i[n] == lab_i[n + 27], f"C24030 male/female line mismatch {n}"

    def oc(n):
        return occ[f"C24010_E{n:03d}"] + occ[f"C24010_E{n + 36:03d}"]

    def ic(n):
        return ind[f"C24030_E{n:03d}"] + ind[f"C24030_E{n + 27:03d}"]

    out = pd.DataFrame(index=ids)
    af = b["B23025_E006"]
    civ_emp = b["B23025_E004"]
    out["employed"] = civ_emp + af
    out["labor_force"] = b["B23025_E002"]
    out["unemployed"] = b["B23025_E005"]
    out["unemployment_rate"] = 100 * b["B23025_E005"] / b["B23025_E003"].replace(0, np.nan)
    out["lfpr"] = 100 * b["B23025_E002"] / b["B23025_E001"].replace(0, np.nan)
    for k, lines in US_OCC.items():
        out[k] = sum(oc(n) for n in lines)
    out["cit_soldiers"] = af
    for k, lines in US_IND.items():
        out[k] = sum(ic(n) for n in lines)
    out["emp_public_admin"] = out["emp_public_admin"] + af
    out = out[["employed", "labor_force", "unemployed", "unemployment_rate", "lfpr"] + CLASSES + INDUSTRIES]

    # consistency: classes vs C24010 total, industries vs C24030 total, both vs B23025 civilian employed
    civ_cls = out[[c for c in CLASSES if c != "cit_soldiers"]].sum(axis=1)
    assert (civ_cls - occ["C24010_E001"]).abs().max() < 0.5, "occupation classes do not sum to C24010 total"
    assert (out[INDUSTRIES].sum(axis=1) - af - ind["C24030_E001"]).abs().max() < 0.5, "industries != C24030 total"
    d_occ = (occ["C24010_E001"] - civ_emp).abs().max()
    d_ind = (ind["C24030_E001"] - civ_emp).abs().max()
    print(f"  max |C24010 total - B23025 civilian employed| = {d_occ:.0f}; C24030: {d_ind:.0f}")
    out.index.name = "admin_id"
    util.write_admin(cc, DOMAIN, "adm2", out.reset_index())

    tot = out.sum()
    print(f"  employed {tot.employed:,.0f} (civilian {civ_emp.sum():,.0f} + armed forces {af.sum():,.0f}); "
          f"labour force {tot.labor_force:,.0f}; unemployment "
          f"{100 * tot.unemployed / b['B23025_E003'].sum():.2f}%; LFPR {100 * tot.labor_force / b['B23025_E001'].sum():.1f}%")
    print("  classes:    " + ", ".join(f"{k[4:]}={tot[k]:,.0f}" for k in CLASSES))
    print("  industries: " + ", ".join(f"{k[4:]}={tot[k]:,.0f}" for k in INDUSTRIES))
    for y in (2022, 2024):
        lf_wb, _ = util.wb_value("SL.TLF.TOTL.IN", "USA", y)
        ur_wb, _ = util.wb_value("SL.UEM.TOTL.NE.ZS", "USA", y)
        pr_wb, _ = util.wb_value("SL.TLF.CACT.NE.ZS", "USA", y)
        print(f"  World Bank {y}: labour force {lf_wb:,.0f}; unemployment {ur_wb:.2f}%; LFPR (15+) {pr_wb:.1f}%")
    write_domain_meta(interim_dir(cc, DOMAIN), METRICS, SOURCES, cc)


def build(country: str) -> None:
    print(f"[labor] building {country}")
    if country == "lka":
        build_lka()
    elif country == "usa":
        build_usa()
    else:
        raise ValueError(country)


if __name__ == "__main__":
    import sys

    from .. import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    for cc in ccs:
        build(cc)
    sys.exit(validate.main([DOMAIN] + ccs))
