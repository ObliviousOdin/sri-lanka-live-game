"""Export the web app's static data files (contract: docs/PLAN.md §5)."""
from __future__ import annotations

import gzip
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .civ import (CULTURE_BONUS, DISTRICT_ORDER, DISTRICT_RULES, DISTRICTS, FAITH_BONUS, FEATURES, PLANTATION,
                  PLANTATION_CROPS, POWER_BONUS, RESEARCH_BONUS, TERRAIN, YIELD_BASES, YIELD_CAP, YIELDS, load_points,
                  plantation_land_share)
from .config import COUNTRIES, INTERIM, WEB_DATA, web_dir
from .core import load_admin, load_hexes
from .domains.history import ERAS
from .fetch import manifest

# point layers shipped to the browser: (table, value column for sizing, max rows, extra columns)
POINT_LAYERS = {
    "power_plants": ("capacity_mw", 1500, ["primary_fuel", "capacity_mw", "generation_gwh", "commissioning_year", "owner"]),
    "ports": ("port_cargo_t", 400, ["port_cargo_t", "cargo_tonnes", "teu", "port_calls", "year"]),
    "airports": ("airport_pax", 400, ["iata", "airport_pax", "passengers", "pax_year", "year"]),
    "universities": ("research_works_5y", 400, ["research_works_5y", "works_5y", "cited_by_count", "rank", "rd_usd", "city"]),
    "institutions": ("research_works_5y", 600, ["research_works_5y", "works_5y", "kind"]),
    "heritage": ("sitelinks", 100, ["kind", "inscribed", "year", "sitelinks", "wikidata"]),
    "parks": ("park_visitors", 150, ["park_visitors", "visitors", "visitors_year", "area_km2", "wikidata"]),
    "museums": ("sitelinks", 600, ["sitelinks", "wikidata"]),
    "holy_sites": ("sitelinks", 150, ["sitelinks", "wikidata"]),
    "military": (None, 1500, ["branch", "operator", "wikidata"]),
    "spaceports": (None, 50, ["wikidata", "orbital", "launch_type", "faa_license", "state"]),
    "industrial_zones": (None, 100, ["area_ha"]),
}


def _round(v: float, sig: int = 4):
    if v is None or not np.isfinite(v):
        return None
    if v == 0:
        return 0
    d = sig - int(math.floor(math.log10(abs(v)))) - 1
    r = round(v, d)
    return int(r) if float(r).is_integer() and abs(r) < 2**53 else r


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# metrics.bin keeps 13 of float32's 23 mantissa bits (relative error <= 2^-14 ~ 6e-5, i.e. ~4 significant
# digits, the precision of the admin JSON values); the zeroed low bits make the gzip copy ~20% smaller.
MATRIX_DROP_BITS = 10


def quantise_f32(a: np.ndarray, drop_bits: int = MATRIX_DROP_BITS) -> np.ndarray:
    """Round float32 values to (23 - drop_bits) mantissa bits (round half away from zero on the
    magnitude: a carry into the exponent is the correct next power of two). NaN stays NaN, ±inf
    and 0 are unchanged; integers below 2^(24 - drop_bits) (8,192) stay exact."""
    a = np.ascontiguousarray(a, dtype=np.float32)
    if drop_bits <= 0:
        return a.copy()
    u = a.view(np.uint32)
    half, mask = np.uint32(1 << (drop_bits - 1)), np.uint32((0xFFFFFFFF << drop_bits) & 0xFFFFFFFF)
    q = ((u + half) & mask).view(np.float32)
    keep = ~np.isfinite(a)                      # NaN payloads / inf must not be rounded into each other
    return np.where(keep, a, q).astype(np.float32)


def write_matrix(out, mat_col_major: np.ndarray) -> dict:
    """metrics.bin (raw little-endian float32, column-major) and metrics.bin.gz (gzip level 9).
    The app fetches the .gz and inflates it with DecompressionStream, falling back to the raw file."""
    raw = np.ascontiguousarray(mat_col_major, dtype="<f4").tobytes()
    (out / "metrics.bin").write_bytes(raw)
    # mtime=0: byte-identical output for identical data (stable hashes, no spurious diffs)
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    (out / "metrics.bin.gz").write_bytes(gz)
    return {"bin_bytes": len(raw), "gz_bytes": len(gz)}


