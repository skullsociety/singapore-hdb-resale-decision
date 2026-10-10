# Singapore HDB Resale Price Research

A local, reproducible data science project for exploring Singapore HDB resale transactions, comparable sales, and rough resale-price estimates. The current model release covers 26 towns; the current listing snapshot covers eight towns. Estimates are research outputs, not official HDB or professional valuations.

For the complete end-to-end flow chart, calculations, models, and data stores, see [How the data and calculations flow](docs/end_to_end_data_flow.md).

## What it does

The workflow:

1. Downloads HDB resale transaction and HDB property datasets from data.gov.sg.
2. Downloads supporting school, childcare, healthcare, and MRT/LRT exit datasets from data.gov.sg, and uses OneMap to geocode HDB blocks and discover amenities.
3. Builds a persistent DuckDB database and transaction-level feature table.
4. Creates comparable-sales and recent-median baselines, profiles feature/price associations, and trains resale-price models.
5. Evaluates estimates on time-ordered data, reports weak-performing segments, calibrates price ranges, and attaches explanations and comparable-sale evidence.
6. Plans a buyer's resale purchase cash, CPF, stamp duties, mortgage payment, and offer scenarios from user supplied assumptions.
7. Estimates a seller's cash proceeds and CPF refund under three sale prices, with an optional next-home comparison.

The project has a local Streamlit dashboard with buyer and seller planning, plus a future-price scenario explorer. The Chrome extension collects Singapore HDB search cards when started in regular Chrome. There is no hosted public website, complete live listings feed, or automatic scheduler.

## Current status and next phase

Phases 1–7 now support nationwide HDB inputs. The next operational step is to run and validate the full national rebuild, followed by **multi-source data operations**:

1. Use the local Control Center to run collection, processing and dashboard workflows.
2. Define one source-independent listing contract and collection-run registry.
3. Automate official APIs, licensed feeds, or permitted direct HTTP sources where available.
4. Add portal-specific adapters for additional property sites without changing downstream tables.
5. Keep the Chrome extension as a fallback for sites that require a normal browser session.
6. Monitor transaction, block, amenity, comparable-sale and model coverage by town.

Automation does not mean bypassing access challenges. Each source must have a documented access method, rate limit, terms/permission record, parser test and coverage check. A source that cannot be collected reliably should fail independently without blocking the official-data refresh or erasing the last valid snapshot.

## Requirements

- Windows with PowerShell.
- Python 3 available on `PATH` for the extraction launchers. The scripts in steps 01 and 02 use the Python standard library.
- Internet access to data.gov.sg and OneMap.

The DuckDB build launcher creates a project-local `.venv` and installs the pinned packages from `requirements.txt` if they are not installed. Model training also uses this environment. CatBoost is optional at runtime: if it cannot load in your environment, the workflow warns and uses its scikit-learn alternative where applicable.

## Quick start

Open PowerShell in the project root (`PropertyProject`) and run the steps in order:

```powershell
# 1. Download HDB transaction and property data from data.gov.sg
.\scripts\phase_1_resale_model\01_run_extraction.ps1

# 2. Geocode HDB blocks and extract amenities / facility locations
.\scripts\phase_1_resale_model\02_run_onemap_extraction.ps1

# 3. Build DuckDB and model-ready features
.\scripts\phase_1_resale_model\03_run_duckdb_build.ps1

# 4. Build and evaluate transaction baselines
.\scripts\phase_1_resale_model\04_run_baselines.ps1

# 5. Analyse feature associations with resale price
.\scripts\phase_1_resale_model\05_run_feature_price_analysis.ps1

# 6. Train and compare resale-price models
.\scripts\phase_1_resale_model\06_run_resale_models.ps1

# 7. Review reliability, calibrated ranges, and estimate explanations
.\scripts\phase_1_resale_model\07_run_phase1_diagnostics.ps1

# 9. Calibrate the validation-selected model and review its backtest
.\scripts\phase_1_resale_model\09_run_blend_calibration.ps1

# 10. Estimate a single flat using the currently released model
.\scripts\phase_1_resale_model\10_run_flat_valuation.ps1 -Block '106' -Street 'RIVERVALE WALK' -FlatType '4 ROOM' -FloorAreaSqm 100 -StoreyRange '01 TO 03' -FlatModel 'Model A' -RemainingLeaseYears 71.5

# 11–14. Use the Buy and sell planning page in the dashboard after starting it in Step 24.

# 15. Start the local receiver for the Singapore HDB Chrome extension
.\scripts\phase_4_market_watch\15_run_propertyguru_browser.ps1
```

Step 4 compares the original comparable-sales median with a second method that gives recent sales more weight (a six-month half-life). Both first seek earlier flats whose lease commencement is within three years of the target's; a labelled wider-lease fallback preserves coverage when too few matches exist. The method with lower validation MAE supplies the starting price for Step 6. Step 6 trains each of the six resale models to estimate the adjustment from that price. Training sales without earlier comparables are excluded and counted in the Step 6 report. Step 6 selects the model with the lowest MAE in the latest half of the validation period. After changing the model code or source data, rerun Steps 4, 6–7 and 9 before using Step 10 or listing valuations. Step 9 uses that ML model only if it beats the selected comparable-sales method on both validation and test sales; otherwise it uses the comparable baseline and reports the reason.

The first data extraction step skips downloading a raw HDB file if that file already exists. To get fresh HDB copies, run:

```powershell
.\scripts\phase_1_resale_model\01_run_extraction.ps1 -ForceDownload
```

## Script guide

This table covers the active scripts under `scripts/`, the local form scripts under `web/`, and the dashboard code under `dashboard/`. Update it whenever a script is added, renamed, or removed. The `.ps1` files are PowerShell launchers; the `.py` and `.js` files do the data processing or browser work.

The numbered scripts, reports, and model files sit in matching `phase_1_resale_model`, `phase_2_buyer_planner`, `phase_3_seller_planner`, and `phase_4_market_watch` subfolders where applicable. Their numbered filenames are unchanged. The installed Chrome extension remains at `scripts/17_propertyguru_chrome_extension/` so its existing Chrome installation keeps working.

