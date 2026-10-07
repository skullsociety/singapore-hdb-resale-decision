# Resale price models

Run from the project root after the DuckDB build and baseline workflow:

```powershell
.\scripts\phase_1_resale_model\06_run_resale_models.ps1
```

For a quicker comparison that skips optional CatBoost fits:

```powershell
.\scripts\phase_1_resale_model\06_run_resale_models.ps1 -SkipCatBoost
```

This step predicts `resale_price` using Ridge, Huber gradient boosting, a
50-tree squared-error random forest, CatBoost with native categorical features when
the CatBoost package can load, and a P10/P50/P90 quantile model. The random
forest uses squared-error splits and mean predictions in its leaves. Its MAE is
still measured on the same validation and test data as the other candidates.
CatBoost uses one multi-quantile estimator;
the scikit-learn fallback uses three histogram-based quantile estimators, each
with a 220-iteration cap and early stopping on a holdout drawn from training
rows. The quantile model estimates the 10th, 50th, and 90th price
percentiles, giving a central estimate and a nominal 80% prediction range.
Actual coverage is measured and reported. It fits all models using only
training rows. Validation MAE selects the winning fitted machine-learning model;
RMSE breaks a tie. Baselines remain in the report for comparison. The test split
is evaluated after selection and is not used to pick a model.

Engineered inputs include relative storey within block height, squared building
age, log and threshold versions of MRT distance, cyclical month features, and
flat-type-specific floor-area and remaining-lease interactions. Ridge and
Gradient Boosting use one-hot categories and scaled numeric values. CatBoost
uses native categorical handling for flat type and flat model.

The evaluation matrix reports MAE, median absolute error, MAPE, median APE,
RMSE, R-squared, mean bias, interval coverage, and interval width for both
validation and test data, including the comparable-sales and recent-median
baselines. Error analysis groups results by flat type, actual-price quartile, transaction year, storey range, and
remaining-lease band.

Outputs:

| Path | Contents |
|---|---|
| `models/phase_1_resale_model/06_ridge_resale_price.joblib` | Fitted Ridge model and feature metadata. |
| `models/phase_1_resale_model/06_gradient_boosting_resale_price.joblib` | Fitted gradient boosting model and feature metadata. |
| `models/phase_1_resale_model/06_random_forest_squared_error_resale_price.joblib` | Fitted 50-tree squared-error random forest and feature metadata. |
| `models/phase_1_resale_model/06_catboost_resale_price.cbm` | Fitted CatBoost price model, when CatBoost loads. |
| `models/phase_1_resale_model/06_catboost_quantiles_resale_price.cbm` | Fitted CatBoost P10/P50/P90 model, when CatBoost loads. |
| `models/phase_1_resale_model/06_sklearn_quantiles_resale_price.joblib` | Fitted scikit-learn quantile models used if CatBoost cannot load. |
| `reports/phase_1_resale_model/06_model_performance.csv` | Consistent validation/test metric comparison for all fitted candidates and both baselines. |
| `reports/phase_1_resale_model/06_error_analysis.csv` | Performance breakdown by the error analysis groups. |
| `reports/phase_1_resale_model/06_quantile_price_ranges.csv` | P10/P50/P90 estimates and whether the actual price fell inside the P10–P90 range. |
| `reports/phase_1_resale_model/06_model_selection.json` | Selection rule, selected model, metrics, features, and artifact paths. |
| `data/model_ready/06_ml_predictions.csv` | Validation/test ML predictions. |
| DuckDB `model_evaluation_metrics` | Comparable metric rows. |
| DuckDB `model_error_analysis` | Grouped error analysis. |
| DuckDB `selected_model` | Selected model and selection evidence. |
| DuckDB `quantile_price_ranges` | Quantile range, actual price, and interval coverage by transaction. |

The selected trained model is the candidate with the lowest validation MAE, with
validation RMSE used only for ties. The final test score is a forward-looking
check. A low overall error does not guarantee accuracy for every flat type or
price band; review the error breakdown before using estimates with buyers or
sellers. Amenity locations are current snapshots, so older transactions may
have location-feature timing mismatch.

## Phase 1 diagnostics and release check

After the model run, execute `scripts/phase_1_resale_model/07_run_phase1_diagnostics.ps1`. It:

- Breaks held-out errors out by town, flat type, actual-price quartile, floor-area quartile, storey range, lease band, and transaction year. Groups with fewer than 30 sales are marked too small to judge; groups with MAE at least 1.25 times the overall selected-model MAE are flagged for review.
- Compares global and town/flat-type residual calibration using an early-validation calibration window and a later-validation assessment window. It uses global residual calibration because that method had better out-of-time validation coverage for this pilot. The main range targets 95% coverage; estimates with fewer than five comparable sales use a wider 97.5% interval. Test coverage is reported as the final check, not used to fit the interval.
- Writes a per-estimate Ridge contribution breakdown when Ridge is selected, and up to ten ranked, prior comparable sales. Nonlinear models do not currently have individual feature explanations. Ridge contributions describe model associations, not causes.
- Checks each Phase 1 exit criterion and records evidence and remaining gaps.

