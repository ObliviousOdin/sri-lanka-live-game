"""Terrain domain: elevation, slope, land cover and climate per hex (raster zonal statistics).

    python -m civmap.domains.terrain            # builds lka + usa, then validates

Output (data/interim/<country>/terrain/hex.csv), one row per hex of civmap.core.load_hexes()
(land hexes and the water rings):

* Elevation (Copernicus DEM GLO-90, AWS open-data COGs): full native 3 arc-second (~90 m) tiles
  for BOTH countries, so every elevation key (in particular slope, which roughly halves when
  measured on a 4x coarser grid) means the same thing in Sri Lanka and the US.  Every DEM
  pixel is assigned to the hex whose polygon contains its centre (rasterised H3 boundaries),
  so statistics use all pixels, not a point sample.  Ocean is 0 m in the DEM and tiles exist
  only over land; elevation statistics are computed over pixels with DEM != 0 (land and
  inland-water surfaces).  Mean / min / max / slope are streamed tile by tile; the relief
  percentiles come from a per-hex 0.1 m histogram (a second pass), so the ~1.4 billion US
  pixels never have to be held in memory at once.
* Land cover (ESA WorldCover 10 m 2021 v200).  Sri Lanka: every native 10 m pixel (whole tiles
  downloaded, processed in windows).  United States: the COG's 4x internal overview (~40 m).
  The overviews are MODE-resampled (checked: 99.6-99.98% of 8x overview pixels equal the mode
  of their 8x8 block of 10 m pixels), so scattered minority classes shrink.  Checked against
  the native 10 m pixels around 80 random US land hexes (area-uniform, so mostly rural): at 4x
  built-up is -11% relative (max 0.5 percentage points per hex), shrub / wetland / moss -1 to
  -2%, all other classes within +-0.5%; at 8x (used earlier) built-up was -26%.  In dense
  urban hexes the bias reverses because built-up is the dominant class (4x: Manhattan +1.9,
  Silicon Valley +2.9 points; 8x: +3.9 / +7.0).  Sri Lanka uses native 10 m, so US built-up
  is somewhat understated relative to Sri Lanka (national US built_km2 rose 12% from 8x to 4x).
  Fractions are over the whole hex; ocean outside the tiles / no-data = water.
* Allocation weights cropland_km2 / tree_km2 / built_km2 / grass_km2 = class share of the
  hex's *in-country* pixels x core land_km2, so Canadian / Mexican land in border hexes does
  not count.
* Climate (WorldClim 2.1, 1970-2000 normals, 2.5 arc-min grid for both countries): BIO1 mean
  annual temperature, BIO12 annual precipitation, mean of the H3 child-point samples (res+2,
  49 points per hex).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# GDAL remote-read settings (set before rasterio initialises GDAL).
for _k, _v in {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_CACHEMAX": "256",  # MB; keep the block cache small, whole overviews are read at once
}.items():
    os.environ.setdefault(_k, _v)
if "CURL_CA_BUNDLE" not in os.environ and Path("/root/.ccr/ca-bundle.crt").exists():
    os.environ["CURL_CA_BUNDLE"] = "/root/.ccr/ca-bundle.crt"

import h3  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import shapely  # noqa: E402
import shapely.affinity  # noqa: E402
from rasterio import features  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.windows import Window  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

from civmap import util  # noqa: E402
from civmap.config import CACHE, COUNTRIES, interim_dir  # noqa: E402
from civmap.core import SOURCES as CORE_SOURCES  # noqa: E402
from civmap.core import load_admin, load_hexes  # noqa: E402
from civmap.fetch import fetch  # noqa: E402
from civmap.schema import Metric, Source, write_domain_meta  # noqa: E402

DOMAIN = "terrain"

DEM_BASE = "https://copernicus-dem-90m.s3.amazonaws.com"
WC_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021"
WCLIM_BASE = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base"

# Per-country raster resolution choices (see module docstring).  The DEM is always read at its
# native 3 arc-second resolution (both countries) so the elevation keys are comparable.
CFG = {
    "lka": {"wc_factor": 1, "clim": "2.5m", "clim_child": 2},
    "usa": {"wc_factor": 4, "clim": "2.5m", "clim_child": 2},
}
THREADS = 8      # parallel downloads of whole files (DEM tiles, 10 m WorldCover tiles)
COG_THREADS = 4  # parallel /vsicurl/ overview reads (each holds a decoded 9000 x 9000 array, ~330 MB peak)
WC_CHUNK = 4000  # window size for WorldCover processing (10 m windows / overview blocks)
DEM_BIN = 0.1    # m, histogram bin width for the per-hex relief percentiles

# ESA WorldCover classes -> metric keys.  0 (no data: open ocean inside a tile) counts as water.
WC_CLASSES = [
    (10, "lc_tree", "Tree cover"),
    (20, "lc_shrub", "Shrubland"),
    (30, "lc_grass", "Grassland"),
    (40, "lc_crop", "Cropland"),
    (50, "lc_built", "Built-up"),
    (60, "lc_bare", "Bare / sparse vegetation"),
    (70, "lc_snow", "Snow and ice"),
    (80, "lc_water", "Permanent water bodies"),
    (90, "lc_wetland", "Herbaceous wetland"),
    (95, "lc_mangrove", "Mangroves"),
    (100, "lc_moss", "Moss and lichen"),
]
NC = len(WC_CLASSES)
WATER_IDX = [k for k, (c, _, _) in enumerate(WC_CLASSES) if c == 80][0]
_LUT = np.full(256, WATER_IDX, dtype=np.int64)
for _i, (_c, _, _) in enumerate(WC_CLASSES):
    _LUT[_c] = _i

# ---------------------------------------------------------------------------------------------
# Sources & metrics
# ---------------------------------------------------------------------------------------------

_core = {s.id: s for s in CORE_SOURCES}

SOURCES = [
    Source("copernicus_dem_glo90", "Copernicus DEM GLO-90 (Copernicus_DSM_COG_30, Cloud Optimized GeoTIFF)",
           "European Space Agency / Copernicus Programme (DEM produced by Airbus Defence and Space from TanDEM-X)",
           "https://registry.opendata.aws/copernicus-dem/",
           "GLO-90 is available on a free basis for the general public under the terms and conditions of "
           "the Copernicus DEM licence (https://dataspace.copernicus.eu/explore-data/data-collections/"
           "copernicus-contributing-missions/collections-description/COP-DEM)",
           "Copernicus DEM 2021 release", ["lka", "usa"],
           "1x1 degree tiles s3://copernicus-dem-90m/Copernicus_DSM_COG_30_<N|S>yy_00_<E|W>xxx_00_DEM/ "
           "(tile list: tileList.txt). Digital surface model (includes canopy and buildings), heights "
           "above EGM2008 geoid, ocean = 0 m; tiles exist only where there is land (readme: "
           "https://copernicus-dem-30m.s3.amazonaws.com/readme.html). Both countries: whole native "
           "3 arc-second tiles downloaded to data/cache/terrain/copdem90/ (12 tiles for Sri Lanka, "
           "1,445 for the US grid incl. border/sea-ring hexes)."),
    Source("esa_worldcover_2021", "ESA WorldCover 10 m 2021 v200 (land cover map)",
           "European Space Agency, ESA WorldCover consortium (Zanaga et al. 2022)",
           "https://esa-worldcover.org/en", "CC BY 4.0", "2021", ["lka", "usa"],
           "3x3 degree COG tiles s3://esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_<tile>_Map.tif "
           "(tile grid esa_worldcover_grid.geojson). Sri Lanka: whole 10 m tiles downloaded "
           "(data/cache/terrain/worldcover_10m/). USA: the 4x internal overview (~40 m, mode-resampled) "
           "read with GDAL /vsicurl/ and cached as .npz under data/cache/terrain/worldcover_f4/. "
           "11 classes (10 tree ... 100 moss & lichen); 0 = no data (open sea)."),
    Source("worldclim_21", "WorldClim version 2.1 bioclimatic variables (1970-2000 averages)",
           "WorldClim (Fick, S.E. & R.J. Hijmans, 2017, Int. J. Climatology 37:4302-4315)",
           "https://www.worldclim.org/data/worldclim21.html",
           "Free for academic and other non-commercial use; redistribution or commercial use not allowed "
           "without prior permission (WorldClim licence). Only hex-level aggregates are derived here.",
           "1970-2000 (released January 2020)", ["lka", "usa"],
           "BIO1 (annual mean temperature, deg C) and BIO12 (annual precipitation, mm) from the "
           "2.5 arc-minute grid (wc2.1_2.5m_bio.zip, ~4.6 km) for both countries."),
    _core["uber_h3"],
    _core["ocha_codab_lka"],
    _core["census_cb_2023"],
    _core["worldpop_2020"],
]

_ELEV_SRC = ["copernicus_dem_glo90", "uber_h3"]
_LC_SRC = ["esa_worldcover_2021", "uber_h3"]
_W_SRC = ["esa_worldcover_2021", "ocha_codab_lka", "census_cb_2023", "worldpop_2020", "uber_h3"]
_CLIM_SRC = ["worldclim_21", "uber_h3"]

_DEM_METHOD = (
    "Copernicus GLO-90 DSM pixels assigned to the H3 hex whose boundary contains the pixel centre "
    "(H3 polygons rasterised on each DEM tile grid). Both countries use the native 3 arc-sec tiles "
    "(~90 m N-S; E-W spacing widens north of 50N following the GLO tiling: 4.5 arc-sec at 50-60N, "
    "6 at 60-70N, 9 at 70-80N), so values are comparable between Sri Lanka and the US. "
    "Statistics use pixels with DEM != 0 (land and inland-water surfaces, which the DEM flattens; "
    "ocean is 0 m and absent tiles are sea); hexes without such pixels get 0 m and 0 deg. Means "
    "are weighted by pixel area. DSM heights include canopy/buildings, relative to the EGM2008 "
    "geoid; 90 m pixels smooth sharp summits, so hex maxima sit somewhat below surveyed peak heights. "
    "Border hexes are described as whole hexes (foreign land inside a US border hex is included)."
)
_SLOPE_METHOD = (
    " Slope = atan(|grad z|) from central differences (numpy.gradient) on the native 90 m DEM grid "
    "with metric pixel spacing (dx = deg x 111.32 km x cos(lat), dy = deg x 110.57 km), one-sided at "
    "tile edges, averaged (pixel-area weighted) over the hex's DEM != 0 pixels. The same grid is used "
    "in both countries because slope is strongly resolution dependent (on a 4x coarser, "
    "~370 m grid the Sri Lankan hex slopes are ~0.57x the 90 m values); 30 m or lidar DEMs give "
    "steeper values still."
)
_LC_METHOD = (
    "Share of the hex's pixels in WorldCover class {code} ({name}). WorldCover pixels ({res}) "
    "are assigned to the hex containing their centre (rasterised H3 "
    "polygons); fractions are over the whole hex area, so the 11 classes sum to 1. Open ocean "
    "(no-data 0 inside tiles, or no tile at all) is counted as water (class 80). Border hexes are "
    "described as whole hexes (foreign land included); the *_km2 weights are in-country only."
)
_RES_TXT = ("Sri Lanka: all native 10 m pixels; USA: the COG's 4x internal overview, 1/3000 deg ~ 40 m, "
            "which was built with mode resampling, so scattered minority classes are under-represented: "
            "against native 10 m pixels around 80 random US land hexes (mostly rural), built-up was -11% "
            "relative (max 0.5 percentage points per hex), shrub/wetland/moss -1 to -2%, other classes "
            "within +-0.5%; in dense urban hexes built-up is instead over-represented by ~2-3 points "
            "(Manhattan, Silicon Valley). Sri Lankan shares (10 m) have no such bias")
_W_METHOD = (
    "{what} area (km2) = share of the hex's in-country WorldCover pixels (centre inside the "
    "COD-AB / Census cartographic boundary union) in {cls} x core land_km2 of the hex (WorldPop "
    "pixel-area crosswalk). Border hexes therefore exclude Canadian/Mexican land; water-ring hexes "
    "with land_km2 = 0 get 0. Hexes with land_km2 > 0 but no in-country WorldCover pixel at the "
    "processing resolution fall back to the whole-hex non-water shares. Intended as the '{w}' "
    "allocation weight; note that civmap.reconcile currently derives its '{w}' weight from the "
    "whole-hex lc_* fraction x piece land area instead, which under-weights part-sea coastal hexes "
    "(Sri Lanka national totals: built -13%, grass+shrub -5%, cropland -2% vs this column)."
)
_CLIM_METHOD = (
    "{bio} of the WorldClim 2.1 1970-2000 normals, nearest-pixel value at the centres of the hex's "
    "H3 children (res+2, 49 points/hex), averaged over points with data, on the 2.5 arc-min grid "
    "(~4.6 km) for both countries. Land hexes with no valid sample "
    "(coastal slivers) take the nearest valid pixel within 10 pixels (~46 km); open-water hexes and "
    "remote atolls (e.g. Northwestern Hawaiian Islands) with no valid sample are left empty."
)

METRICS = [
    Metric("elev_mean_m", "Mean elevation", "m", "elevation", "intensive", "land", "hex", _ELEV_SRC,
           "2021 release", "Mean height of the land (and inland-water) surface in the hex (DSM, metres "
           "above EGM2008 geoid).", _DEM_METHOD, "neutral"),
    Metric("elev_min_m", "Minimum elevation", "m", "elevation", "intensive", "land", "hex", _ELEV_SRC,
           "2021 release", "Lowest land pixel in the hex. A small pit can set it: a few eastern US hexes "
           "reach 70-114 m below sea level in deep ~0.5 km-wide pits, probably open-pit quarries (e.g. "
           "near Columbia SC and Richmond VA); relief_m uses the 5th percentile and is unaffected.",
           _DEM_METHOD, "neutral"),
    Metric("elev_max_m", "Maximum elevation", "m", "elevation", "intensive", "land", "hex", _ELEV_SRC,
           "2021 release", "Highest land pixel in the hex (sharp peaks are smoothed by the ~90 m pixel "
           "size in both countries).", _DEM_METHOD, "neutral"),
    Metric("relief_m", "Local relief", "m", "elevation", "intensive", "land", "hex", _ELEV_SRC,
           "2021 release", "Robust elevation range inside the hex: 95th minus 5th percentile of land pixels "
           "(drives Hills / Mountains terrain).",
           _DEM_METHOD + " Percentiles are the lower order statistics at rank floor(q x (n-1)) of the "
           "hex's n pixels (unweighted), read from a per-hex histogram with 0.1 m bins (error < 0.1 m).",
           "neutral"),
    Metric("slope_mean_deg", "Mean slope", "deg", "elevation", "intensive", "land", "hex", _ELEV_SRC,
           "2021 release", "Average terrain slope of land pixels in the hex (90 m DEM in both countries).",
           _DEM_METHOD + _SLOPE_METHOD, "neutral"),
]
for _code, _key, _name in WC_CLASSES:
    METRICS.append(Metric(
        _key, f"Land cover: {_name}", "share", "landcover", "intensive", "land", "hex", _LC_SRC, "2021",
        f"Fraction (0-1) of the hex covered by ESA WorldCover class {_code} '{_name}'.",
        _LC_METHOD.format(code=_code, name=_name, res=_RES_TXT), "neutral"))
METRICS += [
    Metric("cropland_km2", "Cropland area", "km2", "landcover", "extensive", "none", "hex", _W_SRC, "2021",
           "Area of cropland (WorldCover class 40) inside the country part of the hex.",
           _W_METHOD.format(what="Cropland", cls="class 40", w="cropland"), "neutral"),
    Metric("tree_km2", "Tree-cover area", "km2", "landcover", "extensive", "none", "hex", _W_SRC, "2021",
           "Area of tree cover (WorldCover class 10; mangroves are the separate class 95) inside the "
           "country part of the hex.",
           _W_METHOD.format(what="Tree-cover", cls="class 10", w="tree"), "neutral"),
    Metric("built_km2", "Built-up area", "km2", "landcover", "extensive", "none", "hex", _W_SRC, "2021",
           "Area of built-up land (WorldCover class 50) inside the country part of the hex.",
           _W_METHOD.format(what="Built-up", cls="class 50", w="built"), "neutral"),
    Metric("grass_km2", "Grass & shrub area", "km2", "landcover", "extensive", "none", "hex", _W_SRC, "2021",
           "Area of grassland + shrubland (WorldCover classes 30 + 20; rangeland/pasture proxy) inside the "
           "country part of the hex.",
           _W_METHOD.format(what="Grassland + shrubland", cls="classes 30 + 20", w="grass"), "neutral"),
    Metric("temp_mean_c", "Mean annual temperature", "degC", "environment", "intensive", "land", "hex",
           _CLIM_SRC, "1970-2000", "Annual mean air temperature (WorldClim BIO1).",
           _CLIM_METHOD.format(bio="BIO1 (annual mean temperature, deg C)"), "neutral"),
    Metric("precip_mm", "Annual precipitation", "mm/yr", "environment", "intensive", "land", "hex",
           _CLIM_SRC, "1970-2000", "Total annual precipitation (WorldClim BIO12).",
           _CLIM_METHOD.format(bio="BIO12 (annual precipitation, mm)"), "neutral"),
]

# ---------------------------------------------------------------------------------------------
# Geometry helpers.  Longitudes are handled in a frame [-270, 90) so the Aleutians (172E-180)
# sit next to the rest of Alaska (-188..-180) and nothing crosses a seam; Sri Lanka is unchanged.
# ---------------------------------------------------------------------------------------------


def _frame(lon):
    return ((np.asarray(lon, dtype=float) - 90.0) % 360.0) - 270.0


def _true(lon: float) -> float:
    return lon + 360.0 if lon < -180 else lon


def hex_polygons(cells: list[str]) -> list[Polygon]:
    out = []
    for c in cells:
        la0, lo0 = h3.cell_to_latlng(c)
        f0 = float(_frame(lo0))
        pts = [(f0 + ((lo - lo0 + 180.0) % 360.0 - 180.0), la) for la, lo in h3.cell_to_boundary(c)]
        out.append(Polygon(pts))
    return out


def country_geometry(country: str):
    g = load_admin(country, "adm1").geometry
    parts = []
    for geom in g:
        for p in getattr(geom, "geoms", [geom]):
            if p.bounds[0] > 90:  # Aleutians east of the antimeridian
                p = shapely.affinity.translate(p, xoff=-360.0)
            parts.append(p)
    u = shapely.union_all(parts)
    shapely.prepare(u)
    return u


def tiles_touched(polys: list[Polygon], step: int) -> set[tuple[int, int]]:
    """(lat0, lon0_frame) of every step-degree tile that a hex polygon's bbox touches."""
    out = set()
    for p in polys:
        x0, y0, x1, y1 = p.bounds
        for la in range(int(math.floor(y0 / step)) * step, int(math.floor(y1 / step)) * step + 1, step):
            for lo in range(int(math.floor(x0 / step)) * step, int(math.floor(x1 / step)) * step + 1, step):
                out.add((la, lo))
    return out


