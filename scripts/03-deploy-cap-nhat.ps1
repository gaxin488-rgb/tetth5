$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$config = if (Test-Path '.\wrangler.full.jsonc') { '.\wrangler.full.jsonc' } else { '.\wrangler.jsonc' }
npm install
npm run check
npx wrangler deploy --config=$config
