"""Hazards domain (Disasters lens): cyclones, earthquakes, tsunamis, FEMA NRI, DesInventar losses.

Outputs (data/interim/<country>/hazards/):
  hex.csv          both countries, every hex of core.load_hexes():
                   cyclone_tracks, cyclone_max_wind_kt, quakes_m5, quake_max_mag,
                   tsunami_runups, tsunami_events, tsunami_max_m, hazard_index
  admin_adm2.csv   USA: FEMA National Risk Index (county risk score + expected annual losses)
                   LKA: DesInventar disaster losses by district, 1990 to the last complete year (2019)

Hex metrics are computed from point/track catalogues with great-circle radius searches around
each hex centre (3-D unit vectors + scipy cKDTree, so the Aleutians' antimeridian crossing is
handled).  The composite ``hazard_index`` uses percentile ranks pooled across BOTH countries'
land hexes, so it is computed after both countries' raw hex metrics exist (both are computed,
memoised in-process, whichever country is being built).

    python -m civmap.domains.hazards        # builds lka + usa, then validates
"""
from __future__ import annotations

import dataclasses
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .. import config, schema, util
from ..core import load_admin, load_hexes
from ..fetch import fetch
from ..schema import Metric, Source

DOMAIN = "hazards"
hazards = DOMAIN  # module-shape alias

R_EARTH_KM = 6371.0088
CYCLONE_RADIUS_KM = 100.0
QUAKE_RADIUS_KM = 100.0
TSUNAMI_RADIUS_KM = 25.0
CYCLONE_MIN_SEASON = 1980
CYCLONE_MIN_WIND_KT = 34.0
TRACK_STEP_KM = 10.0           # densification step for cyclone track segments
QUAKE_YEARS = (1976, 2025)     # inclusive
QUAKE_MIN_MAG = 5.0
TSUNAMI_MIN_YEAR = 1900
TSUNAMI_MIN_VALIDITY = 3       # NCEI event validity: 3 = probable, 4 = definite tsunami
DESINV_MIN_YEAR = 1990
DESINV_DEFAULT_LAST_YEAR = 2019   # last complete year of the cached export (labels below); checked at build
DESINV_MIN_MONTHS = 10            # a year counts as complete if datacards fall in >= 10 calendar months

IBTRACS_URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
               "v04r01/access/csv/ibtracs.since1980.list.v04r01.csv")
USGS_FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
HAZEL_RUNUPS = "https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/tsunamis/runups"
NRI_LAYER = ("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
             "National_Risk_Index_Counties/FeatureServer/0/query")
DESINV_EXPORT = "https://www.desinventar.net/DesInventar/download/DI_export_lka.zip"
DESINV_HDX_XLS = ("https://data.humdata.org/dataset/08c9ca36-8b95-4361-abf1-ce21bfc49184/resource/"
                  "4cb4bd14-e5ba-4443-af23-948bfb05f479/download/di_report-srilanka.xls")

# Search regions (lat0, lat1, lon0, lon1); longitudes may exceed 180 to cross the antimeridian
# where the service supports it (USGS FDSN does).  Margins exceed the search radii.
QUAKE_REGIONS = {
    "lka": {"lka": (4.0, 11.8, 77.8, 83.8)},
    "usa": {"conus": (22.0, 51.0, -128.0, -64.0),
            "alaska": (49.0, 73.0, 168.0, 232.0),
            "hawaii": (16.0, 25.0, -163.0, -152.0),
            # Northwestern Hawaiian Islands (French Frigate Shoals to Kure Atoll, Honolulu County 15003)
            # are land hexes of the grid; the main Hawaii box stops at 163W.
            "nwhi": (21.0, 30.0, -180.0, -161.0)},
}
TSUNAMI_REGIONS = {   # HazEL does not wrap the antimeridian -> Alaska is split in two boxes
    "lka": {"lka": (4.5, 11.3, 78.2, 83.3)},
    "usa": {"conus": (23.0, 50.5, -127.0, -65.0),
            "alaska_w": (50.0, 72.5, 168.0, 180.0),
            "alaska_e": (50.0, 72.5, -180.0, -128.0),
            "hawaii": (17.5, 23.5, -161.5, -153.5),
            "nwhi": (22.5, 29.0, -179.5, -161.0)},
}

# DesInventar Sri Lanka event types -> hazard families used here.  Only natural hazards count
# towards deaths / affected / houses destroyed; epidemics, animal attacks, snake bites, fires,
# accidents, drownings, structural collapses and technological events are excluded.
DESINV_FAMILY = {
    "FLOOD": "flood", "FLASH FLOOD": "flood", "URBAN FLOOD": "flood", "CYCLONE & FLOOD": "flood+cyclone",
    "HEAVY RAINS": "hydromet", "RAINS": "hydromet", "SURGE": "hydromet", "TIDAL WAVE": "hydromet",
    "SEDIMENTATION": "hydromet", "COASTLINE": "hydromet", "COASTAL EROSION": "hydromet",
    "CYCLONE": "cyclone",
    "STRONG WIND": "hydromet", "GALE": "hydromet", "TORNADO": "hydromet", "STORM": "hydromet",
    "HAILSTORM": "hydromet", "LIGHTNING": "hydromet", "TREE FALLEN": "hydromet", "FROST": "hydromet",
    "DROUGHT": "drought", "FOREST FIRE": "climatological",
    "LANDSLIDE": "landslide", "EARTH SLIP": "landslide", "ROCK FALL": "landslide",
    "CUTTING FAILURE": "landslide",
    "LAND SUBSIDENCE": "geophysical", "SUBSIDENCE": "geophysical", "GROUND VIBRATIO": "geophysical",
    "EARTHQUAKE": "geophysical", "TSUNAMI": "geophysical",
}
DESINV_EVENT_COUNTS = {
    "flood_events": ("flood", "flood+cyclone"),
    "landslide_events": ("landslide",),
    "drought_events": ("drought",),
    "cyclone_events": ("cyclone", "flood+cyclone"),
}
DESINV_EXCLUDED = ("EPIDEMIC, PLAGUE (crop pests), ANIMAL ATTACK, SNAKE BITE, FIRE (structural), ACCIDENT, "
                   "Drowning, BOAT CAPSIZE, Electrocution, STRUCTURE, Wall Collapse, RETAINING WF, Collapse of "
                   "building/mine/garbage fill, EXPLOSION, LEAK OR SPILL, Chemical")

NRI_FIELDS = {
    "RISK_SCORE": "nri_risk_score",
    "EAL_VALT": "nri_eal_usd",
    "CFLD_EALT": "nri_eal_coastal_flood_usd",
    "IFLD_EALT": "nri_eal_riverine_flood_usd",
    "HRCN_EALT": "nri_eal_hurricane_usd",
    "ERQK_EALT": "nri_eal_earthquake_usd",
    "WFIR_EALT": "nri_eal_wildfire_usd",
    "TRND_EALT": "nri_eal_tornado_usd",
    "DRGT_EALT": "nri_eal_drought_usd",
}

# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------

SOURCES = [
    Source("ibtracs_v04r01",
           "International Best Track Archive for Climate Stewardship (IBTrACS) v04r01 - since1980 list CSV",
           "NOAA National Centers for Environmental Information (NCEI) / WMO",
           "https://www.ncei.noaa.gov/products/international-best-track-archive",
           "public domain (U.S. Government work; cite Knapp et al. 2010, BAMS, doi:10.1175/2009BAMS2755.1)",
           "1980-2026 (2026 season provisional)", ["lka", "usa"],
           "File ibtracs.since1980.list.v04r01.csv (" + IBTRACS_URL + "), one row per 3-hourly track point "
           "from all agencies merged.  Read columns SID, SEASON, ISO_TIME, NATURE, LAT, LON, WMO_WIND, "
           "USA_WIND, TRACK_TYPE; TRACK_TYPE main, PROVISIONAL and US-PROVISIONAL (recent storms not yet in the final best track, e.g. Cyclone Ditwah, Nov 2025); spur tracks (spur-merge, spur-split, spur-other, US-PROVISIONAL_spur) dropped.  "
           "Column definitions (NATURE: DS Disturbance, TS Tropical, ET Extratropical, SS Subtropical, NR Not "
           "reported, MX Mixture; USA_WIND 1-min mean; WMO_WIND not adjusted for averaging period) from "
           "IBTrACS v04r01 column documentation, https://www.ncei.noaa.gov/sites/default/files/2025-09/"
           "IBTrACS_v04r01_column_documentation.pdf."),
    Source("usgs_comcat",
           "ANSS Comprehensive Earthquake Catalog (ComCat), FDSN event web service",
           "U.S. Geological Survey, Earthquake Hazards Program",
           "https://earthquake.usgs.gov/fdsnws/event/1/",
           "public domain (U.S. Government work)", "1976-2025", ["lka", "usa"],
           "CSV queries with minmagnitude=5, eventtype=earthquake, per search box and decade "
           "(each response < 20,000 rows).  Boxes: Sri Lanka 4-11.8N 77.8-83.8E; USA CONUS "
           "22-51N 128-64W, Alaska 49-73N 168E-128W (antimeridian crossing), Hawaii 16-25N 163-152W, "
           "Northwestern Hawaiian Islands 21-30N 180-161W."),
    Source("ncei_tsunami_runups",
           "NCEI/WDS Global Historical Tsunami Database - tsunami runups (Hazard Event Library API)",
           "NOAA National Centers for Environmental Information (NCEI)",
           "https://www.ngdc.noaa.gov/hazel/view/hazards/tsunami/runup-search",
           "public domain (U.S. Government work; cite doi:10.7289/V5PN93H7)", "1900-2026", ["lka", "usa"],
           "Paged JSON from " + HAZEL_RUNUPS + " with minLatitude/maxLatitude/minLongitude/maxLongitude "
           "boxes around each country (so runups in neighbouring countries within the radius also count, "
           "e.g. India for Mannar).  Fields used: year, latitude, longitude, runupHt (maximum water "
           "height, m), sourceEventValidity."),
    Source("fema_nri_counties",
           "National Risk Index - Counties (NRI_Counties_Prod feature layer), December 2025 release",
           "Federal Emergency Management Agency (FEMA)",
           "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer",
           "public domain (U.S. Government work)", "December 2025 release", ["usa"],
           "FEMA's public ArcGIS Online feature service (owner FEMA_NationalRiskIndex, item "
           "39485e8035d446a5bff03259508ae355); the hazards.fema.gov/fema.gov downloads are unreachable "
           "from the build environment.  Attributes queried with returnGeometry=false, paged by "
           "resultOffset.  Field NRI_VER = 'December 2025'; counties keyed by STCOFIPS, already on "
           "current geography (Connecticut planning regions 09110-09190; Alaska 02063/02066)."),
    Source("desinventar_lka",
           "DesInventar Sendai - Sri Lanka national disaster loss database (full XML export)",
           "Disaster Management Centre (DMC), Sri Lanka / UNDRR DesInventar",
           "https://www.desinventar.net/DesInventar/profiletab.jsp?countrycode=lka",
           "CC BY (HDX licence 'cc-by' of the same DMC database, dataset "
           "'disaster-loss-and-damage-dataset-for-sri-lanka')",
           "1974-2020 (2 stray datacards 1965/1968; 2020 has Jan-Feb only) - records used: 1990-2019", ["lka"],
           "Full database export " + DESINV_EXPORT + " (DI_export_lka.xml, 130,140 datacards; the 2020 "
           "datacards cover January-February only, so 2020 is dropped and the period ends with the last "
           "complete year, 2019) - a superset of the HDX snapshot DI_report-SriLanka.xls (HDX dataset date "
           "2015-10-01, records to 2014; " + DESINV_HDX_XLS + "), which is used only as a fallback if the "
           "export is unavailable. "
           "Datacard fields: evento, fechano/fechames/fechadia, level1 (district code lka00P00D), "
           "muertos (deaths), afectados (affected), vivdest (houses destroyed).  District codes mapped "
           "to COD-AB pcodes via the database's lev1 names."),
]

_HEX_METHOD_COMMON = ("Great-circle search around each hex centre (core.load_hexes lat/lon; land and "
                      "water-ring hexes) using 3-D unit vectors in a scipy cKDTree.  Radius-based, so the "
                      "value does not depend on hex size (LKA res-6 and USA res-5 tiles are comparable). "
                      "0 = no qualifying event.")

