$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Chua cai Git.' }
if (-not (Test-Path '.git')) { git init; git branch -M main }
git add .
git commit -m 'Initial CineZero web'
Write-Host 'Da tao commit. Tao repository GitHub trong trinh duyet, sau do chay:' -ForegroundColor Green
Write-Host 'git remote add origin https://github.com/USERNAME/cinezero-web.git'
Write-Host 'git push -u origin main'
