# Hex Atlas: Sri Lanka vs United States — architecture & data pipeline plan

This document is the contract between the data pipeline (`pipeline/`) and the web app (`web/`).
Read it before adding a data domain or a UI panel.

## 1. Hex grids

| Country | Grid | Avg hex area | Across (corner–corner) | Hexes (approx.) |
|---|---|---|---|---|
| Sri Lanka | H3 resolution **6** | 36.1 km² | ~7.3 km | ~2,000 land + 2 rings of sea |
| United States (50 states + DC) | H3 resolution **5** | 252.9 km² | ~17 km | ~37,000 land + 1 ring of sea |

* H3 levels step by √7 in linear size. Res 6 sits inside the requested 5–10 km band for Sri Lanka.
  For the US the request was "~25 km": res 5 (~17 km) is the nearest level (res 4 is ~45 km, which
  would merge Manhattan, Brooklyn and Jersey City into one tile). The ratio (×7 area) is deliberate:
  both maps have a comparable number of tiles across a metro area.
* An optional **equal-tile** mode recomputes Sri Lanka at res 5, so a tile means the same land area in
  both countries.
* Yields are computed from **densities** (per km²), so a res-6 tile and a res-5 tile with the same
  intensity of activity get the same yield. Totals always sum absolute values.
* Land hexes = every hex that contains at least one population-raster pixel inside the country boundary.
  Water hexes = k-rings around land hexes (2 rings for LKA, 1 ring for USA), classed Coast/Ocean.

## 2. Pipeline stages

```
fetch (cached) ──► parse (domain modules) ──► tidy tables (data/interim) ──► reconcile to hexes ──► civ layer ──► export (web/public/data)
```

1. **Fetch** — `civmap.fetch.fetch(url, source_id, subdir)` downloads once into `data/cache/<subdir>/`,
   writing a sidecar `<file>.meta.json` (url, sha256, bytes, fetched_at, source_id). Re-runs are offline.
2. **Core geography** (`civmap.core`):
   * boundaries — LKA: OCHA COD-AB (Survey Dept / DCS) ADM1 province, ADM2 district, ADM3 DS division.
     USA: Census cartographic boundary files 2023 (states, counties, 1:500k).
   * population rasters — WorldPop 2020 (LKA 100 m, USA 1 km), rescaled to official admin totals.
   * **crosswalk** — every population pixel is assigned (hex, finest admin unit). Aggregating gives a
     table of *pieces* `(h3, admin_id, pop, land_km2)` used for all admin→hex allocation.
3. **Domain modules** (`civmap/domains/<domain>.py`) each expose:
   ```python
   SOURCES: list[Source]
   METRICS: list[Metric]
   def build(country: str) -> None   # writes tidy tables to data/interim/<country>/<domain>/
   ```
4. **Reconcile** (`civmap.reconcile`) — admin tables → hexes using the crosswalk:
   * extensive metrics (sums, e.g. GDP): `hex = Σ_admin value_admin × w(hex∩admin) / w(admin)`
   * intensive metrics (rates, e.g. median income): `hex = Σ_admin value_admin × w(hex∩admin) / w(hex)`
   * the weight `w` is `pop` (default), `land` (area), or a land-cover weight such as `cropland`.
   * point tables (plants, ports, universities) → `h3.latlng_to_cell`, snapped to the nearest grid hex.
5. **Civ layer** (`civmap.civ`) — terrain class, yields, districts, wonders, citizens, cities (§4).
6. **Export** (`civmap.export`) — static files in `web/public/data/<country>/` (§3).

Run everything: `python -m civmap.run` (or `--country lka --stage reconcile`, see README).

## 3. Tidy-table contract (data/interim/&lt;country&gt;/&lt;domain&gt;/)

| File | Columns | Notes |
|---|---|---|
| `admin_<level>.csv` | `admin_id`, metric keys… | `level` ∈ `adm1`, `adm2`, `adm3`. IDs: LKA COD-AB pcodes (`LK1`, `LK11`, `LK1103`…); USA FIPS (`06`, `06037`). |
| `points_<name>.csv` | `id,name,lat,lon,kind`, attributes… | Facilities and sites. |
| `hex.csv` | `h3`, metric keys… | Values already computed per hex (rasters). |
| `metrics.json` | list of `Metric` | Written by `civmap.schema.write_domain_meta`. |
| `sources.json` | list of `Source` | idem |

`Metric` fields: `key` (same key in both countries when comparable), `label`, `unit`, `category`,
`kind` (`extensive`/`intensive`), `weight` (`pop`/`land`/`cropland`/`tree`/`built`), `table`
(file stem, e.g. `admin_adm2`), `sources` (ids), `vintage`, `description`, `method`, `better`
(`higher`/`lower`/`neutral`).

Money is **current US dollars** (market exchange rate of the vintage year). The app can display
Sri Lankan values PPP-adjusted using the World Bank PPP conversion factor.

## 4. Civ translation (implemented in `civmap/civ.py`, documented in METHODOLOGY.md)

* **Yields** (per tile, shared scale for both countries, from per-km² densities on a log scale):
  Gold ← GDP; Production ← industrial GDP (+ power-plant bonus); Food ← agricultural & fisheries
  output; Science ← research output + STEM workers; Culture ← tourism, heritage & cultural sites;
  Faith ← places of worship & pilgrimage sites.
* **Terrain** ← ESA WorldCover fractions + Copernicus DEM relief (Grassland, Plains, Desert, Tundra,
  Snow, Hills, Mountains; features Forest, Rainforest, Marsh, Mangrove; Coast, Lake, Ocean; Urban).
* **Districts** ← location quotients & facility thresholds (Commercial Hub, Harbor, Campus,
  Industrial Zone, Plantation, Farmland, Holy Site, Theater Square, Entertainment, Encampment,
  Aerodrome, Dam/Power, Spaceport, Neighborhood).
* **Wonders** ← published ranking rules (national parks by visitors, universities by research output,
  ports by cargo, power plants by capacity, UNESCO World Heritage, airports by passengers).
* **Citizens** ← workforce composition (ACS occupations / Sri Lanka LFS ISCO groups) mapped to
  Farmers, Workers, Merchants, Engineers, Scholars, Healers, Service, Administrators, Artists, Soldiers.

## 5. Web data contract (web/public/data/)

```
index.json                    countries, build date, global yield scales
sources.json                  all sources (id, title, publisher, url, license, vintage, accessed)
national.json                 World Bank national indicators for both countries
<cc>/meta.json                hex res, bbox, metric list (Metric + stats), admin levels, column order
<cc>/hexes.json               {h3:[…], terrain:[…], district:[…], adm1:[…], adm2:[…], yields:{gold:[…],…}}
<cc>/metrics.bin              Float32 column-major matrix, n_hex × n_metrics (NaN = no data)
<cc>/admin_<level>.json       units with id, name, parent, centroid, aggregated metric values
<cc>/admin_<level>.geojson    simplified boundaries for outlines and admin picking
<cc>/points.json              facilities used for wonders/districts (with source ids)
<cc>/wonders.json             wonders with rule, rank, value, source
<cc>/cities.json              City View payload: centre hex, ring tiles, citizens, districts
```

## 6. Web app

Vite + React + TypeScript + deck.gl (`H3HexagonLayer`, `GeoJsonLayer`) on an optional MapLibre basemap.
Panels: Tile inspector · City View · Advisors · Explorer (table + linked scatter) · Sources.
Layouts: side-by-side (desktop) or toggle (mobile).
