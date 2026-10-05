[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputFile,
    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot '11_buyer_cost_planner.py'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}
$arguments = @($script, '--input', $InputFile)
if ($OutputFile) { $arguments += @('--output', $OutputFile) }
& $python @arguments
exit $LASTEXITCODE
