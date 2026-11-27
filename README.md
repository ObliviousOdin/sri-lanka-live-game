# Hex Atlas · Sri Lanka vs United States

A strategy-game style ("4X", Civilization-like) interactive hex map that compares Sri Lanka and the
United States with **real public data**. Every tile carries Gold, Production, Food, Science, Culture
and Faith yields computed from official statistics; districts, wonders, citizens and city screens are
assigned by published-data rules, and every number links back to its source.

* Sri Lanka on an H3 resolution-6 grid (~36 km² tiles, ~7 km across): 1,861 land + 435 sea tiles
* United States on an H3 resolution-5 grid (~253 km² tiles, ~17 km across): 38,405 land + 2,557 sea tiles
* 6 yields, 18+ lenses (income, education, religion, ethnicity, workforce, disasters, energy, trade,
  population, land cover, elevation…), City View, five Advisors, an Explorer (linked table + scatter),
  a Wonders race and a full source catalogue with download provenance.

> The rules are documented in [docs/METHODOLOGY.md](docs/METHODOLOGY.md); the architecture and data
> contract in [docs/PLAN.md](docs/PLAN.md).

## Quick start (app only)

The built data is committed in `web/public/data`, so the app runs without the pipeline:

```bash
cd web
npm install
npm run dev          # http://localhost:5173
# or a production build
npm run build && npm run preview   # http://localhost:4173
```

Requires Node 18+. The optional basemap (CARTO) needs internet; everything else is local.

## Rebuilding the data

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt
cd pipeline
python -m civmap.run                 # all stages, both countries (first run downloads ~2–3 GB)
python -m civmap.run --stage reconcile civ export   # re-derive the map from cached tables
python -m civmap.run --stage national reconcile civ export   # also refresh World Bank indicators + metros
python -m civmap.validate economy    # check one domain's tables against the contract
```

Downloads are cached in `data/cache/` with a `.meta.json` sidecar per file (URL, SHA-256, size,
time); a second run works offline. Intermediate tidy tables go to `data/interim/` (both gitignored).

### Pipeline stages

| Stage | Module | What it does |
|---|---|---|
| core | `civmap/core.py` | Boundaries (OCHA COD-AB; Census cartographic files), WorldPop rasters → H3 grid + population crosswalk (pixel → tile × admin unit), rescaled to official 2023 populations |
| domains | `civmap/domains/*.py` | One module per theme writes tidy admin / point / hex tables + metric & source metadata |
| national | `civmap/domains/national.py` | World Bank indicators for the advisors, metro list for City View |
| reconcile | `civmap/reconcile.py` | Allocates every admin table to tiles (mass-preserving for totals, weighted means for rates) and re-aggregates to every admin level |
| civ | `civmap/civ.py` | Terrain classes, yields, districts, wonders, citizens, city screens |
| export | `civmap/export.py` | Columnar JSON + Float32 metric matrix (`metrics.bin`, rounded to ~4 significant digits, plus a gzip copy `metrics.bin.gz` that the app inflates in the browser) + simplified GeoJSON for the web app |

### Data domains

| Domain | Sri Lanka | United States |
|---|---|---|
| terrain | Copernicus DEM GLO-90, ESA WorldCover 2021, WorldClim 2.1 | same |
| economy | DCS provincial GDP, HIES income & poverty, Meta Relative Wealth Index | BEA county GDP, ACS income & poverty |
| labor | DCS Labour Force Survey (ISCO / ISIC) | ACS C24010 / C24030 / B23025 |
| food | DCS provincial agriculture GVA, crop & fisheries statistics | BEA state agriculture GDP × Census of Agriculture, NOAA landings |
| science | OpenAlex institutions, Census education, schools | OpenAlex, NSF HERD, ACS B15003 |
| society | Census religion & ethnicity, OSM places of worship, Wikidata | 2020 U.S. Religion Census, ACS B03002, Wikidata |
| culture | Wikidata (UNESCO WHS, museums), DWC/SLTDA park visitors, WDPA | Wikidata, NPS visitor statistics, WDPA |
| hazards | IBTrACS, USGS earthquakes, NOAA tsunami run-ups, DesInventar | same + FEMA National Risk Index |
| infra | WRI Global Power Plant Database, PortWatch, OurAirports (no open airport-level passenger statistics), Wikidata | EIA-860, PortWatch, OurAirports + FAA enplanements (boardings), Wikidata |

The app's **Sources** tab lists every dataset with publisher, vintage, licence, access date and the
metrics it feeds.

## Repository layout

```
pipeline/civmap/   Python data pipeline (see stages above)
data/              cache/ and interim/ (gitignored, re-creatable)
web/               Vite + React + TypeScript + deck.gl app; built data in web/public/data
docs/              PLAN.md (architecture & contract), METHODOLOGY.md (game rules)
```
