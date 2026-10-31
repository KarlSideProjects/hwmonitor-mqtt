param(
    [string]$ArtifactDir,
    [string]$HelperDir,
    [string]$EvidenceDir,
    [switch]$InsideContainer
)
$ErrorActionPreference = 'Stop'
$archiveHash = '31b8ebebc9004896438bed9c531313a1403df15adb648e5db2c7c6fcb173328c'
$source = 'da634ab491076353d769681292ff174c9abfd3c5'
$expectedError = 'Install the Microsoft Visual C++ x64 Redistributable 14.44.35211 or later from https://aka.ms/vc14/vc_redist.x64.exe, then retry.'
if ($InsideContainer) {
    $evidence = 'C:\qa-evidence'
    New-Item -ItemType Directory $evidence | Out-Null
    function Facts {
        $regPath = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
        $runtime = Get-ItemProperty -LiteralPath $regPath -ErrorAction SilentlyContinue
        $dlls = foreach ($name in @('mfc140u.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
            $file = Get-Item -LiteralPath (Join-Path "$env:WINDIR\System32" $name) -ErrorAction SilentlyContinue
            [ordered]@{ name = $name; exists = [bool]$file; version = if ($file) { $file.VersionInfo.FileVersion } else { $null } }
        }
        $uninstall = foreach ($key in @('Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
            Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '(?i)^Microsoft Visual C\+\+(?=.*(Redistributable|Runtime))(?=.*(x64|64-bit))' } | Select-Object DisplayName, DisplayVersion, PSChildName
        }
        $task = try {
            $service = Get-Service Schedule -ErrorAction Stop
            $cmd = Get-Command Get-ScheduledTask -ErrorAction Stop
            $items = @(Get-ScheduledTask -ErrorAction Stop | Where-Object TaskName -eq 'HWMonitorMQTTSender' | Select-Object TaskName, State)
            @{ available = $true; service = $service.Status.ToString(); tasks = $items }
        } catch { @{ available = $false; reason = $_.Exception.Message } }
        [ordered]@{ runtime = if ($runtime) { @{ installed = $runtime.Installed; version = $runtime.Version } } else { $null }; dlls = @($dlls); uninstall = @($uninstall); scheduler = $task }
    }
    function Inventory($path) {
        @(Get-ChildItem -LiteralPath $path -Recurse -Force | Sort-Object FullName | ForEach-Object {
            $relative = $_.FullName.Substring($path.Length)
            if ($_.PSIsContainer) { "D $relative" } else { "F $relative $($_.Length) $((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" }
        }) -join "`n"
    }
    $result = [ordered]@{ limitation = 'Server Core container only; fixture cffi notices incomplete; no release or physical-host approval'; before = $null; after = $null; negative = 'not run'; positive = 'not run' }
    try {
        $result.before = Facts
        & 'C:\fixture\validate_sender_package.ps1' -Archive 'C:\fixture\sender-windows-x64.zip' -Sha256 $archiveHash -Destination 'C:\stage'
        $inventory = Inventory 'C:\stage'
        $root = 'C:\project-not-created'
        try {
            & 'C:\fixture\activate_sender_package.ps1' -ProjectRoot $root -StagedPackage 'C:\stage' -Source $source -CheckPrerequisitesOnly
            throw 'INCONCLUSIVE: base image prerequisite is already ready'
        } catch {
            if ($_.Exception.Message -cne $expectedError) { throw }
        }
        if ((Test-Path $root) -or (Inventory 'C:\stage') -cne $inventory) { throw 'Negative preflight changed root or staged inventory' }
        $result.negative = 'passed: exact prerequisite error and unchanged inventory'
        $process = Start-Process -FilePath 'C:\fixture\vc_redist.x64.exe' -ArgumentList '/install', '/quiet', '/norestart', '/log', 'C:\qa-evidence\installer.log' -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Redistributable exit $($process.ExitCode): failure or reboot-required inconclusive" }
        $result.after = Facts
        & 'C:\fixture\activate_sender_package.ps1' -ProjectRoot $root -StagedPackage 'C:\stage' -Source $source -CheckPrerequisitesOnly
        if ((Test-Path $root) -or (Inventory 'C:\stage') -cne $inventory) { throw 'Positive preflight changed root or staged inventory' }
        $result.positive = 'passed: prerequisite check and unchanged inventory'
    } catch { $result.failure = $_.Exception.Message; throw }
    finally { $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence 'container.json') -Encoding UTF8 }
    exit 0
}
if (-not $ArtifactDir -or -not $HelperDir -or -not $EvidenceDir) { throw 'Host requires artifact, helper and evidence directories' }
New-Item -ItemType Directory -Force $EvidenceDir | Out-Null
$fixture = Join-Path $env:RUNNER_TEMP ('container-fixture-' + [guid]::NewGuid().ToString('N'))
$name = 'hwmonitor-qa-' + [guid]::NewGuid().ToString('N')
$image = 'mcr.microsoft.com/windows/servercore@sha256:593cf4a5ef08cfff00a03637e45eba073b8fe9dc90b80af4449bc5f427c7cdcd'
$created = $false
try {
    New-Item -ItemType Directory $fixture | Out-Null
    $info = docker info --format '{{json .}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $info.OSType -ne 'windows') { throw 'INCONCLUSIVE: Docker Windows engine unavailable' }
    docker pull $image
    if ($LASTEXITCODE -ne 0) { throw 'Image pull failed' }
    $imageInfo = docker image inspect $image | ConvertFrom-Json
    @{ dockerOSType = $info.OSType; dockerVersion = $info.ServerVersion; hostOS = [Environment]::OSVersion.VersionString; imageId = $imageInfo[0].Id; repoDigests = $imageInfo[0].RepoDigests } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $EvidenceDir 'host.json')
    $archive = Join-Path $ArtifactDir 'assets/sender-windows-x64.zip'
    if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $archiveHash) { throw 'Fixture archive hash mismatch' }
    Copy-Item $archive (Join-Path $fixture 'sender-windows-x64.zip')
    foreach ($pair in @(@('validate_sender_package.ps1', 'e79122c3e16755e736e7e54c4e88ce313a9e3ca5'), @('activate_sender_package.ps1', 'ccbad7324f8ca376ecf80e7f915428141e7dcaec'))) {
        $helper = Join-Path $HelperDir ('scripts/windows/' + $pair[0])
        $blob = git hash-object $helper
        if ($LASTEXITCODE -ne 0 -or $blob -ne $pair[1]) { throw "Helper blob mismatch: $($pair[0])" }
        Copy-Item $helper (Join-Path $fixture $pair[0])
    }
    Copy-Item $PSCommandPath (Join-Path $fixture 'test.ps1')
    $redist = Join-Path $fixture 'vc_redist.x64.exe'
    Invoke-WebRequest 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $redist
    if ((Get-FileHash $redist -Algorithm SHA256).Hash -ne '843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c') { throw 'Redistributable hash mismatch' }
    $signature = Get-AuthenticodeSignature $redist
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation' -or (Get-Item $redist).VersionInfo.ProductVersion -ne '14.51.36247.0') { throw 'Redistributable signature or version mismatch' }
    docker create --name $name --label "hwmonitor.qa.owner=$name" --isolation=process --network=none --mount "type=bind,source=$fixture,destination=C:\fixture,readonly" $image powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\fixture\test.ps1' -InsideContainer
    if ($LASTEXITCODE -ne 0) { throw 'INCONCLUSIVE: process-isolated container creation failed' }
    $created = $true
    docker start -a $name *> (Join-Path $EvidenceDir 'test.log')
    $exitCode = $LASTEXITCODE
    $state = docker inspect --format '{{json .State}}' $name | ConvertFrom-Json
    $inspectExitCode = $LASTEXITCODE
    docker cp "${name}:C:\qa-evidence\." $EvidenceDir
    if ($LASTEXITCODE -ne 0) { throw 'Container evidence unavailable' }
    if ($inspectExitCode -ne 0 -or $null -eq $state -or $state.Running -ne $false -or $state.Status -cne 'exited' -or $state.ExitCode -ne 0) { throw 'Container state did not establish a successful exit' }
    if ($exitCode -ne 0) { throw "Container test failed: $exitCode" }
    $result = Get-Content -LiteralPath (Join-Path $EvidenceDir 'container.json') -Raw | ConvertFrom-Json
    if ($null -eq $result -or $result.failure -or $result.negative -cne 'passed: exact prerequisite error and unchanged inventory' -or $result.positive -cne 'passed: prerequisite check and unchanged inventory') { throw 'Container evidence is missing, incomplete, or reports failure' }
} finally {
    $cleanupFailed = $false
    foreach ($file in @(Get-ChildItem -LiteralPath $EvidenceDir -File)) {
        if ($file.Length -gt 1048576) { Remove-Item -LiteralPath $file.FullName -Force; $cleanupFailed = $true }
    }
    if ($created) {
        docker rm -f $name
        if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
    }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
    if ((Test-Path -LiteralPath $fixture) -or $cleanupFailed) { throw 'Cleanup failed or evidence exceeded 1 MiB per file' }
}
