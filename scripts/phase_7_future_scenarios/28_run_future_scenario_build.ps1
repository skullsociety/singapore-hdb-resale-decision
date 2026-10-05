$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Project Python environment not found. Run an earlier project setup script first."
}

& $Python (Join-Path $PSScriptRoot "28_build_future_scenario_evidence.py") --project-root $ProjectRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
