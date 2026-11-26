"""Core geography: admin boundaries, population rasters, H3 hex grid and the admin<->hex crosswalk.

Outputs (data/interim/<country>/core/):
  admin_<level>.parquet   GeoDataFrame: admin_id, name, parent_id, area_km2, pop, geometry
  pieces.parquet          h3, <finest admin id + all parents>, pop, land_km2  (the crosswalk)
  hexes.parquet           one row per hex (land + water rings): h3, lat, lon, ring, hex_km2,
                          land_km2, pop, adm1, adm2[, adm3] (dominant unit by land area)

The crosswalk is built from the population raster: every pixel whose centre falls inside the
country is tagged with (H3 cell, finest admin unit).  Summing pixels gives pieces of
hex ∩ admin with their population and land area, which every later admin->hex allocation uses.
Population is rescaled so each official admin unit total is matched exactly.
"""
from __future__ import annotations

import json

import geopandas as gpd
import h3
import h3.api.numpy_int as h3i
import numpy as np
import pandas as pd
import rasterio
from rasterio import features

from .config import COUNTRIES, US_STATE_FIPS, CACHE, interim_dir
from .fetch import fetch, unzip
from .schema import Metric, Source, write_domain_meta

DOMAIN = "core"

SOURCES = [
    Source("ocha_codab_lka", "Sri Lanka - Subnational Administrative Boundaries (COD-AB, v03)",
           "OCHA FISS / Survey Department of Sri Lanka & Department of Census and Statistics",
           "https://data.humdata.org/dataset/cod-ab-lka", "CC BY-IGO", "2022-08-16", ["lka"],
           "Province (ADM1), district (ADM2), divisional secretariat (ADM3) polygons."),
    Source("unfpa_codps_lka", "Sri Lanka - Subnational Population Statistics (COD-PS, 2023)",
           "UNFPA / Department of Census and Statistics Sri Lanka",
           "https://data.humdata.org/dataset/cod-ps-lka", "CC BY-IGO", "2023", ["lka"],
           "District mid-year population 2023 (DCS estimates)."),
    Source("census_cb_2023", "Cartographic Boundary Files 2023 (states, counties, 1:500,000)",
           "U.S. Census Bureau", "https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
           "public domain", "2023", ["usa"], "Connecticut uses the 9 planning regions (FIPS 09110-09190)."),
    Source("census_pep_2023", "County Population Totals: 2020-2023 (Vintage 2023)",
           "U.S. Census Bureau, Population Estimates Program",
           "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/counties/totals/",
           "public domain", "2023", ["usa"]),
    Source("worldpop_2020", "WorldPop Global Project Population Data 2020 (unconstrained, people per pixel)",
           "WorldPop, University of Southampton", "https://hub.worldpop.org/project/categories?id=3",
           "CC BY 4.0", "2020", ["lka", "usa"],
           "Sri Lanka at 3 arc-sec (~100 m); United States at 30 arc-sec (~1 km). Used only as the "
           "within-unit distribution: totals are rescaled to official admin populations."),
    Source("uber_h3", "H3 hierarchical hexagonal grid", "Uber Technologies (open source)",
           "https://h3geo.org", "Apache-2.0", "v4", ["lka", "usa"]),
]

METRICS = [
    Metric("pop", "Population", "persons", "population", "extensive", "pop", "hexes",
           ["worldpop_2020", "unfpa_codps_lka", "census_pep_2023"], "2023",
           "Resident population.",
           "WorldPop 2020 pixels summed per hex, rescaled so every district (LKA) / county (USA) "
           "matches the official 2023 estimate."),
    Metric("land_km2", "Land area", "km2", "population", "extensive", "land", "hexes",
           ["ocha_codab_lka", "census_cb_2023"], "2023", "Land area inside the country boundary.",
           "Sum of raster-pixel areas whose centres fall inside the admin boundaries."),
    Metric("pop_density", "Population density", "persons/km2", "population", "intensive", "land", "hexes",
           ["worldpop_2020", "unfpa_codps_lka", "census_pep_2023"], "2023",
           "People per km² of land.", "pop / land_km2", "neutral"),
]

POP_RASTER = {
    "lka": ("https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/LKA/lka_ppp_2020.tif", "lka/population", None),
    "usa": ("https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/USA/usa_ppp_2020_1km_Aggregated.tif", "usa/population", None),
}


# ---------------------------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------------------------

