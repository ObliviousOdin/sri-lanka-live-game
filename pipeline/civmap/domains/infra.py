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
import re
import time

import numpy as np
import pandas as pd

from .. import util
from ..config import CACHE, COUNTRIES, US_STATE_FIPS, interim_dir
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
FAA_SPACEPORTS_URL = "https://www.faa.gov/space/spaceports_by_state"

SOURCES = [
    Source("wri_gppd", "Global Power Plant Database v1.3.0",
           "World Resources Institute (WRI) et al.",
           "https://datasets.wri.org/dataset/globalpowerplantdatabase", "CC BY 4.0",
           "v1.3.0 (capacity year not stated for Sri Lankan rows); estimated generation 2017", ["lka"],
           "Downloaded from the WRI GitHub release (output_database/global_power_plant_database.csv). "
           "Used for Sri Lankan plant locations, capacity of plants not owned by CEB and modelled "
           "generation 'estimated_generation_gwh_2017' (methods CAPACITY-FACTOR-V1, HYDRO-V1, SOLAR-V1-NO-AGE) "
           "where no reported figure exists. Its year_of_capacity_data field is blank for all Sri Lankan rows."),
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
           "by owner and technology for 2024-2025 (national totals; used for coverage checks, to "
           "identify retired independent oil-fired plants and to reject GPPD generation estimates that "
           "exceed the reported output of their whole IPP technology category)."),
    Source("eia_860", "Form EIA-860 detailed data, 2025 (annual file eia8602025.zip)",
           "U.S. Energy Information Administration (EIA)", "https://www.eia.gov/electricity/data/eia860/",
           "public domain", "2025", ["usa"],
           "2___Plant_Y2025.xlsx (plant locations, operator) and 3_1_Generator_Y2025.xlsx sheet 'Operable' "
           "(generator nameplate capacity, technology, status, operating year)."),
    Source("eia_923", "Form EIA-923 detailed data, 2025 Final Data",
           "U.S. Energy Information Administration (EIA)", "https://www.eia.gov/electricity/data/eia923/",
           "public domain", "2025", ["usa"],
           "EIA923_Schedules_2_3_4_5_M_12_2025_Final.xlsx, sheet 'Page 1 Generation and Fuel Data': annual "
           "net generation (MWh) by plant, prime mover and fuel."),
    Source("imf_portwatch", "IMF PortWatch - Daily Port Activity Data and Trade Estimates; PortWatch ports database",
           "International Monetary Fund (IMF) PortWatch / UN Global Platform (via HDX)",
           "https://portwatch.imf.org/", "IMF copyright and terms (https://www.imf.org/en/about/copyright-and-terms)",
           "2025", ["lka", "usa"],
           "Daily AIS-based port calls and estimated import / export volumes (metric tonnes) per port, "
           "from HDX datasets 'sri-lanka-daily-port-activity-data-and-shipment-estimates' and "
           "'united-states-of-america-daily-port-activity-data-and-shipment-estimates'. Port coordinates "
           f"from the PortWatch_ports_database ArcGIS feature service ({PW_PORTS_DB}). Methodology: IMF "
           "Working Papers 'Tracking Trade from Space' (2021) and 'Nowcasting Global Trade from Space' (2025)."),
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
           "https://davidmegginson.github.io/ourairports-data/", "public domain", "2026 (continuously updated)",
           ["lka", "usa"],
           f"{OURAIRPORTS_URL}; types large_airport and medium_airport."),
    Source("faa_enplanements_cy2025", "CY 2025 Passenger Boarding (Enplanement) Data - all airports",
           "U.S. Federal Aviation Administration (FAA), Air Carrier Activity Information System",
           "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger",
           "public domain", "2025", ["usa"],
           f"{FAA_URL} (workbook last modified 2026-09-25). Revenue passenger enplanements (boardings) per "
           "airport, CY 2025."),
    Source("faa_spaceports", "Spaceports by State", "U.S. Federal Aviation Administration, Office of Commercial Space "
           "Transportation", FAA_SPACEPORTS_URL, "public domain", "accessed 2026-11", ["usa"],
           "List of the 20 U.S. spaceports: FAA-licensed launch / reentry site operators, federal launch "
           "ranges and private exclusive-use sites, with operator, location, licence and launch type."),
    Source("dod_mirta", "Military Installations, Ranges, and Training Areas (MIRTA) - Points",
           "U.S. Department of Defense, Defense Installation Spatial Data Infrastructure (DISDI); "
           "served by Esri US Federal Data",
           "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/MIRTA_Points_A_view/FeatureServer/0",
           "public domain", "2025", ["usa"],
           f"ArcGIS feature service {MIRTA_URL}; points of the most commonly known DoD sites, "
           "installations, ranges and training areas reported in the Base Structure Report."),
    Source("wikidata", "Wikidata (SPARQL query service)", "Wikimedia Foundation / Wikidata contributors",
           "https://query.wikidata.org/", "CC0 1.0", "accessed 2026-11", ["lka", "usa"],
           "Sri Lankan military bases / air bases (class and operator queries), coordinates of the "
           "federal and private US launch sites on the FAA spaceport list (instances of spaceport Q194188), "
           "and coordinates of Sri Lankan plants missing from or misplaced in GPPD (Thambapavani wind "
           "farm, Uma Oya, Old Laxapana, Polpitiya, Samanalawewa, Inginiyagala / Gal Oya Dam)."),
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
           "points_power_plants", ["eia_860", "wri_gppd", "ceb_annual_report_2023", "ceb_statistical_digest_2025",
                                   "wikidata"],
           "USA 2025; LKA 2023 (CEB plants) / GPPD v1.3.0, capacity year not stated (others)",
           "Installed (nameplate) electric generating capacity of utility-scale power plants.",
           "USA: EIA-860 2025 generator file, sheet 'Operable', summing nameplate MW of generators with "
           "status OP (operating) or SB (standby) per plant; generators out of service (OS/OA) excluded; "
           "plants in the 50 states + DC at EIA-860 plant coordinates. LKA: CEB-owned stations from CEB "
           "Annual Report 2023 Tables G1-G3 (Old+New Laxapana reported as one 153.8 MW station; the four "
           "Kelanitissa units KCCP 165 + KCCP2 157 + GT7 115 + Frame V GTs 80 = 517 MW combined into one "
           "point; Uma Oya 120 MW added from the same report, commissioned Apr 2024); other plants from WRI "
           "GPPD v1.3.0. GPPD coordinates more than 20 km from every Wikidata power station / dam of the same name "
           "are replaced by the Wikidata coordinate (Old Laxapana, Polpitiya, Samanala), and Inginiyagala, whose "
           "GPPD point duplicates Bowatenna Dam, is placed at the Gal Oya Dam. Three small legacy independent oil plants in GPPD (Asia Power 51 MW, Northern "
           "Power 36 MW, Lakdhanavi 24 MW) are dropped because the CEB Statistical Digest 2025 lists only "
           "2 independent oil-fired stations (482 MW in 2024), which the 300 MW Yugadanavi plant already "
           "dominates. CEB's 40 x 1 MW dispersed diesel units and most small IPP mini-hydro / solar plants "
           "have no published locations and are missing (see coverage in the infra report). Rooftop "
           "solar is not included in either country.", "higher"),
    Metric("generation_gwh", "Electricity generation", "GWh", "energy", "extensive", "none",
           "points_power_plants", ["eia_923", "ceb_annual_report_2023", "ceb_statistical_digest_2025", "wri_gppd"],
           "USA 2025; LKA 2023 (CEB) / 2017 estimate (others)",
           "Annual net electricity generation of each plant.",
           "USA: EIA-923 2025 'Page 1 Generation and Fuel Data' net generation (MWh) summed over all "
           "prime-mover/fuel rows of each plant, /1000; plants with negative net generation (pumped "
           "storage, batteries) set to 0; the 'State-Fuel Level Increment' pseudo-plant (id 99999) is "
           "excluded. LKA: CEB-owned stations use reported 2023 net generation (CEB Annual Report 2023 "
           "Tables G1-G3); other plants use GPPD v1.3.0 'estimated_generation_gwh_2017' (modelled: "
           "CAPACITY-FACTOR-V1 / HYDRO-V1 / SOLAR-V1-NO-AGE), except that an estimate larger than the 2024 net "
           "generation of all IPP plants of that technology (CEB Statistical Digest 2025) is set to NaN: this drops "
           "Yugadanavi (GPPD 1,396 GWh vs 768 GWh for both IPP oil stations); the estimate is kept in column "
           "generation_gwh_gppd_estimate. USA: plants reported in EIA-923 without an operating/standby generator "
           "in EIA-860 are not mapped (count and GWh in the infra report). Columns generation_year and "
           "generation_basis record which applies.", "higher"),
    Metric("renewable_capacity_mw", "Renewable power capacity", "MW", "energy", "extensive", "none",
           "points_power_plants",
           ["eia_860", "wri_gppd", "ceb_annual_report_2023"], "USA 2025; LKA 2023 (CEB) / GPPD v1.3.0 (others)",
           "Installed capacity of hydro (excl. pumped storage), wind, solar, geothermal and biomass plants per hex.",
           "Plant-level capacity of renewable technologies (column renewable_capacity_mw of the plant points), "
           "summed per snapped H3 cell and per admin unit containing the plant. For US plants the "
           "renewable part is computed per generator (EIA technologies Conventional Hydroelectric, "
           "Onshore/Offshore Wind, Solar PV/Thermal, Geothermal, Wood/Wood Waste Biomass, Landfill Gas, "
           "Other Waste Biomass); municipal solid waste, pumped storage and batteries are not counted. "
           "The infra hex table carries the same per-hex sums of power_capacity_mw / generation_gwh / "
           "renewable_capacity_mw for convenience.", "higher"),
    Metric("port_cargo_t", "Seaport cargo (imports + exports)", "t", "trade", "extensive", "none", "points_ports",
           ["imf_portwatch", "dcs_abstract_2025_transport"], "2025",
           "Estimated annual maritime import + export volume handled by each port (metric tonnes).",
           "IMF PortWatch daily port activity (AIS-based estimates of tonnes discharged / loaded for trade), "
           "import + export summed over calendar year 2025, all vessel types. Same method in both countries. "
           "PortWatch measures trade: its Colombo estimate (30.6 Mt in 2025) is about a quarter of the cargo "
           "SLPA reports as handled incl. transshipment (114.6 Mt in 2024, DCS Abstract Table 7.14; kept in "
           "column official_cargo_t), so transshipment is evidently not captured. Ports with zero estimated "
           "cargo are dropped (Galle), as are PortWatch 'USA' ports that do not snap to the 50-states + DC grid "
           "(a mislabelled 'Sydney' at 46.2N 60.2W and the deep-water 'Offshore Oil Terminal 1' at 26.4N 90.8W). "
           "Port names are PortWatch's (its 'Freeport (US-FL)' lies at Freeport, Texas).", "higher"),
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
           "location id (local_code; FAA ids are unique nationally), falling back to the IATA code only within the "
           "same state (50 states + DC; territories excluded from the coverage denominator). Enplanements count boarding passengers "
           "only (about half of total passenger movements). Sri Lanka: no per-airport figure obtainable "
           "(aasl.lk / caa.lk unreachable); nationally the Civil Aviation Authority reports 4,437,156 "
           "passengers departing Sri Lanka by air in 2024 (DCS Statistical Abstract 2025 Table 7.16), "
           "so the metric is US-only and the Sri Lankan points carry NaN.", "higher", ["usa"]),
    Metric("military_sites", "Military installations", "count", "military", "extensive", "none",
           "points_military", ["dod_mirta", "wikidata"], "2025 (USA) / 2026 (LKA)",
           "Military bases, stations, ranges and training areas (one per site).",
           "USA: DoD MIRTA points with operational status 'act' (active) or 'semi' (semi-active) in the 50 "
           "states + DC, all reporting components incl. Reserve and National Guard (caretaker, closed and excess "
           "sites dropped). LKA: union of three Wikidata queries - items in Sri Lanka that are instances of "
           "military installation / military base / airbase / naval base (or subclasses), items operated by the "
           "Sri Lanka Army, Navy, Air Force, Coast Guard or Armed Forces, and items whose English label starts "
           "with SLAF/SLNS/SLN or ends with Garrison, Army Camp, Army Base, Cantonment, Naval Base or Navy Camp "
           "- all with coordinates and no dissolution date. Excluded: colonial forts (label contains the word "
           "'fort'), fortresses, citadels, castles, former sites (label 'Old ...'), universities, military "
           "units, aircraft accidents and the civil Bandaranaike International Airport. An official register "
           "(MIRTA) is compared with crowd-sourced data (Wikidata has few Sri Lanka Navy and Army camps), so "
           "Sri Lankan counts are a lower bound.", "neutral"),
    Metric("spaceports", "Spaceports and launch sites", "count", "infrastructure", "extensive", "none",
           "points_spaceports", ["faa_spaceports", "ourairports", "wikidata"], "2026",
           "Spaceports / launch and reentry sites (one per site on the FAA list).",
           "Every site on the FAA Office of Commercial Space Transportation 'Spaceports by State' page (FAA-licensed "
           "launch/reentry sites, federal ranges, private exclusive-use sites); kind = FAA-licensed spaceport, "
           "federal launch site or private launch site. Coordinates: OurAirports for airport-based spaceports, "
           "Wikidata for launch ranges, or the plus code printed on the FAA page (Launch Complex 46, Shuttle "
           "Landing Facility, Blue Origin Launch Site One). Wikidata's own 'spaceport' class is not used as the "
           "list because it includes historic ranges, engine test stands and non-spaceports. Sri Lanka has none "
           "(empty table).", "higher", ["usa"]),
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


