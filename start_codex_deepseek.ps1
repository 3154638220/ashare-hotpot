[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CodexArguments
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$codexHome = Join-Path $projectRoot ".codex-deepseek"
$configPath = Join-Path $codexHome "config.toml"
$localCodex = Join-Path $projectRoot ".codex-cli\node_modules\.bin\codex.cmd"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Codex config not found: $configPath"
}

if (-not (Test-Path -LiteralPath $localCodex -PathType Leaf)) {
    throw "Project-local Codex CLI was not found. Run: Push-Location .codex-cli; npm install; Pop-Location"
}

$previousCodexHome = $env:CODEX_HOME
$hadApiKey = -not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)
$previousApiKey = $env:DEEPSEEK_API_KEY

# `setx` values are available only to new terminals. Read the persisted user
# value as a fallback so this launcher also works from an already-open shell.
if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}

if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    $secureApiKey = Read-Host "Enter your DeepSeek API key (kept only in this terminal)" -AsSecureString
    $apiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureApiKey)
    try {
        $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apiKeyPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apiKeyPointer)
    }

    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        throw "DEEPSEEK_API_KEY was not provided; startup cancelled."
    }
    $env:DEEPSEEK_API_KEY = $apiKey.Trim()
}
else {
    $env:DEEPSEEK_API_KEY = $env:DEEPSEEK_API_KEY.Trim()
}

# Use a project-specific Codex home so this setup does not change the user's global Codex config.
$env:CODEX_HOME = $codexHome
Set-Location -LiteralPath $projectRoot

Write-Host "Starting Codex CLI: DeepSeek / deepseek-v4-flash" -ForegroundColor Cyan
Write-Host "Project directory: $projectRoot" -ForegroundColor DarkGray

try {
    & $localCodex @CodexArguments
    $exitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $previousCodexHome) {
        Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue
    }
    else {
        $env:CODEX_HOME = $previousCodexHome
    }

    if ($hadApiKey) {
        $env:DEEPSEEK_API_KEY = $previousApiKey
    }
    else {
        Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    }
}

exit $exitCode
