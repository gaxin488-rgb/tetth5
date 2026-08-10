$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host '=== CINEZERO: DEPLOY DU LIEU MAU ===' -ForegroundColor Cyan
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Chua co Node.js. Cai Node.js LTS roi chay lai.' }
npm install
npm run check
npx wrangler login
npx wrangler deploy
Write-Host 'DEPLOY_PASS: Website da chay tren ten-mien-mien-phi.workers.dev' -ForegroundColor Green
