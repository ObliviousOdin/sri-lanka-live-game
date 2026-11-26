"""Food domain (Food yield, Plantation / Farmland districts): agriculture & fisheries.

Outputs (data/interim/<country>/food/):
  admin_adm2.csv   ag_gva_usd, tree_crop_share  (both countries)
                   LKA: paddy_t, tea_ha, rubber_ha, coconut_ha, highland_ha, fish_catch_t, fish_inland_t
                   USA: ag_sales_usd
  admin_adm1.csv   USA: fish_catch_t, fish_value_usd (commercial marine landings by state)
  report.json      reconciliation checks against published national totals

Sri Lanka (2024)
  * National GVA of agriculture, forestry & fishing at current prices for the 17 DCS activities
    (DCS revised quarterly GDP 2024, Table 21, released 17 Mar 2026) -> districts with a
    production-value index: each activity's national GVA is spread over districts in proportion
    to a physical indicator of that activity (paddy production, highland-crop extent, tea / rubber /
    coconut / spice / fruit extents, livestock numbers, marine and inland fish production,
    agricultural land).  Provincial totals are then calibrated to the provincial agriculture shares
    published in the CBSL Provincial GDP 2024 release (as quoted by EconomyNext).
  * Crop tables from the DCS Statistical Abstract 2025 (Chapter 5) and the Economic Census 2013/14
    (Agricultural Activities); fisheries from the Ministry of Fisheries.

United States
  * BEA SAGDP2 state GDP of NAICS 11 (latest year, 2025): farms (line 4) -> counties by USDA NASS
    2022 Census of Agriculture market value of agricultural products sold; forestry, fishing &
    related activities (line 5) -> counties by ACS 2020-2024 employment in agriculture, forestry,
    fishing and hunting.
  * NOAA Fisheries FOSS commercial landings by state (2024).
"""
from __future__ import annotations

import json
import re
import unicodedata

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pdfplumber
from shapely.geometry import Polygon

from civmap import util
from civmap.config import US_STATE_FIPS, interim_dir
from civmap.core import SOURCES as CORE_SOURCES
from civmap.core import load_admin, load_hexes, load_pieces
from civmap.fetch import fetch, unzip
from civmap.schema import Metric, Source, write_domain_meta

DOMAIN = "food"
food = DOMAIN

LKA_YEAR = 2024          # national accounts, paddy (Maha 2023/24 + Yala 2024), marine fish, CBSL PGDP
LKA_INLAND_YEAR = 2023   # latest inland & aquaculture production by district (MoF)
LKA_CENSUS_YEAR = 2014   # Economic Census 2013/14 - Agricultural Activities (perennial crop extents)
US_GDP_YEAR = 2025       # BEA SAGDP2 (state GDP by industry)
US_COA_YEAR = 2022       # Census of Agriculture
US_FISH_YEAR = 2024      # NOAA FOSS commercial landings (same year as Sri Lanka marine production)
ACS_YEAR = 2024

ACRE_HA = 0.40468564224
LB_T = 0.45359237e-3

DCS = "https://www.statistics.gov.lk/"
NASS = ("https://www.nass.usda.gov/sites/default/files/publications/agcensus/2022/Full_Report/"
        "Volume_1,_Chapter_2_County_Level/")
URL = {
    "qgdp2024": DCS + "Resource/en/NationalAccounts/2015/Reports/Production/2026.03.17/QGDP_2024_Revi.pdf",
    "abs_5_2": DCS + "abstract2025/CHAP5/5.2.pdf",
    "abs_5_17": DCS + "abstract2025/CHAP5/5.17.pdf",
    "abs_5_19": DCS + "abstract2025/CHAP5/5.19.pdf",
    "abs_5_20": DCS + "abstract2025/CHAP5/5.20.pdf",
    "abs_5_33": DCS + "abstract2025/CHAP5/5.33.pdf",
    "abs_5_34": DCS + "abstract2025/CHAP5/5.34.pdf",
    "abs_10_2": DCS + "abstract2025/CHAP10/10.2.pdf",
    "ec2014": DCS + "Economic/Final_Report_Agri.pdf",
    "mof2024": "https://www.fisheries.gov.lk/media/annual_statistics/files/2026/Fisheries_Statistics_-2024_compressed.pdf",
    "pgdp_press": "https://economynext.com/sri-lankas-regional-economic-disparities-narrow-central-bank-254593/",
    "sagdp": "https://apps.bea.gov/regional/zip/SAGDP.zip",
    "foss": "https://apps-st.fisheries.noaa.gov/ods/foss/landings/",
    "cb2021": "https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_county_500k.zip",
}

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

_core = {s.id: s for s in CORE_SOURCES}

SOURCES: list[Source] = [
    Source("dcs_qgdp2024_rev",
           "Revised GDP estimates for the four quarters (Q1 to Q4) of 2024 - Table 21: Value added by "
           "quarter at current price, 2024 (revised estimates as at 17 March 2026)",
           "Department of Census and Statistics, Sri Lanka (National Accounts Division)", URL["qgdp2024"],
           "open (DCS dissemination policy)", "2024", ["lka"],
           "Rs. million, 2015 base. Gross value added of the 17 agriculture, forestry & fishing activities "
           "(SLSIC/ISIC rev.4 based: cereals except rice, rice, vegetables, sugar cane/tobacco/other "
           "non-perennials, fruits, oleaginous fruits (coconut), tea, coffee/cocoa, spices, rubber, other "
           "perennials, animal production, plant propagation, support activities, forestry & logging, marine "
           "fishing & aquaculture, fresh-water fishing & aquaculture). The four quarters are summed to the "
           "calendar year; the activity rows are checked against the published sector total."),
    Source("dcs_abstract2025_agri",
           "Statistical Abstract 2025, Chapter 5 (Agriculture): Tables 5.2, 5.17, 5.19, 5.20, 5.33, 5.34",
           "Department of Census and Statistics, Sri Lanka", "https://www.statistics.gov.lk/abstract2025/chapter5",
           "open (DCS dissemination policy)", "2014 (5.2); Maha 2023/24 + Yala 2024 (5.17, 5.20); 2024 (5.19, 5.33, 5.34)",
           ["lka"],
           "5.2 Cultivated extent of tea, rubber and coconut by district, census years 2002 and 2014 (ha; "
           "Census of Agriculture 2002 / Economic Census 2013/14); 5.17 Extent of highland crops by district "
           "and season (ha); 5.19 Production of main crops (national; tea, rubber, coconut, paddy); 5.20 Paddy "
           "production by district and season ('000 MT); 5.33 Fish production by sub-sector (national); "
           "5.34 Marine fish production by fisheries district 2018-2024 (Ministry of Fisheries data). "
           "'..' (not available / negligible) is read as zero."),
    Source("dcs_ec2014_agri",
           "Economic Census 2013/14 - General Report on Agricultural Activities",
           "Department of Census and Statistics, Sri Lanka", URL["ec2014"], "open (DCS dissemination policy)",
           "2013/14", ["lka"],
           "Annexure tables by district: Table 2 agricultural land under small holdings and estates (acres); "
           "Table 22 extent under other export agricultural crops (cocoa, coffee, cinnamon, pepper, cardamom, "
           "cloves, nutmeg, betel, arecanut; systematic cultivation, acres); Table 23 extent under fruit crops "
           "(acres); Table 28 cattle and buffalo population; Table 30 poultry population. Latest agricultural "
           "census with district detail for perennial crops and livestock."),
    Source("mof_fisheries_stats_2024",
           "Fisheries Statistics 2024 (provisional; data year 2023) - Tables 4.3 and 4.9",
           "Ministry of Fisheries, Aquatic and Ocean Resources, Sri Lanka (Statistics Unit) / DFAR / NAQDA",
           URL["mof2024"], "open (government publication)", "2023", ["lka"],
           "Table 4.3: national fish production by sub-sector (marine; inland & aquaculture) and "
           "administrative district, 2021-2023 (Mt). Used for inland & aquaculture production by district and "
           "to verify the fisheries-district -> administrative-district mapping of marine production."),
    Source("cbsl_pgdp2024_press",
           "Provincial Gross Domestic Product 2024 (Central Bank of Sri Lanka statement, 24 Dec 2025), "
           "as reported by EconomyNext: \"Sri Lanka's regional economic disparities narrow: central bank\"",
           "Central Bank of Sri Lanka (Statistics Department); reported by EconomyNext (Echelon Media)",
           URL["pgdp_press"], "news report quoting an official statistical release", "2024", ["lka"],
           "Agriculture shares of the national total 2024 quoted from the CBSL release: North Western 20.0 %, "
           "Central 13.9 %, Southern 11.8 %; also Western's share of total GDP (42.4 %), of industry (47.6 %) "
           "and of services (44.5 %), from which Western's agriculture share is derived. cbsl.gov.lk itself "
           "is not reachable from the build environment; phrases are verified in the cached article."),
    Source("dcs_abstract2025",
           "Statistical Abstract 2025, Table 10.2: Gross Domestic Product by major economic activity at "
           "current market prices 2019-2024",
           "Department of Census and Statistics, Sri Lanka", URL["abs_10_2"], "open (DCS dissemination policy)",
           "2024 (provisional)", ["lka"],
           "2024 column (Rs. million): Agriculture 2,481,487; industry 5,988,981 + 1,643,766; services "
           "17,182,560; GVA 27,296,792. Used only to derive Western Province's agriculture share from the "
           "published CBSL shares (same vintage as the CBSL release)."),
    Source("bea_sagdp2",
           "SAGDP2: Gross domestic product (GDP) by state, by industry (current dollars)",
           "U.S. Bureau of Economic Analysis", URL["sagdp"], "public domain", str(US_GDP_YEAR), ["usa"],
           f"SAGDP2__ALL_AREAS_1997_{US_GDP_YEAR}.csv, millions of current dollars, column {US_GDP_YEAR} "
           "(BEA release of Sept 2026). Line 3 agriculture, forestry, fishing and hunting = line 4 farms + "
           "line 5 forestry, fishing, and related activities."),
    Source("usda_coa2022",
           "2022 Census of Agriculture, Volume 1, Chapter 2: County Level Data (state text files <st>v1.txt)",
           "USDA National Agricultural Statistics Service",
           "https://www.nass.usda.gov/Publications/AgCensus/2022/Full_Report/Volume_1,_Chapter_2_County_Level/",
           "public domain", "2022", ["usa"],
           "Table 1 County Summary Highlights (farms; total cropland acres; market value of agricultural "
           "products sold, $1,000) and Table 30 Land in Orchards (acres). (D) = withheld to avoid disclosing "
           "individual operations (filled as described in the metric methods). Published for 3,078 county "
           "units: Alaska is tabulated for 5 agricultural census districts, Connecticut for its 8 former "
           "counties, Virginia's independent cities other than Chesapeake, Suffolk and Virginia Beach are not "
           "tabulated separately. The Quick Stats bulk file and API were not usable (file gateway / API key)."),
    Source("census_acs5_2024", "American Community Survey 2020-2024 5-year estimates (table-based Summary File)",
           "U.S. Census Bureau", "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/",
           "public domain", "2020-2024", ["usa"],
           "Table C24030 (sex by industry for the civilian employed population 16+): lines 4 + 31, "
           "agriculture, forestry, fishing and hunting; used as the county share of BEA line 5."),
    Source("census_cb_2021",
           "Cartographic Boundary Files 2021 - counties 1:500,000 (last vintage with the 8 former "
           "Connecticut counties)", "U.S. Census Bureau", URL["cb2021"], "public domain", "2021", ["usa"],
           "Used only to split Census of Agriculture values for Connecticut's former counties into the 9 "
           "planning regions."),
    Source("noaa_foss_landings",
           "Fisheries One Stop Shop (FOSS) - Commercial Fisheries Landings (ORDS API, collection = Commercial)",
           "NOAA Fisheries, Office of Science and Technology", "https://www.fisheries.noaa.gov/foss",
           "public domain", str(US_FISH_YEAR), ["usa"],
           f"apps-st.fisheries.noaa.gov/ods/foss/landings/?q={{\"year\":{US_FISH_YEAR},\"collection\":"
           "\"Commercial\"}: pounds and ex-vessel dollars by state and species (incl. 'withheld for "
           "confidentiality' aggregates). 'Process at sea' landings are not attributed to a state. Great "
           "Lakes landings (source GLAKES) end in 2018 in FOSS and are not included."),
    Source("terrain_worldcover",
           "Cropland per hex from the terrain domain (ESA WorldCover land-cover fractions)",
           "ESA WorldCover / Hex Atlas terrain domain", "https://esa-worldcover.org", "CC BY 4.0", "2021",
           ["usa"], "Used only as the weight that splits combined / older Census of Agriculture units "
           "(Alaska districts, Connecticut former counties) into current counties."),
    Source("worldbank_wdi", "World Development Indicators", "World Bank",
           "https://data.worldbank.org", "CC BY 4.0", "2021-2025", ["lka", "usa"],
           "PA.NUS.FCRF (LKR per USD, period average) for currency conversion; NV.AGR.TOTL.CD, ER.FSH.CAPT.MT "
           "and ER.FSH.AQUA.MT only to check national totals."),
    _core["census_pep_2023"],
]

# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

_LKA_COMP_DOC = (
    "Activity -> district indicator: rice -> paddy production Maha 2023/24 + Yala 2024 (Abstract 5.20); "
    "cereals except rice -> highland cereal extent (kurakkan, maize, sorghum, meneri), Maha 2023/24 + Yala "
    "2024 (5.17); vegetables and sugar cane/tobacco/other non-perennials -> total highland-crop extent of "
    "the same seasons (5.17); tea, rubber, coconut -> 2014 census extents (5.2); fruits -> 2014 extent of "
    "systematic fruit cultivation (EC 2013/14 Table 23); coffee/cocoa -> 2014 cocoa + coffee extent (Table "
    "22); spices -> 2014 cinnamon, pepper, cardamom, cloves, nutmeg, betel and arecanut extent (Table 22) "
    "+ 2023/24 ginger and turmeric extent (5.17); animal production -> mean of the district shares of "
    "cattle + buffalo and of poultry (2014, Tables 28 and 30; equal weights are an assumption); other "
    "perennials, plant propagation, support activities and forestry & logging -> total agricultural land "
    "2014 (Table 2); marine fishing -> marine fish production 2024 (5.34, fisheries districts mapped to "
    "administrative districts: Negombo -> Gampaha, Chilaw + Puttalam -> Puttalam, Tangalle -> Hambantota, "
    "Kalmunai -> Ampara; mapping verified against MoF Table 4.3 for 2023); fresh-water fishing -> inland & "
    "aquaculture production 2023 (MoF Table 4.3)."
)

_LKA_GVA_METHOD = (
    f"Sri Lanka {LKA_YEAR}: national GVA of the 17 agriculture, forestry & fishing activities at current "
    "prices (DCS revised QGDP 2024, Table 21, four quarters summed; Rs 2,520,119 mn, vs Rs 2,481,487 mn in "
    "the earlier Statistical Abstract 2025 vintage) is spread over the 25 districts activity by activity, "
    "in proportion to a physical indicator of each activity. " + _LKA_COMP_DOC + " Provincial calibration: "
    "the district index is rescaled so that the province totals equal the agriculture shares of the CBSL "
    "Provincial GDP 2024 release: North Western 20.0 %, Central 13.9 %, Southern 11.8 % (quoted), Western "
    "derived as (42.4 % x GVA - 47.6 % x industry - 44.5 % x services) / agriculture with the DCS Abstract "
    "Table 10.2 sector values (i.e. assuming net product taxes are distributed like GVA); the remaining "
    "share is split among the other five provinces in proportion to their index totals. Within a province "
    "districts keep their index proportions. Converted at the World Bank PA.NUS.FCRF 2024 average rate. "
    "Hex allocation: cropland weight."
)

_US_GVA_METHOD = (
    f"USA {US_GDP_YEAR}: BEA SAGDP2 state GDP, current dollars. Line 4 (farms) is split to counties by "
    f"each county's share of the state's {US_COA_YEAR} Census of Agriculture market value of agricultural "
    "products sold (Table 1); line 5 (forestry, fishing, and related activities: support activities for "
    f"agriculture & forestry, forestry & logging, fishing, hunting & trapping) by the county share of ACS "
    f"{ACS_YEAR - 4}-{ACS_YEAR} 5-year employment in agriculture, forestry, fishing and hunting (C24030 "
    "lines 4 + 31, place of residence). District of Columbia: all to 11001. Counties sum exactly to the "
    "BEA state rows (50 states + DC). Sales-share caveats as for ag_sales_usd (withheld cells filled; "
    "Alaska and Connecticut split by cropland). Hex allocation: cropland weight."
)

_US_SALES_METHOD = (
    f"USDA NASS {US_COA_YEAR} Census of Agriculture, county Table 1, 'Market value of agricultural products "
    "sold' ($1,000 x 1,000; crops incl. nursery & greenhouse + livestock, poultry & products; incl. "
    "landlord's share is NOT included at county level). Withheld '(D)' county values: the state total minus "
    "the disclosed counties is shared among the withheld counties in proportion to their number of farms. "
    "Alaska is published only for 5 agricultural census districts whose borough composition is not given "
    "in the text files, so the Alaska state total is split to the 30 boroughs / census areas in proportion "
    "to ESA WorldCover cropland (terrain domain). Connecticut's 8 former counties are split to the 9 "
    "planning regions by the cropland inside each (former county x planning region) intersection (Census "
    "cb_2021 county polygons overlaid on cb_2023 planning regions). Virginia independent cities that the "
    "census does not tabulate separately (all except Chesapeake, Suffolk, Virginia Beach) and Kalawao "
    "County, HI get 0."
)

