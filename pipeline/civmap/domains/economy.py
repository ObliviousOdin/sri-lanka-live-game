"""Economy domain (Gold yield, income lens): GDP by sector, household income, poverty, wealth.

Outputs (data/interim/<country>/economy/):
  admin_adm2.csv   gdp_usd, gdp_agri_usd, gdp_industry_usd, gdp_services_usd, gdp_pc_usd,
                   hh_income_usd, poverty_rate, poor_persons
                   (+ gdp_manufacturing_usd, gdp_finance_usd for the US)
  hex.csv          rwi (Sri Lanka only: Meta Relative Wealth Index averaged per H3 cell)
  report.json      reconciliation checks against published national totals

United States
  * GDP: BEA CAGDP2 (county GDP by industry, current dollars, 2024).  Suppressed "(D)" cells of
    the 20 leaf industries are estimated per state by multi-margin iterative proportional fitting
    (seeded with ACS 2020-2024 C24030 industry employment) so that every disclosed county aggregate
    (private total, goods/services-producing, manufacturing, FIRE, ...) and every state industry
    total published by BEA are reproduced.
    BEA's Virginia independent-city combinations and Maui + Kalawao are split into the Census 2023
    counties in proportion to population (civmap.core pieces).
  * Income / poverty: ACS 2020-2024 5-year B19013 and B17001.

Sri Lanka
  * GDP 2024: national GVA by sector at current prices (DCS Statistical Abstract 2025, Table 10.2)
    -> provinces using the shares published in the CBSL Provincial GDP 2024 statement (as quoted
    by EconomyNext, 24 Dec 2025; cbsl.gov.lk itself is not reachable from the build environment),
    with the provinces/sectors the statement does not quote filled from the last complete CBSL
    provincial breakdown available as open data (DOSE v2.9, year 2020) -> districts using LFS 2024
    sector employment (x HIES 2019 mean household income for industry & services).
  * Income / poverty: HIES 2019 (DCS) median household income and official poverty headcount.
  * Relative Wealth Index (Meta / Chi et al. 2022) aggregated to the H3 grid.
"""
from __future__ import annotations

import html
import json
import re

import h3
import numpy as np
import pandas as pd
import pdfplumber

from civmap import util
from civmap.config import COUNTRIES, US_STATE_FIPS, interim_dir
from civmap.core import SOURCES as CORE_SOURCES
from civmap.core import load_admin, load_hexes, load_pieces
from civmap.fetch import fetch, unzip
from civmap.schema import Metric, Source, write_domain_meta

DOMAIN = "economy"
economy = DOMAIN

US_YEAR = 2024          # BEA county GDP
ACS_YEAR = 2024         # ACS 5-year (2020-2024)
LKA_YEAR = 2024         # national accounts + CBSL provincial GDP release
LKA_BASE_YEAR = 2020    # last complete provincial x sector breakdown in DOSE
HIES_YEAR = 2019

DCS = "https://www.statistics.gov.lk/Resource/en/"
URL = {
    "bea": "https://apps.bea.gov/regional/zip/CAGDP2.zip",
    "dose": "https://zenodo.org/records/13773040/files/DOSE_V2.9.csv?download=1",
    "abstract": "https://www.statistics.gov.lk/abstract2025/CHAP10/10.2.pdf",
    "pgdp_press": "https://economynext.com/sri-lankas-regional-economic-disparities-narrow-central-bank-254593/",
    "lfs": DCS + "LabourForce/Annual_Reports/LFS2024.pdf",
    "hies": DCS + "IncomeAndExpenditure/HouseholdIncomeandExpenditureSurvey2019FinalResults.pdf",
    "poverty": DCS + "Poverty/PovertyIndicators-2019.pdf",
    "rwi": ("https://data.humdata.org/dataset/76f2a2ea-ba50-40f5-b79c-db95d668b843/resource/"
            "7e3ab0ab-fb56-44b0-99f5-84950ed04f7c/download/lka_relative_wealth_index.csv"),
}

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

_core = {s.id: s for s in CORE_SOURCES}

