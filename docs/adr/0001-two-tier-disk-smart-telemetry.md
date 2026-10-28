# Two-tier disk SMART telemetry envelope with critical metrics and raw attributes

Status: accepted

Storage devices across Linux, Proxmox VE, and Windows expose incompatible health schemas: SATA drives report numbered SMART attributes (`1..255`) with normalized and raw values, NVMe controllers report a fixed health log page without numeric attribute IDs, and Windows Storage WMI (`MSFT_PhysicalDisk` / `MSFT_StorageReliabilityCounter`) and LibreHardwareMonitor expose OS-level reliability counters and sensors. Publishing only raw attributes forces every downstream MQTT subscriber to implement vendor- and protocol-specific parsing, while publishing only distilled metrics hides vendor-specific attributes needed for deep diagnostics.

We publish a top-level `disk_smart` snapshot in `full` profile payloads containing both:
1. Host-level aggregated `status` (`ok`, `warning`, `critical`, `unknown`, `unavailable`) and structured `alerts` for immediate alerting.
2. Per-device `critical_metrics` (normalized across SATA, NVMe, PVE API, and Windows WMI/LHM) alongside the full list of `raw_attributes` formatted with a uniform object schema (`id`, `name`, `raw_value`, `normalized_value`, `worst`, `threshold`, `when_failed`).

If the serialized MQTT payload exceeds the 256 KiB safety limit (`MAX_PAYLOAD_BYTES`), the sender preserves `status`, `alerts`, and per-device `critical_metrics` while truncating `raw_attributes` so critical health alerts are never dropped due to payload size. In `esp` profile payloads, only a top-level `disk_health` status string is emitted, and any state transition triggers an immediate publish.