METRICS = [
    Metric("cyclone_tracks", "Tropical cyclone passes (1980-2026)", "count", "hazard", "intensive", "land",
           "hex", ["ibtracs_v04r01"], "1980-2026",
           "Number of distinct tropical cyclones whose track passed within 100 km of the hex centre at "
           "tropical-storm strength or more, 1980 season to the latest (provisional) 2026 track.",
           "IBTrACS since1980 list: main, PROVISIONAL and US-PROVISIONAL tracks (spurs dropped), points filtered to a box 5 deg around the "
           "country.  Wind = USA_WIND (1-min sustained, JTWC/NHC/CPHC) if reported, else WMO_WIND "
           "(agency-specific averaging, e.g. IMD 3-min, RSMC Tokyo 10-min).  A track point qualifies if "
           "NATURE is not ET (extratropical) or DS (disturbance) - TS (tropical), SS (subtropical), MX and "
           "NR are kept - and its wind is >= 34 kt.  Points with no reported wind never qualify (IBTrACS "
           "NATURE 'TS' means 'Tropical', any intensity, so it is not used as a strength proxy).  "
           "Consecutive points of a storm (<= 6 h apart) are densified to <= 10 km steps (great-circle "
           "interpolation of position; wind interpolated linearly when both fixes report one, otherwise "
           "the half-segment next to the fix with a wind takes that wind) so fast storms are not missed "
           "between 3-hourly fixes.  Count = distinct SIDs with any qualifying point within 100 km.  "
           "Exposure frequency at a point (not additive across hexes). "
           + _HEX_METHOD_COMMON, "lower"),
    Metric("cyclone_max_wind_kt", "Strongest cyclone wind nearby (1980-2026)", "kt", "hazard", "intensive",
           "land", "hex", ["ibtracs_v04r01"], "1980-2026",
           "Highest maximum sustained wind (knots) of any qualifying tropical cyclone while its track "
           "was within 100 km of the hex centre.",
           "Max of the (interpolated) track-point wind over the qualifying points counted in "
           "cyclone_tracks.  USA_WIND (1-min) preferred, WMO_WIND fallback.  0 = no qualifying pass. "
           + _HEX_METHOD_COMMON, "lower"),
    Metric("quakes_m5", "M5+ earthquakes within 100 km (1976-2025)", "count", "hazard", "intensive", "land",
           "hex", ["usgs_comcat"], "1976-2025",
           "Number of magnitude >= 5.0 earthquakes with epicentre within 100 km of the hex centre, "
           "1 Jan 1976 - 31 Dec 2025 (any depth).",
           "USGS ComCat FDSN CSV (preferred magnitude 'mag', any magType; eventtype=earthquake), deduplicated "
           "by event id across overlapping search boxes.  Exposure frequency at a point (not additive). "
           + _HEX_METHOD_COMMON, "lower"),
    Metric("quake_max_mag", "Largest earthquake within 100 km (1976-2025)", "magnitude", "hazard",
           "intensive", "land", "hex", ["usgs_comcat"], "1976-2025",
           "Largest magnitude among the M5+ earthquakes within 100 km of the hex centre.",
           "Max of ComCat preferred magnitude over events counted in quakes_m5.  0 = no M5+ event. "
           + _HEX_METHOD_COMMON, "lower"),
    Metric("tsunami_runups", "Tsunami runup observations within 25 km (1900-2026)", "count", "hazard",
           "intensive", "land", "hex", ["ncei_tsunami_runups"], "1900-2026",
           "Number of NOAA NCEI tsunami runup / tide-gauge observations located within 25 km of the hex "
           "centre since 1900.",
           "HazEL runups API, boxes around each country (no country filter, so cross-border runups count). "
           "Kept: year >= 1900 and source event validity >= 3 (probable or definite tsunami; seiches, "
           "doubtful and erroneous events dropped).  Each runup record counts once (one tsunami usually "
           "produces several records along a coast). " + _HEX_METHOD_COMMON, "lower"),
    Metric("tsunami_events", "Tsunamis observed within 25 km (1900-2026)", "count", "hazard",
           "intensive", "land", "hex", ["ncei_tsunami_runups"], "1900-2026",
           "Number of distinct tsunamis (source events) with at least one NOAA NCEI runup / tide-gauge "
           "observation within 25 km of the hex centre since 1900.",
           "Same runup records as tsunami_runups, counted by distinct tsunamiEventId instead of by record, "
           "so a heavily surveyed single event (e.g. the 2004 Indian Ocean tsunami along Sri Lanka's coast) "
           "counts once.  A frequency measure, used in hazard_index. " + _HEX_METHOD_COMMON, "lower"),
    Metric("tsunami_max_m", "Highest tsunami water height within 25 km (1900-2026)", "m", "hazard",
           "intensive", "land", "hex", ["ncei_tsunami_runups"], "1900-2026",
           "Maximum tsunami water height (runup or tide-gauge amplitude, metres) among the observations "
           "counted in tsunami_runups.",
           "Max of runupHt over the kept runup records; records without a height are counted but do not "
           "set the maximum.  0 = no observation with a height. " + _HEX_METHOD_COMMON, "lower"),
    Metric("hazard_index", "Natural hazard index", "index", "hazard", "intensive", "land", "hex",
           ["ibtracs_v04r01", "usgs_comcat", "ncei_tsunami_runups"], "1900-2026",
           "Composite 0-100 exposure to tropical cyclones, strong earthquakes and tsunamis, on one scale "
           "for both countries (100 = most exposed).",
           "Three event-frequency inputs: cyclone_tracks, quakes_m5 and tsunami_events (distinct "
           "tsunamis, not runup records).  For each input, a hex with 0 events scores 0; a hex with >= 1 "
           "event scores the mid-rank percentile of its value among all LAND hexes of BOTH countries "
           "(Sri Lanka res-6 + USA res-5 pooled) that have >= 1 event of that hazard: "
           "(n_below + 0.5 n_equal) / n_exposed.  Ranking among exposed hexes only keeps a single storm in "
           "47 years from scoring like a hurricane coast just because most US hexes see none.  "
           "hazard_index = 100 x mean of the three scores, so a place exposed to only one hazard tops out "
           "near 33 and multi-hazard coasts (Hawaii, Alaska, California) score highest.  The reference "
           "distribution is pooled, so values are comparable between countries (inputs are radius counts, "
           "independent of hex size).  Each land hex counts once in the pool, so per km2 Sri Lanka's smaller "
           "res-6 tiles carry ~7x the weight of US res-5 tiles (Sri Lanka is ~5% of the pooled land hexes, "
           "but a larger share of the cyclone-exposed ones).  Floods, droughts, landslides, wildfire and tornadoes are not in this "
           "index (no consistent hex-level catalogue for both countries); see the admin tables for those.",
           "lower"),
    # ---------------- USA: FEMA National Risk Index (county) -------------------------------------
    Metric("nri_risk_score", "FEMA National Risk Index score", "index", "hazard", "intensive", "pop",
           "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA National Risk Index composite risk score (0-100): a county's percentile-based score of "
           "NRI risk (expected annual loss from 18 natural hazards, scaled by social vulnerability and "
           "community resilience) relative to all counties in the NRI layer.",
           "Field RISK_SCORE ('National Risk Index - Score - Composite') of the NRI Counties feature layer "
           "(NRI_VER 'December 2025'), joined on STCOFIPS = county GEOID (cb_2023 geography; no splitting "
           "needed).  The layer has 3,232 rows: the 3,144 counties of the 50 states + DC kept here plus 88 "
           "Puerto Rico / Island Area units (dropped), and FEMA's percentile ranks are over all 3,232.  US-only "
           "ranking, so it has no Sri Lankan counterpart.", "lower", ["usa"]),
    Metric("nri_eal_usd", "Expected annual loss from natural hazards (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss, all 18 hazards: buildings + population (statistical value of "
           "life equivalence) + agriculture, in dollars per year.",
           "Field EAL_VALT (Expected Annual Loss - Total - Composite; the 18 hazards are avalanche, coastal "
           "flooding, cold wave, drought, earthquake, hail, heat wave, hurricane, ice storm, landslide, "
           "lightning, inland flooding, strong wind, tornado, tsunami, volcanic activity, wildfire, winter "
           "weather), joined on STCOFIPS.  Dollar basis "
           "as published by FEMA for the December 2025 release (Hazus building inventory, value of a "
           "statistical life) - not re-based.  Allocated to hexes by population.", "lower", ["usa"]),
    Metric("nri_eal_coastal_flood_usd", "Expected annual loss: coastal flooding (FEMA NRI)", "USD/yr",
           "hazard", "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from coastal flooding.",
           "Field CFLD_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' (no "
           "coastline) -> 0.  Allocated to hexes by population (the losses are to buildings, people and "
           "crops on land, so the sea-ring 'coast' weight is not used; within a coastal county the loss is "
           "spread over all its populated hexes, not only the shoreline ones).", "lower", ["usa"]),
    Metric("nri_eal_riverine_flood_usd", "Expected annual loss: inland (riverine) flooding (FEMA NRI)",
           "USD/yr", "hazard", "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from inland flooding (published as 'Riverine Flooding' in "
           "earlier NRI releases).",
           "Field IFLD_EALT ('Inland Flooding - Expected Annual Loss - Total').  The December 2025 layer "
           "has IFLD fields and no RFLD (Riverine Flooding) fields used by earlier releases; the metric key "
           "keeps the riverine name for continuity.  Joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  "
           "Allocated to hexes by population.",
           "lower", ["usa"]),
    Metric("nri_eal_hurricane_usd", "Expected annual loss: hurricanes (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from hurricane winds.",
           "Field HRCN_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  Allocated "
           "to hexes by population.", "lower", ["usa"]),
    Metric("nri_eal_earthquake_usd", "Expected annual loss: earthquakes (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from earthquakes.",
           "Field ERQK_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  Allocated "
           "to hexes by population.", "lower", ["usa"]),
    Metric("nri_eal_wildfire_usd", "Expected annual loss: wildfire (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from wildfire.",
           "Field WFIR_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  Allocated "
           "to hexes by population.", "lower", ["usa"]),
    Metric("nri_eal_tornado_usd", "Expected annual loss: tornadoes (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "pop", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from tornadoes.",
           "Field TRND_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  Allocated "
           "to hexes by population.", "lower", ["usa"]),
    Metric("nri_eal_drought_usd", "Expected annual loss: drought (FEMA NRI)", "USD/yr", "hazard",
           "extensive", "cropland", "admin_adm2", ["fema_nri_counties"], "NRI Dec 2025",
           "FEMA NRI expected annual loss from drought (agricultural losses).",
           "Field DRGT_EALT, joined on STCOFIPS; NULL where FEMA rates the hazard 'Not Applicable' -> 0.  "
           "Allocated to hexes by cropland.", "lower", ["usa"]),
    # ---------------- Sri Lanka: DesInventar (district) ------------------------------------------
    Metric("disaster_deaths", "Deaths in natural disasters (1990-2019)", "persons", "hazard", "extensive",
           "pop", "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Deaths recorded in DesInventar datacards for natural-hazard events, 1990-2019 cumulative "
           "(dominated by the 26 Dec 2004 Indian Ocean tsunami).",
           "Sum of 'muertos' over datacards with year 1990-2019, approved = 0 and a natural-hazard event "
           "type (floods, rains, cyclones, winds, lightning, drought, forest fire, landslides/slope failures, "
           "subsidence, earthquake, tsunami, coastal surge/erosion); excluded: " + DESINV_EXCLUDED + ". "
           "Grouped by district (level1 code -> COD-AB pcode by name).  Allocated to hexes by population.",
           "lower", ["lka"]),
    Metric("disaster_affected", "People affected by natural disasters (1990-2019)", "persons", "hazard",
           "extensive", "pop", "admin_adm2", ["desinventar_lka"], "1990-2019",
           "People reported affected by natural-hazard events, 1990-2019 cumulative (a person affected "
           "by several events is counted each time).",
           "Sum of 'afectados' (DesInventar 'Affected') over the same datacards as disaster_deaths.  "
           "Drought datacards contribute most of the total.  Allocated to hexes by population.",
           "lower", ["lka"]),
    Metric("houses_destroyed", "Houses destroyed by natural disasters (1990-2019)", "houses", "hazard",
           "extensive", "pop", "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Houses reported destroyed by natural-hazard events, 1990-2019 cumulative.",
           "Sum of 'vivdest' (Houses destroyed) over the same datacards as disaster_deaths.  Allocated to "
           "hexes by population.", "lower", ["lka"]),
    Metric("flood_events", "Flood events (1990-2019)", "events", "hazard", "intensive", "land",
           "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Number of flood event-days recorded in the district, 1990-2019.",
           "Event-days: distinct dates with at least one datacard in the district for FLOOD, FLASH FLOOD, "
           "URBAN FLOOD or CYCLONE & FLOOD (datacards of the same day at several GN / DS units, or of "
           "several types of the family, count once; a multi-day event reported on several dates counts "
           "once per date).  A district-level frequency: every hex in the district carries the "
           "district's count (intensive, land weight).", "lower", ["lka"]),
    Metric("landslide_events", "Landslide events (1990-2019)", "events", "hazard", "intensive", "land",
           "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Number of landslide / slope-failure event-days recorded in the district, 1990-2019.",
           "As flood_events, for LANDSLIDE, EARTH SLIP, ROCK FALL and CUTTING FAILURE.", "lower", ["lka"]),
    Metric("drought_events", "Drought events (1990-2019)", "events", "hazard", "intensive", "land",
           "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Number of drought event-days recorded in the district, 1990-2019.",
           "As flood_events, for DROUGHT.", "lower", ["lka"]),
    Metric("cyclone_events", "Cyclone events (1990-2019)", "events", "hazard", "intensive", "land",
           "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Number of cyclone event-days recorded in the district, 1990-2019.",
           "As flood_events, for CYCLONE and CYCLONE & FLOOD (strong winds / gales are not included).",
           "lower", ["lka"]),
    Metric("disaster_deaths_per_100k", "Disaster deaths per 100k per year (1990-2019 avg)",
           "deaths/100k/yr", "hazard", "intensive", "pop", "admin_adm2", ["desinventar_lka"], "1990-2019",
           "Average annual deaths in natural disasters per 100,000 residents, 1990-2019.",
           "disaster_deaths / 30 years / district population (2023, core.load_admin, DCS/UNFPA COD-PS) "
           "x 100,000.  Uses current population as the denominator for the whole period.", "lower", ["lka"]),
]