METRICS: list[Metric] = [
    Metric("ag_gva_usd", "Agriculture, forestry & fishing GVA (Food)", "USD", "food", "extensive", "cropland",
           "admin_adm2",
           ["dcs_qgdp2024_rev", "dcs_abstract2025_agri", "dcs_ec2014_agri", "mof_fisheries_stats_2024",
            "cbsl_pgdp2024_press", "dcs_abstract2025", "bea_sagdp2", "usda_coa2022", "census_acs5_2024",
            "census_cb_2021", "terrain_worldcover", "worldbank_wdi"],
           f"USA {US_GDP_YEAR}; Sri Lanka {LKA_YEAR}",
           "Gross value added of agriculture, forestry and fishing (ISIC A / NAICS 11), current US dollars.",
           _US_GVA_METHOD + " | " + _LKA_GVA_METHOD, "higher"),
    Metric("ag_sales_usd", "Farm sales (market value of agricultural products sold)", "USD", "food",
           "extensive", "cropland", "admin_adm2", ["usda_coa2022", "census_cb_2021", "terrain_worldcover"],
           str(US_COA_YEAR), "Gross market value of crops, livestock and poultry sold by farms (gross output, "
           "not value added).", _US_SALES_METHOD, "higher", ["usa"]),
    Metric("tree_crop_share", "Tree & plantation crop share of agricultural land", "%", "food", "intensive",
           "cropland", "admin_adm2", ["dcs_abstract2025_agri", "dcs_ec2014_agri", "usda_coa2022",
                                      "census_cb_2021", "terrain_worldcover"],
           f"USA {US_COA_YEAR}; Sri Lanka {LKA_CENSUS_YEAR}",
           "Share of agricultural land under perennial tree / plantation crops (drives Plantation districts).",
           f"USA: {US_COA_YEAR} Census of Agriculture land in orchards (Table 30: fruit and nut trees, citrus, "
           "vineyards, bearing + non-bearing acres) / total cropland (Table 1) x 100 - orchards are part of "
           "cropland, so the share is <= 100. Withheld '(D)' acres are filled per state from the state total "
           "minus disclosed counties, shared by the number of farms reporting; Alaska / Connecticut are split "
           "as for ag_sales_usd (both acreages with the same weights). | Sri Lanka: (tea + rubber + coconut "
           "extent) / total agricultural land of small holdings and estates x 100, Economic Census 2013/14 "
           "(DCS Abstract 2025 Table 5.2 for the crops in hectares, EC 2013/14 Table 2 for the land in acres "
           "converted at 0.404686 ha/acre). Definitional difference: the US denominator is cropland (excludes "
           "permanent pasture and woodland), the Sri Lankan one all agricultural land of holdings (incl. home "
           "gardens); the US numerator excludes berries, nurseries and Christmas trees.",
           "neutral"),
    Metric("paddy_t", "Paddy production", "t", "food", "extensive", "cropland", "admin_adm2",
           ["dcs_abstract2025_agri"], "Maha 2023/24 + Yala 2024",
           "Paddy (unmilled rice) production of the 2024 cultivation year.",
           "DCS Statistical Abstract 2025 Table 5.20 (Paddy Statistics, crop-cutting survey), production "
           "'000 MT of Maha 2023/24 + Yala 2024 x 1,000. Jaffna has no Yala cultivation (0). National total "
           "checked against Table 5.19 (4,698,454 t).", "higher", ["lka"]),
    Metric("tea_ha", "Tea extent", "ha", "food", "extensive", "tree", "admin_adm2",
           ["dcs_abstract2025_agri"], str(LKA_CENSUS_YEAR),
           "Cultivated extent of tea (small holdings + estates).",
           "DCS Statistical Abstract 2025 Table 5.2, 2014 column (Economic Census 2013/14, agricultural "
           "activities). District tea production is not published (Tea Board production is by elevation), so "
           "extent is used. '..' read as 0.", "neutral", ["lka"]),
    Metric("rubber_ha", "Rubber extent", "ha", "food", "extensive", "tree", "admin_adm2",
           ["dcs_abstract2025_agri"], str(LKA_CENSUS_YEAR), "Cultivated extent of rubber.",
           "DCS Statistical Abstract 2025 Table 5.2, 2014 column (Economic Census 2013/14). '..' read as 0.",
           "neutral", ["lka"]),
    Metric("coconut_ha", "Coconut extent", "ha", "food", "extensive", "tree", "admin_adm2",
           ["dcs_abstract2025_agri"], str(LKA_CENSUS_YEAR), "Cultivated extent of coconut.",
           "DCS Statistical Abstract 2025 Table 5.2, 2014 column (Economic Census 2013/14; systematic "
           "cultivation incl. estimated extent of scattered trees).", "neutral", ["lka"]),
    Metric("highland_ha", "Highland (other field) crop extent", "ha", "food", "extensive", "cropland",
           "admin_adm2", ["dcs_abstract2025_agri"], "Maha 2023/24 + Yala 2024",
           "Seasonal extent cultivated with highland crops (cereals other than rice, pulses, oilseeds, "
           "roots & tubers, onions, chillies, ginger, turmeric).",
           "DCS Statistical Abstract 2025 Table 5.17, sum of all crop columns for Maha 2023/24 and Yala 2024 "
           "(gross seasonal extent: land cropped in both seasons counts twice). '..' read as 0.",
           "higher", ["lka"]),
    Metric("fish_catch_t", "Marine fish production / commercial landings", "t", "food", "extensive", "coast",
           "admin_adm2", ["dcs_abstract2025_agri", "mof_fisheries_stats_2024", "noaa_foss_landings"],
           f"Sri Lanka {LKA_YEAR}; USA {US_FISH_YEAR}",
           "Marine fish catch landed, metric tonnes (live weight).",
           f"Sri Lanka: DCS Statistical Abstract 2025 Table 5.34, marine fish production {LKA_YEAR} by the 15 "
           "fisheries districts (coastal + offshore/multi-day; Ministry of Fisheries estimates), mapped to "
           "administrative districts (Negombo -> Gampaha, Chilaw + Puttalam -> Puttalam, Tangalle -> "
           "Hambantota, Kalmunai -> Ampara; others same name; the mapping reproduces MoF Table 4.3's 2023 "
           "administrative-district figures exactly). Inland districts 0. | USA: state level, see the adm1 "
           "metric. Hex allocation: coast weight.", "higher", ["lka"]),
    Metric("fish_inland_t", "Inland fisheries & aquaculture production", "t", "food", "extensive", "land",
           "admin_adm2", ["mof_fisheries_stats_2024"], str(LKA_INLAND_YEAR),
           "Fish and shrimp from reservoirs, lagoons-based aquaculture and farms, metric tonnes.",
           f"Ministry of Fisheries 'Fisheries Statistics 2024' Table 4.3, inland & aquaculture production "
           f"{LKA_INLAND_YEAR} by administrative district (latest year with district detail; national "
           "2024 total 102,330 t per Abstract 5.33). No comparable US series (FOSS has no aquaculture and "
           "Great Lakes data end in 2018).", "higher", ["lka"]),
    Metric("fish_catch_t", "Marine fish production / commercial landings", "t", "food", "extensive", "coast",
           "admin_adm1", ["noaa_foss_landings"], str(US_FISH_YEAR),
           "Commercial marine landings, metric tonnes (live weight).",
           f"NOAA FOSS commercial landings {US_FISH_YEAR}: pounds summed over all species (incl. confidential "
           "aggregates) by state of landing x 0.45359237 / 1,000; Florida East + West coasts combined. "
           "'Process at sea' landings (at-sea processors, mostly Alaska pollock) are not attributed to any "
           "state and are excluded; Great Lakes commercial landings are not in FOSS after 2018 and are "
           "excluded (consistent with Sri Lanka's marine-only definition); states without marine landings "
           "= 0. Hex allocation: coast weight.", "higher", ["usa"]),
    Metric("fish_value_usd", "Commercial landings ex-vessel value", "USD", "food", "extensive", "coast",
           "admin_adm1", ["noaa_foss_landings"], str(US_FISH_YEAR),
           "Ex-vessel value of commercial marine landings, current dollars.",
           f"NOAA FOSS commercial landings {US_FISH_YEAR}: dollars summed by state (rows without a value "
           "contribute 0); same coverage rules as fish_catch_t.", "higher", ["usa"]),
]

# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

_LKA_ALIASES = {
    "moneragala": "monaragala", "rathnapura": "ratnapura", "vavunia": "vavuniya", "mullaittivu": "mullaitivu",
    "mullaithivu": "mullaitivu", "kaluthara": "kalutara", "anuradapura": "anuradhapura", "puttalama": "puttalam",
    "puttlam": "puttalam", "kegalla": "kegalle", "digamadulla": "ampara", "killinochchi": "kilinochchi",
    "kilinochi": "kilinochchi", "kurunagala": "kurunegala", "nuwaraeliya": "nuwaraeliya",
    "srilanka": "total", "total": "total",
}
_NUM = r"(?:-?[\d,]*\.?\d+|\.\.|-)"


def _key(name: str) -> str:
    k = re.sub(r"[^a-z]", "", str(name).lower())
    return _LKA_ALIASES.get(k, k)


def _num(s: str) -> float:
    s = s.strip()
    if s in ("..", "-", "na", ""):
        return 0.0
    return float(s.replace(",", ""))


def _ascii(t: str) -> str:
    return "\n".join(re.sub(r"[^\x00-\x7f]+", " ", l) for l in t.splitlines())


