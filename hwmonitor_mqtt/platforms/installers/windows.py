from pathlib import Path


def command(project_root):
    script = str(Path(project_root) / "scripts" / "windows" / "install_windows_sender_task.ps1")
    return ["powershell", "-ExecutionPolicy", "Bypass", "-File", script]