| Script | Purpose |
| --- | --- |
| `scripts/phase_1_resale_model/01_run_extraction.ps1` | Starts the data.gov.sg download. |
| `scripts/phase_1_resale_model/01_extract_data_gov_sg.py` | Downloads HDB and supporting government datasets. |
| `scripts/phase_1_resale_model/02_run_onemap_extraction.ps1` | Starts OneMap extraction and asks for the access token. |
| `scripts/phase_1_resale_model/02_extract_onemap_locations.py` | Geocodes HDB blocks (including an Everton Park alternate spelling) and collects amenity locations. |
| `scripts/phase_1_resale_model/03_run_duckdb_build.ps1` | Sets up Python packages and runs the database build. |
| `scripts/phase_1_resale_model/03_build_duckdb.py` | Builds the research DuckDB tables and model features, then reports all unusable source rows together. |
| `scripts/phase_1_resale_model/04_run_baselines.ps1` | Runs baseline price evaluation. |
| `scripts/phase_1_resale_model/04_build_baselines.py` | Builds ordinary and recency-weighted comparable baselines, first matching lease commencement within three years and labelling any wider-lease fallback; selects by validation MAE. |
| `scripts/phase_1_resale_model/04_test_baselines.py` | Checks lease-matched comparable selection and the labelled fallback for sparse groups. |
| `scripts/phase_1_resale_model/05_run_feature_price_analysis.ps1` | Runs the feature/price analysis. |
| `scripts/phase_1_resale_model/05_analyse_feature_price_correlations.py` | Reports feature associations with resale price, reusing price calculations and compact column data to reduce runtime and memory use. |
| `scripts/phase_1_resale_model/06_run_resale_models.ps1` | Checks required packages and starts model training and comparison; `-ReselectOnly` reapplies the selection rule to saved model results without retraining. |
| `scripts/phase_1_resale_model/06_train_resale_models.py` | Trains six models on sales from January 2023 onward to adjust an earlier comparable-sales price; older sales remain available for comparable history. Selects by recent validation MAE and saves the artifacts; includes an 80-tree squared-error random forest, quantile boosting with early stopping, progress updates, and 5-worker limits. |
| `scripts/experiments/covid_training_window/compare_training_windows.py` | Compares 2017-, 2020-, and 2023-onward training for the CatBoost candidate on identical later sales; the result informed the current training window. |
| `scripts/phase_1_resale_model/07_run_phase1_diagnostics.ps1` | Runs reliability and explanation checks. |
| `scripts/phase_1_resale_model/07_phase1_diagnostics.py` | Examines errors and ranges; appends detailed town, flat-type, lease, comparable, predicted-price, and month breakdowns to the exit review. Provides Ridge contributions when Ridge wins and marks individual explanations unavailable for nonlinear models. |
| `scripts/phase_1_resale_model/09_run_blend_calibration.ps1` | Runs calibration and backtesting for the selected model. |
| `scripts/phase_1_resale_model/09_calibrate_accepted_blend.py` | Chooses ML or the comparable method, calibrates the price range, and checks a wider range for town/flat-type groups with fewer than 100 recent training sales. |
| `scripts/phase_1_resale_model/10_run_flat_valuation.ps1` | Starts a valuation for one flat. |
| `scripts/phase_1_resale_model/10_predict_flat.py` | Prices one flat with the released model, comparable sales, and a limited-local-evidence warning and wider range where applicable. |
| `scripts/phase_2_buyer_planner/11_buyer_cost_planner.py` | Calculates buyer cash, CPF, duties, and loan scenarios. |
| `scripts/phase_2_buyer_planner/11_test_buyer_cost_planner.py` | Tests the buyer calculations. |
| `scripts/phase_3_seller_planner/13_seller_proceeds_planner.py` | Calculates sale proceeds, CPF refunds, and shortfalls. |
| `scripts/phase_3_seller_planner/13_test_seller_proceeds_planner.py` | Tests the seller calculations. |
| `scripts/phase_4_market_watch/15_open_chrome_with_collector.ps1` | Starts the listing receiver if needed, then opens Chrome. |
| `scripts/phase_4_market_watch/15_run_propertyguru_browser.ps1` | Starts only the listing receiver. |
| `scripts/phase_4_market_watch/15_receive_propertyguru_browser.py` | Validates extension data and saves listings to DuckDB. |
| `scripts/phase_4_market_watch/15_propertyguru_cards.py` | Parses PropertyGuru listing cards into fields. |
| `scripts/phase_4_market_watch/15_audit_listing_data.py` | Checks listing rows and optionally fixes supported address and title errors. |
| `scripts/phase_4_market_watch/15_test_propertyguru_browser.py` | Tests listing storage, deduplication, and request checks. |
| `scripts/phase_4_market_watch/15_test_listing_audit.py` | Tests audit preview, safe repair, and repeat runs. |
| `scripts/17_propertyguru_chrome_extension/sites.js` | Reads supported listing pages; its folder name is kept for Chrome compatibility. |
| `scripts/17_propertyguru_chrome_extension/collector.js` | Collects each results page and advances through the search. |
| `scripts/17_propertyguru_chrome_extension/background.js` | Sends collected cards to the local receiver. |
| `scripts/17_propertyguru_chrome_extension/popup.js` | Shows progress and provides Start/Stop controls. |
| `scripts/17_propertyguru_chrome_extension/15_test_listing_sites.js` | Tests listing-page recognition and parsing. |
| `scripts/17_propertyguru_chrome_extension/15_test_collector_recovery.js` | Tests collection recovery after a reload. |
| `scripts/phase_4_market_watch/16_import_legacy_propertyguru.py` | One-time import of an older saved run into DuckDB. |
| `scripts/phase_4_market_watch/17_19_build_listing_analysis.py` | Performs Steps 17–19: matches listings to blocks, adds features, and estimates prices with a wider calibrated range for sparse local training groups; shares transaction lookups and identical valuation scenarios, predicts in batches, and reuses unchanged same-month valuations. |
| `scripts/phase_4_market_watch/17_19_test_listing_analysis.py` | Tests address normalization, block matching, flat-type inference, and storey scenarios used by Steps 17–19. |
| `scripts/phase_4_market_watch/20_22_build_market_watch.py` | Performs Steps 20–22: saves a search profile, ranks current listings, tracks snapshots, and creates the market-watch report. |
| `scripts/phase_4_market_watch/20_22_test_market_watch.py` | Tests preference validation, deal-ranking rules, and price-change tracking. |
| `scripts/phase_5_stakeholder_dashboard/23_build_dashboard_views.py` | Builds the dashboard's stable DuckDB views and includes performance metrics for the model in the current release. |
| `scripts/phase_5_stakeholder_dashboard/23_test_dashboard_views.py` | Tests view availability, row identity, and category safeguards. |
| `scripts/phase_5_stakeholder_dashboard/24_run_dashboard.ps1` | Starts the local Streamlit dashboard on this computer. |
| `scripts/phase_5_stakeholder_dashboard/24_test_dashboard.py` | Opens the dashboard in Streamlit's test runner and checks for startup errors. |
| `dashboard/phase_5/24_streamlit_app.py` | Provides the interactive overview, listing, change, quality, and report pages. |
| `dashboard/phase_5/24_decision_planner.py` | Provides the combined buyer and seller planning page and passes inputs to the existing calculation modules. |
| `dashboard/phase_5/25_agent_report.py` | Creates the self-contained printable listing comparison report in memory. |
| `scripts/phase_5_stakeholder_dashboard/25_test_agent_report.py` | Tests report safety, selection limits, and planning-file input. |
| `scripts/phase_6_neighbourhood_explorer/26_run_feature_explorer_build.ps1` | Starts the neighbourhood and feature-explorer data build. |
| `scripts/phase_6_neighbourhood_explorer/26_build_feature_explorer.py` | Copies a focused transaction snapshot and feature associations into the dashboard database. |
| `scripts/phase_6_neighbourhood_explorer/26_test_feature_explorer.py` | Tests explorer views, transaction integrity, association scope, and block summaries. |
| `dashboard/phase_6/27_feature_explorer.py` | Renders transaction trends, a distribution, a map, and feature relationships. |
| `scripts/phase_6_neighbourhood_explorer/27_test_feature_explorer_dashboard.py` | Tests that the feature-explorer dashboard page opens successfully. |
| `scripts/phase_7_future_scenarios/28_run_future_scenario_build.ps1` | Starts the historical evidence and scenario backtest build. |
| `scripts/phase_7_future_scenarios/28_build_future_scenario_evidence.py` | Estimates the observed lease association and backtests one-, three-, and five-year ranges using matched repeat sales. |
| `scripts/phase_7_future_scenarios/28_test_future_scenario_evidence.py` | Tests the Phase 7 evidence views, backtest values, scenario formula, and loan calculation. |
| `dashboard/phase_7/29_future_scenario_explorer.py` | Renders editable future-price scenarios, uncertainty bands, financing sensitivity, and historical backtests. |
| `scripts/phase_7_future_scenarios/29_test_future_scenario_dashboard.py` | Tests that the future-price scenario dashboard page opens successfully. |
| `scripts/phase_8_desktop_app/30_run_control_center.ps1` | Opens the Windows Control Center without a console window. |
| `scripts/phase_8_desktop_app/30_control_center.py` | Provides scrollable desktop workflow controls, an activity log, and a button to open the current model diagnostics review. |
| `scripts/phase_8_desktop_app/30_build_control_center_exe.ps1` | Optionally packages the Control Center as a Windows executable with PyInstaller. |
| `scripts/phase_8_desktop_app/30_test_control_center.py` | Tests workflow definitions, command order and OneMap-token requirements. |

