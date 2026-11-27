"""Helpers shared by domain modules (writers, World Bank, Wikidata, OpenAlex, ACS, point snapping).

Domain modules should only *add* helpers locally; keep this file stable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from .config import COUNTRIES, interim_dir
from .fetch import fetch

# ---------------------------------------------------------------------------------------------
# Writers (the tidy-table contract, see docs/PLAN.md §3)
# ---------------------------------------------------------------------------------------------


def write_admin(country: str, domain: str, level: str, df: pd.DataFrame) -> Path:
    """Write admin-level table. Must contain `admin_id` + metric columns."""
    assert "admin_id" in df.columns, "admin table needs admin_id"
    assert level in COUNTRIES[country]["admin_levels"], f"unknown level {level}"
    df = df.copy()
    df["admin_id"] = df["admin_id"].astype(str)
    assert not df.admin_id.duplicated().any(), "duplicate admin_id"
    p = interim_dir(country, domain) / f"admin_{level}.csv"
    df.to_csv(p, index=False)
    return p


def write_points(country: str, domain: str, name: str, df: pd.DataFrame) -> Path:
    """Write point table with id,name,lat,lon,kind (+attributes). Adds the snapped `h3` column."""
    for c in ("id", "name", "lat", "lon", "kind"):
        assert c in df.columns, f"points table needs {c}"
    df = df.copy()
    df["h3"] = snap_points(df["lat"].to_numpy(float), df["lon"].to_numpy(float), country)
    p = interim_dir(country, domain) / f"points_{name}.csv"
    df.to_csv(p, index=False)
    return p


def write_hex(country: str, domain: str, df: pd.DataFrame, name: str = "hex") -> Path:
    assert "h3" in df.columns
    p = interim_dir(country, domain) / f"{name}.csv"
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------------------------

_GRID: dict[str, set[str]] = {}


def grid(country: str) -> set[str]:
    if country not in _GRID:
        from .core import load_hexes
        _GRID[country] = set(load_hexes(country).h3)
    return _GRID[country]


def snap_points(lats, lons, country: str, max_k: int = 6) -> list[str | None]:
    """H3 cell of each point at the country resolution, snapped to the nearest grid cell."""
    res = COUNTRIES[country]["h3_res"]
    g = grid(country)
    out = []
    for la, lo in zip(lats, lons):
        if not (np.isfinite(la) and np.isfinite(lo)):
            out.append(None)
            continue
        c = h3.latlng_to_cell(float(la), float(lo), res)
        if c not in g:
            hit = None
            for k in range(1, max_k + 1):
                ring = [n for n in h3.grid_ring(c, k) if n in g]
                if ring:
                    hit = ring[0]
                    break
            c = hit
        out.append(c)
    return out


def hex_children_points(cells, child_res: int) -> pd.DataFrame:
    """Sample points (centres of H3 children) for zonal statistics of rasters.

    Returns DataFrame h3 (parent), lat, lon.  Use child_res = res+2 (49 pts/hex) or res+3 (343).
    """
    rows = []
    for c in cells:
        for ch in h3.cell_to_children(c, child_res):
            la, lo = h3.cell_to_latlng(ch)
            rows.append((c, la, lo))
    return pd.DataFrame(rows, columns=["h3", "lat", "lon"])


# ---------------------------------------------------------------------------------------------
# World Bank WDI
# ---------------------------------------------------------------------------------------------


def wb_series(indicator: str, countries: str = "LKA;USA") -> pd.DataFrame:
    """All years of a WDI indicator for the given ISO3 list -> DataFrame iso3, year, value."""
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
    p = fetch(url, "worldbank_wdi", "worldbank", f"{indicator}_{countries.replace(';', '_')}.json",
              params={"format": "json", "per_page": 20000})
    js = json.loads(p.read_text())
    if not isinstance(js, list) or len(js) < 2 or js[1] is None:
        return pd.DataFrame(columns=["iso3", "year", "value"])
    rows = [(r["countryiso3code"], int(r["date"]), r["value"]) for r in js[1]]
    return pd.DataFrame(rows, columns=["iso3", "year", "value"]).dropna()


def wb_value(indicator: str, iso3: str, year: int | None = None) -> tuple[float | None, int | None]:
    """(value, year) for `year`, or the latest non-null year if year is None / missing."""
    d = wb_series(indicator)
    d = d[d.iso3 == iso3].sort_values("year")
    if d.empty:
        return None, None
    if year is not None and (d.year == year).any():
        r = d[d.year == year].iloc[0]
    else:
        r = d.iloc[-1]
    return float(r.value), int(r.year)


def lkr_per_usd(year: int) -> float:
    """Official exchange rate (LCU per US$, period average), World Bank PA.NUS.FCRF."""
    v, y = wb_value("PA.NUS.FCRF", "LKA", year)
    if v is None:
        raise RuntimeError("no LKR/USD rate")
    return v


# ---------------------------------------------------------------------------------------------
# Wikidata / OpenAlex
# ---------------------------------------------------------------------------------------------


def sparql(query: str, name: str) -> pd.DataFrame:
    """Run a Wikidata SPARQL query (cached by query hash). Returns flat DataFrame of values."""
    hsh = hashlib.sha1(query.encode()).hexdigest()[:10]
    p = fetch("https://query.wikidata.org/sparql", "wikidata", "wikidata", f"{name}_{hsh}.json",
              params={"query": query, "format": "json"},
              headers={"Accept": "application/sparql-results+json"}, timeout=120)
    js = json.loads(p.read_text())
    rows = [{k: v.get("value") for k, v in b.items()} for b in js["results"]["bindings"]]
    return pd.DataFrame(rows)


def parse_wkt_point(s: str) -> tuple[float, float]:
    """'Point(lon lat)' -> (lat, lon)."""
    s = s[s.index("(") + 1: s.index(")")]
    lo, la = s.split()[:2]
    return float(la), float(lo)


def openalex(path: str, params: dict, name: str, max_pages: int = 50) -> list[dict]:
    """Cursor-paged OpenAlex query, cached per page. Returns concatenated `results`."""
    out = []
    cursor = "*"
    for page in range(max_pages):
        q = dict(params)
        q.update({"per-page": 200, "cursor": cursor})
        p = fetch(f"https://api.openalex.org/{path}", "openalex", "openalex", f"{name}_p{page:03d}.json", params=q)
        js = json.loads(p.read_text())
        out.extend(js.get("results", []))
        cursor = js.get("meta", {}).get("next_cursor")
        if not cursor or not js.get("results"):
            break
    return out


# ---------------------------------------------------------------------------------------------
# ACS (US Census American Community Survey) — table-based summary file, no API key needed
# ---------------------------------------------------------------------------------------------


def acs_table(table: str, year: int = 2023, span: int = 5) -> pd.DataFrame:
    """County rows of an ACS detailed table from the table-based Summary File.

    Returns DataFrame indexed by 5-digit county FIPS with the table's estimate columns
    (e.g. B19013_E001).  Margins of error are dropped.
    """
    t = table.lower()
    url = (f"https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/"
           f"{span}YRData/acsdt{span}y{year}-{t}.dat")
    p = fetch(url, f"census_acs{span}_{year}", f"usa/acs{year}")
    df = pd.read_csv(p, sep="|", dtype=str)
    df = df[df.GEO_ID.str.startswith("0500000US")].copy()
    df.index = df.GEO_ID.str[-5:]
    df.index.name = "admin_id"
    est = [c for c in df.columns if "_E" in c]
    return df[est].apply(pd.to_numeric, errors="coerce")
