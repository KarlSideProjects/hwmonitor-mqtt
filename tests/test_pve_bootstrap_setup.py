from pathlib import Path

import pytest

from hwmonitor_mqtt.platforms import pve_bootstrap


def test_prepare_creates_local_config_and_copies_trusted_ca(tmp_path):
    config = tmp_path / "etc/hwmonitor-mqtt/pve.env"
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"
    ca_source = tmp_path / "etc/pve/pve-root-ca.pem"
    ca_source.parent.mkdir(parents=True)
    ca_source.write_text("trusted ca fixture")

    pve_bootstrap.prepare(config, template, ca_source, hostname="pve01.example.test", cluster_name="lab-cluster", node_name="pve01", default_ca=config.parent / "pve-root-ca.pem")

    values = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert values["PVE_URL"] == "https://pve01.example.test:8006"
    assert values["PVE_CLUSTER_ID"] == "lab-cluster"
    assert values["PVE_NODE"] == "pve01"
    assert "PVE_TIMEOUT_SEC" not in values
    assert config.stat().st_mode & 0o777 == 0o600
    ca_target = config.parent / "pve-root-ca.pem"
    assert ca_target.read_text() == "trusted ca fixture"
    assert ca_target.stat().st_mode & 0o777 == 0o644


def test_fresh_template_preserves_explicit_public_ca_bundle_path(tmp_path):
    config = tmp_path / "pve.env"
    template = tmp_path / "pve.env.example"
    custom_ca = tmp_path / "custom-ca.pem"
    custom_ca.write_text("custom trusted ca")
    template.write_text(
        "PVE_URL=https://pve.example.invalid:8006\nPVE_TOKEN_ID=monitor@pve!readonly\n"
        "PVE_TOKEN_SECRET=replace-with-private-token-secret\nPVE_CA_FILE=" + str(custom_ca) + "\n"
        "PVE_CLUSTER_ID=lab\nPVE_NODE=pve-node-a\n"
    )
    ca_source = tmp_path / "local-ca.pem"
    ca_source.write_text("native CA")

    pve_bootstrap.prepare(config, template, ca_source, hostname="pve01.example.test", cluster_name="cluster-a",
                          node_name="pve01", default_ca=config.parent / "pve-root-ca.pem")

    assert f"PVE_CA_FILE={custom_ca}" in config.read_text()
    assert custom_ca.read_text() == "custom trusted ca"
    assert not (config.parent / "pve-root-ca.pem").exists()


def test_prepare_migrates_only_known_stock_values_and_preserves_real_settings(tmp_path):
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve.example.invalid:8006\n"
        "PVE_TOKEN_ID=operator@pve!monitor\n"
        "PVE_TOKEN_SECRET=human-secret\n"
        "PVE_CA_FILE=" + str(config.parent / "pve-root-ca.pem") + "\n"
        "PVE_CLUSTER_ID=lab\nPVE_NODE=pve-node-a\nCUSTOM=value\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"
    ca_source = tmp_path / "pve-root-ca.pem"
    ca_source.write_text("trusted")

    pve_bootstrap.prepare(config, template, ca_source, hostname="pve01.example.test", cluster_name="cluster-b", node_name="pve01", default_ca=config.parent / "pve-root-ca.pem")

    text = config.read_text()
    assert "PVE_URL=https://pve01.example.test:8006" in text
    assert "PVE_TOKEN_ID=operator@pve!monitor" in text
    assert "PVE_TOKEN_SECRET=human-secret" in text
    assert "PVE_CLUSTER_ID=cluster-b" in text
    assert "PVE_NODE=pve01" in text
    assert "CUSTOM=value" in text


