import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _import_agent_sender_windows():
    class DummyInfo:
        rc = 0

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args, **kwargs):
            pass

        def loop_start(self, *args, **kwargs):
            pass

        def loop_stop(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            pass

        def reconnect(self, *args, **kwargs):
            pass

        def connect_async(self, *args, **kwargs):
            pass

        def disconnect(self, *args, **kwargs):
            pass

        def reconnect_delay_set(self, *args, **kwargs):
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

    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    class DummyWMIClient:
        def __init__(self, *args, **kwargs):
            pass

        def Sensor(self):
            return []

    wmi_module = types.ModuleType("wmi")
    wmi_module.WMI = DummyWMIClient
    wmi_module.x_wmi = Exception

    class DummyFreq:
        def _asdict(self):
            return {"current": 3500.0, "min": 800.0, "max": 4200.0}

    class DummyVMem:
        total = 16 * 1024**3
        used = 8 * 1024**3
        available = 8 * 1024**3
        percent = 50.0

    class DummySwap:
        total = 2 * 1024**3
        used = 1 * 1024**3
        free = 1 * 1024**3
        percent = 50.0

    class DummyDisk:
        read_bytes = 0
        write_bytes = 0
        read_count = 0
        write_count = 0

    class DummyNet:
        bytes_recv = 0
        bytes_sent = 0

    class DummyIfStat:
        isup = True
        speed = 1000
        mtu = 1500
        duplex = 2

    psutil_module = types.ModuleType("psutil")
    psutil_module.cpu_count = lambda logical=True: 8 if logical else 4
    psutil_module.cpu_freq = lambda: DummyFreq()
    psutil_module.cpu_percent = lambda interval=None, percpu=False: [0.0] * 8 if percpu else 0.0
    psutil_module.virtual_memory = lambda: DummyVMem()
    psutil_module.swap_memory = lambda: DummySwap()
    psutil_module.boot_time = lambda: 0.0
    psutil_module.disk_io_counters = lambda perdisk=True: {"PhysicalDrive0": DummyDisk()}
    psutil_module.net_io_counters = lambda pernic=True: {"Ethernet": DummyNet()}
    psutil_module.net_if_stats = lambda: {"Ethernet": DummyIfStat()}

    sys.modules["paho"] = paho_module
    sys.modules["paho.mqtt"] = mqtt_module
    sys.modules["paho.mqtt.client"] = client_module
    sys.modules["dotenv"] = dotenv_module
    sys.modules["wmi"] = wmi_module
    sys.modules["psutil"] = psutil_module

    import platform

    original_platform_system = platform.system
    platform.system = lambda: "Windows"
    try:
        module_name = "hwmonitor_mqtt.agents.agent_sender_windows"
        if module_name in sys.modules:
            del sys.modules[module_name]
        return importlib.import_module(module_name)
    finally:
        platform.system = original_platform_system


def test_windows_build_esp_payload_schema_and_aggregation():
    module = _import_agent_sender_windows()

    module.metrics["cpu"] = {"percent_total": 37.4}
    module.metrics["memory"] = {
        "ram": {"percent": 66.6, "used": 8 * 1024**3, "total": 16 * 1024**3}
    }
    module.metrics["disk_io"] = {
        "PhysicalDrive0": {"rate": {"read_bytes_per_s": 100.0, "write_bytes_per_s": 10.0}},
        "PhysicalDrive1": {"rate": {"read_bytes_per_s": 50.0, "write_bytes_per_s": 5.0}},
    }
    module.metrics["network_io"] = {
        "total": {"rate": {"rx_bytes_per_s": 1234.0, "tx_bytes_per_s": 4321.0}}
    }
    module.metrics["temperatures"] = {
        "cpu": [{"current": 71.2}],
        "storage": {"nvme0": [{"current": 43.0}]},
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
        "disk_io",
        "gpu",
        "host",
        "memory",
        "network_io",
        "temperatures",
        "ts",
    ]
    assert payload["ts"] == 1700000000
    assert payload["cpu"]["percent_total"] == 37.4
    assert payload["memory"]["ram"]["percent"] == 66.6
    assert payload["disk_io"]["total"]["rate"]["read_bytes_per_s"] == 150.0
    assert payload["disk_io"]["total"]["rate"]["write_bytes_per_s"] == 15.0
    assert payload["temperatures"]["cpu"][0]["current"] == 71.2
    assert payload["network_io"]["total"]["rate"]["rx_bytes_per_s"] == 1234.0
    assert "mqtt_stats" not in payload
    assert "system" not in payload
    assert "gpus" not in payload


def test_windows_should_publish_esp_payload_by_change_or_heartbeat():
    module = _import_agent_sender_windows()
    module.reset_esp_publish_state()

    payload = {
        "cpu": {"percent_total": 20.0},
        "memory": {"ram": {"percent": 30.0}},
        "gpu": {"usage_percent": 10.0, "temperature_celsius": 40.0},
        "network_io": {"total": {"rate": {"rx_bytes_per_s": 1000.0, "tx_bytes_per_s": 1000.0}}},
        "disk_io": {"total": {"rate": {"read_bytes_per_s": 200.0, "write_bytes_per_s": 200.0}}},
        "temperatures": {"cpu": [{"current": 50.0}]},
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


def test_process_lhm_sensors_maps_cpu_gpu_and_storage():
    module = _import_agent_sender_windows()

    class Sensor:
        def __init__(self, sensor_type, name, value, parent):
            self.SensorType = sensor_type
            self.Name = name
            self.Value = value
            self.Parent = parent

    sensors = [
        Sensor("Load", "CPU Total", 21.0, "/intelcpu/0"),
        Sensor("Load", "CPU Core #1", 20.0, "/intelcpu/0"),
        Sensor("Load", "CPU Core #2", 22.0, "/intelcpu/0"),
        Sensor("Clock", "CPU Core #1", 4200.0, "/intelcpu/0"),
        Sensor("Clock", "CPU Core #2", 4100.0, "/intelcpu/0"),
        Sensor("Temperature", "CPU Package", 68.0, "/intelcpu/0"),
        Sensor("Temperature", "Temperature", 41.0, "/nvme/0"),
        Sensor("Load", "GPU Core", 65.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Core", 71.0, "/gpu-nvidia/0"),
        Sensor("SmallData", "GPU Memory Used", 2048.0, "/gpu-nvidia/0"),
        Sensor("SmallData", "GPU Memory Total", 8192.0, "/gpu-nvidia/0"),
    ]

    cpu_block = module.get_cpu_base_block()
    cpu_block["count_logical"] = 2
    out = module.process_lhm_sensors(sensors, cpu_block)

    assert out["cpu"]["percent_total"] == 21.0
    assert out["cpu"]["percent_per_core"] == [20.0, 22.0]
    assert out["cpu"]["freq_mhz_per_core"] == [4200.0, 4100.0]
    assert out["temperatures"]["cpu"][0]["current"] == 68.0
    assert "nvme0" in out["temperatures"]["storage"]
    assert len(out["gpus"]) == 1
    assert out["gpus"][0]["usage_percent"] == 65.0
    assert out["gpus"][0]["temperature_celsius"] == 71.0
    assert out["gpus"][0]["memory_percent"] == 25.0


def test_process_lhm_sensors_skips_tjmax_distance_and_prefers_gpu_core():
    module = _import_agent_sender_windows()

    class Sensor:
        def __init__(self, sensor_type, name, value, parent):
            self.SensorType = sensor_type
            self.Name = name
            self.Value = value
            self.Parent = parent

    sensors = [
        Sensor("Temperature", "CPU Package", 45.0, "/intelcpu/0"),
        Sensor("Temperature", "Core #1 Distance to TjMax", 55.0, "/intelcpu/0"),
        Sensor("Temperature", "GPU Hot Spot", 80.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Core", 66.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Memory Junction", 84.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "Composite Temperature", 49.0, "/nvme/1"),
        Sensor("Temperature", "Warning Temperature", 69.0, "/nvme/1"),
        Sensor("Temperature", "Critical Temperature", 74.0, "/nvme/1"),
    ]

    out = module.process_lhm_sensors(sensors, module.get_cpu_base_block())
    assert [t["label"] for t in out["temperatures"]["cpu"]] == ["CPU Package"]
    assert out["gpus"][0]["temperature_celsius"] == 66.0
    assert out["gpus"][0]["temperature_label"] == "GPU Core"
    assert len(out["gpus"][0]["temperatures"]) == 3
    assert [t["label"] for t in out["temperatures"]["storage"]["nvme1"]] == ["Composite Temperature"]


def test_windows_gpu_nvidia_fallback_parses_csv():
    module = _import_agent_sender_windows()

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = "57, 68, 2048, 8192, 70\n"

    module.subprocess.run = lambda *args, **kwargs: DummyResult()
    gpus = module.get_gpu_block_nvidia_fallback()

    assert len(gpus) == 1
    assert gpus[0]["usage_percent"] == 57.0
    assert gpus[0]["temperature_celsius"] == 68.0
    assert gpus[0]["memory_percent"] == 25.0
    assert gpus[0]["temperatures"][0]["label"] == "GPU"


def test_update_lhm_metrics_keeps_last_known_gpu_and_cpu_temp_on_partial_poll():
    module = _import_agent_sender_windows()
    module.metrics["cpu"] = module.get_cpu_base_block()
    module.metrics["temperatures"] = None
    module.metrics["gpu"] = []
    module.get_gpu_block_nvidia_fallback = lambda: []

    class Sensor:
        def __init__(self, sensor_type, name, value, parent):
            self.SensorType = sensor_type
            self.Name = name
            self.Value = value
            self.Parent = parent

    full = [
        Sensor("Temperature", "CPU Package", 67.0, "/intelcpu/0"),
        Sensor("Load", "GPU Core", 44.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Core", 70.0, "/gpu-nvidia/0"),
    ]
    partial = [
        Sensor("Load", "CPU Total", 12.0, "/intelcpu/0"),
    ]

    module.update_lhm_metrics(full)
    assert module.metrics["temperatures"]["cpu"][0]["current"] == 67.0
    assert len(module.metrics["gpu"]) == 1
    assert module.metrics["gpu"][0]["temperature_celsius"] == 70.0

    module.update_lhm_metrics(partial)
    assert module.metrics["temperatures"]["cpu"][0]["current"] == 67.0
    assert len(module.metrics["gpu"]) == 1
    assert module.metrics["gpu"][0]["temperature_celsius"] == 70.0


def test_process_lhm_sensors_picks_cpu_temp_by_name_when_parent_is_generic():
    module = _import_agent_sender_windows()

    class Sensor:
        def __init__(self, sensor_type, name, value, parent):
            self.SensorType = sensor_type
            self.Name = name
            self.Value = value
            self.Parent = parent

    sensors = [
        Sensor("Temperature", "CPU Package", 63.5, "/lpc/nct6798d/0"),
        Sensor("Load", "GPU Core", 17.0, "/gpu-intel/0"),
    ]

    out = module.process_lhm_sensors(sensors, module.get_cpu_base_block())
    assert out["temperatures"]["cpu"][0]["current"] == 63.5


def test_update_lhm_metrics_keeps_missing_secondary_gpu_from_previous_cycle():
    module = _import_agent_sender_windows()
    module.metrics["cpu"] = module.get_cpu_base_block()
    module.metrics["temperatures"] = None
    module.metrics["gpu"] = []
    module.get_gpu_block_nvidia_fallback = lambda: []

    class Sensor:
        def __init__(self, sensor_type, name, value, parent):
            self.SensorType = sensor_type
            self.Name = name
            self.Value = value
            self.Parent = parent

    full = [
        Sensor("Load", "GPU Core", 44.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Core", 70.0, "/gpu-nvidia/0"),
        Sensor("Load", "GPU Core", 55.0, "/gpu-nvidia/1"),
        Sensor("Temperature", "GPU Core", 75.0, "/gpu-nvidia/1"),
    ]
    partial = [
        Sensor("Load", "GPU Core", 22.0, "/gpu-nvidia/0"),
        Sensor("Temperature", "GPU Core", 66.0, "/gpu-nvidia/0"),
    ]

    module.update_lhm_metrics(full)
    assert len(module.metrics["gpu"]) == 2
    assert module.metrics["gpu"][1]["id"] == "nvidia-1"
    assert module.metrics["gpu"][1]["temperature_celsius"] == 75.0

    module.update_lhm_metrics(partial)
    assert len(module.metrics["gpu"]) == 2
    gpu_by_id = {gpu["id"]: gpu for gpu in module.metrics["gpu"]}
    assert gpu_by_id["nvidia-0"]["temperature_celsius"] == 66.0
    assert gpu_by_id["nvidia-1"]["temperature_celsius"] == 75.0