def _pdf_pages(path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [_ascii(pg.extract_text() or "") for pg in pdf.pages]


def _district_rows(text: str, n: int, extra: tuple[str, ...] = (), keep_all: bool = False) -> dict[str, list[float]]:
    """{district key: first n numbers (all if keep_all)} for lines 'Name [(1)|*] v1 v2 ...'."""
    rx = re.compile(r"([A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*)*?)\s*(?:\(\d\))?\s*\*?\s+"
                    rf"({_NUM}(?:\s+{_NUM})*)\s*$")
    names = set(_lka_index()) | {"total", *extra}
    out: dict[str, list[float]] = {}
    for line in text.splitlines():
        line = line.replace("*", " ")     # revision markers ('22,205*')
        m = rx.search(line)
        if not m:
            continue
        words = m.group(1).split()   # drop stray leading fragments ('p  Kilinochchi')
        k = next((kk for kk in (_key(" ".join(words[i:])) for i in range(len(words))) if kk in names),
                 _key(m.group(1)))
        toks = re.findall(_NUM, m.group(2))
        if k in names and len(toks) >= n and k not in out:
            out[k] = [_num(t) for t in (toks if keep_all else toks[:n])]
    return out


_LKA_IDX: dict[str, str] = {}


def _lka_index() -> dict[str, str]:
    if not _LKA_IDX:
        a = load_admin("lka", "adm2")
        _LKA_IDX.update({_key(n): i for i, n in zip(a.admin_id, a.name)})
    return _LKA_IDX


def _to_adm2(rows: dict[str, list[float]], cols: list[str], what: str,
             absent_zero: tuple[str, ...] = ()) -> pd.DataFrame:
    """District table from parsed rows; districts listed in `absent_zero` may be missing (= 0)."""
    idx = _lka_index()
    miss = sorted(k for k in idx if k not in rows and k not in absent_zero)
    if miss:
        raise ValueError(f"{what}: no row for {miss}")
    zero = [0.0] * len(cols)
    d = pd.DataFrame({idx[k]: rows.get(k, zero) for k in idx}, index=cols).T
    d.index.name = "admin_id"
    return d


def _check_total(d: pd.Series, total: float, what: str, tol: float = 0.002) -> None:
    s = float(d.sum())
    if total and abs(s - total) > tol * abs(total) + 1.5:
        raise ValueError(f"{what}: districts sum to {s:,.1f}, published total {total:,.1f}")


# ---------------------------------------------------------------------------------------------
# Sri Lanka - national GVA by agricultural activity
# ---------------------------------------------------------------------------------------------

# (component id, regex on the Table 21 row label)
LKA_ACTIVITIES = [
    ("cereals", r"Cereals \(except rice\)"), ("rice", r"Growing of Rice"), ("vegetables", r"Vegetables"),
    ("nonperennial", r"Suger cane|Sugar cane"), ("fruits", r"Growing of fruits"),
    ("coconut", r"Oleaginous"), ("tea", r"Growing of Tea"), ("beverage", r"Coffee, cocoa"),
    ("spices", r"spices, aromatic"), ("rubber", r"Growing of rubber"), ("perennial", r"other perennial"),
    ("animal", r"Animal Production"), ("propagation", r"Plant propagation"),
    ("support", r"supporting activities"), ("forestry", r"Forestry and Logging"),
    ("marine", r"Marine fishing"), ("freshwater", r"Fresh water fishing"),
]


def _lka_gva_components() -> tuple[dict[str, float], float]:
    """Calendar-year 2024 GVA (Rs mn, current prices) by activity, and the published sector total."""
    p = fetch(URL["qgdp2024"], "dcs_qgdp2024_rev", "lka/food", "QGDP_2024_Revi.pdf")
    with pdfplumber.open(p) as pdf:
        page = next(pg for pg in pdf.pages
                    if (pg.extract_text() or "").lstrip().startswith("Table 21") and "2024" in (pg.extract_text() or "")[:80])
        words = page.extract_words(x_tolerance=1.5)
    lines: dict[int, list[dict]] = {}
    for w in words:
        lines.setdefault(int(round(w["top"] / 3.0)), []).append(w)
    rows = []
    for _, ws in sorted(lines.items()):
        ws = sorted(ws, key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in ws if re.search(r"[A-Za-z]", w["text"]))
        nums: list[list] = []
        for w in ws:
            if not re.fullmatch(r"[\d,]+", w["text"]) or w["x0"] < 300:
                continue
            if nums and w["x0"] - nums[-1][1] < 1.6:   # digits split by the PDF's character spacing
                nums[-1][0] += w["text"]
                nums[-1][1] = w["x1"]
            else:
                nums.append([w["text"], w["x1"]])
        if label and len(nums) == 4:
            rows.append((label, [float(n.replace(",", "")) for n, _ in nums]))
        if label.startswith("Industries"):
            break
    total = next(sum(v) for l, v in rows if l.startswith("Agriculture, Forestry and Fishing"))
    comp = {}
    for cid, rx in LKA_ACTIVITIES:
        hit = [v for l, v in rows if re.search(rx, l)]
        if len(hit) != 1:
            raise ValueError(f"QGDP Table 21: activity {cid!r} matched {len(hit)} rows")
        comp[cid] = float(sum(hit[0]))
    if abs(sum(comp.values()) - total) > 4:
        raise ValueError(f"QGDP Table 21: activities sum {sum(comp.values()):,.0f} != total {total:,.0f}")
    return comp, float(total)


# ---------------------------------------------------------------------------------------------
# Sri Lanka - district indicators
# ---------------------------------------------------------------------------------------------

def _abs(name: str) -> list[str]:
    p = fetch(URL[f"abs_{name.replace('.', '_')}"], "dcs_abstract2025_agri", "lka/food", f"abstract2025_{name}.pdf")
    return _pdf_pages(p)


def _lka_paddy() -> pd.DataFrame:
    pages = _abs("5.20")
    t = next(pg for pg in pages if "Maha 2023/2024" in pg and "Yala 2024" in pg)
    rows = _district_rows(t, 4)
    d = _to_adm2(rows, ["maha_kt", "maha_yield", "yala_kt", "yala_yield"], "Abstract 5.20")
    d["paddy_t"] = (d.maha_kt + d.yala_kt) * 1000.0
    _check_total(d.maha_kt, rows["total"][0], "paddy Maha 2023/24", 0.003)
    _check_total(d.yala_kt, rows["total"][2], "paddy Yala 2024", 0.003)
    # national production in tonnes (Table 5.19, paddy 2024 cultivation year)
    t19 = "\n".join(_abs("5.19"))
    m = re.search(r"Paddy\s*\n\s*Culti\. year Total(?:\s+[\d,]+){8}\s+([\d,]+)", t19)
    nat = _num(m.group(1)) if m else float("nan")
    return d[["paddy_t"]], {"paddy_t_districts": float(d.paddy_t.sum()), "paddy_t_table_5_19": nat}


HIGHLAND_CEREALS = ["Kurakkan", "Maize", "Sorghum", "Meneri"]


def _lka_highland() -> pd.DataFrame:
    """Highland-crop extents (ha), Maha 2023/24 + Yala 2024, from Abstract 5.17."""
    pages = _abs("5.17")
    out = pd.DataFrame(0.0, index=sorted(_lka_index().values()), columns=["highland_ha", "cereal_ha", "ginger_turmeric_ha"])
    out.index.name = "admin_id"
    used = 0
    for i, pg in enumerate(pages):
        season = ("maha" if re.search(r"^\s*Maha 2023/2024\s*$", pg, re.M)
                  else "yala" if re.search(r"^\s*Yala 2024\s*$", pg, re.M) else None)
        if season is None:
            continue
        cereals_page = "Kurakkan" in pg
        if cereals_page:
            cols = ["Kurakkan", "Maize", "Sorghum", "Meneri", "Green Gram", "Cowpea", "Gingelly", "Ground Nuts"]
        elif season == "maha":
            cols = ["Manioc", "Sweet Potatoes", "Potatoes", "Red Onion", "Big Onion", "Chillies", "Mustard",
                    "Ginger", "Turmeric"]
        else:
            cols = ["Manioc", "Sweet Potatoes", "Potatoes", "Red Onion", "Big Onion", "Chillies", "Mustard"]
        rows = _district_rows(pg, len(cols))
        d = _to_adm2(rows, cols, f"Abstract 5.17 p{i + 1}")
        for c in cols:
            _check_total(d[c], rows["total"][cols.index(c)], f"5.17 {season} {c}", 0.01)
        out["highland_ha"] += d.sum(axis=1)
        if cereals_page:
            out["cereal_ha"] += d[HIGHLAND_CEREALS].sum(axis=1)
        for c in ("Ginger", "Turmeric"):
            if c in d:
                out["ginger_turmeric_ha"] += d[c]
        used += 1
    if used != 4:
        raise ValueError(f"Abstract 5.17: expected 4 pages for Maha 2023/24 + Yala 2024, found {used}")
    return out


def _lka_tree_crops() -> pd.DataFrame:
    """Tea, rubber, coconut extent (ha) for 2014.

    Tea and coconut: 2014 column of Abstract 5.2.  Rubber: EC 2013/14 Table 6.3.1 (acres -> ha), because
    Abstract 5.2 prints '..' for Kurunegala (8,015 acres in the census report) although its national
    total includes it."""
    t = _abs("5.2")[0]
    cols = ["tea02", "tea_ha", "rub02", "rubber_ha", "coc02", "coconut_ha"]
    rows = _district_rows(t, 6)
    d = _to_adm2(rows, cols, "Abstract 5.2")
    for c in ("tea_ha", "coconut_ha"):
        _check_total(d[c], rows["total"][cols.index(c)], f"5.2 {c}", 0.001)
    pages = _ec_pages()
    pg = next(x for x in pages if "Table 6.3.1: Number of Holdings and Extent under Rubber Cultivation" in x)
    r = _district_rows(pg, 4)
    rub = _to_adm2(r, ["holdings", "h_pct", "extent_ac", "e_pct"], "EC Table 6.3.1",
                   absent_zero=("jaffna", "mannar", "mullaitivu", "kilinochchi", "batticaloa", "trincomalee"))
    _check_total(rub.extent_ac, r["total"][2], "EC Table 6.3.1 rubber")
    d["rubber_ha"] = rub.extent_ac * ACRE_HA
    _check_total(d.rubber_ha, rows["total"][cols.index("rubber_ha")], "rubber (EC) vs Abstract 5.2 total", 0.002)
    return d[["tea_ha", "rubber_ha", "coconut_ha"]]


_EC_PAGES: list[str] = []


def _ec_pages() -> list[str]:
    if not _EC_PAGES:
        p = fetch(URL["ec2014"], "dcs_ec2014_agri", "lka/food", "EC2013_14_Final_Report_Agri.pdf")
        _EC_PAGES.extend(_pdf_pages(p))
    return _EC_PAGES


def _lka_ec2014() -> pd.DataFrame:
    """Economic Census 2013/14 (agricultural activities) district annex tables."""
    pages = _ec_pages()

    def page(title: str, nth: int = 0) -> str:
        hits = [pg for pg in pages if pg.lstrip().startswith(title)]
        if len(hits) <= nth:
            raise ValueError(f"EC 2013/14: page starting {title!r} not found")
        return hits[nth]

    out = {}
    # Table 2: total agricultural land (acres): [holdings, extent, ...]
    r = _district_rows(page("Table 2: Agricultural Land under Small Holdings"), 2)
    d = _to_adm2(r, ["holdings", "extent_ac"], "EC Table 2")
    _check_total(d.extent_ac, r["total"][1], "EC Table 2 agricultural land")
    out["agri_land_ha"] = d.extent_ac * ACRE_HA
    # Table 22 (two pages): export agricultural crops, 3 columns (total, small, estate) per crop
    r1 = _district_rows(page("Table 22: Extent under Other Export Agricultural Crops", 0), 12)
    c1 = _to_adm2(r1, [f"{c}_{k}" for c in ("cocoa", "coffee", "cinnamon", "pepper") for k in "tse"], "EC Table 22a")
    r2 = _district_rows(page("Table 22: Extent under Other Export Agricultural Crops", 1), 15)
    c2 = _to_adm2(r2, [f"{c}_{k}" for c in ("cardamom", "cloves", "nutmeg", "betel", "arecanut") for k in "tse"],
                  "EC Table 22b")
    for c, rr in (("cinnamon_t", r1), ("pepper_t", r1)):
        _check_total(c1[c], rr["total"][list(c1.columns).index(c)], f"EC Table 22 {c}", 0.01)
    out["beverage_ha"] = (c1.cocoa_t + c1.coffee_t) * ACRE_HA
    out["spice_ha"] = (c1.cinnamon_t + c1.pepper_t + c2.cardamom_t + c2.cloves_t + c2.nutmeg_t
                       + c2.betel_t + c2.arecanut_t) * ACRE_HA
    # Table 23 (fruit crops, possibly several pages, 3 columns per crop): sum the 'Total' columns
    fruit = pd.Series(0.0, index=d.index)
    for pg in [pg for pg in pages if pg.lstrip().startswith("Table 23: Extent under Fruit Crops")]:
        rr = _district_rows(pg, 3, keep_all=True)
        ncol = len(rr["total"])
        if ncol % 3 or any(len(v) != ncol for v in rr.values()):
            raise ValueError("EC Table 23: ragged fruit table")
        f = _to_adm2(rr, [f"c{i}" for i in range(ncol)], "EC Table 23")
        for j in range(0, ncol, 3):
            _check_total(f[f"c{j}"], rr["total"][j], f"EC Table 23 col {j}", 0.002)
        fruit += f[[f"c{i}" for i in range(0, ncol, 3)]].sum(axis=1)
    out["fruit_ha"] = fruit * ACRE_HA
    # Table 28 cattle & buffaloes (total columns 0 and 4); Table 30 poultry (column 0)
    r = _district_rows(page("Table 28: Cattle and Buffalo Population"), 8)
    lv = _to_adm2(r, ["cattle", "c1", "c2", "c3", "buffalo", "b1", "b2", "b3"], "EC Table 28")
    _check_total(lv.cattle, r["total"][0], "EC cattle")
    out["cattle_buffalo"] = lv.cattle + lv.buffalo
    r = _district_rows(page("Table 30: Poultry Population"), 4)
    po = _to_adm2(r, ["poultry", "p1", "p2", "p3"], "EC Table 30")
    _check_total(po.poultry, r["total"][0], "EC poultry")
    out["poultry"] = po.poultry
    return pd.DataFrame(out)


# fisheries district -> administrative district (name key)
FISH_DISTRICTS = {
    "puttalam": "puttalam", "chilaw": "puttalam", "negombo": "gampaha", "colombo": "colombo",
    "kalutara": "kalutara", "galle": "galle", "matara": "matara", "tangalle": "hambantota",
    "kalmunai": "ampara", "batticaloa": "batticaloa", "trincomalee": "trincomalee",
    "mullaitivu": "mullaitivu", "jaffna": "jaffna", "kilinochchi": "kilinochchi", "mannar": "mannar",
}


def _lka_fish() -> tuple[pd.DataFrame, dict]:
    # marine production 2024 by fisheries district (Abstract 5.34)
    t = _abs("5.34")[0]
    rows = _district_rows(t, 7, extra=tuple(FISH_DISTRICTS))
    years = list(range(2018, 2025))
    missing = sorted(set(FISH_DISTRICTS) - set(rows))
    if missing:
        raise ValueError(f"Abstract 5.34: fisheries districts not found: {missing}")
    marine = pd.Series(0.0, index=sorted(_lka_index().values()))
    m23 = pd.Series(0.0, index=marine.index)
    for fd, dk in FISH_DISTRICTS.items():
        pc = _lka_index()[dk]
        marine[pc] += rows[fd][years.index(LKA_YEAR)]
        m23[pc] += rows[fd][years.index(2023)]
    t33 = _abs("5.33")[0]
    m = re.search(rf"^\s*{LKA_YEAR}\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", t33, re.M)
    nat_total, coastal, offshore, inland_nat = (_num(m.group(i)) for i in range(1, 5))
    _check_total(marine, coastal + offshore, "marine fish 2024 (5.34 vs 5.33)", 0.001)
    # inland & aquaculture 2023 by administrative district (MoF Fisheries Statistics Table 4.3)
    p = fetch(URL["mof2024"], "mof_fisheries_stats_2024", "lka/food", "Fisheries_Statistics_2024.pdf")
    with pdfplumber.open(p) as pdf:
        page = next(pg for pg in pdf.pages
                    if (pg.extract_text() or "").lstrip().startswith("Table 4.3: National Fish Production"))
        rows = _word_rows(page, x_min=180)
    # columns: 2021 (marine, inland & aquaculture, total), 2022 (...), 2023 (...): 9 right edges taken
    # from the fully populated rows; inland districts leave the 2023 marine cell blank
    full = [toks for _, toks in rows if len(toks) == 9]
    edges = np.median(np.array([[x for _, x in toks] for toks in full]), axis=0)
    idx = _lka_index()
    tab = {}
    for label, toks in rows:
        k = _key(re.sub(r"^\d+\s*", "", label))
        if k not in idx and k != "total":
            continue
        vals = [np.nan] * 9
        for txt, x in toks:
            j = int(np.argmin(np.abs(edges - x)))
            if abs(edges[j] - x) < 12:
                vals[j] = _num(txt)
        tab[k] = vals
    miss = sorted(set(idx) - set(tab))
    if miss:
        raise ValueError(f"MoF Table 4.3: no row for {miss}")
    t43 = pd.DataFrame({idx[k]: tab[k] for k in idx}, index=[f"{y}_{c}" for y in (2021, 2022, 2023)
                                                                for c in ("marine", "inland", "total")]).T
    t43 = t43.fillna(0.0)
    # column sums must match the published totals (a few published row totals differ from marine +
    # inland by up to 85 t - Gampaha, Hambantota, Puttalam 2023 - and are not used)
    for j, c in enumerate(t43.columns):
        if j % 3 != 2:
            _check_total(t43[c], tab["total"][j], f"MoF Table 4.3 {c}", 0.001)
    row_gap = (t43["2023_marine"] + t43["2023_inland"] - t43["2023_total"])
    inland_s = t43["2023_inland"]
    bad = (t43["2023_marine"] - m23.reindex(t43.index)).abs()
    if bad.max() > 1:
        raise ValueError("fisheries-district mapping does not reproduce MoF 2023 marine figures: "
                         f"{bad[bad > 1].to_dict()}")
    out = pd.DataFrame({"fish_catch_t": marine, "fish_inland_t": inland_s.reindex(marine.index)})
    out.index.name = "admin_id"
    chk = {"marine_2024_t": float(marine.sum()), "marine_2024_abstract_5_33_t": coastal + offshore,
           "inland_2023_t": float(inland_s.sum()), "inland_2024_national_t": inland_nat,
           "total_fish_2024_t": nat_total,
           "mof_4_3_2023_rows_where_marine_plus_inland_ne_total": {k: float(v) for k, v in row_gap.items() if abs(v) > 1}}
    return out, chk


def _word_rows(page, x_min: float = 0.0, gap: float = 1.0) -> list[tuple[str, list[tuple[str, float]]]]:
    """Rows of a PDF table as (label, [(number text, right x), ...]).

    Words closer than `gap` points are merged: these PDFs split numbers into pieces ('2' ',495')."""
    words = page.extract_words(x_tolerance=1.5)
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) < 2.0:
            lines[-1].append(w)
        else:
            lines.append([w])
    out = []
    for ws in lines:
        ws = sorted(ws, key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in ws if w["x0"] < x_min or re.search(r"[A-Za-z]", w["text"]))
        toks: list[list] = []
        for w in ws:
            if w["x0"] < x_min or not re.fullmatch(r"[\d,.\-]+", w["text"]):
                continue
            if toks and w["x0"] - toks[-1][1] < gap:
                toks[-1][0] += w["text"]
                toks[-1][1] = w["x1"]
            else:
                toks.append([w["text"], w["x1"]])
        out.append((label.strip(), [(t, x) for t, x in toks]))
    return out