| Output | Contents |
|---|---|
| `reports/phase_1_resale_model/07_reliability_breakdown.csv` | Error, bias, and interval coverage by estimate segment. |
| `reports/phase_1_resale_model/07_calibrated_price_ranges.csv` | Test estimates with calibrated bounds, comparable counts, and confidence labels. |
| `reports/phase_1_resale_model/07_estimate_explanations.csv` | One explanation and model version per test estimate. |
| `reports/phase_1_resale_model/07_comparable_evidence.csv` | Up to ten ranked, earlier sales supporting each test estimate. |
| `reports/phase_1_resale_model/07_calibration_method_check.csv` | Time-ordered validation comparison of global and town/flat-type interval calibration. |
| `reports/phase_1_resale_model/07_phase1_exit_review.json` | Exit-criterion statuses, measured evidence, and limitations. |

The script also persists these outputs to the `phase1_*` DuckDB tables and
updates lower/upper bounds on the selected model's test prediction rows. The
current source data is a Sengkang pilot, so its single-town results do not
establish performance across Singapore.

## Hybrid and recent-price experiment

After steps 04 and 06, run:

```powershell
.\scripts\phase_1_resale_model\08_run_hybrid_experiment.ps1
```

This optional experiment reuses the current transaction table and baseline predictions. It trains Ridge on rolling historical windows and recreates comparable-sale estimates using only earlier months. The first two training folds choose one Ridge weight in 5% increments; the third training fold checks it. A second Ridge model adds the prior 12-month median price per square metre for the same town and flat type (multiplied by subject floor area), the percentage change versus the preceding 12 months, and the prior-year sale count. Transactions in the target month or later cannot enter these features. At least ten earlier sales are required for a group median; missing medians are handled by the training pipeline.

The experiment reports comparable sales, original Ridge, Ridge with recent-price features, and each Ridge variant blended with comparable sales. Validation MAE selects a candidate, and the test period is a final report-only gate. Current findings:

| Candidate | Rolling check MAE | Validation MAE | Test MAE |
|---|---:|---:|---:|
| Comparable sales | S$49,160 | S$53,552 | S$39,679 |
| Original Ridge | S$48,335 | S$51,017 | S$40,010 |
| 45% Ridge / 55% comparable blend | S$45,982 | S$49,058 | S$35,790 |
| Ridge with recent-price features | S$26,279 | S$31,311 | S$46,683 |

The recent-price model won validation but failed the test gate. Its test predictions were high by S$34,238 on average. The simple blend reduced test MAE by S$3,890 versus comparable sales; a descriptive paired bootstrap 95% interval for that difference was S$2,866–S$4,950 lower. These outcomes are exploratory because earlier test results had already been reviewed while developing this project. Step 08 leaves the model selected by step 06 and the ranges/explanations from step 07 unchanged. Confirm the blend on future transactions before promoting it.

| Output | Contents |
|---|---|
| `reports/phase_1_resale_model/08_hybrid_model_comparison.csv` | Metrics for weight tuning, rolling check, validation, and test. |
| `reports/phase_1_resale_model/08_hybrid_flat_type_errors.csv` | Validation and test errors by flat type. |
| `reports/phase_1_resale_model/08_hybrid_selection.json` | Weights, validation choice, test gate, paired comparison, and limitations. |
| `data/model_ready/08_hybrid_predictions.csv` | Per-transaction predictions for each candidate and period. |
| `models/phase_1_resale_model/08_ridge_recent_trend.joblib` | Experimental recent-price Ridge pipeline and feature definition. |

## National candidate: steps 09 and 10

Run step 09 after the numbered build, baseline, model, and blend steps:

```powershell
.\scripts\phase_1_resale_model\09_run_blend_calibration.ps1
```

Step 09 reads the current Step 06 winner, calibrates its residual range using validation predictions, and reports its untouched test performance. It saves the winning model ID and artifact path in `reports/phase_1_resale_model/09_blend_release.json`; Step 10 and listing valuations load that released artifact automatically. Comparable sales remain supporting evidence and do not change the model's point estimate. The range is based on overall validation residuals, widened when fewer than five comparable sales are available. Since the winner is selected and calibrated on the same validation period, range coverage can be optimistic; a separate calibration split would improve this. The report files retain their existing `09_blend_*` names for compatibility, and the matching `pilot_blend_*` DuckDB tables are retained. The test results have already been examined during development, so future transaction data is needed for an independent release check.

Run step 10 to estimate one known HDB flat:

```powershell
.\scripts\phase_1_resale_model\10_run_flat_valuation.ps1 -Block '106' -Street 'RIVERVALE WALK' -FlatType '4 ROOM' -FloorAreaSqm 100 -StoreyRange '01 TO 03' -FlatModel 'Model A' -RemainingLeaseYears 71.5 -Output 'reports/phase_1_resale_model/10_my_estimate.json'
```

The JSON shows the selected model estimate, calibrated bounds, comparable count and tier, and up to ten traceable prior sales. Ridge contributions are included only when Ridge is the selected model. `-ValuationMonth 'YYYY-MM'` selects a month; otherwise the current month is used. `-LeaseCommenceYear` can replace `-RemainingLeaseYears`. If both are omitted, or `-FlatModel` is omitted, the command infers a value from earlier same-block sales and shows a warning. Confirm those details for a real flat. The script checks that the database still matches step 09 and that source transactions are no more than 12 months old. A range is an empirical uncertainty band for this pilot, not a guaranteed valuation.
