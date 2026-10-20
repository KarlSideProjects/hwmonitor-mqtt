$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'hwmonitor-mqtt'
$repo = 'https://github.com/KarlSideProjects/hwmonitor-mqtt/archive/refs/heads/master.zip'
$temp = Join-Path $env:TEMP ('hwmonitor-mqtt-' + [guid]::NewGuid().ToString('N'))

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this bootstrap from an elevated PowerShell window.'
}
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    if (Test-Path $root) {
        if (-not (Test-Path (Join-Path $root '.hwmonitor-mqtt-bootstrap')) -or -not (Test-Path (Join-Path $root 'install.sh')) -or -not (Test-Path (Join-Path $root 'scripts/windows/install_windows_sender_task.ps1'))) {
            throw "Refusing to use existing unrecognized directory: $root"
        }
    }
    else {
        $zip = Join-Path $temp 'source.zip'
        Invoke-WebRequest -UseBasicParsing -Uri $repo -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $temp
        $source = Get-ChildItem -Path $temp -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'scripts/windows/install_windows_sender_task.ps1') } | Select-Object -First 1
        if (-not $source) { throw 'Downloaded archive did not contain the project.' }
        Move-Item -Path $source.FullName -Destination $root
        New-Item -ItemType File -Path (Join-Path $root '.hwmonitor-mqtt-bootstrap') | Out-Null
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
        if ($user -match '\$\{[^}]+\}' -or $password -match '\$\{[^}]+\}') {
            throw 'MQTT username and password cannot contain ${...} because the existing dotenv reader expands that syntax.'
        }

        function Quote-EnvValue([string]$value) { return "'" + $value.Replace('\', '\\').Replace("'", "\'") + "'" }
        $content = @(
            "BROKER_HOST=$(Quote-EnvValue $hostName)",
            "BROKER_PORT=$(Quote-EnvValue $port)",
            "MQTT_USER=$(Quote-EnvValue $user)",
            "MQTT_PASS=$(Quote-EnvValue $password)"
        ) -join "`n"
        New-Item -ItemType File -Path $envFile | Out-Null
        $acl = [Security.AccessControl.FileSecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow')
            $acl.AddAccessRule($rule)
        }
        Set-Acl -Path $envFile -AclObject $acl
        [IO.File]::WriteAllText($envFile, $content + "`n", [Text.UTF8Encoding]::new($false))
        Remove-Variable password, securePassword
    }

    $installer = Join-Path $root 'scripts/windows/install_windows_sender_task.ps1'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -ProjectRoot $root -AtStartup
    if ($LASTEXITCODE -ne 0) { throw "Windows sender installation failed with exit code $LASTEXITCODE" }
    Start-ScheduledTask -TaskName 'HWMonitorMQTTSender'
    Write-Output "Sender installed and started from $root."
}
finally {
    Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue
}