# ---------------------------------------------------------------------------------------------
# Sri Lanka - provincial calibration (CBSL PGDP 2024 as quoted) and build
# ---------------------------------------------------------------------------------------------

# (sector, province name key, share %, phrase that must appear in the cached article)
PGDP_2024_QUOTED = [
    ("agri", "northwestern", 20.0, "contributor to agricultural activities, accounting for 20.0 per cent"),
    ("agri", "central", 13.9, "Central and Southern provinces were the next highest contributors with shares of "
                              "13.9 per cent and 11.8 per cent"),
    ("agri", "southern", 11.8, "Central and Southern provinces were the next highest contributors with shares of "
                               "13.9 per cent and 11.8 per cent"),
    ("total", "western", 42.4, "accounting for 42.4 percent"),
    ("industry", "western", 47.6, "accounting for 47.6 per cent of the total Industry value"),
    ("services", "western", 44.5, "The Western province led Services activities with 44.5 per cent"),
]
# DCS Statistical Abstract 2025 Table 10.2, 2024 (Rs mn) - same vintage as the CBSL release
DCS_2024_SECTORS = {"agri": [2_481_487], "industry": [5_988_981, 1_643_766],
                    "services": [8_270_177, 606_743, 2_218_227, 1_193_511, 580_609, 2_231_482, 2_081_811]}


