"""Collect actual installed distribution notices for private candidate review."""

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--python-license", type=Path, required=True)
    parser.add_argument("packages", nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    python_notice = args.output / "python.txt"
    shutil.copyfile(args.python_license, python_notice)
    if not python_notice.stat().st_size:
        parser.error("Python license is empty")
    inventory = {"python_license_sha256": hashlib.sha256(python_notice.read_bytes()).hexdigest(), "distributions": {}}
    for name in args.packages:
        dist = importlib.metadata.distribution(name)
        files = [item for item in (dist.files or []) if ("license" in str(item).lower() or "copying" in str(item).lower()) and dist.locate_file(item).is_file()]
        if not files:
            parser.error(f"no installed license file for {name} {dist.version}")
        entries = []
        for index, item in enumerate(files):
            target = re.sub(r"[^a-zA-Z0-9_-]", "-", name) + f"-{index}.txt"
            content = dist.locate_file(item).read_bytes()
            if not content:
                parser.error(f"empty license file for {name} {dist.version}")
            (args.output / target).write_bytes(content)
            entries.append({"file": target, "source": str(item), "sha256": hashlib.sha256(content).hexdigest()})
        inventory["distributions"][name] = {"version": dist.version, "notices": entries}
    args.inventory.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
