$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host '=== CINEZERO: TAO D1 + R2 ===' -ForegroundColor Cyan
npm install
npx wrangler login
Write-Host "`n[1/4] Tao database D1..." -ForegroundColor Yellow
npx wrangler d1 create cinezero-db
Write-Host "`nSao chep file wrangler.full.example.jsonc thanh wrangler.full.jsonc, sau do dan database_id vua hien thi vao file." -ForegroundColor Yellow
Copy-Item '.\wrangler.full.example.jsonc' '.\wrangler.full.jsonc' -Force
Write-Host 'File wrangler.full.jsonc da duoc tao. Hay sua DAN_DATABASE_ID_VAO_DAY, roi bam Enter.' -ForegroundColor Yellow
Read-Host
if ((Get-Content '.\wrangler.full.jsonc' -Raw) -match 'DAN_DATABASE_ID_VAO_DAY') { throw 'Ban chua thay database_id trong wrangler.full.jsonc.' }
Write-Host "`n[2/4] Tao bucket R2..." -ForegroundColor Yellow
npx wrangler r2 bucket create cinezero-media
Write-Host "`n[3/4] Tao bang va nap du lieu mau..." -ForegroundColor Yellow
npx wrangler d1 execute cinezero-db --remote --file=./schema.sql --config=./wrangler.full.jsonc --yes
npx wrangler d1 execute cinezero-db --remote --file=./seed.sql --config=./wrangler.full.jsonc --yes
Write-Host "`n[4/4] Tao token quan tri..." -ForegroundColor Yellow
Write-Host 'Nhap chuoi token dai, kho doan. Wrangler se luu thanh secret ADMIN_TOKEN.'
npx wrangler secret put ADMIN_TOKEN --config=./wrangler.full.jsonc
npx wrangler deploy --config=./wrangler.full.jsonc
Write-Host 'FULL_DEPLOY_PASS' -ForegroundColor Green
