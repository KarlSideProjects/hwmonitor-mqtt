"""Read-only, cross-platform disk SMART normalization and alert evaluation."""

import json
import os
import re
import shutil
import subprocess
import time


_SEVERITY = {"unavailable": 0, "unknown": 1, "ok": 2, "warning": 3, "critical": 4}


def _number(value):
    try:
        value = float(value)
        return value if value >= 0 and value != float("inf") else None
    except (TypeError, ValueError):
        return None


def _env(name, default):
    return _number(os.getenv(name)) if _number(os.getenv(name)) is not None else default


class SmartCollector:
    def __init__(self, smartctl="smartctl", pve_client=None, provider=None,
                 monotonic=time.monotonic, wall_clock=time.time):
        self.smartctl, self.pve_client, self.provider = smartctl, pve_client, provider
        self.monotonic, self.wall_clock = monotonic, wall_clock
        self._crc_baselines = {}

    def _run(self, *args):
        return subprocess.run([self.smartctl, *args], capture_output=True, text=True, timeout=15)

    def poll_once(self, now=None):
        observed_at = int(self.wall_clock() if now is None else now)
        try:
            if self.provider:
                sources = self.provider()
            else:
                sources = self._smartctl_devices()
            devices, alerts = {}, []
            for item in sources or []:
                name, raw, meta = item if isinstance(item, tuple) else (item.get("device", item.get("name", "unknown")), item, {})
                device = _normalize_device(name, raw, meta, self._crc_baselines)
                _evaluate(device, alerts)
                devices[device["device"]] = device
            status = max((d["status"] for d in devices.values()), key=lambda x: _SEVERITY[x]) if devices else "unavailable"
            return {"observed_at": observed_at, "status": status, "alerts": alerts, "devices": devices}
        except Exception:
            return {"observed_at": observed_at, "status": "unavailable", "alerts": [], "devices": {}}

    def _smartctl_devices(self):
        try:
            if not shutil.which(self.smartctl):
                raise FileNotFoundError(self.smartctl)
            scan = json.loads(self._run("--scan", "-j").stdout)
            result = []
            for row in scan.get("devices", []):
                path = row.get("name")
                if not isinstance(path, str) or not (path.startswith("/") or path.lower().startswith("\\\\.\\physicaldrive")):
                    continue
                try:
                    data = json.loads(self._run("-a", "-j", path).stdout)
                except (OSError, subprocess.SubprocessError, ValueError, TypeError):
                    continue
                usable = isinstance(data, dict) and any((
                    isinstance(data.get("smart_status"), dict) and data["smart_status"].get("passed") is not None,
                    isinstance(data.get("critical_metrics"), dict) and bool(data["critical_metrics"]),
                    isinstance(data.get("ata_smart_attributes"), dict) and bool(data["ata_smart_attributes"].get("table")),
                    isinstance(data.get("nvme_smart_health_information_log"), dict) and bool(data["nvme_smart_health_information_log"]),
                    any(data.get(key) for key in ("scsi_error_counter_log", "scsi_grown_defect_list", "temperature", "power_on_time")),
                ))
                if not usable:
                    continue
                result.append((path, data, {}))
            if result or not self.pve_client:
                return result
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            if not self.pve_client: raise
        rows = self.pve_client.disks_list()
        result = []
        for row in rows:
            path = row.get("devpath") or row.get("path") or row.get("dev")
            if not isinstance(path, str): continue
            try:
                smart = self.pve_client.disk_smart(path)
                data = _pve_smart_to_json(smart)
                if data: result.append((path, data, row))
            except Exception:
                continue
        return result


