import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _import_agent_sender_async():
    class DummyInfo:
        rc = 0

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args, **kwargs):
            pass

        def loop_start(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            pass

        def publish(self, *args, **kwargs):
            return DummyInfo()

        def is_connected(self, *args, **kwargs):
            return True

    class DummyCallbackAPIVersion:
        VERSION2 = object()

    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = DummyClient
    client_module.CallbackAPIVersion = DummyCallbackAPIVersion
    client_module.MQTT_ERR_SUCCESS = 0

    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_module.client = client_module

    paho_module = types.ModuleType("paho")

    sys.modules["paho"] = paho_module
    sys.modules["paho.mqtt"] = mqtt_module
    sys.modules["paho.mqtt.client"] = client_module

    module_name = "hwmonitor_mqtt.agents.agent_sender_async"
    if module_name in sys.modules:
        del sys.modules[module_name]

    return importlib.import_module(module_name)


def test_build_esp_payload_keeps_minimal_schema_and_aggregates():
    module = _import_agent_sender_async()

    module.metrics["cpu"] = {"percent_total": 37.4}
    module.metrics["memory"] = {
        "ram": {"percent": 66.6, "used": 8 * 1024**3, "total": 16 * 1024**3}
    }
    module.metrics["disk_io"] = {
        "nvme0n1": {"rate": {"read_bytes_per_s": 100.0, "write_bytes_per_s": 10.0}},
        "sda": {"rate": {"read_bytes_per_s": 50.0, "write_bytes_per_s": 5.0}},
    }
    module.metrics["network_io"] = {
        "total": {"rate": {"rx_bytes_per_s": 1234.0, "tx_bytes_per_s": 4321.0}}
    }
    module.metrics["temperatures"] = {
        "k10temp": [{"current": 71.2}],
        "nvme0n1": [{"current": 43.0}],
    }
    module.metrics["gpu"] = [
        {
            "usage_percent": 57.0,
            "temperature_celsius": 68.0,
            "memory_percent": 33.3,
            "temperatures": [{"label": "GPU", "current": 68.0}],
        }
    ]

    payload = module.build_esp_payload(now_ts=1700000000)

    assert sorted(payload.keys()) == [
        "cpu",
        "disk_health",
        "disk_io",
        "gpu",
        "host",
        "memory",
        "network_io",
        "temperatures",
        "ts",
    ]
    assert payload["ts"] == 1700000000
    assert payload["disk_health"] == "unavailable"
    assert payload["cpu"]["percent_total"] == 37.4
    assert payload["memory"]["ram"]["percent"] == 66.6
    assert payload["disk_io"]["total"]["rate"]["read_bytes_per_s"] == 150.0
    assert payload["disk_io"]["total"]["rate"]["write_bytes_per_s"] == 15.0
    assert payload["temperatures"]["k10temp"][0]["current"] == 71.2
    assert payload["network_io"]["total"]["rate"]["rx_bytes_per_s"] == 1234.0
    assert "mqtt_stats" not in payload
    assert "system" not in payload
    assert "gpus" not in payload


def test_should_publish_esp_payload_by_change_or_heartbeat():
    module = _import_agent_sender_async()

    module.reset_esp_publish_state()

    payload = {
        "cpu": {"percent_total": 20.0},
        "memory": {"ram": {"percent": 30.0}},
        "gpu": {"usage_percent": 10.0, "temperature_celsius": 40.0},
        "network_io": {"total": {"rate": {"rx_bytes_per_s": 1000.0, "tx_bytes_per_s": 1000.0}}},
        "disk_io": {"total": {"rate": {"read_bytes_per_s": 200.0, "write_bytes_per_s": 200.0}}},
        "temperatures": {"k10temp": [{"current": 50.0}]},
    }

    assert module.should_publish_esp_payload(payload, now_monotonic=0.0) is True
    assert module.should_publish_esp_payload(payload, now_monotonic=1.0) is False
    assert module.should_publish_esp_payload(payload, now_monotonic=11.1) is True

    changed = {
        **payload,
        "cpu": {"percent_total": 28.5},
    }
    assert module.should_publish_esp_payload(changed, now_monotonic=12.0) is False
    assert module.should_publish_esp_payload(changed, now_monotonic=13.2) is True


def test_esp_disk_health_transition_bypasses_delta_thresholds():
    module = _import_agent_sender_async()
    module.reset_esp_publish_state()
    payload = {"disk_health": "ok"}
    assert module.should_publish_esp_payload(payload, now_monotonic=0.0)
    assert module.should_publish_esp_payload({"disk_health": "critical"}, now_monotonic=2.1)


def test_full_payload_truncates_raw_smart_attributes_only_when_oversized():
    module = _import_agent_sender_async()
    module.metrics["gpu"] = []
    attributes = [{"name": "x" * 300000}]
    module.metrics["disk_smart"] = {"status": "warning", "alerts": [{"severity": "warning"}], "devices": {
        "disk0": {"critical_metrics": {"wear_percent": 90}, "raw_attributes": attributes}}
    }
    payload = module.build_full_payload(now_ts=1)
    disk = payload["disk_smart"]["devices"]["disk0"]
    assert disk["critical_metrics"]["wear_percent"] == 90
    assert "raw_attributes" not in disk and disk["raw_attributes_truncated"] is True
    assert payload["disk_smart"]["status"] == "warning"
    assert payload["disk_smart"]["alerts"]
    assert module.metrics["disk_smart"]["devices"]["disk0"]["raw_attributes"] == attributes
