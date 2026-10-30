# Bounded sender distribution adoption

The source baseline covers `sender.distribution` and `sender.license` only. It records existing behavior and does not claim whole-project adoption or release readiness.

| Artifact or gate | State |
| --- | --- |
| `AGENTS.md` reading and delivery rule | Created |
| `docs/flows/README.md`, `docs/flows/contracts.md` | Created from committed sources |
| `docs/flows/sender.distribution.mmd`, `docs/flows/sender.license.mmd` | Created from committed sources |
| `docs/flow-manifest.json` | Created, registered source fingerprints refreshed |
| `tools/flow-docs/flow_checker.py`, `flow_parsers.py`, `requirements-ast.txt` | Copied from installed flow-docs-sync bundle |
| Root `README.md`, `LICENSE`, `pyproject.toml` | Existing authoritative inputs reused |
| Installation and runtime flow dependencies | Gap; expand and review before dependent edits |
| Independent review and candidate validation | Pending; no review is asserted |
| Runtime evidence, agent discovery, candidate CI and remote merge enforcement | Unverified |
| Hook and unattended generation | Not configured; delivery boundary is PR |

The baseline comparison source is commit `86b074bc1aa6c38bbdb236f23546b1024ffc227a`. Its Git tree contains no `docs/flows/`, `docs/flow-manifest.json`, or `tools/flow-docs/` paths. Existing behavior and dependency declarations do not establish the contents or licensing compliance of any future compiled artifact.
