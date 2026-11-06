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
    # Each TestClient lifespan closes the service executors. Keep API suites in
    # independent processes and libraries, including the batch export fixtures.
    $apiSuites = @('tests/test_server.py', 'tests/test_projects_api.py', 'tests/test_batch_export_api.py')
    $ignoreArgs = @($apiSuites | ForEach-Object { '--ignore=' + $_ })
    & $pythonPath -m pytest tests @ignoreArgs -q -p no:cacheprovider --basetemp (Join-Path $testRunRoot 'pytest-unit')
    $testExitCode = $LASTEXITCODE
    if ($testExitCode -eq 0) {
        foreach ($suite in $apiSuites) {
            $suiteName = [IO.Path]::GetFileNameWithoutExtension($suite)
            $env:FOOTAGE_ROOT = Join-Path $testRunRoot $suiteName
            & $pythonPath -m pytest $suite -q -p no:cacheprovider --basetemp (Join-Path $testRunRoot ('pytest-' + $suiteName))
            $testExitCode = $LASTEXITCODE
            if ($testExitCode -ne 0) { break }
        }
    }
} finally {
    Pop-Location
}
exit $testExitCode
