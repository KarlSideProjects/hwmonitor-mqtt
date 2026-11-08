$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'hwmonitor-mqtt'
$temp = Join-Path $env:ProgramData ('hwmonitor-mqtt-stage-' + [guid]::NewGuid().ToString('N'))
# Begin source download
function Get-VerifiedSource([string]$WorkDirectory) {
    try {
        $commit = [string](Invoke-RestMethod -Uri 'https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt/commits/master' -Headers @{ 'User-Agent' = 'hwmonitor-mqtt-bootstrap' }).sha
    } catch {
        $refs = Join-Path $WorkDirectory 'source-refs'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/KarlSideProjects/hwmonitor-mqtt.git/info/refs?service=git-upload-pack' -OutFile $refs
        if ((Get-Item -LiteralPath $refs).Length -gt 10485760) { throw 'Invalid source references.' }
        $matches = @([IO.File]::ReadAllText($refs, [Text.Encoding]::ASCII) -split "`n" | Where-Object {
            $_ -cmatch '^([0-9a-f]{4})([0-9a-f]{40}) refs/heads/master$' -and [Convert]::ToInt32($_.Substring(0, 4), 16) -eq $_.Length + 1
        })
        if ($matches.Count -ne 1) { throw 'Invalid source commit.' }
        $commit = $matches[0].Substring(4, 40)
    }
    if ($commit -cnotmatch '^[a-f0-9]{40}$') { throw 'Invalid source commit.' }
    $validator = Join-Path $WorkDirectory 'validate_source_archive.ps1'
    $archive = Join-Path $WorkDirectory 'source.zip'
    $destination = Join-Path $WorkDirectory 'unpacked'
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/KarlSideProjects/hwmonitor-mqtt/$commit/scripts/windows/validate_source_archive.ps1" -OutFile $validator
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/KarlSideProjects/hwmonitor-mqtt/archive/$commit.zip" -OutFile $archive
    $staged = & $validator -Archive $archive -Destination $destination -Source $commit
    return [pscustomobject]@{ Commit = $commit; Path = $staged }
}
# End source download
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this bootstrap from an elevated PowerShell window.' }
if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.Architecture]::X64 -or [IntPtr]::Size -ne 8) { throw 'Only Windows x64 with 64-bit PowerShell is supported.' }
$trustedSids = @('S-1-5-18', 'S-1-5-32-544', 'S-1-3-0', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464', $identity.User.Value)
function Assert-TrustedItem([string]$Path, [bool]$Ancestor, [bool]$Secret = $false) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Untrusted reparse path: $Path" }
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trustedSids) { throw "Untrusted owner: $Path" }
    $mask = [int][Security.AccessControl.FileSystemRights]::Delete -bor [int][Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [int][Security.AccessControl.FileSystemRights]::ChangePermissions -bor [int][Security.AccessControl.FileSystemRights]::TakeOwnership -bor 0x10000000
    if (-not $Ancestor) { $mask = $mask -bor [int][Security.AccessControl.FileSystemRights]::Write -bor 0x40000000 }
    if ($Secret) { $mask = $mask -bor [int][Security.AccessControl.FileSystemRights]::ReadData -bor [int]::MinValue }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($Ancestor -and (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)) { continue }
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trustedSids -and (([int]$rule.FileSystemRights -band $mask) -ne 0)) { throw "Untrusted write access: $Path" }
    }
}
# Begin Python alias check
function Assert-TrustedPythonAlias($Item) {
    $targets = @($Item.Target)
    if ($Item.LinkType -ne 'Junction' -or $targets.Count -ne 1) { throw 'Untrusted Python runtime alias.' }
    $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $root '.uv-python')).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath([string]$targets[0]).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($target), $runtimeRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Untrusted Python runtime alias target.'
    }
    Assert-TrustedItem $target $false
}
# End Python alias check
# End trusted path checks
$ancestor = Split-Path -Parent $root
while ($ancestor) {
    Assert-TrustedItem $ancestor $true
    $next = Split-Path -Parent $ancestor
    if ($next -eq $ancestor) { break }
    $ancestor = $next
}
$lockHandle = $null
if (Test-Path $root) {
    Assert-TrustedItem $root $false
    foreach ($relative in @('.env', '.hwmonitor-mqtt-bootstrap', '.hwmonitor-mqtt-bootstrap-pending',
            '.source-install.lock', 'scripts', 'package', 'hwmonitor_mqtt', '.venv', '.uv-cache',
            '.uv-python', 'third_party', 'pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md')) {
        $path = Join-Path $root $relative
        if (Test-Path -LiteralPath $path) {
            Assert-TrustedItem $path $false ($relative -eq '.env')
            if ((Get-Item -LiteralPath $path -Force).PSIsContainer) {
                Get-ChildItem -LiteralPath $path -Force -Recurse | ForEach-Object {
                    if ($relative -eq '.uv-python' -and (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
                        Assert-TrustedPythonAlias $_
                    } else { Assert-TrustedItem $_.FullName $false }
                }
            }
        }
    }
    $oldInstall = (Test-Path (Join-Path $root 'install.sh')) -and (Test-Path (Join-Path $root 'scripts/windows/install_windows_sender_task.ps1'))
    $compiledInstall = Test-Path (Join-Path $root 'package/sender.exe') -PathType Leaf
    $pendingInstall = (Test-Path (Join-Path $root '.hwmonitor-mqtt-bootstrap-pending')) -and -not (Test-Path (Join-Path $root 'package'))
    if (-not $pendingInstall -and (-not (Test-Path (Join-Path $root '.hwmonitor-mqtt-bootstrap')) -or -not ($oldInstall -or $compiledInstall))) { throw "Refusing to use existing unrecognized directory: $root" }
    if (-not $pendingInstall) {
        $installed = "$(Get-Content -Raw (Join-Path $root '.hwmonitor-mqtt-bootstrap'))".Trim()
        if (-not $installed) { $installed = 'unknown' }
        Write-Output "Installed source: $installed"
    }
    $lockPath = Join-Path $root '.source-install.lock'
    $lockHandle = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
}
try {
    New-Item -ItemType Directory -Path $temp | Out-Null
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -Path $temp -AclObject $acl
    $verified = Get-VerifiedSource -WorkDirectory $temp
    $staged = $verified.Path
    $activator = Join-Path $staged 'scripts/windows/activate_source.ps1'
    if (-not (Test-Path $root)) {
        New-Item -ItemType Directory -Path $root | Out-Null
        $rootAcl = [Security.AccessControl.DirectorySecurity]::new()
        $rootAcl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
            $rootAcl.AddAccessRule($rule)
        }
        Set-Acl -Path $root -AclObject $rootAcl
        New-Item -ItemType File -Path (Join-Path $root '.hwmonitor-mqtt-bootstrap-pending') | Out-Null
    }
    Assert-TrustedItem $root $false
    if (-not $lockHandle) {
        $lockPath = Join-Path $root '.source-install.lock'
        if (Test-Path -LiteralPath $lockPath) { Assert-TrustedItem $lockPath $false }
        $lockHandle = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    }

    $envFile = Join-Path $root '.env'
    if (-not (Test-Path $envFile)) {
        $hostName = Read-Host 'MQTT broker host'
        if ($hostName -notmatch '^[A-Za-z0-9.-]+$') { throw 'Use a DNS name or IPv4 address for MQTT broker host.' }
        $port = Read-Host 'MQTT broker port [1883]'
        if ([string]::IsNullOrWhiteSpace($port)) { $port = '1883' }
        $portNumber = 0
        if (-not [int]::TryParse($port, [ref]$portNumber) -or $portNumber -lt 1 -or $portNumber -gt 65535) { throw 'MQTT port must be between 1 and 65535.' }
        $user = Read-Host 'MQTT username'
        $securePassword = Read-Host 'MQTT password' -AsSecureString
        $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr) }
        if ($user -match '\$\{[^}]*\}' -or $password -match '\$\{[^}]*\}') {
            throw 'MQTT username and password cannot contain ${...} because the existing dotenv reader expands that syntax.'
        }

        function Quote-EnvValue([string]$value) { return "'" + $value.Replace('\', '\\').Replace("'", "\'") + "'" }
        $content = @(
            "BROKER_HOST=$(Quote-EnvValue $hostName)",
            "BROKER_PORT=$(Quote-EnvValue $port)",
            "MQTT_USER=$(Quote-EnvValue $user)",
            "MQTT_PASS=$(Quote-EnvValue $password)"
        ) -join "`n"
        $envTemp = Join-Path $root ('.env-' + [guid]::NewGuid().ToString('N'))
        try {
            New-Item -ItemType File -Path $envTemp | Out-Null
            $acl = [Security.AccessControl.FileSecurity]::new()
            $acl.SetAccessRuleProtection($true, $false)
            foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
                $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow')
                $acl.AddAccessRule($rule)
            }
            Set-Acl -Path $envTemp -AclObject $acl
            [IO.File]::WriteAllText($envTemp, $content + "`n", [Text.UTF8Encoding]::new($false))
            [IO.File]::Move($envTemp, $envFile)
        } finally { Remove-Item -LiteralPath $envTemp -Force -ErrorAction SilentlyContinue }
        Remove-Variable password, securePassword
    }

    & $activator -ProjectRoot $root -StagedSource $staged -Source $verified.Commit
    Remove-Item -LiteralPath (Join-Path $root '.hwmonitor-mqtt-bootstrap-pending') -Force -ErrorAction SilentlyContinue
    Write-Output "Sender source $($verified.Commit) installed and started from $root."
}
finally {
    if ($lockHandle) { $lockHandle.Dispose() }
    Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue
}
