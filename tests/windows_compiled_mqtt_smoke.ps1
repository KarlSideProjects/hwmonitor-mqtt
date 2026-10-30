param([Parameter(Mandatory = $true)][string]$SenderExe, [Parameter(Mandatory = $true)][string]$BrokerExe)
$ErrorActionPreference = 'Stop'
Remove-Item Env:BROKER_HOST, Env:BROKER_PORT, Env:MQTT_USER, Env:MQTT_PASS, Env:SENDER_PROFILE -ErrorAction SilentlyContinue
$testRoot = Join-Path $env:RUNNER_TEMP ('sender-mqtt-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$broker = $null
$sender = $null
try {
    $config = Join-Path $testRoot 'mosquitto.conf'
    [IO.File]::WriteAllText($config, "listener 18883 127.0.0.1`nallow_anonymous true`n", [Text.UTF8Encoding]::new($false))
    $broker = Start-Process -FilePath $BrokerExe -ArgumentList '-c', "`"$config`"" -PassThru
    Start-Sleep -Seconds 2
    if ($broker.HasExited) { throw 'Test broker exited.' }
    $env:LHM_PATH = Join-Path $PWD 'third_party/librehardwaremonitor/LibreHardwareMonitor.exe'
    foreach ($profile in @('full', 'esp')) {
        $content = "BROKER_HOST=127.0.0.1`nBROKER_PORT=18883`nSENDER_PROFILE=$profile`nSMART_POLL_INTERVAL_SEC=2`n"
        [IO.File]::WriteAllText((Join-Path $testRoot '.env'), $content, [Text.UTF8Encoding]::new($false))
        $sender = Start-Process -FilePath $SenderExe -WorkingDirectory $testRoot -PassThru
        & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile $profile --port 18883
        if ($LASTEXITCODE -ne 0) { throw "No compiled sender $profile payload." }
        if ($sender.HasExited) { throw "Compiled sender exited during $profile test." }
        & taskkill.exe /PID $sender.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop isolated $profile sender." }
        $sender = $null
    }
}
finally {
    if ($sender -and -not $sender.HasExited) { & taskkill.exe /PID $sender.Id /T /F | Out-Null }
    if ($broker -and -not $broker.HasExited) { Stop-Process -Id $broker.Id -Force }
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
