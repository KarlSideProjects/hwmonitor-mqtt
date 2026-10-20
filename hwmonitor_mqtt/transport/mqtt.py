"""MQTT client and publication behavior shared by platform collectors."""

import json
import os

from paho.mqtt import client as mqtt


def configuration(hostname, default_broker_host=""):
    return {
        "hostname": hostname,
        "broker_host": os.getenv("BROKER_HOST", default_broker_host).strip(),
        "broker_port": int(os.getenv("BROKER_PORT", "1883")),
        "username": os.getenv("MQTT_USER", ""),
        "password": os.getenv("MQTT_PASS", ""),
        "topic": f"sys/agents/{hostname}/metrics",
    }


def create_client(config):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"agent-{config['hostname']}")
    if config["username"] or config["password"]:
        client.username_pw_set(config["username"] or None, config["password"] or None)
    stats = {
        "publish_ok": 0,
        "publish_err": 0,
        "last_publish_rc": None,
        "reconnects": 0,
        "is_connected": False,
        "last_error": None,
    }

    def on_connect(client, userdata, connect_flags, reason_code, properties=None):
        stats["is_connected"] = reason_code == 0
        if reason_code == 0:
            print(f"MQTT connected to {config['broker_host']}:{config['broker_port']}")
            stats["last_error"] = None
        else:
            print(f"MQTT connect failed: reason_code={reason_code}")
            stats["last_error"] = f"Connect failed: {reason_code}"

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
        stats["is_connected"] = False
        stats["last_error"] = f"Disconnected: {reason_code}"

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    return client, stats, on_connect, on_disconnect


def publish(client, stats, topic, payload):
    try:
        info = client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=0, retain=False)
        stats["last_publish_rc"] = info.rc
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            stats["publish_ok"] += 1
        else:
            stats["publish_err"] += 1
    except Exception as error:
        stats["publish_err"] += 1
        stats["last_error"] = str(error)
