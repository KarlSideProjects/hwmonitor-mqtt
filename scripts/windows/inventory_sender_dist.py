"""Record the actual pre-onefile Windows bundle without publishing its contents."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.dist.is_dir() or args.dist.is_symlink():
        parser.error("dist must be a real directory")
    entries = []
    for path in sorted(args.dist.rglob("*")):
        if path.is_symlink():
            parser.error(f"links are not permitted: {path}")
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append({"path": path.relative_to(args.dist).as_posix(), "size": path.stat().st_size, "sha256": digest})
    if not entries:
        parser.error("dist is empty")
    args.output.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
