[CmdletBinding()]
param([int]$Port = 8788)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot '12_buyer_web_form.py'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}
& $python $script --project-root $projectRoot --port $Port
exit $LASTEXITCODE
