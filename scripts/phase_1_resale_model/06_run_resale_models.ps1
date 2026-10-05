[CmdletBinding()]
param(
    [switch]$SkipCatBoost
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $projectRoot 'requirements.txt'
$trainer = Join-Path $PSScriptRoot '06_train_resale_models.py'

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Project Python environment is missing. Run .\scripts\phase_1_resale_model\03_run_duckdb_build.ps1 first.'
}

& $venvPython -c 'import duckdb, sklearn, joblib, numpy'
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("catboost") else 1)' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython -c 'import catboost' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'CatBoost could not be loaded in this Python environment. The workflow will run its scikit-learn quantile alternative and remaining models.'
}

$trainerArgs = @('--project-root', $projectRoot)
if ($SkipCatBoost) { $trainerArgs += '--skip-catboost' }
& $venvPython $trainer @trainerArgs
exit $LASTEXITCODE
