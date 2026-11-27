"""Pipeline orchestrator.

    python -m civmap.run                       # everything, both countries
    python -m civmap.run --stage domains --domain economy --country lka
    python -m civmap.run --stage reconcile civ export

Stages: core → domains → reconcile → civ → export.  Every download is cached under
data/cache, so a second run works offline and only recomputes.
"""
from __future__ import annotations

import argparse
import importlib
import json
import time
import traceback

from .config import COUNTRIES

DOMAINS = ["terrain", "economy", "labor", "food", "science", "society", "culture", "hazards", "infra"]
STAGES = ["core", "domains", "national", "reconcile", "civ", "export"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", nargs="*", default=STAGES, choices=STAGES)
    ap.add_argument("--country", nargs="*", default=list(COUNTRIES), choices=list(COUNTRIES))
    ap.add_argument("--domain", nargs="*", default=DOMAINS)
    ap.add_argument("--keep-going", action="store_true", help="continue when a domain fails")
    a = ap.parse_args()
    failed = []

    def step(name, fn, *args):
        t = time.time()
        print(f"▶ {name}", flush=True)
        try:
            r = fn(*args)
            print(f"  ✓ {name} ({time.time() - t:.0f}s) {json.dumps(r, default=str)[:300] if r else ''}", flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failed.append(name)
            if not a.keep_going:
                raise

    if "core" in a.stage:
        from . import core
        for cc in a.country:
            step(f"core:{cc}", core.build, cc)
    if "domains" in a.stage:
        for d in a.domain:
            mod = importlib.import_module(f"civmap.domains.{d}")
            for cc in a.country:
                step(f"{d}:{cc}", mod.build, cc)
    if "national" in a.stage:
        mod = importlib.import_module("civmap.domains.national")
        step("national", mod.build)
    if "reconcile" in a.stage:
        from .reconcile import reconcile
        for cc in a.country:
            step(f"reconcile:{cc}", reconcile, cc)
    if "civ" in a.stage:
        from . import civ
        for cc in a.country:
            step(f"civ:{cc}", civ.build, cc)
    if "export" in a.stage:
        from . import export
        for cc in a.country:
            step(f"export:{cc}", export.export_country, cc)
        step("export:global", export.export_global)
    if failed:
        print("FAILED:", failed)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
