# Singapore HDB Resale Price Research

A local, reproducible data science project for exploring HDB resale transactions, comparable sales, and rough resale-price estimates. The current Phase 1 dataset and evaluation are a **Sengkang pilot**. Estimates are research outputs, not official HDB or professional valuations.

## What it does

The workflow:

1. Downloads HDB resale transaction and HDB property datasets from data.gov.sg.
2. Downloads supporting school, childcare, healthcare, and MRT/LRT exit datasets from data.gov.sg, and uses OneMap to geocode HDB blocks and discover amenities.
3. Builds a persistent DuckDB database and transaction-level feature table.
4. Creates comparable-sales and recent-median baselines, profiles feature/price associations, and trains resale-price models.
5. Evaluates estimates on time-ordered data, reports weak-performing segments, calibrates price ranges, and attaches explanations and comparable-sale evidence.
6. Plans a buyer's resale purchase cash, CPF, stamp duties, mortgage payment, and offer scenarios from user supplied assumptions.
7. Estimates a seller's cash proceeds and CPF refund under three sale prices, with an optional next-home comparison.

The project has local browser forms for buyer and seller planning. The Chrome extension collects Sengkang HDB search cards when started in regular Chrome. There is no hosted public website, complete live listings feed, or automatic scheduler.

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

# 8. Experiment with a Ridge/comparable blend and prior-price features
.\scripts\phase_1_resale_model\08_run_hybrid_experiment.ps1

# 9. Calibrate the accepted Sengkang blend and review its backtest
.\scripts\phase_1_resale_model\09_run_blend_calibration.ps1

# 10. Estimate a single flat using the saved pilot configuration
.\scripts\phase_1_resale_model\10_run_flat_valuation.ps1 -Block '106' -Street 'RIVERVALE WALK' -FlatType '4 ROOM' -FloorAreaSqm 100 -StoreyRange '01 TO 03' -FlatModel 'Model A' -RemainingLeaseYears 71.5

# 11. Plan a buyer's purchase using editable assumptions
.\scripts\phase_2_buyer_planner\11_run_buyer_planner.ps1 -InputFile '.\data\reference\11_buyer_inputs.example.json' -OutputFile '.\reports\phase_2_buyer_planner\11_buyer_plan_example.json'

# 12. Open the local buyer form in your browser
.\scripts\phase_2_buyer_planner\12_run_buyer_web_form.ps1

# 13. Calculate seller proceeds from editable example inputs
.\scripts\phase_3_seller_planner\13_run_seller_planner.ps1 -InputFile '.\data\reference\13_seller_inputs.example.json' -OutputFile '.\reports\phase_3_seller_planner\13_seller_plan_example.json'

# 14. On the same local website, click "Seller proceeds" or open:
# http://127.0.0.1:8788/seller