_DIAG: dict = {}   # coverage diagnostics collected during a build, written to report.json


def _states() -> pd.DataFrame:
    a = load_admin("usa", "adm1")
    return pd.DataFrame({"fips": a.admin_id, "abbr": a["abbr"]})


def _attach_admin(df: pd.DataFrame, country: str) -> pd.DataFrame:
    """Add the admin units (adm1, adm2[, adm3]) containing each point.

    Point-in-polygon on the core boundaries, so a plant on a state-line river (Vogtle GA, Salem NJ,
    Quad Cities IL) keeps its own state even when its snapped hex is dominated by the neighbour.
    Points outside every polygon (offshore wind, port berths, coastal sites) fall back to the
    dominant unit of their snapped hex; column admin_method records which applied.  Hex-level
    aggregation in civmap.reconcile uses the h3 column, not these columns."""
    import geopandas as gpd

    hx = load_hexes(country).set_index("h3")
    cells = util.snap_points(df["lat"].to_numpy(float), df["lon"].to_numpy(float), country)
    df = df.copy()
    if not len(df):
        for lvl in COUNTRIES[country]["admin_levels"]:
            df[lvl] = pd.Series(dtype=object)
        df["admin_method"] = pd.Series(dtype=object)
        return df
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(df["lon"].astype(float), df["lat"].astype(float)),
                           crs=4326, index=df.index)
    pip = None
    for lvl in COUNTRIES[country]["admin_levels"]:
        a = load_admin(country, lvl)[["admin_id", "geometry"]]
        j = gpd.sjoin(pts, a, how="left", predicate="within")
        pip = j[~j.index.duplicated()]["admin_id"].reindex(df.index)
        fb = pd.Series([hx.at[c, lvl] if c is not None and c in hx.index else None for c in cells], index=df.index)
        df[lvl] = pip.where(pip.notna(), fb)
    df["admin_method"] = np.where(pip.notna(), "point-in-polygon", "snapped hex (point outside boundaries)")
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
# GPPD coordinates that are copies of another plant's site and are not caught by _check_location
# (no Wikidata label contains the plant name).  name -> (copied site's Wikidata id, replacement
# Wikidata id, reason).  Inginiyagala: the GPPD point (7.6674N 80.6668E) is Bowatenna Dam
# (Q15830465, 7.6667N 80.6667E, ~0.1 km away) in Matale district; CEB Annual Report 2023 p.21
# places Inginiyagala in the Samanala Complex "utilizing resources from the Walawe, Kalu, and
# Gal Oya Rivers", so the station is at the Gal Oya dam (Wikidata Q16891495, on the Ampara /
# Monaragala district border; COD-AB puts the point 0.15 km inside Monaragala).
GPPD_LOCATION_FIX = {
    "Inginiyagala": ("Q15830465", "Q16891495",
                     ("GPPD coordinate duplicates Bowatenna Dam (Q15830465); Gal Oya Dam (Q16891495) used "
                      "(CEB AR 2023 p.21: Samanala Complex uses the Gal Oya river)")),
}
# CEB Statistical Digest 2025, 'Generation statistics' (PDF p.1; verified against the cached PDF):
# 2024 net generation (GWh) of all independent power producers of each technology.  A GPPD modelled
# 2017 estimate larger than the reported 2024 output of its whole category is not used.
CEB_SD_IPP_GEN_2024 = {"Oil": 768, "Hydro": 1473, "Wind": 409, "Solar": 257}

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


