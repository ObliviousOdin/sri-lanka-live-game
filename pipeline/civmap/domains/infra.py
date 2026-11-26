"""Infrastructure domain: power plants, seaports, airports, military sites, spaceports, industrial zones.

Feeds the Production yield (power-plant bonus), the Harbor / Aerodrome / Encampment / Spaceport /
Industrial Zone districts and the infrastructure Wonders (largest plants, busiest ports & airports).

Outputs (data/interim/<country>/infra/):
  points_power_plants.csv     id,name,lat,lon,kind(=primary fuel),power_capacity_mw,generation_gwh,...
  points_ports.csv            id,name,lat,lon,kind='seaport',port_cargo_t,port_calls[,port_teu]
  points_airports.csv         id,name,lat,lon,kind(=OurAirports type),iata,airports,airport_pax
  points_military.csv         id,name,lat,lon,kind,branch,military_sites
  points_spaceports.csv       id,name,lat,lon,kind,operator,spaceports          (US; LKA empty)
  points_industrial_zones.csv id,name,lat,lon,kind,industrial_zones             (LKA)
  hex.csv                     h3,power_capacity_mw,generation_gwh,renewable_capacity_mw,plants

Run:  python -m civmap.domains.infra      (builds lka + usa, then validates)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import util
from ..config import CACHE, US_STATE_FIPS, interim_dir
from ..core import load_admin, load_hexes
from ..fetch import fetch, unzip
from ..schema import Metric, Source, write_domain_meta

DOMAIN = "infra"
infra = DOMAIN

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

GPPD_URL = ("https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/"
            "global_power_plant_database.csv")
EIA860_URL = "https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip"
EIA923_URL = "https://www.eia.gov/electricity/data/eia923/xls/f923_2025.zip"
CEB_AR_URL = ("https://www.dropbox.com/scl/fi/xzi7k1ntby3y96ip4nimc/3.-CEB-Annual-Report-2023-English.pdf"
              "?rlkey=kbjxu6hj4pwdz2it3d7vfuz03&st=48utl0ii&dl=1")
CEB_SD_URL = "https://www.ceb.lk/front_img/img_reports/1783568926SD-2025-Finalized-(Web-Edition).pdf"
PW_CSV = {
    "lka": ("https://data.humdata.org/dataset/bdda47c2-e027-4812-a389-93c787355132/resource/"
            "e96e5f61-5b20-4e0e-9aa5-a224d7613636/download/"
            "sri-lanka-daily-port-activity-data-and-shipment-estimates.csv"),
    "usa": ("https://data.humdata.org/dataset/85fb644b-a84c-4e3f-9bbf-2f82bcad50dc/resource/"
            "561bdf82-9497-4654-81fe-404355387c0c/download/"
            "united-states-of-america-daily-port-activity-data-and-shipment-estimates.csv"),
}
PW_PORTS_DB = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
               "PortWatch_ports_database/FeatureServer/0/query")
SLPA_AR_URL = "https://www.slpa.lk/uploads/article_attachment/annual-report-2024.pdf"
DCS_714_URL = "https://www.statistics.gov.lk/abstract2025/CHAP7/7.14.pdf"
DCS_716_URL = "https://www.statistics.gov.lk/abstract2025/CHAP7/7.16.pdf"
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
FAA_URL = ("https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/"
           "arp-cy2025-all-enplanements.xlsx")
MIRTA_URL = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
             "MIRTA_Points_A_view/FeatureServer/0/query")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

SOURCES = [
    Source("wri_gppd", "Global Power Plant Database v1.3.0",
           "World Resources Institute (WRI) et al.",
           "https://datasets.wri.org/dataset/globalpowerplantdatabase", "CC BY 4.0",
           "capacity c. 2017-2019; estimated generation 2017", ["lka"],
           "Downloaded from the WRI GitHub release (output_database/global_power_plant_database.csv). "
           "Used for Sri Lankan plant locations, capacity of plants not owned by CEB and modelled "
           "(capacity-factor) generation 'estimated_generation_gwh_2017' where no reported figure exists."),
    Source("ceb_annual_report_2023", "Ceylon Electricity Board Annual Report 2023 (English)",
           "Ceylon Electricity Board (CEB)", "https://www.ceb.lk/publication-media/annual-reports/en",
           "public document (CEB)", "2023", ["lka"],
           "Tables G1-G3 (report pp. 22-23 / PDF pp. 24-25): installed capacity (MW) and 2023 net "
           "generation (GWh) of every CEB-owned hydro, wind, oil and coal power station; p. 15 (PDF 17): "
           "CEB took over the 163 MW Sojitz Kelanitissa combined-cycle plant in April 2023 (renamed KCCP2); "
           "p. 209 (PDF 211) Table 01: Uma Oya hydro power plant 120 MW, commissioned April 2024. "
           f"PDF linked from the CEB annual-reports page: {CEB_AR_URL}"),
    Source("ceb_statistical_digest_2025", "CEB Statistical Digest 2025 (Web Edition)",
           "Ceylon Electricity Board (CEB)", CEB_SD_URL, "public document (CEB)", "2025", ["lka"],
           "Summary page 'Generation statistics': number of stations, installed capacity and net generation "
           "by owner and technology for 2024-2025 (national totals; used for coverage checks and to "
           "identify retired independent oil-fired plants)."),
    Source("eia_860", "Form EIA-860 detailed data, 2025 (final release, Sept 2026)",
           "U.S. Energy Information Administration (EIA)", "https://www.eia.gov/electricity/data/eia860/",
           "public domain", "2025", ["usa"],
           "2___Plant_Y2025.xlsx (plant locations, operator) and 3_1_Generator_Y2025.xlsx sheet 'Operable' "
           "(generator nameplate capacity, technology, status, operating year)."),
    Source("eia_923", "Form EIA-923 detailed data, 2025 (final release, Sept 2026)",
           "U.S. Energy Information Administration (EIA)", "https://www.eia.gov/electricity/data/eia923/",
           "public domain", "2025", ["usa"],
           "EIA923_Schedules_2_3_4_5_M_12_2025_Final.xlsx, sheet 'Page 1 Generation and Fuel Data': annual "
           "net generation (MWh) by plant, prime mover and fuel."),
    Source("imf_portwatch", "IMF PortWatch - Daily Port Activity Data and Trade Estimates; PortWatch ports database",
           "International Monetary Fund (IMF) PortWatch / UN Global Platform (via HDX)",
           "https://portwatch.imf.org/", "IMF PortWatch terms (open data, attribution)", "2025", ["lka", "usa"],
           "Daily AIS-based port calls and estimated import / export volumes (metric tonnes) per port, "
           "from HDX datasets 'sri-lanka-daily-port-activity-data-and-shipment-estimates' and "
           "'united-states-of-america-daily-port-activity-data-and-shipment-estimates'. Port coordinates "
           f"from the PortWatch_ports_database ArcGIS feature service ({PW_PORTS_DB})."),
    Source("slpa_annual_report_2024", "Sri Lanka Ports Authority Annual Report 2024",
           "Sri Lanka Ports Authority (SLPA)", "https://www.slpa.lk/port-colombo/annual-reports",
           "public document (SLPA)", "2024", ["lka"],
           f"{SLPA_AR_URL}. 'Operational highlights' pp. 9-11 (PDF pp. 10-12): Port of Colombo container "
           "throughput 7,792,069 TEU (2024), ship arrivals by port, cargo handled at Trincomalee, Galle and "
           "Hambantota (thousand tonnes)."),
    Source("dcs_abstract_2025_transport", "Statistical Abstract 2025, Chapter 7 (Tables 7.14, 7.16)",
           "Department of Census and Statistics Sri Lanka (source agencies: SLPA, Civil Aviation Authority)",
           "https://www.statistics.gov.lk/abstract2025/chapter7", "public document (DCS)", "2024", ["lka"],
           f"Table 7.14 Total cargo handled by port 2018-2024 ({DCS_714_URL}); Table 7.16 number of air "
           f"passengers to/from Sri Lanka 2024 ({DCS_716_URL}): national totals only, no per-airport split."),
    Source("ourairports", "OurAirports open airport database (airports.csv)", "OurAirports (David Megginson et al.)",
           "https://ourairports.com/data/", "public domain", "2026 (continuously updated)", ["lka", "usa"],
           f"{OURAIRPORTS_URL}; types large_airport and medium_airport."),
    Source("faa_enplanements_cy2025", "CY 2025 Passenger Boarding (Enplanement) Data - all airports",
           "U.S. Federal Aviation Administration (FAA), Air Carrier Activity Information System",
           "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger",
           "public domain", "2025", ["usa"],
           f"{FAA_URL} (updated 2026-09-24). Revenue passenger enplanements (boardings) per airport, CY 2025."),
    Source("dod_mirta", "Military Installations, Ranges, and Training Areas (MIRTA) - Points",
           "U.S. Department of Defense, Defense Installation Spatial Data Infrastructure (DISDI); "
           "served by Esri US Federal Data", "https://www.acq.osd.mil/eie/imr/rpid/disdi/",
           "public domain", "2025", ["usa"],
           f"ArcGIS feature service {MIRTA_URL}; points of the most commonly known DoD sites, "
           "installations, ranges and training areas reported in the Base Structure Report."),
    Source("wikidata", "Wikidata (SPARQL query service)", "Wikimedia Foundation / Wikidata contributors",
           "https://query.wikidata.org/", "CC0 1.0", "accessed 2026-11", ["lka", "usa"],
           "Sri Lankan military bases / air bases (class and operator queries), US spaceports "
           "(instances of spaceport Q194188 and subclasses) and coordinates of a few Sri Lankan plants "
           "missing from GPPD (Thambapavani wind farm, Uma Oya)."),
    Source("osm_nominatim", "OpenStreetMap via the Nominatim search API",
           "OpenStreetMap contributors / OSM Foundation", "https://nominatim.openstreetmap.org/",
           "ODbL 1.0", "accessed 2026-11", ["lka"],
           "Named landuse=industrial and boundary=special_economic_zone features in Sri Lanka "
           "(Board of Investment export processing zones and industrial parks); centroid coordinates."),
]

# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

RENEWABLE_FUELS = {"Hydro", "Wind", "Solar", "Geothermal", "Biomass"}

METRICS = [
    Metric("power_capacity_mw", "Power plant capacity", "MW", "energy", "extensive", "none",
           "points_power_plants", ["eia_860", "wri_gppd", "ceb_annual_report_2023", "wikidata"],
           "USA 2025; LKA 2023 (CEB plants) / c.2017-2019 (others)",
           "Installed (nameplate) electric generating capacity of utility-scale power plants.",
           "USA: EIA-860 2025 generator file, sheet 'Operable', summing nameplate MW of generators with "
           "status OP (operating) or SB (standby) per plant; generators out of service (OS/OA) excluded; "
           "plants in the 50 states + DC at EIA-860 plant coordinates. LKA: CEB-owned stations from CEB "
           "Annual Report 2023 Tables G1-G3 (Old+New Laxapana reported as one 153.8 MW station; the four "
           "Kelanitissa units KCCP 165 + KCCP2 157 + GT7 115 + Frame V GTs 80 = 517 MW combined into one "
           "point; Uma Oya 120 MW added from the same report, commissioned Apr 2024); other plants from WRI "
           "GPPD v1.3.0. Three small legacy independent oil plants in GPPD (Asia Power 51 MW, Northern "
           "Power 36 MW, Lakdhanavi 24 MW) are dropped because the CEB Statistical Digest 2025 lists only "
           "2 independent oil-fired stations (482 MW in 2024), which the 300 MW Yugadanavi plant already "
           "dominates. CEB's 40 x 1 MW dispersed diesel units and most small IPP mini-hydro / solar plants "
           "have no published locations and are missing (see coverage in the infra report). Rooftop "
           "solar is not included in either country.", "higher"),
    Metric("generation_gwh", "Electricity generation", "GWh", "energy", "extensive", "none",
           "points_power_plants", ["eia_923", "ceb_annual_report_2023", "wri_gppd"],
           "USA 2025; LKA 2023 (CEB) / 2017 estimate (others)",
           "Annual net electricity generation of each plant.",
           "USA: EIA-923 2025 'Page 1 Generation and Fuel Data' net generation (MWh) summed over all "
           "prime-mover/fuel rows of each plant, /1000; plants with negative net generation (pumped "
           "storage, batteries) set to 0; the 'State-Fuel Level Increment' pseudo-plant (id 99999) is "
           "excluded. LKA: CEB-owned stations use reported 2023 net generation (CEB Annual Report 2023 "
           "Tables G1-G3); other plants use GPPD v1.3.0 'estimated_generation_gwh_2017' (capacity-factor "
           "model). Columns generation_year and generation_basis record which applies.", "higher"),
    Metric("renewable_capacity_mw", "Renewable power capacity", "MW", "energy", "extensive", "none", "hex",
           ["eia_860", "wri_gppd", "ceb_annual_report_2023"], "USA 2025; LKA 2023 / c.2017-2019",
           "Installed capacity of hydro (excl. pumped storage), wind, solar, geothermal and biomass plants per hex.",
           "Plant-level capacity of renewable technologies summed per snapped H3 cell. For US plants the "
           "renewable part is computed per generator (EIA technologies Conventional Hydroelectric, "
           "Onshore/Offshore Wind, Solar PV/Thermal, Geothermal, Wood/Wood Waste Biomass, Landfill Gas, "
           "Other Waste Biomass); municipal solid waste, pumped storage and batteries are not counted. "
           "The hex table also carries power_capacity_mw / generation_gwh aggregated per hex for "
           "convenience (identical to aggregating the plant points).", "higher"),
    Metric("port_cargo_t", "Seaport cargo (imports + exports)", "t", "trade", "extensive", "none", "points_ports",
           ["imf_portwatch"], "2025",
           "Estimated annual maritime import + export volume handled by each port (metric tonnes).",
           "IMF PortWatch daily port activity (AIS-based estimates of tonnes discharged/loaded for trade), "
           "summed over calendar year 2025 (import + export, all vessel types). Same method in both "
           "countries. PortWatch estimates trade flows, so transshipment volumes (dominant in Colombo: "
           "SLPA reports 114.6 Mt total cargo handled in 2024, column official_cargo_t) are largely "
           "not counted. Ports with zero estimated cargo dropped; US ports outside the 50 states + DC "
           "(Puerto Rico etc.) dropped.", "higher"),
    Metric("port_calls", "Seaport calls", "calls", "trade", "extensive", "none", "points_ports",
           ["imf_portwatch"], "2025", "Number of cargo-vessel port calls per year.",
           "IMF PortWatch daily 'portcalls' (container, dry bulk, general cargo, ro-ro, tanker) summed over 2025.",
           "higher"),
    Metric("port_teu", "Container throughput", "TEU", "trade", "extensive", "none", "points_ports",
           ["slpa_annual_report_2024"], "2024",
           "Container throughput of the Port of Colombo in twenty-foot equivalent units (incl. transshipment).",
           "SLPA Annual Report 2024 p. 9: Port of Colombo total (SLPA + SAGT + CICT terminals) 7,792,069 TEU "
           "in 2024. Other Sri Lankan ports: no TEU figure published (NaN). No US source available "
           "(USACE/BTS hosts unreachable), so Sri Lanka only.", "higher", ["lka"]),
    Metric("airports", "Major airports", "count", "infrastructure", "extensive", "none", "points_airports",
           ["ourairports"], "2026", "Large and medium airports (OurAirports classification).",
           "One per OurAirports airport of type large_airport or medium_airport (US: 50 states + DC).",
           "higher"),
    Metric("airport_pax", "Airport passenger boardings", "enplanements", "infrastructure", "extensive", "none",
           "points_airports", ["faa_enplanements_cy2025", "ourairports"], "2025",
           "Annual revenue passenger enplanements (boardings) at each airport.",
           "FAA CY 2025 enplanements (all airports file) joined to OurAirports large/medium airports by FAA "
           "location id (local_code), falling back to IATA code. Enplanements count boarding passengers "
           "only (about half of total passenger movements). Sri Lanka: no per-airport figure obtainable "
           "(aasl.lk / caa.lk unreachable); nationally the Civil Aviation Authority reports 4,437,156 "
           "passengers departing Sri Lanka by air in 2024 (DCS Statistical Abstract 2025 Table 7.16), "
           "so the metric is US-only and the Sri Lankan points carry NaN.", "higher", ["usa"]),
    Metric("military_sites", "Military installations", "count", "military", "extensive", "none",
           "points_military", ["dod_mirta", "wikidata"], "2025 (USA) / 2026 (LKA)",
           "Military bases, stations, ranges and training areas (one per site).",
           "USA: DoD MIRTA points with operational status 'act' or 'semi' in the 50 states + DC (all "
           "components incl. Reserve and National Guard). LKA: Wikidata items in Sri Lanka that are "
           "military installations / bases / airbases / naval bases, or that are operated by the Sri Lanka "
           "Army, Navy, Air Force or Armed Forces, with coordinates; historic forts, fortresses, citadels, "
           "universities, units, accidents and the civil Bandaranaike International Airport are excluded. "
           "Coverage differs strongly (an official register vs. crowd-sourced data), so compare "
           "with care.", "neutral"),
    Metric("spaceports", "Spaceports and launch sites", "count", "infrastructure", "extensive", "none",
           "points_spaceports", ["wikidata"], "2026",
           "Spaceports / launch sites (one per site).",
           "Wikidata instances of spaceport (Q194188) and subclasses located in the US with coordinates; "
           "items with an end/dissolution date or typed 'proposed entity' are dropped, as are heritage-listed "
           "sites whose only type is spaceport (historic launch sites such as Goddard's 1926 site). "
           "Sri Lanka has none (empty table).", "higher", ["usa"]),
    Metric("industrial_zones", "Export processing zones & industrial parks", "count", "production", "extensive",
           "none", "points_industrial_zones", ["osm_nominatim"], "2026",
           "Named export processing zones, export promotion zones, industrial parks and industrial zones.",
           "OpenStreetMap features found with the Nominatim search API (queries for 'Export Processing Zone', "
           "'Export Promotion Zone', 'Industrial Park', 'Industrial Zone' and named BOI zones, countrycodes=lk), "
           "kept when they are landuse=industrial, boundary=special_economic_zone or a place named as an "
           "export processing zone; duplicates (several OSM ways of one zone) are merged at their mean "
           "centroid. The Board of Investment's own list (boi.lk) was unreachable.", "higher", ["lka"]),
]

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _states() -> pd.DataFrame:
    a = load_admin("usa", "adm1")
    return pd.DataFrame({"fips": a.admin_id, "abbr": a["abbr"]})


def _attach_admin(df: pd.DataFrame, country: str) -> pd.DataFrame:
    """Add adm1/adm2 of the snapped hex (dominant unit) for convenience."""
    hx = load_hexes(country).set_index("h3")
    cells = util.snap_points(df["lat"].to_numpy(float), df["lon"].to_numpy(float), country)
    df = df.copy()
    for lvl in ("adm1", "adm2"):
        df[lvl] = [hx.at[c, lvl] if c is not None and c in hx.index else None for c in cells]
    return df


def _wkt(s: str) -> tuple[float, float]:
    return util.parse_wkt_point(s)


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------------------------
# Power plants
# ---------------------------------------------------------------------------------------------

# CEB Annual Report 2023, Tables G1-G3 (report pp. 22-23, PDF pp. 24-25): CEB-owned stations,
# installed capacity (MW) and 2023 net generation (GWh).  Transcribed verbatim; `gppd` lists the
# GPPD v1.3.0 entries whose location is used (and which the CEB figure replaces).
CEB_2023 = [
    # name, gppd names, capacity_mw, generation_gwh_2023, fuel, note
    ("Victoria", ["Victoria"], 210.0, 627.5, "Hydro", "Mahaweli complex"),
    ("Kotmale", ["Kotmale"], 201.0, 415.3, "Hydro", "Mahaweli complex"),
    ("Upper Kotmale", ["Upper Kotmale"], 150.0, 421.0, "Hydro", "Mahaweli complex"),
    ("Randenigala", ["Randenigala"], 122.6, 321.7, "Hydro", "Mahaweli complex"),
    ("Rantembe", ["Rantembe"], 50.0, 161.7, "Hydro", "Mahaweli complex"),
    ("Ukuwela", ["Ukuwela"], 40.0, 193.4, "Hydro", "Mahaweli complex"),
    ("Bowatenna", ["Bowatenna"], 40.0, 92.8, "Hydro", "Mahaweli complex"),
    ("Nilambe", ["Nilambe"], 3.2, 9.7, "Hydro", "Mahaweli complex (CEB: Nillambe)"),
    ("Thambapavani Wind Farm (Mannar)", [], 103.5, 391.4, "Wind", "location from Wikidata"),
    ("Laxapana (Old + New Laxapana)", ["Old Laxapana", "New Laxapana"], 153.8, 776.4, "Hydro", "Laxapana complex"),
    ("Polpitiya", ["Polpitiya"], 90.0, 430.1, "Hydro", "Laxapana complex"),
    ("Canyon", ["Canyon"], 60.0, 153.0, "Hydro", "Laxapana complex"),
    ("Wimalasurendra", ["Wimalasurendra"], 50.0, 126.7, "Hydro", "Laxapana complex"),
    ("Broadlands", ["Broadlands"], 35.0, 104.3, "Hydro", "Laxapana complex"),
    ("Samanalawewa", ["Samanala"], 120.0, 375.2, "Hydro", "Samanala complex"),
    ("Kukule Ganga", ["Kukule Ganga"], 75.0, 335.5, "Hydro", "Samanala complex"),
    ("Udawalawe", ["Udawalawe"], 4.0, 9.3, "Hydro", "Samanala complex"),
    ("Inginiyagala", ["Inginiyagala"], 8.8, 19.4, "Hydro", "Samanala complex"),
    ("Sapugaskanda (A + B)", ["Sapugaskanda"], 160.0, 571.9, "Oil", "Table G2: A 80 MW 205.7 GWh + B 80 MW 366.2 GWh"),
    ("Kelanitissa complex (KCCP, KCCP2, GT7, Frame V GTs)", ["CEB Kelantitissa", "Sojitz Kelanitissa"], 517.0, 953.5,
     "Oil", "Table G2: KCCP 165 MW 701.1 GWh, KCCP2 157 MW 167.5 GWh, GT7 115 MW 84.0 GWh, Frame V 80 MW 0.9 GWh"),
    ("Uthuru Janani", ["Uthuru Janani"], 24.0, 99.2, "Oil", ""),
    ("Barge Power Station (Colombo Port)", ["Colombo Port"], 60.0, 304.0, "Oil", ""),
    ("Lakvijaya (Norochcholai)", ["Lakvijaya"], 900.0, 4645.5, "Coal", "Table G3: net generation units 1-3 1593.4 + 1875.5 + 1176.6 GWh"),
]
# Commissioned after the 2023 report, from the same report (p. 209, Table 01): capacity only.
CEB_LATER = [("Uma Oya", 120.0, "Hydro", 2024, "CEB Annual Report 2023 p.209 (Table 01): commissioned April 2024")]
# GPPD independent oil plants not among the 2 IPP oil stations of the CEB Statistical Digest 2025.
GPPD_RETIRED_LKA = {"Asia Power Sapugaskanda", "Northern Power", "Lakdhanavi"}

EIA_TECH_FUEL = {
    "Conventional Steam Coal": "Coal", "Coal Integrated Gasification Combined Cycle": "Coal",
    "Natural Gas Fired Combined Cycle": "Gas", "Natural Gas Fired Combustion Turbine": "Gas",
    "Natural Gas Steam Turbine": "Gas", "Natural Gas Internal Combustion Engine": "Gas",
    "Natural Gas with Compressed Air Storage": "Gas", "Other Natural Gas": "Gas", "Other Gases": "Gas",
    "Petroleum Liquids": "Oil", "Petroleum Coke": "Oil",
    "Nuclear": "Nuclear",
    "Conventional Hydroelectric": "Hydro", "Hydroelectric Pumped Storage": "Storage",
    "Onshore Wind Turbine": "Wind", "Offshore Wind Turbine": "Wind",
    "Solar Photovoltaic": "Solar", "Solar Thermal with Energy Storage": "Solar",
    "Solar Thermal without Energy Storage": "Solar",
    "Geothermal": "Geothermal",
    "Wood/Wood Waste Biomass": "Biomass", "Landfill Gas": "Biomass", "Other Waste Biomass": "Biomass",
    "Municipal Solid Waste": "Waste",
    "Batteries": "Storage", "Flywheels": "Storage",
    "All Other": "Other",
}


def _wikidata_lka_plant_coords() -> dict[str, tuple[float, float]]:
    # label-based lookup (the query resolves the QIDs)
    q = """SELECT ?item ?itemLabel ?coord WHERE {
      VALUES ?lab { "Thambapavani Wind Farm"@en "Uma Oya Hydropower Complex"@en }
      ?item rdfs:label ?lab ; wdt:P625 ?coord .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""
    d = util.sparql(q, "infra_lka_plant_coords")
    return {r.itemLabel: _wkt(r.coord) + (_qid(r.item),) for r in d.itertuples()}


def power_plants_lka() -> pd.DataFrame:
    p = fetch(GPPD_URL, "wri_gppd", "infra/gppd")
    g = pd.read_csv(p, low_memory=False)
    g = g[g.country == "LKA"].copy()
    used: set[str] = set()
    rows = []
    wd = _wikidata_lka_plant_coords()
    for name, gnames, cap, gen, fuel, note in CEB_2023:
        hit = g[g.name.isin(gnames)]
        if len(hit):
            lat, lon = float(hit.latitude.mean()), float(hit.longitude.mean())
            gid = "|".join(hit.gppd_idnr)
            cy = hit.commissioning_year.dropna()
            cyear = float(cy.min()) if len(cy) else np.nan
            loc_src = "wri_gppd"
        else:
            key = "Thambapavani Wind Farm" if "Thambapavani" in name else name
            lat, lon, qid = wd[key]
            gid, cyear, loc_src = qid, np.nan, "wikidata"
        used |= set(gnames)
        rows.append(dict(id=f"lk_ceb_{len(rows):02d}", name=name, lat=lat, lon=lon, primary_fuel=fuel,
                         power_capacity_mw=cap, generation_gwh=gen, generation_year=2023,
                         generation_basis="reported net generation (CEB Annual Report 2023)",
                         commissioning_year=cyear, owner="Ceylon Electricity Board",
                         capacity_source="ceb_annual_report_2023", location_source=loc_src,
                         source_ref=gid, note=note))
    for name, cap, fuel, year, note in CEB_LATER:
        lat, lon, qid = wd["Uma Oya Hydropower Complex"]
        rows.append(dict(id=f"lk_ceb_{len(rows):02d}", name=name, lat=lat, lon=lon, primary_fuel=fuel,
                         power_capacity_mw=cap, generation_gwh=np.nan, generation_year=np.nan,
                         generation_basis="not published", commissioning_year=year,
                         owner="Ceylon Electricity Board", capacity_source="ceb_annual_report_2023",
                         location_source="wikidata", source_ref=qid, note=note))
    rest = g[~g.name.isin(used) & ~g.name.isin(GPPD_RETIRED_LKA)]
    for r in rest.itertuples():
        rows.append(dict(id=r.gppd_idnr, name=r.name, lat=r.latitude, lon=r.longitude, primary_fuel=r.primary_fuel,
                         power_capacity_mw=r.capacity_mw, generation_gwh=r.estimated_generation_gwh_2017,
                         generation_year=2017,
                         generation_basis=f"estimated (GPPD v1.3.0 {r.estimated_generation_note_2017})",
                         commissioning_year=r.commissioning_year, owner=r.owner if isinstance(r.owner, str) else "",
                         capacity_source="wri_gppd", location_source="wri_gppd", source_ref=r.gppd_idnr,
                         note=f"GPPD source: {r.source}"))
    df = pd.DataFrame(rows)
    df["renewable_capacity_mw"] = np.where(df.primary_fuel.isin(RENEWABLE_FUELS), df.power_capacity_mw, 0.0)
    return df


def _eia_states() -> set[str]:
    return set(_states().abbr)


def power_plants_usa() -> pd.DataFrame:
    z = fetch(EIA860_URL, "eia_860", "usa/eia860")
    d = unzip(z)
    plant = pd.read_excel(d / "2___Plant_Y2025.xlsx", sheet_name=0, header=1)
    gen = pd.read_excel(d / "3_1_Generator_Y2025.xlsx", sheet_name="Operable", header=1)
    states = _eia_states()
    gen = gen[gen.Status.isin(["OP", "SB"]) & gen.State.isin(states)].copy()
    gen["mw"] = pd.to_numeric(gen["Nameplate Capacity (MW)"], errors="coerce").fillna(0.0)
    gen["fuel"] = gen.Technology.map(EIA_TECH_FUEL).fillna("Other")
    unknown = sorted(set(gen.Technology) - set(EIA_TECH_FUEL))
    if unknown:
        print("  infra: unmapped EIA technologies ->Other:", unknown)
    gen["ren_mw"] = np.where(gen.fuel.isin(RENEWABLE_FUELS), gen.mw, 0.0)
    gen["oy"] = pd.to_numeric(gen["Operating Year"], errors="coerce")
    by = gen.groupby("Plant Code")
    cap = by.mw.sum()
    ren = by.ren_mw.sum()
    fuelcap = gen.groupby(["Plant Code", "fuel"]).mw.sum().reset_index().sort_values(
        ["Plant Code", "mw"], ascending=[True, False]).drop_duplicates("Plant Code").set_index("Plant Code").fuel
    techcap = gen.groupby(["Plant Code", "Technology"]).mw.sum().reset_index().sort_values(
        ["Plant Code", "mw"], ascending=[True, False]).drop_duplicates("Plant Code").set_index("Plant Code").Technology
    w = gen.dropna(subset=["oy"])
    cy = (w.oy * w.mw).groupby(w["Plant Code"]).sum() / w.mw.groupby(w["Plant Code"]).sum().replace(0, np.nan)
    n_gen = by.size()

    z2 = fetch(EIA923_URL, "eia_923", "usa/eia923")
    d2 = unzip(z2)
    f923 = next(d2.rglob("EIA923_Schedules_2_3_4_5_M_12_2025_Final.xlsx"))
    p1 = pd.read_excel(f923, sheet_name="Page 1 Generation and Fuel Data", header=5)
    p1.columns = [str(c).replace("\n", " ") for c in p1.columns]
    p1 = p1[(p1["Plant Id"] != 99999) & p1["Plant State"].isin(states)]
    ng = pd.to_numeric(p1["Net Generation (Megawatthours)"], errors="coerce").groupby(p1["Plant Id"]).sum() / 1000.0

    pl = plant[plant["Plant Code"].isin(cap.index)].copy()
    out = pd.DataFrame({
        "id": "eia_" + pl["Plant Code"].astype(int).astype(str),
        "name": pl["Plant Name"],
        "lat": pd.to_numeric(pl.Latitude, errors="coerce"),
        "lon": pd.to_numeric(pl.Longitude, errors="coerce"),
        "eia_plant_code": pl["Plant Code"].astype(int),
        "state": pl.State,
        "county": pl.County,
        "owner": pl["Utility Name"],
        "sector": pl["Sector Name"],
    })
    code = pl["Plant Code"].to_numpy()
    out["primary_fuel"] = fuelcap.reindex(code).to_numpy()
    out["primary_technology"] = techcap.reindex(code).to_numpy()
    out["power_capacity_mw"] = cap.reindex(code).to_numpy()
    out["renewable_capacity_mw"] = ren.reindex(code).to_numpy()
    out["n_generators"] = n_gen.reindex(code).to_numpy()
    out["commissioning_year"] = np.round(cy.reindex(code).to_numpy(), 1)
    gw = ng.reindex(code).to_numpy()
    out["generation_gwh_net_reported"] = gw
    out["generation_gwh"] = np.clip(gw, 0, None)
    out["generation_year"] = np.where(np.isfinite(gw), 2025, np.nan)
    out["generation_basis"] = np.where(np.isfinite(gw), "reported net generation (EIA-923 2025)", "not reported")
    out["capacity_source"] = "eia_860"
    out["location_source"] = "eia_860"
    out = out.dropna(subset=["lat", "lon"])
    out = out[out.power_capacity_mw > 0]
    return out.reset_index(drop=True)


def build_power(country: str) -> pd.DataFrame:
    df = power_plants_lka() if country == "lka" else power_plants_usa()
    df["capacity_mw"] = df["power_capacity_mw"]
    df["kind"] = df["primary_fuel"]
    df["plants"] = 1
    df = _attach_admin(df, country)
    lead = ["id", "name", "lat", "lon", "kind", "power_capacity_mw", "capacity_mw", "primary_fuel",
            "generation_gwh", "generation_year", "generation_basis", "renewable_capacity_mw",
            "commissioning_year", "owner"]
    df = df[lead + [c for c in df.columns if c not in lead]]
    df = df.sort_values("power_capacity_mw", ascending=False).reset_index(drop=True)
    util.write_points(country, DOMAIN, "power_plants", df)
    # hex aggregation
    cells = util.snap_points(df.lat.to_numpy(float), df.lon.to_numpy(float), country)
    h = df.assign(h3=cells).dropna(subset=["h3"])
    hx = h.groupby("h3").agg(power_capacity_mw=("power_capacity_mw", "sum"),
                             generation_gwh=("generation_gwh", lambda s: s.sum(min_count=1)),
                             renewable_capacity_mw=("renewable_capacity_mw", "sum"),
                             plants=("plants", "sum")).reset_index()
    util.write_hex(country, DOMAIN, hx)
    print(f"  power plants: {len(df)} plants, {df.power_capacity_mw.sum():,.0f} MW, "
          f"{df.generation_gwh.sum():,.0f} GWh, renewable {df.renewable_capacity_mw.sum():,.0f} MW; "
          f"{len(hx)} hexes")
    return df


# ---------------------------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------------------------

# SLPA Annual Report 2024 (pp. 9-11) and DCS Statistical Abstract 2025 Table 7.14 (source SLPA):
# official 2024 figures per port (transcribed).  official_cargo_t = total cargo handled incl.
# transshipment (Colombo: DCS Table 7.14 'Total cargo handled' 114,594 thousand t; Trincomalee
# SLPA AR p.11 2,613 kt; Hambantota SLPA AR p.11 3,032.15 kt; Galle SLPA AR p.11 0.4 kt).
SLPA_2024 = {
    "Colombo": dict(port_teu=7_792_069, ship_arrivals=3968, official_cargo_t=114_594_000),
    "Hambantota": dict(port_teu=np.nan, ship_arrivals=472, official_cargo_t=3_032_150),
    "Trincomalee": dict(port_teu=np.nan, ship_arrivals=116, official_cargo_t=2_613_000),
    "Galle": dict(port_teu=np.nan, ship_arrivals=6, official_cargo_t=400),
}


def _portwatch_db() -> pd.DataFrame:
    p = fetch(PW_PORTS_DB, "imf_portwatch", "infra/portwatch", "portwatch_ports_lka_usa.json",
              params={"where": "ISO3 IN ('LKA','USA')", "outFields": "*", "f": "json",
                      "resultRecordCount": 2000, "returnGeometry": "false"})
    js = json.loads(p.read_text())
    return pd.DataFrame([f["attributes"] for f in js["features"]])


def build_ports(country: str, year: int = 2025) -> pd.DataFrame:
    p = fetch(PW_CSV[country], "imf_portwatch", "infra/portwatch")
    d = pd.read_csv(p)
    full = d.groupby("year").date.nunique()
    assert full.get(year, 0) >= 365, f"PortWatch {country}: year {year} incomplete"
    y = d[d.year == year]
    agg = y.groupby("portid").agg(port_calls=("portcalls", "sum"), import_t=("import", "sum"),
                                  export_t=("export", "sum"), days=("date", "nunique")).reset_index()
    agg["port_cargo_t"] = agg.import_t + agg.export_t
    db = _portwatch_db().set_index("portid")
    agg = agg.join(db[["portname", "fullname", "lat", "lon", "LOCODE", "industry_top1",
                       "share_country_maritime_import", "share_country_maritime_export"]], on="portid")
    agg = agg[agg.port_cargo_t > 0].dropna(subset=["lat", "lon"]).copy()
    out = pd.DataFrame({
        "id": agg.portid, "name": agg.portname, "lat": agg.lat, "lon": agg.lon, "kind": "seaport",
        "port_cargo_t": agg.port_cargo_t, "port_calls": agg.port_calls,
        "import_t": agg.import_t, "export_t": agg.export_t, "locode": agg.LOCODE,
        "industry_top1": agg.industry_top1, "data_year": year,
    })
    if country == "lka":
        for c in ("port_teu", "ship_arrivals", "official_cargo_t"):
            out[c] = [SLPA_2024.get(n, {}).get(c, np.nan) for n in out.name]
        out["official_year"] = 2024
    else:
        # keep the 50 states + DC: ports that snap to the US grid (drops Puerto Rico, Guam, USVI ...)
        cells = util.snap_points(out.lat.to_numpy(float), out.lon.to_numpy(float), country)
        keep = np.array([c is not None for c in cells])
        dropped = out.loc[~keep, "name"].tolist()
        if dropped:
            print(f"  ports outside the 50 states + DC dropped: {dropped}")
        out = out[keep]
    out = _attach_admin(out, country)
    if country == "usa":
        out = out[out.adm1.isin(US_STATE_FIPS)]
    out = out.sort_values("port_cargo_t", ascending=False).reset_index(drop=True)
    util.write_points(country, DOMAIN, "ports", out)
    print(f"  ports: {len(out)} ports, {out.port_cargo_t.sum() / 1e6:,.1f} Mt cargo {year}, "
          f"{out.port_calls.sum():,.0f} calls")
    return out


# ---------------------------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------------------------


def _faa() -> pd.DataFrame:
    p = fetch(FAA_URL, "faa_enplanements_cy2025", "usa/faa")
    f = pd.read_excel(p, header=0)
    f = f[f.Locid.notna() & f["CY 25 Enplanements"].notna() & f.ST.notna()].copy()
    f["Locid"] = f.Locid.astype(str).str.strip()
    f["pax"] = pd.to_numeric(f["CY 25 Enplanements"], errors="coerce")
    return f


def build_airports(country: str) -> pd.DataFrame:
    p = fetch(OURAIRPORTS_URL, "ourairports", "infra/ourairports")
    a = pd.read_csv(p, keep_default_na=False, na_values=[""], low_memory=False)
    iso2 = {"lka": "LK", "usa": "US"}[country]
    a = a[(a.iso_country == iso2) & a.type.isin(["large_airport", "medium_airport"])].copy()
    if country == "usa":
        st = set(_states().abbr)
        a = a[a.iso_region.str[3:].isin(st)]
    out = pd.DataFrame({
        "id": a.ident, "name": a["name"], "lat": a.latitude_deg.astype(float), "lon": a.longitude_deg.astype(float),
        "kind": a.type, "iata": a.iata_code, "icao": a.icao_code, "faa_lid": a.local_code,
        "municipality": a.municipality, "scheduled_service": a.scheduled_service, "airports": 1,
    })
    out["airport_pax"] = np.nan
    if country == "usa":
        f = _faa()
        by_lid = f.groupby("Locid").pax.sum()
        pax = out.faa_lid.map(by_lid)
        pax = pax.fillna(out.iata.map(by_lid))
        out["airport_pax"] = pax
        out["pax_year"] = 2025
        cov = pax.sum() / f.pax.sum()
        print(f"  FAA enplanements matched to large/medium airports: {pax.sum() / 1e6:,.1f} M "
              f"of {f.pax.sum() / 1e6:,.1f} M ({cov:.1%})")
    out = _attach_admin(out, country)
    out = out.sort_values(["airport_pax", "kind"], ascending=[False, True]).reset_index(drop=True)
    util.write_points(country, DOMAIN, "airports", out)
    print(f"  airports: {len(out)} ({(out.kind == 'large_airport').sum()} large)")
    return out


# ---------------------------------------------------------------------------------------------
# Military
# ---------------------------------------------------------------------------------------------

MIRTA_BRANCH = {
    "usa": "Army", "usar": "Army Reserve", "armyNationalGuard": "Army National Guard",
    "usaf": "Air Force", "afr": "Air Force Reserve", "airNationalGuard": "Air National Guard",
    "usn": "Navy", "usnr": "Navy Reserve", "usmc": "Marine Corps", "usmcr": "Marine Corps Reserve",
    "whs": "Washington Headquarters Services", "other": "Other DoD",
}


def military_usa() -> pd.DataFrame:
    p = fetch(MIRTA_URL, "dod_mirta", "usa/mirta", "mirta_points.geojson",
              params={"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                      "resultRecordCount": 2000})
    js = json.loads(p.read_text())
    rows = []
    for f in js["features"]:
        pr = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        rows.append(dict(pr, lat=lat, lon=lon))
    m = pd.DataFrame(rows)
    st = set(_states().abbr)
    m = m[m.siteOperationalStatus.isin(["act", "semi"]) & m.stateNameCode.isin(st)]
    br = m.siteReportingComponent.map(MIRTA_BRANCH).fillna(m.siteReportingComponent)
    return pd.DataFrame({
        "id": "mirta_" + m.OBJECTID.astype(str), "name": m.siteName, "lat": m.lat, "lon": m.lon,
        "kind": br, "branch": br, "component": m.siteReportingComponent, "joint_base": m.isJointBase == "Yes",
        "status": m.siteOperationalStatus, "state": m.stateNameCode, "military_sites": 1,
        "source": "dod_mirta",
    })


LKA_FORCES = {"Q2579178": "Sri Lanka Armed Forces", "Q2607844": "Sri Lanka Army",
              "Q3274075": "Sri Lanka Navy", "Q600027": "Sri Lanka Air Force", "Q3274402": "Sri Lanka Coast Guard"}
MIL_EXCLUDE_TYPES = {"fort", "fortress", "citadel", "castle", "geographical feature", "university",
                     "staff college", "aviation accident", "military unit", "archaeological site", "ruins",
                     "international airport"}


def military_lka() -> pd.DataFrame:
    forces = " ".join(f"wd:{q}" for q in LKA_FORCES)
    q1 = """SELECT ?item ?itemLabel ?coord (GROUP_CONCAT(DISTINCT ?tl; separator="; ") AS ?types)
      (GROUP_CONCAT(DISTINCT ?op; separator=" ") AS ?ops) WHERE {
      VALUES ?cls { wd:Q18691599 wd:Q245016 wd:Q695850 wd:Q1324633 }
      ?item wdt:P31/wdt:P279* ?cls ; wdt:P17 wd:Q854 ; wdt:P625 ?coord .
      FILTER NOT EXISTS { ?item wdt:P576 [] }
      OPTIONAL { ?item wdt:P137|wdt:P241 ?op }
      OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label ?tl . FILTER(lang(?tl)='en') }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?item ?itemLabel ?coord"""
    q2 = """SELECT ?item ?itemLabel ?coord (GROUP_CONCAT(DISTINCT ?tl; separator="; ") AS ?types)
      (GROUP_CONCAT(DISTINCT ?op; separator=" ") AS ?ops) WHERE {
      VALUES ?op { %s }
      ?item wdt:P137|wdt:P241|wdt:P127 ?op ; wdt:P625 ?coord .
      FILTER NOT EXISTS { ?item wdt:P576 [] }
      OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label ?tl . FILTER(lang(?tl)='en') }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?item ?itemLabel ?coord""" % forces
    d = pd.concat([util.sparql(q1, "infra_lka_military_cls"), util.sparql(q2, "infra_lka_military_ops")],
                  ignore_index=True)
    d["qid"] = d["item"].map(_qid)
    d["types"] = d["types"].fillna("")
    d["ops"] = d["ops"].fillna("")
    d = d.groupby(["qid", "itemLabel"], as_index=False).agg(
        coord=("coord", "first"), types=("types", lambda s: "; ".join(sorted({t for x in s for t in x.split("; ") if t}))),
        ops=("ops", lambda s: " ".join(sorted({o for x in s for o in x.split() if o}))))
    rows = []
    for r in d.itertuples():
        types = {t.strip() for t in r.types.split(";") if t.strip()}
        lab = r.itemLabel.lower()
        if types & MIL_EXCLUDE_TYPES or "shootdown" in lab or "crash" in lab:
            continue
        ops = [LKA_FORCES.get(_qid(o)) for o in r.ops.split()]
        ops = [o for o in ops if o]
        branch = ops[0] if len(ops) == 1 else ("; ".join(ops) if ops else "")
        if "airbase" in types or "Sri Lanka Air Force" in ops or lab.startswith("slaf"):
            kind = "air base"
            branch = branch or "Sri Lanka Air Force"
        elif "naval base" in types or "Sri Lanka Navy" in ops:
            kind = "naval base"
        elif "barracks" in types:
            kind = "barracks"
        else:
            kind = "military base"
        lat, lon = _wkt(r.coord)
        rows.append(dict(id=r.qid, name=r.itemLabel, lat=lat, lon=lon, kind=kind, branch=branch,
                         wikidata_types=r.types, wikidata=r.qid, military_sites=1, source="wikidata"))
    return pd.DataFrame(rows)


def build_military(country: str) -> pd.DataFrame:
    df = military_lka() if country == "lka" else military_usa()
    df = _attach_admin(df, country)
    df = df.sort_values(["kind", "name"]).reset_index(drop=True)
    util.write_points(country, DOMAIN, "military", df)
    print(f"  military: {len(df)} sites; {df.kind.value_counts().to_dict()}")
    return df


# ---------------------------------------------------------------------------------------------
# Spaceports
# ---------------------------------------------------------------------------------------------


def build_spaceports(country: str) -> pd.DataFrame:
    cols = ["id", "name", "lat", "lon", "kind", "operator", "wikidata_types", "inception", "heritage_listed",
            "wikidata", "spaceports"]
    cq = {"lka": "Q854", "usa": "Q30"}[country]
    q = """SELECT ?item ?itemLabel ?coord (GROUP_CONCAT(DISTINCT ?tl; separator="; ") AS ?types)
      (SAMPLE(?opl) AS ?operator) (SAMPLE(?her) AS ?heritage) (SAMPLE(?end) AS ?ended) (SAMPLE(?inc) AS ?inception)
      WHERE {
      ?item wdt:P31/wdt:P279* wd:Q194188 ; wdt:P17 wd:%s ; wdt:P625 ?coord .
      OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label ?tl . FILTER(lang(?tl)='en') }
      OPTIONAL { ?item wdt:P137 ?op . ?op rdfs:label ?opl . FILTER(lang(?opl)='en') }
      OPTIONAL { ?item wdt:P1435 ?her }
      OPTIONAL { ?item wdt:P576|wdt:P582|wdt:P3999 ?end }
      OPTIONAL { ?item wdt:P571|wdt:P1619 ?inc }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?item ?itemLabel ?coord""" % cq
    d = util.sparql(q, f"infra_spaceports_{country}")
    rows = []
    if len(d):
        for c in ("ended", "heritage", "operator", "inception"):
            if c not in d.columns:
                d[c] = None
        d = d.drop_duplicates("item")
        for r in d.itertuples():
            types = {t.strip() for t in (r.types or "").split(";") if t.strip()}
            if isinstance(r.ended, str) or "proposed entity" in types:
                continue
            if isinstance(r.heritage, str) and types == {"spaceport"}:
                continue
            lat, lon = _wkt(r.coord)
            kind = "launch site" if "launch site" in types else "spaceport"
            rows.append(dict(id=_qid(r.item), name=r.itemLabel, lat=lat, lon=lon, kind=kind,
                             operator=r.operator if isinstance(r.operator, str) else "",
                             wikidata_types="; ".join(sorted(types)),
                             inception=(r.inception or "")[:4] if isinstance(r.inception, str) else "",
                             heritage_listed=isinstance(r.heritage, str), wikidata=_qid(r.item), spaceports=1))
    df = pd.DataFrame(rows, columns=cols)
    # US only: keep sites inside the 50 states + DC grid
    if len(df):
        cells = util.snap_points(df.lat.to_numpy(float), df.lon.to_numpy(float), country)
        df = df[[c is not None for c in cells]]
    util.write_points(country, DOMAIN, "spaceports", df.reset_index(drop=True))
    print(f"  spaceports: {len(df)}")
    return df


# ---------------------------------------------------------------------------------------------
# Industrial zones (Sri Lanka)
# ---------------------------------------------------------------------------------------------

NOMINATIM_QUERIES = [
    "Export Processing Zone", "Export Promotion Zone", "Industrial Park", "Industrial Zone",
    "Industrial Estate", "Mawathagama Industrial Zone", "Mirijjawila Export Promotion Zone",
    "Malwatta Export Processing Zone", "Kandy Industrial Park", "Arabokka Industrial Zone",
    "Kilinochchi Industrial Zone", "Achchuveli Industrial Zone", "Pannala Export Processing Zone",
]
IZ_PATTERNS = ("export processing zone", "export promotion zone", "industrial park", "industrial zone",
               "industrial estate", "investment promotion zone")


def _nominatim(q: str) -> list[dict]:
    name = "nominatim_lk_" + "".join(ch if ch.isalnum() else "_" for ch in q.lower()) + ".json"
    dest = CACHE / "lka" / "osm" / name
    cached = dest.exists() and dest.stat().st_size > 0
    try:
        p = fetch(NOMINATIM_URL, "osm_nominatim", "lka/osm", name,
                  params={"q": q, "countrycodes": "lk", "format": "jsonv2", "limit": 50})
        txt = p.read_text().strip()
        res = json.loads(txt) if txt else []
    except Exception as e:  # noqa: BLE001
        print(f"  nominatim '{q}' failed: {e}")
        res = []
        if dest.exists():
            dest.unlink()
    if not cached:
        time.sleep(1.2)  # Nominatim usage policy: max 1 request / second
    return res


def build_industrial_zones(country: str) -> pd.DataFrame:
    cols = ["id", "name", "lat", "lon", "kind", "osm_ids", "osm_class", "industrial_zones", "source"]
    if country != "lka":
        df = pd.DataFrame(columns=cols)
        util.write_points(country, DOMAIN, "industrial_zones", df)
        return df
    hits = []
    for q in NOMINATIM_QUERIES:
        for r in _nominatim(q):
            nm = (r.get("name") or "").strip()
            low = nm.lower()
            cat = f"{r.get('category')}={r.get('type')}"
            okcat = cat in ("landuse=industrial", "boundary=special_economic_zone") or (
                r.get("category") == "place" and "export processing zone" in low)
            if not okcat or not any(p in low for p in IZ_PATTERNS) or low in IZ_PATTERNS:
                continue
            key = low.replace("(boi)", "").replace("boi -", "").strip()
            hits.append(dict(key=key, name=nm.replace(" (BOI)", ""), lat=float(r["lat"]), lon=float(r["lon"]),
                             osm=f"{r['osm_type']}/{r['osm_id']}", cls=cat))
    h = pd.DataFrame(hits).drop_duplicates("osm")
    g = h.groupby("key").agg(name=("name", "first"), lat=("lat", "mean"), lon=("lon", "mean"),
                             osm_ids=("osm", lambda s: " ".join(sorted(s))),
                             osm_class=("cls", lambda s: " ".join(sorted(set(s))))).reset_index()

    def kind(n: str) -> str:
        n = n.lower()
        if "export processing" in n or "export promotion" in n:
            return "export processing zone"
        if "park" in n:
            return "industrial park"
        if "estate" in n:
            return "industrial estate"
        return "industrial zone"

    df = pd.DataFrame({"id": ["osm_" + o.split()[0].replace("/", "_") for o in g.osm_ids], "name": g.name,
                       "lat": g.lat, "lon": g.lon, "kind": [kind(n) for n in g.name], "osm_ids": g.osm_ids,
                       "osm_class": g.osm_class, "industrial_zones": 1, "source": "osm_nominatim"})
    df = _attach_admin(df, country)
    df = df.sort_values("name").reset_index(drop=True)
    util.write_points(country, DOMAIN, "industrial_zones", df)
    print(f"  industrial zones: {len(df)}: {', '.join(df.name)}")
    return df


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def _report(country: str, results: dict) -> None:
    """National sanity checks against published totals; written to report.json."""
    rep: dict = {}
    pw = results["power"]
    rep["power_capacity_mw"] = float(pw.power_capacity_mw.sum())
    rep["generation_gwh"] = float(pw.generation_gwh.sum())
    rep["renewable_capacity_mw"] = float(pw.renewable_capacity_mw.sum())
    rep["plants"] = int(len(pw))
    if country == "lka":
        # CEB Statistical Digest 2025 p.2-3 (transcribed): installed capacity excl. rooftop solar 4,814 MW
        # (2025), 4,651 MW (2024); net generation incl. rooftop 17,812 GWh (2025).
        rep["published"] = {"installed_capacity_mw_2025_excl_rooftop": 4814,
                            "net_generation_gwh_2025_incl_rooftop": 17812,
                            "ceb_owned_capacity_mw_2023": 3218,
                            "source": "CEB Statistical Digest 2025 p.2-3; CEB Annual Report 2023 p.22"}
        rep["capacity_coverage_vs_2025"] = rep["power_capacity_mw"] / 4814
    else:
        rep["published"] = {"eia923_net_generation_twh_2025_all_plants": "4412.5 (sum of Page 1, excl. state increments)"}
    v, y = util.wb_value("EG.ELC.RNEW.ZS", country.upper())
    rep["wb_renewable_output_share_pct"] = {"value": v, "year": y}
    pt = results["ports"]
    rep["port_cargo_t"] = float(pt.port_cargo_t.sum())
    rep["ports"] = int(len(pt))
    ap = results["airports"]
    rep["airports"] = int(len(ap))
    rep["airport_pax"] = float(np.nansum(ap.airport_pax))
    rep["military_sites"] = int(len(results["military"]))
    rep["spaceports"] = int(len(results["spaceports"]))
    rep["industrial_zones"] = int(len(results["industrial_zones"]))
    (interim_dir(country, DOMAIN) / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print("  report:", json.dumps(rep, default=str))


def _fetch_documents(country: str) -> None:
    """Cache the documents whose tables are transcribed above (provenance sidecars)."""
    if country != "lka":
        return
    for url, sid, name in [
        (CEB_AR_URL, "ceb_annual_report_2023", "ceb_annual_report_2023_en.pdf"),
        (CEB_SD_URL, "ceb_statistical_digest_2025", "ceb_statistical_digest_2025.pdf"),
        (SLPA_AR_URL, "slpa_annual_report_2024", "slpa_annual_report_2024.pdf"),
        (DCS_714_URL, "dcs_abstract_2025_transport", "abstract2025_7.14.pdf"),
        (DCS_716_URL, "dcs_abstract_2025_transport", "abstract2025_7.16.pdf"),
    ]:
        sub = {"ceb": "lka/ceb", "slp": "lka/slpa", "dcs": "lka/dcs_abstract2025"}[sid[:3]]
        try:
            fetch(url, sid, sub, name)
        except Exception as e:  # noqa: BLE001 - transcribed values remain valid without the cache
            print(f"  (could not cache {name}: {e})")


def build(country: str) -> None:
    print(f"== infra / {country}")
    out = interim_dir(country, DOMAIN)
    _fetch_documents(country)
    results = {
        "power": build_power(country),
        "ports": build_ports(country),
        "airports": build_airports(country),
        "military": build_military(country),
        "spaceports": build_spaceports(country),
        "industrial_zones": build_industrial_zones(country),
    }
    _report(country, results)
    write_domain_meta(out, METRICS, SOURCES, country)


if __name__ == "__main__":
    import sys

    from .. import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    for cc in ccs:
        build(cc)
    sys.exit(validate.main([DOMAIN] + ccs))
