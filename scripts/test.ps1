$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:TEMP = 'E:\CodexFiles\.tmp\codex'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:XDG_CACHE_HOME = 'E:\CodexFiles\.cache'
$env:PYTHONIOENCODING = 'utf-8'
$env:FOOTAGE_TEST = '1'
$testRunRoot = Join-Path $env:TEMP ('footage-tests-' + [guid]::NewGuid().ToString('N'))
$env:FOOTAGE_ROOT = Join-Path $testRunRoot 'library'
New-Item -ItemType Directory -Path $testRunRoot -Force | Out-Null
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.ps1 first.' }
Push-Location $projectRoot
try {
    & $pythonPath -m pytest tests -q -p no:cacheprovider --basetemp (Join-Path $testRunRoot 'pytest')
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $testExitCode
