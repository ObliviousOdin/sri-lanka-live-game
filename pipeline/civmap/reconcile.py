"""Reconcile every domain's tidy tables onto the hex grid (and back up to every admin level).

Allocation happens at the *piece* level (a piece = hex ∩ finest admin unit, from the
population crosswalk) so one code path serves both hex values and admin aggregates:

* admin tables, extensive:  v_piece = value_admin × w_piece / Σ_admin w        (mass preserving)
* admin tables, intensive:  v_piece = value_admin; hexes/admins average with weight w
* hex tables:               extensive values split over a hex's pieces by land share;
                            intensive values copied to every piece of the hex
* point tables:             summed per hex (extensive), then split like hex tables

Water hexes (sea rings) take part as pseudo-pieces with zero land and population that
inherit the admin units of their nearest land hex, so coastal allocations (fish catch) and
facilities snapped to the sea (ports) are kept.

Weights `w`: pop, land, cropland, tree, built, grass (land × land-cover fraction of the hex),
coast (ring-1 sea hexes, by area).  If an admin unit has zero total weight the allocation
falls back to land area, then to population.
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

from .config import COUNTRIES, INTERIM
from .core import load_hexes, load_pieces

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
        self.P = P.reset_index(drop=True)
        self.hex_index = self.H.index
        self._lc = None

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
            return np.where(P.ring == 1, P.hex_km2, 0.0)
        cols = {"cropland": ["lc_crop"], "tree": ["lc_tree"], "built": ["lc_built"],
                "grass": ["lc_grass", "lc_shrub"]}.get(kind)
        lc = self._landcover()
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
            w_avg = w.copy()
            # hexes whose pieces all have zero weight (empty land) still get a value via land
            return val, w_avg
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

    def allocate_hex(self, values: pd.Series, kind: str) -> np.ndarray:
        v = values.reindex(self.P.h3).to_numpy(float)
        if kind == "extensive":
            return v * self.P.share_in_hex.to_numpy(float)
        return v

    # -- run ---------------------------------------------------------------------------------
    def run(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[dict], list[dict]]:
        metrics, sources = load_domain_metrics(self.country)
        root = INTERIM / self.country
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
                if t.startswith("points_"):
                    g = pd.DataFrame({"h3": df.h3, "v": col}).dropna(subset=["h3"])
                    s = g.groupby("h3").v.sum(min_count=1) if kind == "extensive" else g.groupby("h3").v.mean()
                    if kind == "extensive":
                        s = s.reindex(self.hex_index).fillna(0.0)
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
    return {"hexes": len(hexdf), "metrics": len(metrics), "sources": len(sources)}


if __name__ == "__main__":
    import sys
    for cc in sys.argv[1:] or ["lka", "usa"]:
        print(cc, reconcile(cc))
