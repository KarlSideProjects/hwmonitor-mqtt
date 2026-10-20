"""Provision a dedicated, read-only PVE API identity for the sender."""

import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile


APP_COMMENT = "Managed by hwmonitor-mqtt sender bootstrap"
STOCK_TOKEN_ID = "monitor@pve!readonly"


class PveTokenError(Exception):
    """Safe-to-display token setup error."""


def _read(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    except OSError:
        raise PveTokenError("PVE configuration is unreadable") from None
    return values


def _atomic_text(path, content, mode=0o600):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(mode)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        raise PveTokenError("Could not securely save PVE token setup state") from None


def _write_config(path, token_id, token_secret):
    path = Path(path)
    try:
        original = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise PveTokenError("PVE configuration is unreadable") from None
    updated = []
    seen = set()
    for line in original:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key == "PVE_TOKEN_ID":
                updated.append(f"PVE_TOKEN_ID={token_id}")
                seen.add(key)
                continue
            if key == "PVE_TOKEN_SECRET":
                updated.append(f"PVE_TOKEN_SECRET={token_secret}")
                seen.add(key)
                continue
        updated.append(line)
    if "PVE_TOKEN_ID" not in seen:
        updated.append(f"PVE_TOKEN_ID={token_id}")
    if "PVE_TOKEN_SECRET" not in seen:
        updated.append(f"PVE_TOKEN_SECRET={token_secret}")
    _atomic_text(path, "\n".join(updated).rstrip() + "\n")


def _run(run, command, failure):
    try:
        result = run(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        raise PveTokenError(failure) from None
    if result.returncode:
        raise PveTokenError(failure)
    return result


def _load_state(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("version") != 1 or not re.fullmatch(r"hwmonitor-[a-f0-9]{8}@pve", value.get("user_id", ""))
                or not re.fullmatch(r"[a-z0-9-]{1,64}", value.get("token_name", ""))
                or not isinstance(value.get("acl_complete", False), bool)
                or ("token_id" in value and value["token_id"] != f'{value["user_id"]}!{value["token_name"]}')):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        raise PveTokenError("PVE token setup state is invalid; inspect the private state file before retrying") from None


def _save_state(path, state):
    _atomic_text(path, json.dumps(state, sort_keys=True) + "\n")


def _listing(run, command, failure):
    result = _run(run, command, failure)
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError
        return rows
    except (TypeError, ValueError, json.JSONDecodeError):
        raise PveTokenError("Could not safely inspect existing PVE token setup; no credentials were changed") from None


def provision_token(config_file, state_file=None, *, run=subprocess.run, app_id=None, prepare_config=None):
    """Serialize bootstrap attempts so a one-time token secret cannot be lost."""
    config_file = Path(config_file)
    state_file = Path(state_file) if state_file else _default_state_path(config_file)
    lock_path = config_file.parent / ".pve-token-setup.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if prepare_config is not None:
            prepare_config()
        return _provision_token_locked(config_file, state_file, run=run, app_id=app_id)
    except OSError:
        raise PveTokenError("Could not safely lock PVE token setup; no credentials were changed") from None
    finally:
        if "lock_fd" in locals():
            os.close(lock_fd)


def _default_state_path(config_file):
    config_file = Path(config_file)
    name = "pve-token-state.json" if config_file.name == "pve.env" else f"{config_file.name}.token-state.json"
    return config_file.parent / name


def _provision_token_locked(config_file, state_file, *, run, app_id):
    """Provision only for an empty/stock token; reuse complete existing credentials."""
    values = _read(config_file)
    configured_id = values.get("PVE_TOKEN_ID", "")
    configured_secret = values.get("PVE_TOKEN_SECRET", "")
    stock_secret = not configured_secret or configured_secret.lower().startswith(("replace-with-", "placeholder", "change-me"))
    state = _load_state(state_file)

    if not stock_secret:
        if state and configured_id == state.get("token_id"):
            if not state.get("acl_complete", False):
                _apply_acl(run, state_file, state, configured_id)
            return state["user_id"], configured_id
        return None, configured_id
    if configured_id and configured_id != STOCK_TOKEN_ID:
        raise PveTokenError("PVE_TOKEN_SECRET is missing or a placeholder for the configured custom token; set its real secret explicitly")

    if state is None:
        app_id = app_id or secrets.token_hex(4)
        if not re.fullmatch(r"[a-f0-9]{8}", app_id):
            raise PveTokenError("Invalid internal PVE token setup identifier")
        state = {"version": 1, "user_id": f"hwmonitor-{app_id}@pve", "token_name": "hwmonitor-mqtt"}
        _save_state(state_file, state)

    user_id = state["user_id"]
    users = _listing(run, ["pveum", "user", "list", "--output-format", "json"], "Could not inspect PVE users; no credentials were changed")
    if any(not isinstance(row.get("userid"), str) for row in users):
        raise PveTokenError("Could not safely inspect PVE users; no credentials were changed")
    found = next((row for row in users if row["userid"] == user_id), None)
    if found and found.get("comment") != APP_COMMENT:
        raise PveTokenError("Generated PVE account name already belongs to another user; inspect the private setup state before retrying")
    if not found:
        _run(run, ["pveum", "user", "add", user_id, "--comment", APP_COMMENT], "Could not create the dedicated PVE monitor account")

    while True:
        tokens = _listing(run, ["pveum", "user", "token", "list", user_id, "--output-format", "json"], "Could not inspect dedicated PVE tokens; no credentials were changed")
        if any(not isinstance(row.get("tokenid"), str) for row in tokens):
            raise PveTokenError("Could not safely inspect dedicated PVE tokens; no credentials were changed")
        token_names = {row.get("tokenid") for row in tokens}
        token_name = state["token_name"]
        if token_name not in token_names:
            break
        # A token with no saved secret is not recoverable; leave it untouched.
        state["token_name"] = f"hwmonitor-mqtt-{secrets.token_hex(4)}"
        state.pop("token_id", None)
        _save_state(state_file, state)
    token_id = f"{user_id}!{token_name}"
    state["token_id"] = token_id
    _save_state(state_file, state)
    result = _run(run, ["pveum", "user", "token", "add", user_id, token_name,
                        "--privsep", "1", "--comment", APP_COMMENT, "--output-format", "json"],
                  "Could not create the dedicated PVE API token; no token output was logged")
    try:
        response = json.loads(result.stdout)
        token_secret = response["value"]
        returned_id = response["full-tokenid"]
        if (not isinstance(token_secret, str) or not re.fullmatch(r"[A-Za-z0-9_./+=-]{1,512}", token_secret)
                or returned_id != token_id):
            raise ValueError
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise PveTokenError("PVE returned an unreadable token response; rerun bootstrap to continue safely") from None
    _write_config(config_file, token_id, token_secret)
    state["token_id"] = token_id
    _save_state(state_file, state)

    _apply_acl(run, state_file, state, token_id)
    return user_id, token_id


def _apply_acl(run, state_file, state, token_id):
    user_id = state["user_id"]
    _run(run, ["pveum", "acl", "modify", "/", "--users", user_id, "--roles", "PVEAuditor", "--propagate", "1"],
         "PVE ACL setup failed while assigning the read-only user role; rerun bootstrap to resume token setup")
    _run(run, ["pveum", "acl", "modify", "/", "--tokens", token_id, "--roles", "PVEAuditor", "--propagate", "1"],
         "PVE ACL setup failed while assigning the read-only token role; rerun bootstrap to resume token setup")
    state["acl_complete"] = True
    _save_state(state_file, state)
