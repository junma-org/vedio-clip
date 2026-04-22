param(
    [string]$Destination = (Get-Location).Path,
    [string]$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

$ErrorActionPreference = "Stop"

$destinationPath = (Resolve-Path -LiteralPath $Destination).Path
$ffmpegPath = Join-Path $destinationPath "ffmpeg.exe"
$ffprobePath = Join-Path $destinationPath "ffprobe.exe"

if ((Test-Path -LiteralPath $ffmpegPath) -and (Test-Path -LiteralPath $ffprobePath)) {
    Write-Host "OK: ffmpeg.exe and ffprobe.exe already exist."
    exit 0
}

$workDir = Join-Path $destinationPath "downloads\ffmpeg"
$zipPath = Join-Path $workDir "ffmpeg.zip"

if (Test-Path -LiteralPath $workDir) {
    Remove-Item -LiteralPath $workDir -Recurse -Force
}

New-Item -ItemType Directory -Path $workDir -Force | Out-Null

Write-Host "Downloading FFmpeg essentials build..."
Invoke-WebRequest -Uri $Url -OutFile $zipPath

Write-Host "Extracting FFmpeg..."
Expand-Archive -Path $zipPath -DestinationPath $workDir -Force

$binDir = Get-ChildItem -Path $workDir -Directory -Filter "ffmpeg-*" |
    ForEach-Object { Join-Path $_.FullName "bin" } |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ "ffmpeg.exe") } |
    Select-Object -First 1

if (-not $binDir) {
    throw "Extracted FFmpeg bin directory was not found."
}

Copy-Item -LiteralPath (Join-Path $binDir "ffmpeg.exe") -Destination $ffmpegPath -Force
Copy-Item -LiteralPath (Join-Path $binDir "ffprobe.exe") -Destination $ffprobePath -Force

Remove-Item -LiteralPath $workDir -Recurse -Force

Write-Host "OK: ffmpeg.exe and ffprobe.exe were downloaded."
