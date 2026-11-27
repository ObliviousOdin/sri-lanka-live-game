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
tile**, so a Colombo tile and a Manhattan tile are compared on intensity, not on tile area. The app
follows the same convention: densities of additive quantities are "per km² of tile" (sea and lake
area included); only population density is per km² of land. Yields are per-tile intensity indices,
so compare their per-tile means across countries, never their sums (the US has ~20× more tiles).

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

One yield point per half-decade (×√10 ≈ 3.16) of density above a base, capped at 12 before bonuses
(16 with bonuses):

`yield = floor(2 · log10(density / base)) + 1` when `density ≥ base`, else 0.

| Yield | Density | Base | Bonuses |
|---|---|---|---|
| Gold | GDP (current US$) per km² | $100,000 | — |
| Production | industrial GVA (mining, manufacturing, construction, utilities) per km² | $30,000 | +1 / +2 / +3 for ≥ 100 / 1,000 / 3,000 MW of power stations |
| Food (land) | agriculture, forestry & fishing GVA per km² | $3,000 | — (a land tile takes the larger of its land and sea Food) |
| Food (sea) | fish landings per km² of coastal sea (sea tiles: per km² of tile) | 0.1 t | — |
| Science | STEM workers per km² | 0.1 | +1 / +2 / +3 for ≥ 1,000 / 10,000 / 50,000 research works in 5 years from institutions in the tile |
| Culture | arts, recreation & hospitality workers per km² | 0.3 | +2 per World Heritage Site (up to 2 sites, +4), +1 for ≥ 3 museums, +1 for a national park with ≥ 1 M visitors/yr |
| Faith | places of worship per km² | 0.003 | +2 for a tile holding one or more of the country's most widely documented religious sites (Wikidata; top 30 in Sri Lanka, top 80 in the US by Wikipedia language editions) |

Sea tiles only carry Food, Gold and Production. Money is in current US dollars at the market
exchange rate of the data year; the app can also show Sri Lankan values at purchasing-power parity.

## 4. Districts

A tile receives every district whose rule it meets; the first in this priority order is shown.

| District | Rule |
|---|---|
| City Center | centre tile of a City View city: the most populous tile within one ring of the city's coordinates |
| Spaceport | a launch site that has hosted an orbital or orbital-class launch: Cape Canaveral (incl. Launch Complex 46), Kennedy, Vandenberg, Wallops / Mid-Atlantic Regional Spaceport, Pacific Spaceport Complex – Alaska, Starbase. Other sites on the FAA spaceport list (runway, suborbital or reentry-only) stay on the map as points but do not qualify |
| Harbor | a seaport with ≥ 1 million t/yr of cargo, or one of the country's 3 busiest ports |
| Aerodrome | an airport with ≥ 1 million passenger boardings/yr (US: FAA enplanements, about half of all arriving + departing passengers), or the country's busiest airport by boardings. Sri Lanka has no open airport-level passenger statistics, so there every OurAirports *large airport* with scheduled service qualifies |
| Campus | ≥ 2,000 research works in 5 years (OpenAlex), or STEM workforce LQ ≥ 1.8 at ≥ 300 people/km² |
| Commercial Hub | finance + real estate + professional services employment LQ ≥ 1.15, and Gold ≥ 7 or GDP density in the country's top 1% of tiles |
| Industrial Zone | Production ≥ 5 and manufacturing LQ ≥ 1.5, or an export-processing zone / industrial park |
| Dam | ≥ 100 MW hydroelectric capacity |
| Power Plant | ≥ 500 MW of other generating capacity |
| Holy Site | one of the most widely documented religious sites (top 10 in Sri Lanka, top 25 in the US by Wikipedia language editions), or worship-site density in the country's top 1% of land tiles |
| Theater Square | a cultural World Heritage Site, ≥ 5 museums, or arts/recreation/hospitality LQ ≥ 2 at ≥ 300 people/km² |
| Encampment | a military installation |
| Entertainment Complex | arts, recreation & hospitality LQ ≥ 2.5 (tourism economies) with ≥ 5,000 residents in the tile |
| Plantation | perennial/tree-crop share of agricultural land ≥ 40%, Food ≥ 3, and ≥ 40% tree + crop cover |
| Farmland | Food ≥ 4 and ≥ 40% cropland (ESA WorldCover) |
| Fishery | sea tile with Food ≥ 6 (≥ 31.6 t of fish per km² of coastal sea) |
| Neighborhood | ≥ 1,500 people per km² of land |
| Preserve | ≥ 75% of the tile strictly protected (WDPA, IUCN categories Ia–IV; other designations do not count), or a national park with visitor statistics |

LQ = location quotient: the tile's share of employment in an industry divided by the national share.
"Gold ≥ 7" and the other yield conditions use the yields of §3; the top-1% thresholds are computed
over the country's land tiles on every build. The same rule text is shipped in `index.json` and shown
in the app's tooltips.

## 5. Wonders

Ranked from published figures; the rank is recomputed on every build. Each wonder cites the
datasets that produced its value and location (for example IMF PortWatch for a port, EIA-860 or
CEB / WRI for a power station, Wikidata sitelinks for a holy site), not every source of its domain.

| Wonder | Rule | Sri Lanka | US |
|---|---|---|---|
| Great University | most scholarly works in the last five complete years (OpenAlex) | top 5 | top 10 |
| Great Port | annual cargo volume | top 3 | top 10 |
| Great Power Station | installed capacity | top 5 | top 10 |
| Natural Wonder | annual visits to national parks (NPS recreation visits; SLTDA / DWC visitor counts) | top 5 | top 10 |
| Great Airport | annual passenger boardings (FAA enplanements: each boarding passenger counted once, about half of total passengers) | none: no open airport-level passenger statistics | top 10 |
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

A City View city is the metro's centre tile plus 3 rings of tiles around it. Where two rings
overlap (Colombo / Negombo, Los Angeles / Riverside) each shared tile belongs only to the nearer
centre (grid steps, then distance, then the larger official population), so no resident, job,
yield, district or wonder is counted in two cities; the city records how many of its ring tiles went
to which neighbour. Ring totals are therefore not official city or metro figures.

Each city also carries its **official population with its scope**, and the two countries' figures
are not the same kind of number: a US city is its Metropolitan Statistical Area (Census Bureau
Vintage 2025 estimate, OMB 2023 delineation counties), `population_scope` = *metro area (Census
MSA)*; a Sri Lankan city has only the population statement on its Wikidata item (P1082, various
years, usually the municipal / urban council area, sometimes the UN World Urbanization Prospects
projection), `population_scope` = *city proper (Wikidata)*. For a like-for-like official Sri Lankan
figure the city also carries the Census 2024 population of the DS division that holds its centre
(`census_ds_population`). Compare cities across countries on their tile rings, not on these figures.

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
* Soldiers: Sri Lanka's Labour Force Survey covers household residents only, so armed-forces members
  living in barracks are missing (about 41,000 household-resident soldiers vs the World Bank's ~317,000
  armed forces personnel), while the ACS counts the armed forces including group quarters. The
  Soldiers class is shown for each country but not compared between them.
* Airports: US values are FAA enplanements (boardings, about half of all passengers). Sri Lanka has no
  open airport-level passenger statistics, so its airports carry no traffic values and it has no Great
  Airport.
* `metrics.bin` stores tile values rounded to ~4 significant digits (relative error ≤ 6 × 10⁻⁵), the
  same precision as the admin tables; national totals use the unrounded pipeline values.
