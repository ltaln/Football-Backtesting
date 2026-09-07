$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    $PythonArgs = @('-3')
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        $PythonArgs = @()
    }
    if (-not $PythonCommand) {
        throw '未找到 Python 3.10+。请先安装 Python，然后重新运行。'
    }
    & $PythonCommand.Source @PythonArgs -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        throw 'Python 虚拟环境创建失败。请确认 Python 3.10+ 可正常运行。'
    }
    & $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        throw '依赖安装失败，请检查网络后重新运行。'
    }
}

$script:HH520ProjectRoot = $ProjectRoot
$script:HH520Python = $VenvPython
