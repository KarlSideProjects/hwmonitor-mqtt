"""Fixed sender and installer dispatch; importing this module has no effects."""

COLLECTORS = {
    "windows": "hwmonitor_mqtt.agents.agent_sender_windows",
    "linux": "hwmonitor_mqtt.agents.agent_sender_async",
}
# shortcut: only Debian, Ubuntu, and Raspbian are validated here; add another distro after validating its Compose support.
SUPPORTED_LINUX_DISTRIBUTIONS = {"debian", "ubuntu", "raspbian"}


def collector_for(target):
    if target.system == "unsupported":
        raise ValueError("Target detection is unsupported or contradictory")
    if target.system == "pve":
        return COLLECTORS["linux"], "PVE collector support pending"
    if target.system == "unknown":
        return COLLECTORS["linux"], "container sender; host system unknown"
    key = target.os if target.os == "windows" else "linux" if target.os == "linux" else None
    if key is None:
        raise ValueError(f"Unsupported target OS: {target.os}")
    return COLLECTORS[key], "ready"


def install_command(target, project_root):
    """Return the existing platform installer command without running it."""
    from .installers import linux, windows

    if target.system == "pve":
        raise ValueError("PVE detected; PVE installation support is pending")
    if target.system == "unknown":
        raise ValueError("Container target detected; host-aware installation support is pending")
    if target.os == "windows":
        return windows.command(project_root)
    if target.os == "linux" and target.system == "linux":
        if target.distribution not in SUPPORTED_LINUX_DISTRIBUTIONS:
            raise ValueError(f"Unsupported Linux distribution: {target.distribution or 'unknown'}")
        return linux.command(project_root)
    raise ValueError("Target is unsupported or PVE evidence is incomplete; no installation selected")
