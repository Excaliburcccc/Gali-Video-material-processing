param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'launch.ps1') -NoBrowser:$NoBrowser
