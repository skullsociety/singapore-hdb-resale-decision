[CmdletBinding()]
param(
    [int]$RequestsPerMinute = 240,
    [int]$Workers = 8
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$extractor = Join-Path $scriptDirectory '02_extract_onemap_locations.py'

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $pythonCommand) {
    $pythonExecutable = $pythonCommand.Source
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        $pythonExecutable = $bundledPython
    } else {
        throw 'Python 3 was not found. Install Python 3 or run this project through Codex Desktop.'
    }
}

$tokenWasProvided = -not [string]::IsNullOrWhiteSpace($env:ONEMAP_TOKEN)
$tokenBstr = [IntPtr]::Zero
if (-not $tokenWasProvided) {
    $secureToken = Read-Host 'Paste your OneMap access token' -AsSecureString
    $tokenBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $env:ONEMAP_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenBstr)
}

try {
    & $pythonExecutable $extractor --requests-per-minute $RequestsPerMinute --workers $Workers
    exit $LASTEXITCODE
} finally {
    if ($tokenBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenBstr)
    }
    if (-not $tokenWasProvided) {
        Remove-Item Env:\ONEMAP_TOKEN -ErrorAction SilentlyContinue
    }
}
