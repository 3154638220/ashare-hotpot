param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $projectRoot

if (-not $SkipDependencyInstall) {
    python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "Python dependencies failed to install." }
}

$targets = @(
    (Join-Path $projectRoot "build"),
    (Join-Path $projectRoot "dist\AshareHotPot"),
    (Join-Path $projectRoot "dist\installer")
)
foreach ($target in $targets) {
    $fullTarget = [IO.Path]::GetFullPath($target)
    if (-not $fullTarget.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove a path outside the project: $fullTarget"
    }
    if (Test-Path -LiteralPath $fullTarget) {
        Remove-Item -LiteralPath $fullTarget -Recurse -Force
    }
}

python -m PyInstaller --noconfirm ashare_hotpot.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

if ($SkipInstaller) {
    Write-Host "Application directory created: $projectRoot\dist\AshareHotPot"
    exit 0
}

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $knownPaths = @(@(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if ($knownPaths.Count -gt 0) {
        $isccPath = [string]$knownPaths[0]
    } else {
        throw "Inno Setup 6 was not found. Install it, or rerun with -SkipInstaller."
    }
} else {
    $isccPath = $iscc.Source
}

& $isccPath (Join-Path $projectRoot "installer\AshareHotPot.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
Write-Host "Installer created in: $projectRoot\dist\installer"
