# Sender distribution flow index

This bounded inventory describes sender distribution, inspection, build and release orchestration. It does not establish whole-project coverage.

| Feature ID | Flow | Sources and owners |
| --- | --- | --- |
| `sender.distribution` | [Flow](sender.distribution.mmd) | `bootstrap.sh`, `bootstrap.ps1`, `Dockerfile`, `release-public/README.md`; project maintainers |
| `sender.smart-discovery` | [Flow](sender.smart-discovery.mmd) | `hwmonitor_mqtt/collectors/smart.py`, `tests/test_smart_collector.py`; project maintainers |
| `sender.license` | [Flow](sender.license.mmd) | `README.md`, `LICENSE`, `pyproject.toml`, `release-public/LICENSE-NOTICE.md`; project maintainers |
| `sender.release-inspection` | [Flow](sender.release-inspection.mmd) | `scripts/inspect_sender_candidate.py`, `scripts/sender_build_inputs.py`, `tests/test_release_candidate.py`, `docs/release/sender-candidate.md`; project maintainers |
| `sender.build` | [Flow](sender.build.mmd) | Linux and Windows build workflows, package and evidence producers, ARM startup inputs; project maintainers |
| `sender.release-orchestration` | [Flow](sender.release-orchestration.mmd) | Release workflow, controller, backend, version, asset checker and loopback prototype; project maintainers |
| `sender.compiled-lifecycle` | [Flow](sender.compiled-lifecycle.mmd) | Compiled install and upgrade controllers, platform installers and task scripts; project maintainers |

[Shared contracts](contracts.md) describes the boundaries. The existing README and LICENSE own the published project license statement. The dependency manifest owns declared dependencies, not a released bill of materials.

The distribution flow summarizes installation and rollback; sender runtime and complete build dependencies and test coverage remain explicit inventory gaps. The build and release flows describe source behavior and do not establish that a fresh candidate was produced or approved. Inspection is a boundary check, not release approval or proof of complete content or third-party compliance. Expand and verify the inventory before delivery. The reusable CI flow-docs job runs explain and check against the exact selected-source parent or PR base; repository branch protection must separately require this job. No automated diagram generator is configured. The delivery boundary is a non-draft pull request; independent review and candidate validation remain pending.

The PowerShell syntax test discovers all `*.ps1` files except `.venv`; the manifest registers the current tracked set. Newly added scripts require scope review and registration. Untracked scripts can also affect local test collection.

The SMART discovery flow covers executable discovery and the test seam only. Normalization, alert thresholds, PVE fallback internals and platform integration remain outside its reviewed scope; expand it before changing those behaviors.

For review dispatch, run `python3 tools/flow-docs/flow_checker.py explain --base <trusted-sha>` after synchronizing snapshots. Its JSON lists only affected feature IDs and exact fingerprints. Select the same worktree/index mode for review and check; explain does not grant approval. Use `packet --base <trusted-sha> --feature <id>` for bounded direct input navigation. To align repeated local candidates with CI, pin the fetched actual PR base once with `pin-base --base <sha> --default-branch master` and use `--pinned-base`; CI retains its independent event base.