def dump(path, obj, indent=None):
    path.write_text(json.dumps(_clean(obj), indent=indent, separators=(",", ":") if indent is None else None,
                               allow_nan=False))


def metric_stats(v: np.ndarray, kind: str, v_all: np.ndarray | None = None) -> dict:
    """Distribution stats over `v` (land tiles); for extensive metrics `total` sums `v_all` (every tile, land
    and sea rings, so sea-only quantities such as fish landings and offshore facilities are counted)."""
    f = v[np.isfinite(v)]
    tot = None
    if kind == "extensive":
        a = v if v_all is None else v_all
        a = a[np.isfinite(a)]
        tot = _round(float(a.sum()), 6) if len(a) else None
    if not len(f):
        return {"n": 0, **({"total": tot} if tot is not None else {})}
    s = {"n": int(len(f)), "min": _round(float(f.min())), "max": _round(float(f.max())),
         "p05": _round(float(np.quantile(f, 0.05))), "p50": _round(float(np.quantile(f, 0.5))),
         "p95": _round(float(np.quantile(f, 0.95)))}
    if kind == "extensive":
        s["total"] = tot
    return s


# Metric methods written for both countries ("USA: ... | Sri Lanka: ...") are shipped to each country's
# meta.json with only that country's part, so Sri Lankan provenance does not open with the US recipe.
_CC_MARK = {"usa": ("USA", "US", "United States"), "lka": ("Sri Lanka", "LKA")}
_MARK_RE = re.compile(r"(?:^|(?<=\| )|(?<=[.;)] ))(USA|US|United States|Sri Lanka|LKA)(?: \d{4})?:")
_OWN_SENTENCE = {"usa": re.compile(r"^(US|USA|U\.S\.|United States)\b(?! ?\d{0,4}:)"),
                 "lka": re.compile(r"^(Sri Lanka|Sri Lankan|LKA)\b(?! ?\d{0,4}:)")}
_COMPARE_RE = re.compile(r"\b(Definitions? differ|Definitional difference|Different survey|compare|comparable|"
                         r"both are|both countries|either country)")


def country_method(text: str, country: str) -> str:
    """The part of a two-country method text that describes `country` (unchanged when the text is not
    split by country). Splits only texts that open with a country label or separate the countries
    with ' | '; a shared lead-in before the first label is kept."""
    if not text or not ((m0 := _MARK_RE.match(text)) or " | Sri Lanka" in text or " | USA" in text):
        return text
    marks = list(_MARK_RE.finditer(text))
    if len(marks) < 2 or not any(m.group(1) in _CC_MARK["lka"] for m in marks) \
            or not any(m.group(1) in _CC_MARK["usa"] for m in marks):
        return text
    lead = text[:marks[0].start()].strip()
    keep, notes = [], []
    own_start = _OWN_SENTENCE[country]
    other_start = _OWN_SENTENCE["lka" if country == "usa" else "usa"]
    for i, m in enumerate(marks):
        seg = text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip().rstrip(" |").strip()
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", seg)
        if m.group(1) in _CC_MARK[country]:
            # own part, minus stray sentences about the other country ("US park coordinates ...")
            keep.append(" ".join([sents[0]] + [x for x in sents[1:] if not other_start.match(x)]))
        else:
            # from the other country's part keep sentences about this country and cross-country caveats
            for sent in sents[1:]:
                if own_start.match(sent) or _COMPARE_RE.search(sent):
                    notes.append(sent.strip())
    if not keep:
        return text
    return " ".join(([lead.rstrip(" |").strip()] if lead else []) + keep + notes)


# Source ids that the app cites under another (canonical) id, and sources that are software (no download).
SOURCE_ALIASES = {"terrain_worldcover": "esa_worldcover_2021"}
SOFTWARE_SOURCES = {"uber_h3": "Open-source software (the h3 Python package): the grid is computed locally, "
                               "nothing is downloaded."}