Step 02 reuses cached HDB block geocoding results, but refreshes its amenity and supporting source extracts when rerun. It retries block 1 Everton Park using the expanded spelling `EVERTON PARK`. Blocks still unmatched remain in the data with blank coordinates and blank location and amenity-distance features. The model pipeline handles these missing values without treating them as zero distance. Step 03 collects row-level address-join and invalid-value issues and reports them together at the end. Unsupported property and transaction rows stay in the downloaded source files but are left out of the research tables. Re-run steps 03–09 after refreshed source data so the database, features, baselines, selected model, and calibrated range are rebuilt. Step 10 prices one flat and can be rerun whenever needed.

## Buy and sell planning (steps 11–14)

Choose **Buy and sell planning** in the Streamlit dashboard. The buyer tab calculates cash, CPF, duties, loan payments, offer scenarios and an affordability screen from the assumptions you enter. The seller tab calculates cash proceeds, CPF refund, shortfalls and three sale-price scenarios. If you calculate a buyer plan first, the seller tab can use its asking-price cash and CPF needs for the next-home comparison.

The calculation runs locally and does not save inputs. Enter the applicable ABSD rate explicitly, including `0` only if appropriate. Use an HDB Request for Value result when available; otherwise mark the value as an assumption. The research price estimate is not HDB's official value. Use current lender, CPF and HDB statements for loan, CPF refund and sale-cost figures. The estimate is for a **whole-flat sale**, not a sale of only one owner's share.

## Singapore HDB listings: Chrome extension (step 15)

Step 15 uses a Chrome extension in your regular browser and a local receiver. PropertyGuru Singapore HDB results are the first supported source. Each site has its own page-reading adapter in `scripts/17_propertyguru_chrome_extension/sites.js`; the collection flow and database table are shared. The extension folder retains its original name because Chrome has already loaded it from that path. A future site needs an adapter, a narrow page match in the extension manifest, and a source parser/registration in the receiver. Adding an adapter does not grant access to a site or guarantee its pages are readable.

The numbered files for this step have separate jobs: `15_open_chrome_with_collector.ps1` opens Chrome and starts the receiver; `15_run_propertyguru_browser.ps1` starts only the receiver; `15_receive_propertyguru_browser.py` validates and saves cards; `15_propertyguru_cards.py` parses PropertyGuru cards. The `15_test_*.py` and extension `15_test_*.js` files check those parts. Step 16, `16_import_legacy_propertyguru.py`, was used once to move an older 38-page JSON run into DuckDB; it is not part of normal collection.

Each time you want a snapshot, start the receiver, open a supported HDB results tab, and click **Start from page 1**. The extension accepts both PropertyGuru's `/hdb-for-sale` pages and `/property-for-sale` pages with `propertyTypeGroup=H`. On the newer results pages it keeps the selected search filters while returning to page 1 and moving between pages. The adapter reads the result count and final page number on page 1, saves visible HDB cards, waits a random 3–4 seconds, and opens the next numbered results URL in the same tab. The collector stops if cards disappear, a page does not advance, or the local receiver is unavailable. It does not solve access challenges or read cookies, other tabs, photos, or contact details.

