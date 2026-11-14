param([Parameter(Mandatory=$true)][string]$State)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$validator=Join-Path (Split-Path $PSScriptRoot) 'scripts/windows/validate_source_archive.ps1'
$commit='a'*40
$required=@('hwmonitor_mqtt/agents/agent_sender_windows.py','scripts/windows/install_windows_sender_task.ps1','scripts/windows/start_windows_sender.ps1','scripts/windows/activate_source.ps1','pyproject.toml','uv.lock','install.sh','.python-version','README.md')
$work=Join-Path $State ('archive-test-'+[Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($work) | Out-Null
try {
 foreach($kind in @('old','new','mixed','wrong')) {
  $zip=Join-Path $work ($kind+'.zip');$out=Join-Path $work ($kind+'-out')
  $prefix=if ($kind -eq 'new') { 'JWJhome-agent-'+$commit+'/' } elseif ($kind -eq 'wrong') { 'other-'+$commit+'/' } else { 'hwmonitor-mqtt-'+$commit+'/' }
  $archive=[IO.Compression.ZipFile]::Open($zip,[IO.Compression.ZipArchiveMode]::Create)
  try {
   foreach($name in $required) { [void]$archive.CreateEntry($prefix+$name) }
   if ($kind -eq 'mixed') { [void]$archive.CreateEntry('JWJhome-agent-'+$commit+'/extra') }
  } finally { $archive.Dispose() }
  $failed=$false;try { $result=& $validator -Archive $zip -Destination $out -Source $commit } catch { $failed=$true }
  if ($kind -in @('old','new')) {
   if ($failed -or !(Test-Path -LiteralPath (Join-Path $result $required[0]))) { throw "$kind failed" }
  } elseif (!$failed -or (Test-Path -LiteralPath $out)) { throw "$kind was accepted" }
  Write-Output "$kind pass"
 }
} finally { Remove-Item -LiteralPath $work -Recurse -Force }