def _normalize_device(name, data, metadata, baselines):
    data = data if isinstance(data, dict) else {}
    attrs = list(data.get("raw_attributes", [])) if isinstance(data.get("raw_attributes"), list) else []
    has_raw_attributes = bool(attrs)
    ata = data.get("ata_smart_attributes", {})
    for row in ata.get("table", []) if isinstance(ata, dict) else []:
        raw = row.get("raw", {})
        raw_value = raw.get("value", raw.get("string")) if isinstance(raw, dict) else raw
        attrs.append({"id": row.get("id"), "name": row.get("name", ""), "raw_value": raw_value,
                      "normalized_value": row.get("value"), "worst": row.get("worst"),
                      "threshold": row.get("thresh"), "when_failed": row.get("when_failed")})
    health = data.get("nvme_smart_health_information_log", {})
    if not isinstance(health, dict): health = {}
    scsi_errors = data.get("scsi_error_counter_log", {})
    if not isinstance(scsi_errors, dict): scsi_errors = {}
    scsi_error_values = [
        _number(scsi_errors.get(kind, {}).get("total_uncorrected_errors"))
        if isinstance(scsi_errors.get(kind), dict) else None for kind in ("read", "write", "verify")
    ]
    scsi_uncorrectable = sum(value for value in scsi_error_values if value is not None) if any(
        value is not None for value in scsi_error_values) else None
    grown_defects = data.get("scsi_grown_defect_list")
    if isinstance(grown_defects, dict):
        grown_defects = grown_defects.get("grown_defect_list_count")
    temperature = data.get("temperature") if isinstance(data.get("temperature"), dict) else {}
    power_on_time = data.get("power_on_time") if isinstance(data.get("power_on_time"), dict) else {}
    if not has_raw_attributes:
        attrs.extend({"id": None, "name": key, "raw_value": value, "normalized_value": None,
                      "worst": None, "threshold": health.get("available_spare_threshold") if key == "available_spare" else None,
                      "when_failed": None} for key, value in health.items())
        attrs.extend({"id": None, "name": f"scsi_error_counter_log.{kind}.total_uncorrected_errors",
                      "raw_value": section["total_uncorrected_errors"], "normalized_value": None,
                      "worst": None, "threshold": None, "when_failed": None}
                     for kind, section in scsi_errors.items() if isinstance(section, dict) and
                     section.get("total_uncorrected_errors") is not None)
        for key, value in (("scsi_grown_defect_list.grown_defect_list_count", grown_defects),
                           ("temperature.current", temperature.get("current")),
                           ("temperature.drive_trip", temperature.get("drive_trip")),
                           ("power_on_time.hours", power_on_time.get("hours"))):
            if value is not None:
                attrs.append({"id": None, "name": key, "raw_value": value, "normalized_value": None,
                              "worst": None, "threshold": None, "when_failed": None})
    values = {int(a["id"]): a for a in attrs if isinstance(a.get("id"), int)}
    def attr(ids):
        for ident in ids:
            if ident in values:
                return _number(values[ident]["raw_value"])
        return None
    wear = _number(health.get("percentage_used"))
    if wear is None:
        wear = _number(metadata.get("wearout", data.get("wearout")))
    if wear is None:
        life = next((_number(values[i].get("normalized_value")) for i in (177, 202, 231, 233) if i in values), None)
        if life is not None and life <= 100: wear = max(0.0, 100.0 - life)
    protocol = (data.get("device", {}).get("protocol") or metadata.get("type") or "unknown").lower()
    if protocol == "ata": protocol = "sata"
    if protocol == "scsi": protocol = "sas"
    rotation = _number(data.get("rotation_rate"))
    media = "nvme" if "nvme" in protocol else metadata.get("media_type", "hdd" if rotation and rotation > 0 else "ssd" if rotation == 0 else "unknown")
    temp = _number(health.get("temperature"))
    if temp is not None: temp -= 273.15 if temp > 200 else 0
    if temp is None: temp = _number(temperature.get("current"))
    if temp is None: temp = attr((194, 190))
    hours = _number(health.get("power_on_hours"))
    if hours is None: hours = _number(power_on_time.get("hours"))
    if hours is None: hours = attr((9,))
    written = _number(health.get("data_units_written"))
    if written is not None: written *= 512000
    else:
        sectors_written = attr((241,))
        if sectors_written is not None: written = sectors_written * 512
    metrics = {
        "overall_passed": data.get("smart_status", {}).get("passed") if isinstance(data.get("smart_status"), dict) else metadata.get("overall_passed"),
        "unstable_sectors": attr((197,)),
        "uncorrectable_errors": max([x for x in (attr((187,)), attr((198,)), _number(health.get("media_errors")), scsi_uncorrectable) if x is not None], default=None),
        "reallocated_sectors": _number(grown_defects) if grown_defects is not None else attr((5,)), "wear_percent": wear,
        "spare_percent": _number(health.get("available_spare")) if health.get("available_spare") is not None else attr((232,)),
        "interface_crc_errors": attr((199,)) if attr((199,)) is not None else _number(health.get("num_err_log_entries")),
        "temperature_c": temp, "power_on_hours": hours,
        "total_bytes_written": written,
    }
    for key, aliases in (("warning_temp", ("warning_temp", "warning_temperature")),
                         ("critical_temp", ("critical_temp", "critical_comp_temperature"))):
        metrics[key] = next((_number(health.get(alias, data.get(alias))) for alias in aliases
                             if _number(health.get(alias, data.get(alias))) is not None), None)
    if metrics["critical_temp"] is None:
        metrics["critical_temp"] = _number(temperature.get("drive_trip"))
    if "available_spare_threshold" in data:
        health = {**health, "available_spare_threshold": data["available_spare_threshold"]}
    if metrics.get("temperature_c") is not None and metrics["temperature_c"] > 200:
        metrics["temperature_c"] -= 273.15
    metrics.update(data.get("critical_metrics", {}))
    try:
        critical_warning = int(str(health.get("critical_warning", 0)), 0)
    except (TypeError, ValueError):
        critical_warning = _number(health.get("critical_warning")) or 0
    if critical_warning:
        metrics["overall_passed"] = False
    device = {"device": str(name), "model": data.get("model_name") or metadata.get("model"),
              "serial": data.get("serial_number") or metadata.get("serial"), "protocol": protocol,
              "media_type": media, "status": "unknown", "critical_metrics": metrics,
              "alerts": [], "raw_attributes": attrs}
    device["available_spare_threshold"] = _number(health.get("available_spare_threshold", data.get("available_spare_threshold"))) or 10
    count = metrics["interface_crc_errors"]
    if count is not None:
        baseline = baselines.setdefault(str(name), count)
        device["crc_delta"] = max(0, count - baseline)
    return device


