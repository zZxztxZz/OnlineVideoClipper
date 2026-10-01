$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$env:TEMP = Join-Path $projectRoot 'work'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'work\pip-cache'
New-Item -ItemType Directory -Path $env:TEMP,(Join-Path $projectRoot 'tools') -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) { python -m venv (Join-Path $projectRoot '.venv') }
function Fetch-File($Url, $Path) {
    Write-Host "Downloading $(Split-Path $Path -Leaf)"
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $projectRoot 'scripts\fetch.py') $Url $Path
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
}
$toolsDir = Join-Path $projectRoot 'tools'
Fetch-File 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_win.zip' (Join-Path $env:TEMP 'yt-dlp_win.zip')
Fetch-File 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS' (Join-Path $toolsDir 'SHA2-256SUMS')
$expected = ((Get-Content (Join-Path $toolsDir 'SHA2-256SUMS') | Where-Object { $_ -match '\s+\*?yt-dlp_win\.zip$' }) -split '\s+')[0]
if (-not $expected -or (Get-FileHash (Join-Path $env:TEMP 'yt-dlp_win.zip') -Algorithm SHA256).Hash -ne $expected) { throw 'yt-dlp checksum mismatch' }
Expand-Archive -LiteralPath (Join-Path $env:TEMP 'yt-dlp_win.zip') -DestinationPath (Join-Path $toolsDir 'yt-dlp') -Force
Fetch-File 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' (Join-Path $env:TEMP 'deno.zip')
Expand-Archive -LiteralPath (Join-Path $env:TEMP 'deno.zip') -DestinationPath $toolsDir -Force
Fetch-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' (Join-Path $env:TEMP 'ffmpeg.zip')
Fetch-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' (Join-Path $env:TEMP 'ffmpeg.sha256')
$ffmpegExpected = (Get-Content -Raw -LiteralPath (Join-Path $env:TEMP 'ffmpeg.sha256')).Trim()
if ((Get-FileHash -LiteralPath (Join-Path $env:TEMP 'ffmpeg.zip') -Algorithm SHA256).Hash -ne $ffmpegExpected) { throw 'FFmpeg checksum mismatch' }
$ffmpegExtract = Join-Path $env:TEMP 'ffmpeg-distribution'
Expand-Archive -LiteralPath (Join-Path $env:TEMP 'ffmpeg.zip') -DestinationPath $ffmpegExtract -Force
foreach ($toolName in @('ffmpeg.exe','ffprobe.exe')) {
    $source = Get-ChildItem -LiteralPath $ffmpegExtract -Filter $toolName -Recurse | Select-Object -First 1
    if (-not $source) { throw "Missing $toolName" }
    Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $toolsDir $toolName) -Force
}
New-Item -ItemType Directory -Path (Join-Path $toolsDir 'licenses') -Force | Out-Null
Get-ChildItem -LiteralPath $ffmpegExtract -File -Recurse | Where-Object { $_.Name -match 'LICENSE|COPYING' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $toolsDir 'licenses') -Force }
Fetch-File 'https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/THIRD_PARTY_LICENSES.txt' (Join-Path $toolsDir 'licenses\yt-dlp-third-party.txt')
Fetch-File 'https://raw.githubusercontent.com/denoland/deno/main/LICENSE.md' (Join-Path $toolsDir 'licenses\deno.txt')
$manifest = Get-ChildItem -LiteralPath $toolsDir -Filter '*.exe' -Recurse | ForEach-Object { [PSCustomObject]@{ name=$_.FullName.Substring($toolsDir.Length+1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash; bytes=$_.Length } }
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $toolsDir 'manifest.json') -Encoding utf8
& (Join-Path $toolsDir 'yt-dlp\yt-dlp.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'yt-dlp runtime check failed' }
& (Join-Path $toolsDir 'deno.exe') --version
Write-Host 'Runtime components ready.'
