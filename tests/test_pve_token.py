import json
from subprocess import CompletedProcess

import pytest

from hwmonitor_mqtt.platforms import pve_token


def test_provision_creates_dedicated_user_and_token_then_stores_secret_before_acl(tmp_path):
    config = _token_config(tmp_path)
    state = tmp_path / "pve-token-state.json"
    token_secret = "one-time-token-secret"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"' + token_secret + '","full-tokenid":"hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt"}')
        if command[:3] == ["pveum", "acl", "modify"]:
            assert f"PVE_TOKEN_SECRET={token_secret}" in config.read_text()
            assert config.stat().st_mode & 0o777 == 0o600
        return _result(0)

    user, token = pve_token.provision_token(config, state, run=run, app_id="a1b2c3d4")

    assert user == "hwmonitor-a1b2c3d4@pve"
    assert token == user + "!hwmonitor-mqtt"
    assert f"PVE_TOKEN_ID={token}" in config.read_text()
    assert f"PVE_TOKEN_SECRET={token_secret}" in config.read_text()
    assert state.stat().st_mode & 0o777 == 0o600
    assert token_secret not in " ".join(arg for command in calls for arg in command)
    assert [command[2] for command in calls if command[:2] == ["pveum", "acl"]] == ["modify", "modify"]


def test_provision_reuses_complete_existing_token_without_mutations(tmp_path):
    config = _token_config(tmp_path, token_id="operator@pve!existing", secret="human-secret")
    calls = []
    user, token = pve_token.provision_token(config, tmp_path / "state.json", run=lambda command, **kwargs: calls.append(command))

    assert user is None
    assert token == "operator@pve!existing"
    assert calls == []


def test_provision_uses_new_app_token_without_rotating_preferred_existing_token(tmp_path):
    config = _token_config(tmp_path)
    preferred_token = "monitor@pve!readonly"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, '[{"tokenid":"hwmonitor-mqtt"}]')
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"new-secret","full-tokenid":"' + command[4] + '!' + command[5] + '"}')
        return _result(0)

    user, token = pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert preferred_token not in " ".join(arg for command in calls for arg in command)
    assert token.startswith(user + "!hwmonitor-mqtt-")
    assert token != preferred_token
    assert f"PVE_TOKEN_ID={token}" in config.read_text()


def test_provision_retry_after_acl_failure_resumes_without_creating_another_token(tmp_path):
    config = _token_config(tmp_path)
    state = tmp_path / "state.json"
    token_secret = "only-copy-of-secret"
    token_add_count = 0
    token_created = False
    fail_token_acl = True

    def run(command, **kwargs):
        nonlocal token_add_count, token_created
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]" if not token_created else '[{"userid":"hwmonitor-a1b2c3d4@pve","comment":"Managed by hwmonitor-mqtt sender bootstrap"}]')
        if command[:4] == ["pveum", "user", "token", "list"]:
            if not token_created:
                return _result(0, "[]")
            token_name = state.read_text()
            import json
            name = json.loads(token_name)["token_name"]
            return _result(0, '[{"tokenid":"' + name + '"}]')
        if command[:4] == ["pveum", "user", "token", "add"]:
            token_add_count += 1
            token_created = True
            return _result(0, '{"value":"' + token_secret + '","full-tokenid":"hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt"}')
        if command[:3] == ["pveum", "acl", "modify"] and "--tokens" in command and fail_token_acl:
            return _result(1, stderr="failure with potentially sensitive details")
        return _result(0)

    with pytest.raises(pve_token.PveTokenError, match="ACL"):
        pve_token.provision_token(config, state, run=run, app_id="a1b2c3d4")
    assert "failure with potentially sensitive details" not in str(pve_token.PveTokenError)
    fail_token_acl = False
    calls = []

    def successful_retry(command, **kwargs):
        calls.append(command)
        return run(command, **kwargs)

    pve_token.provision_token(config, state, run=successful_retry, app_id="ignored")

    assert token_add_count == 1
    assert any(command[:3] == ["pveum", "acl", "modify"] for command in calls)


def _result(returncode=0, stdout="", stderr=""):
    from subprocess import CompletedProcess
    return CompletedProcess([], returncode, stdout, stderr)


def _token_config(tmp_path, token_id="monitor@pve!readonly", secret="replace-with-private-token-secret"):
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=" + token_id + "\n"
        "PVE_TOKEN_SECRET=" + secret + "\nPVE_CA_FILE=/etc/ssl/certs/ca-certificates.crt\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )
    config.chmod(0o600)
    return config


def test_owned_token_with_completed_acl_is_reused_without_mutations(tmp_path):
    config = _token_config(tmp_path, token_id="hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt", secret="saved-secret")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"version": 1, "user_id": "hwmonitor-a1b2c3d4@pve", "token_name": "hwmonitor-mqtt", "token_id": "hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt", "acl_complete": True}))
    calls = []

    result = pve_token.provision_token(config, state, run=lambda command, **kwargs: calls.append(command))

    assert result == ("hwmonitor-a1b2c3d4@pve", "hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt")
    assert calls == []


def test_rejects_mismatched_full_token_id_before_saving_or_acl(tmp_path):
    config = _token_config(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"do-not-leak","full-tokenid":"other@pve!token"}')
        return _result(0)

    with pytest.raises(pve_token.PveTokenError):
        pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert "do-not-leak" not in config.read_text()
    assert not any(command[:3] == ["pveum", "acl", "modify"] for command in calls)


