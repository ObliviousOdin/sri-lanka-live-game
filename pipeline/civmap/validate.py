"""Validate a domain's interim outputs against the tidy-table contract.

    python -m civmap.validate <domain> [lka|usa ...]

Checks: metrics.json/sources.json exist and parse; every metric's table exists and has the
column; admin ids are known; points are inside the country bbox and snapped to the grid;
values are finite and not all-missing; referenced sources exist.  Prints coverage and the
national total of every extensive metric so it can be compared to published figures.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from .config import COUNTRIES, INTERIM
from .core import load_admin, load_hexes


def validate(domain: str, country: str) -> list[str]:
    errs: list[str] = []
    d = INTERIM / country / domain
    if not d.exists():
        return [f"{country}/{domain}: no output dir"]
    try:
        metrics = json.loads((d / "metrics.json").read_text())
        sources = {s["id"] for s in json.loads((d / "sources.json").read_text())}
    except Exception as e:  # noqa: BLE001
        return [f"{country}/{domain}: metrics.json/sources.json unreadable: {e}"]
    admin_ids = {lvl: set(load_admin(country, lvl).admin_id) for lvl in COUNTRIES[country]["admin_levels"]}
    hexes = set(load_hexes(country).h3)
    lon0, lat0, lon1, lat1 = COUNTRIES[country]["bbox"]
    tables: dict[str, pd.DataFrame] = {}
    for m in metrics:
        t = m.get("table")
        if not t:
            errs.append(f"{m['key']}: no table")
            continue
        if t not in tables:
            f = d / f"{t}.csv"
            if not f.exists():
                errs.append(f"{m['key']}: missing table {t}.csv")
                continue
            tables[t] = pd.read_csv(f, dtype={"admin_id": str})
        df = tables[t]
        if m["key"] not in df.columns:
            errs.append(f"{m['key']}: column missing in {t}.csv")
            continue
        for s in m.get("sources", []):
            if s not in sources:
                errs.append(f"{m['key']}: source {s} not in sources.json")
        v = pd.to_numeric(df[m["key"]], errors="coerce")
        n_ok = int(np.isfinite(v).sum())
        if n_ok == 0:
            errs.append(f"{m['key']}: all values missing")
        line = f"  {m['key']:<32} {t:<18} rows={len(df):>6} non-null={n_ok:>6}"
        if m["kind"] == "extensive":
            line += f" total={v.sum():.4g}"
        else:
            line += f" median={v.median():.4g} min={v.min():.4g} max={v.max():.4g}"
        print(line, f"[{m['unit']}]")
    for t, df in tables.items():
        if t.startswith("admin_"):
            lvl = t.split("_", 1)[1]
            bad = set(df.admin_id) - admin_ids.get(lvl, set())
            if bad:
                errs.append(f"{t}: {len(bad)} unknown admin_id e.g. {sorted(bad)[:5]}")
            miss = admin_ids.get(lvl, set()) - set(df.admin_id)
            if miss:
                print(f"  note {t}: {len(miss)} admin units without a row (e.g. {sorted(miss)[:5]})")
        elif t.startswith("points_"):
            for c in ("id", "name", "lat", "lon", "kind", "h3"):
                if c not in df.columns:
                    errs.append(f"{t}: missing column {c}")
            if {"lat", "lon"} <= set(df.columns):
                out = df[(df.lon < lon0) | (df.lon > lon1) | (df.lat < lat0) | (df.lat > lat1)]
                if country == "usa":  # Aleutians cross the antimeridian
                    out = out[~((out.lon > 170) & (out.lat > 50))]
                if len(out):
                    errs.append(f"{t}: {len(out)} points outside bbox e.g. {out.name.head(3).tolist()}")
            if "h3" in df.columns and df.h3.isna().any():
                print(f"  note {t}: {int(df.h3.isna().sum())} points could not be snapped to the grid")
        elif "h3" in df.columns:
            bad = set(df.h3) - hexes
            if bad:
                errs.append(f"{t}: {len(bad)} h3 cells not in grid")
    return errs


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    domain, countries = argv[0], argv[1:] or ["lka", "usa"]
    bad = 0
    for cc in countries:
        print(f"== {domain} / {cc}")
        errs = validate(domain, cc)
        for e in errs:
            print("  ERROR", e)
        bad += len(errs)
        if not errs:
            print("  OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
