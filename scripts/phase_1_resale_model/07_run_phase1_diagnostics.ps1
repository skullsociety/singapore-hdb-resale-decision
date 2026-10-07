[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$diagnostics = Join-Path $PSScriptRoot '07_phase1_diagnostics.py'

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

Write-Output "Running diagnostics script: $diagnostics"
& $venvPython $diagnostics --project-root $projectRoot
exit $LASTEXITCODE