def test_malformed_user_list_fails_closed_before_creating_account(tmp_path):
    config = _token_config(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return _result(0, "")

    with pytest.raises(pve_token.PveTokenError, match="inspect"):
        pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert len(calls) == 1
    assert calls[0][:3] == ["pveum", "user", "list"]


def test_concurrent_bootstraps_serialize_one_time_token_creation(tmp_path):
    import threading

    config = _token_config(tmp_path)
    state = tmp_path / "state.json"
    token_add_started = threading.Event()
    release_token_add = threading.Event()
    token_add_count = 0
    count_lock = threading.Lock()
    errors = []

    def run(command, **kwargs):
        nonlocal token_add_count
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            with count_lock:
                token_add_count += 1
                is_first = token_add_count == 1
            if is_first:
                token_add_started.set()
                assert release_token_add.wait(3)
            return _result(0, '{"value":"secret-' + str(token_add_count) + '","full-tokenid":"hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt"}')
        return _result(0)

    def provision():
        try:
            pve_token.provision_token(config, state, run=run, app_id="a1b2c3d4")
        except Exception as error:  # surfaced in the parent test thread
            errors.append(error)

    first = threading.Thread(target=provision)
    second = threading.Thread(target=provision)
    first.start()
    assert token_add_started.wait(3)
    second.start()
    release_token_add.set()
    first.join(3)
    second.join(3)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert token_add_count == 1


def test_rejects_control_character_in_returned_secret_without_saving_it(tmp_path):
    config = _token_config(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"secret\\nleak","full-tokenid":"hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt"}')
        return _result(0)

    with pytest.raises(pve_token.PveTokenError):
        pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert "PVE_TOKEN_SECRET=replace-with-private-token-secret" in config.read_text()
    assert not any(command[:3] == ["pveum", "acl", "modify"] for command in calls)


def test_invalid_state_token_id_cannot_direct_acl_to_another_identity(tmp_path):
    config = _token_config(tmp_path, token_id="other@pve!untrusted", secret="saved-secret")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"version": 1, "user_id": "hwmonitor-a1b2c3d4@pve", "token_name": "hwmonitor-mqtt", "token_id": "other@pve!untrusted", "acl_complete": False}))
    calls = []

    with pytest.raises(pve_token.PveTokenError, match="state"):
        pve_token.provision_token(config, state, run=lambda command, **kwargs: calls.append(command))

    assert calls == []


def test_rejects_token_response_without_exact_full_token_id(tmp_path):
    config = _token_config(tmp_path)

    def run(command, **kwargs):
        if command[:3] == ["pveum", "user", "list"] or command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"secret-without-id"}')
        return _result(0)

    with pytest.raises(pve_token.PveTokenError):
        pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert "secret-without-id" not in config.read_text()


def test_rejects_shell_metacharacters_in_generated_token_secret(tmp_path):
    config = _token_config(tmp_path)

    def run(command, **kwargs):
        if command[:3] == ["pveum", "user", "list"] or command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, "[]")
        if command[:4] == ["pveum", "user", "token", "add"]:
            return _result(0, '{"value":"secret$unsafe","full-tokenid":"hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt"}')
        return _result(0)

    with pytest.raises(pve_token.PveTokenError):
        pve_token.provision_token(config, tmp_path / "state.json", run=run, app_id="a1b2c3d4")

    assert "secret$unsafe" not in config.read_text()


def test_locked_prepare_runs_before_existing_credentials_are_read(tmp_path):
    config = _token_config(tmp_path)
    calls = []

    def prepare_config():
        config.write_text(
            "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=operator@pve!existing\n"
            "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=/etc/ssl/certs/ca-certificates.crt\n"
            "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
        )

    result = pve_token.provision_token(config, run=lambda command, **kwargs: calls.append(command), prepare_config=prepare_config)

    assert result == (None, "operator@pve!existing")
    assert calls == []


def test_custom_config_uses_its_own_private_state_filename(tmp_path):
    assert pve_token._default_state_path(tmp_path / "pve.env") == tmp_path / "pve-token-state.json"
    assert pve_token._default_state_path(tmp_path / "custom.env") == tmp_path / "custom.env.token-state.json"


def test_replacing_lost_secret_resets_completed_acl_state_for_retry(tmp_path):
    config = _token_config(tmp_path)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "version": 1,
        "user_id": "hwmonitor-a1b2c3d4@pve",
        "token_name": "hwmonitor-mqtt",
        "token_id": "hwmonitor-a1b2c3d4@pve!hwmonitor-mqtt",
        "acl_complete": True,
    }))
    token_add_count = 0
    token_acl_attempts = 0
    fail_new_token_acl = True
    new_name = None

    def run(command, **kwargs):
        nonlocal token_add_count, token_acl_attempts, new_name
        if command[:3] == ["pveum", "user", "list"]:
            return _result(0, '[{"userid":"hwmonitor-a1b2c3d4@pve","comment":"Managed by hwmonitor-mqtt sender bootstrap"}]')
        if command[:4] == ["pveum", "user", "token", "list"]:
            return _result(0, '[{"tokenid":"hwmonitor-mqtt"}]')
        if command[:4] == ["pveum", "user", "token", "add"]:
            token_add_count += 1
            new_name = command[5]
            return _result(0, '{"value":"replacement-secret","full-tokenid":"' + command[4] + '!' + new_name + '"}')
        if command[:3] == ["pveum", "acl", "modify"] and "--tokens" in command:
            token_acl_attempts += 1
            if fail_new_token_acl:
                return _result(1, stderr="private error")
        return _result(0)

    with pytest.raises(pve_token.PveTokenError, match="ACL"):
        pve_token.provision_token(config, state, run=run)

    fail_new_token_acl = False
    pve_token.provision_token(config, state, run=run)

    assert token_add_count == 1
    assert token_acl_attempts == 2
    assert new_name and new_name != "hwmonitor-mqtt"
    assert json.loads(state.read_text())["acl_complete"] is True
