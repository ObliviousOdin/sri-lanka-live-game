"""Export the web app's static data files (contract: docs/PLAN.md §5)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .civ import (CULTURE_BONUS, DISTRICT_ORDER, DISTRICT_RULES, DISTRICTS, FAITH_BONUS, FEATURES, POWER_BONUS,
                  RESEARCH_BONUS, TERRAIN, YIELD_BASES, YIELD_CAP, YIELDS, load_points)
from .config import COUNTRIES, INTERIM, WEB_DATA, web_dir
from .core import load_admin, load_hexes
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


def dump(path, obj, indent=None):
    path.write_text(json.dumps(_clean(obj), indent=indent, separators=(",", ":") if indent is None else None,
                               allow_nan=False))


def metric_stats(v: np.ndarray, kind: str) -> dict:
    f = v[np.isfinite(v)]
    if not len(f):
        return {"n": 0}
    s = {"n": int(len(f)), "min": _round(float(f.min())), "max": _round(float(f.max())),
         "p05": _round(float(np.quantile(f, 0.05))), "p50": _round(float(np.quantile(f, 0.5))),
         "p95": _round(float(np.quantile(f, 0.95)))}
    if kind == "extensive":
        s["total"] = _round(float(f.sum()), 6)
    return s


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
    for m in metrics:
        if m["key"] == "pop_density":
            break
    else:
        metrics.append({"key": "pop_density", "label": "Population density", "unit": "persons/km2",
                        "category": "population", "kind": "intensive", "weight": "land", "domain": "core",
                        "sources": ["worldpop_2020"], "description": "People per km² of land.",
                        "method": "pop / land_km2", "better": "neutral", "vintage": "2023"})
    metrics = metrics + derived
    keys = [m["key"] for m in metrics if m["key"] in R.columns]
    metrics = [m for m in metrics if m["key"] in R.columns]
    mat = R[keys].to_numpy(dtype=np.float32)
    # column-major Float32 matrix
    mat.T.copy().tofile(out / "metrics.bin")
    for i, m in enumerate(metrics):
        m["col"] = i
        m["stats"] = metric_stats(R[m["key"]].to_numpy(float)[core.ring.to_numpy() == 0], m["kind"])

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
    return {"metrics": len(metrics), "hexes": len(core), "points": {k: len(v["rows"]) for k, v in points_out.items()}}


def export_global() -> dict:
    out = web_dir()
    # sources: union over both countries + national, with the metrics each one feeds
    sources: dict[str, dict] = {}
    for cc in COUNTRIES:
        f = INTERIM / cc / "reconciled" / "sources.json"
        if f.exists():
            for s in json.loads(f.read_text()):
                sources.setdefault(s["id"], {**s, "metrics": {}})
        mf = INTERIM / cc / "reconciled" / "metrics.json"
        if mf.exists():
            for m in json.loads(mf.read_text()):
                for sid in m.get("sources", []):
                    if sid in sources:
                        sources[sid]["metrics"].setdefault(cc, []).append(m["key"])
    nat_dir = INTERIM / "national"
    if (nat_dir / "sources.json").exists():
        for s in json.loads((nat_dir / "sources.json").read_text()):
            sources.setdefault(s["id"], {**s, "metrics": {}})
    downloads = manifest()
    for s in sources.values():
        files = [d for d in downloads if d.get("source_id") == s["id"]]
        s["downloads"] = [{"url": d["url"], "bytes": d["bytes"], "fetched_at": d["fetched_at"], "sha256": d["sha256"][:16]}
                          for d in files[:20]]
        s["accessed"] = max((d["fetched_at"] for d in files), default=None)
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
        "districts": [{"code": c, "label": DISTRICTS.get(c, ""), "rule": DISTRICT_RULES.get(c, "")} for c in [""] + DISTRICT_ORDER],
        "terrain_codes": list(TERRAIN), "feature_codes": list(FEATURES),
    }
    dump(out / "index.json", index, indent=1)
    return {"sources": len(sources)}


def export_all():
    res = {cc: export_country(cc) for cc in COUNTRIES}
    res["global"] = export_global()
    return res


if __name__ == "__main__":
    print(json.dumps(export_all(), indent=1))
