"""Society domain (Religion & Ethnicity lenses; Faith yield inputs).

Outputs (data/interim/<country>/society/):
  LKA admin_adm3.csv        Census of Population and Housing 2024 religion and ethnic group by DS division
                            (DCS Census Data Portal), Gini-Simpson diversity, OSM places of worship per DS division
  LKA points_worship.csv    OpenStreetMap amenity=place_of_worship (kind = religion)
  USA admin_adm2.csv        2020 U.S. Religion Census (ASARB / ARDA RCMS county file): adherents by tradition and
                            selected groups as % of population, unclaimed share, congregations; ACS 2020-2024
                            B03002 race / Hispanic origin; Gini-Simpson diversity
  both points_holy_sites.csv  Wikidata religious sites ranked by number of Wikipedia/Wikimedia sitelinks

Sri Lanka DS divisions: the census portal publishes 340 2024 DS divisions; they are matched by name within
district to the 339 COD-AB DS divisions (Kalmunai North Sub-division is summed into COD-AB Kalmunai), and each
match is checked for spatial overlap.  District sums are cross-checked against the Final Report Tables 6.1.11
and 6.1.15.

Connecticut: the Religion Census uses the 8 pre-2022 counties (09001-09015).  Counts are moved to the 9 planning
regions (09110-09190) with the Census Bureau's county -> county-subdivision crosswalk and 2020 town populations
(PEP ESTIMATESBASE2020); every town nests in exactly one old county and one planning region.

    python -m civmap.domains.society        # builds lka + usa, then validates
"""
from __future__ import annotations

import difflib
import io
import json
import re
import time
import zipfile
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import config, schema, util
from ..core import load_admin
from ..fetch import fetch, unzip
from ..schema import Metric, Source

DOMAIN = "society"
society = DOMAIN  # module-shape alias

PORTAL = "https://www.statistics.gov.lk/DashBoard/censusdataportal/"
CPH2024_REPORT = "https://www.statistics.gov.lk/Resource/en/Population/CPH_2024/CPH2024_Final_Eng.pdf"
HOT_API = "https://api-prod.raw-data.hotosm.org/v1/"
HOT_CULTURAL_GPKG = ("https://production-raw-data-api.s3.amazonaws.com/ISO3/LKA/cultural_places/"
                     "hotosm_lka_cultural_places_osm_gpkg.zip")
HOT_SNAPSHOT_NAME = "hot_rawdata_lka_place_of_worship.zip"
RCMS_DTA = "https://osf.io/download/jc2yv"        # ARDA RCMSCY20, completely labelled Stata file
RCMS_CODEBOOK = "https://osf.io/download/wekxa"   # ARDA RCMSCY20, variable descriptions
CT_XWALK = "https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.txt"
CT_SUBEST = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023_9.csv"
ACS_YEAR = 2024
N_HOLY = {"lka": 30, "usa": 80}

# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------

SOURCES = [
    Source("dcs_census_portal", "Census Data Portal - Census of Population and Housing 2024 (DS-division tables)",
           "Department of Census and Statistics Sri Lanka", PORTAL, "open (DCS)", "2024", ["lka"],
           "fetch_data.php with table ds_table_2024 for the 'Religious Affiliation of Population' indicators "
           "tot_Ra, tot_Ra_Buddhist, tot_Ra_Hindu, tot_Ra_Islam, tot_Ra_Roman_Catholic, tot_Ra_Other_Christian, "
           "tot_Ra_Other and the 'Ethnic Group of Population' indicators tot_Eth, tot_Eth_Sinhalese, tot_Eth_SL_Tamil, "
           "tot_Eth_Ind_Tamil, tot_Eth_SL_Moor, tot_Eth_Burgher, tot_Eth_Malay, tot_Eth_SL_Chetty, tot_Eth_Bharatha, "
           "tot_Eth_Veddhas, tot_Eth_Other (persons) for the 340 DS divisions; DS codes, names and outlines from "
           "2024-dsdivisions-topo.js."),
    Source("dcs_cph2024", "Census of Population and Housing 2024 - Final Report (English)",
           "Department of Census and Statistics Sri Lanka",
           "https://www.statistics.gov.lk/Population/StaticalInformation/CPH2024", "open (DCS)", "2024", ["lka"],
           "CPH2024_Final_Eng.pdf Table 6.1.11 (population by ethnic group and district, 2012 and 2024) and Table "
           "6.1.15 (population by religion and district, 2024), parsed from the PDF text layer and used only to "
           "cross-check the DS-division portal values summed to districts."),
    Source("ocha_codab_lka", "Sri Lanka - Subnational Administrative Boundaries (COD-AB, v03)",
           "OCHA FISS / Survey Department of Sri Lanka & Department of Census and Statistics",
           "https://data.humdata.org/dataset/cod-ab-lka", "CC BY-IGO", "2022-08-16", ["lka"],
           "DS-division (ADM3) polygons used for the name match check and the spatial join of places of worship "
           "(via civmap.core)."),
    Source("hot_rawdata_api", "HOT Raw Data API - OpenStreetMap snapshot (amenity=place_of_worship, Sri Lanka)",
           "Humanitarian OpenStreetMap Team (data: OpenStreetMap contributors)",
           "https://api-prod.raw-data.hotosm.org/v1/docs", "ODbL 1.0", "OSM snapshot 2026-11-26", ["lka"],
           "POST /v1/snapshot/ with the COD-AB national outline (buffered 0.02 deg, simplified), tags filter "
           "amenity=place_of_worship and attributes name, name:en, name:si, name:ta, amenity, religion, denomination, "
           "building, historic, wikidata; centroids of ways/relations. Gives the OSM religion tag that the HDX "
           "pre-built exports do not carry."),
    Source("hotosm_lka_cultural_places", "Sri Lanka Cultural Places (OpenStreetMap Export)",
           "Humanitarian OpenStreetMap Team via HDX (data: OpenStreetMap contributors)",
           "https://data.humdata.org/dataset/hotosm_lka_cultural_places", "ODbL 1.0", "2026-11-04 export", ["lka"],
           "GeoPackage export; amenity=place_of_worship features (9,337 in the 2026-11-04 export). The export has no "
           "religion tag. Used as a cross-check of the snapshot count and as the fallback input when the Raw Data API "
           "is unavailable (religion then inferred from names only)."),
    Source("arda_rcms2020_county", "U.S. Religion Census - Religious Congregations and Membership Study, 2020 "
           "(County File)", "Association of Statisticians of American Religious Bodies (ASARB); distributed by the "
           "Association of Religion Data Archives (ARDA)",
           "https://www.thearda.com/data-archive?fid=RCMSCY20", "free for research with attribution (ARDA terms)",
           "2020", ["usa"],
           "File RCMSCY20 (Stata, osf.io/download/jc2yv; codebook osf.io/download/wekxa). 3,143 counties (2020 "
           "geography: Connecticut's 8 old counties, Alaska 02063/02066). Variables: POP2020, TOTCNG_2020, "
           "TOTADH_2020, tradition adherents EVANADH/MPRTADH/BPRTADH/CATHADH/ORTHADH/OTHADH_2020 and group "
           "adherents LDSADH, CJUDADH, IJUDADH, OJUDADH, RJUDADH, RFRMADH, MSLMADH (estimate), MAHBUDADH, THBUDADH, "
           "VAJBUDADH, HINTADH, HINYMADH (_2020). Data were collected by the ASARB (C. Grammich, E. Dollhopf, M. "
           "Gautier, R. Houseal, D. Jones, A. Krindatch, R. Stanley, S. Thumma) and downloaded from the ARDA."),
    Source("census_ct_xwalk", "Connecticut county to county subdivision crosswalk (old counties -> planning regions)",
           "U.S. Census Bureau, Geography Division", CT_XWALK, "public domain", "2022", ["usa"],
           "One row per town: OLD_COUNTYFP (09001-09015), NEW_COUNTYFP (planning region 110-190), COUSUBFP."),
    Source("census_pep_subcounty_2023", "City and Town Population Totals: 2020-2023, Connecticut county "
           "subdivisions (sub-est2023_9.csv)", "U.S. Census Bureau, Population Estimates Program", CT_SUBEST,
           "public domain", "April 1, 2020 estimates base", ["usa"],
           "SUMLEV 061 rows (towns); ESTIMATESBASE2020 used as the population weight of each town."),
    Source("census_acs5_2024", "American Community Survey 5-year estimates 2020-2024, table B03002 "
           "(Hispanic or Latino origin by race)", "U.S. Census Bureau",
           "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/", "public domain",
           "2020-2024", ["usa"], "Table-based Summary File, county rows (cb_2023 GEOIDs, Connecticut planning regions)."),
    Source("wikidata", "Wikidata (SPARQL query service)", "Wikimedia Foundation", "https://query.wikidata.org",
           "CC0", "queried 2026-11", ["lka", "usa"],
           "Religious sites: items with P17 = Sri Lanka (Q854) / United States (Q30) that are P31/P279* instances of "
           "temple Q44539, Buddhist temple Q5393308, Hindu temple Q842402, kovil Q17059988, stupa Q180987, mosque "
           "Q32815, church building Q16970, cathedral Q2977, basilica Q163687, synagogue Q34627, shrine Q697295 or "
           "pilgrimage site Q15135589; coordinates P625, religion P140, sitelinks (wikibase:sitelinks)."),
    Source("worldbank_wdi", "World Development Indicators", "World Bank", "https://data.worldbank.org",
           "CC BY 4.0", "latest", ["lka", "usa"],
           "SP.POP.TOTL used only for the national sanity checks printed by build()."),
]

# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

_LKA_SRC = ["dcs_census_portal", "dcs_cph2024", "ocha_codab_lka"]
_LKA_REL_M = ("Census of Population and Housing 2024 (DCS), religious affiliation of the total population by DS "
              "division from the Census Data Portal (ds_table_2024, persons). The 340 portal DS divisions are matched "
              "by name within district to the 339 COD-AB DS divisions (Kalmunai North Sub-division summed into COD-AB "
              "Kalmunai); counts are summed per COD-AB unit and divided by the unit's total (tot_Ra). District sums "
              "agree with Final Report Table 6.1.15. Hexes: population-weighted average of the DS-division shares.")
_LKA_ETH_M = ("Census of Population and Housing 2024 (DCS), ethnic group of the total population by DS division "
              "from the Census Data Portal (ds_table_2024, persons), matched to COD-AB DS divisions as for religion and "
              "divided by tot_Eth. District sums agree with Final Report Table 6.1.11. Note: the 2024 census reports "
              "'Indian Tamil' as 'Indian Tamil/Malaiyaga Thamilar'; the 2012->2024 fall in this group and rise in Sri "
              "Lankan Tamils in the hill country partly reflect self-identification. Hexes: population-weighted average.")
_US_RCMS_SRC = ["arda_rcms2020_county", "census_ct_xwalk", "census_pep_subcounty_2023"]
_US_RCMS_M = ("2020 U.S. Religion Census (ASARB, ARDA county file RCMSCY20): adherents (members, their children and "
              "regular attenders counted at congregations) divided by the 2020 census population POP2020 x 100. "
              "Adherents are counted where the congregation meets, so in 30 small counties adherents exceed residents; "
              "there shares are divided by total adherents instead (claimed shares then sum to 100 and unclaimed is 0). "
              "Groups that did not report are not counted (adherents, not self-identification). Connecticut: the 8 "
              "old counties (09001-09015) are split to the 9 planning regions with the Census CT county->town "
              "crosswalk, weighting each town by its April 2020 population (PEP ESTIMATESBASE2020), and shares are "
              "recomputed from the allocated counts. Hexes: population-weighted average of county shares.")
_US_ACS_M = ("ACS 2020-2024 5-year table B03002 (Hispanic or Latino origin by race), county estimate divided by "
             "the total population B03002_001 x 100. The six groups are mutually exclusive and sum to 100: Hispanic "
             "or Latino of any race, and non-Hispanic White alone, Black alone, Asian alone, American Indian/Alaska "
             "Native + Native Hawaiian/Pacific Islander alone, and some other race or two or more races. "
             "Hexes: population-weighted average.")


def _metric(key, label, unit, category, kind, weight, table, sources, vintage, desc, method, better, cc):
    return Metric(key, label, unit, category, kind, weight, table, sources, vintage, desc, method, better, [cc])


def _lka_rel(key, label, desc, cats):
    return _metric(key, label, "%", "society", "intensive", "pop", "admin_adm3", _LKA_SRC, "2024",
                   desc + " (Census 2024 self-report).", _LKA_REL_M + f" Categories: {cats}.", "neutral", "lka")


def _lka_eth(key, label, desc, cats):
    return _metric(key, label, "%", "society", "intensive", "pop", "admin_adm3", _LKA_SRC, "2024",
                   desc + " (Census 2024 self-report).", _LKA_ETH_M + f" Categories: {cats}.", "neutral", "lka")


def _us_rel(key, label, vars_, desc):
    return _metric(key, label, "%", "society", "intensive", "pop", "admin_adm2", _US_RCMS_SRC, "2020", desc,
                   _US_RCMS_M + f" Variables: {vars_}.", "neutral", "usa")


def _us_eth(key, label, cols, desc):
    return _metric(key, label, "%", "society", "intensive", "pop", "admin_adm2", ["census_acs5_2024"], "2020-2024",
                   desc, _US_ACS_M + f" This group: {cols}.", "neutral", "usa")