def _pve_smart_to_json(value):
    if not isinstance(value, dict): return {}
    data = value.get("data", value)
    if not isinstance(data, dict): return {}
    if isinstance(data.get("attributes"), list):
        attrs = []
        for row in data["attributes"]:
            if isinstance(row, dict):
                attrs.append({"id": row.get("id"), "name": row.get("name", ""), "raw_value": row.get("raw_value", row.get("raw")),
                              "normalized_value": row.get("normalized_value", row.get("value")), "worst": row.get("worst"),
                              "threshold": row.get("threshold", row.get("thresh")), "when_failed": row.get("when_failed")})
        health = data.get("health")
        return {"raw_attributes": attrs, "model_name": data.get("model"),
                "smart_status": {"passed": str(health).upper() not in {"FAILED", "FAIL"} if health is not None else None}}
    text = data.get("text")
    if not isinstance(text, str): return data if "smart_status" in data else {}
    attrs = []
    for line in text.splitlines():
        columns = line.split()
        if len(columns) < 10 or not columns[0].isdigit():
            continue
        try:
            raw_value = int(columns[9]) if columns[9].isdigit() else columns[9]
            attrs.append({"id": int(columns[0]), "name": columns[1], "raw_value": raw_value,
                          "normalized_value": int(columns[3]), "worst": int(columns[4]),
                          "threshold": int(columns[5]), "when_failed": columns[8] if columns[8] != "-" else None})
        except (ValueError, IndexError):
            continue
    def field(*labels):
        for label in labels:
            match = re.search(rf"^\s*{label}\s*:\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, re.I | re.M)
            if match: return float(match.group(1).replace(",", ""))
        return None
    units_written = field("Data Units Written")
    spare_threshold = field("Available Spare Threshold")
    error_log_entries = field("Error Information Log Entries")
    for line in text.splitlines():
        match = re.match(r"\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            key, value = match.groups()
            raw = re.match(r"([0-9][0-9,]*(?:\.[0-9]+)?|0x[0-9a-f]+)", value, re.I)
            if raw:
                value = raw.group(1)
                parsed = int(value.replace(",", ""), 0) if value.lower().startswith("0x") else _number(value.replace(",", ""))
                attrs.append({"id": None, "name": key.strip(), "raw_value": parsed, "normalized_value": None,
                              "worst": None, "threshold": None, "when_failed": None})
    for attr in attrs:
        if attr.get("name", "").lower() == "available spare":
            attr["threshold"] = spare_threshold
    metrics = {
        "wear_percent": field("Percentage Used", "Wearout"),
        "spare_percent": field("Available Spare"),
        "uncorrectable_errors": field("Media and Data Integrity Errors", "Media Errors"),
        "temperature_c": field("Temperature"),
        "power_on_hours": field("Power On Hours", "Power_On_Hours"),
        "total_bytes_written": units_written * 512000 if units_written is not None else None,
        "warning_temp": field("Warning Temperature", "Warning Temp"),
        "critical_temp": field("Critical Temperature", "Critical Temp", "Critical Comp Temperature"),
        "interface_crc_errors": error_log_entries,
    }
    critical_warning = re.search(r"^\s*Critical Warning\s*:\s*(0x[0-9a-f]+|[0-9]+)", text, re.I | re.M)
    if critical_warning:
        try:
            warning = int(critical_warning.group(1), 0)
        except ValueError:
            warning = 0
        if warning:
            metrics["overall_passed"] = False
    if metrics["temperature_c"] is not None and metrics["temperature_c"] > 200:
        metrics["temperature_c"] = round(metrics["temperature_c"] - 273.15, 2)
    metrics = {key: value for key, value in metrics.items() if value is not None}
    health_match = re.search(r"^\s*SMART overall-health self-assessment test result\s*:\s*(.*?)\s*$|^\s*SMART Health Status\s*:\s*(.*?)\s*$", text, re.I | re.M)
    health_text = next((value for value in health_match.groups() if value is not None), "") if health_match else ""
    passed = (True if re.match(r"^(?:PASSED?|OK|GOOD)\b", health_text, re.I) else
              False if re.match(r"^(?:FAILED?|FAILURE|BAD|CRITICAL)\b", health_text, re.I) else None)
    return {"raw_attributes": attrs, "critical_metrics": metrics,
            "available_spare_threshold": spare_threshold,
            "smart_status": {"passed": passed}}


def _evaluate(device, host_alerts):
    m = device["critical_metrics"]
    alerts = device["alerts"]
    def check(metric, value, warn, crit, direction="high", message=None):
        if value is None: return
        level = "critical" if (value >= crit if direction == "high" else value <= crit) else (
            "warning" if (value >= warn if direction == "high" else value <= warn) else None)
        if level:
            alerts.append({"device": device["device"], "severity": level, "metric": metric, "value": value,
                           "threshold": crit if level == "critical" else warn,
                           "message": message or f"{metric} is {value}"})
    if m["overall_passed"] is False or any(str(a.get("when_failed", "")).upper() == "FAILING_NOW" for a in device["raw_attributes"]):
        check("overall_passed", False, 0, 0, message="SMART self-assessment failed")
    if m["unstable_sectors"] and m["unstable_sectors"] > 0:
        check("unstable_sectors", m["unstable_sectors"], 0, 0)
    if m["uncorrectable_errors"] and m["uncorrectable_errors"] > 0:
        check("uncorrectable_errors", m["uncorrectable_errors"], 0, 0)
    realloc = next((a for a in device["raw_attributes"] if a.get("id") == 5), {})
    vendor_critical = (_number(realloc.get("normalized_value")) is not None and
                       _number(realloc.get("threshold")) is not None and
                       realloc["normalized_value"] <= realloc["threshold"])
    if vendor_critical or (m["reallocated_sectors"] is not None and m["reallocated_sectors"] >= _env("SMART_REALLOC_CRIT", 10)):
        alerts.append({"device": device["device"], "severity": "critical", "metric": "reallocated_sectors",
                       "value": m["reallocated_sectors"], "threshold": realloc.get("threshold", _env("SMART_REALLOC_CRIT", 10)),
                       "message": f"reallocated_sectors is {m['reallocated_sectors']} (vendor threshold {realloc.get('threshold')})"})
    elif m["reallocated_sectors"] is not None and m["reallocated_sectors"] >= _env("SMART_REALLOC_WARN", 1):
        check("reallocated_sectors", m["reallocated_sectors"], _env("SMART_REALLOC_WARN", 1), float("inf"))
    check("wear_percent", m["wear_percent"], _env("SMART_WEAR_WARN_PCT", 80), _env("SMART_WEAR_CRIT_PCT", 100))
    check("spare_percent", m["spare_percent"], _env("SMART_SPARE_WARN_PCT", 20),
          _env("SMART_SPARE_CRIT_PCT", device["available_spare_threshold"]), "low")
    if m["interface_crc_errors"] is not None and device.get("crc_delta", 0) > 0:
        alerts.append({"device": device["device"], "severity": "warning", "metric": "interface_crc_errors",
                       "value": m["interface_crc_errors"], "threshold": device["crc_delta"],
                       "message": f"interface CRC errors {m['interface_crc_errors']} (delta {device['crc_delta']})"})
    hdd = device["media_type"] == "hdd"
    check("temperature_c", m["temperature_c"], _env("SMART_TEMP_WARN_HDD" if hdd else "SMART_TEMP_WARN_SSD", m.get("warning_temp") or (50 if hdd else 65)),
          _env("SMART_TEMP_CRIT_HDD" if hdd else "SMART_TEMP_CRIT_SSD", m.get("critical_temp") or (55 if hdd else 75)))
    device["status"] = max((a["severity"] for a in alerts), key=lambda x: _SEVERITY[x]) if alerts else (
        "ok" if any(value is not None for value in m.values()) else "unknown")
    host_alerts.extend(alerts)
    device.pop("available_spare_threshold", None)
    device.pop("crc_delta", None)
