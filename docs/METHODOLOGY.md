# Methodology: from public statistics to a strategy-game map

This page documents every rule that turns official statistics into the game vocabulary the app uses.
All rules live in `pipeline/civmap/civ.py`; the app shows the same rule text in tooltips.
Nothing on the map is hand-placed: change a threshold in `civ.py`, re-run
`python -m civmap.run --stage civ export`, and the map follows.

## 1. Tiles

* **Grid.** Uber H3 hexagons. Sri Lanka at resolution 6 (average tile 36 km², ~7 km across);
  United States at resolution 5 (average tile 253 km², ~17 km across). See `docs/PLAN.md` §1 for
  why these levels.
* **Which tiles exist.** A land tile is any hex containing at least one population-raster pixel
  whose centre lies inside the national boundary (OCHA COD-AB for Sri Lanka, Census cartographic
  boundaries for the US). Sea tiles are the 2 (LKA) or 1 (US) rings of hexes around the land.
* **Population.** WorldPop 2020 pixels are summed per tile and rescaled so each district (LKA)
  or county (US) matches its official 2023 population (DCS mid-year estimate via UNFPA COD-PS;
  Census Bureau Vintage 2023 estimates).
* **Admin data → tiles.** Statistics published for provinces, districts, DS divisions, states or
  counties are allocated with the population crosswalk (pieces = tile ∩ admin unit):
  * additive quantities (GDP, jobs, tonnes): split in proportion to a weight inside each unit —
    population by default; cropland area for agricultural value added; coastal sea area for fish
    landings. Totals are preserved exactly at every level.
  * rates and shares (median income, % with a degree, religion shares): every piece takes its unit's
    value; tiles average their pieces weighted by population (or land area for land-cover metrics).
* **Facilities** (power stations, ports, airports, universities, heritage sites…) are placed in the
  tile containing their coordinates, snapped to the nearest tile when they sit just offshore.

Because the two grids differ in tile size, every yield is computed from a **density per km² of
tile**, so a Colombo tile and a Manhattan tile are compared on intensity, not on tile area.

## 2. Terrain

| Terrain | Rule (first matching rule wins from the bottom up) |
|---|---|
| Ocean / Coast | sea rings 2+ / ring 1; also land tiles that are < 35% land |
| Lake | ≥ 55% open water (ESA WorldCover) inside the boundary |
| Mountains | mean slope ≥ 16° with a peak ≥ 900 m, or mean elevation ≥ 2,200 m with slope ≥ 9°, or relief ≥ 1,600 m with slope ≥ 11° |
| Snow | ≥ 35% permanent snow & ice |
| Tundra | ≥ 20% moss & lichen, or mean annual temperature < −3 °C with < 30% trees |
| Desert | ≥ 35% bare ground, or < 330 mm rain with ≥ 50% bare + shrub |
| Grassland | ≥ 35% crop + grass + trees in humid climates (≥ 1,000 mm rain, or ≥ 40% cropland with ≥ 700 mm) |
| Plains | other vegetated land (drier prairies, dry farming) |
| *Hills* (modifier) | not mountains, mean slope ≥ 6° or relief ≥ 500 m |
| *Forest / Rainforest* (feature) | ≥ 45% tree cover; Rainforest when tropical (|lat| < 23.5°) and ≥ 1,400 mm rain |
| *Marsh / Mangrove* (feature) | ≥ 20% herbaceous wetland / ≥ 8% mangroves |
| *Urban* (feature) | ≥ 25% built-up |

Sources: ESA WorldCover 2021 v200 (10 m), Copernicus DEM GLO-90, WorldClim 2.1 (BIO1, BIO12).

## 3. Yields

One yield point per half-decade (×√10 ≈ 3.16) of density above a base, capped at 12 before bonuses:

`yield = floor(2 · log10(density / base)) + 1` when `density ≥ base`, else 0.

| Yield | Density | Base | Bonuses |
|---|---|---|---|
| Gold | GDP (current US$) per km² | $100,000 | — |
| Production | industrial GVA (mining, manufacturing, construction, utilities) per km² | $30,000 | +1 / +2 / +3 for ≥ 100 / 1,000 / 3,000 MW of power stations |
| Food (land) | agriculture, forestry & fishing GVA per km² | $3,000 | — |
| Food (sea) | fish landings per km² of coastal sea | 0.1 t | — |
| Science | STEM workers per km² | 0.1 | +1 / +2 / +3 for ≥ 1,000 / 10,000 / 50,000 research works in 5 years from institutions in the tile |
| Culture | arts, recreation & hospitality workers per km² | 0.3 | +2 per World Heritage Site (max 2), +1 for ≥ 3 museums, +1 for a national park with ≥ 1 M visitors |
| Faith | places of worship per km² | 0.003 | +2 for one of the country's most notable religious sites |

