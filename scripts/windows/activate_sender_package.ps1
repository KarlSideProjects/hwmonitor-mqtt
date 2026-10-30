param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$StagedPackage,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Source
)
$ErrorActionPreference = 'Stop'
$taskName = 'HWMonitorMQTTSender'
$package = Join-Path $ProjectRoot 'package'
$backup = Join-Path $ProjectRoot ('package-backup-' + [guid]::NewGuid().ToString('N'))
$installer = Join-Path $StagedPackage 'scripts/windows/install_windows_sender_task.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $StagedPackage 'sender.exe') -PathType Leaf) -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Validated package is incomplete.' }
$previousTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($previousTask) {
    $actions = @($previousTask.Actions)
    $recognized = $false
    if ($actions.Count -eq 1 -and (Split-Path -Leaf $actions[0].Execute) -ieq 'powershell.exe' -and $actions[0].Arguments) {
        foreach ($launcher in @((Join-Path $ProjectRoot 'scripts/windows/start_windows_sender.ps1'), (Join-Path $package 'scripts/windows/start_windows_sender.ps1'))) {
            $filePattern = '(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($launcher) + '"?(?:\s|$)'
            $rootPattern = '(?i)(?:^|\s)-ProjectRoot\s+"?' + [regex]::Escape($ProjectRoot) + '"?(?:\s|$)'
            if ($actions[0].Arguments -match $filePattern -and $actions[0].Arguments -match $rootPattern) { $recognized = $true }
        }
    }
    if (-not $recognized) { throw "Refusing to replace unrecognized scheduled task: $taskName" }
}
$previousXml = if ($previousTask) { Export-ScheduledTask -TaskName $taskName } else { $null }
$wasRunning = $previousTask -and $previousTask.State -eq 'Running'
$stopped = $false
$movedOld = $false
$movedNew = $false
$marker = Join-Path $ProjectRoot '.hwmonitor-mqtt-bootstrap'
$markerTemp = Join-Path $ProjectRoot ('.marker-' + [guid]::NewGuid().ToString('N'))
$markerBackup = Join-Path $ProjectRoot ('.marker-backup-' + [guid]::NewGuid().ToString('N'))
$markerCommitted = $false
[IO.File]::WriteAllText($markerTemp, $Source + "`n", [Text.UTF8Encoding]::new($false))
function Stop-TaskAndWait {
    Stop-ScheduledTask -TaskName $taskName
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'Scheduled task did not stop.'
}
try {
    if ($wasRunning) { Stop-TaskAndWait; $stopped = $true }
    if (Test-Path -LiteralPath $package) { Move-Item -LiteralPath $package -Destination $backup; $movedOld = $true }
    Move-Item -LiteralPath $StagedPackage -Destination $package
    $movedNew = $true
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $package 'scripts/windows/install_windows_sender_task.ps1') -ProjectRoot $ProjectRoot -SenderExe (Join-Path $package 'sender.exe') -LhmInstallDir (Join-Path $package 'third_party/librehardwaremonitor') -AtStartup
    if ($LASTEXITCODE -ne 0) { throw "Windows task installer failed with exit code $LASTEXITCODE" }
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 500
        $state = (Get-ScheduledTask -TaskName $taskName).State
    } while ($state -ne 'Running' -and [DateTime]::UtcNow -lt $deadline)
    if ($state -ne 'Running') { throw 'New sender did not remain running.' }
    $stableUntil = [DateTime]::UtcNow.AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 500
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { throw 'New sender stopped during startup.' }
    } while ([DateTime]::UtcNow -lt $stableUntil)
    if (Test-Path -LiteralPath $marker) { [IO.File]::Replace($markerTemp, $marker, $markerBackup) }
    else { [IO.File]::Move($markerTemp, $marker) }
    $markerCommitted = $true
}
catch {
    $failure = $_
    try {
        if ($movedNew) {
            $current = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if ($current -and $current.State -eq 'Running') { Stop-TaskAndWait }
            Remove-Item -LiteralPath $package -Recurse -Force
        }
        if ($movedOld) { Move-Item -LiteralPath $backup -Destination $package }
        if ($previousXml -and ($movedOld -or $movedNew)) {
            Register-ScheduledTask -TaskName $taskName -Xml $previousXml -Force | Out-Null
            if ($wasRunning -and $stopped) { Start-ScheduledTask -TaskName $taskName }
        }
        elseif ($previousXml -and $wasRunning -and $stopped) { Start-ScheduledTask -TaskName $taskName }
        elseif ($movedNew) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
    }
    catch {
        $rollbackFailure = $_
        $recoveryPath = Join-Path $ProjectRoot ('task-recovery-' + [guid]::NewGuid().ToString('N') + '.xml')
        try {
            if ($previousXml) {
                New-Item -ItemType File -Path $recoveryPath | Out-Null
                if ($env:OS -eq 'Windows_NT') {
                    $acl = [Security.AccessControl.FileSecurity]::new()
                    $acl.SetAccessRuleProtection($true, $false)
                    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
                        $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow')
                        $acl.AddAccessRule($rule)
                    }
                    Set-Acl -Path $recoveryPath -AclObject $acl
                }
                [IO.File]::WriteAllText($recoveryPath, $previousXml, [Text.UTF8Encoding]::new($false))
            }
            else { $recoveryPath = 'no previous task' }
        }
        catch { $recoveryPath = 'task XML could not be saved' }
        throw "Activation failed: $failure. Rollback failed: $rollbackFailure. Preserve $backup; task recovery: $recoveryPath."
    }
    throw $failure
}
finally {
    Remove-Item -LiteralPath $markerTemp -Force -ErrorAction SilentlyContinue
    if ($markerCommitted) { Remove-Item -LiteralPath $markerBackup -Force -ErrorAction SilentlyContinue }
}
if ($movedOld) {
    try { Remove-Item -LiteralPath $backup -Recurse -Force }
    catch { Write-Warning "New sender is running; prior package retained at $backup." }
}