def _ns(lat: int, w: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):0{w}d}"


def _ew(lon: int) -> str:
    return f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def dem_tile_name(lat0: int, lon0_true: int) -> str:
    return f"Copernicus_DSM_COG_30_{_ns(lat0, 2)}_00_{_ew(lon0_true)}_00_DEM"


def wc_tile_name(lat0: int, lon0_true: int) -> str:
    return f"{_ns(lat0, 2)}{_ew(lon0_true)}"


def _label(transform: Affine, shape, tree: STRtree, polys: list[Polygon]):
    h, w = shape
    west, north = transform.c, transform.f
    east, south = west + w * transform.a, north + h * transform.e
    idx = tree.query(box(west, south, east, north))
    if len(idx) == 0:
        return None
    lab = features.rasterize(((polys[i], int(i) + 1) for i in idx), out_shape=shape, transform=transform,
                             fill=0, dtype="int32")
    return lab


def _shift(transform: Affine, dx: float) -> Affine:
    return Affine(transform.a, transform.b, transform.c + dx, transform.d, transform.e, transform.f)


# ---------------------------------------------------------------------------------------------
# Cached remote COG reads (decimated overviews) with fetch-style provenance sidecars
# ---------------------------------------------------------------------------------------------


def read_cog_cached(url: str, source_id: str, subdir: str, key: str, factor: int):
    """Read a COG (whole tile) at 1/factor resolution via /vsicurl/, cached as .npz + .meta.json.

    Returns (array, Affine transform in true longitudes).
    """
    d = CACHE / subdir
    d.mkdir(parents=True, exist_ok=True)
    npy = d / f"{key}.npz"
    meta = npy.with_name(npy.name + ".meta.json")
    if npy.exists() and meta.exists():
        m = json.loads(meta.read_text())
        with np.load(npy) as z:
            return z["a"], Affine(*m["transform"])
    last = None
    for attempt in range(5):
        try:
            with rasterio.open("/vsicurl/" + url) as s:
                ovs = s.overviews(1)
                base_h, base_w = s.height, s.width
            if factor == 1:
                with rasterio.open("/vsicurl/" + url) as s:
                    arr, tr = s.read(1), s.transform
            elif factor in ovs:
                with rasterio.open("/vsicurl/" + url, overview_level=ovs.index(factor)) as s:
                    arr, tr = s.read(1), s.transform
            else:  # no matching overview: nearest decimation of the closest finer level
                with rasterio.open("/vsicurl/" + url) as s:
                    oh, ow = math.ceil(base_h / factor), math.ceil(base_w / factor)
                    arr = s.read(1, out_shape=(oh, ow))
                    tr = s.transform * Affine.scale(base_w / ow, base_h / oh)
            break
        except Exception as e:  # noqa: BLE001 - retry transient HTTP errors
            last = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"COG read failed for {url}: {last}")
    tmp = npy.with_name(npy.name + ".part.npz")
    np.savez_compressed(tmp, a=arr)
    tmp.replace(npy)
    meta.write_text(json.dumps({
        "url": url,
        "source_id": source_id,
        "bytes": npy.stat().st_size,
        "sha256": hashlib.sha256(npy.read_bytes()).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_type": "application/x-npz (numpy savez_compressed, array 'a')",
        "access": f"GDAL /vsicurl/ range read of the whole tile at 1/{factor} resolution "
                  f"({'full resolution' if factor == 1 else 'internal COG overview'})",
        "overview_factor": factor,
        "base_shape": [base_h, base_w],
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "transform": list(tr)[:6],
    }, indent=2))
    return arr, tr


