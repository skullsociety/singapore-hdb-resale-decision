[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$virtualEnvironment = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $virtualEnvironment 'Scripts\python.exe'
$requirements = Join-Path $projectRoot 'requirements.txt'
$builder = Join-Path $PSScriptRoot '03_build_duckdb.py'

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $bootstrapPython = $pythonCommand.Source
    } else {
        $bootstrapPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (-not (Test-Path -LiteralPath $bootstrapPython)) {
            throw 'Python 3 was not found. Install Python 3 or run through Codex Desktop.'
        }
    }
    & $bootstrapPython -m venv $virtualEnvironment
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython -c 'import duckdb'
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython $builder --project-root $projectRoot
exit $LASTEXITCODE