METRICS = [
    # ---- Sri Lanka: religion (Census 2024, DS division)
    _lka_rel("rel_buddhist_pct", "Buddhist (% of population)", "Share of residents who are Buddhist",
             "tot_Ra_Buddhist"),
    _lka_rel("rel_hindu_pct", "Hindu (% of population)", "Share of residents who are Hindu", "tot_Ra_Hindu"),
    _lka_rel("rel_muslim_pct", "Muslim (% of population)", "Share of residents whose religion is Islam",
             "tot_Ra_Islam ('Islam')"),
    _lka_rel("rel_christian_pct", "Christian (% of population)",
             "Share of residents who are Christian (Roman Catholic or other Christian)",
             "tot_Ra_Roman_Catholic + tot_Ra_Other_Christian"),
    _lka_rel("rel_catholic_pct", "Roman Catholic (% of population)", "Share of residents who are Roman Catholic",
             "tot_Ra_Roman_Catholic"),
    _lka_rel("rel_other_pct", "Other religion (% of population)",
             "Share of residents reporting a religion other than the five main census categories",
             "tot_Ra_Other (the census 'Other' religion category, as published)"),
    # ---- Sri Lanka: ethnicity
    _lka_eth("eth_sinhalese_pct", "Sinhalese (% of population)", "Share of residents who are Sinhalese",
             "tot_Eth_Sinhalese"),
    _lka_eth("eth_sl_tamil_pct", "Sri Lankan Tamil (% of population)", "Share of residents who are Sri Lankan Tamil",
             "tot_Eth_SL_Tamil"),
    _lka_eth("eth_indian_tamil_pct", "Indian Tamil / Malaiyaga Thamilar (% of population)",
             "Share of residents who are Indian Tamil (Malaiyaga Thamilar)", "tot_Eth_Ind_Tamil"),
    _lka_eth("eth_moor_pct", "Sri Lanka Moor (% of population)", "Share of residents who are Sri Lanka Moor",
             "tot_Eth_SL_Moor"),
    _lka_eth("eth_other_pct", "Other ethnic groups (% of population)",
             "Share of residents who are Burgher, Malay, Sri Lanka Chetty, Bharatha, Veddha or another group",
             "tot_Eth_Burgher + tot_Eth_Malay + tot_Eth_SL_Chetty + tot_Eth_Bharatha + tot_Eth_Veddhas + tot_Eth_Other"),
    _metric("religious_diversity", "Religious diversity (Gini-Simpson)", "index", "society", "intensive", "pop",
            "admin_adm3", _LKA_SRC, "2024",
            "Probability that two randomly chosen residents report different religions (0 = everyone the same).",
            "1 - sum(p_i^2) over the six census religion categories (Buddhist, Hindu, Islam, Roman Catholic, other "
            "Christian, other) of each COD-AB DS division, from the Census 2024 portal counts. "
            "Hexes: population-weighted average of DS-division values.", "neutral", "lka"),
    _metric("ethnic_diversity", "Ethnic diversity (Gini-Simpson)", "index", "society", "intensive", "pop",
            "admin_adm3", _LKA_SRC, "2024",
            "Probability that two randomly chosen residents belong to different ethnic groups.",
            "1 - sum(p_i^2) over the ten census ethnic categories (Sinhalese, Sri Lankan Tamil, Indian Tamil, Sri "
            "Lanka Moor, Burgher, Malay, Sri Lanka Chetty, Bharatha, Veddha, other) of each COD-AB DS division, from "
            "the Census 2024 portal counts. Hexes: population-weighted average.", "neutral", "lka"),
    _metric("worship_sites", "Places of worship", "count", "faith", "extensive", "pop", "admin_adm3",
            ["hot_rawdata_api", "hotosm_lka_cultural_places", "ocha_codab_lka"], "OSM snapshot 2026-11-26",
            "Places of worship mapped in OpenStreetMap (temples, kovils, mosques, churches...).",
            "OpenStreetMap amenity=place_of_worship (nodes, and centroids of ways/relations), de-duplicated where a "
            "node and a way/relation with the same normalised name lie within 100 m, assigned to COD-AB DS divisions "
            "by point-in-polygon (features within 3 km offshore to the nearest unit), and counted. Religion per site "
            "comes from the OSM religion tag, else from the name (English, Sinhala and Tamil keywords such as "
            "vihara, kovil, mosque/pallivasal, church); see points_worship. Hexes: DS counts split by population. "
            "Completeness caveat: OSM is volunteer-mapped and far from a census of places of worship (rural areas and "
            "small shrines are under-mapped), unlike the US value, which counts congregations reported to the 2020 "
            "U.S. Religion Census.", "higher", "lka"),
    # ---- United States: religion (2020 U.S. Religion Census, county)
    _us_rel("rel_evangelical_pct", "Evangelical Protestant adherents (% of population)", "EVANADH_2020",
            "Adherents of Evangelical Protestant congregations as a share of residents."),
    _us_rel("rel_mainline_pct", "Mainline Protestant adherents (% of population)", "MPRTADH_2020",
            "Adherents of Mainline Protestant congregations as a share of residents."),
    _us_rel("rel_black_protestant_pct", "Black Protestant adherents (% of population)", "BPRTADH_2020",
            "Adherents of historically Black Protestant denominations as a share of residents."),
    _us_rel("rel_catholic_pct", "Catholic adherents (% of population)", "CATHADH_2020",
            "Adherents of Catholic parishes as a share of residents."),
    _us_rel("rel_orthodox_pct", "Orthodox Christian adherents (% of population)", "ORTHADH_2020",
            "Adherents of Orthodox Christian parishes as a share of residents."),
    _us_rel("rel_christian_pct", "Christian adherents (% of population)",
            "EVANADH + MPRTADH + BPRTADH + CATHADH + ORTHADH (_2020); Latter-day Saints, Jehovah's Witnesses and "
            "other groups that ASARB classes as 'Other' are not included",
            "Adherents of the five Christian traditions (Evangelical, Mainline, Black Protestant, Catholic, Orthodox) "
            "as a share of residents."),
    _us_rel("rel_lds_pct", "Latter-day Saints (% of population)", "LDSADH_2020",
            "Members of The Church of Jesus Christ of Latter-day Saints as a share of residents."),
    _us_rel("rel_jewish_pct", "Jewish adherents (% of population)",
            "CJUDADH + IJUDADH + OJUDADH + RJUDADH + RFRMADH (_2020: Conservative, Independent, Orthodox, "
            "Reconstructionist, Reform Judaism)",
            "Adherents of synagogues/Jewish congregations as a share of residents (congregational count, lower than "
            "estimates of the Jewish population)."),
    _us_rel("rel_muslim_pct", "Muslim adherents (% of population)", "MSLMADH_2020 (ASARB 'Muslim Estimate')",
            "Estimated mosque adherents as a share of residents."),
    _us_rel("rel_buddhist_pct", "Buddhist adherents (% of population)",
            "MAHBUDADH + THBUDADH + VAJBUDADH (_2020: Mahayana, Theravada, Vajrayana)",
            "Adherents of Buddhist temples/centres as a share of residents."),
    _us_rel("rel_hindu_pct", "Hindu adherents (% of population)", "HINTADH + HINYMADH (_2020: Hindu Temples, "
            "Hindu Yoga and Meditation)", "Adherents of Hindu temples and centres as a share of residents."),
    _us_rel("rel_other_pct", "Other religious adherents (% of population)",
            "OTHADH_2020 minus LDS, Jewish, Muslim, Buddhist and Hindu adherents (e.g. Jehovah's Witnesses, Baha'i, "
            "Unitarian Universalist, Christian Science, Sikh, Jain, other groups ASARB classes as 'Other')",
            "Adherents of all other counted groups as a share of residents."),
    _us_rel("rel_unclaimed_pct", "Unclaimed by any counted congregation (% of population)",
            "100 - 100 x TOTADH_2020 / POP2020, floored at 0",
            "Residents not counted as adherents of any of the 372 participating groups (includes the non-religious "
            "and members of non-participating groups)."),
    # ---- United States: ethnicity (ACS B03002)
    _us_eth("eth_white_nh_pct", "White, non-Hispanic (% of population)", "B03002_003",
            "Non-Hispanic White alone as a share of the population."),
    _us_eth("eth_black_pct", "Black, non-Hispanic (% of population)", "B03002_004",
            "Non-Hispanic Black or African American alone as a share of the population."),
    _us_eth("eth_hispanic_pct", "Hispanic or Latino (% of population)", "B03002_012",
            "Hispanic or Latino (any race) as a share of the population."),
    _us_eth("eth_asian_pct", "Asian, non-Hispanic (% of population)", "B03002_006",
            "Non-Hispanic Asian alone as a share of the population."),
    _us_eth("eth_native_pct", "Native American & Pacific Islander, non-Hispanic (% of population)",
            "B03002_005 + B03002_007", "Non-Hispanic American Indian/Alaska Native alone plus Native Hawaiian/Other "
            "Pacific Islander alone as a share of the population."),
    _us_eth("eth_other_pct", "Other / multiracial, non-Hispanic (% of population)", "B03002_008 + B03002_009",
            "Non-Hispanic some other race alone or two or more races as a share of the population."),
    _metric("religious_diversity", "Religious diversity (Gini-Simpson)", "index", "society", "intensive", "pop",
            "admin_adm2", _US_RCMS_SRC, "2020",
            "Probability that two randomly chosen adherents belong to different religious traditions/groups.",
            "1 - sum(q_i^2) where q_i are shares of claimed adherents (not of population) in 11 categories: "
            "Evangelical, Mainline, Black Protestant, Catholic, Orthodox, Latter-day Saints, Jewish, Muslim, "
            "Buddhist, Hindu, other (2020 U.S. Religion Census; CT split as for the shares). The unclaimed population "
            "is excluded; counties with no counted adherents are blank. Hexes: population-weighted average.",
            "neutral", "usa"),
    _metric("ethnic_diversity", "Ethnic diversity (Gini-Simpson)", "index", "society", "intensive", "pop",
            "admin_adm2", ["census_acs5_2024"], "2020-2024",
            "Probability that two randomly chosen residents belong to different race/ethnicity groups.",
            "1 - sum(p_i^2) over the six mutually exclusive ACS B03002 groups (Hispanic; non-Hispanic White, Black, "
            "Asian, AIAN+NHPI, other/multiracial). Hexes: population-weighted average.", "neutral", "usa"),
    _metric("worship_sites", "Places of worship (congregations)", "count", "faith", "extensive", "pop",
            "admin_adm2", _US_RCMS_SRC, "2020",
            "Congregations counted by the 2020 U.S. Religion Census.",
            "TOTCNG_2020 (all participating groups' congregations, including the ASARB Muslim estimate) per county; "
            "blank = 0. Connecticut old counties split to planning regions by 2020 town population (see religion "
            "shares), hence fractional values there. Hexes: county count split by population. Completeness caveat: "
            "a census of congregations of 372 participating groups (non-participating groups and house churches "
            "are missing), versus volunteer-mapped OpenStreetMap places of worship for Sri Lanka.", "higher", "usa"),
]

# ---------------------------------------------------------------------------------------------
# Sri Lanka: census 2024 religion & ethnicity (portal, DS divisions)
# ---------------------------------------------------------------------------------------------

REL_IND = {"tot_Ra": "total", "tot_Ra_Buddhist": "buddhist", "tot_Ra_Hindu": "hindu", "tot_Ra_Islam": "islam",
           "tot_Ra_Roman_Catholic": "roman_catholic", "tot_Ra_Other_Christian": "other_christian",
           "tot_Ra_Other": "other"}
ETH_IND = {"tot_Eth": "total", "tot_Eth_Sinhalese": "sinhalese", "tot_Eth_SL_Tamil": "sl_tamil",
           "tot_Eth_Ind_Tamil": "indian_tamil", "tot_Eth_SL_Moor": "sl_moor", "tot_Eth_Burgher": "burgher",
           "tot_Eth_Malay": "malay", "tot_Eth_SL_Chetty": "sl_chetty", "tot_Eth_Bharatha": "bharatha",
           "tot_Eth_Veddhas": "veddha", "tot_Eth_Other": "other"}

_LKA_DISTRICT_ALIASES = {
    "moneragala": "monaragala", "rathnapura": "ratnapura", "mullaittivu": "mullaitivu", "kegalla": "kegalle",
    "hambanthota": "hambantota", "digamadulla": "ampara", "puttalama": "puttalam", "mulativu": "mullaitivu",
    "trincomale": "trincomalee",
}


def _dkey(s: str) -> str:
    k = re.sub(r"[^a-z]", "", s.lower())
    return _LKA_DISTRICT_ALIASES.get(k, k)


