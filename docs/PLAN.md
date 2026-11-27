# Hex Atlas: Sri Lanka vs United States — architecture & data pipeline plan

This document is the contract between the data pipeline (`pipeline/`) and the web app (`web/`).
Read it before adding a data domain or a UI panel.

## 1. Hex grids

| Country | Grid | Mean tile area (exported grid) | Across: flat–flat / corner–corner | Tiles |
|---|---|---|---|---|
| Sri Lanka | H3 resolution **6** | 39.0 km² (global res-6 average: 36.1 km²) | ~6.7 km / ~7.7 km | 1,861 land + 435 sea (2 rings) |
| United States (50 states + DC) | H3 resolution **5** | 255.2 km² (global res-5 average: 252.9 km²) | ~17.2 km / ~19.8 km | 38,405 land + 2,557 sea (1 ring) |

The means are `avg_hex_km2` in `<cc>/meta.json` (all exported tiles, land and sea alike; Sri Lanka's land
tiles alone average 39.0 km², the US land tiles 255.7 km²). H3 cells near the equator are larger than the
global average for their resolution. Widths are those of a regular hexagon of the mean area.

* H3 levels step by √7 in linear size. Res 6 sits inside the requested 5–10 km band for Sri Lanka.
  For the US the request was "~25 km": res 5 (~17 km across the flats, ~20 km corner to corner) is the
  nearest level (res 4 is ~45 km across the flats, which would merge Manhattan, Brooklyn and Jersey City
  into one tile). The ratio (×7 area) is deliberate: both maps have a comparable number of tiles across a
  metro area.
* *Possible extension, not implemented:* an equal-tile mode that recomputes Sri Lanka at res 5 so a tile
  means the same area in both countries (`civmap.core.build(country, res=5, tag="core_r5")` can already
  build the grid and crosswalk; reconcile, civ, export and the app have no res-5 Sri Lanka option). The
  app's "True size" view only locks the km-per-pixel scale of the two maps.
* Yields are computed from **densities per km² of tile area**, so a res-6 tile and a res-5 tile with the
  same intensity of activity get the same yield; compare yields across countries as per-tile means,
  never as sums (the US has ~20× more tiles). Totals of extensive metrics always sum absolute values.
  Densities of extensive metrics in the app are per km² of tile ("per km² of tile"); population
  density alone is per km² of land.
* Land hexes = every hex that contains at least one population-raster pixel inside the country boundary.
  Water hexes = k-rings around land hexes (2 rings for LKA, 1 ring for USA), classed Coast/Ocean.
  `land_km2` = area inside the national boundary (summed pixel areas), *including* inland water.

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
   * intensive metrics (rates, e.g. median income): `hex = Σ_admin value_admin × w(hex∩admin) / w(hex)`;
     admin units above the published level get the same weighted average of their pieces. Sea hexes take
     no admin-level intensive value (NaN) unless the weight is `coast`.
   * the weight `w` is `pop` (default), `land` (area), `coast` (ring-1 sea area) or a land-cover weight
     such as `cropland`, `tree`, `built`, `grass`.
   * point tables (plants, ports, universities) → the hex in the table's `h3` column (`h3.latlng_to_cell`,
     snapped to the nearest grid hex), and the finest admin unit whose polygon contains the point
     (point-in-polygon; offshore points: the hex's dominant unit). Each point's whole value goes to that
     piece, so hex totals and admin totals are both exact sums of points.
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
`kind` (`extensive`/`intensive`), `weight` (`pop`/`land`/`cropland`/`tree`/`built`/`grass`/`coast`), `table`
(file stem, e.g. `admin_adm2`), `sources` (ids), `vintage`, `description`, `method`, `better`
(`higher`/`lower`/`neutral`).

Money is **current US dollars** (market exchange rate of the vintage year). The app shows no PPP-adjusted
tile, admin or city values; purchasing-power comparisons are limited to the Advisors' national remarks,
which quote the World Bank PPP indicators in `national.json`.

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
  ports by cargo, power plants by capacity, UNESCO World Heritage, airports by enplanements, i.e. FAA
  passenger boardings; Sri Lanka publishes no airport-level passenger statistics, so it has no Great
  Airport).
* **Citizens** ← workforce composition (ACS occupations / Sri Lanka LFS ISCO groups) mapped to
  Farmers, Workers, Merchants, Engineers, Scholars, Healers, Service, Administrators, Artists, Soldiers.

## 5. Web data contract (web/public/data/)

```
index.json                    countries, build date, global yield scales, district rule texts
sources.json                  all sources (id, title, publisher, url, license, vintage, accessed, metrics per
                              country, used_in {cc: ['cities' | 'wonders'], national: ['advisors']},
                              provenance 'download' | 'software' | 'none', n_downloads, total_bytes and the
                              first 20 download records: url, bytes, fetched_at, sha256 prefix)
national.json                 World Bank national indicators for both countries
<cc>/meta.json                hex res, bbox, metric list (Metric + stats), admin levels, column order. Each
                              metric's `method` holds only that country's part of a two-country text.
                              stats: n, min, p05, p50, p95, max over land tiles; `total` (extensive
                              metrics) over all tiles, land and sea rings
<cc>/hexes.json               {h3:[…], terrain:[…], district:[…], adm1:[…], adm2:[…], yields:{gold:[…],…}}
<cc>/metrics.bin.gz           Float32 column-major matrix, n_hex × n_metrics (NaN = no data), values rounded
                              to 13 mantissa bits (~4 significant digits, relative error ≤ 6e-5), gzip level 9
                              (US ~8.7 MB vs 23 MB raw); the app fetches it and inflates it with
                              DecompressionStream (fflate where the browser lacks it)
<cc>/metrics.bin              the same matrix uncompressed: written by the pipeline for local inspection,
                              gitignored and not loaded by the app
<cc>/admin_<level>.json       units with id, name, parent, centroid, aggregated metric values
<cc>/admin_<level>.geojson    simplified boundaries for outlines and admin picking
<cc>/points.json              facilities used for wonders/districts (with source ids)
<cc>/wonders.json             wonders with rule, rank, value, source (meta.json wonder_categories: status
                              per category, e.g. no_data for Sri Lanka's Great Airport, with note and rule)
<cc>/cities.json              City View payload: centre hex, ring tiles (after de-duplication) and
                              ring_tiles / overlap (tiles ceded to a nearer city), citizens, districts,
                              official_pop + population_scope ('metro area (Census MSA)' |
                              'city proper (Wikidata)'), population_note, population_sources; Sri Lanka
                              also census_ds_population (Census 2024, DS division holding the centre)
```

## 6. Web app

Vite + React + TypeScript + deck.gl (`H3HexagonLayer`, `GeoJsonLayer`) on an optional MapLibre basemap.
Panels: Tile inspector · City View · Advisors · Explorer (table + linked scatter) · Sources.
Layouts: side-by-side (desktop) or toggle (mobile).
