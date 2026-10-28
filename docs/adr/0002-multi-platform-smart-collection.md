# Multi-platform disk SMART collection and privilege fallback

Status: accepted

Collecting SMART data normally requires direct block-device access (`CAP_SYS_RAWIO` / `CAP_SYS_ADMIN` on Linux or Administrator privileges on Windows), which conflicts with sandboxing such as Proxmox VE's `DynamicUser=yes` native systemd service or unprivileged containers.

We adopt a platform-adaptive collection strategy that degrades gracefully (`status: "unavailable"`) rather than failing the sender:
- **Linux**: Query `smartctl --scan -j` and `smartctl -a -j <device>` on a slow background polling loop (default 300s). Container deployments mount `/dev:/dev:ro` and grant `SYS_RAWIO` and `SYS_ADMIN`.
- **Proxmox VE**: Attempt local `smartctl -j` first; when local device access is unavailable (such as under the native `DynamicUser=yes` service), query the Proxmox VE REST API (`/nodes/{node}/disks/list` and `/nodes/{node}/disks/smart`) using the existing `PVEAuditor` read-only token, parsing both structured attributes and plain-text SMART output into the canonical schema.
- **Windows**: Combine `smartctl -j` (when installed) with built-in Windows Storage WMI (`MSFT_PhysicalDisk` and `MSFT_StorageReliabilityCounter` in `root\Microsoft\Windows\Storage`) and `LibreHardwareMonitorLib` storage sensors so out-of-the-box Windows installations report critical health metrics without requiring manual third-party CLI installation.