# ---------------------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------------------

def _xyz(lat, lon) -> np.ndarray:
    la = np.radians(np.asarray(lat, dtype=float))
    lo = np.radians(np.asarray(lon, dtype=float))
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _chord(km: float) -> float:
    return 2.0 * math.sin(km / (2.0 * R_EARTH_KM))


def _in_box(lat, lon, box) -> np.ndarray:
    la0, la1, lo0, lo1 = box
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    inlat = (lat >= la0) & (lat <= la1)
    if lo1 > 180:   # crosses the antimeridian
        inlon = (lon >= lo0) | (lon <= lo1 - 360)
    else:
        inlon = (lon >= lo0) & (lon <= lo1)
    return inlat & inlon


def _pairs(hex_xyz: np.ndarray, pts_xyz: np.ndarray, radius_km: float) -> tuple[np.ndarray, np.ndarray]:
    """All (hex_index, point_index) pairs with great-circle distance <= radius."""
    if len(pts_xyz) == 0:
        return np.zeros(0, int), np.zeros(0, int)
    th = cKDTree(hex_xyz)
    tp = cKDTree(pts_xyz)
    sm = th.sparse_distance_matrix(tp, _chord(radius_km), output_type="coo_matrix")
    return np.asarray(sm.row, dtype=np.int64), np.asarray(sm.col, dtype=np.int64)


