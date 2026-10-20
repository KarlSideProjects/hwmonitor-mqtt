#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Async System Monitor (Multi-rate MQTT sender)
CPU/MEM/NET 每秒、DISK 每 3 秒、TEMP 每 10 秒
- 加入 cpu.loadavg、system.uptime_sec
- 加入 mqtt_stats：publish_ok/err、last_rc、is_connected、reconnects
"""

import asyncio
import os
import re
import socket
import time
import glob
import json
from typing import Any, Dict, Optional

import psutil
from dotenv import load_dotenv
from hwmonitor_mqtt.transport.mqtt import configuration, create_client, publish

if os.getenv("PVE_ENV_PRELOADED") != "1":
    load_dotenv()

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default

# ===== MQTT CONFIG =====
HOSTNAME    = socket.gethostname()
MQTT_CONFIG = configuration(HOSTNAME, default_broker_host="192.168.5.32")
BROKER_HOST = MQTT_CONFIG["broker_host"]
BROKER_PORT = MQTT_CONFIG["broker_port"]
MQTT_USER   = MQTT_CONFIG["username"]
MQTT_PASS   = MQTT_CONFIG["password"]
TOPIC       = MQTT_CONFIG["topic"]
SENDER_PROFILE = os.getenv("SENDER_PROFILE", "full").strip().lower()
if SENDER_PROFILE not in {"full", "esp"}:
    print(f"⚠️ Unknown SENDER_PROFILE='{SENDER_PROFILE}', fallback to full")
    SENDER_PROFILE = "full"
PUBLISH_INTERVAL_SEC = _env_float("PUBLISH_INTERVAL_SEC", 1.0)
ESP_MIN_PUBLISH_INTERVAL_SEC = _env_float("ESP_MIN_PUBLISH_INTERVAL_SEC", 2.0)
ESP_HEARTBEAT_INTERVAL_SEC = _env_float("ESP_HEARTBEAT_INTERVAL_SEC", 10.0)
ESP_CPU_DELTA = _env_float("ESP_CPU_DELTA", 3.0)
ESP_RAM_DELTA = _env_float("ESP_RAM_DELTA", 2.0)
ESP_TEMP_DELTA = _env_float("ESP_TEMP_DELTA", 1.0)
ESP_NET_DELTA_BYTES = _env_float("ESP_NET_DELTA_BYTES", 16384.0)
ESP_DISK_DELTA_BYTES = _env_float("ESP_DISK_DELTA_BYTES", 65536.0)
ESP_GPU_DELTA = _env_float("ESP_GPU_DELTA", 3.0)

# ===== MQTT Client =====
mqtt_client, mqtt_stats, on_connect, on_disconnect = create_client(MQTT_CONFIG)

def mqtt_connect():
    try:
        mqtt_client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
    except Exception as e:
        mqtt_stats["last_error"] = str(e)
        print(f"⚠️ MQTT connect failed: {e}")

# ===== GLOBAL STATE =====
metrics: Dict[str, Any] = {
    "cpu": None,
    "memory": None,
    "disk_io": None,
    "temperatures": None,
    "network_io": None,
    "system": None,
    "gpu": None,
}
_last_esp_signature: Optional[Dict[str, float]] = None
_last_esp_publish_monotonic = 0.0
pve_collector = None


def initialize_pve_collector():
    """Start the independent PVE poller only for the native PVE service."""
    global pve_collector
    if os.getenv("PVE_ENABLED") != "1":
        return None
    from hwmonitor_mqtt.clients.pve import PveApiClient, PveConfig, PveError
    from hwmonitor_mqtt.collectors.pve import PveCollector
    try:
        config = PveConfig.from_environment(env_file=os.getenv("PVE_CONFIG_FILE", "/etc/hwmonitor-mqtt/pve.env"))
        pve_collector = PveCollector(PveApiClient(config)) if config else PveCollector(None)
    except PveError as error:
        pve_collector = PveCollector(None)
        pve_collector.mark_error(error)
    if pve_collector.client:
        pve_collector.start()
    return pve_collector

# ===== Helpers =====
def normalize_device_name(name: str) -> str:
    if m := re.match(r"^(nvme\d+n\d+)(?:p\d+)?$", name): return m.group(1)
    if m := re.match(r"^(sd[a-z]+)\d*$", name): return m.group(1)
    if m := re.match(r"^(mmcblk\d+)(?:p\d+)?$", name): return m.group(1)
    if m := re.match(r"^(md\d+)(?:p\d+)?$", name): return m.group(1)
    return name

# ===== CPU / MEM =====
def get_cpu_block() -> Dict[str, Any]:
    freq = psutil.cpu_freq()
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = None
    return {
        "percent_total": psutil.cpu_percent(interval=None),
        "percent_per_core": psutil.cpu_percent(interval=None, percpu=True),
        "freq_mhz": freq._asdict() if freq else None,
        "count_logical": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
        "loadavg": [load1, load5, load15],
    }

def get_mem_block() -> Dict[str, Any]:
    vm, sm = psutil.virtual_memory(), psutil.swap_memory()
    return {
        "ram":  {"total": vm.total, "used": vm.used, "available": vm.available, "percent": vm.percent},
        "swap": {"total": sm.total, "used": sm.used, "free": sm.free, "percent": sm.percent},
    }

def get_system_block() -> Dict[str, Any]:
    return {
        "uptime_sec": int(time.time() - psutil.boot_time()),
        "hostname": HOSTNAME,
        "pid": os.getpid(),
    }

# ===== Disk I/O =====
_prev_disk = psutil.disk_io_counters(perdisk=True)
def get_disk_io_block(elapsed: float) -> Dict[str, Any]:
    global _prev_disk
    curr = psutil.disk_io_counters(perdisk=True)
    result: Dict[str, Any] = {}
    for dev, io in curr.items():
        if dev.startswith("loop") or dev.startswith("dm-"):
            continue
        parent = normalize_device_name(dev)
        prev = _prev_disk.get(dev)
        if not prev:
            continue
        rbps = (io.read_bytes - prev.read_bytes) / elapsed
        wbps = (io.write_bytes - prev.write_bytes) / elapsed
        riops = (io.read_count - prev.read_count) / elapsed
        wiops = (io.write_count - prev.write_count) / elapsed
        # 若多個分割區被歸到同一 parent，取加總（同秒內通常只有一筆）
        if parent not in result:
            result[parent] = {"rate": {"read_bytes_per_s": 0.0, "write_bytes_per_s": 0.0, "read_iops": 0.0, "write_iops": 0.0}}
        result[parent]["rate"]["read_bytes_per_s"]  += rbps
        result[parent]["rate"]["write_bytes_per_s"] += wbps
        result[parent]["rate"]["read_iops"]         += riops
        result[parent]["rate"]["write_iops"]        += wiops
    # round
    for v in result.values():
        for k in v["rate"]:
            v["rate"][k] = round(v["rate"][k], 3)
    _prev_disk = curr
    return result

# ===== Network I/O (ALL NICs) =====
_prev_net = psutil.net_io_counters(pernic=True)
def get_net_io_block(elapsed: float) -> Dict[str, Any]:
    global _prev_net
    curr = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    per_nic: Dict[str, Any] = {}
    total = {"rate": {"rx_bytes_per_s": 0.0, "tx_bytes_per_s": 0.0},
             "cumulative": {"bytes_recv": 0, "bytes_sent": 0}}

    for nic, io in curr.items():
        prev = _prev_net.get(nic)
        if not prev:
            continue
        rx_bps = (io.bytes_recv - prev.bytes_recv) / elapsed
        tx_bps = (io.bytes_sent - prev.bytes_sent) / elapsed
        st = stats.get(nic)
        meta = {"isup": None, "speed_mbps": None, "mtu": None, "duplex": None}
        if st:
            meta["isup"] = st.isup
            meta["speed_mbps"] = st.speed if st.speed >= 0 else None
            meta["mtu"] = st.mtu if st.mtu >= 0 else None
            meta["duplex"] = st.duplex

        per_nic[nic] = {
            "rate": {"rx_bytes_per_s": round(rx_bps, 3), "tx_bytes_per_s": round(tx_bps, 3)},
            "cumulative": {"bytes_recv": io.bytes_recv, "bytes_sent": io.bytes_sent},
            "meta": meta
        }
        total["rate"]["rx_bytes_per_s"] += rx_bps
        total["rate"]["tx_bytes_per_s"] += tx_bps
        total["cumulative"]["bytes_recv"] += io.bytes_recv
        total["cumulative"]["bytes_sent"] += io.bytes_sent

    _prev_net = curr
    total["rate"]["rx_bytes_per_s"] = round(total["rate"]["rx_bytes_per_s"], 3)
    total["rate"]["tx_bytes_per_s"] = round(total["rate"]["tx_bytes_per_s"], 3)
    return {"per_nic": per_nic, "total": total}

# ===== Temperatures: map drivetemp -> sda/sdb/mmcblk/vd*, and NVMe -> nvmeXnY =====
import glob
import os
import re
from typing import Optional, Dict, Any

def _resolve_drivetemp_blockdev(hwmon_dir: str) -> Optional[str]:
    """
    從 /sys/class/hwmon/hwmonX （name=drivetemp）追溯到對應的 block 裝置名，例如 sda/sdb/mmcblk0/vda。
    """
    try:
        dev = os.path.realpath(os.path.join(hwmon_dir, "device"))
        # 1) 直接在當前節點找 block 子目錄
        blk = os.path.join(dev, "block")
        if os.path.isdir(blk):
            for e in os.listdir(blk):
                if re.match(r"^(sd[a-z]+|mmcblk\d+|vd[a-z]+)$", e):
                    return e
        # 2) 向上回溯找 block
        cur = dev
        while cur != "/":
            blk = os.path.join(cur, "block")
            if os.path.isdir(blk):
                for e in os.listdir(blk):
                    if re.match(r"^(sd[a-z]+|mmcblk\d+|vd[a-z]+)$", e):
                        return e
            cur = os.path.dirname(cur)
    except Exception:
        pass
    return None

def _read_first(glob_pat: str) -> Optional[float]:
    for p in sorted(glob.glob(glob_pat)):
        try:
            with open(p, "r") as f:
                return int(f.read().strip()) / 1000.0  # 毫度C -> 度C
        except Exception:
            continue
    return None

def _nvme_controller_of_namespace(ns_block: str) -> Optional[str]:
    """
    從 /sys/block/nvmeXnY 找到對應控制器 nvmeX。
    """
    try:
        dev_link = os.path.realpath(f"/sys/block/{ns_block}/device")
        cur = dev_link
        while cur != "/":
            base = os.path.basename(cur)
            if re.fullmatch(r"nvme\d+", base) and os.path.isdir(f"/sys/class/nvme/{base}"):
                return base  # e.g., nvme0
            cur = os.path.dirname(cur)
    except Exception:
        pass
    return None

def _collect_nvme_namespace_temps() -> Dict[str, float]:
    """
    回傳 { 'nvme0n1': 43.5, ... } ：讀取控制器 hwmon Composite 溫度，套用到所有 namespace。
    """
    out: Dict[str, float] = {}
    try:
        blocks = [d for d in os.listdir("/sys/block") if re.match(r"nvme\d+n\d+", d)]
    except FileNotFoundError:
        blocks = []
    for ns in sorted(blocks):
        ctl = _nvme_controller_of_namespace(ns)
        val = _read_first(f"/sys/class/nvme/{ctl}/device/hwmon/hwmon*/temp*_input") if ctl else None
        if val is not None:
            out[ns] = val
    return out

def get_temps_block() -> Optional[Dict[str, Any]]:
    """
    回傳鍵包含：
      - 'sda'、'sdb'、'mmcblk0'、'vda'…（drivetemp 映射）
      - 'nvme0n1'、'nvme1n1'…（NVMe 每 namespace）
      - 以及 CPU/GPU 等一般 sensor（k10temp/coretemp/amdgpu…）
    """
    # 先讀 psutil，保留 CPU/GPU 等非磁碟 sensor
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except Exception:
        sensors = None

    out: Dict[str, Any] = {}
    if sensors:
        for name, entries in sensors.items():
            # 先排除 drivetemp/nvme，這兩個我們手動更精準地處理
            if name in ("drivetemp", "nvme"):
                continue
            out[name] = [
                {"label": (e.label or ""), "current": e.current, "high": e.high, "critical": e.critical}
                for e in entries
                if e.current is not None
            ]

    # 解析 drivetemp -> sda/sdb/mmcblk/vd*
    hwmon_root = "/sys/class/hwmon"
    if os.path.isdir(hwmon_root):
        try:
            for entry in os.listdir(hwmon_root):
                p = os.path.join(hwmon_root, entry)
                name_file = os.path.join(p, "name")
                if not os.path.isfile(name_file):
                    continue
                try:
                    nm = open(name_file).read().strip()
                except Exception:
                    continue
                if nm != "drivetemp":
                    continue

                blk = _resolve_drivetemp_blockdev(p) or "drivetemp"
                # 讀這個 hwmon 節點裡所有 temp*_input
                for fn in os.listdir(p):
                    if fn.startswith("temp") and fn.endswith("_input"):
                        base = fn[:-6]
                        try:
                            cur = int(open(os.path.join(p, f"{base}_input")).read().strip()) / 1000.0
                            lbl = ""
                            lf = os.path.join(p, f"{base}_label")
                            if os.path.isfile(lf):
                                lbl = open(lf).read().strip()
                            out.setdefault(blk, []).append(
                                {"label": lbl, "current": cur, "high": 0.0, "critical": 0.0}
                            )
                        except Exception:
                            pass
        except Exception:
            pass

    # 解析 NVMe -> 每個 namespace
    try:
        ns_temps = _collect_nvme_namespace_temps()
        for ns, val in ns_temps.items():
            out.setdefault(ns, []).append({"label": "Composite", "current": val, "high": 0.0, "critical": 0.0})
    except Exception:
        pass

    # 後援：若 NVMe 仍沒有對應，才把 psutil 的 'nvme' 通用值套用到各 namespace
    if sensors and sensors.get("nvme"):
        try:
            generic = float(sensors["nvme"][0].current)
        except Exception:
            generic = None
        if generic is not None:
            try:
                blocks = [d for d in os.listdir("/sys/block") if re.match(r"nvme\d+n\d+", d)]
            except FileNotFoundError:
                blocks = []
            for ns in blocks:
                if ns not in out:
                    out[ns] = [{"label": "Composite", "current": generic, "high": 0.0, "critical": 0.0}]

    return out or None

# ===== GPU =====
def get_gpu_usage() -> Optional[float]:
    """Get GPU usage percentage."""
    import subprocess
    try:
        # Try NVIDIA GPU first
        result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass

    try:
        # Try Intel GPU (via intel_gpu_top)
        result = subprocess.run(['intel_gpu_top', '-o', '-', '-s', '100'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Render/3D' in line:
                    parts = line.split(':')
                    if len(parts) > 1:
                        pct = parts[1].strip().replace('%', '')
                        return float(pct)
    except Exception:
        pass

    try:
        # Try Intel GPU via sysfs
        with open('/sys/class/drm/card0/engine/rcs0/busy_percent', 'r') as f:
            return float(f.read().strip())
    except Exception:
        pass

    try:
        # Try AMD GPU
        with open('/sys/class/drm/card0/device/gpu_busy_percent', 'r') as f:
            return float(f.read().strip())
    except Exception:
        pass

    return None

def get_gpu_temp() -> Optional[float]:
    """Get GPU temperature in Celsius."""
    import subprocess
    try:
        # Try NVIDIA GPU first
        result = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass

    try:
        # Try Intel GPU via sensors
        temps = psutil.sensors_temperatures()
        for sensor_name in ['i915', 'coretemp', 'pch_cannonlake']:
            if sensor_name in temps:
                for entry in temps[sensor_name]:
                    if entry.label and any(x in entry.label.lower() for x in ['gpu', 'gt']):
                        return entry.current
    except Exception:
        pass

    try:
        # Try Intel GPU via sysfs hwmon
        for hwmon_path in glob.glob('/sys/class/drm/card0/hwmon/hwmon*/temp*_input'):
            with open(hwmon_path, 'r') as f:
                temp_millidegrees = int(f.read().strip())
                return temp_millidegrees / 1000.0
    except Exception:
        pass

    try:
        # Try Raspberry Pi GPU
        result = subprocess.run(['vcgencmd', 'measure_temp'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            temp_str = result.stdout.strip()
            temp_val = temp_str.split('=')[1].replace("'C", "")
            return float(temp_val)
    except Exception:
        pass

    try:
        # Try AMD GPU
        temps = psutil.sensors_temperatures()
        for sensor_name in ['amdgpu', 'radeon']:
            if sensor_name in temps and temps[sensor_name]:
                return temps[sensor_name][0].current
    except Exception:
        pass

    return None

def _read_first_float(paths: list[str]) -> Optional[float]:
    for path in paths:
        try:
            with open(path, 'r') as f:
                return float(f.read().strip())
        except Exception:
            continue
    return None

def _compact_temp_label(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    raw = label.strip().lower()
    label_map = {
        "edge": "EDG",
        "junction": "JCT",
        "hotspot": "HSP",
        "mem": "MEM",
        "memory": "MEM",
        "vram": "VRM",
        "soc": "SOC",
        "core": "COR",
    }
    for key, val in label_map.items():
        if raw == key or key in raw:
            return val
    cleaned = re.sub(r'[^a-z0-9]', '', raw.upper())
    return cleaned[:3] if cleaned else None

def _read_hwmon_temps_all(card_path: str) -> list[dict]:
    """Read ALL GPU hwmon temperatures, return list of {label, current}."""
    patterns = [
        os.path.join(card_path, 'device', 'hwmon', 'hwmon*', 'temp*_input'),
        os.path.join(card_path, 'hwmon', 'hwmon*', 'temp*_input'),
    ]
    temps = []
    seen = set()
    for pattern in patterns:
        for hwmon_path in sorted(glob.glob(pattern)):
            if hwmon_path in seen:
                continue
            seen.add(hwmon_path)
            try:
                with open(hwmon_path, 'r') as f:
                    temp_c = int(f.read().strip()) / 1000.0
                label_path = hwmon_path.replace('_input', '_label')
                label_raw = ""
                try:
                    with open(label_path, 'r') as f:
                        label_raw = f.read().strip()
                except Exception:
                    pass
                label = _compact_temp_label(label_raw) or "GPU"
                temps.append({"label": label, "current": round(temp_c, 1)})
            except Exception:
                continue
    return temps

def _read_drm_vendor(card_path: str) -> Optional[str]:
    try:
        with open(os.path.join(card_path, 'device', 'vendor'), 'r') as f:
            return f.read().strip().lower()
    except Exception:
        return None

def _list_drm_cards() -> list[str]:
    cards = []
    for path in glob.glob('/sys/class/drm/card*'):
        base = os.path.basename(path)
        if re.match(r'^card\d+$', base):
            cards.append(path)
    def _card_sort_key(p: str) -> int:
        m = re.search(r'card(\d+)$', p)
        return int(m.group(1)) if m else 0
    return sorted(cards, key=_card_sort_key)

def _read_drm_usage(card_path: str) -> Optional[float]:
    return _read_first_float([
        os.path.join(card_path, 'engine', 'rcs0', 'busy_percent'),  # Intel
        os.path.join(card_path, 'device', 'gpu_busy_percent'),      # AMD
    ])

def get_gpu_block() -> list:
    """Collect GPU metrics (Multi-GPU support)."""
    gpus: list = []
    
    # 1. NVIDIA GPUs
    import subprocess
    try:
        res = subprocess.run(
            ['nvidia-smi',
             '--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,temperature.memory',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0 and res.stdout.strip():
            import csv
            reader = csv.reader(res.stdout.strip().splitlines())
            for row in reader:
                try:
                    gpu_temp = float(row[1])
                    mem_used = float(row[2])
                    mem_total = float(row[3])
                    temps = [{"label": "GPU", "current": gpu_temp}]
                    # temperature.memory (index 4, may be [Not Supported])
                    if len(row) > 4:
                        try:
                            mem_temp = float(row[4].strip())
                            temps.append({"label": "MEM", "current": mem_temp})
                        except (ValueError, IndexError):
                            pass
                    gpus.append({
                        "usage_percent": float(row[0]),
                        "temperature_celsius": gpu_temp,
                        "temperature_label": None,
                        "temperatures": temps,
                        "memory_used_mb": mem_used,
                        "memory_total_mb": mem_total,
                        "memory_percent": round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0.0,
                    })
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass

    
    # 2. Integrated/Other GPUs (Intel/AMD)
    added_non_nvidia = 0
    for card_path in _list_drm_cards():
        vendor = _read_drm_vendor(card_path)
        if vendor == '0x10de':  # NVIDIA already handled by nvidia-smi
            continue
        usage = _read_drm_usage(card_path)
        temps_all = _read_hwmon_temps_all(card_path)
        if usage is None and not temps_all:
            continue
        # 主溫度改採最高值，避免漏掉 hotspot/memory 過熱
        main_temp = None
        main_label = None
        if temps_all:
            hottest = max(temps_all, key=lambda t: _safe_float(t.get("current"), -273.15))
            main_temp = hottest["current"]
            main_label = hottest["label"]
        gpus.append({
            "usage_percent": usage if usage is not None else 0.0,
            "temperature_celsius": main_temp,
            "temperature_label": main_label,
            "temperatures": temps_all,
            "memory_used_mb": 0.0,
            "memory_total_mb": 0.0,
            "memory_percent": 0.0,
        })
        added_non_nvidia += 1

    if added_non_nvidia == 0:
        other_usage = None
        other_temp = None

        # Usage detection
        try:
            # Intel GPU (intel_gpu_top)
            res = subprocess.run(['intel_gpu_top', '-o', '-', '-s', '100'],
                                 capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                for line in res.stdout.split('\n'):
                    if 'Render/3D' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            other_usage = float(parts[1].strip().replace('%', ''))
                            break
        except Exception:
            pass

        if other_usage is None:
            for card_path in _list_drm_cards():
                other_usage = _read_drm_usage(card_path)
                if other_usage is not None:
                    break

        # Temp detection
        other_temps_all = []
        try:
            temps = psutil.sensors_temperatures()
            for name in ['i915', 'amdgpu', 'radeon']:
                if name in temps:
                    for entry in temps[name]:
                        if entry.current is not None:
                            label = _compact_temp_label(entry.label) or "GPU"
                            other_temps_all.append({"label": label, "current": round(entry.current, 1)})
                    if other_temps_all:
                        other_temp = other_temps_all[0]["current"]
                        break
        except Exception:
            pass

        if other_usage is not None or other_temp is not None:
            gpus.append({
                "usage_percent": other_usage if other_usage is not None else 0.0,
                "temperature_celsius": other_temp,
                "temperature_label": None,
                "temperatures": other_temps_all,
                "memory_used_mb": 0.0,
                "memory_total_mb": 0.0,
                "memory_percent": 0.0,
            })

    return gpus


# ===== MQTT publish =====
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick_cpu_temp_block(temps_block: Any) -> Dict[str, Any]:
    if not isinstance(temps_block, dict):
        return {}
    for key in ("k10temp", "coretemp", "cpu_thermal"):
        entries = temps_block.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("current") is not None:
                    return {key: [{"current": _safe_float(entry.get("current"))}]}
    return {}


def _aggregate_disk_io(disk_block: Any) -> Dict[str, Any]:
    read_bps = 0.0
    write_bps = 0.0
    if isinstance(disk_block, dict):
        for disk in disk_block.values():
            if not isinstance(disk, dict):
                continue
            rate = disk.get("rate")
            if not isinstance(rate, dict):
                continue
            read_bps += _safe_float(rate.get("read_bytes_per_s"))
            write_bps += _safe_float(rate.get("write_bytes_per_s"))
    return {
        "total": {
            "rate": {
                "read_bytes_per_s": round(read_bps, 3),
                "write_bytes_per_s": round(write_bps, 3),
            }
        }
    }


def _normalize_gpu_payload(gpu_obj: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(gpu_obj, dict):
        return None
    temps = []
    raw_temps = gpu_obj.get("temperatures")
    if isinstance(raw_temps, list):
        for t in raw_temps:
            if not isinstance(t, dict):
                continue
            label = t.get("label")
            current = t.get("current")
            if label is None or current is None:
                continue
            temps.append({"label": str(label), "current": round(_safe_float(current), 1)})
            if len(temps) >= 3:
                break
    return {
        "usage_percent": round(_safe_float(gpu_obj.get("usage_percent")), 1),
        "temperature_celsius": round(_safe_float(gpu_obj.get("temperature_celsius")), 1),
        "memory_percent": round(_safe_float(gpu_obj.get("memory_percent")), 1),
        "temperatures": temps,
    }


def build_full_payload(now_ts: Optional[int] = None) -> Dict[str, Any]:
    payload = {
        "ts": int(time.time()) if now_ts is None else int(now_ts),
        "host": HOSTNAME,
        "system": metrics["system"],
        "cpu": metrics["cpu"],
        "memory": metrics["memory"],
        "disk_io": metrics["disk_io"],
        "temperatures": metrics["temperatures"],
        "network_io": metrics["network_io"],
        "gpu": metrics["gpu"][0] if metrics["gpu"] else None,
        "gpus": metrics["gpu"],
        "mqtt_stats": {
            "publish_ok": mqtt_stats["publish_ok"],
            "publish_err": mqtt_stats["publish_err"],
            "last_publish_rc": mqtt_stats["last_publish_rc"],
            "is_connected": mqtt_stats["is_connected"],
            "reconnects": mqtt_stats["reconnects"],
            "last_error": mqtt_stats["last_error"],
        },
    }
    if pve_collector is not None:
        payload["virtualization"] = pve_collector.snapshot()
        if len(json.dumps(payload, separators=(",", ":")).encode()) > 262144:
            payload["virtualization"] = pve_collector.capacity_error()
    return payload


def build_esp_payload(now_ts: Optional[int] = None) -> Dict[str, Any]:
    cpu = metrics.get("cpu") if isinstance(metrics.get("cpu"), dict) else {}
    memory = metrics.get("memory") if isinstance(metrics.get("memory"), dict) else {}
    ram = memory.get("ram") if isinstance(memory.get("ram"), dict) else {}
    network = metrics.get("network_io") if isinstance(metrics.get("network_io"), dict) else {}
    net_total = network.get("total") if isinstance(network.get("total"), dict) else {}
    net_rate = net_total.get("rate") if isinstance(net_total.get("rate"), dict) else {}
    gpu_block = metrics.get("gpu")
    gpu_obj = gpu_block[0] if isinstance(gpu_block, list) and gpu_block else None

    payload = {
        "ts": int(time.time()) if now_ts is None else int(now_ts),
        "host": HOSTNAME,
        "cpu": {
            "percent_total": round(_safe_float(cpu.get("percent_total")), 1),
        },
        "memory": {
            "ram": {
                "percent": round(_safe_float(ram.get("percent")), 1),
                "used": int(_safe_float(ram.get("used"))),
                "total": int(_safe_float(ram.get("total"))),
            }
        },
        "disk_io": _aggregate_disk_io(metrics.get("disk_io")),
        "network_io": {
            "total": {
                "rate": {
                    "rx_bytes_per_s": round(_safe_float(net_rate.get("rx_bytes_per_s")), 3),
                    "tx_bytes_per_s": round(_safe_float(net_rate.get("tx_bytes_per_s")), 3),
                }
            }
        },
        "temperatures": _pick_cpu_temp_block(metrics.get("temperatures")),
        "gpu": _normalize_gpu_payload(gpu_obj),
    }
    return payload


def _build_esp_signature(payload: Dict[str, Any]) -> Dict[str, float]:
    cpu = payload.get("cpu", {})
    memory = payload.get("memory", {})
    ram = memory.get("ram", {}) if isinstance(memory, dict) else {}
    network = payload.get("network_io", {})
    net_total = network.get("total", {}) if isinstance(network, dict) else {}
    net_rate = net_total.get("rate", {}) if isinstance(net_total, dict) else {}
    disk = payload.get("disk_io", {})
    disk_total = disk.get("total", {}) if isinstance(disk, dict) else {}
    disk_rate = disk_total.get("rate", {}) if isinstance(disk_total, dict) else {}
    gpu = payload.get("gpu", {})
    temps = payload.get("temperatures", {})

    cpu_temp = 0.0
    if isinstance(temps, dict):
        for _, entries in temps.items():
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict):
                    cpu_temp = _safe_float(first.get("current"))
                    break

    return {
        "cpu": _safe_float(cpu.get("percent_total")),
        "ram": _safe_float(ram.get("percent")),
        "cpu_temp": cpu_temp,
        "gpu": _safe_float(gpu.get("usage_percent")) if isinstance(gpu, dict) else 0.0,
        "gpu_temp": _safe_float(gpu.get("temperature_celsius")) if isinstance(gpu, dict) else 0.0,
        "net_rx": _safe_float(net_rate.get("rx_bytes_per_s")),
        "net_tx": _safe_float(net_rate.get("tx_bytes_per_s")),
        "disk_read": _safe_float(disk_rate.get("read_bytes_per_s")),
        "disk_write": _safe_float(disk_rate.get("write_bytes_per_s")),
    }


def reset_esp_publish_state():
    global _last_esp_signature, _last_esp_publish_monotonic
    _last_esp_signature = None
    _last_esp_publish_monotonic = 0.0


def should_publish_esp_payload(payload: Dict[str, Any], now_monotonic: Optional[float] = None) -> bool:
    global _last_esp_signature, _last_esp_publish_monotonic
    if now_monotonic is None:
        now_monotonic = time.monotonic()

    current = _build_esp_signature(payload)
    if _last_esp_signature is None:
        _last_esp_signature = current
        _last_esp_publish_monotonic = now_monotonic
        return True

    elapsed = now_monotonic - _last_esp_publish_monotonic
    if elapsed >= ESP_HEARTBEAT_INTERVAL_SEC:
        _last_esp_signature = current
        _last_esp_publish_monotonic = now_monotonic
        return True

    if elapsed < ESP_MIN_PUBLISH_INTERVAL_SEC:
        return False

    thresholds = {
        "cpu": ESP_CPU_DELTA,
        "ram": ESP_RAM_DELTA,
        "cpu_temp": ESP_TEMP_DELTA,
        "gpu": ESP_GPU_DELTA,
        "gpu_temp": ESP_TEMP_DELTA,
        "net_rx": ESP_NET_DELTA_BYTES,
        "net_tx": ESP_NET_DELTA_BYTES,
        "disk_read": ESP_DISK_DELTA_BYTES,
        "disk_write": ESP_DISK_DELTA_BYTES,
    }
    for key, threshold in thresholds.items():
        if abs(current.get(key, 0.0) - _last_esp_signature.get(key, 0.0)) >= threshold:
            _last_esp_signature = current
            _last_esp_publish_monotonic = now_monotonic
            return True
    return False


def _publish_payload(payload: Dict[str, Any]):
    payload = {
        **payload
    }
    publish(mqtt_client, mqtt_stats, TOPIC, payload)


def publish_metrics():
    if SENDER_PROFILE == "esp":
        payload = build_esp_payload()
        if not should_publish_esp_payload(payload):
            return False
        _publish_payload(payload)
        return True

    payload = build_full_payload()
    _publish_payload(payload)
    return True

# ===== Async tasks =====
async def loop_cpu_mem():
    while True:
        metrics["cpu"] = get_cpu_block()
        metrics["memory"] = get_mem_block()
        metrics["system"] = get_system_block()
        await asyncio.sleep(1)

async def loop_disk():
    last = time.time()
    while True:
        now = time.time()
        metrics["disk_io"] = get_disk_io_block(max(1e-6, now - last))
        last = now
        await asyncio.sleep(3)

async def loop_temps():
    while True:
        metrics["temperatures"] = get_temps_block()
        await asyncio.sleep(10)

async def loop_gpu():
    while True:
        metrics["gpu"] = get_gpu_block()
        await asyncio.sleep(5)  # GPU metrics every 5 seconds

async def loop_network():
    last = time.time()
    while True:
        now = time.time()
        metrics["network_io"] = get_net_io_block(max(1e-6, now - last))
        last = now
        await asyncio.sleep(1)

async def loop_publish():
    while True:
        publish_metrics()
        await asyncio.sleep(PUBLISH_INTERVAL_SEC)

async def mqtt_reconnector():
    """自動重連機制，使用指數退避策略"""
    retry_delay = 3  # 初始重連延遲（秒）
    max_delay = 60   # 最大重連延遲（秒）

    while True:
        if not mqtt_client.is_connected():
            mqtt_stats["reconnects"] += 1
            print(f"🔄 嘗試重連 MQTT (第 {mqtt_stats['reconnects']} 次)...")
            mqtt_connect()

            # 等待連線結果
            await asyncio.sleep(2)

            # 根據連線狀態調整延遲
            if mqtt_stats["is_connected"]:
                retry_delay = 3  # 重連成功，重置延遲
            else:
                # 連線失敗，使用指數退避
                retry_delay = min(retry_delay * 2, max_delay)
                print(f"⏳ 重連失敗，{retry_delay} 秒後重試")
        else:
            # 已連線，保持短間隔檢查
            retry_delay = 3

        await asyncio.sleep(retry_delay)

# ===== MAIN =====
async def main():
    print(f"🚀 Async Agent started on {HOSTNAME}")
    print(f"📡 Sender profile: {SENDER_PROFILE}")
    initialize_pve_collector()
    if SENDER_PROFILE == "esp":
        reset_esp_publish_state()
    
    # --- 修正：將 MQTT 連線啟動移入 main 裡面 ---
    try:
        mqtt_connect()
        mqtt_client.loop_start() 
    except Exception as e:
        print(f"❌ Failed to start MQTT: {e}")
        return # 如果連線初始化就失敗，直接退出

    # 預熱 CPU 計算
    psutil.cpu_percent(interval=None, percpu=True)
    
    # 使用 gather 同步執行所有任務
    try:
        await asyncio.gather(
            loop_cpu_mem(),
            loop_disk(),
            loop_temps(),
            loop_gpu(),
            loop_network(),
            loop_publish(),
            mqtt_reconnector(),
        )
    except Exception as e:
        print(f"🚨 Runtime Error: {e}")

if __name__ == "__main__":
    # 確保環境變數有正確讀取，或是提供 fallback
    if not BROKER_HOST:
        print("❌ Error: BROKER_HOST is not set.")
    else:
        try:
            # 這是標準啟動方式
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n🛑 Stopped by user")
        except Exception as e:
            print(f"💥 Fatal Error: {e}")
        finally:
            # 確保最後一定有清理資源
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            print("Cleanup complete.")
