<#
.SYNOPSIS
  Compress a video for R2 without upscaling or changing the source frame rate.

.DESCRIPTION
  The default H.264 profile is aimed at broad HTML5 playback compatibility.
  H.265 is available when storage savings matter more than universal playback.
  CRF is quality-based: a lower value means higher quality and a larger file.
  CRF=-1 selects a sensible default for the selected codec.

  The output is written to a temporary file first and moved into place only
  after FFmpeg and the output probe both succeed.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$InputFile,

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

  [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Resolve-Executable([string]$Name) {
  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $command) {
    throw "Khong tim thay $Name trong PATH. Hay cai FFmpeg va mo lai PowerShell."
  }
  return $command.Source
}

function Get-JsonProbe([string]$ProbePath, [string]$Path) {
  $raw = & $ProbePath -v error -select_streams v:0 -show_entries 'stream=codec_name,width,height,avg_frame_rate,pix_fmt:format=duration,size' -of json -- $Path 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "FFprobe khong doc duoc file: $Path$([Environment]::NewLine)$($raw -join [Environment]::NewLine)"
  }
  try {
    return ($raw -join [Environment]::NewLine) | ConvertFrom-Json
  } catch {
    throw "FFprobe tra ve du lieu khong hop le cho file: $Path"
  }
}

function Format-Bytes([int64]$Bytes) {
  if ($Bytes -ge 1TB) { return ('{0:N2} TB' -f ($Bytes / 1TB)) }
  if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
  if ($Bytes -ge 1MB) { return ('{0:N2} MB' -f ($Bytes / 1MB)) }
  if ($Bytes -ge 1KB) { return ('{0:N2} KB' -f ($Bytes / 1KB)) }
  return "$Bytes B"
}

$ffmpeg = Resolve-Executable 'ffmpeg'
$ffprobe = Resolve-Executable 'ffprobe'
$inputItem = Get-Item -LiteralPath $InputFile -ErrorAction Stop
if (-not $inputItem.PSIsContainer) {
  if ($inputItem.Length -le 0) { throw "File dau vao rong: $($inputItem.FullName)" }
} else {
  throw "InputFile phai la file video, khong phai thu muc: $InputFile"
}

$sourceProbe = Get-JsonProbe $ffprobe $inputItem.FullName
$sourceVideo = @($sourceProbe.streams) | Select-Object -First 1
if (-not $sourceVideo -or -not $sourceVideo.width -or -not $sourceVideo.height) {
  throw "Khong tim thay video stream trong file: $($inputItem.FullName)"
}

if ([string]::IsNullOrWhiteSpace($OutputFile)) {
  $stem = [IO.Path]::GetFileNameWithoutExtension($inputItem.Name)
  $OutputFile = Join-Path $inputItem.DirectoryName "$stem.compressed.mp4"
}