def _cache_only(url: str, source_id: str, subdir: str, key: str, factor: int) -> None:
    """Make sure a tile is in the read_cog_cached cache without keeping the array around."""
    npy = CACHE / subdir / f"{key}.npz"
    if npy.exists() and npy.with_name(npy.name + ".meta.json").exists():
        return None
    read_cog_cached(url, source_id, subdir, key, factor)
    return None


def _prefetch(jobs, label: str):
    """Cache read_cog_cached jobs in a thread pool (I/O bound), printing progress.

    The workers return nothing: holding every decoded tile in the futures (81 MB per 4x
    WorldCover tile) would need ~17 GB for the US.
    """
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(COG_THREADS) as ex:
        futs = {ex.submit(_cache_only, *j): j for j in jobs}
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 100 == 0 or done == len(jobs):
                print(f"    {label}: {done}/{len(jobs)} tiles cached ({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------------------------


def _slope_deg(z: np.ndarray, tr: Affine) -> np.ndarray:
    h = z.shape[0]
    lat = tr.f + (np.arange(h) + 0.5) * tr.e
    dx = abs(tr.a) * 111_320.0 * np.cos(np.radians(lat))
    dy = abs(tr.e) * 110_574.0
    zz = z.astype(np.float32)
    gy, gx = np.gradient(zz)
    sx = gx / dx[:, None]
    sy = gy / dy
    return np.degrees(np.arctan(np.hypot(sx, sy))).astype(np.float32)


def elevation(country: str, cells: list[str], polys: list[Polygon], tree: STRtree) -> pd.DataFrame:
    """Per-hex DEM statistics from the native 3 arc-second GLO-90 tiles (both countries).

    Pass 1 streams the tiles and accumulates pixel-area-weighted sums (mean elevation, mean
    slope), pixel counts and exact min / max.  Pass 2 re-reads the cached tiles and fills a
    per-hex histogram (DEM_BIN wide bins between the hex's min and max) from which the 5th / 95th
    percentiles are read, so memory stays bounded for the ~1.4e9 US pixels.
    """
    names = set(fetch(f"{DEM_BASE}/tileList.txt", "copernicus_dem_glo90", "terrain/copdem90",
                      "tileList.txt").read_text().split())
    want = []
    for la, lof in sorted(tiles_touched(polys, 1)):
        lot = int(_true(lof))
        nm = dem_tile_name(la, lot)
        if nm in names:
            want.append((la, lof, nm))
    print(f"  DEM: {len(want)} Copernicus GLO-90 tiles intersect the {country} grid (native 3 arc-sec)",
          flush=True)

    def get(nm):
        return fetch(f"{DEM_BASE}/{nm}/{nm}.tif", "copernicus_dem_glo90", "terrain/copdem90")

    t0 = time.time()
    paths: dict[str, Path] = {}
    with ThreadPoolExecutor(THREADS) as ex:
        futs = {ex.submit(get, w[2]): w[2] for w in want}
        for k, f in enumerate(as_completed(futs)):
            paths[futs[f]] = f.result()
            if (k + 1) % 200 == 0:
                print(f"    DEM: {k + 1}/{len(want)} tiles cached ({time.time() - t0:.0f}s)", flush=True)

    def tiles():
        """Yield (z, transform, land mask, hex index of every masked pixel) per tile."""
        for k, (la, lof, nm) in enumerate(want):
            with rasterio.open(paths[nm]) as s:
                z, tr = s.read(1), s.transform
            tr = _shift(tr, lof - int(_true(lof)))
            lab = _label(tr, z.shape, tree, polys)
            if (k + 1) % 300 == 0:
                print(f"    DEM pass: {k + 1}/{len(want)} tiles ({time.time() - t0:.0f}s)", flush=True)
            if lab is None:
                continue
            z = np.where(np.isfinite(z), z, 0).astype(np.float32)
            m = (lab > 0) & (z != 0)
            if m.any():
                yield z, tr, m, (lab[m] - 1).astype(np.int64)

    n = len(cells)
    wsum = np.zeros(n)
    zsum = np.zeros(n)
    ssum = np.zeros(n)
    cnt = np.zeros(n, dtype=np.int64)
    zmin = np.full(n, np.inf)
    zmax = np.full(n, -np.inf)
    for z, tr, m, L in tiles():
        slope = _slope_deg(z, tr)
        lat = tr.f + (np.arange(z.shape[0]) + 0.5) * tr.e
        pw = (abs(tr.a) * abs(tr.e) * np.cos(np.radians(lat))).astype(np.float64)
        wpix = np.broadcast_to(pw[:, None], z.shape)[m]
        zm = z[m].astype(np.float64)
        wsum += np.bincount(L, weights=wpix, minlength=n)
        zsum += np.bincount(L, weights=wpix * zm, minlength=n)
        ssum += np.bincount(L, weights=wpix * slope[m], minlength=n)
        cnt += np.bincount(L, minlength=n)
        np.fmin.at(zmin, L, zm)
        np.fmax.at(zmax, L, zm)
    has = cnt > 0
    print(f"  DEM: {int(cnt.sum()):,} land pixels in hexes; {int(has.sum())}/{n} hexes have land pixels",
          flush=True)

    # pass 2: per-hex histograms for the percentiles
    nb = np.where(has, np.floor((np.where(has, zmax - zmin, 0)) / DEM_BIN).astype(np.int64) + 1, 0)
    off = np.concatenate([[0], np.cumsum(nb)])
    hist = np.zeros(int(off[-1]), dtype=np.uint32)
    print(f"  DEM: relief histogram {len(hist):,} bins of {DEM_BIN} m", flush=True)
    for z, tr, m, L in tiles():
        b = np.floor((z[m].astype(np.float64) - zmin[L]) / DEM_BIN).astype(np.int64)
        b = np.clip(b, 0, nb[L] - 1)
        u, c = np.unique(off[L] + b, return_counts=True)
        hist[u] += c.astype(np.uint32)
    pct = {q: np.zeros(n) for q in (0.05, 0.95)}
    for i in np.nonzero(has)[0]:
        cum = np.cumsum(hist[off[i]:off[i + 1]], dtype=np.int64)
        assert cum[-1] == cnt[i], "histogram count mismatch"
        for q, arr in pct.items():
            r = int(np.floor(q * (cnt[i] - 1)))            # 0-based rank of the order statistic
            j = int(np.searchsorted(cum, r + 1, side="left"))  # first bin holding that rank
            arr[i] = min(zmin[i] + j * DEM_BIN, zmax[i])
    out = pd.DataFrame({
        "h3": cells,
        "elev_mean_m": np.where(wsum > 0, zsum / np.maximum(wsum, 1e-30), 0.0),
        "elev_min_m": np.where(has, zmin, 0.0),
        "elev_max_m": np.where(has, zmax, 0.0),
        "relief_m": np.where(has, np.maximum(pct[0.95] - pct[0.05], 0.0), 0.0),
        "slope_mean_deg": np.where(wsum > 0, ssum / np.maximum(wsum, 1e-30), 0.0),
        "dem_land_px": cnt,
    })
    return out


# ---------------------------------------------------------------------------------------------
# Land cover
# ---------------------------------------------------------------------------------------------


def landcover(country: str, cells: list[str], polys: list[Polygon], tree: STRtree,
              land_km2: np.ndarray) -> pd.DataFrame:
    factor = CFG[country]["wc_factor"]
    gj = json.loads(fetch(f"{WC_BASE}/esa_worldcover_grid.geojson", "esa_worldcover_2021",
                          "terrain/worldcover", "esa_worldcover_grid.geojson").read_text())
    names = {f["properties"]["ll_tile"] for f in gj["features"]}
    want = []
    for la, lof in sorted(tiles_touched(polys, 3)):
        nm = wc_tile_name(la, int(_true(lof)))
        want.append((la, lof, nm, nm in names))
    have = [w for w in want if w[3]]
    print(f"  WorldCover: {len(want)} 3x3 deg tiles touch the grid, {len(have)} exist "
          f"(others are open ocean); resolution factor {factor}", flush=True)
    sub = f"terrain/worldcover_f{factor}"

    def url(nm):
        return f"{WC_BASE}/map/ESA_WorldCover_10m_2021_v200_{nm}_Map.tif"

    if factor == 1:  # whole 10 m tiles (standard cached downloads)
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda w: fetch(url(w[2]), "esa_worldcover_2021", "terrain/worldcover_10m"), have))
    else:
        _prefetch([(url(nm), "esa_worldcover_2021", sub, nm, factor) for _, _, nm, _ in have], "WorldCover")

    cgeom = country_geometry(country)
    n = len(cells)
    cnt_all = np.zeros(n * NC, dtype=np.float64)
    cnt_in = np.zeros(n * NC, dtype=np.float64)

    def accumulate(a, tr, mult):
        lab = _label(tr, a.shape, tree, polys)
        if lab is None:
            return
        m = lab > 0
        if not m.any():
            return
        code = (lab[m].astype(np.int64) - 1) * NC + _LUT[a[m]]
        cnt_all[:] += np.bincount(code, minlength=n * NC) * mult
        h, w = a.shape
        west, north = tr.c, tr.f
        clip = shapely.clip_by_rect(cgeom, west, north + h * tr.e, west + w * tr.a, north)
        if not clip.is_empty:
            inside = features.rasterize([(clip, 1)], out_shape=a.shape, transform=tr, fill=0, dtype="uint8")
            mi = inside[m].astype(bool)
            if mi.any():
                cnt_in[:] += np.bincount(code[mi], minlength=n * NC) * mult

    side = 36000 // factor
    t0 = time.time()
    for k, (la, lof, nm, exists) in enumerate(want):
        dx = lof - int(_true(lof))
        if not exists:  # no tile = open ocean: coarse all-water grid, counts scaled to the tile's pixel size
            cs = min(side, 1125)
            a = np.zeros((cs, cs), dtype=np.uint8)
            accumulate(a, Affine(3.0 / cs, 0, float(lof), 0, -3.0 / cs, float(la + 3)), (side / cs) ** 2)
        elif factor == 1:  # full resolution, processed in windows
            p = fetch(url(nm), "esa_worldcover_2021", "terrain/worldcover_10m")
            with rasterio.open(p) as s:
                for r0 in range(0, s.height, WC_CHUNK):
                    for c0 in range(0, s.width, WC_CHUNK):
                        win = Window(c0, r0, min(WC_CHUNK, s.width - c0), min(WC_CHUNK, s.height - r0))
                        tr = _shift(s.window_transform(win), dx)
                        b = box(tr.c, tr.f + win.height * tr.e, tr.c + win.width * tr.a, tr.f)
                        if len(tree.query(b)) == 0:
                            continue
                        accumulate(s.read(1, window=win), tr, 1.0)
        else:  # cached overview, processed in blocks to bound memory
            a, tr = read_cog_cached(url(nm), "esa_worldcover_2021", sub, nm, factor)
            tr = _shift(tr, dx)
            for r0 in range(0, a.shape[0], WC_CHUNK):
                for c0 in range(0, a.shape[1], WC_CHUNK):
                    blk = a[r0:r0 + WC_CHUNK, c0:c0 + WC_CHUNK]
                    tb = tr * Affine.translation(c0, r0)
                    b = box(tb.c, tb.f + blk.shape[0] * tb.e, tb.c + blk.shape[1] * tb.a, tb.f)
                    if len(tree.query(b)) == 0:
                        continue
                    accumulate(blk, tb, 1.0)
            del a
        if (k + 1) % 25 == 0 or factor == 1:
            print(f"    WorldCover processed {k + 1}/{len(want)} tiles ({time.time() - t0:.0f}s)", flush=True)
    cnt_all = cnt_all.reshape(n, NC)
    cnt_in = cnt_in.reshape(n, NC)
    tot = cnt_all.sum(1)
    frac = cnt_all / np.where(tot > 0, tot, np.nan)[:, None]
    out = pd.DataFrame({"h3": cells})
    for i, (_, key, _) in enumerate(WC_CLASSES):
        out[key] = frac[:, i]
    # in-country shares for the allocation weights
    tin = cnt_in.sum(1)
    share = cnt_in / np.where(tin > 0, tin, np.nan)[:, None]
    nonwater = cnt_all.copy()
    nonwater[:, WATER_IDX] = 0
    nw = nonwater.sum(1)
    fallback = nonwater / np.where(nw > 0, nw, np.nan)[:, None]
    need_fb = (tin <= 0) & (land_km2 > 0)
    share[need_fb] = fallback[need_fb]
    share = np.nan_to_num(share, nan=0.0)
    idx = {key: i for i, (_, key, _) in enumerate(WC_CLASSES)}
    out["cropland_km2"] = share[:, idx["lc_crop"]] * land_km2
    out["tree_km2"] = share[:, idx["lc_tree"]] * land_km2
    out["built_km2"] = share[:, idx["lc_built"]] * land_km2
    out["grass_km2"] = (share[:, idx["lc_grass"]] + share[:, idx["lc_shrub"]]) * land_km2
    out["wc_px"] = tot
    out["wc_px_in_country"] = tin
    print(f"  WorldCover: {int((tot > 0).sum())}/{n} hexes with pixels; {int(need_fb.sum())} land hexes "
          f"used the whole-hex fallback for weights", flush=True)
    return out