# 15. Start the local receiver for the Sengkang Chrome extension
.\scripts\phase_4_market_watch\15_run_propertyguru_browser.ps1
```

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
| `scripts/phase_1_resale_model/02_extract_onemap_locations.py` | Geocodes HDB blocks and collects amenity locations. |
| `scripts/phase_1_resale_model/03_run_duckdb_build.ps1` | Sets up Python packages and runs the database build. |
| `scripts/phase_1_resale_model/03_build_duckdb.py` | Builds the research DuckDB tables and model features. |
| `scripts/phase_1_resale_model/04_run_baselines.ps1` | Runs baseline price evaluation. |
| `scripts/phase_1_resale_model/04_build_baselines.py` | Builds comparable-sales and recent-median baselines. |
| `scripts/phase_1_resale_model/05_run_feature_price_analysis.ps1` | Runs the feature/price analysis. |
| `scripts/phase_1_resale_model/05_analyse_feature_price_correlations.py` | Reports how each feature relates to resale price. |
| `scripts/phase_1_resale_model/06_run_resale_models.ps1` | Runs model training and comparison. |
| `scripts/phase_1_resale_model/06_train_resale_models.py` | Trains and evaluates the resale-price models. |
| `scripts/phase_1_resale_model/07_run_phase1_diagnostics.ps1` | Runs reliability and explanation checks. |
| `scripts/phase_1_resale_model/07_phase1_diagnostics.py` | Examines errors, price ranges, and estimate explanations. |
| `scripts/phase_1_resale_model/08_run_hybrid_experiment.ps1` | Runs the model/comparable blend experiment. |
| `scripts/phase_1_resale_model/08_evaluate_hybrid.py` | Evaluates hybrid predictions and engineered features. |
| `scripts/phase_1_resale_model/09_run_blend_calibration.ps1` | Runs calibration of the accepted blend. |
| `scripts/phase_1_resale_model/09_calibrate_accepted_blend.py` | Calibrates and backtests the selected price estimate. |
| `scripts/phase_1_resale_model/10_run_flat_valuation.ps1` | Starts a valuation for one flat. |
| `scripts/phase_1_resale_model/10_predict_flat.py` | Produces the single-flat estimate and supporting evidence. |
| `scripts/phase_2_buyer_planner/11_run_buyer_planner.ps1` | Runs a buyer plan from an input file. |
| `scripts/phase_2_buyer_planner/11_buyer_cost_planner.py` | Calculates buyer cash, CPF, duties, and loan scenarios. |
| `scripts/phase_2_buyer_planner/11_test_buyer_cost_planner.py` | Tests the buyer calculations. |
| `scripts/phase_2_buyer_planner/12_run_buyer_web_form.ps1` | Starts the local buyer and seller website. |
| `scripts/phase_2_buyer_planner/12_buyer_web_form.py` | Serves the local forms and calls their calculators. |
| `scripts/phase_2_buyer_planner/12_test_buyer_web_form.py` | Tests the local form server. |
| `scripts/phase_2_buyer_planner/12_test_buyer_validation.js` | Tests buyer form input checks. |
| `web/12_buyer_form.js` | Handles buyer form inputs and displays its result. |
| `web/12_buyer_validation.js` | Checks buyer inputs in the browser. |
| `scripts/phase_3_seller_planner/13_run_seller_planner.ps1` | Runs a seller plan from an input file. |
| `scripts/phase_3_seller_planner/13_seller_proceeds_planner.py` | Calculates sale proceeds, CPF refunds, and shortfalls. |
| `scripts/phase_3_seller_planner/13_test_seller_proceeds_planner.py` | Tests the seller calculations. |
| `scripts/phase_3_seller_planner/14_test_seller_web_form.py` | Tests the seller page and form server. |
| `web/14_seller_form.js` | Handles seller form inputs and displays its result. |
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
| `scripts/phase_4_market_watch/17_19_build_listing_analysis.py` | Performs Steps 17–19: matches listings to HDB blocks, adds property and amenity features, and estimates fair-price ranges. |
| `scripts/phase_4_market_watch/17_19_test_listing_analysis.py` | Tests address normalization, block matching, flat-type inference, and storey scenarios used by Steps 17–19. |
| `scripts/phase_4_market_watch/20_22_build_market_watch.py` | Performs Steps 20–22: saves a search profile, ranks current listings, tracks snapshots, and creates the market-watch report. |
| `scripts/phase_4_market_watch/20_22_test_market_watch.py` | Tests preference validation, deal-ranking rules, and price-change tracking. |
| `scripts/phase_5_stakeholder_dashboard/23_build_dashboard_views.py` | Builds the dashboard's stable DuckDB views and small configuration/metric tables. |
| `scripts/phase_5_stakeholder_dashboard/23_test_dashboard_views.py` | Tests view availability, row identity, and category safeguards. |
| `scripts/phase_5_stakeholder_dashboard/24_run_dashboard.ps1` | Starts the local Streamlit dashboard on this computer. |
| `scripts/phase_5_stakeholder_dashboard/24_test_dashboard.py` | Opens the dashboard in Streamlit's test runner and checks for startup errors. |
| `dashboard/phase_5/24_streamlit_app.py` | Provides the interactive overview, listing, change, quality, and report pages. |
| `dashboard/phase_5/25_agent_report.py` | Creates the self-contained printable listing comparison report in memory. |
| `scripts/phase_5_stakeholder_dashboard/25_test_agent_report.py` | Tests report safety, selection limits, and planning-file input. |
| `scripts/phase_6_neighbourhood_explorer/26_run_feature_explorer_build.ps1` | Starts the neighbourhood and feature-explorer data build. |
| `scripts/phase_6_neighbourhood_explorer/26_build_feature_explorer.py` | Copies a focused transaction snapshot and feature associations into the dashboard database. |
| `scripts/phase_6_neighbourhood_explorer/26_test_feature_explorer.py` | Tests explorer views, transaction integrity, association scope, and block summaries. |
| `dashboard/phase_6/27_feature_explorer.py` | Renders transaction trends, a distribution, a map, and feature relationships. |
| `scripts/phase_6_neighbourhood_explorer/27_test_feature_explorer_dashboard.py` | Tests that the feature-explorer dashboard page opens successfully. |

Step 02 reuses cached HDB block geocoding results, but refreshes its amenity and supporting source extracts when rerun. Re-run steps 03–09 after refreshed source data so the database, features, baselines, models, and accepted pilot range are rebuilt. Step 10 prices one flat and can be rerun whenever needed.

Step 11 is independent of the database. Copy the example JSON to `reports/phase_2_buyer_planner/11_my_inputs.json`, replace the example figures with your own, then run step 11 with that file. These personal input and output files are ignored by Git. Enter the applicable ABSD rate explicitly, including `0` only if appropriate. Enter an HDB Request for Value result as `hdb_value_sgd` with `value_status: "official"`, or clearly mark a planning assumption as `"assumed"`. The step 10 model range can be copied into `fair_value_lower_sgd` and `fair_value_upper_sgd` for comparison; it is not HDB's value.

### Easier input: local buyer form

Run `.\scripts\phase_2_buyer_planner\12_run_buyer_web_form.ps1` in PowerShell. Your browser opens `http://127.0.0.1:8788/`. Keep PowerShell open while using the form; press **Ctrl+C** there to stop it. If port 8788 is busy, use `.\scripts\phase_2_buyer_planner\12_run_buyer_web_form.ps1 -Port 8789`. The form asks for the flat price, financing and available funds in plain language, with optional costs tucked under expandable sections. It calls the same Python calculator as step 11, so it does not need a JSON input file.

