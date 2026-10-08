# Baseline models

Run the baseline evaluation from the project root:

```powershell
.\scripts\phase_1_resale_model\04_run_baselines.ps1
```

The script reads `data/property.duckdb`; it does not download data or call
OneMap. Run the DuckDB build first whenever processed source CSVs change.

It creates three deliberately simple, non-machine-learning benchmarks:

| Model | Prediction method |
|---|---|
| `recent_flat_type_median_24m` | Median of earlier same-town transactions of the same flat type from the previous 24 months. |
| `comparable_sales_v1` | Median of earlier recent sales that first match the same block, then nearby similar flats, then similar flats in the same town, with a documented fallback. |
| `comparable_sales_recency_weighted_v1` | Weighted median of the same earlier comparables; a sale's weight halves for every additional six months of age. |

For every validation or test transaction, all three methods may use only records from
earlier calendar months. Records in the same month are withheld together because
the source does not contain a sale date. This prevents future-price leakage.

Outputs:

| Output | Contents |
|---|---|
| DuckDB `model_registry` | The three baseline definitions. |
| DuckDB `model_predictions` | One historical prediction per baseline and validation/test transaction. |
| DuckDB `baseline_metrics` | Error and estimate-range metrics by model and split. |
| `data/model_ready/baseline_predictions.csv` | Portable copy of prediction-level results. |
| `reports/phase_1_resale_model/04_baseline_metrics.csv` | Easy-to-open model comparison. |
| `reports/phase_1_resale_model/04_baseline_metrics.json` | Machine-readable run summary. |

Step 04 selects the comparable method with lower validation MAE as the starting
price for model training. Step 09 then compares the selected ML model with that
comparable method on validation and test sales. If ML does not beat it on both,
the comparable method becomes the released estimator, with a calibrated range.
The broad flat-type median remains a simple reference point. Existing test
results have been inspected during development; later untouched sales are
needed to confirm the choice independently.
