param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$env:TEMP = 'E:\CodexFiles\.tmp\codex'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:XDG_CACHE_HOME = 'E:\CodexFiles\.cache'
$env:HF_HOME = 'E:\CodexFiles\.cache\huggingface'
$env:PIP_CACHE_DIR = 'E:\CodexFiles\.cache\pip'
$address = 'http://127.0.0.1:8877'
$running = $false
try { $state = Invoke-RestMethod -Uri ($address + '/api/state') -TimeoutSec 2; $running = $null -ne $state.videos } catch { }
if (-not $running) {
    $consolePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $consolePython -PathType Leaf) -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw '项目环境尚未安装。请在项目目录运行 powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1，然后再次启动。'
    }
    Push-Location $projectRoot
    try {
        & $consolePython -c "import fastapi,uvicorn,websocket,cv2,numpy,onnxruntime,rapidocr_onnxruntime; from pathlib import Path; from app.media import FFMPEG,FFPROBE; assert Path(FFMPEG).is_file() and Path(FFPROBE).is_file(), 'FFmpeg or ffprobe not found'"
        if ($LASTEXITCODE -ne 0) { throw '运行组件不完整。请运行 setup.ps1 检查并安装依赖；FFmpeg 的 bin 目录需要加入 PATH。' }
    } finally { Pop-Location }
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'data\logs') -Force | Out-Null
    $priorPidPath = Join-Path $projectRoot 'data\service.pid'
    if (Test-Path -LiteralPath $priorPidPath) {
        $priorServiceId = 0
        if ([int]::TryParse((Get-Content -LiteralPath $priorPidPath -Raw).Trim(), [ref]$priorServiceId)) {
            $priorInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$priorServiceId" -ErrorAction SilentlyContinue
            if ($priorInfo -and $priorInfo.CommandLine -and $priorInfo.CommandLine.IndexOf((Join-Path $projectRoot 'run.py'), [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $priorService = Get-Process -Id $priorServiceId -ErrorAction SilentlyContinue
                if ($priorService -and -not $priorService.WaitForExit(15000)) { throw '助手仍在退出，请关闭正在播放的预览页面后重试。' }
            }
        }
    }
    $scriptPath = Join-Path $projectRoot 'run.py'
    $service = Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $scriptPath + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $service.Id | Set-Content -LiteralPath (Join-Path $projectRoot 'data\service.pid')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try { $state = Invoke-RestMethod -Uri ($address + '/api/state') -TimeoutSec 2; $running = $true; break } catch { }
    }
}
if (-not $running) { throw '素材助手未能启动，请查看 data\logs\server.log' }
if (-not $NoBrowser) { Start-Process $address }

