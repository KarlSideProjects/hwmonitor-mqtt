param(
    [string]$ProjectRoot = "",
    [string]$SenderExe = "",
    [string]$PythonExe = "python",
    [switch]$UseUv,
    [string]$UvExe = "",
    [string]$UvCacheDir = "",
    [string]$UvPythonInstallDir = "",
    [string]$LhmPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
Set-Location $ProjectRoot

if (-not [string]::IsNullOrWhiteSpace($LhmPath)) {
    $env:LHM_PATH = $LhmPath
}

if (-not [string]::IsNullOrWhiteSpace($SenderExe)) {
    & $SenderExe
    exit $LASTEXITCODE
}

function Resolve-UvExePath {
    param([string]$Candidate)

    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path $Candidate)) {
        return [System.IO.Path]::GetFullPath($Candidate)
    }

    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }

    $known = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe",
        "$env:LocalAppData\Programs\uv\uv.exe"
    )
    foreach ($p in $known) {
        if (Test-Path $p) {
            return $p
        }
    }
    return $null
}

if ($UseUv) {
    $resolvedUvExe = Resolve-UvExePath -Candidate $UvExe
    if (-not $resolvedUvExe) {
        throw "uv executable not found. Re-run install script with EnsureUv=true."
    }

    if ([string]::IsNullOrWhiteSpace($UvCacheDir)) {
        $UvCacheDir = Join-Path $ProjectRoot ".uv-cache"
    }
    if ([string]::IsNullOrWhiteSpace($UvPythonInstallDir)) {
        $UvPythonInstallDir = Join-Path $ProjectRoot ".uv-python"
    }

    New-Item -ItemType Directory -Path $UvCacheDir -Force | Out-Null
    New-Item -ItemType Directory -Path $UvPythonInstallDir -Force | Out-Null

    $env:UV_CACHE_DIR = $UvCacheDir
    $env:UV_PYTHON_INSTALL_DIR = $UvPythonInstallDir

    & $resolvedUvExe run --project $ProjectRoot python -m hwmonitor_mqtt.agents.agent_sender_windows
    exit $LASTEXITCODE
}

& $PythonExe -m hwmonitor_mqtt.agents.agent_sender_windows
exit $LASTEXITCODE
