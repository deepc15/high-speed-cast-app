<#
.SYNOPSIS
    Sets up the virtualenv if needed, then runs the HSCast CLI.

.EXAMPLE
    .\run.ps1 doctor
    .\run.ps1 mirror
    .\run.ps1 desktop --bitrate 20M --fps 60
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$venv = Join-Path $PSScriptRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host 'Creating .venv ...' -ForegroundColor Cyan
    # Prefer 3.12/3.13: av and dxcam wheels lag behind the newest Python.
    $launcher = (Get-Command py -ErrorAction SilentlyContinue)
    $created = $false
    if ($launcher) {
        foreach ($version in @('3.13', '3.12', '3.11')) {
            & py "-$version" -m venv $venv 2>$null
            if ($LASTEXITCODE -eq 0) { $created = $true; break }
        }
    }
    if (-not $created) {
        python -m venv $venv
    }
    & $python -m pip install --upgrade pip --quiet
    Write-Host 'Installing dependencies ...' -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
}

if (-not $CliArgs -or $CliArgs.Count -eq 0) {
    $CliArgs = @('gui')
}

& $python -m hscast @CliArgs
exit $LASTEXITCODE