def _lka_gdb():
    z = fetch("https://data.humdata.org/dataset/0bedcaf3-88cd-4591-b9d5-5d3220e26abf/resource/"
              "3bc0e89f-92c9-42f7-86f6-062949cbf8f1/download/lka_admin_boundaries.gdb.zip",
              "ocha_codab_lka", "lka/boundaries", "lka_admin_boundaries.gdb.zip")
    d = unzip(z)
    return next(d.rglob("*.gdb"))


def load_boundaries_raw(country: str, level: str) -> gpd.GeoDataFrame:
    """Return admin polygons with columns admin_id, name, parent_id, geometry (EPSG:4326)."""
    if country == "lka":
        n = level[-1]
        g = gpd.read_file(_lka_gdb(), layer=f"lka_admin{n}")
        out = gpd.GeoDataFrame({
            "admin_id": g[f"adm{n}_pcode"],
            "name": g[f"adm{n}_name"],
            "parent_id": g[f"adm{int(n) - 1}_pcode"] if n != "1" else "LK",
        }, geometry=g.geometry, crs=g.crs)
    elif country == "usa":
        if level == "adm1":
            z = fetch("https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip",
                      "census_cb_2023", "usa/boundaries")
            g = gpd.read_file(unzip(z, ".shp"))
            g = g[g.STATEFP.isin(US_STATE_FIPS)]
            out = gpd.GeoDataFrame({"admin_id": g.STATEFP, "name": g.NAME, "parent_id": "US",
                                    "abbr": g.STUSPS}, geometry=g.geometry, crs=g.crs)
        elif level == "adm2":
            z = fetch("https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip",
                      "census_cb_2023", "usa/boundaries")
            g = gpd.read_file(unzip(z, ".shp"))
            g = g[g.STATEFP.isin(US_STATE_FIPS)]
            out = gpd.GeoDataFrame({"admin_id": g.GEOID, "name": g.NAMELSAD, "parent_id": g.STATEFP,
                                    "state_abbr": g.STUSPS}, geometry=g.geometry, crs=g.crs)
        else:
            raise ValueError(level)
    else:
        raise ValueError(country)
    out = out.to_crs(4326).reset_index(drop=True)
    return out


def official_population(country: str) -> pd.Series:
    """Official population by the admin level used for rescaling (LKA adm2, USA adm2)."""
    if country == "lka":
        p = fetch("https://data.humdata.org/dataset/e982aec3-478e-4910-ae5c-072ae2cf6933/resource/"
                  "4405f46a-e4b3-4778-9290-131613a7ce71/download/lka_admpop_adm2_2023.csv",
                  "unfpa_codps_lka", "lka/population", "lka_admpop_adm2_2023.csv")
        d = pd.read_csv(p)
        return d.set_index("ADM2_PCODE")["T_TL"].astype(float)
    p = fetch("https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/counties/totals/co-est2023-alldata.csv",
              "census_pep_2023", "usa/population")
    d = pd.read_csv(p, encoding="latin-1", dtype={"STATE": str, "COUNTY": str})
    d = d[d.SUMLEV == 50]
    d["admin_id"] = d.STATE + d.COUNTY
    return d.set_index("admin_id")["POPESTIMATE2023"].astype(float)


# ---------------------------------------------------------------------------------------------
# Crosswalk
# ---------------------------------------------------------------------------------------------

def _pixel_area_km2(transform, rows: np.ndarray) -> np.ndarray:
    lat = transform.f + (rows + 0.5) * transform.e
    dx = abs(transform.a) * 111.320 * np.cos(np.radians(lat))
    dy = abs(transform.e) * 110.574
    return dx * dy


