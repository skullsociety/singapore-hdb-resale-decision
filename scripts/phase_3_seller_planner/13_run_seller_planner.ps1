[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputFile,
    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot '13_seller_proceeds_planner.py'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}
$args = @($script, '--input', $InputFile)
if ($OutputFile) { $args += @('--output', $OutputFile) }
& $python @args
exit $LASTEXITCODE