If the popup remains on a saved page, first check that the receiver window is still running. The popup now flags an offline receiver. Reloading a tab or extension no longer loses the scheduled next page in a current run; an older run without recovery state stops and asks you to start again from page 1.

1. Open Chrome through the **Chrome + Property Collector** desktop shortcut. It starts the local receiver if needed, then opens your normal Chrome profile. You can also start the receiver manually: in PowerShell, run `cd "C:\Users\temas\Documents\PropertyProject"` then `.\scripts\phase_4_market_watch\15_run_propertyguru_browser.ps1` and leave that window open. The receiver listens on `127.0.0.1:8771` only.
2. In Chrome, visit `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `C:\Users\temas\Documents\PropertyProject\scripts\17_propertyguru_chrome_extension`. If already installed, click **Reload** on the extension after updating its files. After a receiver code update, stop any receiver that is still running and launch Step 5 again; an old receiver process cannot accept the newer HDB results URL. The popup now identifies that version mismatch. Current access is limited to the local receiver and PropertyGuru HDB results pages.
3. Open a PropertyGuru HDB results page in Chrome, including a filtered `/property-for-sale?propertyTypeGroup=H` search, click the extension icon, then **Start from page 1**. Keep Chrome running until the extension says Finished or Stopped. The shortcut-launched receiver stays running after Chrome closes; opening Chrome through a different shortcut will not start it. Disconnect DBeaver from `listings.db` before collecting, since its open connection can lock the database file.

The receiver creates `data/listings.db` (a **DuckDB** file) on the first successful page. It contains one `listings` table for every supported site. `source_site` identifies the website; `(source_site, run_id, listing_id)` is the unique key. Repeated cards on different pages of one run merge into one row, while new runs remain available as separate snapshots. Each row stores price, address, size, other parsed card fields, the original card JSON, pages seen, and collection times. The receiver reports saved and missing pages to the extension after every page. It no longer creates listing CSV, per-page JSON, or a listing manifest file. Older listing files, if present, are historical and are not imported automatically. This database is separate from `data/property.duckdb`, which the resale-model build replaces.

To check listing quality, first disconnect `listings.db` in DBeaver and preview the audit with `.\.venv\Scripts\python.exe scripts\phase_4_market_watch\15_audit_listing_data.py`. Review the row counts and sample changes. Then run the same command with `--apply` to correct numeric-only titles and address parts supported by the saved card heading. The audit also reports unresolved addresses, invalid prices or areas, and listing URL/ID mismatches for manual review. It preserves the original `raw_card_json` and does not guess missing fields. Run the preview again after applying; `rows_to_fix` should be zero for the safe corrections.

To see row counts by website after a run:

```powershell
.\.venv\Scripts\python.exe -c "import duckdb; print(duckdb.connect('data/listings.db', read_only=True).execute('SELECT source_site, COUNT(*) FROM listings GROUP BY source_site').fetchall())"
```

A result is complete only when every advertised search page has been saved. Listings can change during a run, so also check the extension's difference between its listing count and the displayed total. The first 38-page live run yielded 753 distinct listings and was imported from the older JSON format into `listings.db`.

## Listing analysis (steps 17–19)

Run these steps after a complete listing snapshot and after the Phase 1 database and accepted model have been built:

```powershell
.\.venv\Scripts\python.exe scripts\phase_4_market_watch\17_19_build_listing_analysis.py
```

The repeatable build adds three tables to the existing `data/listings.db`; it does not create another database:

| Step | Table | Purpose |
| --- | --- | --- |
| 17 | `listing_block_matches` | Links every listing in the latest complete run to an HDB block, with method, confidence and review notes. |
| 18 | `listing_features` | Adds block details, coordinates, nearby amenities and an inferred flat type. |
| 19 | `listing_valuations` | Applies the released model and stores a price range, asking-price difference, confidence, scenarios and supporting comparable count. |

PropertyGuru result cards do not reliably contain the exact flat model, lease commencement year or floor range. The build therefore infers flat type from floor area and evaluates storey scenarios observed in historical transactions for the matched block. A matched block receives its town from the official HDB contract-town code even when it has no registered resale history, so town filtering does not depend on price evidence. These assumptions are recorded in the database. A listing is marked `not_valued` when the available evidence is insufficient; the build does not invent a price. The latest local snapshot covers Ang Mo Kio, Bishan, Hougang, Punggol, Sengkang, Serangoon, Tampines and Yishun: 4,874 listings, 4,711 block matches, 4,383 estimates and 491 withheld estimates. The machine-readable run summary is `reports/phase_4_market_watch/19_listing_analysis_summary.json`.

The October 2026 local-evidence safeguard counts real model-training sales for the same town and flat type from January 2023 through the September 2024 training cutoff. Below 100, it keeps the released price model, flags limited local evidence, and uses a separate, wider interval calibrated on validation sales. This does **not** substitute fabricated sales or switch to an older model. The wider range covered 98.47% of later validation sales and 94.92% of test sales in the sparse group (729 test sales), against a nominal 97.5%; it is still a research range, not a promise of coverage for one town. Step 09 reads the model's training start month when calculating the group count. Rebuild Steps 09, 17–19, 20–22 and 23 after a future model refresh to carry the current safeguard into the dashboard database. In the current eight-town snapshot, 76 estimates use the limited-local-training-data label.

To inspect the estimates in DBeaver, refresh the database and open `listing_valuations`, or run:

```sql
SELECT *
FROM listing_valuations
ORDER BY asking_premium_discount_pct;
```

## Market watch (steps 20–22)

Run this after Steps 17–19:

```powershell
.\.venv\Scripts\python.exe scripts\phase_4_market_watch\20_22_build_market_watch.py
```

The first run uses a broad Singapore HDB profile: it has no budget, size or flat-type limit, requires a Step 19 estimate, needs at least three comparable sales, and treats a listing snapshot older than 14 days as stale. Its purpose is to make the complete current shortlist visible before you set your personal limits.

To use your own preferences, copy `data/reference/20_market_watch_preferences.example.json` to an untracked personal file such as `reports/phase_4_market_watch/20_my_preferences.json`, edit the values, then run:

```powershell
.\.venv\Scripts\python.exe scripts\phase_4_market_watch\20_22_build_market_watch.py --preferences reports\phase_4_market_watch\20_my_preferences.json
```

The build adds four tables to the same `data/listings.db` file:

| Step | Table | Purpose |
| --- | --- | --- |
| 20 | `market_watch_preferences` | Stores each saved profile and its filters. |
| 20 | `market_watch_rankings` | Scores listings on value, suitability, evidence and snapshot freshness. |
| 21 | `listing_snapshot_history` | Stores each listing's price and status in every collection snapshot. |
| 21 | `listing_snapshot_runs` | Summarises new, reduced-price, increased-price and disappeared listings per snapshot. |

Step 22 writes a viewable report at `reports/phase_4_market_watch/22_market_watch.html` and a machine-readable receipt at `reports/phase_4_market_watch/22_market_watch_summary.json`. Open the HTML file in a browser. The report uses **investigate** when a fresh listing meets the profile and its asking price is at or below the research point estimate; it always shows the estimated range and comparable count. It is a shortlist for further checks, not an official valuation.

Price-change tracking becomes useful after at least two complete collection runs. The first snapshot labels every listing as `new`, because there is no earlier snapshot for comparison.

## Stakeholder dashboard and local report (steps 23–25)

Step 23 adds a presentation layer to the existing `data/listings.db`; it does not create another database. In DuckDB, a **table** stores rows. A **view** stores a named SQL query. When the dashboard reads a view, DuckDB runs that query against the current tables and returns rows that look like a table. This lets the dashboard use clear, stable column names without copying the listings into another set of tables.

Step 23 creates these seven views:

| View | What the dashboard receives |
| --- | --- |
| `dashboard_market_overview` | Summary counts by snapshot, town, flat type, and stakeholder category. |
| `dashboard_current_listings` | One filterable row per current listing. |
| `dashboard_listing_details` | One enriched listing with estimate, assumptions, amenities, and quality flags. |
| `dashboard_listing_changes` | Listing observations used for new, changed, and disappeared listings. |
| `dashboard_comparables` | Historical transactions supporting each estimate. |
| `dashboard_data_quality` | Coverage and integrity checks for the current build. |
| `dashboard_model_performance` | Historical model metrics shown with their evaluation split. |

It also creates two small physical tables. `dashboard_category_rules` records the configurable category thresholds. `dashboard_model_metrics` copies the approved evaluation figures from `property.duckdb`, allowing the dashboard to open only `listings.db`. The views remain the public contract for the dashboard; internal collection and model tables can change without forcing every chart to be rewritten.

Disconnect `listings.db` in DBeaver before rebuilding Step 23 because the build needs write access. From the project root, run:

```powershell
.\.venv\Scripts\python.exe scripts\phase_5_stakeholder_dashboard\23_build_dashboard_views.py
.\scripts\phase_5_stakeholder_dashboard\24_run_dashboard.ps1
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501) if the browser does not open automatically. Keep the PowerShell window open while using the dashboard and press **Ctrl+C** there to stop it. The local app binds to `127.0.0.1`, so another computer cannot open it.

