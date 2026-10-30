param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$UpgradeCandidate,
    [Parameter(Mandatory = $true)][string]$BrokerExe,
    [Parameter(Mandatory = $true)][string]$Source
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'This smoke test only runs on a disposable GitHub Actions runner.' }
Remove-Item Env:BROKER_HOST, Env:BROKER_PORT, Env:MQTT_USER, Env:MQTT_PASS, Env:SENDER_PROFILE -ErrorAction SilentlyContinue
$testRoot = Join-Path $env:RUNNER_TEMP ('official-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$oldProgramData = $env:ProgramData
$env:ProgramData = Join-Path $testRoot 'ProgramData'
New-Item -ItemType Directory -Path $env:ProgramData | Out-Null
$root = Join-Path $env:ProgramData 'hwmonitor-mqtt'
$assets = Join-Path $testRoot 'assets'
New-Item -ItemType Directory -Path $assets | Out-Null
$broker = $null
$ownsTask = $false
$global:FailNextStart = $false
$global:FailNextDownload = $false
$global:SmokeRelease = '0.0.0'
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
    if ((Get-ScheduledTask -TaskName 'HWMonitorMQTTSender').State -ne 'Running') { throw 'Previous task is not running.' }
}
try {
    if (Get-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue) { throw 'Refusing to replace an existing task in the smoke environment.' }
    $ownsTask = $true
    foreach ($item in @(@('0.0.0', $Candidate), @('0.0.1', $UpgradeCandidate))) {
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
        [IO.File]::WriteAllText((Join-Path $dir 'version.json'), (@{version=$version;source=$Source} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    }
    $passwordFile = Join-Path $testRoot 'mosquitto.passwd'
    & (Join-Path (Split-Path -Parent $BrokerExe) 'mosquitto_passwd.exe') -b -c $passwordFile 'ci-user' 'ci-test-password'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create test broker credentials.' }
    $config = Join-Path $testRoot 'mosquitto.conf'
    $passwordPath = $passwordFile.Replace('\', '/')
    [IO.File]::WriteAllText($config, "listener 18884 127.0.0.1`nallow_anonymous false`npassword_file $passwordPath`n", [Text.UTF8Encoding]::new($false))
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

    [IO.File]::AppendAllText((Join-Path $root '.env'), "SENDER_PROFILE=esp`n", [Text.UTF8Encoding]::new($false))
    $oldExe = (Get-FileHash (Join-Path $root 'package/sender.exe') -Algorithm SHA256).Hash
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
    $global:FailNextStart = $true
    try { & $bootstrap; throw 'Expected upgrade start failure.' } catch { if ($_ -notmatch 'Injected start failure') { throw } }
    Assert-OldState $oldExe $oldEnv $oldAcl
    & $bootstrap
    if ((Get-FileHash (Join-Path $root '.env') -Algorithm SHA256).Hash -ne $oldEnv -or (Get-Acl (Join-Path $root '.env')).Sddl -ne $oldAcl) { throw 'Upgrade changed settings or ACL.' }
    $manifest = [IO.File]::ReadAllText((Join-Path $root 'package/manifest.json')) | ConvertFrom-Json
    if ($manifest.version -ne '0.0.1') { throw 'Upgrade did not activate candidate manifest.' }
    & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile esp --port 18884
    if ($LASTEXITCODE -ne 0) { throw 'No installed esp payload.' }
    Write-Output 'Official bootstrap rehearsal passed; both candidate archives use the same compiled binary.'
}
finally {
    if ($ownsTask -and (Get-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue)) {
        Stop-ScheduledTask -TaskName 'HWMonitorMQTTSender' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName 'HWMonitorMQTTSender' -Confirm:$false -ErrorAction SilentlyContinue
    }
    if ($broker -and -not $broker.HasExited) { Stop-Process -Id $broker.Id -Force }
    $env:ProgramData = $oldProgramData
    Remove-Item Env:SMOKE_MQTT_USER, Env:SMOKE_MQTT_PASS -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