@lru_cache(maxsize=None)
def _district_ids() -> dict[str, str]:
    a2 = load_admin("lka", "adm2")
    return {_dkey(n): i for i, n in zip(a2.admin_id, a2.name)}


def _portal_topo():
    return fetch(PORTAL + "2024-dsdivisions-topo.js", "dcs_census_portal", "lka/census_portal",
                 "2024-dsdivisions-topo.json")


def _portal_ds(indicators: list[str]) -> pd.DataFrame:
    """DS-division values of census-portal indicators (one column per indicator, persons)."""
    t = json.loads(_portal_topo().read_text())
    geoms = t["objects"][list(t["objects"])[0]]["geometries"]
    props = pd.DataFrame([g["properties"] for g in geoms])[["ADM_ID", "ADM_NAME", "BELONGS_TO"]].astype(str)
    ids = props.ADM_ID.tolist()
    for ind in indicators:
        p = fetch(PORTAL + "fetch_data.php", "dcs_census_portal", "lka/census_portal", f"ds_table_2024_{ind}.json",
                  method="POST", data=json.dumps({"year": "2024", "indicator": ind, "ids": ids,
                                                  "table": "ds_table_2024"}),
                  headers={"Content-Type": "application/json"}, timeout=120)
        vals = {str(r["ADM_ID"]): r["value"] for r in json.loads(p.read_text())}
        props[ind] = pd.to_numeric(props.ADM_ID.map(vals), errors="coerce")
        if props[ind].isna().any():
            raise RuntimeError(f"census portal {ind}: {int(props[ind].isna().sum())} DS divisions without a value")
    return props


_DS_ALIASES = {   # portal name -> COD-AB name (after folding)
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


def _match_ds(portal: pd.DataFrame) -> pd.Series:
    """Portal ADM_ID -> COD-AB adm3 pcode: exact folded-name match within district, then an optimal fuzzy
    assignment of the remainder; every match is checked for spatial overlap."""
    from scipy.optimize import linear_sum_assignment
    a3 = load_admin("lka", "adm3")[["admin_id", "name", "parent_id", "geometry"]]
    out: dict[str, str] = {}
    fuzzy = []
    for d, pg in portal.groupby("BELONGS_TO"):
        cg = a3[a3.parent_id == "LK" + d]
        cn = {r.admin_id: _ds_norm(r.name) for r in cg.itertuples()}
        left = []
        for r in pg.itertuples():
            hit = [k for k, v in cn.items() if v == _ds_norm(r.ADM_NAME)]
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
                fuzzy.append((left[a].ADM_NAME, cg.set_index("admin_id").name[free[b]], round(float(S[a, b]), 2)))
    unmatched = sorted(set(portal.ADM_ID) - set(out))
    if unmatched:
        raise RuntimeError(f"census portal DS divisions unmatched: {unmatched}")
    lost = sorted(set(a3.admin_id) - set(out.values()))
    if lost:
        raise RuntimeError(f"COD-AB DS divisions without portal data: {lost}")
    # spatial check: share of each portal DS polygon that lies inside its matched COD-AB unit
    g24 = gpd.read_file(_portal_topo())
    if g24.crs is None:
        g24 = g24.set_crs(4326)
    g24["ADM_ID"] = g24.ADM_ID.astype(str)
    g24 = g24.set_index("ADM_ID").geometry.make_valid().to_crs(5234)
    cod = a3.set_index("admin_id").geometry.make_valid().to_crs(5234)
    ov = {k: g24[k].intersection(cod[v]).area / max(g24[k].area, 1.0) for k, v in out.items()}
    weak = sorted((round(f, 2), k, portal.set_index("ADM_ID").ADM_NAME[k], out[k]) for k, f in ov.items() if f < 0.5)
    print(f"  census portal: {len(portal)} DS divisions -> {len(set(out.values()))} COD-AB units; {len(fuzzy)} fuzzy "
          f"name matches {fuzzy}; median spatial overlap {np.median(list(ov.values())):.2f}"
          + (f"; overlap < 0.5: {weak}" if weak else ""), flush=True)
    return pd.Series(out)


def _gini_simpson(counts: pd.DataFrame) -> pd.Series:
    tot = counts.sum(axis=1)
    p = counts.div(tot.where(tot > 0), axis=0)
    return (1 - (p ** 2).sum(axis=1)).where(tot > 0)


def _report_district_tables() -> tuple[dict, dict]:
    """Final Report Table 6.1.15 (religion 2024) and 6.1.11 (ethnicity 2012/2024) -> {adm2 id | 'LK': [values]}."""
    import pdfplumber
    import pypdfium2 as pdfium
    p = fetch(CPH2024_REPORT, "dcs_cph2024", "lka/census2024", "CPH2024_Final_Eng.pdf", timeout=900)
    titles = {"rel": "Table 6.1.15 : Distribution of Population by Religion and District, 2024",
              "eth": "Table 6.1.11 : Distribution of Population by Ethnic Group and District"}
    doc = pdfium.PdfDocument(str(p))
    found: dict[str, int] = {}
    for i in range(len(doc)):
        tx = doc[i].get_textpage().get_text_range()
        for k, t in titles.items():
            j = tx.find(t)
            if k not in found and j >= 0 and "...." not in tx[j: j + 200]:
                found[k] = i
    doc.close()
    if set(found) != set(titles):
        raise RuntimeError(f"CPH2024 report: tables not found {set(titles) - set(found)}")
    ids = _district_ids()
    out = {}
    with pdfplumber.open(p) as pdf:
        for k, ncol in (("rel", 7), ("eth", 16)):
            text = pdf.pages[found[k]].extract_text() or ""
            rows = {}
            for line in text.splitlines():
                m = re.match(r"^([A-Za-z][A-Za-z .]*?)\s+((?:[\d,]+\s*){%d})$" % ncol, line.strip())
                if not m:
                    continue
                key = _dkey(m.group(1))
                vals = [float(x.replace(",", "")) for x in m.group(2).split()]
                if key == "srilanka":
                    rows["LK"] = vals
                elif key in ids:
                    rows[ids[key]] = vals
            if len(rows) != 26:
                raise RuntimeError(f"CPH2024 report {titles[k]}: parsed {len(rows)} rows (expected 26)")
            out[k] = rows
    return out["rel"], out["eth"]


def lka_census() -> pd.DataFrame:
    """COD-AB DS-division counts and shares of religion and ethnicity (Census 2024)."""
    p = _portal_ds(list(REL_IND) + list(ETH_IND))
    p["admin_id"] = p.ADM_ID.map(_match_ds(p))
    for ind, tot, lab in ((REL_IND, "tot_Ra", "religion"), (ETH_IND, "tot_Eth", "ethnicity")):
        parts = [c for c in ind if c != tot]
        diff = (p[parts].sum(axis=1) - p[tot]).abs()
        if diff.max() > 0:
            print(f"  WARNING {lab}: categories differ from total in {int((diff > 0).sum())} DS (max {diff.max():.0f})")
    # district cross-check against the Final Report
    rel_t, eth_t = _report_district_tables()
    dsum = p.assign(d="LK" + p.BELONGS_TO).groupby("d")[list(REL_IND) + list(ETH_IND)].sum()
    rel_cols = ["tot_Ra", "tot_Ra_Buddhist", "tot_Ra_Hindu", "tot_Ra_Islam", "tot_Ra_Roman_Catholic",
                "tot_Ra_Other_Christian", "tot_Ra_Other"]
    eth_cols = ["tot_Eth", "tot_Eth_Sinhalese", "tot_Eth_SL_Tamil", "tot_Eth_Ind_Tamil", "tot_Eth_SL_Moor",
                "tot_Eth_Burgher", "tot_Eth_Malay"]
    worst = 0.0
    for d in dsum.index:
        r = np.array(rel_t[d])
        e = np.array(eth_t[d])[1::2][:7]          # 2024 columns: total, Sinhalese, SLT, IT, Moor, Burgher, Malay
        mine = np.r_[dsum.loc[d, rel_cols].to_numpy(), dsum.loc[d, eth_cols].to_numpy()]
        ref = np.r_[r, e]
        worst = max(worst, float(np.max(np.abs(mine - ref) / np.maximum(ref, 1))))
    nat = dsum.sum()
    print(f"  Census 2024 (portal, summed): pop {nat.tot_Ra:,.0f}; Buddhist {100 * nat.tot_Ra_Buddhist / nat.tot_Ra:.2f}% "
          f"Hindu {100 * nat.tot_Ra_Hindu / nat.tot_Ra:.2f}% Islam {100 * nat.tot_Ra_Islam / nat.tot_Ra:.2f}% "
          f"Christian {100 * (nat.tot_Ra_Roman_Catholic + nat.tot_Ra_Other_Christian) / nat.tot_Ra:.2f}%; "
          f"Sinhalese {100 * nat.tot_Eth_Sinhalese / nat.tot_Eth:.2f}% (Report Table 6.1.15 national pop "
          f"{rel_t['LK'][0]:,.0f}, Buddhist {rel_t['LK'][1]:,.0f}); max relative district difference vs Report "
          f"Tables 6.1.11/6.1.15: {100 * worst:.3f}%", flush=True)
    if worst > 0.005:
        print("  WARNING district sums differ from the Final Report by more than 0.5%")

    g = p.groupby("admin_id")[list(REL_IND) + list(ETH_IND)].sum()
    R = g.rename(columns={k: f"rel_{v}" for k, v in REL_IND.items()})
    E = g.rename(columns={k: f"eth_{v}" for k, v in ETH_IND.items()})
    out = pd.DataFrame(index=g.index)
    rt = R.rel_total.where(R.rel_total > 0)
    et = E.eth_total.where(E.eth_total > 0)
    out["rel_buddhist_pct"] = 100 * R.rel_buddhist / rt
    out["rel_hindu_pct"] = 100 * R.rel_hindu / rt
    out["rel_muslim_pct"] = 100 * R.rel_islam / rt
    out["rel_christian_pct"] = 100 * (R.rel_roman_catholic + R.rel_other_christian) / rt
    out["rel_catholic_pct"] = 100 * R.rel_roman_catholic / rt
    out["rel_other_pct"] = 100 * R.rel_other / rt
    out["eth_sinhalese_pct"] = 100 * E.eth_sinhalese / et
    out["eth_sl_tamil_pct"] = 100 * E.eth_sl_tamil / et
    out["eth_indian_tamil_pct"] = 100 * E.eth_indian_tamil / et
    out["eth_moor_pct"] = 100 * E.eth_sl_moor / et
    other_eth = ["eth_burgher", "eth_malay", "eth_sl_chetty", "eth_bharatha", "eth_veddha", "eth_other"]
    out["eth_other_pct"] = 100 * E[other_eth].sum(axis=1) / et
    out["religious_diversity"] = _gini_simpson(R[[f"rel_{v}" for v in REL_IND.values() if v != "total"]])
    out["ethnic_diversity"] = _gini_simpson(E[[f"eth_{v}" for v in ETH_IND.values() if v != "total"]])
    out = out.round(4)
    out["census_pop_2024"] = R.rel_total
    # largest groups (attributes for tooltips, not metrics)
    rel_lab = {"rel_buddhist_pct": "Buddhist", "rel_hindu_pct": "Hindu", "rel_muslim_pct": "Muslim",
               "rel_christian_pct": "Christian", "rel_other_pct": "Other"}
    eth_lab = {"eth_sinhalese_pct": "Sinhalese", "eth_sl_tamil_pct": "Sri Lankan Tamil",
               "eth_indian_tamil_pct": "Indian Tamil", "eth_moor_pct": "Sri Lanka Moor", "eth_other_pct": "Other"}
    out["rel_largest"] = out[list(rel_lab)].idxmax(axis=1).map(rel_lab)
    out["eth_largest"] = out[list(eth_lab)].idxmax(axis=1).map(eth_lab)
    return out.reset_index()


# ---------------------------------------------------------------------------------------------
# Sri Lanka: places of worship (OpenStreetMap)
# ---------------------------------------------------------------------------------------------

_HOT_ATTRS = ["name", "name:en", "name:si", "name:ta", "amenity", "religion", "denomination", "building",
              "historic", "wikidata"]


def _hot_snapshot(max_wait_s: int = 2400):
    """Cached HOT Raw Data API snapshot of amenity=place_of_worship with tags (None if the API fails)."""
    dest = config.CACHE / "lka/osm" / HOT_SNAPSHOT_NAME
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    a1 = load_admin("lka", "adm1")
    geom = a1.union_all().buffer(0.02).simplify(0.01)
    body = {"fileName": "lka_place_of_worship", "outputType": "geojson", "centroid": True,
            "geometry": json.loads(json.dumps(geom.__geo_interface__)),
            "filters": {"tags": {"allGeometry": {"joinOr": {"amenity": ["place_of_worship"]}}},
                        "attributes": {"allGeometry": _HOT_ATTRS}}}
    try:
        task = json.loads(fetch(HOT_API + "snapshot/", "hot_rawdata_api", "lka/osm", "hot_rawdata_task.json",
                                method="POST", data=json.dumps(body), refresh=True,
                                headers={"Content-Type": "application/json"}, timeout=120).read_text())
        tid = task.get("task_id") or task.get("taskId")
        print(f"  HOT Raw Data API task {tid} queued; polling (up to {max_wait_s // 60} min)", flush=True)
        t0 = time.time()
        while time.time() - t0 < max_wait_s:
            st = json.loads(fetch(HOT_API + f"tasks/status/{tid}/", "hot_rawdata_api", "lka/osm",
                                  "hot_rawdata_task_status.json", refresh=True, timeout=60).read_text())
            if st.get("status") == "SUCCESS":
                url = st["result"].get("download_url") or st["result"].get("downloadUrl")
                return fetch(url, "hot_rawdata_api", "lka/osm", HOT_SNAPSHOT_NAME, timeout=600)
            if st.get("status") in ("FAILURE", "REVOKED"):
                print(f"  HOT Raw Data API task failed: {st}", flush=True)
                return None
            time.sleep(30)
        print("  HOT Raw Data API task did not finish in time", flush=True)
    except Exception as e:  # noqa: BLE001 - fall back to the HDX export
        print(f"  HOT Raw Data API unavailable: {str(e)[:160]}", flush=True)
    return None


def _read_geojson_any(path) -> gpd.GeoDataFrame:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".geojson", ".json"))
                     and not any(s in n.lower() for s in ("meta", "clipping_boundary"))]
            if not names:
                raise RuntimeError(f"no GeoJSON in {path}: {z.namelist()}")
            return gpd.read_file(io.BytesIO(z.read(names[0])))
    return gpd.read_file(path)


