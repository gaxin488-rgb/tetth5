param(
  [string]$PythonLauncher = 'py',
  [string]$PythonVersion = '3.13'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$venv = Join-Path (Get-Location) '.venv-subtitles'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
  & $PythonLauncher "-$PythonVersion" -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw "Khong tao duoc virtualenv bang Python $PythonVersion." }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Khong cap nhat duoc pip.' }
& $python -m pip install -r .\requirements-free.txt
if ($LASTEXITCODE -ne 0) { throw 'Cai dat model local that bai.' }
Write-Host 'FREE_SUBTITLE_SETUP_PASS' -ForegroundColor Green
Write-Host "Python: $python"
Write-Host 'Khong can khoa API AI. Diarization can mot Hugging Face read token mien phi.'