SOURCES: list[Source] = [
    Source("bea_cagdp2",
           "CAGDP2: Gross domestic product (GDP) by county and metropolitan area, by industry (current dollars)",
           "U.S. Bureau of Economic Analysis", URL["bea"], "public domain", "2024", ["usa"],
           "Table CAGDP2, all-areas CSV, column 2024 (thousands of current dollars, NAICS 2017). "
           "County cells suppressed '(D)' for confidentiality are estimated (see metric methods). "
           "Virginia independent-city combinations (5190x/5191x..5195x) and Maui + Kalawao (15901) are "
           "BEA reporting units that are split to Census counties by population. Connecticut is "
           "published for the 9 planning regions (09110-09190) from 2024."),
    Source("census_acs5_2024", "American Community Survey 2020-2024 5-year estimates (table-based Summary File)",
           "U.S. Census Bureau", "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/",
           "public domain", "2020-2024", ["usa"],
           "Tables B19013 (median household income in the past 12 months, 2024 inflation-adjusted "
           "dollars), B17001 (poverty status in the past 12 months by sex by age) and C24030 (sex by "
           "industry for the civilian employed population 16+, used only as the proxy that fills "
           "suppressed BEA cells)."),
    Source("dcs_abstract2025",
           "Statistical Abstract 2025, Table 10.2: Gross Domestic Product by major economic activity at "
           "current market prices 2019-2024",
           "Department of Census and Statistics, Sri Lanka", URL["abstract"], "open (DCS dissemination policy)",
           "2024", ["lka"],
           "Rs. million, 2015 base. 2024 column (last column; the PDF flags 2020-22 and 2024 with '*' = "
           "revised and 2023 with (1) = provisional): Agriculture 2,481,487; Manufacturing, mining & other "
           "industries 5,988,981; Construction 1,643,766; seven service activities summing to 17,182,560; "
           "GVA 27,296,792; taxes on products 2,641,646; subsidies 39,873; GDP 29,898,564. Values are "
           "transcribed in the module and verified at build time to be the last (2024) value of a row of "
           "the cached PDF text. The World Bank's 2024 NY.GDP.MKTP.CD is ~0.7 % higher (probably a later revision; see report.json)."),
    Source("cbsl_pgdp2024_press",
           "Provincial Gross Domestic Product 2024 (Central Bank of Sri Lanka statement, 24 Dec 2025), "
           "as reported by EconomyNext: \"Sri Lanka's regional economic disparities narrow: central bank\"",
           "Central Bank of Sri Lanka (Statistics Department); reported by EconomyNext (Echelon Media)",
           URL["pgdp_press"], "news report quoting an official statistical release", "2024", ["lka"],
           "Shares of nominal GDP 2024 quoted from the CBSL release: total - Western 42.4%, North Western "
           "11.5%, Central 10.7%; Agriculture - North Western 20.0%, Central 13.9%, Southern 11.8%; "
           "Industry - Western 47.6%, North Western 12.0%, Central 9.6%; Services - Western 44.5%, "
           "Central 10.7%, North Western 10.1%; national nominal GDP Rs 29,899 bn. The full nine-province "
           "table is published only on cbsl.gov.lk, which was not reachable from the build environment; "
           "the quoted shares are verified against the cached article text at build time."),
    Source("dose_v29", "DOSE - Global dataset of reported subnational economic output, v2.9",
           "Wenz, Carr, Kögel, Kotz & Kalkuhl (MCC Berlin / PIK); Scientific Data 10, 425 (2023)",
           "https://zenodo.org/records/13773040", "CC BY 4.0", "Sri Lanka 2013-2020", ["lka"],
           "Sri Lanka rows reproduce the CBSL Provincial GDP series (2010 base; GRP in Rs, rounded to "
           "3 significant figures) with agriculture, industry ('man') and services per-capita GVA. Year "
           f"{LKA_BASE_YEAR} is the latest complete province x sector breakdown available as open data."),
    Source("dcs_lfs2024", "Sri Lanka Labour Force Survey Annual Report 2024, Table 13: Employment by major "
           "industry group by districts (both sexes)",
           "Department of Census and Statistics, Sri Lanka", URL["lfs"], "open (DCS dissemination policy)",
           "2024", ["lka"], "Employed persons by district of residence: agriculture, industry, services (page 75)."),
    Source("dcs_hies2019", "Household Income and Expenditure Survey 2019 - Final Results, Table 01: Mean and "
           "median household income per month by sector, province and district",
           "Department of Census and Statistics, Sri Lanka", URL["hies"], "open (DCS dissemination policy)",
           "2019", ["lka"],
           "Latest HIES with district estimates (HIES 2025 is so far published only as a Jan-Mar 2025 "
           "preliminary bulletin at national/sector level). Monthly Rs per household."),
    Source("dcs_poverty2019", "Poverty Indicators 2019, Table 04: Poverty headcount index, number of poor and "
           "contribution to total poverty by sector, province and district",
           "Department of Census and Statistics, Sri Lanka", URL["poverty"], "open (DCS dissemination policy)",
           "2019", ["lka"],
           "From HIES 2019. Updated official poverty line (2012/13 basis, NCPI-adjusted) = Rs 6,966 per "
           "person per month in 2019; old (2002-basis) line = Rs 4,830."),
    Source("meta_rwi", "Relative Wealth Index - Sri Lanka (lka_relative_wealth_index.csv)",
           "Meta Data for Good / Chi, Fang, Chatterjee & Blumenstock (PNAS 119(3), 2022)",
           "https://data.humdata.org/dataset/relative-wealth-index", "CC BY-NC 4.0 (Data for Good)", "2021",
           ["lka"],
           "Predicted relative standard of living for 2.4 km Bing tiles (zoom 14), relative within "
           "the country (unitless; not comparable across countries). 8,336 populated tiles for Sri Lanka "
           "(uninhabited areas such as Yala and Wilpattu national parks have no tile). Released April 2021."),
    Source("worldbank_wdi", "World Development Indicators", "World Bank",
           "https://data.worldbank.org", "CC BY 4.0", "2019-2024", ["lka", "usa"],
           "PA.NUS.FCRF (official LKR per USD, period average) for currency conversion; "
           "NY.GDP.MKTP.CD used only to check national totals."),
    _core["unfpa_codps_lka"],
    _core["census_pep_2023"],
]

# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

_US_GDP_METHOD = (
    "BEA CAGDP2, year {y}, current dollars (thousands x 1,000). Lines: total = 1; agriculture = 3 (NAICS "
    "11); industry = 6 + 10 + 11 + 13 + 25 (mining, utilities, construction, durable + nondurable "
    "manufacturing); services = total - agriculture - industry (includes government, line 83); "
    "manufacturing = 13 + 25 (= line 12); finance = 51 + 56 + 60 (finance & insurance; real estate & "
    "rental/leasing; professional, scientific & technical services). Suppressed '(D)' county cells "
    "(18,737 in {y}; BEA suppresses complementary cells, so no identity has a single unknown) are "
    "estimated for the 20 leaf industries that partition line 2 (private industries) by multi-margin "
    "iterative proportional fitting within each state, seeded with ACS {acs} 5-year C24030 employment "
    "of the matching industry (+1). Margins: every disclosed county aggregate (lines 2, 91, 92, 87, 12, "
    "50, 59, 68, 75, 88, 89, 90, minus its disclosed leaves) and every state leaf total (state value "
    "minus disclosed counties). Disclosed county aggregates are reproduced to within BEA's own $1-4 "
    "thousand rounding except a handful of counties (all < 0.1 %) and state industry totals to within 0.02 % (exact figures in "
    "report.json); national sector totals equal the sum of BEA state rows. BEA combination areas "
    "(23 Virginia county + independent-city combinations, Maui + Kalawao) are split to Census 2023 "
    "counties pro rata to 2023 population (Census PEP vintage 2023). Totals are the sum of the 50 states "
    "+ DC; BEA's US row also includes overseas federal government activity (the whole ~0.55 % gap is in "
    "line 83, government). Allocation to hexes: population-weighted within each county."
).format(y=US_YEAR, acs=ACS_YEAR)

_LKA_GDP_METHOD = (
    "Province level ({y}, current market prices, Rs mn): national GVA by sector from DCS Statistical "
    "Abstract 2025 Table 10.2 (agriculture = A&F&F; industry = manufacturing, mining & other industries "
    "+ construction; services = the seven service activities). Provincial shares of each sector: the "
    "shares quoted from the CBSL PGDP {y} release are used directly (agriculture: NW 20.0, C 13.9, S "
    "11.8; industry: W 47.6, NW 12.0, C 9.6; services: W 44.5, C 10.7, NW 10.1 %); Western agriculture "
    "is the residual that makes Western's GVA share equal its published GDP share (42.4 %); the "
    "remaining share of each sector is split among the other provinces in proportion to their "
    "{b} CBSL provincial GVA in that sector (DOSE v2.9, 2010-base series). Net taxes on products "
    "(taxes 2,641,646 - subsidies 39,873) are allocated pro rata to provincial GVA. Check: the "
    "derived NW and Central GDP shares reproduce the published 11.5 % and 10.7 % (see report.json). "
    "District level: each provincial sector total is split over its districts in proportion to LFS "
    "{y} Table 13 employment in that sector (agriculture) or that employment x HIES 2019 mean monthly "
    "household income of the district (industry, services; a relative productivity proxy, because LFS "
    "counts workers where they live). Net taxes follow district GVA. Districts sum exactly to "
    "provinces and the national total equals DCS GDP. Converted at the World Bank PA.NUS.FCRF "
    "{y} average rate. Allocation to hexes: population-weighted within each district."
).format(y=LKA_YEAR, b=LKA_BASE_YEAR)


