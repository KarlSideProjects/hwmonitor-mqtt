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
import xml.etree.ElementTree as ET
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
NATIVE_INPUTS = {
    "openssl-3.5.9-LICENSE.txt": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
    "xz-5.2.5-COPYING": "bcb02973ef6e87ea73d331b3a80df7748407f17efdb784b61b47e0e610d3bb5c",
    "cpython-3.13.17-expat-COPYING": "31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87",
}
CPYTHON_BINARY_LICENSE = "62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120"
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
    normalized_name = name.lower().replace("_", "-")
    if normalized_name == "paho-mqtt" and dist.version == "2.1.0":
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
    info = SUPPLEMENTS.get((normalized_name, dist.version))
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
    if normalized_name == "wmi" and dist.version == "1.5.1":
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
    parser.add_argument("--python-bundle", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("packages", nargs="*")
    args = parser.parse_args()
    if args.report and args.packages:
        parser.error("choose a Nuitka report or explicit packages")
    expected = {}
    if args.report:
        report = ET.parse(args.report).getroot()
        if report.tag != "nuitka-compilation-report" or report.get("completion") != "yes":
            parser.error("Nuitka report is incomplete")
        for item in report.findall("./distributions/distribution"):
            name, version = item.get("name"), item.get("version")
            if not name or not version or name in expected:
                parser.error("invalid distribution in Nuitka report")
            expected[name] = version
        if not expected:
            parser.error("Nuitka report contains no distributions")
    elif args.packages:
        expected = dict.fromkeys(args.packages)
    else:
        parser.error("a Nuitka report or packages are required")
    args.output.mkdir(parents=True, exist_ok=False)
    python_notice = args.output / "python.txt"
    raw_python_license_sha256 = hashlib.sha256(args.python_license.read_bytes()).hexdigest()
    if args.python_bundle:
        if raw_python_license_sha256 != CPYTHON_BINARY_LICENSE:
            parser.error('Python binary license differs from pinned upstream bytes')
        if hashlib.sha256(args.python_bundle.read_bytes()).hexdigest() != 'fafca1da78d44368edbb3e5fbcc6d505411ed5d5e5177799fa92afe5fc2cd2e7':
            parser.error('Python notice bundle differs from pinned bytes')
    shutil.copyfile(args.python_bundle or args.python_license, python_notice)
    if not python_notice.stat().st_size:
        parser.error("Python license is empty")
    inventory = {"python_license_sha256": hashlib.sha256(python_notice.read_bytes()).hexdigest(), "distributions": {}}
    if raw_python_license_sha256 == CPYTHON_BINARY_LICENSE:
        native_dir = Path(__file__).resolve().parents[2] / "release-notices/windows-x64-cpython-3.13.17"
        inventory["native_notices"] = {}
        for name, expected_hash in NATIVE_INPUTS.items():
            content = (native_dir / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected_hash:
                raise ValueError(f"native notice SHA-256 mismatch: {name}")
            (args.output / name).write_bytes(content)
            inventory["native_notices"][name] = expected_hash
    for name, expected_version in expected.items():
        dist = importlib.metadata.distribution(name)
        if expected_version is not None and dist.version != expected_version:
            parser.error(f"installed {name} version does not match Nuitka report")
        if name.lower() == "paho-mqtt" and dist.version != "2.1.0":
            parser.error(f"Paho notice sources are not pinned for {dist.version}")
        files = [item for item in (dist.files or []) if re.match(r"^(license|copying|authors)(?:$|[._-])", item.name, re.IGNORECASE) and item.suffix.lower() not in {".py", ".pyc", ".pyd", ".dll", ".so"} and dist.locate_file(item).is_file()]
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
                target = Path(source.split("#")[-1]).name if name.lower() == "paho-mqtt" else re.sub(r"[^a-zA-Z0-9_-]", "-", name) + f"-{len(entries)}.txt"
                (args.output / target).write_bytes(content)
                entries.append({"file": target, "source": source, "source_sha256": source_hash, "sha256": hashlib.sha256(content).hexdigest()})
        if not entries:
            parser.error(f"no license notice for {name} {dist.version}")
        inventory["distributions"][name] = {"version": dist.version, "notices": entries}
    args.inventory.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
