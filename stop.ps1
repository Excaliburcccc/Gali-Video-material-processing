$ErrorActionPreference = 'Stop'
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8877/api/shutdown' -Method Post -TimeoutSec 10 | Out-Null
} catch {
    Write-Host $_.Exception.Message
    Read-Host 'Press Enter to close'
}
