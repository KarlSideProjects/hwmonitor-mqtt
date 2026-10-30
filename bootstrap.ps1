$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'hwmonitor-mqtt'
$temp = Join-Path $env:ProgramData ('hwmonitor-mqtt-stage-' + [guid]::NewGuid().ToString('N'))
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
# End trusted path checks
$ancestor = Split-Path -Parent $root
while ($ancestor) {
    Assert-TrustedItem $ancestor $true
    $next = Split-Path -Parent $ancestor
    if ($next -eq $ancestor) { break }
    $ancestor = $next
}
if (Test-Path $root) {
    Assert-TrustedItem $root $false
    foreach ($relative in @('.env', '.hwmonitor-mqtt-bootstrap', '.hwmonitor-mqtt-bootstrap-pending', 'scripts', 'package')) {
        $path = Join-Path $root $relative
        if (Test-Path -LiteralPath $path) {
            Assert-TrustedItem $path $false ($relative -eq '.env')
            if ((Get-Item -LiteralPath $path -Force).PSIsContainer) {
                Get-ChildItem -LiteralPath $path -Force -Recurse | ForEach-Object { Assert-TrustedItem $_.FullName $false }
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
}
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -Path $temp -AclObject $acl
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest' -Headers @{ 'User-Agent' = 'hwmonitor-mqtt-bootstrap' }
    $tag = [string]$release.tag_name
    if ($release.draft -or $release.prerelease -or $tag -cnotmatch '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid public release version.' }
    $base = "https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/download/$tag"
    function Download-ReleaseAsset([string]$name) {
        if (@($release.assets | Where-Object { $_.name -ceq $name }).Count -ne 1) { throw "Missing release asset: $name" }
        $path = Join-Path $temp $name
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $path
        return $path
    }
    function Download-VerifiedAsset([string]$name) {
        $checksum = Download-ReleaseAsset ($name + '.sha256')
        $record = [IO.File]::ReadAllText($checksum).Trim()
        if ($record -notmatch ('^([a-fA-F0-9]{64})  ' + [regex]::Escape($name) + '$')) { throw "Invalid checksum for $name" }
        $expected = $Matches[1]
        $path = Download-ReleaseAsset $name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) { throw "SHA-256 mismatch for $name" }
        return [pscustomobject]@{ Path = $path; Sha256 = $expected }
    }
    $versionPath = Download-ReleaseAsset 'version.json'
    $version = [IO.File]::ReadAllText($versionPath) | ConvertFrom-Json
    if ($version.version -isnot [string] -or $version.source -isnot [string] -or $version.version -cne $tag.Substring(1) -or $version.source -cnotmatch '^[a-f0-9]{40}$') { throw 'Release version metadata does not match its tag.' }
    $validator = (Download-VerifiedAsset 'validate_sender_package.ps1').Path
    $activator = (Download-VerifiedAsset 'activate_sender_package.ps1').Path
    $archive = Download-VerifiedAsset 'sender-windows-x64.zip'
    $staged = Join-Path $temp 'package'
    & $validator -Archive $archive.Path -Sha256 $archive.Sha256 -Destination $staged
    $manifest = [IO.File]::ReadAllText((Join-Path $staged 'manifest.json')) | ConvertFrom-Json
    if ($manifest.version -cne $version.version -or $manifest.source -cne $version.source) { throw 'Package identity does not match release metadata.' }
    if ((Get-Command $activator).Parameters.ContainsKey('CheckPrerequisitesOnly')) {
        & $activator -ProjectRoot $root -StagedPackage $staged -Source $version.source -CheckPrerequisitesOnly
    }
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

    & $activator -ProjectRoot $root -StagedPackage $staged -Source $version.source
    Remove-Item -LiteralPath (Join-Path $root '.hwmonitor-mqtt-bootstrap-pending') -Force -ErrorAction SilentlyContinue
    Write-Output "Sender $($version.version) installed and started from $root."
}
finally {
    Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue
}