Step 24 uses **Streamlit**, a Python web-interface framework. Each filter or selection reruns the page code, which sends read-only SQL queries to the dashboard views and draws metrics, tables, charts, and a map in the browser. DuckDB remains an embedded file database: Streamlit loads the DuckDB Python package and opens `data/listings.db` directly. There is no separate database server to install.

Step 25 is part of the Streamlit **Comparison report** page. A user selects up to four listings, may use the current dashboard buyer or seller plan or attach an earlier planning JSON, add a neutral report reference, and add comparison notes. The app creates a self-contained HTML report in memory. Nothing is saved by the project until the user clicks **Download printable report**; the downloaded file can be opened in a browser and printed or saved as PDF. This supports the longer-term goal of software that an end user can operate locally without DBeaver or command-line SQL.

The local build currently presents 4,874 listings, 58,405 supporting comparable rows, seven data-quality checks, and 18 model-evaluation rows. Forty-five asking prices are below their research range and labelled **To investigate low price**; 2,265 are within the range but at or below the point estimate and labelled **Asking below estimate**. The remaining categories are 1,834 fairly priced, 154 slightly expensive, 85 likely expensive, and 491 with insufficient evidence. These are asking-price comparisons, not assessments of unit condition, property history, or official valuations. An incident must not be inferred from a low asking price. Listing details also flag a large gap between the point estimate and comparable-sale price, and advertisements with the same block, area and asking price; the latter may still be different flats.

### Later online access

GitHub can store the application code, but it does not run Streamlit. A later free pilot can deploy the repository through Streamlit Community Cloud. The app accepts `PROPERTY_DASHBOARD_DB` as the path to an approved deployment database. The working DuckDB files, raw listing snapshots, personal plans, and secrets are excluded from Git, so online deployment still needs a small demonstration database whose redistribution is permitted, or a private hosted data service. Do not publish the current working database merely to make the app run online. A shared online app also needs a refresh design and appropriate access controls; those are outside Steps 23–25.

## Neighbourhood and feature explorer (steps 26–27)

Step 26 prepares a dashboard-ready copy of the historical transaction features already built in Phase 1. It adds the following objects to the existing `data/listings.db` file:

| Object | Type | Purpose |
| --- | --- | --- |
| `explorer_transactions` | Table | Focused snapshot of 19,513 registered Sengkang transactions with price, flat, lease, location, and amenity fields. |
| `explorer_feature_associations` | Table | Training-only feature/price association results reused from Step 05. |
| `explorer_build_metadata` | Table | Build time, coverage dates, and row, block, town, and flat-type counts. |
| `dashboard_explorer_transactions` | View | Transaction-level contract used by the interactive filters and charts. |
| `dashboard_explorer_monthly_trends` | View | Monthly transaction-count and median-price summaries. |
| `dashboard_explorer_blocks` | View | Block and flat-type summaries for maps and neighbourhood comparisons. |
| `dashboard_feature_associations` | View | User-facing association results with identifiers and evaluation metadata removed. |

Disconnect `listings.db` in DBeaver before running the write step:

