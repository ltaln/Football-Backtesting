param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BacktestCommand)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
if (-not $BacktestCommand) {
    $BacktestCommand = @(Read-Host '请输入回测命令')
}
Set-Location $HH520ProjectRoot
& $HH520Python cli.py @BacktestCommand
exit $LASTEXITCODE
