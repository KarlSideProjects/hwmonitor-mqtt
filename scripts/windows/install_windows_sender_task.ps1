param(
    [string]$TaskName = "HWMonitorMQTTSender",
    [string]$ProjectRoot = "",
    [string]$PythonExe = "python",
    [bool]$UseUv = $true,
    [string]$UvExe = "",
    [string]$UvCacheDir = "",
    [string]$UvPythonInstallDir = "",
    [bool]$EnsureUv = $true,
    [bool]$EnsureDeps = $true,
    [bool]$EnsureLhm = $true,
    [string]$LhmPath = "",
    [string]$LhmInstallDir = "",
    [string]$LhmDownloadUrl = "",
    [bool]$EnsurePawnIo = $true,
    [switch]$AtStartup,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

if ([string]::IsNullOrWhiteSpace($UvCacheDir)) {
    $UvCacheDir = Join-Path $ProjectRoot ".uv-cache"
}
if ([string]::IsNullOrWhiteSpace($UvPythonInstallDir)) {
    $UvPythonInstallDir = Join-Path $ProjectRoot ".uv-python"
}
if ([string]::IsNullOrWhiteSpace($LhmInstallDir)) {
    $LhmInstallDir = Join-Path $ProjectRoot "third_party\librehardwaremonitor"
}

$StartScript = Join-Path $PSScriptRoot "start_windows_sender.ps1"
if (-not (Test-Path $StartScript)) {
    throw "Cannot find start script: $StartScript"
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

# Functions whose result is assigned must keep progress off the pipeline (Write-Host / Out-Host),
# otherwise the messages become part of the returned path.
function Install-UvIfNeeded {
    $resolved = Resolve-UvExePath -Candidate $UvExe
    if ($resolved) {
        return $resolved
    }
    if (-not $EnsureUv) {
        throw "uv is not available and EnsureUv=false."
    }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Installing uv via winget..."
        & winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements --silent | Out-Host
        $resolved = Resolve-UvExePath -Candidate $UvExe
        if ($resolved) {
            return $resolved
        }
    }

    Write-Host "Installing uv via official install script..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Host
    $resolved = Resolve-UvExePath -Candidate $UvExe
    if ($resolved) {
        return $resolved
    }

    throw "Failed to install uv automatically. Please install uv manually and rerun."
}

function Ensure-ProjectDependencies {
    param(
        [string]$ResolvedUvExe,
        [string]$RootPath,
        [string]$CacheDir,
        [string]$PythonDir
    )

    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null

    $env:UV_CACHE_DIR = $CacheDir
    $env:UV_PYTHON_INSTALL_DIR = $PythonDir

    Write-Output "Running uv sync in project root..."
    & $ResolvedUvExe sync --project $RootPath
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }
}

function Resolve-LhmExePath {
    param(
        [string]$Candidate,
        [string]$InstallDir,
        [string]$RootPath
    )

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Candidate)) {
        $candidates += $Candidate
    }
    $candidates += (Join-Path $InstallDir "LibreHardwareMonitor.exe")
    $candidates += (Join-Path $RootPath "third_party\librehardwaremonitor\LibreHardwareMonitor.exe")
    $candidates += "C:\Program Files\LibreHardwareMonitor\LibreHardwareMonitor.exe"
    $candidates += "C:\Program Files (x86)\LibreHardwareMonitor\LibreHardwareMonitor.exe"
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "LibreHardwareMonitor\LibreHardwareMonitor.exe")
    }

    foreach ($p in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($p) -and (Test-Path $p)) {
            return [System.IO.Path]::GetFullPath($p)
        }
    }

    return $null
}

# Pinned: LHM 0.9.5 silently removed its WMI provider; upgrades must be a reviewed change.
$LhmDefaultUrl = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
$LhmDefaultSha256 = "086D9F1B5A99E643EDC2CFAAAC16051685B551E4C5AC0B32A57C58C0E529C001"