def _osm_worship_raw() -> tuple[gpd.GeoDataFrame, str]:
    """OSM places of worship as points with columns osm_id, name, religion, denomination (+ source label)."""
    snap = _hot_snapshot()
    if snap is not None:
        g = _read_geojson_any(snap)
        if g.crs is None:
            g = g.set_crs(4326)
        cols = {c.lower().replace(":", "_"): c for c in g.columns}
        df = gpd.GeoDataFrame({
            "osm_id": [f"{str(t).rstrip('s')}/{i}" for t, i in zip(g["osm_type"], g["osm_id"])],
            "name": g.get(cols.get("name", "name")),
            "name_en": g.get(cols.get("name_en", "name_en")),
            "name_si": g.get(cols.get("name_si", "name_si")),
            "name_ta": g.get(cols.get("name_ta", "name_ta")),
            "religion": g.get(cols.get("religion", "religion")),
            "denomination": g.get(cols.get("denomination", "denomination")),
            "wikidata": g.get(cols.get("wikidata", "wikidata")),
        }, geometry=g.geometry.representative_point() if not (g.geom_type == "Point").all() else g.geometry,
            crs=g.crs)
        src = "hot_rawdata_api"
    else:
        z = fetch(HOT_CULTURAL_GPKG, "hotosm_lka_cultural_places", "lka/osm", "hotosm_lka_cultural_places_osm_gpkg.zip")
        g = gpd.read_file(next(unzip(z).rglob("*.gpkg")))
        g = g[g.amenity == "place_of_worship"]
        df = gpd.GeoDataFrame({"osm_id": g["id"], "name": g["name"], "name_en": g.get("name_en"),
                               "name_si": g.get("name_si"), "name_ta": g.get("name_ta"),
                               "religion": None, "denomination": None, "wikidata": None},
                              geometry=g.geometry.representative_point(), crs=g.crs)
        src = "hotosm_lka_cultural_places"
    df = df.to_crs(4326)
    for c in ("name", "name_en", "name_si", "name_ta", "religion", "denomination", "wikidata"):
        df[c] = df[c].where(df[c].notna(), None)
    return df.reset_index(drop=True), src


