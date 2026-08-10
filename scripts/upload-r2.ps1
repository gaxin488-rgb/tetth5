param(
  [Parameter(Mandatory=$true)][string]$File,
  [Parameter(Mandatory=$true)][string]$Key,
  [string]$ContentType = 'video/mp4',
  [switch]$AutoSubtitle,
  [string]$Slug,
  [string]$SiteUrl
)
$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path $File)) { throw "Khong tim thay file: $File" }
if (-not (Test-Path '.\wrangler.full.jsonc')) { throw 'Chua co wrangler.full.jsonc. Hay chay 02-tao-cloud-full.ps1.' }
npx wrangler r2 object put "cinezero-media/$Key" --file="$File" --content-type="$ContentType" --cache-control='public, max-age=31536000, immutable' --remote --config=./wrangler.full.jsonc
if ($LASTEXITCODE -ne 0) { throw 'UPLOAD_FAIL' }
Write-Host "UPLOAD_PASS video_key=$Key" -ForegroundColor Green
if ($AutoSubtitle) {
  if (-not $Slug) { throw 'AutoSubtitle can yeu -Slug.' }
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Khong tim thay Node.js de chay auto subtitle.' }
  $autoArgs = @('scripts/auto-sub.mjs','--input',$File,'--slug',$Slug)
  if ($SiteUrl) { $autoArgs += @('--site-url',$SiteUrl) }
  if ($env:ADMIN_TOKEN) { $autoArgs += @('--admin-token',$env:ADMIN_TOKEN) }
  & node @autoArgs
  if ($LASTEXITCODE -ne 0) { throw 'AUTO_SUB_FAIL' }
}
