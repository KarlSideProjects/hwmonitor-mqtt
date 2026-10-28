import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hwmonitor_mqtt.collectors.smart import SmartCollector


def test_smartctl_snapshot_normalizes_sata_and_nvme_and_tracks_crc_delta(monkeypatch):
    outputs = {
        "/dev/sda": {
            "model_name": "Disk A", "serial_number": "a1", "device": {"protocol": "ATA"},
            "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "thresh": 10, "raw": {"value": 0}},
                {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 2}},
                {"id": 199, "name": "UDMA_CRC_Error_Count", "raw": {"value": 3}},
                {"id": 194, "name": "Temperature_Celsius", "raw": {"value": 40}},
            ]},
        },
        "/dev/nvme0": {
            "model_name": "NVMe", "device": {"protocol": "NVMe"}, "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": [{"id": 5, "name": "Reallocated", "raw": {"value": 0}}]},
            "nvme_smart_health_information_log": {"critical_warning": "0x00", "percentage_used": 85,
                "available_spare": 15, "media_errors": 1, "temperature": 305,
                "power_on_hours": 123, "data_units_written": 2_000_000},
        },
    }

    class Result:
        returncode = 0
        def __init__(self, value): self.stdout = json.dumps(value)

    def run(args, **kwargs):
        if args[1] == "--scan":
            return Result({"devices": [{"name": "/dev/sda"}, {"name": "/dev/nvme0"}]})
        return Result(outputs[args[-1]])

    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", run)
    collector = SmartCollector(monotonic=lambda: 1)
    first = collector.poll_once(now=100)
    assert first["status"] == "critical"
    assert first["devices"]["/dev/sda"]["critical_metrics"]["unstable_sectors"] == 2
    assert first["devices"]["/dev/sda"]["raw_attributes"][0]["id"] == 5
    assert not any(a["metric"] == "interface_crc_errors" for a in first["alerts"])
    assert first["devices"]["/dev/nvme0"]["critical_metrics"]["total_bytes_written"] == 2_000_000 * 512_000
    nvme_attrs = first["devices"]["/dev/nvme0"]["raw_attributes"]
    assert {a["name"] for a in nvme_attrs} >= {"critical_warning", "percentage_used", "available_spare", "data_units_written"}
    health_attrs = [a for a in nvme_attrs if a["id"] is None]
    assert all(a["normalized_value"] is None and a["worst"] is None for a in health_attrs)
    assert first["devices"]["/dev/nvme0"]["critical_metrics"]["overall_passed"] is True

    outputs["/dev/sda"]["ata_smart_attributes"]["table"][2]["raw"]["value"] = 4
    second = collector.poll_once(now=101)
    crc = [a for a in second["alerts"] if a["metric"] == "interface_crc_errors"]
    assert crc and "delta 1" in crc[0]["message"]


def test_missing_smartctl_is_unavailable(monkeypatch):
    def run(*args, **kwargs): raise FileNotFoundError()
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", run)
    assert SmartCollector().poll_once()["status"] == "unavailable"


def test_smartctl_open_failure_uses_pve_fallback_or_unavailable(monkeypatch):
    class Result:
        def __init__(self, value): self.stdout = json.dumps(value)

    def run(args, **kwargs):
        if args[1] == "--scan": return Result({"devices": [{"name": "/dev/sda"}]})
        return Result({"smartctl": {"exit_status": 2}, "device": {"protocol": "ATA"},
                       "smartctl": {"exit_status": 2, "messages": [{"string": "Permission denied"}]}})

    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: "/usr/sbin/smartctl")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", run)
    class Pve:
        def __init__(self): self.called = 0
        def disks_list(self): self.called += 1; return [{"devpath": "/dev/sda", "type": "ata"}]
        def disk_smart(self, _): return {"attributes": [{"id": 197, "name": "Current_Pending_Sector", "raw_value": 1}]}
    pve = Pve()
    assert SmartCollector(pve_client=pve).poll_once()["devices"]["/dev/sda"]["critical_metrics"]["unstable_sectors"] == 1
    assert pve.called == 1
    assert SmartCollector().poll_once()["status"] == "unavailable"