def _html_text(path) -> str:
    import html as _h
    s = path.read_text(errors="ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", s)))


def _lka_province_targets() -> tuple[dict[str, float], dict]:
    """Agriculture share of each quoted province (fraction) incl. the derived Western share."""
    p = fetch(URL["pgdp_press"], "cbsl_pgdp2024_press", "lka/economy", "economynext_pgdp2024.html",
              headers={"User-Agent": "Mozilla/5.0"})
    text = _html_text(p).replace("’", "'")
    for _, _, _, phrase in PGDP_2024_QUOTED:
        if phrase not in text:
            raise ValueError(f"PGDP 2024 article: phrase not found: {phrase!r}")
    q = {(s, prov): v / 100.0 for s, prov, v, _ in PGDP_2024_QUOTED}
    a = fetch(URL["abs_10_2"], "dcs_abstract2025", "lka/economy", "abstract2025_10.2.pdf")
    t = "\n".join(_pdf_pages(a))
    for vals in DCS_2024_SECTORS.values():
        for v in vals:
            if f"{v:,}" not in t:
                raise ValueError(f"DCS Table 10.2: {v:,} not found")
    nat = {k: float(sum(v)) for k, v in DCS_2024_SECTORS.items()}
    gva = sum(nat.values())
    w = (q[("total", "western")] * gva - q[("industry", "western")] * nat["industry"]
         - q[("services", "western")] * nat["services"]) / nat["agri"]
    prov = {k: i for i, k in ((i, _key(n)) for i, n in zip(*[load_admin("lka", "adm1")[c] for c in ("admin_id", "name")]))}
    targets = {prov[pk]: v for (s, pk), v in q.items() if s == "agri"}
    info = {"western_agri_share_residual_pct": round(100 * w, 2),
            "quoted_agri_share_pct": {prov[pk]: 100 * v for (s, pk), v in q.items() if s == "agri"}}
    # The article ranks NW, Central, Southern as the top three agriculture provinces, so Western must be
    # below Southern's 11.8 %; the residual carries ~1 pp rounding error (shares are quoted to 0.1 pp).
    cap = q[("agri", "southern")] - 0.001     # i.e. at most 11.7 %
    targets[prov["western"]] = min(w, cap)
    info["western_agri_share_used_pct"] = round(100 * targets[prov["western"]], 2)
    return targets, info


def _lka_ag_index() -> tuple[pd.DataFrame, dict, dict]:
    """District production-value index (Rs mn) by activity."""
    comp, total = _lka_gva_components()
    paddy, pchk = _lka_paddy()
    hl = _lka_highland()
    tc = _lka_tree_crops()
    ec = _lka_ec2014()
    fish, fchk = _lka_fish()
    ind = pd.DataFrame(index=sorted(_lka_index().values()))
    share = lambda s: s.reindex(ind.index).fillna(0.0) / s.reindex(ind.index).fillna(0.0).sum()  # noqa: E731
    animal = 0.5 * share(ec.cattle_buffalo) + 0.5 * share(ec.poultry)
    indicator = {
        "rice": share(paddy.paddy_t), "cereals": share(hl.cereal_ha), "vegetables": share(hl.highland_ha),
        "nonperennial": share(hl.highland_ha), "tea": share(tc.tea_ha), "rubber": share(tc.rubber_ha),
        "coconut": share(tc.coconut_ha), "fruits": share(ec.fruit_ha), "beverage": share(ec.beverage_ha),
        "spices": share(ec.spice_ha + hl.ginger_turmeric_ha), "animal": animal,
        "perennial": share(ec.agri_land_ha), "propagation": share(ec.agri_land_ha),
        "support": share(ec.agri_land_ha), "forestry": share(ec.agri_land_ha),
        "marine": share(fish.fish_catch_t), "freshwater": share(fish.fish_inland_t),
    }
    assert set(indicator) == set(comp)
    for cid, s in indicator.items():
        ind[cid] = s * comp[cid]
    ind.index.name = "admin_id"
    tables = {"paddy": paddy, "highland": hl, "tree": tc, "ec": ec, "fish": fish}
    checks = {"gva_components_rs_mn": comp, "gva_total_rs_mn": total, **pchk, **fchk}
    return ind, tables, checks


def build_lka() -> dict:
    cc = "lka"
    ind, t, checks = _lka_ag_index()
    adm2 = load_admin(cc, "adm2").set_index("admin_id")
    prov_of = adm2.parent_id
    idx_d = ind.sum(axis=1)
    idx_p = idx_d.groupby(prov_of).sum()
    targets, tinfo = _lka_province_targets()
    rest = 1.0 - sum(targets.values())
    others = [p for p in idx_p.index if p not in targets]
    share_p = pd.Series({p: targets[p] if p in targets else rest * idx_p[p] / idx_p[others].sum()
                         for p in idx_p.index})
    total_rs = checks["gva_total_rs_mn"]
    gva_d = idx_d / prov_of.map(idx_p).reindex(idx_d.index) * prov_of.map(share_p).reindex(idx_d.index) * total_rs
    rate = util.lkr_per_usd(LKA_YEAR)
    out = pd.DataFrame(index=idx_d.index)
    out["ag_gva_usd"] = (gva_d * 1e6 / rate).round(0)
    out["paddy_t"] = t["paddy"].paddy_t.round(0)
    out["tea_ha"] = t["tree"].tea_ha.round(1)
    out["rubber_ha"] = t["tree"].rubber_ha.round(1)
    out["coconut_ha"] = t["tree"].coconut_ha.round(1)
    out["tree_crop_share"] = (100 * (t["tree"].tea_ha + t["tree"].rubber_ha + t["tree"].coconut_ha)
                              / t["ec"].agri_land_ha).round(2)
    out["highland_ha"] = t["highland"].highland_ha.round(1)
    out["fish_catch_t"] = t["fish"].fish_catch_t
    out["fish_inland_t"] = t["fish"].fish_inland_t
    out.index.name = "admin_id"
    util.write_admin(cc, DOMAIN, "adm2", out.reset_index())
    wb, wy = util.wb_value("NV.AGR.TOTL.CD", "LKA", LKA_YEAR)
    rep = {
        "year": LKA_YEAR, "lkr_per_usd": rate,
        "ag_gva_usd_total": float(out.ag_gva_usd.sum()),
        "worldbank_ag_va_usd": wb, "worldbank_year": wy,
        "ratio_to_worldbank": float(out.ag_gva_usd.sum() / wb) if wb else None,
        "province_index_share_pct": (100 * idx_p / idx_p.sum()).round(2).to_dict(),
        "province_share_used_pct": (100 * share_p).round(2).to_dict(),
        "province_targets": tinfo,
        "index_share_pct_by_activity": (100 * ind.sum() / ind.sum().sum()).round(2).to_dict(),
        "fao_capture_t_wb": util.wb_value("ER.FSH.CAPT.MT", "LKA", LKA_YEAR),
        "fao_aquaculture_t_wb": util.wb_value("ER.FSH.AQUA.MT", "LKA", LKA_YEAR),
        **{k: v for k, v in checks.items() if k != "gva_components_rs_mn"},
        "gva_components_rs_mn": checks["gva_components_rs_mn"],
    }
    return rep


