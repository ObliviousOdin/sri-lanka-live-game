"""Reconcile every domain's tidy tables onto the hex grid (and back up to every admin level).

Allocation happens at the *piece* level (a piece = hex ∩ finest admin unit, from the
population crosswalk) so one code path serves both hex values and admin aggregates:

* admin tables, extensive:  v_piece = value_admin × w_piece / Σ_admin w        (mass preserving)
* admin tables, intensive:  v_piece = value_admin; hexes/admins average with weight w
* hex tables:               extensive values split over a hex's pieces by land share;
                            intensive values copied to every piece of the hex
* point tables (facilities): each point's full value goes to the piece (its hex x the finest
                            admin unit whose polygon contains the point), so admin totals are the
                            sums of the points inside each unit and hex totals the sums of the
                            points snapped to each hex; a point outside every polygon (offshore)
                            goes to the dominant (largest-land) piece of its hex

Water hexes (sea rings) take part as pseudo-pieces with zero land and population that
inherit the admin units of their nearest land hex, so coastal allocations (fish catch) and
facilities snapped to the sea (ports) are kept.  They take no admin-level rates or shares:
an intensive admin metric is NaN on a sea hex unless its weight is 'coast' (hex-level rasters
such as terrain, hazards and protected area keep their sea values).

Weights `w`: pop, land, cropland, tree, built, grass (land × land-cover fraction of the hex),
coast (ring-1 sea hexes, by area).  If an admin unit has zero total weight the allocation
falls back to land area, then to population.
"""
from __future__ import annotations

import json
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import COUNTRIES, INTERIM
from .core import load_admin, load_hexes, load_pieces

SKIP_DIRS = {"core", "national", "core_r5", "reconciled", "civ"}


def load_domain_metrics(country: str) -> tuple[list[dict], list[dict]]:
    """All Metric dicts (with `domain`) and Source dicts produced by domain modules."""
    metrics, sources, seen = [], {}, set()
    root = INTERIM / country
    dirs = ["core"] + sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)
    for d in dirs:
        mp = root / d / "metrics.json"
        if not mp.exists():
            continue
        for m in json.loads(mp.read_text()):
            if m["key"] in seen:
                warnings.warn(f"{country}: duplicate metric key {m['key']} in {d} (ignored)")
                continue
            seen.add(m["key"])
            m["domain"] = d
            metrics.append(m)
        sp = root / d / "sources.json"
        if sp.exists():
            for s in json.loads(sp.read_text()):
                sources.setdefault(s["id"], s)
    return metrics, list(sources.values())


