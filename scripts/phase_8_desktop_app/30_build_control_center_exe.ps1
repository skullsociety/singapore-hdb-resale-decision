$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$App = Join-Path $PSScriptRoot "30_control_center.py"
$Output = Join-Path $ProjectRoot "dist\control-center"
$Work = Join-Path $ProjectRoot ".local\pyinstaller"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found. Run Step 03 setup first."
}

& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run '.\.venv\Scripts\python.exe -m pip install pyinstaller' once, then retry."
}

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name PropertyProjectControlCenter `
    --distpath $Output --workpath $Work --specpath $Work $App
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built: $Output\PropertyProjectControlCenter.exe"
Write-Host "Keep the executable inside this project or set PROPERTY_PROJECT_ROOT before opening it."