def test_smartctl_merges_local_and_pve_results_for_inaccessible_disks(monkeypatch):
    class Result:
        def __init__(self, value): self.stdout = json.dumps(value)
    def run(args, **kwargs):
        if args[1] == "--scan": return Result({"devices": [{"name": "/dev/sda"}, {"name": "/dev/sdb"}]})
        if args[-1] == "/dev/sda": return Result({"smart_status": {"passed": True}, "device": {"protocol": "ATA"}})
        return Result({"smartctl": {"exit_status": 2}, "smart_status": {"passed": None}})
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: "/usr/sbin/smartctl")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", run)
    class Pve:
        def disks_list(self): return [{"devpath": "/dev/sda"}, {"devpath": "/dev/sdb"}]
        def disk_smart(self, path):
            assert path == "/dev/sdb"
            return {"health": "FAILED!", "attributes": []}
    snapshot = SmartCollector(pve_client=Pve()).poll_once()
    assert set(snapshot["devices"]) == {"/dev/sda", "/dev/sdb"}
    assert snapshot["devices"]["/dev/sdb"]["critical_metrics"]["overall_passed"] is False
    assert snapshot["status"] == "critical"


def test_smartctl_preserves_usable_data_with_disk_failure_exit_bits(monkeypatch):
    class Result:
        def __init__(self, value): self.stdout = json.dumps(value)
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: "/usr/sbin/smartctl")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", lambda args, **kwargs:
        Result({"devices": [{"name": "/dev/sda"}]} if args[1] == "--scan" else {
            "smartctl": {"exit_status": 4}, "device": {"protocol": "ATA"}, "smart_status": {"passed": False}}))
    assert SmartCollector().poll_once()["devices"]["/dev/sda"]["status"] == "critical"


def test_pve_inventory_api_error_degrades_to_unavailable(monkeypatch):
    class Pve:
        def disks_list(self): raise RuntimeError("API unavailable")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    snapshot = SmartCollector(pve_client=Pve()).poll_once()
    assert snapshot["status"] == "unavailable" and snapshot["alerts"] == []


def test_pve_inventory_api_error_preserves_local_smart_results(monkeypatch):
    class Result:
        def __init__(self, value): self.stdout = json.dumps(value)
    def run(args, **kwargs):
        if args[1] == "--scan":
            return Result({"devices": [{"name": "/dev/sda"}, {"name": "/dev/sdb"}]})
        if args[-1] == "/dev/sda":
            return Result({"smart_status": {"passed": False}})
        return Result({"smart_status": {"passed": None}})
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: "/usr/sbin/smartctl")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.subprocess.run", run)
    class Pve:
        def disks_list(self): raise RuntimeError("API unavailable")
    snapshot = SmartCollector(pve_client=Pve()).poll_once()
    assert set(snapshot["devices"]) == {"/dev/sda"}
    assert snapshot["devices"]["/dev/sda"]["critical_metrics"]["overall_passed"] is False
    assert snapshot["status"] == "critical"


