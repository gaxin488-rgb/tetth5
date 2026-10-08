<#
.SYNOPSIS
  Compress a local video and upload the result to one R2 key.

.DESCRIPTION
  This is the safe one-command path for large videos: encoding runs locally
  with FFmpeg, then upload-r2.ps1 applies the existing R2 storage guard before
  putting the MP4 into the configured bucket.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$InputFile,

  [Parameter(Mandatory = $true, Position = 1)]
  [string]$Key,

  [string]$OutputFile,

  [ValidateSet('h264', 'h265')]
  [string]$Codec = 'h264',

  [ValidateRange(-1, 51)]
  [int]$Crf = -1,

  [ValidateSet('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow')]
  [string]$Preset = 'slow',

  [ValidateRange(0, 4320)]
  [int]$MaxHeight = 0,

  [ValidateSet('aac', 'copy')]
  [string]$Audio = 'aac',

  [ValidateRange(64, 320)]
  [int]$AudioBitrateKbps = 160,

  [string]$SiteUrl,
  [switch]$SkipStorageGuard,
  [switch]$AutoSubtitle,
  [string]$Slug,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$compressScript = Join-Path $PSScriptRoot 'compress-video.ps1'
$uploadScript = Join-Path $PSScriptRoot 'upload-r2.ps1'

if (-not (Test-Path -LiteralPath $compressScript)) { throw "Khong tim thay script nen: $compressScript" }
if (-not (Test-Path -LiteralPath $uploadScript)) { throw "Khong tim thay script upload: $uploadScript" }

$inputItem = Get-Item -LiteralPath $InputFile -ErrorAction Stop
if ($inputItem.PSIsContainer) { throw "InputFile phai la file video: $InputFile" }
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
  $stem = [IO.Path]::GetFileNameWithoutExtension($inputItem.Name)
  $OutputFile = Join-Path $inputItem.DirectoryName "$stem.compressed.mp4"
}

$powershellCommand = Get-Command powershell -ErrorAction SilentlyContinue
if (-not $powershellCommand) { $powershellCommand = Get-Command pwsh -ErrorAction SilentlyContinue }
if (-not $powershellCommand) { throw 'Khong tim thay PowerShell de chay pipeline.' }
$powershell = $powershellCommand.Source

$compressArgs = @(
  '-NoProfile',
  '-ExecutionPolicy', 'Bypass',
  '-File', $compressScript,
  '-InputFile', $inputItem.FullName,
  '-OutputFile', $OutputFile,
  '-Codec', $Codec,
  '-Crf', $Crf,
  '-Preset', $Preset,
  '-MaxHeight', $MaxHeight,
  '-Audio', $Audio,
  '-AudioBitrateKbps', $AudioBitrateKbps
)
if ($Force) { $compressArgs += '-Force' }

& $powershell @compressArgs
if ($LASTEXITCODE -ne 0) { throw 'COMPRESS_FAIL' }

$uploadArgs = @(
  '-NoProfile',
  '-ExecutionPolicy', 'Bypass',
  '-File', $uploadScript,
  '-File', [IO.Path]::GetFullPath($OutputFile),
  '-Key', $Key,
  '-ContentType', 'video/mp4'
)
if ($SiteUrl) { $uploadArgs += @('-SiteUrl', $SiteUrl) }
if ($SkipStorageGuard) { $uploadArgs += '-SkipStorageGuard' }
if ($AutoSubtitle) { $uploadArgs += '-AutoSubtitle' }
if ($Slug) { $uploadArgs += @('-Slug', $Slug) }

& $powershell @uploadArgs
if ($LASTEXITCODE -ne 0) { throw 'UPLOAD_FAIL' }

Write-Host "PIPELINE_PASS key=$Key file=$([IO.Path]::GetFullPath($OutputFile))" -ForegroundColor Green
