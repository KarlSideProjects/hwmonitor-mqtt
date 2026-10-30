param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$StagedPackage
)
$ErrorActionPreference = 'Stop'
$taskName = 'HWMonitorMQTTSender'
$package = Join-Path $ProjectRoot 'package'
$backup = Join-Path $ProjectRoot ('package-backup-' + [guid]::NewGuid().ToString('N'))
$installer = Join-Path $StagedPackage 'scripts/windows/install_windows_sender_task.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $StagedPackage 'sender.exe') -PathType Leaf) -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Validated package is incomplete.' }
$previousTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$previousXml = if ($previousTask) { Export-ScheduledTask -TaskName $taskName } else { $null }
$wasRunning = $previousTask -and $previousTask.State -eq 'Running'
$stopped = $false
$movedOld = $false
$movedNew = $false
try {
    if ($wasRunning) { Stop-ScheduledTask -TaskName $taskName; $stopped = $true }
    if (Test-Path -LiteralPath $package) { Move-Item -LiteralPath $package -Destination $backup; $movedOld = $true }
    Move-Item -LiteralPath $StagedPackage -Destination $package
    $movedNew = $true
    & (Join-Path $package 'scripts/windows/install_windows_sender_task.ps1') -ProjectRoot $ProjectRoot -SenderExe (Join-Path $package 'sender.exe') -AtStartup
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 500
        $state = (Get-ScheduledTask -TaskName $taskName).State
    } while ($state -ne 'Running' -and [DateTime]::UtcNow -lt $deadline)
    if ($state -ne 'Running') { throw 'New sender did not remain running.' }
    if ($movedOld) { Remove-Item -LiteralPath $backup -Recurse -Force }
}
catch {
    $failure = $_
    if ($movedNew) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $package -Recurse -Force
    }
    if ($movedOld) { Move-Item -LiteralPath $backup -Destination $package }
    if ($previousXml) {
        Register-ScheduledTask -TaskName $taskName -Xml $previousXml -Force | Out-Null
        if ($wasRunning -and $stopped) { Start-ScheduledTask -TaskName $taskName }
    }
    elseif ($movedNew) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
    throw $failure
}
