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


def test_pve_inventory_api_error_degrades_to_unavailable(monkeypatch):
    class Pve:
        def disks_list(self): raise RuntimeError("API unavailable")
    monkeypatch.setattr("hwmonitor_mqtt.collectors.smart.shutil.which", lambda _: None)
    snapshot = SmartCollector(pve_client=Pve()).poll_once()
    assert snapshot["status"] == "unavailable" and snapshot["alerts"] == []


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
