$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonWindow = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$App = Join-Path $PSScriptRoot "30_control_center.py"

if (Test-Path -LiteralPath $PythonWindow) {
    Start-Process -FilePath $PythonWindow -ArgumentList ('"{0}"' -f $App) -WorkingDirectory $ProjectRoot
} elseif (Test-Path -LiteralPath $Python) {
    & $Python $App
} else {
    throw "Project Python environment not found. Run Step 03 setup first."
}