Sea tiles only carry Food, Gold and Production. Money is in current US dollars at the market
exchange rate of the data year; the app can also show Sri Lankan values at purchasing-power parity.

## 4. Districts

A tile receives every district whose rule it meets; the first in this priority order is shown.

| District | Rule |
|---|---|
| City Center | centre tile of a City View metro (most populous tile at the city's coordinates) |
| Spaceport | an orbital launch site |
| Harbor | a seaport with ≥ 1 million t/yr of cargo, or one of the country's 3 busiest ports |
| Aerodrome | an airport with ≥ 1 million passengers/yr, or the country's busiest airport |
| Campus | ≥ 2,000 research works in 5 years (OpenAlex), or STEM workforce LQ ≥ 1.8 at ≥ 300 people/km² |
| Commercial Hub | finance + real estate + professional services employment LQ ≥ 1.15, and Gold ≥ 7 or GDP density in the country's top 1% of tiles |
| Industrial Zone | Production ≥ 5 and manufacturing LQ ≥ 1.5, or an export-processing zone / industrial park |
| Dam | ≥ 100 MW hydroelectric capacity |
| Power Plant | ≥ 500 MW of other generating capacity |
| Holy Site | one of the most widely documented religious sites, or worship-site density in the country's top 1% |
| Theater Square | a cultural World Heritage Site, ≥ 5 museums, or arts/recreation/hospitality LQ ≥ 2 at ≥ 300 people/km² |
| Encampment | a military installation |
| Entertainment Complex | arts, recreation & hospitality LQ ≥ 2.5 (tourism economies) |
| Plantation | perennial/tree-crop share of agriculture ≥ 40%, Food ≥ 3, ≥ 40% tree + crop cover |
| Farmland | Food ≥ 4 and ≥ 40% cropland |
| Fishery | sea tile with Food ≥ 6 (≥ 31.6 t of fish per km² of coastal sea) |
| Neighborhood | ≥ 1,500 people/km² |
| Preserve | ≥ 75% strictly protected (WDPA, IUCN Ia–IV), or a national park with visitor statistics |

LQ = location quotient: the tile's share of employment in an industry divided by the national share.

## 5. Wonders

Ranked from published figures; the rank is recomputed on every build.

| Wonder | Rule | Sri Lanka | US |
|---|---|---|---|
| Great University | most scholarly works in the last five complete years (OpenAlex) | top 5 | top 10 |
| Great Port | annual cargo volume | top 3 | top 10 |
| Great Power Station | installed capacity | top 5 | top 10 |
| Natural Wonder | annual visitors to national parks | top 5 | top 10 |
| Great Airport | annual passengers | top 1 | top 10 |
| World Heritage | every UNESCO World Heritage Site in the country | all | all |
| Holy Wonder | number of Wikipedia language editions describing the site | top 3 | top 5 |

## 6. Citizens

Employed residents are mapped to ten citizen classes. US: ACS 5-year table C24010 (SOC major
groups) and B23025 (armed forces). Sri Lanka: Labour Force Survey (ISCO-08 major groups).

| Class | US SOC groups | ISCO-08 |
|---|---|---|
| Farmers | 45 | 6 |
| Workers | 47, 49, 51, 53 | 7, 8 |
| Merchants | 41, 43 | 4 (+ sales part of 5) |
| Engineers | 15, 17, 19 | 21, 31 |
| Scholars | 21, 23, 25 | 23, 26 (legal/social) |
| Healers | 29, 31 | 22, 32, 532 |
| Service | 33, 35, 37, 39 | 5 (service), 9 |
| Administrators | 11, 13 | 1, 24 |
| Artists | 27 | 26 (creative) |
| Soldiers | armed forces (B23025) | 0 |

A city's **size** is `round(population^0.4 / 20)`; its citizen icons split that size across classes
by the largest-remainder method. Where Sri Lankan tables are only published by province, the
domain module documents how they were split to districts (see each metric's *method*).

## 7. Known limitations

* Sri Lankan GDP is officially published only by province; district and tile values are
  model-based disaggregations (method in the metric's provenance).
* OpenStreetMap places of worship (Sri Lanka) and the U.S. Religion Census congregation counts
  differ in completeness; compare Faith within a country with more confidence than across.
* H3 tiles straddle administrative boundaries; tile values for admin-sourced statistics are
  population-weighted blends of the units they overlap.
