# Repeatable DuckDB build

From the project root in PowerShell:

```powershell
.\scripts\phase_1_resale_model\03_run_duckdb_build.ps1
```

The launcher creates a local `.venv` on the first run, installs the pinned DuckDB
dependency, and runs `scripts/phase_1_resale_model/03_build_duckdb.py`. Later runs reuse the environment.
This build uses the current CSVs in `data/processed`; it does not call data.gov.sg
or OneMap. Refresh those extracts separately before rebuilding the database.

Outputs:

| File | Purpose |
|---|---|
| `data/property.duckdb` | Trusted source, block location, transaction feature, and build history tables. |
| `data/model_ready/sengkang_transaction_features.parquet` | Portable copy of the transaction feature table. |
| `reports/phase_1_resale_model/03_duckdb_build_summary.json` | Source and table counts, split counts, exclusions, and run ID. |

Each rerun creates a fresh database in a temporary file. The existing database
is replaced only after required columns, unique IDs, joins, coordinates, and
feature row counts pass validation. The build reuses no live API credentials.
Transaction IDs are deterministic for the same input rows, including repeated
identical source rows.

The feature table includes `resale_price` solely as the outcome for later
evaluation and training. Exclude it from model inputs. Its current amenity
distances describe today's locations; historical opening dates have not been
established for every facility. The `dataset_split` column places the latest
12 transaction months in test, the preceding 12 months in validation, and
earlier months in training. The baseline estimator is the next pipeline step.

DuckDB's [Python API](https://duckdb.org/docs/stable/clients/python/overview)
persists tables in a file, and its
[Parquet export](https://duckdb.org/docs/data/parquet) supports the portable
model-ready output.