def build_crosswalk(country: str, res: int | None = None) -> tuple[pd.DataFrame, dict]:
    cfg = COUNTRIES[country]
    res = res or cfg["h3_res"]
    finest = cfg["finest_admin"]
    url, sub, name = POP_RASTER[country]
    tif = fetch(url, "worldpop_2020", sub, name)

    admins = {lvl: load_boundaries_raw(country, lvl) for lvl in cfg["admin_levels"]}
    fine = admins[finest]
    codes = {aid: i + 1 for i, aid in enumerate(fine.admin_id)}

    with rasterio.open(tif) as src:
        pop = src.read(1, masked=True)
        transform = src.transform
        shape = src.shape
    popv = np.where(pop.mask, 0.0, pop.filled(0)).astype(np.float64)
    popv[~np.isfinite(popv)] = 0
    popv[popv < 0] = 0

    shapes = ((geom, codes[aid]) for geom, aid in zip(fine.geometry, fine.admin_id))
    lab = features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="int32")
    # recover populated pixels that straddle the coastline / boundary (all_touched pass)
    miss = (lab == 0) & (popv > 0)
    if miss.any():
        shapes = ((geom, codes[aid]) for geom, aid in zip(fine.geometry, fine.admin_id))
        lab2 = features.rasterize(shapes, out_shape=shape, transform=transform, fill=0,
                                  dtype="int32", all_touched=True)
        lab = np.where(miss, lab2, lab)

    rows, cols = np.nonzero(lab)
    lats = transform.f + (rows + 0.5) * transform.e
    lons = transform.c + (cols + 0.5) * transform.a
    cells = np.fromiter((h3i.latlng_to_cell(a, b, res) for a, b in zip(lats.tolist(), lons.tolist())),
                        dtype=np.uint64, count=len(rows))
    df = pd.DataFrame({
        "cell": cells,
        "code": lab[rows, cols],
        "pop_raw": popv[rows, cols],
        "land_km2": _pixel_area_km2(transform, rows),
    })
    pieces = df.groupby(["cell", "code"], as_index=False).agg(pop_raw=("pop_raw", "sum"),
                                                              land_km2=("land_km2", "sum"))
    inv = {v: k for k, v in codes.items()}
    pieces[finest] = pieces.code.map(inv)
    pieces["h3"] = [h3.int_to_str(int(c)) for c in pieces.cell]
    pieces = pieces.drop(columns=["code", "cell"])

    # attach parent levels
    if country == "lka":
        p3 = admins["adm3"].set_index("admin_id")["parent_id"]
        pieces["adm2"] = pieces.adm3.map(p3)
        p2 = admins["adm2"].set_index("admin_id")["parent_id"]
        pieces["adm1"] = pieces.adm2.map(p2)
    else:
        pieces["adm1"] = pieces.adm2.str[:2]

    # rescale to official totals
    off = official_population(country)
    scale_level = "adm2"
    raw_tot = pieces.groupby(scale_level).pop_raw.sum()
    factor = (off.reindex(raw_tot.index) / raw_tot).replace([np.inf, -np.inf], np.nan)
    missing = factor[factor.isna()].index.tolist()
    pieces["pop"] = pieces.pop_raw * pieces[scale_level].map(factor).fillna(1.0)
    # official units with zero raw population: spread their total over their land area
    for aid in off.index.difference(raw_tot.index[raw_tot > 0]):
        m = pieces[scale_level] == aid
        if m.any():
            pieces.loc[m, "pop"] = off[aid] * pieces.loc[m, "land_km2"] / pieces.loc[m, "land_km2"].sum()
    report = {
        "res": res,
        "pixels_in_country": int(len(df)),
        "raw_pop_total": float(df.pop_raw.sum()),
        "official_pop_total": float(off.sum()),
        "final_pop_total": float(pieces["pop"].sum()),
        "units_without_official_pop": missing,
        "official_units_not_on_grid": sorted(set(off.index) - set(pieces[scale_level])),
    }
    cols = ["h3", "adm1", "adm2"] + (["adm3"] if "adm3" in pieces else []) + ["pop", "pop_raw", "land_km2"]
    return pieces[cols], report


