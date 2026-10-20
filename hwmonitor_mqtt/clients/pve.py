"""Small, read-only HTTPS adapter for the Proxmox VE API."""

from dataclasses import dataclass
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class PveConfig:
    base_url: str
    token_id: str
    token_secret: str
    ca_file: str
    cluster_id: str
    node: str
    timeout: float = 3.0

    @classmethod
    def from_environment(cls, env=None, env_file="/etc/hwmonitor-mqtt/pve.env"):
        env = os.environ if env is None else env
        values = {}
        if env.get("PVE_CONFIG_LOADED") != "1":
            path = Path(env_file)
        else:
            path = None
        if path and path.is_file():
            try:
                lines = path.read_text().splitlines()
            except OSError:
                raise PveError("config_unreadable") from None
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        values.update(env)
        keys = ("PVE_URL", "PVE_TOKEN_ID", "PVE_TOKEN_SECRET", "PVE_CA_FILE", "PVE_CLUSTER_ID", "PVE_NODE")
        if any(not values.get(key) for key in keys):
            return None
        timeout = _finite(values.get("PVE_TIMEOUT_SEC", "3"), "invalid_config")
        config = cls(*(values[key].strip() for key in keys), timeout)
        _validate_config(config)
        return config


class PveError(Exception):
    """Sanitized PVE failure code; never contains credentials or response text."""


def _finite(value, code):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PveError(code) from None
    if not math.isfinite(number):
        raise PveError(code)
    return number


def _validate_config(config):
    try:
        parsed = urllib.parse.urlsplit(config.base_url)
    except ValueError:
        raise PveError("invalid_config") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or len(config.base_url) > 2048):
        raise PveError("invalid_config")
    if not config.token_id or not config.token_secret or not config.cluster_id or not config.node:
        raise PveError("invalid_config")
    if (len(config.token_id) > 256 or len(config.token_secret) > 512
            or any(ord(char) < 33 or ord(char) == 127 for char in config.token_id + config.token_secret)):
        raise PveError("invalid_config")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", config.cluster_id) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", config.node) or config.timeout <= 0 or config.timeout > 30:
        raise PveError("invalid_config")
    try:
        ssl.create_default_context(cafile=config.ca_file)
    except (OSError, ssl.SSLError):
        raise PveError("invalid_ca") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class PveApiClient:
    def __init__(self, config: PveConfig):
        _validate_config(config)
        self.config = config
        self.context = ssl.create_default_context(cafile=config.ca_file)
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.context), _NoRedirect())

    def _open(self, request):
        try:
            return self.opener.open(request, timeout=self.config.timeout)
        except urllib.error.URLError as error:
            strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
            reason = error.reason
            if (not strict or not self.context.verify_flags & strict
                    or not isinstance(reason, ssl.SSLCertVerificationError)
                    or reason.verify_code != 92):
                raise
            self.context.verify_flags &= ~strict
            self.opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self.context), _NoRedirect())
            return self.opener.open(request, timeout=self.config.timeout)

    def _get(self, path):
        url = self.config.base_url.rstrip("/") + "/api2/json/" + path.lstrip("/")
        request = urllib.request.Request(url, headers={
            "Authorization": f"PVEAPIToken={self.config.token_id}={self.config.token_secret}",
            "Accept": "application/json",
        })
        try:
            with self._open(request) as response:
                if response.status != 200:
                    raise PveError("http_error")
                raw = response.read(262145)
            if len(raw) > 262144:
                raise PveError("response_too_large")
            data = json.loads(raw)
            if not isinstance(data, dict) or "data" not in data:
                raise PveError("invalid_response")
            return data["data"]
        except PveError:
            raise
        except urllib.error.HTTPError as error:
            code = "unauthorized" if error.code == 401 else "forbidden" if error.code == 403 else "http_error"
            raise PveError(code) from None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
            raise PveError("connection_error") from None
        except (ValueError, json.JSONDecodeError, RecursionError):
            raise PveError("invalid_response") from None

    def resources(self):
        self.validate_scope()
        data = self._get("cluster/resources?type=vm")
        if not isinstance(data, list):
            raise PveError("invalid_response")
        return data

    def validate_scope(self):
        nodes = self._get("nodes")
        if not isinstance(nodes, list) or not any(isinstance(row, dict) and row.get("node") == self.config.node for row in nodes):
            raise PveError("node_not_visible")
        permissions = self._get("access/permissions")
        complete = isinstance(permissions, dict) and any(
            path in {"/", "/vms"} and isinstance(privileges, dict)
            and privileges.get("VM.Audit") in (True, 1, "1")
            for path, privileges in permissions.items()
        )
        acl_audit = isinstance(permissions, dict) and any(
            isinstance(privileges, dict) and (
                path == "/access" and "Sys.Audit" in privileges
                or path == "/" and privileges.get("Sys.Audit") in (True, 1, "1")
            )
            for path, privileges in permissions.items()
        )
        if not complete or not acl_audit:
            raise PveError("vm_audit_scope_missing")
        if any(
            not isinstance(privileges, dict)
            or any(not isinstance(name, str) or not name.endswith(".Audit") for name in privileges)
            for privileges in permissions.values()
        ):
            raise PveError("vm_read_only_scope_unsupported")
        acl = self._get("access/acl")
        if not isinstance(acl, list):
            raise PveError("vm_audit_scope_incomplete")
        for row in acl:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise PveError("vm_audit_scope_incomplete")
            path = row["path"]
            if path.startswith("/pool/"):
                raise PveError("pool_scope_unsupported")
            if path == "/vms" or path.startswith("/vms/"):
                effective = permissions.get(path)
                if not isinstance(effective, dict) or "VM.Audit" not in effective:
                    raise PveError("vm_audit_scope_incomplete")
        return True
