$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$env:TEMP = Join-Path $projectRoot 'work'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'work\pip-cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'work\pyinstaller-cache'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$portableDir = Join-Path $projectRoot 'dist\YouTubeClipper'
$sessionPath = Join-Path $portableDir 'data\session.json'
if (Test-Path -LiteralPath $sessionPath) {
    $sessionInfo = Get-Content -Raw -LiteralPath $sessionPath | ConvertFrom-Json
    $runningApp = Get-Process -Id $sessionInfo.pid -ErrorAction SilentlyContinue
    if ($runningApp -and $runningApp.Path -in @((Join-Path $portableDir 'YouTubeClipper.exe'),(Join-Path $portableDir 'OnlineVideoClipper.exe'))) { throw 'Exit the portable app before rebuilding.' }
}
if (-not (Test-Path -LiteralPath $pythonExe)) { python -m venv (Join-Path $projectRoot '.venv') }
& $pythonExe -m pip install -r (Join-Path $projectRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed' }
foreach ($toolName in @('yt-dlp\yt-dlp.exe','ffmpeg.exe','ffprobe.exe','deno.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "tools\$toolName"))) { throw 'Run setup.ps1 first' }
}
$stagingRoot = Join-Path $projectRoot 'work\package'
& $pythonExe (Join-Path $projectRoot 'scripts\make_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
& $pythonExe -m PyInstaller --noconfirm --clean --onedir --windowed --name OnlineVideoClipper --icon (Join-Path $projectRoot 'work\clipper.ico') --distpath $stagingRoot --workpath (Join-Path $projectRoot 'work\build') --specpath (Join-Path $projectRoot 'work') --add-data "$(Join-Path $projectRoot 'web');web" (Join-Path $projectRoot 'app\launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
$stagedApp = Join-Path $stagingRoot 'OnlineVideoClipper'
Copy-Item -LiteralPath (Join-Path $projectRoot 'tools') -Destination $stagedApp -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot '使用说明.txt') -Destination $stagedApp -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY.md') -Destination $stagedApp -Force
New-Item -ItemType Directory -Path $portableDir -Force | Out-Null
# Replace only application-owned runtime directories, preserving data/downloads.
foreach ($component in @('_internal','tools')) {
    $componentPath = Join-Path $portableDir $component
    if (Test-Path -LiteralPath $componentPath) {
        $resolvedComponent = (Resolve-Path -LiteralPath $componentPath).Path
        $resolvedProject = (Resolve-Path -LiteralPath $projectRoot).Path
        if (-not $resolvedComponent.StartsWith($resolvedProject+'\',[StringComparison]::OrdinalIgnoreCase) -or ((Get-Item -LiteralPath $componentPath).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe runtime directory' }
        Remove-Item -LiteralPath $resolvedComponent -Recurse -Force
    }
}
Get-ChildItem -LiteralPath $stagedApp | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $portableDir -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $portableDir 'OnlineVideoClipper.exe') -Destination (Join-Path $portableDir 'YouTubeClipper.exe') -Force
# The archive is made from clean staging, never from local user data or cookies.
Compress-Archive -Path (Join-Path $stagedApp '*') -DestinationPath (Join-Path $projectRoot 'dist\OnlineVideoClipper-portable.zip') -Force
Write-Host "Ready: $portableDir\OnlineVideoClipper.exe"