You can load a saved `reports/phase_1_resale_model/10_*.json` estimate in the form to fill its fair-price range; that file is read in the browser and is not uploaded. HDB value remains a separate field. Results appear on the page and are not saved automatically. After calculating, the optional **Compare with your documents** section lets you choose the matching price scenario and enter independently checked figures from an HFE letter, bank offer, HDB Request for Value, CPF or final purchase documents. It shows plan, document figure and difference; it does not verify a document or recalculate the plan. Blank figures are skipped. **Download result JSON** saves a copy only when you click it, including the document comparison if you have completed one. The server listens only on this computer (`127.0.0.1`); it does not contact HDB, CPF, a bank, or a remote service. The official-source links in the form open those sites only when clicked. The form requires no additional Python packages.

### Seller proceeds planner

Step 13 accepts expected, conservative and optimistic sale prices, the outstanding loan, CPF refund for all owners, and selling costs. It returns cash proceeds and CPF returned separately, plus loan, CPF and fee shortfalls. If low or high prices are blank, it uses illustrative prices 5% either side of the expected price. Enter either the CPF refund total from CPF's Home ownership dashboard or its principal and accrued-interest components. See [seller rule notes](data/reference/13_seller_policy_notes.md) for the official references and limitations.

Step 14 is the **Seller proceeds** page on the same local server started by step 12. Open [http://127.0.0.1:8788/seller](http://127.0.0.1:8788/seller) or click the link in the buyer form. No second port is needed. You can enter the next home's cash and CPF needs or load a downloaded step 12 buyer plan; the page uses its asking-price scenario. That comparison does not determine CPF reuse, second HDB loan eligibility, or the timing of sale proceeds. Downloading the seller result is optional; entries are not saved automatically.

The estimate is for a **whole-flat sale**, not a sale of only one owner's share. Check the loan balance with HDB or your bank and the required refund for every owner in CPF's Home ownership dashboard. If a sale price cannot cover the loan, or if the CPF refund is short, confirm the settlement treatment with HDB and CPF before relying on the result.

## Sengkang listings: Chrome extension (step 15)

Step 15 uses a Chrome extension in your regular browser and a local receiver. PropertyGuru Sengkang HDB is the first supported site. Each site has its own page-reading adapter in `scripts/17_propertyguru_chrome_extension/sites.js`; the collection flow and database table are shared. The extension folder retains its original name because Chrome has already loaded it from that path. A future site needs an adapter, a narrow page match in the extension manifest, and a source parser/registration in the receiver. Adding an adapter does not grant access to a site or guarantee its pages are readable.

The numbered files for this step have separate jobs: `15_open_chrome_with_collector.ps1` opens Chrome and starts the receiver; `15_run_propertyguru_browser.ps1` starts only the receiver; `15_receive_propertyguru_browser.py` validates and saves cards; `15_propertyguru_cards.py` parses PropertyGuru cards. The `15_test_*.py` and extension `15_test_*.js` files check those parts. Step 16, `16_import_legacy_propertyguru.py`, was used once to move an older 38-page JSON run into DuckDB; it is not part of normal collection.

Each time you want a snapshot, start the receiver, open a supported results tab, and click **Start from page 1**. The PropertyGuru adapter reads the result count and final page number on page 1, saves visible HDB cards, waits a random 3–4 seconds, and opens the next numbered results URL in the same tab. The collector stops if cards disappear, a page does not advance, or the local receiver is unavailable. It does not solve access challenges or read cookies, other tabs, photos, or contact details.

If the popup remains on a saved page, first check that the receiver window is still running. The popup now flags an offline receiver. Reloading a tab or extension no longer loses the scheduled next page in a current run; an older run without recovery state stops and asks you to start again from page 1.

1. Open Chrome through the **Chrome + Property Collector** desktop shortcut. It starts the local receiver if needed, then opens your normal Chrome profile. You can also start the receiver manually: in PowerShell, run `cd "C:\Users\temas\Documents\PropertyProject"` then `.\scripts\phase_4_market_watch\15_run_propertyguru_browser.ps1` and leave that window open. The receiver listens on `127.0.0.1:8771` only.
2. In Chrome, visit `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `C:\Users\temas\Documents\PropertyProject\scripts\17_propertyguru_chrome_extension`. If already installed, click **Reload** on the extension after updating its files. Current access is limited to the local receiver and Sengkang HDB results pages.
3. Open `https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang` in Chrome, click the extension icon, then **Start from page 1**. Keep Chrome running until the extension says Finished or Stopped. The shortcut-launched receiver stays running after Chrome closes; opening Chrome through a different shortcut will not start it. Disconnect DBeaver from `listings.db` before collecting, since its open connection can lock the database file.

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
| 19 | `listing_valuations` | Applies the accepted Ridge/comparable blend and stores a price range, asking-price difference, confidence, scenarios and supporting comparable count. |

PropertyGuru result cards do not reliably contain the exact flat model, lease commencement year or floor range. The build therefore infers flat type from floor area and evaluates storey scenarios observed in historical transactions for the matched block. These assumptions are recorded in the database. A listing is marked `not_valued` when the available evidence is insufficient; the build does not invent a price. The current Sengkang snapshot contains 753 listings: 752 block matches, 733 estimates and 20 withheld estimates. The machine-readable run summary is `reports/phase_4_market_watch/19_listing_analysis_summary.json`.

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

The first run uses a broad Sengkang profile: it has no budget, size or flat-type limit, requires a Step 19 estimate, needs at least three comparable sales, and treats a listing snapshot older than 14 days as stale. Its purpose is to make the complete current shortlist visible before you set your personal limits.

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

Step 23 adds a presentation layer to the existing `data/listings.db`; it does not create another database. In DuckDB, a **table** stores rows. A **view** stores a named SQL query. When the dashboard reads a view, DuckDB runs that query against the current tables and returns rows that look like a table. This lets the dashboard use clear, stable column names without copying the 753 listings into another set of tables.

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

Step 25 is part of the Streamlit **Comparison report** page. A user selects up to four listings, may attach a buyer or seller plan JSON, add a neutral report reference, and add comparison notes. The app creates a self-contained HTML report in memory. Nothing is saved by the project until the user clicks **Download printable report**; the downloaded file can be opened in a browser and printed or saved as PDF. This supports the longer-term goal of software that an end user can operate locally without DBeaver or command-line SQL.

The local build currently presents 753 listings, 10,271 supporting comparable rows, seven data-quality checks, and 14 model-evaluation rows. The current category counts are 275 strong candidates, 429 fairly priced, 17 negotiation candidates, 12 likely expensive, and 20 with insufficient evidence. These are research categories based on the saved snapshot and model evidence, not confirmed bargains or official valuations.

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

Open [http://127.0.0.1:8501](http://127.0.0.1:8501) and choose **Neighbourhood and features**. Step 27 lets the user filter by flat type and transaction years. It shows transaction count, block count, median price, median price per square metre, a monthly trend, price distribution, transaction-block map, one selected feature relationship, and the latest 500 matching transactions.

The current explorer covers Sengkang from January 2017 through September 2026: 19,513 transactions across 618 transaction-bearing blocks and five flat types. Its 42 displayed feature relationships were calculated from 15,766 training rows only. Validation and test prices were excluded from feature screening.

The explorer deliberately describes **associations**. A positive or negative correlation does not prove that a school, station, park, lease, floor, or another feature caused the price difference. Flat type, time, location, and overlapping characteristics can produce the observed relationship. Current amenity locations may also differ from those available on an older transaction date.

## OneMap account and access token

Each user must obtain their own OneMap account and access token before running step 02:

1. [Register for a OneMap API account](https://www.onemap.gov.sg/apidocs/register) and complete account confirmation.
2. Follow [OneMap's authentication instructions](https://www.onemap.gov.sg/apidocs/authentication) to obtain an access token.
3. Run `.\scripts\phase_1_resale_model\02_run_onemap_extraction.ps1`. If `ONEMAP_TOKEN` is not already set in the current PowerShell process, the launcher asks you to paste your token using a secure prompt.

The launcher passes the token to the extractor for that run and does not save it in the project, reports, or geocoding cache. Do not commit a token, password, or a file containing either one. OneMap currently documents its generated authentication token as valid for three days; users need to obtain a fresh token after it expires. The script does **not** register accounts, generate or refresh tokens, or store OneMap login credentials.

## What is automated, and what still needs a person

| Workflow area | Automated | Still manual / limitation |
|---|---|---|
| HDB data.gov.sg extracts | Step 01 calls the public data.gov.sg download API, validates required columns, preserves raw copies, and creates pilot subsets. | Existing raw HDB files are reused by default; use `-ForceDownload` to refresh them. Someone must start the script. |
| Supporting data.gov.sg extracts | Step 02 downloads the LTA MRT/LRT station-exit GeoJSON, MOE schools, ECDA childcare centres, and MOH CHAS clinics. | Someone must start step 02; it requires a valid OneMap token for its geocoding and location requests. |
| OneMap data | Step 02 caches block geocoding results, reuses cached results, and saves raw theme/search responses and processed coordinate tables. | Each user must register for OneMap and supply a valid token. Token creation and renewal are not automated. Non-exact geocoding matches and amenity coverage still need review. |
| DuckDB and features | Step 03 builds and validates the analytical database and model-ready feature data. | It is a separate command; it does not download or refresh source data. |
| Baselines and analysis | Steps 04–05 build baselines and generate feature/price association reports. | They are separate commands and require the DuckDB build first. Associations are not causal effects. |
| Model training and diagnostics | Steps 06–08 fit/evaluate candidate models and compare the blend. Step 09 calibrates the accepted Sengkang pilot blend and backtests its range. Step 10 prices one flat with component estimates, a range, and comparable evidence. | A person must inspect the error, subgroup, coverage, and data-quality reports before using estimates. The accepted blend is a pilot decision, not a national model approval. |
| Buyer cost planner | Step 11 calculates purchase cash and CPF allocation, BSD, user entered ABSD, monthly repayments, offer and interest/term/renovation comparisons, and an indicative affordability ceiling. | User supplies buyer finances, applicable ABSD, loan terms, value assumption, and optional costs. It cannot determine HFE or bank approval, CPF eligibility, grants, or ABSD remission. |
| Local buyer form | Step 12 collects the same inputs in a browser and shows the results without requiring a JSON file. It can import a Step 10 estimate range locally. | A person starts and stops the local server and confirms personal financial inputs. Nothing is saved unless the user downloads the result. |
| Seller proceeds planner | Step 13 estimates sale cash, CPF refund, shortfalls and three price scenarios from seller-entered figures. | Loan, CPF and selling-cost amounts must be verified with HDB, CPF, the lender or other documents. It does not decide CPF shortfall treatment or loan eligibility. |
| Local seller form | Step 14 shows the seller planner on the same local server and can read a downloaded buyer plan locally for a next-home comparison. | The buyer plan may need recalculation; sale CPF is not automatically usable CPF for a new flat. |
| Scheduled refresh | Each stage has a PowerShell launcher and is rerunnable. | There is no end-to-end orchestrator, Windows Task Scheduler job, alerting, or automatic refresh cadence configured yet. |
| Current listings and user experience | Step 15 collects Sengkang HDB search cards from the Chrome extension and deduplicates listing IDs. | Install the extension once, start the local receiver for each run, and check its coverage report. The first live run covered 38 pages and 753 unique listings; the site's displayed count differed by one. |
| Dashboard and comparison report | Step 23 builds stable views, Step 24 provides the local Streamlit interface, and Step 25 generates a printable report when requested. | Rebuild Step 23 after the upstream listing analysis changes. A user must start the local app. Public online use still needs an approved deployable dataset and hosting configuration. |
| Neighbourhood and feature explorer | Step 26 creates the explorer snapshot and views; Step 27 displays filters, charts, a map, associations, and the selected comparison group in Streamlit. | Rebuild Step 26 after Phase 1 data changes. Interpretation remains descriptive, and current coverage is Sengkang only. |

## Current model snapshot

The latest recorded test evaluation is for Sengkang only. Ridge was selected using validation data; on the later test set its MAE was S$40,010, compared with S$39,679 for the comparable-sales baseline and S$63,885 for the recent town/flat-type median baseline. Thus Ridge beats the simple median but the comparable-sales baseline was slightly lower on this test. The test interval coverage was 90.88% versus a mean nominal level of 95.54%, with mean interval width around S$193,739. Results vary by flat and price segment; the range can be broad and some segments are unreliable. See `reports/phase_1_resale_model/07_phase1_exit_review.json` and `reports/phase_1_resale_model/07_reliability_breakdown.csv`.

Step 08 found that a 45% Ridge / 55% comparable-sales blend had test MAE S$35,790, below either component on this snapshot. A Ridge model with recent-price features won validation but failed the later test check (MAE S$46,683). The blend is accepted for the Sengkang pilot. Step 09 calibrated its symmetric residual range: observed test coverage was 97.02% against 95.54% average nominal coverage, with a broad mean width of S$247,683. These outcomes are exploratory because earlier test results were inspected during development; confirm on future transactions before wider use.

Step 10 requires an existing block in the pilot database, flat type, area, and storey range. Supplying the actual flat model and lease details is preferable; if omitted, it infers them from earlier same-block sales and flags that assumption. Use `-Output 'reports/phase_1_resale_model/10_my_estimate.json'` to save a local JSON result. These optional personal estimates are ignored by Git.

These figures describe the current local data snapshot. Rerunning the workflow can change them. One-town performance does not establish accuracy across Singapore.

## Important outputs

| Location | Contents |
|---|---|
| `data/raw/` | Downloaded source snapshots. |
| `data/processed/` | Cleaned pilot extracts and coordinate tables. |
| `data/interim/` | Resumable OneMap block geocoding cache. |
| `data/property.duckdb` | Analytical database and model/evaluation tables. |
| `data/model_ready/` | Model-ready feature and prediction exports. |
| `data/reference/` | Numbered Phase 2 example inputs and dated notes linking each policy rule to official sources. |
| `models/phase_1_resale_model/` | Fitted resale-price model artifacts. |
| `reports/phase_*/` | Numbered outputs grouped by product phase, including extraction manifests, model metrics, diagnostics, and listing history. |
| `implementation_plan.md` | Product phases, data sources, architecture, and current plan. |
| `docs/` | Detailed guides for extraction, DuckDB, baselines, and model evaluation. |
| `web/` | Numbered local buyer and seller form pages, styles, and browser logic. |

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

Step 11 uses the residential [IRAS Buyer's Stamp Duty schedule](https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/buyer%27s-stamp-duty-%28bsd%29) effective 15 February 2023 and takes the [ABSD rate](https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/additional-buyer%27s-stamp-duty-%28absd%29) from the user. It applies the lower of price and HDB value when estimating loan and CPF funding, following [HDB resale financing](https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/resale-flat-planning/mode-of-financing) and [CPF down-payment guidance](https://www.cpf.gov.sg/service/article/can-i-use-my-cpf-savings-for-the-down-payment-of-my-property). [MoneySense's affordability guide](https://www.moneysense.gov.sg/buying-a-property-how-much-can-you-afford/) supplies the bank term, cash, and debt screening rules. Verify CPF eligibility with the [CPF housing usage calculator](https://www.cpf.gov.sg/member/tools-and-services/calculators/cpf-housing-usage). HDB's [Request for Value](https://www.hdb.gov.sg/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/option-to-purchase/request-for-value) provides the value needed for an actual resale application. Loan interest is entered by the user; check the current [HDB rate](https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/managing-my-home/finances/loan-matters/interest-rate) or your bank offer. The exact checked dates, assumptions, and exclusions are in [`data/reference/11_policy_notes.md`](data/reference/11_policy_notes.md).

## Sources and usage

The extraction scripts use open government datasets and OneMap location services. Check each source's terms and attribution requirements before redistributing raw or derived data. Key sources are listed in [`implementation_plan.md`](implementation_plan.md) and [`docs/onemap_location_extraction.md`](docs/onemap_location_extraction.md).

## Limitations

- The current model workflow is a single-town Sengkang pilot, not a Singapore-wide valuation service.
- The available data does not describe a unit's interior condition, renovation quality, exact floor, orientation, view, or seller motivation.
- Amenity locations are current snapshots; historic opening/closing dates are not available for every facility.
- CHAS clinics are not a complete registry of all healthcare providers. OneMap search results and themes can have incomplete coverage or false positives.
- Price ranges are estimates. Coverage can be below the stated nominal level, and group-level error varies.
- Do not use the output as an official valuation, loan decision, or guarantee of future price.
- The buyer planner is a cash-flow estimate, not official HFE eligibility, CPF approval, an ABSD ruling, or a lender offer. Its affordability ceiling assumes value equals price; cash over valuation or a lower approved loan can reduce it.