# ---------------------------------------------------------------------------------------------
# United States - Census of Agriculture 2022 county tables (text volumes)
# ---------------------------------------------------------------------------------------------

def _us_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower().strip()
    s = re.sub(r"\bsaint\b", "st", s)
    for suf in (" city and borough", " census area", " planning region", " municipality", " county",
                " parish", " borough"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return re.sub(r"[^a-z]", "", s)


def _coa_value(tok: str) -> float:
    """Census of Agriculture cell -> number; (D) -> NaN (withheld), '-' / (Z) -> 0, (NA)/(X) -> NaN."""
    if tok in ("-", "(Z)"):
        return 0.0
    if tok.startswith("("):
        return np.nan
    return float(tok.replace(",", ""))


def _coa_text(state_fips: str, state_name: str, abbr: str) -> str:
    folder = state_name.replace(" ", "_")
    url = f"{NASS}{folder}/{abbr.lower()}v1.txt"
    p = fetch(url, "usda_coa2022", "usa/agcensus2022", f"{abbr.lower()}v1.txt", timeout=600)
    return p.read_text(encoding="latin-1")


def _coa_pages(text: str, title: str) -> list[str]:
    parts = re.split(r"\n(?=Table \d+\.\s)", text)
    return [p for p in parts if p.startswith(title)]


T1_ITEMS = {("Farms", "number"): "farms", ("Total cropland", "farms"): "crop_farms",
            ("Total cropland", "acres"): "cropland_ac",
            ("Market value of agricultural products sold", "$1,000"): "sales_k"}


def _coa_table1(text: str) -> tuple[pd.DataFrame, list[str]]:
    """County Summary Highlights -> DataFrame indexed by column name (first column = state)."""
    pages = _coa_pages(text, "Table 1.  County Summary Highlights")
    if not pages:
        raise ValueError("Census of Agriculture: Table 1 County Summary Highlights not found")
    cols: list[str] = []
    data: dict[str, list[float]] = {v: [] for v in T1_ITEMS.values()}
    for pg in pages:
        lines = pg.splitlines()
        hi = next(i for i, l in enumerate(lines) if re.match(r"\s+Item\s+:", l))
        names = [x.strip() for x in lines[hi].split(":")[1:]]
        names = [n for n in names if n]
        got: dict[str, list[float]] = {}
        item = ""
        for l in lines[hi + 1:]:
            if ":" not in l:
                continue
            lab, rest = l.split(":", 1)
            toks = rest.split()
            if not lab.strip():
                continue
            m = re.match(r"^\s*(.*?)\s*\.{2,}\s*([^.]*)$", lab)
            if m:
                item, unit = m.group(1).strip() or item, m.group(2).strip()
            else:
                if re.search(r"\.\.", lab) is None and lab.strip() and not re.match(r"^\s{20,}\S+$", lab):
                    item = lab.strip()       # section headers ('Livestock and poultry')
                    continue
                unit = lab.strip()
            key = T1_ITEMS.get((item, unit))
            if key and key not in got and len(toks) == len(names):
                got[key] = [_coa_value(t) for t in toks]
        missing = set(T1_ITEMS.values()) - set(got)
        if missing:
            raise ValueError(f"Census of Agriculture Table 1 page {names[:2]}: items not parsed {missing}")
        cols += names
        for k in data:
            data[k] += got[k]
    df = pd.DataFrame(data)
    df["name"] = cols
    return df, cols


def _coa_table30(text: str) -> tuple[dict, dict]:
    """Land in Orchards -> ({county name: (farms, acres)}, state (farms, acres)) for 2022."""
    pages = _coa_pages(text, "Table 30.  Land in Orchards")
    counties, state = {}, None
    for pg in pages:
        section = None
        for l in pg.splitlines():
            if l.startswith("State Total"):
                section = "state"
            elif l.startswith("Counties"):
                section = "county"
            m = re.match(r"^(\S.*?)\s*\.{2,}\s*:\s*(.*)$", l)
            if not m or section is None:
                continue
            toks = m.group(2).split()
            if len(toks) < 2:
                continue
            v = (_coa_value(toks[0]), _coa_value(toks[1]))
            if section == "state" and state is None:
                state = v
            elif section == "county":
                counties[m.group(1).strip()] = v
    if state is None:
        raise ValueError("Census of Agriculture Table 30: state row not found")
    return counties, state


def _fill_withheld(values: pd.Series, proxy: pd.Series, state_total: float) -> tuple[pd.Series, int]:
    """Replace NaN (withheld) county values by the state residual shared in proportion to `proxy`."""
    v = values.copy()
    w = v.isna()
    if not w.any():
        return v, 0
    resid = max(0.0, state_total - float(v[~w].sum()))
    px = proxy[w].fillna(0.0).clip(lower=0)
    if px.sum() <= 0:
        px = pd.Series(1.0, index=px.index)
    v[w] = resid * px / px.sum()
    return v, int(w.sum())


def _us_cropland_by_county() -> pd.Series:
    """ESA WorldCover cropland km2 per county (terrain domain hex table x core pieces)."""
    from civmap.config import INTERIM
    pieces = load_pieces("usa")
    f = INTERIM / "usa" / "terrain" / "hex.csv"
    if f.exists():
        hx = pd.read_csv(f, usecols=["h3", "cropland_km2"]).set_index("h3").cropland_km2
        hl = pieces.groupby("h3").land_km2.sum()
        crop = pieces.h3.map(hx).fillna(0.0) * pieces.land_km2 / pieces.h3.map(hl).clip(lower=1e-9)
    else:
        print("  warn: terrain hex.csv missing - Alaska/Connecticut splits fall back to land area")
        crop = pieces.land_km2
    pieces = pieces.assign(crop=crop)
    return pieces.groupby("adm2").crop.sum(), pieces


def _ct_weights(pieces: pd.DataFrame) -> pd.DataFrame:
    """Cropland in each (former CT county x planning region) cell -> DataFrame old_name x region GEOID."""
    z = fetch(URL["cb2021"], "census_cb_2021", "usa/boundaries")
    old = gpd.read_file(unzip(z, ".shp"))
    old = old[old.STATEFP == "09"][["NAME", "geometry"]].to_crs(4326)
    regions = load_admin("usa", "adm2")
    regions = regions[regions.admin_id.str.startswith("09")].set_index("admin_id")
    pc = pieces[pieces.adm2.str.startswith("09")].copy()
    polys = []
    for c, r in zip(pc.h3, pc.adm2):
        hexpoly = Polygon([(lo, la) for la, lo in h3.cell_to_boundary(c)])
        polys.append(hexpoly.intersection(regions.geometry[r]))
    g = gpd.GeoDataFrame(pc[["h3", "adm2", "crop", "land_km2"]].reset_index(drop=True), geometry=polys, crs=4326)
    g = g.to_crs(5070)
    g["a0"] = g.area
    g["pid"] = np.arange(len(g))
    ov = gpd.overlay(g, old.to_crs(5070), how="intersection", keep_geom_type=True)
    ov["frac"] = ov.area / ov.a0.where(ov.a0 > 0, np.nan)
    ov["w_crop"] = ov.crop * ov.frac
    ov["w_land"] = ov.land_km2 * ov.frac
    w = ov.groupby(["NAME", "adm2"])[["w_crop", "w_land"]].sum().reset_index()
    return w


def _us_coa() -> tuple[pd.DataFrame, dict]:
    """County farms, sales, cropland, orchards (2022) for all 50 states, on Census 2023 counties."""
    states = load_admin("usa", "adm1").set_index("admin_id")
    counties = load_admin("usa", "adm2")
    crop_cty, pieces = _us_cropland_by_county()
    rows = []
    info = {"withheld_filled": {}, "unmatched_census_counties_zero": [], "state_sales_k": {}}
    for fips in US_STATE_FIPS:
        if fips == "11":
            continue            # DC has no Census of Agriculture county volume
        st = states.loc[fips]
        text = _coa_text(fips, st["name"], st["abbr"])
        t1, cols = _coa_table1(text)
        orch, orch_state = _coa_table30(text)
        state_row = t1.iloc[0]
        cty = t1.iloc[1:].copy()
        cty["orch_farms"] = [orch.get(n, (np.nan, np.nan))[0] for n in cty.name]
        cty["orchard_ac"] = [orch.get(n, (np.nan, np.nan))[1] for n in cty.name]
        lost = [n for n in cty.name if n not in orch]
        if lost:
            raise ValueError(f"{st['abbr']}: Table 30 has no row for {lost[:5]}")
        tot = {"sales_k": state_row.sales_k, "cropland_ac": state_row.cropland_ac, "orchard_ac": orch_state[1]}
        nfill = {}
        cty = cty.set_index("name")
        for col, proxy in (("sales_k", "farms"), ("cropland_ac", "crop_farms"), ("orchard_ac", "orch_farms")):
            cty[col], nfill[col] = _fill_withheld(cty[col], cty[proxy], tot[col])
        info["withheld_filled"][st["abbr"]] = nfill
        info["state_sales_k"][st["abbr"]] = float(tot["sales_k"])
        mine = counties[counties.parent_id == fips]
        if fips == "02":
            # 5 agricultural census districts with unpublished borough composition: state total -> boroughs
            w = crop_cty.reindex(mine.admin_id).fillna(0.0)
            w = w / w.sum()
            for gid, wi in w.items():
                rows.append({"admin_id": gid, **{c: tot[c] * wi for c in ("sales_k", "cropland_ac", "orchard_ac")}})
            continue
        if fips == "09":
            cw = _ct_weights(pieces)
            for oname, r in cty.iterrows():
                sub = cw[cw.NAME.str.lower() == oname.lower()]
                if sub.empty:
                    raise ValueError(f"CT: former county {oname} not found in cb_2021")
                ww = sub.w_crop if sub.w_crop.sum() > 0 else sub.w_land
                for gid, wi in zip(sub.adm2, ww / ww.sum()):
                    rows.append({"admin_id": gid, **{c: r[c] * wi for c in ("sales_k", "cropland_ac", "orchard_ac")}})
            continue
        idx: dict[str, str] = {}
        for gid, nm in zip(mine.admin_id, mine.name):
            idx.setdefault(_us_key(nm), gid)
        used = set()
        for nm, r in cty.iterrows():
            k = _us_key(nm)
            gid = idx.get(k) or idx.get(k + "city")
            if gid is None or gid in used:
                raise ValueError(f"{st['abbr']}: Census of Agriculture county {nm!r} not matched")
            used.add(gid)
            rows.append({"admin_id": gid, **{c: r[c] for c in ("sales_k", "cropland_ac", "orchard_ac")}})
        for gid, nm in zip(mine.admin_id, mine.name):
            if gid not in used:
                info["unmatched_census_counties_zero"].append(f"{gid} {nm}")
                rows.append({"admin_id": gid, "sales_k": 0.0, "cropland_ac": 0.0, "orchard_ac": 0.0})
    d = pd.DataFrame(rows).groupby("admin_id").sum()
    return d, info


def _us_state_gdp() -> pd.DataFrame:
    z = fetch(URL["sagdp"], "bea_sagdp2", "usa/bea")
    f = unzip(z, f"SAGDP2__ALL_AREAS_1997_{US_GDP_YEAR}.csv")
    d = pd.read_csv(f, dtype=str, encoding="latin-1")
    d["fips"] = d.GeoFIPS.str.strip().str.strip('"').str[:2]
    d = d[d.GeoFIPS.str.strip().str.strip('"').str[2:] == "000"]
    d = d[d.fips.isin(US_STATE_FIPS)]
    out = {}
    for line, key in ((3, "naics11"), (4, "farms"), (5, "forfish")):
        r = d[d.LineCode.str.strip() == str(line)].set_index("fips")[str(US_GDP_YEAR)]
        out[key] = pd.to_numeric(r, errors="coerce") * 1e6
    out = pd.DataFrame(out)
    if out.isna().any().any():
        raise ValueError(f"SAGDP2 {US_GDP_YEAR}: missing values {out[out.isna().any(axis=1)].index.tolist()}")
    gap = (out.farms + out.forfish - out.naics11).abs().max()
    if gap > 2e5:
        raise ValueError(f"SAGDP2: line 4 + line 5 != line 3 (max gap {gap:,.0f})")
    us = d[d.fips == "00"]
    return out


def _us_fish() -> tuple[pd.DataFrame, dict]:
    q = json.dumps({"year": US_FISH_YEAR, "collection": "Commercial"}, separators=(",", ":"))
    p = fetch(URL["foss"], "noaa_foss_landings", "usa/noaa_foss", f"foss_commercial_{US_FISH_YEAR}.json",
              params={"q": q, "limit": 10000})
    js = json.loads(p.read_text())
    if js.get("hasMore"):
        raise ValueError("FOSS: more than 10,000 rows - add paging")
    it = pd.DataFrame(js["items"])
    it["state"] = it.state_name.str.replace(r"-(EAST|WEST)$", "", regex=True).str.title()
    states = load_admin("usa", "adm1")
    by_name = {n.upper(): i for i, n in zip(states.admin_id, states.name)}
    it["fips"] = it.state.str.upper().map(by_name)
    unk = sorted(set(it.loc[it.fips.isna(), "state_name"]))
    agg = it.dropna(subset=["fips"]).groupby("fips").agg(lb=("pounds", "sum"), usd=("dollars", "sum"))
    out = pd.DataFrame(index=states.admin_id)
    out["fish_catch_t"] = (agg.lb.reindex(out.index).fillna(0.0) * LB_T).round(1)
    out["fish_value_usd"] = agg.usd.reindex(out.index).fillna(0.0).round(0)
    out.index.name = "admin_id"
    pas = it[it.state_name == "PROCESS AT SEA"]
    chk = {"landings_states_t": float(out.fish_catch_t.sum()), "value_states_usd": float(out.fish_value_usd.sum()),
           "process_at_sea_t": float(pas.pounds.sum() * LB_T), "process_at_sea_usd": float(pas.dollars.sum()),
           "not_attributed_state_names": unk}
    return out, chk


def build_usa() -> dict:
    cc = "usa"
    coa, cinfo = _us_coa()
    gdp = _us_state_gdp()
    counties = load_admin(cc, "adm2").set_index("admin_id")
    coa = coa.reindex(counties.index)
    acs = util.acs_table("C24030", ACS_YEAR)
    emp = (acs["C24030_E004"] + acs["C24030_E031"]).reindex(counties.index).fillna(0.0)
    st = pd.Series(counties.index.str[:2], index=counties.index)
    sales = coa.sales_k.fillna(0.0) * 1000.0
    # DC: no farms tabulated - its (tiny) BEA farm GDP goes to its single county
    sales_share = sales / sales.groupby(st).transform("sum")
    sales_share[st == "11"] = 1.0
    emp_share = emp / emp.groupby(st).transform("sum")
    emp_share = emp_share.fillna(sales_share)
    gva = st.map(gdp.farms) * sales_share.fillna(0.0) + st.map(gdp.forfish) * emp_share.fillna(0.0)
    out = pd.DataFrame(index=counties.index)
    out["ag_gva_usd"] = gva.round(0)
    out["ag_sales_usd"] = sales.where(st != "11").round(0)
    out["tree_crop_share"] = (100 * coa.orchard_ac / coa.cropland_ac.where(coa.cropland_ac > 0)).clip(upper=100).round(2)
    out.index.name = "admin_id"
    util.write_admin(cc, DOMAIN, "adm2", out.reset_index())
    fish, fchk = _us_fish()
    util.write_admin(cc, DOMAIN, "adm1", fish.reset_index())
    wb_ag = util.wb_value("NV.AGR.TOTL.CD", "USA")
    rep = {
        "gdp_year": US_GDP_YEAR,
        "ag_gva_usd_total": float(out.ag_gva_usd.sum()),
        "bea_states_naics11_usd": float(gdp.naics11.sum()),
        "bea_farms_usd": float(gdp.farms.sum()), "bea_forestry_fishing_usd": float(gdp.forfish.sum()),
        "worldbank_ag_va_usd_latest": wb_ag,
        "ag_sales_usd_total": float(out.ag_sales_usd.sum()),
        "coa_state_sales_sum_usd": 1000.0 * sum(cinfo["state_sales_k"].values()),
        "orchard_acres_total": float(coa.orchard_ac.sum()), "cropland_acres_total": float(coa.cropland_ac.sum()),
        "coa": {k: v for k, v in cinfo.items() if k != "state_sales_k"},
        "fish": fchk,
        "fao_capture_t_wb": util.wb_value("ER.FSH.CAPT.MT", "USA", US_FISH_YEAR),
    }
    return rep
