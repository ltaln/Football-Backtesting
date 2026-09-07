$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
Set-Location $HH520ProjectRoot
Write-Host 'HH520 Insight AI 电脑端已启动：http://127.0.0.1:8000/docs'
& $HH520Python -m uvicorn api.backtest_api:app --host 127.0.0.1 --port 8000
