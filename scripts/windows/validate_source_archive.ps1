param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$Source
)
$ErrorActionPreference = 'Stop'
if ($Source -cnotmatch '^[a-f0-9]{40}$') { throw 'Invalid source commit.' }
if (Test-Path -LiteralPath $Destination) { throw 'Destination already exists.' }
if ((Get-Item -LiteralPath $Archive).Length -gt 134217728) { throw 'Source archive is too large.' }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    if ($zip.Entries.Count -eq 0 -or $zip.Entries.Count -gt 50000) { throw 'Invalid source entry count.' }
    $prefix = 'hwmonitor-mqtt-' + $Source + '/'
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $files = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $total = 0L
    foreach ($entry in $zip.Entries) {
        if (-not $entry.FullName.StartsWith($prefix, [StringComparison]::Ordinal)) { throw 'Invalid source identity.' }
        $relative = $entry.FullName.Substring($prefix.Length)
        if (-not $relative) { continue }
        $directory = $relative.EndsWith('/')
        $name = $relative.TrimEnd('/')
        if (-not $name -or -not $names.Add($name) -or $name.Contains('\')) { throw 'Unsafe or duplicate source path.' }
        foreach ($part in $name.Split('/')) {
            if (-not $part -or $part -in @('.', '..') -or $part -match '[<>:"|?*\x00-\x1f]' -or
                $part -match '[. ]$' -or $part -match '^(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)') {
                throw 'Unsafe source path.'
            }
        }
        $mode = ($entry.ExternalAttributes -shr 16) -band 0xF000
        if ($mode -notin @(0, 0x4000, 0x8000) -or ($directory -and $entry.Length -ne 0)) { throw 'Unsupported source entry type.' }
        if (-not $directory) { [void]$files.Add($name) }
        $total += $entry.Length
        if ($entry.Length -gt 134217728 -or $total -gt 536870912) { throw 'Source archive is too large.' }
    }
    foreach ($name in $names) {
        $parts = $name.Split('/')
        for ($i = 1; $i -lt $parts.Length; $i++) {
            if ($files.Contains(($parts[0..($i - 1)] -join '/'))) { throw 'Source file conflicts with a directory.' }
        }
    }
    foreach ($required in @('hwmonitor_mqtt/agents/agent_sender_windows.py',
            'scripts/windows/install_windows_sender_task.ps1', 'scripts/windows/start_windows_sender.ps1',
            'scripts/windows/activate_source.ps1', 'pyproject.toml', 'uv.lock', 'install.sh', '.python-version')) {
        if (-not $files.Contains($required)) { throw 'Source archive is incomplete.' }
    }
    try {
        [IO.Compression.ZipFileExtensions]::ExtractToDirectory($zip, $Destination)
    } catch {
        Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
} finally { $zip.Dispose() }
Join-Path $Destination ('hwmonitor-mqtt-' + $Source)