# ---------------------------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------------------------


def _worldclim(res: str, bio: int) -> Path:
    z = fetch(f"{WCLIM_BASE}/wc2.1_{res}_bio.zip", "worldclim_21", "terrain/worldclim")
    member = f"wc2.1_{res}_bio_{bio}.tif"
    out = z.with_suffix("") / member
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zz:
            names = [n for n in zz.namelist() if Path(n).name == member]
            if not names:
                raise FileNotFoundError(f"{member} not in {z}")
            tmp = out.with_name(out.name + ".part")
            with zz.open(names[0]) as src, open(tmp, "wb") as dst:
                while True:
                    b = src.read(1 << 22)
                    if not b:
                        break
                    dst.write(b)
            tmp.replace(out)
    return out


def climate(country: str, hexes: pd.DataFrame) -> pd.DataFrame:
    res = CFG[country]["clim"]
    child = COUNTRIES[country]["h3_res"] + CFG[country]["clim_child"]
    cells = hexes.h3.tolist()
    pts = util.hex_children_points(cells, child)
    print(f"  WorldClim {res}: {len(pts):,} child points (res {child})", flush=True)
    out = pd.DataFrame({"h3": cells})
    land = hexes.ring.to_numpy() == 0
    for bio, key in ((1, "temp_mean_c"), (12, "precip_mm")):
        with rasterio.open(_worldclim(res, bio)) as s:
            a = s.read(1, masked=True)
            tr = s.transform
        arr = a.filled(np.nan).astype(np.float64)
        arr[~np.isfinite(arr) | (arr < -1e30)] = np.nan
        H, W = arr.shape
        cols = np.floor((pts.lon.to_numpy() - tr.c) / tr.a).astype(np.int64) % W
        rows = np.clip(np.floor((pts.lat.to_numpy() - tr.f) / tr.e).astype(np.int64), 0, H - 1)
        v = arr[rows, cols]
        g = pd.DataFrame({"h3": pts.h3.to_numpy(), "v": v}).groupby("h3", sort=False)["v"].mean()
        vals = np.array(pd.Series(cells).map(g), dtype=float)
        # land hexes with no valid sample: nearest valid pixel within 10 px of the hex centre
        miss = np.where(land & ~np.isfinite(vals))[0]
        filled = 0
        for i in miss:
            r0 = int(np.floor((hexes.lat.iat[i] - tr.f) / tr.e))
            c0 = int(np.floor((hexes.lon.iat[i] - tr.c) / tr.a)) % W
            for k in range(1, 11):
                rr = np.clip(np.arange(r0 - k, r0 + k + 1), 0, H - 1)
                cc = np.arange(c0 - k, c0 + k + 1) % W
                win = arr[np.ix_(rr, cc)]
                if np.isfinite(win).any():
                    R, C = np.meshgrid(rr, cc, indexing="ij")
                    d = (R - r0) ** 2 + (C - c0) ** 2
                    d = np.where(np.isfinite(win), d, np.inf)
                    j = np.unravel_index(np.argmin(d), d.shape)
                    vals[i] = win[j]
                    filled += 1
                    break
        out[key] = vals
        print(f"  {key}: {int(np.isfinite(vals).sum())}/{len(vals)} hexes with values "
              f"({filled} coastal land hexes filled from nearest pixel)", flush=True)
    return out


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def build(country: str) -> None:
    t0 = time.time()
    print(f"== terrain / {country}", flush=True)
    hexes = load_hexes(country).reset_index(drop=True)
    cells = hexes.h3.tolist()
    polys = hex_polygons(cells)
    tree = STRtree(polys)
    land_km2 = hexes.land_km2.to_numpy(dtype=float)

    elev = elevation(country, cells, polys, tree)
    print(f"  elevation done ({time.time() - t0:.0f}s)", flush=True)
    lc = landcover(country, cells, polys, tree, land_km2)
    print(f"  land cover done ({time.time() - t0:.0f}s)", flush=True)
    clim = climate(country, hexes)
    print(f"  climate done ({time.time() - t0:.0f}s)", flush=True)

    df = elev.merge(lc, on="h3").merge(clim, on="h3")
    assert len(df) == len(hexes)
    rnd = {"elev_mean_m": 1, "elev_min_m": 1, "elev_max_m": 1, "relief_m": 1, "slope_mean_deg": 2,
           "cropland_km2": 3, "tree_km2": 3, "built_km2": 3, "grass_km2": 3, "temp_mean_c": 2, "precip_mm": 1}
    rnd.update({k: 4 for _, k, _ in WC_CLASSES})
    df = df.round(rnd)
    cols = ["h3"] + [m.key for m in METRICS if country in m.countries]
    util.write_hex(country, DOMAIN, df[cols])
    write_domain_meta(interim_dir(country, DOMAIN), METRICS, SOURCES, country)
    _report(country, df, hexes)
    print(f"  wrote {len(df)} hexes ({time.time() - t0:.0f}s)", flush=True)