function Ensure-LhmExecutable {
    param(
        [string]$Candidate,
        [string]$InstallDir,
        [string]$RootPath,
        [bool]$InstallIfMissing,
        [string]$DownloadUrl
    )

    $resolved = Resolve-LhmExePath -Candidate $Candidate -InstallDir $InstallDir -RootPath $RootPath
    if ($resolved) {
        return $resolved
    }

    if (-not $InstallIfMissing) {
        return $null
    }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    $tmpRoot = Join-Path $env:TEMP ("lhm-install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
    $zipPath = Join-Path $tmpRoot "LibreHardwareMonitor.zip"
    $extractDir = Join-Path $tmpRoot "extract"

    try {
        $url = if ([string]::IsNullOrWhiteSpace($DownloadUrl)) { $LhmDefaultUrl } else { $DownloadUrl }
        Write-Host "Downloading LibreHardwareMonitor from: $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zipPath
        if ($url -eq $LhmDefaultUrl) {
            $hash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash
            if ($hash -ne $LhmDefaultSha256) {
                throw "LibreHardwareMonitor archive SHA-256 mismatch: $hash"
            }
        }
        else {
            Write-Host "Custom LhmDownloadUrl: SHA-256 not verified."
        }

        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

        $exe = Get-ChildItem -Path $extractDir -Recurse -Filter "LibreHardwareMonitor.exe" | Select-Object -First 1
        if (-not $exe) {
            throw "LibreHardwareMonitor.exe not found after extraction."
        }

        $sourceDir = Split-Path -Parent $exe.FullName

        if (Test-Path $InstallDir) {
            Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        Copy-Item -Path (Join-Path $sourceDir "*") -Destination $InstallDir -Recurse -Force

        $installedExe = Join-Path $InstallDir "LibreHardwareMonitor.exe"
        if (-not (Test-Path $installedExe)) {
            throw "Failed to install LibreHardwareMonitor.exe into $InstallDir"
        }

        return [System.IO.Path]::GetFullPath($installedExe)
    }
    finally {
        if (Test-Path $tmpRoot) {
            Remove-Item -Path $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# LibreHardwareMonitor >= 0.9.5 reads CPU temperatures through the PawnIO driver instead of WinRing0.
$PawnIoUrl = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
$PawnIoSha256 = "1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032"

function Get-PawnIoVersion {
    $key = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        $sub = $key.OpenSubKey("SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO")
        if (-not $sub) { return $null }
        $version = $null
        if ([version]::TryParse([string]$sub.GetValue("DisplayVersion"), [ref]$version)) { return $version }
        return $null
    }
    finally {
        $key.Dispose()
    }
}

function Ensure-PawnIo {
    $installed = Get-PawnIoVersion
    # Same minimum LibreHardwareMonitor's own startup check accepts.
    if ($installed -and $installed -ge [version]"2.0.0") {
        Write-Output "PawnIO $installed already installed."
        return
    }

    $tmpRoot = Join-Path $env:TEMP ("pawnio-install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
    $setup = Join-Path $tmpRoot "PawnIO_setup.exe"
    try {
        Write-Output "Downloading PawnIO from: $PawnIoUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $PawnIoUrl -OutFile $setup
        $hash = (Get-FileHash -Path $setup -Algorithm SHA256).Hash
        if ($hash -ne $PawnIoSha256) {
            throw "PawnIO installer SHA-256 mismatch: $hash"
        }
        $proc = Start-Process -FilePath $setup -ArgumentList "-install", "-silent" -Wait -PassThru
        if ($proc.ExitCode -eq 3010) {
            Write-Output "PawnIO installed; restart Windows to enable CPU temperatures."
        }
        elseif ($proc.ExitCode -ne 0) {
            throw "PawnIO installer failed with exit code $($proc.ExitCode)"
        }
        else {
            Write-Output "PawnIO installed."
        }
    }
    finally {
        Remove-Item -Path $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$resolvedUvExe = $null
if ($UseUv) {
    if ($WhatIf) {
        $resolvedUvExe = Resolve-UvExePath -Candidate $UvExe
        if (-not $resolvedUvExe) {
            $resolvedUvExe = "<uv not found in PATH/known paths>"
        }
    }
    else {
        $resolvedUvExe = Install-UvIfNeeded
        Write-Output "Using uv executable: $resolvedUvExe"

        if ($EnsureDeps) {
            Ensure-ProjectDependencies -ResolvedUvExe $resolvedUvExe -RootPath $ProjectRoot -CacheDir $UvCacheDir -PythonDir $UvPythonInstallDir
        }
    }
}

$resolvedLhmExe = $null
if ($WhatIf) {
    $resolvedLhmExe = Resolve-LhmExePath -Candidate $LhmPath -InstallDir $LhmInstallDir -RootPath $ProjectRoot
    if (-not $resolvedLhmExe -and $EnsureLhm) {
        $resolvedLhmExe = "<LHM will be auto-installed on real run>"
    }
}
else {
    $resolvedLhmExe = Ensure-LhmExecutable -Candidate $LhmPath -InstallDir $LhmInstallDir -RootPath $ProjectRoot -InstallIfMissing $EnsureLhm -DownloadUrl $LhmDownloadUrl
    if ($EnsurePawnIo) {
        Ensure-PawnIo
    }
}

$taskUseUv = $UseUv
$taskPythonExe = $PythonExe
if ($AtStartup -and $UseUv -and $resolvedUvExe -and -not ($resolvedUvExe -like "<*")) {
    $userProfilePath = $env:USERPROFILE
    if (-not [string]::IsNullOrWhiteSpace($userProfilePath) -and $resolvedUvExe.ToLower().StartsWith($userProfilePath.ToLower())) {
        $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $venvPython) -and -not $WhatIf) {
            throw "AtStartup task runs as SYSTEM; uv in user profile is not reliable. Missing venv python: $venvPython"
        }
        $taskUseUv = $false
        $taskPythonExe = $venvPython
    }
}

$argList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$StartScript`"",
    "-ProjectRoot", "`"$ProjectRoot`"",
    "-PythonExe", "`"$taskPythonExe`""
)

if ($taskUseUv -and $resolvedUvExe -and -not ($resolvedUvExe -like "<*")) {
    $argList += "-UseUv"
    $argList += "-UvExe"
    $argList += "`"$resolvedUvExe`""
    $argList += "-UvCacheDir"
    $argList += "`"$UvCacheDir`""
    $argList += "-UvPythonInstallDir"
    $argList += "`"$UvPythonInstallDir`""
}

if ($resolvedLhmExe -and -not ($resolvedLhmExe -like "<*")) {
    $argList += "-LhmPath"
    $argList += "`"$resolvedLhmExe`""
}

if ($WhatIf) {
    Write-Output "WhatIf: would register scheduled task '$TaskName'"
    Write-Output "  ProjectRoot: $ProjectRoot"
    Write-Output "  StartScript: $StartScript"
    Write-Output "  UseUv: $UseUv"
    Write-Output "  UvExe: $resolvedUvExe"
    Write-Output "  UvCacheDir: $UvCacheDir"
    Write-Output "  UvPythonInstallDir: $UvPythonInstallDir"
    Write-Output "  EnsureUv: $EnsureUv"
    Write-Output "  EnsureDeps: $EnsureDeps"
    Write-Output "  EnsureLhm: $EnsureLhm"
    Write-Output "  LhmPath: $resolvedLhmExe"
    Write-Output "  EnsurePawnIo: $EnsurePawnIo (installed: $(Get-PawnIoVersion))"
    Write-Output "  TaskUseUv: $taskUseUv"
    Write-Output "  TaskPythonExe: $taskPythonExe"
    Write-Output "  LhmInstallDir: $LhmInstallDir"
    Write-Output "  AtStartup: $AtStartup"
    Write-Output "  Action: powershell.exe $($argList -join ' ')"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argList -join " ")

if ($AtStartup) {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
}
else {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Output "Scheduled task '$TaskName' installed."
Write-Output "ProjectRoot: $ProjectRoot"
Write-Output "UseUv: $UseUv"
Write-Output "EnsureUv: $EnsureUv"
Write-Output "EnsureDeps: $EnsureDeps"
Write-Output "EnsureLhm: $EnsureLhm"
Write-Output "LhmPath: $resolvedLhmExe"
Write-Output "AtStartup: $AtStartup"
