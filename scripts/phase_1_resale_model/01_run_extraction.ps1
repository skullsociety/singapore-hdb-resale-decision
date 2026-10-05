[CmdletBinding()]
param(
    [switch]$ForceDownload
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$extractor = Join-Path $scriptDirectory '01_extract_data_gov_sg.py'

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

$arguments = @($extractor)
if ($ForceDownload) {
    $arguments += '--force'
}

& $pythonExecutable @arguments
exit $LASTEXITCODE
