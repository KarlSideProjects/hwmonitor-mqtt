param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$UpgradeCandidate,
    [Parameter(Mandatory = $true)][string]$WrongArchCandidate,
    [Parameter(Mandatory = $true)][string]$BrokerExe,
    [Parameter(Mandatory = $true)][string]$OldSource,
    [Parameter(Mandatory = $true)][string]$OldSha256,
    [Parameter(Mandatory = $true)][string]$Source
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'This smoke test only runs on a disposable GitHub Actions runner.' }
Remove-Item Env:BROKER_HOST, Env:BROKER_PORT, Env:MQTT_USER, Env:MQTT_PASS, Env:SENDER_PROFILE -ErrorAction SilentlyContinue
$testRoot = Join-Path $env:RUNNER_TEMP ('official-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$root = Join-Path $env:ProgramData 'hwmonitor-mqtt'
$assets = Join-Path $testRoot 'assets'
New-Item -ItemType Directory -Path $assets | Out-Null
$broker = $null
$ownsTask = $false
$ownsRoot = $false
$global:FailNextStart = $false
$global:FailNextDownload = $false
$global:SmokeRelease = '0.0.0'
$observations = [Collections.Generic.List[object]]::new()
$env:SMOKE_MQTT_USER = 'ci-user'
$env:SMOKE_MQTT_PASS = 'ci-test-password'

function Invoke-RestMethod {
    param($Uri, $Headers)
    if ($Uri -ne 'https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest') { throw 'Unexpected release API.' }
    $dir = Join-Path $assets $global:SmokeRelease
    return [pscustomobject]@{ tag_name = 'v' + $global:SmokeRelease; draft = $false; prerelease = $false; assets = @(Get-ChildItem $dir -File | ForEach-Object { [pscustomobject]@{name=$_.Name} }) }
}
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Uri, $OutFile)
    if ($global:FailNextDownload) { $global:FailNextDownload = $false; throw 'Injected download failure.' }
    $name = $Uri.Substring($Uri.LastIndexOf('/') + 1)
    Copy-Item -LiteralPath (Join-Path (Join-Path $assets $global:SmokeRelease) $name) -Destination $OutFile
}
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    if ($AsSecureString) { return ConvertTo-SecureString 'ci-test-password' -AsPlainText -Force }
    if ($Prompt -like '*broker host*') { return '127.0.0.1' }
    if ($Prompt -like '*broker port*') { return '18884' }
    if ($Prompt -like '*username*') { return 'ci-user' }
    throw 'Unexpected prompt.'
}
function Start-ScheduledTask {
    param($TaskName)
    if ($global:FailNextStart) { $global:FailNextStart = $false; throw 'Injected start failure.' }
    ScheduledTasks\Start-ScheduledTask -TaskName $TaskName
}
function Assert-OldState($expectedExe, $expectedEnv, $expectedAcl) {
    if ((Get-FileHash (Join-Path $root 'package/sender.exe') -Algorithm SHA256).Hash -ne $expectedExe) { throw 'Previous executable was not restored.' }
    if ((Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash -ne $expectedEnv) { throw 'Settings changed.' }
    if ((Get-Acl (Join-Path $root '.env')).Sddl -ne $expectedAcl) { throw 'Settings ACL changed.' }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Get-ScheduledTask -TaskName 'HWMonitorMQTTSender').State -eq 'Running') { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'Previous task is not running.'
}
function Get-SenderTree {
    $launcher = Join-Path $root 'package/scripts/windows/start_windows_sender.ps1'
    $processes = @(Get-CimInstance -ClassName Win32_Process)
    $roots = @($processes | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($launcher, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    if ($roots.Count -ne 1) { throw "Expected one task launcher, observed $($roots.Count)." }
    $ids = [Collections.Generic.HashSet[uint32]]::new()
    [void]$ids.Add([uint32]$roots[0].ProcessId)
    do {
        $added = $false
        foreach ($process in $processes) {
            if ($ids.Contains([uint32]$process.ParentProcessId) -and $ids.Add([uint32]$process.ProcessId)) { $added = $true }
        }
    } while ($added)
    return @($processes | Where-Object { $ids.Contains([uint32]$_.ProcessId) } | ForEach-Object {
        [pscustomobject]@{ pid = $_.ProcessId; parent = $_.ParentProcessId; created = $_.CreationDate.ToString('o'); name = $_.Name }
    })
}
function Assert-TreeExited($tree) {
    foreach ($process in $tree) {
        $remaining = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($process.pid)" -ErrorAction Stop
        if ($remaining -and $remaining.CreationDate.ToString('o') -eq $process.created) { throw "Prior sender process $($process.pid) remains running." }
    }
}
try {
    if (Test-Path -LiteralPath $root) { throw 'Refusing to replace an existing install in the smoke environment.' }
    if (Get-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue) { throw 'Refusing to replace an existing task in the smoke environment.' }
    $ownsTask = $true
    $ownsRoot = $true
    foreach ($item in @(@('0.0.0', $Candidate, $OldSource), @('0.0.1', $UpgradeCandidate, $Source))) {
        $version = $item[0]
        $dir = Join-Path $assets $version
        New-Item -ItemType Directory -Path $dir | Out-Null
        Copy-Item -LiteralPath (Join-Path $item[1] 'sender-windows-x64.zip') -Destination $dir
        Copy-Item -LiteralPath (Join-Path $item[1] 'sender-windows-x64.zip.sha256') -Destination $dir
        foreach ($name in @('validate_sender_package.ps1', 'activate_sender_package.ps1')) {
            $path = Join-Path $dir $name
            Copy-Item -LiteralPath (Join-Path $PWD "scripts/windows/$name") -Destination $path
            $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            [IO.File]::WriteAllText(($path + '.sha256'), "$hash  $name`n", [Text.Encoding]::ASCII)
        }
        [IO.File]::WriteAllText((Join-Path $dir 'version.json'), (@{version=$version;source=$item[2]} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    }
    $passwordFile = Join-Path $testRoot 'mosquitto.passwd'
    & (Join-Path (Split-Path -Parent $BrokerExe) 'mosquitto_passwd.exe') -b -c $passwordFile 'ci-user' 'ci-test-password'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create test broker credentials.' }
    $config = Join-Path $testRoot 'mosquitto.conf'
    $passwordPath = $passwordFile.Replace('\', '/')
    $brokerLog = Join-Path $testRoot 'mosquitto.log'
    $brokerLogPath = $brokerLog.Replace('\', '/')
    [IO.File]::WriteAllText($config, "listener 18884 127.0.0.1`nallow_anonymous false`npassword_file $passwordPath`nlog_dest file $brokerLogPath`nlog_type all`n", [Text.UTF8Encoding]::new($false))
    $broker = Start-Process -FilePath $BrokerExe -ArgumentList '-c', "`"$config`"" -PassThru
    Start-Sleep -Seconds 2
    if ($broker.HasExited) { throw 'Test broker exited.' }

    $bootstrap = Join-Path $PWD 'bootstrap.ps1'
    $global:FailNextStart = $true
    try { & $bootstrap; throw 'Expected first start failure.' } catch { if ($_ -notmatch 'Injected start failure') { throw } }
    if (-not (Test-Path (Join-Path $root '.hwmonitor-mqtt-bootstrap-pending')) -or (Test-Path (Join-Path $root 'package'))) { throw 'Failed first installation left unexpected state.' }
    $firstEnv = (Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash
    $firstAcl = (Get-Acl (Join-Path $root '.env')).Sddl
    & $bootstrap
    if ((Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash -ne $firstEnv -or (Get-Acl (Join-Path $root '.env')).Sddl -ne $firstAcl) { throw 'Retry changed settings or ACL.' }
    & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile full --port 18884
    if ($LASTEXITCODE -ne 0) { throw 'No installed full payload.' }
    $oldTree = @(Get-SenderTree)
    $observations.Add([pscustomobject]@{phase='old-running'; processes=$oldTree})

    [IO.File]::AppendAllText((Join-Path $root '.env'), "SENDER_PROFILE=esp`n", [Text.UTF8Encoding]::new($false))
    $oldExe = (Get-FileHash (Join-Path $root 'package/sender.exe') -Algorithm SHA256).Hash
    if ($oldExe -ne $OldSha256) { throw 'The prior compiled sender is not the expected artifact.' }
    $oldEnv = (Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash
    $oldAcl = (Get-Acl (Join-Path $root '.env')).Sddl
    $global:SmokeRelease = '0.0.1'
    $global:FailNextDownload = $true
    try { & $bootstrap; throw 'Expected download failure.' } catch { if ($_ -notmatch 'Injected download failure') { throw } }
    Assert-OldState $oldExe $oldEnv $oldAcl
    $checksumPath = Join-Path (Join-Path $assets $global:SmokeRelease) 'sender-windows-x64.zip.sha256'
    $checksum = [IO.File]::ReadAllText($checksumPath)
    [IO.File]::WriteAllText($checksumPath, (('0' * 64) + "  sender-windows-x64.zip`n"), [Text.Encoding]::ASCII)
    try { & $bootstrap; throw 'Expected checksum failure.' } catch { if ($_ -notmatch 'SHA-256 mismatch') { throw } }
    [IO.File]::WriteAllText($checksumPath, $checksum, [Text.Encoding]::ASCII)
    Assert-OldState $oldExe $oldEnv $oldAcl
    $upgradeDir = Join-Path $assets $global:SmokeRelease
    $archivePath = Join-Path $upgradeDir 'sender-windows-x64.zip'
    $savedArchive = Join-Path $testRoot 'saved-upgrade.zip'
    Copy-Item -LiteralPath $archivePath -Destination $savedArchive
    try {
        [IO.File]::AppendAllText($archivePath, 'corruption', [Text.Encoding]::ASCII)
        try { & $bootstrap; throw 'Expected corrupted package failure.' } catch { if ($_ -notmatch 'SHA-256 mismatch') { throw } }
    }
    finally { Copy-Item -LiteralPath $savedArchive -Destination $archivePath -Force }
    Assert-OldState $oldExe $oldEnv $oldAcl
    try {
        Copy-Item -LiteralPath (Join-Path $WrongArchCandidate 'sender-windows-x64.zip') -Destination $archivePath -Force
        Copy-Item -LiteralPath (Join-Path $WrongArchCandidate 'sender-windows-x64.zip.sha256') -Destination $checksumPath -Force
        try { & $bootstrap; throw 'Expected architecture failure.' } catch { if ($_ -notmatch 'architecture') { throw } }
    }
    finally {
        Copy-Item -LiteralPath $savedArchive -Destination $archivePath -Force
        [IO.File]::WriteAllText($checksumPath, $checksum, [Text.Encoding]::ASCII)
    }
    Assert-OldState $oldExe $oldEnv $oldAcl
    $global:FailNextStart = $true
    try { & $bootstrap; throw 'Expected upgrade start failure.' } catch { if ($_ -notmatch 'Injected start failure') { throw } }
    Assert-OldState $oldExe $oldEnv $oldAcl
    Assert-TreeExited $oldTree
    $rollbackTree = @(Get-SenderTree)
    $observations.Add([pscustomobject]@{phase='rollback-running'; processes=$rollbackTree})
    & $bootstrap
    if ((Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash -ne $oldEnv -or (Get-Acl (Join-Path $root '.env')).Sddl -ne $oldAcl) { throw 'Upgrade changed settings or ACL.' }
    $manifest = [IO.File]::ReadAllText((Join-Path $root 'package/manifest.json')) | ConvertFrom-Json
    if ($manifest.version -ne '0.0.1') { throw 'Upgrade did not activate candidate manifest.' }
    $newExe = (Get-FileHash (Join-Path $root 'package/sender.exe') -Algorithm SHA256).Hash
    if ($newExe -eq $oldExe -or $manifest.source -ne $Source) { throw 'Upgrade did not activate distinct compiled bytes from the selected source.' }
    $versionOutput = & (Join-Path $root 'package/sender.exe') --version
    if ($LASTEXITCODE -ne 0 -or "$versionOutput" -notmatch [regex]::Escape($Source) -or "$versionOutput" -notmatch '0\.0\.1') { throw 'Installed executable version identity is incorrect.' }
    Assert-TreeExited $rollbackTree
    $observations.Add([pscustomobject]@{phase='upgrade-running'; processes=@(Get-SenderTree)})
    & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile esp --port 18884
    if ($LASTEXITCODE -ne 0) { throw 'No installed esp payload.' }
    Start-Sleep -Seconds 12
    if (Select-String -LiteralPath $brokerLog -Pattern 'already connected' -Quiet) { throw 'Broker observed a duplicate sender client connection.' }
    $observations.Add([pscustomobject]@{phase='broker-check'; duplicateClientConnection=$false})
    Write-Output "Official bootstrap activated distinct compiled bytes: old=$oldExe new=$newExe"
}
finally {
    New-Item -ItemType Directory -Path 'test-evidence' -Force | Out-Null
    $observations | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path 'test-evidence' "process-tree-ps$($PSVersionTable.PSVersion.Major).json") -Encoding UTF8
    if ($ownsTask -and (Get-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue)) {
        Stop-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName 'HWMonitorMQTTSender' -Confirm:$false -ErrorAction SilentlyContinue
    }
    if ($broker -and -not $broker.HasExited) { Stop-Process -Id $broker.Id -Force }
    Remove-Item Env:SMOKE_MQTT_USER, Env:SMOKE_MQTT_PASS -ErrorAction SilentlyContinue
    if ($ownsRoot) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
