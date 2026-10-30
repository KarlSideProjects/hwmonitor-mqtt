param([Parameter(Mandatory = $true)][string]$SenderExe, [Parameter(Mandatory = $true)][string]$BrokerExe)
$ErrorActionPreference = 'Stop'
Remove-Item Env:BROKER_HOST, Env:BROKER_PORT, Env:MQTT_USER, Env:MQTT_PASS, Env:SENDER_PROFILE, Env:LHM_AUTOSTART, Env:SMART_POLL_INTERVAL_SEC -ErrorAction SilentlyContinue
$testRoot = Join-Path $env:RUNNER_TEMP ('sender-mqtt-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$broker = $null
$sender = $null
New-Item -ItemType Directory -Path 'test-evidence' -Force | Out-Null
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
        $stdout = Join-Path $PWD "test-evidence/direct-$profile.stdout.log"
        $stderr = Join-Path $PWD "test-evidence/direct-$profile.stderr.log"
        $sender = Start-Process -FilePath $SenderExe -WorkingDirectory $testRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile $profile --port 18883
        if ($LASTEXITCODE -ne 0) { throw "No compiled sender $profile payload." }
        if ($sender.HasExited) { throw "Compiled sender exited during $profile test." }
        & taskkill.exe /PID $sender.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop isolated $profile sender." }
        $sender = $null
    }
    $baselineCode = @'
import json
try:
    import pythoncom, wmi
    pythoncom.CoInitialize()
    try:
        storage = wmi.WMI(namespace=r"root\Microsoft\Windows\Storage")
        physical = storage.MSFT_PhysicalDisk()
        storage.MSFT_StorageReliabilityCounter()
        print(json.dumps({"available": True, "physical_count": len(physical)}))
    finally:
        pythoncom.CoUninitialize()
except Exception as error:
    print(json.dumps({"available": False, "error_type": type(error).__name__}))
'@
    $baselineOutput = & .venv/Scripts/python.exe -c $baselineCode
    if ($LASTEXITCODE -ne 0) { throw 'Could not run WMI baseline.' }
    $baseline = $baselineOutput | Select-Object -Last 1 | ConvertFrom-Json
    $baseline | ConvertTo-Json | Set-Content -Path 'test-evidence/wmi-baseline.json' -Encoding UTF8
    if ($baseline.available -and $baseline.physical_count -gt 0 -and -not (Get-Command smartctl -ErrorAction SilentlyContinue)) {
        $env:LHM_PATH = Join-Path $testRoot 'missing-lhm.exe'
        $content = "BROKER_HOST=127.0.0.1`nBROKER_PORT=18883`nSENDER_PROFILE=full`nSMART_POLL_INTERVAL_SEC=2`nLHM_AUTOSTART=0`n"
        [IO.File]::WriteAllText((Join-Path $testRoot '.env'), $content, [Text.UTF8Encoding]::new($false))
        $sender = Start-Process -FilePath $SenderExe -WorkingDirectory $testRoot -PassThru
        & .venv/Scripts/python.exe tests/windows_mqtt_observer.py --profile full --port 18883 --min-devices 1
        if ($LASTEXITCODE -ne 0) { throw 'Compiled sender did not publish a Windows WMI storage device.' }
        Write-Output 'Compiled Windows Storage WMI fallback observed on the hosted VM.'
        & taskkill.exe /PID $sender.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop isolated WMI sender.' }
        $sender = $null
    }
    else { Write-Output 'Compiled WMI fallback remains unverified: baseline unavailable, no devices, or smartctl is present.' }
}
finally {
    if ($sender -and -not $sender.HasExited) { & taskkill.exe /PID $sender.Id /T /F | Out-Null }
    if ($broker -and -not $broker.HasExited) { Stop-Process -Id $broker.Id -Force }
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