```powershell
.\scripts\phase_6_neighbourhood_explorer\26_run_feature_explorer_build.ps1
.\scripts\phase_5_stakeholder_dashboard\24_run_dashboard.ps1
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501) and choose **Neighbourhood and features**. Step 27 lets the user filter by flat type, transaction years, feature, and a recent chart/map period through one Apply form. Summary metrics, trends, distributions, feature-band averages and price-per-square-metre comparisons are calculated over all matching transactions in DuckDB. To keep the browser responsive, the relationship chart displays a deterministic sample of at most 8,000 transactions, the map displays at most the 3,000 most active matching blocks, and the transaction table displays the latest 500 rows. Query results are cached for 60 seconds with a bounded cache.

The current explorer covers 26 towns from January 2017 through September 2026: 241,354 transactions across 9,752 transaction-bearing blocks and seven flat types. Its 42 displayed feature relationships were calculated from 190,799 training rows only. Validation and test prices were excluded from feature screening.

The explorer deliberately describes **associations**. A positive or negative correlation does not prove that a school, station, park, lease, floor, or another feature caused the price difference. Flat type, time, location, and overlapping characteristics can produce the observed relationship. Current amenity locations may also differ from those available on an older transaction date.

## Future-price scenario explorer (steps 28–29)

Disconnect `listings.db` in DBeaver and stop the dashboard before running the write step:

```powershell
.\scripts\phase_7_future_scenarios\28_run_future_scenario_build.ps1
.\scripts\phase_5_stakeholder_dashboard\24_run_dashboard.ps1
```

Choose **Future-price scenarios** in the existing dashboard. Select a current listing or enter a starting value, choose a one-, three-, or five-year period, and edit the conservative, baseline and optimistic market-growth assumptions. The chart updates after **Update scenarios**. Use **Lines to display** to compare all scenarios or show one scenario and its range. The observed lease association and any verified flat-specific starting adjustment remain separate so users can see what drives the result.

Interest rate is shown only as a financing sensitivity for the entered loan amount and term; it does not automatically raise or lower the property-price path. Supply is included indirectly in the overall market-growth assumption because the pilot has no dependable forward BTO, MOP, active-listing and household-demand series. Each scenario line is a conditional result under the chosen assumptions. Its shaded range represents historical variation among closely matched repeat sales, so a conservative line and a lower range do not mean the same thing.

The current training estimate associates one fewer remaining lease year with about a 1.54% lower price after the available controls. This is an observational pilot estimate, not an HDB depreciation rule. The dated backtest produced MAE of about S$32,477, S$48,046 and S$64,271 at one, three and five years. The five-year historical band covered only 48.1% of later matched outcomes, so the app flags it as unreliable planning context. Do not use these scenarios as guaranteed appreciation, an investment return or an official valuation.

## Windows Control Center (step 30)

The Control Center provides one desktop interface for the existing workflows. It runs long commands in the background, streams their output into an activity log, passes the OneMap token without saving it, and prevents two processing workflows from starting at the same time.

Open it from PowerShell:

```powershell
.\scripts\phase_8_desktop_app\30_run_control_center.ps1
```

Use the Control Center in the following order. Button **1–4** is a shortcut for the complete official-data and modelling sequence, so use either that button or buttons 1 through 4 individually.

| Button | What it does | Scripts run |
|---|---|---|
| **1. Refresh data.gov.sg** | Downloads and validates the official HDB transaction and property data. | `01_run_extraction.ps1` → `01_extract_data_gov_sg.py` |
| **2. Refresh OneMap and amenities** | Geocodes HDB blocks and refreshes location and amenity datasets. Requires a OneMap token. | `02_run_onemap_extraction.ps1` → `02_extract_onemap_locations.py` |
| **3. Build database** | Rebuilds the nationwide DuckDB database and model-ready feature table from the collected files. | `03_run_duckdb_build.ps1` → `03_build_duckdb.py` |
| **4. Run modelling pipeline** | Rebuilds baselines, feature analysis, models, diagnostics, and calibration. When it finishes, opens a table comparing validation and test metrics for every fitted model and baseline, and names the released method. | Steps 04–07, then 09 |
| **Open model performance table** | Reopens the latest saved model comparison and release decision without rerunning training. | Opens saved reports; runs no script |
| **Open model diagnostics review** | Opens `reports/phase_1_resale_model/07_phase1_exit_review.json` for the current training run as soon as diagnostics finish, even while later modelling steps run. | Opens the saved report; runs no script |
| **1–4. Run full official-data refresh** | Runs buttons 1, 2, 3 and 4 in that order. This is the normal button for a complete data and model refresh. | Steps 01–07, then 09 |
| **5. Collect PropertyGuru HDB listings (via Chrome extension)** | Starts the local listing receiver and opens Chrome directly at PropertyGuru's nationwide HDB results page. The installed extension saves PropertyGuru cards into `data/listings.db`. | `15_open_chrome_with_collector.ps1` and the Chrome extension |
| **Browse 99.co / SRX HDB listings (function not built in yet)** | Opens the selected site's nationwide HDB results page for viewing. Collection requires a dedicated adapter, parser, tests and permitted collection method for each site. | Browser URL only |
| **6. Process listings and dashboard data** | Matches the latest complete listing snapshot, produces estimates and rankings, then rebuilds all dashboard data. | `17_19_build_listing_analysis.py`, `20_22_build_market_watch.py`, `23_build_dashboard_views.py`, `26_run_feature_explorer_build.ps1`, `28_run_future_scenario_build.ps1` |
| **7. Start / open dashboard** | Starts the local Streamlit dashboard and opens it in a dedicated Chrome dashboard window, including the combined Buy and sell planning page. Closing that window stops the local dashboard. | `24_run_dashboard.ps1` → `24_streamlit_app.py` |

Step 6 keeps listing processing manageable as coverage grows:

1. **Index transaction history once.** The script groups registered transactions by block, town, and block/flat type when the run starts. Flat-type inference then reads a small relevant group instead of scanning all transaction rows for every listing.
2. **Cache repeated block calculations.** Listings in the same block and inferred flat type use the same observed floor scenarios, flat model, and lease year. The script calculates that block result once per valuation month and reuses it.
3. **Reuse identical valuations.** Advertisements with the same block, month, town, flat type, flat model, floor area, floor scenario, and lease year share one comparable search and one model input. Each advertisement still keeps its own listing ID and asking-price comparison.
4. **Keep batch model prediction.** All distinct prepared scenarios are sent to the released model together. This avoids invoking the model separately for thousands of listings.
5. **Skip unchanged listings in later collections.** A prior valuation is reused only when the listing ID, block match, inferred flat type, floor area, released model, valuation month, and property-data build are unchanged. A changed asking price reuses the estimate but recalculates its dollar and percentage difference. Any changed valuation input, new month, model release, or property-data build triggers a fresh calculation. The Step 19 summary reports how many valuations were reused.

**Open model performance table**, **Open model diagnostics review**, **Stop dashboard**, **Refresh status**, the OneMap help buttons and **Cancel running workflow** are support controls. They do not advance the workflow order. The performance table is also shown after the 1–4 full refresh; the review button warns if the saved report belongs to an earlier training run.

The OneMap section includes a **Reauthenticate with OneMap** button plus links to the official [registration page](https://www.onemap.gov.sg/apidocs/register) and [authentication guide](https://www.onemap.gov.sg/apidocs/authentication). Enter the registered email and password, then click the reauthentication button. The Control Center requests a fresh token from OneMap, places it in the token field, and clears the password. OneMap currently documents each token as valid for three days. The token is passed to Step 02 for that run and neither the token nor the password is saved to disk.

The Control Center orchestrates the existing numbered scripts; it does not duplicate their extraction or modelling logic. Step 7 opens the dashboard in a dedicated Chrome window, so closing that window also stops the dashboard process. **Stop dashboard** closes that window and stops the process directly. Closing the Control Center while a workflow or dashboard is running asks before stopping that process. Dashboard logs are written to `.local/control_center/dashboard.log`.

The upper controls have their own vertical scrollbar and respond to the mouse wheel. The activity log scrolls separately below them.

An executable is optional. First install PyInstaller in the project environment, then run the packaging script:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\scripts\phase_8_desktop_app\30_build_control_center_exe.ps1
```

