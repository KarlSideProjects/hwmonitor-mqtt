"""Cached PVE guest inventory, independent of the host metric publish loop."""

import json
import math
import threading
import time

from hwmonitor_mqtt.clients.pve import PveError


MAX_PAYLOAD_BYTES = 262144


def _number(value, *, integer=False):
    if value is None:
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not finite:
        raise PveError("invalid_inventory")
    if value < 0 or (integer and int(value) != value):
        raise PveError("invalid_inventory")
    return int(value) if integer else float(value)


def _guest(row, config):
    if not isinstance(row, dict):
        raise PveError("invalid_inventory")
    vmid, kind, node = row.get("vmid"), row.get("type"), row.get("node")
    if not isinstance(node, str) or not node or isinstance(vmid, bool) or not isinstance(vmid, int) or not 0 < vmid <= 999999999 or kind not in {"qemu", "lxc"}:
        raise PveError("invalid_inventory")
    name, status = row.get("name"), row.get("status")
    if name is None or name == "":
        name = f"{kind}-{vmid}"
    if status is None or status == "":
        status = "unknown"
    if not isinstance(name, str) or not name or len(name) > 128 or not isinstance(status, str) or not status or len(status) > 32:
        raise PveError("invalid_inventory")
    if node != config.node:
        return None
    cpu = _number(row.get("cpu"))
    if cpu is not None and cpu > 1:
        raise PveError("invalid_inventory")
    vcpus = _number(row.get("maxcpu"), integer=True)
    if vcpus is not None and vcpus > 4096:
        raise PveError("invalid_inventory")
    if vcpus == 0:
        vcpus = None
    used, total = _number(row.get("mem"), integer=True), _number(row.get("maxmem"), integer=True)
    if max(used or 0, total or 0) > 2**63 - 1:
        raise PveError("invalid_inventory")
    percent = round(100 * used / total, 1) if used is not None and total else None
    return {
        "id": f"{config.cluster_id}:{kind}:{vmid}", "vmid": vmid, "type": kind, "node": config.node,
        "name": name, "status": status, "uptime": _number(row.get("uptime"), integer=True),
        "vcpus": vcpus, "memory_basis": "pve",
        "cpu": {"percent_total": round(cpu * 100, 1) if cpu is not None else None},
        "memory": {"ram": {"used": used, "total": total, "percent": percent}},
        "network_io": {"total": {"rate": {"rx_bytes_per_s": None, "tx_bytes_per_s": None}}},
        "disk_io": {"total": {"rate": {"read_bytes_per_s": None, "write_bytes_per_s": None}}},
    }


class PveCollector:
    def __init__(self, client, poll_interval=10, wall_clock=time.time, monotonic=time.monotonic):
        self.client, self.poll_interval = client, poll_interval
        self.wall_clock, self.monotonic = wall_clock, monotonic
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_good = None
        self._baselines = {}
        self._snapshot = self._envelope(getattr(client, "config", None), "error" if client else "unconfigured")
        if client:
            self._snapshot["error"] = "not_observed"

    @staticmethod
    def _envelope(config, status):
        snapshot = {"version": 1, "provider": "pve", "cluster_id": config.cluster_id if config else None,
                    "node": config.node if config else None, "observed_at": None, "status": status}
        return snapshot

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._snapshot))

    def capacity_error(self):
        with self._lock:
            snapshot = self._envelope(getattr(self.client, "config", None), "error")
            snapshot["observed_at"] = self._last_good.get("observed_at") if self._last_good else None
            snapshot["status"], snapshot["error"] = "error", "capacity_error"
            self._snapshot = snapshot
            return json.loads(json.dumps(snapshot))

    def mark_error(self, code):
        with self._lock:
            self._snapshot = self._envelope(None, "error")
            self._snapshot["error"] = str(code)[:64]

    def poll_once(self, now=None):
        config = getattr(self.client, "config", None)
        if self.client is None:
            return self.snapshot()
        try:
            rows = self.client.resources()
            guests = []
            seen = set()
            baselines = dict(self._baselines)
            observed = self.monotonic()
            for row in rows:
                guest = _guest(row, config)
                if guest is None:
                    continue
                if guest["id"] in seen:
                    raise PveError("invalid_inventory")
                seen.add(guest["id"])
                counters = tuple(_number(row.get(key), integer=True) for key in ("netin", "netout", "diskread", "diskwrite"))
                if any(value is not None and value > 2**63 - 1 for value in counters):
                    raise PveError("invalid_inventory")
                previous = self._baselines.get(guest["id"])
                reset = previous is None
                if previous:
                    reset = (previous["node"] != guest["node"] or
                             previous["uptime"] is not None and guest["uptime"] is not None and guest["uptime"] < previous["uptime"] or
                             any(old is not None and new is not None and new < old
                                 for old, new in zip(previous["counters"], counters)))
                elapsed = observed - previous["time"] if previous else 0
                values = [None] * 4
                if previous and not reset and elapsed > 0:
                    values = [round((new - old) / elapsed, 2) if old is not None and new is not None else None
                              for old, new in zip(previous["counters"], counters)]
                    if any(value is not None and (not math.isfinite(value) or value > 2**63 - 1) for value in values):
                        raise PveError("invalid_inventory")
                guest["network_io"]["total"]["rate"] = {"rx_bytes_per_s": values[0], "tx_bytes_per_s": values[1]}
                guest["disk_io"]["total"]["rate"] = {"read_bytes_per_s": values[2], "write_bytes_per_s": values[3]}
                baselines[guest["id"]] = {"node": guest["node"], "uptime": guest["uptime"], "counters": counters, "time": observed}
                guests.append(guest)
            candidate = {"version": 1, "provider": "pve", "cluster_id": config.cluster_id,
                         "node": config.node, "observed_at": int(self.wall_clock() if now is None else now),
                         "status": "ok", "guests": guests}
            if len(json.dumps(candidate, separators=(",", ":")).encode()) > MAX_PAYLOAD_BYTES:
                raise PveError("capacity_error")
            self._last_good = candidate
            self._baselines = {key: value for key, value in baselines.items() if key in seen}
            snapshot = candidate
        except (PveError, AttributeError, TypeError, ValueError) as error:
            code = str(error) if isinstance(error, PveError) else "invalid_inventory"
            snapshot = dict(self._last_good or self._envelope(config, "error"))
            snapshot["status"], snapshot["error"] = "error", code[:64]
        with self._lock:
            self._snapshot = snapshot
            return json.loads(json.dumps(snapshot))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pve-poller", daemon=True)
        self._thread.start()

    def _run(self):
        deadline = self.monotonic()
        while not self._stop.is_set():
            self.poll_once()
            deadline += self.poll_interval
            if deadline < self.monotonic():
                deadline = self.monotonic() + self.poll_interval
            self._stop.wait(max(0, deadline - self.monotonic()))

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
