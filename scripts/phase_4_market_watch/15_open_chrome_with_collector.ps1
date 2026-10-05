[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$receiver = Join-Path $PSScriptRoot '15_receive_propertyguru_browser.py'
$healthUrl = 'http://127.0.0.1:8771/health'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run the DuckDB build setup first.'
}

$chrome = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
    throw 'Google Chrome was not found in a standard installation location.'
}

function Test-Receiver {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.status -eq 'ready' -and $health.database -eq 'listings.db'
    } catch {
        return $false
    }
}

if (-not (Test-Receiver)) {
    $arguments = '"{0}" --project-root "{1}" --port 8771' -f $receiver, $projectRoot
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Milliseconds 300
        if (Test-Receiver) { $ready = $true; break }
    }
    if (-not $ready) {
        throw 'Listing receiver did not start. Close DBeaver if it has listings.db open, then try again.'
    }
}

Start-Process -FilePath $chrome | Out-Null
