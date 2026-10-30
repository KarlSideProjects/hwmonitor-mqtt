# Sender distribution flow index

This bounded inventory describes the existing distribution and license inputs. It does not establish whole-project coverage.

| Feature ID | Flow | Sources and owners |
| --- | --- | --- |
| `sender.distribution` | [Flow](sender.distribution.mmd) | `bootstrap.sh`, `bootstrap.ps1`, `Dockerfile`, `release-public/README.md`; project maintainers |
| `sender.license` | [Flow](sender.license.mmd) | `README.md`, `LICENSE`, `pyproject.toml`, `release-public/LICENSE-NOTICE.md`; project maintainers |
| `sender.release-inspection` | [Flow](sender.release-inspection.mmd) | `scripts/inspect_sender_candidate.py`, `tests/test_release_candidate.py`, `docs/release/sender-candidate.md`; project maintainers |

[Shared contracts](contracts.md) describes the boundaries. The existing README and LICENSE own the published project license statement. The dependency manifest owns declared dependencies, not a released bill of materials.

The flows stop at the installer and container command boundaries. Their downstream installation, service lifecycle, sender runtime and dependency resolution behavior is outside this bounded inventory and remains unverified. Direct boundary inputs, related tests and CI configuration are registered as policy inputs for invalidation, not claimed as reviewed feature flows. Actual artifact production, release automation, complete content inspection and third-party license compliance also remain unverified. The inspection flow describes a preliminary layout gate, not a release approval. Expand the inventory before editing their behavior. No automated generator or required candidate CI is configured by this baseline. The delivery boundary is a non-draft pull request; independent review and candidate validation remain pending.

The PowerShell syntax test discovers all `*.ps1` files except `.venv`; the manifest registers the current tracked set. Newly added scripts require scope review and registration. Untracked scripts can also affect local test collection.
