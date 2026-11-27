# Methodology: from public statistics to a strategy-game map

This page documents every rule that turns official statistics into the game vocabulary the app uses.
All rules live in `pipeline/civmap/civ.py`; the app shows the same rule text in tooltips.
Nothing on the map is hand-placed: change a threshold in `civ.py`, re-run
`python -m civmap.run --stage civ export`, and the map follows.

## 1. Tiles

* **Grid.** Uber H3 hexagons. Sri Lanka at resolution 6: the exported tiles (land and sea rings
  alike) average **39.0 km²**, about 6.7 km across the flats and 7.7 km corner to corner; H3 cells
  this close to the equator are larger than the global res-6 average of 36.1 km². United States at
  resolution 5: **255.2 km²** on average (land tiles 255.7 km²; global res-5 average 252.9 km²),
  about 17.2 km across the flats and 19.8 km corner to corner. See `docs/PLAN.md` §1 for why these
  levels.
* **Land.** "Land" (`land_km2`, the denominator of population density and of a tile's land share)
  is the area inside the national boundary, *including* inland water (lakes, reservoirs, lagoons)
  but not the sea: the summed areas of the population-raster pixels whose centres fall inside the
  boundary. Sri Lanka totals 66,074 km² (official total area incl. inland water: 65,610 km², World
  Bank AG.SRF.TOTL.K2), the US 9.30 M km² (Census land area ≈ 9.15 M km²).
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
  * rates and shares (median income, % with a degree, religion shares): every land piece takes its
    unit's value; tiles average their pieces weighted by population (or land area for land-cover
    metrics). Sea tiles carry no admin-level rate, share or median (no data), except metrics
    allocated by coastal sea area; hex-level rasters (terrain, hazards, protected area) keep their
    sea values.
* **Facilities** (power stations, ports, airports, universities, heritage sites, museums…) are
  placed in the tile containing their coordinates, snapped to the nearest tile when they sit just
  offshore, and counted for the admin unit whose boundary contains the point (point-in-polygon on
  the finest level: DS division, county); a point outside every boundary (offshore) counts for its
  tile's dominant unit (largest land area). A state's or province's facility total is therefore the
  sum of the facilities inside it.

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
exchange rate of the data year. Tile, admin, city and Explorer values are not converted to
purchasing-power parity; price-adjusted comparisons appear only in the Advisors' national remarks,
which quote the World Bank's PPP series (GDP and GNI per capita, PPP; PA.NUS.PPP).

Sri Lanka has two agriculture value-added figures for 2024, and they are not the same series:
*Agriculture GDP* (`gdp_agri_usd`, economy domain) is the agriculture part of the GDP sector split
(DCS Statistical Abstract 2025 Table 10.2 vintage, Rs 2,481,487 mn; districts by LFS agricultural
employment) and is consistent with Gold and Production; *Agriculture, forestry & fishing GVA*
(`ag_gva_usd`, food domain) uses the revised quarterly national accounts (Rs 2,520,119 mn, +1.6 %)
spread over districts activity by activity (crop extents and output, livestock, fish landings) and
drives Food. Province values therefore differ (in `ag_gva_usd` Uva is 26 % higher, Sabaragamuwa
12 % higher and Northern 20 % lower). In the US `gdp_agri_usd` is the BEA county release for 2024
($269.7 B), `ag_gva_usd` the BEA state series for 2025 ($255.9 B) split by those county shares.

## 4. Districts

A tile receives every district whose rule it meets; the first in this priority order is shown.

