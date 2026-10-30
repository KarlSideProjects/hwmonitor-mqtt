param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$Sha256,
    [Parameter(Mandatory = $true)][string]$Destination
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Destination) { throw 'Destination already exists.' }
if ($Sha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $Sha256) { throw 'Package SHA-256 mismatch.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName -notmatch '^(sender\.exe|manifest\.json|licenses/[a-zA-Z0-9._-]+)$' -or -not $names.Add($entry.FullName)) { throw 'Unsafe or unexpected package entry.' }
        $mode = ($entry.ExternalAttributes -shr 16) -band 0xF000
        if ($mode -eq 0xA000) { throw 'Package links are not allowed.' }
    }
    $exe = $zip.GetEntry('sender.exe')
    $manifestEntry = $zip.GetEntry('manifest.json')
    if (-not $exe -or -not $manifestEntry -or $manifestEntry.Length -gt 1048576 -or $exe.Length -gt 536870912) { throw 'Missing or oversized package entry.' }
    $reader = [IO.StreamReader]::new($manifestEntry.Open())
    try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
    if ($manifest.platform -ne 'windows-x64' -or $manifest.source -notmatch '^[a-f0-9]{40}$' -or -not $manifest.version -or -not $manifest.compiler) { throw 'Invalid package manifest.' }
    $stream = $exe.Open()
    try {
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $exeHash = ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-', '') } finally { $hash.Dispose() }
    } finally { $stream.Dispose() }
    if ($manifest.executable_sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $exeHash -ne $manifest.executable_sha256) { throw 'Executable SHA-256 mismatch.' }
    $stream = $exe.Open()
    $memory = [IO.MemoryStream]::new()
    try {
        $stream.CopyTo($memory)
        $memory.Position = 0
        $reader = [IO.BinaryReader]::new($memory)
        try {
            if ($memory.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5A4D) { throw 'Invalid executable architecture.' }
            $memory.Position = 60
            $offset = $reader.ReadUInt32()
            if ($offset -gt $memory.Length - 6) { throw 'Invalid executable architecture.' }
            $memory.Position = $offset
            if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) { throw 'Invalid executable architecture.' }
        } finally { $reader.Dispose() }
    } finally { $stream.Dispose(); $memory.Dispose() }
} finally { $zip.Dispose() }
Expand-Archive -LiteralPath $Archive -DestinationPath $Destination
