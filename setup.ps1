param(
    [string]$PythonPath,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$env:TEMP = 'E:\CodexFiles\.tmp\codex'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:XDG_CACHE_HOME = 'E:\CodexFiles\.cache'
$env:HF_HOME = 'E:\CodexFiles\.cache\huggingface'
$env:PIP_CACHE_DIR = 'E:\CodexFiles\.cache\pip'
$env:MPLCONFIGDIR = 'E:\CodexFiles\.cache\matplotlib'
foreach ($folder in @($env:TEMP, $env:XDG_CACHE_HOME, $env:HF_HOME, $env:PIP_CACHE_DIR, $env:MPLCONFIGDIR)) {
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
}

function Assert-PythonVersion([string]$Executable) {
    & $Executable -c "import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,13) and struct.calcsize('P') == 8 else 1)"
    if ($LASTEXITCODE -ne 0) { throw '此依赖清单使用 64 位 Python 3.13。请安装该版本后重试，或通过 -PythonPath 指定它。' }
}

# Match app/media.py: use PATH first, then the existing local installation.
$ffmpegCommand = Get-Command ffmpeg.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$ffmpegPath = if ($ffmpegCommand) { $ffmpegCommand.Source } else { 'D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe' }
$ffprobeCommand = Get-Command ffprobe.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$ffprobePath = if ($ffprobeCommand) { $ffprobeCommand.Source } else { Join-Path (Split-Path -Parent $ffmpegPath) 'ffprobe.exe' }
foreach ($binary in @($ffmpegPath, $ffprobePath)) {
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
        throw '未找到 FFmpeg 和 ffprobe。请先安装 FFmpeg，将其 bin 目录加入 PATH，然后重新打开终端运行 setup.ps1。此脚本不会自动下载 FFmpeg。'
    }
    & $binary -version *> $null
    if ($LASTEXITCODE -ne 0) { throw "无法运行视频组件：$binary" }
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if ($CheckOnly) { throw '尚未创建项目环境。请先运行 setup.ps1（不带 -CheckOnly）。' }
    if (-not $PythonPath) {
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($launcher) {
            $PythonPath = (& $launcher.Source -3.13 -c 'import sys; print(sys.executable)' 2>$null)
            if ($LASTEXITCODE -ne 0) { $PythonPath = $null }
        }
        if (-not $PythonPath) {
            $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($pythonCommand -and $pythonCommand.Source -notmatch '\\WindowsApps\\') { $PythonPath = $pythonCommand.Source }
        }
    }
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw '未找到 64 位 Python 3.13。安装后重新运行，或使用 setup.ps1 -PythonPath "Python 的完整路径"。'
    }
    Assert-PythonVersion $PythonPath
    & $PythonPath -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建项目环境失败。请检查上方错误。' }
}
Assert-PythonVersion $venvPython

if (-not $CheckOnly) {
    & $venvPython -m pip install --disable-pip-version-check --requirement (Join-Path $projectRoot 'requirements-lock.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。请检查网络或上方错误，再运行 setup.ps1。' }
}
& $venvPython -c "import fastapi,uvicorn,websocket,obsws_python,cv2,numpy,onnxruntime,rapidocr_onnxruntime; from pathlib import Path; p=Path(rapidocr_onnxruntime.__file__).parent/'models'; assert len(list(p.glob('*.onnx'))) >= 2, 'Missing bundled OCR models'"
if ($LASTEXITCODE -ne 0) { throw '运行组件或本地 OCR 模型不完整。请运行 setup.ps1 安装依赖后重试。' }
foreach ($folder in @('data', 'data\logs', 'data\thumbnails', 'data\proxies', 'exports', 'recordings')) {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot $folder) -Force | Out-Null
}
Write-Output '环境检查通过。双击“启动素材助手.cmd”即可使用。'
Write-Output 'OBS 快捷标记需在本机单独配置连接；本脚本不会修改 OBS。'
