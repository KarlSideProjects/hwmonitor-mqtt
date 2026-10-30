"""Observe sender payload shape on the isolated native smoke broker."""

import argparse
import json
import os
import socket
import threading

from paho.mqtt import client as mqtt


STATUSES = {"ok", "warning", "critical", "unknown", "unavailable"}


def matches(payload, profile):
    if not isinstance(payload, dict) or not isinstance(payload.get("host"), str):
        return False
    if not isinstance(payload.get("cpu"), dict) or not isinstance(payload.get("memory"), dict):
        return False
    if profile == "esp":
        return payload.get("disk_health") in STATUSES and "disk_smart" not in payload
    smart = payload.get("disk_smart")
    return (isinstance(smart, dict) and smart.get("status") in STATUSES
            and isinstance(smart.get("alerts"), list) and isinstance(smart.get("devices"), dict))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("full", "esp"), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    received = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    username = os.environ.get("SMOKE_MQTT_USER", "")
    if username:
        client.username_pw_set(username, os.environ.get("SMOKE_MQTT_PASS", ""))

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(f"sys/agents/{socket.gethostname()}/metrics")

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if matches(payload, args.profile):
            received.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("127.0.0.1", args.port, 30)
    client.loop_start()
    try:
        if not received.wait(45):
            raise SystemExit(f"No valid {args.profile} sender payload observed")
        print(f"Observed valid {args.profile} sender payload")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