def test_prepare_preserves_explicit_url_and_custom_ca(tmp_path):
    config = tmp_path / "pve.env"
    custom_ca = tmp_path / "custom-ca.pem"
    custom_ca.write_text("custom trusted ca")
    config.write_text(
        "PVE_URL=https://my-pve.example.test:8006\nPVE_TOKEN_ID=real@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(custom_ca) + "\n"
        "PVE_CLUSTER_ID=production\nPVE_NODE=pve1\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"
    source = tmp_path / "source-ca.pem"
    source.write_text("source ca")

    pve_bootstrap.prepare(config, template, source, hostname="pve02", cluster_name="other", node_name="pve02", default_ca=config.parent / "pve-root-ca.pem")

    assert "PVE_URL=https://my-pve.example.test:8006" in config.read_text()
    assert "PVE_CA_FILE=" + str(custom_ca) in config.read_text()
    assert custom_ca.read_text() == "custom trusted ca"


def test_prepare_preserves_real_cluster_id_even_when_it_matches_stock_hint(tmp_path):
    config = tmp_path / "pve.env"
    ca = tmp_path / "ca.pem"
    ca.write_text("trusted")
    config.write_text(
        "PVE_URL=https://my-pve.example.test:8006\nPVE_TOKEN_ID=operator@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(ca) + "\n"
        "PVE_CLUSTER_ID=lab\nPVE_NODE=pve01\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"

    pve_bootstrap.prepare(config, template, ca, hostname="pve02.example.test", cluster_name="other", node_name="pve02")

    assert "PVE_CLUSTER_ID=lab" in config.read_text()


def test_prepare_migrates_legacy_local_ca_path(tmp_path):
    config = tmp_path / "pve.env"
    source = tmp_path / "local-ca.pem"
    source.write_text("trusted")
    target = tmp_path / "etc/hwmonitor-mqtt/pve-root-ca.pem"
    config.write_text(
        "PVE_URL=https://pve.example.invalid:8006\nPVE_TOKEN_ID=monitor@pve!readonly\n"
        "PVE_TOKEN_SECRET=replace-with-private-token-secret\nPVE_CA_FILE=/etc/pve/pve-root-ca.pem\n"
        "PVE_CLUSTER_ID=lab\nPVE_NODE=pve-node-a\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"

    pve_bootstrap.prepare(config, template, source, hostname="pve01.example.test", cluster_name="cluster-a",
                          node_name="pve01", default_ca=target)

    assert f"PVE_CA_FILE={target}" in config.read_text()
    assert target.read_text() == "trusted"
    assert target.stat().st_mode & 0o777 == 0o644


def test_prepare_repairs_existing_default_ca_permissions(tmp_path):
    config = tmp_path / "pve.env"
    target = tmp_path / "etc/hwmonitor-mqtt/pve-root-ca.pem"
    target.parent.mkdir(parents=True)
    target.write_text("trusted existing CA")
    target.chmod(0o600)
    target.parent.chmod(0o700)
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=operator@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(target) + "\n"
        "PVE_CLUSTER_ID=cluster-a\nPVE_NODE=pve01\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"
    source = tmp_path / "local-ca.pem"
    source.write_text("native CA")

    pve_bootstrap.prepare(config, template, source, default_ca=target)

    assert target.read_text() == "trusted existing CA"
    assert target.stat().st_mode & 0o777 == 0o644
    assert target.parent.stat().st_mode & 0o777 == 0o755


def test_prepare_rewrites_whitespace_legacy_ca_setting(tmp_path):
    config = tmp_path / "pve.env"
    source = tmp_path / "local-ca.pem"
    source.write_text("trusted")
    target = tmp_path / "etc/hwmonitor-mqtt/pve-root-ca.pem"
    config.write_text(
        "PVE_URL=https://pve.example.invalid:8006\nPVE_TOKEN_ID=monitor@pve!readonly\n"
        "PVE_TOKEN_SECRET=replace-with-private-token-secret\nPVE_CA_FILE = /etc/pve/pve-root-ca.pem\n"
        "PVE_CLUSTER_ID=lab\nPVE_NODE=pve-node-a\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"

    pve_bootstrap.prepare(config, template, source, hostname="pve01.example.test", cluster_name="cluster-a",
                          node_name="pve01", default_ca=target)

    assert f"PVE_CA_FILE={target}" in config.read_text()
    assert "PVE_CA_FILE = /etc/pve/pve-root-ca.pem" not in config.read_text()
    assert target.read_text() == "trusted"


def test_prepare_replaces_default_ca_symlink_without_chmodding_source(tmp_path):
    config = tmp_path / "pve.env"
    target = tmp_path / "etc/hwmonitor-mqtt/pve-root-ca.pem"
    target.parent.mkdir(parents=True)
    source = tmp_path / "pve-root-ca.pem"
    source.write_text("trusted source")
    source.chmod(0o600)
    target.symlink_to(source)
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=operator@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(target) + "\n"
        "PVE_CLUSTER_ID=cluster-a\nPVE_NODE=pve01\n"
    )
    template = Path(__file__).parents[1] / "docs/configuration/pve.env.example"

    pve_bootstrap.prepare(config, template, source, default_ca=target)

    assert not target.is_symlink()
    assert target.read_text() == "trusted source"
    assert target.stat().st_mode & 0o777 == 0o644
    assert source.stat().st_mode & 0o777 == 0o600


def test_cluster_detection_parses_pvecm_name_field(monkeypatch):
    class Result:
        returncode = 0
        stdout = "Cluster information\n-------------------\nName:             prod-central\nConfig Version:   3\n"

    monkeypatch.setattr(pve_bootstrap.subprocess, "run", lambda *args, **kwargs: Result())
    assert pve_bootstrap._cluster_name() == "prod-central"


def test_preflight_rejects_placeholder_without_leaking_secret(tmp_path):
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=monitor@pve!readonly\n"
        "PVE_TOKEN_SECRET=replace-with-private-token-secret\nPVE_CA_FILE=/missing/ca.pem\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )

    with pytest.raises(pve_bootstrap.PveSetupError) as error:
        pve_bootstrap.preflight(config)

    assert "placeholder" in str(error.value).lower()
    assert "replace-with-private-token-secret" not in str(error.value)


def test_preflight_requires_ca_readable_by_dynamic_user(tmp_path):
    config = tmp_path / "pve.env"
    ca = tmp_path / "ca.pem"
    ca.write_text("trusted")
    ca.chmod(0o600)
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=real@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(ca) + "\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )

    with pytest.raises(pve_bootstrap.PveSetupError, match="DynamicUser"):
        pve_bootstrap.preflight(config)


def test_preflight_rejects_ca_symlink_into_private_directory(tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    private_dir.chmod(0o700)
    target = private_dir / "ca.pem"
    target.write_text("trusted")
    target.chmod(0o644)
    symlink = tmp_path / "ca-link.pem"
    symlink.symlink_to(target)
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=operator@pve!token\n"
        "PVE_TOKEN_SECRET=real-secret\nPVE_CA_FILE=" + str(symlink) + "\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )

    with pytest.raises(pve_bootstrap.PveSetupError, match="DynamicUser"):
        pve_bootstrap.preflight(config)


def test_preflight_accepts_legitimate_credentials_containing_example(tmp_path):
    ca = Path("/etc/ssl/certs/ca-certificates.crt")
    if not ca.is_file():
        pytest.skip("system CA bundle is unavailable")
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=example@pve!readonly\n"
        "PVE_TOKEN_SECRET=secret-example-42\nPVE_CA_FILE=" + str(ca) + "\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )

    class Client:
        def __init__(self, parsed):
            assert parsed.token_id == "example@pve!readonly"

        def validate_scope(self):
            return None

    parsed = pve_bootstrap.preflight(config, client_factory=Client)
    assert parsed.cluster_id == "pve01"


def test_preflight_forwards_sanitized_api_failure_without_credentials(tmp_path):
    ca = Path("/etc/ssl/certs/ca-certificates.crt")
    if not ca.is_file():
        pytest.skip("system CA bundle is unavailable")
    config = tmp_path / "pve.env"
    config.write_text(
        "PVE_URL=https://pve01.example.test:8006\nPVE_TOKEN_ID=operator@pve!token\n"
        "PVE_TOKEN_SECRET=super-secret-token\nPVE_CA_FILE=" + str(ca) + "\n"
        "PVE_CLUSTER_ID=pve01\nPVE_NODE=pve01\n"
    )

    class Client:
        def __init__(self, parsed):
            pass

        def validate_scope(self):
            raise pve_bootstrap.PveError("unauthorized")

    with pytest.raises(pve_bootstrap.PveSetupError, match="unauthorized") as error:
        pve_bootstrap.preflight(config, client_factory=Client)
    assert "super-secret-token" not in str(error.value)