def _report(country: str, df: pd.DataFrame, hexes: pd.DataFrame) -> None:
    """Print national sanity checks (land-weighted shares, extremes)."""
    d = df.merge(hexes[["h3", "land_km2", "lat", "lon", "ring", "adm1"]], on="h3")
    land = d[d.ring == 0]
    la = land.land_km2.sum()
    print(f"  land area (core) {la:,.0f} km2; weights: cropland {d.cropland_km2.sum():,.0f} "
          f"({d.cropland_km2.sum() / la:.1%}), tree {d.tree_km2.sum():,.0f} ({d.tree_km2.sum() / la:.1%}), "
          f"built {d.built_km2.sum():,.0f} ({d.built_km2.sum() / la:.1%}), grass+shrub "
          f"{d.grass_km2.sum():,.0f} ({d.grass_km2.sum() / la:.1%})")
    top = d.nlargest(3, "elev_max_m")[["h3", "lat", "lon", "elev_max_m", "elev_mean_m", "relief_m"]]
    print("  highest hexes:\n" + top.to_string(index=False))


if __name__ == "__main__":
    from civmap import validate

    ccs = sys.argv[1:] or ["lka", "usa"]
    for cc in ccs:
        build(cc)
    sys.exit(validate.main([DOMAIN, *ccs]))
