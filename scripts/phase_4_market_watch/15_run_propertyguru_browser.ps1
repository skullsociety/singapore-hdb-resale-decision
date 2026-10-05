[CmdletBinding()]
param([int]$Port = 8771)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}
& $python (Join-Path $PSScriptRoot '15_receive_propertyguru_browser.py') --project-root $projectRoot --port $Port
exit $LASTEXITCODE
