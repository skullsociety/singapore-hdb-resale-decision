# Baseline models

Run the baseline evaluation from the project root:

```powershell
.\scripts\phase_1_resale_model\04_run_baselines.ps1
```

The script reads `data/property.duckdb`; it does not download data or call
OneMap. Run the DuckDB build first whenever processed source CSVs change.

It creates two deliberately simple, non-machine-learning benchmarks:

| Model | Prediction method |
|---|---|
| `recent_flat_type_median_24m` | Median of earlier same-town transactions of the same flat type from the previous 24 months. |
| `comparable_sales_v1` | Median of earlier recent sales that first match the same block, then nearby similar flats, then similar flats in the same town, with a documented fallback. |

For every validation or test transaction, both models may use only records from
earlier calendar months. Records in the same month are withheld together because
the source does not contain a sale date. This prevents future-price leakage.

Outputs:

| Output | Contents |
|---|---|
| DuckDB `model_registry` | The two baseline definitions. |
| DuckDB `model_predictions` | One historical prediction per baseline and validation/test transaction. |
| DuckDB `baseline_metrics` | Error and estimate-range metrics by model and split. |
| `data/model_ready/baseline_predictions.csv` | Portable copy of prediction-level results. |
| `reports/phase_1_resale_model/04_baseline_metrics.csv` | Easy-to-open model comparison. |
| `reports/phase_1_resale_model/04_baseline_metrics.json` | Machine-readable run summary. |

The comparable-sales model is the product-facing starting point. The broad
flat-type median is a control: a later machine-learning model must materially
outperform both models on the untouched test period before it is adopted.
