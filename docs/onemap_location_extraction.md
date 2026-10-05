# OneMap location extraction

This adds geographic coordinates to every HDB block in the official HDB Property Information dataset and creates a separate amenities table from OneMap and data.gov.sg.

## Outputs

- `data/processed/hdb_blocks_with_coordinates.csv` — one coordinate result per unique HDB block and street, including match status and OneMap address.
- `data/processed/onemap_amenities_with_coordinates.csv` — malls, community clubs, public libraries, hawker centres, supermarkets, polyclinics, parks, and MRT/LRT stations, with latitude and longitude and source details.
- `data/processed/primary_schools_with_coordinates.csv` — current MOE primary schools, official addresses, postal codes, and OneMap coordinates.
- `data/processed/childcare_centres_with_coordinates.csv` — active ECDA childcare centres, official addresses, postal codes, and OneMap coordinates.
- `data/processed/healthcare_facilities_with_coordinates.csv` — hospitals, all 28 public polyclinics, and CHAS clinics with addresses and coordinates.
- `data/processed/onemap_amenity_category_summary.csv` — record and coordinate counts for every source query or dataset.
- `data/raw/onemap/` — raw OneMap theme/search responses, the current theme catalog, and the downloaded LTA station-exit GeoJSON.
- `data/interim/onemap_block_geocode_cache.jsonl` — resumable block geocoding cache. It contains results only, never the access token.
- `reports/phase_1_resale_model/02_onemap_extraction_manifest.json` — extraction time, HDB match-status counts, and amenity source themes.

## Run

From the project directory in PowerShell:

```powershell
.\scripts\phase_1_resale_model\02_run_onemap_extraction.ps1
```

The launcher securely prompts for the OneMap access token if `ONEMAP_TOKEN` is not already available in the current process. It does not store the token in any file.

The default rate is 240 requests per minute, below OneMap's documented 300 request per minute limit. A first full run covers roughly 13,000 HDB blocks and may take about one hour. If stopped, run the same command again; cached block results are reused.

## Matching rules

Each block is searched as `<block number> <street> SINGAPORE`. A match is classified as:

- `exact` when OneMap returns the same block number and normalized road name;
- `heuristic` when a coordinate is returned but only a partial match is available;
- `not_found` when no coordinate is returned.

Review all non-exact matches before using them in price models.

## Amenity source rules

OneMap's current theme catalogue does not contain themes for shopping malls, supermarkets, or operating MRT/LRT stations. The extractor therefore uses equivalent sources:

- Shopping malls: paginated OneMap Search API queries for `shopping mall` and `shopping centre`.
- Supermarkets: paginated searches for `supermarket`, `FairPrice`, `Sheng Siong`, `Cold Storage`, `Giant Supermarket`, `Prime Supermarket`, and `CS Fresh`. Results found by more than one query are deduplicated and retain all matching query names.
- MRT/LRT stations: the official LTA MRT Station Exit GeoJSON from data.gov.sg. This dataset also includes LRT exits. The output contains one row per station, with its coordinate calculated as the centroid of its exit coordinates.
- Primary schools: the current MOE General Information of Schools table, filtered to `mainlevel_code = PRIMARY`, then geocoded from its official postal code or address.
- Childcare centres: the current ECDA Listing of Centres table, filtered to `service_model = CC`, then geocoded from its official postal code or address.
- Polyclinics: the official HealthHub list of 28 polyclinics, searched individually by exact facility name instead of using the incomplete vaccination theme.
- Hospitals: the OneMap `moh_hospitals` theme.
- Other clinics: the MOH CHAS Clinics GeoJSON. This is the available bulk open-government clinic layer and does not represent every licensed healthcare provider.
- Other categories: matching OneMap thematic layers from the live catalogue.

Search results are discovery data rather than a guaranteed registry. Keep `source_type`, `source_name`, and `source_query` when modelling so each location remains auditable.

## Sources

- [OneMap Search API](https://www.onemap.gov.sg/apidocs/search)
- [OneMap Themes API](https://www.onemap.gov.sg/apidocs/themes)
- [LTA MRT Station Exit (GEOJSON)](https://data.gov.sg/datasets/d_b39d3a0871985372d7e1637193335da5/view)
- [MOE General information of schools](https://data.gov.sg/datasets/d_688b934f82c1059ed0a6993d2a829089/view)
- [ECDA Listing of Centres](https://data.gov.sg/datasets/d_696c994c50745b079b3684f0e90ffc53/view)
- [MOH CHAS Clinics](https://data.gov.sg/datasets/d_548c33ea2d99e29ec63a7cc9edcccedc/view)
- [HealthHub public healthcare institutions](https://support.healthhub.sg/hc/en-us/articles/59231470113689)
