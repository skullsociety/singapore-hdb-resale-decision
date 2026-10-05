param(
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$app = Join-Path $projectRoot "dashboard\phase_5\24_streamlit_app.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Create .venv and install requirements.txt first."
}

Set-Location $projectRoot
& $python -m streamlit run $app --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false