def _gdp_metric(key, label, desc, method_us, method_lka, weight="pop", countries=("lka", "usa"), extra_src=()):
    srcs = ["bea_cagdp2", "census_acs5_2024", "census_pep_2023", "dcs_abstract2025", "cbsl_pgdp2024_press",
            "dose_v29", "dcs_lfs2024", "dcs_hies2019", "worldbank_wdi", *extra_src]
    method = " | ".join(m for m in (
        ("USA: " + method_us) if "usa" in countries else "",
        ("Sri Lanka: " + method_lka) if "lka" in countries else "") if m)
    return Metric(key, label, "USD", "economy", "extensive", weight, "admin_adm2", srcs,
                  f"USA {US_YEAR}; Sri Lanka {LKA_YEAR}", desc, method, "higher", list(countries))


METRICS: list[Metric] = [
    _gdp_metric("gdp_usd", "GDP (Gold)", "Gross domestic product at current prices, US dollars.",
                _US_GDP_METHOD + " gdp_usd = line 1 (all-industry total).",
                _LKA_GDP_METHOD + " gdp_usd = GVA at basic prices + net taxes on products = GDP at market "
                "prices. Note: US county GDP is industry value added (taxes on production are inside "
                "industry value added), so both are GDP at market prices."),
    _gdp_metric("gdp_agri_usd", "Agriculture GDP", "Value added of agriculture, forestry and fishing (NAICS 11 / ISIC A).",
                _US_GDP_METHOD + " Hex allocation uses the cropland weight.",
                _LKA_GDP_METHOD + " Sector values are GVA at basic prices (they sum to GVA, not GDP). "
                "Hex allocation uses the cropland weight.", weight="cropland"),
    _gdp_metric("gdp_industry_usd", "Industry GDP (Production)",
                "Value added of mining, manufacturing, construction and utilities.",
                _US_GDP_METHOD, _LKA_GDP_METHOD + " Sector values are GVA at basic prices; DCS 'industry' = "
                "mining & quarrying, manufacturing, electricity/gas/water/waste and construction."),
    _gdp_metric("gdp_services_usd", "Services GDP", "Value added of all service activities incl. government.",
                _US_GDP_METHOD, _LKA_GDP_METHOD + " Sector values are GVA at basic prices."),
    _gdp_metric("gdp_manufacturing_usd", "Manufacturing GDP", "Value added of manufacturing (NAICS 31-33).",
                _US_GDP_METHOD, "", countries=("usa",)),
    _gdp_metric("gdp_finance_usd", "Finance, real estate & professional services GDP",
                "Value added of finance & insurance, real estate & rental/leasing and professional, "
                "scientific & technical services (NAICS 52, 53, 54).",
                _US_GDP_METHOD, "", countries=("usa",)),
    Metric("gdp_pc_usd", "GDP per capita", "USD/person", "economy", "intensive", "pop", "admin_adm2",
           ["bea_cagdp2", "census_acs5_2024", "census_pep_2023", "dcs_abstract2025", "cbsl_pgdp2024_press",
            "dose_v29", "dcs_lfs2024", "dcs_hies2019", "unfpa_codps_lka", "worldbank_wdi"],
           f"GDP {US_YEAR if US_YEAR == LKA_YEAR else f'{US_YEAR}/{LKA_YEAR}'}; population 2023",
           "GDP per resident at current prices.",
           "gdp_usd of the county/district divided by its 2023 population used by the core grid "
           "(US Census PEP vintage 2023; Sri Lanka DCS mid-2023 estimates via COD-PS). USA: BEA county "
           "GDP is workplace-based, so commuter and resource hubs (Manhattan, DC, oil counties such as "
           "Loving County TX with 43 residents) are strongly inflated. Sri Lanka: provincial GDP is "
           "workplace-based, but the district split uses LFS employment by place of residence, so "
           "commuting into Colombo district is NOT captured (Colombo likely understated, Gampaha/Kalutara "
           "overstated).",
           "higher"),
    Metric("hh_income_usd", "Median household income", "USD/year", "income", "intensive", "pop", "admin_adm2",
           ["census_acs5_2024", "dcs_hies2019", "worldbank_wdi"],
           f"USA ACS {ACS_YEAR - 4}-{ACS_YEAR}; Sri Lanka HIES {HIES_YEAR}",
           "Median annual household income, current US dollars of the survey year.",
           f"USA: ACS {ACS_YEAR - 4}-{ACS_YEAR} 5-year B19013_E001 (median household income in the past 12 "
           f"months, {ACS_YEAR} inflation-adjusted dollars), county. | Sri Lanka: HIES {HIES_YEAR} Final "
           "Results Table 01, district MEDIAN monthly household income (Rs, all income incl. in-kind and "
           f"imputed rent of owner-occupied housing) x 12, converted at the World Bank PA.NUS.FCRF "
           f"{HIES_YEAR} average rate. Different survey years (2019 vs 2024 $) and income concepts: "
           "compare levels with care (no PPP adjustment applied).",
           "higher"),
    Metric("poverty_rate", "Poverty rate", "%", "income", "intensive", "pop", "admin_adm2",
           ["census_acs5_2024", "dcs_poverty2019", "worldbank_wdi"],
           f"USA ACS {ACS_YEAR - 4}-{ACS_YEAR}; Sri Lanka HIES {HIES_YEAR}",
           "Share of people living below the national official poverty line.",
           "USA: ACS 5-year B17001_E002 / B17001_E001 x 100 (people below the Census Bureau's "
           "income-based official poverty thresholds, by family size/composition; universe = people for "
           "whom poverty status is determined). | Sri Lanka: DCS Poverty Indicators 2019 Table 04 "
           "headcount index under the UPDATED official poverty line (consumption-based, Rs 6,966 per "
           "person per month in 2019 = about US$39 at the 2019 PA.NUS.FCRF rate; cost-of-basic-needs "
           "line anchored at 2,030 kcal per person per day). The two national lines are very different "
           "absolute standards (the Sri Lankan line is far below the US thresholds), so rates are "
           "comparable only within each country.",
           "lower"),
    Metric("poor_persons", "People in poverty", "persons", "income", "extensive", "pop", "admin_adm2",
           ["census_acs5_2024", "dcs_poverty2019"],
           f"USA ACS {ACS_YEAR - 4}-{ACS_YEAR}; Sri Lanka HIES {HIES_YEAR}",
           "Number of people below the national official poverty line.",
           "USA: ACS 5-year B17001_E002. | Sri Lanka: Poverty Indicators 2019 Table 04, number of poor "
           "people under the updated official poverty line. Same definitional caveats as poverty_rate.",
           "lower"),
    Metric("rwi", "Relative Wealth Index", "index", "income", "intensive", "pop", "hex",
           ["meta_rwi"], "2021",
           "Meta Relative Wealth Index: predicted asset-based wealth relative to the rest of Sri Lanka "
           "(unitless relative scale; higher = wealthier; tile statistics in report.json).",
           "Unweighted mean of the RWI values of the 2.4 km Bing quadkey tiles whose centre falls in each "
           "H3 res-6 land cell (tiles centred in a sea cell are snapped to the nearest adjacent land cell). "
           "Land cells with no tile take the mean of the original cell values within 1 ring, then 2 rings; "
           "cells still without a value (uninhabited parts of Yala and Wilpattu national parks, where Meta "
           "publishes no tile) stay empty. "
           "No US equivalent (the RWI covers low- and middle-income countries only).",
           "higher", ["lka"]),
]

# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

_ALIASES = {
    "moneragala": "monaragala", "rathnapura": "ratnapura", "vavunia": "vavuniya",
    "mullaittivu": "mullaitivu", "kaluthara": "kalutara", "anuradapura": "anuradhapura",
    "puttalama": "puttalam", "puttlam": "puttalam", "kegalla": "kegalle", "digamadulla": "ampara",
    "kilinochcchi": "kilinochchi", "northwestern": "northwestern", "northcentral": "northcentral",
}


def _key(name: str) -> str:
    k = re.sub(r"[^a-z]", "", str(name).lower())
    return _ALIASES.get(k, k)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _pdf_text(path, page_match: str, crop_left_of: str | None = None, require: str = "Colombo") -> str:
    """Text of the first page containing `page_match` and `require` (optionally cropped at a header word)."""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if page_match in t and require in t:
                if crop_left_of:
                    xs = [w["x0"] for w in page.extract_words() if w["text"] == crop_left_of]
                    if xs:
                        return page.crop((min(xs) - 3, 0, page.width, page.height)).extract_text() or ""
                return t
    raise ValueError(f"{path.name}: no page containing {page_match!r}")


def _html_text(path) -> str:
    s = path.read_text(errors="ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))


def _admin_index(country: str, level: str) -> dict[str, str]:
    a = load_admin(country, level)
    return {_key(n): i for i, n in zip(a.admin_id, a.name)}


def _match_rows(rows: list[tuple[str, list[float]]], country: str, level: str, what: str) -> pd.DataFrame:
    idx = _admin_index(country, level)
    out = {}
    for name, vals in rows:
        k = _key(name)
        if k in idx:
            out[idx[k]] = vals
    missing = sorted(set(idx.values()) - set(out))
    if missing:
        raise ValueError(f"{what}: no row for {missing}")
    return pd.DataFrame.from_dict(out, orient="index")


# ---------------------------------------------------------------------------------------------
# Sri Lanka
# ---------------------------------------------------------------------------------------------

# DCS Statistical Abstract 2025, Table 10.2, column 2024* (Rs. million, current market prices).
DCS_GDP_2024 = {
    "agri": [("Agriculture, Forestry & Fishing", 2_481_487)],
    "industry": [("Manufacturing, mining and quarrying and other industries", 5_988_981),
                 ("Construction", 1_643_766)],
    "services": [("Wholesale & retail trade, transportation & storage, accommodation & food", 8_270_177),
                 ("Information and communication", 606_743),
                 ("Financial and insurance activities", 2_218_227),
                 ("Real estate activities incl. ownership of dwellings", 1_193_511),
                 ("Professional, scientific, technical, administration & support", 580_609),
                 ("Public administration, defence, education, health & social work", 2_231_482),
                 ("Other services", 2_081_811)],
    "taxes": [("Taxes on products", 2_641_646)],
    "subsidies": [("Subsidies on products", 39_873)],
    "gva": 27_296_792,
    "gdp": 29_898_564,
}

# CBSL Provincial GDP 2024 shares (% of national), quoted in the cached EconomyNext article.
# Each entry: (sector, province pcode, share %, exact phrase that must appear in the article).
PGDP_2024_QUOTED = [
    ("total", "LK1", 42.4, "accounting for 42.4 percent"),
    ("total", "LK6", 11.5, "North Western (11.5 per cent)"),
    ("total", "LK2", 10.7, "Central (10.7 per cent) provinces followed"),
    ("agri", "LK6", 20.0, "contributor to agricultural activities, accounting for 20.0 per cent"),
    ("agri", "LK2", 13.9, "shares of 13.9 per cent and 11.8 per cent"),
    ("agri", "LK3", 11.8, "shares of 13.9 per cent and 11.8 per cent"),
    ("industry", "LK1", 47.6, "accounting for 47.6 per cent of the total Industry value"),
    ("industry", "LK6", 12.0, "North Western (12.0 per cent) and Central (9.6 per cent)"),
    ("industry", "LK2", 9.6, "North Western (12.0 per cent) and Central (9.6 per cent)"),
    ("services", "LK1", 44.5, "Services activities with 44.5 per cent"),
    ("services", "LK2", 10.7, "followed by the Central (10.7 per cent) and North Western (10.1 per cent)"),
    ("services", "LK6", 10.1, "followed by the Central (10.7 per cent) and North Western (10.1 per cent)"),
]
SECTORS = ["agri", "industry", "services"]


def _lka_national() -> dict[str, float]:
    p = fetch(URL["abstract"], "dcs_abstract2025", "lka/economy", "abstract2025_10.2.pdf")
    with pdfplumber.open(p) as pdf:
        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    # rows end with the six year columns 2019 ... 2024; collect the last (2024) value of each row
    num = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    rx = re.compile(rf"(?:{num}\s+){{5}}({num})\s*$")
    last_col = {_num(m.group(1)) for m in (rx.search(l.strip()) for l in text.splitlines()) if m}
    for key, items in DCS_GDP_2024.items():
        vals = items if isinstance(items, list) else [("", items)]
        for _, v in vals:
            if float(v) not in last_col:
                raise ValueError(f"DCS Table 10.2: value {v:,} ({key}) is not a 2024-column value of the PDF")
    nat = {k: float(sum(v for _, v in DCS_GDP_2024[k])) for k in ("agri", "industry", "services")}
    nat["tls"] = float(DCS_GDP_2024["taxes"][0][1] - DCS_GDP_2024["subsidies"][0][1])
    gva = sum(nat[s] for s in SECTORS)
    assert abs(gva - DCS_GDP_2024["gva"]) <= 5, gva
    assert abs(gva + nat["tls"] - DCS_GDP_2024["gdp"]) <= 5
    return nat


def _lka_quoted_shares() -> dict[tuple[str, str], float]:
    p = fetch(URL["pgdp_press"], "cbsl_pgdp2024_press", "lka/economy", "economynext_pgdp2024.html",
              headers={"User-Agent": "Mozilla/5.0"})
    text = _html_text(p)
    for _, _, v, phrase in PGDP_2024_QUOTED:
        if phrase not in text:
            raise ValueError(f"PGDP 2024 article: phrase not found: {phrase!r}")
        if f"{v:.1f} per" not in phrase:
            raise ValueError(f"PGDP 2024 article: share {v} not stated in phrase {phrase!r}")
    if "29,899 billion" not in text:
        raise ValueError("PGDP 2024 article: national GDP figure not found")
    return {(s, pc): v / 100.0 for s, pc, v, _ in PGDP_2024_QUOTED}


def _lka_dose_base() -> pd.DataFrame:
    """CBSL provincial GVA by sector (Rs) for LKA_BASE_YEAR from DOSE v2.9."""
    p = fetch(URL["dose"], "dose_v29", "lka/economy", "DOSE_V2.9.csv")
    d = pd.read_csv(p, low_memory=False)
    d = d[(d.GID_0 == "LKA") & (d.year == LKA_BASE_YEAR)].copy()
    rows = [(r.region, [r.ag_grp_pc_lcu * r["pop"], r.man_grp_pc_lcu * r["pop"],
                        r.serv_grp_pc_lcu * r["pop"], r.grp_lcu]) for _, r in d.iterrows()]
    out = _match_rows(rows, "lka", "adm1", "DOSE provinces")
    out.columns = SECTORS + ["grp"]
    return out


def _lka_provinces() -> tuple[pd.DataFrame, dict]:
    """Provincial GVA by sector + net taxes, Rs million, LKA_YEAR."""
    nat = _lka_national()
    quoted = _lka_quoted_shares()
    base = _lka_dose_base()
    provs = list(base.index)
    gva_nat = sum(nat[s] for s in SECTORS)
    share = pd.DataFrame(np.nan, index=provs, columns=SECTORS)
    for (s, pc), v in quoted.items():
        if s in SECTORS:
            share.loc[pc, s] = v
    # Western agriculture: residual making Western's GVA share equal its published GDP share
    w = "LK1"
    w_rest = sum(share.loc[w, s] * nat[s] for s in ("industry", "services"))
    share.loc[w, "agri"] = (quoted[("total", w)] * gva_nat - w_rest) / nat["agri"]
    for s in SECTORS:
        known = share[s].notna()
        rem = 1.0 - share.loc[known, s].sum()
        if rem < 0:
            raise ValueError(f"quoted {s} shares exceed 100%")
        b = base.loc[~known, s]
        share.loc[~known, s] = rem * b / b.sum()
    prov = share.mul(pd.Series(nat)[SECTORS], axis=1)
    prov["gva"] = prov[SECTORS].sum(axis=1)
    prov["tls"] = nat["tls"] * prov["gva"] / prov["gva"].sum()
    prov["gdp"] = prov["gva"] + prov["tls"]
    check = {
        "national_gdp_rs_mn": float(prov.gdp.sum()),
        "dcs_gdp_rs_mn": DCS_GDP_2024["gdp"],
        "gdp_share_pct": (100 * prov.gdp / prov.gdp.sum()).round(2).to_dict(),
        "sector_share_pct": (100 * share).round(2).to_dict(),
        "published_total_share_pct": {pc: 100 * v for (s, pc), v in quoted.items() if s == "total"},
        "base_year_dose_gdp_share_pct": (100 * base.grp / base.grp.sum()).round(2).to_dict(),
    }
    for (s, pc), v in quoted.items():
        if s == "total":
            got = prov.gdp[pc] / prov.gdp.sum()
            if abs(got - v) > 0.0015:
                print(f"  warn: {pc} derived GDP share {got:.4f} vs published {v:.4f}")
    return prov, check


def _lka_lfs() -> pd.DataFrame:
    p = fetch(URL["lfs"], "dcs_lfs2024", "lka/economy", "LFS2024.pdf")
    t = _pdf_text(p, "Table 13 - Employment by major industry group by districts - Both sexes")
    rx = re.compile(r"^([A-Za-z][A-Za-z ]+?)\s+([\d,]+)\s+100\.0\s+([\d,]+)\s+[\d.]+\s+([\d,]+)\s+[\d.]+"
                    r"\s+([\d,]+)\s+[\d.]+\s*$")
    rows = [(m.group(1), [_num(m.group(i)) for i in range(2, 6)])
            for m in (rx.match(l.strip()) for l in t.splitlines()) if m]
    d = _match_rows(rows, "lka", "adm2", "LFS Table 13")
    d.columns = ["emp_total", "emp_agri", "emp_industry", "emp_services"]
    tot = [v for n, v in rows if _key(n) == "total"]
    if tot:
        assert abs(d.emp_total.sum() - tot[0][0]) < 50, "LFS district total mismatch"
    return d


def _lka_hies() -> pd.DataFrame:
    p = fetch(URL["hies"], "dcs_hies2019", "lka/economy")
    t = _pdf_text(p, "Table 01: Mean and median household income per month", crop_left_of="Sector/Province")
    rx = re.compile(r"^([A-Za-z][A-Za-z -]+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)\s+([\d,]+)\s*$")
    rows = [(m.group(1), [_num(m.group(2)), _num(m.group(6))])
            for m in (rx.match(l.strip()) for l in t.splitlines()) if m]
    d = _match_rows(rows, "lka", "adm2", "HIES 2019 Table 01")
    d.columns = ["hh_income_mean_rs", "hh_income_median_rs"]
    return d


def _lka_poverty() -> pd.DataFrame:
    p = fetch(URL["poverty"], "dcs_poverty2019", "lka/economy")
    t = _pdf_text(p, "Table 04: Poverty headcount index")
    rx = re.compile(r"^([A-Za-z][A-Za-z ]+?)\s+([\d.]+)\s+([\d,]+)\s+([\d.]+)\s+([\d.]+)\s+([\d,]+)\s+([\d.]+)\s*$")
    nat = [[_num(m.group(i)) for i in (2, 3, 5, 6)]
           for m in (rx.match(l.strip()) for l in t[:t.index("\nSector")].splitlines()) if m
           and _key(m.group(1)) == "srilanka"]
    if len(nat) != 1:
        raise ValueError("Poverty Table 04: national (Sri Lanka) row not found")
    t = t[t.index("\nDistrict"):]  # district block only (province names never collide, but be safe)
    rows = [(m.group(1), [_num(m.group(i)) for i in (2, 3, 5, 6)])
            for m in (rx.match(l.strip()) for l in t.splitlines()) if m]
    d = _match_rows(rows, "lka", "adm2", "Poverty Table 04")
    d.columns = ["hci_old", "poor_old", "hci_upl", "poor_upl"]
    d.attrs["national"] = dict(zip(d.columns, nat[0]))
    return d


def _lka_rwi() -> pd.DataFrame:
    p = fetch(URL["rwi"], "meta_rwi", "lka/economy", "lka_relative_wealth_index.csv")
    d = pd.read_csv(p)
    res = COUNTRIES["lka"]["h3_res"]
    hexes = load_hexes("lka")
    land = set(hexes.loc[hexes.ring == 0, "h3"])
    cells = []
    for la, lo in zip(d.latitude, d.longitude):
        c = h3.latlng_to_cell(float(la), float(lo), res)
        if c not in land:
            nb = [n for n in h3.grid_ring(c, 1) if n in land]
            c = min(nb, key=lambda n: h3.great_circle_distance((la, lo), h3.cell_to_latlng(n))) if nb else None
        cells.append(c)
    d["h3"] = cells
    dropped = int(d.h3.isna().sum())
    m = d.dropna(subset=["h3"]).groupby("h3").rwi.mean()
    out = pd.Series(np.nan, index=sorted(land))
    out.loc[m.index] = m
    orig = out.copy()
    for k in (1, 2):
        for c in out.index[out.isna()]:
            vals = [orig[n] for n in h3.grid_disk(c, k) if n in orig.index and np.isfinite(orig[n])]
            if vals:
                out[c] = float(np.mean(vals))
    stats = {"tiles": int(len(d)), "tiles_off_grid": dropped, "tile_mean": round(float(d.rwi.mean()), 4),
             "tile_median": round(float(d.rwi.median()), 4), "tile_min": float(d.rwi.min()),
             "tile_max": float(d.rwi.max()), "cells_with_tiles": int(len(m)),
             "cells_filled_from_neighbours": int(out.notna().sum() - len(m)),
             "cells_empty": sorted(out.index[out.isna()])}
    print(f"  rwi: {len(d)} tiles, {dropped} off-grid, {len(m)} cells with tiles, "
          f"{stats['cells_filled_from_neighbours']} filled from neighbours, {int(out.isna().sum())} empty")
    return pd.DataFrame({"h3": out.index, "rwi": out.round(4).values}), stats


def build_lka() -> dict:
    cc = "lka"
    prov, check = _lka_provinces()
    lfs = _lka_lfs()
    hies = _lka_hies()
    pov = _lka_poverty()
    adm2 = load_admin(cc, "adm2").set_index("admin_id")
    dist = pd.DataFrame(index=adm2.index)
    dist["parent"] = adm2.parent_id
    weights = {
        "agri": lfs.emp_agri,
        "industry": lfs.emp_industry * hies.hh_income_mean_rs,
        "services": lfs.emp_services * hies.hh_income_mean_rs,
    }
    for s in SECTORS:
        w = weights[s].reindex(dist.index)
        wsum = w.groupby(dist.parent).transform("sum")
        dist[s] = prov[s].reindex(dist.parent).values * (w / wsum).values
    dist["gva"] = dist[SECTORS].sum(axis=1)
    dist["tls"] = (prov.tls.reindex(dist.parent).values * dist.gva
                   / dist.gva.groupby(dist.parent).transform("sum"))
    dist["gdp"] = dist.gva + dist.tls
    # districts must sum to provinces
    chk = dist.groupby("parent")[SECTORS + ["gdp"]].sum()
    assert np.allclose(chk.values, prov.loc[chk.index, SECTORS + ["gdp"]].values, rtol=1e-9)

    fx = util.lkr_per_usd(LKA_YEAR)
    fx19 = util.lkr_per_usd(HIES_YEAR)
    to_usd = 1e6 / fx
    out = pd.DataFrame({"admin_id": dist.index, "name": adm2.name.reindex(dist.index).values})
    out["gdp_usd"] = (dist.gdp * to_usd).round(0).values
    out["gdp_agri_usd"] = (dist.agri * to_usd).round(0).values
    out["gdp_industry_usd"] = (dist.industry * to_usd).round(0).values
    out["gdp_services_usd"] = (dist.services * to_usd).round(0).values
    pop = adm2["pop"].reindex(dist.index).values
    out["gdp_pc_usd"] = np.round(out.gdp_usd.values / pop, 1)
    out["hh_income_usd"] = (hies.hh_income_median_rs.reindex(dist.index) * 12 / fx19).round(0).values
    out["poverty_rate"] = pov.hci_upl.reindex(dist.index).values
    out["poor_persons"] = pov.poor_upl.reindex(dist.index).values
    util.write_admin(cc, DOMAIN, "adm2", out)

    rwi, rwi_stats = _lka_rwi()
    util.write_hex(cc, DOMAIN, rwi)
    if abs(out.poor_persons.sum() - pov.attrs["national"]["poor_upl"]) > 1000:
        raise ValueError("district poor persons do not add up to the published national total")

    wb_gdp, wb_year = util.wb_value("NY.GDP.MKTP.CD", "LKA", LKA_YEAR)
    total = float(out.gdp_usd.sum())
    report = {
        "year": LKA_YEAR, "lkr_per_usd": fx, "lkr_per_usd_2019": fx19,
        "gdp_usd_total": total, "worldbank_gdp_usd": wb_gdp, "worldbank_year": wb_year,
        "ratio_to_worldbank": total / wb_gdp if wb_gdp else None,
        "poor_persons_total": float(out.poor_persons.sum()),
        "dcs_national_2019_published": pov.attrs["national"],
        "worldbank_poverty_headcount_nahc": util.wb_value("SI.POV.NAHC", "LKA", HIES_YEAR),
        "rwi_tiles": rwi_stats,
        "provinces": check,
        "colombo_share_of_gdp_pct": float(100 * out.set_index("admin_id").gdp_usd["LK11"] / total),
    }
    print(f"  LKA GDP {LKA_YEAR}: ${total / 1e9:.2f} bn (World Bank {wb_year}: ${(wb_gdp or 0) / 1e9:.2f} bn); "
          f"Colombo {report['colombo_share_of_gdp_pct']:.1f}%; poor {report['poor_persons_total']:,.0f}")
    return report


# ---------------------------------------------------------------------------------------------
# United States
# ---------------------------------------------------------------------------------------------

LINES_NEEDED = [1, 2, 3, 6, 10, 11, 12, 13, 25, 34, 35, 36, 45, 50, 51, 56, 59, 60, 64, 65, 68, 69, 70,
                75, 76, 79, 82, 83, 87, 88, 89, 90, 91, 92]
# ACS C24030 column numbers (male block; female = +27) for each BEA line
_ACS = {3: [4], 6: [5], 10: [12], 11: [6], 12: [7], 13: [7], 25: [7], 34: [8], 35: [9], 36: [11], 45: [13],
        51: [15], 56: [16], 60: [18], 64: [19], 65: [20], 69: [22], 70: [23], 76: [25], 79: [26], 82: [27],
        83: [28]}
_ACS[87] = _ACS[3] + _ACS[6]
_ACS[50] = _ACS[51] + _ACS[56]
_ACS[59] = _ACS[60] + _ACS[64] + _ACS[65]
_ACS[68] = _ACS[69] + _ACS[70]
_ACS[75] = _ACS[76] + _ACS[79]
_ACS[91] = _ACS[87] + _ACS[11] + _ACS[12]
_ACS[92] = _ACS[10] + _ACS[34] + _ACS[35] + _ACS[36] + _ACS[45] + _ACS[50] + _ACS[59] + _ACS[68] + _ACS[75] + _ACS[82]


def _bea_raw() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """(county matrix, state matrix, county names) for US_YEAR; thousands of $; NaN = suppressed."""
    z = fetch(URL["bea"], "bea_cagdp2", "usa/bea")
    f = next(p for p in unzip(z).iterdir() if p.name.startswith("CAGDP2__ALL_AREAS"))
    d = pd.read_csv(f, dtype=str, encoding="latin-1")
    d = d[d.LineCode.notna()].copy()
    d["fips"] = d.GeoFIPS.str.strip().str.strip('"').str.strip()
    d["line"] = d.LineCode.str.strip().astype(int)
    if str(US_YEAR) not in d.columns:
        raise ValueError(f"CAGDP2 has no {US_YEAR} column")
    raw = d[str(US_YEAR)].str.strip()
    d["val"] = pd.to_numeric(raw, errors="coerce")
    d["na"] = raw == "(NA)"
    d = d[d.fips.str[:2].isin(US_STATE_FIPS) & d.line.isin(LINES_NEEDED)]
    tot = d[d.line == 1]
    live = set(tot.loc[~tot.na, "fips"])  # units that exist in US_YEAR
    d = d[d.fips.isin(live)]
    wide = d.pivot(index="fips", columns="line", values="val")[LINES_NEEDED]
    names = d.drop_duplicates("fips").set_index("fips").GeoName.str.strip()
    is_state = wide.index.str[2:] == "000"
    states, counties = wide[is_state].copy(), wide[~is_state].copy()
    if states.isna().any().any():
        raise ValueError("suppressed state cells")
    return counties, states, names


def _combo_map(units: pd.Index, names: pd.Series) -> dict[str, list[str]]:
    """BEA combination areas -> Census 2023 county GEOIDs (matched by name)."""
    adm = load_admin("usa", "adm2")
    census = set(adm.admin_id)
    by_name = {(r.admin_id[:2], r.name.lower()): r.admin_id for r in adm.itertuples()}
    out = {}
    for u in units:
        if u in census:
            continue
        st = u[:2]
        nm = re.sub(r",\s*[A-Z]{2}\*?$", "", names[u]).strip()
        parts = [p.strip() for p in re.split(r"\+|,", nm) if p.strip()]
        ids = []
        for i, p in enumerate(parts):
            p0 = re.sub(r"\s+City$", "", p)
            # first part is the county ("James City" -> "James City County"), the rest are cities
            cands = [f"{p} county", f"{p0} county"] if (st == "15" or i == 0) else [f"{p0} city", f"{p} city"]
            hit = next((by_name[(st, c.lower())] for c in cands if (st, c.lower()) in by_name), None)
            if hit is None:
                raise ValueError(f"cannot match BEA unit {u} part {p!r}")
            ids.append(hit)
        out[u] = ids
    covered = (set(units) & census) | {c for v in out.values() for c in v}
    missing = sorted(census - covered)
    if missing:
        raise ValueError(f"Census counties without a BEA unit: {missing[:10]}")
    return out


def _acs_proxy(units: pd.Index, combos: dict[str, list[str]]) -> pd.DataFrame:
    acs = util.acs_table("C24030", ACS_YEAR)
    unit_of = {c: c for c in acs.index}
    for u, cs in combos.items():
        for c in cs:
            unit_of[c] = u
    acs = acs.groupby(acs.index.map(lambda c: unit_of.get(c, c))).sum()
    prox = pd.DataFrame(index=units)
    for line, cols in _ACS.items():
        cc = [f"C24030_E{c:03d}" for c in cols] + [f"C24030_E{c + 27:03d}" for c in cols]
        prox[line] = acs.reindex(units)[cc].sum(axis=1).fillna(0.0)
    return prox


def _exact_pass(V: pd.DataFrame) -> int:
    """Solve identities with exactly one unknown term. Returns number of cells filled."""
    filled = 0
    for _ in range(10):
        n0 = filled
        for parent, kids in [(1, [2, 83]), *AGGREGATES.items()]:
            cols = [parent] + kids
            M = V[cols]
            one = M.isna().sum(axis=1) == 1
            for idx in M.index[one]:
                row = M.loc[idx]
                miss = row.index[row.isna()][0]
                if miss == parent:
                    val = row[kids].sum()
                else:
                    val = row[parent] - row[[k for k in kids if k != miss]].sum()
                V.at[idx, miss] = max(val, 0.0)
                filled += 1
        if filled == n0:
            break
    return filled


# leaf industries (they partition line 2 = private industries) and the aggregates BEA publishes
LEAVES = [3, 6, 11, 13, 25, 10, 34, 35, 36, 45, 51, 56, 60, 64, 65, 69, 70, 76, 79, 82]
AGGREGATES = {  # aggregate line -> its leaves; order = IPF block order (line 2 last: county totals exact)
    91: [3, 6, 11, 13, 25],
    92: [10, 34, 35, 36, 45, 51, 56, 60, 64, 65, 69, 70, 76, 79, 82],
    87: [3, 6],
    12: [13, 25],
    50: [51, 56],
    59: [60, 64, 65],
    68: [69, 70],
    75: [76, 79],
    88: [34, 35],
    89: [10, 36],
    90: [13, 25, 45],
    2: LEAVES,
}


def _ipf_state(L: np.ndarray, A: dict[int, np.ndarray], leaf_t: np.ndarray, seed: np.ndarray,
               iters: int = 2000, tol: float = 1e-7) -> tuple[np.ndarray, float]:
    """Fill unknown leaf cells of one state by multi-margin IPF.

    L: counties x leaves with NaN = unknown; A: aggregate line -> county values (NaN = unknown);
    leaf_t: state total of each leaf; seed: positive prior for every cell.
    Margins: every known county aggregate (sum of its leaves) and every state leaf total.
    """
    U = np.isnan(L)
    K = np.where(U, 0.0, L)
    x = np.where(U, seed, 0.0)
    col_idx = {ln: i for i, ln in enumerate(LEAVES)}
    blocks = []
    for agg, leaves in AGGREGATES.items():
        cols = [col_idx[c] for c in leaves]
        a = A[agg]
        rows = np.where(np.isfinite(a) & U[:, cols].any(axis=1))[0]
        if len(rows):
            tgt = np.clip(a[rows] - K[np.ix_(rows, cols)].sum(axis=1), 0, None)
            blocks.append((rows, np.array(cols), tgt))
    col_t = np.clip(leaf_t - K.sum(axis=0), 0, None)
    err = np.inf
    for it in range(iters):
        cs = x.sum(axis=0)
        x *= np.divide(col_t, cs, out=np.ones_like(cs), where=cs > 0)
        for rows, cols, tgt in blocks:
            sub = x[np.ix_(rows, cols)]
            rs = sub.sum(axis=1)
            f = np.divide(tgt, rs, out=np.ones_like(rs), where=rs > 0)
            x[np.ix_(rows, cols)] = sub * f[:, None]
        if it % 10 == 9:
            cs = x.sum(axis=0)
            err = float(np.max(np.abs(cs - col_t) / np.maximum(col_t, 1000.0)))
            if err < tol:
                break
    return np.where(U, x, L), err


def _fill_bea(V: pd.DataFrame, S: pd.DataFrame, prox: pd.DataFrame) -> dict:
    n_sup = int(V.isna().sum().sum())
    n_exact = _exact_pass(V)
    n_ipf = 0
    worst = 0.0
    for st in S.index:
        rows = V.index[V.index.str[:2] == st[:2]]
        L = V.loc[rows, LEAVES].to_numpy(dtype=float, copy=True)
        if not np.isnan(L).any():
            continue
        A = {agg: V.loc[rows, agg].to_numpy(dtype=float) for agg in AGGREGATES}
        seed = prox.loc[rows, LEAVES].to_numpy(dtype=float) + 1.0
        filled, err = _ipf_state(L, A, S.loc[st, LEAVES].to_numpy(dtype=float), seed)
        worst = max(worst, err)
        n_ipf += int(np.isnan(L).sum())
        V.loc[rows, LEAVES] = filled
    # suppressed aggregates = sum of their (now complete) leaves
    for agg, leaves in AGGREGATES.items():
        miss = V[agg].isna()
        V.loc[miss, agg] = V.loc[miss, leaves].sum(axis=1)
    left = int(V[LEAVES + list(AGGREGATES)].isna().sum().sum())
    if left:
        raise RuntimeError(f"{left} BEA cells still unknown")
    return {"suppressed_cells": n_sup, "solved_exactly": n_exact, "leaf_cells_filled_by_ipf": n_ipf,
            "worst_state_leaf_residual": round(worst, 8)}


def build_usa() -> dict:
    cc = "usa"
    V, S, names = _bea_raw()
    combos = _combo_map(V.index, names)
    prox = _acs_proxy(V.index, combos)
    V0 = V.copy()
    fill = _fill_bea(V, S, prox)
    # state consistency after filling (every leaf and aggregate vs the BEA state row)
    lines = [1] + LEAVES + list(AGGREGATES)
    st_sum = V.groupby(V.index.str[:2]).sum()
    S2 = S.copy()
    S2.index = S2.index.str[:2]
    rel = ((st_sum[lines] - S2[lines]).abs() / S2[lines].abs().clip(lower=1)).max()
    # disclosed county aggregates vs the sum of their (partly estimated) leaves, $ thousands
    agg_dev, agg_rel = {}, {}
    for agg, leaves in AGGREGATES.items():
        known = V0[agg].notna()
        dv = (V.loc[known, leaves].sum(axis=1) - V0.loc[known, agg]).abs()
        agg_dev[agg] = float(dv.max())
        # beyond BEA's own rounding (published leaves rarely add to the aggregate by up to $4k)
        agg_rel[agg] = float((dv.where(dv > 4, 0.0) / V0.loc[known, agg].abs().clip(lower=1)).max())
    fill["max_state_rel_deviation_pct"] = round(100 * float(rel.max()), 4)
    fill["max_state_rel_deviation_line"] = int(rel.idxmax())
    fill["max_county_aggregate_deviation_kusd"] = round(max(agg_dev.values()), 1)
    fill["max_county_aggregate_rel_deviation_pct"] = round(100 * max(agg_rel.values()), 4)
    if rel.max() > 2e-4:
        raise RuntimeError(f"BEA fill: state totals off by {100 * rel.max():.3f} % (line {rel.idxmax()})")
    print(f"  BEA {US_YEAR}: {fill}")

    u = pd.DataFrame(index=V.index)
    k = 1000.0
    u["gdp_usd"] = V[1] * k
    u["gdp_agri_usd"] = V[3] * k
    u["gdp_industry_usd"] = (V[6] + V[10] + V[11] + V[13] + V[25]) * k
    u["gdp_manufacturing_usd"] = (V[13] + V[25]) * k
    u["gdp_finance_usd"] = (V[51] + V[56] + V[60]) * k
    over = u.gdp_agri_usd + u.gdp_industry_usd - u.gdp_usd
    if (over > 1000).any():
        print(f"  note: {int((over > 1000).sum())} units with agri+industry > total; clipped services at 0")
    u["gdp_services_usd"] = (u.gdp_usd - u.gdp_agri_usd - u.gdp_industry_usd).clip(lower=0)

    # split combination areas into counties by population
    pieces = load_pieces(cc)
    cpop = pieces.groupby("adm2")["pop"].sum()
    rows = []
    for unit, r in u.iterrows():
        members = combos.get(unit, [unit])
        pw = cpop.reindex(members).fillna(0.0)
        sh = pw / pw.sum() if pw.sum() > 0 else pd.Series(1.0 / len(members), index=members)
        for c in members:
            rows.append({"admin_id": c, **(r * sh[c]).to_dict()})
    out = pd.DataFrame(rows)
    adm = load_admin(cc, "adm2").set_index("admin_id")
    out = out.set_index("admin_id").reindex(adm.index)
    gcols = ["gdp_usd", "gdp_agri_usd", "gdp_industry_usd", "gdp_services_usd",
             "gdp_manufacturing_usd", "gdp_finance_usd"]
    out[gcols] = out[gcols].round(0)
    out.insert(0, "name", adm.name)
    out["gdp_pc_usd"] = (out.gdp_usd / adm["pop"].replace(0, np.nan)).round(1)

    inc = util.acs_table("B19013", ACS_YEAR)
    pov = util.acs_table("B17001", ACS_YEAR)
    # ACS "jam" values (e.g. -666666666 = estimate not available) -> missing
    out["hh_income_usd"] = inc.B19013_E001.where(inc.B19013_E001 > 0).reindex(out.index)
    pv_ok = (pov.B17001_E001 > 0) & (pov.B17001_E002 >= 0)
    out["poverty_rate"] = (100 * pov.B17001_E002 / pov.B17001_E001).where(pv_ok).round(2).reindex(out.index)
    out["poor_persons"] = pov.B17001_E002.where(pv_ok).reindex(out.index)
    out = out.reset_index()
    util.write_admin(cc, DOMAIN, "adm2", out)

    wb_gdp, wb_year = util.wb_value("NY.GDP.MKTP.CD", "USA", US_YEAR)
    total = float(out.gdp_usd.sum())
    bea_states = float(S[1].sum()) * k
    report = {
        "year": US_YEAR, "acs": f"{ACS_YEAR - 4}-{ACS_YEAR}",
        "gdp_usd_total": total, "bea_sum_of_states_usd": bea_states,
        "worldbank_gdp_usd": wb_gdp, "worldbank_year": wb_year,
        "ratio_to_worldbank": total / wb_gdp if wb_gdp else None,
        "bea_fill": fill, "combination_areas": combos,
        "sector_totals_usd": {c: float(out[c].sum()) for c in gcols},
        "counties_missing_income": int(out.hh_income_usd.isna().sum()),
        "poor_persons_total": float(out.poor_persons.sum()),
    }
    print(f"  USA GDP {US_YEAR}: ${total / 1e12:.3f} tn (sum of states ${bea_states / 1e12:.3f} tn; "
          f"World Bank {wb_year}: ${(wb_gdp or 0) / 1e12:.3f} tn)")
    return report


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------

def build(country: str) -> None:
    print(f"[economy] building {country}")
    report = build_lka() if country == "lka" else build_usa()
    out = interim_dir(country, DOMAIN)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=float))
    write_domain_meta(out, METRICS, SOURCES, country)


if __name__ == "__main__":
    import sys

    from civmap import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    for cc in ccs:
        build(cc)
    sys.exit(validate.main([DOMAIN, *ccs]))