# name keywords -> religion (English / romanised, Sinhala, Tamil).  Order matters: first match wins.
_NAME_RULES = [
    ("muslim", r"mosque|masjid|musjid|jumm?ah?\b|juma\b|jamiul|jami\b|pallivasal|palliwasal|thakkiya|takkiya|"
               r"zawiya|dargah|darga|ziyaram|arabic college|madrasa|பள்ளிவாசல்|மஸ்ஜித்|ஜும்|මුස්ලිම්|ජුම්මා|"
               r"මස්ජිද්"),
    ("christian", r"church|chapel|cathedral|basilica|\bst\.? |saint|our lady|kingdom hall|assembly of god|"
                  r"methodist|anglican|baptist|pentecost|christian|catholic|calvary|gospel|evangel|presbyter|"
                  r"lutheran|adventist|salvation army|ministr|fellowship|tabernacle|\bholy (cross|family|rosary|"
                  r"spirit|trinity)|church of|தேவாலயம்|திருச்சபை|சபை|දේවස්ථානය|පල්ලිය|කතෝලික"),
    ("hindu", r"kovil|koil|kovilan|amman|pillaiyar|pillayar|vinayagar|vinayakar|murugan|kandasw|kanthasw|"
              r"sivan|\bsiva|shiva|eeswar|eswar|ishwar|vishnu|perumal|kali\b|kaali|ganesh|ganapathi|hindu|"
              r"devasthan|aalayam|alayam|mariamman|muthumari|kannaki|ayyanar|mandir|akshardham|swaminarayan|"
              r"\bbaps\b|iskcon|krishna|சிவ|கோவில்|கோயில்|ஆலயம்|அம்மன்|"
              r"பிள்ளையார்|முருக|விநாயக|කෝවිල"),
    ("buddhist", r"vihar|viharay|pansala|aramaya|\barama|ashramaya|asramaya|dagoba|dageba|dagaba|chaitya|"
                 r"cetiya|stupa|bodhi|bo tree|bomaluwa|pirivena|buddh|maha vihara|raja maha|tapowana|thapowana|"
                 r"senasun|senasuna|aranya|අරණ්‍ය|විහාර|පන්සල|ආරාම|චෛත්‍ය|දාගැබ|බෝධි|පිරිවෙන|සෙනසුන|"
                 r"விகாரை|பௌத்த"),
]
_NAME_RX = [(r, re.compile(p, re.I)) for r, p in _NAME_RULES]
_OSM_REL = {"buddhist": "buddhist", "bud": "buddhist", "buddhism": "buddhist", "hindu": "hindu",
            "hinduism": "hindu", "muslim": "muslim", "islam": "muslim", "islamic": "muslim",
            "christian": "christian", "christianity": "christian", "catholic": "christian",
            "jewish": "jewish", "sikh": "sikh"}   # any other tag value (multifaith, none, a;b, ...) -> "other"


def _religion_from_names(*names) -> str | None:
    txt = " ".join(str(n) for n in names if isinstance(n, str) and n)
    if not txt:
        return None
    for r, rx in _NAME_RX:
        if rx.search(txt):
            return r
    return None


def _norm_name(s) -> str:
    return re.sub(r"[^0-9a-z඀-෿஀-௿]", "", str(s).lower()) if isinstance(s, str) else ""