# downloads recorded under a broader source id that belong to a more specific one (matched on cache path)
EXTRA_DOWNLOADS = {"unesco_whc_via_wikidata": lambda d: Path(d.get("path", "")).name.startswith("culture_whs_")}
MAX_DOWNLOAD_ROWS = 20


def export_country(country: str) -> dict:
    cfg = COUNTRIES[country]
    out = web_dir(country)
    levels = list(cfg["admin_levels"])
    core = load_hexes(country).set_index("h3")
    R = pd.read_parquet(INTERIM / country / "reconciled" / "hexes.parquet").reindex(core.index)
    tiles = pd.read_parquet(INTERIM / country / "civ" / "tiles.parquet").reindex(core.index)
    metrics = json.loads((INTERIM / country / "reconciled" / "metrics.json").read_text())

    # derived metrics shared by both countries
    derived = []
    R["pop"] = core["pop"]
    R["land_km2"] = core["land_km2"]
    R["pop_density"] = np.where(core.land_km2 > 0.5, core["pop"] / core.land_km2.clip(lower=1e-9), np.nan)
    if "gdp_usd" in R:
        # one GDP-per-capita metric only: the economy domain's gdp_pc_usd (unit GDP ÷ unit population,
        # blended onto tiles) equals tile GDP ÷ tile population because GDP is allocated by population,
        # so it is exported once, under the key the app uses, with the domain's provenance and caveats.
        R["gdp_per_capita_usd"] = np.where(R["pop"] > 50, R["gdp_usd"] / R["pop"].clip(lower=1), np.nan)
        g = next((m for m in metrics if m["key"] == "gdp_usd"), {})
        pm = next((m for m in metrics if m["key"] == "pop"), {})
        gpc = next((m for m in metrics if m["key"] == "gdp_pc_usd"), None)
        base = {"label": "GDP per capita", "unit": "USD/person", "category": "economy", "kind": "intensive",
                "weight": "pop", "better": "higher", "domain": "derived", "vintage": g.get("vintage", ""),
                "sources": list(dict.fromkeys(g.get("sources", []) + pm.get("sources", []))),
                "description": "GDP per resident at current prices.", "method": "GDP: " + g.get("method", "")}
        if gpc:
            base = {**base, **{k: v for k, v in gpc.items() if k not in ("key", "col", "stats", "table")}}
        derived.append({**base, "key": "gdp_per_capita_usd",
                        "method": "Tiles: gdp_usd ÷ residents (tiles with ≥ 50 residents; equal to the "
                                  "population-weighted value of the units the tile overlaps). Admin units: unit GDP ÷ "
                                  "unit population. " + base.get("method", "")})
        metrics = [m for m in metrics if m["key"] != "gdp_pc_usd"]
    if "employed" in R:
        for k in [c for c in R.columns if c.startswith("cit_")]:
            R[k + "_pct"] = np.where(R["employed"] > 0, 100 * R[k] / R["employed"], np.nan)
            base = next((m for m in metrics if m["key"] == k), None)
            if base:
                derived.append({**base, "key": k + "_pct", "label": base["label"] + " (share)", "unit": "%",
                                "kind": "intensive", "weight": "pop", "domain": "derived",
                                "method": f"{k} / employed × 100. " + base.get("method", "")})
    if all(k in R.columns for k in PLANTATION_CROPS):
        # the share the Sri Lankan Plantation district rule tests (civ.PLANTATION), so the app can show it
        R["plantation_land_pct"] = 100 * plantation_land_share(R.assign(land_km2=core["land_km2"]))
        R.loc[core.ring.to_numpy() > 0, "plantation_land_pct"] = np.nan
        crop = {m["key"]: m for m in metrics if m["key"] in PLANTATION_CROPS}
        derived.append({
            "key": "plantation_land_pct", "label": "Tea, rubber & coconut share of land", "unit": "%",
            "category": "food", "kind": "intensive", "weight": "land", "domain": "derived", "better": "neutral",
            "vintage": next(iter(crop.values()), {}).get("vintage", ""),
            "sources": list(dict.fromkeys(sum((m.get("sources", []) for m in crop.values()), []) + ["esa_worldcover_2021"])),
            "description": "Share of the land inside the boundary under tea, rubber or coconut (drives the Sri Lankan "
                           "Plantation district).",
            "method": "(tea_ha + rubber_ha + coconut_ha) / (land_km2 x 100) x 100. The district crop extents "
                      "(Economic Census 2013/14: DCS Statistical Abstract 2025 Table 5.2, EC Table 6.3.1) are "
                      "allocated to tiles by ESA WorldCover tree-cover area inside the district; admin units: unit "
                      "crop extent / unit land. Plantation district: >= "
                      f"{PLANTATION['lka_share_min'] * 100:.0f} % on a land tile (see the district rule)."})
    for m in metrics:
        if m["key"] == "pop_density":
            break
    else:
        metrics.append({"key": "pop_density", "label": "Population density", "unit": "persons/km2",
                        "category": "population", "kind": "intensive", "weight": "land", "domain": "core",
                        "sources": ["worldpop_2020"],
                        "description": "People per km² of land (area inside the boundary, incl. inland water).",
                        "method": "pop / land_km2", "better": "neutral", "vintage": "2023"})
    metrics = metrics + derived
    for m in metrics:
        m["method"] = country_method(m.get("method", ""), country)
        m["sources"] = list(dict.fromkeys(SOURCE_ALIASES.get(x, x) for x in m.get("sources", [])))
    keys = [m["key"] for m in metrics if m["key"] in R.columns]
    metrics = [m for m in metrics if m["key"] in R.columns]
    mat = R[keys].to_numpy(dtype=np.float32)
    # column-major Float32 matrix, quantised to ~4 significant digits, plus a gzip copy for the app
    msize = write_matrix(out, quantise_f32(mat.T))
    land_rows = core.ring.to_numpy() == 0
    for i, m in enumerate(metrics):
        m["col"] = i
        v = R[m["key"]].to_numpy(float)
        m["stats"] = metric_stats(v[land_rows], m["kind"], v)

    # admin tables + geojson
    admin_meta = {}
    adm_index = {}
    for lvl in levels:
        g = load_admin(country, lvl)
        A = pd.read_parquet(INTERIM / country / "reconciled" / f"admin_{lvl}.parquet")
        A = A.reindex(g.admin_id.astype(str))
        if "gdp_usd" in A and "pop" in A:
            A["gdp_per_capita_usd"] = A["gdp_usd"] / A["pop"].replace(0, np.nan)
        A["pop_density"] = g.set_index("admin_id")["pop"].to_numpy() / g.set_index("admin_id").land_km2.replace(0, np.nan).to_numpy()
        if "employed" in A:
            for k in [c for c in A.columns if c.startswith("cit_") and not c.endswith("_pct")]:
                A[k + "_pct"] = 100 * A[k] / A["employed"].replace(0, np.nan)
        if "plantation_land_pct" in R.columns and all(k in A.columns for k in PLANTATION_CROPS):
            A["plantation_land_pct"] = 100 * plantation_land_share(
                A.assign(land_km2=g.set_index("admin_id").land_km2.reindex(A.index).to_numpy()))
        units = [{"id": r.admin_id, "name": r["name"], "parent": r.parent_id, "lat": _round(r.lat, 6),
                  "lon": _round(r.lon, 6), **({"abbr": r["abbr"]} if "abbr" in g.columns else {})}
                 for _, r in g.iterrows()]
        vals = {k: [_round(float(v)) if np.isfinite(v) else None for v in A[k].to_numpy(float)]
                for k in keys if k in A.columns}
        dump(out / f"admin_{lvl}.json", {"level": lvl, "label": cfg["admin_levels"][lvl], "units": units, "metrics": vals})
        tol = {"lka": {"adm1": 0.003, "adm2": 0.002, "adm3": 0.001},
               "usa": {"adm1": 0.02, "adm2": 0.01}}[country][lvl]
        gg = g[["admin_id", "name", "geometry"]].copy()
        gg["geometry"] = gg.geometry.simplify(tol, preserve_topology=True)
        gg = gg.rename(columns={"admin_id": "id"})
        (out / f"admin_{lvl}.geojson").write_text(gg.to_json(drop_id=True, to_wgs84=True))
        admin_meta[lvl] = {"label": cfg["admin_levels"][lvl], "count": len(units)}
        adm_index[lvl] = {aid: i for i, aid in enumerate(g.admin_id.astype(str))}

    # hexes.json (columnar)
    terr_codes = list(TERRAIN)
    feat_codes = list(FEATURES)
    dist_codes = [""] + DISTRICT_ORDER
    hexes = {
        "h3": core.index.tolist(),
        "ring": core.ring.astype(int).tolist(),
        "terrain": [terr_codes.index(t) if t in terr_codes else 0 for t in tiles.terrain.fillna("plains")],
        "feature": [feat_codes.index(t) if t in feat_codes else 0 for t in tiles.feature.fillna("")],
        "hills": tiles.hills.fillna(False).astype(int).tolist(),
        "district": [dist_codes.index(d) if d in dist_codes else 0 for d in tiles.district.fillna("")],
        "districts_all": tiles.districts_all.fillna("").tolist(),
        "yields": {y: tiles[f"y_{y}"].fillna(0).astype(int).tolist() for y in YIELDS},
    }
    for lvl in levels:
        hexes[lvl] = [adm_index[lvl].get(str(a), -1) for a in core[lvl]]
    # per-tile yield bonus inputs (sparse: tile indices + values) so the app can explain yields exactly
    bonus = {}
    for k in ("works_5y", "capacity_mw", "whs", "museums", "holy_sites", "park_visitors", "military_sites",
              "port_cargo_t", "airport_pax"):
        if k in tiles.columns:
            v = tiles[k].to_numpy(float)
            nz = np.nonzero(np.nan_to_num(v) > 0)[0]
            bonus[k] = {"i": nz.tolist(), "v": [_round(float(x)) for x in v[nz]]}
    hexes["bonus"] = bonus
    dump(out / "hexes.json", hexes)

    # points
    pts = load_points(country)
    points_out = {}
    for name, (vcol, nmax, extra) in POINT_LAYERS.items():
        df = pts.get(name)
        if df is None or not len(df):
            continue
        d = df.dropna(subset=["lat", "lon"]).copy()
        if vcol and vcol in d.columns:
            d[vcol] = pd.to_numeric(d[vcol], errors="coerce")
            d = d.sort_values(vcol, ascending=False, na_position="last")
        d = d.head(nmax)
        rows = []
        for _, r in d.iterrows():
            row = {"id": str(r["id"]), "name": str(r["name"]), "lat": _round(float(r["lat"]), 7),
                   "lon": _round(float(r["lon"]), 7), "kind": str(r.get("kind", "")), "h3": r.get("h3")}
            for c in extra:
                if c in r.index and pd.notna(r[c]):
                    v = r[c]
                    row[c] = bool(v) if isinstance(v, (bool, np.bool_)) else \
                        _round(float(v)) if isinstance(v, (int, float, np.number)) else str(v)
            rows.append(row)
        points_out[name] = {"domain": df["domain"].iloc[0], "rows": rows, "total": int(len(df))}
    dump(out / "points.json", points_out)

    # historic moments (timeline): every dated event, linked to its wonder when it is one
    ev_f = INTERIM / country / "history" / "points_events.csv"
    if ev_f.exists():
        ev = pd.read_csv(ev_f, dtype={"h3": str})
        wonders = json.loads((INTERIM / country / "civ" / "wonders.json").read_text())
        by_key = {}
        for w in wonders:
            by_key.setdefault((w.get("h3"), str(w.get("name", "")).lower()), w["category"])
            if w.get("wikidata"):
                by_key.setdefault(("qid", w["wikidata"]), w["category"])
        rows = []
        for _, r in ev.iterrows():
            wonder = by_key.get(("qid", r.get("wikidata"))) or by_key.get((r.get("h3"), str(r["name"]).lower()))
            rows.append({
                "id": r["id"], "name": r["name"], "lat": _round(float(r["lat"]), 6), "lon": _round(float(r["lon"]), 6),
                "h3": r.get("h3"), "kind": r["kind"], "event": r["event"], "year": int(r["year"]),
                "precision": int(r["precision"]), "date": r["date_text"],
                "value": _round(float(r["value"])) if pd.notna(r.get("value")) else None,
                "unit": r.get("value_unit") if isinstance(r.get("value_unit"), str) else "",
                "wikidata": r.get("wikidata") if isinstance(r.get("wikidata"), str) else None,
                "source": r["source"], "basis": r.get("date_basis") if isinstance(r.get("date_basis"), str) else "",
                "wonder": wonder,
            })
        dump(out / "events.json", rows)

    # wonders & cities
    for f in ("wonders", "cities"):
        dump(out / f"{f}.json", json.loads((INTERIM / country / "civ" / f"{f}.json").read_text()))
    wc = INTERIM / country / "civ" / "wonder_categories.json"
    wonder_categories = json.loads(wc.read_text()) if wc.exists() else []

    # meta
    lon0, lat0, lon1, lat1 = cfg["bbox"]
    land = core[core.ring == 0]
    meta = {
        "country": country, "name": cfg["name"], "iso3": cfg["iso3"], "h3_res": cfg["h3_res"],
        "hex_count": int(len(core)), "land_hex_count": int(len(land)),
        "avg_hex_km2": _round(float(core.hex_km2.mean())),
        "bbox": [lon0, lat0, lon1, lat1],
        "center": [float(np.average(land.lon, weights=land["pop"] + 1)), float(np.average(land.lat, weights=land["pop"] + 1))],
        "admin_levels": admin_meta, "metrics": metrics,
        "totals": {"pop": _round(float(core["pop"].sum()), 8), "land_km2": _round(float(core.land_km2.sum()), 6),
                   **({"gdp_usd": _round(float(np.nansum(R["gdp_usd"])), 6)} if "gdp_usd" in R else {})},
        "yield_totals": {y: int(tiles[f"y_{y}"].sum()) for y in YIELDS},
        "terrain_counts": tiles.terrain.value_counts().to_dict(),
        "district_counts": tiles.district.value_counts().to_dict(),
        # per wonder category: rule, unit, cap, candidates, status ('ranked' | 'no_data' | 'no_candidates')
        # and a note to show instead of "none qualify" when the country publishes no ranking values
        "wonder_categories": wonder_categories,
    }
    dump(out / "meta.json", meta)
    return {"metrics": len(metrics), "hexes": len(core), **msize,
            "points": {k: len(v["rows"]) for k, v in points_out.items()}}


