# Sender distribution flow index

This bounded inventory describes the existing distribution and license inputs. It does not establish whole-project coverage.

| Feature ID | Flow | Sources and owners |
| --- | --- | --- |
| `sender.distribution` | [Flow](sender.distribution.mmd) | `bootstrap.sh`, `bootstrap.ps1`, `Dockerfile`; project maintainers |
| `sender.license` | [Flow](sender.license.mmd) | `README.md`, `LICENSE`, `pyproject.toml`; project maintainers |

[Shared contracts](contracts.md) describes the boundaries. The existing README and LICENSE own the published project license statement. The dependency manifest owns declared dependencies, not a released bill of materials.

The flows stop at the installer and container command boundaries. Their downstream installation, service lifecycle, sender runtime and dependency resolution behavior is outside this bounded inventory and remains unverified. Direct boundary inputs, related tests and CI configuration are registered as policy inputs for invalidation, not claimed as reviewed feature flows. Artifact production, release automation and third-party license compliance also remain unverified. Expand the inventory before editing their behavior. No automated generator or required candidate CI is configured by this baseline. The delivery boundary is a non-draft pull request; independent review and candidate validation remain pending.

The PowerShell syntax test discovers all `*.ps1` files except `.venv`; the manifest registers the current tracked set. Newly added scripts require scope review and registration. Untracked scripts can also affect local test collection.