| District | Rule |
|---|---|
| City Center | centre tile of a City View city: the most populous tile within one ring of the city's coordinates |
| Spaceport | a launch site that has hosted an orbital or orbital-class launch: Cape Canaveral (incl. Launch Complex 46), Kennedy, Vandenberg, Wallops / Mid-Atlantic Regional Spaceport, Pacific Spaceport Complex – Alaska, Starbase. Other sites on the FAA spaceport list (runway, suborbital or reentry-only) stay on the map as points but do not qualify |
| Harbor | seaports in the tile handle ≥ 1 million t/yr of cargo, or the tile holds one of the country's 3 busiest ports |
| Aerodrome | airports in the tile have ≥ 1 million passenger boardings/yr (US: FAA enplanements, about half of all arriving + departing passengers), or the country's busiest airport by boardings. Sri Lanka has no open airport-level passenger statistics, so there every OurAirports *large airport* with scheduled service qualifies |
| Campus | ≥ 2,000 research works in 5 years from institutions in the tile (OpenAlex), or STEM (Engineers class) workforce LQ ≥ 1.8 at ≥ 300 people per km² of land |
| Commercial Hub | land tile with finance + real estate + professional services employment LQ ≥ 1.15, and Gold ≥ 7 or GDP density in the country's top 1% of land tiles |
| Industrial Zone | Production ≥ 5 and manufacturing LQ ≥ 1.5, or an export-processing zone / industrial park |
| Dam | ≥ 100 MW hydroelectric capacity |
| Power Plant | ≥ 500 MW of other (non-hydro) generating capacity: thermal, nuclear, wind, solar… |
| Holy Site | holds one of the most widely documented religious sites (top 10 in Sri Lanka, top 25 in the US by Wikipedia language editions), or worship-site density in the country's top 1% of land tiles |
| Theater Square | a cultural or mixed World Heritage Site, ≥ 5 museums (Wikidata), or arts/recreation/hospitality LQ ≥ 2 at ≥ 300 people per km² of land |
| Encampment | a military installation in the tile |
| Entertainment Complex | arts, recreation & hospitality LQ ≥ 2.5 (tourism economies) with ≥ 5,000 residents in the tile |
| Plantation | **Sri Lanka:** ≥ 15% of the tile's land under tea, rubber or coconut, ≥ 40% tree + crop cover, < 25% built-up, < 75% strictly protected and no national park. The crop extents are the district figures of the Economic Census 2013/14 (DCS Abstract 2025 Table 5.2; rubber EC Table 6.3.1), allocated to tiles by tree-cover area inside the district (app metric *Tea, rubber & coconut share of land*); because that allocation also reaches natural forest, strictly protected forest and national-park tiles are excluded. With these thresholds 588 of 1,861 land tiles qualify (546 show it as their main district), holding 84% of the tea, 89% of the rubber and 75% of the coconut extent; a single district-wide share cannot separate Badulla's upland tea from its dry lowland forest, so some Uva tea tiles (Haputale 14.7%) fall just below the threshold. **US:** fruit & nut orchards, citrus and vineyards ≥ 40% of the county's cropland (2022 Census of Agriculture, `tree_crop_share`), Food ≥ 3 and ≥ 40% tree + crop cover |
| Farmland | land tile with Food ≥ 4 and ≥ 40% cropland (ESA WorldCover) |
| Fishery | sea tile with Food ≥ 6 (≥ 31.6 t of fish landed per km² of tile; landings are allocated to the ring of coastal sea tiles) |
| Neighborhood | land tile with ≥ 1,500 people per km² of land (also when the tile has other districts) |
| Preserve | land tile ≥ 75% strictly protected (WDPA, IUCN categories Ia–IV; other designations do not count), or holding a national park with visitor statistics |

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
groups) and B23025 (armed forces). Sri Lanka: Labour Force Survey 2024 employment by ISCO-08 major
group (Table 14), cut into 2-digit sub-major groups with the national 2-digit shares of LFS 2024
(ILOSTAT); elementary workers (group 9) employed in agriculture are counted as ISCO 92, and the
other group-9 workers are split with the national shares of 91–96 (the 92 part again counting as
Farmers). The mapping is the one in
`pipeline/civmap/domains/labor.py` (`US_OCC`, `ISCO2_CLASS`) and in each class's metric method.

| Class | US SOC groups | ISCO-08 sub-major groups |
|---|---|---|
| Farmers | 45 | 61–63 (major group 6) + 92 agricultural, forestry & fishery labourers |
| Workers | 47, 49, 51, 53 | 71–75, 81–83 (groups 7, 8) + 93 labourers in mining, construction, manufacturing & transport |
| Merchants | 41, 43 | 41–44 (group 4) + 52 sales workers + 95 street & related sales and service workers |
| Engineers | 15, 17, 19 | 21, 25, 31, 35 (science, engineering & ICT professionals and technicians) |
| Scholars | 21, 23, 25 | 23, 26 (teaching; legal, social & cultural professionals, incl. creative artists 264–265) |
| Healers | 29, 31 | 22, 32, 53 (health professionals and associates, personal care workers) |
| Service | 33, 35, 37, 39 | 51, 54, 91, 94, 96 |
| Administrators | 11, 13 | 11–14 (group 1), 24, 33 |
| Artists | 27 | 34 (legal, social, cultural & related associate professionals; a proxy) |
| Soldiers | armed forces (B23025) | 01–03 (group 0) |

Farmers include ISCO 92 so that they cover the same work as US SOC 45 (which includes farmworkers
and labourers): Sri Lanka's Farmers (2.10 M in 2024) are therefore ISCO group 6 (1.21 M, LFS 2024
Table 7.3) plus about 0.89 M agricultural labourers.

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
* Rates, shares and medians for admin units *above* the level they are published at are
  population-weighted (land-cover metrics: area-weighted) averages of the published lower-level
  values, not the official figure for that unit. A state's median household income is the
  population-weighted mean of its county medians (California $101,900 vs the ACS state median of
  $99,122), a state's unemployment rate the weighted mean of county rates (South Dakota 3.17 % vs
  2.93 %), and a Sri Lankan district's rate the weighted mean of the units it was published for or
  modelled at (Ampara unemployment 4.87 % vs LFS 2024 4.54 %). Additive quantities (people, jobs,
  GDP, tonnes, facilities) are exact sums at every level, and citizen shares and GDP per capita are
  recomputed from those sums.
* `land_km2` includes inland water (see §1), so "per km² of land" densities are slightly lower than
  per km² of dry land.
* Soldiers: Sri Lanka's Labour Force Survey covers household residents only, so armed-forces members
  living in barracks are missing (about 41,000 household-resident soldiers vs the World Bank's ~317,000
  armed forces personnel), while the ACS counts the armed forces including group quarters. The
  Soldiers class is shown for each country but not compared between them.
* Airports: US values are FAA enplanements (boardings, about half of all passengers). Sri Lanka has no
  open airport-level passenger statistics, so its airports carry no traffic values and it has no Great
  Airport.
* `metrics.bin` stores tile values rounded to ~4 significant digits (relative error ≤ 6 × 10⁻⁵), the
  same precision as the admin tables; national totals use the unrounded pipeline values.
