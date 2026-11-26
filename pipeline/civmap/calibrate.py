"""Diagnostics for the Civ layer: pooled density distributions behind each yield, and the
resulting yield / terrain / district histograms.

    python -m civmap.calibrate
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .civ import YIELD_BASES, YIELDS, log_yield
from .config import INTERIM
from .core import load_hexes


def main():
    rows = []
    for cc in ("lka", "usa"):
        R = pd.read_parquet(INTERIM / cc / "reconciled" / "hexes.parquet")
        core = load_hexes(cc).set_index("h3")
        land = core.ring == 0
        for y, (k, base, unit) in YIELD_BASES.items():
            if k not in R.columns:
                rows.append((cc, y, k, "missing"))
                continue
            d = (R[k] / core.hex_km2).where(land if y != "food_sea" else ~land)
            d = d.dropna()
            q = d.quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999]).to_dict()
            yv = log_yield(d.to_numpy(), base)
            rows.append((cc, y, k, f"pos={np.mean(d > 0):.2f} q10={q[0.1]:.3g} q50={q[0.5]:.3g} "
                         f"q90={q[0.9]:.3g} q99={q[0.99]:.3g} max={d.max():.3g} | yields "
                         f"0:{np.mean(yv == 0):.2f} med:{np.median(yv):.0f} p99:{np.quantile(yv, .99):.0f} max:{yv.max()}"))
    for r in rows:
        print(f"{r[0]} {r[1]:<10} {r[2]:<24} {r[3]}")
    for cc in ("lka", "usa"):
        f = INTERIM / cc / "civ" / "summary.json"
        if f.exists():
            print(cc, json.dumps(json.loads(f.read_text()), indent=None)[:1500])


if __name__ == "__main__":
    main()