def _haversine_km(lat1, lon1, lat2, lon2):
    a1, o1, a2, o2 = map(np.radians, [lat1, lon1, lat2, lon2])
    h = np.sin((a2 - a1) / 2) ** 2 + np.cos(a1) * np.cos(a2) * np.sin((o2 - o1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(h))


def _wikidata_lka_plants() -> pd.DataFrame:
    """Power stations and dams in Sri Lanka with coordinates (Wikidata), one row per coordinate."""
    q = """SELECT DISTINCT ?item ?itemLabel ?coord WHERE {
      { ?item wdt:P31/wdt:P279* wd:Q159719 } UNION { ?item wdt:P31/wdt:P279* wd:Q12323 }
      UNION { VALUES ?item { wd:Q28457473 } }
      ?item wdt:P17 wd:Q854 ; wdt:P625 ?coord .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""
    w = util.sparql(q, "infra_lka_plants_dams")
    ll = [_wkt(c) for c in w.coord]
    w["lat"] = [a for a, _ in ll]
    w["lon"] = [b for _, b in ll]
    w["qid"] = w["item"].map(_qid)
    w["label"] = w.itemLabel.str.lower()
    return w.sort_values(["qid", "lat"]).reset_index(drop=True)


def _check_location(name: str, lat: float, lon: float, wd: pd.DataFrame, max_km: float = 20.0):
    """GPPD coordinates are wrong for a few Sri Lankan hydro stations (e.g. Old Laxapana at 9.9N,
    Polpitiya / Samanala swapped).  If every Wikidata power station / dam whose label matches the
    plant name lies > max_km away, use the nearest matching Wikidata coordinate instead."""
    low = name.lower()
    cand = wd[wd.label.str.contains(low, regex=False)]
    if cand.empty:
        tok = low.split()[0]
        if len(tok) >= 4:
            cand = wd[wd.label.str.contains(tok, regex=False)]
    if cand.empty:
        return lat, lon, "wri_gppd", ""
    d = _haversine_km(lat, lon, cand.lat.to_numpy(), cand.lon.to_numpy())
    i = int(np.argmin(d))
    if d[i] <= max_km:
        return lat, lon, "wri_gppd", ""
    c = cand.iloc[i]
    return (float(c.lat), float(c.lon), "wikidata",
            f"GPPD coordinate ({lat:.4f}, {lon:.4f}) is {d[i]:.0f} km from Wikidata '{c.itemLabel}' ({c.qid}); Wikidata used")


def power_plants_lka() -> pd.DataFrame:
    p = fetch(GPPD_URL, "wri_gppd", "infra/gppd")
    g = pd.read_csv(p, low_memory=False)
    g = g[g.country == "LKA"].copy()
    wd = _wikidata_lka_plants()
    fixed = [_check_location(r.name, r.latitude, r.longitude, wd) for r in g.itertuples()]
    for i, r in enumerate(g.itertuples()):
        if r.name not in GPPD_LOCATION_FIX:
            continue
        dup_q, new_q, why = GPPD_LOCATION_FIX[r.name]
        dup = wd[wd.qid == dup_q].iloc[0]
        d_dup = float(_haversine_km(r.latitude, r.longitude, dup.lat, dup.lon))
        assert d_dup < 1.0, f"GPPD {r.name} no longer at {dup_q} ({d_dup:.1f} km): re-check GPPD_LOCATION_FIX"
        c = wd[wd.qid == new_q].iloc[0]
        fixed[i] = (float(c.lat), float(c.lon), "wikidata",
                    f"{why}; GPPD ({r.latitude:.4f}, {r.longitude:.4f}) is {d_dup:.1f} km from {dup.itemLabel}")
    g["lat"] = [f[0] for f in fixed]
    g["lon"] = [f[1] for f in fixed]
    g["loc_src"] = [f[2] for f in fixed]
    g["loc_note"] = [f[3] for f in fixed]
    used: set[str] = set()
    rows = []
    for name, gnames, cap, gen, fuel, note in CEB_2023:
        hit = g[g.name.isin(gnames)]
        if len(hit):
            lat, lon = float(hit.lat.mean()), float(hit.lon.mean())
            ref = "|".join(hit.gppd_idnr)
            cy = hit.commissioning_year.dropna()
            cyear = float(cy.min()) if len(cy) else np.nan
            loc_src = "|".join(sorted(set(hit.loc_src)))
            note = "; ".join([x for x in [note] + list(hit.loc_note) if x])
        else:  # not in GPPD (Thambapavani / Mannar wind, commissioned 2020-21): Wikidata location
            c = wd[wd.label == "thambapavani wind farm"].iloc[0]
            lat, lon, ref, cyear, loc_src = float(c.lat), float(c.lon), c.qid, np.nan, "wikidata"
        used |= set(gnames)
        rows.append(dict(id=f"lk_ceb_{len(rows):02d}", name=name, lat=lat, lon=lon, primary_fuel=fuel,
                         power_capacity_mw=cap, generation_gwh=gen, generation_year=2023,
                         generation_basis="reported net generation (CEB Annual Report 2023)",
                         commissioning_year=cyear, owner="Ceylon Electricity Board",
                         capacity_source="ceb_annual_report_2023", location_source=loc_src,
                         source_ref=ref, note=note))
    for name, cap, fuel, year, note in CEB_LATER:
        # Wikidata 'Uma Oya Hydropower Complex' (Q28457473) carries two coordinates (headworks at
        # Puhulpola 6.91N 80.93E and the tunnel outlet / power-house end 6.80N 81.07E): the southern
        # one is used for the power station.
        c = wd[wd.qid == "Q28457473"].sort_values("lat").iloc[0]
        rows.append(dict(id=f"lk_ceb_{len(rows):02d}", name=name, lat=float(c.lat), lon=float(c.lon),
                         primary_fuel=fuel, power_capacity_mw=cap, generation_gwh=np.nan, generation_year=np.nan,
                         generation_basis="not published", commissioning_year=year,
                         owner="Ceylon Electricity Board", capacity_source="ceb_annual_report_2023",
                         location_source="wikidata", source_ref=c.qid, note=note))
    rest = g[~g.name.isin(used) & ~g.name.isin(GPPD_RETIRED_LKA)]
    for r in rest.itertuples():
        est = r.estimated_generation_gwh_2017
        cat = CEB_SD_IPP_GEN_2024.get(r.primary_fuel)
        if cat is not None and np.isfinite(est) and est > cat:
            gen, gyear = np.nan, np.nan
            basis = (f"GPPD v1.3.0 2017 estimate ({est:,.0f} GWh, {r.estimated_generation_note_2017}) not used: it "
                     f"exceeds the 2024 net generation of all IPP {r.primary_fuel.lower()} plants combined "
                     f"({cat:,} GWh, CEB Statistical Digest 2025)")
        else:
            gen, gyear = est, 2017
            basis = f"estimated (GPPD v1.3.0 {r.estimated_generation_note_2017})"
        rows.append(dict(id=r.gppd_idnr, name=r.name, lat=r.lat, lon=r.lon, primary_fuel=r.primary_fuel,
                         power_capacity_mw=r.capacity_mw, generation_gwh=gen, generation_gwh_gppd_estimate=est,
                         generation_year=gyear, generation_basis=basis,
                         commissioning_year=r.commissioning_year, owner=r.owner if isinstance(r.owner, str) else "",
                         capacity_source="wri_gppd", location_source=r.loc_src, source_ref=r.gppd_idnr,
                         note="; ".join(x for x in [f"GPPD source: {r.source}", r.loc_note] if x)))
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
    orphan = ng[~ng.index.isin(cap.index)]   # reported in EIA-923 but no OP/SB generator in EIA-860 'Operable'
    _DIAG["eia923_total_gwh_excl_99999"] = float(ng.sum())
    _DIAG["eia923_plants_without_operable_generator"] = int(len(orphan))
    _DIAG["eia923_gwh_of_plants_without_operable_generator"] = float(orphan.sum())

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
    f["ST"] = f.ST.astype(str).str.strip()
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
        out["state"] = a.iso_region.str[3:].to_numpy()
        f = _faa()
        f = f[f.ST.isin(set(_states().abbr))]          # 50 states + DC (drops PR, GU, VI, AS, MP, WK)
        assert not f.Locid.duplicated().any(), "FAA location ids are expected to be unique"
        by_lid = f.groupby("Locid").pax.sum()
        by = f.groupby(["Locid", "ST"]).pax.sum()
        # FAA location id first (unique nationally; no state check, as OurAirports codes DCA as US-DC
        # while FAA files it under VA); IATA code as fallback only within the same state (an IATA
        # code can equal another airport's FAA id: Buckley SFB, CO is IATA 'BFK' = FAA 'BFK' Buffalo, OK)
        lid = out.faa_lid.map(by_lid)
        iat = pd.Series([by.get((k, s), np.nan) for k, s in zip(out.iata, out.state)], index=out.index)
        pax = lid.fillna(iat)
        used = [k if np.isfinite(v) else i for k, i, v in zip(out.faa_lid, out.iata, lid)]
        used = [u for u, p in zip(used, pax) if np.isfinite(p)]
        assert len(used) == len(set(used)), "an FAA airport record is matched to two OurAirports airports"
        out["airport_pax"] = pax
        out["pax_year"] = 2025
        cov = pax.sum() / f.pax.sum()
        _DIAG["faa_enplanements_50states_dc"] = float(f.pax.sum())
        _DIAG["faa_enplanements_matched"] = float(pax.sum())
        print(f"  FAA enplanements matched to large/medium airports: {pax.sum() / 1e6:,.1f} M "
              f"of {f.pax.sum() / 1e6:,.1f} M in the 50 states + DC ({cov:.1%})")
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
    q3 = """SELECT ?item ?itemLabel ?coord (GROUP_CONCAT(DISTINCT ?tl; separator="; ") AS ?types) WHERE {
      ?item wdt:P17 wd:Q854 ; wdt:P625 ?coord ; rdfs:label ?l .
      FILTER(lang(?l)='en' && REGEX(?l, "^(SLNS |SLN |SLAF )|(Garrison|Army Camp|Army Base|Cantonment|Naval Base|Navy Camp)$"))
      FILTER NOT EXISTS { ?item wdt:P576 [] }
      OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label ?tl . FILTER(lang(?tl)='en') }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?item ?itemLabel ?coord"""
    d = pd.concat([util.sparql(q1, "infra_lka_military_cls"), util.sparql(q2, "infra_lka_military_ops"),
                   util.sparql(q3, "infra_lka_military_labels")], ignore_index=True)
    if "ops" not in d.columns:
        d["ops"] = ""
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
        # historic colonial forts (labels '... fort' / 'Fort ...') and former sites ('Old ...') are not
        # military installations today even when Wikidata types them as 'military base'
        if (types & MIL_EXCLUDE_TYPES or "shootdown" in lab or "crash" in lab
                or re.search(r"\bfort\b", lab) or lab.startswith("old ")):
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


# FAA Office of Commercial Space Transportation, 'Spaceports by State' (cached page; every name is
# checked against it at build time).  One row per listed site: name as printed, state, licence,
# launch type, and where the coordinate comes from - the OurAirports airport the page names or whose
# street address it gives (KCSM: page says 'Clinton-Sherman Airport (KCSM)'; KEFD: 'Ellington
# Airport'; KTIX: 355 Golden Knights Blvd, Titusville geocodes 0.8 km from it via Nominatim), the
# Wikidata item of a federal / private launch site, or the Open Location Code printed on the page.
FAA_SPACEPORTS = [
    ("Huntsville International Air and Space Port", "AL", "FAA", "Orbital Reentry", "ourairports", "KHSV"),
    ("Pacific Spaceport Complex", "AK", "FAA", "Vertical", "wikidata", "Q283842"),
    ("Mojave Air & Space Port", "CA", "FAA", "Horizontal", "ourairports", "KMHV"),
    ("Vandenberg Space Force Base", "CA", "FAA and Federal", "Vertical and Horizontal", "wikidata", "Q461492"),
    ("Colorado Air & Space Port", "CO", "FAA", "Horizontal", "ourairports", "KFTG"),
    ("Cape Canaveral Space Force Station", "FL", "Federal", "Vertical and Horizontal", "wikidata", "Q334465"),
    ("Kennedy Space Center", "FL", "Federal", "Vertical and Horizontal", "wikidata", "Q48821"),
    ("Space Florida Launch Complex 46", "FL", "FAA", "Vertical", "pluscode", "FCQG+FV@Q334465"),
    ("Space Florida Shuttle Landing Facility", "FL", "FAA", "Horizontal and Orbital Reentry", "pluscode", "J874+Q9@KTTS"),
    ("Cecil Air and Space Port", "FL", "FAA", "Horizontal", "ourairports", "KVQQ"),
    ("Exploration Spaceport", "FL", "FAA", "Horizontal", "ourairports", "KTIX"),
    ("Spaceport Camden", "GA", "FAA", "Vertical", "wikidata", "Q43079524"),
    ("Spaceport America", "NM", "FAA", "Horizontal and Vertical", "wikidata", "Q1352314"),
    ("Infinity One Oklahoma Spaceport", "OK", "FAA", "Horizontal", "ourairports", "KCSM"),
    ("Launch Site One West Texas", "TX", "Private Exclusive Use", "", "pluscode", "853QF62P+MV"),
    ("Boca Chica", "TX", "Private Exclusive Use", "", "wikidata", "Q16950811"),
    ("Houston Spaceport", "TX", "FAA", "Horizontal", "ourairports", "KEFD"),
    ("Midland Spaceport", "TX", "FAA", "Horizontal", "ourairports", "KMAF"),
    ("Mid-Atlantic Regional Spaceport", "VA", "FAA", "Vertical", "wikidata", "Q2660820"),
    ("Wallops Flight Facility", "VA", "Federal", "Vertical", "wikidata", "Q182348"),
]

_OLC = "23456789CFGHJMPQRVWX"
_OLC_RES = (20.0, 1.0, 0.05, 0.0025, 0.000125)


def _olc_decode(code: str, ref: tuple[float, float] | None = None) -> tuple[float, float]:
    """Centre (lat, lon) of an Open Location Code / plus code (github.com/google/open-location-code).
    A short code (fewer than 8 digits before '+') is recovered against the reference point `ref`."""
    c = code.upper()
    n_pre = c.index("+")
    c = c.replace("+", "")
    if n_pre < 8:
        assert ref is not None, f"short plus code {code} needs a reference point"
        la, lo = ref[0] + 90.0, ref[1] + 180.0
        pre = ""
        for r in _OLC_RES[: (8 - n_pre) // 2]:
            dla, dlo = int(la // r), int(lo // r)
            pre += _OLC[dla] + _OLC[dlo]
            la, lo = la - dla * r, lo - dlo * r
        c = pre + c
    lat, lon = -90.0, -180.0
    for i in range(len(c) // 2):
        lat += _OLC.index(c[2 * i]) * _OLC_RES[i]
        lon += _OLC.index(c[2 * i + 1]) * _OLC_RES[i]
    last = _OLC_RES[len(c) // 2 - 1]
    lat, lon = lat + last / 2, lon + last / 2
    if n_pre < 8:  # nearest match to the reference (recoverNearest)
        span = _OLC_RES[(8 - n_pre) // 2 - 1]
        lat += span * round((ref[0] - lat) / span)
        lon += span * round((ref[1] - lon) / span)
    return lat, lon


def _faa_spaceports_page_text() -> str:
    import html as _html
    p = fetch(FAA_SPACEPORTS_URL, "faa_spaceports", "infra/faa_spaceports", "spaceports_by_state.html")
    t = p.read_text(errors="ignore")
    m = re.search(r"<main.*?</main>", t, flags=re.S)
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", m.group(0) if m else t, flags=re.S)
    return _html.unescape(re.sub(r"<[^>]+>", "\n", body))


def spaceports_usa(wd: pd.DataFrame) -> pd.DataFrame:
    """US spaceports = the sites on the FAA 'Spaceports by State' page, geolocated (see FAA_SPACEPORTS)."""
    txt = _faa_spaceports_page_text()
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    flat = re.sub(r"\s+", " ", txt)
    n_listed = sum(1 for ln in lines if ln == "Operator")
    missing = [n for n, *_ in FAA_SPACEPORTS if n not in flat]
    assert not missing, f"FAA spaceport names not on the cached page: {missing}"
    assert n_listed == len(FAA_SPACEPORTS), f"FAA page lists {n_listed} spaceports, FAA_SPACEPORTS has {len(FAA_SPACEPORTS)}"
    ap = pd.read_csv(fetch(OURAIRPORTS_URL, "ourairports", "infra/ourairports"), keep_default_na=False,
                     na_values=[""], low_memory=False).set_index("ident")
    wq = wd.assign(qid=wd["item"].map(_qid)).drop_duplicates("qid").set_index("qid")

    def where(src: str, ref: str) -> tuple[float, float, str]:
        if src == "ourairports":
            r = ap.loc[ref]
            return float(r.latitude_deg), float(r.longitude_deg), f"OurAirports {ref} ({r['name']})"
        if src == "wikidata":
            la, lo = _wkt(wq.loc[ref, "coord"])
            return la, lo, f"Wikidata {ref} ({wq.loc[ref, 'itemLabel']})"
        code, _, near = ref.partition("@")
        base = None
        if near:
            base = where("wikidata" if near.startswith("Q") else "ourairports", near)[:2]
        la, lo = _olc_decode(code, base)
        return la, lo, f"plus code {code} on the FAA page" + (f" (recovered near {near})" if near else "")

    rows = []
    for name, st, lic, launch, src, ref in FAA_SPACEPORTS:
        la, lo, how = where(src, ref)
        kind = {"Federal": "federal launch site", "Private Exclusive Use": "private launch site"}.get(
            lic, "FAA-licensed spaceport")
        rows.append(dict(id="faa_sp_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), name=name, lat=la,
                         lon=lo, kind=kind, state=st, faa_license=lic, launch_type=launch, location_source=how,
                         wikidata=ref if src == "wikidata" else "", spaceports=1))
    return pd.DataFrame(rows)


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
    if country == "usa":
        # Wikidata's 'spaceport' class mixes in historic ranges, engine test stands, a Nike missile
        # site and an airport merely named 'Intergalactic Spaceport', and misses FAA-licensed
        # airport spaceports: the FAA register defines the US list; Wikidata only supplies coordinates.
        df = _attach_admin(spaceports_usa(d), country)
        util.write_points(country, DOMAIN, "spaceports", df)
        print(f"  spaceports: {len(df)} FAA-listed sites; {df.kind.value_counts().to_dict()}")
        return df
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
            key = low.replace("(boi)", "").replace("boi -", "").replace("promotion", "processing").strip()
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


def _wb(ind: str, iso3: str) -> dict:
    v, y = util.wb_value(ind, iso3)
    return {"value": v, "year": y}


def _report(country: str, results: dict) -> None:
    """National totals and sanity checks against published figures; written to report.json."""
    iso3 = country.upper()
    rep: dict = {}
    pw = results["power"]
    gen = pw.generation_gwh.sum()
    share = lambda kinds: float(pw[pw.kind.isin(kinds)].generation_gwh.sum() / gen)  # noqa: E731
    rep["power"] = {
        "plants": int(len(pw)), "capacity_mw": float(pw.power_capacity_mw.sum()), "generation_gwh": float(gen),
        "renewable_capacity_mw": float(pw.renewable_capacity_mw.sum()),
        "generation_share": {"renewable": share(RENEWABLE_FUELS), "hydro": share({"Hydro"}),
                             "coal": share({"Coal"})},
        "wb_check": {"renewable_output_pct EG.ELC.RNEW.ZS": _wb("EG.ELC.RNEW.ZS", iso3),
                     "hydro_output_pct EG.ELC.HYRO.ZS": _wb("EG.ELC.HYRO.ZS", iso3),
                     "coal_output_pct EG.ELC.COAL.ZS": _wb("EG.ELC.COAL.ZS", iso3)},
    }
    if country == "lka":
        # CEB Statistical Digest 2025 pp.2-3 (transcribed): installed capacity excl. rooftop solar
        # 4,814 MW (2025) / 4,651 MW (2024); net generation incl. 1,674 GWh rooftop solar 17,812 GWh (2025).
        rep["power"]["published"] = {"installed_capacity_mw_2025_excl_rooftop": 4814,
                                     "net_generation_gwh_2025_incl_rooftop": 17812,
                                     "ceb_owned_capacity_mw_end2023": 3218,
                                     "source": "CEB Statistical Digest 2025 pp.2-3; CEB Annual Report 2023 p.22"}
        rep["power"]["capacity_coverage_vs_2025"] = rep["power"]["capacity_mw"] / 4814
    else:
        rep["power"]["published"] = {"eia923_2025_net_generation_gwh_all_rows_excl_state_increments":
                                     _DIAG.get("eia923_total_gwh_excl_99999")}
        rep["power"]["eia923_not_mapped"] = {
            "plants_without_operable_generator": _DIAG.get("eia923_plants_without_operable_generator"),
            "net_generation_gwh": _DIAG.get("eia923_gwh_of_plants_without_operable_generator")}
    rep["power"]["generation_reported_plants"] = int(pw.generation_gwh.notna().sum())
    pt = results["ports"]
    rep["ports"] = {"ports": int(len(pt)), "port_cargo_t": float(pt.port_cargo_t.sum()),
                    "port_calls": float(pt.port_calls.sum()),
                    "wb_check": {"container_port_traffic_teu IS.SHP.GOOD.TU": _wb("IS.SHP.GOOD.TU", iso3)}}
    if country == "lka":
        rep["ports"]["port_teu_colombo_2024"] = float(pt.port_teu.sum())
        rep["ports"]["official_cargo_t_2024"] = float(pt.official_cargo_t.sum())
    ap = results["airports"]
    rep["airports"] = {"airports": int(len(ap)), "large": int((ap.kind == "large_airport").sum()),
                       "airport_pax": float(np.nansum(ap.airport_pax)),
                       "faa_enplanements_50states_dc": _DIAG.get("faa_enplanements_50states_dc"),
                       "wb_check": {"air_passengers_carried IS.AIR.PSGR": _wb("IS.AIR.PSGR", iso3)}}
    if country == "lka":
        rep["airports"]["published"] = {"passengers_departing_by_air_2024": 4437156,
                                        "passengers_arriving_by_air_2024": 4378767,
                                        "source": "DCS Statistical Abstract 2025 Table 7.16 (Civil Aviation Authority)"}
    rep["military_sites"] = int(len(results["military"]))
    sp = results["spaceports"]
    rep["spaceports"] = {"sites": int(len(sp)),
                         "by_kind": sp.kind.value_counts().to_dict() if len(sp) else {}}
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
