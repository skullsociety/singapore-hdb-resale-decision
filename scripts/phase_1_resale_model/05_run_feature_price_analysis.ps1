[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot '05_analyse_feature_price_correlations.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'The project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

& $python -c 'import duckdb'
if ($LASTEXITCODE -ne 0) {
    throw 'DuckDB is missing from the project environment. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

& $python $script --project-root $projectRoot
exit $LASTEXITCODE