$outputFullPath = [IO.Path]::GetFullPath($OutputFile)
if ([string]::Equals($outputFullPath, $inputItem.FullName, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'OutputFile phai khac InputFile de tranh mat file goc.'
}
if ([IO.Path]::GetExtension($outputFullPath).ToLowerInvariant() -ne '.mp4') {
  throw 'OutputFile phai co duoi .mp4 de dam bao faststart va playback HTML5.'
}
if ((Test-Path -LiteralPath $outputFullPath) -and -not $Force) {
  throw "File dau ra da ton tai. Dung -Force neu muon thay the: $outputFullPath"
}

$outputDirectory = Split-Path -Parent $outputFullPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$temporaryOutput = Join-Path $outputDirectory (".$([IO.Path]::GetFileName($outputFullPath)).$([guid]::NewGuid().ToString('N')).part.mp4")

if ($Crf -lt 0) {
  if ($Codec -eq 'h264') { $Crf = 18 } else { $Crf = 22 }
}

$encoder = if ($Codec -eq 'h264') { 'libx264' } else { 'libx265' }
$videoFilter = $null
$sourceHeight = [int]$sourceVideo.height
if ($MaxHeight -gt 0 -and $sourceHeight -gt $MaxHeight) {
  # -2 keeps the aspect ratio and guarantees an encoder-friendly even width.
  $videoFilter = "scale=-2:$MaxHeight:flags=lanczos"
}

$ffmpegArgs = @(
  '-hide_banner',
  '-y',
  '-i', $inputItem.FullName,
  '-map', '0:v:0',
  '-map', '0:a?',
  '-map_metadata', '0',
  '-map_chapters', '0',
  '-c:v', $encoder,
  '-preset', $Preset,
  '-crf', $Crf,
  '-pix_fmt', 'yuv420p'
)
if ($Codec -eq 'h265') {
  # Helps Apple players identify HEVC in an MP4 container.
  $ffmpegArgs += @('-tag:v', 'hvc1')
  if ($Crf -eq 0) { $ffmpegArgs += @('-x265-params', 'lossless=1') }
}
if ($videoFilter) { $ffmpegArgs += @('-vf', $videoFilter) }
if ($Audio -eq 'copy') {
  $ffmpegArgs += @('-c:a', 'copy')
} else {
  $ffmpegArgs += @('-c:a', 'aac', '-b:a', ($AudioBitrateKbps.ToString() + 'k'))
}
$ffmpegArgs += @('-movflags', '+faststart', $temporaryOutput)

Write-Host "Compressing: $($inputItem.Name)" -ForegroundColor Cyan
Write-Host "Profile: $Codec / CRF $Crf / preset $Preset / audio $Audio" -ForegroundColor DarkCyan
if ($videoFilter) {
  Write-Host "Resolution: $($sourceVideo.width)x$($sourceVideo.height) -> max height $MaxHeight (downscale only)" -ForegroundColor DarkCyan
} else {
  Write-Host "Resolution: $($sourceVideo.width)x$($sourceVideo.height) (unchanged)" -ForegroundColor DarkCyan
}

try {
  & $ffmpeg @ffmpegArgs
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporaryOutput)) {
    throw 'FFmpeg that bai khi nen video.'
  }

  $outputProbe = Get-JsonProbe $ffprobe $temporaryOutput
  $outputVideo = @($outputProbe.streams) | Select-Object -First 1
  if (-not $outputVideo -or -not $outputVideo.width -or -not $outputVideo.height) {
    throw 'File dau ra khong co video stream hop le.'
  }

  $sourceDuration = [double]($sourceProbe.format.duration)
  $outputDuration = [double]($outputProbe.format.duration)
  if ($sourceDuration -gt 0 -and $outputDuration -gt 0 -and [math]::Abs($sourceDuration - $outputDuration) -gt 1.0) {
    throw ('Thoi luong thay doi qua nhieu: source=' + $sourceDuration + 's output=' + $outputDuration + 's')
  }

  if (Test-Path -LiteralPath $outputFullPath) { Remove-Item -LiteralPath $outputFullPath -Force }
  Move-Item -LiteralPath $temporaryOutput -Destination $outputFullPath

  $inputBytes = [int64]$inputItem.Length
  $outputBytes = [int64](Get-Item -LiteralPath $outputFullPath).Length
  $savedBytes = $inputBytes - $outputBytes
  $reduction = if ($inputBytes -gt 0) { [math]::Round((1 - ($outputBytes / $inputBytes)) * 100, 1) } else { 0 }
  $savedText = if ($savedBytes -ge 0) { "giam $([math]::Round($savedBytes / 1MB, 2)) MB" } else { 'khong giam (nguon da nen rat tot)' }

  Write-Host "COMPRESS_PASS=$outputFullPath" -ForegroundColor Green
  Write-Host "SOURCE_SIZE=$(Format-Bytes $inputBytes) OUTPUT_SIZE=$(Format-Bytes $outputBytes) REDUCTION=$reduction% ($savedText)" -ForegroundColor Green
  Write-Host "OUTPUT_VIDEO=$($outputVideo.width)x$($outputVideo.height) CODEC=$($outputVideo.codec_name)" -ForegroundColor DarkGreen
} catch {
  if (Test-Path -LiteralPath $temporaryOutput) { Remove-Item -LiteralPath $temporaryOutput -Force -ErrorAction SilentlyContinue }
  throw
}
