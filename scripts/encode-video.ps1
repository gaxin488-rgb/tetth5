param(
  [Parameter(Mandatory=$true)][string]$InputFile,
  [Parameter(Mandatory=$true)][string]$Slug,
  [int]$Height = 720,
  [int]$Crf = 24,
  [ValidateSet('ultrafast','superfast','veryfast','faster','fast','medium','slow')][string]$Preset = 'veryfast'
)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$OutDir=Join-Path $Root 'content\encoded'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'Khong tim thay ffmpeg trong PATH.' }
$Out=Join-Path $OutDir "$Slug-720p.mp4"
ffmpeg -y -i $InputFile -vf "scale=-2:$Height" -c:v libx264 -preset $Preset -crf $Crf -c:a aac -b:a 128k -movflags +faststart $Out
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg that bai.' }
Write-Host "ENCODE_PASS=$Out" -ForegroundColor Green
