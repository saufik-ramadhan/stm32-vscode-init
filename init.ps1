# Thin wrapper so Windows users can run .\init.ps1 instead of invoking python directly.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }

if (-not $python) {
    Write-Error "Python 3 not found on PATH. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
}

& $python.Source "$ScriptDir\stm32_vscode_init.py" @args
exit $LASTEXITCODE
