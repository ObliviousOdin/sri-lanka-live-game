"""Civilization-style translation of reconciled statistics (rules documented in docs/METHODOLOGY.md).

Inputs: reconciled hex metrics (data/interim/<cc>/reconciled), core hexes, domain point tables.
Outputs (data/interim/<cc>/civ/): tiles.parquet (terrain, yields, district per hex),
wonders.json, cities.json.

Every rule here is deterministic and parameterised by the constants below so the methodology
page and the app's tooltips can quote them.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from .config import COUNTRIES, INTERIM
from .core import load_hexes

# ---------------------------------------------------------------------------------------------
# Yield scales — shared by both countries.  Yield = floor(2·log10(density / base)) + 1 for
# density ≥ base (one point per half-decade, i.e. every ×3.16), else 0; capped at YIELD_CAP.
# Densities are per km² of tile area, so res-6 (LKA) and res-5 (USA) tiles are comparable.
# ---------------------------------------------------------------------------------------------
YIELD_CAP = 12
YIELD_BASES = {
    # key: (numerator metric, base density per km², unit label)
    "gold": ("gdp_usd", 1.0e5, "US$ GDP per km²"),
    "production": ("gdp_industry_usd", 3.0e4, "US$ industrial GVA per km²"),
    "food": ("ag_gva_usd", 3.0e3, "US$ agricultural GVA per km²"),
    "food_sea": ("fish_catch_t", 0.1, "tonnes of fish landed per km² of sea"),
    "science": ("cit_engineers", 0.1, "STEM workers per km²"),
    "culture": ("emp_arts_accommodation", 0.3, "arts, recreation & hospitality workers per km²"),
    "faith": ("worship_sites", 0.003, "places of worship per km²"),
}
POWER_BONUS = [(100, 1), (1000, 2), (3000, 3)]              # MW in tile -> +Production
RESEARCH_BONUS = [(1_000, 1), (10_000, 2), (50_000, 3)]      # works in last 5 years -> +Science
CULTURE_BONUS = {"per_whs": 2, "max_whs": 2, "museums_min": 3, "museums_bonus": 1,
                 "park_visitors_min": 1_000_000, "park_bonus": 1}
FAITH_BONUS = {"per_holy_site": 2, "max_holy_sites": 1}
YIELDS = ["gold", "production", "food", "science", "culture", "faith"]


def log_yield(density: np.ndarray, base: float) -> np.ndarray:
    d = np.asarray(density, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.floor(2 * np.log10(d / base)) + 1
    y = np.where(np.isfinite(y) & (d >= base), y, 0)
    return np.clip(y, 0, YIELD_CAP).astype(int)


def step_bonus(v: np.ndarray, steps) -> np.ndarray:
    v = np.nan_to_num(np.asarray(v, dtype=float))
    out = np.zeros(len(v), dtype=int)
    for thr, b in steps:
        out = np.where(v >= thr, b, out)
    return out


# ---------------------------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------------------------
TERRAIN = {
    "ocean": "Ocean", "coast": "Coast", "lake": "Lake", "grassland": "Grassland", "plains": "Plains",
    "desert": "Desert", "tundra": "Tundra", "snow": "Snow", "mountains": "Mountains",
}
FEATURES = {"forest": "Forest", "rainforest": "Rainforest", "marsh": "Marsh", "mangrove": "Mangrove",
            "urban": "Urban", "floodplain": "Floodplain", "": ""}


def col(df: pd.DataFrame, k: str, default=np.nan) -> np.ndarray:
    return df[k].to_numpy(float) if k in df.columns else np.full(len(df), default, dtype=float)


def classify_terrain(H: pd.DataFrame) -> pd.DataFrame:
    """Base terrain, feature and hills flag from land cover, DEM and climate."""
    ring = H["ring"].to_numpy(int)
    lc = {k: np.nan_to_num(col(H, f"lc_{k}", 0.0)) for k in
          ("tree", "shrub", "grass", "crop", "built", "bare", "snow", "water", "wetland", "mangrove", "moss")}
    elev = np.nan_to_num(col(H, "elev_mean_m", 0.0))
    emax = np.nan_to_num(col(H, "elev_max_m", 0.0))
    relief = np.nan_to_num(col(H, "relief_m", 0.0))
    slope = np.nan_to_num(col(H, "slope_mean_deg", 0.0))
    temp = col(H, "temp_mean_c")
    precip = col(H, "precip_mm")
    lat = np.abs(H["lat"].to_numpy(float))
    temp = np.where(np.isfinite(temp), temp, 27 - 0.55 * lat - elev / 160)  # rough fallback
    precip = np.where(np.isfinite(precip), precip, 800)

    base = np.full(len(H), "plains", dtype=object)
    veg_open = lc["grass"] + lc["shrub"] + lc["crop"]
    # Grassland = humid or well-watered farmland; Plains = drier open country (prairie, dry farming)
    wet = (precip >= 1000) | ((lc["crop"] >= 0.4) & (precip >= 700))
    base = np.where((lc["crop"] + lc["grass"] + lc["tree"] >= 0.35) & wet, "grassland", base)
    base = np.where(~wet & (veg_open + lc["tree"] >= 0.3), "plains", base)
    base = np.where((lc["bare"] >= 0.35) | ((precip < 330) & (lc["bare"] + lc["shrub"] >= 0.5)), "desert", base)
    base = np.where((lc["moss"] >= 0.2) | ((temp < -3) & (lc["tree"] < 0.3)), "tundra", base)
    base = np.where(lc["snow"] >= 0.35, "snow", base)
    mountains = ((slope >= 16) & (emax >= 900)) | ((elev >= 2200) & (slope >= 9)) | ((relief >= 1600) & (slope >= 11))
    hills = ~mountains & ((slope >= 6) | (relief >= 500))
    base = np.where(mountains & (ring == 0), "mountains", base)
    land_frac = np.nan_to_num(H["land_km2"].to_numpy(float) / H["hex_km2"].to_numpy(float))
    # mostly-sea coastal tiles read as Coast; water inside the boundary reads as Lake
    base = np.where((ring == 0) & (land_frac < 0.35), "coast", base)
    base = np.where((ring == 0) & (land_frac >= 0.35) & (lc["water"] >= 0.55), "lake", base)
    base = np.where(ring == 1, "coast", base)
    base = np.where(ring >= 2, "ocean", base)

    feat = np.full(len(H), "", dtype=object)
    tropical = (lat < 23.5) & (precip >= 1400)
    feat = np.where(lc["tree"] >= 0.45, np.where(tropical, "rainforest", "forest"), feat)
    feat = np.where(lc["wetland"] >= 0.2, "marsh", feat)
    feat = np.where(lc["mangrove"] >= 0.08, "mangrove", feat)
    feat = np.where(lc["built"] >= 0.25, "urban", feat)
    feat = np.where(np.isin(base, ["ocean", "coast", "lake"]), "", feat)
    return pd.DataFrame({"terrain": base, "feature": feat, "hills": hills & (ring == 0)}, index=H.index)


# ---------------------------------------------------------------------------------------------
# Districts
# ---------------------------------------------------------------------------------------------
DISTRICTS = {
    "city_center": "City Center", "harbor": "Harbor", "aerodrome": "Aerodrome", "spaceport": "Spaceport",
    "campus": "Campus", "commercial_hub": "Commercial Hub", "industrial_zone": "Industrial Zone",
    "dam": "Dam", "power_plant": "Power Plant", "encampment": "Encampment", "holy_site": "Holy Site",
    "theater_square": "Theater Square", "entertainment": "Entertainment Complex", "preserve": "Preserve",
    "plantation": "Plantation", "farmland": "Farmland", "fishery": "Fishery", "neighborhood": "Neighborhood",
}
# priority order (first match becomes the tile's primary district)
DISTRICT_ORDER = ["city_center", "spaceport", "harbor", "aerodrome", "campus", "commercial_hub",
                  "industrial_zone", "dam", "power_plant", "holy_site", "theater_square", "encampment",
                  "entertainment", "plantation", "farmland", "fishery", "neighborhood", "preserve"]
DISTRICT_RULES = {
    "city_center": "Centre tile of a City View metro (largest-population tile near the city's coordinates).",
    "harbor": "Seaport with ≥ 1 million tonnes of annual cargo, or one of the country's 3 busiest ports.",
    "aerodrome": "Airport with ≥ 1 million annual passengers, or the country's busiest airport.",
    "spaceport": "Orbital launch site.",
    "campus": "≥ 2,000 research works in 5 years from institutions in the tile (OpenAlex), "
              "or STEM workforce location quotient ≥ 1.8 with ≥ 300 people/km².",
    "commercial_hub": "Finance, real-estate & professional-services employment LQ ≥ 1.15 and either Gold ≥ 7 "
                      "or GDP density in the country's top 1% of tiles.",
    "industrial_zone": "Production yield ≥ 5 and manufacturing employment LQ ≥ 1.5, "
                       "or an export-processing zone / industrial park in the tile.",
    "dam": "Hydroelectric capacity ≥ 100 MW in the tile.",
    "power_plant": "Thermal, nuclear, wind or solar capacity ≥ 500 MW in the tile.",
    "encampment": "Military installation in the tile.",
    "holy_site": "One of the country's most notable religious sites (by Wikipedia language editions), "
                 "or worship-site density in the top 1% of the country's tiles.",
    "theater_square": "UNESCO World Heritage (cultural) site, ≥ 5 museums, or arts/recreation/hospitality LQ ≥ 2 "
                      "with ≥ 300 people/km².",
    "entertainment": "Arts, recreation & hospitality employment LQ ≥ 2.5 (tourism economy).",
    "preserve": "≥ 75% strictly protected (WDPA, IUCN categories Ia–IV), or a national park with visitor statistics.",
    "plantation": "Perennial/tree-crop share of agriculture ≥ 30%, Food yield ≥ 3 and ≥ 40% tree + crop cover.",
    "farmland": "Food yield ≥ 5 and ≥ 40% cropland.",
    "fishery": "Sea tile with Food yield ≥ 4 from fish landings.",
    "neighborhood": "≥ 1,500 people/km² without another district.",
}


def lq(H: pd.DataFrame, num: list[str], den: str = "employed") -> np.ndarray:
    """Location quotient of employment in `num` industries vs the national share."""
    if den not in H.columns or not all(k in H.columns for k in num):
        return np.full(len(H), np.nan)
    n = H[num].sum(axis=1).to_numpy(float)
    d = H[den].to_numpy(float)
    nat = np.nansum(n) / max(np.nansum(d), 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (n / d) / nat


def load_points(country: str) -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted((INTERIM / country).glob("*/points_*.csv")):
        name = f.stem[len("points_"):]
        df = pd.read_csv(f, dtype={"h3": str})
        df["domain"] = f.parent.name
        out[name] = df
    return out


def point_sum(pts: pd.DataFrame | None, col_: str, index, where=None) -> pd.Series:
    if pts is None or col_ not in pts.columns:
        return pd.Series(0.0, index=index)
    p = pts if where is None else pts[where(pts)]
    return pd.to_numeric(p[col_], errors="coerce").groupby(p.h3).sum().reindex(index).fillna(0.0)


def point_count(pts: pd.DataFrame | None, index, where=None) -> pd.Series:
    if pts is None or len(pts) == 0:
        return pd.Series(0.0, index=index)
    p = pts if where is None else pts[where(pts)]
    return p.groupby("h3").size().reindex(index).fillna(0).astype(float)


def first_col(df: pd.DataFrame | None, *names) -> str | None:
    if df is None:
        return None
    for n in names:
        if n in df.columns:
            return n
    return None


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def build_tiles(country: str) -> tuple[pd.DataFrame, dict]:
    R = pd.read_parquet(INTERIM / country / "reconciled" / "hexes.parquet")
    core = load_hexes(country).set_index("h3")
    H = core[["ring", "lat", "lon", "hex_km2", "land_km2"] + list(COUNTRIES[country]["admin_levels"])].join(
        R.drop(columns=[c for c in ("pop", "land_km2") if c in R.columns]), how="left")
    H["pop"] = core["pop"]
    area = H["hex_km2"].to_numpy(float)
    pts = load_points(country)
    idx = H.index

    T = classify_terrain(H)

    # --- yields
    Y = pd.DataFrame(index=idx)
    dens = lambda k: col(H, k, 0.0) / area  # noqa: E731
    Y["gold"] = log_yield(dens("gdp_usd"), YIELD_BASES["gold"][1])
    plants = pts.get("power_plants")
    cap = point_sum(plants, "capacity_mw", idx)
    Y["production"] = log_yield(dens("gdp_industry_usd"), YIELD_BASES["production"][1]) + step_bonus(cap, POWER_BONUS)
    food_land = log_yield(dens("ag_gva_usd"), YIELD_BASES["food"][1])
    food_sea = log_yield(dens("fish_catch_t"), YIELD_BASES["food_sea"][1])
    Y["food"] = np.where(H.ring > 0, food_sea, np.maximum(food_land, food_sea))
    inst = pts.get("institutions")
    wcol = first_col(inst, "research_works_5y", "works_5y")
    works = point_sum(inst, wcol, idx) if wcol else pd.Series(0.0, index=idx)
    Y["science"] = log_yield(dens("cit_engineers"), YIELD_BASES["science"][1]) + step_bonus(works, RESEARCH_BONUS)
    heritage = pts.get("heritage")
    whs = point_count(heritage, idx)
    museums = point_count(pts.get("museums"), idx)
    parks = pts.get("parks")
    vcol = first_col(parks, "park_visitors", "visitors")
    park_vis = point_sum(parks, vcol, idx) if vcol else pd.Series(0.0, index=idx)
    cb = CULTURE_BONUS
    Y["culture"] = (log_yield(dens("emp_arts_accommodation"), YIELD_BASES["culture"][1])
                    + cb["per_whs"] * np.minimum(whs, cb["max_whs"]).astype(int)
                    + cb["museums_bonus"] * (museums >= cb["museums_min"]).astype(int)
                    + cb["park_bonus"] * (park_vis >= cb["park_visitors_min"]).astype(int))
    holy = pts.get("holy_sites")
    holy_n = point_count(holy, idx)
    Y["faith"] = (log_yield(dens("worship_sites"), YIELD_BASES["faith"][1])
                  + FAITH_BONUS["per_holy_site"] * np.minimum(holy_n, FAITH_BONUS["max_holy_sites"]).astype(int))
    Y = Y.clip(upper=YIELD_CAP + 4).astype(int)
    sea = H.ring.to_numpy() > 0
    for c in ("science", "culture", "faith"):
        Y.loc[sea, c] = 0

    # --- districts
    popd = col(H, "pop", 0.0) / np.maximum(col(H, "land_km2", 0.0), 1e-6)
    popd = np.where(col(H, "land_km2", 0.0) > 0.5, popd, 0.0)
    lq_stem = lq(H, ["cit_engineers"])
    lq_fin = lq(H, ["emp_finance", "emp_professional"])
    lq_mfg = lq(H, ["emp_manufacturing"])
    lq_arts = lq(H, ["emp_arts_accommodation"])
    ports = pts.get("ports")
    pcol = first_col(ports, "port_cargo_t", "cargo_tonnes")
    port_t = point_sum(ports, pcol, idx) if pcol else pd.Series(0.0, index=idx)
    top_ports = set()
    if ports is not None and pcol:
        top_ports = set(ports.sort_values(pcol, ascending=False).dropna(subset=["h3"]).h3.head(3))
    airports = pts.get("airports")
    acol = first_col(airports, "airport_pax", "passengers")
    pax = point_sum(airports, acol, idx) if acol else pd.Series(0.0, index=idx)
    top_air = set()
    if airports is not None and acol:
        top_air = set(airports.sort_values(acol, ascending=False).dropna(subset=["h3"]).h3.head(1))
    fuel_col = first_col(plants, "primary_fuel", "kind")
    hydro = point_sum(plants, "capacity_mw", idx,
                      where=(lambda p: p[fuel_col].astype(str).str.lower().str.contains("hydro")) if fuel_col else None)
    other_cap = cap - hydro
    mil = point_count(pts.get("military"), idx)
    space = point_count(pts.get("spaceports"), idx)
    ez = point_count(pts.get("industrial_zones"), idx)
    cultural_whs = point_count(heritage, idx, where=(lambda p: ~p["kind"].astype(str).str.lower().eq("natural"))) \
        if heritage is not None and "kind" in heritage.columns else whs
    holy_top = pd.Series(False, index=idx)
    if holy is not None and len(holy):
        hs = holy.sort_values("sitelinks", ascending=False) if "sitelinks" in holy.columns else holy
        holy_top = pd.Series(idx.isin(set(hs.h3.dropna().head(10 if country == "lka" else 25))), index=idx)
    gdp_dens = dens("gdp_usd")
    gdp_top1 = np.nanquantile(gdp_dens[H.ring.to_numpy() == 0], 0.99) if (H.ring == 0).any() else np.inf
    ws = dens("worship_sites")
    land_mask = H.ring.to_numpy() == 0
    ws_thr = np.nanquantile(ws[land_mask], 0.99) if land_mask.any() else np.inf
    protected = np.nan_to_num(col(H, "protected_strict_pct", np.nan)) if "protected_strict_pct" in H.columns \
        else np.nan_to_num(col(H, "protected_pct", 0.0))
    park_tile = point_count(parks, idx) > 0
    tree_share = np.nan_to_num(col(H, "tree_crop_share", 0.0))
    lc_tree = np.nan_to_num(col(H, "lc_tree", 0.0))
    lc_crop = np.nan_to_num(col(H, "lc_crop", 0.0))

    D = {
        "harbor": (port_t >= 1e6).to_numpy() | idx.isin(top_ports),
        "aerodrome": (pax >= 1e6).to_numpy() | idx.isin(top_air),
        "spaceport": (space > 0).to_numpy(),
        "campus": (works >= 2000).to_numpy() | ((np.nan_to_num(lq_stem) >= 1.8) & (popd >= 300)),
        "commercial_hub": ((Y["gold"].to_numpy() >= 7) | (gdp_dens >= gdp_top1)) & (np.nan_to_num(lq_fin) >= 1.15) & land_mask,
        "industrial_zone": ((Y["production"].to_numpy() >= 5) & (np.nan_to_num(lq_mfg) >= 1.5)) | (ez > 0).to_numpy(),
        "dam": (hydro >= 100).to_numpy(),
        "power_plant": (other_cap >= 500).to_numpy(),
        "encampment": (mil > 0).to_numpy(),
        "holy_site": holy_top.to_numpy() | ((ws >= ws_thr) & land_mask & (ws > 0)),
        "theater_square": (cultural_whs > 0).to_numpy() | (museums >= 5).to_numpy()
        | ((np.nan_to_num(lq_arts) >= 2) & (popd >= 300)),
        "entertainment": (np.nan_to_num(lq_arts) >= 2.5) & (col(H, "pop", 0.0) >= 5000),
        "preserve": ((protected >= 75) | park_tile.to_numpy()) & land_mask,
        "plantation": (tree_share >= 30) & (Y["food"].to_numpy() >= 3) & (lc_tree + lc_crop >= 0.4) & land_mask,
        "farmland": (Y["food"].to_numpy() >= 5) & (lc_crop >= 0.4) & land_mask,
        "fishery": (H.ring.to_numpy() > 0) & (Y["food"].to_numpy() >= 4),
        "neighborhood": (popd >= 1500) & land_mask,
    }
    D = pd.DataFrame(D, index=idx)
    D["city_center"] = False
    primary = pd.Series("", index=idx, dtype=object)
    for k in reversed(DISTRICT_ORDER):
        if k in D.columns:
            primary = primary.where(~D[k], k)
    tiles = pd.concat([H[["ring", "lat", "lon", "hex_km2"]], T, Y.add_prefix("y_")], axis=1)
    tiles["district"] = primary
    tiles["districts_all"] = D.apply(lambda r: ",".join(k for k in DISTRICT_ORDER if k in r.index and r[k]), axis=1)
    extras = {"works_5y": works, "capacity_mw": cap, "port_cargo_t": port_t, "airport_pax": pax,
              "whs": whs, "museums": museums, "holy_sites": holy_n, "park_visitors": park_vis,
              "military_sites": mil, "lq_stem": lq_stem, "lq_finance": lq_fin, "lq_manufacturing": lq_mfg,
              "lq_arts": lq_arts}
    for k, v in extras.items():
        tiles[k] = np.asarray(v, dtype=float)
    return tiles, {"pts": pts, "H": H}


# ---------------------------------------------------------------------------------------------
# Wonders
# ---------------------------------------------------------------------------------------------
WONDER_RULES = [
    # (category, points table, value column(s), N lka, N usa, rule text, unit)
    ("Great University", "universities", ("research_works_5y", "works_5y"), 5, 10,
     "Top research universities by research articles & reviews published 2021–2025 (OpenAlex).", "works"),
    ("Great Port", "ports", ("port_cargo_t", "cargo_tonnes"), 3, 10,
     "Busiest seaports by annual cargo volume.", "tonnes"),
    ("Great Power Station", "power_plants", ("capacity_mw",), 5, 10,
     "Largest power stations by installed capacity.", "MW"),
    ("Natural Wonder", "parks", ("park_visitors", "visitors"), 5, 10,
     "Most visited national parks (annual visitors).", "visitors"),
    ("Great Airport", "airports", ("airport_pax", "passengers"), 1, 10,
     "Busiest airports by annual passengers.", "passengers"),
    ("World Heritage", "heritage", (None,), 99, 99,
     "All UNESCO World Heritage Sites inscribed in the country.", ""),
    ("Holy Wonder", "holy_sites", ("sitelinks",), 3, 5,
     "Most widely documented religious sites (number of Wikipedia language editions).", "Wikipedia editions"),
]


def build_wonders(country: str, pts: dict, tiles: pd.DataFrame, sources_by_domain: dict) -> list[dict]:
    out = []
    for cat, table, cols, n_l, n_u, rule, unit in WONDER_RULES:
        df = pts.get(table)
        if df is None or len(df) == 0:
            continue
        vc = next((c for c in cols if c and c in df.columns), None)
        d = df.dropna(subset=["h3"]).copy()
        if vc:
            d[vc] = pd.to_numeric(d[vc], errors="coerce")
            d = d.dropna(subset=[vc]).sort_values(vc, ascending=False)
        n = n_l if country == "lka" else n_u
        d = d.head(n)
        for rank, (_, r) in enumerate(d.iterrows(), start=1):
            w = {
                "category": cat, "rank": rank, "name": str(r["name"]), "lat": float(r["lat"]), "lon": float(r["lon"]),
                "h3": r["h3"], "kind": str(r.get("kind", "")), "rule": rule,
                "value": (float(r[vc]) if vc else None), "unit": unit,
                "domain": r.get("domain"), "sources": sources_by_domain.get(r.get("domain"), []),
                "cap": n if n < 99 else None, "candidates": int(len(df)),
            }
            for extra in ("visitors_year", "year", "rd_usd", "primary_fuel", "wikidata", "iata", "inscribed"):
                if extra in r.index and pd.notna(r[extra]):
                    v = r[extra]
                    w[extra] = v.item() if hasattr(v, "item") else v
            out.append(w)
    return out


# ---------------------------------------------------------------------------------------------
# Cities (City View)
# ---------------------------------------------------------------------------------------------
CITY_RADIUS = {"lka": 3, "usa": 3}
CITIZEN_CLASSES = ["cit_farmers", "cit_workers", "cit_merchants", "cit_engineers", "cit_scholars",
                   "cit_healers", "cit_service", "cit_administrators", "cit_artists", "cit_soldiers"]

FALLBACK_METROS = {
    "lka": [("colombo", "Colombo", 6.9271, 79.8612), ("kandy", "Kandy", 7.2906, 80.6337),
            ("galle", "Galle", 6.0535, 80.2210), ("jaffna", "Jaffna", 9.6615, 80.0255)],
    "usa": [("nyc", "New York", 40.7128, -74.0060), ("la", "Los Angeles", 34.0522, -118.2437),
            ("chicago", "Chicago", 41.8781, -87.6298), ("houston", "Houston", 29.7604, -95.3698)],
}


def city_size(pop: float) -> int:
    return max(1, int(round((max(pop, 0) ** 0.4) / 20)))


def largest_remainder(shares: dict[str, float], total: int) -> dict[str, int]:
    s = sum(v for v in shares.values() if v > 0)
    if s <= 0 or total <= 0:
        return {k: 0 for k in shares}
    raw = {k: max(v, 0) / s * total for k, v in shares.items()}
    base = {k: int(math.floor(v)) for k, v in raw.items()}
    rest = total - sum(base.values())
    for k in sorted(raw, key=lambda k: raw[k] - base[k], reverse=True)[:rest]:
        base[k] += 1
    return base


def build_cities(country: str, tiles: pd.DataFrame, H: pd.DataFrame, wonders: list[dict]) -> list[dict]:
    metros_f = INTERIM / "national" / "metros.json"
    metros = []
    if metros_f.exists():
        metros = [m for m in json.loads(metros_f.read_text()) if m.get("country") == country]
    if not metros:
        metros = [{"id": i, "name": n, "lat": la, "lon": lo, "country": country} for i, n, la, lo in FALLBACK_METROS[country]]
    res = COUNTRIES[country]["h3_res"]
    rad = CITY_RADIUS[country]
    grid = set(tiles.index)
    out = []
    lq_keys = [k for k in H.columns if k.startswith("emp_")]
    nat_emp = H[lq_keys].sum() if lq_keys else None
    nat_tot = H["employed"].sum() if "employed" in H.columns else None
    for m in metros:
        c0 = h3.latlng_to_cell(float(m["lat"]), float(m["lon"]), res)
        # centre = most populous tile within 1 ring of the city's coordinates
        near = [c for c in h3.grid_disk(c0, 1) if c in grid]
        if not near:
            continue
        centre = max(near, key=lambda c: H.at[c, "pop"] if c in H.index else 0)
        ring_cells = [c for c in h3.grid_disk(centre, rad) if c in grid]
        sub = H.loc[ring_cells]
        t = tiles.loc[ring_cells]
        pop = float(sub["pop"].sum())
        gdp = float(sub["gdp_usd"].sum()) if "gdp_usd" in sub else None
        cit = {k: float(sub[k].sum()) for k in CITIZEN_CLASSES if k in sub.columns}
        size = city_size(pop)
        icons = largest_remainder(cit, size) if cit else {}
        industries = []
        if lq_keys and nat_tot:
            emp_tot = sub["employed"].sum() if "employed" in sub else 0
            for k in lq_keys:
                if emp_tot > 0 and nat_emp[k] > 0:
                    industries.append({"key": k, "jobs": float(sub[k].sum()),
                                       "lq": float((sub[k].sum() / emp_tot) / (nat_emp[k] / nat_tot))})
            industries.sort(key=lambda r: r["lq"], reverse=True)
        tile_rows = []
        for c in ring_cells:
            r = t.loc[c]
            tile_rows.append({
                "h3": c, "d": int(h3.grid_distance(centre, c)), "terrain": r["terrain"], "feature": r["feature"],
                "hills": bool(r["hills"]), "district": "city_center" if c == centre else r["district"],
                "yields": [int(r[f"y_{y}"]) for y in YIELDS], "pop": float(H.at[c, "pop"]),
            })
        ring_set = set(ring_cells)
        out.append({
            "id": str(m.get("id")), "name": m.get("name"), "lat": float(m["lat"]), "lon": float(m["lon"]),
            "centre": centre, "radius": rad, "tiles": tile_rows,
            "pop": pop, "gdp_usd": gdp, "gdp_per_capita": (gdp / pop if gdp and pop else None),
            "official_pop": m.get("population"), "official_pop_year": m.get("population_year"),
            "official_gdp_usd": m.get("gdp_usd"), "official_gdp_year": m.get("gdp_year"),
            "admin_ids": m.get("admin_ids"), "wikidata": m.get("wikidata"),
            "size": size, "citizens": cit, "citizen_icons": icons,
            "employed": float(sub["employed"].sum()) if "employed" in sub else None,
            "yields": {y: int(t[f"y_{y}"].sum()) for y in YIELDS},
            "industries": industries[:6],
            "wonders": [w["name"] for w in wonders if w.get("h3") in ring_set],
            "districts": sorted({r["district"] for r in tile_rows if r["district"]}),
        })
        tiles.at[centre, "district"] = "city_center"
    return out


def build(country: str) -> dict:
    tiles, ctx = build_tiles(country)
    out = INTERIM / country / "civ"
    out.mkdir(parents=True, exist_ok=True)
    rmetrics = json.loads((INTERIM / country / "reconciled" / "metrics.json").read_text())
    by_domain: dict[str, list[str]] = {}
    for m in rmetrics:
        by_domain.setdefault(m["domain"], [])
        for s in m.get("sources", []):
            if s not in by_domain[m["domain"]]:
                by_domain[m["domain"]].append(s)
    wonders = build_wonders(country, ctx["pts"], tiles, by_domain)
    cities = build_cities(country, tiles, ctx["H"], wonders)
    tiles.to_parquet(out / "tiles.parquet")
    (out / "wonders.json").write_text(json.dumps(wonders, indent=1, default=str))
    (out / "cities.json").write_text(json.dumps(cities, indent=1, default=str))
    summary = {
        "terrain": tiles.terrain.value_counts().to_dict(),
        "districts": tiles.district.value_counts().to_dict(),
        "yield_totals": {y: int(tiles[f"y_{y}"].sum()) for y in YIELDS},
        "wonders": len(wonders), "cities": len(cities),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    import sys
    for cc in sys.argv[1:] or ["lka", "usa"]:
        print(cc, json.dumps(build(cc), indent=1, default=str))
