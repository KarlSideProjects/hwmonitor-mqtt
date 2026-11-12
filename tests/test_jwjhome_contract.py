import json
from pathlib import Path
from test_esp_sender_profile import _import_agent_sender_async


def test_full_payload_example_matches_sender(monkeypatch):
    sender = _import_agent_sender_async()
    monkeypatch.setattr(sender, 'HOSTNAME', 'PC-1')
    monkeypatch.setattr(sender, 'SENDER_VERSION', None)
    monkeypatch.setattr(sender, 'pve_collector', None)
    monkeypatch.setattr(sender, 'metrics', {
        'system': None, 'cpu': {'percent_total': 37.4},
        'memory': {'ram': {'percent': 51.2}}, 'disk_io': None,
        'temperatures': {'cpu': [{'current': 71.2}]}, 'network_io': None,
        'gpu': None, 'disk_smart': {'observed_at': None, 'status': 'unavailable', 'alerts': [], 'devices': {}},
    })
    monkeypatch.setattr(sender, 'mqtt_stats', {'publish_ok': 0, 'publish_err': 0, 'last_publish_rc': None, 'is_connected': False, 'reconnects': 0, 'last_error': None})
    example = json.loads((Path(__file__).parent / 'fixtures' / 'jwjhome-full-v1.json').read_text())
    assert sender.build_full_payload(now_ts=1700000000) == example
