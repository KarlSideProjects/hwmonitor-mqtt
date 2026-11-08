param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$StagedSource,
    [Parameter(Mandatory = $true)][string]$Source
)
$ErrorActionPreference = 'Stop'
if ($Source -cnotmatch '^[a-f0-9]{40}$') { throw 'Invalid source commit.' }
foreach ($required in @('hwmonitor_mqtt', 'scripts/windows/install_windows_sender_task.ps1',
        'scripts/windows/start_windows_sender.ps1', 'pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $StagedSource $required))) { throw 'Staged source is incomplete.' }
}
$taskName = 'HWMonitorMQTTSender'
$previousTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($previousTask) {
    $actions = @($previousTask.Actions)
    $recognized = $false
    if ($actions.Count -eq 1 -and (Split-Path -Leaf $actions[0].Execute) -ieq 'powershell.exe' -and $actions[0].Arguments) {
        foreach ($launcher in @((Join-Path $ProjectRoot 'scripts/windows/start_windows_sender.ps1'),
                (Join-Path $ProjectRoot 'package/scripts/windows/start_windows_sender.ps1'))) {
            $filePattern = '(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($launcher) + '"?(?:\s|$)'
            $rootPattern = '(?i)(?:^|\s)-ProjectRoot\s+"?' + [regex]::Escape($ProjectRoot) + '"?(?:\s|$)'
            if ($actions[0].Arguments -match $filePattern -and $actions[0].Arguments -match $rootPattern) { $recognized = $true }
        }
    }
    if (-not $recognized -or $previousTask.State -notin @('Running', 'Ready', 'Disabled')) { throw "Refusing unrecognized scheduled task: $taskName" }
}
$previousXml = if ($previousTask) { Export-ScheduledTask -TaskName $taskName } else { $null }
$wasRunning = $previousTask -and $previousTask.State -eq 'Running'
$backup = Join-Path $ProjectRoot ('source-backup-' + [guid]::NewGuid().ToString('N'))
$marker = Join-Path $ProjectRoot '.hwmonitor-mqtt-bootstrap'
$markerTemp = Join-Path $ProjectRoot ('.marker-' + [guid]::NewGuid().ToString('N'))
$markerBackup = Join-Path $ProjectRoot ('.marker-backup-' + [guid]::NewGuid().ToString('N'))
$names = @('hwmonitor_mqtt', 'scripts', 'pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md',
           '.venv', '.uv-cache', '.uv-python', 'third_party')
$movedOld = [Collections.Generic.List[string]]::new()
$movedNew = [Collections.Generic.List[string]]::new()
$stopped = $false
$stopIssued = $false
$installerAttempted = $false
$markerCommitted = $false
New-Item -ItemType Directory -Path $backup | Out-Null
if ($previousXml) { [IO.File]::WriteAllText((Join-Path $backup 'task.xml'), $previousXml, [Text.UTF8Encoding]::new($false)) }
[IO.File]::WriteAllText($markerTemp, $Source + "`n", [Text.UTF8Encoding]::new($false))
function Stop-TaskAndWait {
    param([switch]$TrackPrevious)
    if ($TrackPrevious) { $script:stopIssued = $true }
    Stop-ScheduledTask -TaskName $taskName
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'Scheduled task did not stop.'
}
try {
    if ($wasRunning) { Stop-TaskAndWait -TrackPrevious; $stopped = $true }
    foreach ($name in $names) {
        $current = Join-Path $ProjectRoot $name
        $incoming = Join-Path $StagedSource $name
        if (Test-Path -LiteralPath $current) {
            Move-Item -LiteralPath $current -Destination (Join-Path $backup $name)
            $movedOld.Add($name)
        }
        if (Test-Path -LiteralPath $incoming) {
            $movedNew.Add($name)
            Copy-Item -LiteralPath $incoming -Destination $current -Recurse
        }
    }
    $installerAttempted = $true
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts/windows/install_windows_sender_task.ps1') -ProjectRoot $ProjectRoot -AtStartup
        $installerExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousErrorPreference }
    if ($installerExit -ne 0) { throw "Windows task installer failed with exit code $installerExit" }
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 500
        $state = (Get-ScheduledTask -TaskName $taskName).State
    } while ($state -ne 'Running' -and [DateTime]::UtcNow -lt $deadline)
    if ($state -ne 'Running') { throw 'New sender did not start.' }
    $stableUntil = [DateTime]::UtcNow.AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 500
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { throw 'New sender stopped during startup.' }
    } while ([DateTime]::UtcNow -lt $stableUntil)
    if (Test-Path -LiteralPath $marker) { [IO.File]::Replace($markerTemp, $marker, $markerBackup) }
    else { [IO.File]::Move($markerTemp, $marker) }
    $markerCommitted = $true
} catch {
    $failure = $_
    try {
        if ($stopIssued -and -not $stopped) {
            if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') { throw 'Previous stop remains unconfirmed.' }
            $stopped = $true
        }
        if ($installerAttempted) {
            $currentTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if ($currentTask -and $currentTask.State -eq 'Running') { Stop-TaskAndWait }
            foreach ($name in @('.venv', '.uv-cache', '.uv-python', 'third_party')) {
                $generated = Join-Path $ProjectRoot $name
                if ((Test-Path -LiteralPath $generated) -and -not $movedNew.Contains($name)) {
                    Remove-Item -LiteralPath $generated -Recurse -Force
                }
            }
        }
        $reversedNew = @($movedNew.ToArray()); [array]::Reverse($reversedNew)
        foreach ($name in $reversedNew) {
            $current = Join-Path $ProjectRoot $name
            if (Test-Path -LiteralPath $current) { Remove-Item -LiteralPath $current -Recurse -Force }
        }
        $reversedOld = @($movedOld.ToArray()); [array]::Reverse($reversedOld)
        foreach ($name in $reversedOld) { Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $ProjectRoot $name) }
        if ($previousXml -and $installerAttempted) { Register-ScheduledTask -TaskName $taskName -Xml $previousXml -Force | Out-Null }
        elseif (-not $previousXml -and $installerAttempted) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
        if ($wasRunning -and $stopped) { Start-ScheduledTask -TaskName $taskName }
        Remove-Item -LiteralPath $backup -Recurse -Force
    } catch {
        throw "Activation failed: $failure. Recovery failed: $_. Backup retained at $backup."
    }
    throw $failure
} finally {
    Remove-Item -LiteralPath $markerTemp -Force -ErrorAction SilentlyContinue
    if ($markerCommitted) { Remove-Item -LiteralPath $markerBackup -Force -ErrorAction SilentlyContinue }
}
try { Remove-Item -LiteralPath $backup -Recurse -Force }
catch { Write-Warning "Sender is running; previous source retained at $backup." }
