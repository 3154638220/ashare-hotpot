$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $projectRoot
if (-not $env:ASHARE_HOTPOT_DATA_DIR) {
    $env:ASHARE_HOTPOT_DATA_DIR = Join-Path $projectRoot "data"
}
python -m ashare_hotpot
