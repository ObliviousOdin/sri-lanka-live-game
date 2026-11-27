"""Paths and per-country configuration shared by every pipeline stage."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = DATA / "cache"          # raw downloads (gitignored, re-creatable)
INTERIM = DATA / "interim"      # tidy tables written by domain modules (gitignored)
WEB_DATA = ROOT / "web" / "public" / "data"   # exported app data (committed)

# Vintage used to convert local-currency figures to current US dollars when a domain
# module has no better year-specific rate.  Domain modules should prefer the World Bank
# PA.NUS.FCRF series for the exact vintage year (see civmap.fx).
COUNTRIES: dict[str, dict] = {
    "lka": {
        "name": "Sri Lanka",
        "iso3": "LKA",
        "iso2": "LK",
        "h3_res": 6,
        "water_rings": 2,
        "admin_levels": {"adm1": "Province", "adm2": "District", "adm3": "DS Division"},
        "finest_admin": "adm3",
        "currency": "LKR",
        "bbox": (79.4, 5.8, 82.1, 10.0),  # lon_min, lat_min, lon_max, lat_max
    },
    "usa": {
        "name": "United States",
        "iso3": "USA",
        "iso2": "US",
        "h3_res": 5,
        "water_rings": 1,
        "admin_levels": {"adm1": "State", "adm2": "County"},
        "finest_admin": "adm2",
        "currency": "USD",
        "bbox": (-179.5, 18.5, -66.5, 71.6),
    },
}

# US: 50 states + DC (FIPS).  Territories are excluded.
US_STATE_FIPS = [
    "01", "02", "04", "05", "06", "08", "09", "10", "11", "12", "13", "15", "16", "17", "18", "19",
    "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35",
    "36", "37", "38", "39", "40", "41", "42", "44", "45", "46", "47", "48", "49", "50", "51", "53",
    "54", "55", "56",
]


def interim_dir(country: str, domain: str) -> Path:
    p = INTERIM / country / domain
    p.mkdir(parents=True, exist_ok=True)
    return p


def web_dir(country: str | None = None) -> Path:
    p = WEB_DATA / country if country else WEB_DATA
    p.mkdir(parents=True, exist_ok=True)
    return p