def _count_max(n_hex: int, hex_idx, group_codes, values) -> tuple[np.ndarray, np.ndarray]:
    """Per hex: number of distinct groups among pairs, and max value (0 if none)."""
    cnt = np.zeros(n_hex)
    mx = np.zeros(n_hex)
    if len(hex_idx) == 0:
        return cnt, mx
    g = np.asarray(group_codes, dtype=np.int64)
    key = hex_idx * (int(g.max()) + 1) + g
    uk = np.unique(key)
    cnt += np.bincount(uk // (int(g.max()) + 1), minlength=n_hex)
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(v)
    np.fmax.at(mx, hex_idx[ok], v[ok])
    return cnt, mx


# ---------------------------------------------------------------------------------------------
# Tropical cyclones (IBTrACS)
# ---------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ibtracs() -> pd.DataFrame:
    p = fetch(IBTRACS_URL, "ibtracs_v04r01", "hazards/ibtracs", "ibtracs.since1980.list.v04r01.csv", timeout=900)
    cols = ["SID", "SEASON", "NAME", "ISO_TIME", "NATURE", "LAT", "LON", "WMO_WIND", "TRACK_TYPE", "USA_WIND"]
    print("  reading IBTrACS ...", flush=True)
    df = pd.read_csv(p, usecols=cols, skiprows=[1], dtype=str, keep_default_na=False, low_memory=False)
    for c in ("SEASON", "LAT", "LON", "WMO_WIND", "USA_WIND"):
        df[c] = pd.to_numeric(df[c].str.strip(), errors="coerce")
    df["NATURE"] = df.NATURE.str.strip()
    df["TRACK_TYPE"] = df.TRACK_TYPE.str.strip()
    df = df[(df.SEASON >= CYCLONE_MIN_SEASON) & df.TRACK_TYPE.isin(["main", "PROVISIONAL", "US-PROVISIONAL"])]
    df["time"] = pd.to_datetime(df.ISO_TIME, errors="coerce")
    df["wind"] = df.USA_WIND.where(df.USA_WIND.notna(), df.WMO_WIND)
    print(f"  IBTrACS: {len(df):,} track points, {df.SID.nunique():,} storms, last fix {df.time.max()}", flush=True)
    return df.dropna(subset=["LAT", "LON", "time"]).reset_index(drop=True)


def _densified_track_points(country: str) -> pd.DataFrame:
    """Qualifying cyclone track points (<= TRACK_STEP_KM apart) near the country: sid, lat, lon, wind."""
    df = _ibtracs()
    hx = load_hexes(country)
    lo0, la0, lo1, la1 = (hx.lon.min(), hx.lat.min(), hx.lon.max(), hx.lat.max())
    m = 5.0
    if country == "usa":   # Aleutians straddle the antimeridian: hexes on both sides
        sel = (df.LAT >= la0 - m) & (df.LAT <= la1 + m) & ((df.LON <= -60.0) | (df.LON >= 165.0))
    else:
        sel = (df.LAT >= la0 - m) & (df.LAT <= la1 + m) & (df.LON >= lo0 - m) & (df.LON <= lo1 + m)
    d = df[sel].sort_values(["SID", "time"]).reset_index(drop=True)
    xyz = _xyz(d.LAT, d.LON)
    wind = d.wind.to_numpy(float)
    nat = d.NATURE.to_numpy(object)
    sid = d.SID.to_numpy(object)
    t = d.time.to_numpy("datetime64[s]").astype(np.int64)
    n = len(d)
    nxt_ok = np.zeros(n, bool)
    if n > 1:
        nxt_ok[:-1] = (sid[1:] == sid[:-1]) & ((t[1:] - t[:-1]) <= 6 * 3600) & ((t[1:] - t[:-1]) > 0)
    i = np.nonzero(nxt_ok)[0]
    j = i + 1
    ang = np.arccos(np.clip((xyz[i] * xyz[j]).sum(1), -1, 1))
    nseg = np.maximum(1, np.ceil(ang * R_EARTH_KM / TRACK_STEP_KM)).astype(int)
    rep_i = np.repeat(i, nseg)
    rep_j = np.repeat(j, nseg)
    frac = np.concatenate([np.arange(k) / k for k in nseg]) if len(nseg) else np.zeros(0)
    p = xyz[rep_i] * (1 - frac)[:, None] + xyz[rep_j] * frac[:, None]
    p /= np.linalg.norm(p, axis=1)[:, None]
    wa, wb = wind[rep_i], wind[rep_j]
    # both fixes have a wind: linear interpolation; only one has a wind: the half of the segment
    # nearer that fix takes it (a fix without any reported wind stays unknown, never qualifies)
    w = np.where(np.isfinite(wa) & np.isfinite(wb), wa + frac * (wb - wa), np.where(frac < 0.5, wa, wb))
    natp = np.where(frac < 0.5, nat[rep_i], nat[rep_j])
    sidp = sid[rep_i]
    # last point of each run (no valid successor) is added as-is
    last = np.nonzero(~nxt_ok)[0]
    p = np.vstack([p, xyz[last]])
    w = np.concatenate([w, wind[last]])
    natp = np.concatenate([natp, nat[last]])
    sidp = np.concatenate([sidp, sid[last]])
    # IBTrACS NATURE: TS = "Tropical" (any intensity, depressions included), so it says nothing
    # about tropical-storm strength; a point qualifies only on a reported (or interpolated) wind.
    tropical = ~np.isin(natp, ["ET", "DS"])
    qual = tropical & np.isfinite(w) & (w >= CYCLONE_MIN_WIND_KT)
    out = pd.DataFrame({"sid": sidp[qual], "x": p[qual, 0], "y": p[qual, 1], "z": p[qual, 2], "wind": w[qual]})
    print(f"  {country}: {len(d):,} track points in box -> {len(out):,} qualifying densified points, "
          f"{out.sid.nunique():,} storms", flush=True)
    return out


def _cyclones(country: str, hx: pd.DataFrame) -> pd.DataFrame:
    pts = _densified_track_points(country)
    hxyz = _xyz(hx.lat, hx.lon)
    hi, pi = _pairs(hxyz, pts[["x", "y", "z"]].to_numpy(), CYCLONE_RADIUS_KM)
    codes = pd.factorize(pts.sid)[0]
    cnt, mx = _count_max(len(hx), hi, codes[pi], pts.wind.to_numpy()[pi])
    return pd.DataFrame({"cyclone_tracks": cnt, "cyclone_max_wind_kt": mx})


# ---------------------------------------------------------------------------------------------
# Earthquakes (USGS ComCat)
# ---------------------------------------------------------------------------------------------

def _quake_catalog(country: str) -> pd.DataFrame:
    frames = []
    y0, y1 = QUAKE_YEARS
    for reg, (la0, la1, lo0, lo1) in QUAKE_REGIONS[country].items():
        for a in range(y0, y1 + 1, 10):
            b = min(a + 10, y1 + 1)
            params = {"format": "csv", "minmagnitude": QUAKE_MIN_MAG, "eventtype": "earthquake",
                      "starttime": f"{a}-01-01", "endtime": f"{b}-01-01",
                      "minlatitude": la0, "maxlatitude": la1, "minlongitude": lo0, "maxlongitude": lo1,
                      "orderby": "time-asc", "limit": 20000}
            p = fetch(USGS_FDSN, "usgs_comcat", "hazards/usgs", f"m5_{country}_{reg}_{a}_{b - 1}.csv",
                      params=params, timeout=300)
            d = pd.read_csv(p)
            if len(d) >= 20000:
                raise RuntimeError(f"USGS query {reg} {a}-{b} hit the 20000-row limit; split it")
            frames.append(d)
            print(f"  usgs {country}/{reg} {a}-{b - 1}: {len(d)} events", flush=True)
    q = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    q = q[(q.mag >= QUAKE_MIN_MAG) & q.latitude.notna() & q.longitude.notna()]
    return q.reset_index(drop=True)


def _quakes(country: str, hx: pd.DataFrame) -> pd.DataFrame:
    q = _quake_catalog(country)
    hi, pi = _pairs(_xyz(hx.lat, hx.lon), _xyz(q.latitude, q.longitude), QUAKE_RADIUS_KM)
    cnt, mx = _count_max(len(hx), hi, pi, q.mag.to_numpy()[pi])
    return pd.DataFrame({"quakes_m5": cnt, "quake_max_mag": mx})


# ---------------------------------------------------------------------------------------------
# Tsunami runups (NOAA NCEI HazEL)
# ---------------------------------------------------------------------------------------------

def _runups(country: str) -> pd.DataFrame:
    rows = []
    for reg, (la0, la1, lo0, lo1) in TSUNAMI_REGIONS[country].items():
        params = {"minLatitude": la0, "maxLatitude": la1, "minLongitude": lo0, "maxLongitude": lo1}
        page, total = 1, 1
        while page <= total:
            q = dict(params, page=page)
            p = fetch(HAZEL_RUNUPS, "ncei_tsunami_runups", "hazards/tsunami",
                      f"runups_{country}_{reg}_p{page:03d}.json", params=q, timeout=120)
            js = json.loads(p.read_text())
            total = int(js.get("totalPages") or 1)
            rows.extend(js.get("items", []))
            page += 1
        print(f"  hazel {country}/{reg}: {total} pages", flush=True)
    d = pd.DataFrame(rows).drop_duplicates("id")
    for c in ("year", "latitude", "longitude", "runupHt", "sourceEventValidity", "tsunamiEventId"):
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    d["tsunamiEventId"] = d.tsunamiEventId.astype("Int64")
    d = d[(d.year >= TSUNAMI_MIN_YEAR) & (d.sourceEventValidity >= TSUNAMI_MIN_VALIDITY)
          & d.latitude.notna() & d.longitude.notna()]
    return d.reset_index(drop=True)


def _tsunamis(country: str, hx: pd.DataFrame) -> pd.DataFrame:
    r = _runups(country)
    print(f"  {country}: {len(r)} runup records kept (>= {TSUNAMI_MIN_YEAR}, validity >= {TSUNAMI_MIN_VALIDITY})")
    hi, pi = _pairs(_xyz(hx.lat, hx.lon), _xyz(r.latitude, r.longitude), TSUNAMI_RADIUS_KM)
    cnt, mx = _count_max(len(hx), hi, pi, r.runupHt.to_numpy()[pi])
    # distinct source events (records without an event id count as their own event)
    ev = r.tsunamiEventId.astype("string").fillna("r" + r["id"].astype(str))
    nev, _ = _count_max(len(hx), hi, pd.factorize(ev)[0][pi], np.zeros(len(pi)))
    return pd.DataFrame({"tsunami_runups": cnt, "tsunami_events": nev, "tsunami_max_m": mx})


# ---------------------------------------------------------------------------------------------
# Raw hex table + pooled hazard index
# ---------------------------------------------------------------------------------------------

_RAW: dict[str, pd.DataFrame] = {}
INDEX_INPUTS = ("cyclone_tracks", "quakes_m5", "tsunami_events")


def _raw_hex(country: str) -> pd.DataFrame:
    if country not in _RAW:
        print(f"[{DOMAIN}] raw hex metrics for {country}", flush=True)
        hx = load_hexes(country).reset_index(drop=True)
        out = pd.concat([hx[["h3", "ring"]], _cyclones(country, hx), _quakes(country, hx),
                         _tsunamis(country, hx)], axis=1)
        _RAW[country] = out
    return _RAW[country]


def _pooled_percentile(values: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Mid-rank percentile (0..1) of each value among the POSITIVE reference values; 0 -> 0."""
    ref = np.sort(ref[ref > 0])
    below = np.searchsorted(ref, values, side="left")
    upto = np.searchsorted(ref, values, side="right")
    pct = (below + 0.5 * (upto - below)) / len(ref)
    return np.where(values > 0, pct, 0.0)


def hazard_index(country: str) -> np.ndarray:
    raws = {cc: _raw_hex(cc) for cc in ("lka", "usa")}
    me = raws[country]
    parts = []
    for k in INDEX_INPUTS:
        ref = np.concatenate([r.loc[r.ring == 0, k].to_numpy(float) for r in raws.values()])
        parts.append(_pooled_percentile(me[k].to_numpy(float), ref))
    return np.round(100.0 * np.mean(parts, axis=0), 2)


# ---------------------------------------------------------------------------------------------
# USA: FEMA National Risk Index
# ---------------------------------------------------------------------------------------------

def _nri_ratings() -> list[str]:
    """Hazard-type risk rating fields matching the per-hazard EAL fields (CFLD_EALT -> CFLD_RISKR)."""
    return [f.replace("_EALT", "_RISKR") for f in NRI_FIELDS if f.endswith("_EALT") and f != "EAL_VALT"]


def _nri() -> pd.DataFrame:
    rows = []
    offset, page = 0, 0
    fields = ",".join(["STCOFIPS", "STATEABBRV", "COUNTY", "NRI_VER"] + list(NRI_FIELDS) + _nri_ratings())
    while True:
        params = {"where": "1=1", "outFields": fields, "returnGeometry": "false", "f": "json",
                  "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 2000}
        p = fetch(NRI_LAYER, "fema_nri_counties", "hazards/fema_nri", f"nri_counties_v2_p{page:02d}.json",
                  params=params, timeout=180)
        js = json.loads(p.read_text())
        if "error" in js:
            raise RuntimeError(f"NRI query error: {js['error']}")
        feats = js.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        if not feats or not js.get("exceededTransferLimit"):
            break
        offset += len(feats)
        page += 1
    d = pd.DataFrame(rows)
    print(f"  NRI: {len(d)} county rows, versions {sorted(d.NRI_VER.dropna().unique())}", flush=True)
    return d


def _usa_admin() -> pd.DataFrame:
    d = _nri()
    adm = load_admin("usa", "adm2")
    d["admin_id"] = d.STCOFIPS.astype(str).str.zfill(5)
    if d.admin_id.duplicated().any():
        raise RuntimeError(f"NRI: duplicate STCOFIPS {d.admin_id[d.admin_id.duplicated()].head().tolist()}")
    other = d[~d.admin_id.isin(set(adm.admin_id))]
    print(f"  NRI: dropped {len(other)} rows outside the 50 states + DC: "
          f"{other.STATEABBRV.value_counts().to_dict()}", flush=True)
    d = d[d.admin_id.isin(set(adm.admin_id))]
    miss = sorted(set(adm.admin_id) - set(d.admin_id))
    if miss:
        print(f"  WARNING NRI: {len(miss)} counties without NRI rows, e.g. {miss[:8]}")
    out = d[["admin_id"]].copy()
    for f, key in NRI_FIELDS.items():
        v = pd.to_numeric(d[f], errors="coerce")
        rf = f.replace("_EALT", "_RISKR")
        if rf in d.columns:   # hazard not applicable to the county (e.g. no coastline) -> zero loss
            na = v.isna() & (d[rf] == "Not Applicable")
            v = v.mask(na, 0.0)
        out[key] = v.to_numpy()
    return out.sort_values("admin_id").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# Sri Lanka: DesInventar
# ---------------------------------------------------------------------------------------------

_DI_KEEP = ["level1", "evento", "fechano", "fechames", "fechadia", "muertos", "afectados", "vivdest", "approved"]


def _desinventar_export() -> tuple[pd.DataFrame, dict[str, str]]:
    """Datacards from the full DesInventar XML export (+ lev1 code -> district name)."""
    z = fetch(DESINV_EXPORT, "desinventar_lka", "hazards/desinventar", "DI_export_lka.zip", timeout=900)
    cache = z.with_name("DI_export_lka_fichas.csv.gz")
    names_p = z.with_name("DI_export_lka_lev1.json")
    if cache.exists() and names_p.exists() and cache.stat().st_mtime >= z.stat().st_mtime:
        return pd.read_csv(cache, dtype=str, keep_default_na=False), json.loads(names_p.read_text())
    print("  parsing DesInventar XML export (1.2 GB, ~30 s) ...", flush=True)
    rows, names, path = [], {}, []
    with zipfile.ZipFile(z) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        with zf.open(member) as f:
            for ev, el in ET.iterparse(f, events=("start", "end")):
                if ev == "start":
                    path.append(el.tag)
                    continue
                path.pop()
                if el.tag == "TR" and len(path) == 2:
                    sec = path[1]
                    if sec == "fichas":
                        rows.append([(el.findtext(k) or "") for k in _DI_KEEP])
                    elif sec == "lev1":
                        names[el.findtext("lev1_cod") or ""] = el.findtext("lev1_name_en") or el.findtext("lev1_name") or ""
                    el.clear()
                elif len(path) <= 1:
                    el.clear()
    df = pd.DataFrame(rows, columns=_DI_KEEP)
    df.to_csv(cache, index=False)
    names_p.write_text(json.dumps(names, indent=1))
    return df, names


def _desinventar_hdx() -> tuple[pd.DataFrame, dict[str, str]]:
    """Fallback: 2016 HDX snapshot (tab-separated despite the .xls name), records to 2014."""
    p = fetch(DESINV_HDX_XLS, "desinventar_lka", "hazards/desinventar", "di_report-srilanka.xls")
    d = pd.read_csv(p, sep="\t", dtype=str, encoding="latin-1", keep_default_na=False)
    dt = d["Date (YMD)"].str.strip()
    dmy = pd.to_datetime(dt, format="%d/%m/%Y", errors="coerce")
    ymd = dt.str.extract(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
    out = pd.DataFrame({
        "level1": d["Code District"], "evento": d["Event"],
        "fechano": dmy.dt.year.astype("Int64").astype(str).where(dmy.notna(), ymd[0]),
        "fechames": dmy.dt.month.astype("Int64").astype(str).where(dmy.notna(), ymd[1]),
        "fechadia": dmy.dt.day.astype("Int64").astype(str).where(dmy.notna(), ymd[2]),
        "muertos": d["Deaths"], "afectados": d["Affected"], "vivdest": d["Houses Destroyed"], "approved": "0",
    })
    z = fetch("https://data.humdata.org/dataset/08c9ca36-8b95-4361-abf1-ce21bfc49184/resource/"
              "49adba32-509f-49e7-b63e-928ef743c52e/download/srilanka.zip",
              "desinventar_lka", "hazards/desinventar", "srilanka.zip")
    import geopandas as gpd
    from ..fetch import unzip
    g = gpd.read_file(unzip(z) / "ditric.dbf")
    return out, dict(zip(g.dis_code, g.DISTRICT_N))


_NAME_FIX = {"moneragala": "monaragala", "mullaittivu": "mullaitivu", "kegalla": "kegalle",
             "digamadulla": "ampara", "puttalama": "puttalam", "nuwaraeliya": "nuwara eliya"}


def _norm(s: str) -> str:
    s = " ".join(str(s).lower().replace("-", " ").split())
    return _NAME_FIX.get(s, _NAME_FIX.get(s.replace(" ", ""), s))


def _lka_admin() -> tuple[pd.DataFrame, dict]:
    try:
        di, names = _desinventar_export()
        origin = "export"
    except Exception as e:  # noqa: BLE001 - fall back to the HDX snapshot
        print(f"  DesInventar export unavailable ({e}); using HDX snapshot")
        di, names = _desinventar_hdx()
        origin = "hdx"
    adm = load_admin("lka", "adm2")
    by_name = {_norm(n): a for a, n in zip(adm.admin_id, adm.name)}
    code2pc = {}
    for code, nm in names.items():
        pc = by_name.get(_norm(nm))
        if pc is None:
            raise RuntimeError(f"DesInventar district {code} {nm!r} not matched to a COD-AB pcode")
        code2pc[code] = pc
    assert len(set(code2pc.values())) == 25, "district mapping is not 1:1"

    d = di.copy()
    for c in ("fechano", "fechames", "fechadia", "muertos", "afectados", "vivdest"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["ev"] = d.evento.str.strip().str.upper()
    d["family"] = d.ev.map(DESINV_FAMILY)
    d["admin_id"] = d.level1.map(code2pc)
    n_all = len(d)
    d = d[(d.approved.astype(str).str.strip().isin(["0", ""])) & (d.fechano >= DESINV_MIN_YEAR)]
    # Last COMPLETE year: the export's final year can be a stub (the 2020 export has datacards for
    # Jan-Feb only, plus one in Dec), so use the latest year with datacards in >= 10 calendar months.
    months = d[d.fechames.between(1, 12)].groupby("fechano").fechames.nunique()
    last_datacard_year = int(d.fechano.max())
    last_year = int(months[months >= DESINV_MIN_MONTHS].index.max())
    d = d[d.fechano <= last_year]
    n_period = len(d)
    nat = d[d.family.notna() & d.admin_id.notna()].copy()
    stats = {"origin": origin, "datacards_total": n_all, "datacards_period": n_period,
             "datacards_natural_with_district": len(nat), "first_year": DESINV_MIN_YEAR, "last_year": last_year,
             "last_datacard_year": last_datacard_year,
             "months_with_datacards_after_last_year": {int(y): int(n) for y, n in months[months.index > last_year].items()},
             "unmapped_event_types": sorted(set(d.ev) - set(DESINV_FAMILY))}
    agg = nat.groupby("admin_id").agg(disaster_deaths=("muertos", "sum"), disaster_affected=("afectados", "sum"),
                                      houses_destroyed=("vivdest", "sum"))
    nat["date_key"] = (nat.fechano.fillna(0).astype(int).astype(str) + "-" + nat.fechames.fillna(0).astype(int).astype(str)
                       + "-" + nat.fechadia.fillna(0).astype(int).astype(str))
    for key, fams in DESINV_EVENT_COUNTS.items():
        sub = nat[nat.family.isin(fams)]
        # one event-day per district and date, whatever the datacard type within the family
        # (e.g. LANDSLIDE + CUTTING FAILURE, or FLOOD + FLASH FLOOD, on the same day count once)
        agg[key] = sub.drop_duplicates(["admin_id", "date_key"]).groupby("admin_id").size()
    out = adm[["admin_id", "pop"]].merge(agg, left_on="admin_id", right_index=True, how="left")
    cols = ["disaster_deaths", "disaster_affected", "houses_destroyed"] + list(DESINV_EVENT_COUNTS)
    out[cols] = out[cols].fillna(0.0)
    n_years = last_year - DESINV_MIN_YEAR + 1
    stats["n_years"] = n_years
    out["disaster_deaths_per_100k"] = (out.disaster_deaths / n_years / out["pop"] * 1e5).round(3)
    stats["by_family"] = nat.groupby("family").agg(n=("ev", "size"), deaths=("muertos", "sum"),
                                                   affected=("afectados", "sum")).reset_index().to_dict("records")
    return out.drop(columns=["pop"]), stats


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------

def build(country: str) -> None:
    print(f"[{DOMAIN}] building {country}", flush=True)
    raw = _raw_hex(country)
    hexdf = raw.drop(columns=["ring"]).copy()
    hexdf["hazard_index"] = hazard_index(country)
    util.write_hex(country, DOMAIN, hexdf)
    land = raw.ring == 0
    print(f"  hex: {len(hexdf)} rows; land hexes with cyclone passes {(raw.cyclone_tracks[land] > 0).mean():.1%}, "
          f"with M5+ quakes {(raw.quakes_m5[land] > 0).mean():.1%}, with tsunami runups "
          f"{(raw.tsunami_runups[land] > 0).mean():.1%}", flush=True)

    metrics = METRICS
    if country == "usa":
        adm = _usa_admin()
        util.write_admin(country, DOMAIN, "adm2", adm)
        print(f"  NRI EAL total ${adm.nri_eal_usd.sum() / 1e9:,.2f} bn/yr; counties {len(adm)}", flush=True)
    else:
        adm, stats = _lka_admin()
        util.write_admin(country, DOMAIN, "adm2", adm)
        (config.interim_dir(country, DOMAIN) / "desinventar_stats.json").write_text(json.dumps(stats, indent=2, default=str))
        print(f"  DesInventar ({stats['origin']}): {stats['datacards_natural_with_district']:,} natural-hazard "
              f"datacards {DESINV_MIN_YEAR}-{stats['last_year']}; deaths {adm.disaster_deaths.sum():,.0f}, "
              f"affected {adm.disaster_affected.sum():,.0f}, houses destroyed {adm.houses_destroyed.sum():,.0f}",
              flush=True)
        if stats["last_year"] != DESINV_DEFAULT_LAST_YEAR:   # e.g. HDX fallback: keep vintages/labels truthful
            old = f"{DESINV_MIN_YEAR}-{DESINV_DEFAULT_LAST_YEAR}"
            span = f"{DESINV_MIN_YEAR}-{stats['last_year']}"
            n0 = f"{DESINV_DEFAULT_LAST_YEAR - DESINV_MIN_YEAR + 1} years"
            metrics = [dataclasses.replace(m, vintage=span, label=m.label.replace(old, span),
                                           description=m.description.replace(old, span),
                                           method=m.method.replace(old, span).replace(n0, f"{stats['n_years']} years"))
                       if "desinventar_lka" in m.sources else m for m in METRICS]
    schema.write_domain_meta(config.interim_dir(country, DOMAIN), metrics, SOURCES, country)


if __name__ == "__main__":
    import sys

    from .. import validate

    for cc in ("lka", "usa"):
        build(cc)
    sys.exit(validate.main([DOMAIN, "lka", "usa"]))