def test_pve_smart_api_fallback_normalizes_structured_and_text(monkeypatch):
    class Pve:
        def disks_list(self):
            return [{"devpath": "/dev/sda", "model": "SATA", "type": "ata"},
                    {"devpath": "/dev/nvme0", "model": "NVMe", "type": "nvme"}]
        def disk_smart(self, disk):
            if disk.endswith("sda"):
                return {"attributes": [{"id": 197, "name": "Current_Pending_Sector", "raw_value": 2}]}
            return {"text": "Percentage Used: 85%\nAvailable Spare: 15%\nMedia and Data Integrity Errors: 1\nTemperature: 305 K"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    snapshot = SmartCollector(pve_client=Pve()).poll_once(now=7)
    assert snapshot["devices"]["/dev/sda"]["critical_metrics"]["unstable_sectors"] == 2
    nvme = snapshot["devices"]["/dev/nvme0"]["critical_metrics"]
    assert nvme["wear_percent"] == 85
    assert nvme["uncorrectable_errors"] == 1
    assert nvme["temperature_c"] == 31.85


def test_pve_structured_attributes_accept_provider_aliases_and_string_ids(monkeypatch):
    class Pve:
        def disks_list(self): return [{"devpath": "/dev/sda", "type": "ssd"}]
        def disk_smart(self, _):
            return {"health": "PASSED", "attributes": [{"id": "197", "name": "Current_Pending_Sector",
                    "raw": "3", "normalized": 100, "fail": "FAILING_NOW"}]}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    device = SmartCollector(pve_client=Pve()).poll_once()["devices"]["/dev/sda"]
    assert device["critical_metrics"]["unstable_sectors"] == 3
    assert device["raw_attributes"][0]["id"] == 197
    assert device["raw_attributes"][0]["raw_value"] == "3"
    assert device["raw_attributes"][0]["normalized_value"] == 100
    assert device["raw_attributes"][0]["when_failed"] == "FAILING_NOW"
    assert device["status"] == "critical"


def test_pve_health_status_is_shared_by_structured_text_and_inventory(monkeypatch):
    class Pve:
        def disks_list(self): return [
            {"devpath": "/dev/sda"}, {"devpath": "/dev/sdb"}, {"devpath": "/dev/sdc", "health": "FAILED!"}]
        def disk_smart(self, path):
            if path.endswith("sda"): return {"health": "FAILED!", "attributes": []}
            if path.endswith("sdb"): return {"health": "UNKNOWN", "text": "Device Model: Disk"}
            return {"text": "Device Model: Disk"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    devices = SmartCollector(pve_client=Pve()).poll_once()["devices"]
    assert devices["/dev/sda"]["critical_metrics"]["overall_passed"] is False
    assert devices["/dev/sdb"]["critical_metrics"]["overall_passed"] is None
    assert devices["/dev/sdc"]["critical_metrics"]["overall_passed"] is False


def test_pve_inventory_hdd_uses_hdd_temperature_threshold(monkeypatch):
    class Pve:
        def disks_list(self): return [{"devpath": "/dev/sda", "type": "hdd"}]
        def disk_smart(self, _): return {"text": "Temperature: 56 Celsius"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    device = SmartCollector(pve_client=Pve()).poll_once()["devices"]["/dev/sda"]
    assert device["media_type"] == "hdd"
    assert next(a for a in device["alerts"] if a["metric"] == "temperature_c")["severity"] == "critical"


def test_sata_available_spare_uses_attribute_normalized_percentage():
    for normalized, expected_status in ((100, "ok"), (15, "warning")):
        source = ("/dev/sda", {"device": {"protocol": "ATA"}, "ata_smart_attributes": {"table": [
            {"id": 232, "name": "Available_Reserved_Space", "value": normalized, "raw": {"value": 0}}]}}, {})
        device = SmartCollector(provider=lambda: [source]).poll_once()["devices"]["/dev/sda"]
        assert device["critical_metrics"]["spare_percent"] == normalized
        assert device["status"] == expected_status


def test_threshold_environment_overrides_and_sata_life_left_are_applied(monkeypatch):
    monkeypatch.setenv("SMART_REALLOC_WARN", "2")
    monkeypatch.setenv("SMART_REALLOC_CRIT", "4")
    collector = SmartCollector(provider=lambda: [("/dev/sdb", {
        "model_name": "SSD", "device": {"protocol": "ATA"}, "rotation_rate": 0,
        "ata_smart_attributes": {"table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 2}},
            {"id": 177, "name": "Wear_Leveling_Count", "value": 15, "raw": {"value": 15}},
        ]}}, {})])
    snapshot = collector.poll_once(now=3)
    device = snapshot["devices"]["/dev/sdb"]
    assert device["critical_metrics"]["wear_percent"] == 85
    assert device["status"] == "warning"
    assert next(a for a in snapshot["alerts"] if a["metric"] == "reallocated_sectors")["severity"] == "warning"


def test_pve_sata_text_table_retains_uniform_raw_attribute_schema(monkeypatch):
    class Pve:
        def disks_list(self): return [{"devpath": "/dev/sda", "type": "ata"}]
        def disk_smart(self, _):
            return {"text": "ID# ATTRIBUTE_NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE\n"
                           "197 Current_Pending_Sector 0x0012 100 100 000 Old_age Always - 3"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    device = SmartCollector(pve_client=Pve()).poll_once()["devices"]["/dev/sda"]
    assert device["critical_metrics"]["unstable_sectors"] == 3
    assert device["raw_attributes"] == [{"id": 197, "name": "Current_Pending_Sector", "raw_value": 3,
        "normalized_value": 100, "worst": 100, "threshold": 0, "when_failed": None}]


def test_pve_nvme_text_parses_reliability_fields_and_temperature_thresholds(monkeypatch):
    class Pve:
        def disks_list(self): return [{"devpath": "/dev/nvme0", "type": "nvme"}]
        def disk_smart(self, _):
            return {"text": "Data Units Written: 1,234,567 [632 GB]\nCritical Warning: 0x0002\n"
                           "Available Spare: 5%\nAvailable Spare Threshold: 10%\n"
                           "Warning Temperature: 70 Celsius\nCritical Temperature: 80 Celsius\nTemperature: 65 Celsius"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    device = SmartCollector(pve_client=Pve()).poll_once()["devices"]["/dev/nvme0"]
    metrics = device["critical_metrics"]
    assert metrics["total_bytes_written"] == 1_234_567 * 512000
    assert metrics["overall_passed"] is False
    spare_alert = next(a for a in device["alerts"] if a["metric"] == "spare_percent")
    assert spare_alert["threshold"] == 10
    assert metrics["warning_temp"] == 70 and metrics["critical_temp"] == 80
    assert any(a["name"] == "Data Units Written" and a["raw_value"] == 1_234_567 for a in device["raw_attributes"])
    assert any(a["name"] == "Critical Warning" and a["threshold"] is None for a in device["raw_attributes"])


def test_pve_health_text_is_case_insensitive_and_missing_health_is_unknown(monkeypatch):
    class Pve:
        def disks_list(self): return [{"devpath": path, "type": "scsi"} for path in ("/dev/sda", "/dev/sdb")]
        def disk_smart(self, path):
            return {"text": ("SMART overall-health self-assessment test result: FAILED!" if path.endswith("sda")
                              else "Device Model: Unknown\n")}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    devices = SmartCollector(pve_client=Pve()).poll_once()["devices"]
    assert devices["/dev/sda"]["critical_metrics"]["overall_passed"] is False
    assert devices["/dev/sda"]["status"] == "critical"
    assert devices["/dev/sdb"]["critical_metrics"]["overall_passed"] is None


def test_sas_smartctl_json_normalizes_error_counters_and_health_fields():
    sas = {"device": {"protocol": "SCSI"}, "smart_status": {"passed": False},
           "scsi_error_counter_log": {"read": {"total_uncorrected_errors": 12, "total_errors_corrected": 45,
                                               "gigabytes_processed": 123.5},
                                      "write": {"total_uncorrected_errors": 2},
                                      "verify": {"total_uncorrected_errors": 1}},
           "scsi_grown_defect_list": {"grown_defect_list_count": 3},
           "temperature": {"current": 60, "drive_trip": 68}, "power_on_time": {"hours": 24000}}
    disk = SmartCollector(provider=lambda: [("/dev/sas0", sas, {})]).poll_once()["devices"]["/dev/sas0"]
    metrics = disk["critical_metrics"]
    assert disk["protocol"] == "sas"
    assert metrics["uncorrectable_errors"] == 15 and metrics["reallocated_sectors"] == 3
    assert metrics["temperature_c"] == 60 and metrics["critical_temp"] == 68
    assert metrics["power_on_hours"] == 24000 and disk["status"] == "critical"
    assert {a["name"] for a in disk["raw_attributes"]} >= {
        "scsi_error_counter_log.read.total_uncorrected_errors", "scsi_grown_defect_list.grown_defect_list_count",
        "temperature.current", "temperature.drive_trip", "power_on_time.hours"}
    assert {a["name"]: a["raw_value"] for a in disk["raw_attributes"]}["scsi_error_counter_log.read.total_errors_corrected"] == 45
    assert {a["name"]: a["raw_value"] for a in disk["raw_attributes"]}["scsi_error_counter_log.read.gigabytes_processed"] == 123.5


def test_nvme_error_log_entries_drive_crc_delta_alert_and_pve_text_metric(monkeypatch):
    source = {"device": {"protocol": "nvme"}, "nvme_smart_health_information_log": {"num_err_log_entries": 5}}
    collector = SmartCollector(provider=lambda: [("nvme0", source, {})])
    collector.poll_once(now=1)
    source["nvme_smart_health_information_log"]["num_err_log_entries"] = 6
    second = collector.poll_once(now=2)
    alert = next(a for a in second["alerts"] if a["metric"] == "interface_crc_errors")
    assert alert["value"] == 6 and "delta 1" in alert["message"]

    class Pve:
        def disks_list(self): return [{"devpath": "/dev/nvme0", "type": "nvme"}]
        def disk_smart(self, _): return {"text": "Error Information Log Entries: 7\n"}
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    pve_metric = SmartCollector(pve_client=Pve()).poll_once()["devices"]["/dev/nvme0"]["critical_metrics"]
    assert pve_metric["interface_crc_errors"] == 7


def test_ata_read_lba_attribute_is_not_used_as_bytes_written():
    source = ("disk", {"device": {"protocol": "ATA"}, "ata_smart_attributes": {"table": [
        {"id": 242, "name": "Total_LBAs_Read", "raw": {"value": 100}}]}}, {})
    metrics = SmartCollector(provider=lambda: [source]).poll_once()["devices"]["disk"]["critical_metrics"]
    assert metrics["total_bytes_written"] is None


def test_device_temperature_thresholds_override_defaults_but_env_wins(monkeypatch):
    source = ("nvme", {"device": {"protocol": "nvme"}, "nvme_smart_health_information_log": {
        "temperature": 340, "warning_temp": 60, "critical_temp": 65}}, {})
    device = SmartCollector(provider=lambda: [source]).poll_once()["devices"]["nvme"]
    assert device["status"] == "critical"
    assert device["alerts"][0]["threshold"] == 65
    monkeypatch.setenv("SMART_TEMP_CRIT_SSD", "75")
    device = SmartCollector(provider=lambda: [source]).poll_once()["devices"]["nvme"]
    assert device["alerts"][0]["threshold"] == 60


def test_wear_spare_and_temperature_environment_thresholds(monkeypatch):
    for key, value in {"SMART_WEAR_WARN_PCT": "70", "SMART_WEAR_CRIT_PCT": "90",
                       "SMART_SPARE_WARN_PCT": "18", "SMART_SPARE_CRIT_PCT": "14",
                       "SMART_TEMP_WARN_HDD": "48", "SMART_TEMP_CRIT_HDD": "52"}.items():
        monkeypatch.setenv(key, value)
    sources = [
        ("ssd", {"critical_metrics": {"wear_percent": 75, "spare_percent": 17}}, {"media_type": "ssd"}),
        ("hdd", {"critical_metrics": {"temperature_c": 49}}, {"media_type": "hdd"}),
    ]
    snapshot = SmartCollector(provider=lambda: sources).poll_once()
    assert snapshot["devices"]["ssd"]["status"] == "warning"
    assert {alert["metric"] for alert in snapshot["devices"]["ssd"]["alerts"]} == {"wear_percent", "spare_percent"}
    assert snapshot["devices"]["hdd"]["alerts"][0]["metric"] == "temperature_c"