def export_global() -> dict:
    out = web_dir()
    # sources: union over both countries + national, with the metrics each one feeds
    sources: dict[str, dict] = {}
    for cc in COUNTRIES:
        f = INTERIM / cc / "reconciled" / "sources.json"
        if f.exists():
            for s in json.loads(f.read_text()):
                sources.setdefault(s["id"], {**s, "metrics": {}})
        # metric keys as shipped to the app (meta.json), so every key a source lists exists in the app
        mf = web_dir(cc) / "meta.json"
        mlist = json.loads(mf.read_text())["metrics"] if mf.exists() else []
        if not mlist and (INTERIM / cc / "reconciled" / "metrics.json").exists():
            mlist = json.loads((INTERIM / cc / "reconciled" / "metrics.json").read_text())
        if mlist:
            for m in mlist:
                if m.get("domain") == "derived":          # e.g. citizen shares: listed via their base metric
                    continue
                for sid in m.get("sources", []):
                    if sid in sources:
                        sources[sid]["metrics"].setdefault(cc, []).append(m["key"])
    nat_dir = INTERIM / "national"
    if (nat_dir / "sources.json").exists():
        for s in json.loads((nat_dir / "sources.json").read_text()):
            sources.setdefault(s["id"], {**s, "metrics": {}})
    # aliases: the canonical entry takes over the alias's metric links; the alias entry is dropped
    for alias, canon in SOURCE_ALIASES.items():
        a = sources.pop(alias, None)
        if a and canon in sources:
            for cc, ks in a["metrics"].items():
                sources[canon]["metrics"].setdefault(cc, [])
                sources[canon]["metrics"][cc] += [k for k in ks if k not in sources[canon]["metrics"][cc]]
    # other places that cite sources: City View (official population / GDP, metro definition), wonders,
    # national indicators (Advisors)
    used: dict[str, dict[str, set]] = {}

    def _use(sid, cc, what):
        used.setdefault(SOURCE_ALIASES.get(sid, sid), {}).setdefault(cc, set()).add(what)
    for cc in COUNTRIES:
        for c in json.loads((INTERIM / cc / "civ" / "cities.json").read_text()) \
                if (INTERIM / cc / "civ" / "cities.json").exists() else []:
            for f in ("population_sources", "census_sources", "official_gdp_sources", "metro_sources"):
                for sid in c.get(f) or []:
                    _use(sid, cc, "cities")
        for w in json.loads((INTERIM / cc / "civ" / "wonders.json").read_text()) \
                if (INTERIM / cc / "civ" / "wonders.json").exists() else []:
            for sid in w.get("sources") or []:
                _use(sid, cc, "wonders")
    if (nat_dir / "indicators.json").exists():
        for ind in json.loads((nat_dir / "indicators.json").read_text()):
            for sid in re.split(r"[,;\s]+", str(ind.get("source") or "")):
                if sid:
                    _use(sid, "national", "advisors")
    downloads = manifest()
    audit = {"cited_without_provenance": [], "not_cited": [], "software": []}
    for s in sources.values():
        sid = s["id"]
        extra = EXTRA_DOWNLOADS.get(sid)
        files = [d for d in downloads if d.get("source_id") == sid or (extra and extra(d))]
        # newest first is not meaningful for provenance: keep the cache order, list at most MAX_DOWNLOAD_ROWS
        s["downloads"] = [{"url": d["url"], "bytes": d["bytes"], "fetched_at": d["fetched_at"], "sha256": d["sha256"][:16]}
                          for d in files[:MAX_DOWNLOAD_ROWS]]
        s["n_downloads"] = len(files)
        s["total_bytes"] = int(sum(int(d.get("bytes") or 0) for d in files))
        s["accessed"] = max((d["fetched_at"] for d in files), default=None)
        s["used_in"] = {cc: sorted(v) for cc, v in sorted(used.get(sid, {}).items())}
        cited = bool(any(s["metrics"].values()) or s["used_in"])
        if sid in SOFTWARE_SOURCES:
            s["provenance"] = "software"
            s["notes"] = (s.get("notes") or "") + (" " if s.get("notes") else "") + SOFTWARE_SOURCES[sid]
            audit["software"].append(sid)
        elif files:
            s["provenance"] = "download"
        else:
            s["provenance"] = "none"
            if cited:
                audit["cited_without_provenance"].append(sid)
        if not cited:
            audit["not_cited"].append(sid)
    dump(out / "sources.json", sorted(sources.values(), key=lambda s: s["id"]), indent=None)
    if (nat_dir / "indicators.json").exists():
        dump(out / "national.json", json.loads((nat_dir / "indicators.json").read_text()))
    index = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "countries": [{"id": cc, "name": cfg["name"], "h3_res": cfg["h3_res"],
                       "admin_levels": cfg["admin_levels"]} for cc, cfg in COUNTRIES.items()],
        "yields": YIELDS,
        "yield_scale": {"cap": YIELD_CAP, "bases": {k: {"metric": v[0], "base": v[1], "unit": v[2]} for k, v in YIELD_BASES.items()},
                        "power_bonus": POWER_BONUS, "research_bonus": RESEARCH_BONUS,
                        "culture_bonus": CULTURE_BONUS, "faith_bonus": FAITH_BONUS},
        "terrain": TERRAIN, "features": FEATURES,
        "eras": ERAS,
        "districts": [{"code": c, "label": DISTRICTS.get(c, ""), "rule": DISTRICT_RULES.get(c, "")} for c in [""] + DISTRICT_ORDER],
        "terrain_codes": list(TERRAIN), "feature_codes": list(FEATURES),
    }
    dump(out / "index.json", index, indent=1)
    return {"sources": len(sources), "downloads": sum(s["n_downloads"] for s in sources.values()),
            "download_rows_listed": sum(len(s["downloads"]) for s in sources.values()), **audit}


def export_all():
    res = {cc: export_country(cc) for cc in COUNTRIES}
    res["global"] = export_global()
    return res


if __name__ == "__main__":
    print(json.dumps(export_all(), indent=1))