class Reconciler:
    def __init__(self, country: str, tag: str = "core"):
        self.country = country
        self.cfg = COUNTRIES[country]
        self.levels = list(self.cfg["admin_levels"])
        hexes = load_hexes(country, tag)
        pieces = load_pieces(country, tag)
        water = hexes[hexes.ring > 0][["h3"] + self.levels].copy()
        water["pop"] = 0.0
        water["land_km2"] = 0.0
        P = pd.concat([pieces[["h3"] + self.levels + ["pop", "land_km2"]], water], ignore_index=True)
        self.H = hexes.set_index("h3")
        P["ring"] = P.h3.map(self.H.ring).astype(int)
        P["hex_km2"] = P.h3.map(self.H.hex_km2)
        hex_land = P.groupby("h3").land_km2.transform("sum")
        n_in_hex = P.groupby("h3").h3.transform("size")
        P["share_in_hex"] = np.where(hex_land > 0, P.land_km2 / hex_land.replace(0, np.nan), 1.0 / n_in_hex)
        # pt_piece: (hex, admin unit) pieces added only to carry facility points whose containing unit has
        # no population pixel in the point's hex (zero land, population, coast weight and hex share)
        P["pt_piece"] = False
        self.P = P.reset_index(drop=True)
        self.hex_index = self.H.index
        self._lc = None
        self._pt_rows: dict[tuple[str, str], np.ndarray] = {}
        self.point_report: dict[str, dict] = {}

    # -- weights -----------------------------------------------------------------------------
    def _landcover(self) -> pd.DataFrame | None:
        if self._lc is None:
            f = INTERIM / self.country / "terrain" / "hex.csv"
            self._lc = pd.read_csv(f).set_index("h3") if f.exists() else pd.DataFrame()
        return self._lc if len(self._lc) else None

    def weight(self, kind: str) -> np.ndarray:
        P = self.P
        if kind == "pop":
            return P["pop"].to_numpy(float)
        if kind in ("land", "none", ""):
            return P["land_km2"].to_numpy(float)
        if kind == "coast":
            return np.where((P.ring == 1) & ~P.pt_piece, P.hex_km2, 0.0)
        lc = self._landcover()
        # Preferred: the terrain domain's in-country class areas (cropland_km2 ...), which exclude the
        # sea and foreign land in coastal / border hexes; share = class km2 / hex land km2.
        area_col = {"cropland": "cropland_km2", "tree": "tree_km2", "built": "built_km2", "grass": "grass_km2"}.get(kind)
        if lc is not None and area_col in lc.columns:
            hex_land = self.H["land_km2"].reindex(lc.index).to_numpy(float)
            share = pd.Series(np.where(hex_land > 0, lc[area_col].to_numpy(float) / np.maximum(hex_land, 1e-9), 0.0),
                              index=lc.index).clip(0, 1)
            return P["land_km2"].to_numpy(float) * share.reindex(P.h3).fillna(0).to_numpy(float)
        cols = {"cropland": ["lc_crop"], "tree": ["lc_tree"], "built": ["lc_built"],
                "grass": ["lc_grass", "lc_shrub"]}.get(kind)
        if cols is None or lc is None or not set(cols) <= set(lc.columns):
            return P["land_km2"].to_numpy(float)
        frac = lc[cols].sum(axis=1).reindex(P.h3).fillna(0).to_numpy(float)
        return P["land_km2"].to_numpy(float) * frac

    # -- allocation ----------------------------------------------------------------------------
    def allocate_admin(self, values: pd.Series, level: str, kind: str, weight: str) -> tuple[np.ndarray, np.ndarray]:
        """Piece-level values (and averaging weights) for an admin-level metric."""
        P = self.P
        adm = P[level].astype(str).to_numpy()
        val = values.reindex(adm).to_numpy(float)
        w = self.weight(weight)
        if kind == "intensive":
            # a unit's rate / share / median describes its residents or land: sea pseudo-pieces (and the
            # zero-weight pieces that only carry facility points) take no value, so ocean tiles show
            # no data instead of the nearest unit's rate. Coast-weighted metrics keep their sea values.
            drop = self.P.pt_piece.to_numpy(bool)
            if weight != "coast":
                drop = drop | (self.P.ring.to_numpy() > 0)
            val = np.where(drop, np.nan, val)
            return val, w.copy()
        out = np.zeros(len(P))
        chain = [w, self.weight("land"), self.weight("pop")]
        remaining = pd.Series(values.dropna())
        for wv in chain:
            if remaining.empty:
                break
            W = pd.Series(wv).groupby(adm).sum()
            ok = remaining.index[remaining.index.isin(W.index[W > 0])]
            m = pd.Series(adm).isin(ok).to_numpy()
            denom = pd.Series(adm[m]).map(W).to_numpy(float)
            out[m] += remaining.reindex(adm[m]).to_numpy(float) * wv[m] / denom
            remaining = remaining.drop(ok)
        if len(remaining):
            warnings.warn(f"{self.country}: {len(remaining)} admin units at {level} had no pieces: "
                          f"{list(remaining.index[:5])}")
        return out, None

    # -- facility points ---------------------------------------------------------------------
    def _finest_chain(self) -> pd.DataFrame:
        """Finest admin polygons with the ids of every coarser level (admin_id -> adm1, adm2[, adm3])."""
        finest = self.cfg["finest_admin"]
        g = load_admin(self.country, finest)[["admin_id", "parent_id", "geometry"]].copy()
        g["admin_id"] = g.admin_id.astype(str)
        g[finest] = g.admin_id
        lv = self.levels
        for i in range(len(lv) - 1, 0, -1):
            child, parent = lv[i], lv[i - 1]
            up = load_admin(self.country, child).set_index("admin_id").parent_id.astype(str)
            up.index = up.index.astype(str)
            g[parent] = g[child].map(up)
        return g

    def prepare_points(self, tables: list[tuple[str, str]]) -> None:
        """Place every row of the given point tables on a piece (h3 x containing finest admin unit).

        Point-in-polygon against the finest admin boundaries (civmap.core.load_admin); the hex is the
        table's own (snapped) h3, so hex totals do not change.  A point outside every polygon (offshore,
        or snapped from just outside the boundary) goes to the dominant piece of its hex (largest land
        area; a sea hex has a single pseudo-piece).  When the containing unit has no piece in that hex,
        a zero-weight piece (pt_piece) is added for it."""
        if not tables:
            return
        root = INTERIM / self.country
        finest = self.cfg["finest_admin"]
        chain = self._finest_chain()
        poly = gpd.GeoDataFrame(chain[["admin_id"]], geometry=chain.geometry, crs=chain.crs)
        par = chain.set_index("admin_id")[self.levels]
        P = self.P
        key_index = {(h, a): i for i, (h, a) in enumerate(zip(P.h3.to_numpy(), P[finest].astype(str).to_numpy()))}
        dom = (P.assign(_i=np.arange(len(P))).sort_values(["h3", "land_km2", "_i"], ascending=[True, False, True])
               .drop_duplicates("h3").set_index("h3")["_i"])
        new_rows: list[dict] = []
        for d, t in tables:
            f = root / d / f"{t}.csv"
            df = pd.read_csv(f, dtype={"admin_id": str, "h3": str})
            rows = np.full(len(df), -1, dtype=np.int64)
            nan = pd.Series(np.nan, index=df.index)
            lat = pd.to_numeric(df["lat"], errors="coerce") if "lat" in df.columns else nan
            lon = pd.to_numeric(df["lon"], errors="coerce") if "lon" in df.columns else nan
            ok = df.h3.notna().to_numpy() & df.h3.isin(self.hex_index).to_numpy()
            inside = pd.Series(np.nan, index=df.index, dtype=object)
            has_ll = ok & lat.notna().to_numpy() & lon.notna().to_numpy()
            if has_ll.any():
                pts = gpd.GeoDataFrame(index=df.index[has_ll],
                                       geometry=gpd.points_from_xy(lon[has_ll], lat[has_ll]), crs=4326)
                j = gpd.sjoin(pts, poly.to_crs(4326), how="left", predicate="within")
                j = j[~j.index.duplicated(keep="first")]
                inside.loc[j.index] = j["admin_id"].to_numpy(object)
            n_in = n_out = n_new = 0
            for i in np.nonzero(ok)[0]:
                h = df.h3.iat[i]
                a = inside.iat[i]
                if isinstance(a, str):
                    n_in += 1
                    k = (h, a)
                    if k not in key_index:
                        r = {"h3": h, **{lvl: par.at[a, lvl] for lvl in self.levels}, "pop": 0.0, "land_km2": 0.0,
                             "ring": int(self.H.at[h, "ring"]), "hex_km2": float(self.H.at[h, "hex_km2"]),
                             "share_in_hex": 0.0, "pt_piece": True}
                        key_index[k] = len(P) + len(new_rows)
                        new_rows.append(r)
                        n_new += 1
                    rows[i] = key_index[k]
                else:
                    n_out += 1
                    rows[i] = int(dom[h])
            self._pt_rows[(d, t)] = rows
            self.point_report[f"{d}/{t}"] = {"points": int(ok.sum()), "in_polygon": n_in, "outside_all_polygons": n_out,
                                             "pieces_added": n_new, "not_on_grid": int((~ok).sum())}
        if new_rows:
            self.P = pd.concat([P, pd.DataFrame(new_rows)], ignore_index=True)
            self.P["pt_piece"] = self.P.pt_piece.astype(bool)

    def allocate_points(self, values: np.ndarray, dom: str, table: str) -> np.ndarray:
        """Piece-level values of an extensive point metric: each point's value on its piece."""
        rows = self._pt_rows[(dom, table)]
        v = np.asarray(values, dtype=float)
        ok = (rows >= 0) & np.isfinite(v)
        out = np.zeros(len(self.P))
        np.add.at(out, rows[ok], v[ok])
        return out

    def allocate_hex(self, values: pd.Series, kind: str) -> np.ndarray:
        v = values.reindex(self.P.h3).to_numpy(float)
        if kind == "extensive":
            return v * self.P.share_in_hex.to_numpy(float)
        return v

    # -- run ---------------------------------------------------------------------------------
    def run(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[dict], list[dict]]:
        metrics, sources = load_domain_metrics(self.country)
        root = INTERIM / self.country
        # facility tables first: placing their points may add pieces, so every later array has the final length
        self.prepare_points(sorted({(m["domain"], m["table"]) for m in metrics
                                    if m["domain"] != "core" and str(m.get("table", "")).startswith("points_")
                                    and m.get("kind") == "extensive"
                                    and (root / m["domain"] / f"{m['table']}.csv").exists()}))
        tables: dict[tuple[str, str], pd.DataFrame] = {}
        piece_vals: dict[str, np.ndarray] = {}
        piece_w: dict[str, np.ndarray] = {}
        kept = []
        for m in metrics:
            dom, t, key = m["domain"], m["table"], m["key"]
            if dom == "core":
                if key in ("pop", "land_km2"):
                    piece_vals[key] = self.P[key].to_numpy(float)
                    kept.append(m)
                continue
            f = root / dom / f"{t}.csv"
            if (dom, t) not in tables:
                if not f.exists():
                    warnings.warn(f"{self.country}: {key}: missing {f}")
                    continue
                tables[(dom, t)] = pd.read_csv(f, dtype={"admin_id": str, "h3": str})
            df = tables[(dom, t)]
            if key not in df.columns:
                warnings.warn(f"{self.country}: {key} not a column of {dom}/{t}")
                continue
            col = pd.to_numeric(df[key], errors="coerce")
            # Census/ACS annotate missing estimates with large negative sentinels
            # (-666666666, -999999999, -888888888 ...): treat them as missing.
            col = col.mask(col <= -1e8)
            kind, weight = m["kind"], m.get("weight") or "pop"
            if t.startswith("admin_"):
                level = t.split("_", 1)[1]
                s = pd.Series(col.to_numpy(), index=df.admin_id.astype(str))
                s = s[~s.index.duplicated()]
                v, w = self.allocate_admin(s, level, kind, weight)
            elif "h3" in df.columns:
                if t.startswith("points_") and kind == "extensive" and (dom, t) in self._pt_rows:
                    v = self.allocate_points(col.to_numpy(float), dom, t)
                else:
                    if t.startswith("points_"):          # intensive point attribute: mean per hex
                        s = pd.DataFrame({"h3": df.h3, "v": col}).dropna(subset=["h3"]).groupby("h3").v.mean()
                    else:
                        s = pd.Series(col.to_numpy(), index=df.h3).groupby(level=0).first()
                    v = self.allocate_hex(s, kind)
                w = None
            else:
                warnings.warn(f"{self.country}: {key}: table {t} has neither admin_id nor h3")
                continue
            piece_vals[key] = v
            if kind == "intensive":
                wv = w if w is not None else self.weight(weight if weight not in ("coast",) else "land")
                # fall back to land weight for pieces with zero weight so empty land is covered
                piece_w[key] = wv
            kept.append(m)

        hexdf = self._aggregate(piece_vals, piece_w, kept, by="h3")
        admins = {lvl: self._aggregate(piece_vals, piece_w, kept, by=lvl) for lvl in self.levels}
        return hexdf, admins, kept, sources

    def _aggregate(self, piece_vals, piece_w, metrics, by: str) -> pd.DataFrame:
        P = self.P
        keys = P[by].astype(str).to_numpy()
        land = self.weight("land")
        cols = {}
        for m in metrics:
            k = m["key"]
            v = piece_vals.get(k)
            if v is None:
                continue
            if m["kind"] == "extensive":
                s = pd.Series(v).groupby(keys).sum(min_count=1)
            else:
                w = piece_w.get(k, land)
                ok = np.isfinite(v)
                num = pd.Series(np.where(ok, v * w, 0.0)).groupby(keys).sum()
                den = pd.Series(np.where(ok, w, 0.0)).groupby(keys).sum()
                s = num / den.replace(0, np.nan)
                # units / hexes with zero weight: unweighted mean of available values
                num2 = pd.Series(np.where(ok, v, 0.0)).groupby(keys).sum()
                den2 = pd.Series(ok.astype(float)).groupby(keys).sum()
                s = s.fillna(num2 / den2.replace(0, np.nan))
            cols[k] = s
        out = pd.DataFrame(cols)
        if by == "h3":
            out = out.reindex(self.hex_index)
        out.index.name = by
        return out


def reconcile(country: str) -> dict:
    rec = Reconciler(country)
    hexdf, admins, metrics, sources = rec.run()
    out = INTERIM / country / "reconciled"
    out.mkdir(parents=True, exist_ok=True)
    hexdf.to_parquet(out / "hexes.parquet")
    for lvl, df in admins.items():
        df.to_parquet(out / f"admin_{lvl}.parquet")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (out / "sources.json").write_text(json.dumps(sources, indent=2))
    (out / "points_report.json").write_text(json.dumps(rec.point_report, indent=2))
    return {"hexes": len(hexdf), "metrics": len(metrics), "sources": len(sources),
            "point_pieces_added": int(rec.P.pt_piece.sum())}


if __name__ == "__main__":
    import sys
    for cc in sys.argv[1:] or ["lka", "usa"]:
        print(cc, reconcile(cc))
