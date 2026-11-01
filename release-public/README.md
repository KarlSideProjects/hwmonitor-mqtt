# HWMonitor MQTT Sender

Download compiled Windows x64, Linux x64 and Linux ARM64 packages from the [latest public release](https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest). This repository distributes packages, bootstrap scripts, version metadata and license notices. Credentials stay on the installed machine.

## Linux

Use x64 or ARM64 Linux with glibc 2.31 or later, Bash, curl, jq, tar, sha256sum, runuser, od and flock. Standard Linux installation uses Docker Compose v2. Existing PVE detection and native-service support remain available; a separate PVE VM is not a release requirement. Run when a Sender restart is acceptable.

```bash
release_tag=$(curl --fail --silent --show-error https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest | jq -er '.tag_name')
[[ "$release_tag" =~ ^v0\.1\.[0-9]+$ ]] || exit 1
release_base="https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/download/$release_tag"
curl --fail --location "$release_base/install.sh" --output install.sh
curl --fail --location "$release_base/install.sh.sha256" --output install.sh.sha256
sha256sum --check install.sh.sha256 && sudo bash ./install.sh
```

The bootstrap prompts for local MQTT settings, preserves recognized existing settings and verifies the complete compiled package. It refuses unrecognized directories and conflicting PVE Docker installations; arrange migration before retrying.

## Windows

Run in 64-bit PowerShell as Administrator when a Sender restart is acceptable.

```powershell
$releaseTag = (Invoke-RestMethod 'https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest').tag_name
if ($releaseTag -cnotmatch '^v0\.1\.[0-9]+$') { throw 'Unexpected release tag' }
$releaseBase = "https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/download/$releaseTag"
Invoke-WebRequest "$releaseBase/install.ps1" -OutFile '.\install.ps1'
Invoke-WebRequest "$releaseBase/install.ps1.sha256" -OutFile '.\install.ps1.sha256'
$expectedHash = ((Get-Content '.\install.ps1.sha256' -Raw).Trim() -split '\s+')[0]
if ($expectedHash -cnotmatch '^[a-f0-9]{64}$' -or (Get-FileHash '.\install.ps1' -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Installer checksum mismatch' }
Unblock-File '.\install.ps1'
.\install.ps1
```

Windows monitoring requires compatible LibreHardwareMonitor components, drivers and privileges. Successful installation does not establish sensor accuracy on every hardware model.

Karl's software is free for noncommercial use. Commercial use, including internal company use, requires prior written permission under [the license](LICENSE-NOTICE.md). Third-party terms and notices remain applicable; previously granted GPL rights remain effective.