The result is `dist/control-center/PropertyProjectControlCenter.exe`. It remains a launcher for this project checkout rather than embedding the databases, model files and Chrome extension inside one binary. Keep it inside the project folder, or set `PROPERTY_PROJECT_ROOT` to the project directory.

## OneMap account and access token

Each user must obtain their own OneMap account and access token before running step 02:

1. [Register for a OneMap API account](https://www.onemap.gov.sg/apidocs/register) and complete account confirmation.
2. Follow [OneMap's authentication instructions](https://www.onemap.gov.sg/apidocs/authentication) to obtain an access token.
3. Run `.\scripts\phase_1_resale_model\02_run_onemap_extraction.ps1`. If `ONEMAP_TOKEN` is not already set in the current PowerShell process, the launcher asks you to paste your token using a secure prompt.

The launcher passes the token to the extractor for that run and does not save it in the project, reports, or geocoding cache. Do not commit a token, password, or a file containing either one. OneMap currently documents its generated authentication token as valid for three days; users need to obtain a fresh token after it expires. The script does **not** register accounts, generate or refresh tokens, or store OneMap login credentials.

## What is automated, and what still needs a person

| Workflow area | Automated | Still manual / limitation |
|---|---|---|
| HDB data.gov.sg extracts | Step 01 calls the public data.gov.sg download API, validates required columns, preserves raw copies, and creates nationwide processed files plus legacy pilot subsets. | Existing raw HDB files are reused by default; use `-ForceDownload` to refresh them. Someone must start the script. |
| Supporting data.gov.sg extracts | Step 02 downloads the LTA MRT/LRT station-exit GeoJSON, MOE schools, ECDA childcare centres, and MOH CHAS clinics. | Someone must start step 02; it requires a valid OneMap token for its geocoding and location requests. |
| OneMap data | Step 02 caches block geocoding results, retries 1 EVERTON PK as 1 EVERTON PARK, reuses cached results, and saves raw theme/search responses and processed coordinate tables. | Each user must register for OneMap and supply a valid token. Unmatched blocks remain with blank coordinates; check the extraction report and review non-exact geocoding matches and amenity coverage. |
| DuckDB and features | Step 03 builds and validates the analytical database and model-ready feature data. | It is a separate command; it does not download or refresh source data. |
| Baselines and analysis | Steps 04–05 build baselines and generate feature/price association reports. | They are separate commands and require the DuckDB build first. Associations are not causal effects. |
| Model training and diagnostics | Steps 06–07 fit/evaluate candidate models and inspect their errors. Step 06 selects by recent validation MAE; Step 09 chooses ML or comparable sales and calibrates the selected range. Step 10 prices one flat with that released method and supporting comparable evidence. | A person must inspect town-level error, subgroup, coverage, and data-quality reports before accepting a national release. |
| Buy and sell planning | The dashboard's combined planning page calculates buyer cash, CPF, BSD, user-entered ABSD, repayments, offer scenarios, seller proceeds, CPF refunds and a next-home comparison. | Users supply financial, lender, CPF and sale details. It cannot determine HFE or bank approval, CPF eligibility, grants, ABSD remission or sale settlement treatment. |
| Scheduled refresh | Each stage has a PowerShell launcher and is rerunnable. | There is no end-to-end orchestrator, Windows Task Scheduler job, alerting, or automatic refresh cadence configured yet. |
| Current listings and user experience | Step 15 collects Singapore HDB search cards from the Chrome extension and deduplicates listing IDs. | Install the extension once, start the local receiver for each run, and check its coverage report. The first pilot run covered 38 pages and 753 unique listings; the site's displayed count differed by one. |
| Dashboard and comparison report | Step 23 builds stable views, Step 24 provides the local Streamlit interface, and Step 25 generates a printable report when requested. | Rebuild Step 23 after the upstream listing analysis changes. A user must start the local app. Public online use still needs an approved deployable dataset and hosting configuration. |
| Neighbourhood and feature explorer | Step 26 creates the explorer snapshot and views; Step 27 displays filters, charts, a map, associations, and the selected comparison group in Streamlit. | Rebuild Step 26 after Phase 1 data changes. Interpretation remains descriptive; verify coverage town by town. |
| Future-price scenarios | Step 28 estimates the observed lease association and matched-sale backtests; Step 29 applies user assumptions and displays price paths, empirical ranges and financing sensitivity. | Rebuild Step 28 after transaction data changes. Market growth is manual and includes any expected supply effect; validate range coverage by town and horizon. |

## Current model snapshot

The latest recorded model release covers 26 towns and selects CatBoost's median estimate, trained on January 2023–September 2024 sales. Older transactions still supply earlier-sale comparable prices. Comparable matching now first seeks flats with lease commencement within three years of the target, falling back to a labelled wider-lease group when necessary. On the held-out test sales, the model's MAE is S$31,337, compared with S$32,213 for the selected recency-weighted comparable method, S$34,089 for the ordinary comparable median, and S$83,421 for the simple town/flat-type median. The model also beat the selected comparable method on validation sales, so the automatic Step 09 gate released it with a calibrated price range. These are aggregate retrospective results; inspect individual listings and matched sales before relying on a price. See `reports/phase_1_resale_model/04_baseline_metrics.json`, `07_phase1_exit_review.json`, and `09_blend_release.json`. The Step 7 review places a detailed `error_breakdown` after its original summary and limitations.

The added breakdown compares model and comparable errors on the same test sales by town, flat type, lease band, predicted price band, comparable tier and count, and month. Groups with fewer than 30 sales are marked as limited evidence. The plan does not add an automatic price correction solely because a town has few sales, or split the model at a fixed price threshold. See `implementation_plan.md` for the reasons and next validation step.

Step 06 selects the trained model with the lowest MAE in the latest half of the validation period, with overall validation MAE as a tie-breaker. Step 09 uses it only if its MAE is strictly lower than the Step 4 selected comparable method on both validation and test sales. Otherwise Step 09 releases the comparable baseline, records both scores and the reason, and continues normally. Flat and listing valuations use whichever method Step 09 selected. Test results in the existing reports were inspected during development and now affect this choice, so confirm future performance on later transactions before wider use.

Step 10 requires an existing block in the pilot database, flat type, area, and storey range. Supplying the actual flat model and lease details is preferable; if omitted, it infers them from earlier same-block sales and flags that assumption. Use `-Output 'reports/phase_1_resale_model/10_my_estimate.json'` to save a local JSON result. These optional personal estimates are ignored by Git.

These figures describe the current local data snapshot. Rerunning the workflow can change them. Overall performance does not establish accuracy for every town or flat.

## Important outputs

| Location | Contents |
|---|---|
| `data/raw/` | Downloaded source snapshots. |
| `data/processed/` | Nationwide processed extracts, coordinate tables, and retained legacy pilot subsets. |
| `data/interim/` | Resumable OneMap block geocoding cache. |
| `data/property.duckdb` | Analytical database and model/evaluation tables. |
| `data/model_ready/` | Model-ready feature and prediction exports. |
| `data/reference/` | Numbered Phase 2 example inputs and dated notes linking each policy rule to official sources. |
| `models/phase_1_resale_model/` | Fitted resale-price model artifacts. |
| `reports/phase_*/` | Numbered outputs grouped by product phase, including extraction manifests, model metrics, diagnostics, and listing history. |
| `docs/` | Detailed guides for extraction, DuckDB, baselines, and model evaluation. |

Generated data files, DuckDB and SQLite databases, fitted model artifacts, generated reports, personal planning files, local browser-collection profiles, and the Python environment are ignored by Git. The small `data/reference/` policy notes and synthetic example inputs are intended to be shared.

## Preparing a GitHub repository

This project is prepared as a **source-only repository**. Git excludes downloaded source data, DuckDB and SQLite database files, fitted model binaries, generated reports, logs, local Chrome collector profiles, personal planning outputs, and credentials. The project code, tests, documentation, reference policy notes, and synthetic example inputs remain shareable.

Before creating a public GitHub repository, review the files proposed for upload:

```powershell
git status --short
git status --ignored --short
```

Create the first local commit only after reviewing that list:

```powershell
git add .
git status --short
git commit -m "Initial project source"
```

Then create an empty repository on GitHub and follow the GitHub-provided commands to add its remote and push the commit. Do not upload `data/listings.db`, `data/property.duckdb`, files from `data/raw/`, files from `reports/`, a OneMap token, personal financial inputs, or PropertyGuru collection data.

There is no licence file yet. Until you choose and add one, others may view the source on a public repository but do not receive permission to reuse it. Keep the GitHub repository private if you do not intend to publish the source code under a licence.

## Buyer planner rules and sources

The buyer calculation uses the residential [IRAS Buyer's Stamp Duty schedule](https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/buyer%27s-stamp-duty-%28bsd%29) effective 15 February 2023 and takes the [ABSD rate](https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/additional-buyer%27s-stamp-duty-%28absd%29) from the user. It applies the lower of price and HDB value when estimating loan and CPF funding, following [HDB resale financing](https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/resale-flat-planning/mode-of-financing) and [CPF down-payment guidance](https://www.cpf.gov.sg/service/article/can-i-use-my-cpf-savings-for-the-down-payment-of-my-property). [MoneySense's affordability guide](https://www.moneysense.gov.sg/buying-a-property-how-much-can-you-afford/) supplies the bank term, cash, and debt screening rules. Verify CPF eligibility with the [CPF housing usage calculator](https://www.cpf.gov.sg/member/tools-and-services/calculators/cpf-housing-usage). HDB's [Request for Value](https://www.hdb.gov.sg/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/option-to-purchase/request-for-value) provides the value needed for an actual resale application. Loan interest is entered by the user; check the current [HDB rate](https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/managing-my-home/finances/loan-matters/interest-rate) or your bank offer. The exact checked dates, assumptions, and exclusions are in [`data/reference/11_policy_notes.md`](data/reference/11_policy_notes.md).

## Sources and usage

The extraction scripts use open government datasets and OneMap location services. Check each source's terms and attribution requirements before redistributing raw or derived data. Source details and extraction notes are recorded in [`docs/onemap_location_extraction.md`](docs/onemap_location_extraction.md) and the dated files under `data/reference/`.

## Limitations

- Nationwide support does not establish equal accuracy in every town; town-level validation is required before relying on the national candidate.
- The available data does not describe a unit's interior condition, renovation quality, exact floor, orientation, view, or seller motivation.
- Amenity locations are current snapshots; historic opening/closing dates are not available for every facility.
- CHAS clinics are not a complete registry of all healthcare providers. OneMap search results and themes can have incomplete coverage or false positives.
- Price ranges are estimates. Coverage can be below the stated nominal level, and group-level error varies.
- Do not use the output as an official valuation, loan decision, or guarantee of future price.
- The buyer planner is a cash-flow estimate, not official HFE eligibility, CPF approval, an ABSD ruling, or a lender offer. Its affordability ceiling assumes value equals price; cash over valuation or a lower approved loan can reduce it.

