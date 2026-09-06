$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
Set-Location $HH520ProjectRoot
Write-Host 'HH520 Insight AI 已启动：http://localhost:8000/docs'
Write-Host '手机同一局域网可访问：http://本机IP:8000/docs'
& $HH520Python -m uvicorn api.backtest_api:app --host 0.0.0.0 --port 8000