def lka_worship() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw, src = _osm_worship_raw()
    n_raw = len(raw)
    # de-duplicate node + way/relation pairs with the same name within 100 m (keep the way/relation)
    raw["nkey"] = [_norm_name(a) or _norm_name(b) for a, b in zip(raw.name, raw.name_en)]
    raw["is_node"] = raw.osm_id.astype(str).str.startswith("node")
    m = raw.to_crs(5234)
    drop = set()
    named = m[m.nkey != ""]
    for _, grp in named.groupby("nkey"):
        if len(grp) < 2:
            continue
        grp = grp.sort_values("is_node")       # ways/relations first
        kept = []
        for i, r in grp.iterrows():
            if any(r.geometry.distance(k.geometry) <= 100 for k in kept):
                drop.add(i)
            else:
                kept.append(r)
    pts = raw.drop(index=list(drop)).copy()
    # religion: OSM tag, else name keywords
    tag = pts.religion.astype(str).str.lower().str.strip().map(_OSM_REL)
    tag = tag.where(pts.religion.notna(), None)
    tag = tag.where(~(pts.religion.notna() & tag.isna()), "other")       # tagged with another religion
    by_name = [_religion_from_names(a, b, c, d) for a, b, c, d in zip(pts.name, pts.name_en, pts.name_si, pts.name_ta)]
    pts["religion_source"] = np.where(tag.notna(), "osm_tag", np.where(pd.notna(by_name), "name", "none"))
    pts["kind"] = tag.where(tag.notna(), pd.Series(by_name, index=pts.index)).fillna("unknown")
    # DS division: point in polygon, else nearest within 3 km (coastal / offshore features)
    a3 = load_admin("lka", "adm3")[["admin_id", "geometry"]]
    j = gpd.sjoin(pts, a3, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    pts["adm3"] = j.admin_id
    miss = pts.adm3.isna()
    if miss.any():
        nn = gpd.sjoin_nearest(pts[miss].to_crs(5234), a3.to_crs(5234), how="left", max_distance=3000)
        nn = nn[~nn.index.duplicated()]
        pts.loc[miss, "adm3"] = nn.admin_id
    n_out = int(pts.adm3.isna().sum())
    pts = pts[pts.adm3.notna()].copy()
    pts["lat"] = pts.geometry.y.round(6)
    pts["lon"] = pts.geometry.x.round(6)
    pts["display_name"] = [a if isinstance(a, str) and a else (b if isinstance(b, str) and b else None)
                           for a, b in zip(pts.name_en, pts.name)]
    pts["display_name"] = pts.display_name.fillna("Place of worship (" + pts.kind + ")")
    out = pd.DataFrame({"id": "osm:" + pts.osm_id.astype(str), "name": pts.display_name, "lat": pts.lat,
                        "lon": pts.lon, "kind": pts.kind, "religion_source": pts.religion_source,
                        "religion_tag": pts.religion, "denomination": pts.denomination, "name_local": pts.name,
                        "wikidata": pts.wikidata, "adm3": pts.adm3, "source": src}).reset_index(drop=True)
    # counts per DS division
    a3ids = load_admin("lka", "adm3").admin_id
    cnt = out.groupby(["adm3", "kind"]).size().unstack(fill_value=0)
    adm = pd.DataFrame({"admin_id": a3ids})
    adm["worship_sites"] = adm.admin_id.map(cnt.sum(axis=1)).fillna(0).astype(int)
    for k in ("buddhist", "hindu", "muslim", "christian", "other", "unknown"):
        adm[f"worship_{k}"] = adm.admin_id.map(cnt[k] if k in cnt else pd.Series(dtype=float)).fillna(0).astype(int)
    if src == "hot_rawdata_api":   # cross-check against the HDX pre-built export
        try:
            z = fetch(HOT_CULTURAL_GPKG, "hotosm_lka_cultural_places", "lka/osm",
                      "hotosm_lka_cultural_places_osm_gpkg.zip")
            hdx = gpd.read_file(next(unzip(z).rglob("*.gpkg")), columns=["amenity"], ignore_geometry=True)
            print(f"  cross-check: HDX cultural_places export has {int((hdx.amenity == 'place_of_worship').sum()):,} "
                  f"amenity=place_of_worship features vs {n_raw:,} in the snapshot", flush=True)
        except Exception as e:  # noqa: BLE001 - cross-check only
            print(f"  cross-check with the HDX export skipped: {str(e)[:80]}")
    diag = {"source": src, "raw": n_raw, "duplicates_dropped": len(drop), "outside_country": n_out,
            "kept": len(out), "by_kind": out.kind.value_counts().to_dict(),
            "religion_source": out.religion_source.value_counts().to_dict()}
    print(f"  OSM places of worship ({src}): {n_raw:,} features, {len(drop):,} node/way duplicates dropped, "
          f"{n_out} outside; kept {len(out):,}; kinds {diag['by_kind']}; religion from {diag['religion_source']}",
          flush=True)
    return out, adm, diag


# ---------------------------------------------------------------------------------------------
# United States: 2020 U.S. Religion Census (+ Connecticut split) and ACS B03002
# ---------------------------------------------------------------------------------------------

RCMS_GROUPS = {
    "evangelical": ["EVANADH_2020"], "mainline": ["MPRTADH_2020"], "black_protestant": ["BPRTADH_2020"],
    "catholic": ["CATHADH_2020"], "orthodox": ["ORTHADH_2020"], "lds": ["LDSADH_2020"],
    "jewish": ["CJUDADH_2020", "IJUDADH_2020", "OJUDADH_2020", "RJUDADH_2020", "RFRMADH_2020"],
    "muslim": ["MSLMADH_2020"], "buddhist": ["MAHBUDADH_2020", "THBUDADH_2020", "VAJBUDADH_2020"],
    "hindu": ["HINTADH_2020", "HINYMADH_2020"],
}
_OTHER_SUB = ["lds", "jewish", "muslim", "buddhist", "hindu"]   # groups inside ASARB's 'Other' tradition


def _rcms_counts() -> pd.DataFrame:
    p = fetch(RCMS_DTA, "arda_rcms2020_county", "usa/religion", "RCMSCY20_county.dta")
    fetch(RCMS_CODEBOOK, "arda_rcms2020_county", "usa/religion", "RCMSCY20_codebook.txt")
    d = pd.read_stata(p, convert_categoricals=False).copy()
    d["admin_id"] = d.fips.astype(int).astype(str).str.zfill(5)
    need = ["POP2020", "TOTCNG_2020", "TOTADH_2020", "OTHADH_2020"] + [c for v in RCMS_GROUPS.values() for c in v]
    X = d.set_index("admin_id")[need].apply(pd.to_numeric, errors="coerce").fillna(0.0)   # blank = none reported
    out = pd.DataFrame(index=X.index)
    out["pop"] = X.POP2020
    out["congregations"] = X.TOTCNG_2020
    out["adherents"] = X.TOTADH_2020
    for k, cols in RCMS_GROUPS.items():
        out[k] = X[cols].sum(axis=1)
    out["other"] = X.OTHADH_2020 - out[_OTHER_SUB].sum(axis=1)
    if (out.other < -0.5).any():
        raise RuntimeError("RCMS: LDS + Jewish + Muslim + Buddhist + Hindu exceed the 'Other' tradition")
    out["other"] = out.other.clip(lower=0)
    chk = out[list(RCMS_GROUPS) + ["other"]].sum(axis=1) - out.adherents
    if chk.abs().max() > 1:
        raise RuntimeError(f"RCMS: categories do not add to TOTADH (max diff {chk.abs().max()})")
    return out


def _ct_weights() -> pd.DataFrame:
    """old CT county (09001..09015) -> planning region (09110..09190) population weights."""
    xw = pd.read_csv(fetch(CT_XWALK, "census_ct_xwalk", "usa/boundaries", "ct_cou_to_cousub_crosswalk.txt"),
                     sep="|", dtype=str, encoding="utf-8-sig")
    xw.columns = [c.split("\n")[0].strip().strip('"') for c in xw.columns]
    xw = xw.rename(columns={"STATEFP": "st", "OLD_COUNTYFP": "old", "NEW_COUNTYFP": "new"})
    xw = xw[(xw.st == "09") & xw.old.notna() & xw.new.notna() & xw.COUSUBFP.notna()].copy()
    pep = pd.read_csv(fetch(CT_SUBEST, "census_pep_subcounty_2023", "usa/population", "sub-est2023_9.csv"),
                      dtype=str, encoding="latin-1")
    pep = pep[pep.SUMLEV == "061"]
    pop = pd.Series(pd.to_numeric(pep.ESTIMATESBASE2020).to_numpy(), index=pep.COUNTY + pep.COUSUB)
    xw["pop"] = (xw.new + xw.COUSUBFP).map(pop).fillna(0.0)
    unmatched = xw[(xw.COUSUBFP != "00000") & ((xw.new + xw.COUSUBFP).map(pop).isna())]
    if len(unmatched):
        raise RuntimeError(f"CT towns without PEP population: {unmatched.COUSUB_NAMELSAD.tolist()[:5]}")
    w = xw.groupby(["old", "new"], as_index=False)["pop"].sum()
    w["old"] = "09" + w.old
    w["new"] = "09" + w.new
    w["w"] = w["pop"] / w.groupby("old")["pop"].transform("sum")
    return w[w.w > 0]


def us_religion() -> pd.DataFrame:
    c = _rcms_counts()
    w = _ct_weights()
    old = c.loc[c.index.isin(w.old.unique())]
    if len(old) != w.old.nunique():
        raise RuntimeError("RCMS: Connecticut old counties missing")
    alloc = (old.reindex(w.old).to_numpy() * w.w.to_numpy()[:, None])
    new = pd.DataFrame(alloc, columns=old.columns).assign(admin_id=w.new.to_numpy()).groupby("admin_id").sum()
    c = pd.concat([c.drop(index=old.index), new])
    print(f"  RCMS: Connecticut {len(old)} old counties ({old['pop'].sum():,.0f} people, "
          f"{old.congregations.sum():,.0f} congregations) -> {len(new)} planning regions "
          f"({new['pop'].sum():,.0f}, {new.congregations.sum():,.1f})", flush=True)
    a2 = set(load_admin("usa", "adm2").admin_id)
    miss, extra = sorted(a2 - set(c.index)), sorted(set(c.index) - a2)
    if miss or extra:
        raise RuntimeError(f"RCMS counties vs cb_2023: missing {miss[:5]} extra {extra[:5]}")
    cats = list(RCMS_GROUPS) + ["other"]
    denom = np.maximum(c["pop"], c.adherents).where(lambda s: s > 0)
    out = pd.DataFrame(index=c.index)
    for k in cats:
        out[f"rel_{k}_pct"] = 100 * c[k] / denom
    out["rel_christian_pct"] = out[[f"rel_{k}_pct" for k in ("evangelical", "mainline", "black_protestant",
                                                              "catholic", "orthodox")]].sum(axis=1)
    out["rel_unclaimed_pct"] = (100 - 100 * c.adherents / c["pop"].where(c["pop"] > 0)).clip(lower=0)
    out["religious_diversity"] = _gini_simpson(c[cats])
    out = out.round(4)
    out["worship_sites"] = c.congregations.round(2)
    out["rcms_pop_2020"] = c["pop"].round(1)
    out["rcms_adherents"] = c.adherents.round(1)
    out["rcms_adherence_rate"] = (1000 * c.adherents / c["pop"]).round(1)   # per 1,000 residents, as published
    n_over = int((c.adherents > c["pop"]).sum())
    print(f"  RCMS 2020: pop {c['pop'].sum():,.0f}, adherents {c.adherents.sum():,.0f} "
          f"({100 * c.adherents.sum() / c['pop'].sum():.1f}%), congregations {c.congregations.sum():,.0f}; "
          f"{n_over} counties with adherents > residents", flush=True)
    nat = c[cats].sum() / c["pop"].sum() * 100
    print("  RCMS national % of population: " + ", ".join(f"{k} {v:.2f}" for k, v in nat.items()), flush=True)
    return out


def us_ethnicity() -> pd.DataFrame:
    t = util.acs_table("B03002", ACS_YEAR)
    a2 = load_admin("usa", "adm2").admin_id
    t = t.reindex(a2)
    if t.B03002_E001.isna().any():
        raise RuntimeError(f"ACS B03002: {int(t.B03002_E001.isna().sum())} counties missing")
    tot = t.B03002_E001.where(t.B03002_E001 > 0)
    groups = {
        "eth_white_nh_pct": t.B03002_E003, "eth_black_pct": t.B03002_E004, "eth_hispanic_pct": t.B03002_E012,
        "eth_asian_pct": t.B03002_E006, "eth_native_pct": t.B03002_E005 + t.B03002_E007,
        "eth_other_pct": t.B03002_E008 + t.B03002_E009,
    }
    out = pd.DataFrame({k: 100 * v / tot for k, v in groups.items()})
    chk = (out.sum(axis=1) - 100).abs().max()
    if chk > 0.01:
        raise RuntimeError(f"ACS B03002 groups do not sum to 100 (max diff {chk})")
    out["ethnic_diversity"] = _gini_simpson(pd.DataFrame(groups))
    out = out.round(4)
    out["acs_pop"] = t.B03002_E001
    lab = {"eth_white_nh_pct": "White", "eth_black_pct": "Black", "eth_hispanic_pct": "Hispanic",
           "eth_asian_pct": "Asian", "eth_native_pct": "Native", "eth_other_pct": "Other/multiracial"}
    out["eth_largest"] = out[list(lab)].idxmax(axis=1).map(lab)
    n = t.B03002_E001.sum()
    print(f"  ACS {ACS_YEAR - 4}-{ACS_YEAR} B03002: pop {n:,.0f}; " + ", ".join(
        f"{lab[k]} {100 * v.sum() / n:.2f}%" for k, v in groups.items()), flush=True)
    return out


# ---------------------------------------------------------------------------------------------
# Both: notable religious sites from Wikidata
# ---------------------------------------------------------------------------------------------

HOLY_CLASSES = {   # QID -> (label, religion implied by membership or None)
    "Q5393308": ("Buddhist temple", "buddhist"), "Q180987": ("stupa", "buddhist"),
    "Q842402": ("Hindu temple", "hindu"), "Q17059988": ("kovil", "hindu"),
    "Q32815": ("mosque", "muslim"), "Q16970": ("church building", "christian"),
    "Q2977": ("cathedral", "christian"), "Q163687": ("basilica", "christian"),
    "Q34627": ("synagogue", "jewish"), "Q44539": ("temple", None), "Q697295": ("shrine", None),
    "Q15135589": ("pilgrimage site", None),
}
_COUNTRY_QID = {"lka": "Q854", "usa": "Q30"}
_REL_TEXT = [   # religion inferred from P140 / P31 labels (first match wins)
    ("muslim", r"islam|muslim|mosque|sufi|shia|sunni"),
    ("jewish", r"juda|jewish|synagogue"),
    ("sikh", r"sikh|gurdwara"),
    ("hindu", r"hindu|kovil|shaiv|vaishnav|shakt|swaminarayan|iskcon|krishna"),
    ("buddhist", r"buddh|stupa|pagoda|dagoba|theravada|mahayana|zen|vajrayana"),
    ("christian", r"christ|church|cathedral|basilica|catholic|protestant|anglican|episcopal|methodist|baptist|"
                  r"lutheran|presbyterian|orthodox|chapel|mission|latter|mormon|reformed|congregational|quaker|"
                  r"evangelical|pentecostal|adventist"),
]


def _wd_id(u: str) -> str:
    return str(u).rsplit("/", 1)[-1]


def holy_sites(country: str) -> pd.DataFrame:
    cq = _COUNTRY_QID[country]
    min_sl, lim = (0, 500) if country == "lka" else (5, 300)
    member: dict[str, set[str]] = {}
    sl: dict[str, int] = {}
    for qid in HOLY_CLASSES:
        q = (f"SELECT ?item ?sl WHERE {{ ?t wdt:P279* wd:{qid} . ?item wdt:P31 ?t . ?item wdt:P17 wd:{cq} . "
             f"?item wikibase:sitelinks ?sl . FILTER(?sl >= {min_sl}) }} ORDER BY DESC(?sl) LIMIT {lim}")
        d = util.sparql(q, f"holy_{country}_{qid}")
        for r in d.itertuples():
            i = _wd_id(r.item)
            member.setdefault(i, set()).add(qid)
            sl[i] = int(r.sl)
        time.sleep(0.5)
    cand = sorted(sl, key=lambda i: (-sl[i], i))[: max(4 * N_HOLY[country], 200)]
    rows = []
    for k in range(0, len(cand), 100):
        vals = " ".join(f"wd:{i}" for i in cand[k: k + 100])
        q = ("SELECT ?item ?itemLabel (SAMPLE(?c) AS ?coord) (GROUP_CONCAT(DISTINCT ?tl; separator='|') AS ?types) "
             "(GROUP_CONCAT(DISTINCT ?rl; separator='|') AS ?religions) WHERE { VALUES ?item { " + vals + " } "
             "OPTIONAL { ?item wdt:P625 ?c } "
             "OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label ?tl . FILTER(LANG(?tl) = 'en') } "
             "OPTIONAL { ?item wdt:P140 ?r . ?r rdfs:label ?rl . FILTER(LANG(?rl) = 'en') } "
             "SERVICE wikibase:label { bd:serviceParam wikibase:language 'en,si,ta'. } } GROUP BY ?item ?itemLabel")
        rows.append(util.sparql(q, f"holy_{country}_details_{k // 100:02d}"))
    d = pd.concat(rows, ignore_index=True)
    d["wikidata"] = d.item.map(_wd_id)
    d = d.drop_duplicates("wikidata")
    d = d[d.coord.notna() & d.coord.astype(str).str.startswith("Point")].copy()
    ll = [util.parse_wkt_point(s) for s in d.coord]
    d["lat"] = [a for a, _ in ll]
    d["lon"] = [b for _, b in ll]
    d["sitelinks"] = d.wikidata.map(sl).astype(int)
    for c in ("types", "religions"):
        d[c] = d[c].fillna("").astype(str) if c in d else ""
    # drop non-religious 'temples' (Masonic halls etc.)
    d = d[~d.types.str.contains(r"masonic|freemason|lodge|ancient greek|roman temple", case=False, regex=True)]

    def religion(r) -> tuple[str, str]:
        for q in sorted(member.get(r.wikidata, ()), key=lambda x: list(HOLY_CLASSES).index(x)):
            if HOLY_CLASSES[q][1]:
                return HOLY_CLASSES[q][1], "class"
        for txt, srcl in ((r.religions, "P140"), (r.types, "P31")):
            for rel, pat in _REL_TEXT:
                if txt and re.search(pat, txt, re.I):
                    return rel, srcl
        nm = _religion_from_names(r.itemLabel)
        return (nm, "name") if nm else ("other", "none")

    rk = [religion(r) for r in d.itertuples()]
    d["kind"] = [a for a, _ in rk]
    d["religion_source"] = [b for _, b in rk]
    d["classes"] = [", ".join(HOLY_CLASSES[q][0] for q in sorted(member.get(i, ()))) for i in d.wikidata]
    # inside the country (50 states + DC / Sri Lanka): admin unit by point in polygon, else nearest within 5 km
    lvl = "adm3" if country == "lka" else "adm2"
    adm = load_admin(country, lvl)[["admin_id", "geometry"]]
    g = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.lon, d.lat), crs=4326)
    j = gpd.sjoin(g, adm, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    g[lvl] = j.admin_id
    miss = g[lvl].isna()
    if miss.any():
        crs = 5234 if country == "lka" else 5070
        nn = gpd.sjoin_nearest(g[miss].to_crs(crs), adm.to_crs(crs), how="left", max_distance=5000)
        nn = nn[~nn.index.duplicated()]
        g.loc[miss, lvl] = nn.admin_id
    g = g[g[lvl].notna()]
    g = g.sort_values(["sitelinks", "itemLabel"], ascending=[False, True]).head(N_HOLY[country]).copy()
    g["rank"] = np.arange(1, len(g) + 1)
    out = pd.DataFrame({"id": "wd:" + g.wikidata, "name": g.itemLabel, "lat": g.lat.round(6), "lon": g.lon.round(6),
                        "kind": g.kind, "type": g.types.str.replace("|", "; ", regex=False), "classes": g.classes,
                        "religion_detail": g.religions.str.replace("|", "; ", regex=False),
                        "religion_source": g.religion_source, "sitelinks": g.sitelinks, "rank": g["rank"],
                        "wikidata": g.wikidata, "admin_id": g[lvl]}).reset_index(drop=True)
    print(f"  Wikidata holy sites: {len(sl):,} candidates, kept top {len(out)} by sitelinks; kinds "
          f"{out.kind.value_counts().to_dict()}; top: " + "; ".join(
              f"{r.name} ({r.sitelinks})" for r in out.head(6).itertuples()), flush=True)
    return out


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def _wb_check(country: str, mine: float, label: str, year: int | None = None) -> None:
    try:
        v, y = util.wb_value("SP.POP.TOTL", config.COUNTRIES[country]["iso3"], year)
        print(f"  check {label}: ours {mine:,.0f} vs World Bank SP.POP.TOTL {y} {v:,.0f} "
              f"({100 * (mine / v - 1):+.2f}%)", flush=True)
    except Exception as e:  # noqa: BLE001 - sanity check only
        print(f"  check {label}: World Bank unavailable ({str(e)[:60]})")


def build(country: str) -> None:
    print(f"[{DOMAIN}] building {country}", flush=True)
    hs = holy_sites(country)
    util.write_points(country, DOMAIN, "holy_sites", hs)
    if country == "lka":
        cen = lka_census()
        pts, wadm, diag = lka_worship()
        adm = cen.merge(wadm, on="admin_id", how="outer")
        util.write_admin(country, DOMAIN, "adm3", adm)
        util.write_points(country, DOMAIN, "worship", pts)
        _wb_check(country, cen.census_pop_2024.sum(), "census 2024 population", 2024)
        nat_w = wadm.worship_sites.sum()
        print(f"  worship sites: {nat_w:,} ({1e4 * nat_w / cen.census_pop_2024.sum():.2f} per 10,000 people)",
              flush=True)
    else:
        rel = us_religion()
        eth = us_ethnicity()
        adm = rel.join(eth, how="outer")
        adm.index.name = "admin_id"
        util.write_admin(country, DOMAIN, "adm2", adm.reset_index())
        _wb_check(country, rel.rcms_pop_2020.sum(), "RCMS POP2020", 2020)
        _wb_check(country, eth.acs_pop.sum(), f"ACS {ACS_YEAR - 4}-{ACS_YEAR} population", ACS_YEAR - 2)
        print(f"  worship sites (congregations): {rel.worship_sites.sum():,.0f} "
              f"({1e4 * rel.worship_sites.sum() / rel.rcms_pop_2020.sum():.2f} per 10,000 people)", flush=True)
    schema.write_domain_meta(config.interim_dir(country, DOMAIN), METRICS, SOURCES, country)


if __name__ == "__main__":
    import sys

    from .. import validate

    countries = sys.argv[1:] or ["lka", "usa"]
    for cc in countries:
        build(cc)
    sys.exit(validate.main([DOMAIN] + countries))
