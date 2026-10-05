[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Block,
    [Parameter(Mandatory = $true)][string]$Street,
    [Parameter(Mandatory = $true)][string]$FlatType,
    [Parameter(Mandatory = $true)][double]$FloorAreaSqm,
    [Parameter(Mandatory = $true)][string]$StoreyRange,
    [string]$FlatModel,
    [int]$LeaseCommenceYear,
    [double]$RemainingLeaseYears,
    [string]$ValuationMonth,
    [string]$Output
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot '10_predict_flat.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

$arguments = @($script, '--project-root', $projectRoot, '--block', $Block,
    '--street', $Street, '--flat-type', $FlatType, '--floor-area-sqm', $FloorAreaSqm,
    '--storey-range', $StoreyRange)
if ($FlatModel) { $arguments += @('--flat-model', $FlatModel) }
if ($PSBoundParameters.ContainsKey('LeaseCommenceYear')) { $arguments += @('--lease-commence-year', $LeaseCommenceYear) }
if ($PSBoundParameters.ContainsKey('RemainingLeaseYears')) { $arguments += @('--remaining-lease-years', $RemainingLeaseYears) }
if ($ValuationMonth) { $arguments += @('--valuation-month', $ValuationMonth) }
if ($Output) { $arguments += @('--output', $Output) }

& $python @arguments
exit $LASTEXITCODE
