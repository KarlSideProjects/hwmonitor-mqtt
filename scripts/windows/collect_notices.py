"""Collect actual installed distribution notices for private candidate review."""

import argparse
import hashlib
import importlib.metadata
import io
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path


SUPPLEMENTS = {
    ("pythonnet", "3.0.5"): (
        "https://files.pythonhosted.org/packages/9a/d6/1afd75edd932306ae9bd2c2d961d603dc2b52fcec51b04afea464f1f6646/pythonnet-3.0.5.tar.gz",
        "48e43ca463941b3608b32b4e236db92d8d40db4c58a75ace902985f76dac21cf",
        "pythonnet-3.0.5/LICENSE",
        "e1dc3a6ff017a6800dc27a7220500f512b95fbcd996d38ebbbff85c693245369",
    ),
    ("clr-loader", "0.2.10"): (
        "https://files.pythonhosted.org/packages/18/24/c12faf3f61614b3131b5c98d3bf0d376b49c7feaa73edca559aeb2aee080/clr_loader-0.2.10.tar.gz",
        "81f114afbc5005bafc5efe5af1341d400e22137e275b042a8979f3feb9fc9446",
        "clr_loader-0.2.10/LICENSE",
        "b00f9a4b112b44f91276ad8388ed470ad36668ff80f56f15e1e6981c85e6d23d",
    ),
}
MIT_URL = "https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/MIT.txt"
MIT_SHA256 = "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5"
PAHO_URL = "https://files.pythonhosted.org/packages/39/15/0a6214e76d4d32e7f663b109cf71fb22561c2be0f701d67f93950cd40542/paho_mqtt-2.1.0.tar.gz"
PAHO_SHA256 = "12d6e7511d4137555a3f6ea167ae846af2c7357b10bc6fa4f7c3968fc1723834"
PAHO_TERMS = (
    ("paho_mqtt-2.1.0/edl-v10", "e8cf7d54ea46c19aba793983889b7f7425e1ebfcaaccec764a7db091646e203c"),
    ("paho_mqtt-2.1.0/epl-v20", "8c349f80764d0648e645f41ef23772a70c995a0924b5235f735f4a3d09df127c"),
)


def fetch_verified(url, expected):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch for {url}")
    return data


def supplemental_notices(name, dist):
    if name.lower() == "paho-mqtt" and dist.version == "2.1.0":
        data = fetch_verified(PAHO_URL, PAHO_SHA256)
        notices = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member, expected in PAHO_TERMS:
                entry = archive.getmember(member)
                if not entry.isfile() or entry.size > 2 * 1024 * 1024:
                    raise ValueError("invalid Paho license entry")
                content = archive.extractfile(entry).read()
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError("Paho notice SHA-256 mismatch")
                notices.append((content, f"{PAHO_URL}#{member}", PAHO_SHA256))
        return notices
    info = SUPPLEMENTS.get((name.lower(), dist.version))
    if info:
        url, archive_hash, member, notice_hash = info
        data = fetch_verified(url, archive_hash)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            entry = archive.getmember(member)
            if not entry.isfile() or entry.size > 2 * 1024 * 1024:
                raise ValueError(f"invalid notice in {url}")
            content = archive.extractfile(entry).read()
        if hashlib.sha256(content).hexdigest() != notice_hash:
            raise ValueError(f"notice SHA-256 mismatch for {name}")
        return [(content, f"{url}#{member}", archive_hash)]
    if name.lower() == "wmi" and dist.version == "1.5.1":
        metadata = dist.read_text("METADATA")
        if not metadata or hashlib.sha256(metadata.encode("utf-8")).hexdigest() != "bf90809237a8ce1dac86bed7e34e4e76692d87ffd21aed16541105cd63ea533a":
            raise ValueError("WMI metadata SHA-256 mismatch")
        return [(metadata.encode("utf-8"), "https://files.pythonhosted.org/packages/ee/b9/a80d1ed4d115dac8e2ac08d16af046a77ab58e3d186e22395bf2add24090/WMI-1.5.1-py2.py3-none-any.whl#WMI-1.5.1.dist-info/METADATA", "1d6b085e5c445141c475476000b661f60fff1aaa19f76bf82b7abb92e0ff4942"),
                (fetch_verified(MIT_URL, MIT_SHA256), MIT_URL, MIT_SHA256)]
    return []


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
        if name.lower() == "paho-mqtt" and dist.version != "2.1.0":
            parser.error(f"Paho notice sources are not pinned for {dist.version}")
        files = [item for item in (dist.files or []) if ("license" in str(item).lower() or "copying" in str(item).lower()) and dist.locate_file(item).is_file()]
        entries = []
        for index, item in enumerate(files):
            target = re.sub(r"[^a-zA-Z0-9_-]", "-", name) + f"-{index}.txt"
            content = dist.locate_file(item).read_bytes()
            if not content:
                parser.error(f"empty license file for {name} {dist.version}")
            (args.output / target).write_bytes(content)
            entries.append({"file": target, "source": str(item), "sha256": hashlib.sha256(content).hexdigest()})
        if not entries or name.lower() == "paho-mqtt":
            for content, source, source_hash in supplemental_notices(name, dist):
                target = re.sub(r"[^a-zA-Z0-9_-]", "-", name) + f"-{len(entries)}.txt"
                (args.output / target).write_bytes(content)
                entries.append({"file": target, "source": source, "source_sha256": source_hash, "sha256": hashlib.sha256(content).hexdigest()})
        if not entries:
            parser.error(f"no license notice for {name} {dist.version}")
        inventory["distributions"][name] = {"version": dist.version, "notices": entries}
    args.inventory.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