def build_hexes(country: str, pieces: pd.DataFrame, res: int) -> pd.DataFrame:
    cfg = COUNTRIES[country]
    land = pieces.groupby("h3").agg(pop=("pop", "sum"), land_km2=("land_km2", "sum")).reset_index()
    land["ring"] = 0
    # dominant admin unit by land area at each level
    for lvl in cfg["admin_levels"]:
        dom = (pieces.groupby(["h3", lvl]).land_km2.sum().reset_index()
               .sort_values("land_km2", ascending=False).drop_duplicates("h3").set_index("h3")[lvl])
        land[lvl] = land.h3.map(dom)
    landset = set(land.h3)
    rings = []
    frontier = landset
    seen = set(landset)
    for k in range(1, cfg["water_rings"] + 1):
        nxt = set()
        for c in frontier:
            for n in h3.grid_ring(c, 1):
                if n not in seen:
                    nxt.add(n)
        seen |= nxt
        rings.append(pd.DataFrame({"h3": sorted(nxt), "ring": k}))
        frontier = nxt
    water = pd.concat(rings, ignore_index=True)
    water["pop"] = 0.0
    water["land_km2"] = 0.0
    hexes = pd.concat([land, water], ignore_index=True)
    # water hexes inherit the admin units of their nearest land neighbour (for coastal allocation)
    lvl_cols = list(cfg["admin_levels"])
    lookup = land.set_index("h3")[lvl_cols]
    for lvl in lvl_cols:
        hexes[lvl] = hexes[lvl].astype(object)
    for idx in hexes.index[hexes.ring > 0]:
        c = hexes.at[idx, "h3"]
        for k in range(1, cfg["water_rings"] + 2):
            nb = [n for n in h3.grid_ring(c, k) if n in lookup.index]
            if nb:
                for lvl in lvl_cols:
                    hexes.at[idx, lvl] = lookup.loc[nb[0], lvl]
                break
    ll = np.array([h3.cell_to_latlng(c) for c in hexes.h3])
    hexes["lat"] = ll[:, 0]
    hexes["lon"] = ll[:, 1]
    hexes["hex_km2"] = [h3.cell_area(c, unit="km^2") for c in hexes.h3]
    hexes["pop_density"] = np.where(hexes.land_km2 > 0, hexes["pop"] / hexes.land_km2.clip(lower=1e-9), 0.0)
    return hexes


def admin_tables(country: str, pieces: pd.DataFrame) -> dict[str, gpd.GeoDataFrame]:
    cfg = COUNTRIES[country]
    out = {}
    for lvl in cfg["admin_levels"]:
        g = load_boundaries_raw(country, lvl)
        agg = pieces.groupby(lvl).agg(pop=("pop", "sum"), land_km2=("land_km2", "sum"))
        g["pop"] = g.admin_id.map(agg["pop"]).fillna(0.0)
        g["land_km2"] = g.admin_id.map(agg["land_km2"]).fillna(0.0)
        rp = g.representative_point()
        g["lat"] = rp.y
        g["lon"] = rp.x
        out[lvl] = g
    return out


def build(country: str, res: int | None = None, tag: str = "core") -> dict:
    cfg = COUNTRIES[country]
    res = res or cfg["h3_res"]
    out = interim_dir(country, tag)
    pieces, report = build_crosswalk(country, res)
    hexes = build_hexes(country, pieces, res)
    pieces.to_parquet(out / "pieces.parquet", index=False)
    hexes.to_parquet(out / "hexes.parquet", index=False)
    if tag == "core":
        for lvl, g in admin_tables(country, pieces).items():
            g.to_parquet(out / f"admin_{lvl}.parquet", index=False)
    report.update({
        "land_hexes": int((hexes.ring == 0).sum()),
        "water_hexes": int((hexes.ring > 0).sum()),
        "land_km2": float(hexes.land_km2.sum()),
    })
    (out / "report.json").write_text(json.dumps(report, indent=2))
    hexes[["h3", "pop", "land_km2", "pop_density"]].to_csv(out / "hexes.csv", index=False)
    write_domain_meta(out, METRICS, SOURCES, country)
    return report


# ---------------------------------------------------------------------------------------------
# Helpers for domain modules
# ---------------------------------------------------------------------------------------------

def load_hexes(country: str, tag: str = "core") -> pd.DataFrame:
    from .config import INTERIM
    return pd.read_parquet(INTERIM / country / tag / "hexes.parquet")


def load_pieces(country: str, tag: str = "core") -> pd.DataFrame:
    from .config import INTERIM
    return pd.read_parquet(INTERIM / country / tag / "pieces.parquet")


def load_admin(country: str, level: str) -> gpd.GeoDataFrame:
    from .config import INTERIM
    return gpd.read_parquet(INTERIM / country / "core" / f"admin_{level}.parquet")


def point_to_hex(lat: float, lon: float, country: str, grid: set[str] | None = None, max_k: int = 6) -> str | None:
    """H3 cell for a point, snapped to the nearest cell of the country grid (within max_k rings)."""
    res = COUNTRIES[country]["h3_res"]
    c = h3.latlng_to_cell(lat, lon, res)
    if grid is None or c in grid:
        return c
    for k in range(1, max_k + 1):
        for n in h3.grid_ring(c, k):
            if n in grid:
                return n
    return None


if __name__ == "__main__":
    import sys
    for cc in (sys.argv[1:] or ["lka", "usa"]):
        print(cc, json.dumps(build(cc), indent=2))
