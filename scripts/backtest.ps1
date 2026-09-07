param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BacktestCommand)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = 'utf-8'
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
if (-not $BacktestCommand) {
    $BacktestCommand = @(Read-Host '请输入回测命令')
}
Set-Location $HH520ProjectRoot
& $HH520Python cli.py @BacktestCommand
exit $LASTEXITCODE
