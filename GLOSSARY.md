# Hardware Monitor MQTT

Hardware Monitor MQTT collects host, storage, and virtualization telemetry across Linux, Windows, and Proxmox VE hosts and publishes normalized JSON payloads over MQTT for real-time viewers and alerting consumers.

## Language

### Telemetry & Profiles

**Sender Profile**:
A configuration mode (`full` or `esp`) governing payload richness and publish frequency. `full` emits complete telemetry snapshots at a fixed interval; `esp` emits compact summaries gated by delta thresholds and heartbeats for microcontroller displays.
_Avoid_: Mode, output format, payload type

### Storage & Disk SMART

**Disk SMART Snapshot (`disk_smart`)**:
The top-level payload envelope representing the health summary, active alerts, normalized critical metrics, and raw SMART attributes for all physical storage devices on a host.
_Avoid_: SMART info, disk status, drive health block

**Critical SMART Metrics (`critical_metrics`)**:
A vendor- and protocol-agnostic set of normalized storage health indicators (such as unstable sectors, reallocated sectors, media errors, wear percentage, spare percentage, interface CRC errors, temperature, power-on hours, and total bytes written) distilled from heterogeneous SATA, SAS, NVMe, or OS storage telemetry.
_Avoid_: Key SMART fields, normalized attributes, important values

**Raw SMART Attributes (`raw_attributes`)**:
The complete, unfiltered list or dictionary of device-specific SMART attributes or NVMe/OS health log entries reported by the underlying hardware provider for a single physical disk.
_Avoid_: SMART table, raw data, full SMART dump

**SMART Alert (`alerts`)**:
A structured warning or critical notification generated when a disk's Critical SMART Metrics or Raw SMART Attributes breach a defined health threshold or report hardware failure.
_Avoid_: Disk error, SMART alarm, warning message

**Disk Health Status**:
The discrete severity state (`ok`, `warning`, `critical`, `unknown`, or `unavailable`) of either an individual physical disk or the aggregated host storage subsystem.
_Avoid_: Drive state, SMART pass/fail
