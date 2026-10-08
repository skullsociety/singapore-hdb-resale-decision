[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$experiment = Join-Path $PSScriptRoot '08_evaluate_hybrid.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

& $python $experiment --project-root $projectRoot
exit $LASTEXITCODE
