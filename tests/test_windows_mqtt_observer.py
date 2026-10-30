import importlib.util
from pathlib import Path


def test_observer_checks_full_and_esp_payload_shapes():
    path = Path(__file__).parent / "windows_mqtt_observer.py"
    spec = importlib.util.spec_from_file_location("windows_mqtt_observer", path)
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    full = {"host": "test", "cpu": {}, "memory": {}, "disk_smart": {"status": "unavailable", "alerts": [], "devices": {}}}
    esp = {"host": "test", "cpu": {}, "memory": {}, "disk_health": "unavailable"}
    assert observer.matches(full, "full")
    assert observer.matches(esp, "esp")
    assert not observer.matches(esp, "full")
    assert not observer.matches(full, "esp")
